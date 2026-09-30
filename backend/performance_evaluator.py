"""Score-referenced bass performance evaluation.

The recording is aligned to the bass track of the song's GP file (pitch-aware
cross-correlation), then every score note is judged against the detected
onsets / pitch energy of the recording, BanG Dream! style:
PERFECT / GREAT / GOOD / BAD / MISS.

The absolute offset between recording and song is unknown (the recording
starts whenever the user pressed record), so timing is measured relative to
the median offset: a constant delay is free, rushing / dragging is not.

Needs numpy + librosa (Python311 environment).
CLI: performance_evaluator.py <audio_path> <song_id> <gp_path> [--lag-hint S] [--range START END] [--rate R]
                               [--label NAME] [--no-save]
     -> JSON on the last stdout line; the per-note report goes to the file in report_path
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bassnet.gpif_parser import parse_gp  # noqa: E402

FFMPEG_EXE = r"E:\BassStation\tools\Ultimate Vocal Remover\ffmpeg.exe"
DB_PATH = r"E:\BassStation\backend\data.db"
BASSNET_CACHE = r"E:\BassStation\cache\bassnet"
REPORT_DIR = r"E:\BassStation\cache\evaluations\reports"

SR = 22050
HOP = 256
FPS = SR / HOP
MIDI_LO = 21                    # A0: lowest CQT semitone
N_SEMI = 88
# pitch salience = weighted harmonic sum; the upper harmonics carry the timing because the
# constant-Q window at the fundamental of a low E is longer than an eighth note
HARMONICS = ((0, 0.5), (12, 1.0), (19, 1.0), (24, 0.8))
BASS_LO, BASS_HI = 23, 64
TONAL_MIN = 1.35                # pitch salience over the register median that counts as a tone
LEVEL_HI = 76                   # level gate band: bass fundamentals + 2nd harmonics
QUIET_OF_LOUD = 0.05            # pass 1: plucks reach 5 % (-26 dB) of the recording's loud frames
QUIET_OF_HITS = 0.10            # pass 2: ... and 10 % (-20 dB) of a typical matched pluck (dead notes: half)

# Judgment windows on |timing error| (seconds); anything matched beyond GOOD is BAD.
PERFECT_WIN, GREAT_WIN, GOOD_WIN = 0.040, 0.075, 0.115
MAX_SEARCH = 0.160              # widest onset search radius around a score note
MIN_SEARCH = 0.100
WEIGHT = {"PERFECT": 1.0, "GREAT": 0.8, "GOOD": 0.5, "BAD": 0.2, "MISS": 0.0}
MIN_NOTES = 16
FULL_COVERAGE = 0.9             # a take counts toward a best only if it covers this share of its target notes
MIN_RANGE_NOTES = 8             # section takes: the playback start is known, so fewer notes still align
HINT_WIN = 0.6                  # device latency uncertainty around a known playback start (s)
CAND_DELTA = 0.02               # permissive onset picking for matching
STRICT_DELTA = 0.10             # confident onsets for extra-note detection


# ---------------------------------------------------------------- audio

def _decode(path: str) -> np.ndarray:
    import librosa
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.run([FFMPEG_EXE, "-y", "-v", "error", "-i", path, "-ar", str(SR), "-ac", "1", wav],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, creationflags=flags)
        y, _ = librosa.load(wav, sr=SR, mono=True)
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
    return y


def _features(y: np.ndarray):
    """Returns (En, H, env, lvl): per-frame normalised semitone energy (88, T), pitch salience (88, T),
    onset strength (T,) and absolute bass-register level (T,)."""
    import librosa
    C = librosa.cqt(y, sr=SR, hop_length=HOP, fmin=librosa.midi_to_hz(MIDI_LO), n_bins=N_SEMI, bins_per_octave=12)
    E = np.log1p(np.abs(C) * 20.0).astype(np.float32)
    peak = E.max(axis=0)
    # floor keeps silent frames from being blown up to full scale by the per-frame normalisation
    En = E / (np.maximum(peak, 0.3 * np.percentile(peak, 95)) + 1e-3)
    H = np.zeros_like(E)
    for off, w in HARMONICS:
        H[:N_SEMI - off] += w * E[off:]
    # onsets from a short-window mel spectrum: CQT onsets are smeared by up to ~100 ms in the bass register
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP, n_fft=1024, fmax=2000, lag=1, max_size=3)
    env = env / (np.percentile(env, 99.5) + 1e-6)
    # the log-mel flux above is level-blind (hiss has onsets too); plucks are told apart by this level
    lvl = np.abs(C[BASS_LO - MIDI_LO:LEVEL_HI - MIDI_LO]).max(axis=0).astype(np.float32)
    return En, H, env, lvl


def _loud(lvl, f, floor, k=4):
    """Whether the bass register reaches floor within k frames after frame f."""
    return bool(lvl[f:f + k].max(initial=0.0) >= floor)


def _onsets(env, delta):
    import librosa
    # offline peak picking (looks ahead too), so a peak is never reported on its rising edge
    return librosa.onset.onset_detect(onset_envelope=env, sr=SR, hop_length=HOP, units="frames",
                                      normalize=False, delta=delta, pre_max=2, post_max=3, wait=2)


# ---------------------------------------------------------------- alignment

def _cached_audio_times(gp_path: str):
    """Score position -> real backing-audio time, from bassnet's re-aligned training labels.

    GP sync points drift up to ~100 ms from the actual audio, which would turn a player who
    follows the backing track exactly into GOOD / BAD judgments. Returns (qpos[], time[]) or None.
    """
    try:
        with open(os.path.join(BASSNET_CACHE, "index.json"), encoding="utf-8") as f:
            index = json.load(f)
        key = os.path.normcase(os.path.abspath(gp_path))
        md5 = next((v["md5"] for g, v in index.items() if os.path.normcase(os.path.abspath(g)) == key), None)
        if md5 is None:
            return None
        with open(os.path.join(BASSNET_CACHE, "feats", md5 + ".json"), encoding="utf-8") as f:
            cached = json.load(f)["notes"]
        pts = sorted({(float(n["qpos"]), float(n["time"])) for n in cached if not n.get("grace")})
        q = np.array([a for a, _ in pts])
        t = np.array([b for _, b in pts])
        return (q, t) if len(q) >= MIN_NOTES and np.all(np.diff(q) > 0) else None
    except (OSError, ValueError, KeyError):
        return None


def score_time_map(gp_path: str, info):
    """q (quarter notes, playback order) -> backing-audio seconds, vectorised.

    Uses the cached aligned note times inside their range and the GP time map outside it,
    shifted to meet the cached times at the edges (count-in / metronome need bars without notes).
    """
    aq = np.array([a for a, _ in info.anchors], float)
    at = np.array([b for _, b in info.anchors], float)

    def gp_map(q):
        q = np.asarray(q, float)
        if len(aq) < 2:
            return q * 60.0 / info.tempo0
        t = np.interp(q, aq, at)
        # linear extrapolation past the last anchor at the final tempo
        slope_hi = (at[-1] - at[-2]) / max(aq[-1] - aq[-2], 1e-9)
        slope_lo = (at[1] - at[0]) / max(aq[1] - aq[0], 1e-9)
        t = np.where(q > aq[-1], at[-1] + (q - aq[-1]) * slope_hi, t)
        return np.where(q < aq[0], at[0] + (q - aq[0]) * slope_lo, t)

    cached = _cached_audio_times(gp_path)
    if cached is None:
        return gp_map
    cq, ct = cached
    d_lo = ct[0] - float(gp_map(cq[0]))
    d_hi = ct[-1] - float(gp_map(cq[-1]))

    def mapped(q):
        q = np.asarray(q, float)
        t = np.interp(q, cq, ct)
        t = np.where(q < cq[0], gp_map(q) + d_lo, t)
        return np.where(q > cq[-1], gp_map(q) + d_hi, t)

    return mapped


def _refine_local(env, notes, lag_s, win_s=3.0, search_s=0.10):
    """Fallback for embedded-audio scores without cached alignment: per-window offset (median-smoothed)
    that lands the score notes on the recording's attacks."""
    from scipy.ndimage import maximum_filter1d
    from scipy.signal import medfilt
    T = len(env)
    envm = maximum_filter1d(env, 3)
    times = np.array([n["time"] for n in notes])
    steps = np.arange(-search_s, search_s + 1e-9, 1.0 / FPS)
    centers, shifts = [], []
    t = times[0]
    while t < times[-1] + 1e-6:
        sel = times[(times >= t) & (times < t + win_s)]
        if len(sel) >= 6:
            def fit(sh):
                f = np.round((sel + lag_s + sh) * FPS).astype(int)
                return envm[f[(f >= 0) & (f < T)]].sum()
            centers.append(t + win_s / 2)
            shifts.append(max(steps, key=fit))
        t += win_s / 2
    if len(shifts) < 2:
        return notes
    shifts = np.array(shifts)
    if len(shifts) >= 5:
        shifts = medfilt(shifts, 5)
    shifts -= np.median(shifts)
    for n, sh in zip(notes, np.interp(times, centers, shifts)):
        n["time"] += float(sh)
    return notes


def _score_roll(notes, T):
    roll = np.zeros((N_SEMI, T), np.float32)
    for n in notes:
        a = int(round(n["time"] * FPS))
        b = a + max(2, int(min(n["dur"], 0.2) * FPS))
        p = n["midi"] - MIDI_LO
        if 0 <= p < N_SEMI and a < T:
            roll[p, max(0, a):min(T, b)] = 1.0
            if p + 12 < N_SEMI:
                roll[p + 12, max(0, a):min(T, b)] = np.maximum(roll[p + 12, max(0, a):min(T, b)], 0.5)
    return roll


def _coarse_lag(En, notes, hint_s: Optional[float] = None):
    """Frame lag with rec_frame = score_frame + lag, and a peak-sharpness confidence.

    hint_s: known approximate lag (in-app takes, where playback start is known up to device latency);
    the search is then limited to a window around it.
    """
    T_rec = En.shape[1]
    T_sc = int(max(n["time"] for n in notes) * FPS) + int(FPS)
    L = _score_roll(notes, T_sc)
    n = 1 << int(np.ceil(np.log2(T_rec + T_sc)))
    Ef = np.fft.rfft(En - En.mean(axis=1, keepdims=True), n, axis=1)
    Lf = np.fft.rfft(L, n, axis=1)
    cc = np.fft.irfft((Ef * np.conj(Lf)).sum(axis=0), n)
    # valid lags: recording may start before the song (lag > 0) or mid-song (lag < 0)
    lags = np.concatenate([np.arange(0, T_rec), np.arange(-T_sc + 1, 0)])
    vals = np.concatenate([cc[:T_rec], cc[n - T_sc + 1:]])
    if hint_s is not None:
        vals = np.where(np.abs(lags - hint_s * FPS) <= HINT_WIN * FPS, vals, -np.inf)
    best = int(np.argmax(vals))
    if hint_s is not None:
        return int(lags[best]), float("inf")
    away = np.abs(lags - lags[best]) > int(0.5 * FPS)
    runner_up = vals[away].max() if away.any() else 0.0
    conf = float((vals[best] - np.median(vals)) / (runner_up - np.median(vals) + 1e-6))
    return int(lags[best]), conf


# ---------------------------------------------------------------- note judgment

def _pitch_ok(H, a, p, k):
    """Expected pitch dominates its +-2 semitone neighbourhood just after the onset."""
    T = H.shape[1]
    a = min(max(0, a + 2), T - 1)
    seg = H[:, a:min(T, a + k)].mean(axis=1)
    local = seg[max(0, p - 2):p + 3].max()
    band = seg[BASS_LO - MIDI_LO:BASS_HI - MIDI_LO]
    # ... and stands out of the register at all: in hiss every bin is about the median (ratio ~1, notes >= 1.6)
    return bool(seg[p] >= 0.85 * local and seg[p] >= 0.6 * band.max() and seg[p] >= TONAL_MIN * np.median(band))


def _pyin_ok(y, a, k, midi):
    """Second opinion for notes the harmonic-sum check rejects: pYIN on just that note's frames."""
    import librosa
    frame = 2048
    lo = max(0, (a + 2) * HOP - frame // 2)
    hi = min(len(y), (a + 2 + k) * HOP + frame // 2)
    if hi - lo < frame:
        return False
    f0, voiced, _ = librosa.pyin(y[lo:hi], fmin=35, fmax=500, sr=SR, frame_length=frame, hop_length=HOP,
                                 center=False)
    f0 = f0[voiced & ~np.isnan(f0)]
    if len(f0) < 2:
        return False
    dev = librosa.hz_to_midi(f0) - midi
    dev -= 12 * np.round(dev / 12)                       # octave errors are the tracker's, not the player's
    return bool(np.mean(np.abs(dev) <= 0.5) >= 0.3)


def _pitch_entry(En, p, lo, hi):
    """First frame in [lo, hi) where pitch p appears after being quiet (for legato notes / missed plucks)."""
    T = En.shape[1]
    for f in range(max(1, lo), min(T - 2, hi)):
        if En[p, f] >= 0.5 and En[p, f - 1] < 0.35 and En[p, f:f + 3].mean() >= 0.5:
            return f
    return None


def _match(notes, y, En, H, env, lvl, peaks, lag_s, min_strength=0.0, floor=0.0):
    """In-order matching of score notes to onset peaks. Returns per-note (onset_time|None, pitch_ok).

    Peaks where the expected pitch sounds are preferred; among those the strongest attack wins,
    distance only breaks ties, so bleed / noise onsets near the grid can't mask a late pluck.
    Peaks weaker than min_strength (decay ripples, string noise) or quieter than floor (hiss after the
    player stopped) are not plucks.
    """
    peak_t = peaks / FPS
    used = {}
    out = []
    times = [n["time"] for n in notes]
    for i, n in enumerate(notes):
        te = n["time"] + lag_s
        prev_gap = next((times[i] - times[j] for j in range(i - 1, -1, -1) if times[i] - times[j] > 1e-3), None)
        next_gap = next((times[j] - times[i] for j in range(i + 1, len(notes)) if times[j] - times[i] > 1e-3), None)
        gaps = [g for g in (prev_gap, next_gap) if g is not None]
        w = float(np.clip(0.5 * min(gaps), MIN_SEARCH, MAX_SEARCH)) if gaps else MAX_SEARCH
        p = n["midi"] - MIDI_LO
        # pitch is read between the attack and the next score note, never inside the next note
        span = min(n["dur"], 0.25, next_gap - 0.02 if next_gap else 0.25)
        k = int(np.clip(span * FPS - 2, 2, 20))
        lo, hi = np.searchsorted(peak_t, [te - w, te + w])
        cands = []
        for c in range(lo, hi):
            if c in used and abs(used[c] - n["time"]) > 1e-3:      # shared only by simultaneous notes
                continue
            f = int(peaks[c])
            if env[f] < min_strength or not _loud(lvl, f, floor * (0.5 if n["dead"] else 1.0)):
                continue
            ok = n["dead"] or not (0 <= p < N_SEMI) or _pitch_ok(H, f, p, k)
            cands.append((c, ok, float(env[f]), abs(peak_t[c] - te)))
        pool = [x for x in cands if x[1]] or ([] if n["legato"] else cands)
        if pool:
            top = max(x[2] for x in pool) + 1e-6
            c, ok, _, _ = max(pool, key=lambda x: x[2] / top - 0.5 * x[3] / w)
            if not ok and y is not None:
                ok = _pyin_ok(y, int(peaks[c]), k, n["midi"])
            used[c] = n["time"]
            out.append((float(peak_t[c]), ok))
            continue
        # hammer-ons / legato slides have no pluck: accept a clean pitch entry instead
        if n["legato"] and 0 <= p < N_SEMI:
            f = _pitch_entry(En, p, int((te - w) * FPS), int((te + w) * FPS))
            if f is not None:
                out.append((f / FPS, True))
                continue
        out.append((None, False))
    return out


def _judge(dt: Optional[float], ok: bool) -> str:
    if dt is None:
        return "MISS"
    if not ok:
        return "BAD"
    a = abs(dt)
    if a <= PERFECT_WIN:
        return "PERFECT"
    if a <= GREAT_WIN:
        return "GREAT"
    if a <= GOOD_WIN:
        return "GOOD"
    return "BAD"


# ---------------------------------------------------------------- report

def _grade(score: float) -> str:
    for g, lim in (("SS", 95), ("S", 90), ("A", 80), ("B", 70)):
        if score >= lim:
            return g
    return "C"


def _bar_ranges(bars: List[int]) -> str:
    bars = sorted(set(bars))
    parts, s = [], 0
    for i in range(1, len(bars) + 1):
        if i == len(bars) or bars[i] != bars[i - 1] + 1:
            a, b = bars[s], bars[i - 1]
            parts.append(f"{a}" if a == b else f"{a}–{b}")
            s = i
    return "、".join(parts[:3])


def _comment(heatmap, n_wrong, n_miss, n_extra, n_total) -> str:
    parts = []
    early = [h["measure"] for h in heatmap if h["status"] == "EARLY"]
    late = [h["measure"] for h in heatmap if h["status"] == "LATE"]
    if early:
        parts.append(f"第 {_bar_ranges(early)} 小节抢拍")
    if late:
        parts.append(f"第 {_bar_ranges(late)} 小节拖拍")

    def worst(field):
        ranked = sorted((h for h in heatmap if h[field] > 0), key=lambda h: -h[field])
        return _bar_ranges([h["measure"] for h in ranked[:3]])

    if n_wrong:
        parts.append(f"错音 {n_wrong}（第 {worst('wrong')} 小节）")
    if n_miss:
        parts.append(f"漏音 {n_miss}（第 {worst('miss')} 小节）")
    if n_extra > max(3, 0.05 * n_total):
        parts.append(f"杂音 {n_extra}")
    return "；".join(parts)


def evaluate_audio_performance(audio_file_path: str, song_id: str, gp_path: str,
                               lag_hint_s: Optional[float] = None, bar_range: Optional[tuple] = None,
                               rate: float = 1.0, label: str = "") -> Dict[str, Any]:
    """bar_range: playback bars [start, end) the take covers (label: its section name);
    rate: tempo factor the backing was played at."""
    if not os.path.exists(audio_file_path):
        return {"success": False, "error": "录音文件不存在"}
    if not gp_path or not os.path.exists(gp_path):
        return {"success": False, "error": "曲谱文件不存在"}

    try:
        info = parse_gp(gp_path)
    except Exception:
        return {"success": False, "error": "曲谱解析失败"}
    notes = []
    for g in info.notes:
        if g.grace:
            continue
        notes.append({"time": g.time, "q": float(g.qpos), "dur": max(0.03, g.end - g.time), "midi": g.midi,
                      "dead": g.dead, "legato": g.hopo_dest, "bar": g.bar, "occ": g.occurrence, "s": g.string})
    # a legato slide (flag 2) makes the next note on that string sound without a pluck
    for i, g in enumerate(info.notes):
        if g.slide & 2:
            nxt = next((h for h in info.notes[i + 1:] if h.string == g.string and h.qpos > g.qpos), None)
            if nxt is not None:
                for n in notes:
                    if abs(n["time"] - nxt.time) < 1e-6 and n["midi"] == nxt.midi:
                        n["legato"] = True
    audio_times = _cached_audio_times(gp_path)
    if audio_times is not None:
        for n in notes:
            n["time"] = float(np.interp(n["q"], *audio_times))
    notes.sort(key=lambda n: (n["time"], n["midi"]))
    if len(notes) < MIN_NOTES:
        return {"success": False, "error": "曲谱无贝斯音符"}
    min_notes = MIN_NOTES
    if bar_range is not None:
        a, e = max(0, bar_range[0]), min(len(info.bars), bar_range[1])
        q0 = float(info.bars[a][2])
        q1 = float(info.bars[e - 1][2] + info.bars[e - 1][3])
        notes = [n for n in notes if q0 - 1e-6 <= n["q"] < q1 - 1e-6]
        min_notes = MIN_RANGE_NOTES
        if len(notes) < min_notes:
            return {"success": False, "error": "段落音符过少"}
    rate = float(min(1.0, max(0.5, rate)))
    if rate != 1.0:
        # the backing was stretched: the player hears (and plays) score time / rate
        for n in notes:
            n["time"] /= rate
            n["dur"] /= rate
    n_target = len(notes)

    try:
        y = _decode(audio_file_path)
    except Exception:
        return {"success": False, "error": "录音解码失败"}
    if len(y) < SR * 3:
        return {"success": False, "error": "录音过短"}
    if np.sqrt(np.mean(y ** 2)) < 1e-4:
        return {"success": False, "error": "录音无声"}

    En, H, env, lvl = _features(y)
    T = En.shape[1]
    peaks = _onsets(env, CAND_DELTA)
    floor1 = QUIET_OF_LOUD * float(np.percentile(lvl, 95))
    strict = np.array([f for f in _onsets(env, STRICT_DELTA) if _loud(lvl, f, floor1)], dtype=int)
    if len(strict) < min_notes // 2:
        return {"success": False, "error": "未检测到演奏"}

    lag, conf = _coarse_lag(En, notes, lag_hint_s)
    lag_s = lag / FPS

    # only judge the part of the song the recording covers
    t0 = max(0.0, strict[0] / FPS - 0.5) - lag_s
    t1 = min(T / FPS, strict[-1] / FPS + 0.5) - lag_s
    notes = [n for n in notes if t0 <= n["time"] <= t1]
    if len(notes) < min_notes:
        return {"success": False, "error": "录音与曲谱不匹配"}

    # pass 1 at the coarse lag, then re-centre on the median timing error and judge
    m1 = _match(notes, None, En, H, env, lvl, peaks, lag_s, floor=floor1)
    d1 = [t - (n["time"] + lag_s) for n, (t, ok) in zip(notes, m1) if t is not None and ok]
    if len(d1) < 0.2 * len(notes) or conf < 1.05:
        return {"success": False, "error": "录音与曲谱不匹配"}
    lag_s += float(np.median(d1))
    hit_env = [env[int(round(t * FPS))] for t, ok in m1 if t is not None and ok]
    min_strength = 0.25 * float(np.median(hit_env))
    hit_lvl = [lvl[int(round(t * FPS)):int(round(t * FPS)) + 4].max(initial=0.0) for t, ok in m1 if t is not None and ok]
    floor = max(floor1, QUIET_OF_HITS * float(np.median(hit_lvl)))
    if audio_times is None and info.audio_asset:
        # without embedded audio the GP plays its own synth, whose timing is the score map itself
        notes = _refine_local(env, notes, lag_s)
    matches = _match(notes, y, En, H, env, lvl, peaks, lag_s, min_strength, floor)

    judged = []
    for n, (t, ok) in zip(notes, matches):
        dt = None if t is None else t - (n["time"] + lag_s)
        judged.append((n, dt, ok, _judge(dt, ok)))

    counts = {k: 0 for k in WEIGHT}
    combo = max_combo = 0
    for _, _, _, j in judged:
        counts[j] += 1
        combo = combo + 1 if j in ("PERFECT", "GREAT", "GOOD") else 0
        max_combo = max(max_combo, combo)

    N = len(judged)
    matched = [(n, dt, ok) for n, dt, ok, _ in judged if dt is not None]
    pitched = [(n, ok) for n, _, ok in matched if not n["dead"]]
    good_dt = [abs(dt) for _, dt, ok in matched if ok]
    n_wrong = sum(1 for _, ok in pitched if not ok)

    # confident onsets, as strong as a typical played note, that belong to no score note
    hit_t = np.array(sorted([t for t, _ in matches if t is not None] + [n["time"] + lag_s for n in notes]))
    span = (strict / FPS >= t0 + lag_s) & (strict / FPS <= t1 + lag_s)
    n_extra = 0
    for f in strict[span]:
        s = f / FPS
        i = np.searchsorted(hit_t, s)
        near = min(abs(s - hit_t[j]) for j in (i - 1, i) if 0 <= j < len(hit_t))
        n_extra += int(near > 0.06 and env[f] >= 2 * min_strength and _loud(lvl, f, floor))

    accuracy = sum(WEIGHT[j] for *_, j in judged) / N
    extra_rate = n_extra / N
    overall = round(100.0 * accuracy * (1.0 - min(0.15, 0.25 * extra_rate)), 4)   # x25000 = game points
    dims = {
        "timing": round(100.0 * float(np.mean(np.clip(1.0 - (np.array(good_dt) - 0.015) / 0.12, 0, 1))), 1) if good_dt else 0.0,
        "pitch": round(100.0 * (1 - n_wrong / len(pitched)), 1) if pitched else 0.0,
        "complete": round(100.0 * len(matched) / N, 1),
        "clean": round(100.0 * max(0.0, 1.0 - extra_rate), 1),
    }

    # per-bar heatmap in playback order
    bars: Dict[tuple, Dict[str, Any]] = {}
    for n, dt, ok, j in judged:
        b = bars.setdefault((n["occ"], n["bar"]), {"measure": n["bar"] + 1, "w": 0.0, "notes": 0,
                                                    "dts": [], "wrong": 0, "miss": 0})
        b["w"] += WEIGHT[j]
        b["notes"] += 1
        if dt is not None and ok:
            b["dts"].append(dt)
        b["wrong"] += j == "BAD" and not ok
        b["miss"] += j == "MISS"
    heatmap = []
    for key in sorted(bars, key=lambda k: min(n["time"] for n in notes if (n["occ"], n["bar"]) == k)):
        b = bars[key]
        acc = b["w"] / b["notes"]
        mean_dt = float(np.mean(b["dts"])) if b["dts"] else 0.0
        if acc < 0.5:
            status = "MISS"
        elif len(b["dts"]) >= 2 and abs(mean_dt) > 0.04:
            status = "EARLY" if mean_dt < 0 else "LATE"
        elif acc >= 0.95:
            status = "PERFECT"
        elif acc >= 0.8:
            status = "GREAT"
        else:
            status = "GOOD"
        heatmap.append({"measure": b["measure"], "status": status, "diff_ms": round(mean_dt * 1000, 1),
                        "notes": b["notes"], "acc": round(acc, 3), "wrong": int(b["wrong"]), "miss": b["miss"]})

    if counts["PERFECT"] == N:
        combo_badge = "ALL PERFECT"
    elif counts["BAD"] == 0 and counts["MISS"] == 0:
        combo_badge = "FULL COMBO"
    elif overall >= 70:
        combo_badge = "CLEAR"
    else:
        combo_badge = "FAILED"

    # FAST / SLOW as in the game: timing of the non-PERFECT hits with the right pitch
    fast = sum(1 for _, dt, ok, j in judged if dt is not None and ok and j != "PERFECT" and dt < 0)
    slow = sum(1 for _, dt, ok, j in judged if dt is not None and ok and j != "PERFECT" and dt > 0)

    result = {
        "success": True,
        "song_id": song_id,
        "fast": fast,
        "slow": slow,
        "wrong": n_wrong,
        "overall_score": overall,
        "grade": _grade(overall),
        "combo_badge": combo_badge,
        "max_combo": max_combo,
        "notes_total": N,
        "judgments": {k.lower(): v for k, v in counts.items()},
        "extra_notes": n_extra,
        "dimensions": dims,
        "coach_comment": _comment(heatmap, n_wrong, counts["MISS"], n_extra, N),
        "heatmap": heatmap,
        "notes": [[n["q"], n["midi"], j, None if dt is None else round(dt * 1000, 1)] for n, dt, _, j in judged],
        "offset_ms": round(lag_s * 1000, 1),
        "sync": "cache" if audio_times is not None else "local" if info.audio_asset else "score",
        # share of the take's target notes (whole song or section) the recording covered
        "coverage": round(N / n_target, 3),
        "range": list(bar_range) if bar_range is not None else None,
        "label": label,
        "rate": rate,
    }

    result["report_path"] = None
    result["sections"] = []
    try:
        from eval_report import build_report
        report = build_report(gp_path, {(n["bar"], n["occ"], round(n["q"], 6), n["s"]): (j, dt, ok)
                                        for n, dt, ok, j in judged})
        result["sections"] = [{k: s[k] for k in ("name", "start", "end", "acc", "grade")}
                              for s in report["sections"] if "acc" in s]
        report["summary"] = {k: v for k, v in result.items() if k not in ("notes", "heatmap", "sections")}
        report["summary"]["audio"] = os.path.abspath(audio_file_path)
        report["summary"]["evaluated_at"] = time.strftime("%Y-%m-%d %H:%M")
        os.makedirs(REPORT_DIR, exist_ok=True)
        path = os.path.join(REPORT_DIR, f"{hashlib.md5(song_id.encode()).hexdigest()[:10]}_{int(time.time())}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, separators=(",", ":"))
        result["report_path"] = path
    except Exception as e:      # the score stands without the detailed report
        print(f"report failed: {e}", file=sys.stderr)
    return result


# ---------------------------------------------------------------- persistence

_NEW_COLUMNS = {"combo_badge": "TEXT", "heatmap_json": "TEXT", "pitch_score": "REAL",
                "complete_score": "REAL", "clean_score": "REAL", "judgments_json": "TEXT"}


def init_eval_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS performance_scores (
            song_id TEXT PRIMARY KEY,
            overall_score REAL,
            grade TEXT,
            timing_score REAL,
            dynamics_score REAL,
            articulation_score REAL,
            tone_score REAL,
            coach_comment TEXT,
            evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    have = {r[1] for r in cur.execute("PRAGMA table_info(performance_scores)")}
    for col, typ in _NEW_COLUMNS.items():
        if col not in have:
            cur.execute(f"ALTER TABLE performance_scores ADD COLUMN {col} {typ}")
    # every take (the table above keeps only the best full-song take)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS performance_takes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_id TEXT NOT NULL,
            evaluated_at TEXT,
            overall_score REAL,
            grade TEXT,
            combo_badge TEXT,
            notes_total INTEGER,
            coverage REAL,
            range_start INTEGER,
            range_end INTEGER,
            range_label TEXT,
            rate REAL,
            complete INTEGER,
            new_best INTEGER,
            report_path TEXT,
            audio_path TEXT,
            sections_json TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_takes_song ON performance_takes(song_id, evaluated_at)")
    conn.commit()
    conn.close()


def save_score(r: Dict[str, Any]) -> bool:
    """Keeps the best score per song. Rows from the old heuristic evaluator (no judgments) are replaced."""
    init_eval_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        row = cur.execute("SELECT overall_score, judgments_json FROM performance_scores WHERE song_id = ?",
                          (r["song_id"],)).fetchone()
        r["prev_best"] = float(row[0]) if row and row[1] and row[0] is not None else None
        if r["prev_best"] is not None and r["prev_best"] > r["overall_score"]:
            return False
        d = r["dimensions"]
        judg = dict(r["judgments"], max_combo=r["max_combo"], notes_total=r["notes_total"], extra=r["extra_notes"])
        cur.execute("""
            INSERT OR REPLACE INTO performance_scores (
                song_id, overall_score, grade, timing_score, pitch_score, complete_score, clean_score,
                dynamics_score, articulation_score, tone_score,
                coach_comment, combo_badge, heatmap_json, judgments_json, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, datetime('now', 'localtime'))
        """, (r["song_id"], r["overall_score"], r["grade"], d["timing"], d["pitch"], d["complete"], d["clean"],
              r["coach_comment"], r["combo_badge"], json.dumps(r["heatmap"]), json.dumps(judg)))
        conn.commit()
        return True
    finally:
        conn.close()


def get_song_best_score(song_id: str) -> Optional[Dict[str, Any]]:
    try:
        init_eval_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM performance_scores WHERE song_id = ?", (song_id,)).fetchone()
        conn.close()
        if not row:
            return None
        out = dict(row)
        out["heatmap"] = json.loads(out.pop("heatmap_json") or "[]")
        out["judgments"] = json.loads(out.pop("judgments_json") or "{}")
        return out
    except Exception as e:
        print(f"Error fetching best score: {e}", file=sys.stderr)
        return None


def save_take(r: Dict[str, Any], label: str = "", audio_path: str = "") -> bool:
    """Logs the take and returns whether it is a new best of its own chart.

    Charts: the whole song at full tempo (its best is the song's score, see save_score), and each
    (section, tempo) pair. Takes that cover less than FULL_COVERAGE of their target are logged
    but never become a best, so stopping early after the easy intro can't set a high score.
    """
    init_eval_db()
    complete = r["coverage"] >= FULL_COVERAGE
    rng = r.get("range")
    if rng is None and r["rate"] == 1.0:
        if complete:
            new_best = save_score(r)
        else:
            new_best = False
            best = get_song_best_score(r["song_id"])
            r["prev_best"] = float(best["overall_score"]) if best and best.get("judgments") else None
    else:
        conn = sqlite3.connect(DB_PATH)
        try:
            row = conn.execute("""
                SELECT MAX(overall_score) FROM performance_takes
                WHERE song_id = ? AND IFNULL(range_start, -1) = ? AND IFNULL(range_end, -1) = ?
                      AND ABS(rate - ?) < 1e-6 AND complete = 1
            """, (r["song_id"], rng[0] if rng else -1, rng[1] if rng else -1, r["rate"])).fetchone()
        finally:
            conn.close()
        r["prev_best"] = float(row[0]) if row and row[0] is not None else None
        new_best = complete and (r["prev_best"] is None or r["overall_score"] >= r["prev_best"])
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            INSERT INTO performance_takes (song_id, evaluated_at, overall_score, grade, combo_badge, notes_total,
                coverage, range_start, range_end, range_label, rate, complete, new_best, report_path, audio_path,
                sections_json)
            VALUES (?, datetime('now', 'localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (r["song_id"], r["overall_score"], r["grade"], r["combo_badge"], r["notes_total"], r["coverage"],
              rng[0] if rng else None, rng[1] if rng else None, label, r["rate"], int(complete), int(new_best),
              r.get("report_path"), os.path.abspath(audio_path) if audio_path else None,
              json.dumps(r.get("sections", []), ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    return new_best


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("song_id")
    ap.add_argument("gp")
    ap.add_argument("--lag-hint", type=float)
    ap.add_argument("--range", nargs=2, type=int, metavar=("START", "END"), help="playback bars [START, END)")
    ap.add_argument("--rate", type=float, default=1.0, help="tempo factor the backing was played at")
    ap.add_argument("--label", default="", help="section name, for the take history")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()
    res = evaluate_audio_performance(args.audio, args.song_id, args.gp, args.lag_hint,
                                     tuple(args.range) if args.range else None, args.rate, args.label)
    if res.get("success") and not args.no_save:
        res["new_best"] = save_take(res, args.label, args.audio)
    print(json.dumps(res, ensure_ascii=False))
