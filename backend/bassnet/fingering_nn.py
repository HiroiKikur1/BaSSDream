"""Fingering as a sequence model (Phase 5): which string each note is played on, learned from 雪鹽子's tabs.

The current assignment (fingering_learn: emission + hand-movement costs, Viterbi) agrees with the tabs on ~87% of
notes and with the IDMT player on 72% of strings. Here a bidirectional GRU reads the whole line (pitch, timing,
the fret each string would need) and scores every playable string of each note; the fret follows from the string.
Split: frozen val/test songs are held out. Also checked on IDMT-SMT-Bass-Single-Tracks (a different player).

python -m bassnet.fingering_nn build | train | eval
"""
import argparse
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
DS = os.path.join(ROOT, "fingering_ds.pkl")
CKPT = os.path.join(ROOT, "staging", "fingering_nn.pt")
MAXF, MAXS = 24, 6


def features(midis, times, durs, tuning):
    """-> X (N, F) per-note features, C (N, MAXS, 3) per-string candidate features, mask (N, MAXS)."""
    n = len(midis)
    t = np.asarray(times, float)
    ioi = np.diff(t, prepend=t[0] - 1.0)
    X = np.zeros((n, 5), np.float32)
    X[:, 0] = (np.asarray(midis) - 40) / 12.0
    X[:, 1] = np.log(np.clip(ioi, 0.02, 4.0))
    X[:, 2] = np.log(np.clip(np.asarray(durs, float), 0.02, 4.0))
    X[:, 3] = len(tuning) - 4
    X[:, 4] = (np.diff(np.asarray(midis), prepend=midis[0])) / 12.0
    C = np.zeros((n, MAXS, 3), np.float32)
    mask = np.zeros((n, MAXS), bool)
    for i, m in enumerate(midis):
        for s, o in enumerate(tuning[:MAXS]):
            f = m - o
            if 0 <= f <= MAXF:
                mask[i, s] = True
                C[i, s] = [f / 12.0, float(f == 0), s / 5.0]
    return X, C, mask


def build():
    from bassnet.build_stems import build_index
    from bassnet.gpif_parser import parse_gp
    from bassnet.train import song_key, frozen_keys
    held = {"val": frozen_keys("val"), "test": frozen_keys("test")}
    ds = {"train": [], "val": [], "test": []}
    for gp in build_index():
        try:
            info = parse_gp(gp)
        except Exception:
            continue
        ns = sorted((n for n in info.notes if not n.grace and not n.dead and 0 <= n.fret <= MAXF
                     and 0 <= n.string < len(info.tuning)), key=lambda n: (n.qpos, n.midi))
        if len(ns) < 20 or len(info.tuning) > MAXS:
            continue
        k = song_key(gp)
        split = "val" if k in held["val"] else "test" if k in held["test"] else "train"
        times = [n.time if n.time else float(n.qpos) * 0.5 for n in ns]
        durs = [max(0.02, n.end - n.time) if n.end > n.time else float(n.qdur) * 0.5 for n in ns]
        X, C, mask = features([n.midi for n in ns], times, durs, info.tuning)
        y = np.array([n.string for n in ns])
        ok = mask[np.arange(len(y)), y]
        ds[split].append({"gp": gp, "X": X[ok], "C": C[ok], "mask": mask[ok], "y": y[ok], "tuning": info.tuning,
                          "midi": np.array([n.midi for n in ns])[ok], "times": np.array(times)[ok]})
    pickle.dump(ds, open(DS, "wb"))
    print({k: len(v) for k, v in ds.items()})


def make_model(d=128):
    import torch
    import torch.nn as nn

    class FingerNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.inp = nn.Sequential(nn.Linear(5 + MAXS * 4, d), nn.GELU())
            self.gru = nn.GRU(d, d // 2, num_layers=2, batch_first=True, bidirectional=True, dropout=0.1)
            self.head = nn.Sequential(nn.Linear(d + 3 + MAXS, d), nn.GELU(), nn.Linear(d, 1))

        def forward(self, X, C, mask):
            B, N, S, _ = C.shape
            z = torch.cat([X, C.reshape(B, N, -1), mask.float()], -1)
            h, _ = self.gru(self.inp(z))
            eye = torch.eye(S, device=X.device).expand(B, N, S, S)
            hs = torch.cat([h.unsqueeze(2).expand(B, N, S, h.shape[-1]), C, eye], -1)
            logit = self.head(hs).squeeze(-1)
            return logit.masked_fill(~mask, -1e4)
    return FingerNet()


def _t(g, dev):
    import torch
    return (torch.from_numpy(g["X"])[None].to(dev), torch.from_numpy(g["C"])[None].to(dev),
            torch.from_numpy(g["mask"])[None].to(dev))


def accuracy(model, items, dev):
    import torch
    model.eval()
    ok = tot = 0
    with torch.no_grad():
        for g in items:
            p = model(*_t(g, dev))[0].argmax(-1).cpu().numpy()
            ok += (p == g["y"]).sum()
            tot += len(g["y"])
    model.train()
    return ok / max(1, tot)


def train(epochs=40, lr=2e-3, seg=256):
    import torch
    from bassnet.thermal import guard, lower_priority
    lower_priority()
    ds = pickle.load(open(DS, "rb"))
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = make_model().to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)
    rng = np.random.default_rng(0)
    best = 0.0
    os.makedirs(os.path.dirname(CKPT), exist_ok=True)
    for ep in range(epochs):
        for i in rng.permutation(len(ds["train"])):
            guard()
            g = ds["train"][i]
            n = len(g["y"])
            a = int(rng.integers(0, max(1, n - seg)))
            sub = {k: g[k][a:a + seg] for k in ("X", "C", "mask", "y")}
            logit = model(*_t(sub, dev))[0]
            loss = torch.nn.functional.cross_entropy(logit, torch.from_numpy(sub["y"]).to(dev))
            opt.zero_grad()
            loss.backward()
            opt.step()
        acc = accuracy(model, ds["val"], dev)
        if acc > best:
            best = acc
            torch.save({"model": model.state_dict()}, CKPT)
        print(f"epoch {ep} val string agreement {acc:.4f}{' *' if acc == best else ''}", flush=True)


def baseline(items):
    """String agreement of the current learned-cost Viterbi (fretboard.assign_frets) on the same notes."""
    from bassnet.fretboard import assign_frets

    class _N:
        def __init__(self, m):
            self.midi, self.string, self.fret = int(m), 0, 0
    ok = tot = 0
    for g in items:
        objs = [_N(m) for m in g["midi"]]
        assign_frets(objs, g["tuning"], times=list(g["times"]))
        ok += sum(o.string == y for o, y in zip(objs, g["y"]))
        tot += len(objs)
    return ok / max(1, tot)


def assign_nn(midis, times, durs, tuning, model=None, dev="cpu"):
    """-> [(string, fret)] for a note line (app use)."""
    import torch
    if model is None:
        model = make_model()
        model.load_state_dict(torch.load(CKPT, map_location=dev)["model"])
        model.eval()
    X, C, mask = features(midis, times, durs, tuning)
    with torch.no_grad():
        p = model(torch.from_numpy(X)[None], torch.from_numpy(C)[None], torch.from_numpy(mask)[None])[0]
    s = p.argmax(-1).numpy()
    return [(int(si), int(m - tuning[si])) for si, m in zip(s, midis)]


def idmt(model):
    import glob
    import xml.etree.ElementTree as ET
    root = os.path.join("D:" + os.sep, "BassData", "IDMT-SMT-BASS-SINGLE-TRACKS", "annotation")
    items = []
    for x in sorted(glob.glob(os.path.join(root, "*.xml"))):
        r = ET.parse(x).getroot()
        tuning = [int(v) for v in r.findtext("globalParameter/instrumentTuning").split(",")]
        ev = [e for e in r.findall("transcription/event") if e.findtext("excitationStyle") != "DN"]
        ev = [e for e in ev if e.findtext("expressionStyle") != "HA"]           # harmonics: pitch != fret
        m = [int(e.findtext("pitch")) for e in ev]
        t = [float(e.findtext("onsetSec")) for e in ev]
        d = [float(e.findtext("offsetSec")) - float(e.findtext("onsetSec")) for e in ev]
        y = np.array([int(e.findtext("stringNumber")) - 1 for e in ev])
        X, C, mask = features(m, t, d, tuning)
        ok = mask[np.arange(len(y)), y]
        items.append({"X": X[ok], "C": C[ok], "mask": mask[ok], "y": y[ok], "midi": np.array(m)[ok],
                      "times": np.array(t)[ok], "tuning": tuning})
    return items


def evaluate():
    import torch
    ds = pickle.load(open(DS, "rb"))
    model = make_model()
    model.load_state_dict(torch.load(CKPT, map_location="cpu")["model"])
    for name, items in (("val tabs", ds["val"]), ("test tabs", ds["test"]), ("IDMT (other player)", idmt(model))):
        print(f"{name:22s} current Viterbi {baseline(items):.4f}   sequence model {accuracy(model, items, 'cpu'):.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "train", "eval"])
    a = ap.parse_args()
    {"build": build, "train": train, "eval": evaluate}[a.cmd]()


if __name__ == "__main__":
    main()
