"""Technique detection quality (slide / hammer-on / slap / pop) on cached posteriors.

python -m bassnet.eval_tech --split val --cache DIR [DIR ...] [--tune]   # --tune writes cache/bassnet/tech_thr.json
python -m bassnet.eval_tech --split test --cache DIR [DIR ...]           # report with the tuned thresholds
Notes are matched to the original tab by onset (+-50 ms) and pitch; each technique is scored per matched note.
"""
import paths
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.decode import decode_notes  # noqa: E402
from bassnet.model import TECH_NAMES  # noqa: E402

THR_FILE = paths.cache(r"bassnet\tech_thr.json")


def gt_flags(gt, i):
    n, p = gt[i], gt[i - 1] if i > 0 else None
    sl = int(n.get("slide", 0) or 0)
    return [bool(p is not None and int(p.get("slide", 0) or 0) & 3), bool(sl & 4), bool(sl & 8), bool(sl & 48),
            bool(n.get("hopo_d")), bool(n.get("slap")), bool(n.get("pop"))]


def collect(metas, caches):
    from bassnet.eval_cached import load_post
    from bassnet.decode import FPS
    P, Y = [], []
    for m in metas:
        name = os.path.basename(m["npz"])
        if not all(os.path.exists(os.path.join(c, name)) for c in caches):
            continue
        tes = []
        for c in caches:
            d = np.load(os.path.join(c, name))
            if "te" in d:
                tes.append(d["te"].astype(np.float32))
        if not tes:
            continue
        te = np.mean(tes, 0)
        fr, on, de, be, do = load_post(name, caches)
        notes = [n for n in decode_notes(fr, on, de) if not n["dead"]]
        gt = [n for n in m["notes"] if not n.get("grace") and not n.get("dead")]
        gt_t = np.array([n["time"] for n in gt])
        for n in notes:
            j = np.searchsorted(gt_t, n["time"])
            cs = [k for k in (j - 1, j) if 0 <= k < len(gt) and abs(gt_t[k] - n["time"]) <= 0.05 and gt[k]["midi"] == n["midi"]]
            if not cs:
                continue
            a = int(round(n["time"] * FPS))
            P.append(te[max(0, a - 1):a + 3].max(0))
            Y.append(gt_flags(gt, cs[0]))
    return np.array(P), np.array(Y, bool)


def prf(p, y, t):
    pr = p >= t
    tp = (pr & y).sum()
    P = tp / max(1, pr.sum())
    R = tp / max(1, y.sum())
    return P, R, 2 * P * R / max(1e-9, P + R)


def main():
    from bassnet.eval_e2e import test_metas
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val", choices=["val", "test"])
    ap.add_argument("--cache", nargs="+", required=True)
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--min-precision", type=float, default=0.6)
    a = ap.parse_args()
    if a.split == "val":
        from bassnet.tune_decode import val_metas
        metas = val_metas()
    else:
        metas = test_metas()
    P, Y = collect(metas, a.cache)
    print(f"matched notes: {len(Y)}")
    thr = json.load(open(THR_FILE)) if os.path.exists(THR_FILE) and not a.tune else [0.5] * len(TECH_NAMES)
    for k, name in enumerate(TECH_NAMES):
        if a.tune:
            # precision first: a wrong technique mark is worse than a missing one; 1.01 disables the technique
            grid = np.arange(0.1, 0.96, 0.05)
            ok = [t for t in grid if Y[:, k].sum() >= 5 and (P[:, k] >= t).sum() >= 3
                  and prf(P[:, k], Y[:, k], t)[0] >= a.min_precision]
            thr[k] = float(round(max(ok, key=lambda t: prf(P[:, k], Y[:, k], t)[2]), 2)) if ok else 1.01
        p, r, f = prf(P[:, k], Y[:, k], thr[k])
        print(f"  {name:16s} n={int(Y[:, k].sum()):5d} thr={thr[k]:.2f}  P={p:.2f} R={r:.2f} F1={f:.2f}")
    if a.tune:
        json.dump(thr, open(THR_FILE, "w"))
        print("saved", THR_FILE)


if __name__ == "__main__":
    main()
