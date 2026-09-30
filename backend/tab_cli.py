import os
import sys
import json
import contextlib

try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import paths  # noqa: E402

from tab_fetcher import search_online_tabs, search_audio_sources, search_bilibili_references, import_tab_to_library, transcribe_audio_to_library, clean_name
from backing_track_enhancer import auto_ensure_backing_track
from tab_scanner import scan_all_tabs, DB_PATH
from gp_audio_linker import inject_backing_track_to_gp
from pdf_generator import generate_bass_tab_pdf
import sqlite3
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus")

@contextlib.contextmanager
def silence_stdout():
    old_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = old_stdout

def update_song_db_field(song_id: str, field: str, value: str):
    if not os.path.exists(DB_PATH):
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(f"UPDATE song_cache SET {field} = ? WHERE id = ?", (value, song_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB update error: {e}", file=sys.stderr)

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No action specified"}))
        return

    action = sys.argv[1]

    try:
        if action == "search-tabs":
            keyword = sys.argv[2] if len(sys.argv) > 2 else ""
            with silence_stdout():
                res = search_online_tabs(keyword)
            print(json.dumps(res, ensure_ascii=False))

        elif action == "search-audio":
            keyword = sys.argv[2] if len(sys.argv) > 2 else ""
            with silence_stdout():
                res = search_audio_sources(keyword)
            print(json.dumps(res, ensure_ascii=False))

        elif action == "search-all":
            keyword = sys.argv[2] if len(sys.argv) > 2 else ""
            with silence_stdout():
                tabs_res = search_online_tabs(keyword)
                tab_list = tabs_res.get("results", []) if isinstance(tabs_res, dict) else (tabs_res if isinstance(tabs_res, list) else [])
                audio_res = search_audio_sources(keyword)
                audio_list = audio_res.get("results", []) if isinstance(audio_res, dict) else (audio_res if isinstance(audio_res, list) else [])
                bili_res = search_bilibili_references(keyword)
                bili_list = bili_res if isinstance(bili_res, list) else []
            print(json.dumps({
                "tabs": tab_list,
                "audio": audio_list,
                "bilibili": bili_list
            }, ensure_ascii=False))

        elif action == "search-bilibili":
            keyword = sys.argv[2] if len(sys.argv) > 2 else ""
            with silence_stdout():
                res = search_bilibili_references(keyword)
            print(json.dumps(res, ensure_ascii=False))

        elif action == "confirm-tab":
            title = sys.argv[2] if len(sys.argv) > 2 else ""
            artist = sys.argv[3] if len(sys.argv) > 3 else "BanG Dream!"
            url = sys.argv[4] if len(sys.argv) > 4 else ""
            
            tab_info = None
            err_msg = ""
            with silence_stdout():
                try:
                    # backing track and PDF are already produced inside import_tab_to_library
                    tab_info = import_tab_to_library(title=title, artist=artist, franchise="BanG Dream!", download_url=url)
                    scan_all_tabs(force_rescan=True)
                except Exception as ex:
                    err_msg = str(ex)

            if tab_info:
                print(json.dumps({"status": "success", "song": tab_info}, ensure_ascii=False))
            else:
                print(json.dumps({"status": "error", "message": err_msg or "导入失败"}, ensure_ascii=False))

        elif action == "sync-check":
            # 核对版 saved in Guitar Pro -> practice file (score from the check file, practice audio kept)
            from check_version import sync_check_to_practice
            with silence_stdout():
                res = sync_check_to_practice(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
                if res.get("ok"):
                    scan_all_tabs(force_rescan=True)
            print(json.dumps({"status": "success" if res.get("ok") else "error", **res}, ensure_ascii=False))

        elif action == "transcribe-audio":
            title = sys.argv[2] if len(sys.argv) > 2 else ""
            artist = sys.argv[3] if len(sys.argv) > 3 else "BanG Dream!"
            audio_id = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].isdigit() else None
            album = sys.argv[5] if len(sys.argv) > 5 else "AI 原声"
            
            with silence_stdout():
                tab_info = transcribe_audio_to_library(title=title, artist=artist, audio_id=audio_id, album=album)
                folder_path = tab_info.get("folder_path")
                if folder_path and os.path.exists(folder_path):
                    pdf_path = os.path.join(folder_path, f"[BASS TAB] {clean_name(title)}.pdf")
                    if not os.path.exists(pdf_path):
                        try:
                            generate_bass_tab_pdf(pdf_path, title, artist, "AI Transcription", gp_path=tab_info.get("gp_path"))
                        except Exception:
                            pass
                scan_all_tabs(force_rescan=True)
            print(json.dumps({"status": "success", "song": tab_info}, ensure_ascii=False))

        elif action == "import-local" and sys.argv[2].lower().endswith(AUDIO_EXTS):
            # local recording -> BassNet transcription into a new library folder
            src_file = sys.argv[2]
            title = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(os.path.basename(src_file))[0]
            artist = sys.argv[4] if len(sys.argv) > 4 else "BanG Dream!"
            with silence_stdout():
                tab_info = transcribe_audio_to_library(title=title, artist=artist, album="AI 原声", local_audio=src_file)
                scan_all_tabs(force_rescan=True)
            print(json.dumps({"status": "success", "song": tab_info}, ensure_ascii=False))

        elif action == "import-local":
            src_file = sys.argv[2]
            title = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(os.path.basename(src_file))[0]
            artist = sys.argv[4] if len(sys.argv) > 4 else "BanG Dream!"
            franchise = "BanG Dream!"
            
            safe_title = clean_name(title)
            safe_artist = clean_name(artist)
            folder_name = f"{safe_title} _ {safe_artist} _ {franchise}"
            folder_path = os.path.join(paths.TABS, folder_name)
            os.makedirs(folder_path, exist_ok=True)

            dest_path = os.path.join(folder_path, os.path.basename(src_file))
            if os.path.abspath(src_file) != os.path.abspath(dest_path):
                import shutil
                shutil.copy2(src_file, dest_path)

            pdf_target_path = ""
            with silence_stdout():
                if src_file.lower().endswith(('.gp', '.gp5', '.gpx')):
                    pdf_target_path = os.path.join(folder_path, f"[BASS TAB] {safe_title}.pdf")
                    if not os.path.exists(pdf_target_path):
                        try:
                            generate_bass_tab_pdf(pdf_target_path, safe_title, safe_artist, franchise, gp_path=dest_path)
                        except Exception:
                            pass
                elif src_file.lower().endswith('.pdf'):
                    pdf_target_path = dest_path
                    from gp_builder import create_clean_gp_project
                    gp_path = os.path.join(folder_path, f"[BASS TAB] {safe_title}.gp")
                    if not os.path.exists(gp_path):
                        try:
                            create_clean_gp_project(gp_path, safe_title, safe_artist, franchise)
                        except Exception:
                            pass

                try:
                    auto_ensure_backing_track(folder_path, safe_title, safe_artist)
                except Exception:
                    pass

                scan_all_tabs(force_rescan=True)
            print(json.dumps({"status": "success", "folder": folder_name, "pdf_path": pdf_target_path}, ensure_ascii=False))

        elif action == "ensure-pdf":
            song_id = sys.argv[2]
            title = sys.argv[3] if len(sys.argv) > 3 else ""
            artist = sys.argv[4] if len(sys.argv) > 4 else ""
            gp_path = sys.argv[5] if len(sys.argv) > 5 else ""

            folder = os.path.dirname(gp_path) if gp_path and os.path.exists(gp_path) else ""
            if not folder or not os.path.exists(folder):
                for f in os.listdir(paths.TABS):
                    if title and title in f:
                        folder = os.path.join(paths.TABS, f)
                        break

            pdf_file = None
            with silence_stdout():
                if folder and os.path.exists(folder):
                    # Search for any authentic PDF in folder
                    pdf_candidates = [
                        os.path.join(folder, f) for f in os.listdir(folder)
                        if f.lower().endswith('.pdf') and os.path.getsize(os.path.join(folder, f)) >= 30000
                    ]
                    if pdf_candidates:
                        # Pick the latest modified authentic PDF
                        pdf_candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                        pdf_file = pdf_candidates[0]
                    elif gp_path and os.path.exists(gp_path):
                        # Auto-engrave authentic vector score using Verovio
                        target_pdf = os.path.join(folder, f"[BASS TAB] {clean_name(title)}.pdf")
                        try:
                            generate_bass_tab_pdf(target_pdf, title, artist, gp_path=gp_path)
                            if os.path.exists(target_pdf) and os.path.getsize(target_pdf) >= 10000:
                                pdf_file = target_pdf
                        except Exception as e:
                            pass

                if pdf_file and os.path.exists(pdf_file):
                    update_song_db_field(song_id, "pdf_path", pdf_file)
            
            if pdf_file and os.path.exists(pdf_file):
                print(json.dumps({"status": "success", "pdf_path": pdf_file}, ensure_ascii=False))
            else:
                print(json.dumps({"status": "not_found", "error": "No companion PDF found"}, ensure_ascii=False))

        elif action == "ensure-backing":
            song_id = sys.argv[2]
            title = sys.argv[3] if len(sys.argv) > 3 else ""
            artist = sys.argv[4] if len(sys.argv) > 4 else ""
            gp_path = sys.argv[5] if len(sys.argv) > 5 else ""

            folder = os.path.dirname(gp_path) if gp_path and os.path.exists(gp_path) else ""
            if not folder or not os.path.exists(folder):
                for f in os.listdir(paths.TABS):
                    if title and title in f:
                        folder = os.path.join(paths.TABS, f)
                        break

            res = None
            with silence_stdout():
                if folder and os.path.exists(folder):
                    res = auto_ensure_backing_track(folder, title, artist)
                    if res.get("has_backing"):
                        if res.get("gp_path"):
                            update_song_db_field(song_id, "gp_path", res["gp_path"])
                        update_song_db_field(song_id, "audio_path", res.get("audio_path") or "")
                        update_song_db_field(song_id, "has_backing_track", "1")

            if res and res.get("has_backing"):
                print(json.dumps({"status": "success", "audio_path": res.get("audio_path") or "", "gp_path": res.get("gp_path") or ""}, ensure_ascii=False))
            else:
                print(json.dumps({"error": "Failed to fetch backing track"}, ensure_ascii=False))

        elif action == "auto-align":
            gp_path = sys.argv[2] if len(sys.argv) > 2 else ""
            audio_path = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
            
            with silence_stdout():
                from audio_aligner import auto_align_backing_track
                res = auto_align_backing_track(gp_path, audio_path)
            
            if res.get("success"):
                print(json.dumps({
                    "status": "success",
                    "offset_ms": res.get("aligned_offset_ms", 0.0),
                    "tempo": res.get("tempo", 120.0),
                    "score_onset_ms": res.get("score_onset_ms", 0.0),
                    "audio_onset_ms": res.get("audio_onset_ms", 0.0)
                }, ensure_ascii=False))
            else:
                print(json.dumps({"error": res.get("error", "对齐失败")}, ensure_ascii=False))

        elif action == "adjust-offset":
            gp_path = sys.argv[2] if len(sys.argv) > 2 else ""
            delta_ms = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
            audio_path = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
            
            with silence_stdout():
                from audio_aligner import adjust_backing_track_offset
                res = adjust_backing_track_offset(gp_path, delta_ms, audio_path)
            
            if res.get("success"):
                print(json.dumps({
                    "status": "success",
                    "offset_ms": res.get("current_offset_ms", 0.0)
                }, ensure_ascii=False))
            else:
                print(json.dumps({"error": res.get("error", "调整偏移失败")}, ensure_ascii=False))
        else:
            print(json.dumps({"error": f"Unknown action {action}"}))

    except Exception as ex:
        print(json.dumps({"error": str(ex)}, ensure_ascii=False))

if __name__ == "__main__":
    main()
