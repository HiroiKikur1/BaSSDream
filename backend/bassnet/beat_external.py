"""Beats / downbeats from Beat This! (CPJKU, MIT license; checkpoint "final0", auto-downloaded) on the full mix.

python -m bassnet.beat_external --split val|test     -> cache/bassnet/eval/beatthis/<md5>.json
`track(audio_path)` is the entry point used by the pipeline.
"""
import paths
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT = paths.cache(r"bassnet\eval\beatthis")
MIX_DIR = paths.cache(r"bassnet\mixes")
_MODEL = None


def _model(dev="cuda"):
    global _MODEL
    if _MODEL is None:
        from beat_this.inference import File2Beats
        _MODEL = File2Beats(checkpoint_path="final0", device=dev, dbn=False)
    return _MODEL


def track(audio_path: str, dev: str = "cuda"):
    beats, downs = _model(dev)(audio_path)
    return [float(x) for x in beats], [float(x) for x in downs]


def mix_for(meta) -> str:
    from bassnet.gpif_parser import parse_gp, extract_audio
    md5 = os.path.basename(meta["npz"])[:-4]
    os.makedirs(MIX_DIR, exist_ok=True)
    for ext in (".mp3", ".wav", ".ogg", ".flac", ".m4a"):
        p = os.path.join(MIX_DIR, md5 + ext)
        if os.path.exists(p):
            return p
    info = parse_gp(meta["gp"])
    # asset names can contain full-width characters: whitelist the extension instead of splitext
    low = (info.audio_asset or "").lower()
    ext = next((e for e in (".mp3", ".wav", ".ogg", ".flac", ".m4a") if low.endswith(e) or e in low), ".mp3")
    p = os.path.join(MIX_DIR, md5 + ext)
    return p if extract_audio(meta["gp"], info, p) else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val", choices=["val", "test"])
    a = ap.parse_args()
    if a.split == "val":
        from bassnet.tune_decode import val_metas
        metas = val_metas()
    else:
        from bassnet.eval_e2e import test_metas
        metas = test_metas()
    os.makedirs(OUT, exist_ok=True)
    for m in metas:
        md5 = os.path.basename(m["npz"])[:-4]
        out = os.path.join(OUT, md5 + ".json")
        if os.path.exists(out):
            continue
        mix = mix_for(m)
        if not mix:
            print("no audio", md5)
            continue
        b, d = track(mix)
        json.dump({"beats": b, "downbeats": d}, open(out, "w"))
        print(md5, len(b), len(d), flush=True)


if __name__ == "__main__":
    main()
