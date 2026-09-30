"""Beat-grid quantisation: note events (seconds) -> bars of rhythmic events.

Works in beat space so tempo drift (live recordings) never accumulates:
every beat interval is mapped independently, and each beat picks its own
subdivision. Bars follow the downbeat sequence, so time-signature changes
are preserved.
"""
from dataclasses import dataclass, field
from typing import List

import numpy as np

TPB = 24   # ticks per beat

# subdivision -> complexity penalty (squared-beat-fraction units, per onset)
SUBDIVS_SIMPLE = {1: 0.0, 2: 0.0, 4: 0.0008, 3: 0.004, 8: 0.012, 6: 0.012}
SUBDIVS_COMPOUND = {1: 0.0, 3: 0.0, 6: 0.0008, 2: 0.006, 12: 0.012, 4: 0.012}


@dataclass
class QNote:
    tick: int            # absolute tick from bar 0
    dur: int             # ticks
    midi: int
    dead: bool = False
    string: int = 0
    fret: int = 0
    slide: int = 0
    hopo_origin: bool = False
    hopo_dest: bool = False
    flag: bool = False       # low-confidence note, marked for proofreading
    slap: bool = False
    pop: bool = False


@dataclass
class QScore:
    beats: np.ndarray                  # extended beat times; beat k starts at tick (k - bar0)*TPB
    bar0: int                          # beat index of bar 0
    bar_starts: List[int]              # beat offsets (from bar0) of every bar start, plus the final end
    compound: bool = False
    bar_compound: List[bool] = field(default_factory=list)
    notes: List[QNote] = field(default_factory=list)
    beat_div: dict = field(default_factory=dict)   # beat offset -> subdivision

    @property
    def n_bars(self):
        return len(self.bar_starts) - 1

    def meter_of(self, b):
        return self.bar_starts[b + 1] - self.bar_starts[b]

    def bar_time(self, b):
        return beat_time(self.beats, self.bar0 + self.bar_starts[min(b, len(self.bar_starts) - 1)])

    @property
    def meter(self):
        return int(np.bincount(np.diff(self.bar_starts)).argmax())


def _per(beats, head=True):
    d = np.diff(beats[:9] if head else beats[-9:])
    return float(np.median(d)) if len(d) else 0.5


def beat_time(beats, k):
    if not float(k).is_integer():
        k0 = int(np.floor(k))
        a, b = beat_time(beats, k0), beat_time(beats, k0 + 1)
        return a + (k - k0) * (b - a)
    k = int(k)
    n = len(beats)
    if 0 <= k < n:
        return float(beats[k])
    return float(beats[-1] + (k - n + 1) * _per(beats, False)) if k >= n else float(beats[0] + k * _per(beats))


def time_to_beat(beats, t):
    """Fractional beat index of time t (linear inside beats, extrapolated outside)."""
    n = len(beats)
    if t < beats[0]:
        return (t - beats[0]) / _per(beats)
    if t >= beats[-1]:
        return n - 1 + (t - beats[-1]) / _per(beats, False)
    i = int(np.searchsorted(beats, t, side="right") - 1)
    return i + (t - beats[i]) / (beats[i + 1] - beats[i])


def extend_beats(beats, t_min, t_max):
    beats = np.asarray(beats, float)
    per0, per1 = _per(beats), _per(beats, False)
    pre, t = [], beats[0] - per0
    while t > t_min - 2 * per0:
        pre.append(t)
        t -= per0
    post, t = [], beats[-1] + per1
    while t < t_max + 3 * per1:
        post.append(t)
        t += per1
    return np.concatenate([pre[::-1], beats, post]), len(pre)


def choose_subdiv(fracs, compound=False):
    """fracs: positions in [0,1) of onsets inside one beat."""
    table = SUBDIVS_COMPOUND if compound else SUBDIVS_SIMPLE
    default = 6 if compound else 4
    if not fracs:
        return default
    best, best_c = default, 1e9
    for d, pen in table.items():
        err = sum(min(abs(f - k / d) for k in range(d + 1)) ** 2 for f in fracs)
        slots = [round(f * d) for f in fracs]
        if len(set(slots)) < len(slots):
            err += 0.05 * (len(slots) - len(set(slots)))
        c = err + pen * max(1, len(fracs))
        if c < best_c - 1e-9:
            best, best_c = d, c
    if compound:
        return 6 if best in (1, 3) else (12 if best in (2, 4) else best)
    return 4 if best in (1, 2) else best


def bar_grid(beats, downbeats, first_beat, last_beat, meter_hint=4):
    """Beat indices of bar starts covering [first_beat, last_beat]."""
    db = sorted({int(np.argmin(np.abs(beats - d))) for d in downbeats}) if len(downbeats) else []
    if len(db) < 2:
        m = meter_hint
        start = first_beat - (first_beat % m)
        return list(range(start, last_beat + 2 * m, m))
    lens = np.diff(db)
    m_first, m_last = int(lens[0]), int(lens[-1])
    starts = list(db)
    while starts[0] > first_beat:
        starts.insert(0, starts[0] - m_first)
    while starts[-1] <= last_beat:
        starts.append(starts[-1] + m_last)
    # trim leading bars that end before the first note
    while len(starts) > 2 and starts[1] <= first_beat:
        starts.pop(0)
    starts.append(starts[-1] + m_last)
    return starts


def song_is_compound(fb, min_votes=20, ratio=2.0):
    """Song-level meter feel. Onsets on positions only a 1/6 grid explains (1/6, 1/3, 2/3, 5/6 of a beat)
    against positions only a 1/4 grid explains (1/4, 3/4); positions shared by both (0, 1/2) don't vote.
    94% of the library is simple meter, so compound needs a clear majority."""
    tri = six = 0
    for f in fb:
        u = f - np.floor(f)
        d4 = min(abs(u - k / 4) for k in range(5))
        d6 = min(abs(u - k / 6) for k in range(7))
        if min(abs(u - k / 2) for k in range(3)) < 0.06:
            continue
        if d6 + 0.03 < d4:
            tri += 1
        elif d4 + 0.03 < d6:
            six += 1
    return tri >= min_votes and tri > ratio * six


def quantize(notes, beats, downbeats, meter_hint=4, compound=False, legato_steps=1, meter_mode="simple",
             lead_in=True, split_compound=False):
    notes = sorted(notes, key=lambda n: n["time"])
    if not notes:
        raise ValueError("no notes")
    beats, _ = extend_beats(beats, min(0.0, notes[0]["time"]), notes[-1]["end"])
    fb = [time_to_beat(beats, n["time"]) for n in notes]
    first_beat = int(np.floor(fb[0] + 0.06))
    last_beat = int(np.ceil(time_to_beat(beats, notes[-1]["end"])))
    starts = bar_grid(beats, downbeats, first_beat, last_beat, meter_hint)
    if lead_in:
        # rest bars back to the start of the recording (like hand-made tabs: intro bars before the bass enters)
        m0 = starts[1] - starts[0]
        while starts[0] - m0 >= 0 and beat_time(beats, starts[0] - m0) >= -0.05:
            starts.insert(0, starts[0] - m0)
    bar0 = starts[0]

    by_beat = {}
    for f in fb:
        k = int(np.floor(f + 1e-6))
        u = f - k
        if u < 0.94:
            by_beat.setdefault(k, []).append(u)
    # per-bar meter feel from grid-fit evidence (simple 1/4 grid vs compound 1/6 grid), smoothed over bars
    def grid_err(u, d):
        return min(abs(u - k / d) for k in range(d + 1)) ** 2
    nb = len(starts) - 1
    evid = np.zeros(nb)
    for bi in range(nb):
        for k in range(starts[bi], starts[bi + 1]):
            for u in by_beat.get(k, []):
                evid[bi] += grid_err(u, 4) - grid_err(u, 6)
    kern = np.array([0.5, 1.0, 2.0, 1.0, 0.5])
    sm = np.convolve(evid, kern, mode="same")
    if meter_mode == "bar":
        bar_comp = [bool(compound or sm[bi] > 0.004) for bi in range(nb)]
    else:
        song_comp = compound or (meter_mode == "song" and song_is_compound(fb))
        bar_comp = [bool(song_comp)] * nb
    beat_comp = {}
    for bi in range(len(starts) - 1):
        for k in range(starts[bi], starts[bi + 1]):
            beat_comp[k] = bar_comp[bi]
    div = {k: choose_subdiv(v, beat_comp.get(k, compound)) for k, v in by_beat.items()}

    def dflt_at(k):
        return 6 if beat_comp.get(k, compound) else 4

    def q_tick(f):
        k = int(np.floor(f + 1e-6))
        u = f - k
        d = div.get(k, dflt_at(k))
        slot = int(round(u * d))
        if u >= 0.94 or slot >= d:
            return (k + 1 - bar0) * TPB
        return (k - bar0) * TPB + slot * (TPB // d)

    def step_at(tick):
        k = tick // TPB + bar0
        return TPB // div.get(k, dflt_at(k))

    ticks = [q_tick(f) for f in fb]
    qn: List[QNote] = []
    for i, n in enumerate(notes):
        t = ticks[i]
        if qn and t <= qn[-1].tick:
            t = qn[-1].tick + step_at(qn[-1].tick)
        e = q_tick(time_to_beat(beats, n["end"]))
        nxt = ticks[i + 1] if i + 1 < len(ticks) else None
        if nxt is not None and nxt > t and e >= nxt - legato_steps * step_at(t):
            e = nxt           # legato into the next note (tab convention)
        e = max(e, t + step_at(t))
        qn.append(QNote(tick=t, dur=e - t, midi=int(n["midi"]), dead=bool(n.get("dead", False)),
                        slide=int(n.get("slide", 0)), hopo_origin=bool(n.get("hopo_origin", False)),
                        hopo_dest=bool(n.get("hopo_dest", False)), flag=bool(n.get("low_conf", False)),
                        slap=bool(n.get("slap", False)), pop=bool(n.get("pop", False))))
    for i in range(len(qn) - 1):
        if qn[i].tick + qn[i].dur > qn[i + 1].tick:
            qn[i].dur = max(1, qn[i + 1].tick - qn[i].tick)
    bar_starts = [s - bar0 for s in starts]
    last_tick = max(n.tick + n.dur for n in qn)
    while bar_starts[-1] * TPB < last_tick:
        bar_starts.append(bar_starts[-1] + (bar_starts[-1] - bar_starts[-2]))
        bar_comp.append(bar_comp[-1] if bar_comp else compound)
    bar_comp = (bar_comp + [compound] * len(bar_starts))[:len(bar_starts) - 1]
    if split_compound:
        # the purchased tabs never use 12/8 (6/8: 1789 bars in 21 songs): a compound bar of 4 dotted-quarter beats
        # is written as two 6/8 bars
        nbs, nbc = [bar_starts[0]], []
        for b in range(len(bar_starts) - 1):
            a, e = bar_starts[b], bar_starts[b + 1]
            if bar_comp[b] and e - a == 4:
                nbs.append(a + 2)
                nbc.append(True)
            nbs.append(e)
            nbc.append(bar_comp[b])
        bar_starts, bar_comp = nbs, nbc
    return QScore(beats=beats, bar0=bar0, bar_starts=bar_starts, compound=compound, bar_compound=bar_comp, notes=qn,
                  beat_div={k - bar0: v for k, v in div.items()})
