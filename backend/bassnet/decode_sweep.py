"""Decode-stage variants against the re-aligned tab notes (the stage where most notes are lost, loss_audit.py).

python -m bassnet.decode_sweep [--splits val,test]
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")


def reattacks(notes, cqt, beats, fps, rise=0.25, min_len=0.12):
    """Split sustained notes where the bass stem's energy at the note's own pitch jumps again on a grid slot
    (a re-pluck of the same string the onset head missed). Returns a new note list."""
    from bassnet.decode import grid_slots
    from bassnet.dataset import MIDI_LO, BINS_PER_SEMI
    slots = grid_slots(beats, subdivs=(2, 4))
    out = []
    for n in notes:
        if n.get("dead"):
            out.append(n)
            continue
        a, b = int(round(n["time"] * fps)), int(round(n["end"] * fps))
        if b - a < int(min_len * 2 * fps):
            out.append(n)
            continue
        k0 = (n["midi"] - MIDI_LO) * BINS_PER_SEMI
        env = cqt[max(0, k0 - 1):k0 + 2].max(0) + 0.5 * cqt[max(0, k0 + 35):k0 + 38].max(0)
        cuts = []
        for s in slots[(slots > a + int(min_len * fps)) & (slots < b - int(min_len * fps))]:
            pre = env[max(a, s - 4):s - 1].min() if s - 1 > a else env[s]
            post = env[s:s + 3].max()
            if post - pre > rise * max(1e-3, env[a:a + 4].max()):
                if not cuts or s - cuts[-1] >= int(min_len * fps):
                    cuts.append(int(s))
        t0 = a
        for c in cuts + [b]:
            m = dict(n)
            m["time"], m["end"] = t0 / fps, c / fps
            if t0 != a:
                m["on_peak"] = 0.0
                m["reattack"] = True
            out.append(m)
            t0 = c
    return out


def main():
    from bassnet.phase_audit import song_inputs
    from bassnet.decode import decode_notes, FPS
    from bassnet.loss_audit import f1
    from bassnet import pipeline
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", default="val,test")
    a = ap.parse_args()
    splits = set(a.splits.split(","))
    res = {}
    for m, split, (fr, on, de, be, do) in song_inputs():
        if split not in splits:
            continue
        key = os.path.basename(m["npz"])[:-4]
        lf = os.path.join(ROOT, "labels_v2", key + ".json")
        lab = json.load(open(lf, encoding="utf8"))["notes"] if os.path.exists(lf) else m["notes"]
        ref = sorted((n for n in lab if not n.get("grace") and not n.get("dead")), key=lambda n: n["time"])
        base = decode_notes(fr, on, de)
        beats, downs, meter, _ = pipeline.song_beats(be, do, base)
        cqt = np.load(m["npz"])["cqt"].astype(np.float32)
        variants = {
            "production": base,
            "grid weak 0.25": decode_notes(fr, on, de, beats=beats),
            "grid weak 0.15": decode_notes(fr, on, de, beats=beats, weak_thr=0.15),
            "onset thr 0.4": decode_notes(fr, on, de, onset_thr=0.4),
            "reattack 0.25": reattacks(base, cqt, beats, FPS, 0.25),
            "reattack 0.4": reattacks(base, cqt, beats, FPS, 0.4),
            "grid 0.25 + reattack 0.4": reattacks(decode_notes(fr, on, de, beats=beats), cqt, beats, FPS, 0.4),
        }
        for k, v in variants.items():
            est = sorted((n for n in v if not n.get("dead")), key=lambda n: n["time"])
            res.setdefault(k, []).append(f1(ref, est))
    for k, v in res.items():
        v = np.array(v)
        print(f"{k:26s} F1 {v[:, 0].mean():.4f}  P {v[:, 1].mean():.4f}  R {v[:, 2].mean():.4f}")


if __name__ == "__main__":
    main()
