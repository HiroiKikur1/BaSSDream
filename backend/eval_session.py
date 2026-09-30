"""Builds the playback file for an in-app evaluation take: 4-beat count-in, bass-less backing, metronome.

The backing is the GP's embedded audio minus its separated bass stem (cache/bassnet/stems, or a
one-off separation cached under cache/eval_session/stems). Clicks are pure tones above the
evaluator's analysis band (onset flux stops at 2 kHz), so metronome bleed into a microphone does
not read as plucks.

CLI (Python311): eval_session.py <gp_path> [--range START END] [--rate R] | --sections
  -> JSON on the last stdout line (times in session seconds, i.e. stretched by 1 / rate):
  wav       44.1 kHz stereo session file
  lead_s    backing-audio time at session time 0, / rate (negative = silence before the audio starts)
  count_in  seconds of count-in before bar 1
"""
import hashlib
import json
import os
import subprocess
import sys
import zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bassnet.gpif_parser import parse_gp  # noqa: E402
from performance_evaluator import FFMPEG_EXE, score_time_map  # noqa: E402

SR = 44100
ROOT = r"E:\BassStation\cache\eval_session"
STEMS = [r"E:\BassStation\cache\bassnet\stems", os.path.join(ROOT, "stems")]
MODELS = r"E:\BassStation\cache\models"
VERSION = 4
COUNT_IN = 4
CLICK_HZ = (3400.0, 2800.0)     # accent, normal
CLICK_GAIN = 0.32
TAIL_S = 2.0


def _ffmpeg_f32(src: str, sr: int, ch: int) -> np.ndarray:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    raw = subprocess.run([FFMPEG_EXE, "-v", "error", "-i", src, "-ar", str(sr), "-ac", str(ch), "-f", "f32le", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, creationflags=flags).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, ch).copy()


def _separate(src: str, md5: str) -> str:
    """One-off BS-Roformer bass separation for audio that is not in the training cache."""
    from audio_separator.separator import Separator
    os.environ["PATH"] = os.path.dirname(FFMPEG_EXE) + os.pathsep + os.environ.get("PATH", "")
    tmp = os.path.join(ROOT, "_tmp")
    os.makedirs(tmp, exist_ok=True)
    sep = Separator(model_file_dir=MODELS, output_dir=tmp, output_format="WAV", use_soundfile=True,
                    log_level=40, use_autocast=True)
    sep.load_model("BS-Roformer-SW.ckpt")
    wav = os.path.join(tmp, md5 + ".wav")
    subprocess.run([FFMPEG_EXE, "-y", "-v", "error", "-i", src, "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", wav],
                   check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    outs = sep.separate(wav)
    bass = [o for o in outs if "(bass)" in os.path.basename(o).lower()]
    if not bass:
        raise RuntimeError("no bass stem")
    bp = bass[0] if os.path.isabs(bass[0]) else os.path.join(tmp, bass[0])
    out = os.path.join(STEMS[1], md5 + ".flac")
    os.makedirs(STEMS[1], exist_ok=True)
    subprocess.run([FFMPEG_EXE, "-y", "-v", "error", "-i", bp, "-ar", "22050", "-ac", "1", out], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    for f in os.listdir(tmp):
        try:
            os.remove(os.path.join(tmp, f))
        except OSError:
            pass
    return out


def _remove_bass(mix: np.ndarray, stem_path: str) -> np.ndarray:
    """mix (N, 2) minus the mono 22.05 kHz stem, with the stem's lag and gain fitted to the mix."""
    import soundfile as sf
    from scipy.signal import butter, resample_poly, sosfiltfilt
    stem, sr = sf.read(stem_path, dtype="float32", always_2d=True)
    stem = resample_poly(stem[:, 0], SR, sr).astype(np.float32)
    n = min(len(stem), len(mix))
    stem = np.pad(stem[:n], (0, len(mix) - n))
    mono = mix.mean(axis=1)
    # fit on the loudest 60 s of low band, where the bass dominates
    sos = butter(4, 250, "low", fs=SR, output="sos")
    seg = slice(0, min(len(mono), 60 * SR))
    lo_m = sosfiltfilt(sos, mono[seg])
    lo_s = sosfiltfilt(sos, stem[seg])
    best = (0, 1.0, -np.inf)
    for lag in range(-24, 25):
        s = np.roll(lo_s, lag)
        g = float(np.dot(lo_m, s) / (np.dot(s, s) + 1e-9))
        red = np.dot(lo_m, lo_m) - np.sum((lo_m - g * s) ** 2)
        if red > best[2]:
            best = (lag, g, red)
    lag, g, _ = best
    g = float(np.clip(g, 0.7, 1.3))
    return mix - g * np.roll(stem, lag)[:, None]


def _click(freq: float, gain: float) -> np.ndarray:
    t = np.arange(int(0.045 * SR)) / SR
    env = np.exp(-t / 0.012)
    rise = int(0.002 * SR)
    env[:rise] *= 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, rise))
    return (gain * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _beats(info, qmap):
    """(time, accent, playback bar) for every beat in playback order, plus each bar's beat length (s)."""
    out, beat_len = [], []
    for i, (bi, oc, q0, qlen) in enumerate(info.bars):
        num, den = info.time_sigs[bi]
        beat_q = 1.5 if den == 8 and num % 3 == 0 and num >= 6 else 4.0 / den
        k = 0
        while k * beat_q < float(qlen) - 1e-6:
            q = float(q0) + k * beat_q
            out.append((float(qmap(q)), k == 0, i))
            k += 1
        beat_len.append(float(qmap(float(q0) + beat_q) - qmap(float(q0))))
    return out, beat_len


def _gp_key(gp_path: str) -> str:
    st = os.stat(gp_path)
    return hashlib.md5(f"{os.path.abspath(gp_path)}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:16]


def _backing(gp_path: str, info):
    """(N, 2) float32 embedded audio minus its bass, and whether the bass was removed; (None, False) without
    audio. The bass-less backing is cached, so every section / tempo take after the first starts at once."""
    if not info.audio_asset:
        return None, False
    import soundfile as sf
    cached = os.path.join(ROOT, _gp_key(gp_path) + "_nobass.flac")
    if os.path.exists(cached):
        data, _ = sf.read(cached, dtype="float32", always_2d=True)
        return data, True
    tmp = os.path.join(ROOT, _gp_key(gp_path) + "_src" + os.path.splitext(info.audio_asset)[1])
    with zipfile.ZipFile(gp_path) as z:
        data = z.read(info.audio_asset)
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        backing = _ffmpeg_f32(tmp, SR, 2)
        md5 = hashlib.md5(data).hexdigest()[:16]
        stem = next((p for p in (os.path.join(d, md5 + ".flac") for d in STEMS) if os.path.exists(p)), None)
        if stem is None:
            try:
                stem = _separate(tmp, md5)
            except Exception as e:     # separation is an improvement, not a requirement
                print(f"separation failed: {e}", file=sys.stderr)
        if stem is None:
            return backing, False
        backing = _remove_bass(backing, stem)
        sf.write(cached, np.clip(backing, -1.0, 1.0), SR, format="FLAC", subtype="PCM_16")
        return backing, True
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _stretch(x: np.ndarray, rate: float) -> np.ndarray:
    """Tempo change at constant pitch (ffmpeg atempo, WSOLA: drum attacks stay tight)."""
    import soundfile as sf
    src = os.path.join(ROOT, f"_stretch_{os.getpid()}.wav")
    sf.write(src, x, SR, subtype="FLOAT")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        raw = subprocess.run([FFMPEG_EXE, "-v", "error", "-i", src, "-filter:a", f"atempo={rate:.4f}",
                              "-ar", str(SR), "-ac", "2", "-f", "f32le", "-"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, creationflags=flags).stdout
    finally:
        try:
            os.remove(src)
        except OSError:
            pass
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def list_sections(gp_path: str) -> dict:
    """Practice ranges for the take sheet: the report's sections that hold enough notes to be scored."""
    from eval_report import song_sections
    from performance_evaluator import MIN_RANGE_NOTES
    info = parse_gp(gp_path)
    qmap = score_time_map(gp_path, info)
    starts = [float(b[2]) for b in info.bars] + [float(info.bars[-1][2] + info.bars[-1][3])]
    qs = np.array(sorted(float(n.qpos) for n in info.notes if not n.grace))
    out = []
    for name, a, e in song_sections(gp_path):
        n = int(np.sum((qs >= starts[a] - 1e-6) & (qs < starts[e] - 1e-6)))
        if n < MIN_RANGE_NOTES or e - a >= len(info.bars):
            continue
        out.append({"name": name, "start": a, "end": e, "bar": info.bars[a][0] + 1, "notes": n,
                    "seconds": round(float(qmap(starts[e]) - qmap(starts[a])), 1)})
    return {"success": True, "bars": len(info.bars), "sections": out}


def build_session(gp_path: str, start: int = None, end: int = None, rate: float = 1.0) -> dict:
    """Session for the whole song, or for playback bars [start, end), at a tempo factor (0.5-1).

    Times in the result are session (stretched) seconds: a note at backing time t is played at
    recording time t / rate + (RecordOffset - lead_s).
    """
    rate = float(min(1.0, max(0.5, rate)))
    key = hashlib.md5(f"{_gp_key(gp_path)}|{start}|{end}|{rate:.3f}|{VERSION}".encode()).hexdigest()[:16]
    os.makedirs(ROOT, exist_ok=True)
    out_wav = os.path.join(ROOT, key + ".wav")
    out_json = os.path.join(ROOT, key + ".json")
    if os.path.exists(out_wav) and os.path.exists(out_json):
        with open(out_json, encoding="utf-8") as f:
            return json.load(f)

    info = parse_gp(gp_path)
    qmap = score_time_map(gp_path, info)
    nb = len(info.bars)
    a = max(0, min(nb - 1, start or 0))
    e = max(a + 1, min(nb, nb if end is None else end))
    beats, beat_len = _beats(info, qmap)
    beats = [(t, acc) for t, acc, i in beats if a <= i < e]
    if not beats:
        raise RuntimeError("no bars")
    beat_s = beat_len[a] or 0.5
    bar1 = beats[0][0]
    lead = bar1 - COUNT_IN * beat_s
    end_score = float(qmap(float(info.bars[e - 1][2] + info.bars[e - 1][3]))) + TAIL_S

    backing, nobass = _backing(gp_path, info)
    audio_end = len(backing) / SR if backing is not None else 0.0
    if e < nb:
        end_t = end_score
    else:
        end_t = max(end_score, audio_end) if backing is None else min(audio_end, max(end_score, bar1 + 5))
    n = int((end_t - lead) * SR)
    mix = np.zeros((n, 2), np.float32)
    if backing is not None:
        a0 = int(round(lead * SR))           # backing sample at session sample 0
        src0, dst0 = max(0, a0), max(0, -a0)
        m = min(len(backing) - src0, n - dst0)
        if m > 0:
            mix[dst0:dst0 + m] = backing[src0:src0 + m]
        peak = float(np.max(np.abs(mix))) or 1.0
        mix *= min(1.0, 0.8 / peak)
        if rate != 1.0:
            mix = _stretch(mix, rate)
    elif rate != 1.0:
        mix = np.zeros((int(n / rate), 2), np.float32)
    n = len(mix)

    clicks = {True: _click(CLICK_HZ[0], CLICK_GAIN), False: _click(CLICK_HZ[1], CLICK_GAIN * 0.8)}
    times = [(bar1 - (COUNT_IN - k) * beat_s, k == 0) for k in range(COUNT_IN)] + beats
    for t, acc in times:
        i = int(round((t - lead) / rate * SR))
        c = clicks[acc]
        if 0 <= i < n:
            m = min(len(c), n - i)
            mix[i:i + m] += c[:m, None]
    mix = np.clip(mix, -1.0, 1.0)

    import soundfile as sf
    sf.write(out_wav, mix, SR, subtype="PCM_16")
    meta = {"success": True, "wav": out_wav, "lead_s": round(lead / rate, 4), "count_in": round(COUNT_IN * beat_s / rate, 4),
            "beat_s": round(beat_s / rate, 4), "duration": round(n / SR, 3), "has_audio": backing is not None,
            "nobass": nobass, "start": a, "end": e, "rate": rate}
    # a failed separation is retried next time instead of being cached
    if nobass or backing is None:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    return meta


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("gp")
    ap.add_argument("--sections", action="store_true", help="list practice ranges instead of building a session")
    ap.add_argument("--range", nargs=2, type=int, metavar=("START", "END"), help="playback bars [START, END)")
    ap.add_argument("--rate", type=float, default=1.0)
    args = ap.parse_args()
    try:
        if args.sections:
            res = list_sections(args.gp)
        else:
            st, en = args.range if args.range else (None, None)
            res = build_session(args.gp, st, en, args.rate)
    except Exception as e:
        res = {"success": False, "error": "伴奏准备失败", "detail": str(e)}
    print(json.dumps(res, ensure_ascii=False))
