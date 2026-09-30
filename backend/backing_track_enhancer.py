import os
import shutil
import zipfile
import subprocess
from typing import Dict, Any, Optional

UVR_DIR = r"E:\BassStation\tools\Ultimate Vocal Remover"
UVR_EXE = os.path.join(UVR_DIR, "UVR.exe")

def check_backing_track_status(gp_path: str) -> Dict[str, Any]:
    """Checks if GP file contains an embedded backing track or sibling audio file."""
    folder = os.path.dirname(gp_path)
    audio_files = [f for f in os.listdir(folder) if f.lower().endswith(('.mp3', '.wav', '.flac', '.m4a'))]
    has_local_audio = len(audio_files) > 0
    has_nobass = os.path.exists(os.path.join(folder, "backing_nobass.mp3")) or any("nobass" in f.lower() for f in audio_files)

    has_embedded = False
    is_nobass_active = False
    if gp_path.endswith('.gp') and os.path.exists(gp_path):
        try:
            with zipfile.ZipFile(gp_path, 'r') as z:
                has_embedded = any(
                    item.filename.startswith('Content/Assets/') and item.file_size > 100000
                    for item in z.infolist()
                )
                if "Content/score.gpif" in z.namelist():
                    gpif = z.read("Content/score.gpif").decode("utf-8", errors="ignore")
                    if "backing_nobass" in gpif:
                        is_nobass_active = True
        except Exception:
            pass

    return {
        "has_backing_track": has_embedded or (has_local_audio and gp_path.endswith('.gp5')),
        "has_nobass_track": has_nobass,
        "current_mode": "nobass" if is_nobass_active else "original",
        "is_embedded": has_embedded,
        "local_audio_files": audio_files,
        "primary_audio_path": os.path.join(folder, audio_files[0]) if audio_files else ""
    }

def link_audio_to_song_folder(audio_path: str, song_folder: str, make_minus_one: bool = False) -> str:
    """Copies audio file into song directory as backing.mp3."""
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    ext = os.path.splitext(audio_path)[1]
    target_name = "backing_nobass" + ext if make_minus_one else "backing" + ext
    target_path = os.path.join(song_folder, target_name)
    shutil.copy2(audio_path, target_path)
    return target_path

def run_uvr_gui_or_cli():
    """Opens UVR tool interface from E drive for advanced vocal/bass separation."""
    if os.path.exists(UVR_EXE):
        subprocess.Popen([UVR_EXE], cwd=UVR_DIR)
        return True
    return False

PY311 = r"C:\Users\hongw\AppData\Local\Programs\Python\Python311\python.exe"
ALIGN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bassnet", "score_align.py")


def _align_to_derived(src_gp: str, audio: str) -> Dict[str, Any]:
    """Runs score/audio alignment (Python 3.11) and writes '<name> [伴奏].gp' when the audio matches."""
    import json
    out = os.path.splitext(src_gp)[0] + " [伴奏].gp"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        r = subprocess.run([PY311, ALIGN_SCRIPT, "--gp", src_gp, "--audio", audio, "--out", out],
                           capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=900, env=env)
        for line in r.stdout.splitlines():
            if line.startswith("__ALIGN_JSON__"):
                return json.loads(line[len("__ALIGN_JSON__"):])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "alignment produced no result"}


def auto_ensure_backing_track(folder_path: str, title: str, artist: str, audio_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Background backing-track pipeline. Original scores are never modified:
    1. A score that already embeds audio is used as-is.
    2. Otherwise candidate recordings (local, NetEase id, searches) are aligned to the score
       bar-by-bar; only a recording that passes the pitch-match check is written into a
       derived '<name> [伴奏].gp' with per-bar sync points.
    """
    from gp_guard import has_embedded_audio, is_derived
    from tab_fetcher import get_audio_file_duration, download_audio_from_netease, search_and_download_full_audio

    gps = sorted(f for f in os.listdir(folder_path) if f.lower().endswith(".gp"))
    for f in gps:
        p = os.path.join(folder_path, f)
        if has_embedded_audio(p):
            return {"has_backing": True, "status": "embedded", "audio_path": "", "gp_path": p, "filename": f}

    if not gps:
        # no score yet (new song for transcription): just fetch the recording
        dst = os.path.join(folder_path, "backing.mp3")
        if os.path.exists(dst):
            return {"has_backing": True, "status": "ready", "audio_path": dst, "filename": "backing.mp3"}
        ok = download_audio_from_netease(audio_id, dst) if audio_id else False
        if not ok:
            ok = search_and_download_full_audio(title, dst, prefer_artist=artist)
        if ok and os.path.exists(dst):
            return {"has_backing": True, "status": "downloaded", "audio_path": dst, "filename": "backing.mp3",
                    "duration": get_audio_file_duration(dst)}
        return {"has_backing": False, "status": "no_source_found", "audio_path": None, "filename": None}

    sources = [f for f in gps if not is_derived(os.path.join(folder_path, f))]
    if not sources:
        return {"has_backing": False, "status": "no_gp_score", "audio_path": None, "filename": None}
    src_gp = os.path.join(folder_path, sorted(sources, key=lambda f: (f.lower().endswith("_n.gp"), f))[0])

    score_dur = 0.0
    try:
        from tab_scanner import parse_gp_file
        score_dur = float((parse_gp_file(src_gp) or {}).get("duration") or 0.0)
    except Exception:
        pass

    def try_audio(path):
        res = _align_to_derived(src_gp, path)
        if res.get("ok"):
            return {"has_backing": True, "status": "aligned", "audio_path": path, "gp_path": res.get("out"),
                    "filename": os.path.basename(path), "quality": res.get("quality"),
                    "duration": get_audio_file_duration(path)}
        print(f"[backing] rejected {os.path.basename(path)} (match {res.get('quality')})")
        return None

    local = [f for f in os.listdir(folder_path) if f.lower().endswith((".mp3", ".wav", ".flac", ".m4a"))]
    for f in local:
        got = try_audio(os.path.join(folder_path, f))
        if got:
            return got

    attempts = []
    if audio_id:
        attempts.append(lambda dst: download_audio_from_netease(audio_id, dst, expected_duration=score_dur))
    attempts.append(lambda dst: search_and_download_full_audio(title, dst, prefer_artist=artist, expected_duration=score_dur))
    if artist:
        attempts.append(lambda dst: search_and_download_full_audio(f"{artist} {title}", dst, expected_duration=score_dur))
    for k, fetch in enumerate(attempts):
        dst = os.path.join(folder_path, f"backing_cand{k}.mp3")
        try:
            ok = fetch(dst)
        except Exception:
            ok = False
        if ok and os.path.exists(dst):
            got = try_audio(dst)
            if got:
                final = os.path.join(folder_path, "backing.mp3")
                if not os.path.exists(final):
                    shutil.move(dst, final)
                    got["audio_path"] = final
                return got
            try:
                os.remove(dst)
            except OSError:
                pass

    return {"has_backing": False, "status": "no_matching_source", "audio_path": None, "filename": None}


def switch_backing_track_mode(gp_path: str, mode: str) -> Dict[str, Any]:
    """
    Switches backing track in GP8 score between 'original' (backing.mp3) and 'nobass' (backing_nobass.mp3).
    """
    if not os.path.exists(gp_path):
        return {"success": False, "error": "GP文件未找到"}

    folder = os.path.dirname(gp_path)
    target_audio = "backing_nobass.mp3" if mode == "nobass" else "backing.mp3"
    target_path = os.path.join(folder, target_audio)

    if mode == "nobass" and not os.path.exists(target_path):
        return {"success": False, "error": "尚未生成纯净消贝伴奏 (backing_nobass.mp3)"}
    from gp_guard import is_protected
    if is_protected(gp_path):
        return {"success": False, "error": "原版谱面受保护"}

    try:
        entries = {}
        with zipfile.ZipFile(gp_path, "r") as z:
            for item in z.infolist():
                entries[item.filename] = z.read(item.filename)

        if "Content/score.gpif" not in entries:
            return {"success": False, "error": "曲谱中缺少 score.gpif"}

        gpif = entries["Content/score.gpif"].decode("utf-8", errors="ignore")
        if mode == "nobass":
            gpif = gpif.replace("/backing.mp3", "/backing_nobass.mp3")
            gpif = gpif.replace("\\backing.mp3", "\\backing_nobass.mp3")
        else:
            gpif = gpif.replace("/backing_nobass.mp3", "/backing.mp3")
            gpif = gpif.replace("\\backing_nobass.mp3", "\\backing.mp3")

        entries["Content/score.gpif"] = gpif.encode("utf-8")

        with zipfile.ZipFile(gp_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for name, data in entries.items():
                z.writestr(name, data)

        return {"success": True, "mode": mode, "audio_file": target_audio}
    except PermissionError:
        return {"success": False, "error": "曲谱正被 Guitar Pro 8 占用，请在 GP 中关闭该标签页后重试"}
    except Exception as e:
        return {"success": False, "error": str(e)}


