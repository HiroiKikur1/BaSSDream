"""Verifies that GP-derived note times line up with the separated bass stem."""
import json
import os
import sys

import librosa
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import paths  # noqa: E402
from bassnet.gpif_parser import parse_gp  # noqa: E402

ROOT = paths.cache(r"bassnet")


def align_score(gp, stem, max_lag=1.5):
    info = parse_gp(gp)
    y, sr = librosa.load(stem, sr=22050)
    hop = 256
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, fmax=2000)
    env = env / (np.percentile(env, 99) + 1e-6)
    times = np.array([n.time for n in info.notes if not n.grace])
    lags = np.arange(-max_lag, max_lag, 0.005)
    scores = []
    for lag in lags:
        fr = np.round((times + lag) * sr / hop).astype(int)
        fr = fr[(fr >= 0) & (fr < len(env))]
        scores.append(env[fr].mean() if len(fr) else 0)
    scores = np.array(scores)
    best = lags[int(np.argmax(scores))]
    fr0 = np.round(times * sr / hop).astype(int)
    fr0 = fr0[(fr0 >= 0) & (fr0 < len(env))]
    return {
        "best_lag": round(float(best), 3), "score_best": round(float(scores.max()), 3),
        "score_0": round(float(env[fr0].mean()), 3), "baseline": round(float(env.mean()), 3),
        "pad": info.frame_padding / 44100, "n": len(times),
    }


if __name__ == "__main__":
    index = json.load(open(os.path.join(ROOT, "index.json"), encoding="utf8"))
    done = 0
    for gp, v in index.items():
        stem = os.path.join(ROOT, "stems", v["md5"] + ".flac")
        if not os.path.exists(stem):
            continue
        r = align_score(gp, stem)
        print(os.path.basename(os.path.dirname(gp))[:30], r, flush=True)
        done += 1
        if done >= int(sys.argv[1] if len(sys.argv) > 1 else 8):
            break
