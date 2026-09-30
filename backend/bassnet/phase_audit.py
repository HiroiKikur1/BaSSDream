"""Where do our bar lines go wrong? Per song, the phase of every tab bar line against our decoded bars.

For each tab bar line that falls on one of our beats: the distance (in beats) to our previous bar line, 0 = right.
Songs are sorted into: ok, whole-song shift, wrong start then corrected (our odd bar fixes it), wrong middle
stretches, meter mismatch. Also the library facts the phase solver can lean on: how often the first bass note
of a tab is on beat 1, and how many tabs contain an odd bar at all.

python -m bassnet.phase_audit [--allin1 DIR:W]
"""
import argparse
import collections
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")


def song_inputs():
    """[(meta, split, (fr, on, de, be, do))] with honest posteriors (cross-fold for training songs)."""
    from bassnet.train import load_metas, song_key, frozen_keys
    val, test = frozen_keys("val"), frozen_keys("test")
    E = os.path.join(ROOT, "eval")
    seen, out = set(), []
    for m in load_metas():
        key = os.path.basename(m["npz"])[:-4]
        if key in seen:
            continue
        k = song_key(m["gp"])
        split = "val" if k in val else "test" if k in test else "train"
        dirs = {"val": ["val_v3", "val_v4"], "test": ["post_cache_v3", "post_cache_v4"], "train": ["xfold"]}[split]
        if not all(os.path.exists(os.path.join(E, d, key + ".npz")) for d in dirs):
            continue
        seen.add(key)
        zs = [np.load(os.path.join(E, d, key + ".npz")) for d in dirs]
        post = [np.mean([z[x].astype(np.float32) for z in zs], 0) for x in ("fr", "on", "de", "be", "do")]
        out.append((m, split, post))
    return out


def tab_grid(m, split):
    from bassnet.gpif_parser import parse_gp
    from bassnet.notation_eval import fixed_q_map, label_q_map
    info = parse_gp(m["gp"])
    qm = (fixed_q_map(m, info) if split != "train" else None) or label_q_map(m)
    bars = [(qm(float(q0)), float(ql)) for b, oc, q0, ql in info.bars]
    first = min((n.qpos for n in info.notes if not n.grace), default=None)
    return info, qm, bars, first


def phase_string(beats, downs, tab_bars, tol=0.07):
    bt = np.asarray(beats)
    di = set(int(np.argmin(np.abs(bt - d))) for d in downs if len(bt) and np.abs(bt - d).min() < tol)
    s = []
    for t, _ in tab_bars:
        i = int(np.argmin(np.abs(bt - t)))
        if abs(bt[i] - t) > tol:
            s.append("?")
            continue
        k = 0
        while i - k >= 0 and (i - k) not in di and k < 9:
            k += 1
        s.append(str(k) if i - k >= 0 and k < 9 else "-")
    return "".join(s)


def classify(ps):
    core = [c for c in ps if c not in "?-"]
    if len(core) < 8:
        return "unknown"
    wrong = [c != "0" for c in core]
    if not any(wrong):
        return "ok"
    if all(wrong):
        return "whole-song shift"
    n = len(core)
    first_ok = not wrong[0]
    runs = sum(1 for i in range(1, n) if wrong[i] != wrong[i - 1])
    if not first_ok and runs == 1:
        return "wrong start, corrected later"
    if first_ok and np.mean(wrong) < 0.15:
        return "mostly ok, short wrong stretches"
    return "wrong middle stretches"


def main():
    from bassnet.decode import decode_beats
    ap = argparse.ArgumentParser()
    ap.add_argument("--allin1", default="")
    ap.add_argument("--hmm", default="{}")
    ap.add_argument("--show", type=int, default=12)
    ap.add_argument("--ctx", action="store_true", help="contextual downbeat model (bassnet/downbeat_ctx.py)")
    ap.add_argument("--only", default="", help="substring of song names to show")
    ap.add_argument("--dump", default="", help="per-song phase strings -> json")
    ap.add_argument("--prod", action="store_true", help="exactly the production beat decoding (pipeline.song_beats)")
    ap.add_argument("--red", type=float, default=0.0, help="repeat down-weighting exponent (decode.repeat_weights)")
    a = ap.parse_args()
    hk = json.loads(a.hmm)
    cls = collections.Counter()
    first_on_one = odd_tabs = n_tabs = 0
    lines = []
    dump = []
    for m, split, (fr, on, de, be, do) in song_inputs():
        try:
            info, qm, bars, first = tab_grid(m, split)
        except Exception:
            continue
        n_tabs += 1
        lens = [ql for _, ql in bars]
        main_len = collections.Counter(lens).most_common(1)[0][0]
        odd_tabs += any(l != main_len for l in lens[1:-1])
        if first is not None:
            q0s = [float(q0) for b, oc, q0, ql in info.bars]
            j = int(np.searchsorted(q0s, float(first), side="right")) - 1
            first_on_one += abs(float(first) - q0s[max(j, 0)]) < 1e-6
        if a.allin1:
            from bassnet.pipeline import fuse_downbeat
            d, w = a.allin1.rsplit(":", 1)
            f = os.path.join(d, os.path.basename(m["npz"])[:-4] + ".npz")
            if os.path.exists(f):
                do = fuse_downbeat(do, np.load(f)["downbeat"], float(w))
        down_fn = None
        if a.ctx:
            from bassnet.downbeat_ctx import rescore
            from bassnet.decode import decode_notes
            from bassnet.train import fold_of
            notes = decode_notes(fr, on, de)
            cf = os.path.join(ROOT, "chords", os.path.basename(m["npz"])[:-4] + "_orig.json")
            chords = json.load(open(cf)) if os.path.exists(cf) else []
            which = fold_of(m["gp"]) if split == "train" else "all"     # never a model that saw this song
            down_fn = lambda b, do=do, on=on, notes=notes, chords=chords, w=which: rescore(b, do, on, notes, chords, w)
        extra = {}
        if a.red:
            from bassnet.decode import decode_notes
            extra = {"notes": decode_notes(fr, on, de), "redundancy": a.red}
        if a.prod:
            from bassnet import pipeline
            from bassnet.decode import decode_notes
            beats, downs, meter, _ = pipeline.song_beats(be, do, decode_notes(fr, on, de))
        else:
            beats, downs, meter = decode_beats(be, do, hmm_kw=hk or None, down_fn=down_fn, **extra)
        t0 = qm(float(first)) - 0.1 if first is not None else -1
        ps = phase_string(beats, downs, [b for b in bars if b[0] >= t0])
        c = classify(ps)
        cls[(split, c)] += 1
        cls[("all", c)] += 1
        cls[(split, "_bars")] += sum(ch != "?" for ch in ps)
        cls[(split, "_right")] += ps.count("0")
        cls[("all", "_bars")] += sum(ch != "?" for ch in ps)
        cls[("all", "_right")] += ps.count("0")
        dump.append({"name": os.path.basename(os.path.dirname(m["gp"]))[:40], "split": split, "ps": ps, "cls": c,
                     "meter": int(meter), "tab_lens": sorted(set(lens))})
        if a.only and a.only in m["gp"]:
            print("  ", os.path.basename(os.path.dirname(m["gp"]))[:30], ps)
        if c not in ("ok", "unknown"):
            lines.append((split, os.path.basename(os.path.dirname(m["gp"]))[:30], c, ps[:90]))
    print(f"tabs {n_tabs}: first bass note on beat 1: {first_on_one / max(1, n_tabs):.3f}; tabs with an odd bar "
          f"(not first/last): {odd_tabs / max(1, n_tabs):.3f}")
    for split in ("all", "train", "val", "test"):
        tot = sum(v for (s, c), v in cls.items() if s == split)
        tot = sum(v for (s, c), v in cls.items() if s == split and not c.startswith("_"))
        print(split, tot, {c: v for (s, c), v in sorted(cls.items()) if s == split and not c.startswith("_")},
              f"bar lines right {cls[(split, '_right')] / max(1, cls[(split, '_bars')]):.3f}")
    for x in lines[:a.show]:
        print(*x)
    if a.dump:
        json.dump(dump, open(a.dump, "w", encoding="utf8"), ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
