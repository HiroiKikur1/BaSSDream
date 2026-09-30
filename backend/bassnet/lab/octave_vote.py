"""Song-level octave consistency: notes that play the same role in a repeated figure (same pitch class, same
intervals to their neighbours, same place in the beat) should sit in the same octave. Each such group votes with
its summed acoustic probabilities; a note moves to the group's octave only if its own posterior gives that
octave at least `min_p`.

python -m bassnet.lab.octave_vote --split test
"""
import argparse
import os
import pickle
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

NP = 48


def signature(arg, S, i):
    pc = arg[i] % 12
    prv = (arg[i] - arg[i - 1]) % 12 if i > 0 else -1
    nxt = (arg[i + 1] - arg[i]) % 12 if i + 1 < len(arg) else -1
    ph = int(round(np.arctan2(S[i, 2], S[i, 3]) / (2 * np.pi) * 4)) % 4     # 16th position within the beat
    return (pc, prv, nxt, ph)


def octave_vote(logp, S, min_p=0.05, min_group=4):
    p = np.exp(logp)
    arg = logp.argmax(-1)
    groups = defaultdict(list)
    for i in range(len(arg)):
        groups[signature(arg, S, i)].append(i)
    out = arg.copy()
    for sig, idx in groups.items():
        if len(idx) < min_group:
            continue
        pc = sig[0]
        octs = [k for k in range(pc, NP, 12)]
        score = {k: float(p[idx, k].sum()) for k in octs}
        best = max(score, key=score.get)
        for i in idx:
            if out[i] != best and p[i, best] >= min_p:
                out[i] = best
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    a = ap.parse_args()
    ds = pickle.load(open(os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "context_ds.pkl"), "rb"))
    for split in (["val", "test"] if a.split == "both" else [a.split]):
        for min_p in (0.02, 0.05, 0.1, 0.2):
            for mg in (3, 6):
                base = ok = tot = 0
                for g in ds[split]:
                    y = g["y"]
                    m = y >= 0
                    arg = g["P"].argmax(-1)
                    ch = octave_vote(g["P"], g["S"], min_p, mg)
                    tot += m.sum()
                    base += (arg[m] == y[m]).sum()
                    ok += (ch[m] == y[m]).sum()
                print(f"{split} min_p={min_p} min_group={mg}: pitch {base / tot:.4f} -> {ok / tot:.4f}", flush=True)


if __name__ == "__main__":
    main()
