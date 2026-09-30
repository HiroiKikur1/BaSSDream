"""DadaGP (Sarmento et al. 2021, 26k Guitar Pro scores, research access) -> band-level symbolic records for Layer 2/3.

What we take from a score (never used as labels for real recordings):
  bass track notes (pitch, string, fret, onset / duration in quarters, dead, tie), the harmony of the same song
  (pitch classes sounding on the guitar / keys tracks, per beat, and a chord label per half bar by template
  matching), drum kick / snare onsets, time signature / key signature / section markers / tempo per bar.
Quality gate (docs/扒谱架构v2.md 9.1): exactly one bass track with >= 50 notes, bass range B0..G4, >= 16 bars,
a time signature and a tempo.

python -m bassnet.dadagp parse <dadagp root> [--limit N]   -> D:\\BassData\\dadagp_parsed\\<id>.json
python -m bassnet.dadagp stats                             -> style statistics of the parsed set
"""
import argparse
import glob
import hashlib
import json
import os
import sys
from collections import Counter

import numpy as np

OUT = os.path.join("D:" + os.sep, "BassData", "dadagp_parsed")
TPQ = 960                     # Guitar Pro ticks per quarter
KICK, SNARE = {35, 36}, {37, 38, 40}
LOW, HIGH = 23, 67            # B0 .. G4


def _group(track):
    """bass / drums / harmony (guitars, keys, pads) / other, from the MIDI program and flags."""
    if track.isPercussionTrack:
        return "drums"
    prog = track.channel.instrument if track.channel is not None else 0
    tuning = [s.value for s in track.strings]
    if 32 <= prog <= 39 or (tuning and max(tuning) <= 55 and len(tuning) in (4, 5, 6) and min(tuning) <= 28):
        return "bass"
    if prog <= 31 or 80 <= prog <= 95 or 16 <= prog <= 23:
        return "harmony"
    return "other"


CHORD_T = {"maj": (0, 4, 7), "min": (0, 3, 7), "5": (0, 7), "7": (0, 4, 7, 10), "min7": (0, 3, 7, 10),
           "maj7": (0, 4, 7, 11), "dim": (0, 3, 6), "sus4": (0, 5, 7), "sus2": (0, 2, 7), "aug": (0, 4, 8)}
NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def chord_label(pc_weight, bass_pc=None):
    """Template match of a 12-d pitch-class weight vector -> 'G:min' or 'N'."""
    if pc_weight.sum() <= 0:
        return "N"
    v = pc_weight / pc_weight.sum()
    best, lab = -1e9, "N"
    for r in range(12):
        for q, iv in CHORD_T.items():
            tpl = np.zeros(12)
            tpl[[(r + i) % 12 for i in iv]] = 1.0
            s = float(v @ tpl) - 0.5 * float(v @ (1 - tpl)) - 0.02 * len(iv) + (0.1 if bass_pc == r else 0.0)
            if s > best:
                best, lab = s, f"{NAMES[r]}:{q}"
    return lab


def parse_song(path):
    import guitarpro
    song = guitarpro.parse(path)
    groups = {"bass": [], "drums": [], "harmony": [], "other": []}
    for t in song.tracks:
        groups[_group(t)].append(t)
    if len(groups["bass"]) != 1:
        return None, "bass tracks: %d" % len(groups["bass"])
    headers = song.measureHeaders
    if len(headers) < 16:
        return None, "short"
    bars = []
    for h in headers:
        ks = h.keySignature.value if h.keySignature is not None else (0, 0)
        bars.append({"start": h.start / TPQ - headers[0].start / TPQ,
                     "len": h.timeSignature.numerator * 4.0 / h.timeSignature.denominator.value,
                     "ts": [h.timeSignature.numerator, h.timeSignature.denominator.value],
                     "key": int(ks[0]), "minor": bool(ks[1]),
                     "marker": h.marker.title if h.marker is not None else "",
                     "repeat_open": bool(h.isRepeatOpen), "repeat_close": int(h.repeatClose),
                     "alt": int(h.repeatAlternative)})
    q0 = headers[0].start / TPQ

    def notes_of(track, drums=False):
        out = []
        tuning = [s.value for s in track.strings]
        for mi, m in enumerate(track.measures):
            for v in m.voices:
                for b in v.beats:
                    if not b.notes:
                        continue
                    t = b.start / TPQ - q0
                    d = b.duration.time / TPQ
                    for n in b.notes:
                        typ = n.type.name if n.type is not None else "normal"
                        if typ == "rest":
                            continue
                        if drums:
                            out.append([round(t, 4), int(n.value)])
                            continue
                        s = n.string - 1
                        if not 0 <= s < len(tuning):
                            continue
                        midi = tuning[s] + n.value + track.offset
                        out.append({"q": round(t, 4), "d": round(d, 4), "midi": int(midi), "string": int(n.string),
                                    "fret": int(n.value), "dead": typ == "dead", "tie": typ == "tie", "bar": mi})
        return out

    bass_t = groups["bass"][0]
    bass = notes_of(bass_t)
    real = [n for n in bass if not n["dead"] and not n["tie"]]
    if len(real) < 50:
        return None, "few bass notes"
    lo, hi = min(n["midi"] for n in real), max(n["midi"] for n in real)
    if lo < LOW or hi > HIGH:
        return None, f"bass range {lo}-{hi}"
    harm = [n for t in groups["harmony"] for n in notes_of(t)]
    drums = [n for t in groups["drums"] for n in notes_of(t, drums=True)]
    # harmony per half bar -> chord label (bass pitch class as a tie-breaker)
    chords = []
    for bi, b in enumerate(bars):
        half = b["len"] / 2
        for k in range(2):
            a, e = b["start"] + k * half, b["start"] + (k + 1) * half
            w = np.zeros(12)
            for n in harm:
                ov = min(e, n["q"] + n["d"]) - max(a, n["q"])
                if ov > 0 and not n["dead"]:
                    w[n["midi"] % 12] += ov
            bp = next((n["midi"] % 12 for n in real if a <= n["q"] < e), None)
            chords.append(chord_label(w, bp) if w.sum() > 0 else "N")
    rec = {"file": os.path.basename(path), "tempo": int(song.tempo), "title": song.title, "artist": song.artist,
           "bass_tuning": [s.value for s in bass_t.strings], "bars": bars, "bass": bass,
           "kick": [q for q, v in drums if v in KICK], "snare": [q for q, v in drums if v in SNARE],
           "half_bar_chords": chords, "n_harmony_tracks": len(groups["harmony"])}
    return rec, "ok"


def parse_all(root, limit=0):
    os.makedirs(OUT, exist_ok=True)
    files = [f for ext in ("gp3", "gp4", "gp5", "gpx", "gp") for f in glob.glob(os.path.join(root, "**", "*." + ext),
                                                                                  recursive=True)]
    why = Counter()
    ok = 0
    for i, f in enumerate(sorted(files)):
        if limit and i >= limit:
            break
        sid = hashlib.md5(os.path.relpath(f, root).encode("utf8")).hexdigest()[:16]
        out = os.path.join(OUT, sid + ".json")
        if os.path.exists(out):
            ok += 1
            continue
        try:
            rec, r = parse_song(f)
        except Exception as e:
            rec, r = None, "error " + type(e).__name__
        why[r.split(" ")[0] if rec is None else "ok"] += 1
        if rec is not None:
            rec["path"] = os.path.relpath(f, root)
            json.dump(rec, open(out, "w", encoding="utf8"), ensure_ascii=False)
            ok += 1
        if i % 500 == 0:
            print(i, len(files), dict(why), flush=True)
    print("parsed", ok, "of", len(files), dict(why))


def stats():
    """Style statistics of the parsed scores (tempo/meter changes, repeats, markers, bass range and rhythm)."""
    fs = glob.glob(os.path.join(OUT, "*.json"))
    n_tempo_meter, rep, mark, sixteenth, keys = Counter(), 0, 0, [], Counter()
    for f in fs:
        r = json.load(open(f, encoding="utf8"))
        ts = {tuple(b["ts"]) for b in r["bars"]}
        n_tempo_meter[min(len(ts), 4)] += 1
        rep += any(b["repeat_open"] for b in r["bars"])
        mark += any(b["marker"] for b in r["bars"])
        keys[r["bars"][0]["key"]] += 1
        q = np.array([n["q"] for n in r["bass"] if not n["tie"]])
        sixteenth.append(float(np.mean(np.abs(q * 4 - np.round(q * 4)) > 1e-3)) if len(q) else 0.0)
    print(len(fs), "songs; meters per song", dict(n_tempo_meter), "; with repeats", rep, "; with markers", mark)
    print("first-bar key signatures", dict(keys.most_common()))
    print("bass onsets off the 16th grid (mean share)", round(float(np.mean(sixteenth)), 4))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["parse", "stats", "test"])
    ap.add_argument("root", nargs="?", default=os.path.join("D:" + os.sep, "BassData", "dadagp"))
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if a.cmd == "parse":
        parse_all(a.root, a.limit)
    elif a.cmd == "stats":
        stats()
    else:
        for f in glob.glob(os.path.join(a.root, "*.gp3")):
            rec, why = parse_song(f)
            print(os.path.basename(f), why, rec and (len(rec["bass"]), rec["half_bar_chords"][:16], rec["bars"][0]))


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
