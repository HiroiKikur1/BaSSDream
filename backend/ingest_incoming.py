"""Imports Guitar Pro files dropped into E:/BassStation/incoming/ as new library songs / training data.

For every *.gp (Guitar Pro 7/8):
  * title / artist from the score metadata (file name as fallback)
  * rejected: no bass track, or marked as an AI transcription (e.g. Songsterr "AI transcribe")
  * library folder "<title> _ <artist> _ 扩充曲库" (existing originals are never touched)
  * recording fetched and aligned bar-by-bar (backing_track_enhancer.auto_ensure_backing_track); only a recording
    whose pitch-match quality passes the gate yields a derived "[伴奏].gp" -> this is what training uses
Older formats (.gp5 / .gpx / .gp4 / .gp3) are moved to incoming/需要转换/ (use Guitar Pro 8: File > Batch Converter).
Processed files go to incoming/_done or incoming/_rejected; a report is written to incoming/_report.json.
Usage: python tab_cli.py ingest-incoming     (or python ingest_incoming.py)
"""
import json
import os
import re
import shutil
import sys
import time
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

INCOMING = os.path.join("E:" + os.sep, "BassStation", "incoming")
TABS = os.path.join("E:" + os.sep, "BassStation", "tabs")
OLD_FORMATS = (".gp5", ".gpx", ".gp4", ".gp3", ".gtp")
FRANCHISE = "扩充曲库"


def _text(root, tag):
    t = root.findtext(f"Score/{tag}") or ""
    return re.sub(r"\s+", " ", t).strip()


def inspect_gp(path):
    with zipfile.ZipFile(path) as z:
        x = z.read("Content/score.gpif").decode("utf-8", "ignore")
    x = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", x)
    root = ET.fromstring(x.split("?>", 1)[1] if x.lstrip().startswith("<?xml") else x)
    title, artist = _text(root, "Title"), _text(root, "Artist")
    meta = " ".join([title, artist, _text(root, "Tabber"), _text(root, "SubTitle"), os.path.basename(path)])
    ai = bool(re.search(r"\bAI\b|ai transcri|AI-generated|AI transcribed", meta, re.I))
    has_bass = False
    for tr in root.iter("Track"):
        name = (tr.findtext("Name") or "") + " " + (tr.findtext("InstrumentSet/Name") or "")
        pitches = tr.findtext(".//Property[@name='Tuning']/Pitches")
        low = min((int(p) for p in pitches.split()), default=99) if pitches else 99
        if "bass" in name.lower() or "ベース" in name or "贝斯" in name or low <= 28:
            has_bass = True
    return {"title": title, "artist": artist, "ai": ai, "has_bass": has_bass}


def clean(s):
    return re.sub(r'[\\/:*?"<>|]', " ", s).strip()[:80] or "untitled"


def ingest(limit=None):
    from backing_track_enhancer import auto_ensure_backing_track
    os.makedirs(INCOMING, exist_ok=True)
    for d in ("_done", "_rejected", "需要转换"):
        os.makedirs(os.path.join(INCOMING, d), exist_ok=True)
    report_path = os.path.join(INCOMING, "_report.json")
    report = json.load(open(report_path, encoding="utf8")) if os.path.exists(report_path) else []
    files = sorted(f for f in os.listdir(INCOMING) if os.path.isfile(os.path.join(INCOMING, f)))
    n = 0
    for f in files:
        src = os.path.join(INCOMING, f)
        low = f.lower()
        if low.endswith(OLD_FORMATS):
            shutil.move(src, os.path.join(INCOMING, "需要转换", f))
            report.append({"file": f, "status": "needs_conversion", "time": time.strftime("%F %T")})
            continue
        if not low.endswith(".gp"):
            continue
        if limit and n >= limit:
            break
        n += 1
        entry = {"file": f, "time": time.strftime("%F %T")}
        try:
            info = inspect_gp(src)
        except Exception as e:
            entry.update(status="rejected", reason=f"unreadable: {e}")
            shutil.move(src, os.path.join(INCOMING, "_rejected", f))
            report.append(entry)
            continue
        title = info["title"] or os.path.splitext(f)[0]
        artist = info["artist"] or "Unknown"
        entry.update(title=title, artist=artist)
        if info["ai"] or not info["has_bass"]:
            entry.update(status="rejected", reason="AI transcription" if info["ai"] else "no bass track")
            shutil.move(src, os.path.join(INCOMING, "_rejected", f))
            report.append(entry)
            print(json.dumps(entry, ensure_ascii=False), flush=True)
            continue
        folder = os.path.join(TABS, f"{clean(title)} _ {clean(artist)} _ {FRANCHISE}")
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, f"[BASS TAB] {clean(title)}.gp")
        if os.path.exists(dst):
            entry.update(status="rejected", reason="already in library")
            shutil.move(src, os.path.join(INCOMING, "_rejected", f))
            report.append(entry)
            continue
        shutil.copy2(src, dst)
        try:
            res = auto_ensure_backing_track(folder, title, artist)
        except Exception as e:
            res = {"has_backing": False, "status": f"error: {e}"}
        entry.update(folder=os.path.basename(folder), backing=res.get("status"), quality=res.get("quality"))
        # "aligned" = recording found and passed the pitch-match gate -> usable for training
        entry["status"] = "training" if res.get("status") == "aligned" else "library_only"
        shutil.move(src, os.path.join(INCOMING, "_done", f))
        report.append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    json.dump(report, open(report_path, "w", encoding="utf8"), ensure_ascii=False, indent=1)
    try:
        from tab_scanner import scan_all_tabs
        scan_all_tabs(force_rescan=True)
    except Exception:
        pass
    summary = {}
    for e in report:
        summary[e["status"]] = summary.get(e["status"], 0) + 1
    return {"processed": n, "summary": summary}


if __name__ == "__main__":
    print(json.dumps(ingest(), ensure_ascii=False))
