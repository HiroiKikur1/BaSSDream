"""Where does the bar really start? A song-level choice of the downbeat phase.

In rock the backbeat makes beats 1 and 3 look alike, and the frame-wise downbeat posterior can flip between the two
half-bar phases section by section; the HMM then inserts odd 2/4 bars and everything after is shifted. Most songs
keep one meter (78% of the purchased tabs), so the phase is decided once for the whole song from evidence summed
over all bars, then the HMM runs with a strong prior for that phase.

Evidence per candidate phase p (beats i with i % M == p), each centred across the M candidates:
  downbeat posterior, bass entries after a rest, bass pitch-class changes, long notes starting there, onset strength.
Weights: logistic softmax over phases, trained on the training songs (cross-fold posteriors) against the bar lines of
the purchased tabs.

python -m bassnet.phase_model build | train | eval
"""
import argparse
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.decode import FPS, decode_notes, decode_beats, bass_entry_evidence  # noqa: E402

ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
DS = os.path.join(ROOT, "phase_ds.pkl")
W_FILE = os.path.join(ROOT, "phase_w.npy")
N_F = 5


def phase_features(beats_s, down_prob, notes, M):
    """(M, N_F) evidence for each phase; beats_s in seconds."""
    bt = np.asarray(beats_s, float)
    nb = len(bt)
    fi = np.clip(np.round(bt * FPS).astype(int), 0, len(down_prob) - 1)
    dv = np.array([down_prob[max(0, i - 2):i + 3].max() for i in fi])
    ent = bass_entry_evidence(notes, bt) + 0.0
    ent = (ent > ent.mean()).astype(float) if len(ent) else ent
    chg = np.zeros(nb)
    longn = np.zeros(nb)
    strength = np.zeros(nb)
    ns = sorted((n for n in notes if not n.get("dead")), key=lambda n: n["time"])
    t = np.array([n["time"] for n in ns])
    per = float(np.median(np.diff(bt))) if nb > 2 else 0.5
    for i, b in enumerate(bt):
        if not len(t):
            break
        j = int(np.argmin(np.abs(t - b)))
        if abs(t[j] - b) > 0.07:
            continue
        n = ns[j]
        strength[i] = n.get("on_peak", 0.5)
        if j and n["midi"] % 12 != ns[j - 1]["midi"] % 12:
            chg[i] = 1.0
        if n["end"] - n["time"] >= 1.5 * per:
            longn[i] = 1.0
    F = np.zeros((M, N_F))
    for p in range(M):
        sel = np.arange(p, nb, M)
        if not len(sel):
            continue
        F[p] = [dv[sel].mean(), ent[sel].sum() / max(1.0, ent.sum()), chg[sel].mean(), longn[sel].mean(),
                strength[sel].mean()]
    return F - F.mean(0, keepdims=True)


def gt_phase(beats_s, gt_downs, M):
    """Majority phase of the true downbeats among the decoded beats (None if they don't line up)."""
    bt = np.asarray(beats_s, float)
    votes = np.zeros(M)
    for d in gt_downs:
        i = int(np.argmin(np.abs(bt - d)))
        if abs(bt[i] - d) < 0.07:
            votes[i % M] += 1
    return int(votes.argmax()) if votes.sum() >= 4 else None


def build():
    from bassnet.train import load_metas, song_key, frozen_keys
    from bassnet.notation_oracle import gt_beats
    from bassnet.gpif_parser import parse_gp
    val, test = frozen_keys("val"), frozen_keys("test")
    E = os.path.join(ROOT, "eval")
    ds = {"train": [], "val": [], "test": []}
    for m in load_metas():
        k = song_key(m["gp"])
        key = os.path.basename(m["npz"])[:-4]
        split, dirs = ("val", ["val_v3", "val_v4"]) if k in val else ("test", ["post_cache_v3", "post_cache_v4"]) \
            if k in test else ("train", ["xfold"])
        if not all(os.path.exists(os.path.join(E, d, key + ".npz")) for d in dirs):
            continue
        zs = [np.load(os.path.join(E, d, key + ".npz")) for d in dirs]
        fr, on, de, be, do = [np.mean([z[x].astype(np.float32) for z in zs], 0) for x in ("fr", "on", "de", "be", "do")]
        notes = decode_notes(fr, on, de)
        beats, downs, meter = decode_beats(be, do)
        M = int(meter)
        try:
            if split == "train":
                from bassnet.notation_eval import label_q_map
                info = parse_gp(m["gp"])
                qm = label_q_map(m)
                gd = [qm(q0) for b, oc, q0, ql in info.bars] if qm else m["downbeats"]
            else:
                gd = gt_beats(m)[1]
        except Exception:
            continue
        g = gt_phase(beats, gd, M)
        cur = gt_phase(beats, downs, M)
        if g is None:
            continue
        ds[split].append({"F": phase_features(beats, do, notes, M), "y": g, "cur": cur, "M": M,
                          "name": os.path.basename(os.path.dirname(m["gp"]))[:30]})
    pickle.dump(ds, open(DS, "wb"))
    for s, v in ds.items():
        print(s, len(v), "current HMM phase correct", round(float(np.mean([x["cur"] == x["y"] for x in v])), 3))


def train(l2=1e-2, iters=3000, lr=0.3):
    ds = pickle.load(open(DS, "rb"))
    tr = ds["train"]
    mu = np.concatenate([x["F"] for x in tr]).std(0) + 1e-6
    w = np.zeros(N_F)
    for _ in range(iters):
        g = l2 * w
        for x in tr:
            s = (x["F"] / mu) @ w
            p = np.exp(s - s.max())
            p /= p.sum()
            g += ((x["F"] / mu).T @ p - (x["F"] / mu)[x["y"]]) / len(tr)
        w -= lr * g
    np.save(W_FILE, w / mu)
    print("weights", np.round(w / mu, 3))
    evaluate()


def choose_phase(F, w=None):
    w = np.load(W_FILE) if w is None else w
    return int(np.argmax(F @ w))


def evaluate():
    ds = pickle.load(open(DS, "rb"))
    w = np.load(W_FILE)
    for s in ("train", "val", "test"):
        v = ds[s]
        print(s, "songs", len(v), "HMM phase", round(float(np.mean([x["cur"] == x["y"] for x in v])), 3),
              "song-level model", round(float(np.mean([choose_phase(x["F"], w) == x["y"] for x in v])), 3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "train", "eval"])
    a = ap.parse_args()
    {"build": build, "train": train, "eval": evaluate}[a.cmd]()


if __name__ == "__main__":
    main()
