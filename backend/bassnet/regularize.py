"""Tidy repeated phrases: bars that play the same figure get the same spelling, unless the audio says otherwise.

1. Families: bars with the same meter whose (onset slot, pitch) sets overlap strongly (Jaccard >= sim) are grouped
   around medoids.
2. Each distinct spelling in a family is scored at every member's position against the acoustic posteriors
   (frame pitch/rest log-probabilities + onset evidence), and the spelling with the best total wins.
3. A member takes that spelling only if the audio at its own position does not clearly prefer its current one
   (per-frame score loss <= tau). Real fills (clearly different audio) stay as played.

Bars whose notes tie across a bar line are left alone.
"""
import copy
from typing import Dict, List, Tuple

import numpy as np

from bassnet.decode import FPS
from bassnet.model import PITCH_LO
from bassnet.quantize import TPB, beat_time


def _bar_notes(qs):
    edges = [b * TPB for b in qs.bar_starts]
    import bisect
    bars: List[List] = [[] for _ in range(qs.n_bars)]
    crossing = set()
    for n in qs.notes:
        b = bisect.bisect_right(edges, n.tick) - 1
        if 0 <= b < qs.n_bars:
            bars[b].append(n)
            if n.tick + n.dur > edges[b + 1]:
                crossing.add(b)
                crossing.add(b + 1)
    return bars, edges, crossing


def _spelling(ns, start):
    return tuple(sorted((n.tick - start, n.dur, n.midi, n.dead) for n in ns))


def _score(sp, qs, b, edges, fr, on, lo=1e-4):
    """Mean per-frame log-likelihood of spelling sp placed in bar b."""
    t0 = beat_time(qs.beats, qs.bar0 + edges[b] / TPB)
    t1 = beat_time(qs.beats, qs.bar0 + edges[b + 1] / TPB)
    f0, f1 = int(t0 * FPS), max(int(t1 * FPS), int(t0 * FPS) + 1)
    T = len(on)
    f1 = min(f1, T)
    if f1 <= f0:
        return 0.0
    lab = np.zeros(f1 - f0, int)
    onsets = []
    for (dt, du, midi, dead) in sp:
        a = int(beat_time(qs.beats, qs.bar0 + (edges[b] + dt) / TPB) * FPS)
        e = int(beat_time(qs.beats, qs.bar0 + (edges[b] + dt + du) / TPB) * FPS)
        k = midi - PITCH_LO + 1
        if not dead and 1 <= k < fr.shape[1]:
            lab[max(0, a - f0):max(0, min(e, f1) - f0)] = k
        onsets.append(a)
    frame = np.log(fr[np.arange(f0, f1), lab] + lo).mean()
    ons = [np.log(on[max(0, a - 2):a + 3].max() + lo) for a in onsets if 0 <= a < T]
    # onsets the spelling does NOT claim, but the audio has
    claimed = np.zeros(f1 - f0, bool)
    for a in onsets:
        claimed[max(0, a - 3 - f0):max(0, a + 4 - f0)] = True
    o = on[f0:f1]
    pk = (o[1:-1] > 0.5) & (o[1:-1] >= o[:-2]) & (o[1:-1] > o[2:]) & ~claimed[1:-1]
    missed = float(np.log(1 - o[1:-1][pk] + lo).sum())
    return float(frame + (sum(ons) + missed) / max(1, (f1 - f0)))


def regularize(qs, fr, on, sim=0.7, tau=0.05) -> Tuple[object, Dict]:
    qs = copy.deepcopy(qs)
    bars, edges, crossing = _bar_notes(qs)
    sigs = [{(n.tick - edges[b], n.midi) for n in ns} for b, ns in enumerate(bars)]
    cand = [b for b in range(qs.n_bars) if bars[b] and b not in crossing]
    # medoid clustering by Jaccard
    fam: Dict[int, List[int]] = {}
    assigned = set()
    order = sorted(cand, key=lambda b: -sum(1 for c in cand if c != b and qs.meter_of(c) == qs.meter_of(b)
                                            and len(sigs[b] & sigs[c]) / max(1, len(sigs[b] | sigs[c])) >= sim))
    for b in order:
        if b in assigned:
            continue
        mem = [c for c in cand if c not in assigned and qs.meter_of(c) == qs.meter_of(b)
               and len(sigs[b] & sigs[c]) / max(1, len(sigs[b] | sigs[c])) >= sim]
        if len(mem) >= 2:
            fam[b] = mem
            assigned.update(mem)
    changed_bars = changed_notes = 0
    new_bar_notes = {}
    for med, mem in fam.items():
        spells = {b: _spelling(bars[b], edges[b]) for b in mem}
        versions = sorted(set(spells.values()))
        if len(versions) < 2:
            continue
        sc = {(v, b): _score(v, qs, b, edges, fr, on) for v in versions for b in mem}
        best = max(versions, key=lambda v: sum(sc[(v, b)] for b in mem))
        for b in mem:
            if spells[b] == best:
                continue
            if sc[(best, b)] >= sc[(spells[b], b)] - tau:
                new_bar_notes[b] = best
                changed_bars += 1
                changed_notes += len(set(best) ^ set(spells[b]))
    if new_bar_notes:
        tmpl = {}
        for b, ns in enumerate(bars):
            for n in ns:
                tmpl.setdefault(b, []).append(n)
        keep = [n for b, ns in enumerate(bars) if b not in new_bar_notes for n in ns]
        # notes before the first bar edge / outside bars
        inside = {id(n) for ns in bars for n in ns}
        keep += [n for n in qs.notes if id(n) not in inside]
        proto = qs.notes[0]
        for b, sp in new_bar_notes.items():
            olds = bars[b]
            for (dt, du, midi, dead) in sp:
                n = copy.copy(proto)
                src = next((o for o in olds if o.tick - edges[b] == dt and o.midi == midi), None)
                if src is not None:
                    n = copy.copy(src)
                else:
                    n.flag = False
                    n.slide = 0
                    n.hopo_origin = n.hopo_dest = False
                    n.slap = n.pop = False
                n.tick, n.dur, n.midi, n.dead = edges[b] + dt, du, midi, dead
                keep.append(n)
        qs.notes = sorted(keep, key=lambda n: (n.tick, n.midi))
    return qs, {"families": len(fam), "changed_bars": changed_bars, "changed_notes": changed_notes}
