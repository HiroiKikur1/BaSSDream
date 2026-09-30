"""Key signature per passage (46 of the 321 purchased tabs change key inside the song).

The song is cut into windows of `win` seconds; each window gets the same evidence as the song-level detector
(key_eval.sig_features on the window's chroma and chords, weights from key_w.json). A Viterbi over the 12
signatures then keeps one key unless the evidence for another one lasts: every change costs `change` and staying
on the song-level key earns `home` per window. The written score may only change key at a section start, so
changes are snapped to the nearest section boundary by the caller.

python -m bassnet.key_local      (evaluates window-level signature accuracy against the tabs' bar keys)
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet import key_eval as K  # noqa: E402

ROOT = K.ROOT


def window_features(mix_chroma, chords, dur, win=16.0, fps=K.SR / K.HOP):
    """[(t0, t1, F (12, nF))] over the song."""
    keep = K.SOURCES
    K.SOURCES = ("pc_mix", "pc_chords_orig")
    out = []
    try:
        t = 0.0
        while t < dur - 1e-6:
            t1 = min(dur, t + win)
            a, b = int(t * fps), max(int(t * fps) + 1, int(t1 * fps))
            pc_mix = mix_chroma[:, a:b].mean(1) if b <= mix_chroma.shape[1] else mix_chroma[:, a:].mean(1)
            sub = [c for c in chords if float(c["end_time"]) > t and float(c["start_time"]) < t1]
            clipped = [dict(c, start_time=max(t, float(c["start_time"])), end_time=min(t1, float(c["end_time"])))
                       for c in sub]
            pc_ch = K.chord_pc(clipped) if clipped else None
            out.append((t, t1, K.sig_features({"pc_mix": pc_mix, "pc_chords_orig": pc_ch})))
            t = t1
    finally:
        K.SOURCES = keep
    return out


def viterbi_keys(wins, w, home, change=6.0, home_bonus=0.5, temp=1.0):
    """-> signature (mod 12) per window."""
    L = np.array([F @ w / temp for _, _, F in wins])            # (N, 12) scores
    L = L - np.log(np.exp(L - L.max(1, keepdims=True)).sum(1, keepdims=True)) - L.max(1, keepdims=True)
    L[:, home] += home_bonus
    N = len(L)
    score = L[0].copy()
    back = np.zeros((N, 12), int)
    for i in range(1, N):
        stay = score
        best = int(np.argmax(score))
        new = np.empty(12)
        for s in range(12):
            if stay[s] >= score[best] - change:
                new[s], back[i, s] = stay[s], s
            else:
                new[s], back[i, s] = score[best] - change, best
        score = new + L[i]
    s = int(np.argmax(score))
    path = [s]
    for i in range(N - 1, 0, -1):
        s = back[i, s]
        path.append(s)
    return path[::-1]


WIN, CHANGE = 12.0, 6.0   # 321 tabs: key-change songs 49% -> 76% of windows right, single-key songs 84.6 -> 84.4%,
                          # 8.8% of single-key songs get a spurious change (win 12 s, change cost 6)


def local_keys(mix_chroma, chords, home_sig, win=WIN, change=CHANGE):
    """-> [(t0, t1, signature -5..6)] merged runs; mix_chroma = key_eval._chroma_norm(chroma frames)."""
    w = np.array(json.load(open(os.path.join(ROOT, "key_w.json")))["w"])
    dur = mix_chroma.shape[1] / (K.SR / K.HOP)
    wins = window_features(mix_chroma, chords, dur, win)
    if not wins:
        return []
    path = viterbi_keys(wins, w, home_sig % 12, change, 0.0)
    out = []
    for (t0, t1, _), p in zip(wins, path):
        sig = p - 12 if p > 6 else p
        if out and out[-1][2] == sig:
            out[-1] = (out[-1][0], t1, sig)
        else:
            out.append((t0, t1, sig))
    return out


def section_keys(segments, sec_starts, bar_time, n_bars, home):
    """Snap key runs to section starts: {first bar of section: signature} where it differs from the one before."""
    if len(segments) <= 1:
        return {}
    starts = sorted(set(sec_starts) | {0})
    out, prev = {}, home
    for i, b in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else n_bars
        t0, t1 = bar_time(b), bar_time(e)
        ov = {}
        for a, z, sig in segments:
            o = min(t1, z) - max(t0, a)
            if o > 0:
                ov[sig] = ov.get(sig, 0.0) + o
        sig = max(ov, key=ov.get) if ov else prev
        if sig != prev:
            out[b] = sig
            prev = sig
    return out


def evaluate(win=16.0, change=6.0, home_bonus=0.5, temp=1.0):
    import pickle
    from bassnet.build_stems import build_index
    rows = pickle.load(open(os.path.join(ROOT, "key_eval_rows.pkl"), "rb"))
    w = np.array(json.load(open(os.path.join(ROOT, "key_w.json")))["w"])
    idx = build_index()
    tot = {"global": [0, 0], "local": [0, 0]}
    chg = {"global": [0, 0], "local": [0, 0]}
    single = {"global": [0, 0], "local": [0, 0]}
    spurious = []
    for r in rows:
        md5 = idx[r["gp"]]["md5"]
        cf = os.path.join(ROOT, "chords", md5 + "_orig.json")
        ff = os.path.join(K.FEAT, md5 + ".npz")
        if not (os.path.exists(cf) and os.path.exists(ff)) or "fused" not in r:
            continue
        mix = K._chroma_norm(np.load(ff)["mix"])
        chords = json.load(open(cf))
        dur = mix.shape[1] / (K.SR / K.HOP)
        _, _, segs = K.gt_keys(r["gp"])
        st = np.array([t for t, _ in segs])
        wins = window_features(mix, chords, dur, win)
        home = r["fused"] % 12
        path = viterbi_keys(wins, w, home, change, home_bonus, temp)
        multi = r["n_keys"] > 1
        if not multi:
            spurious.append(sum(a != b for a, b in zip(path[:-1], path[1:])))
        for (t0, t1, _), p in zip(wins, path):
            c = 0.5 * (t0 + t1)
            j = int(np.searchsorted(st, c)) - 1
            if j < 0:
                continue
            g = segs[j][1] % 12
            for name, pred in (("global", home), ("local", p)):
                tot[name][0] += pred == g
                tot[name][1] += 1
                d = chg if multi else single
                d[name][0] += pred == g
                d[name][1] += 1
    f = lambda d: {k: round(v[0] / max(1, v[1]), 4) for k, v in d.items()}  # noqa: E731
    print(f"win {win} change {change} home {home_bonus} | all {f(tot)} | key-change songs {f(chg)} | single-key songs "
          f"{f(single)}, songs with spurious changes {np.mean(np.array(spurious) > 0):.3f}")
    return f(tot), f(chg)


if __name__ == "__main__":
    for win, ch in ((16.0, 3.0), (16.0, 4.0), (16.0, 5.0), (12.0, 5.0), (24.0, 4.0)):
        evaluate(win, ch, 0.0)
