"""Quantiser + writer fidelity: GT notes/beats -> QScore -> .gp -> parse -> compare with GT."""
import paths
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.quantize import quantize  # noqa: E402
from bassnet.gp_writer import write_gp  # noqa: E402
from bassnet.gpif_parser import parse_gp  # noqa: E402
from bassnet.score_eval import compare_scores  # noqa: E402


def run(meta_path, out_dir):
    m = json.load(open(meta_path, encoding="utf8"))
    notes = [n for n in m["notes"] if not n.get("grace")]
    beats = np.array(m["beats"])
    downs = np.array(m["downbeats"])
    if len(beats) < 16:
        return None
    meter = int(round(np.median(np.diff([np.argmin(np.abs(beats - d)) for d in downs])))) if len(downs) > 2 else 4
    compound = m["time_sigs"][0][1] == 8 and m["time_sigs"][0][0] % 3 == 0
    qs = quantize(notes, beats, downs, meter, compound=compound)
    out = os.path.join(out_dir, os.path.basename(meta_path).replace(".json", ".gp"))
    tuning = m["tuning"]
    from bassnet.fretboard import assign_frets
    assign_frets(qs.notes, tuning)
    write_gp(qs, out, "rt", "rt", None, tuning, compound=compound)
    est = parse_gp(out)
    return compare_scores(m, est, audio_offset=qs.bar_time(0))


if __name__ == "__main__":
    out_dir = paths.cache(r"bassnet\roundtrip")
    os.makedirs(out_dir, exist_ok=True)
    rs = []
    for f in sorted(glob.glob(paths.cache(r"bassnet\feats\*.json")))[: int(sys.argv[1]) if len(sys.argv) > 1 else 50]:
        r = run(f, out_dir)
        if r:
            print(os.path.basename(os.path.dirname(json.load(open(f, encoding='utf8'))['gp']))[:30], {k: round(v, 4) if isinstance(v, float) else v for k, v in r.items()})
            rs.append(r)
    for k in ("pitch_acc", "onset_recall", "dur_acc"):
        print(k, np.mean([r[k] for r in rs]))
