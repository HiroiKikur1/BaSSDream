"""Learns string/fret habits from the tab library and evaluates agreement with human tabs.

Stats (saved to cache/bassnet/fingering.json):
  emit[nstr][string][fret]           how often a pitch is played at (string, fret), per 4/5-string
  trans[bucket][dfret][dstring]      hand movement between consecutive notes, by inter-onset gap
"""
import glob
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.gpif_parser import parse_gp  # noqa: E402

ROOT = r"E:\BassStation\cache\bassnet"
OUT = os.path.join(ROOT, "fingering.json")
MAXF = 24


def gap_bucket(dt):
    return 0 if dt < 0.16 else 1 if dt < 0.32 else 2 if dt < 0.7 else 3


def note_seq(info):
    return [n for n in info.notes if not n.grace and not n.dead and 0 <= n.fret <= MAXF]


def collect(exclude):
    emit = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    trans = defaultdict(lambda: defaultdict(float))
    for gp in glob.glob(r"E:\BassStation\tabs\*\*.gp"):
        if gp in exclude:
            continue
        try:
            info = parse_gp(gp)
        except Exception:
            continue
        ns = note_seq(info)
        k = str(len(info.tuning))
        for n in ns:
            emit[k][str(n.string)][str(n.fret)] += 1
        for a, b in zip(ns, ns[1:]):
            key = f"{gap_bucket(b.time - a.time)}|{max(-12, min(12, b.fret - a.fret))}|{b.string - a.string}|{int(a.fret == 0)}{int(b.fret == 0)}"
            trans[key] = trans.get(key, 0) + 1
    return {"emit": emit, "trans": trans}


class LearnedCost:
    def __init__(self, stats):
        self.emit = stats["emit"]
        self.trans = stats["trans"]
        self.tot = {}
        for k in ("0", "1", "2", "3"):
            self.tot[k] = sum(v for kk, v in self.trans.items() if kk.startswith(k + "|"))

    def emit_cost(self, nstr, s, f):
        e = self.emit.get(str(nstr), {})
        tot = sum(sum(v.values()) for v in e.values()) or 1
        c = e.get(str(s), {}).get(str(f), 0.0)
        return -math.log((c + 0.5) / (tot + 0.5 * nstr * MAXF))

    def trans_cost(self, dt, s1, f1, s2, f2):
        b = str(gap_bucket(dt))
        key = f"{b}|{max(-12, min(12, f2 - f1))}|{s2 - s1}|{int(f1 == 0)}{int(f2 == 0)}"
        c = self.trans.get(key, 0.0)
        return -math.log((c + 0.3) / (self.tot.get(b, 1) + 0.3 * 25 * 9 * 4))


def assign_learned(notes, tuning, cost, times):
    cands = []
    for n in notes:
        c = [(s, n.midi - o) for s, o in enumerate(tuning) if 0 <= n.midi - o <= MAXF]
        if not c:
            s = 0 if n.midi < tuning[0] else len(tuning) - 1
            c = [(s, max(0, min(MAXF, n.midi - tuning[s])))]
        cands.append(c)
    ns = len(tuning)
    dp = [[cost.emit_cost(ns, s, f) for s, f in cands[0]]]
    bp = [[-1] * len(cands[0])]
    for i in range(1, len(notes)):
        dt = times[i] - times[i - 1]
        row, brow = [], []
        for s2, f2 in cands[i]:
            best, arg = float("inf"), 0
            for k, (s1, f1) in enumerate(cands[i - 1]):
                v = dp[-1][k] + cost.trans_cost(dt, s1, f1, s2, f2)
                if v < best:
                    best, arg = v, k
            row.append(best + cost.emit_cost(ns, s2, f2))
            brow.append(arg)
        dp.append(row)
        bp.append(brow)
    k = min(range(len(dp[-1])), key=lambda j: dp[-1][j])
    for i in range(len(notes) - 1, -1, -1):
        notes[i].string, notes[i].fret = cands[i][k]
        k = bp[i][k]


def evaluate(test_gps, stats):
    from copy import deepcopy
    from bassnet.fretboard import assign_frets
    cost = LearnedCost(stats)
    agree = {"heuristic": [0, 0], "learned": [0, 0]}
    for gp in test_gps:
        info = parse_gp(gp)
        ns = note_seq(info)
        if len(ns) < 20:
            continue
        truth = [(n.string, n.fret) for n in ns]
        h = deepcopy(ns)
        assign_frets(h, info.tuning)
        lr = deepcopy(ns)
        assign_learned(lr, info.tuning, cost, [n.time for n in ns])
        for name, arr in (("heuristic", h), ("learned", lr)):
            agree[name][0] += sum((n.string, n.fret) == t for n, t in zip(arr, truth))
            agree[name][1] += len(arr)
    return {k: v[0] / max(1, v[1]) for k, v in agree.items()}


if __name__ == "__main__":
    split_path = os.path.join(ROOT, "test_split.json")
    test = set(json.load(open(split_path, encoding="utf8"))) if os.path.exists(split_path) else set()
    if not test:
        import random
        allg = sorted(glob.glob(r"E:\BassStation\tabs\*\*.gp"))
        random.Random(3).shuffle(allg)
        test = set(allg[:30])
    stats = collect(test)
    json.dump(stats, open(OUT, "w", encoding="utf8"))
    print("agreement with human tabs on held-out:", evaluate(sorted(test), stats))
