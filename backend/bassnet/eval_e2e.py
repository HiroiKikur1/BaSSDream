"""End-to-end evaluation on held-out songs: model -> decode -> quantise -> .gp -> parse -> compare with the original tab.

Usage: python -m bassnet.eval_e2e [--limit N] [--no-consensus] [--tag NAME]
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths  # noqa: E402
from bassnet.decode import decode_notes, decode_beats, note_metrics  # noqa: E402
from bassnet.quantize import quantize  # noqa: E402
from bassnet.fretboard import assign_frets  # noqa: E402
from bassnet.gp_writer import write_gp  # noqa: E402
from bassnet.gpif_parser import parse_gp  # noqa: E402
from bassnet.score_eval import compare_scores  # noqa: E402
from bassnet import pipeline  # noqa: E402

ROOT = paths.cache(r"bassnet")
OUT = os.path.join(ROOT, "eval")


def test_metas(limit=None):
    split = json.load(open(os.path.join(ROOT, "test_split.json"), encoding="utf8"))
    metas = []
    for f in sorted(os.listdir(os.path.join(ROOT, "feats"))):
        if not f.endswith(".json"):
            continue
        lf = os.path.join(os.environ.get("BASSNET_LABELS", ""), f) if os.environ.get("BASSNET_LABELS") else ""
        m = json.load(open(lf if lf and os.path.exists(lf) else os.path.join(ROOT, "feats", f), encoding="utf8"))
        if m["gp"] in split and m["rate_final"] >= 0.85:
            m["npz"] = os.path.join(ROOT, "feats", f.replace(".json", ".npz"))
            metas.append(m)
    return metas[:limit] if limit else metas


def event_f1(ref, est, tol=0.07):
    ref, est = np.sort(np.asarray(ref, float)), np.sort(np.asarray(est, float))
    if len(ref) == 0 or len(est) == 0:
        return 0.0
    lo, hi = ref[0] - 1, ref[-1] + 1
    est = est[(est >= lo) & (est <= hi)]
    used = np.zeros(len(ref), bool)
    hit = 0
    for e in est:
        j = np.searchsorted(ref, e)
        for k in (j - 1, j):
            if 0 <= k < len(ref) and not used[k] and abs(ref[k] - e) <= tol:
                used[k] = True
                hit += 1
                break
    return 2 * hit / (len(ref) + len(est))


def run_song(m, consensus=True, use_lm=False, dev="cuda"):
    d = np.load(m["npz"])
    cqt, mel = d["cqt"].astype(np.float32), d["mel"].astype(np.float32)
    fr, on, de, be, do = pipeline.posteriors(cqt, mel, dev)
    if consensus:
        from bassnet.consensus import apply_consensus
        beats0, downs0, _ = decode_beats(be, do)
        fr, on = apply_consensus(fr, on, beats0, downs0)
    notes = decode_notes(fr, on, de)
    if use_lm:
        from bassnet.lm import rescore
        notes = rescore(notes, fr)
    beats, downs, meter = decode_beats(be, do)
    gt = [n for n in m["notes"] if not n.get("grace") and not n.get("dead")]
    t0, t1 = gt[0]["time"] - 1.0, gt[-1]["end"] + 1.0
    raw = note_metrics(gt, [n for n in notes if not n["dead"] and t0 <= n["time"] <= t1])
    qs = quantize(notes, beats, downs, meter)
    tuning = m["tuning"] if m.get("tuning") else pipeline.choose_tuning(notes)
    from bassnet.quantize import beat_time, TPB
    assign_frets(qs.notes, tuning, times=[beat_time(qs.beats, qs.bar0 + n.tick / TPB) for n in qs.notes])
    out = os.path.join(OUT, os.path.basename(m["npz"]).replace(".npz", ".gp"))
    write_gp(qs, out, "eval", "eval", None, tuning)
    sc = compare_scores(m, parse_gp(out), audio_offset=qs.bar_time(0))
    sc["beatF1"] = event_f1(m.get("beats", []), beats)
    sc["downF1"] = event_f1(m.get("downbeats", []), downs)
    return raw, sc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-consensus", action="store_true")
    ap.add_argument("--tag", default="")
    ap.add_argument("--lm", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for m in test_metas(a.limit):
        t = time.time()
        raw, sc = run_song(m, consensus=not a.no_consensus, use_lm=a.lm)
        name = os.path.basename(os.path.dirname(m["gp"]))[:30]
        print(f"{name:32s} noteF1={raw['F1']:.3f} | GP pitch={sc['pitch_acc']:.3f} onsetR={sc['onset_recall']:.3f} "
              f"dur={sc['dur_acc']:.3f} note={sc['note_acc']:.3f} extra={sc['extra']:.3f} beat={sc['beatF1']:.2f} down={sc['downF1']:.2f} ({time.time() - t:.0f}s)", flush=True)
        rows.append({"name": name, "raw": raw, "gp": sc})
    agg = {k: float(np.mean([r["gp"][k] for r in rows])) for k in ("pitch_acc", "onset_recall", "dur_acc", "note_acc", "extra", "beatF1", "downF1")}
    agg["noteF1"] = float(np.mean([r["raw"]["F1"] for r in rows]))
    print("MEAN", json.dumps({k: round(v, 4) for k, v in agg.items()}))
    json.dump({"agg": agg, "rows": rows}, open(os.path.join(OUT, f"results{a.tag}.json"), "w", encoding="utf8"),
              ensure_ascii=False, indent=1, default=float)


if __name__ == "__main__":
    main()
