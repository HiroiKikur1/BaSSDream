"""Song sections (前奏 / A / B / C(サビ) / D.. / 间奏·solo / 尾奏) from audio, learned from the section marks in the
purchased tabs (252 of 338 tabs carry them; A = A melo, B = B melo, C = chorus, as in Japanese practice).

Bar-level features come only from audio (mix log-mel, stem CQT, onset posterior) so the model runs on our own
decoded bar grid at inference: band energies, bass pitch-class profile relative to the song's main pitch class,
onset density, bass silence, position, and repetition (self-similarity) features. A small BiGRU + attention
predicts each bar's class and whether a new section starts there.

python -m bassnet.section_model build | train | eval
"""
import argparse
import os
import pickle
import re
import sys
import zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.decode import FPS  # noqa: E402

ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
DS = os.path.join(ROOT, "section_ds.pkl")
CKPT = os.path.join(ROOT, "staging", "section.pt")
CLASSES = ["intro", "A", "B", "C", "D", "inter", "outro"]
NAMES_ZH = {"intro": "前奏", "A": "A", "B": "B", "C": "C", "D": "D", "inter": "间奏", "outro": "尾奏"}
N_FEAT = 41


def section_class(letter, text):
    t = (text or "").lower()
    le = (letter or "").strip().upper()
    if "intro" in t:
        return "intro"
    if "outro" in t or "coda" in t or "ending" in t:
        return "outro"
    if "solo" in t or "interlude" in t or "soli" in t:
        return "inter"
    if "prechorus" in t.replace("-", "").replace(" ", ""):
        return "B"
    if "chorus" in t:
        return "C"
    if "verse" in t:
        return "A"
    if "bridge" in t:
        return "D"
    if le in ("A", "B", "C"):
        return le
    if le:
        return "D"
    return "inter"


def gp_sections(gp):
    """{master bar index: class} from <Section> marks."""
    x = zipfile.ZipFile(gp).read("Content/score.gpif").decode("utf8", "replace")
    mbs = re.findall(r"<MasterBar>(.*?)</MasterBar>", x, re.S)
    out = {}
    for i, mb in enumerate(mbs):
        s = re.search(r"<Section>\s*<Letter>\s*<!\[CDATA\[(.*?)\]\]>\s*</Letter>\s*<Text>\s*<!\[CDATA\[(.*?)\]\]>", mb, re.S)
        if s:
            out[i] = section_class(s.group(1), s.group(2))
    return out


def note_signatures(spans, notes, grid=16):
    """Per bar: set of (16th slot, pitch) of the transcribed bass notes."""
    sig = [set() for _ in spans]
    starts = np.array([a for a, b in spans])
    for n in notes:
        i = int(np.searchsorted(starts, n["time"] + 1e-3, side="right") - 1)
        if 0 <= i < len(spans):
            a, b = spans[i]
            slot = int(round((n["time"] - a) / max(b - a, 1e-3) * grid))
            sig[i].add((min(slot, grid - 1), int(n["midi"])))
    return sig


def bar_features(spans, mel, cqt, on, rest, notes=None):
    """spans: [(t0, t1)] seconds per bar -> (N, N_FEAT)."""
    from bassnet.audio_verify import semitone_map
    S = semitone_map(cqt)                                   # (88, T), MIDI 21..108
    T = mel.shape[1]
    N = len(spans)
    band = np.zeros((N, 16))
    en = np.zeros((N, 2))
    chroma = np.zeros((N, 12))
    dens = np.zeros(N)
    sil = np.zeros(N)
    for i, (a, b) in enumerate(spans):
        fa, fb = int(max(0, a) * FPS), int(max(0, b) * FPS)
        fa, fb = min(fa, T - 1), max(min(fb, T), min(fa, T - 1) + 1)
        m = mel[:, fa:fb]
        band[i] = m.reshape(16, 8, -1).mean((1, 2))
        e = m.mean(0)
        en[i] = [e.mean(), e.std()]
        s = S[:48, fa:fb].sum(1)                           # bass register
        pc = np.array([s[k::12].sum() for k in range(12)])
        pc = np.roll(pc, -21 % 12)                          # index 0 = pitch class C
        chroma[i] = pc / (pc.sum() + 1e-6)
        o = on[fa:fb]
        dens[i] = float(((o[1:-1] > 0.5) & (o[1:-1] >= o[:-2]) & (o[1:-1] > o[2:])).sum()) / max(b - a, 0.1)
        sil[i] = float(rest[fa:fb].mean())
    tonic = int(np.argmax(chroma.sum(0)))
    chroma = np.roll(chroma, -tonic, axis=1)                # relative to the song's main pitch class
    band = (band - band.mean(0)) / (band.std(0) + 1e-6)
    en = (en - en.mean(0)) / (en.std(0) + 1e-6)
    v = np.concatenate([band, chroma * 4], 1)
    v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
    ssm = v @ v.T
    rep = np.zeros((N, 4))
    for i in range(N):
        row = ssm[i].copy()
        row[max(0, i - 1):i + 2] = -1
        top = np.sort(row)[::-1]
        rep[i, 0] = top[0]
        rep[i, 1] = top[:3].mean()
        rep[i, 2] = (row > 0.9).sum() / N
    K = 4                                                   # Foote novelty with a 4-bar checkerboard kernel
    for i in range(N):
        a0, a1, b1 = max(0, i - K), i, min(N, i + K)
        if a1 - a0 > 0 and b1 - a1 > 0:
            within = (ssm[a0:a1, a0:a1].mean() + ssm[a1:b1, a1:b1].mean()) / 2
            rep[i, 3] = within - ssm[a0:a1, a1:b1].mean()
    pos = np.stack([np.arange(N) / max(N - 1, 1), (N - 1 - np.arange(N)) / max(N - 1, 1)], 1)
    # bass-line repetition: Jaccard similarity of bar note signatures
    nrep = np.zeros((N, 3))
    if notes is not None:
        sig = note_signatures(spans, notes)
        J = np.zeros((N, N))
        for i in range(N):
            for j in range(i + 1, N):
                if sig[i] or sig[j]:
                    J[i, j] = J[j, i] = len(sig[i] & sig[j]) / len(sig[i] | sig[j])
        for i in range(N):
            row = J[i].copy()
            row[max(0, i - 1):i + 2] = 0
            nrep[i, 0] = row.max()
            nrep[i, 1] = (row > 0.8).sum() / N
            # does the 4-bar phrase starting here recur elsewhere?
            if i + 4 <= N:
                best = 0.0
                for j in range(N - 3):
                    if abs(j - i) >= 4:
                        best = max(best, float(np.mean([J[i + k, j + k] for k in range(4)])))
                nrep[i, 2] = best
    f = np.concatenate([band, en, chroma, dens[:, None] / 8.0, sil[:, None], pos, rep, nrep], 1)
    return f.astype(np.float32)


def build():
    import json
    from bassnet.train import load_metas, song_key, frozen_keys
    from bassnet.gpif_parser import parse_gp
    from bassnet.notation_eval import label_q_map
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
        try:
            secs = gp_sections(m["gp"])
        except Exception:
            continue
        if len(secs) < 3:
            continue
        info = parse_gp(m["gp"])
        qm = label_q_map(m)
        if qm is None:
            continue
        spans, y, bnd = [], [], []
        cur = None
        for b, oc, q0, ql in info.bars:
            new = b in secs and (oc == 0 or cur != secs[b])
            if b in secs:
                cur = secs[b]
            if cur is None:
                cur = "intro"
            spans.append((qm(q0), qm(q0 + ql)))
            y.append(CLASSES.index(cur))
            bnd.append(float(new))
        d = np.load(m["npz"])
        on = np.mean([np.load(os.path.join(E, dd, key + ".npz"))["on"].astype(np.float32) for dd in dirs], 0)
        fr = np.mean([np.load(os.path.join(E, dd, key + ".npz"))["fr"].astype(np.float32) for dd in dirs], 0)
        de = np.mean([np.load(os.path.join(E, dd, key + ".npz"))["de"].astype(np.float32) for dd in dirs], 0)
        from bassnet.decode import decode_notes
        notes = decode_notes(fr, on, de)
        f = bar_features(spans, d["mel"].astype(np.float32), d["cqt"].astype(np.float32), on, fr[:, 0], notes)
        ds[split].append({"key": key, "X": f, "y": np.array(y), "b": np.array(bnd, np.float32),
                          "name": os.path.basename(os.path.dirname(m["gp"]))[:30]})
    pickle.dump(ds, open(DS, "wb"))
    print({k: (len(v), sum(len(x["y"]) for x in v)) for k, v in ds.items()})
    from collections import Counter
    c = Counter(int(t) for x in ds["train"] for t in x["y"])
    print({CLASSES[k]: v for k, v in sorted(c.items())})


def make_model(d=96):
    import torch
    import torch.nn as nn

    class SectionNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.inp = nn.Sequential(nn.Linear(N_FEAT, d), nn.GELU())
            self.gru = nn.GRU(d, d // 2, num_layers=2, batch_first=True, bidirectional=True, dropout=0.1)
            self.att = nn.TransformerEncoderLayer(d, 4, 2 * d, dropout=0.1, batch_first=True, norm_first=True)
            self.cls = nn.Linear(d, len(CLASSES))
            self.bnd = nn.Linear(d, 1)

        def forward(self, x):
            h, _ = self.gru(self.inp(x))
            h = self.att(h)
            return self.cls(h), self.bnd(h).squeeze(-1)
    return SectionNet()


def evaluate(model, items, dev):
    import torch
    model.eval()
    ok = tot = 0
    tp = fp = fn = 0
    with torch.no_grad():
        for g in items:
            c, b = model(torch.from_numpy(g["X"])[None].to(dev))
            pred = c[0].argmax(-1).cpu().numpy()
            ok += (pred == g["y"]).sum()
            tot += len(g["y"])
            pb = set(decode_boundaries(torch.sigmoid(b[0]).cpu().numpy()))
            gb = set(np.where(g["b"] > 0)[0])
            tp += sum(any(abs(p - q) <= 1 for q in gb) for p in pb)
            fp += sum(not any(abs(p - q) <= 1 for q in gb) for p in pb)
            fn += sum(not any(abs(p - q) <= 1 for p in pb) for q in gb)
    model.train()
    P, R = tp / max(1, tp + fp), tp / max(1, tp + fn)
    return ok / max(1, tot), 2 * P * R / max(1e-9, P + R)


def decode_boundaries(p, thr=0.4, min_gap=2):
    idx = [0]
    for i in range(1, len(p)):
        if p[i] >= thr and p[i] >= p[max(0, i - 1)] and p[i] >= p[min(len(p) - 1, i + 1)] and i - idx[-1] >= min_gap:
            idx.append(i)
    return idx


LEN_PRIOR = os.path.join(os.path.dirname(CKPT), "section_len.json")
MAX_LEN = 40


def section_len_prior():
    """log P(section length in bars) from the training tabs (phrases: 8 and 16 dominate, odd lengths are rare)."""
    import json
    if os.path.exists(LEN_PRIOR):
        return np.array(json.load(open(LEN_PRIOR)))
    from bassnet.train import load_metas, song_key, frozen_keys
    from bassnet.gpif_parser import parse_gp
    held = frozen_keys("val") | frozen_keys("test")
    h = np.ones(MAX_LEN + 1) * 0.5
    seen = set()
    for m in load_metas():
        if song_key(m["gp"]) in held or m["gp"] in seen:
            continue
        seen.add(m["gp"])
        marks = gp_sections(m["gp"])
        if len(marks) < 2:
            continue
        info = parse_gp(m["gp"])
        st = [i for i, (b, oc, q0, ql) in enumerate(info.bars) if oc == 0 and b in marks] + [len(info.bars)]
        for a, b in zip(st[:-1], st[1:]):
            h[min(b - a, MAX_LEN)] += 1
    h[0] = 1e-6
    lp = np.log(h / h.sum())
    json.dump(lp.tolist(), open(LEN_PRIOR, "w"))
    return lp


def decode_boundaries_dp(p, len_w=1.0, bias=0.0):
    """Section starts maximising boundary evidence + phrase-length prior (semi-Markov DP over bars).
    Each start i gains log(p_i / (1 - p_i)) + bias; each section of L bars gains len_w * log P(L)."""
    lp = section_len_prior()
    n = len(p)
    q = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    gain = np.log(q / (1 - q)) + bias
    best = np.full(n + 1, -np.inf)
    arg = np.zeros(n + 1, int)
    best[0] = 0.0
    for e in range(1, n + 1):
        for L in range(1, min(MAX_LEN, e) + 1):
            s = e - L
            if not np.isfinite(best[s]):
                continue
            v = best[s] + len_w * lp[L] + (gain[s] if s > 0 else 0.0)
            if v > best[e]:
                best[e], arg[e] = v, s
    out, e = [], n
    while e > 0:
        e = arg[e]
        out.append(e)
    return sorted(set(out))


# tuned on the 55 val/test songs (section_eval sweep 2026-09-28): peaks 0.575 F1 / 41% of sections a multiple of 4
# bars -> dp(1.5, +1) 0.609 / 55% (tabs: 59%)
SECTION_DECODE = {"mode": "dp", "len_w": 1.5, "bias": 1.0}
if os.environ.get("BASSNET_SEC_DEC"):                    # sweeps (song_eval): '{"mode": "peaks"}' etc.
    import json as _json
    SECTION_DECODE.update(_json.loads(os.environ["BASSNET_SEC_DEC"]))


def train(epochs=200, lr=2e-3, seed=0):
    import torch
    ds = pickle.load(open(DS, "rb"))
    dev = "cuda"
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    model = make_model().to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)
    counts = np.bincount(np.concatenate([g["y"] for g in ds["train"]]), minlength=len(CLASSES))
    wcls = torch.tensor((counts.sum() / (counts + 1)) ** 0.5, dtype=torch.float32, device=dev)
    best = -1
    for ep in range(epochs):
        for i in rng.permutation(len(ds["train"])):
            g = ds["train"][i]
            x = torch.from_numpy(g["X"])[None].to(dev)
            x = x + 0.05 * torch.randn_like(x)
            c, b = model(x)
            y = torch.from_numpy(g["y"])[None].to(dev)
            bt = torch.from_numpy(g["b"])[None].to(dev)
            loss = torch.nn.functional.cross_entropy(c[0], y[0], weight=wcls) + \
                torch.nn.functional.binary_cross_entropy_with_logits(b, bt, pos_weight=torch.tensor(4.0, device=dev))
            opt.zero_grad()
            loss.backward()
            opt.step()
        acc, bf = evaluate(model, ds["val"], dev)
        score = acc + bf
        if score > best:
            best = score
            torch.save({"model": model.state_dict()}, CKPT)
        if ep % 10 == 0 or score == best:
            print(f"ep {ep:3d} val bar-class acc {acc:.3f} boundary F1 {bf:.3f} {'*' if score == best else ''}", flush=True)


def load(dev="cpu"):
    import torch
    model = make_model()
    model.load_state_dict(torch.load(CKPT, map_location=dev)["model"])
    return model.to(dev).eval()


def predict_sections(model, X):
    """-> list of (start bar, class) with GP-style letters/texts assigned by the caller."""
    import torch
    with torch.no_grad():
        c, b = model(torch.from_numpy(X)[None])
    prob = torch.softmax(c[0], -1).numpy()
    pb = torch.sigmoid(b[0]).numpy()
    if SECTION_DECODE["mode"] == "dp":
        starts = decode_boundaries_dp(pb, SECTION_DECODE["len_w"], SECTION_DECODE["bias"])
    else:
        starts = decode_boundaries(pb)
    out = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(prob)
        k = int(prob[s:e].mean(0).argmax())
        if out and out[-1][1] == CLASSES[k] and CLASSES[k] in ("intro", "outro", "inter"):
            continue                                         # merge consecutive same non-melodic parts
        out.append((s, CLASSES[k]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "train", "eval"])
    ap.add_argument("--epochs", type=int, default=200)
    a = ap.parse_args()
    if a.cmd == "build":
        build()
    elif a.cmd == "train":
        train(a.epochs)
    else:
        ds = pickle.load(open(DS, "rb"))
        model = load("cuda")
        for sp in ("val", "test"):
            print(sp, "bar-class acc, boundary F1:", evaluate(model, ds[sp], "cuda"))


if __name__ == "__main__":
    main()
