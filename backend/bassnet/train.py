"""Trains BassNet on GP-aligned bass stems. Usage: python -m bassnet.train [--epochs N] [--resume]"""
import paths
import argparse
import glob
import json
import os
import random
import re
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.model import BassNet, PITCH_LO, PITCH_HI, BINS_PER_SEMI, TECH_NAMES  # noqa: E402
N_TECH = len(TECH_NAMES)
from bassnet.decode import decode_notes, note_metrics, decode_beats, FPS  # noqa: E402

ROOT = paths.cache(r"bassnet")
FEAT = os.path.join(ROOT, "feats")
CKPT = os.path.join(ROOT, "bassnet.pt")
CROP = 862            # ~10 s
MIN_RATE = 0.85


def song_key(gp_path):
    name = os.path.basename(os.path.dirname(gp_path)).lower()
    name = re.sub(r"[『』「」【】\[\]()（）]", " ", name)
    name = re.sub(r"(4|5)\s*(弦|st\.?|string)", " ", name)
    name = re.sub(r"(live|ver\.?|short|full|game size|tv size|現場版|現場|studio)", " ", name)
    tok = [t for t in re.split(r"[\s_/|×x]+", name) if t]
    return tok[0] if tok else name


SPLIT_FILE = os.path.join(ROOT, "split_keys.json")


def frozen_keys(kind):
    """Song keys of the frozen test / val sets (split_keys.json). Songs added to the library later always go to
    training, so earlier results stay comparable and nothing leaks from test into train."""
    return set(json.load(open(SPLIT_FILE, encoding="utf8"))[kind])


def split_songs(metas, test_frac=0.12, seed=7):
    """Song-level split: frozen test keys if split_keys.json exists, else the original seeded shuffle."""
    if os.path.exists(SPLIT_FILE):
        test = frozen_keys("test")
    else:
        idx = json.load(open(os.path.join(ROOT, "index.json"), encoding="utf8"))
        keys = sorted({song_key(g) for g in idx})
        rng = random.Random(seed)
        rng.shuffle(keys)
        test = set(keys[:max(3, int(len(keys) * test_frac))])
    tr = [m for m in metas if song_key(m["gp"]) not in test]
    te = [m for m in metas if song_key(m["gp"]) in test]
    return tr, te


LABEL_DIR = os.environ.get("BASSNET_LABELS", "")   # --labels: directory of re-aligned label jsons (same file names as feats/), overriding feats/


def fold_of(gp_path, k=2):
    import hashlib
    return int(hashlib.md5(song_key(gp_path).encode("utf8")).hexdigest(), 16) % k


def load_metas():
    metas = []
    for f in sorted(glob.glob(os.path.join(FEAT, "*.json"))):
        lf = os.path.join(LABEL_DIR, os.path.basename(f)) if LABEL_DIR else ""
        m = json.load(open(lf if lf and os.path.exists(lf) else f, encoding="utf8"))
        m["npz"] = f.replace(".json", ".npz")
        if m["rate_final"] >= MIN_RATE and os.path.exists(m["npz"]):
            metas.append(m)
    return metas


def make_targets(m, T):
    frame = np.zeros(T, np.int64)
    onset = np.zeros(T, np.float32)
    dead = np.zeros(T, np.float32)
    mask = np.zeros(T, np.float32)
    rep = np.zeros(T, np.float32)
    sus = np.zeros(T, np.float32)
    tech = np.zeros((T, N_TECH), np.float32)
    alt = np.zeros(T, np.int64)       # >0: the frame's note may also be read one octave lower (see audio_verify)
    notes = [n for n in m["notes"] if not n.get("grace")]
    if notes:
        a0 = int(max(0, notes[0]["time"] * FPS - 2 * FPS))
        a1 = int(min(T, notes[-1]["end"] * FPS + 2 * FPS))
        mask[a0:a1] = 1.0                       # only supervise inside the transcribed span
    prev = None
    for n in notes:
        a = int(round(n["time"] * FPS))
        b = int(round(n["end"] * FPS))
        p, prev = prev, n
        if a >= T or b <= 0:
            continue
        if n.get("dead"):
            dead[max(0, a):min(T, a + 1)] = 1.0
            onset[max(0, a):min(T, a + 1)] = 1.0
            continue
        cls = n["midi"] - PITCH_LO + 1
        if not (1 <= cls <= PITCH_HI - PITCH_LO + 1):
            continue
        frame[max(0, a):min(T, max(b, a + 2))] = cls
        if OCT_TOL and n.get("oct_amb") and cls - 12 >= 1:
            alt[max(0, a):min(T, max(b, a + 2))] = cls - 12
        onset[max(0, a):min(T, a + 1)] = 1.0
        if p is not None and not p.get("dead") and p.get("midi") == n["midi"] and p["end"] >= n["time"] - 0.03:
            rep[max(0, a):min(T, a + 1)] = 1.0
        if 0 <= a < T:
            sl = int(n.get("slide", 0) or 0)
            flags = [bool(p is not None and int(p.get("slide", 0) or 0) & 3), bool(sl & 4), bool(sl & 8),
                     bool(sl & 48), bool(n.get("hopo_d")), bool(n.get("slap")), bool(n.get("pop"))]
            for k, v in enumerate(flags):
                if v:
                    tech[a, k] = 1.0
        sus[max(0, a + 3):max(0, min(T, b - 2))] = 1.0
    beat = np.zeros(T, np.float32)
    down = np.zeros(T, np.float32)
    for t in m.get("beats", []):
        i = int(round(t * FPS))
        if 0 <= i < T:
            beat[i] = 1.0
    for t in m.get("downbeats", []):
        i = int(round(t * FPS))
        if 0 <= i < T:
            down[i] = 1.0
    sus[onset > 0] = 0
    return frame, onset, dead, beat, down, mask, rep, sus, tech, alt


FX_DIR = os.path.join(ROOT, "feats_fx")
FX_P = 0.0      # probability of training on an effect-pedal variant of the stem CQT (set by --fx-p)
MERT_DIR = os.path.join(ROOT, "feats_mert")
MERT_DIM = 0    # >0: load MERT features (set by --mert)


def load_mert(m, T):
    """MERT features (75 fps) resampled to the CQT frame rate -> (D, T) float16, or None."""
    f = os.path.join(MERT_DIR, os.path.basename(m["npz"])[:-4] + ".npz")
    if not os.path.exists(f):
        return None
    x = np.load(f)["mert"].astype(np.float32)                   # (T75, D)
    idx = np.clip(np.round(np.arange(T) * 75.0 / FPS).astype(int), 0, len(x) - 1)
    return x[idx].T.astype(np.float16)


def _norm16(x):
    x = x.astype(np.float32)
    return (x / (np.percentile(x, 99.5) + 1e-3)).astype(np.float16)


class SongCache:
    def __init__(self, metas, fx=False):
        self.items = []
        self.fx = []
        self.mert = []
        for m in metas:
            d = np.load(m["npz"])
            cqt = _norm16(d["cqt"])
            mel = d["mel"].astype(np.float32)
            mel /= (np.percentile(mel, 99.5) + 1e-3)
            T = cqt.shape[1]
            tg = make_targets(m, T)
            span = np.where(tg[5] > 0)[0]
            self.items.append((m, cqt, mel, tg, (span.min(), span.max()) if len(span) else (0, T)))
            self.mert.append(load_mert(m, T) if MERT_DIM else None)
            variants = []
            if fx:
                md5 = os.path.basename(m["npz"])[:-4]
                for k in range(4):
                    f = os.path.join(FX_DIR, f"{md5}_{k}.npz")
                    if os.path.exists(f):
                        c = np.load(f)["cqt"]
                        if c.shape == cqt.shape:
                            variants.append(_norm16(c))
            self.fx.append(variants)

    def sample(self, rng, shift_max=3, stretch=0.1):
        idx = rng.randrange(len(self.items))
        m, cqt, mel, (frame, onset, dead, beat, down, mask, rep, sus, tech, alt), (s0, s1) = self.items[idx]
        if self.fx[idx] and rng.random() < FX_P:
            cqt = self.fx[idx][rng.randrange(len(self.fx[idx]))]
        T = cqt.shape[1]
        r = rng.uniform(1 - stretch, 1 + stretch)            # source frames per output frame
        L = int(CROP * r)
        lo, hi = max(0, s0 - L // 2), max(max(0, s0 - L // 2) + 1, min(T - L, s1 - L // 2))
        a = rng.randrange(lo, hi) if hi > lo else 0
        # time-stretch by resampling frame indices (labels follow the same map)
        src = np.clip((a + np.arange(CROP) * r).astype(int), 0, T - 1)
        c, ml = cqt[:, src].astype(np.float32), mel[:, src]
        mt = None
        if MERT_DIM:
            mv = self.mert[idx]
            mt = mv[:, src].astype(np.float32) if mv is not None else np.zeros((MERT_DIM, CROP), np.float32)
        f, de, do, mk, su = frame[src].copy(), dead[src], down[src], mask[src], sus[src]
        al = alt[src].copy()
        # 1-frame events: keep them 1-frame after resampling
        def events(x):
            y = np.zeros(CROP, np.float32)
            ev = np.flatnonzero(x[a:min(T, a + L + 2)] > 0) + a
            pos = np.round((ev - a) / r).astype(int)
            pos = pos[(pos >= 0) & (pos < CROP)]
            y[pos] = 1.0
            return y
        on, be = events(onset), events(beat)
        do = events(down)
        de = events(dead)
        rp = events(rep)
        tc = np.stack([events(tech[:, k]) for k in range(tech.shape[1])], 1)
        k = rng.randint(-shift_max, shift_max)
        if MERT_DIM:
            # MERT features cannot be pitch-shifted: shift half of the crops and hide MERT on those
            k = k if rng.random() < 0.5 else 0
            if k or rng.random() < 0.1:
                mt = np.zeros_like(mt)
        if k:
            c = np.roll(c, k * BINS_PER_SEMI, axis=0)
            if k > 0:
                c[:k * BINS_PER_SEMI] = 0
            else:
                c[k * BINS_PER_SEMI:] = 0
            nz = f > 0
            f[nz] = f[nz] + k
            bad = nz & ((f < 1) | (f > PITCH_HI - PITCH_LO + 1))
            f[bad] = 0
            az = al > 0
            al[az] = al[az] + k
            al[az & ((al < 1) | (al > PITCH_HI - PITCH_LO + 1))] = 0
            al[bad] = 0
        gain = rng.uniform(0.7, 1.3)
        # spectral tilt (pickup / amp EQ) and additive noise (separation residue)
        tilt = np.linspace(-1, 1, c.shape[0])[:, None] * rng.uniform(-0.15, 0.15)
        c = np.clip(c * gain * (1 + tilt), 0, None)
        if rng.random() < 0.5:
            c = c + np.abs(np.random.default_rng(rng.randrange(1 << 30)).normal(0, rng.uniform(0.005, 0.03), c.shape)).astype(np.float32)
        out = (c.astype(np.float32), (ml * rng.uniform(0.8, 1.2)).astype(np.float32), f, on, de, be, do, mk, rp, su, tc, al)
        return out + (mt,) if MERT_DIM else out


def soften(x):
    """Widen 1-frame onset/beat targets to a [0.5, 1, 0.5] kernel (label jitter tolerance)."""
    k = torch.tensor([0.3, 1.0, 0.3], device=x.device).view(1, 1, 3)
    return torch.clamp(F.conv1d(x.unsqueeze(1), k, padding=1).squeeze(1), 0, 1)


REP_W, SUS_W = 1.0, 1.0
OCT_TOL = False   # octave-tolerant loss on octave-ambiguous label notes (set by --oct-tol)


def loss_fn(out, tg):
    frame_l, on_l, dead_l, beat_l, down_l = out[:5]
    f, on, de, be, do, mk, rp, su, tc, al = tg
    ce = F.cross_entropy(frame_l.transpose(1, 2), f, reduction="none")
    if (al > 0).any():
        # octave-ambiguous frames: either the written pitch or one octave lower counts as correct
        lp = F.log_softmax(frame_l, -1)
        both = torch.logsumexp(torch.stack([lp.gather(-1, f.unsqueeze(-1)).squeeze(-1),
                                            lp.gather(-1, al.clamp(min=0).unsqueeze(-1)).squeeze(-1)]), 0)
        ce = torch.where(al > 0, -both, ce)
    l_frame = (ce * mk).sum() / mk.sum().clamp(min=1)
    def bce(logit, y, pw, w=None):
        l = F.binary_cross_entropy_with_logits(logit, soften(y), pos_weight=torch.tensor(pw, device=y.device), reduction="none")
        w = mk if w is None else mk * w
        return (l * w).sum() / mk.sum().clamp(min=1)
    w_on = 1 + (REP_W - 1) * soften(rp) + (SUS_W - 1) * su
    l_on = bce(on_l, on, 6.0, w_on)
    l_dead = bce(dead_l, de, 4.0)
    l_beat = F.binary_cross_entropy_with_logits(beat_l, soften(be), pos_weight=torch.tensor(5.0, device=be.device))
    l_down = F.binary_cross_entropy_with_logits(down_l, soften(do), pos_weight=torch.tensor(10.0, device=do.device))
    total = l_frame + 1.5 * l_on + 0.3 * l_dead + 0.5 * l_beat + 0.5 * l_down
    l_tech = torch.zeros((), device=f.device)
    if len(out) > 5:
        tl = out[5]                                                   # B,T,K
        k = torch.tensor([0.3, 1.0, 0.3], device=tc.device).view(1, 1, 3)
        ts = torch.clamp(F.conv1d(tc.permute(0, 2, 1).reshape(-1, 1, tc.shape[1]), k, padding=1), 0, 1)
        ts = ts.reshape(tc.shape[0], tc.shape[2], tc.shape[1]).permute(0, 2, 1)
        lt = F.binary_cross_entropy_with_logits(tl, ts, pos_weight=torch.tensor(8.0, device=tc.device), reduction="none")
        l_tech = (lt.mean(-1) * mk).sum() / mk.sum().clamp(min=1)
        total = total + 0.5 * l_tech
    return total, (l_frame.item(), l_on.item(), l_beat.item(), l_down.item(), l_tech.item())


@torch.no_grad()
def infer_full(model, cqt, mel, dev, chunk=3000, ctx=200, mert=None):
    """Overlapping-chunk inference over a whole song. Returns numpy posteriors."""
    T = cqt.shape[1]
    n_tech = getattr(model, "tech", 0)
    outs = [np.zeros((T, 49), np.float32)] + [np.zeros(T, np.float32) for _ in range(4)]
    if n_tech:
        outs.append(np.zeros((T, n_tech), np.float32))
    s = 0
    while s < T:
        a, b = max(0, s - ctx), min(T, s + chunk + ctx)
        c = torch.from_numpy(cqt[:, a:b]).unsqueeze(0).to(dev)
        m = torch.from_numpy(mel[:, a:b]).unsqueeze(0).to(dev)
        mt = None
        if getattr(model, "mert", 0) and mert is not None:
            mt = torch.from_numpy(np.ascontiguousarray(mert[:, a:b], dtype=np.float32)).unsqueeze(0).to(dev)
        with torch.autocast("cuda", dtype=torch.float16):
            o = model(c, m, mt) if getattr(model, "mert", 0) else model(c, m)
        e = min(T, s + chunk)
        outs[0][s:e] = torch.softmax(o[0][0].float(), -1).cpu().numpy()[s - a:e - a]
        for i in range(1, 5):
            outs[i][s:e] = torch.sigmoid(o[i][0].float()).cpu().numpy()[s - a:e - a]
        if n_tech:
            outs[5][s:e] = torch.sigmoid(o[5][0].float()).cpu().numpy()[s - a:e - a]
        s = e
    return outs


def evaluate(model, cache, dev, max_songs=40):
    model.eval()
    res = []
    for i, (m, cqt, mel, tg, span) in enumerate(cache.items[:max_songs]):
        mert = cache.mert[i] if cache.mert else None
        fr, on, de, be, do = infer_full(model, cqt.astype(np.float32), mel, dev, mert=mert)[:5]
        est = decode_notes(fr, on, de)
        ref = [n for n in m["notes"] if not n.get("grace") and not n.get("dead")]
        est = [n for n in est if not n["dead"]]
        t0, t1 = ref[0]["time"] - 1, ref[-1]["end"] + 1
        est = [n for n in est if t0 <= n["time"] <= t1]
        r = note_metrics(ref, est)
        r["name"] = os.path.basename(os.path.dirname(m["gp"]))[:30]
        res.append(r)
    model.train()
    agg = {k: float(np.mean([r[k] for r in res])) for k in ("P", "R", "F1", "onset_F1", "pitch_acc_on_matched")}
    return agg, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--steps", type=int, default=400)
    ap.add_argument("--bs", type=int, default=12)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--out", default=CKPT)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--rep-w", type=float, default=1.0, help="onset loss weight on same-pitch re-attacks")
    ap.add_argument("--sus-w", type=float, default=1.0, help="onset loss weight inside sustained notes")
    ap.add_argument("--tech", action="store_true", help="technique head (slides, hammer-ons, slap/pop)")
    ap.add_argument("--fx-p", type=float, default=0.0, help="probability of an effect-pedal CQT variant")
    ap.add_argument("--oct-tol", action="store_true", help="octave-tolerant loss on ambiguous labels")
    ap.add_argument("--fold", type=int, default=-1, help="train on one half of the training songs (0/1)")
    ap.add_argument("--labels", default="", help="directory of re-aligned label jsons")
    ap.add_argument("--mert", type=int, default=0, help="MERT feature dim (384) to add the foundation-model branch")
    ap.add_argument("--init", default="", help="start from these weights (fine-tuning)")
    ap.add_argument("--extra", default="", help="extra songs in feats format (bassnet/ext_build.py: teacher labels)")
    ap.add_argument("--extra-p", type=float, default=0.3, help="share of crops drawn from the extra songs")
    args = ap.parse_args()

    dev = "cuda"
    global REP_W, SUS_W, FX_P, MERT_DIM, OCT_TOL, LABEL_DIR
    REP_W, SUS_W, FX_P, MERT_DIM, OCT_TOL = args.rep_w, args.sus_w, args.fx_p, args.mert, args.oct_tol
    LABEL_DIR = args.labels
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    metas = load_metas()
    tr, te = split_songs(metas)
    # validation (model selection) is carved out of the training songs; the test split is only used by eval_e2e
    keys = sorted({song_key(m["gp"]) for m in tr})
    random.Random(11).shuffle(keys)
    val_keys = frozen_keys("val") if os.path.exists(SPLIT_FILE) else set(keys[:max(3, len(keys) // 12)])
    va = [m for m in tr if song_key(m["gp"]) in val_keys]
    tr = [m for m in tr if song_key(m["gp"]) not in val_keys]
    if args.fold >= 0:
        tr = [m for m in tr if fold_of(m["gp"]) == args.fold]
    print(f"train {len(tr)} songs, val {len(va)} songs, (test {len(te)} songs held out)", flush=True)
    trc, tec = SongCache(tr, fx=FX_P > 0), SongCache(va)
    print(f"fx variants: {sum(len(v) for v in trc.fx)} for {sum(1 for v in trc.fx if v)} songs, p={FX_P}", flush=True)
    exc = None
    if args.extra:
        ext = []
        for f in sorted(glob.glob(os.path.join(args.extra, "*.json"))):
            m = json.load(open(f, encoding="utf8"))
            m["npz"] = f.replace(".json", ".npz")
            if m.get("ext_split") == "train" and os.path.exists(m["npz"]):
                ext.append(m)
        exc = SongCache(ext)
        print(f"extra songs {len(ext)} (p={args.extra_p})", flush=True)
    arch = {"stem_branch": True, "tech": N_TECH if args.tech else 0, "mert": MERT_DIM}
    init = None
    if args.init:
        init = torch.load(args.init, map_location=dev)
        arch = dict(init.get("arch", arch))
    model = BassNet(**arch).to(dev)
    if init is not None:
        model.load_state_dict(init["model"])
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=args.epochs * args.steps, pct_start=0.08)
    scaler = torch.amp.GradScaler()
    best = -1
    if args.resume and os.path.exists(args.out):
        sd = torch.load(args.out, map_location=dev)
        model.load_state_dict(sd["model"])
        best = sd.get("f1", -1)
    rng = random.Random(args.seed)
    for ep in range(args.epochs):
        t0 = time.time()
        logs = []
        for _ in range(args.steps):
            batch = [(exc if exc is not None and rng.random() < args.extra_p else trc).sample(rng) for _ in range(args.bs)]
            tens = [torch.from_numpy(np.stack(x)).to(dev) for x in zip(*batch)]
            c, ml, *tg = tens
            mt = tg.pop() if MERT_DIM else None
            with torch.autocast("cuda", dtype=torch.float16):
                out = model(c, ml, mt) if MERT_DIM else model(c, ml)
            out = [o.float() for o in out]
            loss, parts = loss_fn(out, tg)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 3.0)
            scaler.step(opt)
            scaler.update()
            sched.step()
            logs.append(parts)
        lg = np.mean(logs, axis=0)
        agg, _ = evaluate(model, tec, dev)
        flag = ""
        if agg["F1"] > best:
            best = agg["F1"]
            torch.save({"model": model.state_dict(), "f1": best, "epoch": ep, "arch": arch,
                        "recipe": {"seed": args.seed, "rep_w": REP_W, "sus_w": SUS_W, "fx_p": FX_P, "tech": args.tech,
                                   "fold": args.fold, "labels": args.labels, "init": args.init,
                                   "extra": args.extra, "extra_p": args.extra_p}},
                       args.out)
            flag = "*"
        print(f"ep {ep:3d} loss f={lg[0]:.3f} on={lg[1]:.3f} beat={lg[2]:.3f} down={lg[3]:.3f} tech={lg[4]:.3f} | "
              f"val F1={agg['F1']:.4f} onF1={agg['onset_F1']:.4f} pitch|on={agg['pitch_acc_on_matched']:.4f} "
              f"P={agg['P']:.3f} R={agg['R']:.3f} {time.time() - t0:.0f}s {flag}", flush=True)


if __name__ == "__main__":
    main()
