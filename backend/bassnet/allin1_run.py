"""allin1 (All-In-One music structure analyzer, Kim & Nam 2023) on our own 6-stem separation (Layer 1, zero-shot).

allin1 wants 4 stems (bass, drums, other, vocals); ours come from BS-Roformer-SW, so other = guitar + piano + other.
Runs in the isolated venv cache/venv_allin1 (madmom built there; NATTEN replaced by a pure-PyTorch shim in its
site-packages because NATTEN has no Windows build).

cache/venv_allin1/Scripts/python.exe -m bassnet.allin1_run [--limit N] [--md5 X]
-> cache/bassnet/allin1/<md5>.json (beats, downbeats, beat positions, segments, bpm) + <md5>.npz (activations)
"""
import argparse
import json
import os
import sys

import numpy as np

STEMS6 = os.path.join("D:" + os.sep, "BassData", "stems6")
OUT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "allin1")
HF_CACHE = os.path.join("E:" + os.sep, "BassStation", "cache", "models", "allin1")


def load4(d):
    import soundfile as sf
    ys = {}
    for s in ("bass", "drums", "guitar", "piano", "vocals", "other"):
        y, sr = sf.read(os.path.join(d, s + ".flac"), dtype="float32", always_2d=True)
        assert sr == 44100
        ys[s] = y.mean(1)
    n = min(len(v) for v in ys.values())
    return {"bass": ys["bass"][:n], "drums": ys["drums"][:n],
            "other": ys["guitar"][:n] + ys["piano"][:n] + ys["other"][:n], "vocals": ys["vocals"][:n]}


def spectrogram(stems):
    from madmom.audio.signal import FramedSignalProcessor, Signal
    from madmom.audio.stft import ShortTimeFourierTransformProcessor
    from madmom.processors import SequentialProcessor
    from madmom.audio.spectrogram import FilteredSpectrogramProcessor, LogarithmicSpectrogramProcessor
    proc = SequentialProcessor([FramedSignalProcessor(frame_size=2048, fps=100), ShortTimeFourierTransformProcessor(),
                                FilteredSpectrogramProcessor(num_bands=12, fmin=30, fmax=17000, norm_filters=True),
                                LogarithmicSpectrogramProcessor(mul=1, add=1)])
    return np.stack([proc(Signal(stems[s], sample_rate=44100, num_channels=1))
                     for s in ("bass", "drums", "other", "vocals")]).astype(np.float32)


def load4_dir(d):
    """bass / drums / other / vocals .flac as written by ai_transcriber.save_stems4."""
    import soundfile as sf
    ys = {}
    for s in ("bass", "drums", "other", "vocals"):
        y, sr = sf.read(os.path.join(d, s + ".flac"), dtype="float32")
        if sr != 44100:
            import librosa
            y = librosa.resample(y, orig_sr=sr, target_sr=44100)
        ys[s] = y
    n = min(len(v) for v in ys.values())
    return {k: v[:n] for k, v in ys.items()}


def run_app(stems4, ckpt, out, dev):
    """App mode: one song's 4 stems -> fine-tuned model -> activations npz (beat, downbeat, 100 fps)."""
    import torch
    from allin1.models import load_pretrained_model
    from allin1.helpers import compute_activations
    c = torch.load(ckpt, map_location=dev, weights_only=False)
    model = load_pretrained_model(f"harmonix-fold{c['fold']}", cache_dir=HF_CACHE, device=dev)
    model.load_state_dict(c["state_dict"])
    model.eval()
    spec = spectrogram(load4_dir(stems4))
    with torch.no_grad():
        act = compute_activations(model(torch.from_numpy(spec).unsqueeze(0).to(dev)))
    np.savez_compressed(out, beat=np.asarray(act["beat"], np.float16), downbeat=np.asarray(act["downbeat"], np.float16))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--md5", default="")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--stems4", default="")
    ap.add_argument("--ckpt", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.stems4:
        import torch
        return run_app(a.stems4, a.ckpt, a.out, a.device if torch.cuda.is_available() else "cpu")
    import torch
    from allin1.models import load_pretrained_model
    from allin1.postprocessing import postprocess_metrical_structure, postprocess_functional_structure
    from allin1.helpers import compute_activations
    os.makedirs(OUT, exist_ok=True)
    model = load_pretrained_model("harmonix-all", cache_dir=HF_CACHE, device=a.device)
    todo = [a.md5] if a.md5 else sorted(x for x in os.listdir(STEMS6)
                                        if os.path.exists(os.path.join(STEMS6, x, "meta.json")))
    done = 0
    for md5 in todo:
        if os.path.exists(os.path.join(OUT, md5 + ".json")) and not a.md5:
            continue
        if a.limit and done >= a.limit:
            break
        spec = spectrogram(load4(os.path.join(STEMS6, md5)))
        with torch.no_grad():
            logits = model(torch.from_numpy(spec).unsqueeze(0).to(a.device))
            met = postprocess_metrical_structure(logits, model.cfg)
            seg = postprocess_functional_structure(logits, model.cfg)
            act = compute_activations(logits)
        res = {"beats": met["beats"], "downbeats": met["downbeats"], "beat_positions": met["beat_positions"],
               "segments": [{"start": s.start, "end": s.end, "label": s.label} for s in seg]}
        json.dump(res, open(os.path.join(OUT, md5 + ".json"), "w"))
        np.savez_compressed(os.path.join(OUT, md5 + ".npz"), **{k: np.asarray(v, np.float16) for k, v in act.items()})
        done += 1
        print(md5, len(res["beats"]), "beats", len(res["segments"]), "segments", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
