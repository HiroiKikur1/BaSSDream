"""Feature extraction + label alignment for BassNet training.

For each unique backing track: CQT of the separated bass stem, and GP notes
re-aligned to the stem (global offset search + piecewise local refinement).
"""
import paths
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.gpif_parser import parse_gp  # noqa: E402

ROOT = paths.cache(r"bassnet")
FEAT = os.path.join(ROOT, "feats")

SR = 22050
HOP = 256
FPS = SR / HOP
BINS_PER_SEMI = 3
MIDI_LO = 21                    # A0: lowest CQT bin
N_SEMI = 88
N_BINS = N_SEMI * BINS_PER_SEMI
PITCH_LO, PITCH_HI = 23, 70     # model output range (B0 .. A#4)
N_PITCH = PITCH_HI - PITCH_LO + 1


def compute_cqt(y: np.ndarray) -> np.ndarray:
    import librosa
    C = librosa.cqt(y, sr=SR, hop_length=HOP, fmin=librosa.midi_to_hz(MIDI_LO) / (2 ** (1 / 36)),
                    n_bins=N_BINS, bins_per_octave=12 * BINS_PER_SEMI)
    return np.log1p(np.abs(C) * 20.0).astype(np.float32)   # (bins, T)


def semitone_energy(logc: np.ndarray) -> np.ndarray:
    """(88, T) max over the 3 sub-bins, per-frame normalised."""
    e = logc.reshape(N_SEMI, BINS_PER_SEMI, -1).max(axis=1)
    return e / (e.max(axis=0, keepdims=True) + 1e-3)


def label_roll(notes, T, shift=0.0, max_len=0.35):
    roll = np.zeros((N_SEMI, T), np.float32)
    for n in notes:
        a = int(round((n["time"] + shift) * FPS))
        b = int(round((min(n["end"], n["time"] + max_len) + shift) * FPS))
        p = n["midi"] - MIDI_LO
        if 0 <= p < N_SEMI and b > 0 and a < T:
            roll[p, max(0, a):min(T, max(b, a + 2))] = 1.0
    return roll


def _xcorr_lag(E, notes, T, max_lag_s):
    """Pitch-aware cross-correlation over integer frame lags via FFT."""
    L = label_roll(notes, T)
    n = 1 << int(np.ceil(np.log2(2 * T)))
    Ef = np.fft.rfft(E - E.mean(axis=1, keepdims=True), n, axis=1)
    Lf = np.fft.rfft(L, n, axis=1)
    cc = np.fft.irfft((Ef * np.conj(Lf)).sum(axis=0), n)
    maxlag = int(max_lag_s * FPS)
    lags = np.concatenate([np.arange(0, maxlag + 1), np.arange(-maxlag, 0)])
    vals = np.concatenate([cc[:maxlag + 1], cc[-maxlag:]])
    return lags, vals


def note_hit_rate(E, notes, shift=0.0):
    """Fraction of notes whose pitch is the strongest (or within 0.6 of) in the frames after the onset."""
    T = E.shape[1]
    hits, tot = 0, 0
    for n in notes:
        a = int(round((n["time"] + shift) * FPS)) + 1
        b = min(T, a + max(2, min(8, int((n["end"] - n["time"]) * FPS))))
        p = n["midi"] - MIDI_LO
        if a < 0 or a >= T or not (0 <= p < N_SEMI):
            continue
        tot += 1
        seg = E[:, a:b].mean(axis=1)
        if seg[p] >= 0.6 or seg[min(N_SEMI - 1, p + 12)] >= 0.8:
            hits += 1
    return hits / max(1, tot)


def refine_local(E, notes, win_s=6.0, search_s=0.12):
    """Piecewise constant offsets per window, median-smoothed, linearly interpolated per note."""
    if not notes:
        return notes, ([0.0], [0.0])
    T = E.shape[1]
    times = np.array([n["time"] for n in notes])
    centers, shifts = [], []
    t = times[0]
    steps = np.arange(-search_s, search_s + 1e-9, 1.0 / FPS)
    while t < times[-1] + 1e-6:
        sel = [n for n in notes if t <= n["time"] < t + win_s]
        if len(sel) >= 4:
            best, best_v = 0.0, -1e9
            for s in steps:
                v = 0.0
                for n in sel:
                    a = int(round((n["time"] + s) * FPS))
                    p = n["midi"] - MIDI_LO
                    if 1 <= a < T - 3 and 0 <= p < N_SEMI:
                        # onset: rising pitch energy + sustained presence
                        v += E[p, a:a + 3].mean() - 0.5 * E[p, a - 1]
                if v > best_v:
                    best, best_v = s, v
            centers.append(t + win_s / 2)
            shifts.append(best)
        t += win_s / 2
    if not centers:
        return notes, ([0.0], [0.0])
    shifts = np.array(shifts)
    if len(shifts) >= 5:
        from scipy.signal import medfilt
        shifts = medfilt(shifts, 5)
    per_note = np.interp(times, centers, shifts)
    out = []
    for n, s in zip(notes, per_note):
        m = dict(n)
        m["time"] = n["time"] + float(s)
        m["end"] = n["end"] + float(s)
        out.append(m)
    return out, (list(map(float, centers)), list(map(float, shifts)))


N_MEL = 128


def compute_mel(y: np.ndarray) -> np.ndarray:
    import librosa
    M = librosa.feature.melspectrogram(y=y, sr=SR, n_fft=2048, hop_length=HOP, n_mels=N_MEL, fmin=30, fmax=11000)
    return np.log1p(M * 10.0).astype(np.float32)


def mix_mel(gp, info):
    import tempfile
    import zipfile
    import librosa
    if not info.audio_asset:
        return None
    with zipfile.ZipFile(gp) as z:
        data = z.read(info.audio_asset)
    suffix = ".bin"
    for known in (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"):
        if info.audio_asset.lower().endswith(known):
            suffix = known
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    os.write(fd, data)
    os.close(fd)
    try:
        try:
            y, _ = librosa.load(tmp, sr=SR, mono=True)
        except Exception:
            import subprocess
            wav = tmp + ".wav"
            ff = paths.FFMPEG
            subprocess.run([ff, "-y", "-loglevel", "error", "-i", tmp, "-ac", "1", "-ar", str(SR), wav])
            y, _ = librosa.load(wav, sr=SR, mono=True)
            os.remove(wav)
        return compute_mel(y)
    finally:
        os.remove(tmp)


def build_one(gp, stem, out_npz):
    import librosa
    info = parse_gp(gp)
    y, _ = librosa.load(stem, sr=SR, mono=True)
    C = compute_cqt(y)
    E = semitone_energy(C)
    T = C.shape[1]
    notes = [{"time": n.time, "end": n.end, "midi": n.midi, "dead": n.dead, "grace": n.grace,
              "qpos": float(n.qpos), "qdur": float(n.qdur), "string": n.string, "fret": n.fret,
              "slide": n.slide, "hopo_o": n.hopo_origin, "hopo_d": n.hopo_dest, "slap": n.slap, "pop": n.pop}
             for n in info.notes]
    main = [n for n in notes if not n["grace"] and not n["dead"]]
    lags, vals = _xcorr_lag(E, main, T, 15.0)
    order = np.argsort(vals)[::-1]
    best_lag = lags[order[0]] / FPS
    # beat-periodic ambiguity: evaluate the top peaks with the hit-rate metric
    cands = []
    for i in order[:400]:
        lg = lags[i] / FPS
        if all(abs(lg - c) > 0.05 for c in cands):
            cands.append(lg)
        if len(cands) >= 6:
            break
    rates = [(note_hit_rate(E, main, c), c) for c in cands]
    rate0 = note_hit_rate(E, main, 0.0)
    best_rate, best_lag = max(rates)
    if rate0 >= best_rate - 0.02:
        best_lag, best_rate = 0.0, rate0
    shifted = [dict(n, time=n["time"] + best_lag, end=n["end"] + best_lag) for n in notes]
    refined, (sc, ss) = refine_local(E, shifted)
    final_rate = note_hit_rate(E, [n for n in refined if not n["grace"] and not n["dead"]])
    from bassnet.gpif_parser import q_to_sec
    def score_time(q):
        t0 = q_to_sec(info.anchors, q) + best_lag
        return t0 + float(np.interp(t0, sc, ss))
    beats, downbeats = [], []
    first_q = min(float(n.qpos) for n in info.notes) if info.notes else 0.0
    last_q = max(float(n.qpos + n.qdur) for n in info.notes) if info.notes else 0.0
    for bi, oc, q0, ql in info.bars:
        q0, ql = float(q0), float(ql)
        if q0 + ql < first_q - 8 or q0 > last_q + 4:
            continue
        downbeats.append(score_time(q0))
        num, den = info.time_sigs[bi]
        step = 1.5 if (den == 8 and num % 3 == 0) else 4.0 / den if den >= 4 else 1.0
        k = 0.0
        while k < ql - 1e-6:
            beats.append(score_time(q0 + k))
            k += step
    mel = mix_mel(gp, info)
    if mel is not None and mel.shape[1] != T:
        mel = mel[:, :T] if mel.shape[1] > T else np.pad(mel, ((0, 0), (0, T - mel.shape[1])))
    np.savez_compressed(out_npz, cqt=C.astype(np.float16),
                        mel=(mel if mel is not None else np.zeros((N_MEL, T), np.float32)).astype(np.float16))
    meta = {
        "gp": gp, "stem": stem, "T": int(T), "global_lag": float(best_lag), "rate0": rate0,
        "rate_global": best_rate, "rate_final": final_rate, "tuning": info.tuning,
        "time_sigs": info.time_sigs[:1], "tempo0": info.tempo0, "key": list(info.key),
        "notes": refined, "beats": beats, "downbeats": downbeats, "has_mel": mel is not None,
    }
    json.dump(meta, open(out_npz.replace(".npz", ".json"), "w", encoding="utf8"), ensure_ascii=False)
    return meta


def main():
    os.makedirs(FEAT, exist_ok=True)
    index = json.load(open(os.path.join(ROOT, "index.json"), encoding="utf8"))
    by_md5 = {}
    for gp, v in index.items():
        by_md5.setdefault(v["md5"], []).append(gp)
    for md5, gps in by_md5.items():
        stem = os.path.join(ROOT, "stems", md5 + ".flac")
        out = os.path.join(FEAT, md5 + ".npz")
        if not os.path.exists(stem) or os.path.exists(out.replace(".npz", ".json")):
            continue
        # the recording is the full 5-string performance: prefer 5-string tabs, then non-simplified, then the richest tab
        def pref(g):
            v = index[g]
            name = os.path.basename(os.path.dirname(g)).lower()
            easy = any(k in name for k in ("簡單", "简单", "easy", "簡易"))
            return (len(v.get("tuning", [])) != 5, easy, -v.get("n_notes", 0), g)
        gps = sorted(gps, key=pref)
        try:
            m = build_one(gps[0], stem, out)
            print(f"{os.path.basename(os.path.dirname(gps[0]))[:34]:36s} lag={m['global_lag']:+.2f} "
                  f"hit0={m['rate0']:.2f} hitG={m['rate_global']:.2f} hitF={m['rate_final']:.2f}", flush=True)
        except Exception as e:
            print("fail", gps[0], e, flush=True)


if __name__ == "__main__":
    main()
