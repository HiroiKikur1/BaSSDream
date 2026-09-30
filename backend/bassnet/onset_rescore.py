"""Second opinion on note starts: recover notes the model heard but the fixed thresholds threw away.

loss_audit.py: of the tab notes lost at the decode stage, ~75% are "heard" -- the frame posterior has the right
pitch, but the onset peak is under the 0.5 threshold, or there is no peak at all because the same pitch is
re-plucked (eighth-note pedal lines). Accepting every weak peak brings back notes but adds more wrong ones.

Every candidate start (onset peaks >= 0.08, pitch switches in the frame posterior) is described by: onset
strength and prominence, dead-note probability, pitch before / after and their confidence, rest before / after,
energy jump of the bass stem at the candidate's own pitch, position on the beat grid (8th / 16th / triplet),
distance to neighbouring candidates. A gradient-boosted classifier trained on the tabs (training songs, honest
cross-fold posteriors) decides; notes are then built from the accepted starts exactly as before.

python -m bassnet.onset_rescore build | train | eval
"""
import argparse
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
DS = os.path.join(ROOT, "onset_rescore_ds.pkl")
MODEL = os.path.join(ROOT, "onset_rescore.pkl")


def candidates(fr, on):
    from bassnet.decode import _peaks
    cand = set(_peaks(on, 0.08, 3))
    arg = np.argmax(fr, 1)
    T = len(on)
    for t in range(1, T - 3):
        a, b = arg[t - 1], arg[t]
        if b > 0 and a != b and arg[t + 1] == b and arg[t + 2] == b:
            cand.add(t)
    cand = sorted(cand)
    out = []
    for t in cand:                               # merge candidates closer than 3 frames (keep the stronger onset)
        if out and t - out[-1] < 3:
            if on[t] > on[out[-1]]:
                out[-1] = t
            continue
        out.append(t)
    return np.array(out, int)


def features(cand, fr, on, de, cqt, beats_s):
    from bassnet.decode import FPS
    from bassnet.dataset import MIDI_LO, BINS_PER_SEMI
    from bassnet.model import PITCH_LO
    T = len(on)
    bt = np.asarray(beats_s, float) * FPS
    per = float(np.median(np.diff(bt))) if len(bt) > 2 else 40.0
    rest = fr[:, 0]
    pitch = fr[:, 1:]
    flux = np.concatenate([[0], np.maximum(np.diff(cqt, axis=1), 0).sum(0)])
    flux = flux / (np.percentile(flux, 99) + 1e-6)
    X = np.zeros((len(cand), 24), np.float32)
    for i, t in enumerate(cand):
        a0, a1 = min(T - 1, t + 2), min(T, t + 9)
        b0, b1 = max(0, t - 7), max(1, t - 1)
        after = pitch[a0:a1].mean(0) if a1 > a0 else pitch[t]
        before = pitch[b0:b1].mean(0)
        pa, pb = int(np.argmax(after)), int(np.argmax(before))
        k = (PITCH_LO + pa - MIDI_LO) * BINS_PER_SEMI
        env = cqt[max(0, k - 1):k + 2].max(0) + 0.5 * cqt[max(0, k + 35):k + 38].max(0) if k + 38 <= len(cqt) \
            else cqt[max(0, k - 1):k + 2].max(0)
        e_post = env[t:min(T, t + 4)].max()
        e_pre = env[max(0, t - 5):max(1, t - 1)].min()
        e_ref = env[max(0, t - 20):min(T, t + 20)].max() + 1e-3
        j = int(np.searchsorted(bt, t))
        if 0 < j < len(bt):
            ph = (t - bt[j - 1]) / max(1.0, bt[j] - bt[j - 1])
        else:
            ph = 0.0
        d16 = min(abs(ph * 4 - round(ph * 4)), 0.5)
        d3 = min(abs(ph * 3 - round(ph * 3)), 0.5)
        prev_c = cand[i - 1] if i > 0 else t - 200
        next_c = cand[i + 1] if i + 1 < len(cand) else t + 200
        X[i] = [on[t], on[t] - on[max(0, t - 6):t + 1].min(), on[max(0, t - 2):t + 3].max(),
                de[t:t + 3].max() if de is not None else 0.0,
                rest[b0:b1].mean(), rest[a0:a1].mean() if a1 > a0 else rest[t],
                after[pa], before[pb], float(pa == pb), after[pb], before[pa],
                (e_post - e_pre) / e_ref, e_post / e_ref, flux[max(0, t - 1):t + 2].max(),
                ph, d16, d3, float(d16 < 0.08 and round(ph * 4) % 2 == 0),
                min(200, t - prev_c) / per, min(200, next_c - t) / per, on[prev_c] if prev_c >= 0 else 0.0,
                float(on[t] >= 0.5), per / FPS, float(pa)]
    return X


def _labels(cand, ref_times, fps):
    """1 for the candidate closest to each tab note start within 50 ms."""
    y = np.zeros(len(cand), np.int8)
    ct = cand / fps
    for r in ref_times:
        if not len(ct):
            break
        i = int(np.argmin(np.abs(ct - r)))
        if abs(ct[i] - r) < 0.05:
            y[i] = 1
    return y


def build():
    from bassnet.phase_audit import song_inputs
    from bassnet.decode import decode_notes, FPS
    from bassnet import pipeline
    from bassnet.train import fold_of
    ds = []
    for m, split, (fr, on, de, be, do) in song_inputs():
        md5 = os.path.basename(m["npz"])[:-4]
        lf = os.path.join(ROOT, "labels_v2", md5 + ".json")
        lab = json.load(open(lf, encoding="utf8"))["notes"] if os.path.exists(lf) else m["notes"]
        ref = sorted(n["time"] for n in lab if not n.get("grace"))
        base = decode_notes(fr, on, de)
        beats, _, _, _ = pipeline.song_beats(be, do, base)
        cqt = np.load(m["npz"])["cqt"].astype(np.float32)
        cand = candidates(fr, on)
        X = features(cand, fr, on, de, cqt, beats)
        lo, hi = (ref[0] - 1.0, ref[-1] + 1.0) if ref else (0, 0)
        inside = (cand / FPS >= lo) & (cand / FPS <= hi)
        ds.append({"md5": md5, "split": split, "fold": fold_of(m["gp"]), "cand": cand, "X": X,
                   "y": _labels(cand, ref, FPS), "inside": inside})
        print(split, md5, len(cand), int(ds[-1]["y"].sum()), len(ref), flush=True)
    pickle.dump(ds, open(DS, "wb"))


def _fit(items):
    from sklearn.ensemble import HistGradientBoostingClassifier
    X = np.concatenate([d["X"][d["inside"]] for d in items])
    y = np.concatenate([d["y"][d["inside"]] for d in items])
    clf = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, max_leaf_nodes=63, l2_regularization=1.0,
                                         random_state=0)
    return clf.fit(X, y)


def train():
    ds = pickle.load(open(DS, "rb"))
    tr = [d for d in ds if d["split"] == "train"]
    models = {"all": _fit(tr), 0: _fit([d for d in tr if d["fold"] != 0]), 1: _fit([d for d in tr if d["fold"] != 1])}
    pickle.dump(models, open(MODEL, "wb"))
    print("trained on", len(tr), "songs")


_M = {}


def onsets_for(fr, on, de, cqt, beats_s, thr=0.5, which="all"):
    """Accepted note-start frames (production entry point)."""
    if which not in _M:
        _M[which] = pickle.load(open(MODEL, "rb"))[which]
    cand = candidates(fr, on)
    if not len(cand):
        return cand
    p = _M[which].predict_proba(features(cand, fr, on, de, cqt, beats_s))[:, 1]
    return cand[p >= thr]


def evaluate():
    from bassnet.phase_audit import song_inputs
    from bassnet.decode import decode_notes
    from bassnet.loss_audit import f1
    from bassnet import pipeline
    models = pickle.load(open(MODEL, "rb"))
    ds = {d["md5"]: d for d in pickle.load(open(DS, "rb"))}
    res = {}
    for m, split, (fr, on, de, be, do) in song_inputs():
        if split == "train":
            continue
        md5 = os.path.basename(m["npz"])[:-4]
        lf = os.path.join(ROOT, "labels_v2", md5 + ".json")
        lab = json.load(open(lf, encoding="utf8"))["notes"] if os.path.exists(lf) else m["notes"]
        ref = sorted((n for n in lab if not n.get("grace") and not n.get("dead")), key=lambda n: n["time"])
        d = ds[md5]
        p = models["all"].predict_proba(d["X"])[:, 1]
        for name, notes in [("production", decode_notes(fr, on, de))] + \
                [(f"rescore {t}", decode_notes(fr, on, de, onsets=d["cand"][p >= t])) for t in (0.35, 0.45, 0.55, 0.65)]:
            est = sorted((n for n in notes if not n.get("dead")), key=lambda n: n["time"])
            res.setdefault(name, []).append(f1(ref, est))
    for k, v in res.items():
        v = np.array(v)
        print(f"{k:14s} F1 {v[:, 0].mean():.4f}  P {v[:, 1].mean():.4f}  R {v[:, 2].mean():.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "train", "eval"])
    a = ap.parse_args()
    {"build": build, "train": train, "eval": evaluate}[a.cmd]()


if __name__ == "__main__":
    main()
