"""How sure is a test-set score? Bootstrap over songs (the songs are the sample, not the notes).

Notes inside one song are not independent (same mix, same separator errors, same riff 20 times), so the error bar
comes from resampling whole songs. Three questions:

  one run      mean of each per-song metric with a 95 % interval; note F1 also pooled over all notes (micro) when the
               rows carry the counts (song_eval writes note_hit / n_ref / n_est)
  A vs B       paired bootstrap of B - A on the songs both runs share: interval of the difference, share of
               resamples with B > A, songs up / down and the exact sign-test p
  seeds        the same recipe trained with different seeds: spread of the run means (training noise, which the
               song bootstrap does not see; a gain should clear both)

python -m bassnet.score_ci RUN                       (RUN = song_eval run name or a summary.json path)
python -m bassnet.score_ci RUN_A RUN_B [--keys note_f1 barline] [--split test]
python -m bassnet.score_ci --seeds RUN1 RUN2 RUN3
"""
import argparse
import json
import math
import os

import numpy as np

SONG_EVAL = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "song_eval")
KEYS = ["note_f1", "onset_f1", "notation_acc", "barline", "downbeat_f1", "first_bar_ok", "bar_key_ok", "section_f1"]
B = 10000


def load_rows(run, split=None):
    f = run if run.endswith(".json") else os.path.join(SONG_EVAL, run, "summary.json")
    d = json.load(open(f, encoding="utf8"))
    rows = d["songs"] if isinstance(d, dict) else d
    return [r for r in rows if split is None or r.get("split") == split]


def song_id(r):
    return f"{r.get('split', '')}|{r['song']}"


def boot_idx(n, b=B, seed=0):
    return np.random.default_rng(seed).integers(0, n, size=(b, n))


def interval(samples, level=0.95):
    a = (1 - level) / 2
    return float(np.quantile(samples, a)), float(np.quantile(samples, 1 - a))


def micro_f1(rows, idx):
    """Pooled note F1 per resample: 2 * hits / (ref + est) over the drawn songs."""
    hit = np.array([r["note_hit"] for r in rows], float)
    tot = np.array([r["n_ref"] + r["n_est"] for r in rows], float)
    return 2 * hit[idx].sum(1) / np.maximum(1, tot[idx].sum(1))


def sign_test(up, down):
    """Two-sided exact binomial p for up vs down (ties dropped)."""
    n = up + down
    if n == 0:
        return 1.0
    k = min(up, down)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def one_run(rows, keys):
    idx = boot_idx(len(rows))
    out = {}
    for k in keys:
        v = np.array([r[k] for r in rows if k in r], float)
        if len(v) < 2:
            continue
        s = v[boot_idx(len(v))].mean(1)
        out[k] = {"n": len(v), "mean": float(v.mean()), "ci95": interval(s), "se": float(s.std()),
                  "song_sd": float(v.std(ddof=1))}
    if rows and all("note_hit" in r for r in rows):
        full = micro_f1(rows, np.arange(len(rows))[None])[0]
        s = micro_f1(rows, idx)
        out["note_f1_micro"] = {"n": len(rows), "mean": float(full), "ci95": interval(s), "se": float(s.std())}
    return out


def paired(rows_a, rows_b, keys):
    a = {song_id(r): r for r in rows_a}
    b = {song_id(r): r for r in rows_b}
    common = sorted(set(a) & set(b))
    out = {"songs_a": len(a), "songs_b": len(b), "shared": len(common)}
    for k in keys:
        ids = [s for s in common if k in a[s] and k in b[s]]
        if len(ids) < 2:
            continue
        d = np.array([b[s][k] - a[s][k] for s in ids], float)
        s = d[boot_idx(len(d))].mean(1)
        up, down = int((d > 1e-9).sum()), int((d < -1e-9).sum())
        out[k] = {"n": len(d), "a": float(np.mean([a[s][k] for s in ids])), "b": float(np.mean([b[s][k] for s in ids])),
                  "diff": float(d.mean()), "ci95": interval(s), "p_b_better": float((s > 0).mean()),
                  "up": up, "down": down, "sign_p": sign_test(up, down)}
    ids = [s for s in common if "note_hit" in a[s] and "note_hit" in b[s]]
    if len(ids) >= 2:
        ra, rb = [a[s] for s in ids], [b[s] for s in ids]
        idx = boot_idx(len(ids))
        s = micro_f1(rb, idx) - micro_f1(ra, idx)
        full = np.arange(len(ids))[None]
        out["note_f1_micro"] = {"n": len(ids), "a": float(micro_f1(ra, full)[0]), "b": float(micro_f1(rb, full)[0]),
                                "diff": float(micro_f1(rb, full)[0] - micro_f1(ra, full)[0]), "ci95": interval(s),
                                "p_b_better": float((s > 0).mean())}
    return out


def seeds(runs, keys, split=None):
    out = {}
    for k in keys:
        m = [np.mean([r[k] for r in load_rows(x, split) if k in r]) for x in runs]
        out[k] = {"runs": len(m), "mean": float(np.mean(m)), "seed_sd": float(np.std(m, ddof=1)) if len(m) > 1 else 0.0,
                  "means": [round(float(x), 4) for x in m]}
    return out


def _pct(x):
    return f"{100 * x:6.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--keys", nargs="+", default=KEYS)
    ap.add_argument("--split", default=None, help="val / test (default: all rows)")
    ap.add_argument("--seeds", nargs="+")
    a = ap.parse_args()
    if a.seeds:
        for k, v in seeds(a.seeds, a.keys, a.split).items():
            print(f"{k:16s} {_pct(v['mean'])} ± {_pct(v['seed_sd'])} (seed sd, {v['runs']} runs)  {v['means']}")
        return
    if len(a.runs) == 1:
        for k, v in one_run(load_rows(a.runs[0], a.split), a.keys + ["note_f1_micro"]).items():
            lo, hi = v["ci95"]
            print(f"{k:16s} {_pct(v['mean'])}  95% [{_pct(lo)}, {_pct(hi)}]  se {_pct(v['se'])}  n={v['n']}")
        return
    if len(a.runs) == 2:
        res = paired(load_rows(a.runs[0], a.split), load_rows(a.runs[1], a.split), a.keys)
        print(f"songs A {res['songs_a']}  B {res['songs_b']}  shared {res['shared']}")
        for k, v in res.items():
            if not isinstance(v, dict):
                continue
            lo, hi = v["ci95"]
            extra = f"  up {v['up']} down {v['down']} sign p {v['sign_p']:.3f}" if "up" in v else ""
            print(f"{k:16s} {_pct(v['a'])} -> {_pct(v['b'])}  diff {_pct(v['diff'])}  95% [{_pct(lo)}, {_pct(hi)}]"
                  f"  P(B>A) {v['p_b_better']:.3f}{extra}")
        return
    ap.error("one run, two runs, or --seeds")


if __name__ == "__main__":
    main()
