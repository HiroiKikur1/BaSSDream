"""Resynthesis check: which score explains the recording better, the model's transcription or the purchased tab?

Both note lists are rendered with the same synthetic bass and compared with the song's separated bass stem in a
timbre-neutral space: semitone CQT, each row whitened by its song-wide mean (removes the instrument's EQ), each
frame L2-normalised. Frames where either signal is active count; a rendered note where the stem is silent (or the
reverse) scores 0 there. Per 2-second window the better-matching score wins.

python -m bassnet.resynth_compare [--listen KEY ...]
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.audio_verify import semitone_map  # noqa: E402

SR = 22050
WIN = 2.0
LISTEN = os.path.join("E:" + os.sep, "BassStation", "cache", "resynth_listen")


def whiten(S):
    return S / (S.mean(1, keepdims=True) + 1e-3)


def frame_sim(A, B, thr_a, thr_b):
    """Per-frame cosine of whitened semitone maps over frames where either is active."""
    T = min(A.shape[1], B.shape[1])
    A, B = A[:, :T], B[:, :T]
    ea, eb = A.sum(0), B.sum(0)
    act = (ea > thr_a) | (eb > thr_b)
    na = A / (np.linalg.norm(A, axis=0, keepdims=True) + 1e-9)
    nb = B / (np.linalg.norm(B, axis=0, keepdims=True) + 1e-9)
    sim = (na * nb).sum(0)
    sim[(ea <= thr_a) | (eb <= thr_b)] = 0.0
    return sim, act


def features(y):
    from bassnet.dataset import compute_cqt
    return whiten(semitone_map(compute_cqt(y)))


def main():
    import librosa
    import soundfile as sf
    from bassnet import eval_cached
    from bassnet.decode import decode_notes, FPS
    from bassnet.sep_ceiling import render_bass
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", nargs="*", default=[])
    a = ap.parse_args()
    C = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "eval")
    rows = []
    for split, dirs in (("val", ("val_v3", "val_v4")), ("test", ("post_cache_v3", "post_cache_v4"))):
        eval_cached.SPLIT = split
        for m in eval_cached.metas():
            fr, on, de, be, do = eval_cached.load_post(os.path.basename(m["npz"]), [os.path.join(C, d) for d in dirs])
            model_notes = decode_notes(fr, on, de)
            tab_notes = [n for n in m["notes"] if not n.get("grace")]
            stem, _ = librosa.load(m["stem"], sr=SR, mono=True)
            rng = np.random.default_rng(0)
            # one neutral, undistorted timbre for both renders
            r_tab = render_bass(tab_notes, len(stem), np.random.default_rng(1), sr=SR)
            r_mod = render_bass(model_notes, len(stem), np.random.default_rng(1), sr=SR)
            Fs, Ft, Fm = features(stem), features(r_tab), features(r_mod)
            ts = np.percentile(Fs.sum(0), 30)
            tt = np.percentile(Ft.sum(0), 30)
            tm = np.percentile(Fm.sum(0), 30)
            st, act_t = frame_sim(Fs, Ft, ts, tt)
            sm, act_m = frame_sim(Fs, Fm, ts, tm)
            act = act_t | act_m
            T = min(len(st), len(sm))
            st, sm, act = st[:T], sm[:T], act[:T]
            w = int(WIN * FPS)
            win_t, win_m = [], []
            for s in range(0, T - w + 1, w):
                ac = act[s:s + w]
                if ac.mean() < 0.3:
                    continue
                win_t.append(st[s:s + w][ac].mean())
                win_m.append(sm[s:s + w][ac].mean())
            win_t, win_m = np.array(win_t), np.array(win_m)
            name = os.path.basename(os.path.dirname(m["gp"]))[:30]
            r = {"name": name, "split": split, "tab": float(st[act].mean()), "model": float(sm[act].mean()),
                 "model_wins": float(np.mean(win_m > win_t + 0.03)), "tab_wins": float(np.mean(win_t > win_m + 0.03))}
            rows.append(r)
            print(f"{split:4s} {name:32s} sim tab {r['tab']:.3f} model {r['model']:.3f} | 2s windows: model better "
                  f"{r['model_wins']:.2f} tab better {r['tab_wins']:.2f}", flush=True)
            if any(k in m["gp"] for k in a.listen):
                d = os.path.join(LISTEN, name.replace("/", "_").strip())
                os.makedirs(d, exist_ok=True)
                pk = lambda x: x / (np.abs(x).max() + 1e-9) * 0.9  # noqa: E731
                sf.write(os.path.join(d, "1_分离贝斯轨.wav"), pk(stem), SR, subtype="PCM_16")
                sf.write(os.path.join(d, "2_谱面渲染.wav"), pk(r_tab), SR, subtype="PCM_16")
                sf.write(os.path.join(d, "3_模型渲染.wav"), pk(r_mod), SR, subtype="PCM_16")
                # left = stem, right = render: hear both at once on headphones
                sf.write(os.path.join(d, "4_左分离_右谱面.wav"), np.stack([pk(stem), pk(r_tab)], 1) * 0.8, SR, subtype="PCM_16")
                sf.write(os.path.join(d, "5_左分离_右模型.wav"), np.stack([pk(stem), pk(r_mod)], 1) * 0.8, SR, subtype="PCM_16")
    for split in ("val", "test"):
        rs = [r for r in rows if r["split"] == split]
        print(split, {k: round(float(np.mean([r[k] for r in rs])), 4) for k in ("tab", "model", "model_wins", "tab_wins")})


if __name__ == "__main__":
    main()
