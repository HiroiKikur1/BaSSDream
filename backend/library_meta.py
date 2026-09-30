"""Song identity for the library list: title, artist, franchise, version label and 4/5-string grouping.

Folder names come from the tab seller and use "_" for illegal characters and "BanG Dream!" as a catch-all
suffix, so the GP file's own Title/Artist are preferred and the franchise is decided by band membership only.
"""
import json
import os
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

OVERRIDES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library_overrides.json")

BANGDREAM, SEKAI, CLASSICAL = "BanG Dream!", "Project SEKAI", "古典练习曲"

# (canonical name, franchise, alias regex). Aliases are matched case-insensitively.
BANDS: List[Tuple[str, str, str]] = [
    ("Poppin'Party", BANGDREAM, r"poppin['’]?\s?party"),
    ("Afterglow", BANGDREAM, r"afterglow"),
    ("Pastel＊Palettes", BANGDREAM, r"pastel\s?[＊*_ ]\s?palettes"),
    ("Roselia", BANGDREAM, r"roselia"),
    ("ハロー、ハッピーワールド！", BANGDREAM, r"ハロー、?ハッピーワールド[！!]?|hello,?\s?happy\s?world!?"),
    ("Morfonica", BANGDREAM, r"morfonica"),
    ("RAISE A SUILEN", BANGDREAM, r"raise\s?a\s?suilen"),
    ("MyGO!!!!!", BANGDREAM, r"mygo!*"),
    ("Ave Mujica", BANGDREAM, r"ave\s?mu(?:ji|ij)ca"),
    ("CRYCHIC", BANGDREAM, r"crychic"),
    ("夢ノ結唱", BANGDREAM, r"夢ノ結唱"),
    ("millsage", BANGDREAM, r"millsage"),
    ("一家Dumb Rock!", BANGDREAM, r"一家\s?dumb\s?rock!?"),
    ("夢限大みゅーたいぷ", BANGDREAM, r"夢限大みゅーたいぷ"),
    ("Leo/need", SEKAI, r"leo\s?[/／_]?\s?need"),
    ("MORE MORE JUMP！", SEKAI, r"more\s?more\s?jump\s?[！!]?"),
    ("Vivid BAD SQUAD", SEKAI, r"vivid\s?bad\s?squad"),
    ("ワンダーランズ×ショウタイム", SEKAI, r"ワンダーランズ\s?[×x]\s?ショウタイム|wonderlands\s?[×x]\s?showtime"),
    ("25時、ナイトコードで。", SEKAI, r"25時、?\s?ナイトコードで。?"),
]

# characters / unit names that pin the franchise without being a band credit
FRANCHISE_HINTS: List[Tuple[str, str]] = [
    (BANGDREAM, r"卒業生|花女|羽丘|優雅なティータイム|友希那|千聖|日菜|香澄|パレオ|立希|愛音|そよ|\b燈\b|×燈|×蘭|クールな奇人"),
    (SEKAI, r"project\s?sek|プロジェクトセカイ|宵崎|東雲|桐谷\s?遥|日野森|星乃\s?一歌"),
]

OTHER_FRANCHISES: List[Tuple[str, str]] = [
    ("Love Live!", r"lovelive|ラブライブ|虹ヶ咲|虹咲|aqours"),
    ("Blue Archive", r"blue archive|蔚藍檔案"),
    ("Heaven Burns Red", r"heaven burns red"),
    ("Girls Band Cry", r"girls band cry|無刺有刺|トゲナシトゲアリ"),
]

# version label rules, first match wins (applied to folder + GP subtitle/album + GP filename, title removed)
VERSION_RULES: List[Tuple[str, str]] = [
    ("Acoustic", r"acoustic"),
    ("Easy", r"\beasy\b|簡單版|简单版"),
    ("Live", r"(?<!love)live|現場版|现场版|(?<!ラブ)ライブ|rausch und|公演"),
    ("先行", r"先行"),
    ("Cover", r"cover"),
    ("TV", r"tv\s?(?:op\s?)?size|tvop"),
    ("Game", r"game\s?size|gamesize|(?<![a-z])game(?![a-z])"),
    ("Short", r"short|pv\s?size|試聴|试听|短版"),
    ("Full", r"(?<![a-z])full(?![a-z])|完整版"),
]
EPISODE_RE = re.compile(r"第\s*(\d{1,2})\s*[集話话]|#\s*(\d{1,2})(?!\d)")

STRING_TOKEN_RE = re.compile(r"(?<![0-9])([45])\s?(?:弦|st\.?|-string)(?:\s?ver\.?)?(?:版)?", re.IGNORECASE)

_overrides: Optional[Dict[str, Dict[str, str]]] = None


def _load_overrides() -> Dict[str, Dict[str, str]]:
    global _overrides
    if _overrides is None:
        try:
            with open(OVERRIDES_PATH, encoding="utf-8") as f:
                _overrides = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
        except FileNotFoundError:
            _overrides = {}
    return _overrides


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _has_cjk(s: str) -> bool:
    return any(unicodedata.east_asian_width(c) in "WF" for c in s)


def read_gp_meta(gp_path: str) -> Dict[str, object]:
    """Title / SubTitle / Artist / Album and the bass track's string count from a GP8 file."""
    out: Dict[str, object] = {"title": "", "subtitle": "", "artist": "", "album": "", "strings": 0}
    if not gp_path.lower().endswith(".gp"):
        return out
    try:
        with zipfile.ZipFile(gp_path) as z:
            raw = z.read("Content/score.gpif")
    except Exception:
        return out
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # some files embed a mis-encoded audio file name; replace bad bytes and XML-illegal control chars
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw.decode("utf-8", errors="replace"))
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return out
    score = root.find("Score")
    if score is not None:
        for key, tag in (("title", "Title"), ("subtitle", "SubTitle"), ("artist", "Artist"), ("album", "Album")):
            # a few files carry a doubled CDATA ("]]><![CDATA[") that ElementTree keeps as text
            out[key] = _collapse((score.findtext(tag) or "").replace("]]><![CDATA[", ""))
    tracks = root.findall(".//Tracks/Track")
    best = 0
    for t in tracks:
        pitches = (t.findtext(".//Property[@name='Tuning']/Pitches") or "").split()
        name = (t.findtext("Name") or "").lower()
        inst = (t.findtext(".//Property[@name='Tuning']/Instrument") or "").lower()
        if len(pitches) in (4, 5, 6) and ("bass" in name or "bass" in inst or "贝斯" in name or len(tracks) == 1):
            best = len(pitches)
            break
        if len(pitches) in (4, 5) and not best:
            best = len(pitches)
    out["strings"] = best
    return out


def find_bands(*texts: str) -> List[str]:
    """Canonical band names in order of first appearance across the given texts."""
    hits: List[Tuple[int, int, str]] = []
    for ti, text in enumerate(texts):
        for name, _, pat in BANDS:
            m = re.search(pat, text or "", re.IGNORECASE)
            if m and name not in (h[2] for h in hits):
                hits.append((ti, m.start(), name))
    hits.sort()
    return [h[2] for h in hits]


def normalize_artist(s: str) -> str:
    s = _collapse(s)
    for name, _, pat in BANDS:
        s = re.sub(pat, name, s, flags=re.IGNORECASE)
    s = re.sub(r"\s*\((?:cv|CV)[.:：\s][^)]*\)", "", s)          # 譜風(CV.羊宮妃那) -> 譜風
    s = re.sub(r"\s*covere?d?\s+by\s+\S+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+[xX]\s+|\s*×\s*", " × ", s)
    if _has_cjk(s):
        s = re.sub(r"\s*[-–]\s*[A-Za-z][A-Za-z .'\-]*$", "", s)      # あたらよ - Atarayo
        s = re.sub(r"\s*\(([A-Za-z][A-Za-z .'\-]*)\)", "", s)        # ヨルシカ (Yorushika)
    s = re.sub(r"(?<=[^\x00-\x7f\u00d7&\uff06/\u3001]) +(?=[^\x00-\x7f\u00d7&\uff06/\u3001])", "", _collapse(s))
    return _collapse(s.replace("、 ", "、").strip(" /,、"))


def _folder_title(folder: str) -> str:
    f = folder.replace("Mas_uerade", "Mas?uerade").replace("Re_uest", "Re?uest")
    m = re.search(r"^[『「]\s*(.*?)\s*[』」]", f)
    title = m.group(1) if m else re.split(r"\s*[_／/]\s+|\s+[_／/]\s*", f)[0]
    title = STRING_TOKEN_RE.sub(" ", title)
    title = re.sub(r"\b(live|studio|short|acoustic)\s*ver\.?|\bver\.?(?=\s|$)|\b(tv|tvop|game|pv)\s*size\b|\bfull\b|"
                   r"現場版|先行|短版改編|完整版改編|簡單版", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\s\d+(st|nd|rd|th)\b.*$", "", title)                   # "Henceforth 4th LIVE"
    title = re.sub(r"\bLIVE\b", " ", title)
    return _collapse(title).rstrip(" ._")


def clean_title(title: str) -> str:
    t = _collapse(title)
    t = re.sub(r"[_\s]*(live|studio|short|acoustic|full)\s*ver\.?$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*[（(](?:[^()（）]*ver\.?)[)）]$", "", t, flags=re.IGNORECASE)   # (パラレルver.)
    t = re.sub(r"^(Symbol [IV]+)\s*[:：]\s*", r"\1 : ", t)
    return _collapse(t)


def _prefer_cased(a: str, b: str) -> str:
    """Same text in two spellings (folder vs GP): keep the one with more capitals (Our Carol, needLe, ZEAL)."""
    return a if sum(c.isupper() for c in a) >= sum(c.isupper() for c in b) else b


def _version_label(text: str) -> str:
    low = text.lower()
    for label, pat in VERSION_RULES:
        if re.search(pat, low, re.IGNORECASE):
            return label
    m = EPISODE_RE.search(text)
    if m:
        return f"#{m.group(1) or m.group(2)}"
    return ""


def _strip_title(text: str, *titles: str) -> str:
    variants = set()
    for t in titles:
        variants.update({t, t.strip("-_ .!?「」『』\"'")})
    for t in sorted(variants, key=len, reverse=True):
        if t and len(t) >= 2:
            text = re.sub(re.escape(t), " ", text, flags=re.IGNORECASE)
    return text


def group_key(title: str, artist: str, version: str) -> str:
    t = unicodedata.normalize("NFKC", title).lower()
    t = re.sub(r"[\s\W_]+", "", t)
    bands = find_bands(artist)
    a = bands[0] if bands else unicodedata.normalize("NFKC", artist).lower().replace(" ", "")
    return f"{t}|{a}|{version}"


def describe(folder: str, gp_path: str) -> Dict[str, object]:
    """Returns title, artist, franchise, version, is_5string (None if unknown) for one library folder."""
    meta = read_gp_meta(gp_path) if gp_path else {"title": "", "subtitle": "", "artist": "", "album": "", "strings": 0}
    gp_title, gp_artist = str(meta["title"]), str(meta["artist"])
    gp_file = os.path.splitext(os.path.basename(gp_path or ""))[0].replace("[BASS TAB]", "").replace("[伴奏]", "")
    low_folder = folder.lower()
    is_classical = "古典" in folder or "classical" in low_folder

    f_title = _folder_title(folder)
    if is_classical or not gp_title:
        title = f_title
    else:
        title = clean_title(gp_title)
        if f_title.lower().startswith(title.lower()):
            title = _prefer_cased(f_title[:len(title)], title)

    folder_parts = [p.strip() for p in re.split(r"\s*[_／/]\s+|\s+[_／/]\s*", folder) if p.strip()]
    folder_rest = " ".join(folder_parts[1:]) if len(folder_parts) > 1 else re.sub(r"^[『「].*?[』」]", "", folder)
    bands = find_bands(folder_rest, gp_artist, folder)
    everything = f"{folder} {gp_artist} {meta['subtitle']} {meta['album']}"

    franchise = ""
    if is_classical:
        franchise = CLASSICAL
    elif bands:
        franchise = next(fr for name, fr, _ in BANDS if name == bands[0])
    else:
        for fr, pat in FRANCHISE_HINTS:
            if re.search(pat, everything, re.IGNORECASE):
                franchise = fr
                break
        if not franchise:
            for fr, pat in OTHER_FRANCHISES:
                if re.search(pat, everything, re.IGNORECASE):
                    franchise = fr
                    break

    junk = re.compile(r"アニメ|插入歌|挿入歌|插曲|[「」#]|\bTV\b|\bOP\b|\bED\b", re.IGNORECASE)
    if is_classical:
        artist = "J.S. Bach"
    elif bands:
        norm = normalize_artist(gp_artist)
        artist = norm if norm and not junk.search(norm) and find_bands(norm) and len(norm) <= 40 else " × ".join(bands)
    else:
        f_artist = folder_parts[1] if len(folder_parts) > 1 else ""
        norm = normalize_artist(gp_artist)
        if norm and not junk.search(norm):
            artist = norm
            if f_artist and re.sub(r"\s", "", f_artist).lower() == re.sub(r"\s", "", norm).lower():
                artist = f_artist
        else:
            artist = normalize_artist(f_artist) if f_artist and not junk.search(f_artist) else ""

    ver_text = _strip_title(f"{folder} {meta['subtitle']} {meta['album']} {gp_file}", title, gp_title, f_title)
    ver_text = STRING_TOKEN_RE.sub(" ", ver_text)
    version = _version_label(ver_text)

    strings = int(meta["strings"] or 0)
    if strings in (4, 5):
        is5: Optional[bool] = strings == 5
    else:
        m = STRING_TOKEN_RE.search(folder)
        is5 = (m.group(1) == "5") if m and "4_5" not in folder else None

    out: Dict[str, object] = {"title": title, "artist": artist, "franchise": franchise, "version": version, "is_5string": is5}
    ov = _load_overrides().get(folder)
    if ov:
        out.update({k: v for k, v in ov.items() if k in out})
    out["group_key"] = group_key(str(out["title"]), str(out["artist"]), str(out["version"]))
    return out


def string_alternate(folder_path: str, gp_path: str) -> str:
    """Other-string GP inside the same folder: a "[4弦版]" AI arrangement or a sibling "…4st/5st…" file."""
    base = os.path.splitext(os.path.basename(gp_path))[0]
    ai_four = os.path.join(folder_path, f"{base} [4弦版].gp")
    if os.path.exists(ai_four):
        return ai_four
    m = STRING_TOKEN_RE.search(base)
    if not m:
        return ""
    other = "4" if m.group(1) == "5" else "5"
    for name in os.listdir(folder_path):
        if not name.lower().endswith(".gp") or "[核对版]" in name or "[4弦版]" in name:
            continue
        stem = os.path.splitext(name)[0]
        m2 = STRING_TOKEN_RE.search(stem)
        if m2 and m2.group(1) == other and STRING_TOKEN_RE.sub("", stem) == STRING_TOKEN_RE.sub("", base):
            return os.path.join(folder_path, name)
    return ""


LIVE_TAG_RE = re.compile(r"\b(Rose|Farbe|Wei[ßs]klee|Edelstein|Sonnenschein|Flamme|Wasser|Rosenchor|OVERKILL|"
                         r"\d{1,2}(?:st|nd|rd|th))\b", re.IGNORECASE)


def finalize_versions(rows: List[Dict[str, object]]) -> None:
    """Library-wide pass over describe() results (each row also needs "folder").
    "#N" labels only matter when the same song has another version, so they are dropped otherwise; two entries
    that still look identical (same song, label and string count) get their live/tour name appended."""
    def rekey(r: Dict[str, object]) -> None:
        r["group_key"] = group_key(str(r["title"]), str(r["artist"]), str(r["version"]))

    by_song: Dict[str, List[Dict[str, object]]] = {}
    for r in rows:
        by_song.setdefault(str(r["group_key"]).rsplit("|", 1)[0], []).append(r)
    for members in by_song.values():
        versions = {str(r["version"]) for r in members}
        for r in members:
            if str(r["version"]).startswith("#") and len(versions) == 1:
                r["version"] = ""
                rekey(r)

    clash: Dict[Tuple[str, object], List[Dict[str, object]]] = {}
    for r in rows:
        clash.setdefault((str(r["group_key"]), r["is_5string"]), []).append(r)
    for members in clash.values():
        if len(members) < 2:
            continue
        tags = []
        for r in members:
            folder = re.sub(r"(?<!\w)" + re.escape(str(r["title"])) + r"(?!\w)", " ", str(r["folder"]))
            m = LIVE_TAG_RE.search(folder)
            tags.append(m.group(1) if m else "")
        if len(set(tags)) == len(tags):
            for r, tag in zip(members, tags):
                r["version"] = _collapse(f"{r['version']} {tag}")
                rekey(r)
