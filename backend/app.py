import os
import sys

# Ensure pythonw has valid stdout/stderr to prevent exit on print/logging
if sys.stdout is None:
    try:
        sys.stdout = open(os.path.join(os.path.dirname(__file__), "server.log"), "a", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.path.join(os.path.dirname(__file__), "server.log"), "a", encoding="utf-8")
    except Exception:
        pass

import json
import sqlite3
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from tab_scanner import scan_all_tabs, DB_PATH
from gp_process_monitor import monitor
from practice_history import get_heatmap_data, get_practice_stats, get_recent_sessions
from clipboard_service import copy_file_to_clipboard
from netease_service import search_song_info, download_cover_image, decrypt_ncm, COVERS_DIR, AUDIO_DIR
from tab_fetcher import search_online_tabs, search_audio_sources, import_tab_to_library, transcribe_audio_to_library
from cover_generator import generate_procedural_jacket
from backing_track_enhancer import check_backing_track_status, run_uvr_gui_or_cli, link_audio_to_song_folder

app = FastAPI(title="BassStation API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

class PracticeStartRequest(BaseModel):
    song_id: str

class ExportClipboardRequest(BaseModel):
    song_id: str

class SearchTabRequest(BaseModel):
    title: str

class ConfirmTabRequest(BaseModel):
    title: str
    artist: str
    franchise: Optional[str] = "BanG Dream!"
    download_url: Optional[str] = None

class SearchAudioRequest(BaseModel):
    title: str

class TranscribeAudioRequest(BaseModel):
    title: str
    artist: str
    audio_id: Optional[int] = None
    album: Optional[str] = ""

# 1. Song List & Search
@app.get("/api/songs")
def get_songs(
    query: Optional[str] = None,
    band: Optional[str] = None,
    tier: Optional[str] = None,
    is_5string: Optional[bool] = None,
    has_backing: Optional[bool] = None,
    min_level: Optional[int] = None,
    max_level: Optional[int] = None
):
    songs = scan_all_tabs()
    filtered = songs

    if query:
        q = query.lower().strip()
        filtered = [s for s in filtered if q in s["title"].lower() or q in s["artist"].lower() or q in s["folder_name"].lower()]

    if band and band != "ALL":
        if band.upper() == "CLASSICAL":
            filtered = [s for s in filtered if "古典" in s["franchise"] or "classical" in s["franchise"].lower() or "bach" in s["artist"].lower() or "古典" in s["folder_name"]]
        else:
            filtered = [s for s in filtered if band.lower() in s["artist"].lower() or band.lower() in s["franchise"].lower() or band.lower() in s["folder_name"].lower()]

    if tier and tier != "ALL":
        filtered = [s for s in filtered if s["tier"] == tier]

    if is_5string is not None:
        filtered = [s for s in filtered if s["is_5string"] == is_5string]

    if has_backing is not None:
        filtered = [s for s in filtered if s["has_backing_track"] == has_backing]

    if min_level is not None:
        filtered = [s for s in filtered if s["level"] >= min_level]

    if max_level is not None:
        filtered = [s for s in filtered if s["level"] <= max_level]

    try:
        from performance_evaluator import get_song_best_score
        for s in filtered:
            best = get_song_best_score(s["id"])
            if best:
                s["best_score"] = best["overall_score"]
                s["best_grade"] = best["grade"]
            else:
                s["best_score"] = None
                s["best_grade"] = None
    except Exception:
        pass

    return {"total": len(filtered), "songs": filtered}

# 2. Song Detail
@app.get("/api/songs/{song_id}")
def get_song(song_id: str):
    songs = scan_all_tabs()
    match = next((s for s in songs if s["id"] == song_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Song not found")

    backing_info = check_backing_track_status(match["gp_path"])
    match["backing_info"] = backing_info
    return match

# 3. Practice Flow
@app.post("/api/practice/start")
def start_practice(req: PracticeStartRequest):
    songs = scan_all_tabs()
    match = next((s for s in songs if s["id"] == req.song_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Song not found")

    status = monitor.start_practice(match)
    return status

@app.post("/api/practice/stop")
def stop_practice():
    result = monitor.stop_practice()
    return {"status": "stopped", "session": result}

@app.get("/api/practice/status")
def practice_status():
    return monitor.get_status()

@app.post("/api/practice/clear-completed")
def clear_completed():
    monitor.clear_completed()
    return {"status": "cleared"}

# 4. Statistics & Calendar Heatmap
@app.get("/api/practice/heatmap")
def practice_heatmap():
    return get_heatmap_data()

@app.get("/api/practice/stats")
def practice_stats():
    return get_practice_stats()

@app.get("/api/practice/recent")
def practice_recent():
    return get_recent_sessions()

# 5. Export to Tablet (Clipboard / PDF Stream)
@app.post("/api/export/clipboard")
def export_clipboard(req: ExportClipboardRequest):
    songs = scan_all_tabs()
    match = next((s for s in songs if s["id"] == req.song_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Song not found")

    pdf_path = match.get("pdf_path")
    if pdf_path and os.path.exists(pdf_path):
        target_file = pdf_path
        file_type = "PDF"
    elif match.get("gp_path") and os.path.exists(match.get("gp_path")):
        target_file = match.get("gp_path")
        file_type = "GP 乐谱"
    else:
        raise HTTPException(status_code=400, detail="该曲目目录下未找到可导出的乐谱文件")

    success = copy_file_to_clipboard(target_file)
    if not success:
        raise HTTPException(status_code=500, detail="注入剪贴板失败")

    return {
        "success": True,
        "file_type": file_type,
        "message": f"{file_type} 已复制到剪贴板！你现在可以在微信/QQ聊天框直接按 Ctrl+V 发送给平板。",
        "pdf_path": target_file,
        "filename": os.path.basename(target_file)
    }

@app.get("/api/export/pdf/{song_id}")
def view_pdf(song_id: str):
    songs = scan_all_tabs()
    match = next((s for s in songs if s["id"] == song_id), None)
    if not match or not match.get("pdf_path") or not os.path.exists(match["pdf_path"]):
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(match["pdf_path"], media_type="application/pdf")

# 6. Cover Art (NEVER FAILS: Fallback to procedural BanG Dream jacket)
@app.get("/api/cover/{song_id}")
def get_cover(song_id: str):
    local_cover = os.path.join(COVERS_DIR, f"{song_id}.jpg")
    if os.path.exists(local_cover) and os.path.getsize(local_cover) > 500:
        return FileResponse(local_cover)

    songs = scan_all_tabs()
    match = next((s for s in songs if s["id"] == song_id), None)
    if match:
        # Try NetEase
        info = search_song_info(f"{match['artist']} {match['title']}")
        if info and info.get("cover_url"):
            cached_path = download_cover_image(song_id, info["cover_url"])
            if cached_path and os.path.exists(cached_path):
                return FileResponse(cached_path)
        # Fallback to procedural jacket
        gen_path = generate_procedural_jacket(song_id, match["title"], match["artist"], match["folder_name"])
        return FileResponse(gen_path)

    # General default jacket
    gen_path = generate_procedural_jacket(song_id, "BanG Dream!", "Poppin'Party", "default")
    return FileResponse(gen_path)

# 7. UNIFIED ADD SONG WORKFLOW (新增曲目)
@app.post("/api/add-song/search-tabs")
def search_tabs(req: SearchTabRequest):
    return search_online_tabs(req.title)

@app.post("/api/add-song/confirm-tab")
def confirm_tab(req: ConfirmTabRequest):
    tab_info = import_tab_to_library(
        title=req.title,
        artist=req.artist,
        franchise=req.franchise,
        download_url=req.download_url
    )
    # Rescan library to register new tab
    scan_all_tabs(force_rescan=True)
    return {"status": "success", "song": tab_info}

@app.post("/api/add-song/search-audio")
def search_audio(req: SearchAudioRequest):
    return search_audio_sources(req.title)

TRANSCRIBE_PROGRESS: Dict[str, Any] = {
    "status": "idle",
    "step": "",
    "percent": 0,
    "start_time": 0.0,
    "eta_seconds": 0,
    "eta_text": ""
}

@app.get("/api/add-song/transcribe-status")
def get_transcribe_status():
    import time
    if TRANSCRIBE_PROGRESS.get("status") == "running":
        start_t = float(TRANSCRIBE_PROGRESS.get("start_time", 0.0))
        pct = int(TRANSCRIBE_PROGRESS.get("percent", 0))
        if start_t > 0 and pct >= 15:
            elapsed = max(1.0, time.time() - start_t)
            projected_total = elapsed / (pct / 100.0)
            eta = max(0, int(round(projected_total - elapsed)))
            TRANSCRIBE_PROGRESS["eta_seconds"] = eta
            TRANSCRIBE_PROGRESS["eta_text"] = f"约 {eta} 秒" if eta > 0 else "即将完成"
        elif pct >= 100 or TRANSCRIBE_PROGRESS.get("status") == "done":
            TRANSCRIBE_PROGRESS["eta_seconds"] = 0
            TRANSCRIBE_PROGRESS["eta_text"] = "已就绪"
        else:
            TRANSCRIBE_PROGRESS["eta_seconds"] = 30
            TRANSCRIBE_PROGRESS["eta_text"] = "计算中"
    elif TRANSCRIBE_PROGRESS.get("status") == "done":
        TRANSCRIBE_PROGRESS["eta_seconds"] = 0
        TRANSCRIBE_PROGRESS["eta_text"] = "已就绪"
    return TRANSCRIBE_PROGRESS

@app.post("/api/add-song/transcribe-from-audio")
def transcribe_audio(req: TranscribeAudioRequest):
    # Pipeline: create entry with audio backing and AI bass score template
    tab_info = transcribe_audio_to_library(
        title=req.title,
        artist=req.artist,
        audio_id=req.audio_id,
        album=req.album or "AI 扒谱原声"
    )
    scan_all_tabs(force_rescan=True)
    return {
        "status": "success",
        "message": f"已导入: {req.title}",
        "song": tab_info
    }

@app.post("/api/add-song/transcribe-local-audio")
def transcribe_local_audio(
    file: UploadFile = File(...),
    title: str = Form(""),
    artist: str = Form(""),
    franchise: str = Form("BanG Dream!")
):
    from tab_fetcher import transcribe_local_audio_to_library, clean_name
    from netease_service import decrypt_ncm, AUDIO_DIR

    filename = file.filename
    clean_base = clean_name(os.path.splitext(filename)[0])
    final_title = title.strip() or clean_base
    final_artist = artist.strip() or "BanG Dream!"
    final_franchise = franchise.strip() or "BanG Dream!"

    temp_path = os.path.join(AUDIO_DIR, filename)
    content = file.file.read()
    with open(temp_path, "wb") as f:
        f.write(content)

    audio_source_path = temp_path
    if filename.lower().endswith(".ncm"):
        try:
            res = decrypt_ncm(temp_path)
            audio_source_path = res.get("path", temp_path)
        except Exception as e:
            print(f"Error decrypting NCM: {e}")

    tab_info = transcribe_local_audio_to_library(
        raw_audio_path=audio_source_path,
        title=final_title,
        artist=final_artist,
        franchise=final_franchise
    )
    scan_all_tabs(force_rescan=True)
    return {
        "status": "success",
        "message": f"已导入: {final_title}",
        "song": tab_info
    }

# 8. Upload & Decrypt NCM / MP3
@app.post("/api/audio/upload")
async def upload_audio(file: UploadFile = File(...)):
    filename = file.filename
    temp_path = os.path.join(AUDIO_DIR, filename)
    with open(temp_path, "wb") as f:
        content = await file.read()
        f.write(content)

    if filename.lower().endswith(".ncm"):
        res = decrypt_ncm(temp_path)
        return {"status": "decrypted", "result": res}
    return {"status": "saved", "path": temp_path}

@app.post("/api/add-song/upload-local")
async def upload_local_tab(
    file: UploadFile = File(...),
    title: str = Form(...),
    artist: str = Form(...),
    franchise: str = Form("BanG Dream!")
):
    from tab_fetcher import clean_name
    from backing_track_enhancer import auto_ensure_backing_track

    safe_title = clean_name(title)
    safe_artist = clean_name(artist or "BanG Dream!")
    safe_franchise = clean_name(franchise or "BanG Dream!")

    folder_name = f"{safe_title} _ {safe_artist} _ {safe_franchise}"
    folder_path = os.path.join(r"E:\BassStation\tabs", folder_name)
    os.makedirs(folder_path, exist_ok=True)

    filename = file.filename
    content = await file.read()
    dest_path = os.path.join(folder_path, filename)
    with open(dest_path, "wb") as f:
        f.write(content)

    # Ensure companion files (both GP and PDF)
    if filename.lower().endswith(('.gp', '.gp5', '.gpx')):
        from pdf_generator import generate_bass_tab_pdf
        pdf_path = os.path.join(folder_path, f"[BASS TAB] {safe_title}.pdf")
        if not os.path.exists(pdf_path):
            generate_bass_tab_pdf(pdf_path, safe_title, safe_artist, safe_franchise)
    elif filename.lower().endswith('.pdf'):
        from gp_builder import create_clean_gp_project
        gp_path = os.path.join(folder_path, f"[BASS TAB] {safe_title}.gp")
        if not os.path.exists(gp_path):
            create_clean_gp_project(gp_path, safe_title, safe_artist, safe_franchise)

    # Automatically inspect and fetch backing track
    backing_info = auto_ensure_backing_track(folder_path, safe_title, safe_artist)

    # Rescan library
    scan_all_tabs(force_rescan=True)
    return {
        "status": "success",
        "message": f"已导入: {filename}",
        "folder": folder_name
    }

# 9. Launch UVR5 Tool
@app.post("/api/tools/launch-uvr")
def launch_uvr():
    success = run_uvr_gui_or_cli()
    if not success:
        raise HTTPException(status_code=404, detail="UVR executable not found on E drive")
    return {"status": "launched"}

# 10. Automated Backing Track Synchronization
class AudioAlignRequest(BaseModel):
    song_id: str

class AudioAdjustOffsetRequest(BaseModel):
    song_id: str
    delta_ms: float

@app.post("/api/audio/auto-align")
def api_auto_align(req: AudioAlignRequest):
    songs = scan_all_tabs()
    target = next((s for s in songs if s["id"] == req.song_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="曲目未找到")
    from audio_aligner import auto_align_backing_track
    res = auto_align_backing_track(target["gp_path"])
    return res

@app.post("/api/audio/adjust-offset")
def api_adjust_offset(req: AudioAdjustOffsetRequest):
    songs = scan_all_tabs()
    target = next((s for s in songs if s["id"] == req.song_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="曲目未找到")
    from audio_aligner import adjust_backing_track_offset
    res = adjust_backing_track_offset(target["gp_path"], req.delta_ms)
    return res

@app.get("/api/audio/offset/{song_id}")
def api_get_audio_offset(song_id: str):
    songs = scan_all_tabs()
    target = next((s for s in songs if s["id"] == song_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="曲目未找到")
    import zipfile, re
    padding = 0
    try:
        with zipfile.ZipFile(target["gp_path"], "r") as z:
            xml = z.read("Content/score.gpif").decode("utf-8", errors="ignore")
            m = re.search(r"<FramePadding>(.*?)</FramePadding>", xml)
            if m:
                padding = int(m.group(1))
    except Exception:
        pass
    from audio_aligner import get_score_first_sounding_offset, get_audio_sample_rate, resolve_audio_file
    score_info = get_score_first_sounding_offset(target["gp_path"])
    sr = 44100
    audio_path = resolve_audio_file(target["gp_path"], extract_if_embedded=True)
    if audio_path and os.path.exists(audio_path):
        sr = get_audio_sample_rate(audio_path)

    return {
        "success": True,
        "current_padding": padding,
        "current_offset_ms": round((padding / float(sr)) * 1000, 1),
        "score_onset_ms": score_info.get("first_sounding_offset_ms", 0.0),
        "is_weak_beat": score_info.get("is_weak_beat", False),
        "tempo": score_info.get("tempo", target.get("tempo", 120.0)),
        "sample_rate": sr
    }

# 11. Audio Practice Performance Evaluation & Grading
@app.post("/api/practice/evaluate")
async def api_evaluate_performance(
    song_id: str = Form(...),
    audio: UploadFile = File(...)
):
    songs = scan_all_tabs()
    match = next((s for s in songs if s["id"] == song_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="曲目未找到")

    eval_dir = r"E:\BassStation\cache\evaluations"
    os.makedirs(eval_dir, exist_ok=True)
    temp_path = os.path.join(eval_dir, f"{song_id}_{audio.filename}")
    with open(temp_path, "wb") as f:
        content = await audio.read()
        f.write(content)

    # the evaluator needs librosa, which only the Python311 ML environment has
    import subprocess
    from backing_track_enhancer import PY311
    proc = subprocess.run([PY311, os.path.join(os.path.dirname(os.path.abspath(__file__)), "performance_evaluator.py"),
                           temp_path, song_id, match["gp_path"]],
                          capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))
    lines = [l for l in proc.stdout.splitlines() if l.startswith("{")]
    result = json.loads(lines[-1]) if lines else {"success": False, "error": "分析失败"}

    if result.get("success"):
        try:
            from gamification_service import update_song_mastery
            mastery_info = update_song_mastery(song_id, result.get("overall_score", 0))
            result["mastery"] = mastery_info
        except Exception as e:
            print(f"Error updating mastery: {e}")

    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except Exception:
            pass

    return result

@app.get("/api/practice/score/{song_id}")
def api_get_score(song_id: str):
    from performance_evaluator import get_song_best_score
    score = get_song_best_score(song_id)
    return score or {}

class EquipBadgeRequest(BaseModel):
    badge_id: str

class ToggleBackingModeRequest(BaseModel):
    song_id: str
    mode: str

# 12. Gamification, Badges & Titles
@app.get("/api/gamification/badges")
def api_get_badges():
    from gamification_service import get_user_badges
    return get_user_badges()

@app.post("/api/gamification/equip")
def api_equip_badge(req: EquipBadgeRequest):
    from gamification_service import equip_badge
    success = equip_badge(req.badge_id)
    return {"success": success}

@app.get("/api/gamification/mastery")
def api_get_mastery():
    from gamification_service import get_all_mastery
    return get_all_mastery()

# 13. Backing Track Mode Switch (Original vs No-Bass)
@app.post("/api/audio/toggle-track")
def api_toggle_track_mode(req: ToggleBackingModeRequest):
    songs = scan_all_tabs()
    target = next((s for s in songs if s["id"] == req.song_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="曲目未找到")
    from backing_track_enhancer import switch_backing_track_mode
    return switch_backing_track_mode(target["gp_path"], req.mode)

@app.get("/api/audio/status/{song_id}")
def api_audio_status(song_id: str):
    songs = scan_all_tabs()
    target = next((s for s in songs if s["id"] == song_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="曲目未找到")
    from backing_track_enhancer import check_backing_track_status
    return check_backing_track_status(target["gp_path"])

# 14. Virtual Fretboard Analysis
@app.get("/api/song/fretboard/{song_id}")
def api_get_fretboard(song_id: str):
    songs = scan_all_tabs()
    target = next((s for s in songs if s["id"] == song_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="曲目未找到")
    from fretboard_service import get_song_fretboard_stats
    return get_song_fretboard_stats(target["gp_path"])

# 12. Mount Frontend Static Build with No-Cache on HTML/Favicon
FRONTEND_DIST = r"E:\BassStation\frontend\dist"

@app.get("/")
@app.get("/index.html")
def serve_index():
    index_file = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.exists(index_file):
        response = FileResponse(index_file)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    return JSONResponse({"error": "frontend dist not found"}, status_code=404)

@app.get("/favicon.svg")
def serve_favicon():
    fav_file = os.path.join(FRONTEND_DIST, "favicon.svg")
    if not os.path.exists(fav_file):
        fav_file = r"E:\BassStation\frontend\public\favicon.svg"
    if os.path.exists(fav_file):
        response = FileResponse(fav_file, media_type="image/svg+xml")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
    raise HTTPException(status_code=404)

if os.path.exists(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=False)
