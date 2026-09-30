"""EM-style label refinement: move every GP label note to where the current model hears it.

For each training song, per-note shifts within +-60 ms maximise onset_prob * pitch_prob; shifts are
median-smoothed along the song (tab sync errors are smooth, not per-note), then written back to
the feature json (original times kept under "time_gp").
"""
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import paths  # noqa: E402
from bassnet.decode import FPS  # noqa: E402
from bassnet.model import PITCH_LO  # noqa: E402

ROOT = paths.cache(r"bassnet")


def refine_song(meta, fr, on, max_shift=0.06, smooth=9):
    notes = meta["notes"]
    T = len(on)
    steps = np.arange(-int(max_shift * FPS), int(max_shift * FPS) + 1)
    shifts = []
    idx = []
    for i, n in enumerate(notes):
        if n.get("grace") or n.get("dead"):
            continue
        cls = n.get("midi", 0) - PITCH_LO + 1
        a = int(round(n.get("time_gp", n["time"]) * FPS))
        if not (1 <= cls < fr.shape[1]) or a + steps[0] < 0 or a + steps[-1] + 3 >= T:
            continue
        sc = [on[a + k] * fr[a + k + 1:a + k + 4, cls].mean() for k in steps]
        best = int(np.argmax(sc))
        conf = sc[best]
        shifts.append(steps[best] / FPS if conf > 0.05 else np.nan)
        idx.append(i)
    if not idx:
        return meta, 0.0
    sh = np.array(shifts, float)
    good = ~np.isnan(sh)
    if good.sum() < 5:
        return meta, 0.0
    sh[~good] = np.interp(np.flatnonzero(~good), np.flatnonzero(good), sh[good])
    from scipy.signal import medfilt
    k = smooth if smooth % 2 else smooth + 1
    sh = medfilt(sh, min(k, len(sh) // 2 * 2 - 1 if len(sh) > 2 else 1))
    for i, s in zip(idx, sh):
        n = notes[i]
        n.setdefault("time_gp", n["time"])
        n.setdefault("end_gp", n["end"])
        n["time"] = n["time_gp"] + float(s)
        n["end"] = n["end_gp"] + float(s)
    return meta, float(np.mean(np.abs(sh)))


def main(ckpt_glob=os.path.join(ROOT, "bassnet*.pt")):
    import torch
    from bassnet.model import BassNet
    from bassnet.train import infer_full
    dev = "cuda"
    from bassnet.model import load_checkpoint
    models = [load_checkpoint(p, dev) for p in sorted(glob.glob(ckpt_glob))]
    test = set(json.load(open(os.path.join(ROOT, "test_split.json"), encoding="utf8")))
    moved = []
    for f in sorted(glob.glob(os.path.join(ROOT, "feats", "*.json"))):
        meta = json.load(open(f, encoding="utf8"))
        if meta["gp"] in test:
            continue          # never touch evaluation labels
        d = np.load(f.replace(".json", ".npz"))
        cqt, mel = d["cqt"].astype(np.float32), d["mel"].astype(np.float32)
        cqt /= np.percentile(cqt, 99.5) + 1e-3
        mel /= np.percentile(mel, 99.5) + 1e-3
        fr = on = None
        for m in models:
            o = infer_full(m, cqt, mel, dev)
            fr = o[0] if fr is None else fr + o[0]
            on = o[1] if on is None else on + o[1]
        meta, mv = refine_song(meta, fr / len(models), on / len(models))
        json.dump(meta, open(f, "w", encoding="utf8"), ensure_ascii=False)
        moved.append(mv)
    print(f"refined {len(moved)} songs, mean |shift| = {1000 * np.mean(moved):.1f} ms")


if __name__ == "__main__":
    main()
