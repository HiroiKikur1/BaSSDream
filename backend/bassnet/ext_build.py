"""Teacher-student data from real multitracks (MUSDB18-HQ, MoisesDB): learn to hear in the separated bass what
the clean bass says.

On MUSDB, notes transcribed from BS-Roformer-SW's separated bass differ from notes transcribed from the true bass
stem in ~17% of cases. Here every multitrack becomes a training song in the feats/ format:
  input  = CQT of the SEPARATED bass (production separator, overlap 8) + mel of the mix
  labels = BassNet's notes / beats on the CLEAN bass stem (+ mix mel) -- the teacher
The user allows model outputs as labels when they are trustworthy (2026-09-30); the teacher here sees the isolated
recorded bass, which is what the student cannot. Evaluation stays on independent labels (library val/test tabs,
IDMT), never on these songs; MUSDB test songs are built too but marked split "val" and never trained on.

python -m bassnet.ext_build [--limit N]   -> cache/bassnet/feats_ext/<id>.{npz,json}
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassnet.restore_train import CACHE, SR as SR44  # noqa: E402
import bassnet.build_stems  # noqa: E402,F401  (puts the bundled ffmpeg on PATH for audio_separator)

OUT = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "feats_ext")
TMP = os.path.join("D:" + os.sep, "BassData", "_tmp_ext")


def main():
    import librosa
    import soundfile as sf
    from bassnet.dataset import compute_cqt, compute_mel, SR
    from bassnet.pipeline import posteriors
    from bassnet.decode import decode_notes, decode_beats
    from bassnet.thermal import guard, lower_priority
    from audio_separator.separator import Separator
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    lower_priority()
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    meta = json.load(open(os.path.join(CACHE, "meta.json")))
    sep = Separator(model_file_dir=os.path.join("E:" + os.sep, "BassStation", "cache", "models"), output_dir=TMP,
                    output_format="WAV", use_soundfile=True, log_level=40, use_autocast=True)
    sep.load_model("BS-Roformer-SW.ckpt")
    if getattr(sep, "model_instance", None) is not None:
        sep.model_instance.overlap = 8                         # as in the app (ai_transcriber)
        from bassnet.thermal import hook_module
        hook_module(sep.model_instance.model_run)              # temperature / power check on every chunk
    done = 0
    for item in meta:
        sid, split = item["id"], item["split"]
        if os.path.exists(os.path.join(OUT, sid + ".json")):
            continue
        if a.limit and done >= a.limit:
            break
        guard()
        yb = np.load(os.path.join(CACHE, sid + "_bass.npy")).astype(np.float32)
        yr = np.load(os.path.join(CACHE, sid + "_rest.npy")).astype(np.float32)
        mix = yb + yr
        peak = float(np.abs(mix).max())
        if peak > 0.99:
            mix, yb = mix * 0.99 / peak, yb * 0.99 / peak
        wav = os.path.join(TMP, "mix.wav")
        sf.write(wav, mix, SR44, subtype="PCM_16")
        outs = sep.separate(wav)
        bp = [o for o in outs if "(bass)" in os.path.basename(o).lower()]
        bp = bp[0] if os.path.isabs(bp[0]) else os.path.join(TMP, bp[0])
        ys, _ = librosa.load(bp, sr=SR, mono=True)
        for f in os.listdir(TMP):
            try:
                os.remove(os.path.join(TMP, f))
            except OSError:
                pass
        ym = librosa.resample(mix.mean(1), orig_sr=SR44, target_sr=SR)
        yc = librosa.resample(yb.mean(1), orig_sr=SR44, target_sr=SR)
        cqt_s, cqt_c, mel = compute_cqt(ys), compute_cqt(yc), compute_mel(ym)
        T = min(cqt_s.shape[1], cqt_c.shape[1], mel.shape[1])
        cqt_s, cqt_c, mel = cqt_s[:, :T], cqt_c[:, :T], mel[:, :T]
        fr, on, de, be, do = posteriors(cqt_c, mel)             # the teacher: clean bass
        notes = decode_notes(fr, on, de)
        beats, downs, meter = decode_beats(be, do)
        np.savez_compressed(os.path.join(OUT, sid + ".npz"), cqt=cqt_s.astype(np.float16), mel=mel.astype(np.float16),
                            cqt_clean=cqt_c.astype(np.float16))
        json.dump({"gp": os.path.join("EXT", sid, sid + ".gp"), "stem": "", "T": int(T), "global_lag": 0.0,
                   "rate0": 1.0, "rate_global": 1.0, "rate_final": 1.0, "tuning": [28, 33, 38, 43],
                   "time_sigs": [[4, 4]], "tempo0": 120.0, "key": [0, "Major"], "ext_split": split,
                   "teacher": "bassnet v3+v4 on the clean bass stem",
                   "notes": [{k: (float(v) if isinstance(v, (np.floating, float)) else v) for k, v in n.items()
                              if k in ("time", "end", "midi", "dead")} | {"grace": False} for n in notes],
                   "beats": [float(x) for x in beats], "downbeats": [float(x) for x in downs]},
                  open(os.path.join(OUT, sid + ".json"), "w", encoding="utf8"))
        done += 1
        print(sid, split, len(notes), "notes", flush=True)


if __name__ == "__main__":
    main()
