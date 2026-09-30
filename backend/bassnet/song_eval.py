"""Five-dimension evaluation of the finished score (architecture v2, Phase 0).

The purchased tab is no longer "the answer" for every note, but it is still an excellent reference for the song's
STRUCTURE and for what a readable, playable bass score looks like. For each val/test song the production path
(cached v3+v4 posteriors -> pipeline.build_gp with sections, repeats, sparse sync) writes a GP, which is parsed back
like GP8 would read it, and scored on:

  1 fidelity     notes vs the audio-faithful re-aligned labels (labels_v2): onset+pitch F1; notation accuracy vs
                 the tab bar-by-bar (compare_bars) kept for continuity
  2 structure    bar lines (downbeat F1 at 70 ms vs the tab's bars), first bar line, meter agreement, number of
                 written tempos, key signature, section boundaries (F1 within 1 bar)
  3 readability  written tempos, sync points, notes carrying an accidental outside the key signature (per 100
                 notes), notes off the 16th/triplet grid, repeat blocks -- the tab's own values alongside
  4 playability  mean fret jump, share of hand-position shifts (> 4 frets), max fret span per bar -- tab alongside
  5 judgement    the written GPs are kept in the run folder for listening / A-B by the user

python -m bassnet.song_eval --run NAME [--key METHOD]      -> cache/bassnet/song_eval/NAME/{summary.json, *.gp}
python -m bassnet.song_eval --compare RUN_A RUN_B
"""
import argparse
import json
import os
import re
import sys
import zipfile
from fractions import Fraction

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.gpif_parser import parse_gp, q_to_sec  # noqa: E402

ROOT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet")
OUT = os.path.join(ROOT, "song_eval")
POST = {"val": ["val_v3", "val_v4"], "test": ["post_cache_v3", "post_cache_v4"]}
LABELS = os.path.join(ROOT, "labels_v2")


# ------------------------------------------------------------------ helpers on a GP file
def gp_text(gp):
    return zipfile.ZipFile(gp).read("Content/score.gpif").decode("utf8", "replace")


def written_tempos(text):
    return sorted({round(float(v)) for v in re.findall(
        r"<Automation>\s*<Type>Tempo</Type>.*?<Value>([\d.]+)", text, re.S)})


def n_sync(text):
    return len(re.findall(r"<Type>SyncPoint</Type>", text))


def n_repeats(text):
    return len(re.findall(r'<Repeat start="true"', text))


def bar_key_list(text):
    """Signature of every master bar (the <Key> element is repeated on each MasterBar)."""
    out, cur = [], 0
    for mb in re.findall(r"<MasterBar>(.*?)</MasterBar>", text, re.S):
        m = re.search(r"<AccidentalCount>(-?\d+)</AccidentalCount>", mb)
        cur = int(m.group(1)) if m else cur
        out.append(cur)
    return out


def key_sig(text):
    m = re.search(r"<AccidentalCount>(-?\d+)</AccidentalCount>", text)
    return int(m.group(1)) if m else 0


def readability(info, sig):
    from bassnet.spelling import tonic_name, NAT_PC, ACC_OFS, MAJOR_STEPS
    ns = [n for n in info.notes if not n.grace and not n.dead]
    tl, ta = tonic_name(sig)
    t = (NAT_PC[tl] + ACC_OFS[ta]) % 12
    diat = {(t + s) % 12 for s in MAJOR_STEPS}
    acc = sum(n.midi % 12 not in diat for n in ns)

    def off(x):
        x = Fraction(x).limit_denominator(96)
        return (x * 4).denominator != 1 and (x * 3).denominator != 1
    offgrid = sum(off(n.qpos) for n in ns)
    return {"accidentals_per_100": 100.0 * acc / max(1, len(ns)), "offgrid_per_100": 100.0 * offgrid / max(1, len(ns))}


def playability(info):
    ns = sorted((n for n in info.notes if not n.grace and not n.dead), key=lambda n: (n.qpos, n.string))
    fr = np.array([n.fret for n in ns], float)
    jumps = np.abs(np.diff(fr[fr > 0])) if (fr > 0).sum() > 1 else np.zeros(1)
    spans = {}
    for n in ns:
        if n.fret > 0:
            spans.setdefault((n.bar, n.occurrence), []).append(n.fret)
    span = [max(v) - min(v) for v in spans.values()] or [0]
    return {"fret_jump": float(jumps.mean()), "shift_share": float((jumps > 4).mean()),
            "max_span_p90": float(np.percentile(span, 90))}


def chord_fit(info, tmap, chords):
    """Share of bar-initial bass notes on the chord root (or its inversion bass) / on any chord tone."""
    from bassnet.song_doc import parse_chord, chord_pcs
    st = np.array([float(c["start_time"]) for c in chords])
    starts = {(b, oc): q0 for b, oc, q0, ql in info.bars}
    root = tone = n = 0
    for x in info.notes:
        if x.grace or x.dead or x.qpos != starts.get((x.bar, x.occurrence)):
            continue
        t = tmap(float(x.qpos)) + 0.05
        i = int(np.searchsorted(st, t)) - 1
        if i < 0 or chords[i]["chord"] in ("N", "X"):
            continue
        c = parse_chord(chords[i]["chord"])
        n += 1
        root += x.midi % 12 in (c.root, c.bass)
        tone += x.midi % 12 in chord_pcs(chords[i]["chord"])
    return {"bar_root": root / max(1, n), "bar_chordtone": tone / max(1, n)}


def f1_events(ref, est, tol):
    """Greedy one-to-one matching of sorted times; ref/est = [(t, key)], key must be equal (or None)."""
    ref = sorted(ref)
    used = [False] * len(ref)
    rt = np.array([r[0] for r in ref]) if ref else np.zeros(0)
    hit = 0
    for t, k in sorted(est):
        j = int(np.searchsorted(rt, t - tol))
        while j < len(ref) and rt[j] <= t + tol:
            if not used[j] and (k is None or ref[j][1] == k):
                used[j] = True
                hit += 1
                break
            j += 1
    return 2.0 * hit / max(1, len(ref) + len(est)), hit


def section_times(gp, info, qmap):
    """[(seconds, class)] of the section marks at their first playback."""
    from bassnet.section_model import gp_sections
    marks = gp_sections(gp)
    out = []
    for b, oc, q0, ql in info.bars:
        if oc == 0 and b in marks:
            out.append((qmap(float(q0)), marks[b]))
    return out


# ------------------------------------------------------------------ one song
def run_song(args):
    m, split, run_dir, keys = args
    from bassnet import pipeline
    from bassnet.decode import decode_notes, decode_beats
    from bassnet.notation_eval import fixed_q_map, gt_score_bars, score_bars, compare_bars
    name = os.path.basename(m["npz"])
    key = name[:-4]
    zs = [np.load(os.path.join(ROOT, "eval", d, name)) for d in POST[split]]
    fr, on, de, be, do = [np.mean([z[x].astype(np.float32) for z in zs], 0) for x in ("fr", "on", "de", "be", "do")]
    d = np.load(m["npz"])
    notes = decode_notes(fr, on, de)
    a1 = os.environ.get("SONG_EVAL_ALLIN1")      # e.g. "E:/.../allin1_ft:0.3" -> downbeat fusion as in production
    a_down, a_w = None, None
    if a1:
        dname, w = a1.rsplit(":", 1)
        a_down, a_w = np.load(os.path.join(dname, key + ".npz"))["downbeat"], float(w)
    hk = json.loads(os.environ["SONG_EVAL_HMM"]) if "SONG_EVAL_HMM" in os.environ else None
    red = float(os.environ["SONG_EVAL_RED"]) if "SONG_EVAL_RED" in os.environ else None
    if a1 or hk is not None or red is not None:
        beats, downs, meter, do = pipeline.song_beats(be, do, notes, a_down, red, hk, a_w)
    else:
        notes, beats, downs, meter, do = pipeline.decode_song(fr, on, de, be, do, d["cqt"].astype(np.float32))  # = app
    res = {"notes": notes, "beats": beats, "downbeats": downs, "meter": meter, "post": (fr, on, de, be, do, None),
           "cqt": d["cqt"].astype(np.float32), "mel": d["mel"].astype(np.float32)}
    title = os.path.basename(os.path.dirname(m["gp"]))
    out_gp = os.path.join(run_dir, f"{key}.gp")
    sig = keys.get(os.path.normcase(m["gp"]), (0, "Major"))
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        runs = None
        if os.environ.get("SONG_EVAL_LOCALKEY"):
            from bassnet import key_eval as K
            from bassnet.key_local import local_keys
            ff, cf = os.path.join(K.FEAT, key + ".npz"), os.path.join(ROOT, "chords", key + "_orig.json")
            if os.path.exists(ff) and os.path.exists(cf):
                runs = local_keys(K._chroma_norm(np.load(ff)["mix"]), json.load(open(cf)), sig[0])
        info_b = pipeline.build_gp(res, out_gp, title, "", "", None, key=tuple(sig), prefer_5=len(m["tuning"]) == 5,
                                   key_runs=runs)
    est = parse_gp(out_gp)
    gt = parse_gp(m["gp"])
    qm = fixed_q_map(m, gt)
    t0 = info_b["bar0_time"]         # written without audio: the file's timeline starts at the first bar line
    est_t = lambda q: q_to_sec(est.anchors, float(q)) + t0
    etext, gtext = gp_text(out_gp), gp_text(m["gp"])
    r = {"song": title, "split": split, "key": key}

    # 1 fidelity
    lab = json.load(open(os.path.join(LABELS, key + ".json"), encoding="utf8")) if os.path.exists(
        os.path.join(LABELS, key + ".json")) else m
    ref = [(n["time"], n["midi"]) for n in lab["notes"] if not n.get("grace") and not n.get("dead")]
    en = [(est_t(n.qpos), n.midi) for n in est.notes if not n.grace and not n.dead]
    r["note_f1"], _ = f1_events(ref, en, 0.05)
    r["onset_f1"], _ = f1_events([(t, None) for t, _ in ref], [(t, None) for t, _ in en], 0.05)
    cb = compare_bars(gt_score_bars(m, gt, qm), score_bars(est, qmap=est_t))
    r["notation_acc"], r["barline"] = cb["notation_note_acc"], cb["barline"]

    # 2 structure
    gbars = [qm(float(q0)) for b, oc, q0, ql in gt.bars]
    ebars = [est_t(q0) for b, oc, q0, ql in est.bars]
    gfirst = next((qm(float(n.qpos)) for n in sorted(gt.notes, key=lambda n: n.qpos)), gbars[0])
    gb_in = [t for t in gbars if t >= gfirst - 0.05]
    eb_in = [t for t in ebars if gb_in and gb_in[0] - 0.5 <= t <= gb_in[-1] + 0.5]
    r["downbeat_f1"], _ = f1_events([(t, None) for t, in zip(gb_in)], [(t, None) for t, in zip(eb_in)], 0.07)
    e1 = min(eb_in, key=lambda t: abs(t - gb_in[0])) if eb_in and gb_in else 1e9
    r["first_bar_ok"] = float(abs(e1 - gb_in[0]) < 0.07) if gb_in else 0.0
    glen = {round(t, 2): float(ql) for t, (b, oc, q0, ql) in zip(gbars, gt.bars)}
    ok = tot = 0
    for t, (b, oc, q0, ql) in zip(ebars, est.bars):
        j = min(glen, key=lambda x: abs(x - t)) if glen else None
        if j is not None and abs(j - t) < 0.07:
            tot += 1
            ok += abs(glen[j] - float(ql)) < 1e-6
    r["meter_agree"] = ok / max(1, tot)
    r["tempos_est"], r["tempos_tab"] = len(written_tempos(etext)), len(written_tempos(gtext))
    r["key_ok"] = float((key_sig(etext) - key_sig(gtext)) % 12 == 0)
    # key per bar (tabs change key inside 15% of songs): tab bar -> the est bar playing at that time
    gkeys, ekeys = bar_key_list(gtext), bar_key_list(etext)
    ebt = np.array([est_t(q0) for b, oc, q0, ql in est.bars])
    ok = n = 0
    for (b, oc, q0, ql) in gt.bars:
        t = qm(float(q0)) + 0.1
        j = int(np.searchsorted(ebt, t)) - 1
        if j < 0 or b >= len(gkeys):
            continue
        eb = est.bars[j][0]
        n += 1
        ok += (gkeys[b] - ekeys[min(eb, len(ekeys) - 1)]) % 12 == 0
    r["bar_key_ok"] = ok / max(1, n)
    r["multi_key_tab"] = float(len({k % 12 for k in gkeys}) > 1)
    gsec, esec = section_times(m["gp"], gt, qm), section_times(out_gp, est, est_t)
    bar_len = float(np.median(np.diff(gbars))) if len(gbars) > 2 else 2.0
    for tag, gp_, inf in (("est", out_gp, est), ("tab", m["gp"], gt)):
        from bassnet.section_model import gp_sections
        mk = gp_sections(gp_)
        st = [i for i, (b, oc, q0, ql) in enumerate(inf.bars) if oc == 0 and b in mk] + [len(inf.bars)]
        L = np.diff(st) if len(st) > 2 else np.zeros(0)
        if len(L):
            r[f"sec_mult4_{tag}"] = float(np.mean(L % 4 == 0))
            r[f"n_sections_{tag}"] = len(L)
    if len(gsec) >= 2:          # tabs without rehearsal marks don't count
        r["section_f1"], _ = f1_events([(t, None) for t, _ in gsec], [(t, None) for t, _ in esec], 0.6 * bar_len)

    # 3 readability (tab alongside)
    r.update({k + "_est": v for k, v in readability(est, key_sig(etext)).items()})
    r.update({k + "_tab": v for k, v in readability(gt, key_sig(gtext)).items()})
    r["sync_est"], r["sync_tab"] = n_sync(etext), n_sync(gtext)
    r["repeats_est"], r["repeats_tab"] = n_repeats(etext), n_repeats(gtext)
    # harmony: bass notes starting a bar on the chord's root / a chord tone (lv-chordia on the original recording)
    cf = os.path.join(ROOT, "chords", key + "_orig.json")
    if os.path.exists(cf):
        chords = json.load(open(cf))
        for tag, info, tmap in (("est", est, est_t), ("tab", gt, qm)):
            r.update({f"{k}_{tag}": v for k, v in chord_fit(info, tmap, chords).items()})
    # 4 playability
    r.update({k + "_est": v for k, v in playability(est).items()})
    r.update({k + "_tab": v for k, v in playability(gt).items()})
    return r


MEAN_KEYS = ["note_f1", "onset_f1", "notation_acc", "barline", "downbeat_f1", "first_bar_ok", "meter_agree", "key_ok", "bar_key_ok",
             "section_f1", "sec_mult4_est", "sec_mult4_tab", "n_sections_est", "n_sections_tab", "tempos_est", "tempos_tab", "accidentals_per_100_est", "accidentals_per_100_tab",
             "bar_root_est", "bar_root_tab", "bar_chordtone_est", "bar_chordtone_tab", "offgrid_per_100_est", "offgrid_per_100_tab", "sync_est", "sync_tab", "repeats_est", "repeats_tab",
             "fret_jump_est", "fret_jump_tab", "shift_share_est", "shift_share_tab", "max_span_p90_est",
             "max_span_p90_tab"]


def summarize(rows):
    s = {"n": len(rows)}
    for k in MEAN_KEYS:
        v = [r[k] for r in rows if k in r]
        s[k] = round(float(np.mean(v)), 4) if v else None
    mk = [r["bar_key_ok"] for r in rows if r.get("multi_key_tab")]
    s["bar_key_ok_multikey_songs"] = round(float(np.mean(mk)), 4) if mk else None
    s["one_tempo_share_est"] = round(float(np.mean([r["tempos_est"] <= 1 for r in rows])), 3)
    s["one_tempo_share_tab"] = round(float(np.mean([r["tempos_tab"] <= 1 for r in rows])), 3)
    return s


def load_keys(method):
    """{normcase(gp): (signature, mode)} from key_eval rows; 'tab' = oracle, 'none' = C major."""
    import pickle
    if method == "none":
        return {}
    rows = pickle.load(open(os.path.join(ROOT, "key_eval_rows.pkl"), "rb"))
    out = {}
    for r in rows:
        v = r["gt"] if method == "tab" else r.get(method)
        if v is not None:
            out[os.path.normcase(r["gp"])] = (int(v), "Major")
    # every tab of the same recording gets the same key
    from bassnet.build_stems import build_index
    idx = build_index()
    by_md5 = {idx[g]["md5"]: k for g, k in ((g, out.get(os.path.normcase(g))) for g in idx) if k is not None}
    return {os.path.normcase(g): by_md5[v["md5"]] for g, v in idx.items() if v["md5"] in by_md5}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run")
    ap.add_argument("--key", default="tab", help="key_eval method name, 'tab' (oracle) or 'none'")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--compare", nargs=2)
    a = ap.parse_args()
    if a.compare:
        A, B = [json.load(open(os.path.join(OUT, x, "summary.json"), encoding="utf8"))["summary"] for x in a.compare]
        for k in A:
            print(f"{k:26s} {A[k]!s:>10} {B.get(k)!s:>10}")
        return
    from multiprocessing import Pool
    from bassnet.train import load_metas, song_key, frozen_keys
    val, test = frozen_keys("val"), frozen_keys("test")
    run_dir = os.path.join(OUT, a.run)
    os.makedirs(run_dir, exist_ok=True)
    keys = load_keys(a.key)
    jobs, seen = [], set()
    for m in load_metas():
        k = song_key(m["gp"])
        split = "val" if k in val else "test" if k in test else None
        name = os.path.basename(m["npz"])
        if split is None or name in seen:
            continue
        if not all(os.path.exists(os.path.join(ROOT, "eval", d, name)) for d in POST[split]):
            continue
        seen.add(name)
        jobs.append((m, split, run_dir, keys))
    if a.limit:
        jobs = jobs[:a.limit]
    rows = []
    with Pool(a.workers) as p:
        for r in p.imap_unordered(_safe, jobs):
            if r:
                rows.append(r)
                print(f"{r['song'][:30]:30s} note {r['note_f1']:.3f} db {r['downbeat_f1']:.3f} "
                      f"tempos {r['tempos_est']}/{r['tempos_tab']} key {r['key_ok']:.0f} sec {r.get('section_f1', -1):.2f}",
                      flush=True)
    s = summarize(rows)
    json.dump({"summary": s, "songs": rows, "key": a.key}, open(os.path.join(run_dir, "summary.json"), "w",
                                                                  encoding="utf8"), ensure_ascii=False, indent=1)
    for k, v in s.items():
        print(f"{k:26s} {v}")


def _safe(job):
    try:
        return run_song(job)
    except Exception as e:
        import traceback
        print("fail", job[0]["gp"], e, traceback.format_exc()[-600:], flush=True)
        return None


if __name__ == "__main__":
    main()
