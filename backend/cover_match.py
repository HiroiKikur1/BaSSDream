"""Album art lookup that only accepts a NetEase result whose song name matches the title AND whose credited artists
include the song's band / artist. The old bulk fetch took the first search hit, which produced live photos,
unrelated albums and a generic "4th Anniversary" image for many songs."""
import re
import sys
import unicodedata
from typing import Dict, List, Optional

from library_meta import BANDS, find_bands
from netease_service import session

ARTIST_ALIASES: Dict[str, List[str]] = {
    "MyGO!!!!!": ["mygo"],
    "Ave Mujica": ["avemujica"],
    "Poppin'Party": ["poppinparty"],
    "Pastel＊Palettes": ["pastelpalettes"],
    "ハロー、ハッピーワールド！": ["ハローハッピーワールド", "hellohappyworld"],
    "RAISE A SUILEN": ["raiseasuilen"],
    "25時、ナイトコードで。": ["25時ナイトコードで", "25ji"],
    "Leo/need": ["leoneed"],
    "MORE MORE JUMP！": ["moremorejump"],
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("堕", "墮").replace("壊", "壞")
    return re.sub(r"[\s\W_]+", "", s)


def _title_core(name: str) -> str:
    """NetEase names carry suffixes like "(Live)" / " - TV size" / "（game size）"."""
    name = re.split(r"\s[-–]\s", name)[0]
    name = re.sub(r"[（(\[【][^）)\]】]*[）)\]】]", "", name)
    return _norm(name)


def _artist_keys(artist: str) -> List[str]:
    keys = []
    for band in find_bands(artist):
        keys.append(_norm(band))
        keys += ARTIST_ALIASES.get(band, [])
    if not keys:
        keys = [_norm(a) for a in re.split(r"\s*[×&/、,]\s*", artist) if len(_norm(a)) >= 2]
    return keys


def search(keyword: str, limit: int = 20) -> List[dict]:
    try:
        r = session.post("https://music.163.com/api/cloudsearch/pc",
                         data={"s": keyword, "type": 1, "limit": limit, "offset": 0}, timeout=8)
        return r.json().get("result", {}).get("songs", []) or []
    except Exception:
        return []


def find_cover(title: str, artist: str, version: str = "") -> Optional[dict]:
    """Best validated match: {"url", "album", "name", "artists"} or None."""
    t = _norm(title)
    keys = _artist_keys(artist)
    if not t or not keys:
        return None
    seen, hits = set(), []
    for kw in (f"{title} {artist}", title):
        for s in search(kw):
            if s.get("id") in seen:
                continue
            seen.add(s.get("id"))
            names = [_norm(a.get("name", "")) for a in s.get("ar", [])]
            if _title_core(s.get("name", "")) != t:
                continue
            if not any(k and any(k in n or n in k for n in names if n) for k in keys):
                continue
            al = s.get("al", {}) or {}
            if not al.get("picUrl"):
                continue
            hits.append({"url": al["picUrl"], "album": al.get("name", ""), "name": s.get("name", ""),
                         "artists": " / ".join(a.get("name", "") for a in s.get("ar", []))})
        if hits:
            break
    if not hits:
        return None
    # the song's own single / album beats compilations and live albums unless the score is a live version
    live = version.startswith("Live")

    def rank(h: dict) -> tuple:
        al = _norm(h["album"])
        return (0 if t in al else 1, 0 if (("live" in al) == live) else 1)
    return sorted(hits, key=rank)[0]


def fetch_cover(song_id: str, title: str, artist: str, folder: str = "", version: str = "", replace: bool = False) -> str:
    """Saves verified album art to cache/covers/<id>.jpg, or the logo fallback when nothing verifiable is found."""
    import os
    from cover_generator import COVERS_DIR, generate_procedural_jacket
    path = os.path.join(COVERS_DIR, f"{song_id}.jpg")
    if not replace and os.path.exists(path) and os.path.getsize(path) > 1024:
        return path
    m = find_cover(title, artist, version)
    if m:
        try:
            r = session.get(m["url"] + "?param=600y600", timeout=10)
            if r.status_code == 200 and len(r.content) > 2048:
                with open(path, "wb") as f:
                    f.write(r.content)
                return path
        except Exception:
            pass
    if replace and os.path.exists(path):
        os.remove(path)
    return generate_procedural_jacket(song_id, title, artist, folder)


if __name__ == "__main__":
    import sqlite3
    ids = sys.argv[1:]
    con = sqlite3.connect(r"E:\BassStation\backend\data.db")
    for sid in ids:
        row = con.execute("SELECT title, artist, version FROM song_cache WHERE id = ?", (sid,)).fetchone()
        if not row:
            continue
        m = find_cover(*row)
        print(sid, row[0], "|", row[1], "->", (m["album"] + " | " + m["artists"]) if m else "NO MATCH", flush=True)
