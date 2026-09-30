"""BaSSDream score: our own bass tab format, read by the WPF score view.

One file per song, E:\\BassStation\\scores\\<song_id>\\score.json, read only by the WPF score view (scores are edited in GP;
the view only stores the backing alignment in meta.audio_offset_ms). Bars are in playback order (repeats unrolled); each bar carries its start and
end in seconds of the backing recording, so the view syncs without GP sync-point semantics.

    {"format": "bassdream-score", "version": 1, "tpq": 960,
     "meta": {"title", "artist", "song_id", "source": "gp:<path>" | "bassnet:<run>", "created", "modified"},
     "tuning": [midi low -> high], "capo": 0, "key": [accidentals, "Major"|"Minor"],
     "bars": [{"written": 1-based score bar, "occ": pass, "num", "den", "section": str|null,
               "t0": s, "t1": s, "beats": [{"tick", "dur", "v": 1..128, "d": dots, "t": tuplet num,
                   "notes": [{"id", "s": string (0 = lowest), "f": fret, "midi", "tie", "x": dead,
                              "slide", "hopo", "slap", "pop", "src": "tab"|"model"|"user", "conf"}]}]}],
     "audio": {"backing": wav, "bass": wav|null, "nobass": bool}}

CLI (Python311):
  score_format.py open <song_id> <gp_path>     -> {"success", "score": path, "audio": {...}}  (imports once)
  score_format.py stretch <song_id> <rate>     -> {"success", "backing": wav, "bass": wav|null}   (rate 0.5-1.5)
  score_format.py align <song_id>              -> {"success", "offset_ms", "confidence"}  (audio event = score time + offset)
  score_format.py audio <song_id> <gp_path>    -> {"success", "backing", "bass"}  (tracks only, for report replay)
Times of stretched tracks are session seconds: recording time = session time * rate.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from typing import Any, Dict, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bassnet.gpif_parser import parse_gp  # noqa: E402

FORMAT = "bassdream-score"
VERSION = 1
TPQ = 960
SCORES = r"E:\BassStation\scores"
AUDIO_CACHE = r"E:\BassStation\cache\scores"
SR = 44100


def score_dir(song_id: str) -> str:
    return os.path.join(SCORES, "".join(c if c.isalnum() or c in "-_" else "_" for c in song_id))


def _tick(q: float) -> int:
    return int(round(q * TPQ))


def from_gp(gp_path: str, song_id: str, title: str = "", artist: str = "") -> Dict[str, Any]:
    """Imports a GP score: rhythm and fingering as written, times from the aligned score->audio map."""
    from eval_report import build_report
    from performance_evaluator import score_time_map
    info = parse_gp(gp_path)
    qmap = score_time_map(gp_path, info)
    rep = build_report(gp_path, {})
    tech = {(n.bar, n.occurrence, _tick(float(n.qpos)), n.string): n for n in info.notes if not n.grace}
    sec_of = {}
    for s in rep["sections"]:
        sec_of[s["start"]] = s["name"]
    tuning = rep["tuning"]
    bars, nid = [], 0
    for i, (b, (bi, oc, q0, qlen)) in enumerate(zip(rep["bars"], info.bars)):
        q0, q1 = float(q0), float(q0 + qlen)
        beats = []
        for bt in b["beats"]:
            notes = []
            for n in bt["n"]:
                g = tech.get((bi, oc, _tick(q0 + bt["p"]), n["s"]))
                note = {"id": f"n{nid}", "s": n["s"], "f": n["f"],
                        "midi": tuning[n["s"]] + n["f"] if n["s"] < len(tuning) else (g.midi if g else 0),
                        "src": "tab"}
                nid += 1
                if n.get("tie"):
                    note["tie"] = True
                if n.get("x"):
                    note["x"] = True
                if g is not None:
                    if g.slide:
                        note["slide"] = g.slide
                    if g.hopo_origin or g.hopo_dest:
                        note["hopo"] = True
                    if g.slap:
                        note["slap"] = True
                    if g.pop:
                        note["pop"] = True
                notes.append(note)
            beats.append({"tick": _tick(bt["p"]), "dur": _tick(bt["l"]), "v": bt["v"], "d": bt["d"], "t": bt["t"],
                          "notes": notes})
        if not beats:
            # an empty bar in GP is one whole-bar rest
            v, d = {4: (1, 0), 3: (2, 1), 2: (2, 0), 1: (4, 0)}.get(b["num"] * 4 // b["den"], (1, 0))
            beats = [{"tick": 0, "dur": _tick(q1 - q0), "v": v, "d": d, "t": 0, "notes": []}]
        bars.append({"written": bi + 1, "occ": oc, "num": b["num"], "den": b["den"], "section": sec_of.get(i),
                     "t0": round(float(qmap(q0)), 4), "t1": round(float(qmap(q1)), 4), "beats": beats})
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    return {"format": FORMAT, "version": VERSION, "tpq": TPQ,
            "meta": {"title": title, "artist": artist, "song_id": song_id, "source": "gp:" + os.path.abspath(gp_path),
                     "created": now, "modified": now},
            "tuning": tuning, "capo": 0, "key": list(info.key), "bars": bars}


_VALUE = {"Whole": 1, "Half": 2, "Quarter": 4, "Eighth": 8, "16th": 16, "32nd": 32, "64th": 64}


def write_score(out_path: str, qs, doc=None, tuning=None, key=(0, "Major"), bar_keys=None, sections=None,
                notes_meta=None, model_info=None, title: str = "", artist: str = "") -> str:
    """Model output -> score.json, called by bassnet.pipeline.build_gp next to the GP it writes
    (<gp name>.score.json); open_score() adopts it as the song's working copy.

    qs: quantize.QScore (simple bars: 24 ticks per quarter; compound bars: 24 per dotted quarter)
    doc: song_doc.SongDoc (embedded as is)            bar_keys: {played bar: (accidentals, mode)}
    sections: {played bar: (letter, text)}             notes_meta: per qs.notes entry {time, end, conf, low_conf, cand...}
    model_info: {models_json hash, weights, code snapshot, audio md5}
    Rhythm spelling is the GP writer's (gp_writer.build_bar_events), so the view shows what GP8 would.
    """
    from dataclasses import asdict
    from bassnet.gp_writer import build_bar_events
    from bassnet.quantize import TPB
    events = build_bar_events(qs)
    meta_of = {id(n): m for n, m in zip(qs.notes, notes_meta or [])}
    tuning = list(tuning or [28, 33, 38, 43])
    bars, nid = [], 0
    for b, ev in enumerate(events):
        comp = bool(qs.bar_compound[b])
        scale = TPQ * 3 // 2 // TPB if comp else TPQ // TPB     # our ticks per quantizer tick
        m = qs.meter_of(b)
        num, den = (m * 3, 8) if comp else (m, 4)
        beats: Dict[int, Dict[str, Any]] = {}
        for p, t, val, n, tie_orig, tie_dest in ev:
            tick = p * scale
            bt = beats.get(tick)
            if bt is None:
                if val == "BAR_REST":
                    v, d = ((2, 1) if m == 2 else (1, 0)) if comp else {4: (1, 0), 3: (2, 1), 2: (2, 0), 1: (4, 0)}.get(m, (1, 0))
                    tup = 0
                else:
                    v, d, tup = _VALUE.get(val[0], 4), val[1], (val[2][0] if val[2] else 0)
                bt = beats[tick] = {"tick": tick, "dur": t * scale, "v": v, "d": d, "t": tup, "notes": []}
            if n is None:
                continue
            note = {"id": f"n{nid}", "s": n.string, "f": n.fret, "midi": n.midi, "src": "model"}
            nid += 1
            if tie_dest:
                note["tie"] = True
            for k, on in (("x", n.dead), ("hopo", n.hopo_origin or n.hopo_dest), ("slap", n.slap), ("pop", n.pop),
                          ("flag", n.flag)):
                if on:
                    note[k] = True
            if n.slide:
                note["slide"] = n.slide
            mm = meta_of.get(id(n))
            if mm and not tie_dest:
                if mm.get("conf") is not None:
                    note["conf"] = round(float(mm["conf"]), 3)
                note["time"] = round(float(mm.get("time", 0.0)), 4)
                if mm.get("end") is not None:
                    note["end"] = round(float(mm["end"]), 4)
                if mm.get("cand"):
                    note["cand"] = mm["cand"]
            bt["notes"].append(note)
        bar = {"written": b + 1, "occ": 0, "num": num, "den": den,
               "section": " ".join(x for x in sections[b] if x) if sections and b in sections else None,
               "t0": round(float(qs.bar_time(b)), 4), "t1": round(float(qs.bar_time(b + 1)), 4),
               "beats": [beats[k] for k in sorted(beats)]}
        if bar_keys and b in bar_keys:
            bar["key"] = list(bar_keys[b])
        bars.append(bar)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    score = {"format": FORMAT, "version": VERSION, "tpq": TPQ,
             "meta": {"title": title, "artist": artist, "source": "bassnet:" + os.path.basename(out_path),
                      "created": now, "modified": now},
             "model": model_info or {}, "tuning": tuning, "capo": 0, "key": list(key), "bars": bars}
    if doc is not None:
        score["song_doc"] = asdict(doc) if not isinstance(doc, dict) else doc
    errs = validate(score)
    if errs:
        raise ValueError(f"score invalid: {errs[:3]}")
    path = os.path.splitext(out_path)[0] + ".score.json"
    with open(path + ".part", "w", encoding="utf-8") as f:
        json.dump(score, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(path + ".part", path)
    return path


def validate(score: Dict[str, Any]) -> list:
    """Problems that would break the view or an export (empty list = fine)."""
    errs = []
    if score.get("format") != FORMAT:
        errs.append("format")
    n_str = len(score.get("tuning", []))
    ids = set()
    for i, b in enumerate(score.get("bars", [])):
        if b["t1"] < b["t0"]:
            errs.append(f"bar {i}: t1 < t0")
        for bt in b["beats"]:
            for n in bt["notes"]:
                if n["id"] in ids:
                    errs.append(f"bar {i}: duplicate id {n['id']}")
                ids.add(n["id"])
                if not 0 <= n["s"] < n_str:
                    errs.append(f"bar {i}: string {n['s']}")
    return errs


# ---------------------------------------------------------------- audio tracks

def _write_wav(path: str, x: np.ndarray):
    import soundfile as sf
    tmp = path + ".part"
    sf.write(tmp, np.clip(x, -1.0, 1.0), SR, subtype="PCM_16", format="WAV")
    os.replace(tmp, path)


def _prepare_audio(gp_path: str, song_id: str) -> Dict[str, Any]:
    """Bass-less backing + the removed bass (original mix minus backing), 44.1 kHz, cached per song."""
    import eval_session
    info = parse_gp(gp_path)
    out = os.path.join(AUDIO_CACHE, os.path.basename(score_dir(song_id)))
    os.makedirs(out, exist_ok=True)
    backing_p, bass_p = os.path.join(out, "backing_1.00.wav"), os.path.join(out, "bass_1.00.wav")
    if os.path.exists(backing_p):
        return {"backing": backing_p, "bass": bass_p if os.path.exists(bass_p) else None,
                "nobass": os.path.exists(bass_p)}
    if not info.audio_asset:
        return {"backing": None, "bass": None, "nobass": False}
    backing, nobass = eval_session._backing(gp_path, info)
    _write_wav(backing_p, backing)
    if nobass:
        tmp = os.path.join(out, "_src" + os.path.splitext(info.audio_asset)[1])
        with zipfile.ZipFile(gp_path) as z, open(tmp, "wb") as f:
            f.write(z.read(info.audio_asset))
        try:
            mix = eval_session._ffmpeg_f32(tmp, SR, 2)
        finally:
            os.remove(tmp)
        n = min(len(mix), len(backing))
        _write_wav(bass_p, (mix[:n] - backing[:n]).mean(axis=1))
    return {"backing": backing_p, "bass": bass_p if nobass else None, "nobass": nobass}


def stretch(song_id: str, rate: float) -> Dict[str, Any]:
    """Tempo-changed copies of the audio tracks (ffmpeg atempo, pitch kept), cached per rate."""
    from eval_session import FFMPEG_EXE
    rate = round(float(min(1.5, max(0.5, rate))), 2)
    out = os.path.join(AUDIO_CACHE, os.path.basename(score_dir(song_id)))
    res: Dict[str, Any] = {"success": True, "rate": rate}
    for name in ("backing", "bass"):
        src = os.path.join(out, f"{name}_1.00.wav")
        if not os.path.exists(src):
            res[name] = None
            continue
        dst = os.path.join(out, f"{name}_{rate:.2f}.wav")
        if not os.path.exists(dst):
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.run([FFMPEG_EXE, "-y", "-v", "error", "-i", src, "-filter:a", f"atempo={rate:.4f}",
                            "-c:a", "pcm_s16le", dst + ".part.wav"], check=True, creationflags=flags)
            os.replace(dst + ".part.wav", dst)
        res[name] = dst
    return res


def note_times(score: Dict[str, Any]) -> np.ndarray:
    """Recording time of every sounding note (ties skipped), linear inside each bar as the view places them."""
    out = []
    for b in score["bars"]:
        length = b["num"] * score["tpq"] * 4 // b["den"]
        for bt in b["beats"]:
            if any(not n.get("tie") for n in bt["notes"]):
                out.append(b["t0"] + (b["t1"] - b["t0"]) * bt["tick"] / max(1, length))
    return np.array(out)


def auto_align(song_id: str) -> Dict[str, Any]:
    """Global offset (ms) that lands the score's notes on the original bass's attacks: audio event at score time + offset.

    Searches +-400 ms on the bass stem's onset strength; the confidence is the peak over the median of the curve.
    """
    import librosa
    d = score_dir(song_id)
    with open(os.path.join(d, "score.json"), encoding="utf-8") as f:
        score = json.load(f)
    bass = os.path.join(AUDIO_CACHE, os.path.basename(d), "bass_1.00.wav")
    if not os.path.exists(bass):
        return {"success": False, "error": "没有原曲贝斯轨"}
    y, sr = librosa.load(bass, sr=22050, mono=True)
    hop = 128
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, n_fft=1024, fmax=2000, lag=1, max_size=3)
    env = env / (np.percentile(env, 99) + 1e-9)
    fps = sr / hop
    t = note_times(score)
    offs = np.arange(-0.4, 0.4001, 0.002)
    fr = np.round((t[None, :] + offs[:, None]) * fps).astype(int)
    ok = (fr >= 1) & (fr < len(env) - 1)
    fr = np.clip(fr, 1, len(env) - 2)
    peak = np.maximum(np.maximum(env[fr - 1], env[fr]), env[fr + 1]) * ok
    curve = peak.sum(axis=1)
    best = int(np.argmax(curve))
    conf = float(curve[best] / (np.median(curve) + 1e-9))
    return {"success": True, "offset_ms": round(float(offs[best]) * 1000.0, 1), "confidence": round(conf, 2), "notes": int(len(t))}


def open_score(song_id: str, gp_path: str, title: str = "", artist: str = "") -> Dict[str, Any]:
    """score.json for a song (imported from its GP on first open) with its audio tracks prepared."""
    d = score_dir(song_id)
    path = os.path.join(d, "score.json")
    sibling = os.path.splitext(gp_path)[0] + ".score.json"
    if os.path.exists(path) and os.path.exists(sibling) and os.path.getmtime(sibling) > os.path.getmtime(path):
        os.remove(path)     # re-transcribed: take the new model output
    if not os.path.exists(path):
        os.makedirs(d, exist_ok=True)
        # a transcription carries the model's own score (confidences, note times) next to its GP
        if os.path.exists(sibling):
            with open(sibling, encoding="utf-8") as f:
                score = json.load(f)
            score["meta"].update({"song_id": song_id, "title": title or score["meta"].get("title", ""),
                                  "artist": artist or score["meta"].get("artist", "")})
        else:
            score = from_gp(gp_path, song_id, title, artist)
        errs = validate(score)
        if errs:
            return {"success": False, "error": "曲谱导入失败", "detail": errs[:5]}
        with open(path + ".part", "w", encoding="utf-8") as f:
            json.dump(score, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(path + ".part", path)
    audio = _prepare_audio(gp_path, song_id)
    return {"success": True, "score": path, "audio": audio}


if __name__ == "__main__":
    try:
        if sys.argv[1] == "open":
            res = open_score(sys.argv[2], sys.argv[3], *(sys.argv[4:6]))
        elif sys.argv[1] == "stretch":
            res = stretch(sys.argv[2], float(sys.argv[3]))
        elif sys.argv[1] == "align":
            res = auto_align(sys.argv[2])
        elif sys.argv[1] == "audio":      # tracks only (evaluation report replay): backing / bass wavs of a GP
            res = dict(_prepare_audio(sys.argv[3], sys.argv[2]), success=True)
        else:
            res = {"success": False, "error": "unknown action"}
    except Exception as e:
        res = {"success": False, "error": "曲谱准备失败", "detail": str(e)}
    print(json.dumps(res, ensure_ascii=False))
