"""Machine paths, shared with the WPF client (src-native/Services/AppPaths.cs).

Everything derives from the project root (the folder that holds backend/), so a copy of the project works
wherever it sits. bassdream.json in the root overrides single entries (see bassdream.example.json);
BASSDREAM_CONFIG points to another file.
"""
import json
import os
import sys

BACKEND = os.path.dirname(os.path.abspath(__file__))

_cfg = {}
_cfg_path = os.environ.get("BASSDREAM_CONFIG") or os.path.join(os.path.dirname(BACKEND), "bassdream.json")
try:
    with open(_cfg_path, encoding="utf-8") as _f:
        _cfg = json.load(_f)
except (OSError, ValueError):
    pass

ROOT = _cfg.get("root") or os.path.dirname(BACKEND)


def _path(key, *default):
    v = _cfg.get(key)
    return v if v else os.path.join(ROOT, *default)


TABS = _path("tabs", "tabs")
CACHE = _path("cache", "cache")
SCORES = _path("scores", "scores")
ASSETS = os.path.join(ROOT, "assets")
DB = _path("db", "backend", "data.db")
UVR_DIR = _path("uvr", "tools", "Ultimate Vocal Remover")
FFMPEG = _cfg.get("ffmpeg") or os.path.join(UVR_DIR, "ffmpeg.exe")
# purchased original scores (never written to; see gp_guard)
ORIGINALS = _cfg.get("originals") or os.path.join(os.path.expanduser("~"), "Desktop", "曲谱")
# the machine-learning interpreter (torch, librosa, audio_separator); the current one when not configured
_ML_DEFAULT = os.path.join(os.path.expanduser("~"), r"AppData\Local\Programs\Python\Python311\python.exe")
PY311 = _cfg.get("python_ml") or (_ML_DEFAULT if os.path.exists(_ML_DEFAULT) else sys.executable)


def cache(*parts):
    return os.path.join(CACHE, *parts)
