"""Downbeat evidence in context (the bar-phase problem, e.g. Henceforth's verse).

The frame-wise downbeat posterior judges each beat on its own. When chords and bass change every two beats
(Henceforth: F#m D | A E, eighth notes on one pitch per chord) beats 1 and 3 look the same, the posterior picks a
side for no good reason, and whole passages end up half a bar off. A musician resolves it from context: where the
harmony changes only once per bar, where the bass enters, where a longer note or a new chord lands, and whether
the beat two beats away looks just the same (then the local vote is worth little).

Per beat of our beat tracker: cues (downbeat posterior, bass onset / pitch-class change / length / entry after a
rest, chord change and chord root = bass, onset strength) at offsets -4..+4 beats and the half-bar contrasts, fed
to a gradient-boosted classifier trained on the purchased tabs' bar lines (training songs, honest cross-fold
posteriors). Its probability replaces the raw posterior in the downbeat HMM.

python -m bassnet.downbeat_ctx build | train | eval
"""
import argparse
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
DS = os.path.join(ROOT, "downbeat_ctx_ds.pkl")
MODEL = os.path.join(ROOT, "downbeat_ctx.pkl")
OFFS = (-4, -3, -2, -1, 1, 2, 3, 4)
N_CUE = 9


def beat_cues(beats_s, do, on, notes, chords):
    """(N, N_CUE) cues per beat."""
    from bassnet.decode import FPS
    bt = np.asarray(beats_s, float)
    n = len(bt)
    per = float(np.median(np.diff(bt))) if n > 2 else 0.5
    C = np.zeros((n, N_CUE), np.float32)
    fi = np.clip(np.round(bt * FPS).astype(int), 0, len(do) - 1)
    C[:, 0] = [do[max(0, i - 2):i + 3].max() for i in fi]
    C[:, 7] = [on[max(0, i - 2):i + 3].max() for i in fi]
    ns = sorted((x for x in notes if not x.get("dead")), key=lambda x: x["time"])
    nt = np.array([x["time"] for x in ns]) if ns else np.zeros(0)
    med = float(np.median([x["midi"] for x in ns])) if ns else 40.0
    cst = np.array([float(c["start_time"]) for c in chords]) if chords else np.zeros(0)
    from bassnet.song_doc import parse_chord
    for i, t in enumerate(bt):
        if len(nt):
            j = int(np.argmin(np.abs(nt - t)))
            if abs(nt[j] - t) < 0.07:
                x = ns[j]
                C[i, 1] = 1.0
                C[i, 2] = float(j == 0 or x["midi"] % 12 != ns[j - 1]["midi"] % 12)
                C[i, 3] = min(4.0, (x["end"] - x["time"]) / per) / 4.0
                C[i, 4] = float(j == 0 or x["time"] - ns[j - 1]["end"] > 1.5 * per)
                C[i, 8] = float(np.clip((med - x["midi"]) / 12.0, -1, 1))
                if len(cst):
                    k = int(np.searchsorted(cst, t + 0.05)) - 1
                    if k >= 0:
                        c = parse_chord(chords[k]["chord"])
                        C[i, 6] = float(c.root is not None and x["midi"] % 12 in (c.root, c.bass))
        if len(cst):
            C[i, 5] = float(np.any(np.abs(cst - t) < 0.12))
    return C


def context_features(C):
    n = len(C)
    pad = np.zeros((8, C.shape[1]), np.float32)
    P = np.concatenate([pad, C, pad])
    cols = [C]
    for o in OFFS:
        cols.append(P[8 + o:8 + o + n])
    cols.append(C - P[10:10 + n])          # contrast with the beat half a bar later
    cols.append(C - P[6:6 + n])            # ... and half a bar earlier
    # how "two-beat periodic" the neighbourhood is: harmonic changes on both i and i+2 make the local vote weak
    sym = np.abs(P[10:10 + n, [2, 5]] - C[:, [2, 5]]).mean(1, keepdims=True)
    cols.append(sym)
    return np.concatenate(cols, 1)


def _song_data(m, split, post):
    from bassnet.decode import decode_notes, decode_beats
    from bassnet.phase_audit import tab_grid
    fr, on, de, be, do = post
    info, qm, bars, first = tab_grid(m, split)
    beats, _, _ = decode_beats(be, do)
    notes = decode_notes(fr, on, de)
    md5 = os.path.basename(m["npz"])[:-4]
    cf = os.path.join(ROOT, "chords", md5 + "_orig.json")
    chords = json.load(open(cf)) if os.path.exists(cf) else []
    X = context_features(beat_cues(beats, do, on, notes, chords))
    bt = np.asarray(beats)
    tb = np.array([t for t, _ in bars])
    y = np.array([np.abs(tb - t).min() < 0.07 for t in bt], np.int8) if len(tb) else np.zeros(len(bt), np.int8)
    # songs where our beat grid is not the tab's (tempo-octave errors) are left out of training via bar_hit
    lo, hi = tb.min() - 0.1, tb.max() + 0.1
    inside = (bt >= lo) & (bt <= hi)
    bar_hit = np.mean([np.abs(bt - t).min() < 0.07 for t in tb]) if len(tb) else 0.0
    per_ratio = float(np.median(np.diff(tb))) / max(1e-6, float(np.median(np.diff(bt))))
    return {"md5": md5, "split": split, "X": X, "y": y, "inside": inside, "bar_hit": bar_hit,
            "per_ratio": per_ratio, "name": os.path.basename(os.path.dirname(m["gp"]))[:30]}


def build():
    from bassnet.phase_audit import song_inputs
    from bassnet.train import fold_of
    ds = []
    for m, split, post in song_inputs():
        try:
            d = _song_data(m, split, post)
        except Exception as e:
            print("skip", m["gp"][-40:], e)
            continue
        d["fold"] = fold_of(m["gp"])
        ds.append(d)
    pickle.dump(ds, open(DS, "wb"))
    print(len(ds), "songs")


def _fit(items):
    from sklearn.ensemble import HistGradientBoostingClassifier
    use = [d for d in items if d["bar_hit"] > 0.8 and 3.0 <= d["per_ratio"] <= 4.5]
    X = np.concatenate([d["X"][d["inside"]] for d in use])
    y = np.concatenate([d["y"][d["inside"]] for d in use])
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, l2_regularization=1.0,
                                         random_state=0)
    clf.fit(X, y)
    return clf


def train():
    ds = pickle.load(open(DS, "rb"))
    tr = [d for d in ds if d["split"] == "train"]
    models = {"all": _fit(tr), 0: _fit([d for d in tr if d["fold"] != 0]), 1: _fit([d for d in tr if d["fold"] != 1])}
    pickle.dump(models, open(MODEL, "wb"))
    print("trained on", len(tr), "songs")


_MODELS = {}


def rescore(beats_s, do, on, notes, chords, which="all"):
    """Contextual downbeat probability per beat (replaces the raw posterior in the HMM)."""
    if which not in _MODELS:
        _MODELS[which] = pickle.load(open(MODEL, "rb"))[which]
    X = context_features(beat_cues(beats_s, do, on, notes, chords))
    return _MODELS[which].predict_proba(X)[:, 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "train"])
    a = ap.parse_args()
    {"build": build, "train": train}[a.cmd]()


if __name__ == "__main__":
    main()
