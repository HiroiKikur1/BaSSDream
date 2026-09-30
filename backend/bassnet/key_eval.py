"""Key signature of a recording, checked against the purchased tabs (Layer 1, song understanding).

Ground truth: the <Key> of every master bar (321 tabs; 47 change key inside the song), mapped to the recording's
timeline through the playback order. The written signature is what matters for engraving (relative major/minor
share it, and tabbers often leave Mode = Major), so the main metric is the signature modulo enharmonic equivalence
(7 sharps == 5 flats).

python -m bassnet.key_eval feats       # chroma of the full mix (and of the 6 stems when present), cached
python -m bassnet.key_eval eval        # compare detectors
"""
import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys
import zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.build_stems import FFMPEG, build_index  # noqa: E402

ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
FEAT = os.path.join(ROOT, "keyfeat")
STEMS6 = os.path.join("D:" + os.sep, "BassData", "stems6")
CHORDS = os.path.join(ROOT, "chords")
SR, HOP = 22050, 4096

# key profiles (index 0 = tonic)
KS_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
# Albrecht & Shanahan 2013 (pop/rock corpus)
AS_MAJ = np.array([0.238, 0.006, 0.111, 0.006, 0.137, 0.094, 0.016, 0.214, 0.009, 0.080, 0.008, 0.081])
AS_MIN = np.array([0.220, 0.006, 0.104, 0.123, 0.019, 0.103, 0.012, 0.214, 0.062, 0.022, 0.061, 0.052])
# signature-level template: the 7 diatonic pitch classes of the major scale on C (tonic-agnostic)
DIATONIC = np.array([1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1], float)


def sig_of(tonic: int, mode: str) -> int:
    """Key -> accidental count in -5..6 (major tonic t has t*7 fifths; minor = relative major)."""
    t = tonic if mode == "Major" else (tonic + 3) % 12
    s = (t * 7) % 12
    return s - 12 if s > 6 else s


def tonic_of(sig: int, mode: str) -> int:
    t = (sig * 7) % 12
    return t if mode == "Major" else (t - 3) % 12


def sig_eq(a: int, b: int) -> bool:
    return (a - b) % 12 == 0


def gt_keys(gp: str):
    """-> (majority signature, mode, [(played-bar start seconds, signature)]) from the tab."""
    from bassnet.gpif_parser import parse_gp, q_to_sec
    info = parse_gp(gp)
    t = zipfile.ZipFile(gp).read("Content/score.gpif").decode("utf8", "replace")
    mb = re.findall(r"<MasterBar>(.*?)</MasterBar>", t, re.S)
    keys = []
    for x in mb:
        m = re.search(r"<AccidentalCount>(-?\d+)</AccidentalCount>\s*<Mode>(\w+)</Mode>", x)
        keys.append((int(m.group(1)), m.group(2)) if m else (keys[-1] if keys else (0, "Major")))
    segs = []
    votes = collections.Counter()
    for b, oc, q0, ql in info.bars:
        k = keys[b] if b < len(keys) else keys[-1]
        votes[(k[0] % 12, k[1])] += float(ql)
        segs.append((q_to_sec(info.anchors, float(q0)) if info.anchors else 0.0, k[0]))
    (s, mode), _ = votes.most_common(1)[0]
    return (s - 12 if s > 6 else s), mode, segs


def _chroma(wav):
    import librosa
    y, _ = librosa.load(wav, sr=SR, mono=True)
    return librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP, n_chroma=12, bins_per_octave=36).astype(np.float16)


def _feat_one(args):
    try:
        _feat_one_(*args)
    except Exception as e:
        print("feat fail", args, e, flush=True)


def _feat_one_(gp, md5):
    out = os.path.join(FEAT, md5 + ".npz")
    if os.path.exists(out):
        return
    from bassnet.gpif_parser import parse_gp
    info = parse_gp(gp)
    tmp = os.path.join(FEAT, "_tmp_" + md5)
    ext = os.path.splitext(info.audio_asset)[1]
    src = tmp + (ext if re.fullmatch(r"\.\w{2,4}", ext) else ".bin")
    with zipfile.ZipFile(gp) as z:
        open(src, "wb").write(z.read(info.audio_asset))
    wav = tmp + ".wav"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", src, "-ar", str(SR), "-ac", "1", wav])
    d = {"mix": _chroma(wav)}
    for f in (src, wav):
        try:
            os.remove(f)
        except OSError:
            pass
    np.savez_compressed(out, **d)


def _stem_feat_one(md5):
    """Chroma of the harmonic stems (guitar + piano + other) and of the bass stem, from stems6."""
    out = os.path.join(FEAT, md5 + "_st.npz")
    d = os.path.join(STEMS6, md5)
    if os.path.exists(out) or not os.path.exists(os.path.join(d, "meta.json")):
        return
    import librosa
    ys = {}
    for s in ("guitar", "piano", "other", "bass", "vocals"):
        ys[s], _ = librosa.load(os.path.join(d, s + ".flac"), sr=SR, mono=True)
    n = min(len(v) for v in ys.values())
    harm = ys["guitar"][:n] + ys["piano"][:n] + ys["other"][:n]
    res = {}
    for name, y in (("harm", harm), ("bass", ys["bass"][:n]), ("vocals", ys["vocals"][:n])):
        res[name] = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP, bins_per_octave=36).astype(np.float16)
        res[name + "_rms"] = librosa.feature.rms(y=y, hop_length=HOP)[0].astype(np.float16)
    np.savez_compressed(out, **res)


def feats(workers=6):
    from multiprocessing import Pool
    os.makedirs(FEAT, exist_ok=True)
    idx = build_index()
    jobs = {}
    for gp, v in idx.items():
        jobs.setdefault(v["md5"], gp)
    with Pool(workers) as p:
        p.map(_feat_one, [(gp, md5) for md5, gp in jobs.items()], chunksize=1)
        p.map(_stem_feat_one, list(jobs), chunksize=1)
    print("features", len(glob.glob(os.path.join(FEAT, "*.npz"))))


# ---------------------------------------------------------------- detectors
def key_scores(pc, maj=KS_MAJ, mino=KS_MIN):
    """(24,) correlation of a pitch-class vector with every key: 0..11 major, 12..23 minor."""
    pc = np.asarray(pc, float)
    out = np.zeros(24)
    for t in range(12):
        r = np.roll(pc, -t)
        out[t] = np.corrcoef(r, maj)[0, 1]
        out[12 + t] = np.corrcoef(r, mino)[0, 1]
    return out


def sig_scores(pc):
    """(12,) fit of a pitch-class vector to each signature's diatonic set (index = signature mod 12)."""
    pc = np.asarray(pc, float)
    pc = pc / max(pc.sum(), 1e-9)
    out = np.zeros(12)
    for s in range(12):
        t = (s * 7) % 12                  # major tonic of signature s
        out[s] = float(np.roll(DIATONIC, t) @ pc)
    return out


def best_sig(scores24):
    k = int(np.argmax(scores24))
    return sig_of(k % 12, "Major" if k < 12 else "Minor"), ("Major" if k < 12 else "Minor"), k % 12


def bass_pc(notes, weight="dur"):
    h = np.zeros(12)
    for n in notes:
        if n.get("dead"):
            continue
        h[n["midi"] % 12] += (n["end"] - n["time"]) if weight == "dur" else 1.0
    return h


def chord_pc(chords):
    """Duration-weighted pitch classes of recognised chords (root counted double)."""
    from bassnet.song_doc import chord_pcs
    h = np.zeros(12)
    for c in chords:
        dur = float(c["end_time"]) - float(c["start_time"])
        pcs = chord_pcs(c["chord"])
        for i, p in enumerate(pcs):
            h[p] += dur * (2.0 if i == 0 else 1.0)
    return h


def _chroma_norm(c):
    c = np.asarray(c, np.float32)
    return c / np.maximum(c.max(0, keepdims=True), 1e-6)


def evaluate():
    from bassnet.train import load_metas, song_key, frozen_keys
    from bassnet.decode import decode_notes
    idx = build_index()
    val, test = frozen_keys("val"), frozen_keys("test")
    metas = {os.path.normcase(m["gp"]): m for m in load_metas()}
    E = os.path.join(ROOT, "eval")
    rows = []
    seen = set()
    for gp, v in idx.items():
        md5 = v["md5"]
        if md5 in seen or not os.path.exists(os.path.join(FEAT, md5 + ".npz")):
            continue
        seen.add(md5)
        try:
            gs, gm, segs = gt_keys(gp)
        except Exception:
            continue
        f = np.load(os.path.join(FEAT, md5 + ".npz"))
        mix = _chroma_norm(f["mix"])
        r = {"gp": gp, "gt": gs, "mode": gm, "n_keys": len(set(s % 12 for _, s in segs))}
        fps = SR / HOP
        r["cur"] = best_sig(key_scores(mix[:, :int(90 * fps)].mean(1)))[0]
        r["ks_full"] = best_sig(key_scores(mix.mean(1)))[0]
        r["as_full"] = best_sig(key_scores(mix.mean(1), AS_MAJ, AS_MIN))[0]
        r["dia_full"] = int(np.argmax(sig_scores(mix.mean(1))))
        r["pc_mix"] = mix.mean(1)
        st = os.path.join(FEAT, md5 + "_st.npz")
        if os.path.exists(st):
            s = np.load(st)
            harm = _chroma_norm(s["harm"]) * (s["harm_rms"].astype(np.float32) > 1e-3)
            bass = _chroma_norm(s["bass"]) * (s["bass_rms"].astype(np.float32) > 1e-3)
            r["ks_harm"] = best_sig(key_scores(harm.mean(1)))[0]
            r["as_harm"] = best_sig(key_scores(harm.mean(1), AS_MAJ, AS_MIN))[0]
            r["ks_bass"] = best_sig(key_scores(bass.mean(1)))[0]
            r["as_hb"] = best_sig(key_scores(harm.mean(1) + 0.5 * bass.mean(1), AS_MAJ, AS_MIN))[0]
            r["pc_harm"], r["pc_bassstem"] = harm.mean(1), bass.mean(1)
        for src in ("mix", "harm", "orig"):
            cf = os.path.join(CHORDS, f"{md5}_{src}.json")
            if os.path.exists(cf):
                pc = chord_pc(json.load(open(cf)))
                r["dia_chords_" + src] = int(np.argmax(sig_scores(pc)))
                r["as_chords_" + src] = best_sig(key_scores(pc, AS_MAJ, AS_MIN))[0]
                r["pc_chords_" + src] = pc
        m = metas.get(os.path.normcase(gp))
        if m is not None:
            k = song_key(m["gp"])
            key = os.path.basename(m["npz"])[:-4]
            dirs = ["val_v3", "val_v4"] if k in val else ["post_cache_v3", "post_cache_v4"] if k in test else ["xfold"]
            if all(os.path.exists(os.path.join(E, d, key + ".npz")) for d in dirs):
                zs = [np.load(os.path.join(E, d, key + ".npz")) for d in dirs]
                fr, on, de = [np.mean([z[x].astype(np.float32) for z in zs], 0) for x in ("fr", "on", "de")]
                bpc = bass_pc(decode_notes(fr, on, de))
                r["as_bassnotes"] = best_sig(key_scores(bpc, AS_MAJ, AS_MIN))[0]
                r["dia_bassnotes"] = int(np.argmax(sig_scores(bpc)))
                r["pc_bassnotes"] = bpc
                r["as_mix_bn"] = best_sig(key_scores(mix.mean(1) / mix.mean(1).sum()
                                                     + 0.5 * bpc / max(bpc.sum(), 1e-9), AS_MAJ, AS_MIN))[0]
        rows.append(r)
    methods = sorted({k for r in rows for k in r if not k.startswith("pc_")} - {"gp", "gt", "mode", "n_keys"})
    print(len(rows), "recordings")
    for mth in methods:
        rr = [r for r in rows if mth in r]
        ok = np.mean([sig_eq(r[mth], r["gt"]) for r in rr])
        near = np.mean([min((r[mth] - r["gt"]) % 12, (r["gt"] - r[mth]) % 12) <= 1 for r in rr])
        print(f"{mth:14s} n={len(rr):3d} signature {ok:.3f}  within one fifth {near:.3f}")
    fusion_eval(rows)
    import pickle
    pickle.dump(rows, open(os.path.join(ROOT, "key_eval_rows.pkl"), "wb"))
    return rows


# ---------------------------------------------------------------- fused signature model
SOURCES = ("pc_mix", "pc_harm", "pc_bassstem", "pc_chords_orig", "pc_chords_harm", "pc_bassnotes")


def sig_as(pc):
    """(12,) best of major-tonic and relative-minor Albrecht-Shanahan correlation for each signature."""
    k = key_scores(pc, AS_MAJ, AS_MIN)
    out = np.zeros(12)
    for s in range(12):
        t = (s * 7) % 12
        out[s] = max(k[t], k[12 + (t - 3) % 12])
    return out


def sig_features(srcs):
    """(12, 2 * len(SOURCES)) per-candidate evidence; a missing source contributes zeros."""
    F = np.zeros((12, 2 * len(SOURCES)))
    for i, name in enumerate(SOURCES):
        pc = srcs.get(name)
        if pc is None or np.sum(pc) <= 0:
            continue
        for j, v in enumerate((sig_scores(pc), sig_as(pc))):
            F[:, 2 * i + j] = (v - v.mean()) / (v.std() + 1e-9)
    return F


def fit_weights(X, y, l2=0.05, iters=400, lr=0.5):
    """Softmax over the 12 candidate signatures, linear in the evidence. X (N, 12, F), y (N,)."""
    X, y = np.asarray(X), np.asarray(y)
    w = np.zeros(X.shape[2])
    for _ in range(iters):
        s = X @ w
        p = np.exp(s - s.max(1, keepdims=True))
        p /= p.sum(1, keepdims=True)
        g = l2 * w + (np.einsum("nk,nkf->f", p, X) - X[np.arange(len(y)), y].sum(0)) / len(y)
        w -= lr * g
    return w


def fusion_eval(rows, sources=None):
    """Leave-one-out accuracy of the fused model on the songs that have every source."""
    global SOURCES
    for srcs in ([SOURCES] if sources is None else [sources]) + [("pc_mix", "pc_chords_orig"),
                                                                  ("pc_mix", "pc_harm", "pc_chords_orig"),
                                                                  ("pc_mix", "pc_chords_orig", "pc_bassnotes"),
                                                                  ("pc_chords_orig",)]:
        keep = SOURCES
        SOURCES = srcs
        rr = [r for r in rows if all(k in r for k in srcs)]
        if len(rr) < 10:
            SOURCES = keep
            continue
        X = [sig_features(r) for r in rr]
        y = [r["gt"] % 12 for r in rr]
        X, y = np.asarray(X), np.asarray(y)
        fold = np.arange(len(rr)) % 10
        ok = np.zeros(len(rr), bool)
        for f in range(10):
            w = fit_weights(X[fold != f], y[fold != f])
            ok[fold == f] = np.argmax(X[fold == f] @ w, 1) == y[fold == f]
        print(f"fused {'+'.join(x[3:] for x in srcs):45s} n={len(rr):3d} 10-fold signature {ok.mean():.3f}")
        SOURCES = keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["feats", "eval"])
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    feats(a.workers) if a.cmd == "feats" else evaluate()


if __name__ == "__main__":
    main()
