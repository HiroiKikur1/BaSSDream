"""The song document: everything Layer 1 knows about a recording before the bass is written (architecture v2).

Layer 1 fills it (beats / bar grid / tempo segments / meter, key and per-section keys, chords, sections with their
role and energy, repeated-phrase families); Layer 2 decodes the bass inside it; Layer 3 engraves from it. Every
field records its source so A/B runs can tell which model produced what. Stored as JSON next to the output GP
(`<name>.song.json`) and in cache/bassnet/song_docs/<md5>.json for evaluation.

Times are seconds in the original recording; bars are played-bar indices (repeats unrolled).
"""
import json
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

VERSION = 1


@dataclass
class TempoSegment:
    bar: int                 # first bar of the segment
    bpm: int                 # written (integer) tempo


@dataclass
class Section:
    bar: int                 # first bar
    n_bars: int
    cls: str                 # intro / A / B / C / D / inter / outro (section_model.CLASSES)
    label: str               # rehearsal mark text as written ("A 1", "Intro", ...)
    energy: float = 0.0      # mean loudness of the full mix, 0..1 within the song
    key: Optional[int] = None  # signature if it differs from the global key


@dataclass
class Chord:
    start: float
    end: float
    label: str               # Harte syntax from the chord model, e.g. "G:min", "Bb:maj/3", "N"
    root: Optional[int] = None   # pitch class, None for "N"
    bass: Optional[int] = None   # pitch class of the chord's bass (inversions)


@dataclass
class SongDoc:
    md5: str = ""
    duration: float = 0.0
    beats: List[float] = field(default_factory=list)
    bar_times: List[float] = field(default_factory=list)          # start of every played bar (+ end of the last)
    bar_beats: List[int] = field(default_factory=list)            # beats per bar
    compound: List[bool] = field(default_factory=list)            # 6/8-style bars
    first_bar: int = 0                                            # bar of the song's real start (after count-in)
    tempo: List[TempoSegment] = field(default_factory=list)
    key: int = 0                                                  # accidental count, -7..7
    mode: str = "Major"
    sections: List[Section] = field(default_factory=list)
    chords: List[Chord] = field(default_factory=list)
    bar_chords: List[str] = field(default_factory=list)           # dominant chord label per bar
    families: List[int] = field(default_factory=list)             # repeated-phrase family id per bar (-1 = none)
    source: Dict[str, str] = field(default_factory=dict)          # field -> model / method that produced it
    version: int = VERSION

    def to_json(self, path: str):
        json.dump(asdict(self), open(path, "w", encoding="utf8"), ensure_ascii=False, indent=1)

    @staticmethod
    def from_json(path: str) -> "SongDoc":
        d = json.load(open(path, encoding="utf8"))
        d["tempo"] = [TempoSegment(**x) for x in d.get("tempo", [])]
        d["sections"] = [Section(**x) for x in d.get("sections", [])]
        d["chords"] = [Chord(**x) for x in d.get("chords", [])]
        return SongDoc(**d)

    def validate(self) -> List[str]:
        p = []
        n = len(self.bar_times) - 1
        if n <= 0:
            p.append("no bars")
        if any(b >= a for a, b in zip(self.bar_times[1:], self.bar_times[:-1])):
            p.append("bar times not increasing")
        if len(self.bar_beats) != n:
            p.append("bar_beats length")
        if not self.tempo or self.tempo[0].bar != 0:
            p.append("tempo must start at bar 0")
        if not -7 <= self.key <= 7:
            p.append("key out of range")
        for s in self.sections:
            if not 0 <= s.bar < max(n, 1):
                p.append(f"section outside bars: {s}")
        return p


PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
DEGREE = {"1": 0, "b2": 1, "2": 2, "b3": 3, "3": 4, "4": 5, "#4": 6, "b5": 6, "5": 7, "#5": 8, "b6": 8, "6": 9,
          "bb7": 9, "b7": 10, "7": 11}


def parse_chord(label: str) -> Chord:
    """'Bb:maj/3' -> root 10, bass 2 (Harte syntax as produced by lv-chordia)."""
    if not label or label in ("N", "X"):
        return Chord(0.0, 0.0, label or "N")
    root_s = label.split(":")[0].split("/")[0]
    root = (PC[root_s[0]] + root_s[1:].count("#") - root_s[1:].count("b")) % 12
    bass = root
    if "/" in label:
        b = label.split("/")[1]
        bass = (root + DEGREE.get(b, 0)) % 12
    return Chord(0.0, 0.0, label, root, bass)


QUALITY = {"maj": (0, 4, 7), "min": (0, 3, 7), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "min7": (0, 3, 7, 10),
           "dim": (0, 3, 6), "dim7": (0, 3, 6, 9), "hdim7": (0, 3, 6, 10), "aug": (0, 4, 8), "sus4": (0, 5, 7),
           "sus2": (0, 2, 7), "9": (0, 4, 7, 10, 2), "maj9": (0, 4, 7, 11, 2), "min9": (0, 3, 7, 10, 2),
           "maj6": (0, 4, 7, 9), "min6": (0, 3, 7, 9), "minmaj7": (0, 3, 7, 11), "sus4(b7)": (0, 5, 7, 10), "5": (0, 7),
           "1": (0,)}


def chord_pcs(label: str):
    """Pitch classes of a Harte chord label (root first); () for N."""
    c = parse_chord(label)
    if c.root is None:
        return ()
    q = label.split(":")[1].split("/")[0] if ":" in label else "maj"
    return tuple((c.root + i) % 12 for i in QUALITY.get(q, QUALITY["min" if q.startswith("min") else "maj"]))


def from_pipeline(qs, sections: Dict[int, tuple], key=(0, "Major"), tempo_segments=None, chords=None,
                  md5: str = "", duration: float = 0.0, source: Optional[Dict[str, str]] = None) -> SongDoc:
    """A song document from what the current (v1) pipeline decides; Layer 1 models will replace fields one by one."""
    from bassnet.pipeline import SECTION_CKPT  # noqa: F401  (documents where the section marks come from)
    bars = [float(qs.bar_time(b)) for b in range(qs.n_bars + 1)]
    doc = SongDoc(md5=md5, duration=duration, beats=[float(x) for x in qs.beats], bar_times=bars,
                  bar_beats=[int(qs.meter_of(b)) for b in range(qs.n_bars)],
                  compound=[bool(qs.bar_compound[b]) for b in range(qs.n_bars)], key=int(key[0]), mode=key[1])
    if tempo_segments is None:
        from bassnet.tempo_map import tempo_segments as ts
        tempo_segments = ts([sum(doc.bar_beats[:b]) for b in range(qs.n_bars + 1)], bars)
    doc.tempo = [TempoSegment(int(b), int(t)) for b, t in tempo_segments]
    starts = sorted(sections)
    for i, b in enumerate(starts):
        letter, text = sections[b]
        end = starts[i + 1] if i + 1 < len(starts) else qs.n_bars
        from bassnet.section_model import section_class
        doc.sections.append(Section(int(b), int(end - b), section_class(letter, text), f"{letter} {text}".strip()))
    for c in chords or []:
        ch = parse_chord(c["chord"])
        ch.start, ch.end = float(c["start_time"]), float(c["end_time"])
        doc.chords.append(ch)
    if doc.chords:
        doc.bar_chords = [bar_chord(doc.chords, bars[b], bars[b + 1]) for b in range(qs.n_bars)]
    doc.source = dict(source or {"beats": "bassnet-hmm", "sections": "section_model", "key": "chroma-profile",
                                 "chords": "lv-chordia" if chords else "", "tempo": "tempo_map"})
    return doc


def bar_chord(chords: List[Chord], t0: float, t1: float) -> str:
    best, lab = 0.0, "N"
    for c in chords:
        ov = min(t1, c.end) - max(t0, c.start)
        if ov > best:
            best, lab = ov, c.label
    return lab
