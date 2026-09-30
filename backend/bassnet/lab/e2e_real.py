"""Real end-to-end check: original mix -> ai_transcriber CLI (subprocess, as WPF runs it) -> .gp -> compare with the original tab.

Usage: python -m bassnet.lab.e2e_real [--limit N] [--names substr1,substr2] [--out DIR]
Scores the written GP after parsing it back (audio time via its own SyncPoints), so separation, sync and
packaging are all covered. Stems are cached per song in DIR/<md5>/ (delete to re-separate).
"""
import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import paths  # noqa: E402
from bassnet.eval_e2e import test_metas, event_f1  # noqa: E402
from bassnet.gpif_parser import parse_gp, extract_audio  # noqa: E402
from bassnet.score_eval import compare_notes  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_OUT = paths.cache(r"bassnet\e2e")


def run_cli(audio, out_dir, title, five):
    cmd = [sys.executable, "-u", os.path.join(BACKEND, "ai_transcriber.py"), "--title", title,
           "--audio", audio, "--output", out_dir]
    if five:
        cmd.append("--5string")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    log = p.stdout + "\n" + p.stderr
    open(os.path.join(out_dir, "cli.log"), "w", encoding="utf-8").write(log)
    progress = [l.split(":", 1)[1].strip() for l in p.stdout.splitlines() if l.startswith("__PROGRESS__")]
    res = None
    if "__JSON_RESULT_START__" in p.stdout:
        blob = p.stdout.split("__JSON_RESULT_START__", 1)[1].split("__JSON_RESULT_END__", 1)[0]
        res = json.loads(blob.strip())
    return p.returncode, progress, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--names", default="")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    metas = test_metas()
    if a.names:
        keys = a.names.split(",")
        metas = [m for m in metas if any(k in m["gp"] for k in keys)]
    if a.limit:
        metas = metas[:a.limit]
    rows = []
    for m in metas:
        md5 = os.path.basename(m["npz"])[:-4]
        d = os.path.join(a.out, md5)
        os.makedirs(d, exist_ok=True)
        info = parse_gp(m["gp"])
        mix = os.path.join(d, "mix" + os.path.splitext(info.audio_asset or ".mp3")[1])
        if not os.path.exists(mix) and not extract_audio(m["gp"], info, mix):
            print("no audio", md5)
            continue
        t = time.time()
        rc, prog, res = run_cli(mix, d, md5, m.get("tuning") == 5)
        el = time.time() - t
        if not res or res.get("status") != "success":
            print(f"{md5} FAILED rc={rc} progress={prog}", flush=True)
            rows.append({"md5": md5, "failed": True})
            continue
        est = parse_gp(res["gp_path"])
        est_notes = [{"time": n.time, "end": n.end, "midi": n.midi, "dead": n.dead} for n in est.notes if not n.grace]
        gt = sorted([n for n in m["notes"] if not n.get("grace")], key=lambda n: n["time"])
        sc = compare_notes(gt, sorted(est_notes, key=lambda n: n["time"]), np.array(m["beats"]))
        # residual global offset of the written GP (sync sanity): median of matched onset deltas
        gt_t = np.array([n["time"] for n in gt if not n.get("dead")])
        deltas = []
        for e in est_notes:
            j = np.searchsorted(gt_t, e["time"])
            c = [k for k in (j - 1, j) if 0 <= k < len(gt_t)]
            if c:
                k = min(c, key=lambda k: abs(gt_t[k] - e["time"]))
                if abs(gt_t[k] - e["time"]) < 0.1:
                    deltas.append(e["time"] - gt_t[k])
        sc["offset_ms"] = float(np.median(deltas) * 1000) if deltas else None
        from bassnet.notation_eval import score_bars, compare_bars, gt_score_bars
        from bassnet.gp_lint import lint
        sc.update(compare_bars(gt_score_bars(m, info), score_bars(est)))
        sc["lint_problems"] = len(lint(res["gp_path"]))
        sc["engine"] = res.get("engine")
        sc["sec"] = el
        sc["engine_ok"] = res.get("engine") == "bassnet"
        name = os.path.basename(os.path.dirname(m["gp"]))[:30]
        print(f"{name:32s} [{res.get('engine')}] pitch={sc['pitch_acc']:.3f} onsetR={sc['onset_recall']:.3f} "
              f"dur={sc['dur_acc']:.3f} note={sc['note_acc']:.3f} extra={sc['extra']:.3f} "
              f"off={sc['offset_ms'] or 0:+.0f}ms tuning={res.get('is_5string')} ({el:.0f}s) prog={len(prog)}", flush=True)
        print(f"{'':32s} NOTATION bar_exact={sc['bar_exact']:.3f} rhythm={sc['bar_rhythm']:.3f} meter={sc['meter_acc']:.3f} "
              f"barline={sc['barline']:.3f} note={sc['notation_note_acc']:.3f} lint={sc['lint_problems']}", flush=True)
        rows.append({"md5": md5, "name": name, "gp": sc, "progress": prog, "result": res})
    ok = [r for r in rows if not r.get("failed")]
    if ok:
        agg = {k: float(np.mean([r["gp"][k] for r in ok])) for k in ("pitch_acc", "onset_recall", "dur_acc", "note_acc", "extra",
                                                                     "bar_exact", "bar_rhythm", "meter_acc", "barline", "notation_note_acc")}
        agg["failed"] = len(rows) - len(ok)
        print("MEAN", json.dumps({k: round(v, 4) for k, v in agg.items()}))
        json.dump({"agg": agg, "rows": rows}, open(os.path.join(a.out, f"results{a.tag}.json"), "w", encoding="utf8"),
                  ensure_ascii=False, indent=1, default=float)


if __name__ == "__main__":
    main()
