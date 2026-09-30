"""Separates the bass stem of every GP-embedded backing track in tabs/ (dataset building).

Output: cache/bassnet/stems/<md5>.flac (mono 22050 Hz) + cache/bassnet/index.json
"""
import glob
import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.gpif_parser import parse_gp  # noqa: E402

TABS = r"E:\BassStation\tabs"
ROOT = r"E:\BassStation\cache\bassnet"
STEMS = os.path.join(ROOT, "stems")
TMP = os.path.join(ROOT, "_tmp")
FFMPEG = r"E:\BassStation\tools\Ultimate Vocal Remover\ffmpeg.exe"
os.environ["PATH"] = os.path.dirname(FFMPEG) + os.pathsep + os.environ.get("PATH", "")


def is_ai_transcription(gp: str) -> bool:
    """BaSSDream's own transcriptions (Tabber = BaSSDream) and their 核对版 companions."""
    if "[核对版]" in os.path.basename(gp):
        return True
    try:
        with zipfile.ZipFile(gp) as z:
            head = z.read("Content/score.gpif")[:20000].decode("utf-8", "ignore")
        i = head.find("<Tabber>")
        return i >= 0 and "BaSSDream" in head[i:head.find("</Tabber>", i)]
    except Exception:
        return False


def build_index():
    idx_path = os.path.join(ROOT, "index.json")
    index = json.load(open(idx_path, encoding="utf8")) if os.path.exists(idx_path) else {}
    for gp in sorted(glob.glob(os.path.join(TABS, "*", "*.gp"))):
        if gp in index:
            continue
        if is_ai_transcription(gp):
            continue   # never train on our own output (would teach the model its own mistakes)
        try:
            info = parse_gp(gp)
        except Exception as e:
            print("parse fail", gp, e)
            continue
        if not info.audio_asset or not info.has_sync or len(info.notes) < 50:
            continue
        with zipfile.ZipFile(gp) as z:
            data = z.read(info.audio_asset)
        md5 = hashlib.md5(data).hexdigest()[:16]
        index[gp] = {"md5": md5, "ext": os.path.splitext(info.audio_asset)[1], "n_notes": len(info.notes),
                     "tuning": info.tuning}
    json.dump(index, open(idx_path, "w", encoding="utf8"), ensure_ascii=False, indent=1)
    return index


def main():
    os.makedirs(STEMS, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    index = build_index()
    todo = {}
    for gp, v in index.items():
        out = os.path.join(STEMS, v["md5"] + ".flac")
        if not os.path.exists(out):
            todo.setdefault(v["md5"], (gp, v))
    print(f"{len(index)} scores, {len(set(v['md5'] for v in index.values()))} unique audio, {len(todo)} to separate", flush=True)

    from audio_separator.separator import Separator
    sep = Separator(model_file_dir=r"E:\BassStation\cache\models", output_dir=TMP, output_format="WAV",
                    use_soundfile=True, log_level=30, use_autocast=True)
    sep.load_model("BS-Roformer-SW.ckpt")
    if getattr(sep, "model_instance", None) is not None:
        sep.model_instance.overlap = 2

    for k, (md5, (gp, v)) in enumerate(todo.items()):
        t0 = time.time()
        ext = os.path.splitext(v["ext"])[1] if "." in v["ext"] else v["ext"]
        ext = "." + ext.rsplit(".", 1)[-1] if ext.rsplit(".", 1)[-1].isalnum() else ".bin"
        for known in (".mp3", ".wav", ".ogg", ".m4a", ".flac"):
            if v["ext"].lower().endswith(known):
                ext = known
        src = os.path.join(TMP, md5 + "_src" + ext)
        wav = os.path.join(TMP, md5 + ".wav")
        info = parse_gp(gp)
        with zipfile.ZipFile(gp) as z:
            open(src, "wb").write(z.read(info.audio_asset))
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", src, "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", wav])
        try:
            outs = sep.separate(wav)
            bass = [o for o in outs if "(bass)" in os.path.basename(o).lower()]
            if bass:
                bp = bass[0] if os.path.isabs(bass[0]) else os.path.join(TMP, bass[0])
                subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", bp, "-ar", "22050", "-ac", "1",
                                os.path.join(STEMS, md5 + ".flac")])
        except Exception as e:
            print("sep fail", gp, e, flush=True)
        for f in os.listdir(TMP):
            try:
                os.remove(os.path.join(TMP, f))
            except OSError:
                pass
        print(f"[{k + 1}/{len(todo)}] {os.path.basename(os.path.dirname(gp))[:40]} {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
