import re
import sys
import os
import glob
import json
import zipfile
import sqlite3
import hashlib
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional
from difficulty_evaluator import evaluate_difficulty
from cover_generator import generate_procedural_jacket
from netease_service import search_song_info, download_cover_image
from library_meta import describe, finalize_versions, string_alternate

DB_PATH = r"E:\BassStation\backend\data.db"
TABS_ROOT = r"E:\BassStation\tabs"
COVERS_DIR = r"E:\BassStation\cache\covers"

os.makedirs(COVERS_DIR, exist_ok=True)

KNOWN_ARTISTS = [
    'Ave Mujica', 'AveMujica', 'Roselia', 'MyGO!!!!!', 'MyGO', 'RAISE A SUILEN', 'RAS', 'Poppin\'Party',
    'Afterglow', 'Pastel＊Palettes', 'Pastel_Palettes', 'Morfonica', 'Leo/need', 'Leo_need',
    '25時、ナイトコードで。', 'Vivid BAD SQUAD', 'MORE MORE JUMP！', 'MORE MORE JUMP',
    'CRYCHIC', 'あたらよ', '中島由貴', 'ヨルシカ', 'ツユ', '愛美', '青木陽菜',
    '羊宮妃那', 'ナナヲアカリ', 'yonige', 'millsage', 'Conton Candy', 'Novelbright',
    'Aqours', 'Sugar Rush', 'Heaven Burns Red', 'TRUE', '優木せつ菜', '桐谷遥',
    '宵崎奏', '東雲繪名', '日野森志歩', '日野森雫', '初音ミク', '初音未來', '鏡音レン', 'KAITO', 'MEIKO',
    'J.S. Bach', 'Johann Sebastian Bach', 'Bach', '巴赫', 'Franz Simandl', 'Simandl'
]

def extract_metadata(folder: str):
    artist = ""
    for a in KNOWN_ARTISTS:
        if a.lower() in folder.lower():
            artist = a.replace('AveMujica', 'Ave Mujica').replace('Leo_need', 'Leo/need').replace('Pastel_Palettes', 'Pastel＊Palettes')
            break

    cleaned_f = folder.replace('Mas_uerade', 'Masquerade').replace('Re_uest', 'Request')

    m = re.search(r'^[『「]\s*(.*?)\s*[』」]', cleaned_f)
    if m:
        title = m.group(1)
    else:
        parts = re.split(r'\s+[_\/／]\s+', cleaned_f)
        title = parts[0].strip()

    title = re.sub(r'\b[45](?:弦|st\.?)\b|[45]弦', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\b(Live\s*ver\.?|LIVE\s*ver\.?|short\s*ver\.?|TVOP\s*size|TV\s*size|game\s*size|Studio\s*ver\.?|FULL|Full|Acoustic\s*Ver\.?|先行|現場版|短版改編|完整版改編|ED\s*完整版)\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\b(LIVE|Live)\b', '', title)
    title = re.sub(r'[\(\)（）~～☆\+『』「」【】]', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip().rstrip('. ')

    franchise = "BanG Dream!"
    lower_f = folder.lower()
    if any(x in lower_f for x in ['古典练习曲', '古典', 'classical']):
        franchise = "古典练习曲"
    elif any(x in lower_f for x in ['project sekai', 'leo_need', 'leo/need', '25時', 'vivid bad squad', 'more more jump', '初音']):
        franchise = "Project SEKAI"
    elif any(x in lower_f for x in ['lovelive', '虹ヶ咲', '虹咲', 'aqours']):
        franchise = "Love Live!"
    elif 'blue archive' in lower_f or '蔚藍檔案' in lower_f:
        franchise = "Blue Archive"
    elif 'heaven burns red' in lower_f:
        franchise = "Heaven Burns Red"
    elif 'girls band cry' in lower_f or '無刺有刺' in lower_f:
        franchise = "Girls Band Cry"

    if not artist:
        parts = re.split(r'\s+[_\/／]\s+', cleaned_f)
        if len(parts) > 1 and not any(x in parts[1] for x in ['4弦', '5弦', '4st', '5st', 'LIVE', 'ver', 'short', '古典练习曲']):
            artist = parts[1]
        elif franchise == "古典练习曲":
            artist = "J.S. Bach"
        else:
            artist = "BanG Dream!"

    return title, artist, franchise

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS song_cache (
            id TEXT PRIMARY KEY,
            folder_name TEXT,
            title TEXT,
            artist TEXT,
            franchise TEXT,
            gp_path TEXT,
            pdf_path TEXT,
            mtime REAL,
            tempo REAL,
            duration REAL,
            measures INTEGER,
            notes_count INTEGER,
            is_5string INTEGER,
            tuning TEXT,
            has_backing_track INTEGER,
            audio_path TEXT,
            cover_url TEXT,
            level INTEGER,
            level_exact REAL,
            tier TEXT,
            radar_json TEXT,
            tags_json TEXT,
            peak_nps REAL
        )
    ''')
    cols = {r[1] for r in cur.execute("PRAGMA table_info(song_cache)")}
    for col in ("version", "group_key", "alt_gp_path"):
        if col not in cols:
            cur.execute(f"ALTER TABLE song_cache ADD COLUMN {col} TEXT DEFAULT ''")
    conn.commit()
    conn.close()

def parse_gp_file(gp_path: str) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(gp_path, 'r') as z:
            if 'Content/score.gpif' not in z.namelist():
                return {}
            xml_bytes = z.read('Content/score.gpif')
            try:
                root = ET.fromstring(xml_bytes)
            except Exception:
                cleaned = xml_bytes.decode('utf-8', errors='ignore')
                root = ET.fromstring(cleaned)

            title = root.findtext('.//Title') or ""
            artist = root.findtext('.//Artist') or ""

            has_backing = False
            for item in z.infolist():
                if item.filename.startswith('Content/Assets/') and item.file_size > 100000:
                    has_backing = True
                    break

            tempo = 120.0
            for auto in root.findall('.//Automation'):
                if auto.findtext('Type') == 'Tempo':
                    val = auto.findtext('Value', '').strip()
                    if val:
                        try:
                            bpm = float(val.split()[0])
                            if 30 <= bpm <= 350:
                                tempo = bpm
                                break
                        except Exception:
                            pass

            if tempo == 120.0:
                for el in root.iter():
                    if 'tempo' in el.tag.lower() and el.text:
                        try:
                            t = float(el.text.split()[0])
                            if 30 <= t <= 350:
                                tempo = t
                                break
                        except Exception:
                            pass

            tracks = root.findall('.//Tracks/Track')
            is_5string = False
            tuning = "EADG (4弦)"
            bass_track_idx = 0
            if tracks:
                for idx, t in enumerate(tracks):
                    name = (t.findtext('Name') or '').lower()
                    t_type = (t.findtext('.//InstrumentSet/Type') or '').lower()
                    props = t.findall('.//Properties/Property')
                    t_pitches = []
                    for p in props:
                        if p.get('name') == 'Tuning':
                            tuning_el = p.find('Tuning')
                            if tuning_el is not None:
                                t_pitches = tuning_el.findall('.//Pitch')
                                if not t_pitches:
                                    ptxt = tuning_el.findtext('Pitches') or ''
                                    if ptxt:
                                        t_pitches = ptxt.split()
                                break
                    if 'bass' in name or 'bass' in t_type or len(t_pitches) in (4, 5):
                        bass_track_idx = idx
                        if len(t_pitches) >= 5:
                            is_5string = True
                            tuning = "BEADG (5弦)"
                        elif len(t_pitches) == 4:
                            is_5string = False
                            tuning = "EADG (4弦)"
                        break
            else:
                # If no tracks had tuning properties, check global properties
                for p in root.findall('.//Property[@name="Tuning"]'):
                    ptxt = p.findtext('.//Pitches') or ''
                    if ptxt and len(ptxt.split()) >= 5:
                        is_5string = True
                        tuning = "BEADG (5弦)"
                        break

            rhythms_base = {
                'Whole': 4.0, 'Half': 2.0, 'Quarter': 1.0, 
                'Eighth': 0.5, '16th': 0.25, 'Sixteenth': 0.25, 
                '32nd': 0.125, '64th': 0.0625
            }
            rhythm_map = {}
            for r in root.findall('.//Rhythms/Rhythm'):
                rid = r.get('id')
                val_name = r.findtext('NoteValue') or 'Quarter'
                dur = rhythms_base.get(val_name, 1.0)
                dot = r.find('AugmentationDot')
                if dot is not None:
                    count = int(dot.get('count', 1))
                    if count == 1: dur *= 1.5
                    elif count == 2: dur *= 1.75
                rhythm_map[rid] = dur

            notes_palette = {}
            for n in root.findall('.//Notes/Note'):
                nid = n.get('id')
                fret = 0
                string = 0
                fel = n.find('.//Property[@name="Fret"]/Fret')
                if fel is not None and fel.text:
                    try: fret = int(fel.text)
                    except Exception: pass
                sel = n.find('.//Property[@name="String"]/String')
                if sel is not None and sel.text:
                    try: string = int(sel.text)
                    except Exception: pass

                notes_palette[nid] = {
                    'fret': fret,
                    'string': string,
                    'is_slap': (n.find('.//Property[@name="Slap"]') is not None or n.find('.//Property[@name="Slapped"]') is not None),
                    'is_pop': (n.find('.//Property[@name="Pop"]') is not None or n.find('.//Property[@name="Popped"]') is not None),
                    'is_ghost': (n.find('.//Property[@name="Muted"]') is not None or n.find('.//Property[@name="Ghost"]') is not None),
                    'is_slide': n.find('.//Property[@name="Slide"]') is not None,
                    'is_hammer': (n.find('.//Property[@name="Hammer"]') is not None or n.find('.//Property[@name="HopoOrigin"]') is not None or n.find('.//Property[@name="HopoDestination"]') is not None),
                }

            beats_palette = {}
            for b in root.findall('.//Beats/Beat'):
                bid = b.get('id')
                r_ref = b.find('Rhythm').get('ref') if b.find('Rhythm') is not None else '0'
                ntxt = b.findtext('Notes') or ''
                nids = ntxt.split()
                # Detect beat-level slap/pop (GP8 correct location: Beat.Properties)
                beat_slapped = (
                    b.find('.//Properties/Property[@name="Slapped"]') is not None or
                    b.find('.//Property[@name="Slapped"]') is not None
                )
                beat_popped = (
                    b.find('.//Properties/Property[@name="Popped"]') is not None or
                    b.find('.//Property[@name="Popped"]') is not None
                )
                note_list = []
                for nid in nids:
                    if nid in notes_palette:
                        n_copy = dict(notes_palette[nid])
                        # Propagate beat-level articulations to note for difficulty eval
                        if beat_slapped:
                            n_copy['is_slap'] = True
                        if beat_popped:
                            n_copy['is_pop'] = True
                        note_list.append(n_copy)
                beats_palette[bid] = (rhythm_map.get(r_ref, 1.0), note_list)

            voices = {}
            for v in root.findall('.//Voices/Voice'):
                vid = v.get('id')
                btxt = v.findtext('Beats') or ''
                voices[vid] = btxt.split()

            # Index all Bars
            bar_map = {}
            for b in root.findall('.//Bars/Bar'):
                bid = b.get('id')
                if bid is not None:
                    bar_map[bid] = b

            master_bars = root.findall('.//MasterBars/MasterBar')
            measures = len(master_bars) if master_bars else len(root.findall('.//Bars/Bar'))

            sec_per_beat = 60.0 / max(30.0, tempo)
            notes_stream = []
            cur_time = 0.0

            if master_bars:
                for mb in master_bars:
                    time_sig = mb.findtext('Time') or '4/4'
                    try:
                        num, denom = [int(x) for x in time_sig.split('/')]
                        bar_beats = num * (4.0 / denom)
                    except Exception:
                        bar_beats = 4.0

                    bars_txt = mb.findtext('Bars') or ''
                    bids = bars_txt.split()
                    bar_handled = False
                    if bass_track_idx < len(bids):
                        bar_id = bids[bass_track_idx]
                        bar_el = bar_map.get(bar_id)
                        if bar_el is not None:
                            vtxt = bar_el.findtext('Voices') or ''
                            vids = vtxt.split()
                            if vids and vids[0] != '-1':
                                v0 = vids[0]
                                voice_beats = voices.get(v0, [])
                                if voice_beats:
                                    bar_handled = True
                                    for bid in voice_beats:
                                        dur, n_list = beats_palette.get(bid, (1.0, []))
                                        for n_info in n_list:
                                            item = dict(n_info)
                                            item['timestamp'] = cur_time
                                            item['is_offbeat'] = (len(notes_stream) % 2 == 1)
                                            notes_stream.append(item)
                                        cur_time += dur * sec_per_beat
                    if not bar_handled:
                        cur_time += bar_beats * sec_per_beat
            else:
                bars = root.findall('.//Bars/Bar')
                for bar in bars:
                    vtxt = bar.findtext('Voices') or ''
                    vids = vtxt.split()
                    if not vids or vids[0] == '-1':
                        cur_time += 4.0 * sec_per_beat
                        continue
                    v0 = vids[0]
                    for bid in voices.get(v0, []):
                        dur, n_list = beats_palette.get(bid, (1.0, []))
                        for n_info in n_list:
                            item = dict(n_info)
                            item['timestamp'] = cur_time
                            item['is_offbeat'] = (len(notes_stream) % 2 == 1)
                            notes_stream.append(item)
                        cur_time += dur * sec_per_beat

            total_duration = max(10.0, cur_time)

            return {
                "title": title,
                "artist": artist,
                "tempo": round(tempo, 1),
                "measures": measures,
                "duration": round(total_duration, 1),
                "notes": notes_stream,
                "is_5string": is_5string,
                "tuning": tuning,
                "has_backing": has_backing
            }
    except Exception:
        return {}

def parse_gp5_file(gp_path: str) -> Dict[str, Any]:
    try:
        import guitarpro
        song = guitarpro.parse(gp_path)
        title = song.title or ""
        artist = song.artist or ""
        tempo = float(song.tempo) if song.tempo else 120.0

        bass_track = None
        for t in song.tracks:
            t_name = (t.name or "").lower()
            if len(t.strings) in (4, 5) or 'bass' in t_name or 'basse' in t_name:
                bass_track = t
                break
        if not bass_track and song.tracks:
            bass_track = song.tracks[0]

        is_5string = len(bass_track.strings) >= 5 if bass_track else False
        tuning = "BEADG (5弦)" if is_5string else "EADG (4弦)"
        measures = len(bass_track.measures) if bass_track else (len(song.tracks[0].measures) if song.tracks else 0)

        notes_stream = []
        cur_time = 0.0
        sec_per_beat = 60.0 / max(30.0, tempo)
        if bass_track:
            for m in bass_track.measures:
                for v in m.voices:
                    for b in v.beats:
                        dur_ratio = 4.0 / b.duration.value if b.duration and b.duration.value > 0 else 1.0
                        if b.duration and b.duration.isDotted:
                            dur_ratio *= 1.5
                        for n in b.notes:
                            notes_stream.append({
                                'fret': n.value,
                                'string': n.string - 1,
                                'is_slap': False,
                                'is_pop': False,
                                'is_ghost': n.type == guitarpro.NoteType.dead or getattr(n, 'ghostNote', False),
                                'timestamp': cur_time,
                                'is_offbeat': (len(notes_stream) % 2 == 1)
                            })
                        cur_time += dur_ratio * sec_per_beat

        total_duration = max(10.0, cur_time)
        return {
            "title": title,
            "artist": artist,
            "tempo": round(tempo, 1),
            "measures": measures,
            "duration": round(total_duration, 1),
            "notes": notes_stream,
            "is_5string": is_5string,
            "tuning": tuning,
            "has_backing": False
        }
    except Exception as e:
        print(f"Error parsing GP5 {gp_path}: {e}")
        return {}

def scan_all_tabs(force_rescan: bool = False) -> List[Dict[str, Any]]:
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")

    cached_rows = {}
    if not force_rescan:
        for row in cur.execute("SELECT id, mtime FROM song_cache"):
            cached_rows[row["id"]] = row["mtime"]

    results = []
    folders = [f for f in os.listdir(TABS_ROOT) if os.path.isdir(os.path.join(TABS_ROOT, f))]

    for folder in folders:
        folder_path = os.path.join(TABS_ROOT, folder)
        song_id = hashlib.md5(folder.encode('utf-8')).hexdigest()[:12]

        gp_files = glob.glob(os.path.join(folder_path, "*.gp*"))
        if not gp_files:
            continue
        # Prefer .gp (GP8 native) → .gpx → .gp5 → .gp4
        def _gp_sort_key(p):
            ext = os.path.splitext(p)[1].lower()
            return {'.gp': 0, '.gpx': 1, '.gp5': 2, '.gp4': 3}.get(ext, 9)
        from gp_guard import has_embedded_audio, is_derived
        # practice file: GP8 native, with embedded audio (derived aligned copy first), then plain name
        # "[核对版]" companions (audio = song + boosted bass, for checking notes) are never the practice file
        gp_files.sort(key=lambda p: ("[核对版]" in os.path.basename(p) or "[4弦版]" in os.path.basename(p), _gp_sort_key(p), not has_embedded_audio(p),
                                     not is_derived(p), os.path.basename(p).lower().endswith("_n.gp"),
                                     os.path.basename(p)))
        gp_path = gp_files[0]

        pdf_files = [p for p in glob.glob(os.path.join(folder_path, "*.pdf")) if os.path.getsize(p) >= 30000]
        pdf_path = pdf_files[0] if pdf_files else ""

        mtime = os.path.getmtime(gp_path)

        ident = describe(folder, gp_path)
        title, artist, franchise = str(ident["title"]), str(ident["artist"]), str(ident["franchise"])
        is_5string_folder = bool(ident["is_5string"])

        # Ensure cover exists!
        cover_path = os.path.join(COVERS_DIR, f"{song_id}.jpg")
        if not os.path.exists(cover_path) or os.path.getsize(cover_path) < 500:
            generate_procedural_jacket(song_id, title, artist, folder)

        # Check real audio on disk
        local_audio_files = (
            glob.glob(os.path.join(folder_path, "*.mp3")) +
            glob.glob(os.path.join(folder_path, "*.wav")) +
            glob.glob(os.path.join(folder_path, "*.m4a")) +
            glob.glob(os.path.join(folder_path, "*.flac"))
        )
        real_audio_path = local_audio_files[0] if local_audio_files else ""
        has_real_audio = bool(real_audio_path and os.path.exists(real_audio_path))

        # Check GP embedded audio
        gp_embedded = False
        if gp_path.lower().endswith('.gp') and os.path.exists(gp_path):
            try:
                import zipfile
                with zipfile.ZipFile(gp_path, 'r') as z:
                    gp_embedded = any(
                        item.filename.startswith('Content/Assets/') and item.file_size > 100000
                        for item in z.infolist()
                    )
            except Exception:
                pass


        is_backing_ready = gp_embedded or (has_real_audio and gp_path.lower().endswith('.gp5'))

        if song_id in cached_rows and cached_rows[song_id] == mtime and not force_rescan:
            cur.execute("SELECT * FROM song_cache WHERE id = ?", (song_id,))
            r = cur.fetchone()
            if r:
                # Validate real audio & backing status
                db_audio = real_audio_path if has_real_audio else (r["audio_path"] or "")
                if is_backing_ready != bool(r["has_backing_track"]) or db_audio != (r["audio_path"] or ""):
                    cur.execute("UPDATE song_cache SET has_backing_track = ?, audio_path = ? WHERE id = ?",
                                (1 if is_backing_ready else 0, db_audio, song_id))

                radar_obj = {}
                if r["radar_json"]:
                    try:
                        raw = json.loads(r["radar_json"])
                        if isinstance(raw, dict):
                            radar_obj = raw
                    except Exception:
                        pass

                tags_list = []
                if r["tags_json"]:
                    try:
                        raw = json.loads(r["tags_json"])
                        if isinstance(raw, list):
                            tags_list = [str(x) for x in raw if x]
                        elif isinstance(raw, dict):
                            tags_list = [str(x) for x in raw.values() if x]
                    except Exception:
                        pass

                results.append({
                    "id": song_id,
                    "folder_name": folder,
                    "title": r["title"] or title,
                    "artist": r["artist"] or artist,
                    "franchise": r["franchise"] or franchise,
                    "gp_path": gp_path,
                    "pdf_path": pdf_path or (r["pdf_path"] or ""),
                    "tempo": r["tempo"] or 120.0,
                    "duration": r["duration"] or 90.0,
                    "measures": r["measures"] or 60,
                    "notes_count": r["notes_count"] or 0,
                    "is_5string": bool(r["is_5string"]),
                    "tuning": r["tuning"] or ("BEADG (5弦)" if is_5string_folder else "EADG (4弦)"),
                    "has_backing_track": is_backing_ready,
                    "audio_path": db_audio,
                    "cover_url": r["cover_url"] or f"/api/cover/{song_id}",
                    "level": r["level"] or 10,
                    "level_exact": r["level_exact"] or 10.0,
                    "tier": r["tier"] or "HARD",
                    "radar": radar_obj,
                    "tags": tags_list,
                    "peak_nps": r["peak_nps"] or 0.0
                })
                continue

        parsed = {}
        if gp_path.endswith('.gp'):
            parsed = parse_gp_file(gp_path)
        elif gp_path.endswith('.gp5') or gp_path.endswith('.gpx') or gp_path.endswith('.gp4'):
            parsed = parse_gp5_file(gp_path)

        tempo = parsed.get("tempo", 120.0)
        measures = parsed.get("measures", 60)
        duration = parsed.get("duration", 90.0)
        if gp_path.lower().endswith('.gp'):
            try:
                from bassnet.gpif_parser import parse_gp as _parse_timed, q_to_sec
                _info = _parse_timed(gp_path)
                if _info.bars:
                    _bi, _oc, _q0, _ql = _info.bars[-1]
                    _end = q_to_sec(_info.anchors, float(_q0 + _ql))
                    _start = min(0.0, q_to_sec(_info.anchors, 0.0))
                    if _end - _start > 5:
                        duration = round(_end - _start, 1)
            except Exception:
                pass
        notes = parsed.get("notes", [])
        is_5string = is_5string_folder if ident["is_5string"] is not None else parsed.get("is_5string", False)
        tuning = "BEADG (5弦)" if is_5string else "EADG (4弦)"
        has_backing = is_backing_ready
        diff = evaluate_difficulty(gp_path, notes, tempo, is_5string)

        cur.execute('''
            INSERT OR REPLACE INTO song_cache (
                id, folder_name, title, artist, franchise, gp_path, pdf_path,
                mtime, tempo, duration, measures, notes_count, is_5string,
                tuning, has_backing_track, audio_path, cover_url,
                level, level_exact, tier, radar_json, tags_json, peak_nps
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            song_id, folder, title, artist, franchise, gp_path, pdf_path,
            mtime, tempo, duration, measures, len(notes), 1 if is_5string else 0,
            tuning, 1 if is_backing_ready else 0, real_audio_path, f"/api/cover/{song_id}",
            diff["level"], diff["level_exact"], diff["tier"],
            json.dumps(diff["radar"]), json.dumps(diff["tags"]), diff["peak_nps"]
        ))

        results.append({
            "id": song_id,
            "folder_name": folder,
            "title": title,
            "artist": artist,
            "franchise": franchise,
            "gp_path": gp_path,
            "pdf_path": pdf_path,
            "tempo": tempo,
            "duration": duration,
            "measures": measures,
            "notes_count": len(notes),
            "is_5string": is_5string,
            "tuning": tuning,
            "has_backing_track": is_backing_ready,
            "audio_path": real_audio_path,
            "cover_url": f"/api/cover/{song_id}",
            "level": diff["level"],
            "level_exact": diff["level_exact"],
            "tier": diff["tier"],
            "radar": diff["radar"],
            "tags": diff["tags"],
            "peak_nps": diff["peak_nps"]
        })

    apply_identity(cur, results)

    # Purge deleted songs from database
    existing_ids = [r["id"] for r in results]
    if existing_ids:
        cur.execute(f"DELETE FROM song_cache WHERE id NOT IN ({','.join(['?']*len(existing_ids))})", existing_ids)

    conn.commit()
    conn.close()
    return results

def apply_identity(cur, results: List[Dict[str, Any]]) -> None:
    """Library-wide title / artist / franchise / version / 4-5 string grouping (cheap, runs on every scan)."""
    idents = []
    for r in results:
        d = describe(r["folder_name"], r["gp_path"])
        d["folder"] = r["folder_name"]
        if d["is_5string"] is None:
            d["is_5string"] = bool(r["is_5string"])
        d["alt_gp_path"] = string_alternate(os.path.dirname(r["gp_path"]), r["gp_path"])
        idents.append(d)
    finalize_versions(idents)
    for r, d in zip(results, idents):
        is5 = bool(d["is_5string"])
        r.update({"title": d["title"], "artist": d["artist"], "franchise": d["franchise"], "version": d["version"],
                  "group_key": d["group_key"], "alt_gp_path": d["alt_gp_path"], "is_5string": is5,
                  "tuning": "BEADG (5弦)" if is5 else "EADG (4弦)"})
        cur.execute("UPDATE song_cache SET title = ?, artist = ?, franchise = ?, version = ?, group_key = ?, "
                    "alt_gp_path = ?, is_5string = ?, tuning = ? WHERE id = ?",
                    (r["title"], r["artist"], r["franchise"], r["version"], r["group_key"], r["alt_gp_path"],
                     1 if is5 else 0, r["tuning"], r["id"]))


def rerate_all() -> int:
    """Recomputes only the difficulty columns of every cached song (after a rating-model change)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    rows = cur.execute("SELECT id, gp_path, tempo, is_5string FROM song_cache").fetchall()
    for song_id, gp_path, tempo, is_5 in rows:
        legacy = []
        if gp_path and not gp_path.lower().endswith('.gp') and os.path.exists(gp_path):
            legacy = parse_gp5_file(gp_path).get("notes", [])
        diff = evaluate_difficulty(gp_path, legacy, tempo or 120.0, bool(is_5))
        cur.execute("UPDATE song_cache SET level = ?, level_exact = ?, tier = ?, radar_json = ?, tags_json = ?, "
                    "peak_nps = ? WHERE id = ?",
                    (diff["level"], diff["level_exact"], diff["tier"], json.dumps(diff["radar"]),
                     json.dumps(diff["tags"], ensure_ascii=False), diff["peak_nps"], song_id))
    conn.commit()
    conn.close()
    return len(rows)


if __name__ == "__main__":
    if "--rerate" in sys.argv:
        print(f"Re-rated {rerate_all()} songs.")
        sys.exit(0)
    songs = scan_all_tabs(force_rescan=True)
    print(f"Rescanned {len(songs)} songs.")
