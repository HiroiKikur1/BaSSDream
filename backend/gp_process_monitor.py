import os
import time
import datetime
import subprocess
import threading
import ctypes
from ctypes import wintypes
from typing import Optional, Dict, Any
from practice_history import record_session

GP_EXECUTABLE = r"C:\Program Files\Arobas Music\Guitar Pro 8\GuitarPro.exe"

TH32CS_SNAPPROCESS = 0x00000002

class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', ctypes.c_char * 260)
    ]

def is_guitar_pro_running() -> bool:
    """Fast non-blocking check if GuitarPro.exe is currently active on Windows."""
    try:
        h = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if h == -1:
            return False
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        found = False
        if ctypes.windll.kernel32.Process32First(h, ctypes.byref(pe)):
            while True:
                name = pe.szExeFile.decode('utf-8', errors='ignore').lower()
                if 'guitarpro' in name:
                    found = True
                    break
                if not ctypes.windll.kernel32.Process32Next(h, ctypes.byref(pe)):
                    break
        ctypes.windll.kernel32.CloseHandle(h)
        return found
    except Exception:
        return False

class GPProcessMonitor:
    def __init__(self):
        # Must be RLock so same thread can call get_status inside start_practice/stop_practice without deadlock
        self.lock = threading.RLock()
        self.active_session: Optional[Dict[str, Any]] = None
        self.last_completed: Optional[Dict[str, Any]] = None
        self.monitor_thread: Optional[threading.Thread] = None

    def start_practice(self, song: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            # If already running for this exact song, return existing status
            if self.active_session and self.active_session.get("is_running"):
                if self.active_session.get("song_id") == song.get("id"):
                    return self.get_status()
                # If switching songs, stop previous first
                self.stop_practice()

            gp_path = song.get("gp_path", "")
            if not os.path.exists(gp_path):
                raise FileNotFoundError(f"GP file not found: {gp_path}")

            abs_gp_path = os.path.abspath(gp_path)
            if os.path.exists(GP_EXECUTABLE):
                try:
                    # Windows Registry native format: GuitarPro.exe --open "%1"
                    # --open forces Guitar Pro 8 to bypass welcome screen and open directly into score view!
                    subprocess.Popen([GP_EXECUTABLE, "--open", abs_gp_path])
                except Exception as e:
                    print(f"[GP Monitor] Popen failed: {e}, falling back to startfile")
                    try:
                        os.startfile(abs_gp_path)
                    except Exception as err:
                        raise RuntimeError(f"Could not open GP file: {err}")
            else:
                try:
                    os.startfile(abs_gp_path)
                except Exception as e:
                    raise RuntimeError(f"Could not open file: {e}")

            start_time = datetime.datetime.now()
            self.active_session = {
                "song_id": song.get("id"),
                "song_title": song.get("title"),
                "artist": song.get("artist"),
                "level": song.get("level"),
                "tier": song.get("tier"),
                "gp_path": gp_path,
                "start_time": start_time.isoformat(),
                "start_timestamp": time.time(),
                "is_running": True
            }

            # Spawn monitor thread to watch Guitar Pro lifetime
            self.monitor_thread = threading.Thread(target=self._watch_guitar_pro, daemon=True)
            self.monitor_thread.start()

            return self.get_status()

    def _watch_guitar_pro(self):
        # Give Guitar Pro 3 seconds to spin up
        time.sleep(3.0)

        while True:
            time.sleep(1.5)
            with self.lock:
                if not self.active_session or not self.active_session.get("is_running"):
                    break
            if not is_guitar_pro_running():
                # Process was closed by user in Windows
                self.stop_practice()
                break

    def stop_practice(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            if not self.active_session or not self.active_session.get("is_running"):
                return self.last_completed

            duration = max(1.0, time.time() - self.active_session["start_timestamp"])
            session_id = record_session(
                song_id=self.active_session["song_id"],
                song_title=self.active_session["song_title"],
                artist=self.active_session["artist"],
                duration_seconds=duration,
                start_time=self.active_session["start_time"]
            )

            completed = {
                "id": session_id,
                "song_id": self.active_session["song_id"],
                "song_title": self.active_session["song_title"],
                "artist": self.active_session["artist"],
                "level": self.active_session.get("level"),
                "tier": self.active_session.get("tier"),
                "duration_seconds": round(duration, 1),
                "duration_minutes": round(duration / 60.0, 1),
                "completed_at": datetime.datetime.now().isoformat()
            }

            self.active_session["is_running"] = False
            self.last_completed = completed
            self.active_session = None
            return completed

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            if not self.active_session or not self.active_session.get("is_running"):
                return {
                    "is_practicing": False,
                    "active_session": None,
                    "last_completed": self.last_completed
                }

            current_elapsed = time.time() - self.active_session["start_timestamp"]
            return {
                "is_practicing": True,
                "active_session": {
                    "song_id": self.active_session["song_id"],
                    "song_title": self.active_session["song_title"],
                    "artist": self.active_session["artist"],
                    "level": self.active_session.get("level"),
                    "tier": self.active_session.get("tier"),
                    "start_time": self.active_session["start_time"],
                    "elapsed_seconds": round(current_elapsed, 1),
                    "elapsed_minutes": round(current_elapsed / 60.0, 1)
                },
                "last_completed": self.last_completed
            }

    def clear_completed(self):
        with self.lock:
            self.last_completed = None

monitor = GPProcessMonitor()
