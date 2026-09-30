"""All six BS-Roformer-SW stems (bass, drums, guitar, piano, vocals, other) for every library recording.

Song-level understanding (beats/downbeats/sections from drums+vocals, key/chords from the harmonic stems) needs
the whole band, not only the bass. Output (outside the BassStation folder, which has a size budget):
    D:\\BassData\\stems6\\<audio md5>\\<stem>.flac   (44.1 kHz stereo, the format song-structure models expect)
Resumable: songs whose six files exist are skipped.

python -m bassnet.build_stems6 [--limit N]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.build_stems import ROOT, FFMPEG, build_index  # noqa: E402
from bassnet.gpif_parser import parse_gp  # noqa: E402

OUT = os.path.join("D:" + os.sep, "BassData", "stems6")
TMP = os.path.join("D:" + os.sep, "BassData", "_tmp6")
STEMS = ("bass", "drums", "guitar", "piano", "vocals", "other")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--overlap", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    index = build_index()
    todo = {}
    for gp, v in index.items():
        d = os.path.join(OUT, v["md5"])
        if not all(os.path.exists(os.path.join(d, s + ".flac")) for s in STEMS):
            todo.setdefault(v["md5"], (gp, v))
    print(f"{len(set(v['md5'] for v in index.values()))} recordings, {len(todo)} to separate", flush=True)
    from audio_separator.separator import Separator
    sep = Separator(model_file_dir=os.path.join("E:" + os.sep, "BassStation", "cache", "models"), output_dir=TMP,
                    output_format="FLAC", use_soundfile=True, log_level=30, use_autocast=True)
    sep.load_model("BS-Roformer-SW.ckpt")
    if getattr(sep, "model_instance", None) is not None:
        sep.model_instance.overlap = a.overlap
    done = 0
    for k, (md5, (gp, v)) in enumerate(todo.items()):
        if a.limit and done >= a.limit:
            break
        t0 = time.time()
        info = parse_gp(gp)
        ext = os.path.splitext(info.audio_asset)[1]
        src = os.path.join(TMP, md5 + "_src" + (ext if re.fullmatch(r"\.\w{2,4}", ext) else ".bin"))
        wav = os.path.join(TMP, md5 + ".wav")
        with zipfile.ZipFile(gp) as z:
            open(src, "wb").write(z.read(info.audio_asset))
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", src, "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le",
                        wav])
        d = os.path.join(OUT, md5)
        os.makedirs(d, exist_ok=True)
        try:
            outs = sep.separate(wav)
            for o in outs:
                p = o if os.path.isabs(o) else os.path.join(TMP, o)
                name = os.path.basename(p).lower()
                stem = next((s for s in STEMS if f"({s})" in name), None)
                if stem:
                    os.replace(p, os.path.join(d, stem + ".flac"))
            json.dump({"gp": gp, "md5": md5, "overlap": a.overlap}, open(os.path.join(d, "meta.json"), "w",
                                                                        encoding="utf8"), ensure_ascii=False)
        except Exception as e:
            print("sep fail", gp, e, flush=True)
        for f in os.listdir(TMP):
            try:
                os.remove(os.path.join(TMP, f))
            except OSError:
                pass
        done += 1
        print(f"[{k + 1}/{len(todo)}] {os.path.basename(os.path.dirname(gp))[:40]} {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
