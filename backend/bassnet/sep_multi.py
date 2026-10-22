"""Separate the same song several times, differently, and see whether the passes together know more than one pass.

BS-Roformer-SW is deterministic: running it twice on the same input gives the same bass, so every pass has to see
the song differently:
  base          the production chunking (8 s, 1 s cross-fade)
  shiftK        mix delayed by K x 2.1 s before chunking (other chunk boundaries), output shifted back
  gainD         mix scaled by D dB, output scaled back
  pol           polarity flipped in and out
  swap          left / right swapped in and out
  mcdK          dropout left on (attn / ff 0.1 in the config), seed K: Monte Carlo dropout
Separator errors are mostly the model's bias (kick on a bass note, low guitar string), which every pass shares; only
the uncorrelated part averages out. So the first numbers to read are the diagnostics, not the fusion:
  oracle_R      share of reference notes that at least one pass gets right (upper bound for any fusion / selection)
  miss_jacc     mean pairwise Jaccard of the missed reference notes (1 = every pass misses the same notes)
Fusion:
  wave_mean     mean of the pass waveforms -> BassNet
  post_mean     BassNet on every pass, posteriors averaged, decoded once
Confidence (label-free): for each post_mean note, the share of passes that wrote the same note (pitch, +-50 ms);
AUC of that agreement, of the note's own min(conf, onset peak), and of their product, for "this note is right".

Sets: lib  = our frozen val songs (original mix, labels_v2)            -- the yardstick that counts
      musdb = MUSDB18-HQ test (restore cache): reference = BassNet on the true bass stem (SW may have seen MUSDB)

python -m bassnet.sep_multi --set lib --songs 10 --sec 60 --passes base shift1 shift2 shift3 pol swap mcd1 mcd2 mcd3
-> D:\\BassData\\sep_multi\\<set>_results.json
"""
import argparse
import contextlib
import io
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SR = 44100
OUT = os.path.join("D:" + os.sep, "BassData", "sep_multi")
TOL = 0.05


# ------------------------------------------------------------------ passes
def run_pass(model, mix, name, dev="cuda"):
    """(2, L) mix at 44.1 kHz -> (2, L) bass estimate for one pass."""
    import torch
    from bassnet.restore_train import separate_bass
    L = mix.shape[1]
    m = re.fullmatch(r"([a-z]+)(-?\d*)", name)
    kind, k = m.group(1), int(m.group(2) or 0)
    with contextlib.redirect_stdout(io.StringIO()):
        if kind == "base":
            return separate_bass(model, mix, dev)
        if kind == "shift":
            d = int(k * 2.1 * SR)
            x = np.pad(mix, ((0, 0), (d, 0)))
            return separate_bass(model, x, dev)[:, d:d + L]
        if kind == "gain":
            g = 10 ** (k / 20)
            return separate_bass(model, mix * g, dev) / g
        if kind == "pol":
            return -separate_bass(model, -mix, dev)
        if kind == "swap":
            return separate_bass(model, mix[::-1].copy(), dev)[::-1]
        if kind == "mcd":
            torch.manual_seed(k)
            model.train()                     # RMSNorm only, no batch norm: train() just switches dropout on
            try:
                return separate_bass(model, mix, dev)
            finally:
                model.eval()
    raise ValueError(name)


# ------------------------------------------------------------------ scoring
def match(ref, est, tol=TOL):
    """Greedy onset matching as decode.note_metrics. -> (ref note right?, est note right?) boolean arrays."""
    ri = sorted(range(len(ref)), key=lambda i: ref[i]["time"])
    rt = np.array([ref[i]["time"] for i in ri])
    used = np.zeros(len(ri), bool)
    ref_ok, est_ok = np.zeros(len(ref), bool), np.zeros(len(est), bool)
    j0 = 0
    for ei in sorted(range(len(est)), key=lambda i: est[i]["time"]):
        e = est[ei]
        while j0 < len(rt) and rt[j0] < e["time"] - tol:
            j0 += 1
        best, bd, j = -1, 1e9, j0
        while j < len(rt) and rt[j] <= e["time"] + tol:
            if not used[j]:
                d = abs(rt[j] - e["time"]) - (0.02 if ref[ri[j]]["midi"] == e["midi"] else 0)
                if d < bd:
                    best, bd = j, d
            j += 1
        if best >= 0:
            used[best] = True
            if ref[ri[best]]["midi"] == e["midi"]:
                ref_ok[ri[best]] = est_ok[ei] = True
    return ref_ok, est_ok


def f1(ref, est):
    r, _ = match(ref, est)
    return 2 * float(r.sum()) / max(1, len(ref) + len(est))


def auc(score, label):
    """P(score of a right note > score of a wrong note), ties count half."""
    score, label = np.asarray(score, float), np.asarray(label, bool)
    pos, neg = score[label], score[~label]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    allv = np.concatenate([pos, neg])[order]
    ranks = np.empty(len(allv))
    i = 0
    while i < len(allv):                      # average ranks over ties
        j = i
        while j + 1 < len(allv) and allv[j + 1] == allv[i]:
            j += 1
        ranks[i:j + 1] = (i + j) / 2 + 1
        i = j + 1
    r = np.empty(len(allv))
    r[order] = ranks
    return float((r[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def agreement(notes, passes):
    """Share of passes holding the same note (pitch, onset within TOL)."""
    out = []
    for n in notes:
        k = 0
        for p in passes:
            k += any(q["midi"] == n["midi"] and abs(q["time"] - n["time"]) <= TOL for q in p)
        out.append(k / max(1, len(passes)))
    return np.array(out)


def score_song(ref, pass_notes, fused):
    """Diagnostics and fusion scores for one song."""
    names = list(pass_notes)
    hits = {p: match(ref, pass_notes[p])[0] for p in names}
    misses = {p: ~h for p, h in hits.items()}
    jac = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = misses[names[i]], misses[names[j]]
            u = (a | b).sum()
            jac.append(float((a & b).sum() / u) if u else 1.0)
    any_hit = np.any(np.stack([hits[p] for p in names]), 0) if names else np.zeros(len(ref), bool)
    r = {"n_ref": len(ref),
         "pass_f1": {p: f1(ref, pass_notes[p]) for p in names},
         "pass_R": {p: float(hits[p].mean()) if len(ref) else 0.0 for p in names},
         "oracle_R": float(any_hit.mean()) if len(ref) else 0.0,
         "miss_jacc": float(np.mean(jac)) if jac else None,
         "fused_f1": {k: f1(ref, v) for k, v in fused.items()}}
    if "post_mean" in fused:
        notes = fused["post_mean"]
        _, ok = match(ref, notes)
        ag = agreement(notes, [pass_notes[p] for p in names])
        own = np.array([min(n["conf"], n["on_peak"]) for n in notes])
        r["conf"] = {"agree": ag.tolist(), "own": own.tolist(), "ok": ok.tolist()}
    return r


# ------------------------------------------------------------------ songs
def lib_items(limit, sec):
    import soundfile as sf
    from bassnet.restore_train import libval_songs
    for md5, wav, notes in libval_songs()[:limit]:
        y, _ = sf.read(wav, dtype="float32", always_2d=True)
        i0 = min(len(y) // 3, max(0, len(y) - int(sec * SR)))
        mix = y[i0:i0 + int(sec * SR)].T.copy()
        t0, t1 = i0 / SR, i0 / SR + mix.shape[1] / SR
        ref = [dict(n, time=n["time"] - t0) for n in notes if t0 <= n["time"] < t1]     # as validate_lib
        yield md5, mix, ref


def musdb_items(limit, sec):
    import librosa
    from bassnet.restore_train import CACHE
    from bassnet.sep_real_eval import transcribe
    meta = [m for m in json.load(open(os.path.join(CACHE, "meta.json"))) if m["split"] == "val"][:limit]
    for m in meta:
        b = np.load(os.path.join(CACHE, m["id"] + "_bass.npy"), mmap_mode="r")
        r = np.load(os.path.join(CACHE, m["id"] + "_rest.npy"), mmap_mode="r")
        i0 = min(len(b) // 3, max(0, len(b) - int(sec * SR)))
        yb = np.asarray(b[i0:i0 + int(sec * SR)], np.float32)
        mix = (yb + np.asarray(r[i0:i0 + int(sec * SR)], np.float32)).T.copy()
        t22 = librosa.resample(yb.mean(1), orig_sr=SR, target_sr=22050)
        m22 = librosa.resample(mix.mean(0), orig_sr=SR, target_sr=22050)
        ref = [dict(n, grace=False) for n in transcribe(t22, m22)]                     # as restore_train.validate
        yield m["id"], mix, ref


# ------------------------------------------------------------------ main
def bass_posteriors(y22, m22):
    from bassnet.dataset import compute_cqt, compute_mel
    from bassnet.pipeline import posteriors
    cqt = compute_cqt(y22)
    mel = compute_mel(m22)
    T = cqt.shape[1]
    mel = mel[:, :T] if mel.shape[1] >= T else np.pad(mel, ((0, 0), (0, T - mel.shape[1])))
    return posteriors(cqt, mel)[:3]


def summarize(res):
    songs = list(res.values())
    s = {"songs": len(songs)}
    passes = sorted({p for r in songs for p in r["pass_f1"]})
    s["pass_f1"] = {p: float(np.mean([r["pass_f1"][p] for r in songs if p in r["pass_f1"]])) for p in passes}
    s["oracle_R"] = float(np.mean([r["oracle_R"] for r in songs]))
    s["best_pass_R"] = float(np.mean([max(r["pass_R"].values()) for r in songs]))
    s["miss_jacc"] = float(np.mean([r["miss_jacc"] for r in songs if r["miss_jacc"] is not None]))
    fk = sorted({k for r in songs for k in r["fused_f1"]})
    s["fused_f1"] = {k: float(np.mean([r["fused_f1"][k] for r in songs if k in r["fused_f1"]])) for k in fk}
    c = [r["conf"] for r in songs if "conf" in r]
    if c:
        ag = np.concatenate([x["agree"] for x in c])
        own = np.concatenate([x["own"] for x in c])
        ok = np.concatenate([x["ok"] for x in c]).astype(bool)
        s["auc"] = {"agree": auc(ag, ok), "own": auc(own, ok), "agree_x_own": auc(ag * own, ok)}
        s["wrong_share_agree_lt_half"] = float((~ok[ag < 0.5]).mean()) if (ag < 0.5).any() else None
        s["notes_agree_lt_half"] = float((ag < 0.5).mean())
    return s


def main():
    import librosa
    from bassnet.restore_train import load_sw
    from bassnet.decode import decode_notes
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["lib", "musdb"], default="lib")
    ap.add_argument("--songs", type=int, default=10)
    ap.add_argument("--sec", type=float, default=60.0)
    ap.add_argument("--passes", nargs="+", default=["base", "shift1", "shift2", "shift3", "pol", "swap",
                                                    "mcd1", "mcd2", "mcd3"])
    a = ap.parse_args()
    os.makedirs(os.path.join(OUT, "stems"), exist_ok=True)
    res_f = os.path.join(OUT, f"{a.set}_results.json")
    res = json.load(open(res_f, encoding="utf8")) if os.path.exists(res_f) else {}
    model, _ = load_sw(False)
    model.eval()
    items = lib_items(a.songs, a.sec) if a.set == "lib" else musdb_items(a.songs, a.sec)
    for sid, mix, ref in items:
        key = f"{sid}|{a.sec:g}|{'+'.join(a.passes)}"
        if key in res:
            continue
        m22 = librosa.resample(mix.mean(0), orig_sr=SR, target_sr=22050)
        stems, posts, notes = {}, {}, {}
        for p in a.passes:
            f = os.path.join(OUT, "stems", f"{a.set}_{sid}_{a.sec:g}_{p}.npy")
            if not os.path.exists(f):
                est = run_pass(model, mix, p)
                np.save(f, librosa.resample(est.mean(0).astype(np.float32), orig_sr=SR, target_sr=22050))
            stems[p] = np.load(f)
            posts[p] = bass_posteriors(stems[p], m22)
            notes[p] = decode_notes(*posts[p])
        n = min(len(v) for v in stems.values())
        fused = {"wave_mean": decode_notes(*bass_posteriors(np.mean([v[:n] for v in stems.values()], 0), m22))}
        T = min(len(v[0]) for v in posts.values())
        fused["post_mean"] = decode_notes(*[np.mean([v[h][:T] for v in posts.values()], 0) for h in range(3)])
        res[key] = score_song(ref, notes, fused)
        r = res[key]
        print(f"{sid[:24]:24s} base {r['pass_f1'].get(a.passes[0], 0):.3f} oracle_R {r['oracle_R']:.3f} "
              f"miss_jacc {r['miss_jacc'] or 0:.2f} wave {r['fused_f1']['wave_mean']:.3f} "
              f"post {r['fused_f1']['post_mean']:.3f}", flush=True)
        json.dump(res, open(res_f, "w", encoding="utf8"))
    tag = f"|{a.sec:g}|{'+'.join(a.passes)}"
    s = summarize({k: v for k, v in res.items() if k.endswith(tag)})
    print(json.dumps(s, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
