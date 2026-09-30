"""Where does the pipeline throw away what the model heard? A stage-by-stage loss waterfall on val/test songs.

Reference: the re-aligned tab notes (labels_v2, audio time). Stages, each scored as note F1 (onset 50 ms + pitch):
  decode    posteriors -> notes (decode_notes as in production)
  quantize  notes on the bar grid (quantize, times read back through the beat map)
  tidy      after regularize (repeated phrases spelled alike)
  written   the final GP as parsed back (song_eval run given by --final)
and for the decode stage a breakdown of the misses: did the model hear the note (onset peak below threshold,
right pitch in the frame posterior), was it merged into a neighbour, or is there nothing in the posterior at all;
pitch errors: octave, the right pitch as runner-up, other.

python -m bassnet.loss_audit [--final RUN]
"""
import argparse
import collections
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")


def match(ref, est, tol=0.05):
    """Greedy one-to-one onset matching -> list of (ref index, est index)."""
    rt = np.array([n["time"] for n in ref])
    used = np.zeros(len(ref), bool)
    pairs = []
    for i, e in enumerate(est):
        lo, hi = np.searchsorted(rt, e["time"] - tol), np.searchsorted(rt, e["time"] + tol, side="right")
        best, bd = -1, 1e9
        for j in range(lo, hi):
            if not used[j]:
                d = abs(rt[j] - e["time"]) - (0.02 if ref[j]["midi"] == e["midi"] else 0)
                if d < bd:
                    best, bd = j, d
        if best >= 0:
            used[best] = True
            pairs.append((best, i))
    return pairs


def f1(ref, est, tol=0.05):
    p = match(ref, est, tol)
    ok = sum(ref[a]["midi"] == est[b]["midi"] for a, b in p)
    P, R = ok / max(1, len(est)), ok / max(1, len(ref))
    return 2 * P * R / max(1e-9, P + R), P, R


def main():
    from bassnet.phase_audit import song_inputs
    from bassnet.decode import decode_notes, FPS
    from bassnet.model import PITCH_LO
    from bassnet import pipeline
    from bassnet.quantize import quantize, beat_time, TPB
    from bassnet.regularize import regularize
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", default="ph_new_noa1")
    ap.add_argument("--splits", default="val,test")
    a = ap.parse_args()
    splits = set(a.splits.split(","))
    final = {}
    fs = os.path.join(ROOT, "song_eval", a.final, "summary.json")
    if os.path.exists(fs):
        final = {r["key"]: r["note_f1"] for r in json.load(open(fs, encoding="utf8"))["songs"]}
    S = collections.defaultdict(list)
    miss = collections.Counter()
    perr = collections.Counter()
    fp = collections.Counter()
    for m, split, (fr, on, de, be, do) in song_inputs():
        if split not in splits:
            continue
        key = os.path.basename(m["npz"])[:-4]
        lf = os.path.join(ROOT, "labels_v2", key + ".json")
        lab = json.load(open(lf, encoding="utf8"))["notes"] if os.path.exists(lf) else m["notes"]
        ref = sorted((n for n in lab if not n.get("grace") and not n.get("dead")), key=lambda n: n["time"])
        notes = decode_notes(fr, on, de)
        est = sorted((n for n in notes if not n.get("dead")), key=lambda n: n["time"])
        S["decode"].append(f1(ref, est))
        # ---- misses at the decode stage
        pairs = match(ref, est)
        matched_ref = {r for r, _ in pairs}
        et = np.array([n["time"] for n in est])
        for j, r in enumerate(ref):
            if j in matched_ref:
                continue
            f = int(round(r["time"] * FPS))
            pk = float(on[max(0, f - 3):f + 4].max()) if f < len(on) else 0.0
            w = fr[max(0, f + 2):f + 8]
            heard = len(w) and int(np.argmax(w.mean(0)[1:])) + PITCH_LO == r["midi"]
            near = len(et) and np.abs(et - r["time"]).min() < 0.12
            if pk >= 0.5:
                kind = "onset peak >= thr but suppressed (min_dist / merged / silence rule)"
            elif pk >= 0.25:
                kind = "weak onset 0.25-0.5, pitch heard" if heard else "weak onset 0.25-0.5, pitch not heard"
            elif heard:
                kind = "no onset peak, but pitch heard (re-attack / legato)"
            else:
                kind = "nothing in the posterior"
            if near and pk < 0.5:
                kind += " [an est note within 120 ms]"
            miss[kind] += 1
        for rj, ei in pairs:
            r, e = ref[rj], est[ei]
            if r["midi"] == e["midi"]:
                continue
            f = int(round(e["time"] * FPS))
            p = fr[f + 2:f + 10].mean(0)[1:] if f + 10 < len(fr) else None
            if (r["midi"] - e["midi"]) % 12 == 0:
                perr["octave"] += 1
            elif p is not None and int(np.argsort(p)[-2]) + PITCH_LO == r["midi"]:
                perr["right pitch was the runner-up"] += 1
            elif abs(r["midi"] - e["midi"]) <= 2:
                perr["1-2 semitones off"] += 1
            else:
                perr["other"] += 1
        matched_est = {e for _, e in pairs}
        for i, e in enumerate(est):
            if i not in matched_est:
                fp["low conf (<0.5)" if min(e.get("conf", 1), e.get("on_peak", 1)) < 0.5 else "confident"] += 1
        # ---- quantize / tidy
        beats, downs, meter, do2 = pipeline.song_beats(be, do, notes)
        qs = quantize(notes, beats, downs, meter)

        def back(q):
            return [{"time": float(beat_time(q.beats, q.bar0 + n.tick / TPB)), "midi": n.midi}
                    for n in q.notes if not n.dead]
        S["quantize"].append(f1(ref, sorted(back(qs), key=lambda n: n["time"])))
        S["quantize (70 ms)"].append(f1(ref, sorted(back(qs), key=lambda n: n["time"]), 0.07))
        qt, _ = regularize(qs, fr, on, tau=pipeline.TIDY_TAU)
        S["tidy"].append(f1(ref, sorted(back(qt), key=lambda n: n["time"])))
        if key in final:
            S["written GP"].append((final[key], 0, 0))
        S["n_ref"].append((len(ref), 0, 0))
    print(f"{len(S['decode'])} songs, {int(sum(x[0] for x in S['n_ref']))} tab notes")
    for k in ("decode", "quantize", "quantize (70 ms)", "tidy", "written GP"):
        v = np.array(S[k]) if S[k] else np.zeros((1, 3))
        print(f"  {k:18s} F1 {v[:, 0].mean():.4f}   P {v[:, 1].mean():.4f}  R {v[:, 2].mean():.4f}")
    tot = sum(miss.values())
    print(f"missed tab notes at decode: {tot}")
    for k, v in miss.most_common():
        print(f"  {v:6d} ({v / max(1, tot):.2f})  {k}")
    print("wrong pitch on matched onsets:", dict(perr))
    print("extra notes:", dict(fp))


if __name__ == "__main__":
    main()
