"""QScore -> Guitar Pro 8 (.gp) with per-bar SyncPoints locked to the audio."""
import glob
import json
import os
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

import numpy as np

from bassnet.quantize import QScore, TPB
from bassnet.spelling import pc_table, spell

SR_GP = 44100

# ticks (TPB=24 per beat) -> (NoteValue, dots, tuplet)
STRAIGHT = [(96, ("Whole", 0, None)), (72, ("Half", 1, None)), (48, ("Half", 0, None)), (36, ("Quarter", 1, None)),
            (24, ("Quarter", 0, None)), (18, ("Eighth", 1, None)), (12, ("Eighth", 0, None)), (9, ("16th", 1, None)),
            (6, ("16th", 0, None)), (3, ("32nd", 0, None))]
TRIPLET = [(16, ("Quarter", 0, (3, 2))), (8, ("Eighth", 0, (3, 2))), (4, ("16th", 0, (3, 2))), (2, ("32nd", 0, (3, 2)))]
# compound meters (beat = dotted quarter = 24 ticks)
COMPOUND = [(72, ("Half", 1, None)), (48, ("Half", 0, None)), (36, ("Quarter", 1, None)), (24, ("Quarter", 1, None)),
            (16, ("Quarter", 0, None)), (12, ("Eighth", 1, None)), (8, ("Eighth", 0, None)), (6, ("16th", 1, None)),
            (4, ("16th", 0, None)), (2, ("32nd", 0, None))]


# Fixed template: a purchased GP8 original with a clean bass sound (copied once, never written).
# Everything written below follows the element order / values of real GP8 files (see bassnet/gp_lint.py).
TEMPLATE = r"E:\BassStation\cache\bassnet\template.gp"


def _template_gp() -> str:
    if os.path.exists(TEMPLATE):
        return TEMPLATE
    raise FileNotFoundError(TEMPLATE)


# Text fields GP8 stores as CDATA (ElementTree cannot emit CDATA and drops it from the template;
# without it GP8 ignores e.g. the whole BackingTrack element).
CDATA_TAGS = ("Text", "Letter", "Name", "Label", "ShortName", "Title", "SubTitle", "Artist", "Album", "Words",
              "Music", "WordsAndMusic", "Copyright", "Tabber", "Instructions", "Notices", "FirstPageHeader",
              "FirstPageFooter", "PageHeader", "PageFooter", "OriginalFilePath", "OriginalFileSha1",
              "EmbeddedFilePath", "FreeText")


_CS, _CE = "\x01CDATA_S\x01", "\x01CDATA_E\x01"


def _mark_cdata(el, inside_instrument=False):
    """GP8 keeps InstrumentSet names as plain text; every other text field above is CDATA."""
    inside_instrument = inside_instrument or el.tag == "InstrumentSet"
    if el.tag in CDATA_TAGS and len(el) == 0 and not inside_instrument:
        el.text = _CS + (el.text or "") + _CE
    for c in el:
        _mark_cdata(c, inside_instrument)


def _cdata(xml: str) -> str:
    import html
    return re.sub(re.escape(_CS) + "(.*?)" + re.escape(_CE),
                  lambda m: "<![CDATA[" + html.unescape(m.group(1)).replace("]]>", "]] >") + "]]>", xml, flags=re.S)


def midi_to_gp_pitch(midi: int):
    """GP8 spelling: accidental '#' (or ''), concert octave = midi // 12 (MIDI 35 -> B2)."""
    steps = ['C', 'C', 'D', 'D', 'E', 'F', 'F', 'G', 'G', 'A', 'A', 'B']
    accs = ['', '#', '', '#', '', '', '#', '', '#', '', '#', '']
    return steps[midi % 12], accs[midi % 12], midi // 12


def split_value(start, length, beat_div, compound):
    """Split [start, start+length) ticks (relative to the bar) into notatable pieces."""
    out = []
    pos, end = start, start + length
    while pos < end:
        beat = pos // TPB
        div = beat_div.get(beat, 4)
        table = COMPOUND if compound else (TRIPLET if div in (3, 6) else STRAIGHT)
        chosen = None
        for ticks, val in table:
            if ticks > end - pos:
                continue
            # alignment: long straight values must start on beats and stay beat-aligned
            if ticks >= TPB:
                if pos % TPB != 0 or (ticks % TPB and not compound):
                    continue
                if not compound and ticks in (72, 48, 96) and (pos // TPB) % 2 == 1 and ticks != 72:
                    continue
            else:
                if (pos % TPB) + ticks > TPB:
                    continue          # never cross a beat with sub-beat values
                unit = ticks if val[1] == 0 else ticks * 2 // 3
                if (pos % TPB) % unit != 0:
                    continue
            chosen = (ticks, val)
            break
        if chosen is None:
            # smallest possible remainder in this beat
            rem = min(end - pos, TPB - pos % TPB)
            smallest = table[-1]
            chosen = (min(rem, smallest[0]), smallest[1])
        out.append((pos, chosen[0], chosen[1]))
        pos += chosen[0]
    return out


def build_bar_events(qs: QScore, compound_unused: bool = False):
    """Returns per-bar list of (start_tick_in_bar, ticks, rhythm_val, note_or_None, tie_orig, tie_dest)."""
    edges = [st * TPB for st in qs.bar_starts]
    bars: List[List] = [[] for _ in range(qs.n_bars)]
    import bisect
    for n in qs.notes:
        s, e = n.tick, n.tick + n.dur
        first = True
        while s < e:
            b = bisect.bisect_right(edges, s) - 1
            if b < 0 or b >= qs.n_bars:
                break
            be = min(e, edges[b + 1])
            bars[b].append((s - edges[b], be - s, n, not first, be < e))
            first = False
            s = be
    out = []
    for b in range(qs.n_bars):
        bar_len = edges[b + 1] - edges[b]
        compound = qs.bar_compound[b]
        segs = sorted(bars[b], key=lambda x: x[0])
        beat_div = {k - qs.bar_starts[b]: v for k, v in qs.beat_div.items() if qs.bar_starts[b] <= k < qs.bar_starts[b + 1]}
        ev = []
        pos = 0
        for (s, ln, n, tie_dest, tie_orig) in segs:
            if s > pos:
                for (p, t, val) in split_value(pos, s - pos, beat_div, compound):
                    ev.append((p, t, val, None, False, False))
            pieces = split_value(s, ln, beat_div, compound)
            for i, (p, t, val) in enumerate(pieces):
                ev.append((p, t, val, n, tie_orig or i < len(pieces) - 1, tie_dest or i > 0))
            pos = s + ln
        if pos == 0 and (bar_len // TPB) in (1, 2, 3, 4):
            ev.append((0, bar_len, "BAR_REST", None, False, False))
        elif pos < bar_len:
            for (p, t, val) in split_value(pos, bar_len - pos, beat_div, compound):
                ev.append((p, t, val, None, False, False))
        out.append(ev)
    return out


def _bar_signature(ev, meter, comp):
    """Everything that is drawn in a bar: identical signatures may be written once under a repeat sign."""
    def nsig(n):
        if n is None:
            return None
        return (n.midi, n.string, n.fret, n.dead, n.slide, n.hopo_origin, n.hopo_dest, n.flag,
                bool(getattr(n, "slap", False)), bool(getattr(n, "pop", False)))
    return (meter, comp, tuple((p, t, val, nsig(n), to, td) for (p, t, val, n, to, td) in ev))


# repeat style learned from the purchased tabs: only 10 of 338 use repeat signs at all, always on 2/4/8/16-bar
# phrases (never single bars), mostly 2-4 passes, alternate endings in just 2 files -> write most things out
# Identical 2/4/8-bar blocks exist in far more tabs than use repeats (on the tabs' own content, min_saved 4 would
# put repeats in 27% of them, 8 in 11.5%, 12 in 2.8%) and the blocks he repeats look like the ones he writes out,
# so it is a frequency choice: 8 halves the rate and still catches 8 of his 10 repeat tabs (cache/rep_stat.py).
REPEAT_LENGTHS = (2, 4, 8, 16)
REPEAT_MIN_SAVED = 8
REPEAT_ALT = False


def find_repeats(sigs, section_starts=(), max_len=16, min_saved=None, lengths=None, allow_alt=None):
    """Consecutive identical bar blocks -> [(first bar, block length L, times played k, ending length m)].
    m = 0: plain repeat |: L bars :| x k. m > 0 (k = 2): the two passes share their first L - m bars and differ in
    the last m (|: common |1. ending :|2. ending |). Greedy left to right, most bars saved wins (ties: shorter
    block). A block never swallows a section start except at its own first bar, blocks made only of empty bars stay
    written out, and a repeat must save at least min_saved bars (a lone bar played twice reads better written out)."""
    starts = set(section_starts)
    min_saved = REPEAT_MIN_SAVED if min_saved is None else min_saved
    lengths = REPEAT_LENGTHS if lengths is None else lengths
    allow_alt = REPEAT_ALT if allow_alt is None else allow_alt
    anchors = sorted(starts | {0})
    n = len(sigs)
    out = []
    i = 0
    while i < n:
        best = None
        phrase_pos = i - max(a for a in anchors if a <= i)      # bars since the section (or song) start
        for L in range(1, max_len + 1):
            if L not in lengths or phrase_pos % L:
                if i + 2 * L > n:
                    break
                continue
            if i + 2 * L > n:
                break
            if any(s in starts for s in range(i + 1, i + L)):
                break
            if all(all(e[3] is None for e in sig[2]) for sig in sigs[i:i + L]):
                continue
            k = 1
            while (i + (k + 1) * L <= n and sigs[i + k * L:i + (k + 1) * L] == sigs[i:i + L]
                   and not any(s in starts for s in range(i + k * L, i + (k + 1) * L))):
                k += 1
            if k >= 2 and L * (k - 1) >= min_saved and (best is None or L * (k - 1) > _saved(best)):
                best = (i, L, k, 0)
        for L in (range(2, max_len + 1) if allow_alt else ()):
            if L not in lengths or phrase_pos % L:
                continue
            if i + 2 * L > n or any(s in starts for s in range(i + 1, i + 2 * L)):
                break
            for m in range(1, L // 2 + 1):
                c = L - m
                if sigs[i:i + c] == sigs[i + L:i + L + c] and sigs[i + c:i + L] != sigs[i + L + c:i + 2 * L]:
                    if not all(all(e[3] is None for e in sig[2]) for sig in sigs[i:i + c]):
                        if c >= min_saved and (best is None or c > _saved(best)):
                            best = (i, L, 2, m)
                    break
        if best:
            out.append(best)
            i += best[1] * best[2]
        else:
            i += 1
    return out


def _saved(block):
    _, L, k, m = block
    return L * (k - 1) if m == 0 else L - m


def write_gp(qs: QScore, out_path: str, title: str, artist: str, audio_path: Optional[str],
             tuning: List[int], key: Tuple[int, str] = (0, "Major"), compound: bool = False,
             subtitle: str = "", sections: Optional[Dict[int, Tuple[str, str]]] = None,
             repeats: bool = False, sync_tol: Optional[float] = None,
             bar_keys: Optional[Dict[int, Tuple[int, str]]] = None) -> str:
    """bar_keys: {played bar: (signature, mode)} key changes (from that bar on; bassnet/key_local.py);
    sections: {played bar: (letter, text)} rehearsal marks; repeats: write identical consecutive bar blocks
    once under repeat signs (sync points then carry the bar occurrence of every pass).
    sync_tol: None = a sync point on every bar (exact timing, used by evaluation); a number (s) = a few fixed
    written tempos (tempo_map.tempo_segments) and sync points only where the recording drifts more than that."""
    tpl = _template_gp()
    entries = {}
    with zipfile.ZipFile(tpl) as z:
        for it in z.infolist():
            if not it.filename.startswith("Content/Assets/"):
                entries[it.filename] = z.read(it.filename)
    text = entries["Content/score.gpif"].decode("utf-8", errors="replace")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    root = ET.fromstring(text)

    for tag, val in (("Title", title), ("Artist", artist), ("SubTitle", subtitle), ("Album", ""),
                     ("Words", ""), ("Music", ""), ("Tabber", "BaSSDream")):
        el = root.find(f"Score/{tag}")
        if el is not None:
            el.text = val

    # single bass track with the requested tuning
    tracks = root.find("Tracks")
    for extra in tracks.findall("Track")[1:]:
        tracks.remove(extra)
    tr = tracks.find("Track")
    for p in tr.iter("Property"):
        if p.get("name") == "Tuning":
            p.find("Pitches").text = " ".join(str(x) for x in tuning)
        if p.get("name") == "CapoFret":
            p.find("Fret").text = "0"
    # drop the template song's own automations (sound switches / DSP changes at its bar numbers)
    for path in ("Automations", "RSE/ChannelStrip/Automations"):
        el = tr.find(path)
        if el is not None:
            for a in list(el):
                el.remove(a)
    ly = tr.find("Lyrics")
    if ly is not None:
        for line in ly.iter("Line"):
            for c in list(line):
                if c.tag == "Text":
                    c.text = ""

    bar_len_q = np.array([qs.meter_of(b) * (1.5 if qs.bar_compound[b] else 1.0) for b in range(qs.n_bars)])
    bar_times = [qs.bar_time(b) for b in range(qs.n_bars + 1)]
    durs = np.diff(bar_times)
    tempi = bar_len_q * 60.0 / np.maximum(durs, 1e-3)
    from bassnet.tempo_map import tempo_segments, sparse_sync
    bar_qcum = np.concatenate([[0.0], np.cumsum(bar_len_q)])
    segs = tempo_segments(bar_qcum, bar_times)
    base_tempo = segs[0][1]
    seg_start = {b: bpm for b, bpm in segs}

    def written_bpm(b):
        return [bpm for s0, bpm in segs if s0 <= b][-1]

    mt = root.find("MasterTrack")
    autos = mt.find("Automations")
    if autos is None:
        autos = ET.SubElement(mt, "Automations")
    for a in list(autos):
        autos.remove(a)
    ta = ET.SubElement(autos, "Automation")
    for k, v in (("Type", "Tempo"), ("Linear", "false"), ("Bar", "0"), ("Position", "0"), ("Visible", "true"),
                 ("Value", f"{base_tempo} 2")):
        ET.SubElement(ta, k).text = v
    has_audio = bool(audio_path and os.path.exists(audio_path))
    t0 = bar_times[0]
    events = build_bar_events(qs, compound)
    blocks = []
    if repeats:
        ck, keyed = tuple(key), []
        for b in range(len(events)):
            ck = tuple((bar_keys or {}).get(b, ck))
            keyed.append(ck)
        # the bar's key is part of what is drawn: identical notes under another key signature are not a repeat
        sigs = [_bar_signature(ev, qs.meter_of(b), qs.bar_compound[b]) + (keyed[b],) for b, ev in enumerate(events)]
        blocks = find_repeats(sigs, sorted(set((sections or {}).keys()) | set((bar_keys or {}).keys())))
    written, occ_of, rep_mark, alt_mark = [], {}, {}, {}
    bi = 0
    block_at = {st: (L, k, m) for st, L, k, m in blocks}
    while bi < qs.n_bars:
        if bi in block_at:
            L, k, m = block_at[bi]
            w0 = len(written)
            written.extend(range(bi, bi + L))
            rep_mark[w0] = rep_mark.get(w0, (False, False, 0))
            rep_mark[w0] = (True, rep_mark[w0][1], rep_mark[w0][2])
            last = w0 + L - 1
            rep_mark[last] = (rep_mark.get(last, (False, False, 0))[0], True, k)
            if m == 0:
                for r in range(k):
                    for j in range(L):
                        occ_of[bi + r * L + j] = (w0 + j, r)
            else:
                c = L - m
                w2 = len(written)
                written.extend(range(bi + L + c, bi + 2 * L))          # second ending
                for j in range(L):
                    occ_of[bi + j] = (w0 + j, 0)
                for j in range(c):
                    occ_of[bi + L + j] = (w0 + j, 1)
                for j in range(m):
                    occ_of[bi + L + c + j] = (w2 + j, 0)
                    alt_mark[w0 + c + j] = "1"
                    alt_mark[w2 + j] = "2"
            bi += L * k
        else:
            occ_of[bi] = (len(written), 0)
            written.append(bi)
            bi += 1
    for b0, bpm in segs[1:]:
        ta = ET.SubElement(autos, "Automation")
        for k, v in (("Type", "Tempo"), ("Linear", "false"), ("Bar", str(occ_of[b0][0])), ("Position", "0"),
                     ("Visible", "true"), ("Value", f"{bpm} 2")):
            ET.SubElement(ta, k).text = v
    if sync_tol is None:
        keep = list(range(qs.n_bars))
    else:
        keep = sparse_sync(bar_qcum, bar_times, must=set(seg_start), tol=sync_tol)
    nxt = {k: (keep[i + 1] if i + 1 < len(keep) else qs.n_bars) for i, k in enumerate(keep)}
    for b in keep:
        if True:
            wb, oc = occ_of[b]
            e = nxt[b]
            span_tempo = (bar_qcum[e] - bar_qcum[b]) * 60.0 / max(bar_times[e] - bar_times[b], 1e-3)
            a = ET.SubElement(autos, "Automation")
            for k, v in (("Type", "SyncPoint"), ("Linear", "false"), ("Bar", str(b)), ("Position", "0"), ("Visible", "true")):
                ET.SubElement(a, k).text = v
            val = ET.SubElement(a, "Value")
            ET.SubElement(val, "BarIndex").text = str(wb)
            ET.SubElement(val, "BarOccurrence").text = str(oc)
            ET.SubElement(val, "ModifiedTempo").text = f"{span_tempo:.5f}"
            ET.SubElement(val, "OriginalTempo").text = str(written_bpm(b))
            ET.SubElement(val, "FrameOffset").text = str(int(round((bar_times[b] - t0) * SR_GP)))

    # rhythms
    for tag in ("MasterBars", "Bars", "Voices", "Beats", "Notes", "Rhythms"):
        el = root.find(tag)
        if el is None:
            el = ET.SubElement(root, tag)
        el.clear()
    rhythms_el = root.find("Rhythms")
    rid: Dict = {}

    def rhythm_id(val):
        if val not in rid:
            rid[val] = str(len(rid))
            r = ET.SubElement(rhythms_el, "Rhythm", id=rid[val])
            ET.SubElement(r, "NoteValue").text = val[0]
            if val[1]:
                ET.SubElement(r, "AugmentationDot", count=str(val[1]))
            if val[2]:
                ET.SubElement(r, "PrimaryTuplet", num=str(val[2][0]), den=str(val[2][1]))
        return rid[val]

    mbars_el, bars_el, voices_el = root.find("MasterBars"), root.find("Bars"), root.find("Voices")
    beats_el, notes_el = root.find("Beats"), root.find("Notes")
    def bar_rest(m, compound):
        if compound:
            return {1: ("Quarter", 1, None), 2: ("Half", 1, None), 4: ("Whole", 1, None)}.get(m, ("Half", 1, None))
        return {1: ("Quarter", 0, None), 2: ("Half", 0, None), 3: ("Half", 1, None), 4: ("Whole", 0, None)}.get(m, ("Whole", 0, None))

    # key per played bar (changes hold until the next one); spelling follows the bar's key (bassnet/spelling.py)
    key_of, cur = [], tuple(key)
    for pb in range(len(events)):
        cur = tuple((bar_keys or {}).get(pb, cur))
        key_of.append(cur)
    tables = {}
    beat_id = note_id = 0
    for wb, pb in enumerate(written):
        b = wb                     # written index: ids of Bar / Voice
        ev = events[pb]
        kb = key_of[pb] if pb < len(key_of) else tuple(key)
        spell_table = tables.setdefault(kb, pc_table(kb[0], kb[1]))
        mb = ET.SubElement(mbars_el, "MasterBar")
        k = ET.SubElement(mb, "Key")
        ET.SubElement(k, "AccidentalCount").text = str(kb[0])
        ET.SubElement(k, "Mode").text = kb[1]
        ET.SubElement(k, "TransposeAs").text = "Flats" if kb[0] < 0 else "Sharps"
        m_b = qs.meter_of(pb)
        comp_b = qs.bar_compound[pb]
        ET.SubElement(mb, "Time").text = f"{m_b * 3}/8" if comp_b else f"{m_b}/4"
        # child order as in GP8 files: Key Time Repeat Section Bars
        if wb in rep_mark:
            st, en, cnt = rep_mark[wb]
            ET.SubElement(mb, "Repeat", start="true" if st else "false", end="true" if en else "false",
                          count=str(cnt if en else 0))
        if wb in alt_mark:
            ET.SubElement(mb, "AlternateEndings").text = alt_mark[wb]
        if sections and pb in sections:
            sec = ET.SubElement(mb, "Section")
            ET.SubElement(sec, "Letter").text = sections[pb][0]
            ET.SubElement(sec, "Text").text = sections[pb][1]
        ET.SubElement(mb, "Bars").text = str(b)
        bar = ET.SubElement(bars_el, "Bar", id=str(b))
        ET.SubElement(bar, "Clef").text = "F4"
        ET.SubElement(bar, "Voices").text = f"{b} -1 -1 -1"
        voice = ET.SubElement(voices_el, "Voice", id=str(b))
        ids = []
        for (p, t, val, n, tie_o, tie_d) in ev:
            if val == "BAR_REST":
                if m_b in (1, 2, 3, 4):
                    val = bar_rest(m_b, comp_b)
                else:
                    # odd meters: whole-bar rest as a single beat-length sequence is not representable; use beats
                    val = ("Quarter", 1, None) if comp_b else ("Quarter", 0, None)
            beat = ET.SubElement(beats_el, "Beat", id=str(beat_id))
            ids.append(str(beat_id))
            beat_id += 1
            # child order as in GP8 files: Dynamic Rhythm *StemOrientation FreeText Notes Properties
            ET.SubElement(beat, "Dynamic").text = "MF"
            ET.SubElement(beat, "Rhythm", ref=rhythm_id(val))
            ET.SubElement(beat, "TransposedPitchStemOrientation").text = "Undefined"
            ET.SubElement(beat, "ConcertPitchStemOrientation").text = "Undefined"
            if n is not None:
                if n.flag and not tie_d:
                    ET.SubElement(beat, "FreeText").text = "?"
                ET.SubElement(beat, "Notes").text = str(note_id)
            bprops = ET.SubElement(beat, "Properties")
            for pname in ("PrimaryPickupVolume", "PrimaryPickupTone"):
                ET.SubElement(ET.SubElement(bprops, "Property", name=pname), "Float").text = "0.500000"
            if n is not None and not tie_d:
                if getattr(n, "slap", False):
                    ET.SubElement(ET.SubElement(bprops, "Property", name="Slapped"), "Enable")
                elif getattr(n, "pop", False):
                    ET.SubElement(ET.SubElement(bprops, "Property", name="Popped"), "Enable")
            if n is None:
                continue
            ne = ET.SubElement(notes_el, "Note", id=str(note_id))
            note_id += 1
            if tie_o or tie_d:
                ET.SubElement(ne, "Tie", origin="true" if tie_o else "false", destination="true" if tie_d else "false")
            ET.SubElement(ne, "InstrumentArticulation").text = "0"
            props = ET.SubElement(ne, "Properties")
            step, acc, octv = spell(n.midi, table=spell_table)
            # GP8 writes note properties in alphabetical order of their names
            pending = {}
            for name, pitch_oct in (("ConcertPitch", octv), ("TransposedPitch", octv + 1)):
                pp = ET.Element("Property", name=name)
                pe = ET.SubElement(pp, "Pitch")
                ET.SubElement(pe, "Step").text = step
                ET.SubElement(pe, "Accidental").text = acc
                ET.SubElement(pe, "Octave").text = str(pitch_oct)
                pending[name] = pp
            for name, child, text in (("Fret", "Fret", str(n.fret)), ("Midi", "Number", str(n.midi)),
                                      ("String", "String", str(n.string))):
                pp = ET.Element("Property", name=name)
                ET.SubElement(pp, child).text = text
                pending[name] = pp
            flags = []
            if n.dead:
                flags.append(("Muted", "Enable", None))
            if not tie_d:
                if n.slide:
                    flags.append(("Slide", "Flags", str(n.slide)))
                if n.hopo_origin:
                    flags.append(("HopoOrigin", "Enable", None))
                if n.hopo_dest:
                    flags.append(("HopoDestination", "Enable", None))
            for name, child, text in flags:
                pp = ET.Element("Property", name=name)
                c = ET.SubElement(pp, child)
                if text is not None:
                    c.text = text
                pending[name] = pp
            for name in sorted(pending):
                props.append(pending[name])
        ET.SubElement(voice, "Beats").text = " ".join(ids)

    for tag in ("BackingTrack", "Assets"):
        el = root.find(tag)
        if el is not None:
            root.remove(el)
    if has_audio:
        attach_backing(root, entries, audio_path, t0)
    entries["meta.json"] = json.dumps({"hasAudio": has_audio, "version": "1.0.0", "bassstationDerived": True},
                                      indent=4).encode("utf-8")
    return save_gp(root, entries, out_path)


_BT_CHANNEL = ("0.500000 0.500000 0.500000 0.500000 0.500000 0.500000 0.500000 0.500000 0.500000 0.000000 "
               "0.500000 0.500000 0.700000 0.500000 0.500000 0.500000")


def _backing_element(frame_padding: int) -> ET.Element:
    bt = ET.Element("BackingTrack")
    for k, v in (("IconId", "21"), ("Color", "0 0 0"), ("Name", "Audio Track"), ("ShortName", "a.track"),
                 ("PlaybackState", "Default")):
        ET.SubElement(bt, k).text = v
    ET.SubElement(ET.SubElement(bt, "ChannelStrip"), "Parameters").text = _BT_CHANNEL
    for k, v in (("Enabled", "true"), ("Source", "Local"), ("AssetId", "0"), ("YouTubeVideoUrl", ""),
                 ("Filter", "6"), ("FramesPerPixel", "132"), ("FramePadding", str(int(frame_padding))),
                 ("Semitones", "0"), ("Cents", "0")):
        ET.SubElement(bt, k).text = v
    return bt


def attach_backing(root: ET.Element, entries: Dict, audio_path: str, t0: float) -> None:
    """Embeds audio as the GP8 backing track; score bar 0 sits at t0 seconds into the audio."""
    ext = os.path.splitext(audio_path)[1].lower()
    uid = str(uuid.uuid4())
    asset = f"Content/Assets/{uid}{ext}"
    entries["Content/Assets/"] = b""
    entries[asset] = open(audio_path, "rb").read()
    for tag in ("BackingTrack", "Assets"):
        el = root.find(tag)
        if el is not None:
            root.remove(el)
    root.append(_backing_element(int(round(-t0 * SR_GP))))
    assets = ET.SubElement(root, "Assets")
    a_el = ET.SubElement(assets, "Asset", id="0")
    ET.SubElement(a_el, "OriginalFilePath").text = os.path.abspath(audio_path)
    ET.SubElement(a_el, "OriginalFileSha1").text = uid
    ET.SubElement(a_el, "EmbeddedFilePath").text = asset
    normalize_layout(root)


# top-level order of GP8 files
ROOT_ORDER = ["GPVersion", "GPRevision", "Encoding", "Score", "MasterTrack", "BackingTrack", "Tracks", "MasterBars",
              "Bars", "Voices", "Beats", "Notes", "Rhythms", "Assets", "ScoreViews"]


def normalize_layout(root: ET.Element) -> None:
    """Puts top-level elements in GP8 order and completes a BackingTrack written by older code."""
    bt = root.find("BackingTrack")
    if bt is not None and bt.find("ChannelStrip") is None:
        pad = int(bt.findtext("FramePadding") or 0)
        new = _backing_element(pad)
        for k in ("PlaybackState", "Enabled", "AssetId", "Filter", "Semitones", "Cents"):
            if bt.findtext(k) is not None:
                new.find(k).text = bt.findtext(k)
        root.remove(bt)
        root.append(new)
    for a in root.iter("Asset"):
        sha = a.find("OriginalFileSha1")
        if sha is not None and not (sha.text or "").strip():
            sha.text = os.path.basename(a.findtext("EmbeddedFilePath") or "").rsplit(".", 1)[0]
    kids = list(root)
    rank = {t: i for i, t in enumerate(ROOT_ORDER)}
    kids.sort(key=lambda e: rank.get(e.tag, len(ROOT_ORDER)))
    for e in list(root):
        root.remove(e)
    root.extend(kids)


def save_gp(root: ET.Element, entries: Dict, out_path: str) -> str:
    """Serialises like GP8: CDATA text fields, sorted zip entries, directories stored."""
    _mark_cdata(root)
    xml = _cdata(ET.tostring(root, encoding="unicode"))
    entries["Content/score.gpif"] = ('<?xml version="1.0" encoding="utf-8"?>\n' + xml).encode("utf-8")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for k in sorted(entries):
            z.writestr(k, entries[k], compress_type=zipfile.ZIP_STORED if k.endswith("/") else zipfile.ZIP_DEFLATED)
    return out_path


def load_gp(path: str):
    entries = {}
    with zipfile.ZipFile(path) as z:
        for it in z.infolist():
            entries[it.filename] = z.read(it.filename)
    text = entries["Content/score.gpif"].decode("utf-8", errors="replace")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    if text.lstrip().startswith("<?xml"):
        text = text.split("?>", 1)[1]
    return ET.fromstring(text), entries


def repair_gp(path: str) -> bool:
    """Rewrites a BaSSDream-generated (derived) GP into GP8 layout. Never touches other files."""
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from gp_guard import is_derived
    if not is_derived(path):
        return False
    root, entries = load_gp(path)
    normalize_layout(root)
    save_gp(root, entries, path)
    return True
