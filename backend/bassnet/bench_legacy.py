"""Legacy engine baseline on the BassNet test metric (what the user gets from the old GP output)."""
import paths
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.gpif_parser import parse_gp  # noqa: E402
from bassnet.score_eval import compare_notes  # noqa: E402

OUT = paths.cache(r"bassnet\legacy")


def best_offset(gt, est, span=1.5):
    gt_t = np.array([n["time"] for n in gt])
    et = np.array([n["time"] for n in est])
    best, bo = -1, 0.0
    for off in np.arange(-span, span, 0.01):
        idx = np.searchsorted(gt_t, et + off)
        idx = np.clip(idx, 1, len(gt_t) - 1)
        d = np.minimum(np.abs(gt_t[idx] - et - off), np.abs(gt_t[idx - 1] - et - off))
        s = int((d < 0.06).sum())
        if s > best:
            best, bo = s, off
    return bo


def run(meta):
    from ai_transcriber import transcribe_bass_audio, detect_key_signature
    from gp_builder import create_clean_gp_project
    import soundfile as sf
    stem = meta["stem"]
    tempo, notes, is5, sync, ts = transcribe_bass_audio(stem, is_5string=len(meta["tuning"]) == 5)
    raw = [{"time": n["time"], "end": n["time"] + n["duration"], "midi": n["midi"]} for n in notes]
    out = os.path.join(OUT, os.path.basename(meta["npz"]).replace(".npz", ".gp"))
    create_clean_gp_project(out, "t", "a", tempo=tempo, is_5string=is5, audio_path=None,
                            duration=float(sf.info(stem).duration), detected_onsets=notes,
                            sync_points=sync, time_signature=ts)
    info = parse_gp(out)
    est = [{"time": n.time, "end": n.end, "midi": n.midi} for n in info.notes if not n.grace]
    gt = [n for n in meta["notes"] if not n.get("grace")]
    off = best_offset(gt, est)
    est = [dict(n, time=n["time"] + off, end=n["end"] + off) for n in est]
    beats = np.array(meta["beats"])
    return compare_notes(gt, raw, beats), compare_notes(gt, est, beats)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    rows = []
    only = None
    if "--test" in sys.argv:
        only = set(json.load(open(os.path.join(os.path.dirname(OUT), "test_split.json"), encoding="utf8")))
    for f in sorted(glob.glob(paths.cache(r"bassnet\feats\*.json"))):
        m = json.load(open(f, encoding="utf8"))
        if m["rate_final"] < 0.85 or (only is not None and m["gp"] not in only):
            continue
        m["npz"] = f.replace(".json", ".npz")
        raw, gp = run(m)
        name = os.path.basename(os.path.dirname(m["gp"]))[:28]
        print(f"{name:30s} RAW pitch={raw['pitch_acc']:.3f} onsetR={raw['onset_recall']:.3f} | "
              f"GP pitch={gp['pitch_acc']:.3f} dur={gp['dur_acc']:.3f} note={gp['note_acc']:.3f} extra={gp['extra']:.3f}", flush=True)
        rows.append((raw, gp))
    for k in ("pitch_acc", "onset_recall", "dur_acc", "note_acc"):
        print(k, "raw", round(np.mean([r[0][k] for r in rows]), 4), "gp", round(np.mean([r[1][k] for r in rows]), 4))
