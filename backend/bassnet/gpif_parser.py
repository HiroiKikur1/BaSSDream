"""GPIF (Guitar Pro 6/7/8) bass-track parser with absolute audio-time mapping.

Produces the ground-truth note list used for training and evaluation:
every note gets its score position (quarter notes), its audio time (seconds,
relative to the start of the embedded backing-track asset) and its MIDI pitch.
"""
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

SAMPLE_RATE = 44100
PAD_SIGN = -1

NOTE_VALUES = {
    "DoubleWhole": Fraction(8), "Whole": Fraction(4), "Half": Fraction(2), "Quarter": Fraction(1),
    "Eighth": Fraction(1, 2), "16th": Fraction(1, 4), "32nd": Fraction(1, 8),
    "64th": Fraction(1, 16), "128th": Fraction(1, 32),
}


@dataclass
class GtNote:
    bar: int                 # master bar index
    occurrence: int          # nth time this bar is played
    qpos: Fraction           # absolute score position in quarter notes (playback order)
    qdur: Fraction           # duration in quarter notes (ties merged)
    midi: int
    string: int
    fret: int
    time: float = 0.0        # seconds in the backing-track audio
    end: float = 0.0
    dead: bool = False
    grace: bool = False
    tie_origin: bool = False
    # techniques (GP8 flags): Slide bitmask 1 shift / 2 legato / 4 out-down / 8 out-up / 16 in-below / 32 in-above
    slide: int = 0
    hopo_origin: bool = False
    hopo_dest: bool = False
    slap: bool = False
    pop: bool = False


@dataclass
class ScoreInfo:
    notes: List[GtNote]
    tuning: List[int]
    bars: List[Tuple[int, int, Fraction, Fraction]]   # (bar, occurrence, qstart, qlen) in playback order
    time_sigs: List[Tuple[int, int]]
    tempo0: float
    key: Tuple[int, str]
    audio_asset: Optional[str]
    has_sync: bool
    frame_padding: int
    # (qpos, seconds) anchors of the score->audio map
    anchors: List[Tuple[float, float]] = field(default_factory=list)
    score_anchors: List[Tuple[float, float]] = field(default_factory=list)


def _rhythm_len(r: ET.Element) -> Fraction:
    base = NOTE_VALUES.get(r.findtext("NoteValue") or "Quarter", Fraction(1))
    dot = r.find("AugmentationDot")
    if dot is not None:
        n = int(dot.get("count", "1"))
        base = base * (2 - Fraction(1, 2 ** n))
    for tag in ("PrimaryTuplet", "SecondaryTuplet"):
        t = r.find(tag)
        if t is not None:
            num, den = int(t.get("num", "1")), int(t.get("den", "1"))
            if num > 0 and den > 0:
                base = base * Fraction(den, num)
    return base


def _is_bass_tuning(p: List[int]) -> bool:
    return 3 <= len(p) <= 6 and min(p) <= 33 and max(p) <= 50 and min(p) > 0


def _pick_bass_track(root: ET.Element) -> Tuple[int, List[int]]:
    tracks = root.findall("Tracks/Track")
    best, best_score = 0, -1e9
    best_tuning = [28, 33, 38, 43]
    for i, t in enumerate(tracks):
        name = (t.findtext("Name") or "").lower()
        ttype = (t.findtext("InstrumentSet/Type") or "").lower()
        pitches_txt = t.findtext(".//Property[@name='Tuning']/Pitches") or ""
        pitches = [int(v) for v in pitches_txt.split() if v.lstrip("-").isdigit()]
        score = 0.0
        if "bass" in ttype:
            score += 3
        if "bass" in name or "贝斯" in name or "bs" == name.strip("."):
            score += 2
        if pitches and _is_bass_tuning(pitches):
            score += 4
        if "drum" in ttype or "drum" in name:
            score -= 10
        if score > best_score:
            best, best_score = i, score
            if pitches and min(pitches) > 0:
                best_tuning = pitches
    return best, best_tuning


def _playback_order(mbars: List[ET.Element]) -> List[Tuple[int, int]]:
    """Expands repeats / alternate endings (jumps are rare in this library and ignored)."""
    order: List[Tuple[int, int]] = []
    occ: Dict[int, int] = {}
    n = len(mbars)
    i = 0
    repeat_start = 0
    pass_no: Dict[int, int] = {}   # repeat-end index -> passes done
    cur_pass = 1                    # pass through the current repeat block (alternate endings pick by it)
    block_end = None                # index of the repeat-end bar of the block being played
    guard = 0
    while i < n and guard < 20000:
        guard += 1
        mb = mbars[i]
        rep = mb.find("Repeat")
        alt = mb.findtext("AlternateEndings")
        if not alt and block_end is not None and i > block_end:
            # past the block and its trailing endings (|: A |1. B :|2. C | D): back to normal playback
            cur_pass, block_end = 1, None
        if rep is not None and rep.get("start") == "true":
            if i != repeat_start:
                repeat_start = i
        if alt:
            allowed = {int(a) for a in alt.split() if a.isdigit()}
            if allowed and cur_pass not in allowed:
                i += 1
                continue
        o = occ.get(i, 0)
        occ[i] = o + 1
        order.append((i, o))
        if rep is not None and rep.get("end") == "true":
            count = int(rep.get("count", "2"))
            done = pass_no.get(i, 0) + 1
            if done < count:
                pass_no[i] = done
                cur_pass, block_end = done + 1, i
                i = repeat_start
                continue
            pass_no.pop(i, None)
            block_end = i
            repeat_start = i + 1
        i += 1
    return order


def parse_gp(path: str) -> ScoreInfo:
    with zipfile.ZipFile(path) as z:
        xml = z.read("Content/score.gpif")
        assets = [n for n in z.namelist() if n.startswith("Content/Assets/") and not n.endswith("/")]
    text = xml.decode("utf-8", errors="replace")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    root = ET.fromstring(text)

    t_idx, tuning = _pick_bass_track(root)
    track = root.findall("Tracks/Track")[t_idx]
    capo_txt = track.findtext(".//Property[@name='CapoFret']/Fret")
    capo = int(capo_txt) if capo_txt and capo_txt.lstrip("-").isdigit() else 0

    rhythms = {r.get("id"): r for r in root.findall("Rhythms/Rhythm")}
    rlen = {k: _rhythm_len(v) for k, v in rhythms.items()}
    notes_el = {n.get("id"): n for n in root.findall("Notes/Note")}
    beats_el = {b.get("id"): b for b in root.findall("Beats/Beat")}
    voices_el = {v.get("id"): (v.findtext("Beats") or "").split() for v in root.findall("Voices/Voice")}
    bars_el = {b.get("id"): b for b in root.findall("Bars/Bar")}
    mbars = root.findall("MasterBars/MasterBar")

    key_el = mbars[0].find("Key") if mbars else None
    key = (int(key_el.findtext("AccidentalCount") or 0), key_el.findtext("Mode") or "Major") if key_el is not None else (0, "Major")

    # --- tempo automations (score tempo, used when there are no sync points) ---
    tempos: List[Tuple[int, float, float]] = []   # (bar, position fraction, bpm in quarters)
    syncs: List[Tuple[int, int, float, float, int]] = []  # (bar, occ, modified, original, frameoffset)
    for a in root.findall("MasterTrack/Automations/Automation"):
        typ = a.findtext("Type")
        bar = int(a.findtext("Bar") or 0)
        pos = float(a.findtext("Position") or 0)
        if typ == "Tempo":
            parts = (a.findtext("Value") or "120 2").split()
            bpm = float(parts[0])
            unit = int(parts[1]) if len(parts) > 1 else 2
            # unit: 1=eighth 2=quarter 3=dotted quarter 4=half 5=dotted half
            mult = {1: 0.5, 2: 1.0, 3: 1.5, 4: 2.0, 5: 3.0}.get(unit, 1.0)
            tempos.append((bar, pos, bpm * mult))
        elif typ == "SyncPoint":
            v = a.find("Value")
            if v is not None:
                syncs.append((
                    int(v.findtext("BarIndex") or bar), int(v.findtext("BarOccurrence") or 0),
                    float(v.findtext("ModifiedTempo") or 120), float(v.findtext("OriginalTempo") or 120),
                    int(v.findtext("FrameOffset") or 0),
                ))
    tempos.sort()
    tempo0 = tempos[0][2] if tempos else 120.0

    bt = root.find("BackingTrack")
    frame_padding = int(bt.findtext("FramePadding") or 0) if bt is not None else 0
    asset_path = None
    if bt is not None and assets:
        aid = bt.findtext("AssetId")
        a_el = root.find(f"Assets/Asset[@id='{aid}']")
        emb = a_el.findtext("EmbeddedFilePath") if a_el is not None else None
        if emb and emb in assets:
            asset_path = emb
        else:
            asset_path = assets[0]

    # --- playback order and bar geometry ---
    order = _playback_order(mbars)
    time_sigs = []
    bar_len = []
    for mb in mbars:
        ts = (mb.findtext("Time") or "4/4").split("/")
        num, den = int(ts[0]), int(ts[1])
        time_sigs.append((num, den))
        bar_len.append(Fraction(num * 4, den))

    bars_out = []
    q = Fraction(0)
    bar_q: Dict[Tuple[int, int], Fraction] = {}
    for (bi, oc) in order:
        bar_q[(bi, oc)] = q
        bars_out.append((bi, oc, q, bar_len[bi]))
        q += bar_len[bi]
    total_q = q

    # --- notes ---
    notes: List[GtNote] = []
    open_ties: Dict[int, GtNote] = {}   # string -> note awaiting tie continuation
    for (bi, oc) in order:
        mb = mbars[bi]
        bar_ids = (mb.findtext("Bars") or "").split()
        if t_idx >= len(bar_ids):
            continue
        bar = bars_el.get(bar_ids[t_idx])
        if bar is None:
            continue
        q0 = bar_q[(bi, oc)]
        vids = [v for v in (bar.findtext("Voices") or "").split() if v != "-1"]
        for vnum, vid in enumerate(vids):
            pos = Fraction(0)
            for bid in voices_el.get(vid, []):
                b = beats_el.get(bid)
                if b is None:
                    continue
                rref = b.find("Rhythm").get("ref") if b.find("Rhythm") is not None else None
                blen = rlen.get(rref, Fraction(1))
                grace = b.findtext("GraceNotes")
                note_ids = (b.findtext("Notes") or "").split()
                if grace:
                    gq = q0 + pos - (Fraction(1, 8) if grace == "BeforeBeat" else 0)
                    for nid in note_ids:
                        n = notes_el.get(nid)
                        if n is None:
                            continue
                        midi, s, f = _note_pitch(n, tuning, capo)
                        if midi is None:
                            continue
                        notes.append(GtNote(bi, oc, gq, Fraction(1, 8), midi, s, f, grace=True))
                    continue   # grace beats do not consume time
                for nid in note_ids:
                    n = notes_el.get(nid)
                    if n is None:
                        continue
                    midi, s, f = _note_pitch(n, tuning, capo)
                    if midi is None:
                        continue
                    tie = n.find("Tie")
                    tie_dest = tie is not None and tie.get("destination") == "true"
                    tie_orig = tie is not None and tie.get("origin") == "true"
                    dead = n.find("Properties/Property[@name='Muted']") is not None
                    if tie_dest and s in open_ties and open_ties[s].midi == midi:
                        # techniques on a tied continuation belong to the sounding note
                        sl = n.findtext("Properties/Property[@name='Slide']/Flags")
                        if sl:
                            open_ties[s].slide |= int(sl)
                        prev = open_ties[s]
                        prev.qdur = (q0 + pos + blen) - prev.qpos
                        if not tie_orig:
                            open_ties.pop(s, None)
                        continue
                    gn = GtNote(bi, oc, q0 + pos, blen, midi, s, f, dead=dead, tie_origin=tie_orig)
                    sl = n.findtext("Properties/Property[@name='Slide']/Flags")
                    gn.slide = int(sl) if sl else 0
                    gn.hopo_origin = n.find("Properties/Property[@name='HopoOrigin']") is not None
                    gn.hopo_dest = n.find("Properties/Property[@name='HopoDestination']") is not None
                    gn.slap = b.find("Properties/Property[@name='Slapped']") is not None
                    gn.pop = b.find("Properties/Property[@name='Popped']") is not None
                    notes.append(gn)
                    if tie_orig:
                        open_ties[s] = gn
                    else:
                        open_ties.pop(s, None)
                pos += blen
    notes.sort(key=lambda n: (n.qpos, n.midi))

    info = ScoreInfo(
        notes=notes, tuning=tuning, bars=bars_out, time_sigs=time_sigs, tempo0=tempo0,
        key=key, audio_asset=asset_path, has_sync=bool(syncs), frame_padding=frame_padding,
    )
    info.anchors = _build_time_map(info, order, bar_q, bar_len, tempos, syncs, frame_padding, total_q)
    for n in info.notes:
        n.time = q_to_sec(info.anchors, float(n.qpos))
        n.end = q_to_sec(info.anchors, float(n.qpos + n.qdur))
    return info


def _note_pitch(n: ET.Element, tuning: List[int], capo: int):
    s_txt = n.findtext("Properties/Property[@name='String']/String")
    f_txt = n.findtext("Properties/Property[@name='Fret']/Fret")
    m_txt = n.findtext("Properties/Property[@name='Midi']/Number")
    if n.find("Properties/Property[@name='HarmonicType']") is not None:
        return None, 0, 0
    s = int(s_txt) if s_txt is not None else 0
    f = int(f_txt) if f_txt is not None else 0
    if s_txt is not None and f_txt is not None and 0 <= s < len(tuning):
        midi = tuning[s] + f + capo
    elif m_txt is not None:
        midi = int(m_txt)
    else:
        return None, s, f
    return midi, s, f


def _build_time_map(info, order, bar_q, bar_len, tempos, syncs, frame_padding, total_q):
    """Returns monotonic (qpos, seconds) anchors. Between anchors time is linear in quarters."""
    # Score-tempo timeline anchored at every bar start and every tempo change.
    tempo_at = {}
    for bar, pos, bpm in tempos:
        tempo_at.setdefault(bar, []).append((pos, bpm))
    anchors_score: List[Tuple[float, float]] = []
    cur_bpm = tempos[0][2] if tempos else 120.0
    t = 0.0
    last_q = 0.0
    for (bi, oc) in order:
        qs = float(bar_q[(bi, oc)])
        ql = float(bar_len[bi])
        changes = sorted(tempo_at.get(bi, []))
        cur = qs
        t += (cur - last_q) * 60.0 / cur_bpm
        last_q = cur
        anchors_score.append((cur, t))
        for pos, bpm in changes:
            qc = qs + pos * ql
            t += (qc - last_q) * 60.0 / cur_bpm
            last_q = qc
            cur_bpm = bpm
            anchors_score.append((qc, t))
    t += (float(total_q) - last_q) * 60.0 / cur_bpm
    anchors_score.append((float(total_q), t))
    anchors_score = _dedupe(anchors_score)
    info.score_anchors = anchors_score

    # FramePadding > 0 delays the score relative to the audio file start.
    pad = PAD_SIGN * frame_padding / SAMPLE_RATE
    sync_anchors = []
    for bi, oc, mod, orig, fo in syncs:
        if (bi, oc) in bar_q:
            sync_anchors.append((float(bar_q[(bi, oc)]), fo / SAMPLE_RATE, mod))
    sync_anchors.sort()
    if not sync_anchors:
        return [(q, s + pad) for q, s in anchors_score]

    # Between sync points the audio runs at the segment's ModifiedTempo (linear in quarters).
    out: List[Tuple[float, float]] = []
    q0, s0, mod0 = sync_anchors[0]
    if q0 > 0:
        out.append((0.0, s0 - q0 * 60.0 / mod0))
    for k, (q_s, sec_s, mod) in enumerate(sync_anchors):
        out.append((q_s, sec_s))
        if k + 1 == len(sync_anchors):
            # After the last sync point follow the score tempo shape, rescaled to the sync tempo.
            score_bpm = 60.0 / max(1e-6, _interp(anchors_score, q_s + 1.0) - _interp(anchors_score, q_s))
            ratio = score_bpm / mod if mod > 0 else 1.0
            base = _interp(anchors_score, q_s)
            out += [(q, sec_s + (s - base) * ratio) for q, s in anchors_score if q > q_s]
    return [(q, s + pad) for q, s in _dedupe(sorted(out))]


def _dedupe(pts):
    res = []
    for q, s in pts:
        if res and abs(res[-1][0] - q) < 1e-9:
            res[-1] = (q, s)
        else:
            res.append((q, s))
    return res


def _interp(anchors, q):
    import bisect
    qs = [a[0] for a in anchors]
    i = bisect.bisect_right(qs, q) - 1
    if i < 0:
        (q1, s1), (q2, s2) = anchors[0], anchors[1] if len(anchors) > 1 else (anchors[0][0] + 1, anchors[0][1] + 0.5)
    elif i >= len(anchors) - 1:
        (q1, s1), (q2, s2) = anchors[-2] if len(anchors) > 1 else (anchors[-1][0] - 1, anchors[-1][1] - 0.5), anchors[-1]
    else:
        (q1, s1), (q2, s2) = anchors[i], anchors[i + 1]
    if q2 == q1:
        return s1
    return s1 + (q - q1) * (s2 - s1) / (q2 - q1)


def q_to_sec(anchors, q: float) -> float:
    return _interp(anchors, q)


def sec_to_q(anchors, sec: float) -> float:
    inv = [(s, q) for q, s in anchors]
    return _interp(inv, sec)


def extract_audio(path: str, info: ScoreInfo, out_path: str) -> bool:
    if not info.audio_asset:
        return False
    with zipfile.ZipFile(path) as z:
        with open(out_path, "wb") as f:
            f.write(z.read(info.audio_asset))
    return True
