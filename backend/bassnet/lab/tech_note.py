"""Note-level technique specialist: slap / pop / dead note / plain, from the sound of each attack (Phase 5).

The BassNet technique head is effectively off (tech_thr.json: all but slap disabled; slap posteriors on real slap
single notes stay < 0.05). Slap and pop are a matter of timbre at the attack, so this classifier looks only at the
attack of each note in the bass stem: 8 CQT frames (-1..+6, ~90 ms) re-indexed so the note's own pitch is bin 0
(harmonic pattern independent of pitch) plus the absolute spectrum's brightness.
Labels: the purchased tabs (labels_v2: slap 1155, pop 564, dead 2172 notes); frozen val/test songs held out.
IDMT-SMT-Bass single notes (plucking styles FS / PK / MU / ST / SP, dead notes DN) are used for evaluation only.

python -m bassnet.lab.tech_note build | train | eval
"""
import argparse
import glob
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
DS = os.path.join(ROOT, "tech_note_ds.pkl")
CKPT = os.path.join(ROOT, "staging", "tech_note.pt")
CLASSES = ["plain", "slap", "pop", "dead"]
F0, F1 = -1, 7            # frames around the onset
REL = 150                 # CQT bins above the fundamental (50 semitones)


def attack_features(cqt, t_on, midi, fps):
    """(F) feature vector of one note attack; cqt = dataset.compute_cqt (264 bins from MIDI 21, 3 bins/semitone)."""
    from bassnet.dataset import MIDI_LO, BINS_PER_SEMI
    a = int(round(t_on * fps))
    T = cqt.shape[1]
    idx = np.clip(np.arange(a + F0, a + F1), 0, T - 1)
    w = cqt[:, idx]                                            # (264, 8)
    b0 = max(0, (int(midi) - MIDI_LO) * BINS_PER_SEMI - 1)
    rel = np.zeros((REL, w.shape[1]), np.float32)
    seg = w[b0:b0 + REL]
    rel[:len(seg)] = seg
    rel = rel / (rel.max() + 1e-3)
    bands = w.reshape(-1, 24, w.shape[1]).mean(1)              # 11 coarse bands, absolute
    pre = cqt[:, max(0, a - 6):max(1, a - 2)].mean(1) if a > 2 else np.zeros(cqt.shape[0])
    flux = np.maximum(w[:, 1] - pre, 0).reshape(-1, 24).mean(1)
    return np.concatenate([rel[::3].ravel(), bands.ravel() / 5.0, flux / 5.0]).astype(np.float32)


def label_of(n):
    return 3 if n.get("dead") else 1 if n.get("slap") else 2 if n.get("pop") else 0


def build(neg_per_song=400):
    from bassnet.train import load_metas, song_key, frozen_keys
    from bassnet.decode import FPS
    val, test = frozen_keys("val"), frozen_keys("test")
    ds = {"train": [], "val": [], "test": []}
    rng = np.random.default_rng(0)
    seen = set()
    for m in load_metas():
        md5 = os.path.basename(m["npz"])[:-4]
        if md5 in seen:
            continue
        seen.add(md5)
        k = song_key(m["gp"])
        split = "val" if k in val else "test" if k in test else "train"
        lab = os.path.join(ROOT, "labels_v2", md5 + ".json")
        notes = json.load(open(lab, encoding="utf8"))["notes"] if os.path.exists(lab) else m["notes"]
        notes = [n for n in notes if not n.get("grace")]
        pos = [n for n in notes if label_of(n)]
        neg = [n for n in notes if not label_of(n)]
        neg = [neg[i] for i in rng.permutation(len(neg))[:neg_per_song]]
        if not notes:
            continue
        cqt = np.load(m["npz"])["cqt"].astype(np.float32)
        X = np.stack([attack_features(cqt, n["time"], n["midi"], FPS) for n in pos + neg])
        y = np.array([label_of(n) for n in pos + neg])
        ds[split].append({"md5": md5, "X": X, "y": y, "slap_song": int((y == 1).sum() + (y == 2).sum() > 10)})
    pickle.dump(ds, open(DS, "wb"))
    for s, v in ds.items():
        y = np.concatenate([g["y"] for g in v]) if v else np.zeros(0)
        print(s, len(v), "songs", {c: int((y == i).sum()) for i, c in enumerate(CLASSES)})


def make_model(nf, d=128):
    import torch.nn as nn
    return nn.Sequential(nn.Linear(nf, d), nn.GELU(), nn.Dropout(0.2), nn.Linear(d, d), nn.GELU(),
                         nn.Linear(d, len(CLASSES)))


def train(epochs=60, lr=1e-3):
    import torch
    ds = pickle.load(open(DS, "rb"))
    X = torch.from_numpy(np.concatenate([g["X"] for g in ds["train"]]))
    y = torch.from_numpy(np.concatenate([g["y"] for g in ds["train"]]))
    cnt = np.bincount(y.numpy(), minlength=len(CLASSES))
    w = torch.tensor((cnt.sum() / (cnt + 1)) ** 0.5, dtype=torch.float32)
    model = make_model(X.shape[1])
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-3)
    Xv = torch.from_numpy(np.concatenate([g["X"] for g in ds["val"]]))
    yv = np.concatenate([g["y"] for g in ds["val"]])
    best = -1
    os.makedirs(os.path.dirname(CKPT), exist_ok=True)
    for ep in range(epochs):
        model.train()
        perm = torch.randperm(len(y))
        for i in range(0, len(y), 512):
            b = perm[i:i + 512]
            loss = torch.nn.functional.cross_entropy(model(X[b] + 0.02 * torch.randn_like(X[b])), y[b], weight=w)
            opt.zero_grad()
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            p = model(Xv).argmax(-1).numpy()
        f1s = []
        for c in (1, 2, 3):
            tp, fp, fn = ((p == c) & (yv == c)).sum(), ((p == c) & (yv != c)).sum(), ((p != c) & (yv == c)).sum()
            f1s.append(2 * tp / max(1, 2 * tp + fp + fn))
        score = float(np.mean(f1s))
        if score > best:
            best = score
            torch.save({"model": model.state_dict(), "nf": X.shape[1]}, CKPT)
        if ep % 10 == 0 or score == best:
            print(f"epoch {ep} val F1 slap {f1s[0]:.3f} pop {f1s[1]:.3f} dead {f1s[2]:.3f}", flush=True)


def load():
    import torch
    c = torch.load(CKPT, map_location="cpu")
    m = make_model(c["nf"])
    m.load_state_dict(c["model"])
    return m.eval()


def evaluate():
    import torch
    import librosa
    import re
    from bassnet.dataset import compute_cqt
    from bassnet.decode import FPS
    model = load()
    ds = pickle.load(open(DS, "rb"))
    for s in ("val", "test"):
        if not ds[s]:
            continue
        X = torch.from_numpy(np.concatenate([g["X"] for g in ds[s]]))
        y = np.concatenate([g["y"] for g in ds[s]])
        with torch.no_grad():
            p = model(X).argmax(-1).numpy()
        line = f"{s} tabs:"
        for c in (1, 2, 3):
            tp, fp, fn = ((p == c) & (y == c)).sum(), ((p == c) & (y != c)).sum(), ((p != c) & (y == c)).sum()
            line += f"  {CLASSES[c]} P {tp / max(1, tp + fp):.2f} R {tp / max(1, tp + fn):.2f} (n={int((y == c).sum())})"
        print(line)
    # IDMT single notes (evaluation only)
    pat = re.compile(r"BS_\d+_EQ_\d+_(?:PS_)?([A-Z]+)_(?:ES_)?([A-Z]+)_(\d)_(\d+)\.wav$")
    root = os.path.join("D:" + os.sep, "BassData", "IDMT-SMT-BASS")
    rng = np.random.default_rng(1)
    for ps, es, want in (("FS", "NO", 0), ("PK", "NO", 0), ("MU", "NO", 0), ("ST", "NO", 1), ("SP", "NO", 2),
                         ("FS", "DN", 3)):
        fs = [f for f in glob.glob(os.path.join(root, "*", "*", "*.wav"))
              if (lambda mm: mm and mm.group(1) == ps and mm.group(2) == es)(pat.search(os.path.basename(f)))]
        fs = [fs[i] for i in rng.permutation(len(fs))[:80]]
        pred = []
        for f in fs:
            mm = pat.search(os.path.basename(f))
            midi = 28 + 5 * (int(mm.group(3)) - 1) + int(mm.group(4))
            yv, _ = librosa.load(f, sr=22050)
            yv = np.pad(yv, (2205, 4410))
            x = attack_features(compute_cqt(yv), 0.1, midi, FPS)
            with torch.no_grad():
                pred.append(int(model(torch.from_numpy(x)[None]).argmax(-1)))
        pred = np.array(pred)
        print(f"IDMT {ps}/{es}: expected {CLASSES[want]:5s} -> " +
              " ".join(f"{c} {np.mean(pred == i):.2f}" for i, c in enumerate(CLASSES)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "train", "eval"])
    a = ap.parse_args()
    {"build": build, "train": train, "eval": evaluate}[a.cmd]()


if __name__ == "__main__":
    main()
