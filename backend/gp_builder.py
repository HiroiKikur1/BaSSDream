import os
import math
import zipfile
import re
import json
import xml.etree.ElementTree as ET
from typing import Optional, List, Dict, Any, Tuple

def midi_to_gp_pitch(midi_num: int):
    steps = ['C', 'C', 'D', 'D', 'E', 'F', 'F', 'G', 'G', 'A', 'A', 'B']
    accidentals = ['', 'Sharp', '', 'Sharp', '', '', 'Sharp', '', 'Sharp', '', 'Sharp', '']
    idx = midi_num % 12
    step = steps[idx]
    acc = accidentals[idx]
    concert_oct = midi_num // 12
    transp_oct = concert_oct + 1
    return step, acc, concert_oct, transp_oct

def midi_to_bass_string_fret(midi_num: int, is_5string: bool = False):
    if is_5string:
        open_strings = [23, 28, 33, 38, 43]
    else:
        open_strings = [28, 33, 38, 43]
    best_str = 0
    best_fret = max(0, midi_num - open_strings[0])
    for s_idx, open_midi in enumerate(open_strings):
        if midi_num >= open_midi:
            fret = midi_num - open_midi
            if fret <= 14:
                best_str = s_idx
                best_fret = fret
                if fret <= 7:
                    break
    return best_str, best_fret

RHYTHM_MAP = {
    16: "0",  # Whole
    12: "6",  # Dotted Half
    8:  "1",  # Half
    6:  "4",  # Dotted Quarter
    4:  "3",  # Quarter
    3:  "8",  # Dotted Eighth
    2:  "2",  # Eighth
    1:  "7",  # 16th
    "triplet_8th": "9",   # Eighth Triplet (八分三连音)
    "triplet_16th": "10", # 16th Triplet (十六分三连音 / 六连音)
    "triplet_4th": "11",  # Quarter Triplet (四分三连音)
}

STANDARD_RHYTHMS = [
    ("0", "Whole", False, None),
    ("1", "Half", False, None),
    ("2", "Eighth", False, None),
    ("3", "Quarter", False, None),
    ("4", "Quarter", True, None),
    ("5", "32nd", False, None),
    ("6", "Half", True, None),
    ("7", "16th", False, None),
    ("8", "Eighth", True, None),
    ("9", "Eighth", False, (3, 2)),   # Eighth Triplet
    ("10", "16th", False, (3, 2)),    # 16th Triplet
    ("11", "Quarter", False, (3, 2)), # Quarter Triplet
]

import bisect

def decompose_duration(start_slot: int, duration_slots: int, total_slots: int = 16, is_rest: bool = False) -> List[Tuple[int, int]]:
    """
    Decomposes duration_slots starting at start_slot into standard musical rhythm chunks.
    - For notes (is_rest=False): prioritizes natural dotted figures (dotted half 12, dotted quarter 6,
      dotted eighth 3) and splits cleanly across the Beat 3 (slot 8) midpoint boundary with ties.
    - For rests (is_rest=True): aggregates rests cleanly into Whole, Half, and Quarter rests without crossing
      beat boundaries (0, 4, 8, 12) or producing micro-rests.
    - For 3/4 (12 slots): handles 3-beat measures and dotted half rests/notes naturally.
    """
    chunks = []
    curr_slot = start_slot
    rem_slots = duration_slots
    while rem_slots > 0:
        if total_slots == 16:
            if is_rest:
                if curr_slot == 0 and rem_slots >= 16:
                    max_chunk = 16
                elif curr_slot in (0, 8) and rem_slots >= 8:
                    max_chunk = 8
                else:
                    next_beat = ((curr_slot // 4) + 1) * 4
                    max_chunk = min(rem_slots, next_beat - curr_slot)
            else:
                if curr_slot == 0 and rem_slots >= 16:
                    max_chunk = 16
                elif curr_slot == 0 and rem_slots >= 12:
                    max_chunk = 12
                elif curr_slot == 8 and rem_slots >= 8:
                    max_chunk = 8
                else:
                    dist_to_mid = 8 - curr_slot if curr_slot < 8 else 16 - curr_slot
                    max_chunk = min(rem_slots, dist_to_mid)
        else:
            # 3/4 time (12 sixteenth slots)
            if curr_slot == 0 and rem_slots >= 12:
                max_chunk = 12
            elif curr_slot == 0 and rem_slots >= 8 and not is_rest:
                max_chunk = 8
            else:
                next_boundary = 4 if curr_slot < 4 else (8 if curr_slot < 8 else 12)
                max_chunk = min(rem_slots, next_boundary - curr_slot)

        chosen = 1
        candidates = [16, 8, 4, 2, 1] if is_rest else [16, 12, 8, 6, 4, 3, 2, 1]
        for candidate in candidates:
            if candidate <= max_chunk:
                if candidate == 16 and curr_slot == 0 and total_slots >= 16:
                    chosen = 16
                    break
                elif candidate == 12 and curr_slot == 0 and not is_rest:
                    chosen = 12
                    break
                elif candidate == 8 and (curr_slot in (0, 8) or (total_slots == 12 and curr_slot == 0 and not is_rest)):
                    chosen = 8
                    break
                elif candidate == 6 and not is_rest and curr_slot in (0, 2, 4, 8, 10):
                    chosen = 6
                    break
                elif candidate == 4 and (curr_slot % 4 == 0 or (not is_rest and curr_slot % 2 == 0)):
                    chosen = 4
                    break
                elif candidate == 3 and not is_rest and (curr_slot % 2 == 0):
                    chosen = 3
                    break
                elif candidate == 2 and (curr_slot % 2 == 0):
                    chosen = 2
                    break
                elif candidate == 1:
                    chosen = 1
                    break

        chunks.append((curr_slot, chosen))
        curr_slot += chosen
        rem_slots -= chosen
    return chunks

def create_clean_gp_project(
    output_gp_path: str,
    title: str,
    artist: str,
    franchise: str = "BanG Dream!",
    tempo: float = 120.0,
    is_5string: bool = False,
    audio_path: Optional[str] = None,
    duration: float = 180.0,
    detected_onsets: Optional[List[Any]] = None,
    key_signature: Optional[Tuple[int, str]] = None,
    sync_points: Optional[List[Dict[str, Any]]] = None,
    time_signature: Tuple[int, int] = (4, 4)
) -> str:
    """
    Creates a pristine, dedicated Guitar Pro (.gp) project file strictly tagged
    with the target song's title, artist, tempo, and measure structure.
    Completely eliminates any wrong-song template cloning.
    """
    out_dir = os.path.dirname(output_gp_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Find a structural base gp file in tabs to copy container stylesheet structures
    base_gp = None
    tabs_root = r"E:\BassStation\tabs"
    if os.path.exists(tabs_root):
        for root, dirs, files in os.walk(tabs_root):
            for f in files:
                if f.endswith(".gp"):
                    base_gp = os.path.join(root, f)
                    break
            if base_gp:
                break

    if not base_gp:
        raise FileNotFoundError("Base GP template structure not found in tabs directory.")

    entries = {}
    with zipfile.ZipFile(base_gp, "r") as z:
        for item in z.infolist():
            # Exclude other songs' embedded audio assets completely!
            if not item.filename.startswith("Content/Assets/"):
                entries[item.filename] = z.read(item.filename)

    # 1. Update meta.json
    has_audio = bool(audio_path and isinstance(audio_path, str) and os.path.exists(audio_path))
    meta = {"hasAudio": has_audio, "version": "1.0.0"}
    entries["meta.json"] = json.dumps(meta, indent=4).encode("utf-8")

    # 2. Parse & reconstruct Content/score.gpif
    if "Content/score.gpif" in entries:
        gpif_str = entries["Content/score.gpif"].decode("utf-8", errors="ignore")
        root = ET.fromstring(gpif_str)

        # Basic metadata tags
        title_el = root.find(".//Title")
        if title_el is not None: title_el.text = title
        artist_el = root.find(".//Artist")
        if artist_el is not None: artist_el.text = artist
        sub_el = root.find(".//SubTitle")
        if sub_el is not None: sub_el.text = franchise
        album_el = root.find(".//Album")
        if album_el is not None: album_el.text = f"{title} - Single"

        bpm = max(30.0, tempo)

        # Calculate measure count from duration and time signature
        beats_per_bar, beat_unit = time_signature
        quarters_per_bar = float(beats_per_bar) * (4.0 / float(beat_unit))
        slots_per_measure = int(round(quarters_per_bar * 4.0))
        time_str = f"{beats_per_bar}/{beat_unit}"

        bar_duration = quarters_per_bar * (60.0 / bpm)
        num_measures = max(16, int(math.ceil(duration / bar_duration)))

        # Update MasterTrack Automations (Tempo & SyncPoints)
        mt_el = root.find("MasterTrack")
        if mt_el is not None:
            autos_el = mt_el.find("Automations")
            if autos_el is None:
                autos_el = ET.SubElement(mt_el, "Automations")

            # Remove previous Tempo and SyncPoint automations
            for a in list(autos_el):
                atype = a.findtext("Type")
                if atype in ("Tempo", "SyncPoint"):
                    autos_el.remove(a)

            if sync_points:
                for sp in sync_points:
                    b_idx = int(sp.get("bar", 0))
                    cur_bpm = float(sp.get("bpm", bpm))
                    fo = int(sp.get("frame_offset", 0))

                    spa = ET.SubElement(autos_el, "Automation")
                    ET.SubElement(spa, "Type").text = "SyncPoint"
                    ET.SubElement(spa, "Linear").text = "false"
                    ET.SubElement(spa, "Bar").text = str(b_idx)
                    ET.SubElement(spa, "Position").text = "0"
                    ET.SubElement(spa, "Visible").text = "true"
                    sp_val = ET.SubElement(spa, "Value")
                    ET.SubElement(sp_val, "BarIndex").text = str(b_idx)
                    ET.SubElement(sp_val, "BarOccurrence").text = "0"
                    ET.SubElement(sp_val, "ModifiedTempo").text = f"{cur_bpm:.5f}"
                    ET.SubElement(sp_val, "OriginalTempo").text = f"{int(round(bpm))}"
                    ET.SubElement(sp_val, "FrameOffset").text = str(fo)

            # Global Tempo Automation: strictly on Bar 0 to prevent per-measure BPM jitter
            ta = ET.SubElement(autos_el, "Automation")
            ET.SubElement(ta, "Type").text = "Tempo"
            ET.SubElement(ta, "Linear").text = "false"
            ET.SubElement(ta, "Bar").text = "0"
            ET.SubElement(ta, "Position").text = "0"
            ET.SubElement(ta, "Visible").text = "true"
            ET.SubElement(ta, "Value").text = f"{int(round(bpm))} 2"

        # Completely clear all previous song's notes, beats, voices, bars
        for tag in ["Voices", "Beats", "Notes", "Bars", "MasterBars"]:
            el = root.find(tag)
            if el is not None:
                el.clear()

        mbars_p = root.find("MasterBars")
        bars_p = root.find("Bars")
        voices_p = root.find("Voices")
        beats_p = root.find("Beats")
        notes_p = root.find("Notes")

        # Reconstruct standard rhythms
        rhythms_p = root.find("Rhythms")
        if rhythms_p is None:
            rhythms_p = ET.SubElement(root, "Rhythms")
        rhythms_p.clear()
        for item in STANDARD_RHYTHMS:
            rid = item[0]
            nval = item[1]
            has_dot = item[2]
            tuplet = item[3] if len(item) > 3 else None

            r_el = ET.SubElement(rhythms_p, "Rhythm")
            r_el.set("id", rid)
            ET.SubElement(r_el, "NoteValue").text = nval
            if has_dot:
                d_el = ET.SubElement(r_el, "AugmentationDot")
                d_el.set("count", "1")
            if tuplet is not None:
                t_el = ET.SubElement(r_el, "PrimaryTuplet")
                t_el.set("num", str(tuplet[0]))
                t_el.set("den", str(tuplet[1]))

        # Update Track Name and Tuning, and enforce strictly single Electric Bass track
        tracks_parent = root.find(".//Tracks")
        if tracks_parent is not None:
            all_trs = tracks_parent.findall("Track")
            if len(all_trs) > 1:
                for extra_tr in all_trs[1:]:
                    tracks_parent.remove(extra_tr)

        for tr in root.findall(".//Tracks/Track"):
            name_el = tr.find("Name")
            if name_el is not None:
                name_el.text = "Electric Bass"
            color_el = tr.find("Color")
            if color_el is not None:
                color_el.text = "234 212 125"
            for tuning_prop in tr.findall(".//Property[@name='Tuning']"):
                pitches_el = tuning_prop.find("Pitches")
                if pitches_el is not None:
                    pitches_el.text = "23 28 33 38 43" if is_5string else "28 33 38 43"

        # Reconstruct clean measures matching target duration
        if mbars_p is not None and bars_p is not None:
            # Strictly uniform measure grid to ensure timing stability and eliminate drift
            bar_times = [m * bar_duration for m in range(num_measures + 2)]

            onsets_by_bar = {}
            if detected_onsets:
                # Downbeat phase locking: lock first sound to Bar 0 Beat 0 if lead-in silence <= 0.45s
                first_raw_t = 0.0
                for it in detected_onsets:
                    if isinstance(it, dict):
                        first_raw_t = float(it.get("time", 0.0))
                    elif isinstance(it, (list, tuple)):
                        first_raw_t = float(it[0])
                    else:
                        first_raw_t = float(it)
                    break
                phase_offset = first_raw_t if 0.02 <= first_raw_t <= 0.45 else 0.0

                # Sort all onsets chronologically
                sorted_raw = []
                for item in detected_onsets:
                    if isinstance(item, dict):
                        t = float(item.get("time", 0.0))
                    elif isinstance(item, (list, tuple)):
                        t = float(item[0])
                    else:
                        t = float(item)
                    sorted_raw.append((t, item))
                sorted_raw.sort(key=lambda x: x[0])

                for idx, (t, item) in enumerate(sorted_raw):
                    locked_t = max(0.0, t - phase_offset)
                    next_raw_t = sorted_raw[idx + 1][0] if idx + 1 < len(sorted_raw) else None
                    next_locked_t = max(0.0, next_raw_t - phase_offset) if next_raw_t is not None else None

                    b_idx = int(locked_t // bar_duration)
                    b_idx = max(0, min(num_measures - 1, b_idx))

                    m_t0 = b_idx * bar_duration
                    m_t1 = (b_idx + 1) * bar_duration
                    m_len = bar_duration
                    rel_pos = max(0.0, min(0.999, (locked_t - m_t0) / m_len))

                    slot_dur = m_len / float(slots_per_measure)
                    default_dur = m_len / float(beats_per_bar)
                    item_dur = float(item.get("duration", default_dur)) if isinstance(item, dict) else default_dur
                    if next_locked_t is not None:
                        item_dur = min(item_dur, max(0.04, next_locked_t - locked_t))

                    note_end_t = locked_t + item_dur
                    spill_over = note_end_t - m_t1

                    next_is_at_downbeat = False
                    if next_locked_t is not None and (next_locked_t - m_t1) < (slot_dur * 0.85):
                        next_is_at_downbeat = True

                    if spill_over > (slot_dur * 0.75) and not next_is_at_downbeat and b_idx + 1 < num_measures:
                        # Split note across the barline with tie origin & destination
                        dur_in_bar = m_t1 - locked_t
                        item_b0 = dict(item) if isinstance(item, dict) else {
                            "time": t, "duration": dur_in_bar,
                            "midi": int(item[1]) if isinstance(item, (list, tuple)) and len(item) > 1 else 28,
                            "string": int(item[2]) if isinstance(item, (list, tuple)) and len(item) > 2 else 0,
                            "fret": int(item[3]) if isinstance(item, (list, tuple)) and len(item) > 3 else 0
                        }
                        item_b0["duration"] = dur_in_bar
                        item_b0["tie_origin"] = True

                        if b_idx not in onsets_by_bar:
                            onsets_by_bar[b_idx] = []
                        onsets_by_bar[b_idx].append((rel_pos, m_len, item_b0))

                        # Next measure continuation
                        curr_b = b_idx + 1
                        seg_dur = min(spill_over, bar_duration)
                        item_cont = dict(item_b0)
                        item_cont["time"] = curr_b * bar_duration + phase_offset
                        item_cont["duration"] = seg_dur
                        item_cont["tie_dest"] = True
                        item_cont["tie_origin"] = False
                        item_cont["slide"] = 0
                        item_cont["slide_to_next"] = False
                        item_cont["hopo_origin"] = False
                        item_cont["hopo_dest"] = False
                        item_cont["slapped"] = False
                        item_cont["popped"] = False

                        if curr_b not in onsets_by_bar:
                            onsets_by_bar[curr_b] = []
                        onsets_by_bar[curr_b].append((0.0, bar_duration, item_cont))
                    else:
                        if b_idx not in onsets_by_bar:
                            onsets_by_bar[b_idx] = []
                        onsets_by_bar[b_idx].append((rel_pos, m_len, item))

            voice_counter = 0
            beat_counter = 0
            note_counter = 0

            for m in range(num_measures):
                mb = ET.SubElement(mbars_p, "MasterBar")
                ET.SubElement(mb, "Time").text = time_str
                ET.SubElement(mb, "Bars").text = str(m)

                if key_signature:
                    acc_count, mode = key_signature
                    k = ET.SubElement(mb, "Key")
                    ET.SubElement(k, "AccidentalCount").text = str(acc_count)
                    ET.SubElement(k, "Mode").text = mode
                    ET.SubElement(k, "TransposeAs").text = "Flats" if acc_count < 0 else "Sharps"

                b = ET.SubElement(bars_p, "Bar")
                b.set("id", str(m))
                ET.SubElement(b, "Clef").text = "F4"

                m_items = onsets_by_bar.get(m, [])
                if voices_p is not None and beats_p is not None and notes_p is not None:
                    v = ET.SubElement(voices_p, "Voice")
                    v.set("id", str(voice_counter))
                    ET.SubElement(b, "Voices").text = f"{voice_counter} -1 -1 -1"

                    if not m_items:
                        # Empty Measure: render an authentic single Whole Rest (or Dotted Half Rest in 3/4)
                        beat = ET.SubElement(beats_p, "Beat")
                        beat.set("id", str(beat_counter))
                        ET.SubElement(beat, "Dynamic").text = "MF"
                        rhythm = ET.SubElement(beat, "Rhythm")
                        rhythm.set("ref", "6" if slots_per_measure == 12 else "0")
                        ET.SubElement(v, "Beats").text = str(beat_counter)
                        voice_counter += 1
                        beat_counter += 1
                        continue

                    # Monophonic slot assignment
                    # If both a tie continuation and a real onset exist near slot 0, drop the tie continuation
                    has_real_downbeat = any(not it[2].get("tie_dest", False) and it[0] < (1.2 / slots_per_measure) for it in m_items)
                    filtered_items = [it for it in m_items if not (has_real_downbeat and it[2].get("tie_dest", False))]

                    # Check for per-beat tuplets (8th triplets or 16th sextuplets)
                    slots_per_beat = max(1, slots_per_measure // beats_per_bar)
                    beat_groups: List[List[Any]] = [[] for _ in range(beats_per_bar)]
                    for rel_pos, m_len, item in filtered_items:
                        b_idx = min(beats_per_bar - 1, max(0, int(rel_pos * beats_per_bar)))
                        beat_groups[b_idx].append((rel_pos, m_len, item))

                    tuplet_beats = {}
                    for b in range(beats_per_bar):
                        b_items = beat_groups[b]
                        b_start = float(b) / beats_per_bar
                        if len(b_items) == 3:
                            pos = sorted([(it[0] - b_start) * beats_per_bar for it in b_items])
                            t_targets = [0.0, 1.0/3.0, 2.0/3.0]
                            err = sum(min(abs(p - t) for t in t_targets)**2 for p in pos)
                            s_targets = [0.0, 0.25, 0.50, 0.75]
                            str_err = sum(min(abs(p - s) for s in s_targets)**2 for p in pos)
                            if err < 0.65 * str_err and err < 0.04:
                                tuplet_beats[b] = 'triplet_8th'
                        elif len(b_items) == 6:
                            pos = sorted([(it[0] - b_start) * beats_per_bar for it in b_items])
                            t_targets = [i / 6.0 for i in range(6)]
                            err = sum(min(abs(p - t) for t in t_targets)**2 for p in pos)
                            if err < 0.04:
                                tuplet_beats[b] = 'triplet_16th'

                    timeline = []
                    if not tuplet_beats:
                        # Standard straight measure decomposition
                        slot_notes: Dict[int, Any] = {}
                        for rel_pos, m_len, item in filtered_items:
                            slot_idx = min(slots_per_measure - 1, max(0, int(round(rel_pos * slots_per_measure))))
                            if slot_idx not in slot_notes or (slot_notes[slot_idx][1].get("tie_dest", False) and not item.get("tie_dest", False)):
                                slot_notes[slot_idx] = (m_len, item)

                        sorted_slots = sorted(slot_notes.keys())
                        note_segments = []
                        for idx, s in enumerate(sorted_slots):
                            m_len, item = slot_notes[s]
                            next_s = sorted_slots[idx + 1] if idx + 1 < len(sorted_slots) else slots_per_measure
                            avail = next_s - s
                            slot_dur_sec = m_len / float(slots_per_measure)
                            item_dur = float(item.get("duration", slot_dur_sec)) if isinstance(item, dict) else slot_dur_sec
                            raw_dur_slots = int(round(item_dur / slot_dur_sec))

                            if isinstance(item, dict) and item.get("tie_origin") and idx == len(sorted_slots) - 1:
                                dur_slots = avail
                            elif avail <= 2:
                                dur_slots = avail
                            elif avail == 3:
                                dur_slots = 3 if raw_dur_slots >= 2 else 2
                            elif avail == 4:
                                dur_slots = 4 if raw_dur_slots >= 3 else (2 if raw_dur_slots == 2 else 1)
                            else:
                                cands = [c for c in [16, 12, 8, 6, 4, 3, 2] if c <= avail]
                                dur_slots = min(cands, key=lambda c: abs(c - raw_dur_slots)) if cands else min(avail, max(1, raw_dur_slots))

                            note_segments.append((s, dur_slots, item))

                        curr_slot = 0
                        for s, dur_slots, item in note_segments:
                            if s > curr_slot:
                                for r_start, r_len in decompose_duration(curr_slot, s - curr_slot, total_slots=slots_per_measure, is_rest=True):
                                    timeline.append(("REST", r_start, r_len, None, False, False, RHYTHM_MAP.get(r_len, "7")))
                            n_chunks = decompose_duration(s, dur_slots, total_slots=slots_per_measure, is_rest=False)
                            for c_idx, (n_start, n_len) in enumerate(n_chunks):
                                is_tied_origin = (len(n_chunks) > 1 and c_idx < len(n_chunks) - 1)
                                is_tied_dest = (len(n_chunks) > 1 and c_idx > 0)
                                if isinstance(item, dict):
                                    if c_idx == 0 and item.get("tie_dest"): is_tied_dest = True
                                    if c_idx == len(n_chunks) - 1 and item.get("tie_origin"): is_tied_origin = True
                                timeline.append(("NOTE", n_start, n_len, item, is_tied_origin, is_tied_dest, RHYTHM_MAP.get(n_len, "7")))
                            curr_slot = s + dur_slots

                        if curr_slot < slots_per_measure:
                            for r_start, r_len in decompose_duration(curr_slot, slots_per_measure - curr_slot, total_slots=slots_per_measure, is_rest=True):
                                timeline.append(("REST", r_start, r_len, None, False, False, RHYTHM_MAP.get(r_len, "7")))
                    else:
                        # Beat-by-beat decomposition with tuplet support
                        for b in range(beats_per_bar):
                            b_start = float(b) / beats_per_bar
                            items = beat_groups[b]
                            if b in tuplet_beats:
                                t_type = tuplet_beats[b]
                                r_ref = "9" if t_type == 'triplet_8th' else "10"
                                sorted_items = sorted(items, key=lambda x: x[0])
                                for it in sorted_items:
                                    timeline.append(("NOTE", b * slots_per_beat, 1, it[2], False, False, r_ref))
                            else:
                                if not items:
                                    timeline.append(("REST", b * slots_per_beat, slots_per_beat, None, False, False, "3"))
                                else:
                                    slot_notes = {}
                                    for it in items:
                                        rel_in_beat = (it[0] - b_start) * beats_per_bar
                                        s_idx = min(slots_per_beat - 1, max(0, int(round(rel_in_beat * slots_per_beat))))
                                        slot_notes[s_idx] = it[2]
                                    curr_s = 0
                                    sorted_s = sorted(slot_notes.keys())
                                    for idx, s in enumerate(sorted_s):
                                        if s > curr_s:
                                            r_len = s - curr_s
                                            r_ref = "7" if r_len == 1 else ("2" if r_len == 2 else "8")
                                            timeline.append(("REST", b * slots_per_beat + curr_s, r_len, None, False, False, r_ref))
                                        next_s = sorted_s[idx + 1] if idx + 1 < len(sorted_s) else slots_per_beat
                                        dur_slots = next_s - s
                                        n_ref = "7" if dur_slots == 1 else ("2" if dur_slots == 2 else ("8" if dur_slots == 3 else "3"))
                                        timeline.append(("NOTE", b * slots_per_beat + s, dur_slots, slot_notes[s], False, False, n_ref))
                                        curr_s = s + dur_slots
                                    if curr_s < slots_per_beat:
                                        r_len = slots_per_beat - curr_s
                                        r_ref = "7" if r_len == 1 else ("2" if r_len == 2 else ("8" if r_len == 3 else "3"))
                                        timeline.append(("REST", b * slots_per_beat + curr_s, r_len, None, False, False, r_ref))

                    beat_ids = []
                    for event_type, s_start, s_len, item, tie_orig, tie_dest, r_ref in timeline:
                        beat = ET.SubElement(beats_p, "Beat")
                        beat.set("id", str(beat_counter))
                        beat_ids.append(str(beat_counter))
                        ET.SubElement(beat, "Dynamic").text = "MF"

                        rhythm = ET.SubElement(beat, "Rhythm")
                        rhythm.set("ref", r_ref)

                        if event_type == "NOTE" and item is not None:
                            if isinstance(item, dict):
                                midi = int(item.get("midi", 28))
                                str_idx = int(item.get("string", 0))
                                fret = int(item.get("fret", max(0, midi - 28)))
                                is_muted = bool(item.get("is_muted", False))
                                slide_flags = int(item.get("slide", 0))
                                hopo_orig = bool(item.get("hopo_origin", False))
                                hopo_dest = bool(item.get("hopo_dest", False))
                            elif isinstance(item, (list, tuple)) and len(item) >= 4:
                                midi = int(item[1])
                                str_idx = int(item[2])
                                fret = int(item[3])
                                is_muted = False
                                slide_flags = 0
                                hopo_orig = False
                                hopo_dest = False
                            else:
                                midi = 28
                                str_idx, fret = midi_to_bass_string_fret(midi, is_5string)
                                is_muted = False
                                slide_flags = 0
                                hopo_orig = False
                                hopo_dest = False

                            step, acc, concert_oct, transp_oct = midi_to_gp_pitch(midi)

                            n = ET.SubElement(notes_p, "Note")
                            n.set("id", str(note_counter))

                            # Tie element
                            if tie_orig or tie_dest:
                                t_el = ET.SubElement(n, "Tie")
                                t_el.set("origin", "true" if tie_orig else "false")
                                t_el.set("destination", "true" if tie_dest else "false")

                            ET.SubElement(n, "InstrumentArticulation").text = "0"
                            props = ET.SubElement(n, "Properties")

                            p_cp = ET.SubElement(props, "Property")
                            p_cp.set("name", "ConcertPitch")
                            pitch_cp = ET.SubElement(p_cp, "Pitch")
                            ET.SubElement(pitch_cp, "Step").text = step
                            if acc: acc_cp = ET.SubElement(pitch_cp, "Accidental"); acc_cp.text = acc
                            ET.SubElement(pitch_cp, "Octave").text = str(concert_oct)

                            p_fret = ET.SubElement(props, "Property")
                            p_fret.set("name", "Fret")
                            ET.SubElement(p_fret, "Fret").text = str(fret)

                            p_midi = ET.SubElement(props, "Property")
                            p_midi.set("name", "Midi")
                            ET.SubElement(p_midi, "Number").text = str(midi)

                            p_str = ET.SubElement(props, "Property")
                            p_str.set("name", "String")
                            ET.SubElement(p_str, "String").text = str(str_idx)

                            p_tp = ET.SubElement(props, "Property")
                            p_tp.set("name", "TransposedPitch")
                            pitch_tp = ET.SubElement(p_tp, "Pitch")
                            ET.SubElement(pitch_tp, "Step").text = step
                            if acc: acc_tp = ET.SubElement(pitch_tp, "Accidental"); acc_tp.text = acc
                            ET.SubElement(pitch_tp, "Octave").text = str(transp_oct)

                            if is_muted:
                                p_mute = ET.SubElement(props, "Property")
                                p_mute.set("name", "Muted")
                                ET.SubElement(p_mute, "Enable")

                            # Articulations & Techniques on initial note attack
                            if not tie_dest:
                                if slide_flags > 0:
                                    p_slide = ET.SubElement(props, "Property")
                                    p_slide.set("name", "Slide")
                                    ET.SubElement(p_slide, "Flags").text = str(slide_flags)

                                if hopo_orig:
                                    p_ho = ET.SubElement(props, "Property")
                                    p_ho.set("name", "HopoOrigin")
                                    ET.SubElement(p_ho, "Enable")

                                if hopo_dest:
                                    p_hd = ET.SubElement(props, "Property")
                                    p_hd.set("name", "HopoDestination")
                                    ET.SubElement(p_hd, "Enable")

                            ET.SubElement(beat, "Notes").text = str(note_counter)
                            note_counter += 1

                        beat_counter += 1

                    ET.SubElement(v, "Beats").text = " ".join(beat_ids)
                    voice_counter += 1
                else:
                    if voices_p is not None and beats_p is not None:
                        v = ET.SubElement(voices_p, "Voice")
                        v.set("id", str(voice_counter))
                        ET.SubElement(b, "Voices").text = f"{voice_counter} -1 -1 -1"

                        # Empty rest matching measure duration
                        beat = ET.SubElement(beats_p, "Beat")
                        beat.set("id", str(beat_counter))
                        ET.SubElement(beat, "Dynamic").text = "MF"
                        rhythm = ET.SubElement(beat, "Rhythm")
                        empty_ref = "0" if slots_per_measure == 16 else "6"
                        rhythm.set("ref", empty_ref)
                        ET.SubElement(v, "Beats").text = str(beat_counter)

                        beat_counter += 1
                        voice_counter += 1
                    else:
                        ET.SubElement(b, "Voices").text = "-1 -1 -1 -1"

        # 3. Audio & Backing track configuration
        if audio_path and os.path.exists(audio_path):
            abs_audio = os.path.abspath(audio_path).replace("\\", "/")
            # Update Assets
            assets_el = root.find("Assets")
            if assets_el is None:
                assets_el = ET.SubElement(root, "Assets")
            assets_el.clear()
            asset = ET.SubElement(assets_el, "Asset")
            asset.set("id", "0")
            ET.SubElement(asset, "OriginalFilePath").text = abs_audio

            # Update BackingTrack
            bt_el = root.find("BackingTrack")
            if bt_el is None:
                bt_el = ET.SubElement(root, "BackingTrack")
            bt_el.clear()
            ET.SubElement(bt_el, "Enabled").text = "true"
            ET.SubElement(bt_el, "Source").text = "Local"
            ET.SubElement(bt_el, "AssetId").text = "0"
            ET.SubElement(bt_el, "FramesPerPixel").text = "228"
            ET.SubElement(bt_el, "FramePadding").text = "0"

        entries["Content/score.gpif"] = ET.tostring(root, encoding="utf-8")

    # Write cleanly
    with zipfile.ZipFile(output_gp_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)

    if has_audio and audio_path:
        try:
            from gp_audio_linker import inject_backing_track_to_gp
            inject_backing_track_to_gp(output_gp_path, audio_path)
        except Exception as e:
            print(f"Error injecting backing track in gp_builder: {e}")

    return output_gp_path