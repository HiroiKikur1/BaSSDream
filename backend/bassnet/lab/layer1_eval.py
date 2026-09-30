"""Layer 1 (song understanding) against the purchased tabs' structure: bar lines, beats, first bar, sections.

Reference bar lines / beats: the tab placed on the audio (fixed_q_map for val/test, the re-aligned labels_v2 for
training songs). Candidates: our BassNet beat HMM (cached posteriors; honest cross-fold ones for training songs)
and allin1 (zero-shot, on our 6-stem separation). Only the stretch of the song covered by the tab's notes counts.

python -m bassnet.lab.layer1_eval
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from bassnet.lab.song_eval import f1_events  # noqa: E402

ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
A1 = os.environ.get("ALLIN1_DIR", os.path.join(ROOT, "allin1"))
FUSE_W = tuple(float(x) for x in os.environ.get("L1_FUSE_W", "0.3,0.5").split(",") if x)
FUSE_DO = tuple(float(x) for x in os.environ.get("L1_FUSE_DO", "").split(",") if x)


def f1(ref, est, tol=0.07):
    return f1_events([(t, None) for t in ref], [(t, None) for t in est], tol)[0]


def phase_vote(beats, downs, meter, a_downs, min_share=0.6, tol=0.07):
    """Keep our beats, but let allin1's downbeats (precise when present) vote on the bar phase, song-wide.
    Only when our own bars are strictly periodic (one meter) and the vote is clear."""
    bt = np.asarray(beats, float)
    M = int(meter)
    if len(bt) < 4 * M:
        return downs
    di = [int(np.argmin(np.abs(bt - d))) for d in downs]
    if len(set(np.diff(di))) > 1:
        return downs                              # meter changes: leave it to the HMM
    votes = np.zeros(M)
    for d in a_downs:
        i = int(np.argmin(np.abs(bt - d)))
        if abs(bt[i] - d) < tol:
            votes[i % M] += 1
    if votes.sum() < 8 or votes.max() < min_share * votes.sum():
        return downs
    p = int(np.argmax(votes))
    if p == di[0] % M:
        return downs
    return list(bt[p::M])


def gt_grid(m, info, split):
    """(bar starts, beat times, section starts) of the tab on the audio timeline."""
    from bassnet.notation_eval import fixed_q_map, label_q_map
    from bassnet.section_model import gp_sections
    qm = fixed_q_map(m, info) if split != "train" else None
    qm = qm or label_q_map(m)
    bars, beats = [], []
    for b, oc, q0, ql in info.bars:
        bars.append(qm(float(q0)))
        n = int(round(float(ql)))
        beats += [qm(float(q0) + k * float(ql) / max(1, n)) for k in range(max(1, n))]
    marks = gp_sections(m["gp"])
    secs = [qm(float(q0)) for b, oc, q0, ql in info.bars if oc == 0 and b in marks]
    return np.array(bars), np.array(beats), secs


def main():
    from bassnet.train import load_metas, song_key, frozen_keys
    from bassnet.gpif_parser import parse_gp
    from bassnet.decode import decode_beats, FPS
    val, test = frozen_keys("val"), frozen_keys("test")
    E = os.path.join(ROOT, "eval")
    rows = []
    seen = set()
    for m in load_metas():
        key = os.path.basename(m["npz"])[:-4]
        if key in seen or not os.path.exists(os.path.join(A1, key + ".json")):
            continue
        k = song_key(m["gp"])
        split = "val" if k in val else "test" if k in test else "train"
        if split == "train" and A1.rstrip("/\\").endswith("_ft"):
            continue                       # a fine-tuned allin1 has seen the training songs
        dirs = {"val": ["val_v3", "val_v4"], "test": ["post_cache_v3", "post_cache_v4"], "train": ["xfold"]}[split]
        if not all(os.path.exists(os.path.join(E, d, key + ".npz")) for d in dirs):
            continue
        seen.add(key)
        try:
            info = parse_gp(m["gp"])
            gbars, gbeats, gsec = gt_grid(m, info, split)
        except Exception as e:
            print("skip", key, e)
            continue
        notes_t = sorted(n["time"] for n in m["notes"])
        lo, hi = notes_t[0] - 0.1, notes_t[-1] + 0.1
        gb = gbars[(gbars >= lo) & (gbars <= hi)]
        gbt = gbeats[(gbeats >= lo) & (gbeats <= hi)]
        zs = [np.load(os.path.join(E, d, key + ".npz")) for d in dirs]
        be, do = [np.mean([z[x].astype(np.float32) for z in zs], 0) for x in ("be", "do")]
        beats, downs, meter = decode_beats(be, do)
        a = json.load(open(os.path.join(A1, key + ".json")))
        cands = [("bassnet", beats, downs), ("allin1", a["beats"], a["downbeats"])]
        # fusion: allin1's frame activations (100 fps) mixed into our beat / downbeat posteriors before our HMM
        act = np.load(os.path.join(A1, key + ".npz"))
        tt = np.arange(len(be)) / FPS
        ab = np.interp(tt, np.arange(len(act["beat"])) / 100.0, act["beat"].astype(np.float32))
        ad = np.interp(tt, np.arange(len(act["downbeat"])) / 100.0, act["downbeat"].astype(np.float32))
        for w in FUSE_W:
            fb, fd, _ = decode_beats((1 - w) * be + w * ab, (1 - w) * do + w * ad)
            cands.append((f"fuse{w}", fb, fd))
        for w in FUSE_DO:                   # allin1 only votes on where the bar starts; beats stay ours
            fb, fd, _ = decode_beats(be, (1 - w) * do + w * ad)
            cands.append((f"fdo{w}", fb, fd))
        cands.append(("phasevote", beats, phase_vote(beats, downs, meter, a["downbeats"])))
        cut = lambda xs: [t for t in xs if lo - 0.5 <= t <= hi + 0.5]
        r = {"key": key, "split": split, "name": os.path.basename(os.path.dirname(m["gp"]))[:40]}
        for name, bt, db in cands:
            r[name + "_beat"] = f1(gbt, cut(bt))
            r[name + "_down"] = f1(gb, cut(db))
            # precision: share of its bar lines that fall on a tab bar line (blind to half/double tempo)
            gba = np.asarray(gb)
            r[name + "_downprec"] = float(np.mean([np.abs(gba - t).min() < 0.07 for t in cut(db)])) if len(gba) and cut(db) else 0.0
            r[name + "_period"] = float(np.median(np.diff(bt))) / float(np.median(np.diff(gbt)))
            d0 = [t for t in db if abs(t - gb[0]) < 0.07] if len(gb) else []
            r[name + "_first"] = float(bool(d0))
        if len(gsec) >= 2:
            bl = float(np.median(np.diff(gbars))) if len(gbars) > 2 else 2.0
            r["allin1_sec"] = f1(gsec, [s["start"] for s in a["segments"]], 0.6 * bl)
        rows.append(r)
    json.dump(rows, open(os.path.join(ROOT, "layer1_eval_" + os.path.basename(A1) + ".json"), "w", encoding="utf8"), ensure_ascii=False, indent=1)
    for split in ("train", "val", "test", "all"):
        rr = [r for r in rows if split == "all" or r["split"] == split]
        if not rr:
            continue
        print(f"{split} n={len(rr)}")
        for name in ["bassnet", "allin1", "phasevote"] + [f"fuse{w}" for w in FUSE_W] + [f"fdo{w}" for w in FUSE_DO]:
            line = f"  {name:10s}"
            for k in ("beat", "down", "downprec", "first"):
                line += f"  {k} {np.mean([r[name + '_' + k] for r in rr]):.3f}"
            print(line)
        v = [r["allin1_sec"] for r in rr if "allin1_sec" in r]
        if v:
            print(f"  allin1 section boundary F1 {np.mean(v):.3f} (n={len(v)})")
    both = [r for r in rows]
    for name in ("bassnet", "allin1"):
        per = np.array([r[name + "_period"] for r in rows])
        print(name, "beat level vs tab: same", int(np.sum(np.abs(per - 1) < 0.1)), "half-time", int(np.sum(np.abs(per - 2) < 0.2)),
              "double", int(np.sum(np.abs(per - 0.5) < 0.05)), "other", int(np.sum((np.abs(per - 1) >= 0.1) & (np.abs(per - 2) >= 0.2) & (np.abs(per - 0.5) >= 0.05))))
    print("downbeat: allin1 better on", sum(r["allin1_down"] > r["bassnet_down"] + 0.05 for r in both),
          "songs, worse on", sum(r["allin1_down"] < r["bassnet_down"] - 0.05 for r in both))


if __name__ == "__main__":
    main()
