import os
import re
import zipfile
import subprocess
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional

FFMPEG_EXE = r"E:\BassStation\tools\Ultimate Vocal Remover\ffmpeg.exe"

NOTE_VALUES_IN_QUARTERS = {
    "Whole": 4.0,
    "Half": 2.0,
    "Quarter": 1.0,
    "Eighth": 0.5,
    "16th": 0.25,
    "32nd": 0.125,
    "64th": 0.0625,
}

def get_score_first_sounding_offset(gp_path: str) -> Dict[str, Any]:
    """
    Parses score.gpif within the .gp archive to determine:
    1. Score initial tempo (BPM)
    2. First actual sounding note timestamp across all tracks (accounting for initial rests,
       pickup measures, and anacrusis / 弱起拍).
    """
    if not os.path.exists(gp_path):
        return {"tempo": 120.0, "first_sounding_offset_sec": 0.0, "first_sounding_offset_ms": 0.0, "is_weak_beat": False}

    try:
        with zipfile.ZipFile(gp_path, "r") as z:
            if "Content/score.gpif" not in z.namelist():
                return {"tempo": 120.0, "first_sounding_offset_sec": 0.0, "first_sounding_offset_ms": 0.0, "is_weak_beat": False}
            xml_bytes = z.read("Content/score.gpif")
    except Exception:
        return {"tempo": 120.0, "first_sounding_offset_sec": 0.0, "first_sounding_offset_ms": 0.0, "is_weak_beat": False}

    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        try:
            cleaned = xml_bytes.decode("utf-8", errors="ignore")
            root = ET.fromstring(cleaned)
        except Exception:
            return {"tempo": 120.0, "first_sounding_offset_sec": 0.0, "first_sounding_offset_ms": 0.0, "is_weak_beat": False}

    # 1. Parse Tempo
    tempo = 120.0
    for auto in root.findall(".//Automation"):
        if auto.findtext("Type") == "Tempo":
            val = auto.findtext("Value", "").strip()
            if val:
                try:
                    bpm = float(val.split()[0])
                    if 30 <= bpm <= 350:
                        tempo = bpm
                        break
                except Exception:
                    pass

    sec_per_quarter = 60.0 / tempo

    # 2. Parse Rhythms
    rhythms = {}
    for r in root.findall(".//Rhythms/Rhythm"):
        r_id = r.get("id")
        note_val = r.findtext("NoteValue", "Quarter")
        q_len = NOTE_VALUES_IN_QUARTERS.get(note_val, 1.0)
        
        dots = r.find("AugmentationDot")
        if dots is not None:
            cnt = int(dots.get("count", 1))
            if cnt == 1:
                q_len *= 1.5
            elif cnt == 2:
                q_len *= 1.75
        
        tuplet = r.find("PrimaryTuplet")
        if tuplet is not None:
            num = float(tuplet.get("num", 3))
            den = float(tuplet.get("den", 2))
            if num > 0:
                q_len *= (den / num)
                
        rhythms[r_id] = q_len * sec_per_quarter

    # 3. Index entities
    voices = {v.get("id"): v for v in root.findall(".//Voice") if v.get("id")}
    beats = {b.get("id"): b for b in root.findall(".//Beat") if b.get("id")}
    notes = {n.get("id"): n for n in root.findall(".//Note") if n.get("id")}

    mbars = root.findall(".//MasterBar")
    
    current_time = 0.0
    earliest_note_time = None

    for mb in mbars[:8]:
        time_sig = mb.findtext("Time", "4/4")
        m_bar = re.match(r"(\d+)/(\d+)", time_sig)
        if m_bar:
            num = int(m_bar.group(1))
            den = int(m_bar.group(2))
            bar_duration_quarters = num * (4.0 / den)
            bar_duration = bar_duration_quarters * sec_per_quarter
        else:
            bar_duration = 4.0 * sec_per_quarter

        bar_ids = mb.findtext("Bars", "").split()
        
        for bar_id in bar_ids:
            bar_elem = root.find(f".//Bar[@id='{bar_id}']")
            if bar_elem is None:
                continue
            
            v_ids = bar_elem.findtext("Voices", "").split()
            for v_id in v_ids:
                if v_id == "-1" or v_id not in voices:
                    continue
                v = voices[v_id]
                b_ids = v.findtext("Beats", "").split()
                
                t_beat = current_time
                for b_id in b_ids:
                    if b_id not in beats:
                        continue
                    b = beats[b_id]
                    
                    r_ref = b.find("Rhythm")
                    r_id = r_ref.get("ref") if r_ref is not None else None
                    b_dur = rhythms.get(r_id, sec_per_quarter)
                    
                    note_ids = b.findtext("Notes", "").split()
                    has_note = any(nid in notes and nid != "-1" for nid in note_ids)
                    
                    if has_note:
                        if earliest_note_time is None or t_beat < earliest_note_time:
                            earliest_note_time = t_beat
                    
                    t_beat += b_dur

        if earliest_note_time is not None:
            break
        
        current_time += bar_duration

    first_offset = earliest_note_time if earliest_note_time is not None else 0.0
    
    return {
        "tempo": tempo,
        "first_sounding_offset_sec": first_offset,
        "first_sounding_offset_ms": round(first_offset * 1000, 1),
        "is_weak_beat": first_offset > 0.01
    }

_SR_CACHE: Dict[str, int] = {}

def get_audio_sample_rate(audio_path: str) -> int:
    """Extracts actual audio sample rate from file using ffmpeg with no console window."""
    if not os.path.exists(audio_path):
        return 44100
    if audio_path in _SR_CACHE:
        return _SR_CACHE[audio_path]
    if not os.path.exists(FFMPEG_EXE):
        return 44100
    try:
        cmd = [FFMPEG_EXE, "-i", audio_path]
        creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0x08000000
        res = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, errors="ignore", creationflags=creation_flags)
        for line in res.stderr.split("\n"):
            if "Audio:" in line:
                m = re.search(r"(\d+)\s*Hz", line)
                if m:
                    rate = int(m.group(1))
                    _SR_CACHE[audio_path] = rate
                    return rate
    except Exception:
        pass
    _SR_CACHE[audio_path] = 44100
    return 44100

def detect_precision_audio_onset(audio_path: str, max_duration_sec: float = 8.0) -> float:
    """
    Sample-accurate transient onset detector:
    Extracts raw 16-bit PCM samples via ffmpeg stdout pipe and performs millisecond-level
    RMS and peak energy envelope analysis to detect the true attack onset of the first musical note.
    """
    if not os.path.exists(audio_path) or not os.path.exists(FFMPEG_EXE):
        return 0.0

    cmd = [
        FFMPEG_EXE, "-v", "quiet", "-i", audio_path,
        "-t", str(max_duration_sec),
        "-ac", "1", "-ar", "44100",
        "-f", "s16le", "-"
    ]
    try:
        creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0x08000000
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, creationflags=creation_flags)
        raw_pcm = p.stdout
    except Exception:
        return 0.0

    if not raw_pcm:
        return 0.0

    import struct
    import math

    n_samples = len(raw_pcm) // 2
    raw_samples = struct.unpack(f"{n_samples}h", raw_pcm)

    # Remove DC offset to prevent false noise-floor elevation
    dc_mean = sum(raw_samples) / float(len(raw_samples)) if raw_samples else 0.0
    samples = [s - dc_mean for s in raw_samples]

    # 1ms frames (44 samples @ 44.1kHz)
    ms_samples = 44
    n_ms = len(samples) // ms_samples
    if n_ms < 10:
        return 0.0

    peaks = []
    rmss = []
    for i in range(n_ms):
        chk = samples[i * ms_samples : (i + 1) * ms_samples]
        p_val = max(abs(s) for s in chk)
        r_val = math.sqrt(sum(s * s for s in chk) / ms_samples)
        peaks.append(p_val)
        rmss.append(r_val)

    # Baseline noise floor from the quietest 50ms in the first 300ms
    first_window_ms = min(300, n_ms)
    min_rms = 999999.0
    for i in range(0, max(1, first_window_ms - 50)):
        sub_rms = sum(rmss[i:i+50]) / 50.0
        if sub_rms < min_rms:
            min_rms = sub_rms
    noise_floor_rms = max(min_rms, 10.0)

    # Thresholds for musical attack transient
    onset_threshold_peak = max(800, noise_floor_rms * 15.0)
    onset_threshold_rms = max(200.0, noise_floor_rms * 8.0)

    # Locate first frame that crosses threshold
    detected_ms = None
    for i in range(1, n_ms):
        if peaks[i] >= onset_threshold_peak or rmss[i] >= onset_threshold_rms:
            # Trace backward up to 15ms to find where the waveform began its rapid rise
            foot = i
            for j in range(i, max(0, i - 15), -1):
                if peaks[j] < max(120, noise_floor_rms * 3.0) and rmss[j] < max(50.0, noise_floor_rms * 2.0):
                    foot = j + 1
                    break
            detected_ms = foot
            break

    if detected_ms is None:
        return 0.0

    return detected_ms / 1000.0

def detect_audio_lead_in(audio_path: str) -> float:
    """
    Detects lead-in silence before music begins in seconds.
    Uses sample-accurate PCM transient analysis first, with fallback to ffmpeg silencedetect.
    """
    prec_onset = detect_precision_audio_onset(audio_path)
    if prec_onset > 0.005:
        return prec_onset

    if not os.path.exists(audio_path) or not os.path.exists(FFMPEG_EXE):
        return 0.0

    # Fallback to ffmpeg silence detection (strictly for lead-in at track start)
    cmd = [
        FFMPEG_EXE, "-i", audio_path,
        "-t", "10",
        "-af", "silencedetect=noise=-30dB:d=0.03",
        "-f", "null", "-"
    ]
    creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0x08000000
    res = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore', creationflags=creation_flags)
    current_silence_start = 0.0
    for line in res.stderr.split('\n'):
        if 'silence_start:' in line:
            m = re.search(r'silence_start:\s*([0-9.]+)', line)
            if m:
                current_silence_start = float(m.group(1))
        if 'silence_end:' in line:
            m = re.search(r'silence_end:\s*([0-9.]+)', line)
            if m:
                end_time = float(m.group(1))
                # Only accept as lead-in if the silence started right at or near beginning (< 0.5s)
                if current_silence_start <= 0.5 and end_time < 10.0:
                    return end_time

    return 0.0

def resolve_audio_file(gp_path: str, extract_if_embedded: bool = True) -> Optional[str]:
    """
    Finds or extracts companion audio for a GP score:
    1. Checks if an audio file already exists in the same directory:
       - Prefers files named 'backing.*'
       - Otherwise any .mp3, .wav, .flac, .m4a, .ogg
    2. If not found on disk, checks if gp_path contains an embedded audio asset in Content/Assets/.
       If found:
       - If extract_if_embedded is True, extracts it to 'backing.<ext>' in the same directory
         (or to cache/audio_extracted/ if directory is not writable).
       - Returns the path to this audio file.
    3. Returns None if no audio exists.
    """
    if not os.path.exists(gp_path):
        return None

    folder = os.path.dirname(gp_path)
    valid_exts = ('.mp3', '.wav', '.flac', '.m4a', '.ogg')

    # 1. Search directory for backing.* first
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            f_lower = f.lower()
            if f_lower.startswith("backing.") and f_lower.endswith(valid_exts):
                candidate = os.path.join(folder, f)
                if os.path.isfile(candidate) and os.path.getsize(candidate) > 1024:
                    return candidate

        # Search for any audio file in the folder
        for f in os.listdir(folder):
            if f.lower().endswith(valid_exts):
                candidate = os.path.join(folder, f)
                if os.path.isfile(candidate) and os.path.getsize(candidate) > 1024:
                    return candidate

    # 2. Check if embedded in GP file
    if gp_path.lower().endswith(('.gp', '.gp5', '.gpx')):
        try:
            with zipfile.ZipFile(gp_path, 'r') as z:
                asset_files = [
                    n for n in z.namelist() 
                    if n.startswith('Content/Assets/') and not n.endswith('/')
                ]
                audio_assets = [
                    n for n in asset_files 
                    if n.lower().endswith(valid_exts)
                ]
                if audio_assets:
                    target_asset = audio_assets[0]
                    ext = os.path.splitext(target_asset)[1].lower() or ".mp3"
                    
                    if not extract_if_embedded:
                        return f"embedded:{target_asset}"

                    # Attempt to extract next to GP file as backing.<ext>
                    target_path = os.path.join(folder, f"backing{ext}")
                    try:
                        with z.open(target_asset) as src, open(target_path, "wb") as dst:
                            while chunk := src.read(65536):
                                dst.write(chunk)
                        if os.path.exists(target_path) and os.path.getsize(target_path) > 1024:
                            return target_path
                    except Exception:
                        # Fallback to cache directory if folder is read-only
                        cache_dir = r"E:\BassStation\cache\audio_extracted"
                        os.makedirs(cache_dir, exist_ok=True)
                        import hashlib
                        h = hashlib.md5(gp_path.encode('utf-8')).hexdigest()[:12]
                        cached_path = os.path.join(cache_dir, f"{h}_backing{ext}")
                        with z.open(target_asset) as src, open(cached_path, "wb") as dst:
                            while chunk := src.read(65536):
                                dst.write(chunk)
                        return cached_path
        except Exception:
            pass

    return None

def update_gp_frame_padding(gp_path: str, new_padding: int) -> Dict[str, Any]:
    """Updates <FramePadding> in score.gpif within the .gp archive safely."""
    if not os.path.exists(gp_path):
        return {"success": False, "error": "GP文件未找到"}
    from gp_guard import is_protected
    if is_protected(gp_path):
        return {"success": False, "error": "原版谱面受保护"}

    try:
        entries = []
        with zipfile.ZipFile(gp_path, "r") as z:
            for item in z.infolist():
                entries.append((item, z.read(item.filename)))

        score_idx = None
        for i, (item, data) in enumerate(entries):
            if item.filename == "Content/score.gpif":
                score_idx = i
                break

        if score_idx is None:
            return {"success": False, "error": "谱面中缺少 score.gpif"}

        item, data = entries[score_idx]
        gpif = data.decode("utf-8", errors="ignore")

        if "<FramePadding>" in gpif:
            gpif = re.sub(r"<FramePadding>.*?</FramePadding>", f"<FramePadding>{new_padding}</FramePadding>", gpif)
        elif "<BackingTrack>" in gpif:
            gpif = gpif.replace("</BackingTrack>", f"<FramePadding>{new_padding}</FramePadding>\n</BackingTrack>")
        else:
            return {"success": False, "error": "未在曲谱中找到伴奏轨道 BackingTrack"}

        entries[score_idx] = (item, gpif.encode("utf-8"))

        with zipfile.ZipFile(gp_path, "w") as z:
            for it, d in entries:
                z.writestr(it, d)

        return {"success": True}
    except PermissionError:
        return {
            "success": False,
            "error": "曲谱正在被 Guitar Pro 8 占用锁定。请在 GP 中按 Ctrl+S 保存并关闭该曲谱标签页后再点击，或退出 GP。"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"写入曲谱失败: {str(e)}"
        }

def auto_align_backing_track(gp_path: str, audio_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Score-aware precision automatic audio alignment:
    1. Analyzes score GPIF to detect the exact onset timestamp of the first sounding note across
       all tracks (accounting for initial rests, pickup measures, and weak beats / 弱起拍).
    2. Resolves companion or embedded audio track.
    3. Uses millisecond-level transient onset detection on the audio.
    4. Dynamically reads audio sample rate (e.g. 44.1kHz vs 48kHz).
    5. Calculates sample-accurate FramePadding (T_score_first_note - T_audio_first_onset) * sr.
    """
    if not gp_path.lower().endswith('.gp'):
        return {"success": False, "error": "该格式乐谱不支持内嵌伴奏对齐"}
    if not os.path.exists(gp_path):
        return {"success": False, "error": "GP文件未找到"}

    # Find audio companion (disk or embedded)
    if not audio_path or not os.path.exists(audio_path):
        audio_path = resolve_audio_file(gp_path, extract_if_embedded=True)

    if not audio_path or not os.path.exists(audio_path):
        return {"success": False, "error": "未在曲谱或同目录下找到伴奏音频文件"}

    # 1. Analyze score onset & tempo
    score_info = get_score_first_sounding_offset(gp_path)
    score_onset_sec = score_info["first_sounding_offset_sec"]
    score_tempo = score_info["tempo"]
    is_weak_beat = score_info["is_weak_beat"]

    # 2. Analyze audio onset & sample rate
    audio_lead_in = detect_audio_lead_in(audio_path)
    sample_rate = get_audio_sample_rate(audio_path)

    # 3. Calculate alignment offset
    # Delta T = T_score - T_audio
    aligned_offset_sec = score_onset_sec - audio_lead_in
    new_padding = int(round(aligned_offset_sec * sample_rate))

    # Read old padding
    old_padding = 0
    try:
        with zipfile.ZipFile(gp_path, "r") as z:
            xml = z.read("Content/score.gpif").decode("utf-8", errors="ignore")
            m = re.search(r"<FramePadding>(.*?)</FramePadding>", xml)
            if m:
                old_padding = int(m.group(1))
    except Exception:
        pass

    write_res = update_gp_frame_padding(gp_path, new_padding)
    if not write_res.get("success"):
        return {
            "success": False,
            "error": write_res.get("error", "更新伴奏偏移失败")
        }

    return {
        "success": True,
        "tempo": score_tempo,
        "is_weak_beat": is_weak_beat,
        "score_onset_ms": round(score_onset_sec * 1000, 1),
        "audio_onset_ms": round(audio_lead_in * 1000, 1),
        "aligned_offset_ms": round(aligned_offset_sec * 1000, 1),
        "current_offset_ms": round((new_padding / float(sample_rate)) * 1000, 1),
        "sample_rate": sample_rate,
        "old_padding": old_padding,
        "new_padding": new_padding,
    }

def adjust_backing_track_offset(gp_path: str, delta_ms: float, audio_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Shifts backing track timing by delta_ms.
    Negative delta_ms = shift audio earlier (提前).
    Positive delta_ms = shift audio later (延后).
    """
    if not gp_path.lower().endswith('.gp'):
        return {"success": False, "error": "该格式乐谱不支持内嵌伴奏对齐"}
    if not os.path.exists(gp_path):
        return {"success": False, "error": "GP文件未找到"}

    if not audio_path or not os.path.exists(audio_path):
        audio_path = resolve_audio_file(gp_path, extract_if_embedded=True)

    sample_rate = get_audio_sample_rate(audio_path) if audio_path and os.path.exists(audio_path) else 44100

    old_padding = 0
    try:
        with zipfile.ZipFile(gp_path, "r") as z:
            xml = z.read("Content/score.gpif").decode("utf-8", errors="ignore")
            m = re.search(r"<FramePadding>(.*?)</FramePadding>", xml)
            if m:
                old_padding = int(m.group(1))
    except Exception:
        pass

    # Positive padding delays audio, negative padding brings audio earlier
    delta_samples = int(round((delta_ms / 1000.0) * sample_rate))
    new_padding = old_padding + delta_samples

    write_res = update_gp_frame_padding(gp_path, new_padding)
    if not write_res.get("success"):
        return {
            "success": False,
            "error": write_res.get("error", "微调写入失败")
        }

    current_offset_ms = round((new_padding / float(sample_rate)) * 1000, 1)

    return {
        "success": True,
        "old_padding": old_padding,
        "new_padding": new_padding,
        "delta_ms": delta_ms,
        "current_offset_ms": current_offset_ms,
        "sample_rate": sample_rate
    }
