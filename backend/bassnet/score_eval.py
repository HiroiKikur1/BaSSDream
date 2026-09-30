"""Score-level evaluation of a generated GP against the ground-truth GP (both in audio time)."""
import numpy as np

from bassnet.quantize import time_to_beat


def compare_scores(gt_meta, est_info, audio_offset=0.0, tol=0.06):
    gt = sorted([n for n in gt_meta["notes"] if not n.get("grace")], key=lambda n: n["time"])
    est = sorted([{"time": n.time + audio_offset, "end": n.end + audio_offset, "midi": n.midi, "dead": n.dead}
                  for n in est_info.notes if not n.grace], key=lambda n: n["time"])
    return compare_notes(gt, est, np.array(gt_meta["beats"]), tol)


def compare_notes(gt, est, gt_beats, tol=0.06):
    gt = [n for n in gt if not n.get("dead")]
    est = [n for n in est if not n.get("dead")]
    t0, t1 = gt[0]["time"] - 1.0, gt[-1]["end"] + 1.0
    est = [n for n in est if t0 <= n["time"] <= t1]
    gt_t = np.array([n["time"] for n in gt])
    used = np.zeros(len(gt), bool)
    matched = pitch_ok = dur_ok = full_ok = 0
    for e in est:
        lo = np.searchsorted(gt_t, e["time"] - tol)
        hi = np.searchsorted(gt_t, e["time"] + tol, side="right")
        cands = [j for j in range(lo, hi) if not used[j]]
        if not cands:
            continue
        j = min(cands, key=lambda j: (gt[j]["midi"] != e["midi"], abs(gt_t[j] - e["time"])))
        used[j] = True
        matched += 1
        p = gt[j]["midi"] == e["midi"]
        pitch_ok += p
        gd = time_to_beat(gt_beats, gt[j]["end"]) - time_to_beat(gt_beats, gt[j]["time"])
        ed = time_to_beat(gt_beats, e["end"]) - time_to_beat(gt_beats, e["time"])
        d = abs(gd - ed) < 1.0 / 12 + 1e-6
        dur_ok += d
        full_ok += p and d
    n = max(1, len(gt))
    return {
        "n_gt": len(gt), "n_est": len(est),
        "onset_recall": matched / n, "onset_precision": matched / max(1, len(est)),
        "pitch_acc": pitch_ok / n,                 # correct pitch at the right time, over all GT notes
        "dur_acc": dur_ok / max(1, matched),       # duration correct among time-matched notes
        "note_acc": full_ok / n,                   # pitch + onset + duration all correct
        "extra": (len(est) - matched) / n,
    }
