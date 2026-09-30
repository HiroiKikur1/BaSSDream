"""Listening checklist: where model and tab disagree on pitch, with the audio referee's verdict.
python -m bassnet.audit_list OUT.md KEY [KEY ...]"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet import eval_cached  # noqa: E402
from bassnet.audio_verify import semitone_map, d_here  # noqa: E402
from bassnet.decode import decode_notes  # noqa: E402
from bassnet.gpif_parser import parse_gp  # noqa: E402

NAMES = "C C# D D# E F F# G G# A A# B".split()


def nm(p):
    return f"{NAMES[p % 12]}{p // 12 - 1}"


def main():
    out, keys = sys.argv[1], sys.argv[2:]
    C = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "eval")
    lines = ["# 音高分歧抽查清单", "",
             "打开原谱（曲谱文件夹里的原版 GP），跳到对应小节听录音，判断哪个音高是对的。", ""]
    for split, dirs in (("val", ("val_v3", "val_v4")), ("test", ("post_cache_v3", "post_cache_v4"))):
        eval_cached.SPLIT = split
        for m in eval_cached.metas():
            if not any(k in m["gp"] for k in keys):
                continue
            fr, on, de, be, do = eval_cached.load_post(os.path.basename(m["npz"]), [os.path.join(C, d) for d in dirs])
            pred = decode_notes(fr, on, de)
            pt = np.array([p["time"] for p in pred])
            S = semitone_map(np.load(m["npz"])["cqt"].astype(np.float32))
            info = parse_gp(m["gp"])
            bq = [(float(q0), float(q0 + ql), b) for b, oc, q0, ql in info.bars]
            rows = []
            for n in m["notes"]:
                if n.get("grace") or n.get("dead"):
                    continue
                j = int(np.argmin(np.abs(pt - n["time"])))
                if abs(pt[j] - n["time"]) > 0.05 or pred[j]["midi"] == n["midi"] or pred[j]["dead"]:
                    continue
                L, P = int(n["midi"]), int(pred[j]["midi"])
                d = d_here(S, P, n["time"]) - d_here(S, L, n["time"])
                v = "模型" if d > 0.15 else "谱" if d < -0.15 else "不确定"
                bar = next((b for a, e, b in bq if a <= n["qpos"] < e), None)
                beat = next((n["qpos"] - a for a, e, b in bq if a <= n["qpos"] < e), 0)
                rows.append((n["time"], bar, beat, L, P, v))
            if not rows:
                continue
            rng = np.random.default_rng(0)
            pick = sorted(rng.choice(len(rows), min(12, len(rows)), replace=False))
            lines += [f"## {os.path.basename(os.path.dirname(m['gp']))}", "",
                      f"分歧共 {len(rows)} 处，随机抽 {len(pick)} 处。", "",
                      "| 时间 | 小节 | 拍 | 谱 | 模型 | 录音证据倾向 | 你的判断 |", "|---|---|---|---|---|---|---|"]
            for i in pick:
                t, bar, beat, L, P, v = rows[i]
                lines.append(f"| {int(t // 60)}:{t % 60:05.2f} | {bar + 1 if bar is not None else '?'} | {beat + 1:g} | {nm(L)} | {nm(P)} | {v} |  |")
            lines.append("")
    open(out, "w", encoding="utf8").write("\n".join(lines))
    print("written", out)


if __name__ == "__main__":
    main()
