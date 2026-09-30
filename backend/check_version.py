"""核对版 <-> 正式版.

An AI transcription folder holds
  [BASS TAB] <title>.gp            practice file: backing track = song without bass
  [BASS TAB] <title> [核对版].gp    check file: same score, audio = original mix + boosted bass stem
After the user edits and saves the check file in Guitar Pro, `sync_check_to_practice` makes the practice file
the check file's score (everything the user changed, incl. sync points) with the practice audio put back.
Standard library only (runs under the app's Python 3.14 as well).
"""
import paths
import glob
import json
import os
import re
import shutil
import time
import zipfile

CHECK_TAG = "[核对版]"
BACKUP_DIR = paths.cache(r"check_sync_backup")


def check_path_for(practice_gp: str) -> str:
    base, ext = os.path.splitext(practice_gp)
    return f"{base} {CHECK_TAG}{ext}"


def practice_path_for(check_gp: str) -> str:
    base, ext = os.path.splitext(check_gp)
    return base.replace(f" {CHECK_TAG}", "") + ext


def find_check(practice_gp: str) -> str:
    p = check_path_for(practice_gp)
    if os.path.exists(p):
        return p
    cands = glob.glob(os.path.join(os.path.dirname(practice_gp), f"*{CHECK_TAG}.gp"))
    return cands[0] if len(cands) == 1 else ""


def _read(path):
    with zipfile.ZipFile(path) as z:
        return {i.filename: z.read(i.filename) for i in z.infolist()}


def _assets_block(xml: str) -> str:
    m = re.search(r"<Assets>.*?</Assets>", xml, re.S)
    return m.group(0) if m else ""


def _embedded_paths(block: str):
    return [p.strip() for p in re.findall(r"<EmbeddedFilePath>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</EmbeddedFilePath>",
                                          block, re.S)]


def sync_check_to_practice(check_gp: str, practice_gp: str = "") -> dict:
    from gp_guard import is_derived, is_pristine_original
    practice_gp = practice_gp or practice_path_for(check_gp)
    if not os.path.exists(check_gp) or not os.path.exists(practice_gp):
        return {"ok": False, "error": "文件缺失"}
    # only BaSSDream's own transcriptions are ever rewritten
    if is_pristine_original(practice_gp) or not is_derived(practice_gp):
        return {"ok": False, "error": "正式版受保护"}
    chk, prc = _read(check_gp), _read(practice_gp)
    cx = chk["Content/score.gpif"].decode("utf-8")
    px = prc["Content/score.gpif"].decode("utf-8")
    p_assets = _assets_block(px)
    if not p_assets:
        return {"ok": False, "error": "正式版无伴奏"}
    c_assets = _assets_block(cx)
    new_xml = cx.replace(c_assets, p_assets) if c_assets else cx.replace("</Rhythms>", "</Rhythms>" + p_assets, 1)
    out = {k: v for k, v in chk.items() if not (k.startswith("Content/Assets/") and not k.endswith("/"))}
    for ap in _embedded_paths(p_assets):
        if ap not in prc:
            return {"ok": False, "error": "正式版伴奏缺失"}
        out[ap] = prc[ap]
    out["Content/Assets/"] = b""
    out["Content/score.gpif"] = new_xml.encode("utf-8")
    try:
        meta = json.loads(chk.get("meta.json", b"{}").decode("utf-8") or "{}")
    except ValueError:
        meta = {}
    meta.update({"hasAudio": True, "bassstationDerived": True})
    out["meta.json"] = json.dumps(meta, indent=4).encode("utf-8")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(BACKUP_DIR, f"{stamp}_{os.path.basename(practice_gp)}")
    shutil.copy2(practice_gp, backup)
    tmp = practice_gp + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for k in sorted(out):
            z.writestr(k, out[k], compress_type=zipfile.ZIP_STORED if k.endswith("/") else zipfile.ZIP_DEFLATED)
    os.replace(tmp, practice_gp)
    return {"ok": True, "practice": practice_gp, "backup": backup}
