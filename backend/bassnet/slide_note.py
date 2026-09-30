"""Slides from the pitch contour of each note (bass stem CQT, 3 bins per semitone).

A slide is the pitch moving continuously: out of a note downwards / upwards at its end (GP "slide out"), into a
note from below, or from one note into the next without a new pluck (legato slide). The frame classifier's
technique head is too unreliable to switch on (tech_thr.json), so each decoded note gets contour features --
the fine pitch track (harmonic-summed CQT, parabolic interpolation) at its start, body, end and just after it, the
glide between it and the next note, and how weak the next attack is -- and one gradient-boosted classifier per
slide type, trained on the purchased tabs' slide marks (labels_v2, training songs, honest cross-fold notes).

python -m bassnet.slide_note build | train | eval
"""
import argparse
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
DS = os.path.join(ROOT, "slide_ds.pkl")
MODEL = os.path.join(ROOT, "slide_note.pkl")
KINDS = {"legato": 2, "out_down": 4, "out_up": 8, "in_below": 16}
BPS = 3                        # CQT bins per semitone
LO = 21                        # MIDI of CQT bin 0


def pitch_track(cqt, frames, centre, span=8):
    """Fractional MIDI pitch and salience per frame, searched within +-span semitones of `centre`."""
    nb, T = cqt.shape
    frames = np.asarray(frames, int)
    b0 = max(0, (centre - span - LO) * BPS)
    b1 = min(nb - 1, (centre + span - LO) * BPS)
    ok = (frames >= 0) & (frames < T)
    fc = np.clip(frames, 0, T - 1)
    C = np.pad(cqt, ((0, 36), (0, 0)))[:, fc]
    sal = C[b0:b1 + 1] + 0.5 * C[b0 + 36:b1 + 37]              # + octave above (2nd harmonic)
    k = np.argmax(sal, 0)
    kk = np.clip(k, 1, sal.shape[0] - 2)
    cols = np.arange(sal.shape[1])
    a, b, c = sal[kk - 1, cols], sal[kk, cols], sal[kk + 1, cols]
    den = a - 2 * b + c
    off = np.where((k == kk) & (np.abs(den) > 1e-6), 0.5 * (a - c) / np.where(np.abs(den) > 1e-6, den, 1.0), 0.0)
    p = LO + (b0 + k + off) / BPS - 1.0 / BPS         # bin 0 sits a third of a semitone below MIDI 21 (dataset.compute_cqt)
    e = sal.max(0)
    return np.where(ok, p, np.nan), np.where(ok, e, 0.0)


def note_features(cqt, on, notes, fps):
    """(N, F) contour features for time-sorted decoded notes."""
    F = []
    for i, n in enumerate(notes):
        t0, t1, p = n["time"], n["end"], int(n["midi"])
        nxt = notes[i + 1] if i + 1 < len(notes) else None
        t2 = nxt["time"] if nxt else t1 + 1.0
        a, b = int(round(t0 * fps)), max(int(round(t0 * fps)) + 2, int(round(t1 * fps)))
        post = int(round(min(t1 + 0.15, t2) * fps))
        fr_all = np.arange(a - 3, max(post, b) + 1)
        pt, en = pitch_track(cqt, fr_all, p)
        idx = lambda f: f - (a - 3)  # noqa: E731
        body = slice(idx(a) + max(1, (b - a) // 8), idx(a) + max(2, int((b - a) * 0.6)))
        pb = np.nanmedian(pt[body]) if np.isfinite(pt[body]).any() else p
        eb = float(np.nanmax(en[body])) if len(en[body]) else 1.0
        start = pt[idx(a):idx(a) + 3]
        tail = pt[idx(a) + int((b - a) * 0.75):idx(b) + 1]
        after = slice(idx(b), idx(max(b + 1, post)) + 1)
        pa, ea = pt[after], en[after]
        live = ea > 0.35 * eb
        d_after = float(np.nanmean(pa[live]) - pb) if live.any() else 0.0
        d_tail = float(np.nanmean(tail) - pb) if np.isfinite(tail).any() else 0.0
        d_start = float(np.nanmean(start) - pb) if np.isfinite(start).any() else 0.0
        e_after = float(ea.mean() / (eb + 1e-6)) if len(ea) else 0.0
        # glide towards the next note: tracked pitch strictly between the two nominal pitches near the boundary
        glide = 0.0
        q = int(nxt["midi"]) if nxt else p
        if nxt and abs(q - p) >= 2 and t2 - t1 < 0.25:
            g0, g1 = int(round((t1 - 0.12) * fps)), int(round(t2 * fps)) + 1
            gp, ge = pitch_track(cqt, np.arange(g0, g1), (p + q) // 2, span=abs(q - p) // 2 + 3)
            lo, hi = min(p, q) + 0.6, max(p, q) - 0.6
            glide = float(np.mean((gp > lo) & (gp < hi) & (ge > 0.35 * eb))) if len(gp) else 0.0
        on_next = float(on[max(0, int(round(t2 * fps)) - 2):int(round(t2 * fps)) + 3].max()) if nxt else 0.0
        on_self = float(on[max(0, a - 2):a + 3].max())
        F.append([t1 - t0, pb - p, d_start, d_tail, d_after, e_after, glide, min(1.0, t2 - t1),
                  float(np.clip(q - p, -12, 12)), on_next, on_self, float(t2 - t1 > 0.15), n.get("conf", 1.0)])
    return np.array(F, np.float32).reshape(len(notes), 13)


def _match(dec, lab, tol=0.05):
    """Label slide bitmask for every decoded note (0 if unmatched)."""
    lt = np.array([x["time"] for x in lab])
    out = np.zeros(len(dec), int)
    for i, n in enumerate(dec):
        if not len(lt):
            break
        j = int(np.argmin(np.abs(lt - n["time"])))
        if abs(lt[j] - n["time"]) < tol and lab[j]["midi"] % 12 == n["midi"] % 12:
            out[i] = int(lab[j].get("slide", 0))
    return out


def build():
    import json
    from bassnet.phase_audit import song_inputs
    from bassnet.decode import decode_notes, FPS
    ds = []
    for m, split, (fr, on, de, be, do) in song_inputs():
        md5 = os.path.basename(m["npz"])[:-4]
        lf = os.path.join(ROOT, "labels_v2", md5 + ".json")
        lab = json.load(open(lf, encoding="utf8"))["notes"] if os.path.exists(lf) else m["notes"]
        lab = sorted((x for x in lab if not x.get("grace")), key=lambda x: x["time"])
        dec = sorted((x for x in decode_notes(fr, on, de) if not x.get("dead")), key=lambda x: x["time"])
        if len(dec) < 20:
            continue
        cqt = np.load(m["npz"])["cqt"].astype(np.float32)
        X = note_features(cqt, on, dec, FPS)
        ds.append({"md5": md5, "split": split, "X": X, "y": _match(dec, lab)})
        print(split, md5, len(dec), flush=True)
    pickle.dump(ds, open(DS, "wb"))


def train():
    from sklearn.ensemble import HistGradientBoostingClassifier
    ds = pickle.load(open(DS, "rb"))
    tr = [d for d in ds if d["split"] == "train"]
    X = np.concatenate([d["X"] for d in tr])
    Y = np.concatenate([d["y"] for d in tr])
    models = {}
    for k, bit in KINDS.items():
        y = (Y & bit) > 0
        clf = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
                                             l2_regularization=1.0, class_weight={0: 1.0, 1: 3.0}, random_state=0)
        clf.fit(X, y)
        models[k] = clf
        print(k, "train positives", int(y.sum()))
    pickle.dump(models, open(MODEL, "wb"))


def evaluate():
    ds = pickle.load(open(DS, "rb"))
    models = pickle.load(open(MODEL, "rb"))
    ev = [d for d in ds if d["split"] in ("val", "test")]
    X = np.concatenate([d["X"] for d in ev])
    Y = np.concatenate([d["y"] for d in ev])
    for k, bit in KINDS.items():
        p = models[k].predict_proba(X)[:, 1]
        y = (Y & bit) > 0
        line = f"{k:9s} n={int(y.sum()):4d}"
        for thr in (0.5, 0.7, 0.8, 0.9):
            sel = p >= thr
            tp = int((sel & y).sum())
            line += f" | thr {thr}: P {tp / max(1, sel.sum()):.2f} R {tp / max(1, y.sum()):.2f} (flagged {int(sel.sum())})"
        print(line)


def detect(cqt, on, notes, fps, thr=None):
    """Sets the GP slide bits on decoded notes in place (production)."""
    import json
    if not os.path.exists(MODEL) or not notes:
        return notes
    models = pickle.load(open(MODEL, "rb"))
    thr = thr or json.load(open(THR_FILE)) if os.path.exists(THR_FILE) else {k: 0.9 for k in KINDS}
    order = sorted(range(len(notes)), key=lambda i: notes[i]["time"])
    live = [i for i in order if not notes[i].get("dead")]
    ns = [notes[i] for i in live]
    X = note_features(cqt, on, ns, fps)
    probs = {k: models[k].predict_proba(X)[:, 1] for k in KINDS}
    for j, n in enumerate(ns):
        s = n.get("slide", 0) & ~(2 | 4 | 8 | 16)
        if probs["legato"][j] >= thr["legato"] and j + 1 < len(ns):
            s |= 2
        elif probs["out_down"][j] >= thr["out_down"] and probs["out_down"][j] >= probs["out_up"][j]:
            s |= 4
        elif probs["out_up"][j] >= thr["out_up"]:
            s |= 8
        if probs["in_below"][j] >= thr["in_below"]:
            s |= 16
        n["slide"] = s
        n["slide_p"] = {k: round(float(v[j]), 3) for k, v in probs.items()}
    return notes


THR_FILE = os.path.join(ROOT, "slide_thr.json")

# ------------------------------------------------------------------ patch CNN (learns the glide shape itself)
PATCH_DS = os.path.join(ROOT, "slide_patch_ds.npz")
CNN = os.path.join(ROOT, "staging", "slide_cnn.pt")
LOW_ST, HIGH_ST = 14, 10          # semitones below / above the note in the patch
END_PRE, END_POST = 22, 14        # frames before / after the note end
ST_PRE, ST_POST = 8, 12           # frames around the note start


def note_patches(cqt, notes, fps):
    """(N, 2, H, W) pitch-relative CQT patches: channel 0 around the note's end, channel 1 around its start
    (zero-padded to the same width). H = 3 bins x (LOW_ST + HIGH_ST) semitones."""
    nb, T = cqt.shape
    H = BPS * (LOW_ST + HIGH_ST)
    W = END_PRE + END_POST
    C = np.pad(cqt, ((H, H), (W, W)))
    out = np.zeros((len(notes), 2, H, W), np.float16)
    for i, n in enumerate(notes):
        b0 = (int(n["midi"]) - LOW_ST - LO) * BPS + 1 + H         # +1: bin of the exact semitone
        e = int(round(n["end"] * fps)) + W
        s = int(round(n["time"] * fps)) + W
        pe = C[b0:b0 + H, e - END_PRE:e + END_POST]
        ps = C[b0:b0 + H, s - ST_PRE:s + ST_POST]
        mx = max(1e-3, float(pe.max()), float(ps.max()))
        out[i, 0] = pe / mx
        out[i, 1, :, :ps.shape[1]] = ps / mx
    return out


def build_patches(neg_per_song=300):
    import json
    from bassnet.phase_audit import song_inputs
    from bassnet.decode import decode_notes, FPS
    rng = np.random.default_rng(0)
    Xs, Ys, Ss, Gs = [], [], [], []
    for gi, (m, split, (fr, on, de, be, do)) in enumerate(song_inputs()):
        md5 = os.path.basename(m["npz"])[:-4]
        lf = os.path.join(ROOT, "labels_v2", md5 + ".json")
        lab = json.load(open(lf, encoding="utf8"))["notes"] if os.path.exists(lf) else m["notes"]
        lab = sorted((x for x in lab if not x.get("grace")), key=lambda x: x["time"])
        dec = sorted((x for x in decode_notes(fr, on, de) if not x.get("dead")), key=lambda x: x["time"])
        if len(dec) < 20:
            continue
        y = _match(dec, lab)
        pos = np.where((y & (2 | 4 | 8 | 16)) > 0)[0]
        neg = np.where((y & (2 | 4 | 8 | 16)) == 0)[0]
        if split == "train":
            neg = rng.permutation(neg)[:neg_per_song]
        keep = np.concatenate([pos, neg])
        cqt = np.load(m["npz"])["cqt"].astype(np.float32)
        Xs.append(note_patches(cqt, [dec[i] for i in keep], FPS))
        Ys.append(y[keep])
        Ss.append(np.full(len(keep), {"train": 0, "val": 1, "test": 2}[split]))
        Gs.append(np.full(len(keep), gi))
        print(split, md5, len(keep), flush=True)
    np.savez_compressed(PATCH_DS, X=np.concatenate(Xs), y=np.concatenate(Ys), split=np.concatenate(Ss),
                        song=np.concatenate(Gs))


def make_cnn():
    import torch.nn as nn
    return nn.Sequential(
        nn.Conv2d(2, 24, 3, padding=1), nn.BatchNorm2d(24), nn.GELU(), nn.MaxPool2d(2),
        nn.Conv2d(24, 48, 3, padding=1), nn.BatchNorm2d(48), nn.GELU(), nn.MaxPool2d(2),
        nn.Conv2d(48, 64, 3, padding=1), nn.BatchNorm2d(64), nn.GELU(), nn.AdaptiveAvgPool2d((3, 3)),
        nn.Flatten(), nn.Dropout(0.3), nn.Linear(64 * 9, 64), nn.GELU(), nn.Linear(64, len(KINDS)))


def train_cnn(epochs=25, lr=2e-3):
    import torch
    from bassnet.thermal import guard, lower_priority
    lower_priority()
    d = np.load(PATCH_DS)
    X, y, sp = d["X"], d["y"], d["split"]
    bits = np.array(list(KINDS.values()))
    Y = ((y[:, None] & bits[None]) > 0).astype(np.float32)
    tr, va = np.where(sp == 0)[0], np.where(sp > 0)[0]
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    net = make_cnn().to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-3)
    posw = torch.tensor(np.clip((1 - Y[tr].mean(0)) / Y[tr].mean(0).clip(1e-4), 1, 20), device=dev)
    Xv = torch.from_numpy(X[va].astype(np.float32)).to(dev)
    best = -1
    os.makedirs(os.path.dirname(CNN), exist_ok=True)
    for ep in range(epochs):
        net.train()
        perm = np.random.default_rng(ep).permutation(tr)
        for i in range(0, len(perm), 256):
            guard()
            b = perm[i:i + 256]
            xb = torch.from_numpy(X[b].astype(np.float32)).to(dev)
            if np.random.rand() < 0.5:                      # small pitch jitter
                xb = torch.roll(xb, int(np.random.randint(-1, 2)), dims=2)
            loss = torch.nn.functional.binary_cross_entropy_with_logits(net(xb), torch.from_numpy(Y[b]).to(dev),
                                                                        pos_weight=posw)
            opt.zero_grad()
            loss.backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            p = torch.sigmoid(torch.cat([net(Xv[i:i + 1024]) for i in range(0, len(Xv), 1024)])).cpu().numpy()
        aps = []
        for j in range(len(KINDS)):
            yy = Y[va, j]
            order = np.argsort(-p[:, j])
            tp = np.cumsum(yy[order])
            prec = tp / np.arange(1, len(order) + 1)
            aps.append(float((prec * yy[order]).sum() / max(1, yy.sum())))
        score = float(np.mean(aps))
        if score > best:
            best = score
            torch.save({"model": net.state_dict()}, CNN)
        print(f"epoch {ep} val average precision " + " ".join(f"{k} {a:.3f}" for k, a in zip(KINDS, aps)), flush=True)


def eval_cnn():
    import torch
    d = np.load(PATCH_DS)
    X, y, sp = d["X"], d["y"], d["split"]
    net = make_cnn()
    net.load_state_dict(torch.load(CNN, map_location="cpu")["model"])
    net.eval()
    va = np.where(sp > 0)[0]
    with torch.no_grad():
        p = torch.sigmoid(torch.cat([net(torch.from_numpy(X[va[i:i + 1024]].astype(np.float32)))
                                     for i in range(0, len(va), 1024)])).numpy()
    for j, (k, bit) in enumerate(KINDS.items()):
        yy = (y[va] & bit) > 0
        line = f"{k:9s} n={int(yy.sum()):4d}"
        for thr in (0.5, 0.7, 0.8, 0.9, 0.95):
            sel = p[:, j] >= thr
            tp = int((sel & yy).sum())
            line += f" | {thr}: P {tp / max(1, sel.sum()):.2f} R {tp / max(1, yy.sum()):.2f}"
        print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "train", "eval", "patches", "cnn", "cnn_eval"])
    a = ap.parse_args()
    {"build": build, "train": train, "eval": evaluate, "patches": build_patches, "cnn": train_cnn,
     "cnn_eval": eval_cnn}[a.cmd]()


if __name__ == "__main__":
    main()
