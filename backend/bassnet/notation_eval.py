"""Notation-level evaluation: does the generated tab *read* like the original?

Audio-time note metrics (score_eval) are blind to meter, bar lines and rhythm spelling; a tab with every
note at the right time can still be unreadable (e.g. 4/4 written as 12/8). Here both scores are cut into
bars placed on the audio timeline; each original bar that contains notes is paired with the generated bar
starting within `tol` seconds, and bar contents are compared as (position in bar, pitch, duration) in quarters.
"""
import os
from fractions import Fraction
from typing import Dict, List

import numpy as np

from bassnet.gpif_parser import parse_gp, q_to_sec


def label_time_map(meta):
    """Maps original-GP seconds to the aligned label timeline (align_labels applies a smooth, time-varying shift)."""
    # note: "time_gp" already includes the first-pass global lag (dataset.py), raw GP time does not
    lag = float(meta.get("global_lag", 0.0) or 0.0)
    pairs = sorted((n["time_gp"], n["time"]) for n in meta["notes"] if "time_gp" in n)
    if not pairs:
        return lambda t: t + lag
    g = np.array([p[0] for p in pairs])
    d = np.array([p[1] - p[0] for p in pairs])
    return lambda t: t + lag + float(np.interp(t + lag, g, d))


def label_q_map(meta):
    """Score position (quarters, repeats unrolled) -> aligned audio time, straight from the label notes.
    Preferred over label_time_map: the cached time_gp values came from an older GP time map and can be off by
    50-100 ms (up to ~1 s) against today's parser, which misplaces the original's bars."""
    by_q: Dict = {}
    for n in meta["notes"]:
        if not n.get("grace"):
            by_q.setdefault(float(n["qpos"]), []).append(float(n["time"]))
    if len(by_q) < 2:
        return None
    q = np.array(sorted(by_q))
    t = np.maximum.accumulate(np.array([float(np.median(by_q[x])) for x in q]))
    k = min(len(q) - 1, 8)
    s0 = (t[k] - t[0]) / max(q[k] - q[0], 1e-6)
    s1 = (t[-1] - t[-1 - k]) / max(q[-1] - q[-1 - k], 1e-6)

    def f(x):
        x = float(x)
        if x < q[0]:
            return float(t[0] + (x - q[0]) * s0)
        if x > q[-1]:
            return float(t[-1] + (x - q[-1]) * s1)
        return float(np.interp(x, q, t))
    return f


def score_bars(info, offset: float = 0.0, tmap=None, qmap=None) -> List[Dict]:
    by_bar: Dict = {}
    for n in info.notes:
        if not n.grace:
            by_bar.setdefault((n.bar, n.occurrence), []).append(n)
    out = []
    for (b, oc, q0, ql) in info.bars:
        ns = sorted(by_bar.get((b, oc), []), key=lambda n: (n.qpos, n.midi))
        if qmap is not None:
            t = qmap(q0)
        else:
            t = q_to_sec(info.anchors, float(q0))
            t = tmap(t) if tmap else t
        out.append({"t": t + offset, "len": Fraction(ql), "q0": Fraction(q0),
                    "notes": [(n.qpos - q0, n.midi, n.qdur, bool(n.dead)) for n in ns]})
    return out


def gt_score_bars(meta, info=None, qmap=None) -> List[Dict]:
    """The original tab's bars on the aligned audio timeline of a training/eval meta."""
    info = info or parse_gp(meta["gp"])
    qm = qmap or fixed_q_map(meta, info) or label_q_map(meta)
    return score_bars(info, qmap=qm) if qm else score_bars(info, tmap=label_time_map(meta))


def posterior_q_map(info, post):
    """Score position -> audio time from score_align's beat DP over (fr, on, de, be, do) posteriors."""
    from bassnet.score_align import align_posteriors
    fr, on, _de, be, do = post
    r = align_posteriors(info, fr, on, be, do)
    bq, bt = np.array(r["beat_q"]), np.array(r["beat_t"])
    return lambda q: float(np.interp(float(q), bq, bt))


GT_ALIGN_DIR = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "eval", "gt_align")
GT_ALIGN_POST = {"val": ["val_v3", "val_v4"], "test": ["post_cache_v3", "post_cache_v4"]}


def fixed_q_map(meta, info=None):
    """Reference placement of the original's bars for evaluation: score_align over a FIXED posterior set
    (v3+v4), cached, so model A/B comparisons all use the same bar timeline."""
    import json
    import os
    key = os.path.basename(meta["npz"])[:-4]
    cp = os.path.join(GT_ALIGN_DIR, key + ".json")
    if not os.path.exists(cp):
        base = os.path.dirname(GT_ALIGN_DIR)
        dirs = next((ds for ds in GT_ALIGN_POST.values()
                     if all(os.path.exists(os.path.join(base, d, key + ".npz")) for d in ds)), None)
        if dirs is None:
            return None
        acc = None
        for d in dirs:
            z = np.load(os.path.join(base, d, key + ".npz"))
            arr = [z[k].astype(np.float32) for k in ("fr", "on", "de", "be", "do")]
            acc = arr if acc is None else [x + y for x, y in zip(acc, arr)]
        post = [x / len(dirs) for x in acc]
        from bassnet.score_align import align_posteriors
        r = align_posteriors(info or parse_gp(meta["gp"]), post[0], post[1], post[3], post[4])
        os.makedirs(GT_ALIGN_DIR, exist_ok=True)
        json.dump({"beat_q": r["beat_q"], "beat_t": r["beat_t"]}, open(cp, "w"))
    d = json.load(open(cp))
    bq, bt = np.array(d["beat_q"]), np.array(d["beat_t"])
    return lambda q: float(np.interp(float(q), bq, bt))


def compare_bars(gt: List[Dict], est: List[Dict], tol: float = 0.08) -> Dict:
    et = np.array([b["t"] for b in est]) if est else np.zeros(0)
    n = aligned = meter_ok = exact = rhythm = pitch_rhythm = 0
    note_tot = note_ok = note_pp = note_pos = 0
    for b in gt:
        if not b["notes"]:
            continue
        n += 1
        note_tot += len(b["notes"])
        if not len(et):
            continue
        j = int(np.argmin(np.abs(et - b["t"])))
        if abs(et[j] - b["t"]) > tol:
            continue
        aligned += 1
        g = est[j]
        if g["len"] != b["len"]:
            continue
        meter_ok += 1
        exact += g["notes"] == b["notes"]
        rhythm += [(p, d) for p, _, d, _ in g["notes"]] == [(p, d) for p, _, d, _ in b["notes"]]
        pitch_rhythm += [(p, m) for p, m, _, _ in g["notes"]] == [(p, m) for p, m, _, _ in b["notes"]]
        est_set = set(g["notes"])
        note_ok += sum(x in est_set for x in b["notes"])
        # looser views of the same bar: onset position + pitch (any duration), onset position only
        pp = {(p, m) for p, m, _, _ in g["notes"]}
        po = {p for p, _, _, _ in g["notes"]}
        note_pp += sum((p, m) in pp for p, m, _, _ in b["notes"])
        note_pos += sum(p in po for p, _, _, _ in b["notes"])
    n = max(1, n)
    return {"bars": n, "barline": aligned / n, "meter_acc": meter_ok / max(1, aligned),
            "bar_exact": exact / n, "bar_rhythm": rhythm / n, "bar_onsets_pitch": pitch_rhythm / n,
            "notation_note_acc": note_ok / max(1, note_tot), "note_pos_pitch": note_pp / max(1, note_tot),
            "note_pos": note_pos / max(1, note_tot)}


def compare_files(gt_gp: str, est_gp: str, est_offset: float = 0.0) -> Dict:
    return compare_bars(score_bars(parse_gp(gt_gp)), score_bars(parse_gp(est_gp), est_offset))


def error_breakdown(gt: List[Dict], est: List[Dict], tol: float = 0.08) -> Dict:
    """Why is each original note not reproduced exactly? First failing reason wins, in reading order:
    bar missing/misplaced, bar length differs, no onset at that position (near miss within 1/8 quarter or truly
    missing), wrong pitch, wrong duration."""
    from collections import Counter
    et = np.array([b["t"] for b in est]) if est else np.zeros(0)
    c: Counter = Counter()
    for b in gt:
        for p, m, d, dead in b["notes"]:
            c["total"] += 1
            j = int(np.argmin(np.abs(et - b["t"]))) if len(et) else -1
            if j < 0 or abs(et[j] - b["t"]) > tol:
                c["bar_misplaced"] += 1
                continue
            g = est[j]
            if g["len"] != b["len"]:
                c["bar_length"] += 1
                continue
            at = [x for x in g["notes"] if x[0] == p]
            if not at:
                near = [x for x in g["notes"] if abs(x[0] - p) <= Fraction(1, 8)]
                c["onset_near" if near else "onset_missing"] += 1
                continue
            if not any(x[1] == m for x in at):
                c["pitch"] += 1
                continue
            if not any(x[1] == m and x[2] == d for x in at):
                c["duration_short" if all(x[2] < d for x in at if x[1] == m) else "duration_long"] += 1
                continue
            if not any(x == (p, m, d, dead) for x in at):
                c["dead_flag"] += 1
                continue
            c["ok"] += 1
    return dict(c)
