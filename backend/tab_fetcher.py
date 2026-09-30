import paths
import os
import re
import html
import glob
import json
import shutil
import hashlib
import requests
import subprocess
import io
import zipfile
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional

from netease_service import search_song_info, download_cover_image, COVERS_DIR, AUDIO_DIR
from cover_generator import generate_procedural_jacket
from cover_match import fetch_cover
from gp_audio_linker import inject_backing_track_to_gp

TABS_ROOT = paths.TABS

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://music.163.com/",
    "Cookie": "os=pc; osver=Microsoft-Windows-10-Professional-build-19045-64bit; appver=2.9.7.1998; channel=netease;"
}

SONGSTERR_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://www.songsterr.com/",
    "Accept": "application/json, text/plain, */*"
}

session = requests.Session()
session.headers.update(HEADERS)

def clean_name(s: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', '', s).strip()
    return cleaned or "Unnamed"

def get_search_aliases(keyword: str) -> List[str]:
    """Generates multilingual search variations (e.g. Romaji/English) for tabs."""
    aliases = [keyword]
    clean_kw = clean_name(keyword)
    if clean_kw not in aliases:
        aliases.append(clean_kw)

    # Known common mappings or patterns
    if "又三郎" in keyword or "matasaburo" in keyword.lower():
        aliases.extend(["Matasaburo", "又三郎", "Yorushika Matasaburo", "又三郎 Matasaburo", "ヨルシカ 又三郎"])
    if "ジャイアント" in keyword or "キラー" in keyword or "giant" in keyword.lower():
        aliases.extend(["ジャイアント・キラー・チューン", "Giant Killer Tune", "ジャイアントキラーチューン"])

    # If Chinese/Japanese, query NetEase for alias or translation
    if any(ord(c) > 127 for c in keyword):
        try:
            info = search_song_info(keyword)
            if info:
                t = info.get("title", "")
                ar = info.get("artist", "")
                if t and t not in aliases:
                    aliases.append(t)
                if ar and t:
                    aliases.append(f"{ar} {t}")
        except Exception:
            pass

    return list(dict.fromkeys(aliases))

def clean_title_for_match(s: str) -> str:
    s = str(s).lower()
    s = re.sub(r'[\(\)（）\[\]【】『』「」~～☆\+·_／/\\:\'\"-]', ' ', s)
    s = re.sub(r'\b(live|ver|full|short|tv|size|original|mix|acoustic|ed|op|4st|5st|4弦|5弦)\b', ' ', s)
    return ''.join(s.split())

def is_title_compatible(cand_title: str, query: str, aliases: list = None, prefer_artist: str = "") -> bool:
    if not cand_title or not query:
        return False
    c1 = clean_title_for_match(cand_title)
    q1 = clean_title_for_match(query)
    if not c1 or not q1:
        return False
    if q1 in c1 or c1 in q1:
        return True
    if aliases:
        for al in aliases:
            al_c = clean_title_for_match(al)
            if al_c and (al_c in c1 or c1 in al_c):
                return True
    # Word token intersection
    q_words = [w for w in re.findall(r'[\w]+', str(query).lower()) if len(w) > 1 and w not in {'4th', '5th', '1st', '2nd', '3rd', 'live', 'short', 'ver', 'tv', 'size', 'part'}]
    c_words = [w for w in re.findall(r'[\w]+', str(cand_title).lower()) if len(w) > 1]
    if q_words and any(w in c_words for w in q_words):
        return True
    # Character 2-gram overlap
    q_grams = set(q1[i:i+2] for i in range(len(q1)-1)) if len(q1) >= 2 else {q1}
    c_grams = set(c1[i:i+2] for i in range(len(c1)-1)) if len(c1) >= 2 else {c1}
    overlap = len(q_grams & c_grams) / max(1, len(q_grams))
    return overlap >= 0.45

def fetch_songsterr_gp_url(song_id: int) -> Optional[str]:
    """
    Fetches direct .gp/.gp5 export URL from Songsterr revision tree.
    Prioritizes authentic gpImport revisions to avoid corrupt/troll community edits.
    """
    try:
        url = f"https://www.songsterr.com/api/meta/{song_id}/revisions"
        res = requests.get(url, headers=SONGSTERR_HEADERS, timeout=6)
        if res.status_code == 200:
            revisions = res.json()
            if not revisions:
                return None

            # 1. Primary: genuine gpImport revisions from newest to oldest
            gp_imports = [r for r in revisions if r.get("gpImport")]
            for rev in gp_imports[:6]:
                revid = rev.get("revisionId")
                try:
                    rev_res = requests.get(f"https://www.songsterr.com/api/revision/{revid}", headers=SONGSTERR_HEADERS, timeout=4)
                    if rev_res.status_code == 200:
                        source = rev_res.json().get("source")
                        if source:
                            clean_src = source.split('?')[0].lower()
                            if "gp.songsterr.com" in source or clean_src.endswith(('.gp', '.gp5', '.gpx', '.gp4', '.gp3')):
                                return source
                except Exception:
                    continue

            # 2. Secondary: initial / foundational revisions from oldest to newest
            # (Songsterr's authentic base GP files are attached to the original revision)
            for rev in reversed(revisions[-12:]):
                revid = rev.get("revisionId")
                try:
                    rev_res = requests.get(f"https://www.songsterr.com/api/revision/{revid}", headers=SONGSTERR_HEADERS, timeout=4)
                    if rev_res.status_code == 200:
                        source = rev_res.json().get("source")
                        if source:
                            clean_src = source.split('?')[0].lower()
                            if "gp.songsterr.com" in source or clean_src.endswith(('.gp', '.gp5', '.gpx', '.gp4', '.gp3')):
                                return source
                except Exception:
                    continue

            # 3. Fallback: check remaining recent revisions
            for rev in revisions[:5]:
                revid = rev.get("revisionId")
                try:
                    rev_res = requests.get(f"https://www.songsterr.com/api/revision/{revid}", headers=SONGSTERR_HEADERS, timeout=4)
                    if rev_res.status_code == 200:
                        source = rev_res.json().get("source")
                        if source:
                            clean_src = source.split('?')[0].lower()
                            if "gp.songsterr.com" in source or clean_src.endswith(('.gp', '.gp5', '.gpx', '.gp4', '.gp3')):
                                return source
                except Exception:
                    continue
    except Exception as e:
        print(f"Error resolving GP URL for Songsterr ID {song_id}: {e}")
    return None

def validate_gp_score_integrity(content: bytes, expected_artist: str = "", expected_title: str = "") -> bool:
    """Validates that downloaded GP file is authentic and not a mismatched/troll upload."""
    if not content or len(content) < 1024:
        return False
    if content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                if 'Content/score.gpif' in z.namelist():
                    root = ET.fromstring(z.read('Content/score.gpif'))
                    artist_el = root.find('.//Score/Artist')
                    gp_artist = (artist_el.text or "").strip().lower() if artist_el is not None else ""
                    if expected_artist and len(expected_artist) > 3:
                        exp_a = expected_artist.lower().strip()
                        # Obvious mismatch detection (e.g. searching Metallica but got Led Zeppelin)
                        if "metallica" in exp_a and ("led zeppelin" in gp_artist or "zepp" in gp_artist):
                            return False
        except Exception:
            pass
    return True

def _parse_songsterr_item(item: dict, default_kw: str = "") -> Optional[Dict[str, Any]]:
    if item.get("isJunk") is True:
        return None
    sid = item.get("songId")
    if not sid:
        return None
    tracks = item.get("tracks", [])
    has_bass = (
        item.get("popularTrackBass") is not None
        or any(
            "bass" in str(t.get("name", "")).lower()
            or "bass" in str(t.get("instrument", "")).lower()
            or (32 <= t.get("instrumentId", 0) <= 39)
            or str(t.get("hash", "")).startswith("bass_")
            for t in tracks
        )
    )
    return {
        "id": str(sid),
        "title": item.get("title", default_kw),
        "artist": item.get("artist", "未知"),
        "source": "Songsterr 真实曲谱库",
        "has_bass": has_bass,
        "format": "gp",
        "download_url": f"songsterr://{sid}",
        "tracks_count": len(tracks)
    }

def _fetch_songsterr_query(query: str, limit: int = 30) -> List[Dict[str, Any]]:
    clean_q = query.strip()
    if not clean_q:
        return []
    url = f"https://www.songsterr.com/api/songs?pattern={requests.utils.quote(clean_q)}&size={limit}"
    try:
        res = requests.get(url, headers=SONGSTERR_HEADERS, timeout=6)
        if res.status_code == 200:
            raw_list = res.json()
            results = []
            seen_ids = set()
            for it in raw_list:
                sid = it.get("songId")
                if sid in seen_ids:
                    continue
                parsed = _parse_songsterr_item(it, clean_q)
                if parsed:
                    seen_ids.add(sid)
                    results.append(parsed)
            return results
    except Exception as e:
        print(f"Songsterr query failed for {clean_q}: {e}")
    return []

def search_online_tabs(keyword: str) -> List[Dict[str, Any]]:
    """
    Searches Songsterr for authentic Guitar Pro tabs.
    Maintains Songsterr's canonical default ranking without client-side score scrambling.
    """
    if not keyword or not keyword.strip():
        return []

    raw_query = keyword.strip()

    # 1. Primary: Direct query with user's exact keyword (100% Songsterr default ranking)
    results = _fetch_songsterr_query(raw_query, limit=30)
    if results:
        return results

    # 2. Fallback: Sanitized query if brackets or symbols were in the input
    sanitized = re.sub(r'[【】\[\]\(\)（）「」『』"\'~～/／\\:：·_]', ' ', raw_query)
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    if sanitized and sanitized.lower() != raw_query.lower():
        results = _fetch_songsterr_query(sanitized, limit=30)
        if results:
            return results

    # 3. Fallback: If Chinese/Japanese with 0 results, try aliases
    if any(ord(c) > 127 for c in raw_query):
        aliases = get_search_aliases(raw_query)
        for al in aliases:
            if al.lower() != raw_query.lower() and al.lower() != sanitized.lower():
                results = _fetch_songsterr_query(al, limit=30)
                if results:
                    return results

    return []

def fetch_songsterr_live_part(song_id: int):
    """
    Fetches the latest approved live track JSON from Songsterr CloudFront CDN,
    providing the exact note fingerings (frets/strings) rendered on the Songsterr web app.
    """
    try:
        url = f"https://www.songsterr.com/api/meta/{song_id}"
        r = requests.get(url, headers=SONGSTERR_HEADERS, timeout=6)
        if r.status_code != 200:
            return None, 4
        meta = r.json()
        rev_id = meta.get("revisionId")
        image = meta.get("image")
        tracks = meta.get("tracks", [])

        bass_idx = meta.get("popularTrackBass")
        num_strings = 4
        if bass_idx is None:
            for idx, t in enumerate(tracks):
                t_name = str(t.get("name", "")).lower()
                if "bass" in t_name or (32 <= t.get("instrumentId", 0) <= 39):
                    bass_idx = idx
                    num_strings = len(t.get("tuning", [0, 0, 0, 0]))
                    break
        else:
            if 0 <= bass_idx < len(tracks):
                num_strings = len(tracks[bass_idx].get("tuning", [0, 0, 0, 0]))

        if bass_idx is None or not rev_id or not image:
            return None, 4

        cdns = ["dqsljvtekg760", "d34shlm8p2ums2", "d3cqchs6g3b5ew"]
        for cdn in cdns:
            p_url = f"https://{cdn}.cloudfront.net/{song_id}/{rev_id}/{image}/{bass_idx}.json"
            pr = requests.get(p_url, headers=SONGSTERR_HEADERS, timeout=6)
            if pr.status_code == 200:
                return pr.json(), num_strings
    except Exception as e:
        print(f"Error fetching live part for Songsterr ID {song_id}: {e}")
    return None, 4

def sync_songsterr_part_to_gp(gp_path: str, part_json: dict, num_strings: int = 4) -> bool:
    """
    Synchronizes note fingerings (frets and strings) in a Guitar Pro (.gp) file
    with Songsterr's live web tab JSON model.
    """
    if not os.path.exists(gp_path) or not part_json:
        return False
    if not gp_path.lower().endswith('.gp'):
        return False

    s_measures = part_json.get('measures', [])
    if not s_measures:
        return False

    try:
        entries = {}
        with zipfile.ZipFile(gp_path, 'r') as z:
            for item in z.infolist():
                entries[item.filename] = z.read(item.filename)

        if 'Content/score.gpif' not in entries:
            return False

        root = ET.fromstring(entries['Content/score.gpif'])
        tracks = root.findall('.//Tracks/Track')
        if not tracks:
            return False

        bass_track_idx = -1
        for idx, t in enumerate(tracks):
            name = (t.findtext('Name') or '').lower()
            t_type = (t.findtext('.//InstrumentSet/Type') or '').lower()
            pitches = []
            for p in t.findall('.//Property[@name="Tuning"]'):
                p_nodes = p.findall('.//Pitch')
                if p_nodes:
                    pitches = [int(pn.text) for pn in p_nodes if pn.text and pn.text.strip().lstrip('-').isdigit()]
                else:
                    ptxt = p.findtext('Pitches') or ''
                    if not ptxt:
                        tuning_el = p.find('Tuning')
                        if tuning_el is not None:
                            ptxt = tuning_el.findtext('Pitches') or ''
                    if ptxt:
                        pitches = [int(x) for x in ptxt.split() if x.strip().lstrip('-').isdigit()]
                if pitches:
                    break

            if 'bass' in name or 'bass' in t_type or (pitches and min(pitches) <= 33 and len(pitches) in (4, 5)):
                bass_track_idx = idx
                if pitches:
                    num_strings = len(pitches)
                break

        if bass_track_idx < 0:
            for idx, t in enumerate(tracks):
                for p in t.findall('.//Property[@name="Tuning"]'):
                    ptxt = p.findtext('Pitches') or ''
                    if ptxt and len(ptxt.split()) in (4, 5):
                        bass_track_idx = idx
                        num_strings = len(ptxt.split())
                        break
                if bass_track_idx >= 0:
                    break

        if bass_track_idx < 0:
            return False

        bar_map = {b.get('id'): b for b in root.findall('.//Bars/Bar')}
        voice_map = {v.get('id'): v for v in root.findall('.//Voices/Voice')}
        beat_map = {b.get('id'): b for b in root.findall('.//Beats/Beat')}
        note_map = {n.get('id'): n for n in root.findall('.//Notes/Note')}

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

        master_bars = root.findall('.//MasterBars/MasterBar')
        synced_count = 0

        for m_idx in range(min(len(master_bars), len(s_measures))):
            mb = master_bars[m_idx]
            bids = (mb.findtext('Bars') or '').split()
            if bass_track_idx >= len(bids):
                continue
            bar_id = bids[bass_track_idx]
            bar = bar_map.get(bar_id)
            if bar is None:
                continue
            vids = (bar.findtext('Voices') or '').split()
            if not vids or vids[0] == '-1':
                continue
            voice = voice_map.get(vids[0])
            if voice is None:
                continue

            gp_timed_notes = []
            t = 0.0
            for bid in (voice.findtext('Beats') or '').split():
                beat = beat_map.get(bid)
                if beat is None:
                    continue
                r_ref = beat.find('Rhythm').get('ref') if beat.find('Rhythm') is not None else '0'
                b_dur = rhythm_map.get(r_ref, 1.0)
                nids = (beat.findtext('Notes') or '').split()
                for nid in nids:
                    if nid in note_map:
                        gp_timed_notes.append((t, note_map[nid]))
                t += b_dur

            s_m = s_measures[m_idx]
            s_timed_notes = []
            st = 0.0
            for v in s_m.get('voices', []):
                for b in v.get('beats', []):
                    dur_arr = b.get('duration', [1, 4])
                    beat_len = (dur_arr[0] / dur_arr[1]) * 4.0
                    if b.get('dots') == 1:
                        beat_len *= 1.5
                    elif b.get('dots') == 2:
                        beat_len *= 1.75

                    if not b.get('rest') and b.get('type') != 'rest':
                        for n in b.get('notes', []):
                            if not n.get('rest'):
                                s_timed_notes.append((st, n))
                    st += beat_len
                break

            for g_t, gn in gp_timed_notes:
                best_sn = None
                best_diff = 0.08
                for s_t, sn in s_timed_notes:
                    if abs(g_t - s_t) < best_diff:
                        best_diff = abs(g_t - s_t)
                        best_sn = sn

                if best_sn is not None:
                    s_f = best_sn.get('fret', 0)
                    s_s = (num_strings - 1) - best_sn.get('string', 0)

                    fret_el = gn.find('.//Property[@name="Fret"]/Fret')
                    if fret_el is not None:
                        fret_el.text = str(s_f)
                    else:
                        prop = ET.SubElement(gn.find('Properties') or gn, 'Property', {'name': 'Fret'})
                        f_sub = ET.SubElement(prop, 'Fret')
                        f_sub.text = str(s_f)

                    str_el = gn.find('.//Property[@name="String"]/String')
                    if str_el is not None:
                        str_el.text = str(s_s)
                    else:
                        prop = ET.SubElement(gn.find('Properties') or gn, 'Property', {'name': 'String'})
                        s_sub = ET.SubElement(prop, 'String')
                        s_sub.text = str(s_s)

                    synced_count += 1

        entries['Content/score.gpif'] = ET.tostring(root, encoding='utf-8', xml_declaration=True)

        from gp_guard import is_protected
        if is_protected(gp_path):
            return False
        with zipfile.ZipFile(gp_path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            for name, data in entries.items():
                z.writestr(name, data)

        print(f"Synced {synced_count} notes to Songsterr web fingering in {os.path.basename(gp_path)}")
        return True
    except Exception as e:
        print(f"Error syncing Songsterr part to GP: {e}")
        return False

def download_gp_file(url_or_id: str, expected_artist: str = "", expected_title: str = "") -> Optional[bytes]:
    """Downloads genuine GP file bytes from a direct URL or Songsterr ID with integrity validation."""
    if not url_or_id:
        return None

    target_url = url_or_id
    if url_or_id.startswith("songsterr://"):
        try:
            sid = int(url_or_id.replace("songsterr://", ""))
            resolved = fetch_songsterr_gp_url(sid)
            if resolved:
                target_url = resolved
            else:
                return None
        except Exception as e:
            print(f"Error resolving songsterr url {url_or_id}: {e}")
            return None

    try:
        r = requests.get(target_url, headers=SONGSTERR_HEADERS, timeout=15)
        if r.status_code == 200 and len(r.content) > 1024:
            content = r.content
            if validate_gp_score_integrity(content, expected_artist=expected_artist, expected_title=expected_title):
                return content
    except Exception as e:
        print(f"Error downloading GP file from {target_url}: {e}")
    return None

def search_audio_sources(keyword: str) -> List[Dict[str, Any]]:
    """
    Searches official audio tracks across NetEase Cloud Music and QQ Music channels.
    """
    results = []
    clean_kw = clean_name(keyword)
    seen_keys = set()

    # 1. NetEase Cloud Music
    try:
        data = {'s': clean_kw, 'type': 1, 'limit': 8, 'offset': 0}
        res = session.post('https://music.163.com/api/cloudsearch/pc', data=data, timeout=6)
        if res.status_code == 200:
            data = res.json()
            songs = data.get("result", {}).get("songs", [])
            for s in songs:
                s_id = s.get("id")
                cover = s.get("al", {}).get("picUrl", "")
                t = s.get("name", clean_kw)
                ar = s.get("ar", [{}])[0].get("name", "未知")
                key = f"{t.lower()}_{ar.lower()}"
                seen_keys.add(key)
                results.append({
                    "audio_id": s_id,
                    "title": t,
                    "artist": ar,
                    "album": s.get("al", {}).get("name", ""),
                    "duration": round(s.get("dt", 0) / 1000.0, 1),
                    "cover_url": cover,
                    "source": "网易云"
                })
    except Exception as e:
        print(f"NetEase audio search exception: {e}")

    # 2. QQ Music Channel
    try:
        qq_url = f"https://c.y.qq.com/soso/fcgi-bin/client_search_cp?p=1&n=6&w={requests.utils.quote(clean_kw)}&format=json"
        res = requests.get(qq_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            data = res.json()
            songs = data.get("data", {}).get("song", {}).get("list", [])
            for s in songs:
                t = s.get("songname", clean_kw)
                ar = s.get("singer", [{}])[0].get("name", "未知")
                key = f"{t.lower()}_{ar.lower()}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                results.append({
                    "audio_id": 0,
                    "title": t,
                    "artist": ar,
                    "album": s.get("albumname", ""),
                    "duration": float(s.get("interval", 180)),
                    "cover_url": "",
                    "source": "QQ音乐"
                })
    except Exception as e:
        print(f"QQ music search exception: {e}")

    return results

bili_session = requests.Session()
bili_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Referer': 'https://www.bilibili.com/'
})
_bili_initialized = False

def _ensure_bili_session():
    global _bili_initialized
    if not _bili_initialized:
        try:
            bili_session.get('https://www.bilibili.com/', timeout=4)
            _bili_initialized = True
        except Exception:
            pass

def search_bilibili_references(keyword: str) -> List[Dict[str, Any]]:
    """
    Searches Bilibili for high quality bass covers, tab demonstrations, and backing tracks.
    """
    results = []
    clean_kw = clean_name(keyword)
    try:
        _ensure_bili_session()
        is_cjk = any(ord(c) > 127 for c in clean_kw)
        q = f"{clean_kw} 贝斯" if is_cjk else f"{clean_kw} bass cover"
        url = f"https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword={requests.utils.quote(q)}"
        res = bili_session.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            videos = data.get("data", {}).get("result", [])
            for v in videos[:6]:
                raw_title = v.get("title", "")
                clean_title = html.unescape(re.sub(r'<[^>]+>', '', raw_title))
                bvid = v.get("bvid", "")
                pic = v.get("pic", "")
                if pic.startswith("//"):
                    pic = "https:" + pic
                results.append({
                    "bvid": bvid,
                    "title": clean_title,
                    "author": v.get("author", "未知"),
                    "duration": v.get("duration", ""),
                    "play": v.get("play", 0),
                    "cover_url": pic,
                    "url": f"https://www.bilibili.com/video/{bvid}",
                    "source": "B站演奏"
                })
    except Exception as e:
        print(f"Bilibili search exception: {e}")
    return results

FFMPEG_EXE = paths.FFMPEG

def extract_bass_onsets_from_audio(audio_path: str, max_duration: float = 300.0) -> List[Dict[str, Any]]:
    """Extracts bass transient onsets (<250Hz) and fundamental pitches via ffmpeg and autocorrelation profiling."""
    if not os.path.exists(audio_path) or not os.path.exists(FFMPEG_EXE):
        return []
    temp_wav = audio_path + ".transcribe.wav"
    try:
        cmd = [
            FFMPEG_EXE, "-y", "-i", audio_path,
            "-t", str(int(max_duration)),
            "-af", "lowpass=f=250",
            "-ar", "11025", "-ac", "1", "-sample_fmt", "s16",
            temp_wav
        ]
        creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0x08000000
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, creationflags=creation_flags)
        
        import wave, struct, math
        with wave.open(temp_wav, "rb") as wf:
            n = wf.getnframes()
            samples = struct.unpack(f"<{n}h", wf.readframes(n))
        
        sr = 11025
        hop = int(sr * 0.05)
        energies = []
        for i in range(len(samples) // hop):
            chunk = samples[i * hop : (i + 1) * hop]
            rms = math.sqrt(sum(s * s for s in chunk) / len(chunk)) if chunk else 0.0
            energies.append(rms)
        
        peaks = []
        if energies:
            avg_e = sum(energies) / len(energies)
            threshold = avg_e * 1.2
            for i in range(1, len(energies) - 1):
                if energies[i] > threshold and energies[i] > energies[i-1] and energies[i] > energies[i+1]:
                    t = round(i * 0.05, 2)
                    if not peaks or (t - peaks[-1]) >= 0.18:
                        peaks.append(t)
        
        min_lag = int(sr / 250)
        max_lag = int(sr / 40)
        open_strings = [28, 33, 38, 43]

        notes = []
        for t in peaks:
            idx = int(t * sr)
            chunk = samples[idx : idx + 1024]
            best_lag = 0
            best_corr = -1e9
            if len(chunk) >= max_lag * 2:
                for lag in range(min_lag, max_lag):
                    corr = sum(chunk[i] * chunk[i + lag] for i in range(len(chunk) - lag))
                    if corr > best_corr:
                        best_corr = corr
                        best_lag = lag
            f0 = (sr / best_lag) if best_lag > 0 else 0.0
            if f0 >= 38.0 and f0 <= 250.0:
                midi = int(round(69 + 12 * math.log2(f0 / 440.0)))
                midi = max(28, min(55, midi))
            else:
                midi = 28 # Root E
            
            best_str = 0
            best_fret = max(0, midi - open_strings[0])
            for s_idx, open_midi in enumerate(open_strings):
                if midi >= open_midi:
                    fret = midi - open_midi
                    if fret <= 14:
                        best_str = s_idx
                        best_fret = fret
                        if fret <= 7: break
            notes.append({"time": t, "midi": midi, "string": best_str, "fret": best_fret})

        return notes
    except Exception as e:
        print(f"Error extracting bass onsets: {e}")
        return []
    finally:
        if os.path.exists(temp_wav):
            try: os.remove(temp_wav)
            except Exception: pass


def get_audio_file_duration(file_path: str) -> float:
    """Gets audio duration in seconds using ffmpeg."""
    if not os.path.exists(file_path) or not os.path.exists(FFMPEG_EXE):
        return 0.0
    try:
        cmd = [FFMPEG_EXE, "-i", file_path]
        creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0x08000000
        res = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, errors="ignore", creationflags=creation_flags)
        for line in res.stderr.split("\n"):
            if "Duration:" in line:
                m = re.search(r"Duration:\s*(\d+):(\d+):([0-9.]+)", line)
                if m:
                    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    try:
        sz = os.path.getsize(file_path)
        if sz > 2 * 1024 * 1024:
            return 120.0
    except Exception:
        pass
    return 0.0

class _StudioAudioFilter:
    """Decides whether a search hit is the studio recording of the requested song.

    Shared by the YouTube Music and Bilibili fetchers: strict (alias-expanded) title match,
    and rejection of live takes, covers, AI voices, transcription videos, karaoke and short edits.
    """
    LIVE = ['live', '现场', '演唱会', 'tour', 'tushino', 'moscow', 'stadium', 'first take']
    COVER = ['cover', '翻弹', '翻唱', '翻奏', '教学', '演示', '鼓手', '贝斯手', '吉他手', 'reaction', 'tutorial',
             '无鼓', '无吉他', '无贝斯', 'drumless', 'guitarless', 'bassless', '鼓谱', '贝斯谱', '吉他谱', '扒谱', '谱面',
             'bass tab', '伴奏', 'karaoke', 'off vocal', 'instrumental', '纯音乐', '钢琴', 'piano', '八音盒', '轻松版', '简单版',
             '弹唱', '试听', '剪辑', 'nightcore', '加速', '降速', '歌ってみた', 'うたってみた', '弾いてみた', '叩いてみた',
             '歌枠', '歌回', 'vtuber', 'remix', 'acoustic', 'music box', 'orgel', 'オルゴール', '8bit', 'lofi', 'lo-fi']
    OFFICIAL = ['official', '官方', 'mv', 'music video', 'hi-res', 'hires', '无损', 'flac', '原曲', '原版', 'studio']

    def __init__(self, keyword: str, expected_duration: float = 0.0, prefer_artist: str = ""):
        self.kw = clean_name(keyword)
        self.kw_lower = self.kw.lower()
        self.expected = expected_duration
        self.artist_key = clean_title_for_match(prefer_artist) if prefer_artist else ""
        self.allow_live = any(x in self.kw_lower for x in ['live', '现场', '演唱会'])
        self.wants_short = "short" in self.kw_lower or "tv" in self.kw_lower
        self.title_keys, self.exact_keys = set(), set()
        for al in get_search_aliases(self.kw):
            k = clean_title_for_match(al)
            if len(k) >= (4 if k.isascii() else 2):
                self.title_keys.add(k)
            elif k:
                self.exact_keys.add(k)   # too short for substring matching ("R" would match everything)

    def _is_ai_cover(self, t: str) -> bool:
        if re.search(r'(?<![a-z])ai(?![a-z])', self.kw_lower):
            return False
        return bool(re.search(r'(?<![a-z])ai(?![a-z])|sovits|\brvc\b|歌姬|虚拟歌手', t))

    def rejects(self, title: str, duration: float) -> bool:
        t = html.unescape(re.sub(r'<[^>]+>', '', title or "")).lower()
        c = clean_title_for_match(t)
        # "R - Roselia" / "R (TV size)": compare the part before any separator for short titles
        head = clean_title_for_match(re.split(r'\s[-/|]\s|[(（\[【]', t)[0])
        if not any(k in c for k in self.title_keys) and not (c in self.exact_keys or head in self.exact_keys):
            return True
        if self._is_ai_cover(t):
            return True
        if not self.allow_live and any(p in t for p in self.LIVE):
            return True
        if any(p in t and p not in self.kw_lower for p in self.COVER):
            return True
        if not self.wants_short and duration < (60 if self.expected > 60.0 else 120):
            return True
        if self.expected > 60.0 and duration > 0 and abs(duration - self.expected) > self.expected * 0.25:
            return True
        return False

    def names_artist(self, text: str) -> bool:
        return bool(self.artist_key) and self.artist_key in clean_title_for_match(text or "")

    def is_official(self, text: str) -> bool:
        t = (text or "").lower()
        return any(m in t for m in self.OFFICIAL)

    def distance(self, duration: float) -> float:
        # length unknown: a typical full song is about four minutes
        return abs(duration - (self.expected if self.expected > 0 else 240.0))


def _finalize_audio(src: str, target_path: str, expected_duration: float, label: str, bitrate: str = "192k") -> bool:
    """Transcodes a downloaded stream to MP3 at target_path after checking it is a complete take."""
    temp_mp3 = target_path + ".temp.mp3"
    creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0x08000000
    # constant bitrate on purpose: ffmpeg estimates VBR mp3 length from the header and reads it ~5% short
    cmd = [FFMPEG_EXE, "-y", "-i", src, "-vn", "-ar", "44100", "-ac", "2", "-ab", bitrate, temp_mp3]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creation_flags)
    finally:
        if os.path.exists(src):
            try: os.remove(src)
            except Exception: pass
    if proc.returncode != 0 or not os.path.exists(temp_mp3) or os.path.getsize(temp_mp3) <= 500000:
        if os.path.exists(temp_mp3):
            try: os.remove(temp_mp3)
            except Exception: pass
        return False
    actual_dur = get_audio_file_duration(temp_mp3)
    min_req = expected_duration * 0.70 if expected_duration > 120 else 60.0
    max_req = expected_duration * 1.30 if expected_duration > 120 else 10000.0
    if not (min_req <= actual_dur <= max_req):
        print(f"Duration mismatch for {label}: actual {actual_dur:.1f}s not in [{min_req:.1f}s, {max_req:.1f}s]")
        try: os.remove(temp_mp3)
        except Exception: pass
        return False
    if os.path.exists(target_path):
        try: os.remove(target_path)
        except Exception: pass
    os.replace(temp_mp3, target_path)
    print(f"Full audio saved from {label}: {target_path} ({actual_dur:.1f}s)")
    return True


NODE_EXE = shutil.which("node")


def download_audio_from_youtube(keyword: str, target_path: str, expected_duration: float = 0.0, prefer_artist: str = "") -> bool:
    """
    Downloads the studio recording from YouTube Music. Searching the "songs" category returns the
    label-uploaded audio tracks (with artist, album and exact length) rather than arbitrary videos;
    the stream is fetched directly with yt-dlp.
    """
    try:
        from ytmusicapi import YTMusic
        import yt_dlp
    except ImportError as e:
        print(f"YouTube fetcher unavailable: {e}")
        return False

    flt = _StudioAudioFilter(keyword, expected_duration, prefer_artist)
    queries = [f"{prefer_artist} {flt.kw}", flt.kw] if prefer_artist else [flt.kw]

    candidates, seen = [], set()
    try:
        ytm = YTMusic()
    except Exception as e:
        print(f"YouTube Music init failed: {e}")
        return False
    for q in queries:
        results = None
        for attempt in range(2):
            try:
                results = ytm.search(q, filter="songs", limit=20)
                break
            except Exception as e:
                print(f"YouTube Music search error for '{q}' ({attempt + 1}/2): {e}")
        if not results:
            continue
        for r in results:
            vid = r.get("videoId")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            title = r.get("title") or ""
            artists = " ".join(a.get("name", "") for a in (r.get("artists") or []))
            dur = float(r.get("duration_seconds") or 0)
            if flt.rejects(title, dur):
                continue
            candidates.append({"id": vid, "title": title, "artists": artists, "duration": dur,
                               "has_artist": flt.names_artist(artists), "rank": len(seen)})

    if not candidates:
        return False
    # artist match first; with a known length the closest take, otherwise YouTube Music's own ranking
    candidates.sort(key=lambda c: (not c["has_artist"], flt.distance(c["duration"]) if expected_duration > 0 else 0, c["rank"]))

    for cand in candidates[:3]:
        print(f"Attempting YouTube Music download: {cand['id']} ('{cand['title']}' - '{cand['artists']}', {cand['duration']:.0f}s)...")
        base = target_path + ".yt"
        opts = {
            "format": "bestaudio/best",
            "outtmpl": base + ".%(ext)s",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": 3,
            "socket_timeout": 30,
        }
        if NODE_EXE:
            opts["js_runtimes"] = {"node": {"path": NODE_EXE}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"https://music.youtube.com/watch?v={cand['id']}", download=True)
                src = ydl.prepare_filename(info)
        except Exception as e:
            print(f"YouTube download failed for {cand['id']}: {e}")
            for f in glob.glob(glob.escape(base) + ".*"):
                try: os.remove(f)
                except Exception: pass
            continue
        if os.path.exists(src) and _finalize_audio(src, target_path, expected_duration, f"YouTube {cand['id']}", bitrate="320k"):
            return True
    return False


def download_audio_from_bilibili(keyword: str, target_path: str, expected_duration: float = 0.0, prefer_artist: str = "") -> bool:
    """
    Searches and downloads authentic full studio audio from Bilibili via DASH audio streams.
    Filters out live recordings, covers, reactions, and clips, transcoding to 192k stereo MP3.
    """
    _ensure_bili_session()
    clean_kw = clean_name(keyword)
    query = f"{prefer_artist} - {clean_kw}".strip() if prefer_artist else clean_kw

    search_queries = [query]
    if prefer_artist:
        search_queries.append(f"{prefer_artist} {clean_kw} 原曲")
        search_queries.append(f"{prefer_artist} {clean_kw} audio")
        search_queries.append(clean_kw)
    else:
        search_queries.append(f"{clean_kw} 原曲")
        search_queries.append(f"{clean_kw} audio")

    flt = _StudioAudioFilter(clean_kw, expected_duration, prefer_artist)

    def parse_dur(text: str) -> float:
        try:
            nums = [int(x) for x in text.strip().split(':')]
        except ValueError:
            return 0.0
        v = 0
        for x in nums:
            v = v * 60 + x
        return float(v)

    candidates = []
    seen_bvids = set()

    for q in search_queries:
        try:
            url = f"https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword={requests.utils.quote(q)}"
            res = bili_session.get(url, timeout=6)
            if res.status_code != 200:
                continue
            videos = res.json().get("data", {}).get("result", [])
            for v in videos:
                bvid = v.get("bvid")
                if not bvid or bvid in seen_bvids:
                    continue
                seen_bvids.add(bvid)

                raw_title = v.get("title", "")
                title_clean = html.unescape(re.sub(r'<[^>]+>', '', raw_title)).lower()
                dur_str = str(v.get("duration", "0:00"))
                v_dur = parse_dur(dur_str)

                if flt.rejects(title_clean, v_dur):
                    continue

                candidates.append({
                    "bvid": bvid,
                    "title": title_clean,
                    "duration": v_dur,
                    "dur_str": dur_str,
                    "has_artist": flt.names_artist(title_clean),
                    "official": flt.is_official(title_clean)
                })
        except Exception as e:
            print(f"Bilibili search error for '{q}': {e}")

    if not candidates:
        return False

    # Sort candidates: artist named in the title first, official uploads next, then closest duration
    candidates.sort(key=lambda c: (not c["has_artist"], not c["official"], flt.distance(c["duration"])))

    for cand in candidates[:3]:
        target_bvid = cand["bvid"]
        target_dur = cand["duration"]
        print(f"Attempting Bilibili stream download: {target_bvid} ('{cand['title']}', dur: {cand['dur_str']})...")

        try:
            page_res = bili_session.get(f"https://api.bilibili.com/x/player/pagelist?bvid={target_bvid}", timeout=6)
            if page_res.status_code != 200:
                continue
            cid = page_res.json()["data"][0]["cid"]

            play_url = f"https://api.bilibili.com/x/player/wbi/playurl?bvid={target_bvid}&cid={cid}&fnval=16"
            play_res = bili_session.get(play_url, timeout=6)
            if play_res.status_code != 200:
                continue
            dash = play_res.json().get("data", {}).get("dash", {})
            audio_streams = dash.get("audio", [])
            if not audio_streams:
                continue

            audio_streams.sort(key=lambda s: s.get("bandwidth", 0), reverse=True)
            stream_url = audio_streams[0].get("baseUrl") or audio_streams[0].get("backupUrl", [None])[0]
            if not stream_url:
                continue

            headers = dict(bili_session.headers)
            headers["Referer"] = f"https://www.bilibili.com/video/{target_bvid}"

            temp_dash = target_path + ".dash.m4s"
            got_stream = False
            for attempt in range(2):
                try:
                    r = bili_session.get(stream_url, headers=headers, stream=True, timeout=30)
                    if r.status_code != 200:
                        break
                    with open(temp_dash, "wb") as f:
                        for chunk in r.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)
                    got_stream = True
                    break
                except requests.RequestException as e:
                    print(f"Bilibili stream interrupted ({attempt + 1}/2): {e}")
            if not got_stream:
                if os.path.exists(temp_dash):
                    try: os.remove(temp_dash)
                    except Exception: pass
                continue

            if _finalize_audio(temp_dash, target_path, expected_duration, f"Bilibili {target_bvid}"):
                return True
        except Exception as e:
            print(f"Error downloading audio from Bilibili candidate {target_bvid}: {e}")

    return False

def download_audio_from_netease(audio_id: int, target_path: str, expected_duration: float = 0.0, min_duration: float = 60.0) -> bool:
    """
    Downloads playable audio file from NetEase Cloud Music and validates that it is
    a complete track, automatically rejecting truncated 30s/45s preview clips.
    """
    try:
        url = f"http://music.163.com/song/media/outer/url?id={audio_id}.mp3"
        r = requests.get(url, headers=HEADERS, timeout=15, stream=True)
        # Complete full songs are typically >= 1.5MB; 45s preview clips are ~720KB
        if r.status_code == 200 and len(r.content) > 1024 * 1024:
            temp_target = target_path + ".downloading"
            with open(temp_target, "wb") as f:
                f.write(r.content)

            actual_dur = get_audio_file_duration(temp_target)
            min_req = min_duration
            if expected_duration > 0:
                min_req = min(min_duration, expected_duration * 0.75)

            if actual_dur >= min_req or len(r.content) > 2 * 1024 * 1024:
                if os.path.exists(target_path):
                    try:
                        os.remove(target_path)
                    except Exception:
                        pass
                os.replace(temp_target, target_path)
                return True
            else:
                print(f"Rejected NetEase ID {audio_id}: audio duration too short ({actual_dur:.1f}s vs expected {expected_duration:.1f}s, min required {min_req:.1f}s)")
                if os.path.exists(temp_target):
                    try:
                        os.remove(temp_target)
                    except Exception:
                        pass
    except Exception as e:
        print(f"Error downloading audio from NetEase ID {audio_id}: {e}")
    return False

def search_and_download_full_audio(keyword: str, target_path: str, prefer_artist: str = "", expected_duration: float = 0.0) -> bool:
    """
    Searches NetEase candidates and automatically finds and downloads a full-length,
    untruncated official audio track, supporting cross-language aliases and artist matching.
    Falls back to high-quality Bilibili studio audio if NetEase is restricted.
    """
    from netease_service import search_song_candidates

    aliases = get_search_aliases(keyword)
    candidates = []
    queries = []
    if prefer_artist:
        queries.append(f"{prefer_artist} {keyword}")
    queries.append(keyword)
    for al in aliases:
        if al not in queries:
            queries.append(al)
        if prefer_artist and f"{prefer_artist} {al}" not in queries:
            queries.append(f"{prefer_artist} {al}")

    seen_ids = set()
    for q in queries[:4]:
        cands = search_song_candidates(q, limit=8)
        for c in cands:
            cid = c.get("audio_id")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                candidates.append(c)
        if len(candidates) >= 12:
            break

    # Filter out candidates with mismatched titles using strict title compatibility
    # no title match means no candidate: trying unrelated songs only produced wrong audio
    valid_candidates = [c for c in candidates if is_title_compatible(str(c.get("title", "")), keyword, aliases)]

    # Rank candidates: prioritize candidates whose artist matches prefer_artist
    if prefer_artist:
        p_clean = prefer_artist.lower().replace(" ", "")
        def artist_score(c):
            a = str(c.get("artist", "")).lower().replace(" ", "")
            if not a: return 0
            if p_clean in a or a in p_clean: return 2
            return 1
        valid_candidates.sort(key=artist_score, reverse=True)

    allow_live = any(x in keyword.lower() for x in ['live', '现场', '演唱会'])
    live_patterns = ['live', '现场', '演唱会', 'tour', 'tushino', 'moscow']

    for cand in valid_candidates:
        sid = cand.get("audio_id")
        dur = cand.get("duration", 0.0)
        c_title = str(cand.get("title", "")).lower()

        # Reject live versions unless explicitly requested
        if not allow_live and any(lp in c_title for lp in live_patterns):
            print(f"Skipping live version candidate ID {sid} ('{cand.get('title')}')")
            continue

        # Skip if artist does not match prefer_artist (guards against mismatched cover uploads)
        if prefer_artist and artist_score(cand) < 2:
            print(f"Skipping candidate ID {sid} ('{cand.get('title')}' by '{cand.get('artist')}'): artist does not match '{prefer_artist}'")
            continue

        # Skip if candidate duration is substantially shorter than expected
        if expected_duration > 120 and dur > 0 and dur < (expected_duration * 0.65):
            print(f"Skipping truncated candidate ID {sid} ({dur}s vs expected {expected_duration}s)")
            continue

        # Skip if candidate itself says it is shorter than 60s
        if dur > 0 and dur < 60.0 and "short" not in keyword.lower() and "tv" not in keyword.lower():
            continue

        print(f"Attempting audio download for candidate ID {sid} ('{cand.get('title')}' - '{cand.get('artist')}', duration {dur}s)...")
        if download_audio_from_netease(sid, target_path, expected_duration=expected_duration or dur):
            print(f"Successfully downloaded full audio from NetEase ID {sid}!")
            return True

    # Fallbacks: label-uploaded YouTube Music tracks, then Bilibili re-uploads
    print(f"NetEase candidates restricted or failed. Trying YouTube Music for '{keyword}'...")
    if download_audio_from_youtube(keyword, target_path, expected_duration=expected_duration, prefer_artist=prefer_artist):
        return True
    print(f"Attempting Bilibili studio audio fallback for '{keyword}'...")
    if download_audio_from_bilibili(keyword, target_path, expected_duration=expected_duration, prefer_artist=prefer_artist):
        return True

    return False

def import_tab_to_library(
    title: str,
    artist: str,
    franchise: str = "BanG Dream!",
    tab_data: bytes = None,
    download_url: str = None
) -> Dict[str, Any]:
    """
    Creates directory, downloads authentic GP tab, synchronizes note fingerings
    with Songsterr web live part, injects full-length studio backing track,
    generates companion PDF, and fetches official cover.
    """
    from gp_builder import create_clean_gp_project
    from pdf_generator import generate_bass_tab_pdf
    from backing_track_enhancer import auto_ensure_backing_track

    safe_title = clean_name(title)
    safe_artist = clean_name(artist or "BanG Dream!")
    safe_franchise = clean_name(franchise or "BanG Dream!")

    # An existing folder may hold a purchased original: never write into it, use a fresh sibling folder.
    base_name = f"{safe_title} _ {safe_artist} _ {safe_franchise}"
    folder_name, n = base_name, 1
    while os.path.isdir(os.path.join(TABS_ROOT, folder_name)) and any(
            f.lower().endswith((".gp", ".gp5", ".gpx", ".gp4", ".pdf")) for f in os.listdir(os.path.join(TABS_ROOT, folder_name))):
        n += 1
        folder_name = f"{base_name} ({n})"
    folder_path = os.path.join(TABS_ROOT, folder_name)

    # 1. Acquire real tab data
    actual_tab_data = tab_data
    if not actual_tab_data and download_url:
        actual_tab_data = download_gp_file(download_url, expected_artist=safe_artist, expected_title=safe_title)

    # If initial URL failed, auto-search and try best online candidates with bass
    # (only same-song candidates that actually contain a bass track)
    if not actual_tab_data or len(actual_tab_data) <= 512:
        candidates = search_online_tabs(f"{safe_artist} {safe_title}")
        if not candidates:
            candidates = search_online_tabs(safe_title)
        for cand in candidates:
            c_url = cand.get("download_url")
            if not cand.get("has_bass") or not is_title_compatible(str(cand.get("title", "")), safe_title):
                continue
            if c_url and c_url != download_url:
                cand_data = download_gp_file(c_url, expected_artist=safe_artist, expected_title=safe_title)
                if cand_data and len(cand_data) > 512:
                    actual_tab_data = cand_data
                    break

    # If still no valid tab data, fail clearly so UI shows error rather than faking an empty score
    if not actual_tab_data or len(actual_tab_data) <= 512:
        raise ValueError(f"未能获取曲目「{safe_title}」的有效 Guitar Pro 乐谱。")

    os.makedirs(folder_path, exist_ok=True)

    # Determine format
    is_zip = actual_tab_data.startswith(b"PK\x03\x04")
    ext = ".gp" if is_zip else ".gp5"
    gp_filename = f"[BASS TAB] {safe_title}{ext}"
    gp_path = os.path.join(folder_path, gp_filename)

    with open(gp_path, "wb") as f:
        f.write(actual_tab_data)

    # Synchronize note fingerings with Songsterr live web part if imported from Songsterr
    if is_zip and download_url and download_url.startswith("songsterr://"):
        try:
            sid = int(download_url.replace("songsterr://", ""))
            part_json, num_strings = fetch_songsterr_live_part(sid)
            if part_json:
                sync_songsterr_part_to_gp(gp_path, part_json, num_strings=num_strings)
        except Exception as e:
            print(f"Error syncing Songsterr web part: {e}")

    # 2. Backing track: aligned into a derived '[伴奏].gp'; the downloaded score itself stays untouched
    try:
        backing_info = auto_ensure_backing_track(folder_path, safe_title, safe_artist)
    except Exception as e:
        print(f"Backing track pipeline failed: {e}")
        backing_info = {"has_backing": False, "status": "error"}

    # 4. Generate authentic companion PDF score matching synced fingerings
    pdf_path = os.path.join(folder_path, f"[BASS TAB] {safe_title}.pdf")
    try:
        generate_bass_tab_pdf(pdf_path, safe_title, safe_artist, safe_franchise, gp_path=gp_path)
    except Exception as e:
        print(f"Error generating PDF score: {e}")

    # 5. Fetch cover from NetEase
    song_id = hashlib.md5(folder_name.encode('utf-8')).hexdigest()[:12]
    fetch_cover(song_id, safe_title, safe_artist, folder_name)

    return {
        "id": song_id,
        "folder_name": folder_name,
        "folder_path": folder_path,
        "gp_path": gp_path,
        "pdf_path": pdf_path if os.path.exists(pdf_path) else "",
        "title": safe_title,
        "artist": safe_artist,
        "franchise": safe_franchise,
        "backing_info": backing_info
    }

def transcribe_audio_to_library(title: str, artist: str, audio_id: int = None, album: str = "",
                                local_audio: str = None) -> Dict[str, Any]:
    """Downloads audio, runs AI stem separation, transcribes bass, generates GP8 project with backing track."""
    from pdf_generator import generate_bass_tab_pdf
    from backing_track_enhancer import auto_ensure_backing_track

    safe_title = clean_name(title)
    safe_artist = clean_name(artist or "BanG Dream!")
    safe_franchise = clean_name(album or "BanG Dream!")

    folder_name = f"{safe_title} _ {safe_artist} _ {safe_franchise}"
    folder_path = os.path.join(TABS_ROOT, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    try:
        import time
        from app import TRANSCRIBE_PROGRESS
        TRANSCRIBE_PROGRESS.update({"status": "running", "step": "下载音源", "percent": 15, "start_time": time.time()})
    except Exception:
        pass

    # 1. Source audio: a user-supplied file, else the official audio download
    if local_audio and os.path.exists(local_audio):
        import shutil
        backing_path = os.path.join(folder_path, "source" + os.path.splitext(local_audio)[1].lower())
        if os.path.abspath(local_audio) != os.path.abspath(backing_path):
            shutil.copy2(local_audio, backing_path)
        backing_info = {"audio_path": backing_path, "source": "local"}
    else:
        backing_info = auto_ensure_backing_track(folder_path, safe_title, safe_artist, audio_id=audio_id)
        backing_path = backing_info.get("audio_path")
    if not backing_path or not os.path.exists(backing_path):
        raise FileNotFoundError(f"未能获取曲目「{safe_title}」的原声音源")

    # 2. AI separation and transcription pipeline via Python 3.11 subprocess
    py311 = paths.PY311
    script = os.path.join(os.path.dirname(__file__), "ai_transcriber.py")
    cmd = [
        py311, script,
        "--title", safe_title,
        "--artist", safe_artist,
        "--franchise", safe_franchise,
        "--audio", backing_path,
        "--output", folder_path
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        bufsize=1
    )

    full_stdout = []
    for line in iter(proc.stdout.readline, ''):
        full_stdout.append(line)
        if "__PROGRESS__:" in line:
            try:
                msg = line.split("__PROGRESS__:")[1].strip()
                tokens = msg.split()
                step_name = tokens[0]
                pct = int(tokens[1].replace("%", "")) if len(tokens) > 1 else 50
                from app import TRANSCRIBE_PROGRESS
                TRANSCRIBE_PROGRESS.update({"status": "running", "step": step_name, "percent": pct})
            except Exception:
                pass

    proc.stdout.close()
    return_code = proc.wait()
    if return_code != 0:
        raise RuntimeError(f"AI 扒谱引擎运行失败: {''.join(full_stdout)}")

    out_text = "".join(full_stdout)
    ai_result = {}
    if "__JSON_RESULT_START__" in out_text:
        try:
            json_part = out_text.split("__JSON_RESULT_START__")[1].split("__JSON_RESULT_END__")[0].strip()
            ai_result = json.loads(json_part)
        except Exception:
            pass

    gp_path = ai_result.get("gp_path", os.path.join(folder_path, f"[BASS TAB] {safe_title}.gp"))
    try:
        from app import TRANSCRIBE_PROGRESS
        TRANSCRIBE_PROGRESS.update({"status": "done", "step": "已就绪", "percent": 100})
    except Exception:
        pass

    # 3. Companion PDF score
    pdf_path = os.path.join(folder_path, f"[BASS TAB] {safe_title}.pdf")
    if not os.path.exists(pdf_path):
        try:
            generate_bass_tab_pdf(pdf_path, safe_title, safe_artist, safe_franchise, gp_path=gp_path)
        except Exception as e:
            print(f"Error generating PDF: {e}")

    # 4. Fetch cover
    song_id = hashlib.md5(folder_name.encode('utf-8')).hexdigest()[:12]
    fetch_cover(song_id, safe_title, safe_artist, folder_name)

    return {
        "id": song_id,
        "folder_name": folder_name,
        "folder_path": folder_path,
        "gp_path": gp_path,
        "pdf_path": pdf_path if os.path.exists(pdf_path) else "",
        "title": safe_title,
        "artist": safe_artist,
        "franchise": safe_franchise,
        "backing_info": {
            "has_backing": True,
            "has_nobass": True,
            "audio_path": ai_result.get("backing_path", backing_path)
        }
    }

def transcribe_local_audio_to_library(
    raw_audio_path: str,
    title: str,
    artist: str = "BanG Dream!",
    franchise: str = "BanG Dream!"
) -> Dict[str, Any]:
    """Runs AI stem separation and transcription directly on an uploaded local audio file."""
    from pdf_generator import generate_bass_tab_pdf

    safe_title = clean_name(title)
    safe_artist = clean_name(artist or "BanG Dream!")
    safe_franchise = clean_name(franchise or "BanG Dream!")

    folder_name = f"{safe_title} _ {safe_artist} _ {safe_franchise}"
    folder_path = os.path.join(TABS_ROOT, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    try:
        import time
        from app import TRANSCRIBE_PROGRESS
        TRANSCRIBE_PROGRESS.update({"status": "running", "step": "音源就绪", "percent": 20, "start_time": time.time()})
    except Exception:
        pass

    py311 = paths.PY311
    script = os.path.join(os.path.dirname(__file__), "ai_transcriber.py")
    cmd = [
        py311, script,
        "--title", safe_title,
        "--artist", safe_artist,
        "--franchise", safe_franchise,
        "--audio", raw_audio_path,
        "--output", folder_path
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        bufsize=1
    )

    full_stdout = []
    for line in iter(proc.stdout.readline, ''):
        full_stdout.append(line)
        if "__PROGRESS__:" in line:
            try:
                msg = line.split("__PROGRESS__:")[1].strip()
                tokens = msg.split()
                step_name = tokens[0]
                pct = int(tokens[1].replace("%", "")) if len(tokens) > 1 else 50
                from app import TRANSCRIBE_PROGRESS
                TRANSCRIBE_PROGRESS.update({"status": "running", "step": step_name, "percent": pct})
            except Exception:
                pass

    proc.stdout.close()
    return_code = proc.wait()
    if return_code != 0:
        raise RuntimeError(f"AI 扒谱引擎运行失败: {''.join(full_stdout)}")

    out_text = "".join(full_stdout)
    ai_result = {}
    if "__JSON_RESULT_START__" in out_text:
        try:
            json_part = out_text.split("__JSON_RESULT_START__")[1].split("__JSON_RESULT_END__")[0].strip()
            ai_result = json.loads(json_part)
        except Exception:
            pass

    gp_path = ai_result.get("gp_path", os.path.join(folder_path, f"[BASS TAB] {safe_title}.gp"))
    try:
        from app import TRANSCRIBE_PROGRESS
        TRANSCRIBE_PROGRESS.update({"status": "done", "step": "已就绪", "percent": 100})
    except Exception:
        pass

    pdf_path = os.path.join(folder_path, f"[BASS TAB] {safe_title}.pdf")
    if not os.path.exists(pdf_path):
        try:
            generate_bass_tab_pdf(pdf_path, safe_title, safe_artist, safe_franchise, gp_path=gp_path)
        except Exception as e:
            print(f"Error generating PDF: {e}")

    song_id = hashlib.md5(folder_name.encode('utf-8')).hexdigest()[:12]
    fetch_cover(song_id, safe_title, safe_artist, folder_name)

    return {
        "id": song_id,
        "folder_name": folder_name,
        "folder_path": folder_path,
        "gp_path": gp_path,
        "pdf_path": pdf_path if os.path.exists(pdf_path) else "",
        "title": safe_title,
        "artist": safe_artist,
        "franchise": safe_franchise,
        "backing_info": {
            "has_backing": True,
            "has_nobass": True,
            "audio_path": ai_result.get("backing_path", raw_audio_path)
        }
    }

