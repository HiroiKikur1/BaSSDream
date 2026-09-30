"""Fine-tune allin1 on the purchased tabs' structure (architecture v2, Phase 2).

Zero-shot allin1 on our 6-stem separation tracks J-rock at half tempo on 24 of 39 songs and loses to our own beat
HMM (downbeat F1 0.66 vs 0.84, layer1_eval.py), so it is fine-tuned on what the tabs say:
  beats     every quarter (dotted quarter in compound meters) of every played bar
  downbeats bar starts
  sections  rehearsal-mark starts, function labels mapped to allin1's Harmonix vocabulary
            (intro, A/B -> verse, C -> chorus, D -> bridge, solo/interlude -> inst, outro)
placed on the audio by the same maps the evaluation uses (fixed_q_map for val/test, labels_v2 for training songs).
Only frames between the tab's first and last bar line are scored (mask). Training songs only; val/test untouched.

python -m bassnet.allin1_finetune targets                       (ML python: tab structure -> json)
cache/venv_allin1/Scripts/python.exe -m bassnet.allin1_finetune train [--fold 0] [--epochs 40]
cache/venv_allin1/Scripts/python.exe -m bassnet.allin1_finetune infer --ckpt ... --out cache/bassnet/allin1_ft
"""
import argparse
import json
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
TGT = os.path.join(ROOT, "l1_targets")
SPEC = os.path.join("D:" + os.sep, "BassData", "allin1_spec")
STEMS6 = os.path.join("D:" + os.sep, "BassData", "stems6")
LABELS = ['start', 'end', 'intro', 'outro', 'break', 'bridge', 'inst', 'solo', 'verse', 'chorus']
CLS2LAB = {"intro": "intro", "A": "verse", "B": "verse", "C": "chorus", "D": "bridge", "inter": "inst",
           "outro": "outro"}
FPS = 100


# ------------------------------------------------------------------ targets (base ML python)
def targets():
    from bassnet.train import load_metas, song_key, frozen_keys
    from bassnet.gpif_parser import parse_gp
    from bassnet.notation_eval import fixed_q_map, label_q_map
    from bassnet.section_model import gp_sections
    val, test = frozen_keys("val"), frozen_keys("test")
    os.makedirs(TGT, exist_ok=True)
    n = 0
    for m in load_metas():
        md5 = os.path.basename(m["npz"])[:-4]
        out = os.path.join(TGT, md5 + ".json")
        if os.path.exists(out):
            continue
        k = song_key(m["gp"])
        split = "val" if k in val else "test" if k in test else "train"
        try:
            info = parse_gp(m["gp"])
            qm = (fixed_q_map(m, info) if split != "train" else None) or label_q_map(m)
            if qm is None:
                continue
            beats, downs = [], []
            for b, oc, q0, ql in info.bars:
                num, den = info.time_sigs[b] if b < len(info.time_sigs) else (4, 4)
                step = 1.5 if (den == 8 and num % 3 == 0) else 1.0
                q, qe = float(q0), float(q0) + float(ql)
                downs.append(qm(q))
                while q < qe - 1e-6:
                    beats.append(qm(q))
                    q += step
            marks = gp_sections(m["gp"])
            secs = [(qm(float(q0)), CLS2LAB[marks[b]]) for b, oc, q0, ql in info.bars if oc == 0 and b in marks]
            end = qm(float(info.bars[-1][2] + info.bars[-1][3]))
            json.dump({"split": split, "gp": m["gp"], "beats": beats, "downbeats": downs, "sections": secs,
                       "region": [downs[0], end]}, open(out, "w", encoding="utf8"), ensure_ascii=False)
            n += 1
        except Exception as e:
            print("skip", md5, e)
    print("targets written", n)


# ------------------------------------------------------------------ training (venv)
def spec_of(md5):
    p = os.path.join(SPEC, md5 + ".npy")
    if not os.path.exists(p):
        from bassnet.allin1_run import load4, spectrogram
        os.makedirs(SPEC, exist_ok=True)
        np.save(p, spectrogram(load4(os.path.join(STEMS6, md5))).astype(np.float16))
    return np.load(p).astype(np.float32)


def frame_targets(t, T):
    from scipy.ndimage import maximum_filter1d

    def ev(times):
        x = np.zeros(T, np.float32)
        idx = np.round(np.asarray(times) * FPS).astype(int)
        x[idx[(idx >= 0) & (idx < T)]] = 1.0
        return x

    def widen(e, k):
        w = e.copy()
        for _ in range(k):
            w = maximum_filter1d(w, size=3)
            nb = np.flatnonzero((e != 1) & (w > 0))
            w[nb] *= 0.5
        return w
    beat, down = ev(t["beats"]), ev(t["downbeats"])
    sec = ev([s for s, _ in t["sections"]]) if len(t["sections"]) >= 2 else np.zeros(T, np.float32)
    func = np.full(T, -100, np.int64)                   # ignore_index outside labelled sections
    ss = t["sections"]
    for i, (s, lab) in enumerate(ss):
        e = ss[i + 1][0] if i + 1 < len(ss) else t["region"][1]
        func[max(0, int(s * FPS)):min(T, int(e * FPS))] = LABELS.index(lab)
    mask = np.zeros(T, np.float32)
    lo, hi = int(max(0, t["region"][0] - 0.5) * FPS), int(min(T / FPS, t["region"][1] + 0.5) * FPS)
    mask[lo:hi] = 1.0
    has_sec = float(len(ss) >= 2)
    return widen(beat, 1), widen(down, 1), widen(sec, 2), func, mask, has_sec


def load_split(split):
    items = []
    for f in sorted(os.listdir(TGT)):
        t = json.load(open(os.path.join(TGT, f), encoding="utf8"))
        md5 = f[:-5]
        if t["split"] == split and os.path.exists(os.path.join(STEMS6, md5, "meta.json")):
            items.append((md5, t))
    return items


def train(fold=0, epochs=40, seg=60.0, lr=2e-4, dev="cuda", out=None):
    import torch
    import torch.nn.functional as F
    from allin1.models import load_pretrained_model
    model = load_pretrained_model(f"harmonix-fold{fold}", cache_dir=os.path.join("E:" + os.sep, "BassStation", "cache",
                                                                                  "models", "allin1"), device=dev)
    tr, va = load_split("train"), load_split("val")
    print("train songs", len(tr), "val songs", len(va), flush=True)
    data = {}
    for md5, t in tr + va:
        S = spec_of(md5)
        data[md5] = (S, frame_targets(t, S.shape[1]))
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=2.5e-4)
    out = out or os.path.join(ROOT, "staging", f"allin1_ft_fold{fold}.pt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    best = -1.0
    L = int(seg * FPS)

    def loss_of(S, tg, a, b):
        x = torch.from_numpy(S[:, a:b]).unsqueeze(0).to(dev)
        o = model(x)
        wb, wd, ws, fn, mk, hs = [torch.from_numpy(np.asarray(v[a:b]) if np.ndim(v) else np.asarray(v)).to(dev)
                                  for v in tg]
        mk = mk.unsqueeze(0)
        lb = (F.binary_cross_entropy_with_logits(o.logits_beat, wb.unsqueeze(0), reduction="none") * mk).mean()
        ld = (F.binary_cross_entropy_with_logits(o.logits_downbeat, wd.unsqueeze(0), reduction="none") * mk).mean()
        ls = (F.binary_cross_entropy_with_logits(o.logits_section, ws.unsqueeze(0), reduction="none") * mk).mean() * hs
        lf = F.cross_entropy(o.logits_function, fn.unsqueeze(0), ignore_index=-100) if (fn >= 0).any() else 0.0 * lb
        return lb + ld + ls + lf, o

    z = evaluate(model, va, data, dev)
    print(f"zero-shot val downbeat F1 {z['down']:.3f} beat {z['beat']:.3f} sections {z['sec']:.3f}", flush=True)
    for ep in range(epochs):
        model.train()
        random.shuffle(tr)
        tot = 0.0
        for md5, _ in tr:
            S, tg = data[md5]
            T = S.shape[1]
            a = random.randint(0, max(0, T - L))
            loss, _ = loss_of(S, tg, a, min(T, a + L))
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
            opt.step()
            tot += float(loss)
        score = evaluate(model, va, data, dev)
        print(f"epoch {ep} loss {tot / len(tr):.4f} val downbeat F1 {score['down']:.3f} beat {score['beat']:.3f} "
              f"sections {score['sec']:.3f}", flush=True)
        if score["down"] > best:
            best = score["down"]
            torch.save({"state_dict": model.state_dict(), "config": dict(model.cfg) if hasattr(model.cfg, "keys")
                        else None, "fold": fold, "epoch": ep, "val": score}, out)
    print("best val downbeat F1", best, "->", out)


def predict(model, S, dev):
    import torch
    from allin1.postprocessing import postprocess_metrical_structure, postprocess_functional_structure
    with torch.no_grad():
        o = model(torch.from_numpy(S).unsqueeze(0).to(dev))
        met = postprocess_metrical_structure(o, model.cfg)
        seg = postprocess_functional_structure(o, model.cfg)
    return met, seg, o


def evaluate(model, items, data, dev):
    from bassnet.song_eval import f1_events
    model.eval()
    res = {"beat": [], "down": [], "sec": []}
    for md5, t in items:
        S, _ = data[md5]
        met, seg, _ = predict(model, S, dev)
        lo, hi = t["region"]
        cut = lambda xs: [(x, None) for x in xs if lo - 0.5 <= x <= hi + 0.5]
        res["beat"].append(f1_events(cut(t["beats"]), cut(met["beats"]), 0.07)[0])
        res["down"].append(f1_events(cut(t["downbeats"]), cut(met["downbeats"]), 0.07)[0])
        if len(t["sections"]) >= 2:
            bl = float(np.median(np.diff(t["downbeats"])))
            res["sec"].append(f1_events([(s, None) for s, _ in t["sections"]], [(s.start, None) for s in seg],
                                        0.6 * bl)[0])
    return {k: float(np.mean(v)) if v else 0.0 for k, v in res.items()}


def infer(ckpt, out_dir, dev="cuda"):
    import torch
    from allin1.models import load_pretrained_model
    from allin1.helpers import compute_activations
    c = torch.load(ckpt, map_location=dev, weights_only=False)   # our own checkpoint (holds the omegaconf config)
    model = load_pretrained_model(f"harmonix-fold{c['fold']}", cache_dir=os.path.join("E:" + os.sep, "BassStation",
                                                                                     "cache", "models", "allin1"),
                                  device=dev)
    model.load_state_dict(c["state_dict"])
    model.eval()
    os.makedirs(out_dir, exist_ok=True)
    for md5 in sorted(os.listdir(STEMS6)):
        if not os.path.exists(os.path.join(STEMS6, md5, "meta.json")) or os.path.exists(
                os.path.join(out_dir, md5 + ".json")):
            continue
        met, seg, o = predict(model, spec_of(md5), dev)
        json.dump({"beats": met["beats"], "downbeats": met["downbeats"], "beat_positions": met["beat_positions"],
                   "segments": [{"start": s.start, "end": s.end, "label": s.label} for s in seg]},
                  open(os.path.join(out_dir, md5 + ".json"), "w"))
        act = compute_activations(o)
        np.savez_compressed(os.path.join(out_dir, md5 + ".npz"), **{k: np.asarray(v, np.float16) for k, v in act.items()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["targets", "train", "infer"])
    ap.add_argument("--fold", type=int, default=0)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--ckpt", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.cmd == "targets":
        targets()
    elif a.cmd == "train":
        train(a.fold, a.epochs, lr=a.lr, out=a.out or None)
    else:
        infer(a.ckpt, a.out or os.path.join(ROOT, "allin1_ft"))


if __name__ == "__main__":
    main()
