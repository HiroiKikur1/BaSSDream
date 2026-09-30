"""Cache test-set posteriors once, then evaluate decode/quantise variants quickly on CPU.

python -m bassnet.eval_cached --build [--models GLOB]     # compute posteriors (GPU)
python -m bassnet.eval_cached --variant NAME              # evaluate a named variant
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths  # noqa: E402
from bassnet.eval_e2e import test_metas, event_f1  # noqa: E402
from bassnet.decode import decode_notes, decode_beats, note_metrics  # noqa: E402
from bassnet.quantize import quantize, beat_time, TPB  # noqa: E402
from bassnet.fretboard import assign_frets  # noqa: E402
from bassnet.gp_writer import write_gp  # noqa: E402
from bassnet.gpif_parser import parse_gp  # noqa: E402
from bassnet.score_eval import compare_scores  # noqa: E402

CACHE = paths.cache(r"bassnet\eval\post_cache")


def build(models_glob):
    global CACHE
    import torch
    from bassnet import pipeline
    from bassnet.model import load_checkpoint
    os.makedirs(CACHE, exist_ok=True)
    pipeline.model_paths = lambda: sorted(glob.glob(models_glob))
    pipeline.model_roles = lambda: None
    for m in metas():
        d = np.load(m["npz"])
        from bassnet.train import load_mert
        mert = load_mert(m, d["cqt"].shape[1])
        outs = pipeline.posteriors(d["cqt"].astype(np.float32), d["mel"].astype(np.float32), "cuda", with_tech=True,
                                   mert=mert)
        extra = {"te": outs[5].astype(np.float16)} if outs[5] is not None else {}
        np.savez_compressed(os.path.join(CACHE, os.path.basename(m["npz"])), fr=outs[0].astype(np.float16),
                            on=outs[1], de=outs[2], be=outs[3], do=outs[4], **extra)
        print("cached", os.path.basename(m["npz"]), flush=True)


VARIANTS = {
    "base": {},
    "legato2": {"legato_steps": 2},
    "legato3": {"legato_steps": 3},
    "extend_to_next": {"legato_steps": 99},
    "end6": {"dec": {"end_thr": 0.6}},
    "end7": {"dec": {"end_thr": 0.7}},
    "end8": {"dec": {"end_thr": 0.8}},
    "end4": {"dec": {"end_thr": 0.4}},
    "hmm": {"beat": {"down_mode": "hmm"}},
    "meter_bar": {"q": {"meter_mode": "bar"}},
    "bt": {"ext": "bt"},
    "bt_hmm": {"ext": "bt_hmm"},
    **{f"jump{j}": {"beat": {"jump": float(j)}} for j in (2, 3, 5, 8, 20)},
    **{f"tight{t}": {"beat": {"tight": float(t)}} for t in (30, 120)},
    **{f"bias{int(b * 100)}": {"beat": {"bias": b}} for b in (0.15, 0.35)},
    "meter_simple": {"q": {"meter_mode": "simple"}},
    **{f"sf{int(f * 100)}": {"beat": {"strong_frac": f}} for f in (0.7, 0.85, 0.9, 0.95, 1.0)},
    "hmm_j3": {"beat": {"down_mode": "hmm", "hmm_kw": {"p_jump": 3.0}}},
    "hmm_j10": {"beat": {"down_mode": "hmm", "hmm_kw": {"p_jump": 10.0}}},
    "hmm_m2": {"beat": {"down_mode": "hmm", "hmm_kw": {"p_meter": 2.0}}},
    "hmm_m8": {"beat": {"down_mode": "hmm", "hmm_kw": {"p_meter": 8.0}}},
    "hmm_w2": {"beat": {"down_mode": "hmm", "hmm_kw": {"w": 2.0}}},
    "hmm_342": {"beat": {"down_mode": "hmm", "hmm_kw": {"meters": (4, 3, 2)}}},
    **{f"rep{int(r * 100)}": {"dec": {"rep_thr": r}} for r in (0.6, 0.7, 0.8, 0.9, 0.95)},
}

def external_beats(m):
    f = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "eval", "beatthis", os.path.basename(m["npz"])[:-4] + ".json")
    if not os.path.exists(f):
        return None
    d = json.load(open(f))
    b, dn = np.array(d["beats"]), np.array(d["downbeats"])
    idx = [int(np.argmin(np.abs(b - x))) for x in dn]
    meter = int(np.bincount(np.diff(idx)).argmax()) if len(idx) > 1 else 4
    return b, dn, meter


def rephase_downbeats(beats, down_prob):
    from bassnet.decode import downbeats_hmm, FPS
    beats = np.asarray(beats)
    fi = np.clip(np.round(beats * FPS).astype(int), 0, len(down_prob) - 1)
    dv = np.array([down_prob[max(0, i - 2):i + 3].max() for i in fi])
    idx, meter = downbeats_hmm(dv)
    return beats, beats[np.array(idx)], meter


SPLIT = "test"
PER_SONG = False
GT_BARS: dict = {}


def metas():
    if SPLIT == "val":
        from bassnet.tune_decode import val_metas
        return val_metas()
    return test_metas()


def load_post(name, caches):
    acc = None
    for c in caches:
        d = np.load(os.path.join(c, name))
        arrs = [d["fr"].astype(np.float32), d["on"], d["de"], d["be"], d["do"]]
        acc = arrs if acc is None else [a + b for a, b in zip(acc, arrs)]
    return [a / len(caches) for a in acc]


def run(variant, caches=None, beat_caches=None, down_caches=None):
    global CACHE
    caches = caches or [CACHE]
    kw = VARIANTS[variant]
    rows = []
    for m in metas():
        name = os.path.basename(m["npz"])
        if not all(os.path.exists(os.path.join(c, name)) for c in caches):
            continue
        cp = os.path.join(caches[0], name)
        fr, on, de, be, do = load_post(name, caches)
        if beat_caches:
            _, _, _, be, do = load_post(name, beat_caches)
        if down_caches:
            do = load_post(name, down_caches)[4]
        notes = decode_notes(fr, on, de, **kw.get("dec", {}))
        beats, downs, meter = decode_beats(be, do, **kw.get("beat", {}))
        if kw.get("ext"):
            ext = external_beats(m)
            if ext is not None:
                beats, downs, meter = ext if kw["ext"] == "bt" else rephase_downbeats(ext[0], do)
        qs = quantize(notes, beats, downs, meter, legato_steps=kw.get("legato_steps", 1), **kw.get("q", {}))
        tuning = m["tuning"]
        assign_frets(qs.notes, tuning, times=[beat_time(qs.beats, qs.bar0 + n.tick / TPB) for n in qs.notes])
        out = cp.replace(".npz", f"_{variant}.gp")
        write_gp(qs, out, "e", "e", None, tuning)
        sc = compare_scores(m, parse_gp(out), audio_offset=qs.bar_time(0))
        from bassnet.notation_eval import score_bars, compare_bars, gt_score_bars
        if m["gp"] not in GT_BARS:
            GT_BARS[m["gp"]] = gt_score_bars(m)
        gt_bars = GT_BARS[m["gp"]]
        sc.update(compare_bars(gt_bars, score_bars(parse_gp(out), qs.bar_time(0))))
        sc["name"] = os.path.basename(os.path.dirname(m["gp"]))[:30]
        sc["beatF1"] = event_f1(m.get("beats", []), beats)
        sc["downF1"] = event_f1(m.get("downbeats", []), downs)
        rows.append(sc)
    agg = {k: round(float(np.mean([r[k] for r in rows])), 4)
           for k in ("pitch_acc", "onset_recall", "dur_acc", "note_acc", "extra", "beatF1", "downF1")}
    nagg = {k: round(float(np.mean([r[k] for r in rows])), 4)
            for k in ("barline", "meter_acc", "bar_exact", "bar_rhythm", "notation_note_acc")}
    print(variant, "NOTATION", nagg, flush=True)
    if PER_SONG:
        for r in sorted(rows, key=lambda r: r["bar_exact"]):
            print(f"   {r['name']:32s} exact={r['bar_exact']:.2f} rhythm={r['bar_rhythm']:.2f} meter={r['meter_acc']:.2f} "
                  f"barline={r['barline']:.2f} pitch={r['pitch_acc']:.2f}")
    print(variant, len(rows), agg, flush=True)
    return agg


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--models", default=paths.cache(r"bassnet\bassnet.pt"))
    ap.add_argument("--variant", nargs="*", default=["base"])
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--combine", nargs="*", help="average posteriors from several cache dirs")
    ap.add_argument("--split", default="test", choices=["test", "val"])
    ap.add_argument("--per-song", action="store_true")
    ap.add_argument("--downs", nargs="*", help="take downbeat posteriors from these cache dirs")
    ap.add_argument("--beats", nargs="*", help="take beat/downbeat posteriors from these cache dirs instead")
    a = ap.parse_args()
    CACHE = a.cache
    SPLIT = a.split
    PER_SONG = a.per_song
    if a.build:
        build(a.models)
    else:
        for v in a.variant:
            run(v, a.combine, a.beats, a.downs)
