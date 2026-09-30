"""Repetition consensus: bars that repeat (verse/chorus) pool their posteriors in beat space.

Per-occurrence recognition errors are mostly independent, so a weighted vote across
similar bars removes them, while genuinely different bars (low similarity) are untouched.
"""
import numpy as np

from bassnet.decode import FPS

GRID = 12   # samples per beat for the similarity signature


def _bars(beats, downbeats):
    beats = np.asarray(beats)
    db = [int(np.argmin(np.abs(beats - d))) for d in downbeats]
    db = sorted(set(db))
    return [(db[i], db[i + 1]) for i in range(len(db) - 1) if db[i + 1] > db[i]]


def _time_at(beats, pos):
    """pos: fractional beat index -> seconds (linear between beats)."""
    i = np.clip(np.floor(pos).astype(int), 0, len(beats) - 2)
    return beats[i] + (pos - i) * (beats[i + 1] - beats[i])


def apply_consensus(frame_prob, onset, beats, downbeats, thr=0.55, beta=1.0, pool_onsets=True):
    beats = np.asarray(beats, float)
    T = len(onset)
    if len(beats) < 8 or len(downbeats) < 4:
        return frame_prob, onset
    bars = _bars(beats, downbeats)
    if len(bars) < 4:
        return frame_prob, onset

    # signature of each bar on a beat grid
    sigs, lens = [], []
    for b0, b1 in bars:
        n = b1 - b0
        pos = b0 + np.arange(n * GRID) / GRID
        fr = np.clip(np.round(_time_at(beats, pos) * FPS).astype(int), 0, T - 1)
        sigs.append(np.sqrt(frame_prob[fr]))          # sqrt -> Bhattacharyya via dot product
        lens.append(n)
    nb = len(bars)
    active = [float(1 - np.mean(s[:, 0] ** 2)) > 0.3 for s in sigs]   # skip mostly-rest bars

    out_p = frame_prob.copy()
    out_o = onset.copy()
    for i in range(nb):
        if not active[i]:
            continue
        nbrs = []
        for j in range(nb):
            if j == i or lens[j] != lens[i] or not active[j]:
                continue
            sim = float(np.mean(np.sum(sigs[i] * sigs[j], axis=1)))
            if sim > thr:
                nbrs.append((j, (sim - thr) / (1 - thr)))
        if not nbrs:
            continue
        b0, b1 = bars[i]
        t_start, t_end = beats[b0], beats[b1] if b1 < len(beats) else beats[-1]
        frames = np.arange(int(np.ceil(t_start * FPS)), min(T, int(t_end * FPS)))
        if len(frames) == 0:
            continue
        # fractional beat position of each frame inside bar i
        tt = frames / FPS
        k = np.clip(np.searchsorted(beats, tt, side="right") - 1, b0, b1 - 1)
        rel = (k - b0) + (tt - beats[k]) / (beats[k + 1] - beats[k])
        acc_p = frame_prob[frames].copy()
        acc_o = onset[frames].copy()
        wsum = 1.0
        for j, w in nbrs:
            c0 = bars[j][0]
            tj = _time_at(beats, c0 + rel)
            fj = np.clip(np.round(tj * FPS).astype(int), 0, T - 1)
            acc_p += beta * w * frame_prob[fj]
            if pool_onsets:
                # tolerate small timing differences between occurrences (+-1 frame)
                o = np.maximum(onset[fj], np.maximum(onset[np.clip(fj - 1, 0, T - 1)], onset[np.clip(fj + 1, 0, T - 1)]))
                acc_o += beta * w * o
            wsum += beta * w
        out_p[frames] = acc_p / wsum
        if pool_onsets:
            out_o[frames] = acc_o / wsum
    out_p /= out_p.sum(axis=1, keepdims=True)
    return out_p, out_o
