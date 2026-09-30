import os
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any, List, Tuple

# MIDI pitch to Step, Alter, Octave mapping
SEMITONE_TO_STEP = {
    0: ('C', 0),
    1: ('C', 1),   # C#
    2: ('D', 0),
    3: ('D', 1),   # D# / Eb
    4: ('E', 0),
    5: ('F', 0),
    6: ('F', 1),   # F#
    7: ('G', 0),
    8: ('G', 1),   # G#
    9: ('A', 0),
    10: ('A', 1),  # A# / Bb
    11: ('B', 0)
}

# In flat keys, prefer flats for black keys
SEMITONE_TO_STEP_FLATS = {
    0: ('C', 0),
    1: ('D', -1),  # Db
    2: ('D', 0),
    3: ('E', -1),  # Eb
    4: ('E', 0),
    5: ('F', 0),
    6: ('G', -1),  # Gb
    7: ('G', 0),
    8: ('A', -1),  # Ab
    9: ('A', 0),
    10: ('B', -1), # Bb
    11: ('B', 0)
}

NOTE_VALUE_TO_XML = {
    'Whole': ('whole', 16),
    'Half': ('half', 8),
    'Quarter': ('quarter', 4),
    'Eighth': ('eighth', 2),
    '16th': ('16th', 1),
    'Sixteenth': ('16th', 1),
    '32nd': ('32nd', 0.5),
    '64th': ('64th', 0.25)
}

GP5_DURATION_TO_XML = {
    1: ('whole', 16),
    2: ('half', 8),
    4: ('quarter', 4),
    8: ('eighth', 2),
    16: ('16th', 1),
    32: ('32nd', 0.5),
    64: ('64th', 0.25)
}

def midi_to_pitch(midi_num: int, fifths: int = 0) -> Tuple[str, int, int]:
    """Returns (step, alter, octave)."""
    octave = (midi_num // 12) - 1
    semi = midi_num % 12
    mapping = SEMITONE_TO_STEP_FLATS if fifths < 0 else SEMITONE_TO_STEP
    step, alter = mapping.get(semi, ('C', 0))
    return step, alter, octave

def convert_gp_to_musicxml(gp_path: str, title: str = "", artist: str = "") -> Optional[str]:
    """
    Converts a Guitar Pro (.gp or .gp5) file into MusicXML 3.1 string.
    Generates a 2-staff system:
      Staff 1: Bass Clef (F4) standard notation with rhythm stems, beams, flags, accidentals, key signature.
      Staff 2: 4-string or 5-string TAB staff with fret numbers and rhythm symbols.
    """
    if not os.path.exists(gp_path):
        return None

    if gp_path.lower().endswith('.gp'):
        return _convert_gpif_to_musicxml(gp_path, title, artist)
    elif gp_path.lower().endswith('.gp5'):
        return _convert_gp5_to_musicxml(gp_path, title, artist)
    return None

def _convert_gpif_to_musicxml(gp_path: str, default_title: str = "", default_artist: str = "") -> Optional[str]:
    try:
        with zipfile.ZipFile(gp_path, 'r') as z:
            if 'Content/score.gpif' not in z.namelist():
                return None
            xml_bytes = z.read('Content/score.gpif')
            try:
                root = ET.fromstring(xml_bytes)
            except Exception:
                cleaned = xml_bytes.decode('utf-8', errors='ignore')
                root = ET.fromstring(cleaned)
    except Exception as e:
        print(f"Error opening GP zip {gp_path}: {e}")
        return None

    score_title = root.findtext('.//Score/Title') or default_title or "Bass Tab"
    score_artist = root.findtext('.//Score/Artist') or default_artist or ""

    # MasterBars
    master_bars = root.findall('.//MasterBars/MasterBar')
    if not master_bars:
        return None

    # Find Bass Track
    tracks = root.findall('.//Tracks/Track')
    bass_track_id = 0
    is_5string = False
    tuning_pitches = [28, 33, 38, 43] # Default E1, A1, D2, G2

    for idx, t in enumerate(tracks):
        name = (t.findtext('Name') or '').lower()
        t_type = (t.findtext('.//InstrumentSet/Type') or '').lower()
        t_pitches = []
        for p in t.findall('.//Property[@name="Tuning"]'):
            p_nodes = p.findall('.//Pitch')
            if p_nodes:
                t_pitches = [int(pn.text) for pn in p_nodes if pn.text and pn.text.strip().lstrip('-').isdigit()]
            else:
                ptxt = p.findtext('Pitches') or ''
                if not ptxt:
                    tuning_el = p.find('Tuning')
                    if tuning_el is not None:
                        ptxt = tuning_el.findtext('Pitches') or ''
                if ptxt:
                    t_pitches = [int(x) for x in ptxt.split() if x.strip().lstrip('-').isdigit()]
            if t_pitches:
                break

        if 'bass' in name or 'bass' in t_type or (t_pitches and min(t_pitches) <= 33 and len(t_pitches) in (4, 5)):
            bass_track_id = idx
            if len(t_pitches) >= 5:
                is_5string = True
                tuning_pitches = t_pitches[:5]
            elif len(t_pitches) == 4:
                is_5string = False
                tuning_pitches = t_pitches[:4]
            break

    num_strings = 5 if is_5string else 4

    # Rhythms Palette (divisions = 48 for exact integer tuplet subdivision)
    NOTE_VALUE_TICKS = {
        'Whole': 192,
        'Half': 96,
        'Quarter': 48,
        'Eighth': 24,
        '16th': 12,
        'Sixteenth': 12,
        '32nd': 6,
        '64th': 3
    }

    rhythms_palette: Dict[str, Dict[str, Any]] = {}
    for r in root.findall('.//Rhythms/Rhythm'):
        rid = r.get('id')
        val_name = r.findtext('NoteValue') or 'Quarter'
        xml_type = NOTE_VALUE_TO_XML.get(val_name, ('quarter', 4))[0]
        base_dur = NOTE_VALUE_TICKS.get(val_name, 48)
        dot_el = r.find('AugmentationDot')
        dot_count = int(dot_el.get('count', 1)) if dot_el is not None else 0
        if dot_count == 1:
            base_dur = int(base_dur * 1.5)
        elif dot_count == 2:
            base_dur = int(base_dur * 1.75)

        tuplet_el = r.find('PrimaryTuplet')
        tuplet = None
        if tuplet_el is not None:
            num = int(tuplet_el.get('num', 3))
            den = int(tuplet_el.get('den', 2))
            base_dur = int(round(base_dur * den / num))
            tuplet = (num, den)

        rhythms_palette[rid] = {
            'type': xml_type,
            'duration': base_dur,
            'dots': dot_count,
            'tuplet': tuplet
        }

    # Notes Palette
    notes_palette: Dict[str, Dict[str, Any]] = {}
    for n in root.findall('.//Notes/Note'):
        nid = n.get('id')
        fret = 0
        string_gp = 0
        midi_pitch = 40

        fel = n.find('.//Property[@name="Fret"]/Fret')
        if fel is not None and fel.text:
            try: fret = int(fel.text)
            except Exception: pass

        sel = n.find('.//Property[@name="String"]/String')
        if sel is not None and sel.text:
            try: string_gp = int(sel.text)
            except Exception: pass

        mel = n.find('.//Property[@name="Midi"]/Number')
        if mel is not None and mel.text:
            try: midi_pitch = int(mel.text)
            except Exception: pass
        else:
            if 0 <= string_gp < len(tuning_pitches):
                open_p = tuning_pitches[string_gp]
                midi_pitch = open_p + fret

        cp_node = n.find('.//Property[@name="ConcertPitch"]/Pitch')
        step = None
        accidental = None
        octave = None
        if cp_node is not None:
            step = cp_node.findtext('Step')
            accidental = cp_node.findtext('Accidental')
            oct_txt = cp_node.findtext('Octave')
            if oct_txt and oct_txt.isdigit():
                octave = int(oct_txt)

        tie_node = n.find('Tie')
        is_tie_orig = (tie_node is not None and tie_node.get('origin') == 'true')
        is_tie_dest = (tie_node is not None and tie_node.get('destination') == 'true')

        is_hopo_orig = (n.find('.//Property[@name="HopoOrigin"]') is not None)
        is_hopo_dest = (n.find('.//Property[@name="HopoDestination"]') is not None)

        slide_node = n.find('.//Property[@name="Slide"]/Flags')
        slide_flags = int(slide_node.text) if (slide_node is not None and slide_node.text and slide_node.text.strip().isdigit()) else 0

        notes_palette[nid] = {
            'fret': fret,
            'string_gp': string_gp,
            'midi': midi_pitch,
            'step': step,
            'accidental': accidental,
            'octave': octave,
            'tie_orig': is_tie_orig,
            'tie_dest': is_tie_dest,
            'hopo_orig': is_hopo_orig,
            'hopo_dest': is_hopo_dest,
            'slide': slide_flags
        }

    # Beats Palette
    beats_palette: Dict[str, Dict[str, Any]] = {}
    for b in root.findall('.//Beats/Beat'):
        bid = b.get('id')
        r_ref = b.find('Rhythm').get('ref') if b.find('Rhythm') is not None else '0'
        rhythm_info = rhythms_palette.get(r_ref, {'type': 'quarter', 'duration': 16, 'dots': 0, 'tuplet': None})
        ntxt = b.findtext('Notes') or ''
        nids = ntxt.split()
        note_objs = [notes_palette[nid] for nid in nids if nid in notes_palette]
        beats_palette[bid] = {
            'rhythm': rhythm_info,
            'notes': note_objs,
            'is_rest': len(note_objs) == 0
        }

    # Voices Palette
    voices_palette: Dict[str, List[str]] = {}
    for v in root.findall('.//Voices/Voice'):
        vid = v.get('id')
        btxt = v.findtext('Beats') or ''
        voices_palette[vid] = btxt.split()

    # Bars Map
    bar_map = {b.get('id'): b for b in root.findall('.//Bars/Bar')}

    # Build MusicXML Document
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">',
        '<score-partwise version="3.1">',
        '  <work>',
        f'    <work-title>{_xml_escape(score_title)}</work-title>',
        '  </work>',
        '  <identification>',
        f'    <creator type="composer">{_xml_escape(score_artist)}</creator>',
        '  </identification>',
        '  <part-list>',
        '    <score-part id="P1">',
        '      <part-name>Electric Bass</part-name>',
        '      <score-instrument id="P1-I1">',
        '        <instrument-name>Electric Bass</instrument-name>',
        '      </score-instrument>',
        '    </score-part>',
        '  </part-list>',
        '  <part id="P1">'
    ]

    current_fifths = 0
    current_beats = 4
    current_beat_type = 4
    divisions = 48

    num_measures = len(master_bars)

    for m_idx in range(num_measures):
        mb = master_bars[m_idx]
        bids = (mb.findtext('Bars') or '').split()
        tb = bar_map.get(bids[bass_track_id]) if bass_track_id < len(bids) else None

        # Check Key Signature
        k_el = mb.find('Key')
        fifths = current_fifths
        mode = "major"
        if k_el is not None:
            ac = k_el.findtext('AccidentalCount')
            if ac:
                try: fifths = int(ac)
                except Exception: pass
            m_txt = k_el.findtext('Mode')
            if m_txt: mode = m_txt.lower()

        # Check Time Signature
        time_str = mb.findtext('Time')
        beats_val = current_beats
        beat_type_val = current_beat_type
        if time_str and '/' in time_str:
            parts = time_str.split('/')
            try:
                beats_val = int(parts[0])
                beat_type_val = int(parts[1])
            except Exception: pass

        lines.append(f'    <measure number="{m_idx + 1}">')

        attr_needed = (m_idx == 0) or (fifths != current_fifths) or (beats_val != current_beats) or (beat_type_val != current_beat_type)
        if attr_needed:
            lines.append('      <attributes>')
            if m_idx == 0:
                lines.append(f'        <divisions>{divisions}</divisions>')
            if (m_idx == 0) or (fifths != current_fifths):
                lines.append('        <key>')
                lines.append(f'          <fifths>{fifths}</fifths>')
                lines.append(f'          <mode>{mode}</mode>')
                lines.append('        </key>')
                current_fifths = fifths
            if (m_idx == 0) or (beats_val != current_beats) or (beat_type_val != current_beat_type):
                lines.append('        <time>')
                lines.append(f'          <beats>{beats_val}</beats>')
                lines.append(f'          <beat-type>{beat_type_val}</beat-type>')
                lines.append('        </time>')
                current_beats = beats_val
                current_beat_type = beat_type_val

            if m_idx == 0:
                lines.append('        <staves>2</staves>')
                lines.append('        <clef number="1">')
                lines.append('          <sign>F</sign>')
                lines.append('          <line>4</line>')
                lines.append('        </clef>')
                lines.append('        <clef number="2">')
                lines.append('          <sign>TAB</sign>')
                lines.append(f'          <line>{num_strings}</line>')
                lines.append('        </clef>')
                lines.append('        <staff-details number="2">')
                lines.append(f'          <staff-lines>{num_strings}</staff-lines>')
                for line_idx, p_val in enumerate(tuning_pitches):
                    st, al, oc = midi_to_pitch(p_val, fifths)
                    lines.append(f'          <staff-tuning line="{line_idx + 1}">')
                    lines.append(f'            <tuning-step>{st}</tuning-step>')
                    if al != 0:
                        lines.append(f'            <tuning-alter>{al}</tuning-alter>')
                    lines.append(f'            <tuning-octave>{oc}</tuning-octave>')
                    lines.append('          </staff-tuning>')
                lines.append('        </staff-details>')
            lines.append('      </attributes>')

        measure_beats: List[Dict[str, Any]] = []
        if tb is not None:
            v_ids = (tb.findtext('Voices') or '').split()
            active_vid = next((v for v in v_ids if v != '-1'), None)
            if active_vid and active_vid in voices_palette:
                for bid in voices_palette[active_vid]:
                    if bid in beats_palette:
                        measure_beats.append(beats_palette[bid])

        measure_total_dur = int(current_beats * (divisions * 4 // current_beat_type))
        beats_total_dur = sum(b['rhythm']['duration'] for b in measure_beats)

        if not measure_beats or beats_total_dur == 0:
            lines.append('      <note>')
            lines.append('        <rest measure="yes" />')
            lines.append(f'        <duration>{measure_total_dur}</duration>')
            lines.append('        <voice>1</voice>')
            lines.append('        <staff>1</staff>')
            lines.append('      </note>')
            lines.append(f'      <backup><duration>{measure_total_dur}</duration></backup>')
            lines.append('      <note>')
            lines.append('        <rest measure="yes" />')
            lines.append(f'        <duration>{measure_total_dur}</duration>')
            lines.append('        <voice>2</voice>')
            lines.append('        <staff>2</staff>')
            lines.append('      </note>')
            lines.append('    </measure>')
            continue

        accum_dur_1 = 0
        for b_item in measure_beats:
            rhythm = b_item['rhythm']
            dur = rhythm['duration']
            typ = rhythm['type']
            dots = rhythm['dots']
            notes = b_item['notes']

            if b_item['is_rest'] or not notes:
                lines.append('      <note>')
                lines.append('        <rest />')
                lines.append(f'        <duration>{dur}</duration>')
                lines.append('        <voice>1</voice>')
                lines.append(f'        <type>{typ}</type>')
                for _ in range(dots):
                    lines.append('        <dot />')
                if rhythm.get('tuplet'):
                    t_num, t_den = rhythm['tuplet']
                    lines.append('        <time-modification>')
                    lines.append(f'          <actual-notes>{t_num}</actual-notes>')
                    lines.append(f'          <normal-notes>{t_den}</normal-notes>')
                    lines.append('        </time-modification>')
                lines.append('        <staff>1</staff>')
                lines.append('      </note>')
                accum_dur_1 += dur
            else:
                for n_idx, note_obj in enumerate(notes):
                    lines.append('      <note>')
                    if n_idx > 0:
                        lines.append('        <chord />')
                    
                    st = note_obj.get('step')
                    al = 0
                    if note_obj.get('accidental') == '#': al = 1
                    elif note_obj.get('accidental') == 'b': al = -1
                    oc = note_obj.get('octave')

                    if not st or oc is None:
                        st, al, oc = midi_to_pitch(note_obj['midi'], fifths)

                    lines.append('        <pitch>')
                    lines.append(f'          <step>{st}</step>')
                    if al != 0:
                        lines.append(f'          <alter>{al}</alter>')
                    lines.append(f'          <octave>{oc}</octave>')
                    lines.append('        </pitch>')
                    lines.append(f'        <duration>{dur}</duration>')
                    if note_obj.get('tie_dest'):
                        lines.append('        <tie type="stop" />')
                    if note_obj.get('tie_orig'):
                        lines.append('        <tie type="start" />')
                    lines.append('        <voice>1</voice>')
                    lines.append(f'        <type>{typ}</type>')
                    for _ in range(dots):
                        lines.append('        <dot />')
                    if rhythm.get('tuplet'):
                        t_num, t_den = rhythm['tuplet']
                        lines.append('        <time-modification>')
                        lines.append(f'          <actual-notes>{t_num}</actual-notes>')
                        lines.append(f'          <normal-notes>{t_den}</normal-notes>')
                        lines.append('        </time-modification>')
                    lines.append('        <stem>down</stem>')
                    lines.append('        <staff>1</staff>')
                    notations1 = []
                    if note_obj.get('tie_dest'):
                        notations1.append('          <tied type="stop" />')
                    if note_obj.get('tie_orig'):
                        notations1.append('          <tied type="start" />')
                    if note_obj.get('hopo_dest'):
                        notations1.append('          <slur type="stop" number="1" />')
                    if note_obj.get('hopo_orig'):
                        notations1.append('          <slur type="start" number="1" />')
                    if note_obj.get('slide', 0) > 0:
                        notations1.append('          <slide type="start" number="1" />')
                    if notations1:
                        lines.append('        <notations>')
                        lines.extend(notations1)
                        lines.append('        </notations>')
                    lines.append('      </note>')
                accum_dur_1 += dur

        lines.append(f'      <backup><duration>{accum_dur_1}</duration></backup>')

        for b_item in measure_beats:
            rhythm = b_item['rhythm']
            dur = rhythm['duration']
            typ = rhythm['type']
            dots = rhythm['dots']
            notes = b_item['notes']

            if b_item['is_rest'] or not notes:
                lines.append('      <note>')
                lines.append('        <rest />')
                lines.append(f'        <duration>{dur}</duration>')
                lines.append('        <voice>2</voice>')
                lines.append(f'        <type>{typ}</type>')
                for _ in range(dots):
                    lines.append('        <dot />')
                if rhythm.get('tuplet'):
                    t_num, t_den = rhythm['tuplet']
                    lines.append('        <time-modification>')
                    lines.append(f'          <actual-notes>{t_num}</actual-notes>')
                    lines.append(f'          <normal-notes>{t_den}</normal-notes>')
                    lines.append('        </time-modification>')
                lines.append('        <staff>2</staff>')
                lines.append('      </note>')
            else:
                for n_idx, note_obj in enumerate(notes):
                    lines.append('      <note>')
                    if n_idx > 0:
                        lines.append('        <chord />')
                    
                    st = note_obj.get('step')
                    al = 0
                    if note_obj.get('accidental') == '#': al = 1
                    elif note_obj.get('accidental') == 'b': al = -1
                    oc = note_obj.get('octave')

                    if not st or oc is None:
                        st, al, oc = midi_to_pitch(note_obj['midi'], fifths)

                    string_gp = note_obj.get('string_gp', 0)
                    string_xml = num_strings - string_gp if num_strings > string_gp else 1
                    fret_val = note_obj.get('fret', 0)

                    lines.append('        <pitch>')
                    lines.append(f'          <step>{st}</step>')
                    if al != 0:
                        lines.append(f'          <alter>{al}</alter>')
                    lines.append(f'          <octave>{oc}</octave>')
                    lines.append('        </pitch>')
                    lines.append(f'        <duration>{dur}</duration>')
                    if note_obj.get('tie_dest'):
                        lines.append('        <tie type="stop" />')
                    if note_obj.get('tie_orig'):
                        lines.append('        <tie type="start" />')
                    lines.append('        <voice>2</voice>')
                    lines.append(f'        <type>{typ}</type>')
                    for _ in range(dots):
                        lines.append('        <dot />')
                    if rhythm.get('tuplet'):
                        t_num, t_den = rhythm['tuplet']
                        lines.append('        <time-modification>')
                        lines.append(f'          <actual-notes>{t_num}</actual-notes>')
                        lines.append(f'          <normal-notes>{t_den}</normal-notes>')
                        lines.append('        </time-modification>')
                    lines.append('        <stem>down</stem>')
                    lines.append('        <staff>2</staff>')
                    lines.append('        <notations>')
                    if note_obj.get('tie_dest'):
                        lines.append('          <tied type="stop" />')
                    if note_obj.get('tie_orig'):
                        lines.append('          <tied type="start" />')
                    if note_obj.get('hopo_dest'):
                        lines.append('          <slur type="stop" number="1" />')
                    if note_obj.get('hopo_orig'):
                        lines.append('          <slur type="start" number="1" />')
                    if note_obj.get('slide', 0) > 0:
                        lines.append('          <slide type="start" number="1" />')
                    lines.append('          <technical>')
                    lines.append(f'            <string>{string_xml}</string>')
                    lines.append(f'            <fret>{fret_val}</fret>')
                    lines.append('          </technical>')
                    lines.append('        </notations>')
                    lines.append('      </note>')

        lines.append('    </measure>')

    lines.append('  </part>')
    lines.append('</score-partwise>')
    return '\n'.join(lines)

def _convert_gp5_to_musicxml(gp5_path: str, default_title: str = "", default_artist: str = "") -> Optional[str]:
    try:
        import guitarpro
    except ImportError:
        return None

    try:
        song = guitarpro.parse(gp5_path)
    except Exception as e:
        print(f"Error parsing GP5 {gp5_path}: {e}")
        return None

    score_title = song.title or default_title or "Bass Tab"
    score_artist = song.artist or default_artist or ""

    bass_track = None
    for t in song.tracks:
        name = (t.name or '').lower()
        if 'bass' in name or len(t.strings) in (4, 5):
            bass_track = t
            break
    if not bass_track and song.tracks:
        bass_track = song.tracks[0]

    num_strings = len(bass_track.strings) if bass_track else 4
    tuning_pitches = [s.value for s in bass_track.strings] if bass_track else [28, 33, 38, 43]
    tuning_lowest_first = list(reversed(tuning_pitches))

    divisions = 48
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">',
        '<score-partwise version="3.1">',
        '  <work>',
        f'    <work-title>{_xml_escape(score_title)}</work-title>',
        '  </work>',
        '  <identification>',
        f'    <creator type="composer">{_xml_escape(score_artist)}</creator>',
        '  </identification>',
        '  <part-list>',
        '    <score-part id="P1">',
        '      <part-name>Electric Bass</part-name>',
        '    </score-part>',
        '  </part-list>',
        '  <part id="P1">'
    ]

    current_fifths = 0
    current_beats = 4
    current_beat_type = 4

    for m_idx, measure in enumerate(bass_track.measures):
        header = measure.header
        fifths = header.keySignature.value if hasattr(header, 'keySignature') and hasattr(header.keySignature, 'value') else 0
        beats_val = header.timeSignature.numerator if hasattr(header, 'timeSignature') else 4
        beat_type_val = header.timeSignature.denominator.value if hasattr(header, 'timeSignature') and hasattr(header.timeSignature.denominator, 'value') else 4

        lines.append(f'    <measure number="{m_idx + 1}">')
        attr_needed = (m_idx == 0) or (fifths != current_fifths) or (beats_val != current_beats) or (beat_type_val != current_beat_type)

        if attr_needed:
            lines.append('      <attributes>')
            if m_idx == 0:
                lines.append(f'        <divisions>{divisions}</divisions>')
            if (m_idx == 0) or (fifths != current_fifths):
                lines.append('        <key>')
                lines.append(f'          <fifths>{fifths}</fifths>')
                lines.append('        </key>')
                current_fifths = fifths
            if (m_idx == 0) or (beats_val != current_beats) or (beat_type_val != current_beat_type):
                lines.append('        <time>')
                lines.append(f'          <beats>{beats_val}</beats>')
                lines.append(f'          <beat-type>{beat_type_val}</beat-type>')
                lines.append('        </time>')
                current_beats = beats_val
                current_beat_type = beat_type_val

            if m_idx == 0:
                lines.append('        <staves>2</staves>')
                lines.append('        <clef number="1">')
                lines.append('          <sign>F</sign>')
                lines.append('          <line>4</line>')
                lines.append('        </clef>')
                lines.append('        <clef number="2">')
                lines.append('          <sign>TAB</sign>')
                lines.append(f'          <line>{num_strings}</line>')
                lines.append('        </clef>')
                lines.append('        <staff-details number="2">')
                lines.append(f'          <staff-lines>{num_strings}</staff-lines>')
                for line_idx, p_val in enumerate(tuning_lowest_first):
                    st, al, oc = midi_to_pitch(p_val, fifths)
                    lines.append(f'          <staff-tuning line="{line_idx + 1}">')
                    lines.append(f'            <tuning-step>{st}</tuning-step>')
                    if al != 0:
                        lines.append(f'            <tuning-alter>{al}</tuning-alter>')
                    lines.append(f'            <tuning-octave>{oc}</tuning-octave>')
                    lines.append('          </staff-tuning>')
                lines.append('        </staff-details>')
            lines.append('      </attributes>')

        voice0 = measure.voices[0] if measure.voices else None
        beats = [b for b in voice0.beats if b.status != guitarpro.BeatStatus.empty] if voice0 else []

        measure_total_dur = int(current_beats * (divisions * 4 // current_beat_type))
        if not beats:
            lines.append('      <note>')
            lines.append('        <rest measure="yes" />')
            lines.append(f'        <duration>{measure_total_dur}</duration>')
            lines.append('        <voice>1</voice>')
            lines.append('        <staff>1</staff>')
            lines.append('      </note>')
            lines.append(f'      <backup><duration>{measure_total_dur}</duration></backup>')
            lines.append('      <note>')
            lines.append('        <rest measure="yes" />')
            lines.append(f'        <duration>{measure_total_dur}</duration>')
            lines.append('        <voice>2</voice>')
            lines.append('        <staff>2</staff>')
            lines.append('      </note>')
            lines.append('    </measure>')
            continue

        accum_dur = 0
        for b in beats:
            d_val = b.duration.value if hasattr(b.duration, 'value') else 4
            xml_type = GP5_DURATION_TO_XML.get(d_val, ('quarter', 4))[0]
            base_dur = int(round(192.0 / d_val)) if d_val > 0 else 48
            dots = 1 if (hasattr(b.duration, 'isDotted') and b.duration.isDotted) else 0
            if dots == 1: base_dur = int(base_dur * 1.5)
            tuplet = None
            if hasattr(b.duration, 'tuplet') and b.duration.tuplet:
                t_enters = getattr(b.duration.tuplet, 'enters', 3)
                t_times = getattr(b.duration.tuplet, 'times', 2)
                if t_enters and t_times:
                    base_dur = int(round(base_dur * t_times / t_enters))
                    tuplet = (t_enters, t_times)
            dur = base_dur

            is_rest = (b.status == guitarpro.BeatStatus.rest or not b.notes)
            if is_rest:
                lines.append('      <note>')
                lines.append('        <rest />')
                lines.append(f'        <duration>{dur}</duration>')
                lines.append('        <voice>1</voice>')
                lines.append(f'        <type>{xml_type}</type>')
                if dots: lines.append('        <dot />')
                if tuplet:
                    lines.append('        <time-modification>')
                    lines.append(f'          <actual-notes>{tuplet[0]}</actual-notes>')
                    lines.append(f'          <normal-notes>{tuplet[1]}</normal-notes>')
                    lines.append('        </time-modification>')
                lines.append('        <staff>1</staff>')
                lines.append('      </note>')
                accum_dur += dur
            else:
                for n_idx, n in enumerate(b.notes):
                    lines.append('      <note>')
                    if n_idx > 0: lines.append('        <chord />')
                    str_idx = n.string - 1
                    open_p = tuning_pitches[str_idx] if str_idx < len(tuning_pitches) else 28
                    midi_p = open_p + n.value
                    st, al, oc = midi_to_pitch(midi_p, fifths)
                    is_tied = bool(hasattr(n, 'type') and hasattr(guitarpro, 'NoteType') and n.type == guitarpro.NoteType.tie)
                    is_hopo = bool(hasattr(n, 'effect') and hasattr(n.effect, 'hammer') and n.effect.hammer)
                    has_slide = bool(hasattr(n, 'effect') and hasattr(n.effect, 'slides') and n.effect.slides)

                    lines.append('        <pitch>')
                    lines.append(f'          <step>{st}</step>')
                    if al != 0: lines.append(f'          <alter>{al}</alter>')
                    lines.append(f'          <octave>{oc}</octave>')
                    lines.append('        </pitch>')
                    lines.append(f'        <duration>{dur}</duration>')
                    if is_tied:
                        lines.append('        <tie type="stop" />')
                    lines.append('        <voice>1</voice>')
                    lines.append(f'        <type>{xml_type}</type>')
                    if dots: lines.append('        <dot />')
                    if tuplet:
                        lines.append('        <time-modification>')
                        lines.append(f'          <actual-notes>{tuplet[0]}</actual-notes>')
                        lines.append(f'          <normal-notes>{tuplet[1]}</normal-notes>')
                        lines.append('        </time-modification>')
                    lines.append('        <stem>down</stem>')
                    lines.append('        <staff>1</staff>')
                    notations1 = []
                    if is_tied:
                        notations1.append('          <tied type="stop" />')
                    if is_hopo:
                        notations1.append('          <slur type="start" number="1" />')
                    if has_slide:
                        notations1.append('          <slide type="start" number="1" />')
                    if notations1:
                        lines.append('        <notations>')
                        lines.extend(notations1)
                        lines.append('        </notations>')
                    lines.append('      </note>')
                accum_dur += dur

        lines.append(f'      <backup><duration>{accum_dur}</duration></backup>')
        for b in beats:
            d_val = b.duration.value if hasattr(b.duration, 'value') else 4
            xml_type = GP5_DURATION_TO_XML.get(d_val, ('quarter', 4))[0]
            base_dur = int(round(192.0 / d_val)) if d_val > 0 else 48
            dots = 1 if (hasattr(b.duration, 'isDotted') and b.duration.isDotted) else 0
            if dots == 1: base_dur = int(base_dur * 1.5)
            tuplet = None
            if hasattr(b.duration, 'tuplet') and b.duration.tuplet:
                t_enters = getattr(b.duration.tuplet, 'enters', 3)
                t_times = getattr(b.duration.tuplet, 'times', 2)
                if t_enters and t_times:
                    base_dur = int(round(base_dur * t_times / t_enters))
                    tuplet = (t_enters, t_times)
            dur = base_dur

            is_rest = (b.status == guitarpro.BeatStatus.rest or not b.notes)
            if is_rest:
                lines.append('      <note>')
                lines.append('        <rest />')
                lines.append(f'        <duration>{dur}</duration>')
                lines.append('        <voice>2</voice>')
                lines.append(f'        <type>{xml_type}</type>')
                if dots: lines.append('        <dot />')
                if tuplet:
                    lines.append('        <time-modification>')
                    lines.append(f'          <actual-notes>{tuplet[0]}</actual-notes>')
                    lines.append(f'          <normal-notes>{tuplet[1]}</normal-notes>')
                    lines.append('        </time-modification>')
                lines.append('        <staff>2</staff>')
                lines.append('      </note>')
            else:
                for n_idx, n in enumerate(b.notes):
                    lines.append('      <note>')
                    if n_idx > 0: lines.append('        <chord />')
                    str_idx = n.string - 1
                    open_p = tuning_pitches[str_idx] if str_idx < len(tuning_pitches) else 28
                    midi_p = open_p + n.value
                    st, al, oc = midi_to_pitch(midi_p, fifths)

                    is_tied = bool(hasattr(n, 'type') and hasattr(guitarpro, 'NoteType') and n.type == guitarpro.NoteType.tie)
                    is_hopo = bool(hasattr(n, 'effect') and hasattr(n.effect, 'hammer') and n.effect.hammer)
                    has_slide = bool(hasattr(n, 'effect') and hasattr(n.effect, 'slides') and n.effect.slides)

                    lines.append('        <pitch>')
                    lines.append(f'          <step>{st}</step>')
                    if al != 0: lines.append(f'          <alter>{al}</alter>')
                    lines.append(f'          <octave>{oc}</octave>')
                    lines.append('        </pitch>')
                    lines.append(f'        <duration>{dur}</duration>')
                    if is_tied:
                        lines.append('        <tie type="stop" />')
                    lines.append('        <voice>2</voice>')
                    lines.append(f'        <type>{xml_type}</type>')
                    if dots: lines.append('        <dot />')
                    if tuplet:
                        lines.append('        <time-modification>')
                        lines.append(f'          <actual-notes>{tuplet[0]}</actual-notes>')
                        lines.append(f'          <normal-notes>{tuplet[1]}</normal-notes>')
                        lines.append('        </time-modification>')
                    lines.append('        <stem>down</stem>')
                    lines.append('        <staff>2</staff>')
                    lines.append('        <notations>')
                    if is_tied:
                        lines.append('          <tied type="stop" />')
                    if is_hopo:
                        lines.append('          <slur type="start" number="1" />')
                    if has_slide:
                        lines.append('          <slide type="start" number="1" />')
                    lines.append('          <technical>')
                    lines.append(f'            <string>{n.string}</string>')
                    lines.append(f'            <fret>{n.value}</fret>')
                    lines.append('          </technical>')
                    lines.append('        </notations>')
                    lines.append('      </note>')

        lines.append('    </measure>')

    lines.append('  </part>')
    lines.append('</score-partwise>')
    return '\n'.join(lines)

def _xml_escape(text: str) -> str:
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;'))
