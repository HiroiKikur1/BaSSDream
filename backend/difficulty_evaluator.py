"""Bass chart difficulty rating (Level 6-31) from the GP bass track.

A song is as hard as its hardest sustained passages, not as long as it is:
every note event gets an effort value (speed, left-hand shifts, string crossings,
rhythmic position, techniques), effort is summed over 4 s sliding windows, and
the core difficulty is the mean of the hardest 10 % of windows. Endurance (how
long the song stays near that level) only adds a small, log-scaled bonus, so a
"short ver." and the full version of the same arrangement land within ~1 level.

Timing, repeats, tuplets and ties come from bassnet.gpif_parser (the same
parser the transcription/evaluation pipeline is validated on).
"""
import math
import os
import sys
from fractions import Fraction
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WINDOW_S = 4.0
TOP_SHARE = 0.10
MIN_IOI = 0.06                   # s; faster "events" are flams / chord rolls

# raw -> level: piecewise linear in log(raw). Fixed knots (calibrated once on the 326-song library,
# 2026-09-26: raw quantiles 1/10/50/90/99.5 % -> Lv 8/13/20/26/30.5) so a song's level never
# changes because other songs were added.
LEVEL_KNOTS = ((1.5, 6.0), (2.9, 8.0), (6.5, 13.0), (9.65, 20.0), (12.7, 26.0), (29.0, 30.5), (40.0, 31.0))

TIERS = ((29, "SPECIAL"), (25, "EXPERT"), (19, "HARD"), (13, "NORMAL"), (0, "EASY"))


# ---------------------------------------------------------------- note events

def _events_from_gp(gp_path: str) -> Optional[List[Dict[str, Any]]]:
    """Simultaneous notes merged into one event: time, qpos, strings/frets and technique flags."""
    try:
        from bassnet.gpif_parser import parse_gp
        info = parse_gp(gp_path)
    except Exception:
        return None
    events: List[Dict[str, Any]] = []
    pending_grace = 0
    for n in sorted(info.notes, key=lambda n: (n.qpos, n.grace is False, n.string)):
        if n.grace:
            pending_grace += 1
            continue
        if events and events[-1]["q"] == n.qpos:
            e = events[-1]
        else:
            e = {"t": n.time, "q": n.qpos, "qend": n.qpos + n.qdur, "notes": [], "grace": pending_grace,
                 "slap": False, "pop": False, "hopo": False, "slide": False, "dead": 0}
            events.append(e)
            pending_grace = 0
        e["notes"].append((n.string, n.fret))
        e["qend"] = max(e["qend"], n.qpos + n.qdur)
        e["slap"] |= n.slap
        e["pop"] |= n.pop
        e["hopo"] |= n.hopo_dest
        e["slide"] |= bool(n.slide & 1) or bool(n.slide & 2)
        e["dead"] += n.dead
    return events


def _events_from_legacy(notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fallback for formats the GPIF parser can't read (.gp5): no score positions, 16th grid assumed."""
    events: List[Dict[str, Any]] = []
    for n in sorted(notes, key=lambda n: n.get("timestamp", 0.0)):
        t = float(n.get("timestamp", 0.0))
        if events and abs(events[-1]["t"] - t) < 1e-6:
            events[-1]["notes"].append((n.get("string", 0), n.get("fret", 0)))
            continue
        events.append({"t": t, "q": None, "qend": None, "notes": [(n.get("string", 0), n.get("fret", 0))],
                       "grace": 0, "slap": bool(n.get("is_slap") or n.get("is_pop")), "pop": False,
                       "hopo": bool(n.get("is_hammer")), "slide": bool(n.get("is_slide")), "dead": 0})
    return events


# ---------------------------------------------------------------- effort model

def _rhythm_cost(q: Optional[Fraction], after_rest: bool) -> float:
    if q is None:
        return 0.0
    frac = q - math.floor(q)
    if frac == 0:
        c = 0.0
    elif frac == Fraction(1, 2):
        c = 0.25
    elif frac in (Fraction(1, 4), Fraction(3, 4)):
        c = 0.5
    else:
        c = 0.65                                     # tuplets, 32nds
    # entering on an off-beat after a rest is the classic place to rush
    return c + (0.2 if after_rest and c > 0 else 0.0)


def _efforts(events: List[Dict[str, Any]]) -> List[Dict[str, float]]:
    out = []
    pos = None                                       # index-finger fret of the fretting hand
    prev = None
    for e in events:
        ioi = max(MIN_IOI, e["t"] - prev["t"]) if prev else 1.0
        pressure = min(2.0, max(0.5, 0.3 / ioi))     # how little time there is to do the next thing
        rate = 1.0 / ioi

        # left hand: one-finger-per-fret box of 4 frets (5 above the 7th fret)
        shift = 0.0
        fretted = [f for _, f in e["notes"] if f > 0]
        if fretted:
            lo, hi = min(fretted), max(fretted)
            if pos is None:
                pos = lo
            span = 4 if pos >= 7 else 3
            if lo < pos:
                shift, pos = pos - lo, lo
            elif hi > pos + span:
                shift, pos = hi - span - pos, hi - span
        move = 0.25 * shift ** 0.8 * pressure

        cross = 0.0
        if prev:
            ds = abs(min(s for s, _ in e["notes"]) - min(s for s, _ in prev["notes"]))
            cross = (0.15 * ds + 0.25 * max(0, ds - 1)) * pressure

        after_rest = bool(prev and prev["qend"] is not None and e["q"] is not None and prev["qend"] < e["q"])
        rhythm = _rhythm_cost(e["q"], after_rest)
        tech = (0.5 if (e["slap"] or e["pop"]) else 0.0) + 0.2 * e["hopo"] + 0.3 * e["slide"] \
            + 0.1 * min(1, e["dead"]) + 0.4 * e["grace"] + 0.4 * (len(e["notes"]) - 1)

        speed = 1.0 + max(0.0, rate - 5.0) / 6.0     # 16ths at 180 BPM (12/s) cost ~2x a slow note
        out.append({"t": e["t"], "effort": speed * (1.0 + move + cross + rhythm + tech), "rate": rate,
                    "move": move + cross, "rhythm": rhythm, "tech": tech})
        prev = e
    return out


def _window_strain(eff: List[Dict[str, float]]) -> List[float]:
    """Effort per second over the WINDOW_S seconds ending at every event."""
    strains, left, acc = [], 0, 0.0
    for right, x in enumerate(eff):
        acc += x["effort"]
        while eff[left]["t"] < x["t"] - WINDOW_S:
            acc -= eff[left]["effort"]
            left += 1
        strains.append(acc / WINDOW_S)
    return strains


def _top_mean(values: List[float], share: float = TOP_SHARE) -> float:
    if not values:
        return 0.0
    k = max(1, int(math.ceil(len(values) * share)))
    return sum(sorted(values, reverse=True)[:k]) / k


# ---------------------------------------------------------------- rating

def _raw_to_level(raw: float) -> float:
    if raw <= LEVEL_KNOTS[0][0]:
        return LEVEL_KNOTS[0][1]
    x = math.log(raw)
    for (r0, l0), (r1, l1) in zip(LEVEL_KNOTS, LEVEL_KNOTS[1:]):
        if raw <= r1:
            return l0 + (l1 - l0) * (x - math.log(r0)) / (math.log(r1) - math.log(r0))
    return LEVEL_KNOTS[-1][1]


def evaluate_difficulty(gp_path: str, legacy_notes: Optional[List[Dict[str, Any]]] = None,
                        tempo: float = 120.0, is_5string: bool = False) -> Dict[str, Any]:
    events = _events_from_gp(gp_path) if gp_path and gp_path.lower().endswith(".gp") else None
    if not events:
        events = _events_from_legacy(legacy_notes or [])
    if len(events) < 8:
        return {"level": 6, "level_exact": 6.0, "tier": "EASY", "peak_nps": 0.0, "avg_nps": 0.0,
                "radar": {"speed": 0, "stamina": 0, "rhythm": 0, "agility": 0, "technique": 0}, "tags": []}

    eff = _efforts(events)
    strain = _window_strain(eff)
    core = _top_mean(strain)

    # endurance: seconds spent near the core level (step between events ~ time covered)
    near = sum(min(1.0, eff[i]["t"] - eff[i - 1]["t"]) for i in range(1, len(eff)) if strain[i] >= 0.7 * core)
    raw = core * (1.0 + 0.06 * math.log2(1.0 + near / 60.0))

    level_f = _raw_to_level(raw)
    level = int(round(level_f))
    tier = next(name for lim, name in TIERS if level >= lim)

    # radar / tags describe what makes the hard part hard (top-strain events)
    duration = max(1.0, eff[-1]["t"] - eff[0]["t"])
    hard = [eff[i] for i in sorted(range(len(eff)), key=lambda i: -strain[i])[:max(8, len(eff) // 10)]]
    peak_nps = _top_mean([sum(1 for y in eff if x["t"] - 1.0 < y["t"] <= x["t"]) for x in eff], 0.02)
    avg = lambda k: sum(x[k] for x in hard) / len(hard)  # noqa: E731
    radar = {
        "speed": min(100, round(peak_nps / 14.0 * 100)),
        "stamina": min(100, round(near / 180.0 * 100)),
        "rhythm": min(100, round(avg("rhythm") / 0.6 * 100)),
        "agility": min(100, round(avg("move") / 1.5 * 100)),
        "technique": min(100, round(avg("tech") / 0.6 * 100)),
    }
    n_slap = sum(1 for e in events if e["slap"] or e["pop"])
    tags = []
    if is_5string:
        tags.append("5弦")
    if radar["speed"] >= 70:
        tags.append("高速")
    if radar["agility"] >= 60:
        tags.append("换把")
    if radar["rhythm"] >= 60:
        tags.append("切分")
    if n_slap >= 8:
        tags.append("Slap")
    if radar["stamina"] >= 60:
        tags.append("耐力")
    if tempo >= 185:
        tags.append("快歌")

    return {
        "level": level,
        "level_exact": round(level_f, 1),
        "tier": tier,
        "peak_nps": round(peak_nps, 1),
        "avg_nps": round(len(events) / duration, 1),
        "raw": round(raw, 3),
        "radar": radar,
        "tags": tags,
    }
