import os
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, Any

_FRET_CACHE: Dict[str, Dict[str, Any]] = {}

def get_song_fretboard_stats(gp_path: str) -> Dict[str, Any]:
    """
    Parses the Guitar Pro score file to extract real bass-track fret and string distribution,
    providing visual heatmap data for a 4-string or 5-string virtual fretboard.
    """
    if not os.path.exists(gp_path):
        return {"success": False, "error": "曲谱文件未找到"}

    mtime = os.path.getmtime(gp_path)
    cache_key = f"{gp_path}_{mtime}"
    if cache_key in _FRET_CACHE:
        return _FRET_CACHE[cache_key]

    try:
        with zipfile.ZipFile(gp_path, "r") as z:
            if "Content/score.gpif" not in z.namelist():
                return {"success": False, "error": "缺少 score.gpif"}
            xml_bytes = z.read("Content/score.gpif")

        try:
            root = ET.fromstring(xml_bytes)
        except Exception:
            root = ET.fromstring(xml_bytes.decode("utf-8", errors="ignore"))

        tracks = root.findall(".//Tracks/Track")
        bass_track_id = None
        is_5string = False
        tuning = "EADG (4弦)"

        for t in tracks:
            t_name = t.findtext("Name", "")
            t_id = t.get("id")
            props = t.findall(".//Properties/Property")
            pitches = []
            for p in props:
                if p.get("name") == "Tuning":
                    tuning_el = p.find("Tuning")
                    if tuning_el is not None:
                        pitches = tuning_el.findall(".//Pitch")
            if "bass" in t_name.lower() or "贝斯" in t_name or len(pitches) in (4, 5):
                bass_track_id = t_id
                if len(pitches) >= 5:
                    is_5string = True
                    tuning = "BEADG (5弦)"
                elif len(pitches) == 4:
                    tuning = "EADG (4弦)"
                if "bass" in t_name.lower() or "贝斯" in t_name:
                    break

        if not bass_track_id and tracks:
            bass_track_id = tracks[0].get("id")

        # Map notes
        notes_palette = {}
        for n in root.findall(".//Notes/Note"):
            nid = n.get("id")
            fret = 0
            string = 1
            fel = n.find('.//Property[@name="Fret"]/Fret')
            if fel is not None and fel.text:
                try: fret = int(fel.text)
                except Exception: pass
            sel = n.find('.//Property[@name="String"]/String')
            if sel is not None and sel.text:
                try: string = int(sel.text)
                except Exception: pass
            notes_palette[nid] = {"fret": fret, "string": string}

        beats_palette = {}
        for b in root.findall(".//Beats/Beat"):
            bid = b.get("id")
            ntxt = b.findtext("Notes") or ""
            beats_palette[bid] = [notes_palette[nid] for nid in ntxt.split() if nid in notes_palette]

        voices = {}
        for v in root.findall(".//Voices/Voice"):
            vid = v.get("id")
            voices[vid] = (v.findtext("Beats") or "").split()

        mbars = root.findall(".//MasterBar")
        track_ids = [t.get("id") for t in tracks]
        t_idx = track_ids.index(bass_track_id) if bass_track_id in track_ids else 0

        bass_notes = []
        for mb in mbars:
            bar_ids = (mb.findtext("Bars") or "").split()
            if t_idx < len(bar_ids):
                target_bar_id = bar_ids[t_idx]
                bar_elem = root.find(f".//Bar[@id='{target_bar_id}']")
                if bar_elem is not None:
                    vids = (bar_elem.findtext("Voices") or "").split()
                    if vids and vids[0] != "-1" and vids[0] in voices:
                        for bid in voices[vids[0]]:
                            bass_notes.extend(beats_palette.get(bid, []))

        fret_counts = {}
        string_counts = {}
        for n in bass_notes:
            f = n["fret"]
            s = n["string"]
            if 0 <= f <= 24:
                fret_counts[f] = fret_counts.get(f, 0) + 1
            if 0 <= s <= 5:
                string_counts[s] = string_counts.get(s, 0) + 1

        active_frets = [f for f, cnt in fret_counts.items() if cnt > 0]
        min_fret = min(active_frets) if active_frets else 0
        max_fret = max(active_frets) if active_frets else 12

        # Primary zone description
        if max_fret <= 7:
            primary_position = "第 0 ~ 7 品 (低把位律动区)"
        elif min_fret >= 7:
            primary_position = "第 7 ~ 15 品 (中高把位旋律区)"
        elif max_fret >= 15:
            primary_position = f"第 {min_fret} ~ {max_fret} 品 (全把位大跨度技术曲)"
        else:
            primary_position = f"第 {min_fret} ~ {max_fret} 品 (中低把位综合流动)"

        res = {
            "success": True,
            "is_5string": is_5string,
            "tuning": tuning,
            "total_notes": len(bass_notes),
            "fret_counts": fret_counts,
            "string_counts": string_counts,
            "min_fret": min_fret,
            "max_fret": max_fret,
            "primary_position": primary_position
        }
        _FRET_CACHE[cache_key] = res
        return res
    except Exception as e:
        return {"success": False, "error": f"指板数据提取失败: {str(e)}"}
