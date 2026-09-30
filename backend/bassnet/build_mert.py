"""MERT-v1-330M (m-a-p, CC BY-NC 4.0) features of each bass stem, for the foundation-model probe.

Layers 6/12/18 (each z-normalised) -> concat 3072 -> PCA 384 (fit on training songs) -> float16 at 75 fps.
Run with tools/ymt_env python (has transformers):
  python -m bassnet.build_mert --fit      # PCA from 30 training songs
  python -m bassnet.build_mert            # all songs -> cache/bassnet/feats_mert/<md5>.npz
"""
import argparse
import glob
import json
import os
import random
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
MODEL = os.path.join("E:" + os.sep, "BassStation", "cache", "models", "MERT-v1-330M")
OUT = os.path.join(ROOT, "feats_mert")
PCA_FILE = os.path.join(ROOT, "mert_pca.npz")
LAYERS = (6, 12, 18)
SR = 24000
CHUNK = 20 * SR          # 20 s windows with 2 s context each side
CTX = 2 * SR


def load_model():
    from transformers import AutoModel
    m = AutoModel.from_pretrained(MODEL, trust_remote_code=True).to("cuda").half().eval()
    return m


@torch.no_grad()
def features(model, y: np.ndarray) -> np.ndarray:
    """(T75, 3*1024) float32, frames at 75 fps."""
    outs = []
    n = len(y)
    s = 0
    while s < n:
        a, b = max(0, s - CTX), min(n, s + CHUNK + CTX)
        x = torch.from_numpy(y[a:b]).float().to("cuda")[None]
        x = (x - x.mean()) / (x.std() + 1e-7)
        hs = model(x.half(), output_hidden_states=True).hidden_states
        f = torch.cat([hs[l][0] for l in LAYERS], -1).float().cpu().numpy()       # frames of [a, b)
        fa = int(round((s - a) / 320))
        fb = fa + int(round((min(n, s + CHUNK) - s) / 320))
        outs.append(f[fa:min(fb, len(f))])
        s += CHUNK
    return np.concatenate(outs, 0)


def zscore_layers(f, stats):
    return (f - stats["mu"]) / stats["sd"]


def metas():
    return [json.load(open(p, encoding="utf8")) | {"md5": os.path.basename(p)[:-5]}
            for p in sorted(glob.glob(os.path.join(ROOT, "feats", "*.json")))]


def main():
    import librosa
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--dim", type=int, default=384)
    a = ap.parse_args()
    model = load_model()
    ms = metas()
    if a.fit:
        from bassnet.train import split_songs, load_metas
        tr, _ = split_songs(load_metas())
        train_md5 = {os.path.basename(m["npz"])[:-4] for m in tr}
        pool = [m for m in ms if m["md5"] in train_md5]
        random.Random(0).shuffle(pool)
        rows = []
        for m in pool[:30]:
            y, _ = librosa.load(m["stem"], sr=SR, mono=True)
            f = features(model, y)
            rows.append(f[np.random.default_rng(0).choice(len(f), min(len(f), 1500), replace=False)])
            print("fit", m["md5"], f.shape, flush=True)
        X = np.concatenate(rows, 0)
        mu, sd = X.mean(0), X.std(0) + 1e-5
        Z = (X - mu) / sd
        U, S, Vt = np.linalg.svd(Z - Z.mean(0), full_matrices=False)
        ev = (S ** 2) / (S ** 2).sum()
        print(f"PCA {a.dim}: explained variance {ev[:a.dim].sum():.3f}")
        np.savez(PCA_FILE, mu=mu, sd=sd, zmean=Z.mean(0), comp=Vt[:a.dim].astype(np.float32))
        return
    p = np.load(PCA_FILE)
    os.makedirs(OUT, exist_ok=True)
    for i, m in enumerate(ms):
        out = os.path.join(OUT, m["md5"] + ".npz")
        if os.path.exists(out):
            continue
        y, _ = librosa.load(m["stem"], sr=SR, mono=True)
        f = features(model, y)
        z = ((f - p["mu"]) / p["sd"] - p["zmean"]) @ p["comp"].T
        np.savez_compressed(out, mert=z.astype(np.float16))
        print(f"{i + 1}/{len(ms)} {m['md5']} {z.shape}", flush=True)


if __name__ == "__main__":
    main()
