"""Separation / restoration on REAL multitracks (MUSDB18-HQ test, later MoisesDB): the true bass stem is known.

For every track: mixture -> separator -> bass estimate; scored against the real bass stem by
  SDR (scale-invariant, 22.05 kHz mono) and
  transcription agreement: BassNet notes from the estimate vs BassNet notes from the true stem (onset+pitch F1),
  i.e. how much the separator changes what we would write -- the number that matters for tabs.
Complements sep_stress.py (synthetic timbres in real backings).

python -m bassnet.sep_real_eval --seps bs_sw xlance_bass [--limit N]
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MUSDB = os.path.join("D:" + os.sep, "BassData", "musdb18hq")
OUT = os.path.join("D:" + os.sep, "BassData", "sep_real")


def transcribe(y, ym):
    from bassnet.dataset import compute_cqt, compute_mel
    from bassnet.pipeline import posteriors
    from bassnet.decode import decode_notes
    cqt = compute_cqt(y)
    mel = compute_mel(ym)
    T = cqt.shape[1]
    mel = mel[:, :T] if mel.shape[1] >= T else np.pad(mel, ((0, 0), (0, T - mel.shape[1])))
    fr, on, de, be, do = posteriors(cqt, mel)
    return decode_notes(fr, on, de)


def si_sdr(ref, est):
    n = min(len(ref), len(est))
    r, e = ref[:n], est[:n]
    a = float(np.dot(e, r) / (np.dot(r, r) + 1e-9))
    return float(10 * np.log10(np.sum((a * r) ** 2) / (np.sum((a * r - e) ** 2) + 1e-9)))


def main():
    import librosa
    from bassnet.sep_compare import run_separator
    from bassnet.decode import note_metrics
    ap = argparse.ArgumentParser()
    ap.add_argument("--seps", nargs="+", default=["bs_sw", "xlance_bass"])
    ap.add_argument("--split", default="test")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    res_f = os.path.join(OUT, "results.json")
    res = json.load(open(res_f, encoding="utf8")) if os.path.exists(res_f) else {}
    tracks = sorted(glob.glob(os.path.join(MUSDB, a.split, "*", "mixture.wav")))
    if a.limit:
        tracks = tracks[:a.limit]
    for mix in tracks:
        d = os.path.dirname(mix)
        name = os.path.basename(d)
        yb, _ = librosa.load(os.path.join(d, "bass.wav"), sr=22050, mono=True)
        if np.sqrt(np.mean(yb ** 2)) < 1e-3:
            continue
        ym, _ = librosa.load(mix, sr=22050, mono=True)
        ref_notes = None
        for sep in a.seps:
            rk = f"musdb|{name}|{sep}"
            if rk in res:
                continue
            out = os.path.join(OUT, sep, name + ".flac")
            if not os.path.exists(out):
                run_separator(sep, mix, out)
            ys, _ = librosa.load(out, sr=22050, mono=True)
            if ref_notes is None:
                ref_notes = [dict(n, grace=False) for n in transcribe(yb, ym)]
            est = transcribe(ys, ym)
            r = {"track": name, "separator": sep, "si_sdr": si_sdr(yb, ys), "notes": note_metrics(ref_notes, est)}
            res[rk] = r
            json.dump(res, open(res_f, "w", encoding="utf8"), indent=1, ensure_ascii=False)
            print(f"{name[:30]:30s} {sep:14s} SI-SDR {r['si_sdr']:5.1f}  note F1 vs true-stem notes "
                  f"{r['notes']['F1']:.3f}", flush=True)
    for sep in a.seps:
        rs = [v for v in res.values() if v["separator"] == sep]
        if rs:
            print(f"{sep:14s} n={len(rs)}  SI-SDR {np.mean([r['si_sdr'] for r in rs]):.2f}  "
                  f"note F1 {np.mean([r['notes']['F1'] for r in rs]):.3f}")


if __name__ == "__main__":
    main()
