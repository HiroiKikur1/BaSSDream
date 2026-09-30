"""Chord recognition (lv-chordia, Jiang et al. ISMIR 2019, large vocabulary) for every separated recording (Layer 1).

Two inputs are compared: the full mix (sum of the 6 stems) and the harmonic stems (guitar + piano + other + bass,
no drums / vocals). Runs in cache/venv_allin1.

cache/venv_allin1/Scripts/python.exe -m bassnet.chords_run [--limit N]
-> cache/bassnet/chords/<md5>_{mix,harm}.json  [{start_time, end_time, chord}]
"""
import argparse
import contextlib
import io
import json
import os

import numpy as np

STEMS6 = os.path.join("D:" + os.sep, "BassData", "stems6")
OUT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "chords")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--orig", action="store_true", help="the original recordings inside the tabs -> <md5>_orig.json")
    a = ap.parse_args()
    if a.orig:
        return run_orig()
    import soundfile as sf
    from lv_chordia.chord_recognition import chord_recognition
    os.makedirs(OUT, exist_ok=True)
    tmp = os.path.join(OUT, "_tmp.wav")
    done = 0
    for md5 in sorted(os.listdir(STEMS6)):
        d = os.path.join(STEMS6, md5)
        if not os.path.exists(os.path.join(d, "meta.json")):
            continue
        if all(os.path.exists(os.path.join(OUT, f"{md5}_{k}.json")) for k in ("mix", "harm")):
            continue
        if a.limit and done >= a.limit:
            break
        ys = {s: sf.read(os.path.join(d, s + ".flac"), dtype="float32", always_2d=True)[0].mean(1)
              for s in ("guitar", "piano", "other", "bass", "drums", "vocals")}
        n = min(len(v) for v in ys.values())
        mixes = {"harm": ys["guitar"][:n] + ys["piano"][:n] + ys["other"][:n] + ys["bass"][:n],
                 "mix": sum(v[:n] for v in ys.values())}
        for k, y in mixes.items():
            sf.write(tmp, y / max(1.0, float(np.abs(y).max())), 44100)
            with contextlib.redirect_stdout(io.StringIO()):
                r = chord_recognition(audio_path=tmp, chord_dict_name="submission")
            json.dump(r, open(os.path.join(OUT, f"{md5}_{k}.json"), "w"))
        done += 1
        print(md5, flush=True)
    if os.path.exists(tmp):
        os.remove(tmp)


def run_orig():
    """lv-chordia on the original recording (what the app has before any separation)."""
    import re
    import subprocess
    import sys
    import zipfile
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from bassnet.build_stems import FFMPEG, build_index
    from bassnet.gpif_parser import parse_gp
    from lv_chordia.chord_recognition import chord_recognition
    os.makedirs(OUT, exist_ok=True)
    seen = set()
    for gp, v in build_index().items():
        md5 = v["md5"]
        out = os.path.join(OUT, f"{md5}_orig.json")
        if md5 in seen or os.path.exists(out):
            continue
        seen.add(md5)
        try:
            info = parse_gp(gp)
            ext = os.path.splitext(info.audio_asset)[1]
            src = os.path.join(OUT, "_src" + (ext if re.fullmatch(r"\.\w{2,4}", ext) else ".bin"))
            open(src, "wb").write(zipfile.ZipFile(gp).read(info.audio_asset))
            wav = os.path.join(OUT, "_orig.wav")
            subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", src, "-ar", "44100", "-ac", "1", wav])
            with contextlib.redirect_stdout(io.StringIO()):
                r = chord_recognition(audio_path=wav, chord_dict_name="submission")
            json.dump(r, open(out, "w"))
            os.remove(src)
            print(md5, flush=True)
        except Exception as e:
            print("fail", md5, e, flush=True)


if __name__ == "__main__":
    main()
