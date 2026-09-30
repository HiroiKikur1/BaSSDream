"""Compare bass separators on real songs, without judging the transcription against the purchased tab.

Per song x separator (stems cached in cache/sep_compare/<sep>/<md5>.flac, 22.05 kHz mono):
  purity   share of the stem's semitone-CQT energy (linear, MIDI 23-96) lying on the harmonics (1-8) of the notes
           actually played (aligned tab notes are used only as a harmonic map here). The rest is drum/guitar/vocal
           leakage or noise. Higher = cleaner.
  low_share share of energy below MIDI 52 (E3): how much actual bass body the stem keeps.
  resynth  production transcription of that stem, re-rendered, vs the stem (resynth_compare.frame_sim).
Also "ens_<a>+<b>": the average of two separators' stems.

python -m bassnet.sep_compare --seps bs_sw htdemucs_ft htdemucs_6s kuielab_a
"""
import argparse
import glob
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.sep_ceiling import MIXES  # noqa: E402

OUT = os.path.join("E:" + os.sep, "BassStation", "cache", "sep_compare")
SEPS = {
    "bs_sw": "BS-Roformer-SW.ckpt",
    "htdemucs_ft": "htdemucs_ft.yaml",
    "htdemucs_6s": "htdemucs_6s.yaml",
    "kuielab_a": "kuielab_a_bass.onnx",
}
EXTRA = ["ALIVE _ Morfonica", "墮天 5弦"]


XLANCE_DIR = os.path.join("D:" + os.sep, "BassData", "restoration", "xlance-msr")
XLANCE_CKPT = os.path.join("D:" + os.sep, "BassData", "restoration", "checkpoints")
XLANCE = {"xlance_bass": ["bass_mss.pth"], "xlance_dn_bass": ["denoise.pth", "bass_mss.pth"]}
_xl_models = {}


def run_xlance(sep_name, src, out_flac):
    """X-LANCE MSR 2025 winner (BS-RoFormer from Roformer-SW, trained on MoisesDB + RawStems with degradations):
    optional denoise stage, then the bass restoration model; full mix in, restored bass out."""
    import sys
    import librosa
    import soundfile as sf
    if XLANCE_DIR not in sys.path:
        sys.path.insert(0, XLANCE_DIR)
    cwd = os.getcwd()
    os.chdir(XLANCE_DIR)                  # the checkpoints look up ./configs/<name>.yaml
    try:
        from inference_full import load_models, inference
        for f in XLANCE[sep_name]:
            if f not in _xl_models:
                _xl_models[f] = load_models([os.path.join(XLANCE_CKPT, f)], "cuda")[0]
        y, sr = librosa.load(src, sr=44100, mono=False)
        y = np.stack([y, y]) if y.ndim == 1 else y
        for f in XLANCE[sep_name]:
            y = inference([_xl_models[f]], y, 44100, batch_size=2)
    finally:
        os.chdir(cwd)
    os.makedirs(os.path.dirname(out_flac), exist_ok=True)
    sf.write(out_flac, librosa.resample(y.mean(0).astype(np.float32), orig_sr=44100, target_sr=22050), 22050)


_swft = {}


def run_swft(src, out_flac, ckpt=None):
    """BS-Roformer-SW with the bass output fine-tuned for transcription (bassnet/restore_train.py)."""
    import sys
    import librosa
    import soundfile as sf
    import torch
    from bassnet import restore_train as RT
    if ckpt not in _swft:
        m, _ = RT.load_sw(False)
        if ckpt != "raw":
            m.load_state_dict(torch.load(ckpt, map_location="cuda", weights_only=False)["state_dict"])
        _swft[ckpt] = m.eval()
    m = _swft[ckpt]
    if XLANCE_DIR not in sys.path:
        sys.path.insert(0, XLANCE_DIR)
    from inference_full import process_long_audio

    class _Bass(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.m = m

        def forward(self, x):
            return self.m(x)[:, 0]
    y, _ = librosa.load(src, sr=44100, mono=False)
    y = np.stack([y, y]) if y.ndim == 1 else y
    est = process_long_audio(_Bass(), y, 44100, chunk_duration=8.0, overlap=1.0, batch_size=1)
    os.makedirs(os.path.dirname(out_flac), exist_ok=True)
    sf.write(out_flac, librosa.resample(est.mean(0).astype(np.float32), orig_sr=44100, target_sr=22050), 22050)


def run_separator(sep_name, src, out_flac):
    if sep_name in XLANCE:
        return run_xlance(sep_name, src, out_flac)
    if sep_name == "sw_bassft":
        from bassnet.restore_train import OUT as FT
        return run_swft(src, out_flac, FT)
    if sep_name == "sw_raw":                  # same weights as bs_sw, same chunking as sw_bassft (fair A/B)
        return run_swft(src, out_flac, "raw")
    import eval_session
    from audio_separator.separator import Separator
    tmp = os.path.join(OUT, "_tmp")
    os.makedirs(tmp, exist_ok=True)
    os.environ["PATH"] = os.path.dirname(eval_session.FFMPEG_EXE) + os.pathsep + os.environ.get("PATH", "")
    sep = Separator(model_file_dir=eval_session.MODELS, output_dir=tmp, output_format="WAV", use_soundfile=True,
                    log_level=40, use_autocast=True)
    sep.load_model(SEPS[sep_name])
    wav = os.path.join(tmp, "in.wav")
    subprocess.run([eval_session.FFMPEG_EXE, "-y", "-v", "error", "-i", src, "-ar", "44100", "-ac", "2",
                    "-c:a", "pcm_s16le", wav], check=True)
    outs = sep.separate(wav)
    bass = [o for o in outs if "(bass)" in os.path.basename(o).lower()]
    if not bass:
        raise RuntimeError(f"{sep_name}: no bass stem in {outs}")
    bp = bass[0] if os.path.isabs(bass[0]) else os.path.join(tmp, bass[0])
    os.makedirs(os.path.dirname(out_flac), exist_ok=True)
    subprocess.run([eval_session.FFMPEG_EXE, "-y", "-v", "error", "-i", bp, "-ar", "22050", "-ac", "1", out_flac],
                   check=True)
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))


def purity(y, notes, sr=22050):
    """Energy share on the played notes' harmonics, and low-register share."""
    from bassnet.dataset import compute_cqt, FPS
    from bassnet.audio_verify import semitone_map
    S = np.expm1(semitone_map(compute_cqt(y))) / 20.0          # back to linear magnitude
    S = S ** 2
    T = S.shape[1]
    mask = np.zeros_like(S, bool)
    for n in notes:
        if n.get("dead") or n.get("grace"):
            continue
        a, b = int(n["time"] * FPS), int(n["end"] * FPS) + 2
        for h in range(1, 9):
            p = int(round(n["midi"] + 12 * np.log2(h))) - 21
            for q in (p - 1, p, p + 1):
                if 0 <= q < S.shape[0]:
                    mask[q, max(0, a):min(T, b)] = True
    band = slice(23 - 21, 97 - 21)
    tot = S[band].sum() + 1e-9
    return float(S[band][mask[band]].sum() / tot), float(S[23 - 21:52 - 21].sum() / tot)


def main():
    import librosa
    from bassnet import eval_cached
    from bassnet.dataset import compute_cqt, compute_mel
    from bassnet.pipeline import posteriors
    from bassnet.decode import decode_notes
    from bassnet.sep_ceiling import render_bass
    from bassnet.resynth_compare import features, frame_sim
    ap = argparse.ArgumentParser()
    ap.add_argument("--seps", nargs="+", default=list(SEPS))
    ap.add_argument("--songs", type=int, default=10)
    ap.add_argument("--ens", nargs="*", default=[], help="pairs a+b to average")
    a = ap.parse_args()
    metas = []
    eval_cached.SPLIT = "val"
    metas += [m for m in eval_cached.metas() if glob.glob(os.path.join(MIXES, os.path.basename(m["stem"])[:-5] + ".*"))][:a.songs]
    eval_cached.SPLIT = "test"
    metas += [m for m in eval_cached.metas() if any(k in m["gp"] for k in EXTRA)]
    res_f = os.path.join(OUT, "results.json")
    res = json.load(open(res_f, encoding="utf8")) if os.path.exists(res_f) else {}
    for m in metas:
        key = os.path.basename(m["stem"])[:-5]
        name = os.path.basename(os.path.dirname(m["gp"]))[:28]
        mixf = glob.glob(os.path.join(MIXES, key + ".*"))
        if not mixf:
            continue
        ym, _ = librosa.load(mixf[0], sr=22050, mono=True)
        mel = compute_mel(ym)
        stems = {}
        for s in a.seps:
            f = os.path.join(OUT, s, key + ".flac")
            if s == "bs_sw" and not os.path.exists(f):
                f = m["stem"]                                      # already separated for training
            if not os.path.exists(f):
                run_separator(s, mixf[0], f)
            stems[s] = librosa.load(f, sr=22050, mono=True)[0]
        for pair in a.ens:
            x, y2 = pair.split("+")
            n = min(len(stems[x]), len(stems[y2]))
            stems["ens_" + pair] = 0.5 * (stems[x][:n] + stems[y2][:n])
        for s, y in stems.items():
            rk = f"{key}|{s}"
            if rk in res:
                continue
            pu, low = purity(y, m["notes"])
            cqt = compute_cqt(y)
            T = cqt.shape[1]
            ml = mel[:, :T] if mel.shape[1] >= T else np.pad(mel, ((0, 0), (0, T - mel.shape[1])))
            fr, on, de, be, do = posteriors(cqt, ml)
            notes = decode_notes(fr, on, de)
            r_mod = render_bass(notes, len(y), np.random.default_rng(1), sr=22050)
            Fs, Fm = features(y), features(r_mod)
            sim, act = frame_sim(Fs, Fm, np.percentile(Fs.sum(0), 30), np.percentile(Fm.sum(0), 30))
            res[rk] = {"song": name, "sep": s, "purity": pu, "low_share": low, "resynth": float(sim[act].mean()),
                       "n_notes": len(notes)}
            json.dump(res, open(res_f, "w", encoding="utf8"), indent=1, ensure_ascii=False)
            print(f"{name:28s} {s:22s} purity {pu:.3f} low {low:.3f} resynth {res[rk]['resynth']:.3f} notes {len(notes)}",
                  flush=True)
    names = sorted({v["sep"] for v in res.values()})
    print("\nseparator              purity  low_share resynth  (n)")
    for s in names:
        rs = [v for v in res.values() if v["sep"] == s]
        print(f"{s:22s} {np.mean([r['purity'] for r in rs]):.3f}   {np.mean([r['low_share'] for r in rs]):.3f}   "
              f"{np.mean([r['resynth'] for r in rs]):.3f}   ({len(rs)})")


if __name__ == "__main__":
    main()
