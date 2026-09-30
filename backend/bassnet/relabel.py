"""Re-align training labels to the recordings (labels_v2).

The original labels place each tab note by the author's GP sync plus a local CQT refinement. On live versions
and fast eighth-note lines whole sections end up 60-120 ms (often exactly one eighth) off the recording; an
independent spectral-flux referee sided with the beat-level alignment 2.5:1 where the two disagreed.

Per song: BassNet posteriors from a model that never trained on the song (fold models for training songs,
the cached v3+v4 posteriors for val/test) -> score_align beat DP -> note times on the aligned grid, each snapped
to an onset peak within +-35 ms. The new label set is kept only if the flux referee supports it at least as well
as the old one.

python -m bassnet.relabel [--only KEY_SUBSTR] [--limit N]
"""
import argparse
import json
import os
import sys
from fractions import Fraction

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.decode import FPS  # noqa: E402
from bassnet.gpif_parser import parse_gp  # noqa: E402

ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
FEAT = os.path.join(ROOT, "feats")
OUT = os.path.join(ROOT, "labels_v2")
FOLD_MODELS = [os.path.join(ROOT, "staging", "fold0.pt"), os.path.join(ROOT, "staging", "fold1.pt")]
EVAL = os.path.join(ROOT, "eval")
CACHED = [("val_v3", "val_v4"), ("post_cache_v3", "post_cache_v4")]
SNAP = 3          # frames (+-35 ms)
SR_FLUX, HOP_FLUX = 22050, 128


def cached_post(key):
    for dirs in CACHED:
        ps = [os.path.join(EVAL, d, key + ".npz") for d in dirs]
        if all(os.path.exists(p) for p in ps):
            acc = None
            for p in ps:
                z = np.load(p)
                arr = [z[k].astype(np.float32) for k in ("fr", "on", "de", "be", "do")]
                acc = arr if acc is None else [x + y for x, y in zip(acc, arr)]
            return [x / len(ps) for x in acc]
    return None


_models = {}


def model_post(fold_model, npz):
    import torch  # noqa: F401
    from bassnet.model import load_checkpoint
    from bassnet.pipeline import _model_outputs
    if fold_model not in _models:
        _models[fold_model] = load_checkpoint(fold_model, "cuda")
    d = np.load(npz)
    cqt, mel = d["cqt"].astype(np.float32), d["mel"].astype(np.float32)
    cqt = cqt / (np.percentile(cqt, 99.5) + 1e-3)
    mel = mel / (np.percentile(mel, 99.5) + 1e-3)
    return _model_outputs(_models[fold_model], cqt, mel, "cuda", (0, -1, 1))[:5]


def flux_curve(stem):
    import librosa
    y, _ = librosa.load(stem, sr=SR_FLUX, mono=True)
    S = librosa.feature.melspectrogram(y=y, sr=SR_FLUX, n_fft=1024, hop_length=HOP_FLUX, n_mels=64, fmax=4000)
    L = np.log1p(100 * S)
    f = np.maximum(0, np.diff(L, axis=1)).sum(0)
    return (f - np.median(f)) / (np.percentile(f, 95) - np.median(f) + 1e-9)


def flux_support(flux, times):
    """Fraction of note times with a spectral-flux onset within +-17 ms."""
    if not times:
        return 0.0
    hits = 0
    for t in times:
        i = int(round(t * SR_FLUX / HOP_FLUX))
        w = flux[max(0, i - 3):i + 4]
        hits += bool(len(w) and w.max() > 0.3)
    return hits / len(times)


def realign(m, post):
    from bassnet.score_align import align_posteriors
    fr, on, de, be, do = post
    info = parse_gp(m["gp"])
    r = align_posteriors(info, fr, on, be, do)
    bq, bt = np.array(r["beat_q"]), np.array(r["beat_t"])

    def qmap(q):
        return float(np.interp(float(q), bq, bt))
    T = len(on)
    notes = []
    for n in m["notes"]:
        tg = qmap(n["qpos"])
        f = int(round(tg * FPS))
        t = tg
        if 0 <= f < T and not n.get("grace"):
            lo, hi = max(0, f - SNAP), min(T, f + SNAP + 1)
            k = lo + int(np.argmax(on[lo:hi]))
            if on[k] > 0.3:
                t = k / FPS
        end = qmap(n["qpos"] + n["qdur"]) + (t - tg)
        notes.append(dict(n, time=t, end=max(end, t + 1.0 / FPS), time_grid=tg))
    # beats / downbeats on the aligned grid (same selection as dataset.build_one)
    beats, downbeats = [], []
    first_q = min(float(n["qpos"]) for n in m["notes"])
    last_q = max(float(n["qpos"] + n["qdur"]) for n in m["notes"])
    for bi, oc, q0, ql in info.bars:
        if float(q0 + ql) < first_q - 8 or float(q0) > last_q + 4:
            continue
        downbeats.append(qmap(q0))
        num, den = info.time_sigs[bi]
        step = Fraction(3, 2) if (den == 8 and num % 3 == 0) else Fraction(4, den) if den >= 4 else Fraction(1)
        k = Fraction(0)
        while k < ql:
            beats.append(qmap(q0 + k))
            k += step
    return notes, beats, downbeats


def main():
    from bassnet.train import song_key, frozen_keys, fold_of
    from bassnet.dataset import semitone_energy, note_hit_rate
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--held-only", action="store_true", help="only val/test songs (cached posteriors, no GPU)")
    ap.add_argument("--train-only", action="store_true")
    ap.add_argument("--song-fold", type=int, default=-1, help="only training songs of this fold")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    held = frozen_keys("val") | frozen_keys("test")
    files = sorted(f for f in os.listdir(FEAT) if f.endswith(".json"))
    done = 0
    summary = []
    for fn in files:
        m = json.load(open(os.path.join(FEAT, fn), encoding="utf8"))
        if a.only and a.only not in m["gp"]:
            continue
        key = fn[:-5]
        npz = os.path.join(FEAT, key + ".npz")
        if not os.path.exists(npz) or not m.get("notes"):
            continue
        if a.held_only and song_key(m["gp"]) not in held or a.train_only and song_key(m["gp"]) in held:
            continue
        if a.song_fold >= 0 and (song_key(m["gp"]) in held or fold_of(m["gp"]) != a.song_fold):
            continue
        if song_key(m["gp"]) in held:
            post = cached_post(key)
            src = "v3v4"
        else:
            post = None
        if post is None:
            fm = FOLD_MODELS[1 - fold_of(m["gp"])]     # a model that never saw this song
            post = model_post(fm, npz)
            src = os.path.basename(fm)
        try:
            notes, beats, downs = realign(m, post)
        except Exception as e:
            print("FAIL", key, e, flush=True)
            continue
        flux = flux_curve(m["stem"])
        main_old = [n["time"] for n in m["notes"] if not n.get("grace") and not n.get("dead")]
        main_new = [n["time"] for n in notes if not n.get("grace") and not n.get("dead")]
        s_old, s_new = flux_support(flux, main_old), flux_support(flux, main_new)
        d = np.array([x - y for x, y in zip(main_new, main_old)])
        E = semitone_energy(np.load(npz)["cqt"].astype(np.float32))
        rate_new = note_hit_rate(E, [n for n in notes if not n.get("grace") and not n.get("dead")])
        use_new = s_new >= s_old - 0.01
        out = dict(m)
        if use_new:
            out.update(notes=notes, beats=beats, downbeats=downs, rate_final=max(rate_new, m["rate_final"]),
                       align="post_v2")
        out["relabel"] = {"src": src, "flux_old": round(s_old, 4), "flux_new": round(s_new, 4),
                          "moved_60ms": round(float(np.mean(np.abs(d) > 0.06)), 4), "rate_new": round(rate_new, 4),
                          "used": bool(use_new)}
        json.dump(out, open(os.path.join(OUT, fn), "w", encoding="utf8"), ensure_ascii=False)
        summary.append(out["relabel"])
        print(f"{key} {'NEW' if use_new else 'old'} flux {s_old:.3f}->{s_new:.3f} moved>60ms {np.mean(np.abs(d) > 0.06):.3f} "
              f"rate {m['rate_final']:.3f}/{rate_new:.3f} {src} {os.path.basename(os.path.dirname(m['gp']))[:30]}",
              flush=True)
        done += 1
        if a.limit and done >= a.limit:
            break
    if summary:
        print("used new:", sum(s["used"] for s in summary), "/", len(summary),
              "mean flux old", round(float(np.mean([s["flux_old"] for s in summary])), 4),
              "new", round(float(np.mean([s["flux_new"] for s in summary])), 4))


if __name__ == "__main__":
    main()
