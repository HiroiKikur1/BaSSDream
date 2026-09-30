"""Before/after regularize(): written bars with repeat signs, distinct bar spellings, resynthesis similarity to the
stem, and (secondary) agreement with the purchased tab.

python -m bassnet.lab.regularize_eval --split val --tau 0.02 0.05 0.1
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main():
    import librosa
    from bassnet import eval_cached
    from bassnet.decode import decode_notes, decode_beats
    from bassnet.quantize import quantize, beat_time, TPB
    from bassnet.fretboard import assign_frets
    from bassnet.gp_writer import write_gp, build_bar_events, _bar_signature, find_repeats, _saved
    from bassnet.gpif_parser import parse_gp
    from bassnet.notation_eval import gt_score_bars, score_bars, compare_bars
    from bassnet.regularize import regularize
    from bassnet.sep_ceiling import render_bass
    from bassnet.resynth_compare import features, frame_sim
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--tau", type=float, nargs="+", default=[0.05])
    ap.add_argument("--sim", type=float, default=0.7)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    C = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "eval")
    dirs = ("val_v3", "val_v4") if a.split == "val" else ("post_cache_v3", "post_cache_v4")
    eval_cached.SPLIT = a.split
    tmp = os.path.join(C, "_reg.gp")
    agg = {}
    metas = eval_cached.metas()[:a.limit] if a.limit else eval_cached.metas()
    for m in metas:
        fr, on, de, be, do = eval_cached.load_post(os.path.basename(m["npz"]), [os.path.join(C, d) for d in dirs])
        notes = decode_notes(fr, on, de)
        beats, downs, meter = decode_beats(be, do)
        qs0 = quantize(notes, beats, downs, meter)
        stem, _ = librosa.load(m["stem"], sr=22050, mono=True)
        Fs = features(stem)
        gt = gt_score_bars(m)
        for tau in [None] + a.tau:
            qs, st = (qs0, {}) if tau is None else regularize(qs0, fr, on, sim=a.sim, tau=tau)
            assign_frets(qs.notes, m["tuning"], times=[beat_time(qs.beats, qs.bar0 + n.tick / TPB) for n in qs.notes])
            ev = build_bar_events(qs)
            sigs = [_bar_signature(e, qs.meter_of(b), qs.bar_compound[b]) for b, e in enumerate(ev)]
            blocks = find_repeats(sigs)
            written = qs.n_bars - sum(_saved(b_) for b_ in blocks)
            distinct = len(set(s for s in sigs if any(x[3] is not None for x in s[2])))
            played = sum(1 for s in sigs if any(x[3] is not None for x in s[2]))
            write_gp(qs, tmp, "e", "e", None, m["tuning"])
            info = parse_gp(tmp)
            rn = [{"time": n.time + qs.bar_time(0), "end": n.end + qs.bar_time(0), "midi": n.midi, "dead": n.dead}
                  for n in info.notes]
            r = render_bass(rn, len(stem), np.random.default_rng(1), sr=22050)
            Fm = features(r)
            sim_, act = frame_sim(Fs, Fm, np.percentile(Fs.sum(0), 30), np.percentile(Fm.sum(0), 30))
            cb = compare_bars(gt, score_bars(info, qs.bar_time(0)))
            row = {"written_ratio": written / qs.n_bars, "distinct_ratio": distinct / max(1, played),
                   "resynth": float(sim_[act].mean()), "vs_tab": cb["notation_note_acc"],
                   "changed_notes": st.get("changed_notes", 0) / max(1, len(qs0.notes))}
            agg.setdefault(tau, []).append(row)
        print(os.path.basename(os.path.dirname(m["gp"]))[:30], "done", flush=True)
    for tau, rows in agg.items():
        print(f"tau={tau}", {k: round(float(np.mean([r[k] for r in rows])), 4) for k in rows[0]})


if __name__ == "__main__":
    main()
