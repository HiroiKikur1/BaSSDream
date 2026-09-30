"""Bass restoration, trained for what we need (architecture v2, Phase 4 step 2).

The open-source MSR winner (X-LANCE) restores a nicer-sounding bass but loses notes (stress test -9 dB: F1 0.806 vs
0.865 for our BS-Roformer-SW). So we fine-tune BS-Roformer-SW's own bass output on REAL clean bass stems
(MoisesDB, MUSDB18-HQ train) mixed the hard way: bass pushed down (-12..+2 dB), bass through drive / fuzz / chorus /
EQ, the rest pushed up and sometimes swapped with another song's drums and guitars.
Only the bass mask estimator and the last two transformer layers train (gradient checkpointing, ~1.8 GB at 6 s),
so the other five stems and the general separation skill stay as they are.

Validation (MUSDB18-HQ test, never trained on): SI-SDR of the bass and, what counts, note F1 of BassNet on the
estimate against BassNet on the true bass stem (bassnet/sep_real_eval.py does the same on full tracks).

python -m bassnet.restore_train cache                    (songs -> D:\\BassData\\restore_cache\\<id>_{bass,rest}.npy)
python -m bassnet.restore_train train [--steps 20000]    -> cache/bassnet/staging/sw_bassft.ckpt (+ .yaml)
"""
import argparse
import glob
import json
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SR = 44100
MUSDB = os.path.join("D:" + os.sep, "BassData", "musdb18hq")
MOISES = os.path.join("D:" + os.sep, "BassData", "moisesdb")
CACHE = os.path.join("D:" + os.sep, "BassData", "restore_cache")
XLANCE = os.path.join("D:" + os.sep, "BassData", "restoration", "xlance-msr")   # BSRoformer code (patched)
ROOT_BN = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
SW_CKPT = os.path.join("E:" + os.sep, "BassStation", "cache", "models", "BS-Roformer-SW.ckpt")
SW_YAML = os.path.join("E:" + os.sep, "BassStation", "cache", "models", "BS-Roformer-SW.yaml")
OUT = os.environ.get("BASSNET_RESTORE_OUT", os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "staging", "sw_bassft.ckpt"))


# ------------------------------------------------------------------ data
def _read(paths):
    import soundfile as sf
    acc = None
    for p in paths:
        y, sr = sf.read(p, dtype="float32", always_2d=True)
        if sr != SR:
            import librosa
            y = librosa.resample(y.T, orig_sr=sr, target_sr=SR).T
        if y.shape[1] == 1:
            y = np.repeat(y, 2, 1)
        y = y[:, :2]
        if acc is None:
            acc = y
        else:
            n = max(len(acc), len(y))
            acc = np.pad(acc, ((0, n - len(acc)), (0, 0))) + np.pad(y, ((0, n - len(y)), (0, 0)))
    return acc


def songs():
    """[(id, split, bass files, rest files)]"""
    out = []
    for split in ("train", "test"):
        for d in sorted(glob.glob(os.path.join(MUSDB, split, "*"))):
            if os.path.exists(os.path.join(d, "bass.wav")):
                out.append(("musdb_" + os.path.basename(d).replace(" ", "_")[:60], "val" if split == "test" else "train",
                            [os.path.join(d, "bass.wav")], [os.path.join(d, s + ".wav") for s in ("drums", "other", "vocals")]))
    for d in sorted(glob.glob(os.path.join(MOISES, "**", "data.json"), recursive=True)):
        root = os.path.dirname(d)
        bass = glob.glob(os.path.join(root, "bass", "*.wav"))
        rest = [f for f in glob.glob(os.path.join(root, "*", "*.wav")) if os.path.basename(os.path.dirname(f)) != "bass"]
        if bass and rest:
            out.append(("moises_" + os.path.basename(root), "train", bass, rest))
    return out


def build_cache():
    os.makedirs(CACHE, exist_ok=True)
    meta = []
    for sid, split, b, r in songs():
        fb, fr = os.path.join(CACHE, sid + "_bass.npy"), os.path.join(CACHE, sid + "_rest.npy")
        if not os.path.exists(fr):
            yb, yr = _read(b), _read(r)
            n = min(len(yb), len(yr))
            np.save(fb, yb[:n].astype(np.float16))
            np.save(fr, yr[:n].astype(np.float16))
            print(sid, round(n / SR), "s", flush=True)
        meta.append({"id": sid, "split": split})
    json.dump(meta, open(os.path.join(CACHE, "meta.json"), "w"), indent=1)
    print(len(meta), "songs", sum(m["split"] == "train" for m in meta), "train")
    cache_lib_rest()


STEMS6 = os.path.join("D:" + os.sep, "BassData", "stems6")


def cache_lib_rest():
    """Band without bass from our own J-rock library (6-stem separation minus bass), training songs only: dense
    distorted guitars and loud drums like the recordings the app sees. -> lib_<md5>_rest.npy"""
    from bassnet.build_stems import build_index
    from bassnet.train import song_key, frozen_keys
    held = frozen_keys("val") | frozen_keys("test")
    n = 0
    for gp, v in build_index().items():
        md5 = v["md5"]
        out = os.path.join(CACHE, f"lib_{md5}_rest.npy")
        d = os.path.join(STEMS6, md5)
        if song_key(gp) in held or os.path.exists(out) or not os.path.exists(os.path.join(d, "meta.json")):
            continue
        y = _read([os.path.join(d, s + ".flac") for s in ("drums", "guitar", "piano", "vocals", "other")])
        np.save(out, y.astype(np.float16))
        n += 1
    print("library backings cached", n, flush=True)


# ------------------------------------------------------------------ augmentation (numpy, per chunk)
# run 1 (bass -12..+2 dB, FX 65%, rest +0..6 dB, 30% foreign rest, mask head + last 2 layers, lr 2e-5) made the
# MUSDB-test note F1 worse (0.8275 -> 0.811 after 1-2k steps): milder mixing, only the bass mask head, lower lr
BASS_DB, REST_DB, FX_P, CROSS_P = (-9.0, 3.0), (0.0, 4.0), 0.35, 0.15
TRAIN_LAYERS = False
LIB_P = 0.4           # share of chunks whose band comes from our library (round 2 on)
def _db(x):
    return 10 ** (x / 20)


def bass_fx(y, rng):
    if rng.random() > FX_P:
        return y
    k = 0.35 + 0.65 * rng.random()
    if k < 0.55:                                      # overdrive
        g = rng.uniform(2, 8)
        return np.tanh(g * y) / np.tanh(g)
    if k < 0.7:                                       # fuzz: hard clip + a bit of asymmetry
        c = rng.uniform(0.05, 0.3) * np.abs(y).max()
        return np.clip(y + 0.1 * c, -c, c) / max(c, 1e-6) * np.abs(y).max()
    if k < 0.85:                                      # chorus: modulated short delay
        n = len(y)
        t = np.arange(n)
        d = (0.012 + 0.004 * np.sin(2 * np.pi * rng.uniform(0.3, 1.5) * t / SR)) * SR
        idx = np.clip(t - d.astype(int), 0, n - 1)
        return 0.7 * y + 0.5 * y[idx]
    from scipy.signal import butter, sosfilt           # EQ: brighter or darker
    if rng.random() < 0.5:
        sos = butter(2, rng.uniform(600, 2500), "lowpass", fs=SR, output="sos")
    else:
        sos = butter(2, rng.uniform(40, 120), "highpass", fs=SR, output="sos")
    return sosfilt(sos, y, axis=0).astype(np.float32)


class Chunks:
    def __init__(self, split, sec=6.0, seed=0):
        meta = json.load(open(os.path.join(CACHE, "meta.json")))
        self.ids = [m["id"] for m in meta if m["split"] == split]
        self.b = {i: np.load(os.path.join(CACHE, i + "_bass.npy"), mmap_mode="r") for i in self.ids}
        self.r = {i: np.load(os.path.join(CACHE, i + "_rest.npy"), mmap_mode="r") for i in self.ids}
        self.n = int(sec * SR)
        self.rng = np.random.default_rng(seed)
        self.lib = [np.load(f, mmap_mode="r") for f in sorted(glob.glob(os.path.join(CACHE, "lib_*_rest.npy")))]             if split == "train" else []

    def _crop(self, a, i0):
        return np.asarray(a[i0:i0 + self.n], np.float32)

    def sample(self):
        rng = self.rng
        for _ in range(20):
            sid = self.ids[rng.integers(len(self.ids))]
            L = len(self.b[sid])
            if L <= self.n:
                continue
            i0 = int(rng.integers(0, L - self.n))
            yb = self._crop(self.b[sid], i0)
            if np.sqrt(np.mean(yb ** 2)) < 3e-3:          # need audible bass
                continue
            yr = self._crop(self.r[sid], i0)
            if self.lib and rng.random() < LIB_P:            # our own J-rock band instead of the song's own
                r2 = self.lib[rng.integers(len(self.lib))]
                if len(r2) > self.n:
                    j = int(rng.integers(0, len(r2) - self.n))
                    yr = self._crop(r2, j)
            elif rng.random() < CROSS_P:                    # rest from another song (tempo/key-agnostic stress)
                s2 = self.ids[rng.integers(len(self.ids))]
                if len(self.r[s2]) > self.n:
                    j = int(rng.integers(0, len(self.r[s2]) - self.n))
                    yr = 0.5 * yr + self._crop(self.r[s2], j)
            yb = bass_fx(yb, rng) * _db(rng.uniform(*BASS_DB))
            yr = yr * _db(rng.uniform(*REST_DB))
            mix = yb + yr
            peak = np.abs(mix).max()
            if peak > 0.98:
                yb, mix = yb * 0.98 / peak, mix * 0.98 / peak
            return mix.T.astype(np.float32).copy(), yb.T.astype(np.float32).copy()
        raise RuntimeError("no usable chunk")


# ------------------------------------------------------------------ model
def load_sw(trainable=True, dev="cuda"):
    import torch
    import yaml
    if XLANCE not in sys.path:
        sys.path.insert(0, XLANCE)
    from models.bs_roformer import bs_roformer as BSR
    cfg = yaml.load(open(SW_YAML), Loader=yaml.FullLoader)
    cfg["model"]["use_torch_checkpoint"] = trainable
    m = BSR.BSRoformer(**cfg["model"])
    sd = torch.load(SW_CKPT, map_location="cpu", weights_only=False)
    m.load_state_dict(sd.get("state_dict", sd))
    for n, p in m.named_parameters():
        p.requires_grad = trainable and (n.startswith("mask_estimators.0.") or
                                         (TRAIN_LAYERS and (n.startswith("layers.10") or n.startswith("layers.11"))))
    return m.to(dev), cfg


def stft_loss(est, ref):
    import torch
    loss = 0.0
    for n_fft in (4096, 2048, 1024, 512):
        w = torch.hann_window(n_fft, device=est.device)
        E = torch.stft(est.reshape(-1, est.shape[-1]), n_fft, n_fft // 4, window=w, return_complex=True).abs()
        R = torch.stft(ref.reshape(-1, ref.shape[-1]), n_fft, n_fft // 4, window=w, return_complex=True).abs()
        loss = loss + (E - R).abs().mean() + (torch.log1p(E) - torch.log1p(R)).abs().mean()
    return loss


def validate(model, n_tracks=8, sec=30.0, dev="cuda"):
    """MUSDB test: SI-SDR and BassNet note F1 of the estimate vs the true bass, on 30 s excerpts."""
    import torch
    import librosa
    from bassnet.sep_real_eval import si_sdr, transcribe
    from bassnet.decode import note_metrics
    meta = [m for m in json.load(open(os.path.join(CACHE, "meta.json"))) if m["split"] == "val"][:n_tracks]
    model.eval()
    sdr, f1 = [], []
    for m in meta:
        b = np.load(os.path.join(CACHE, m["id"] + "_bass.npy"), mmap_mode="r")
        r = np.load(os.path.join(CACHE, m["id"] + "_rest.npy"), mmap_mode="r")
        i0 = min(len(b) // 3, max(0, len(b) - int(sec * SR)))
        yb = np.asarray(b[i0:i0 + int(sec * SR)], np.float32)
        mix = yb + np.asarray(r[i0:i0 + int(sec * SR)], np.float32)
        out = []
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
            step = int(6 * SR)
            for a in range(0, len(mix), step):
                x = torch.from_numpy(mix[a:a + step].T.copy())[None].to(dev)
                if x.shape[-1] < 2048:
                    out.append(np.zeros((x.shape[-1], 2), np.float32))
                    continue
                out.append(model(x)[0, 0].float().cpu().numpy().T)
        est = np.concatenate(out)[:len(yb)]
        e22 = librosa.resample(est.mean(1), orig_sr=SR, target_sr=22050)
        t22 = librosa.resample(yb.mean(1), orig_sr=SR, target_sr=22050)
        m22 = librosa.resample(mix.mean(1), orig_sr=SR, target_sr=22050)
        sdr.append(si_sdr(t22, e22))
        ref_notes = [dict(n, grace=False) for n in transcribe(t22, m22)]
        f1.append(note_metrics(ref_notes, transcribe(e22, m22))["F1"])
    model.eval()        # dropout stays off: frozen layers must give the head the same features as at inference
    return float(np.mean(sdr)), float(np.mean(f1))


LIBVAL = os.path.join("D:" + os.sep, "BassData", "restore_libval")


def libval_songs(split="val"):
    """Our own J-rock recordings (frozen val split): original mix as 44.1 kHz wav + re-aligned labels (labels_v2).
    The separator cannot have memorised these, unlike MUSDB (probably in BS-Roformer-SW's training data: every
    fine-tune made MUSDB-test worse from the first 1000 steps)."""
    import subprocess
    import zipfile
    from bassnet.train import load_metas, song_key, frozen_keys
    from bassnet.gpif_parser import parse_gp
    from bassnet.build_stems import FFMPEG
    keys = frozen_keys(split)
    os.makedirs(LIBVAL, exist_ok=True)
    out, seen = [], set()
    for m in load_metas():
        md5 = os.path.basename(m["npz"])[:-4]
        if song_key(m["gp"]) not in keys or md5 in seen:
            continue
        seen.add(md5)
        wav = os.path.join(LIBVAL, md5 + ".wav")
        if not os.path.exists(wav):
            info = parse_gp(m["gp"])
            src = os.path.join(LIBVAL, "_src.bin")
            open(src, "wb").write(zipfile.ZipFile(m["gp"]).read(info.audio_asset))
            subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", src, "-ar", str(SR), "-ac", "2", wav])
            os.remove(src)
        lab = os.path.join(ROOT_BN, "labels_v2", md5 + ".json")
        notes = json.load(open(lab, encoding="utf8"))["notes"] if os.path.exists(lab) else m["notes"]
        out.append((md5, wav, [n for n in notes if not n.get("grace")]))
    return out


def separate_bass(model, mix, dev="cuda"):
    """(2, L) mix -> (2, L) bass, 8 s chunks with 1 s cross-fade (same as sep_compare.run_swft)."""
    import torch
    if XLANCE not in sys.path:
        sys.path.insert(0, XLANCE)
    from inference_full import process_long_audio

    class _Bass(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.m = model

        def forward(self, x):
            return self.m(x)[:, 0]
    return process_long_audio(_Bass(), mix, SR, chunk_duration=8.0, overlap=1.0, batch_size=1)


def validate_lib(model, limit=10, dev="cuda"):
    """Note F1 of BassNet on the separated bass against the labels of our own songs (the number that matters)."""
    import contextlib
    import io
    import librosa
    import soundfile as sf
    from bassnet.sep_real_eval import transcribe
    from bassnet.decode import note_metrics
    model.eval()
    f1 = []
    for md5, wav, notes in libval_songs()[:limit]:
        y, _ = sf.read(wav, dtype="float32", always_2d=True)
        with contextlib.redirect_stdout(io.StringIO()):
            est = separate_bass(model, y.T.copy(), dev)
        e22 = librosa.resample(est.mean(0).astype(np.float32), orig_sr=SR, target_sr=22050)
        m22 = librosa.resample(y.mean(1), orig_sr=SR, target_sr=22050)
        f1.append(note_metrics(notes, transcribe(e22, m22))["F1"])
    model.eval()
    return float(np.mean(f1))


def train(steps=20000, lr=5e-6, sec=6.0, val_every=1500, dev="cuda"):
    import torch
    from bassnet.thermal import guard, lower_priority
    lower_priority()
    model, cfg = load_sw(True, dev)
    model.eval()   # fine-tune without dropout (attn/ff dropout 0.1 in the config would perturb the frozen features)
    data = Chunks("train", sec)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr, weight_decay=1e-4)
    scaler = torch.amp.GradScaler("cuda")
    base = validate(model) + (validate_lib(model),)
    print(f"step 0 (BS-Roformer-SW as is) MUSDB SI-SDR {base[0]:.2f} note F1 {base[1]:.4f} | library note F1 "
          f"{base[2]:.4f}", flush=True)
    best = base[2]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    run = 0.0
    for step in range(1, steps + 1):
        guard()                                  # GPU temperature / power cap (bassnet/thermal.py)
        mix, yb = data.sample()
        x = torch.from_numpy(mix)[None].to(dev)
        t = torch.from_numpy(yb)[None].to(dev)
        with torch.autocast("cuda", dtype=torch.float16):
            y = model(x)[:, 0]
        y = y.float()[..., :t.shape[-1]]
        loss = (y - t[..., :y.shape[-1]]).abs().mean() + stft_loss(y, t[..., :y.shape[-1]])
        opt.zero_grad()
        scaler.scale(loss).backward()
        scaler.unscale_(opt)
        torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
        scaler.step(opt)
        scaler.update()
        run = 0.98 * run + 0.02 * float(loss)
        if step % val_every == 0:
            s, f = validate(model)
            fl = validate_lib(model)
            print(f"step {step} loss {run:.4f} MUSDB SI-SDR {s:.2f} note F1 {f:.4f} | library note F1 {fl:.4f}",
                  flush=True)
            if fl > best:
                best = fl
                torch.save({"state_dict": model.state_dict(), "step": step, "val": [s, f, fl], "base": base}, OUT)
                import shutil
                shutil.copy(SW_YAML, OUT[:-5] + ".yaml")
    print("best library note F1", best, "(as is:", base[2], ")")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["cache", "train", "val"])
    ap.add_argument("--steps", type=int, default=20000)
    ap.add_argument("--lr", type=float, default=5e-6)
    a = ap.parse_args()
    if a.cmd == "cache":
        build_cache()
    elif a.cmd == "train":
        train(a.steps, a.lr)
    else:
        m, _ = load_sw(False)
        print(validate(m), validate_lib(m))


if __name__ == "__main__":
    main()
