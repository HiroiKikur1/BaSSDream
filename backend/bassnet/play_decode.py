""""Bassist" decoding: choose each note's pitch together with where it would be played.

States per note = (pitch candidate from the acoustic top-K, string); cost = -w * log p(pitch) + learned fingering
emission (how often tabs put that pitch there) + learned hand-movement transition from the previous note
(fingering_learn.LearnedCost). A pitch the acoustic model half-believes wins when it keeps the hand in a playable,
idiomatic position; a confident pitch is only overridden when the alternative is far more natural to play.

python -m bassnet.play_decode --split test --w 1 2 4
"""
import argparse
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.fingering_learn import LearnedCost, MAXF  # noqa: E402
from bassnet.model import PITCH_LO  # noqa: E402


def joint_pitch(logp, times, tuning, cost, w=2.0, K=3, min_p=0.05):
    """logp (N, 48) acoustic log-probs per note -> chosen MIDI per note."""
    ns = len(tuning)
    cands = []
    for i in range(len(logp)):
        order = np.argsort(-logp[i])[:K]
        c = []
        for r, k in enumerate(order):
            if r and np.exp(logp[i, k]) < min_p:
                continue
            midi = PITCH_LO + int(k)
            for s, o in enumerate(tuning):
                f = midi - o
                if 0 <= f <= MAXF:
                    c.append((midi, s, f, -w * float(logp[i, k]) + cost.emit_cost(ns, s, f)))
        if not c:
            midi = PITCH_LO + int(order[0])
            s = 0 if midi < tuning[0] else ns - 1
            c = [(midi, s, max(0, min(MAXF, midi - tuning[s])), 0.0)]
        cands.append(c)
    dp = [np.array([x[3] for x in cands[0]])]
    bp = [np.zeros(len(cands[0]), int)]
    for i in range(1, len(cands)):
        dt = times[i] - times[i - 1]
        prev = cands[i - 1]
        row = np.zeros(len(cands[i]))
        brow = np.zeros(len(cands[i]), int)
        for j, (m2, s2, f2, e2) in enumerate(cands[i]):
            v = [dp[-1][k] + cost.trans_cost(dt, s1, f1, s2, f2) for k, (m1, s1, f1, _) in enumerate(prev)]
            k = int(np.argmin(v))
            row[j], brow[j] = v[k] + e2, k
        dp.append(row)
        bp.append(brow)
    k = int(np.argmin(dp[-1]))
    out = [0] * len(cands)
    for i in range(len(cands) - 1, -1, -1):
        out[i] = cands[i][k][0]
        k = bp[i][k]
    return out


def main():
    import json
    from bassnet.pipeline import choose_tuning
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    ap.add_argument("--w", type=float, nargs="+", default=[1.0, 2.0, 4.0])
    ap.add_argument("--K", type=int, default=3)
    a = ap.parse_args()
    ds = pickle.load(open(os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "context_ds.pkl"), "rb"))
    cost = LearnedCost(json.load(open(os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "fingering.json"))))
    for w in a.w:
        base = ok = tot = 0
        for g in ds[a.split]:
            P, y = g["P"], g["y"]
            times = (g["S"][:, 9] * 300.0).tolist()
            arg = P.argmax(-1)
            tuning = choose_tuning([{"midi": PITCH_LO + int(k)} for k in arg])
            ch = np.array(joint_pitch(P, times, tuning, cost, w=w, K=a.K)) - PITCH_LO
            m = y >= 0
            tot += m.sum()
            base += (arg[m] == y[m]).sum()
            ok += (ch[m] == y[m]).sum()
        print(f"w={w} K={a.K}: pitch {base / tot:.4f} -> {ok / tot:.4f}", flush=True)


if __name__ == "__main__":
    main()
