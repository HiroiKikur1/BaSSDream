"""A song has a few fixed tempos, not one per bar.

tempo_segments: robust piecewise-constant segmentation of the per-bar tempo (dynamic programming, each extra segment
costs `penalty`, segments are at least `min_bars` long, single-bar half/double-time glitches are capped). 91% of
the purchased tabs use a single tempo; the penalty is set so steady songs (live drift included) stay one segment
while real tempo changes that last for many bars (prog / math rock) are kept.

sparse_sync: the fewest sync points such that every bar start stays within `tol` seconds of the straight line
between neighbouring sync points (plus the bars where the written tempo changes).
"""
from typing import List, Sequence, Tuple

import numpy as np

CAP = 0.08          # per-bar |log tempo ratio| cap (~8%): one mis-tracked bar can't force a new segment


def bar_tempi(bar_q: Sequence[float], bar_t: Sequence[float]) -> np.ndarray:
    q, t = np.asarray(bar_q, float), np.asarray(bar_t, float)
    return np.diff(q) * 60.0 / np.maximum(np.diff(t), 1e-3)


def tempo_segments(bar_q, bar_t, min_bars=8, penalty=1.5) -> List[Tuple[int, int]]:
    """-> [(first bar, integer BPM)] covering all bars."""
    tp = bar_tempi(bar_q, bar_t)
    n = len(tp)
    if n == 0:
        return [(0, 120)]
    lt = np.log(np.maximum(tp, 1.0))

    def seg_cost(i, j):
        med = np.median(lt[i:j])
        return float(np.minimum(np.abs(lt[i:j] - med), CAP).sum()), med
    if n < 2 * min_bars:
        return [(0, int(round(float(np.exp(np.median(lt))))))]
    best = np.full(n + 1, np.inf)
    arg = np.zeros(n + 1, int)
    best[0] = 0.0
    for j in range(min_bars, n + 1):
        for i in range(0, j - min_bars + 1):
            if not np.isfinite(best[i]) or (i and i < min_bars):
                continue
            c = best[i] + seg_cost(i, j)[0] + penalty
            if c < best[j]:
                best[j], arg[j] = c, i
    cuts = []
    j = n
    while j > 0:
        i = arg[j]
        cuts.append((i, j))
        j = i
    cuts.reverse()
    out = []
    for i, j in cuts:
        bpm = int(round(float(np.exp(seg_cost(i, j)[1]))))
        if out and abs(out[-1][1] - bpm) <= 1:
            continue                                   # merge segments that round to (almost) the same tempo
        out.append((i, bpm))
    return out


def sparse_sync(bar_q, bar_t, must=(), tol=0.035) -> List[int]:
    """Bar indices (0..n_bars-1) that get a sync point."""
    q, t = np.asarray(bar_q, float), np.asarray(bar_t, float)
    nb = len(q) - 1
    must = set(must) | {0}
    keep = [0]
    k = 0
    while k < nb:
        m = k + 1
        while m + 1 <= nb and not any(i in must for i in range(k + 1, m + 1)):
            cand = m + 1
            qq, tt = q[k:cand + 1], t[k:cand + 1]
            line = tt[0] + (qq - qq[0]) * (tt[-1] - tt[0]) / max(qq[-1] - qq[0], 1e-9)
            if np.abs(line - tt).max() > tol:
                break
            m = cand
        keep.append(m)
        k = m
    return sorted(set(i for i in keep if i < nb))
