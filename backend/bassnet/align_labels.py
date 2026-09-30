"""Model-free label realignment with a sharp (STFT) onset envelope of the bass stem.

The first-pass alignment used CQT energy, whose long low-frequency windows smear time (up to
~0.2 s for the lowest strings). Here: global lag (onset x pitch-energy), windowed local shifts
(median smoothed), then a per-note snap to the nearest onset peak. Beats/downbeats follow the
same shift curve. Original GP times are kept as time_gp / end_gp.
"""
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.dataset import SR, HOP, FPS, semitone_energy, MIDI_LO  # noqa: E402

ROOT = r"E:\BassStation\cache\bassnet"


def onset_env(stem):
    import librosa
    y, _ = librosa.load(stem, sr=SR, mono=True)
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP, n_fft=1024)
    return env / (np.percentile(env, 99) + 1e-6)


def align(meta, env, E):
    notes = [n for n in meta["notes"] if not n.get("grace") and not n.get("dead")]
    if len(notes) < 10:
        return None
    t = np.array([n.get("time_gp", n["time"]) for n in notes])
    p = np.clip(np.array([n["midi"] - MIDI_LO for n in notes]), 0, E.shape[0] - 1)
    T = min(len(env), E.shape[1])

    def score(times, shift):
        fr = np.clip(np.round((times + shift) * FPS).astype(int), 0, T - 4)
        return env[fr] * (0.25 + E[p_sel, fr + 3])

    p_sel = p
    lags = np.arange(-0.35, 0.3501, 1 / FPS)
    g = [score(t, l).mean() for l in lags]
    glob_lag = float(lags[int(np.argmax(g))])

    # local windows
    centers, shifts = [], []
    win, hop = 5.0, 2.5
    steps = np.arange(-0.08, 0.0801, 1 / FPS)
    c = t[0]
    while c < t[-1]:
        sel = (t >= c) & (t < c + win)
        if sel.sum() >= 6:
            p_sel = p[sel]
            v = [score(t[sel], glob_lag + s).sum() for s in steps]
            centers.append(c + win / 2)
            shifts.append(glob_lag + float(steps[int(np.argmax(v))]))
        c += hop
    p_sel = p
    if len(shifts) >= 3:
        from scipy.signal import medfilt
        shifts = list(medfilt(np.array(shifts), 5 if len(shifts) >= 5 else 3))
    if not shifts:
        centers, shifts = [t[0]], [glob_lag]
    curve = lambda x: np.interp(x, centers, shifts)  # noqa: E731

    # per-note snap to an onset peak within +-25 ms
    snap = int(round(0.025 * FPS))
    out_shift = {}
    for n in meta["notes"]:
        base = n.get("time_gp", n["time"])
        s = float(curve(base))
        if not n.get("grace") and not n.get("dead"):
            a = int(round((base + s) * FPS))
            lo, hi = max(1, a - snap), min(T - 1, a + snap + 1)
            if hi > lo:
                k = lo + int(np.argmax(env[lo:hi]))
                if env[k] > 0.25 and env[k] >= env[k - 1] and env[k] >= env[k + 1]:
                    s = k / FPS - base
        n.setdefault("time_gp", n["time"])
        n.setdefault("end_gp", n["end"])
        n["time"] = n["time_gp"] + s
        n["end"] = n["end_gp"] + float(curve(n["end_gp"]))
        out_shift[id(n)] = s
    for key in ("beats", "downbeats"):
        gk = key + "_gp"
        meta.setdefault(gk, meta.get(key, []))
        meta[key] = [float(b + curve(b)) for b in meta[gk]]
    meta["align"] = "stft_v1"
    return glob_lag


def main():
    lags = []
    for f in sorted(glob.glob(os.path.join(ROOT, "feats", "*.json"))):
        meta = json.load(open(f, encoding="utf8"))
        d = np.load(f.replace(".json", ".npz"))
        E = semitone_energy(d["cqt"].astype(np.float32))
        env = onset_env(meta["stem"])
        g = align(meta, env, E)
        if g is None:
            continue
        json.dump(meta, open(f, "w", encoding="utf8"), ensure_ascii=False)
        lags.append(g)
        print(f"{os.path.basename(os.path.dirname(meta['gp']))[:30]:32s} global {g * 1000:+.0f} ms", flush=True)
    lags = np.abs(np.array(lags))
    print(f"songs {len(lags)}: |global lag| median {np.median(lags) * 1000:.0f} ms, >50ms: {(lags > 0.05).mean():.2f}")


if __name__ == "__main__":
    main()
