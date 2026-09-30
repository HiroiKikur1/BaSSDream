"""Sweep decode/quantise settings against the notation metrics (original bars placed by the fixed alignment).

python -m bassnet.notation_sweep --split val --combine <cache> <cache> --grid '{"onset_thr": [0.4, 0.5]}'
Keys prefixed "q." go to quantize(), "grid_beats" feeds the decoded beats into decode_notes (weak on-grid onsets).
"""
import argparse
import itertools
import json
import os
import sys
from collections import Counter
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet import eval_cached  # noqa: E402
from bassnet.decode import decode_notes, decode_beats  # noqa: E402
from bassnet.gpif_parser import parse_gp  # noqa: E402
from bassnet.notation_eval import gt_score_bars, error_breakdown, compare_bars, score_bars  # noqa: E402
from bassnet.quantize import quantize, beat_time, TPB  # noqa: E402
from bassnet.fretboard import assign_frets  # noqa: E402
from bassnet.gp_writer import write_gp  # noqa: E402


def extra_notes(gt, est, tol=0.08):
    """Generated notes at positions where the original has none (in bars that line up); precision side."""
    et = np.array([b["t"] for b in est]) if est else np.zeros(0)
    gt_t = np.array([b["t"] for b in gt])
    n = 0
    for g in est:
        if not g["notes"] or not len(gt_t):
            continue
        j = int(np.argmin(np.abs(gt_t - g["t"])))
        b = gt[j]
        if abs(gt_t[j] - g["t"]) > tol or b["len"] != g["len"]:
            continue
        pos = {p for p, _, _, _ in b["notes"]}
        n += sum(p not in pos for p in {x[0] for x in g["notes"]})
    return n


BEATS = []


def run_song(args):
    m, caches, cfg, tag = args
    name = os.path.basename(m["npz"])
    fr, on, de, be, do = eval_cached.load_post(name, caches)
    if BEATS:
        _, _, _, be, do = eval_cached.load_post(name, BEATS)
    dec = {k: v for k, v in cfg.items() if "." not in k and k != "grid_beats"}
    notes = decode_notes(fr, on, de, **dec)
    bkw = {k[2:]: v for k, v in cfg.items() if k.startswith("b.")}
    hk = {k[2:]: v for k, v in cfg.items() if k.startswith("h.")}
    if hk:
        hk = {k: tuple(v) if isinstance(v, list) else v for k, v in hk.items()}
        bkw["hmm_kw"] = hk
    beats, downs, meter = decode_beats(be, do, notes=notes, **bkw)
    if cfg.get("grid_beats"):
        notes = decode_notes(fr, on, de, beats=beats, **dec)
    qs = quantize(notes, beats, downs, meter, **{k[2:]: v for k, v in cfg.items() if k.startswith("q.")})
    assign_frets(qs.notes, m["tuning"], times=[beat_time(qs.beats, qs.bar0 + n.tick / TPB) for n in qs.notes])
    tmp = os.path.join(caches[0], name.replace(".npz", f"_sw{tag}.gp"))
    write_gp(qs, tmp, "e", "e", None, m["tuning"])
    est = score_bars(parse_gp(tmp), qs.bar_time(0))
    os.remove(tmp)
    gt = gt_score_bars(m)
    r = compare_bars(gt, est)
    r["why"] = error_breakdown(gt, est)
    r["why"]["extra"] = extra_notes(gt, est)
    return r


def _init(beats):
    global BEATS
    BEATS = beats


def evaluate(metas, caches, cfg, pool, tag="0"):
    rows = pool.map(run_song, [(m, caches, cfg, tag) for m in metas])
    # (BEATS is passed to workers through the initializer)
    why = Counter()
    for r in rows:
        why.update(r["why"])
    tot = why.pop("total")
    return {"note": round(float(np.mean([r["notation_note_acc"] for r in rows])), 4),
            "bar_exact": round(float(np.mean([r["bar_exact"] for r in rows])), 4),
            **{k: round(v / tot, 4) for k, v in why.most_common() if k != "ok"}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val", choices=["val", "test"])
    ap.add_argument("--combine", nargs="+", required=True)
    ap.add_argument("--grid", default="{}")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--beats", nargs="*", default=[], help="beat/downbeat posteriors from these caches")
    a = ap.parse_args()
    eval_cached.SPLIT = a.split
    global BEATS
    BEATS = a.beats
    metas = [m for m in eval_cached.metas()
             if all(os.path.exists(os.path.join(c, os.path.basename(m["npz"]))) for c in a.combine)]
    grid = json.loads(a.grid)
    keys = list(grid)
    with Pool(a.workers, initializer=_init, initargs=(a.beats,)) as pool:
        for vals in itertools.product(*[grid[k] for k in keys]) if keys else [()]:
            cfg = dict(zip(keys, vals))
            print(json.dumps(cfg), evaluate(metas, a.combine, cfg, pool), flush=True)


if __name__ == "__main__":
    main()
