"""Separation stress test across bass timbres (the clean-timbre test in sep_ceiling.py is not enough).

For each (val song backing x timbre): render the tab with that timbre, mix into the song's real backing, separate
with BS-Roformer, then transcribe the clean render and the separated stem. Reports per timbre:
  SDR of the separated stem, F1/pitch on the clean render (model robustness to the timbre itself) and on the
  separated stem (what separation adds on top).

python -m bassnet.lab.sep_stress [--songs 8] [--styles ...]
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from bassnet.sep_ceiling import MIXES  # noqa: E402
from bassnet.sep_compare import run_separator  # noqa: E402

OUT = os.path.join("E:" + os.sep, "BassStation", "cache", "sep_stress")
SR = 44100
STYLES = ["finger", "pick", "slap", "overdrive", "fuzz", "synth", "octaver", "chorus"]


def _partials(f0, tt, K, amp, dec, rng, fc=None):
    k = np.arange(1, K + 1)[:, None]
    fk = k * f0 * np.sqrt(1 + 1e-4 * k ** 2)
    g = amp * np.exp(-tt[None, :] * dec)
    if fc is not None:                          # time-varying low-pass (synth filter envelope)
        g = g / (1 + (fk / fc[None, :]) ** 2)
    return (g * np.sin(2 * np.pi * fk * tt[None, :] + rng.uniform(0, 6.28, (K, 1)))).sum(0)


def render(notes, n, style, rng, sr=SR, shift=0):
    y = np.zeros(n + sr, np.float32)
    for x in notes:
        if x.get("grace"):
            continue
        t0 = int(x["time"] * sr)
        if t0 < 0 or t0 >= n:
            continue
        f0 = 440.0 * 2 ** ((x["midi"] + shift - 69) / 12)
        dur = max(0.03, x["end"] - x["time"])
        L = int((dur + 0.05) * sr)
        tt = np.arange(L) / sr
        K = int(min(60, 10000 / f0))
        k = np.arange(1, K + 1)[:, None]
        if x.get("dead"):
            L = int(0.04 * sr)
            tt = tt[:L]
            s = rng.standard_normal(L) * np.exp(-tt / 0.008) * 0.3 + np.sin(2 * np.pi * f0 * tt) * np.exp(-tt / 0.015)
            y[t0:t0 + L] += (0.5 * s).astype(np.float32)[:len(y) - t0]
            continue
        trans = 0.15
        if style == "synth":
            fc = 350 + 2600 * np.exp(-tt / 0.12)
            s = _partials(f0, tt, K, 1.0 / k, 0.3 + 0 * k, rng, fc)
            trans = 0.0
        elif style == "slap":
            s = _partials(f0, tt, K, np.abs(np.sin(np.pi * k * 0.08)) / k ** 0.6, 1 / 0.5 + 0.6 * k, rng)
            trans = 0.9
        elif style == "pick":
            s = _partials(f0, tt, K, np.abs(np.sin(np.pi * k * 0.1)) / k ** 0.75, 1 / 1.2 + 0.3 * k, rng)
            trans = 0.45
        else:                                     # finger (also the base for effect styles)
            s = _partials(f0, tt, K, np.abs(np.sin(np.pi * k * 0.22)) / k ** 1.2, 1 / 1.5 + 0.25 * k, rng)
        env = np.minimum(1.0, tt / 0.002) * np.clip((dur + 0.05 - tt) / 0.05, 0, 1)
        s = s * env
        nz = int(0.006 * sr)
        if trans:
            click = rng.standard_normal(nz) * np.exp(-np.arange(nz) / (0.0012 * sr))
            if style == "slap":
                click = np.diff(np.concatenate([[0], click]))       # brighter, snappier attack
            s[:nz] += click * trans
        y[t0:t0 + L] += s.astype(np.float32)[:len(y) - t0]
    y = y[:n]
    y /= np.percentile(np.abs(y), 99.5) + 1e-6
    from scipy.signal import butter, sosfilt
    if style == "overdrive":
        y = np.tanh(5 * y) / np.tanh(5)
        y = sosfilt(butter(2, 4500, "low", fs=sr, output="sos"), y)
    elif style == "fuzz":
        y = np.clip(25 * y, -1, 1)
        y = sosfilt(butter(2, 6000, "low", fs=sr, output="sos"), y)
    elif style == "octaver":
        y = y + 0.8 * render(notes, n, "finger", rng, sr, shift=-12)
    elif style == "chorus":
        t = np.arange(n) / sr
        d = (0.010 + 0.004 * np.sin(2 * np.pi * 0.8 * t)) * sr
        idx = np.clip(np.arange(n) - d, 0, n - 1)
        i0 = idx.astype(int)
        fr = idx - i0
        wet = y[i0] * (1 - fr) + y[np.minimum(i0 + 1, n - 1)] * fr
        y = y + 0.7 * wet
    return (y / (np.abs(y).max() + 1e-6)).astype(np.float32)


def main():
    import librosa
    import soundfile as sf
    import eval_session
    from bassnet.tune_decode import val_metas
    from bassnet.dataset import compute_cqt, compute_mel
    from bassnet.pipeline import posteriors
    from bassnet.decode import decode_notes, note_metrics
    ap = argparse.ArgumentParser()
    ap.add_argument("--songs", type=int, default=8)
    ap.add_argument("--styles", nargs="*", default=STYLES)
    ap.add_argument("--seps", nargs="*", default=["bs_sw"])
    ap.add_argument("--levels", type=float, nargs="*", default=[0.0], help="bass level vs original, dB")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    res_f = os.path.join(OUT, "results.json")
    res = json.load(open(res_f, encoding="utf8")) if os.path.exists(res_f) else {}
    metas = [m for m in val_metas() if glob.glob(os.path.join(MIXES, os.path.basename(m["stem"])[:-5] + ".*"))]
    for m in metas[:a.songs]:
        key = os.path.basename(m["stem"])[:-5]
        name = os.path.basename(os.path.dirname(m["gp"]))[:28]
        mix = eval_session._ffmpeg_f32(glob.glob(os.path.join(MIXES, key + ".*"))[0], SR, 2)
        backing = eval_session._remove_bass(mix, m["stem"])
        ref, _ = librosa.load(m["stem"], sr=22050, mono=True)
        act = lambda v: np.sqrt(np.mean(v[np.abs(v) > 0.02 * np.abs(v).max()] ** 2))  # noqa: E731
        ym_cache = {}
        truth = [x for x in m["notes"] if not x.get("grace")]
        for style, lvl, sep in [(x, y, z) for x in a.styles for y in a.levels for z in a.seps]:
            from bassnet.thermal import guard
            guard(0)
            rk = f"{key}|{style}" + (f"|{lvl:g}dB|{sep}" if (lvl or sep != "bs_sw") else "")
            if rk in res:
                continue
            rng = np.random.default_rng(7)
            bass = render(m["notes"], len(mix), style, rng)
            bass *= act(ref) / (act(librosa.resample(bass, orig_sr=SR, target_sr=22050)) + 1e-9) * 10 ** (lvl / 20)
            mix_f = os.path.join(OUT, f"{key}_{style}_{lvl:g}_mix.wav")
            sep_f = os.path.join(OUT, sep, f"{key}_{style}_{lvl:g}_sep.flac")
            if not os.path.exists(mix_f):
                sf.write(mix_f, np.clip(backing + bass[:, None], -1, 1), SR, subtype="PCM_16")
            if not os.path.exists(sep_f):
                run_separator(sep, mix_f, sep_f)
            yc = librosa.resample(bass, orig_sr=SR, target_sr=22050)
            ys, _ = librosa.load(sep_f, sr=22050, mono=True)
            ym, _ = librosa.load(mix_f, sr=22050, mono=True)
            mel = compute_mel(ym)
            r = {"song": name, "style": style, "level": lvl, "separator": sep}
            for tag, y in (("clean", yc), ("sep", ys)):
                cqt = compute_cqt(y)
                T = cqt.shape[1]
                ml = mel[:, :T] if mel.shape[1] >= T else np.pad(mel, ((0, 0), (0, T - mel.shape[1])))
                fr, on, de, be, do = posteriors(cqt, ml)
                r[tag] = note_metrics(truth, decode_notes(fr, on, de))
            n = min(len(yc), len(ys))
            g = float(np.dot(ys[:n], yc[:n]) / (np.dot(ys[:n], ys[:n]) + 1e-9))
            r["sdr"] = float(10 * np.log10(np.sum(yc[:n] ** 2) / (np.sum((yc[:n] - g * ys[:n]) ** 2) + 1e-9)))
            res[rk] = r
            json.dump(res, open(res_f, "w", encoding="utf8"), indent=1, ensure_ascii=False)
            print(f"{name:28s} {style:9s} {lvl:+4.0f}dB {sep:12s} SDR {r['sdr']:5.1f}  clean F1 {r['clean']['F1']:.3f} pitch "
                  f"{r['clean']['pitch_acc_on_matched']:.3f} | sep F1 {r['sep']['F1']:.3f} pitch "
                  f"{r['sep']['pitch_acc_on_matched']:.3f}", flush=True)
    print("style     level sep           SDR   cleanF1 sepF1  clean_pitch sep_pitch")
    combos = sorted({(v["style"], v.get("level", 0.0), v.get("separator", "bs_sw")) for v in res.values()},
                    key=lambda c: (STYLES.index(c[0]), c[1], c[2]))
    for style, lvl, sep in combos:
        rs = [v for v in res.values() if (v["style"], v.get("level", 0.0), v.get("separator", "bs_sw")) == (style, lvl, sep)]
        if rs:
            f = lambda t, k: np.mean([r[t][k] for r in rs])  # noqa: E731
            print(f"{style:9s} {lvl:+4.0f}  {sep:12s} {np.mean([r['sdr'] for r in rs]):5.1f}  {f('clean', 'F1'):.3f}  {f('sep', 'F1'):.3f}   "
                  f"{f('clean', 'pitch_acc_on_matched'):.3f}      {f('sep', 'pitch_acc_on_matched'):.3f}   (n={len(rs)})")


if __name__ == "__main__":
    main()
