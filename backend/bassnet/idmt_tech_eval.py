"""Technique check on IDMT-SMT-Bass (≈4300 real single notes, 5 plucking x 11 expression styles; evaluation only).

File names: BS_<bass>_EQ_<eq>_<plucking>_<expression>_<string 1..4>_<fret 0..12>.wav, standard tuning E1 A1 D2 G2.
Per style: is a note found, is its pitch right (harmonics excepted), and does the technique head flag it:
  plucking ST (slap thumb) -> slap, SP (slap pop) -> pop; expression SLD / SLU -> a slide flag, DN -> dead note.
The clean-note case (FS + NO) doubles as the false-alarm rate of every flag.

python -m bassnet.idmt_tech_eval [--per-class 150]
"""
import argparse
import glob
import os
import re
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join("D:" + os.sep, "BassData", "IDMT-SMT-BASS")
PAT = re.compile(r"BS_(\d+)_EQ_(\d+)_(?:PS_)?([A-Z]+)_(?:ES_)?([A-Z]+)_(\d)_(\d+)\.wav$")


def main():
    import librosa
    from bassnet.dataset import compute_cqt, compute_mel
    from bassnet.pipeline import posteriors, attach_techniques
    from bassnet.decode import decode_notes
    from bassnet.thermal import guard, lower_priority
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=150)
    ap.add_argument("--only", nargs="*", default=[], help="expression styles to run, e.g. SLD SLU")
    a = ap.parse_args()
    lower_priority()
    files = defaultdict(list)
    for f in glob.glob(os.path.join(ROOT, "*", "*", "*.wav")):
        m = PAT.search(os.path.basename(f))
        if m:
            files[(m.group(3), m.group(4))].append((f, m))
    rng = np.random.default_rng(0)
    stats = defaultdict(lambda: defaultdict(int))
    for (ps, es), lst in sorted(files.items()):
        if a.only and es not in a.only:
            continue
        pick = [lst[i] for i in rng.permutation(len(lst))[:a.per_class]]
        for f, m in pick:
            guard()
            y, _ = librosa.load(f, sr=22050, mono=True)
            y = np.pad(y, (2205, 11025))                              # a little silence around the note
            cqt, mel = compute_cqt(y), compute_mel(y)
            T = min(cqt.shape[1], mel.shape[1])
            fr, on, de, be, do, te = posteriors(cqt[:, :T], mel[:, :T], with_tech=True)
            notes = decode_notes(fr, on, de)
            if te is not None:
                attach_techniques(notes, te)
            s = stats[f"{ps}/{es}"]
            s["n"] += 1
            if not notes:
                continue
            n = max(notes, key=lambda x: x["end"] - x["time"])
            s["found"] += 1
            want = 28 + 5 * (int(m.group(5)) - 1) + int(m.group(6))
            s["pitch"] += int(n["midi"] == want)
            s["octave"] += int(n["midi"] != want and (n["midi"] - want) % 12 == 0)
            s["dead"] += int(bool(n.get("dead")))
            s["slap"] += int(any(x.get("slap") for x in notes))
            s["pop"] += int(any(x.get("pop") for x in notes))
            s["slide"] += int(any(x.get("slide", 0) for x in notes))
            s["extra"] += len(notes) - 1
    print(f"{'style':8s} {'n':>4s} found pitch  oct   dead  slap  pop   slide  extra-notes")
    for k, s in sorted(stats.items()):
        n = max(1, s["n"])
        print(f"{k:8s} {s['n']:4d} {s['found'] / n:.2f}  {s['pitch'] / n:.2f}  {s['octave'] / n:.2f}  "
              f"{s['dead'] / n:.2f}  {s['slap'] / n:.2f}  {s['pop'] / n:.2f}  {s['slide'] / n:.2f}   {s['extra'] / n:.2f}")


if __name__ == "__main__":
    main()
