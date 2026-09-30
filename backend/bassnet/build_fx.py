"""Effect-pedal augmentation: CQT features of each separated bass stem after random drive / cab chains.

Many library songs use overdriven or distorted bass (e.g. Morfonica "ALIVE"), whose dense harmonics cause octave
and onset errors. Training swaps the clean CQT for one of these at random (labels are unchanged).
Output: cache/bassnet/feats_fx/<md5>_<k>.npz  (cqt float16)
Usage: python -m bassnet.build_fx [--variants 2]
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.dataset import SR, compute_cqt  # noqa: E402

ROOT = r"E:\BassStation\cache\bassnet"
OUT = os.path.join(ROOT, "feats_fx")


def fx_chain(y: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    from scipy.signal import butter, sosfilt
    y = y / (np.max(np.abs(y)) + 1e-6)
    drive = float(np.exp(rng.uniform(np.log(1.5), np.log(25))))
    kind = rng.choice(["tanh", "hard", "asym", "fuzz"])
    x = y * drive
    if kind == "tanh":
        w = np.tanh(x)
    elif kind == "hard":
        w = np.clip(x, -1, 1)
    elif kind == "asym":                      # tube-like: even harmonics
        w = np.tanh(x + 0.3 * x ** 2) - np.tanh(0.3 * x ** 2).mean()
    else:                                     # fuzz: squared-off, very dense harmonics
        w = np.sign(x) * (1 - np.exp(-np.abs(x) * 2))
    # pre/post EQ: optional mid boost (tube screamer style) then cabinet low-pass, DC/sub high-pass
    if rng.random() < 0.5:
        sos = butter(2, [400, 1500], btype="bandpass", fs=SR, output="sos")
        w = w + rng.uniform(0.3, 1.2) * sosfilt(sos, w)
    cab = float(rng.uniform(1500, 5000))
    w = sosfilt(butter(4, min(cab, SR / 2 - 100), btype="low", fs=SR, output="sos"), w)
    w = sosfilt(butter(2, 40, btype="high", fs=SR, output="sos"), w)
    mix = float(rng.uniform(0.3, 1.0))        # many rigs blend a clean DI with the drive channel
    w = w / (np.max(np.abs(w)) + 1e-6)
    out = mix * w + (1 - mix) * y
    return (out / (np.max(np.abs(out)) + 1e-6) * 0.9).astype(np.float32)


def main():
    import librosa
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", type=int, default=2)
    ap.add_argument("--shard", default="0/1", help="i/n: process every n-th song starting at i")
    a = ap.parse_args()
    si, sn = (int(x) for x in a.shard.split("/"))
    os.makedirs(OUT, exist_ok=True)
    metas = sorted(glob.glob(os.path.join(ROOT, "feats", "*.json")))
    for i, f in enumerate(metas):
        if i % sn != si:
            continue
        md5 = os.path.basename(f)[:-5]
        todo = [k for k in range(a.variants) if not os.path.exists(os.path.join(OUT, f"{md5}_{k}.npz"))]
        if not todo:
            continue
        m = json.load(open(f, encoding="utf8"))
        y, _ = librosa.load(m["stem"], sr=SR, mono=True)
        for k in todo:
            rng = np.random.default_rng(int(md5[:8], 16) + 7919 * k)
            C = compute_cqt(fx_chain(y, rng))
            np.savez_compressed(os.path.join(OUT, f"{md5}_{k}.npz"), cqt=C.astype(np.float16))
        print(f"{i + 1}/{len(metas)} {md5}", flush=True)


if __name__ == "__main__":
    main()
