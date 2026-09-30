"""Where does notation accuracy get lost? Swap model outputs for ground truth one stage at a time.

  PP: predicted notes + predicted beats (what ships)
  PG: predicted notes + true beats/downbeats      -> gain = beat/bar tracking loss
  GP: true notes (audio time) + predicted beats   -> gain = note detection loss
  GG: true notes + true beats                     -> ceiling of the quantiser itself

python -m bassnet.lab.notation_oracle --split val --combine <cache> <cache>
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from bassnet import eval_cached  # noqa: E402
from bassnet.decode import decode_notes, decode_beats  # noqa: E402
from bassnet.quantize import quantize, beat_time, TPB  # noqa: E402
from bassnet.fretboard import assign_frets  # noqa: E402
from bassnet.gp_writer import write_gp  # noqa: E402
from bassnet.gpif_parser import parse_gp  # noqa: E402
from bassnet.notation_eval import score_bars, compare_bars, gt_score_bars, label_q_map, error_breakdown, posterior_q_map, fixed_q_map  # noqa: E402

KEYS = ("barline", "meter_acc", "bar_exact", "bar_rhythm", "notation_note_acc", "note_pos_pitch", "note_pos")


def gt_notes(m):
    return [{"time": n["time"], "end": n["end"], "midi": n["midi"], "dead": n["dead"]}
            for n in m["notes"] if not n.get("grace")]


def gt_beats(m, info=None, qm=None):
    """True beats/downbeats built from the original score's bars, on the same aligned timeline as the GT bars."""
    from fractions import Fraction
    info = info or parse_gp(m["gp"])
    qm = qm or fixed_q_map(m, info) or label_q_map(m)
    beats, downs = [], []
    for bi, oc, q0, ql in info.bars:
        num, den = info.time_sigs[bi]
        # the quantiser's beat is a quarter (simple) or dotted quarter (compound), whatever the denominator
        step = Fraction(3, 2) if (den == 8 and num % 3 == 0) else Fraction(1) if den <= 4 else Fraction(1, 2)
        downs.append(qm(q0))
        k = Fraction(0)
        while k < ql:
            beats.append(qm(q0 + k))
            k += step
    num, den = info.time_sigs[0]
    return np.array(beats), np.array(downs), (num // 3 if den == 8 and num % 3 == 0 else num * 4 // den if den <= 4 else num)


def score(m, notes, beats, downs, meter, tmp, gt_bars, q_kw=None):
    qs = quantize(notes, beats, downs, meter, **(q_kw or {}))
    tuning = m["tuning"]
    assign_frets(qs.notes, tuning, times=[beat_time(qs.beats, qs.bar0 + n.tick / TPB) for n in qs.notes])
    write_gp(qs, tmp, "e", "e", None, tuning)
    est = score_bars(parse_gp(tmp), qs.bar_time(0))
    r = compare_bars(gt_bars, est)
    r["why"] = error_breakdown(gt_bars, est)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val", choices=["val", "test"])
    ap.add_argument("--combine", nargs="+", required=True)
    ap.add_argument("--modes", nargs="*", default=["PP", "PG", "GP", "GG"])
    ap.add_argument("--per-song", action="store_true")
    ap.add_argument("--gt-align", action="store_true", help="place the original's bars with score_align instead of label times")
    a = ap.parse_args()
    eval_cached.SPLIT = a.split
    rows = {k: [] for k in a.modes}
    for m in eval_cached.metas():
        name = os.path.basename(m["npz"])
        if not all(os.path.exists(os.path.join(c, name)) for c in a.combine):
            continue
        fr, on, de, be, do = eval_cached.load_post(name, a.combine)
        pn = decode_notes(fr, on, de)
        pb = decode_beats(be, do)
        info = parse_gp(m["gp"])
        qm = posterior_q_map(info, (fr, on, de, be, do)) if a.gt_align else None
        gb = gt_beats(m, info, qm)
        gt_bars = gt_score_bars(m, info, qm)
        tmp = os.path.join(a.combine[0], name.replace(".npz", "_oracle.gp"))
        for mode in a.modes:
            notes = pn if mode[0] == "P" else gt_notes(m)
            beats = pb if mode[1] == "P" else gb
            r = score(m, notes, *beats, tmp, gt_bars)
            r["name"] = os.path.basename(os.path.dirname(m["gp"]))[:28]
            rows[mode].append(r)
    for mode, rs in rows.items():
        print(mode, len(rs), {k: round(float(np.mean([r[k] for r in rs])), 3) for k in KEYS}, flush=True)
    from collections import Counter
    for mode, rs in rows.items():
        tot = Counter()
        for r in rs:
            tot.update(r["why"])
        n = tot.pop("total")
        print(mode, "WHY", {k: round(v / n, 3) for k, v in tot.most_common()})
    if a.per_song:
        for i, r in enumerate(rows[a.modes[0]]):
            print(f"  {r['name']:30s}", " ".join(f"{md}={rows[md][i]['notation_note_acc']:.2f}/{rows[md][i]['barline']:.2f}"
                                               for md in a.modes))


if __name__ == "__main__":
    main()
