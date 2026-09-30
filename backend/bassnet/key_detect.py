"""Key signature of a recording for engraving (Layer 1): full-song chroma + recognised chords (lv-chordia).

Evidence per candidate signature (bassnet/key_eval.py): diatonic fit and pop/rock key-profile fit of (a) the mean
chroma of the whole mix and (b) the duration-weighted pitch classes of the chords; weights learnt on the 297
recordings of the purchased tabs (cache/bassnet/key_w.json). Agreement with the tabs' signatures: 80.5%
(10-fold), vs 63.6% for the old first-90-seconds Krumhansl detector; 96% within one fifth.
Returns the chords as well: the song document keeps them for the later layers.
"""
import contextlib
import io
import json
import os
from typing import Dict, List, Tuple

import numpy as np

W_FILE = os.path.join("E:" + os.sep, "BassStation", "cache", "bassnet", "key_w.json")


def chords_of(audio_path: str) -> List[Dict]:
    from lv_chordia.chord_recognition import chord_recognition
    with contextlib.redirect_stdout(io.StringIO()):
        return chord_recognition(audio_path=audio_path, chord_dict_name="submission")


def mix_chroma_frames(audio_path: str) -> np.ndarray:
    import librosa
    from bassnet.key_eval import SR, HOP, _chroma_norm
    y, _ = librosa.load(audio_path, sr=SR, mono=True)
    c = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP, n_chroma=12, bins_per_octave=36)
    return _chroma_norm(c)


def mix_chroma(audio_path: str) -> np.ndarray:
    return mix_chroma_frames(audio_path).mean(1)


def _as_wav(audio_path: str) -> Tuple[str, bool]:
    """Decode compressed input once with the bundled ffmpeg (both readers then see identical samples)."""
    if audio_path.lower().endswith(".wav"):
        return audio_path, False
    import subprocess
    import tempfile
    from bassnet.build_stems import FFMPEG
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    subprocess.run([FFMPEG if os.path.exists(FFMPEG) else "ffmpeg", "-y", "-loglevel", "error", "-i", audio_path,
                    "-ar", "44100", "-ac", "1", tmp], check=True)
    return tmp, True


def detect(audio_path: str, chords: List[Dict] = None, segments: bool = False):
    """-> ((accidental count -5..6, 'Major'/'Minor'), chords[, key runs [(t0, t1, signature)]])."""
    wav, tmp = _as_wav(audio_path)
    try:
        frames = mix_chroma_frames(wav)
        key, chords = _detect(wav, chords, frames)
        if not segments:
            return key, chords
        from bassnet.key_local import local_keys
        try:
            runs = local_keys(frames, chords, key[0])
        except Exception as e:
            print(f"[key] local keys skipped: {e}", flush=True)
            runs = []
        return key, chords, runs
    finally:
        if tmp and os.path.exists(wav):
            os.remove(wav)


def _detect(audio_path, chords, frames=None):
    from bassnet import key_eval as K
    cfg = json.load(open(W_FILE))
    if chords is None:
        try:
            chords = chords_of(audio_path)
        except Exception as e:
            print(f"[key] chord recognition skipped: {e}", flush=True)
            chords = []
    srcs = {"pc_mix": frames.mean(1) if frames is not None else mix_chroma(audio_path), "pc_chords_orig": K.chord_pc(chords) if chords else None}
    keep = K.SOURCES
    K.SOURCES = tuple(cfg["sources"])
    try:
        F = K.sig_features(srcs)
    finally:
        K.SOURCES = keep
    s = int(np.argmax(F @ np.array(cfg["w"])))
    sig = s - 12 if s > 6 else s
    # mode: major tonic vs relative minor, from the chord pitch classes (chroma if no chords)
    pc = srcs["pc_chords_orig"] if srcs["pc_chords_orig"] is not None else srcs["pc_mix"]
    ks = K.key_scores(pc, K.AS_MAJ, K.AS_MIN)
    t = (s * 7) % 12
    mode = "Major" if ks[t] >= ks[12 + (t - 3) % 12] else "Minor"
    return (sig, mode), chords
