"""Second-stage "arrangement" model: re-decides each note's pitch from the whole song's context.

The acoustic model hears one note at a time; tab authors decide octave / string idioms from the whole piece
(a riff keeps its octave when it repeats, slap lines move in root-octave shapes, lines stay in key). This
Transformer sees every decoded note of a song at once - its pitch posterior, onset strength, duration and place
in the beat grid - and outputs a residual on the acoustic log-probabilities, so it starts as the identity and
only overrides when the context is convincing. Trained on honest acoustic errors (cross-fold posteriors, see
xfold_post.py) against the purchased tabs.

python -m bassnet.lab.context_model build            # note-level datasets (CPU)
python -m bassnet.lab.context_model train [--epochs N]
python -m bassnet.lab.context_model eval --split val
"""
import argparse
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from bassnet.decode import decode_notes, decode_beats, FPS  # noqa: E402
from bassnet.model import PITCH_LO  # noqa: E402

ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
EVAL = os.path.join(ROOT, "eval")
DS = os.path.join(ROOT, "context_ds.pkl")
LR = 0.0
CKPT = os.path.join(ROOT, "staging", "context.pt")
NP = 48                       # pitch classes (MIDI PITCH_LO .. PITCH_LO+47)
N_SCAL = 10
PE_PERIODS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0)
STRING_W = 0.3                # auxiliary string-prediction loss weight (0 = off)


def note_features(fr, on, de, notes, beats, downs):
    """Per decoded note: log pitch posterior (NP), scalar features (N_SCAL)."""
    T = len(on)
    beats = np.asarray(beats, float)
    db = np.asarray(downs, float)
    P = np.zeros((len(notes), NP), np.float32)
    S = np.zeros((len(notes), N_SCAL), np.float32)
    bp = np.median(np.diff(beats)) if len(beats) > 2 else 0.5
    for i, n in enumerate(notes):
        a = int(round(n["time"] * FPS))
        nxt = int(round(notes[i + 1]["time"] * FPS)) if i + 1 < len(notes) else T
        w = fr[min(a + 2, max(a + 1, nxt - 1)):max(a + 3, min(nxt, a + 10)), 1:1 + NP]
        if len(w) == 0:
            w = fr[a:a + 1, 1:1 + NP]
        p = w.mean(0)
        P[i] = np.log(p / max(p.sum(), 1e-6) + 1e-5)
        # beat grid position
        k = int(np.searchsorted(beats, n["time"], side="right") - 1)
        if 0 <= k < len(beats) - 1:
            ph = (n["time"] - beats[k]) / max(beats[k + 1] - beats[k], 1e-3)
        else:
            ph = 0.0
        j = int(np.searchsorted(db, n["time"] + 0.03, side="right") - 1)
        in_bar = 0.0
        if 0 <= j < len(db) - 1:
            in_bar = (n["time"] - db[j]) / max(db[j + 1] - db[j], 1e-3)
        dur = (n["end"] - n["time"]) / bp
        ioi = ((notes[i + 1]["time"] - n["time"]) / bp) if i + 1 < len(notes) else 4.0
        S[i] = [np.log1p(dur), np.log1p(min(ioi, 16)), np.sin(2 * np.pi * ph), np.cos(2 * np.pi * ph),
                np.sin(2 * np.pi * in_bar), np.cos(2 * np.pi * in_bar), float(n.get("on_peak", 0.0)),
                float(de[a:a + 3].max()) if de is not None else 0.0, float(n.get("conf", 0.0)),
                n["time"] / 300.0]
    return P, S


def match_targets(notes, labels, tol=0.05, n_strings=4):
    """Tab pitch class (and string, counted on a 5-string layout: 0 = low B, 1 = E ...) for each decoded note whose
    onset matches a (non-grace, non-dead) tab note; -100 otherwise."""
    ref = sorted((x for x in labels if not x.get("grace") and not x.get("dead")), key=lambda x: x["time"])
    rt = np.array([x["time"] for x in ref])
    y = np.full(len(notes), -100, np.int64)
    ys = np.full(len(notes), -100, np.int64)
    off = 1 if n_strings == 4 else 0
    used = set()
    for i, n in enumerate(notes):
        if not len(rt):
            break
        j = int(np.argmin(np.abs(rt - n["time"])))
        if abs(rt[j] - n["time"]) <= tol and j not in used:
            c = int(ref[j]["midi"]) - PITCH_LO
            if 0 <= c < NP:
                y[i] = c
                st = ref[j].get("string")
                if st is not None and 0 <= st + off < 5:
                    ys[i] = st + off
                used.add(j)
    return y, ys


def song_item(m, post):
    fr, on, de, be, do = post
    notes = decode_notes(fr, on, de)
    beats, downs, _ = decode_beats(be, do)
    P, S = note_features(fr, on, de, notes, beats, downs)
    y, ys = match_targets(notes, m["notes"], n_strings=len(m["tuning"]))
    return {"key": os.path.basename(m["npz"])[:-4], "P": P, "S": S, "y": y, "ys": ys,
            "name": os.path.basename(os.path.dirname(m["gp"]))[:30]}


def load_post(dirs, key):
    acc = None
    for d in dirs:
        z = np.load(os.path.join(EVAL, d, key + ".npz"))
        arr = [z[k].astype(np.float32) for k in ("fr", "on", "de", "be", "do")]
        acc = arr if acc is None else [x + y for x, y in zip(acc, arr)]
    return [x / len(dirs) for x in acc]


def build():
    from bassnet.train import load_metas, song_key, frozen_keys
    val, test = frozen_keys("val"), frozen_keys("test")
    ds = {"train": [], "val": [], "test": []}
    for m in load_metas():
        k = song_key(m["gp"])
        key = os.path.basename(m["npz"])[:-4]
        if k in val:
            split, dirs = "val", ("val_v3", "val_v4")
        elif k in test:
            split, dirs = "test", ("post_cache_v3", "post_cache_v4")
        else:
            split, dirs = "train", ("xfold",)
        if not all(os.path.exists(os.path.join(EVAL, d, key + ".npz")) for d in dirs):
            continue
        ds[split].append(song_item(m, load_post(dirs, key)))
    pickle.dump(ds, open(DS, "wb"))
    print({k: (len(v), sum(len(x["y"]) for x in v)) for k, v in ds.items()})


def make_model(d=128, layers=4, heads=4):
    import torch
    import torch.nn as nn

    class ContextNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.inp = nn.Sequential(nn.Linear(NP + N_SCAL + 2 * len(PE_PERIODS), d), nn.GELU(), nn.Linear(d, d))
            self.register_buffer("per", torch.tensor(PE_PERIODS, dtype=torch.float32))
            enc = nn.TransformerEncoderLayer(d, heads, 4 * d, dropout=0.1, batch_first=True, norm_first=True)
            self.enc = nn.TransformerEncoder(enc, layers)
            self.conv = nn.Conv1d(d, d, 5, padding=2)          # local order (neighbouring notes)
            self.out = nn.Linear(d, NP)
            self.string = nn.Linear(d, 5)                       # auxiliary: which string a bassist would use
            nn.init.zeros_(self.out.weight)
            nn.init.zeros_(self.out.bias)

        def forward(self, P, S, pad):
            # pitch posteriors are fed relative to the song's own pitch centre, so transposition is free
            t = S[..., 9:10] * 300.0                            # seconds: time position encoding
            ang = 2 * np.pi * t / self.per
            x = self.inp(torch.cat([P, S, torch.sin(ang), torch.cos(ang)], -1))
            x = x + self.conv(x.transpose(1, 2)).transpose(1, 2)
            h = self.enc(x, src_key_padding_mask=pad)
            return P + self.out(h), self.string(h)              # residual on acoustic log-probs
    return ContextNet()


def batches(items, bs, rng, shift=True, crop=1024):
    import torch
    idx = rng.permutation(len(items))
    for s in range(0, len(idx), bs):
        grp = [items[i] for i in idx[s:s + bs]]
        L = min(crop, max(len(g["y"]) for g in grp))
        P = np.full((len(grp), L, NP), np.log(1e-5), np.float32)
        S = np.zeros((len(grp), L, N_SCAL), np.float32)
        Y = np.full((len(grp), L), -100, np.int64)
        YS = np.full((len(grp), L), -100, np.int64)
        pad = np.ones((len(grp), L), bool)
        for b, g in enumerate(grp):
            n = len(g["y"])
            o = rng.integers(0, n - L + 1) if n > L else 0
            p, sc, y, ys = g["P"][o:o + L], g["S"][o:o + L], g["y"][o:o + L].copy(), g["ys"][o:o + L]
            if shift:
                k = int(rng.integers(-4, 5))
                p = np.roll(p, k, axis=1)
                if k > 0:
                    p[:, :k] = np.log(1e-5)
                elif k < 0:
                    p[:, k:] = np.log(1e-5)
                ok = y >= 0
                y[ok] = y[ok] + k
                y[(y < 0) | (y >= NP)] = -100
            P[b, :len(y)], S[b, :len(y)], Y[b, :len(y)], YS[b, :len(y)] = p, sc, y, ys
            pad[b, :len(y)] = False
        yield torch.from_numpy(P), torch.from_numpy(S), torch.from_numpy(Y), torch.from_numpy(YS), torch.from_numpy(pad)


def accuracy(model, items, dev):
    import torch
    model.eval()
    base = ok = tot = 0
    with torch.no_grad():
        for g in items:
            P = torch.from_numpy(g["P"])[None].to(dev)
            S = torch.from_numpy(g["S"])[None].to(dev)
            pad = torch.zeros(1, len(g["y"]), dtype=torch.bool, device=dev)
            out = model(P, S, pad)[0][0].argmax(-1).cpu().numpy()
            y = g["y"]
            m = y >= 0
            tot += m.sum()
            ok += (out[m] == y[m]).sum()
            base += (g["P"].argmax(-1)[m] == y[m]).sum()
    model.train()
    return base / max(1, tot), ok / max(1, tot)


def train(epochs=60, lr=3e-4, seed=0, out_path=None):
    lr = LR or lr
    import torch
    ds = pickle.load(open(DS, "rb"))
    dev = "cuda"
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    model = make_model().to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)
    best = -1
    for ep in range(epochs):
        losses = []
        for P, S, Y, YS, pad in batches(ds["train"], 4, rng):
            P, S, Y, YS, pad = P.to(dev), S.to(dev), Y.to(dev), YS.to(dev), pad.to(dev)
            out, st = model(P, S, pad)
            loss = torch.nn.functional.cross_entropy(out.reshape(-1, NP), Y.reshape(-1), ignore_index=-100)
            if STRING_W and (YS >= 0).any():
                loss = loss + STRING_W * torch.nn.functional.cross_entropy(st.reshape(-1, 5), YS.reshape(-1),
                                                                           ignore_index=-100)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            losses.append(loss.item())
        b, a = accuracy(model, ds["val"], dev)
        tb, ta = accuracy(model, ds["train"][:40], dev)
        flag = ""
        if a > best:
            best = a
            torch.save({"model": model.state_dict(), "string_w": STRING_W}, out_path or CKPT)
            flag = "*"
        print(f"ep {ep:3d} loss {np.mean(losses):.4f} | train pitch {tb:.4f}->{ta:.4f} | val pitch {b:.4f}->{a:.4f} {flag}",
              flush=True)


def load(dev="cpu"):
    import torch
    model = make_model()
    model.load_state_dict(torch.load(CKPT, map_location=dev)["model"])
    return model.to(dev).eval()


def correct_notes(model, fr, on, de, notes, beats, downs):
    """In place: replace each decoded note's pitch by the context model's choice."""
    import torch
    if not notes:
        return notes
    P, S = note_features(fr, on, de, notes, beats, downs)
    with torch.no_grad():
        out = model(torch.from_numpy(P)[None], torch.from_numpy(S)[None],
                    torch.zeros(1, len(notes), dtype=torch.bool))[0][0].argmax(-1).numpy()
    for n, c in zip(notes, out):
        if not n.get("dead"):
            n["midi"] = int(PITCH_LO + c)
    return notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "train", "eval"])
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=CKPT)
    ap.add_argument("--split", default="val")
    ap.add_argument("--string-w", type=float, default=0.3)
    ap.add_argument("--lr", type=float, default=0.0)
    a = ap.parse_args()
    global STRING_W, LR
    STRING_W, LR = a.string_w, a.lr
    if a.cmd == "build":
        build()
    elif a.cmd == "train":
        train(a.epochs, seed=a.seed, out_path=a.out)
    else:
        ds = pickle.load(open(DS, "rb"))
        model = load("cuda")
        print(a.split, "pitch base->context", accuracy(model, ds[a.split], "cuda"))


if __name__ == "__main__":
    main()
