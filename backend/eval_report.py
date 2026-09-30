"""Per-note evaluation report: the bass track laid out bar by bar (playback order, repeats unrolled)
with each note's judgment, plus bar and section grades. Rendered by the WPF report view."""
import re
import zipfile
import xml.etree.ElementTree as ET
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

from bassnet.gpif_parser import _note_pitch, _pick_bass_track, _playback_order, _rhythm_len

JUDGE_CODE = {"PERFECT": "P", "GREAT": "G", "GOOD": "D", "BAD": "B", "MISS": "M"}
WEIGHT = {"PERFECT": 1.0, "GREAT": 0.8, "GOOD": 0.5, "BAD": 0.2, "MISS": 0.0}
NOTE_VALUE = {"DoubleWhole": 0, "Whole": 1, "Half": 2, "Quarter": 4, "Eighth": 8, "16th": 16,
              "32nd": 32, "64th": 64, "128th": 128}
AUTO_SECTION_BARS = 8
TENDENCY_MS = 40.0


def section_grade(acc: float) -> str:
    s = 100.0 * acc
    for g, lim in (("SS", 95), ("S", 90), ("A", 80), ("B", 70)):
        if s >= lim:
            return g
    return "C"


def bar_grade(acc: float) -> str:
    if acc >= 0.95:
        return "PERFECT"
    if acc >= 0.8:
        return "GREAT"
    if acc >= 0.5:
        return "GOOD"
    return "BAD"


def _q(x: Fraction) -> float:
    return round(float(x), 6)


def _load_root(gp_path: str) -> ET.Element:
    with zipfile.ZipFile(gp_path) as z:
        text = z.read("Content/score.gpif").decode("utf-8", errors="replace")
    return ET.fromstring(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text))


def _section_name(mb: ET.Element) -> Optional[str]:
    sec = mb.find("Section")
    if sec is None:
        return None
    letter = (sec.findtext("Letter") or "").strip()
    label = (sec.findtext("Text") or "").strip()
    return " ".join(p for p in (letter, label) if p) or None


def section_spans(names: List[Optional[str]], bar_numbers: List[int]) -> List[Tuple[str, int, int]]:
    """(name, start, end) over playback bars: GP section markers, else fixed-size groups.

    Consecutive markers with the same name (a phrase-by-phrase marked section, or |: Intro :| x4) form one section.
    """
    starts = []
    for i, n in enumerate(names):
        if n and not (starts and names[starts[-1]] == n):
            starts.append(i)
    if not starts:
        starts = list(range(0, len(names), AUTO_SECTION_BARS))
        labels = [f"{bar_numbers[i]}–{bar_numbers[min(i + AUTO_SECTION_BARS, len(names)) - 1]} 小节" for i in starts]
    else:
        if starts[0] != 0:
            starts.insert(0, 0)
        labels = [names[i] or "Intro" for i in starts]
    return [(labels[k], a, starts[k + 1] if k + 1 < len(starts) else len(names)) for k, a in enumerate(starts)]


def song_sections(gp_path: str) -> List[Tuple[str, int, int]]:
    """Sections of a score in playback order (bar indices into the unrolled bars), as in the report."""
    mbars = _load_root(gp_path).findall("MasterBars/MasterBar")
    order = _playback_order(mbars)
    return section_spans([_section_name(mbars[bi]) for bi, _ in order], [bi + 1 for bi, _ in order])


def build_report(gp_path: str, judged: Dict[Tuple[int, int, float, int], Tuple[str, Optional[float], bool]]) -> Dict[str, Any]:
    """judged: (bar, occurrence, qpos, string) -> (judgment, dt seconds | None, pitch ok)."""
    root = _load_root(gp_path)

    t_idx, tuning = _pick_bass_track(root)
    track = root.findall("Tracks/Track")[t_idx]
    capo_txt = track.findtext(".//Property[@name='CapoFret']/Fret")
    capo = int(capo_txt) if capo_txt and capo_txt.lstrip("-").isdigit() else 0
    rhythms = {r.get("id"): r for r in root.findall("Rhythms/Rhythm")}
    notes_el = {n.get("id"): n for n in root.findall("Notes/Note")}
    beats_el = {b.get("id"): b for b in root.findall("Beats/Beat")}
    voices_el = {v.get("id"): (v.findtext("Beats") or "").split() for v in root.findall("Voices/Voice")}
    bars_el = {b.get("id"): b for b in root.findall("Bars/Bar")}
    mbars = root.findall("MasterBars/MasterBar")

    order = _playback_order(mbars)
    sigs, blen = [], []
    for mb in mbars:
        num, den = (int(v) for v in (mb.findtext("Time") or "4/4").split("/"))
        sigs.append((num, den))
        blen.append(Fraction(num * 4, den))

    bars_out: List[Dict[str, Any]] = []
    from performance_evaluator import score_time_map
    from bassnet.gpif_parser import parse_gp
    qmap = score_time_map(gp_path, parse_gp(gp_path))
    q = Fraction(0)
    for bi, oc in order:
        mb = mbars[bi]
        sec_name = _section_name(mb)
        beats_out: Dict[Fraction, Dict[str, Any]] = {}
        bar_ids = (mb.findtext("Bars") or "").split()
        bar = bars_el.get(bar_ids[t_idx]) if t_idx < len(bar_ids) else None
        vids = [v for v in (bar.findtext("Voices") or "").split() if v != "-1"] if bar is not None else []
        for vnum, vid in enumerate(vids):
            pos = Fraction(0)
            for bid in voices_el.get(vid, []):
                b = beats_el.get(bid)
                if b is None or b.findtext("GraceNotes"):
                    continue
                r_el = b.find("Rhythm")
                r = rhythms.get(r_el.get("ref")) if r_el is not None else None
                length = _rhythm_len(r) if r is not None else Fraction(1)
                note_ids = (b.findtext("Notes") or "").split()
                cur = beats_out.get(pos)
                if cur is None and vnum == 0:
                    tup = r.find("PrimaryTuplet") if r is not None else None
                    dot = r.find("AugmentationDot") if r is not None else None
                    cur = {"p": _q(pos), "l": _q(length),
                           "v": NOTE_VALUE.get(r.findtext("NoteValue") if r is not None else "Quarter", 4),
                           "d": int(dot.get("count", "1")) if dot is not None else 0,
                           "t": int(tup.get("num", "0")) if tup is not None else 0,
                           "n": []}
                    beats_out[pos] = cur
                if cur is not None:
                    for nid in note_ids:
                        n = notes_el.get(nid)
                        if n is None:
                            continue
                        midi, s, f = _note_pitch(n, tuning, capo)
                        if midi is None:
                            continue
                        tie = n.find("Tie")
                        cont = tie is not None and tie.get("destination") == "true"
                        dead = n.find("Properties/Property[@name='Muted']") is not None
                        entry = {"s": s, "f": f}
                        if cont:
                            entry["tie"] = 1
                        if dead:
                            entry["x"] = 1
                        hit = None if cont else judged.get((bi, oc, _q(q + pos), s))
                        if hit is not None:
                            j, dt, ok = hit
                            entry["j"] = JUDGE_CODE[j]
                            if dt is not None:
                                entry["dt"] = round(dt * 1000.0)
                            if j == "BAD" and not ok:
                                entry["w"] = 1
                        cur["n"].append(entry)
                pos += length
        beats = [beats_out[k] for k in sorted(beats_out)]
        bars_out.append({"bar": bi + 1, "occ": oc, "num": sigs[bi][0], "den": sigs[bi][1],
                         "sec": sec_name, "beats": beats,
                         # backing-audio seconds of the bar (bar replay: take time = t / rate + offset)
                         "t0": round(float(qmap(float(q))), 4), "t1": round(float(qmap(float(q + blen[bi]))), 4)})
        q += blen[bi]

    # bar grades from the judged notes
    for b in bars_out:
        js = [n for bt in b["beats"] for n in bt["n"] if "j" in n]
        if not js:
            continue
        inv = {v: k for k, v in JUDGE_CODE.items()}
        acc = sum(WEIGHT[inv[n["j"]]] for n in js) / len(js)
        b["acc"] = round(acc, 3)
        b["grade"] = bar_grade(acc)
        dts = [n["dt"] for n in js if "dt" in n and not n.get("w")]
        if len(dts) >= 2:
            mean = sum(dts) / len(dts)
            if abs(mean) > TENDENCY_MS:
                b["tend"] = round(mean)
        b["wrong"] = sum(1 for n in js if n.get("w"))
        b["miss"] = sum(1 for n in js if n["j"] == "M")

    sections = []
    for k, (name, a, e) in enumerate(section_spans([b["sec"] for b in bars_out], [b["bar"] for b in bars_out])):
        inv = {v: kk for kk, v in JUDGE_CODE.items()}
        js = [n for b in bars_out[a:e] for bt in b["beats"] for n in bt["n"] if "j" in n]
        sec = {"name": name, "start": a, "end": e}
        if js:
            acc = sum(WEIGHT[inv[n["j"]]] for n in js) / len(js)
            sec["acc"] = round(acc, 3)
            sec["grade"] = section_grade(acc)
            sec["notes"] = len(js)
        sections.append(sec)
        for b in bars_out[a:e]:
            b["si"] = k

    return {"tuning": tuning, "sections": sections, "bars": bars_out}
