"""Who is wrong when the model and the tab disagree on a pitch? Independent audio referee on the separated stem.

For every label note with a predicted onset within 50 ms but a different pitch, compare the stem's harmonic
evidence (audio_verify.d_here) for the tab pitch and for the predicted pitch. The referee's own reliability is
measured on notes where model and tab agree: how often would it prefer a wrong alternative at the same interval.

python -m bassnet.error_audit --split val --combine <cache> <cache>
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet import eval_cached  # noqa: E402
from bassnet.audio_verify import semitone_map, d_here  # noqa: E402
from bassnet.decode import decode_notes  # noqa: E402

MARGIN = 0.15


def kind(iv):
    a = abs(iv)
    if a % 12 == 0:
        return "octave"
    if a % 12 in (5, 7):
        return "4th/5th"
    if a % 12 in (1, 11):
        return "semitone"
    if a % 12 in (2, 10):
        return "tone"
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val", choices=["val", "test"])
    ap.add_argument("--combine", nargs="+", required=True)
    ap.add_argument("--per-song", action="store_true")
    a = ap.parse_args()
    eval_cached.SPLIT = a.split
    verdict = defaultdict(Counter)
    ref = defaultdict(Counter)
    n_lab = n_match = n_ok = 0
    songs = []
    for m in eval_cached.metas():
        name = os.path.basename(m["npz"])
        if not all(os.path.exists(os.path.join(c, name)) for c in a.combine):
            continue
        fr, on, de, be, do = eval_cached.load_post(name, a.combine)
        pred = decode_notes(fr, on, de)
        pt = np.array([p["time"] for p in pred])
        S = semitone_map(np.load(m["npz"])["cqt"].astype(np.float32))
        sv = Counter()
        for n in m["notes"]:
            if n.get("grace") or n.get("dead"):
                continue
            n_lab += 1
            j = int(np.argmin(np.abs(pt - n["time"]))) if len(pt) else -1
            if j < 0 or abs(pt[j] - n["time"]) > 0.05 or pred[j]["dead"]:
                continue
            n_match += 1
            L, P, t = int(n["midi"]), int(pred[j]["midi"]), n["time"]
            if L == P:
                n_ok += 1
                # referee reliability: would it wrongly prefer a typical error interval over the true pitch?
                for iv in (-12, 12, -7, 7, -5, 5, -1, 1):
                    d = d_here(S, L + iv, t) - d_here(S, L, t)
                    ref[kind(iv)]["wrong" if d > MARGIN else "right" if d < -MARGIN else "unsure"] += 1
                continue
            d = d_here(S, P, t) - d_here(S, L, t)
            v = "model" if d > MARGIN else "tab" if d < -MARGIN else "unsure"
            verdict[kind(P - L)][v] += 1
            sv[v] += 1
        songs.append((os.path.basename(os.path.dirname(m["gp"]))[:28], sv))
    print(f"label notes {n_lab}  matched onsets {n_match}  pitch agree {n_ok} ({n_ok / max(1, n_match):.3f})")
    tot = Counter()
    for k, c in sorted(verdict.items(), key=lambda x: -sum(x[1].values())):
        s = sum(c.values())
        tot.update(c)
        r = ref[k]
        rs = sum(r.values())
        print(f"  {k:9s} errors {s:5d} ({s / max(1, n_match):.3f})  audio favours model {c['model'] / s:.2f}  tab {c['tab'] / s:.2f}"
              f"  unsure {c['unsure'] / s:.2f}   | referee on correct notes: wrong {r['wrong'] / max(1, rs):.2f} right {r['right'] / max(1, rs):.2f}")
    s = sum(tot.values())
    print(f"  ALL errors {s} ({s / max(1, n_match):.3f}): model {tot['model'] / s:.2f} tab {tot['tab'] / s:.2f} unsure {tot['unsure'] / s:.2f}")
    if a.per_song:
        for nm, c in songs:
            print(f"    {nm:30s} {dict(c)}")


if __name__ == "__main__":
    main()
