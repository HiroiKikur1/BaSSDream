"""Second opinion on each note's pitch (octave slips and near-miss runner-ups).

loss_audit.py: of the notes whose start is right but pitch wrong, 37% are octave errors and 25% have the tab's
pitch as the runner-up in the frame posterior. Earlier attempts overrode the acoustic choice with fixed musical
priors (context LM, octave vote, fingering Viterbi) and lost. Here every note gets candidates -- the top 3 of the
frame posterior and the argmax +-12 -- each described by acoustic evidence (posterior mass and rank, CQT energy at
its fundamental, at the octave above and below), its fit to the line (intervals to the neighbouring notes, octave
relation to them, register against the song's median), to the harmony (chord tone / root, lv-chordia) and to the
instrument (below the open low string?). A gradient-boosted classifier trained on the tabs (training songs, honest
cross-fold posteriors) scores the candidates; a note changes only when a candidate beats the current pitch by a
margin.

python -m bassnet.lab.pitch_rescore build | train | eval
"""
import argparse
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
DS = os.path.join(ROOT, "pitch_rescore_ds.pkl")
MODEL = os.path.join(ROOT, "pitch_rescore.pkl")
NF = 22


def note_candidates(notes, fr, cqt, chords, fps):
    """-> list per note of (candidate midis, features (k, NF))."""
    from bassnet.model import PITCH_LO
    from bassnet.dataset import MIDI_LO, BINS_PER_SEMI
    from bassnet.song_doc import chord_pcs, parse_chord
    P = fr[:, 1:]
    npitch = P.shape[1]
    cst = np.array([float(c["start_time"]) for c in chords]) if chords else np.zeros(0)
    live = [n for n in notes if not n.get("dead")]
    med = float(np.median([n["midi"] for n in live])) if live else 40.0
    T = cqt.shape[1]

    def energy(midi, a, b):
        k = (midi - MIDI_LO) * BINS_PER_SEMI + 1
        if k < 0 or k >= cqt.shape[0]:
            return 0.0
        return float(cqt[max(0, k - 1):k + 2, a:b].max()) if b > a else 0.0
    out = []
    for i, n in enumerate(notes):
        a = int(round(n["time"] * fps))
        e = max(a + 3, int(round(n["end"] * fps)))
        w = P[min(a + 2, T - 1):min(e, a + 12)]
        if not len(w):
            w = P[min(a, len(P) - 1):min(a + 1, len(P))]
        mean = w.mean(0)
        order = np.argsort(-mean)
        cur = n["midi"]
        cands = {cur}
        cands.update(int(PITCH_LO + k) for k in order[:3])
        cands.update([cur - 12, cur + 12])
        cands = sorted(c for c in cands if PITCH_LO <= c < PITCH_LO + npitch)
        prv = next((x["midi"] for x in reversed(notes[:i]) if not x.get("dead")), None)
        nxt = next((x["midi"] for x in notes[i + 1:] if not x.get("dead")), None)
        pcs, root = (), None
        if len(cst):
            j = int(np.searchsorted(cst, n["time"] + 0.05)) - 1
            if j >= 0:
                pcs = chord_pcs(chords[j]["chord"])
                c = parse_chord(chords[j]["chord"])
                root = c.root
        e_a, e_b = a, min(T, a + 10)
        feats = []
        for c in cands:
            k = c - PITCH_LO
            rank = int(np.where(order == k)[0][0]) if k in order else npitch
            feats.append([mean[k], mean[order[0]] - mean[k], min(rank, 10), float(c == cur),
                          energy(c, e_a, e_b), energy(c + 12, e_a, e_b), energy(c - 12, e_a, e_b),
                          energy(c + 19, e_a, e_b),
                          abs(c - prv) if prv is not None else 0, float(prv is not None and (c - prv) % 12 == 0 and c != prv),
                          abs(c - nxt) if nxt is not None else 0, float(nxt is not None and (c - nxt) % 12 == 0 and c != nxt),
                          c - med, float(c < 28), float(c < 23), float(c % 12 in pcs), float(root is not None and c % 12 == root),
                          c - cur, float((c - cur) % 12 == 0 and c != cur), n.get("conf", 1.0),
                          (n["end"] - n["time"]), float(c > 55)])
        out.append((cands, np.array(feats, np.float32)))
    return out


def build():
    from bassnet.phase_audit import song_inputs
    from bassnet.decode import decode_notes, FPS
    from bassnet.loss_audit import match
    from bassnet.train import fold_of
    from bassnet import pipeline
    ds = []
    for m, split, (fr, on, de, be, do) in song_inputs():
        md5 = os.path.basename(m["npz"])[:-4]
        lf = os.path.join(ROOT, "labels_v2", md5 + ".json")
        lab = json.load(open(lf, encoding="utf8"))["notes"] if os.path.exists(lf) else m["notes"]
        ref = sorted((n for n in lab if not n.get("grace") and not n.get("dead")), key=lambda n: n["time"])
        cqt = np.load(m["npz"])["cqt"].astype(np.float32)
        notes, beats, downs, meter, _ = pipeline.decode_song(fr, on, de, be, do, cqt)
        notes = sorted(notes, key=lambda n: n["time"])
        cf = os.path.join(ROOT, "chords", md5 + "_orig.json")
        chords = json.load(open(cf)) if os.path.exists(cf) else []
        cands = note_candidates(notes, fr, cqt, chords, FPS)
        live = [i for i, n in enumerate(notes) if not n.get("dead")]
        pairs = match(ref, [notes[i] for i in live])
        truth = {live[e]: ref[r]["midi"] for r, e in pairs}
        X, y, g, cur = [], [], [], []
        for i, (cs, F) in enumerate(cands):
            if i not in truth or truth[i] not in cs:
                continue               # only notes whose start matches and whose tab pitch is among the candidates
            X.append(F)
            y.append(np.array([c == truth[i] for c in cs], np.int8))
            g.append(np.full(len(cs), i))
        if not X:
            continue
        ds.append({"md5": md5, "split": split, "fold": fold_of(m["gp"]), "X": np.concatenate(X),
                   "y": np.concatenate(y), "g": np.concatenate(g)})
        print(split, md5, len(X), flush=True)
    pickle.dump(ds, open(DS, "wb"))


def _fit(items):
    from sklearn.ensemble import HistGradientBoostingClassifier
    X = np.concatenate([d["X"] for d in items])
    y = np.concatenate([d["y"] for d in items])
    return HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, max_leaf_nodes=31, l2_regularization=1.0,
                                          random_state=0).fit(X, y)


def train():
    ds = pickle.load(open(DS, "rb"))
    tr = [d for d in ds if d["split"] == "train"]
    pickle.dump({"all": _fit(tr), 0: _fit([d for d in tr if d["fold"] != 0]), 1: _fit([d for d in tr if d["fold"] != 1])},
                open(MODEL, "wb"))
    print("trained on", len(tr), "songs")


def choose(model, F, margin):
    """Index of the chosen candidate: the current pitch unless another scores `margin` higher."""
    p = model.predict_proba(F)[:, 1]
    cur = int(np.argmax(F[:, 3]))
    best = int(np.argmax(p))
    return best if p[best] - p[cur] > margin else cur


def evaluate():
    ds = pickle.load(open(DS, "rb"))
    models = pickle.load(open(MODEL, "rb"))
    for name, sel in (("val+test", lambda d: d["split"] != "train"), ("train x-fold", lambda d: d["split"] == "train")):
        items = [d for d in ds if sel(d)]
        for margin in (0.0, 0.1, 0.2, 0.3):
            ok_before = ok_after = fixed = broken = n = 0
            for d in items:
                mdl = models[d["fold"]] if d["split"] == "train" else models["all"]
                p = mdl.predict_proba(d["X"])[:, 1]
                for gi in np.unique(d["g"]):
                    idx = np.where(d["g"] == gi)[0]
                    F, y = d["X"][idx], d["y"][idx]
                    cur = int(np.argmax(F[:, 3]))
                    best = int(np.argmax(p[idx]))
                    pick = best if p[idx][best] - p[idx][cur] > margin else cur
                    ok_before += y[cur]
                    ok_after += y[pick]
                    fixed += (not y[cur]) and y[pick]
                    broken += y[cur] and not y[pick]
                    n += 1
            print(f"{name:13s} margin {margin}: pitch right {ok_before / n:.4f} -> {ok_after / n:.4f}  "
                  f"(fixed {fixed}, broken {broken}, notes {n})")


def apply(notes, fr, cqt, chords, fps, margin=None, which="all"):
    """Production: change note pitches in place where the classifier is clearly more confident."""
    margin = PITCH_MARGIN if margin is None else margin
    if not os.path.exists(MODEL):
        return 0
    mdl = pickle.load(open(MODEL, "rb"))[which]
    order = sorted(range(len(notes)), key=lambda i: notes[i]["time"])
    ns = [notes[i] for i in order]
    changed = 0
    for n, (cs, F) in zip(ns, note_candidates(ns, fr, cqt, chords, fps)):
        if n.get("dead") or len(cs) < 2:
            continue
        k = choose(mdl, F, margin)
        if cs[k] != n["midi"]:
            n["midi_model"] = n["midi"]
            n["midi"] = int(cs[k])
            changed += 1
    return changed


PITCH_MARGIN = 0.2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "train", "eval"])
    a = ap.parse_args()
    {"build": build, "train": train, "eval": evaluate}[a.cmd]()


if __name__ == "__main__":
    main()
