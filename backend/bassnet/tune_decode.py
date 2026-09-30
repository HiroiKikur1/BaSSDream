"""Grid-search decode parameters on validation songs using a checkpoint (posteriors computed once)."""
import glob
import itertools
import json
import os
import random
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.model import BassNet  # noqa: E402
from bassnet.train import load_metas, split_songs, song_key, infer_full, ROOT  # noqa: E402
from bassnet.decode import decode_notes, note_metrics, decode_beats  # noqa: E402


def val_metas():
    tr, _ = split_songs(load_metas())
    keys = sorted({song_key(m["gp"]) for m in tr})
    random.Random(11).shuffle(keys)
    from bassnet.train import SPLIT_FILE, frozen_keys
    vk = frozen_keys("val") if os.path.exists(SPLIT_FILE) else set(keys[:max(3, len(keys) // 12)])
    return [m for m in tr if song_key(m["gp"]) in vk]


def main(ckpt=os.path.join(ROOT, "bassnet.pt"), n=10):
    dev = "cuda"
    from bassnet.model import load_checkpoint
    model = load_checkpoint(ckpt, dev)
    posts = []
    for m in val_metas()[:n]:
        d = np.load(m["npz"])
        c, ml = d["cqt"].astype(np.float32), d["mel"].astype(np.float32)
        c /= np.percentile(c, 99.5) + 1e-3
        ml /= np.percentile(ml, 99.5) + 1e-3
        fr, on, de, be, do = infer_full(model, c, ml, dev)
        ref = [x for x in m["notes"] if not x.get("grace") and not x.get("dead")]
        beats, _, _ = decode_beats(be, do)
        posts.append((fr, on, de, ref, beats))
        print("onset prob pct 50/90/99:", np.percentile(on, [50, 90, 99]).round(3), flush=True)
    res = []
    for thr, wthr, use_grid in itertools.product((0.5, 0.6), (0.2, 0.25, 0.3, 0.35), (False, True)):
        f1 = []
        for fr, on, de, ref, beats in posts:
            est = [e for e in decode_notes(fr, on, de, onset_thr=thr, beats=beats if use_grid else None, weak_thr=wthr) if not e["dead"]]
            t0, t1 = ref[0]["time"] - 1, ref[-1]["end"] + 1
            f1.append(note_metrics(ref, [e for e in est if t0 <= e["time"] <= t1]))
        agg = {k: float(np.mean([r[k] for r in f1])) for k in ("F1", "onset_F1", "pitch_acc_on_matched", "P", "R")}
        res.append(((thr, wthr, use_grid), agg))
    res.sort(key=lambda r: -r[1]["F1"])
    for p, a in res[:8]:
        print(p, {k: round(v, 4) for k, v in a.items()})


if __name__ == "__main__":
    main()
