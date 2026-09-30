"""How much accuracy does source separation cost? (separation upper-bound test)

Per val song: render a synthetic bass from the (re-aligned) tab, add it to the song's backing (original mix minus
its separated bass), separate that new mix again with BS-Roformer, then transcribe both the clean synthetic bass
and the separated one with the production models. Both runs share the synthetic-timbre gap, so their difference
is what separation loses.

python -m bassnet.sep_ceiling [--limit N]
"""
import argparse
import glob
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT = os.path.join("E:" + os.sep, "BassStation", "cache", "sep_ceiling")
MIXES = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "mixes")
SR = 44100


def render_bass(notes, n, rng, sr=SR):
    """Additive plucked-string bass: inharmonic partials with pluck-position comb, frequency-dependent decay,
    pick/finger noise transient; per-song random timbre and optional overdrive."""
    p = rng.uniform(0.12, 0.28)            # pluck position
    tau0 = rng.uniform(0.8, 2.0)           # fundamental decay (s)
    bright = rng.uniform(0.9, 1.6)
    drive = rng.choice([0.0, 0.0, 2.5, 5.0])
    y = np.zeros(n + sr, np.float32)
    for x in notes:
        if x.get("grace"):
            continue
        t0 = int(x["time"] * sr)
        if t0 < 0 or t0 >= n:
            continue
        f0 = 440.0 * 2 ** ((x["midi"] - 69) / 12)
        if x.get("dead"):
            L = int(0.04 * sr)
            tt = np.arange(L) / sr
            s = (rng.standard_normal(L) * np.exp(-tt / 0.008) * 0.3 + np.sin(2 * np.pi * f0 * tt) * np.exp(-tt / 0.015))
            y[t0:t0 + L] += (0.5 * s).astype(np.float32)[:len(y) - t0]
            continue
        dur = max(0.03, x["end"] - x["time"])
        L = int((dur + 0.05) * sr)
        tt = np.arange(L) / sr
        K = int(min(40, 9000 / f0))
        k = np.arange(1, K + 1)[:, None]
        fk = k * f0 * np.sqrt(1 + 1e-4 * k ** 2)
        amp = np.abs(np.sin(np.pi * k * p)) / k ** bright
        dec = np.exp(-tt[None, :] * (1 / tau0 + 0.25 * k))
        s = (amp * dec * np.sin(2 * np.pi * fk * tt[None, :] + rng.uniform(0, 6.28, (K, 1)))).sum(0)
        env = np.minimum(1.0, tt / 0.002)
        rel = np.clip((dur + 0.05 - tt) / 0.05, 0, 1)
        s = s * env * rel
        nz = int(0.006 * sr)
        s[:nz] += rng.standard_normal(nz) * np.exp(-np.arange(nz) / (0.0015 * sr)) * 0.15
        y[t0:t0 + L] += s.astype(np.float32)[:len(y) - t0]
    y = y[:n]
    if drive:
        y = np.tanh(drive * y / (np.percentile(np.abs(y), 99) + 1e-6)) / np.tanh(drive)
    return y


def separate(wav, out_flac):
    from audio_separator.separator import Separator
    import eval_session
    tmp = os.path.join(OUT, "_tmp")
    os.makedirs(tmp, exist_ok=True)
    os.environ["PATH"] = os.path.dirname(eval_session.FFMPEG_EXE) + os.pathsep + os.environ.get("PATH", "")
    sep = Separator(model_file_dir=eval_session.MODELS, output_dir=tmp, output_format="WAV", use_soundfile=True,
                    log_level=40, use_autocast=True)
    sep.load_model("BS-Roformer-SW.ckpt")
    outs = sep.separate(wav)
    bass = [o for o in outs if "(bass)" in os.path.basename(o).lower()][0]
    bp = bass if os.path.isabs(bass) else os.path.join(tmp, bass)
    subprocess.run([eval_session.FFMPEG_EXE, "-y", "-v", "error", "-i", bp, "-ar", "22050", "-ac", "1", out_flac],
                   check=True)
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))


def main():
    import librosa
    import soundfile as sf
    import eval_session
    from bassnet.tune_decode import val_metas
    from bassnet.dataset import compute_cqt, compute_mel
    from bassnet.pipeline import posteriors
    from bassnet.decode import decode_notes, note_metrics
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for i, m in enumerate(val_metas()):
        if a.limit and i >= a.limit:
            break
        key = os.path.basename(m["stem"])[:-5]
        mixf = glob.glob(os.path.join(MIXES, key + ".*"))
        if not mixf:
            print("no mix", key)
            continue
        rng = np.random.default_rng(int(key[:8], 16))
        name = os.path.basename(os.path.dirname(m["gp"]))[:28]
        clean_f, mix_f, sep_f = (os.path.join(OUT, key + s) for s in ("_clean.flac", "_mix.wav", "_sep.flac"))
        if not os.path.exists(sep_f):
            mix = eval_session._ffmpeg_f32(mixf[0], SR, 2)
            backing = eval_session._remove_bass(mix, m["stem"])
            bass = render_bass(m["notes"], len(mix), rng)
            ref, _ = librosa.load(m["stem"], sr=22050, mono=True)
            act = lambda v: np.sqrt(np.mean(v[np.abs(v) > 0.02 * np.abs(v).max()] ** 2))  # noqa: E731
            bass *= act(ref) / (act(bass) + 1e-9)
            sf.write(mix_f, np.clip(backing + bass[:, None], -1, 1), SR, subtype="PCM_16")
            sf.write(clean_f, librosa.resample(bass, orig_sr=SR, target_sr=22050), 22050)
            separate(mix_f, sep_f)
        yc, _ = librosa.load(clean_f, sr=22050, mono=True)
        ys, _ = librosa.load(sep_f, sr=22050, mono=True)
        ym, _ = librosa.load(mix_f, sr=22050, mono=True)
        mel = compute_mel(ym)
        truth = [x for x in m["notes"] if not x.get("grace")]
        r = {"name": name}
        for tag, y in (("clean", yc), ("sep", ys)):
            cqt = compute_cqt(y)
            T = cqt.shape[1]
            ml = mel[:, :T] if mel.shape[1] >= T else np.pad(mel, ((0, 0), (0, T - mel.shape[1])))
            fr, on, de, be, do = posteriors(cqt, ml)
            est = decode_notes(fr, on, de)
            r[tag] = note_metrics(truth, est)
        # separation quality of the stem itself (SDR against the clean synthetic bass)
        n = min(len(yc), len(ys))
        g = float(np.dot(ys[:n], yc[:n]) / (np.dot(ys[:n], ys[:n]) + 1e-9))
        r["sdr"] = float(10 * np.log10(np.sum(yc[:n] ** 2) / (np.sum((yc[:n] - g * ys[:n]) ** 2) + 1e-9)))
        rows.append(r)
        print(f"{name:30s} SDR {r['sdr']:5.1f}dB  clean F1 {r['clean']['F1']:.3f} pitch {r['clean']['pitch_acc_on_matched']:.3f}"
              f"  | sep F1 {r['sep']['F1']:.3f} pitch {r['sep']['pitch_acc_on_matched']:.3f}", flush=True)
    for tag in ("clean", "sep"):
        print(tag, {k: round(float(np.mean([r[tag][k] for r in rows])), 4) for k in rows[0][tag]})
    print("mean SDR", round(float(np.mean([r["sdr"] for r in rows])), 2))
    json.dump(rows, open(os.path.join(OUT, "results.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
