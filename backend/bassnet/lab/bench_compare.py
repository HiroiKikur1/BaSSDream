"""Audio-level comparison on the test songs: BassNet raw notes vs YourMT3+ bass notes (mix or stem input).

python -m bassnet.lab.bench_compare
Same scoring for every system (score_eval.compare_notes: onset +-60 ms, exact pitch; plus pitch-class accuracy).
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from bassnet.eval_e2e import test_metas  # noqa: E402
from bassnet.eval_cached import load_post  # noqa: E402
from bassnet.decode import decode_notes  # noqa: E402
from bassnet.score_eval import compare_notes  # noqa: E402

EVAL = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "eval")


def ours(m):
    fr, on, de, be, do = load_post(os.path.basename(m["npz"]), [os.path.join(EVAL, c) for c in ("post_cache_v3", "post_cache_v4")])
    return [{"time": n["time"], "end": n["end"], "midi": n["midi"], "dead": n["dead"]} for n in decode_notes(fr, on, de)]


def ymt3(m, kind, bass_only=True):
    f = os.path.join(EVAL, f"ymt3_{kind}", os.path.basename(m["npz"])[:-4] + ".json")
    if not os.path.exists(f):
        return None
    rows = json.load(open(f))
    sel = [r for r in rows if not r["drum"] and (32 <= r["program"] <= 39 or not bass_only)]
    return [{"time": r["onset"], "end": r["offset"], "midi": r["pitch"], "dead": False} for r in sel]


def pc_acc(gt, est, tol=0.06):
    gt_t = np.array([n["time"] for n in gt])
    used = np.zeros(len(gt), bool)
    ok = 0
    for e in est:
        lo, hi = np.searchsorted(gt_t, e["time"] - tol), np.searchsorted(gt_t, e["time"] + tol, side="right")
        c = [j for j in range(lo, hi) if not used[j]]
        if c:
            j = min(c, key=lambda j: ((gt[j]["midi"] - e["midi"]) % 12 != 0, abs(gt_t[j] - e["time"])))
            used[j] = True
            ok += (gt[j]["midi"] - e["midi"]) % 12 == 0
    return ok / max(1, len(gt))


def main():
    systems = {"BassNet v3+v4 (stem)": lambda m: ours(m),
               "YourMT3+ (mix, bass program)": lambda m: ymt3(m, "mix"),
               "YourMT3+ (stem, all notes)": lambda m: ymt3(m, "stem", bass_only=False)}
    res = {k: [] for k in systems}
    for m in test_metas():
        gt = sorted([n for n in m["notes"] if not n.get("grace") and not n.get("dead")], key=lambda n: n["time"])
        beats = np.array(m["beats"])
        ests = {k: f(m) for k, f in systems.items()}
        if any(v is None for v in ests.values()):
            continue
        for k, est in ests.items():
            est = sorted([e for e in est if not e["dead"]], key=lambda n: n["time"])
            sc = compare_notes(gt, est, beats)
            sc["pc_acc"] = pc_acc(gt, est)
            sc["name"] = os.path.basename(os.path.dirname(m["gp"]))[:26]
            res[k].append(sc)
    n = len(next(iter(res.values())))
    print(f"songs compared: {n}")
    print(f"{'system':32s} {'pitch':>6s} {'pitch(any oct)':>14s} {'onsetR':>7s} {'onsetP':>7s} {'extra':>6s}")
    for k, rows in res.items():
        f = lambda key: np.mean([r[key] for r in rows])  # noqa: E731
        print(f"{k:32s} {f('pitch_acc'):6.3f} {f('pc_acc'):14.3f} {f('onset_recall'):7.3f} {f('onset_precision'):7.3f} {f('extra'):6.3f}")
    ks = list(res)
    print("\nper song pitch acc:", " | ".join(ks))
    for i in range(n):
        print(f"  {res[ks[0]][i]['name']:28s}", "  ".join(f"{res[k][i]['pitch_acc']:.2f}" for k in ks))


if __name__ == "__main__":
    main()
