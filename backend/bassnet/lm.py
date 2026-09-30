"""Notation language model for bass lines + second-order Viterbi rescoring of decoded pitches.

The LM is an interval trigram (transposition invariant) with add-k smoothing, trained on the
tab library (excluding the evaluation split). Rescoring picks, for every decoded note, one of
its top-K acoustic pitch candidates so that acoustic log-prob + lam * LM log-prob is maximal.
"""
import paths
import glob
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.decode import FPS  # noqa: E402
from bassnet.model import PITCH_LO  # noqa: E402

ROOT = paths.cache(r"bassnet")
LM_PATH = os.path.join(ROOT, "interval_lm.json")
IMAX = 24
V = 2 * IMAX + 1


def _clip(i):
    return max(-IMAX, min(IMAX, i))


def train_lm(exclude):
    from bassnet.gpif_parser import parse_gp
    tri = defaultdict(float)
    bi = defaultdict(float)
    uni = defaultdict(float)
    for gp in glob.glob(os.path.join(paths.TABS, r"*\*.gp")):
        if gp in exclude:
            continue
        try:
            info = parse_gp(gp)
        except Exception:
            continue
        ps = [n.midi for n in info.notes if not n.grace and not n.dead]
        iv = [_clip(b - a) for a, b in zip(ps, ps[1:])]
        for k in range(len(iv)):
            uni[iv[k]] += 1
            if k >= 1:
                bi[f"{iv[k - 1]},{iv[k]}"] += 1
            if k >= 2:
                tri[f"{iv[k - 2]},{iv[k - 1]},{iv[k]}"] += 1
    return {"tri": tri, "bi": bi, "uni": uni}


class IntervalLM:
    def __init__(self, d, k=0.1):
        self.tri, self.bi, self.uni, self.k = d["tri"], d["bi"], d["uni"], k
        self.bi_ctx = defaultdict(float)
        for key, v in self.bi.items():
            self.bi_ctx[key.split(",")[0]] += v
        self.tri_ctx = defaultdict(float)
        for key, v in self.tri.items():
            a, b, _ = key.split(",")
            self.tri_ctx[f"{a},{b}"] += v
        self.N = sum(self.uni.values())
        self.cache = {}

    def logp(self, i2, i1, i0):
        """log P(i0 | i2, i1) with interpolation tri/bi/uni."""
        key = (i2, i1, i0)
        if key in self.cache:
            return self.cache[key]
        i0, i1 = _clip(i0), _clip(i1)
        pu = (self.uni.get(str(i0), 0) + self.k) / (self.N + self.k * V)
        c1 = self.bi_ctx.get(str(i1), 0)
        pb = (self.bi.get(f"{i1},{i0}", 0) + self.k * V * pu) / (c1 + self.k * V)
        if i2 is None:
            p = pb
        else:
            i2 = _clip(i2)
            c2 = self.tri_ctx.get(f"{i2},{i1}", 0)
            p = (self.tri.get(f"{i2},{i1},{i0}", 0) + self.k * V * pb) / (c2 + self.k * V)
        v = math.log(max(p, 1e-12))
        self.cache[key] = v
        return v


_LM = None


def get_lm():
    global _LM
    if _LM is None and os.path.exists(LM_PATH):
        _LM = IntervalLM(json.load(open(LM_PATH, encoding="utf8")))
    return _LM


def rescore(notes, frame_prob, lam=0.35, topk=5):
    """notes from decode_notes (seconds); frame_prob (T,49). Returns notes with possibly changed midi."""
    lm = get_lm()
    if lm is None or len(notes) < 3:
        return notes
    T = frame_prob.shape[0]
    cands = []
    for n in notes:
        a = int(round(n["time"] * FPS))
        e = int(round(n["end"] * FPS))
        lo, hi = min(a + 2, max(a, e - 1)), min(T, max(a + 3, min(e, a + 10)))
        pp = frame_prob[max(0, lo):hi, 1:].mean(axis=0)
        pp = pp / (pp.sum() + 1e-9)
        top = np.argsort(pp)[::-1][:topk]
        cands.append([(PITCH_LO + int(c), math.log(pp[c] + 1e-6)) for c in top])
    # second-order Viterbi: state = (candidate index of previous note, candidate index of current note)
    n = len(notes)
    K = [len(c) for c in cands]
    score = {}
    for j in range(K[1]):
        for i in range(K[0]):
            iv = cands[1][j][0] - cands[0][i][0]
            score[(i, j)] = cands[0][i][1] + cands[1][j][1] + lam * lm.logp(None, 0, iv)
    back = [None, None]
    for t in range(2, n):
        new, bp = {}, {}
        for k in range(K[t]):
            for j in range(K[t - 1]):
                best, arg = -1e18, 0
                for i in range(K[t - 2]):
                    prev = score.get((i, j))
                    if prev is None:
                        continue
                    i1 = cands[t - 1][j][0] - cands[t - 2][i][0]
                    i0 = cands[t][k][0] - cands[t - 1][j][0]
                    # a rest between notes weakens the melodic dependency
                    gap = notes[t]["time"] - notes[t - 1]["end"]
                    w = lam * (0.5 if gap > 0.4 else 1.0)
                    v = prev + w * lm.logp(None, i1, i0) + cands[t][k][1]
                    if v > best:
                        best, arg = v, i
                new[(j, k)] = best
                bp[(j, k)] = arg
        score = new
        back.append(bp)
    (j, k) = max(score, key=score.get)
    path = [k, j]
    for t in range(n - 1, 1, -1):
        i = back[t][(j, k)]
        path.append(i)
        j, k = i, j
    path = path[::-1]
    out = []
    for nt, c, idx in zip(notes, cands, path):
        m = dict(nt)
        m["midi"] = c[idx][0]
        out.append(m)
    return out


if __name__ == "__main__":
    split = os.path.join(ROOT, "test_split.json")
    exclude = set(json.load(open(split, encoding="utf8"))) if os.path.exists(split) else set()
    d = train_lm(exclude)
    json.dump(d, open(LM_PATH, "w", encoding="utf8"))
    lm = IntervalLM(d)
    print("trigrams:", len(d["tri"]), "unigram mass:", int(lm.N))
    for ctx in ((0, 0), (0, 12), (-5, 5), (7, -7)):
        best = sorted(range(-12, 13), key=lambda x: -lm.logp(ctx[0], ctx[1], x))[:5]
        print("after intervals", ctx, "-> most likely next:", best)
