"""Written bar lines and time signatures against the tabs, after quantisation (all 278 songs, honest posteriors).

recall     tab bar lines with one of our bar lines within 70 ms
precision  our bar lines (inside the tab's span) on a tab bar line
meter      share of our bars whose length (in quarters) equals the tab bar they start on
by category: songs whose tabs are 4/4 only, have 3/4 sections, or compound / odd meters.

python -m bassnet.meter_eval --variants simple,song_split
"""
import argparse
import collections
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VARIANTS = {
    "simple": dict(meter_mode="simple"),
    "song": dict(meter_mode="song"),
    "song_split": dict(meter_mode="song", split_compound=True),
}


def main():
    from bassnet.phase_audit import song_inputs, tab_grid
    from bassnet.quantize import quantize, TPB
    from bassnet import pipeline
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="simple,song_split")
    a = ap.parse_args()
    vs = a.variants.split(",")
    S = collections.defaultdict(lambda: collections.Counter())
    for m, split, (fr, on, de, be, do) in song_inputs():
        try:
            info, qm, bars, first = tab_grid(m, split)
        except Exception:
            continue
        lens = sorted({ql for _, ql in bars})
        cat = "4/4 only" if lens == [4.0] else ("compound/odd" if any(l not in (3.0, 4.0, 2.0) for l in lens) else "3/4")
        from bassnet.decode import decode_notes
        notes = decode_notes(fr, on, de)
        beats, downs, meter, _ = pipeline.song_beats(be, do, notes)
        tb = np.array([t for t, _ in bars])
        tl = np.array([ql for _, ql in bars])
        for v in vs:
            qs = quantize(notes, beats, downs, meter, **VARIANTS[v])
            ob = np.array([qs.bar_time(b) for b in range(qs.n_bars)])
            inside = ob[(ob >= tb[0] - 0.05) & (ob <= tb[-1] + 0.05)]
            rec = sum(np.abs(ob - t).min() < 0.07 for t in tb)
            prec = sum(np.abs(tb - t).min() < 0.07 for t in inside)
            mok = mtot = 0
            for b in range(qs.n_bars):
                t = qs.bar_time(b)
                j = int(np.argmin(np.abs(tb - t)))
                if abs(tb[j] - t) < 0.07:
                    q = qs.meter_of(b) * (1.5 if qs.bar_compound[b] else 1.0)
                    mtot += 1
                    mok += abs(q - tl[j]) < 1e-6
            for c in (cat, "all"):
                S[v, c].update({"rec": rec, "n_tab": len(tb), "prec": prec, "n_ours": len(inside), "mok": mok,
                                "mtot": mtot, "songs": 1})
    for v in vs:
        for c in ("all", "4/4 only", "3/4", "compound/odd"):
            s = S[v, c]
            print(f"{v:11s} {c:13s} songs {s['songs']:3d}  recall {s['rec'] / max(1, s['n_tab']):.4f}  "
                  f"precision {s['prec'] / max(1, s['n_ours']):.4f}  meter {s['mok'] / max(1, s['mtot']):.4f}")


if __name__ == "__main__":
    main()
