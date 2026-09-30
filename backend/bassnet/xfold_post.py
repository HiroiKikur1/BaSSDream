"""Honest posteriors for every training song: each song is run through the fold model that never trained on it.
(val/test songs keep the cached v3+v4 posteriors.) Output: cache/bassnet/eval/xfold/<md5>.npz, same keys as post
caches. Used to train second-stage models on realistic acoustic-model errors.

python -m bassnet.xfold_post
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.relabel import FOLD_MODELS, model_post  # noqa: E402

OUT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "eval", "xfold")


def main():
    from bassnet.train import load_metas, song_key, frozen_keys, fold_of
    os.makedirs(OUT, exist_ok=True)
    held = frozen_keys("val") | frozen_keys("test")
    n = 0
    for m in load_metas():
        if song_key(m["gp"]) in held:
            continue
        key = os.path.basename(m["npz"])[:-4]
        out = os.path.join(OUT, key + ".npz")
        if os.path.exists(out):
            continue
        fr, on, de, be, do = model_post(FOLD_MODELS[1 - fold_of(m["gp"])], m["npz"])
        np.savez_compressed(out, fr=fr.astype(np.float16), on=on, de=de, be=be, do=do)
        n += 1
        print("xfold", key, flush=True)
    print("done", n)


if __name__ == "__main__":
    main()
