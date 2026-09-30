"""String/fret assignment for a given tuning (Viterbi over hand position)."""
from typing import List


_LEARNED = None


def _learned():
    global _LEARNED
    if _LEARNED is None:
        import json
        import os
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "cache", "bassnet", "fingering.json")
        _LEARNED = False
        if os.path.exists(p):
            from bassnet.fingering_learn import LearnedCost
            _LEARNED = LearnedCost(json.load(open(p, encoding="utf8")))
    return _LEARNED


def assign_frets(notes: List, tuning: List[int], max_fret: int = 22, times=None):
    """notes: objects with .midi (and .tick/.dur); sets .string/.fret in place.
    Uses fingering habits learned from the tab library when available."""
    if not notes:
        return notes
    cost = _learned()
    if cost:
        from bassnet.fingering_learn import assign_learned
        if times is None:
            times = [getattr(n, "tick", i * 12) / 24 * 0.5 for i, n in enumerate(notes)]
        assign_learned(notes, tuning, cost, times)
        return notes
    cands = []
    for n in notes:
        c = [(s, n.midi - o) for s, o in enumerate(tuning) if 0 <= n.midi - o <= max_fret]
        if not c:
            s = 0 if n.midi < tuning[0] else len(tuning) - 1
            c = [(s, max(0, min(max_fret, n.midi - tuning[s])))]
        cands.append(c)

    def pos_cost(s, f):
        c = 0.0 if f == 0 else 0.05 * f + (0.3 if f > 12 else 0)
        if s == 0 and f >= 7:
            c += 0.6 + 0.1 * (f - 7)   # avoid high frets on the lowest string
        return c

    INF = float("inf")
    dp = [[pos_cost(s, f) for (s, f) in cands[0]]]
    bp = [[-1] * len(cands[0])]
    for i in range(1, len(notes)):
        row, brow = [], []
        for (s2, f2) in cands[i]:
            best, arg = INF, 0
            for k, (s1, f1) in enumerate(cands[i - 1]):
                if f1 == 0 or f2 == 0:
                    move = 0.1 * abs(s2 - s1)
                else:
                    df = abs(f2 - f1)
                    move = (0.0 if df <= 3 else 0.4 + 0.15 * (df - 3)) + 0.08 * abs(s2 - s1)
                v = dp[-1][k] + move
                if v < best:
                    best, arg = v, k
            row.append(best + pos_cost(s2, f2))
            brow.append(arg)
        dp.append(row)
        bp.append(brow)
    k = min(range(len(dp[-1])), key=lambda j: dp[-1][j])
    for i in range(len(notes) - 1, -1, -1):
        notes[i].string, notes[i].fret = cands[i][k]
        k = bp[i][k]
    return notes
