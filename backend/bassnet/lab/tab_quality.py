"""How well does a tab match its recording? Objective checks against the audio (used to vet tabs before training).

For a tab already aligned to its recording (label notes in audio time) and BassNet posteriors of that recording:
  agree      share of tab notes that the model hears at the same time (+-60 ms) with the same pitch
  agree_pc   same, any octave (octave choice is partly an arrangement decision)
  coverage   share of the model's notes that the tab contains (simplified / incomplete tabs score low)
Thresholds are calibrated on the purchased library tabs of held-out songs (val + test).
"""
import numpy as np

from bassnet.decode import decode_notes
from bassnet.score_eval import compare_notes


def _pc_match(gt, est, tol=0.06):
    gt_t = np.array([n["time"] for n in gt])
    used = np.zeros(len(gt), bool)
    ok = 0
    for e in est:
        lo, hi = np.searchsorted(gt_t, e["time"] - tol), np.searchsorted(gt_t, e["time"] + tol, side="right")
        c = [j for j in range(lo, hi) if not used[j]]
        if c:
            j = min(c, key=lambda j: ((gt[j]["midi"] - e["midi"]) % 12 != 0, abs(gt_t[j] - e["time"])))
            used[j] = True
            ok += (gt[j]["midi"] - e["midi"]) % 12 == 0
    return ok / max(1, len(gt))


def score_tab(tab_notes, post, beats):
    """tab_notes: [{time,end,midi,dead,grace}] in audio time; post: (fr, on, de, ...) posteriors."""
    fr, on, de = post[:3]
    est = sorted([n for n in decode_notes(fr, on, de) if not n["dead"]], key=lambda n: n["time"])
    gt = sorted([n for n in tab_notes if not n.get("dead") and not n.get("grace")], key=lambda n: n["time"])
    if len(gt) < 20:
        return {"agree": 0.0, "agree_pc": 0.0, "coverage": 0.0, "n": len(gt)}
    sc = compare_notes(gt, est, np.asarray(beats))
    return {"agree": sc["pitch_acc"], "agree_pc": _pc_match(gt, est), "coverage": sc["onset_precision"], "n": len(gt)}
