"""End-to-end: audio -> separated bass -> BassNet (ensemble + TTA) -> beat-grid quantisation -> .gp"""
import glob
import os
import sys
from typing import Callable, Dict, List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths  # noqa: E402
from bassnet.dataset import SR, compute_cqt, compute_mel, BINS_PER_SEMI  # noqa: E402
from bassnet.decode import decode_notes, decode_beats  # noqa: E402
from bassnet.quantize import quantize  # noqa: E402
from bassnet.fretboard import assign_frets  # noqa: E402
from bassnet.gp_writer import write_gp  # noqa: E402

MODEL_DIR = paths.cache(r"bassnet")
LOW_CONF = 0.5   # val (v3+v4): flags ~4.4% of notes, ~69% of them wrong


def model_paths() -> List[str]:
    return sorted(glob.glob(os.path.join(MODEL_DIR, "bassnet*.pt")))


def model_roles() -> Optional[Dict[str, List[str]]]:
    """Optional MODEL_DIR/models.json: {"notes": [...], "beats": [...], "downbeats": [...]} (file names).
    Without it every bassnet*.pt feeds every head."""
    cfg = os.path.join(MODEL_DIR, "models.json")
    if not os.path.exists(cfg):
        return None
    import json
    roles = json.load(open(cfg, encoding="utf8"))
    return {k: [os.path.join(MODEL_DIR, f) for f in v] for k, v in roles.items()}


def available() -> bool:
    roles = model_roles()
    if roles:
        return all(os.path.exists(p) for v in roles.values() for p in v)
    return bool(model_paths())


def _model_outputs(model, cqt, mel, dev, tta_shifts, mert=None):
    from bassnet.train import infer_full
    acc = None
    for k in tta_shifts:
        c = np.roll(cqt, k * BINS_PER_SEMI, axis=0)
        if k > 0:
            c[:k * BINS_PER_SEMI] = 0
        elif k < 0:
            c[k * BINS_PER_SEMI:] = 0
        # MERT features follow the unshifted audio: only the k=0 pass gets them (as in training)
        outs = infer_full(model, np.ascontiguousarray(c), mel, dev, mert=mert if k == 0 else None)
        fr = outs[0]
        if k:
            # shift pitch classes back (class 0 = rest stays)
            p = fr[:, 1:]
            p = np.roll(p, -k, axis=1)
            if k > 0:
                p[:, -k:] = 0
            else:
                p[:, :-k] = 0
            fr = np.concatenate([fr[:, :1], p], 1)
            fr /= fr.sum(1, keepdims=True)
        outs = [fr] + list(outs[1:])
        acc = outs if acc is None else [a + o for a, o in zip(acc, outs)]
    return [a / len(tta_shifts) for a in acc]


def posteriors(cqt, mel, dev="cuda", tta_shifts=(0, -1, 1), with_tech=False, mert=None):
    """frame/onset/dead/beat/downbeat posteriors, each averaged over its role's models x pitch-shift TTA.
    with_tech: also return the technique posteriors (T, K) averaged over models that have a technique head
    (role "tech" in models.json, else every model with the head), or None."""
    import torch
    from bassnet.model import load_checkpoint
    cqt = cqt / (np.percentile(cqt, 99.5) + 1e-3)
    mel = mel / (np.percentile(mel, 99.5) + 1e-3)
    roles = model_roles()
    paths = model_paths()
    if roles is None:
        roles = {"notes": paths, "beats": paths, "downbeats": paths}
    # heads: 0-2 frame/onset/dead (notes), 3 beat, 4 downbeat
    head_role = ["notes", "notes", "notes", "beats", "downbeats"]
    sums, counts = [None] * 5, [0] * 5
    tech_sum, tech_n = None, 0
    tech_role = roles.get("tech")
    for p in sorted({p for v in roles.values() for p in v}):
        model = load_checkpoint(p, dev)
        outs = _model_outputs(model, cqt, mel, dev, tta_shifts, mert=mert)
        for h in range(5):
            if p in roles[head_role[h]]:
                sums[h] = outs[h] if sums[h] is None else sums[h] + outs[h]
                counts[h] += 1
        if with_tech and len(outs) > 5 and (tech_role is None or p in tech_role):
            tech_sum = outs[5] if tech_sum is None else tech_sum + outs[5]
            tech_n += 1
        del model
        if dev == "cuda":
            torch.cuda.empty_cache()
    res = [s / c for s, c in zip(sums, counts)]
    if with_tech:
        res.append(tech_sum / tech_n if tech_n else None)
    return res


def choose_tuning(notes, prefer_5: bool = False) -> List[int]:
    lo = min(n["midi"] for n in notes) if notes else 28
    if prefer_5 or lo <= 24:
        return [23, 28, 33, 38, 43]
    if lo == 25:
        return [25, 32, 37, 42]          # C# standard (half-step down, dropped)
    if lo in (26, 27):
        # Eb standard if the song never needs E-string pitches that only a dropped string explains
        return [26, 33, 38, 43] if lo == 26 else [27, 32, 37, 42]
    return [28, 33, 38, 43]


def transcribe_stem(bass_wav: str, mix_audio: str, dev: str = "cuda", progress: Callable = print,
                    a_down=None) -> Dict:
    import librosa
    y, _ = librosa.load(bass_wav, sr=SR, mono=True)
    ym, _ = librosa.load(mix_audio, sr=SR, mono=True)
    cqt = compute_cqt(y)
    mel = compute_mel(ym)
    T = cqt.shape[1]
    mel = mel[:, :T] if mel.shape[1] >= T else np.pad(mel, ((0, 0), (0, T - mel.shape[1])))
    progress("__PROGRESS__: 音符识别 70%")
    fr, on, de, be, do, te = posteriors(cqt, mel, dev, with_tech=True)
    notes, beats, downs, meter, do = decode_song(fr, on, de, be, do, cqt, a_down)
    if te is not None:
        attach_techniques(notes, te)
    import hashlib
    with open(mix_audio, "rb") as f:
        audio_md5 = hashlib.md5(f.read()).hexdigest()[:16]     # same key as the library caches (build_stems)
    return {"notes": notes, "beats": beats, "downbeats": downs, "meter": meter,
            "post": (fr, on, de, be, do, te), "cqt": cqt, "mel": mel, "audio_md5": audio_md5}


ALLIN1_W = 0.3        # weight of allin1's downbeat activation in our downbeat posterior (layer1_eval, 55 songs:
                      # downbeat F1 0.948 -> 0.952, first bar right 0.891 -> 0.927)
# Bar phase is a song-level property (2026-09-30, bassnet/phase_audit.py, 278 songs): a riff repeated 20 times is
# one opinion about where the bar starts, not 20 (decode.repeat_weights), and a phase jump / meter change (an odd
# bar) needs more evidence. Henceforth: 54 of 152 bar lines two beats off -> all right; library bar lines right
# 96.5% -> 97.1%, songs with every bar line right 164 -> 198.
BEAT_REDUNDANCY = 0.5
BEAT_HMM = {"p_jump": 12.0, "p_meter": 12.0}
# With the repeat weighting the allin1 downbeat fusion no longer helps (song_eval, 55 songs: bar lines 0.975 without
# vs 0.973 with, first bar 0.927 vs 0.909), so it is off; the venv / checkpoint stay for later experiments.
USE_ALLIN1 = False


ONSET_RESCORE = float(os.environ.get("BASSNET_ONSET_RESCORE", 0.5))   # bassnet/onset_rescore.py: learnt second opinion on every candidate note start (55 val/test
                      # songs F1 0.834 -> 0.839, 223 training songs cross-fold 0.807 -> 0.812; 143 songs up, 52 down)


def decode_song(fr, on, de, be, do, cqt, a_down=None):
    """Posteriors -> notes + beats as in production (the app and song_eval share this)."""
    notes = decode_notes(fr, on, de)
    beats, downs, meter, do = song_beats(be, do, notes, a_down)
    if ONSET_RESCORE and cqt is not None:
        try:
            from bassnet.onset_rescore import onsets_for, MODEL as RS_MODEL
            if os.path.exists(RS_MODEL):
                notes = decode_notes(fr, on, de, onsets=onsets_for(fr, on, de, cqt, beats, ONSET_RESCORE))
        except Exception as e:
            print(f"[onset_rescore] skipped: {e}", file=sys.stderr, flush=True)
    return notes, beats, downs, meter, do


def song_beats(be, do, notes, a_down=None, redundancy=None, hmm_kw=None, allin1_w=None):
    """Beats, downbeats, prevailing meter and the downbeat posterior actually used (production and evaluation)."""
    if a_down is not None:
        do = fuse_downbeat(do, a_down, ALLIN1_W if allin1_w is None else allin1_w)
    red = BEAT_REDUNDANCY if redundancy is None else redundancy
    hk = BEAT_HMM if hmm_kw is None else hmm_kw
    if hmm_kw is None and METER_PROBE:
        # songs that really change meter (3/4 sections: 15% of the tabs) keep the cheap meter switch
        b0, d0, _ = decode_beats(be, do)
        if longest_run(b0, d0, 3) >= METER_PROBE:
            hk = dict(hk, p_meter=4.0)
    beats, downs, meter = decode_beats(be, do, hmm_kw=hk, notes=notes if red else None, redundancy=red)
    return beats, downs, meter, do


METER_PROBE = 8       # bars of 3 beats in a row in a plain first pass that mark a song with real 3/4 sections


def longest_run(beats, downs, length):
    """Longest run of consecutive bars with `length` beats."""
    bt = np.asarray(beats)
    if len(bt) < 2 or len(downs) < 2:
        return 0
    idx = [int(np.argmin(np.abs(bt - d))) for d in downs]
    best = run = 0
    for a, b in zip(idx[:-1], idx[1:]):
        run = run + 1 if b - a == length else 0
        best = max(best, run)
    return best



ALLIN1_PY = os.path.join("E:" + os.sep, "BassStation", "cache", "venv_allin1", "Scripts", "python.exe")
ALLIN1_CKPT = os.path.join(MODEL_DIR, "allin1_ft_fold0.pt")


def allin1_downbeat(stems4_dir: str):
    """allin1 fine-tuned on the purchased tabs (bassnet/lab/allin1_finetune.py), run in its own venv on the 4 stems;
    -> downbeat activation at 100 fps, or None when the venv / checkpoint / stems are missing or it fails."""
    import subprocess
    import tempfile
    if not (os.path.exists(ALLIN1_PY) and os.path.exists(ALLIN1_CKPT)):
        return None
    fd, out = tempfile.mkstemp(suffix=".npz")
    os.close(fd)
    try:
        env = dict(os.environ, HF_HUB_OFFLINE="1", PYTHONIOENCODING="utf-8")
        p = subprocess.run([ALLIN1_PY, "-m", "bassnet.allin1_run", "--stems4", stems4_dir, "--ckpt", ALLIN1_CKPT,
                            "--out", out], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=900)
        if p.returncode != 0:
            print(f"[allin1] skipped: {p.stderr[-300:]}", file=sys.stderr, flush=True)
            return None
        return np.load(out)["downbeat"].astype(np.float32)
    except Exception as e:
        print(f"[allin1] skipped: {e}", file=sys.stderr, flush=True)
        return None
    finally:
        if os.path.exists(out):
            os.remove(out)


def fuse_downbeat(do, a_down, w=ALLIN1_W, a_fps=100.0):
    """Our downbeat posterior (FPS) mixed with allin1's downbeat activation (a_fps) resampled onto our frames."""
    from bassnet.decode import FPS
    t = np.arange(len(do)) / FPS
    ad = np.interp(t, np.arange(len(a_down)) / a_fps, np.asarray(a_down, np.float32))
    return (1 - w) * do + w * ad


SECTION_CKPT = os.path.join(MODEL_DIR, "section.pt")
SONG_DOCS = os.path.join(MODEL_DIR, "song_docs")
TIDY, TIDY_TAU = True, 0.05
SYNC_TOL = 0.035     # s: sparse sync points (a few fixed written tempos), see tempo_map


def song_sections(res: Dict, qs) -> Dict:
    """{played bar: (letter, text)} rehearsal marks in the purchased tabs' style (Intro, A 1, B 1, C 1, ... Outro)."""
    if "cqt" not in res or not os.path.exists(SECTION_CKPT):
        return {}
    try:
        import torch
        from bassnet import section_model
        model = section_model.make_model()
        model.load_state_dict(torch.load(SECTION_CKPT, map_location="cpu")["model"])
        model.eval()
        fr, on = res["post"][0], res["post"][1]
        spans = [(qs.bar_time(b), qs.bar_time(b + 1)) for b in range(qs.n_bars)]
        X = section_model.bar_features(spans, res["mel"], res["cqt"], on, fr[:, 0], res["notes"])
        marks, seen = {}, {}
        for start, cls in section_model.predict_sections(model, X):
            if cls in ("A", "B", "C", "D"):
                seen[cls] = seen.get(cls, 0) + 1
                marks[start] = (cls, str(seen[cls]))
            else:
                marks[start] = ("", {"intro": "Intro", "outro": "Outro", "inter": "Interlude"}[cls])
        return marks
    except Exception as e:
        print(f"[sections] skipped: {e}", file=sys.stderr, flush=True)
        return {}


TECH_THR = [0.5] * 7     # per technique (model.TECH_NAMES order); tuned values: cache/bassnet/tech_thr.json
_thr_file = os.path.join(MODEL_DIR, "tech_thr.json")
if os.path.exists(_thr_file):
    import json as _json
    TECH_THR = _json.load(open(_thr_file))


def attach_techniques(notes, te, thr=None):
    """Technique posteriors around each note onset -> GP flags on the notes (slide / hammer-on / slap / pop)."""
    from bassnet.decode import FPS
    thr = thr or TECH_THR
    T = len(te)
    for i, n in enumerate(notes):
        a = int(round(n["time"] * FPS))
        p = te[max(0, a - 1):min(T, a + 3)].max(0) if a < T else np.zeros(te.shape[1])
        prev = notes[i - 1] if i > 0 else None
        n.setdefault("slide", 0)
        if prev is not None and p[0] >= thr[0] and not prev.get("dead"):
            prev["slide"] = (prev.get("slide", 0) & ~12) | 2        # legato slide into this note
        if p[1] >= thr[1] and p[1] >= p[2]:
            n["slide"] |= 4
        elif p[2] >= thr[2]:
            n["slide"] |= 8
        if p[3] >= thr[3]:
            n["slide"] |= 16
        if prev is not None and p[4] >= thr[4] and not n.get("dead") and not prev.get("dead"):
            prev["hopo_origin"] = True
            n["hopo_dest"] = True
        n["slap"] = bool(p[5] >= thr[5])
        n["pop"] = bool(p[6] >= thr[6] and p[6] > p[5])
        n["tech_p"] = [float(x) for x in p]


STD4 = [28, 33, 38, 43]


def four_string_arrangement(qs, low=28, high=67):
    """4-string version of a 5-string transcription, as hand-made 4-string tabs do it: every bar that contains a
    note below E1 is played an octave up as a whole (keeps the line's shape), unless that would leave the neck."""
    import copy
    from bassnet.quantize import TPB
    q = copy.deepcopy(qs)
    edges = [b * TPB for b in q.bar_starts]

    def bar_of(tick):
        import bisect
        return max(0, bisect.bisect_right(edges, tick) - 1)
    by_bar = {}
    for n in q.notes:
        by_bar.setdefault(bar_of(n.tick), []).append(n)
    for ns in by_bar.values():
        if any(n.midi < low and not n.dead for n in ns):
            if all(n.midi + 12 <= high for n in ns):
                for n in ns:
                    n.midi += 12
            else:
                for n in ns:
                    if n.midi < low:
                        n.midi += 12
    return q


def notes_meta(qs, res) -> List[Dict]:
    """Per written note (qs.notes order): recording time/end, confidence, top pitch candidates at the onset."""
    from bassnet.quantize import beat_time, TPB
    from bassnet.decode import FPS
    src = sorted(res["notes"], key=lambda n: n["time"])
    st = np.array([n["time"] for n in src]) if src else np.zeros(0)
    fr = res["post"][0] if res.get("post") is not None else None
    out = []
    for q in qs.notes:
        t = float(beat_time(qs.beats, qs.bar0 + q.tick / TPB))
        m = {"time": t, "end": float(beat_time(qs.beats, qs.bar0 + (q.tick + q.dur) / TPB)), "conf": None, "cand": []}
        if len(st):
            j = int(np.argmin(np.abs(st - t)))
            if abs(st[j] - t) < 0.08:
                n = src[j]
                m.update(time=float(n["time"]), end=float(n["end"]), conf=float(min(n.get("conf", 1.0),
                                                                                      n.get("on_peak", 1.0))))
        if fr is not None:
            a = int(round(m["time"] * FPS))
            if 0 <= a < len(fr):
                from bassnet.model import PITCH_LO
                p = fr[a:a + 3].mean(0)[1:]                     # column 0 = rest
                top = np.argsort(p)[::-1][:3]
                m["cand"] = [[int(PITCH_LO + k), float(p[k])] for k in top]    # [midi, probability]
        out.append(m)
    return out


def model_info(res) -> Dict:
    import hashlib
    mj = os.path.join(MODEL_DIR, "models.json")
    h = hashlib.md5(open(mj, "rb").read()).hexdigest()[:12] if os.path.exists(mj) else ""
    return {"models_json": h, "roles": model_roles(), "audio_md5": res.get("audio_md5", ""),
            "code": "2026-09-30"}


def build_gp(res: Dict, out_gp: str, title: str, artist: str, subtitle: str, backing_audio: Optional[str],
             key=(0, "Major"), prefer_5: bool = False, check_audio: Optional[str] = None,
             chords: Optional[List[Dict]] = None, key_runs: Optional[List] = None) -> Dict:
    """Writes the practice GP (backing_audio) and, with check_audio, the 核对版 companion (same score)."""
    notes = res["notes"]
    for n in notes:
        n["low_conf"] = min(n.get("conf", 1.0), n.get("on_peak", 1.0)) < LOW_CONF
    qs = quantize(notes, res["beats"], res["downbeats"], res["meter"])
    reg_stats = {}
    if TIDY and res.get("post") is not None:
        # repeated phrases get one spelling unless the audio clearly says otherwise (bassnet/regularize.py)
        from bassnet.regularize import regularize
        qs, reg_stats = regularize(qs, res["post"][0], res["post"][1], tau=TIDY_TAU)
    tuning = choose_tuning(notes, prefer_5)
    from bassnet.quantize import beat_time, TPB
    assign_frets(qs.notes, tuning, times=[beat_time(qs.beats, qs.bar0 + n.tick / TPB) for n in qs.notes])
    sections = song_sections(res, qs)
    bar_keys = {}
    if key_runs:
        # key changes inside the song, snapped to section starts (bassnet/key_local.py)
        from bassnet.key_local import section_keys
        bar_keys = {int(b): (int(k), key[1]) for b, k in section_keys(key_runs, sorted(sections), qs.bar_time, qs.n_bars,
                                                           key[0]).items()}
    layout = {"sections": sections, "repeats": True, "sync_tol": SYNC_TOL, "bar_keys": bar_keys}
    try:
        # the song document (architecture v2): what Layer 1 decided, kept for the later layers and for debugging
        from bassnet.song_doc import from_pipeline
        doc = from_pipeline(qs, sections, key=key, chords=chords)
        for sec in doc.sections:
            if sec.bar in bar_keys:
                sec.key = int(bar_keys[sec.bar][0])
        os.makedirs(SONG_DOCS, exist_ok=True)
        doc.to_json(os.path.join(SONG_DOCS, os.path.splitext(os.path.basename(out_gp))[0] + ".json"))
    except Exception as e:
        print(f"[song_doc] skipped: {e}", file=sys.stderr, flush=True)
    write_gp(qs, out_gp, title, artist, backing_audio, tuning, key=key, subtitle=subtitle, **layout)
    written = [out_gp]
    try:
        # our own score format (backend/score_format.py, used by the in-app viewer / editor): same score plus the
        # recording time, confidence and pitch candidates of every note, so user edits become training labels
        from score_format import write_score
        write_score(out_gp, qs, doc=locals().get("doc"), tuning=tuning, key=key, bar_keys=bar_keys,
                    sections=sections, notes_meta=notes_meta(qs, res), model_info=model_info(res),
                    title=title, artist=artist)
    except Exception as e:
        print(f"[score_format] skipped: {e}", file=sys.stderr, flush=True)
    check_gp = ""
    if check_audio and os.path.exists(check_audio):
        base, ext = os.path.splitext(out_gp)
        check_gp = f"{base} [核对版]{ext}"
        write_gp(qs, check_gp, title, artist, check_audio, tuning, key=key, subtitle=subtitle, **layout)
        written.append(check_gp)
    four_gp = ""
    if len(tuning) == 5:
        # the recording goes below the 4-string range: add a 4-string arrangement (bars an octave up)
        base, ext = os.path.splitext(out_gp)
        four_gp = f"{base} [4弦版]{ext}"
        qs4 = four_string_arrangement(qs)
        assign_frets(qs4.notes, STD4, times=[beat_time(qs4.beats, qs4.bar0 + n.tick / TPB) for n in qs4.notes])
        write_gp(qs4, four_gp, title, artist, backing_audio, STD4, key=key, subtitle=subtitle, **layout)
        written.append(four_gp)
    lint_problems = -1
    try:
        from bassnet.gp_lint import lint
        lint_problems = 0
        for f in written:
            problems = lint(f)
            lint_problems += len(problems)
            for k in problems:
                print(f"[gp_lint] {os.path.basename(f)}: {k}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[gp_lint] skipped: {e}", file=sys.stderr, flush=True)
    bar_q = qs.meter * 1.0
    tempo = 60.0 * bar_q / max(1e-3, (qs.bar_time(qs.n_bars) - qs.bar_time(0)) / qs.n_bars)
    return {"tempo": tempo, "tuning": tuning, "n_bars": qs.n_bars, "meter": qs.meter,
            "note_count": len(qs.notes), "is_5string": len(tuning) == 5, "lint_problems": lint_problems,
            "check_gp": check_gp, "four_string_gp": four_gp, "sections": len(sections),
            "tidied_notes": reg_stats.get("changed_notes", 0), "bar0_time": float(qs.bar_time(0))}
