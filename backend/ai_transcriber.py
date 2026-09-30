import os
import shutil
import sys
import math
import subprocess
import numpy as np
from typing import List, Dict, Any, Tuple, Optional

# Ensure soundfile and librosa can find ffmpeg if present
UVR_TOOLS_DIR = r"E:\BassStation\tools\Ultimate Vocal Remover"
if os.path.exists(UVR_TOOLS_DIR) and UVR_TOOLS_DIR not in os.environ.get("PATH", ""):
    os.environ["PATH"] = UVR_TOOLS_DIR + os.pathsep + os.environ.get("PATH", "")

_ROFORMER_SEPARATOR = None

def get_roformer_separator():
    global _ROFORMER_SEPARATOR
    if _ROFORMER_SEPARATOR is None:
        from audio_separator.separator import Separator
        models_dir = r"E:\BassStation\cache\models"
        os.makedirs(models_dir, exist_ok=True)
        print("[AI Transcriber] Initializing SOTA BS-Roformer separator (persistent)...")
        sep = Separator(
            model_file_dir=models_dir,
            output_dir=models_dir,
            output_format="WAV",
            use_soundfile=True,
            log_level=30
        )
        sep.load_model("BS-Roformer-SW.ckpt")
        _ROFORMER_SEPARATOR = sep
    return _ROFORMER_SEPARATOR

def save_stems4(bass_file, other_files, out_dir):
    """The 6 BS-Roformer stems folded to allin1's 4 (bass, drums, other = guitar + piano + other, vocals), mono FLAC,
    for the song-structure model (bassnet/allin1_run.py). Deleted after the analysis."""
    import soundfile as sf
    os.makedirs(out_dir, exist_ok=True)
    groups = {"bass": [bass_file], "drums": [], "vocals": [], "other": []}
    for f in other_files:
        n = os.path.basename(f).lower()
        k = "drums" if "(drums)" in n else "vocals" if "(vocals)" in n else "other"
        groups[k].append(f)
    for k, fs in groups.items():
        acc = None
        for f in fs:
            if not os.path.exists(f):
                continue
            y, sr = sf.read(f, dtype="float32", always_2d=True)
            y = y.mean(1)
            acc = y if acc is None else acc[:min(len(acc), len(y))] + y[:min(len(acc), len(y))]
        if acc is not None:
            sf.write(os.path.join(out_dir, k + ".flac"), acc, sr)


def separate_stems_roformer(
    input_audio: str,
    output_dir: str,
    overlap: int = 4
) -> Dict[str, str]:
    """
    Separates audio using SOTA Band-Split Roformer (BS-Roformer-SW).
    Outputs clean isolated bass and blends non-bass stems into backing track.
    """
    import shutil
    import soundfile as sf

    os.makedirs(output_dir, exist_ok=True)
    temp_dir = os.path.join(output_dir, "_roformer_temp")
    os.makedirs(temp_dir, exist_ok=True)

    bass_out = os.path.join(output_dir, "extracted_bass.wav")
    nobass_out = os.path.join(output_dir, "backing_nobass.mp3")

    ffmpeg_bin = os.path.join(UVR_TOOLS_DIR, "ffmpeg.exe") if os.path.exists(os.path.join(UVR_TOOLS_DIR, "ffmpeg.exe")) else "ffmpeg"

    try:
        sep = get_roformer_separator()
        sep.output_dir = temp_dir
        if hasattr(sep, 'model_instance') and sep.model_instance is not None:
            sep.model_instance.output_dir = temp_dir
            sep.model_instance.overlap = overlap

        # Convert non-WAV inputs to standard 44.1kHz 16-bit PCM WAV to avoid libsndfile MPEG subtype bug
        wav_input = input_audio
        if not input_audio.lower().endswith(".wav"):
            wav_input = os.path.join(temp_dir, "_input_clean.wav")
            subprocess.run([ffmpeg_bin, "-y", "-i", input_audio, "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", wav_input], capture_output=True)

        print(f"[AI Transcriber] Running BS-Roformer separation (overlap={overlap}) on: {input_audio}")
        outputs = sep.separate(wav_input)
        if not outputs:
            raise RuntimeError("BS-Roformer produced no output files")

        bass_file = None
        backing_files = []
        for f in outputs:
            p = os.path.join(temp_dir, f) if not os.path.isabs(f) else f
            f_lower = os.path.basename(f).lower()
            if "(bass)" in f_lower or "_bass" in f_lower:
                bass_file = p
            else:
                backing_files.append(p)

        if not bass_file or not os.path.exists(bass_file):
            raise FileNotFoundError("BS-Roformer did not produce a bass stem")

        shutil.copy2(bass_file, bass_out)
        try:
            from bassnet.pipeline import USE_ALLIN1
            if USE_ALLIN1:
                save_stems4(bass_file, backing_files, os.path.join(output_dir, "_stems4"))
        except Exception as e:
            print(f"[AI Transcriber] stems for song analysis skipped: {e}", flush=True)

        # Blend non-bass stems (drums, vocals, other, guitar, piano) into pristine no-bass backing
        if backing_files:
            combined = None
            target_sr = 44100
            for bf in backing_files:
                if not os.path.exists(bf):
                    continue
                data, cur_sr = sf.read(bf, always_2d=True)
                target_sr = cur_sr
                if combined is None:
                    combined = np.copy(data)
                else:
                    min_len = min(len(combined), len(data))
                    combined = combined[:min_len] + data[:min_len]

            if combined is not None:
                # Professional RMS loudness normalization (target RMS 0.16)
                cur_rms = float(np.sqrt(np.mean(combined ** 2))) + 1e-6
                target_rms = 0.16
                gain = min(2.5, max(0.4, target_rms / cur_rms))
                combined = combined * gain

                # Dynamic soft limiting with tanh to guarantee zero digital clipping
                max_val = float(np.max(np.abs(combined)))
                if max_val > 0.95:
                    combined = np.tanh(combined / max_val * 1.05) * 0.95

                temp_wav = os.path.join(output_dir, "temp_backing_mix.wav")
                sf.write(temp_wav, combined, target_sr)
                subprocess.run([ffmpeg_bin, "-y", "-i", temp_wav, "-b:a", "320k", nobass_out], capture_output=True)
                if os.path.exists(temp_wav):
                    os.remove(temp_wav)
    finally:
        # GPU resource release: prevent memory fragmentation across multiple songs
        try:
            import torch, gc
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()
        except Exception:
            pass
        shutil.rmtree(temp_dir, ignore_errors=True)
    return {"bass": bass_out, "backing": nobass_out}

def separate_stems(
    input_audio: str,
    output_dir: str,
    python_exe: Optional[str] = None
) -> Dict[str, str]:
    """
    Primary separation router:
    1. Tries SOTA BS-Roformer (Mel-Band / Band-Split architecture)
    2. Falls back to Demucs htdemucs_ft / htdemucs on failure
    """
    bass_out = os.path.join(output_dir, "extracted_bass.wav")
    nobass_out = os.path.join(output_dir, "backing_nobass.mp3")

    if os.path.exists(bass_out) and os.path.exists(nobass_out) and os.path.getsize(bass_out) > 10000:
        return {"bass": bass_out, "backing": nobass_out}

    try:
        print("[AI Transcriber] Primary separation: Running SOTA BS-Roformer...")
        return separate_stems_roformer(input_audio, output_dir)
    except Exception as e:
        print(f"[AI Transcriber] BS-Roformer unavailable or failed ({e}). Falling back to Demucs...")
        return separate_stems_demucs(input_audio, output_dir, python_exe=python_exe)

def separate_stems_demucs(
    input_audio: str,
    output_dir: str,
    python_exe: Optional[str] = None
) -> Dict[str, str]:
    """
    Separates input audio using Demucs on GPU/CPU (Fallback engine).
    Outputs:
    - extracted_bass.wav: isolated bass stem
    - backing_nobass.mp3: pristine backing track without bass
    """
    os.makedirs(output_dir, exist_ok=True)
    if not python_exe:
        python_exe = sys.executable

    bass_out = os.path.join(output_dir, "extracted_bass.wav")
    nobass_out = os.path.join(output_dir, "backing_nobass.mp3")

    if os.path.exists(bass_out) and os.path.exists(nobass_out) and os.path.getsize(bass_out) > 10000:
        return {"bass": bass_out, "backing": nobass_out}

    demucs_temp = os.path.join(output_dir, "_demucs_temp")
    os.makedirs(demucs_temp, exist_ok=True)

    cmd = [
        python_exe, "-m", "demucs.separate",
        "-n", "htdemucs_ft",
        "-d", "cuda",
        "--two-stems", "bass",
        "--clip-mode", "rescale",
        "-o", demucs_temp,
        input_audio
    ]

    print(f"[AI Transcriber] Running Demucs separation: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[AI Transcriber] htdemucs_ft failed, retrying with htdemucs: {result.stderr[:200]}")
        cmd[4] = "htdemucs"
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[AI Transcriber] Demucs error: {result.stderr}")
            raise RuntimeError(f"Demucs separation failed: {result.stderr}")

    track_name = os.path.splitext(os.path.basename(input_audio))[0]
    track_folder = os.path.join(demucs_temp, "htdemucs_ft", track_name)
    if not os.path.exists(track_folder):
        track_folder = os.path.join(demucs_temp, "htdemucs", track_name)
    if not os.path.exists(track_folder):
        for root, _, files in os.walk(demucs_temp):
            if "bass.wav" in files:
                track_folder = root
                break

    stem_bass = os.path.join(track_folder, "bass.wav")
    stem_nobass = os.path.join(track_folder, "no_bass.wav")

    import shutil
    import soundfile as sf

    if os.path.exists(stem_bass):
        shutil.copy2(stem_bass, bass_out)
    else:
        raise FileNotFoundError(f"Demucs did not produce bass.wav in {track_folder}")

    ffmpeg_bin = os.path.join(UVR_TOOLS_DIR, "ffmpeg.exe") if os.path.exists(os.path.join(UVR_TOOLS_DIR, "ffmpeg.exe")) else "ffmpeg"
    if os.path.exists(stem_nobass):
        conv_cmd = [ffmpeg_bin, "-y", "-i", stem_nobass, "-b:a", "320k", nobass_out]
        conv_res = subprocess.run(conv_cmd, capture_output=True)
        if conv_res.returncode != 0 or not os.path.exists(nobass_out):
            shutil.copy2(stem_nobass, nobass_out)
    else:
        stem_drums = os.path.join(track_folder, "drums.wav")
        stem_other = os.path.join(track_folder, "other.wav")
        stem_vocals = os.path.join(track_folder, "vocals.wav")
        if os.path.exists(stem_drums):
            d, sr = sf.read(stem_drums)
            o, _ = sf.read(stem_other)
            v, _ = sf.read(stem_vocals)
            mlen = min(len(d), len(o), len(v))
            backing = d[:mlen] + o[:mlen] + v[:mlen]
            mv = np.max(np.abs(backing))
            if mv > 0.98:
                backing = backing * (0.95 / mv)
            temp_wav = os.path.join(output_dir, "temp_b.wav")
            sf.write(temp_wav, backing, sr)
            subprocess.run([ffmpeg_bin, "-y", "-i", temp_wav, "-b:a", "320k", nobass_out], capture_output=True)
            if os.path.exists(temp_wav):
                os.remove(temp_wav)

    shutil.rmtree(demucs_temp, ignore_errors=True)
    return {"bass": bass_out, "backing": nobass_out}

def optimize_bass_fretboard_viterbi(
    notes: List[Dict[str, Any]],
    is_5string: bool = False
) -> List[Dict[str, Any]]:
    """
    Viterbi dynamic programming algorithm for physically optimal bass fretboard layout.
    Models human hand positions (box patterns), prioritizing natural hand economy,
    slap/pop biomechanics (slap on low strings, pop on high strings),
    and avoiding erratic single-note jumps across positions.
    """
    if not notes:
        return []

    min_note_midi = min(int(n["midi"]) for n in notes) if notes else 28
    if is_5string or min_note_midi <= 24:
        is_5string = True
        open_strings = [23, 28, 33, 38, 43]
    elif min_note_midi == 25:
        # Drop C# (Db1 Ab1 Db2 Gb2)
        open_strings = [25, 33, 38, 43]
    elif min_note_midi in (26, 27):
        # Drop D (D1 A1 D2 G2)
        open_strings = [26, 33, 38, 43]
    else:
        open_strings = [28, 33, 38, 43]

    seq_candidates = []
    for note in notes:
        midi = int(note["midi"])

        candidates = []
        for s_idx, open_midi in enumerate(open_strings):
            fret = midi - open_midi
            if 0 <= fret <= 21:
                # Ergonomic bass fret bias:
                # Open string (fret 0): natural pivot point
                if fret == 0:
                    fret_bias = 0.02
                elif fret <= 5:
                    # Home box (frets 1-5): standard rock/metal foundational position
                    fret_bias = 0.04 + fret * 0.02
                elif fret <= 9:
                    # Mid box (frets 6-9): comfortable melodic range
                    fret_bias = 0.20 + (fret - 5) * 0.05
                elif fret <= 12:
                    # High box (frets 10-12)
                    fret_bias = 0.50 + (fret - 9) * 0.10
                else:
                    # Extreme high frets (> 12)
                    fret_bias = 1.20 + (fret - 12) * 0.30

                # Low strings (E / Low B) should heavily penalize frets >= 8
                # to prevent muddy low-end tone and uncomfortable arm reaches;
                # playing the note on A/D string at lower frets is vastly better.
                if s_idx == 0 and fret >= 8:
                    fret_bias += 1.60 + (fret - 8) * 0.25
                elif s_idx == 1 and fret >= 10:
                    fret_bias += 1.00 + (fret - 10) * 0.20

                candidates.append((s_idx, fret, fret_bias))

        if not candidates:
            candidates = [(0, max(0, min(21, midi - open_strings[0])), 5.0)]
        seq_candidates.append(candidates)

    N = len(notes)
    if N == 1:
        best_cand = min(seq_candidates[0], key=lambda x: x[2])
        notes[0]["string"] = best_cand[0]
        notes[0]["fret"] = best_cand[1]
        return notes

    dp = [{} for _ in range(N)]
    backpointer = [{} for _ in range(N)]

    for c_idx, (s, f, bias) in enumerate(seq_candidates[0]):
        dp[0][c_idx] = bias

    for t in range(1, N):
        dt = max(0.05, notes[t]["time"] - notes[t - 1]["time"])
        prev_dur = float(notes[t - 1].get("duration", 0.0))
        rest_gap = max(0.0, notes[t]["time"] - (notes[t - 1]["time"] + prev_dur))

        is_slide_pair = bool(notes[t - 1].get("slide_to_next", False))

        for curr_idx, (s2, f2, bias2) in enumerate(seq_candidates[t]):
            best_cost = float('inf')
            best_prev = 0

            for prev_idx, (s1, f1, _) in enumerate(seq_candidates[t - 1]):
                fret_diff = abs(f2 - f1)
                str_diff = abs(s2 - s1)

                # Available shifting window:
                # Open string gives full dt to shift; fretted note allows release window or rest gap
                if f1 == 0:
                    shift_window = dt
                else:
                    shift_window = max(rest_gap, max(0.06, dt - 0.10))

                if is_slide_pair:
                    # Verified slide: same string, fretted, within 7 frets
                    if str_diff == 0 and f1 > 0 and f2 > 0 and fret_diff <= 7:
                        trans_cost = 0.05 + fret_diff * 0.04
                    else:
                        trans_cost = 8.0 + fret_diff * 0.50 + str_diff * 1.50
                elif str_diff == 0 and fret_diff == 0:
                    trans_cost = 0.0
                elif f1 == 0 or f2 == 0:
                    trans_cost = 0.04 + str_diff * 0.08
                elif str_diff <= 1 and fret_diff <= 3:
                    # Same box position (within 3 frets): effortless one-finger-per-fret
                    trans_cost = 0.04 + fret_diff * 0.05 + str_diff * 0.06
                elif str_diff <= 2 and fret_diff <= 4 and min(f1, f2) >= 5:
                    # Narrower fret spacing at mid/high positions
                    trans_cost = 0.12 + fret_diff * 0.06 + str_diff * 0.08
                else:
                    # Position shift across the neck
                    if shift_window < 0.18:
                        # Rapid run (16th notes): strict biomechanical lock against large leaps
                        trans_cost = 45.0 + (fret_diff - 3) * 6.0 + str_diff * 5.0
                    elif shift_window < 0.32:
                        # Moderate transition: noticeable shifting effort
                        trans_cost = 8.0 + (fret_diff - 3) * 1.8 + str_diff * 1.5
                    else:
                        # Ample shifting window (>= 320ms or rest): natural hand repositioning
                        trans_cost = 0.60 + (fret_diff - 3) * 0.20 + str_diff * 0.25

                total_cost = dp[t - 1][prev_idx] + trans_cost + bias2
                if total_cost < best_cost:
                    best_cost = total_cost
                    best_prev = prev_idx

            dp[t][curr_idx] = best_cost
            backpointer[t][curr_idx] = best_prev

    best_last_idx = min(dp[N - 1], key=dp[N - 1].get)
    optimal_path = [best_last_idx]

    for t in range(N - 1, 0, -1):
        prev_idx = backpointer[t][optimal_path[-1]]
        optimal_path.append(prev_idx)

    optimal_path.reverse()

    for t in range(N):
        cand_idx = optimal_path[t]
        s, f, _ = seq_candidates[t][cand_idx]
        notes[t]["string"] = s
        notes[t]["fret"] = f

    return notes

def get_scale_pitch_classes(accidental_count: int, mode: str) -> set:
    """
    Returns set of pitch classes (0-11, where 0=C, 1=C#, 2=D...) for the diatonic scale.
    """
    if str(mode).lower() == "minor":
        root = ((accidental_count * 7) - 3) % 12
        intervals = [0, 2, 3, 5, 7, 8, 10, 11]  # Natural minor + harmonic minor leading tone
    else:
        root = (accidental_count * 7) % 12
        intervals = [0, 2, 4, 5, 7, 9, 11]      # Major scale
    return {(root + i) % 12 for i in intervals}

def detect_meter(beat_times: np.ndarray, onset_env: np.ndarray, sr: int, hop_length: int) -> Tuple[int, int]:
    """
    Analyzes beat strength periodicity to detect (3, 4) vs (4, 4).
    """
    if len(beat_times) < 16:
        return (4, 4)
    raw_frames = np.round(np.asarray(beat_times) * float(sr) / float(hop_length)).astype(int)
    frame_indices = np.clip(raw_frames, 0, len(onset_env) - 1)
    strengths = onset_env[frame_indices]
    if len(strengths) < 16:
        return (4, 4)
    n = len(strengths)
    strengths_centered = strengths - np.mean(strengths)
    var = float(np.var(strengths)) + 1e-6
    ac = np.correlate(strengths_centered, strengths_centered, mode='full')
    ac = ac[n - 1:] / (n * var)
    ac3 = float(ac[3]) if len(ac) > 3 else 0.0
    ac4 = float(ac[4]) if len(ac) > 4 else 0.0
    ac6 = float(ac[6]) if len(ac) > 6 else 0.0
    if (ac3 > 0.25 and ac3 > 1.35 * ac4) or (ac6 > 0.30 and ac6 > 1.35 * ac4):
        return (3, 4)
    return (4, 4)

def detect_key_signature(audio_path: str) -> Tuple[int, str]:
    """
    Analyzes audio chroma with Krumhansl-Schmuckler key-finding algorithm.
    Returns (accidental_count, mode) for Guitar Pro GPIF, e.g. (-5, "Minor") or (1, "Major").
    """
    import librosa
    MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    PITCH_NAMES = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B']
    MAJOR_SHARPS = {'C': 0, 'G': 1, 'D': 2, 'A': 3, 'E': 4, 'B': 5, 'F#': 6, 'F': -1, 'Bb': -2, 'Eb': -3, 'Ab': -4, 'Db': -5}
    MINOR_SHARPS = {'A': 0, 'E': 1, 'B': 2, 'F#': 3, 'C#': 4, 'G#': 5, 'D#': 6, 'D': -1, 'G': -2, 'C': -3, 'F': -4, 'Bb': -5}

    try:
        y, sr = librosa.load(audio_path, sr=22050, mono=True, duration=90.0)
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_avg = np.mean(chroma, axis=1)

        best_score = -2.0
        best_key = (0, "Major")

        for i in range(12):
            rolled_chroma = np.roll(chroma_avg, -i)
            r_maj = np.corrcoef(rolled_chroma, MAJOR_PROFILE)[0, 1]
            if r_maj > best_score:
                best_score = r_maj
                p = PITCH_NAMES[i]
                best_key = (MAJOR_SHARPS.get(p, 0), "Major")
            r_min = np.corrcoef(rolled_chroma, MINOR_PROFILE)[0, 1]
            if r_min > best_score:
                best_score = r_min
                p = PITCH_NAMES[i]
                best_key = (MINOR_SHARPS.get(p, 0), "Minor")
        return best_key
    except Exception as e:
        print(f"[AI Transcriber] Warning detecting key signature: {e}")
        return (0, "Major")

def transcribe_bass_audio(
    bass_audio_path: str,
    is_5string: bool = False,
    key_signature: Optional[Tuple[int, str]] = None
) -> Tuple[float, List[Dict[str, Any]], bool, List[Dict[str, Any]], Tuple[int, int]]:
    """
    Transcribes isolated bass audio into quantized note sequence with optimal tablature.
    Uses CQT, harmonic tracking, diatonic key-signature constraints, and live dynamic tempo synchronization.
    """
    import librosa
    import scipy.signal as signal

    y, sr = librosa.load(bass_audio_path, sr=22050, mono=True)

    # 0. Acoustic Pre-Filtering (Highpass 28Hz for DC rumble, Lowpass 3600Hz for high noise)
    try:
        sos = signal.butter(4, [28.0, 3600.0], btype='bandpass', fs=sr, output='sos')
        y = signal.sosfilt(sos, y)
    except Exception as e:
        print(f"[AI Transcriber] Pre-filter bypass ({e})")

    # 1. Robust Tempo & Beat Tracking with Octave-Error Correction
    hop_length = 512
    onset_env_tempo = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    tempo_val, beat_times = librosa.beat.beat_track(onset_envelope=onset_env_tempo, sr=sr, hop_length=hop_length, units='time')
    if hasattr(tempo_val, "__iter__"):
        tempo = float(tempo_val[0])
    else:
        tempo = float(tempo_val)

    # Octave tempo error correction (half-time / double-time detection for Rock/Pop/Anime songs)
    total_dur_sec = float(len(y)) / sr
    onset_rate = len(beat_times) / (total_dur_sec + 1e-6)
    if (tempo < 108.0 and onset_rate >= 1.8) or (tempo < 75.0 and len(beat_times) > 0):
        tempo = tempo * 2.0
    elif tempo > 220.0:
        tempo = tempo / 2.0

    if tempo <= 40 or tempo >= 240:
        tempo = 120.0

    print("__PROGRESS__: 泛音频谱 65%", flush=True)
    # 2. CQT Harmonic Analysis in Bass Range (30Hz ~ 800Hz)
    fmin = librosa.note_to_hz('B0')  # Always start at B0 (30.87 Hz) to lock bin 0 to base_midi = 23
    hop_length = 512
    cqt = np.abs(librosa.cqt(y=y, sr=sr, hop_length=hop_length, fmin=fmin, n_bins=54, bins_per_octave=12))

    # 3. Multi-Band Onset Detection isolating high-frequency pluck clack (1000Hz ~ 4500Hz) and Legato pitch shifts
    # 3. Precision Multi-Band Onset Detection for Electric Bass
    # Band A: Bass fundamental body (fmin to 400Hz)
    onset_env_low = librosa.onset.onset_strength(
        y=y, sr=sr, hop_length=hop_length,
        fmin=fmin,
        fmax=400.0
    )
    # Band B: Bass pluck transient (250Hz to 1100Hz) - strictly avoids drum snare/cymbal bleed above 1200Hz
    onset_env_transient = librosa.onset.onset_strength(
        y=y, sr=sr, hop_length=hop_length,
        fmin=250.0,
        fmax=1100.0
    )
    onset_env_broad = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length, fmax=1500.0)

    # Legato / slur pitch-shift flux: positive energy rise across CQT frequency bins (captures soft hammer-ons/pull-offs)
    cqt_diff = np.diff(cqt, axis=1)
    pos_cqt_diff = np.maximum(0.0, cqt_diff)
    pitch_shift_env = np.pad(np.sum(pos_cqt_diff[:45, :], axis=0), (1, 0), mode='edge')

    min_env_len = min(len(onset_env_low), len(onset_env_transient), len(onset_env_broad), len(pitch_shift_env))
    onset_env_low = onset_env_low[:min_env_len]
    onset_env_transient = onset_env_transient[:min_env_len]
    onset_env_broad = onset_env_broad[:min_env_len]
    pitch_shift_env = pitch_shift_env[:min_env_len]

    p95_low = float(np.percentile(onset_env_low, 95)) + 1e-6
    p95_tran = float(np.percentile(onset_env_transient, 95)) + 1e-6
    p95_broad = float(np.percentile(onset_env_broad, 95)) + 1e-6
    p95_pitch = float(np.percentile(pitch_shift_env, 95)) + 1e-6

    onset_env = (
        0.45 * (onset_env_low / p95_low) +
        0.35 * (onset_env_transient / p95_tran) +
        0.05 * (onset_env_broad / p95_broad) +
        0.15 * (pitch_shift_env / p95_pitch)
    )

    # High-resolution onset detection with calibrated delta and 2-frame wait (~46ms) for 16th note fidelity
    onset_frames_combo = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=hop_length,
        backtrack=False, delta=0.05, wait=2
    )
    # Legato pitch shifts: only include if accompanied by clear pitch bin change
    onset_frames_pitch = librosa.onset.onset_detect(
        onset_envelope=pitch_shift_env / p95_pitch, sr=sr, hop_length=hop_length,
        backtrack=False, delta=0.055, wait=3
    )
    valid_pitch_frames = []
    for pf in onset_frames_pitch:
        if 2 <= pf < cqt.shape[1] - 2:
            prev_b = int(np.argmax(cqt[:36, pf - 2]))
            curr_b = int(np.argmax(cqt[:36, pf + 1]))
            if prev_b != curr_b and np.max(cqt[:36, pf + 1]) >= 0.04 * np.max(cqt):
                valid_pitch_frames.append(pf)

    all_frames = sorted(set(onset_frames_combo).union(set(valid_pitch_frames)))
    merged_frames = []
    for f in all_frames:
        if not merged_frames:
            merged_frames.append(f)
        else:
            prev_f = merged_frames[-1]
            diff_f = f - prev_f
            if diff_f > 2:
                merged_frames.append(f)
            elif diff_f == 2:
                # 2 frames apart (~46ms): check if there is an energy trough at intermediate frame (f-1)
                mid_f = f - 1
                mid_val = onset_env[mid_f] if mid_f < len(onset_env) else 0.0
                p_val = onset_env[prev_f] if prev_f < len(onset_env) else 0.0
                c_val = onset_env[f] if f < len(onset_env) else 0.0
                min_peak = min(p_val, c_val)

                # Check if dominant CQT pitch bin differs between prev_f and f (legato note transition)
                pitch_diff = False
                if prev_f < cqt.shape[1] and f < cqt.shape[1]:
                    bin_prev = int(np.argmax(cqt[:36, prev_f]))
                    bin_curr = int(np.argmax(cqt[:36, f]))
                    pitch_diff = (bin_prev != bin_curr)

                # If intermediate frame has a clear energy trough (>= 25% dip) or pitch shifted, keep both
                if (min_peak > 1e-4 and mid_val < 0.75 * min_peak) or pitch_diff:
                    merged_frames.append(f)
                else:
                    if c_val > p_val:
                        merged_frames[-1] = f
            else:
                # 1 frame apart (~23ms): detector jitter, merge to strongest peak
                p_val = onset_env[prev_f] if prev_f < len(onset_env) else 0.0
                c_val = onset_env[f] if f < len(onset_env) else 0.0
                if c_val > p_val:
                    merged_frames[-1] = f

    onset_frames = np.array(merged_frames, dtype=int)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop_length)

    # Refined tempo error correction based on actual note onset density
    total_dur = float(len(y)) / sr
    actual_note_rate = len(onset_times) / (total_dur + 1e-6)
    if (tempo < 108.0 and actual_note_rate >= 2.8) or (tempo < 75.0 and len(beat_times) > 0):
        tempo = tempo * 2.0
    elif tempo > 220.0:
        tempo = tempo / 2.0

    # 4. Time Signature Detection (3/4 vs 4/4)
    time_signature = detect_meter(beat_times, onset_env, sr, hop_length)
    beats_per_bar = time_signature[0]

    # Generate Measure-level Tempo Map and SyncPoints with exact start timestamps
    bar_duration = float(beats_per_bar) * (60.0 / tempo)
    total_dur = float(len(y)) / sr
    num_measures = max(16, int(math.ceil(total_dur / bar_duration)))
    sync_points = []

    t_bar0 = float(beat_times[0]) if len(beat_times) > 0 else 0.0
    for m in range(num_measures):
        b_start_idx = beats_per_bar * m
        b_end_idx = beats_per_bar * (m + 1)
        if b_start_idx < len(beat_times):
            t_bar_start = float(beat_times[b_start_idx])
            if b_end_idx < len(beat_times):
                t_bar_end = float(beat_times[b_end_idx])
                m_dur = max(0.2, t_bar_end - t_bar_start)
                m_bpm = (float(beats_per_bar) * 60.0) / m_dur
            else:
                m_bpm = tempo
        else:
            if len(beat_times) > 0:
                last_beat = float(beat_times[-1])
                remaining_beats = b_start_idx - (len(beat_times) - 1)
                t_bar_start = last_beat + remaining_beats * (60.0 / tempo)
            else:
                t_bar_start = m * bar_duration
            m_bpm = tempo

        frame_offset = max(0, int(round((t_bar_start - t_bar0) * 44100)))
        sync_points.append({
            "bar": m,
            "bpm": round(m_bpm, 3),
            "frame_offset": frame_offset,
            "time_start": float(t_bar_start)
        })

    # 5. Diatonic Key-Signature Scale Prior Masking
    scale_pcs = get_scale_pitch_classes(key_signature[0], key_signature[1]) if key_signature else None

    # Full-Track Probabilistic YIN (PYIN) Fundamental Pitch Tracking
    fmin_hz = librosa.note_to_hz('B0')  # Track fundamental down to B0
    fmax_hz = librosa.note_to_hz('G4')  # Support high fills up to G4
    try:
        f0_pyin, voiced_flag, voiced_probs = librosa.pyin(
            y, fmin=fmin_hz, fmax=fmax_hz, sr=sr, hop_length=hop_length
        )
    except Exception as e:
        print(f"[AI Transcriber] Warning running PYIN ({e}), using fallback CQT only")
        f0_pyin = None
        voiced_probs = None

    # STFT spectral analysis for Slap Thump and Pop Snap
    stft_mag = np.abs(librosa.stft(y, n_fft=1024, hop_length=hop_length))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    high_idx = np.where(freqs >= 2200)[0]
    low_idx = np.where(freqs <= 140)[0]

    raw_onsets = []
    base_midi = 23  # Always analyze down to B0 (23) to catch 5-string and Drop D low notes
    min_interval = 0.058  # Crisp repeated pluck separation (filters pick scrape / transient double triggers)
    last_time = -1.0
    cqt_global_max = np.max(cqt)
    n_cqt_bins = cqt.shape[0]
    search_range = min(54, n_cqt_bins)

    for idx, t in enumerate(onset_times):
        if t - last_time < min_interval:
            continue

        frame_idx = librosa.time_to_frames(t, sr=sr, hop_length=hop_length)
        if frame_idx >= cqt.shape[1]:
            continue

        # Precision attack & sustain window strictly bounded by next onset frame to prevent cross-note bleed
        next_onset_f = librosa.time_to_frames(onset_times[idx + 1], sr=sr, hop_length=hop_length) if idx + 1 < len(onset_times) else cqt.shape[1]
        avail_frames = max(1, next_onset_f - frame_idx)

        att_len = min(avail_frames, 2)
        sus_start = min(frame_idx + 1, frame_idx + avail_frames - 1)
        sus_end = min(frame_idx + avail_frames, frame_idx + 6)
        if sus_end > sus_start:
            spec_col = np.mean(cqt[:, sus_start:sus_end], axis=1)
        else:
            spec_col = np.mean(cqt[:, frame_idx:frame_idx + att_len], axis=1)

        if np.max(spec_col) < 0.030 * cqt_global_max:
            continue

        # Four-Harmonic Product Salience (HPS) with Missing Fundamental Reconstruction
        salience = np.zeros(search_range)
        for b in range(search_range):
            e1 = spec_col[b]
            e2 = spec_col[b + 12] if b + 12 < n_cqt_bins else 0.0
            e3 = spec_col[b + 19] if b + 19 < n_cqt_bins else 0.0
            e4 = spec_col[b + 24] if b + 24 < n_cqt_bins else 0.0
            salience[b] = e1 + 0.85 * e2 + 0.50 * e3 + 0.25 * e4

        # Diatonic scale prior masking (boost in-scale notes by +15% to eliminate out-of-scale buzz)
        if scale_pcs is not None:
            for b in range(search_range):
                pitch_class = (base_midi + b) % 12
                if pitch_class in scale_pcs:
                    salience[b] *= 1.15

        # Full-Track PYIN cross-validation candidate with 3rd-harmonic pull correction
        pyin_cand = None
        if f0_pyin is not None and frame_idx < len(f0_pyin):
            sus_f0_slice = f0_pyin[min(len(f0_pyin)-1, frame_idx + 1):min(len(f0_pyin), frame_idx + 6)]
            valid_pyin_f0 = sus_f0_slice[~np.isnan(sus_f0_slice)]
            if len(valid_pyin_f0) >= 3 and float(np.std(valid_pyin_f0)) < 1.0:
                pyin_hz = float(np.median(valid_pyin_f0))
                if pyin_hz >= 28.0:
                    pyin_cand = int(round(librosa.hz_to_midi(pyin_hz)))
                    # 3rd-Harmonic PYIN Downward Correction:
                    # If the sub-octave-19 bin (true fundamental if PYIN locked on 3f0) has
                    # non-negligible energy AND PYIN candidate bin has much less energy than the
                    # sub-19 bin, PYIN likely tracked the 3rd harmonic. Correct down 19 semitones.
                    if pyin_cand is not None:
                        py_b = pyin_cand - base_midi
                        sub19_b = py_b - 19
                        if 0 <= sub19_b < n_cqt_bins:
                            e_sub19 = spec_col[sub19_b]
                            e_py = spec_col[py_b] if 0 <= py_b < n_cqt_bins else 0.0
                            # Condition: sub-19 has significant energy, PYIN bin is weak vs sub-19
                            if e_sub19 > 0.20 * (np.max(spec_col) + 1e-6) and e_sub19 > 2.5 * (e_py + 1e-6):
                                corrected = pyin_cand - 19
                                if min_allowed_midi <= corrected <= 77:
                                    pyin_cand = corrected

        # Candidate pitches extraction with 3f0 Acoustic Overtone Disambiguation & Attack Flux Gate
        prev_f = max(0, frame_idx - 1)
        att_flux = cqt[:, frame_idx] - cqt[:, prev_f]

        top_bins = np.argsort(salience)[::-1][:4]
        cands = {}
        min_allowed_midi = 23 if is_5string else 26
        for b in top_bins:
            m = base_midi + int(b)
            if m >= min_allowed_midi:
                cands[m] = max(cands.get(m, 0.0), float(salience[b]))
            if b >= 12:
                sub_b = b - 12
                sub_m = base_midi + sub_b
                if sub_m >= min_allowed_midi:
                    # Missing fundamental acoustic phenomenon occurs on the 5th low B string (B0 to Bb1, MIDI 23 to 34).
                    can_be_sub = bool(is_5string and sub_m <= 34)

                    has_3f0 = False
                    if can_be_sub and b + 7 < n_cqt_bins:
                        e_3f0 = spec_col[b + 7]
                        if e_3f0 > 0.06 * spec_col[b]:
                            has_3f0 = True

                    if has_3f0:
                        # Definite 2nd harmonic overtone on sub-bass: boost fundamental sub-octave, dampen overtone
                        cands[sub_m] = max(cands.get(sub_m, 0.0), float(salience[b]) * 1.35)
                        cands[m] = cands.get(m, 0.0) * 0.65
                    elif spec_col[sub_b] >= 0.25 * spec_col[b] and spec_col[sub_b] >= 0.025 * (cqt_global_max + 1e-6):
                        # Sub-octave candidate only if its own CQT energy is real and proportional
                        sub_score = float(salience[sub_b]) * 0.85
                        cands[sub_m] = max(cands.get(sub_m, 0.0), sub_score)

            if b + 12 < search_range:
                up_b = b + 12
                up_m = base_midi + up_b
                up_score = float(salience[up_b]) * 0.90
                cands[up_m] = max(cands.get(up_m, 0.0), up_score)

        if pyin_cand is not None and min_allowed_midi <= pyin_cand <= 77:
            # 5-String Sub-Bass PYIN Downward Redirection:
            # If PYIN locked onto 2f0 harmonic of a sub-bass note (MIDI 23-34)
            if is_5string and pyin_cand >= min_allowed_midi + 12:
                cand_sub = pyin_cand - 12
                if cand_sub <= 34:
                    sub_b = cand_sub - base_midi
                    py_b = pyin_cand - base_midi
                    # 3f0 of cand_sub is py_b + 7
                    has_sub_3f0 = (py_b + 7 < n_cqt_bins and spec_col[py_b + 7] > 0.06 * spec_col[py_b])
                    has_sub_cqt = (sub_b >= 0 and spec_col[sub_b] > 0.02 * (cqt_global_max + 1e-6))
                    if has_sub_3f0 or has_sub_cqt:
                        pyin_cand = cand_sub

            py_b = pyin_cand - base_midi
            e_py = spec_col[py_b] if 0 <= py_b < n_cqt_bins else 0.0
            e_2f0 = spec_col[py_b + 12] if py_b + 12 < n_cqt_bins else 0.0
            e_3f0 = spec_col[py_b + 19] if py_b + 19 < n_cqt_bins else 0.0
            # Multi-branch spectral-auditory integration:
            # Distinguishes genuine fundamental, distorted fundamental, and phantom sub-octave halving
            # Only integrate PYIN if candidate has meaningful spectral energy or is supported by HPS candidates
            if e_py < 0.08 * (np.max(spec_col) + 1e-6) and pyin_cand not in cands:
                pyin_cand = None

        if pyin_cand is not None and min_allowed_midi <= pyin_cand <= 77:
            py_b = pyin_cand - base_midi
            e_py = spec_col[py_b] if 0 <= py_b < n_cqt_bins else 0.0
            e_2f0 = spec_col[py_b + 12] if py_b + 12 < n_cqt_bins else 0.0
            e_3f0 = spec_col[py_b + 19] if py_b + 19 < n_cqt_bins else 0.0
            if e_2f0 <= e_py:
                # Normal case (f0 >= 2f0): PYIN reads fundamental correctly → boost
                cands[pyin_cand] = cands.get(pyin_cand, 0.5) * 1.45
            elif e_3f0 >= 0.08 * e_2f0:
                # 2f0 > f0 BUT 3f0 present → distorted/saturated fundamental → trust PYIN
                # (Sing Alive, BLACK SHOUT, Shoumei Sanka: overdrive makes 2f0 strong but 3f0 exists)
                cands[pyin_cand] = cands.get(pyin_cand, 0.5) * 1.45
            elif e_2f0 > 1.4 * e_py:
                # 2f0 >> f0 (>1.4×) AND 3f0 absent → phantom sub-octave halving
                # PYIN locked on a note one octave below; redirect to +12 semitones
                # (Yomosugara: cello/pickup emphasis makes 2f0 much stronger than f0)
                if py_b + 12 < n_cqt_bins and pyin_cand + 12 <= 77:
                    cands[pyin_cand + 12] = max(cands.get(pyin_cand + 12, 0.5),
                                                float(salience[py_b + 12]) * 1.45)
            # else: 2f0 slightly > f0 AND 3f0 absent → ambiguous/wrong PYIN read → skip
            # (Kao 4st, Sugar Rush: PYIN reads weak spurious sub-harmonics)

        cand_list = sorted([(m, sc) for m, sc in cands.items() if min_allowed_midi <= m <= 77], key=lambda x: x[1], reverse=True)[:5]
        if not cand_list:
            cand_list = [(28, 1.0)]

        # Note duration and release measurement
        next_t = onset_times[idx + 1] if idx + 1 < len(onset_times) else (t + 0.6)
        dt_next = max(0.04, next_t - t)
        max_possible_dur = max(0.035, dt_next - 0.010)

        top_b = int(np.argmax(salience))
        peak_ratio = spec_col[top_b] / (np.sum(spec_col) + 1e-6)
        is_muted = bool(peak_ratio < 0.065 and (np.max(spec_col) < 0.12 * cqt_global_max))

        peak_energy = np.max(spec_col)
        decay_frame_limit = min(cqt.shape[1], frame_idx + int(round(1.5 * sr / hop_length)))
        note_release_frame = frame_idx + 2
        for f in range(frame_idx + 1, decay_frame_limit):
            f_energy = np.max(cqt[:, f])
            if f_energy < 0.15 * peak_energy or f_energy < 0.008 * cqt_global_max:
                note_release_frame = f
                break
        else:
            note_release_frame = decay_frame_limit

        measured_dur = float(librosa.frames_to_time(max(1, note_release_frame - frame_idx), sr=sr, hop_length=hop_length))
        dur = min(0.08, max_possible_dur) if is_muted else min(max_possible_dur, max(0.035, measured_dur))

        # Extract spectral slap thump and pop snap features
        if frame_idx < stft_mag.shape[1]:
            col_stft = stft_mag[:, frame_idx]
            tot_eng = np.sum(col_stft) + 1e-6
            hr = float(np.sum(col_stft[high_idx]) / tot_eng)
            lr = float(np.sum(col_stft[low_idx]) / tot_eng)
        else:
            hr = 0.0
            lr = 0.0

        raw_onsets.append({
            "time": float(t),
            "frame": frame_idx,
            "duration": float(dur),
            "is_muted": is_muted,
            "high_ratio": hr,
            "low_ratio": lr,
            "candidates": cand_list
        })
        last_time = t

    # Viterbi Global Pitch Sequence Decoder
    N_onsets = len(raw_onsets)
    if N_onsets == 0:
        notes = []
    elif N_onsets == 1:
        best_m = raw_onsets[0]["candidates"][0][0]
        notes = [{
            "time": raw_onsets[0]["time"],
            "frame": raw_onsets[0]["frame"],
            "midi": best_m,
            "duration": raw_onsets[0]["duration"],
            "is_muted": raw_onsets[0]["is_muted"],
            "high_ratio": raw_onsets[0]["high_ratio"],
            "low_ratio": raw_onsets[0]["low_ratio"]
        }]
    else:
        dp = [{} for _ in range(N_onsets)]
        bp = [{} for _ in range(N_onsets)]

        for m, sc in raw_onsets[0]["candidates"]:
            dp[0][m] = -math.log(max(1e-6, sc))

        for i in range(1, N_onsets):
            dt = raw_onsets[i]["time"] - raw_onsets[i - 1]["time"]
            for curr_m, curr_sc in raw_onsets[i]["candidates"]:
                best_cost = float('inf')
                best_prev = None
                emit_cost = -math.log(max(1e-6, curr_sc))

                for prev_m in dp[i - 1]:
                    d = abs(curr_m - prev_m)
                    if d == 0:
                        t_cost = 0.0
                    elif d <= 4:
                        t_cost = 0.05 * d
                    elif d in (5, 7):
                        t_cost = 0.12
                    elif d == 6:
                        t_cost = 0.35
                    elif d == 12:
                        t_cost = 0.20
                    elif d in (13, 14):
                        t_cost = 0.50
                    elif d in (17, 19):
                        t_cost = 0.55
                    elif d in (15, 16):
                        t_cost = 0.60
                    elif d in (8, 9, 10, 11):
                        t_cost = 0.60
                    else:
                        if dt > 0.35:
                            t_cost = 0.95 + 0.07 * (d - 12)
                        else:
                            t_cost = 1.45 + 0.09 * (d - 12)

                    cost = dp[i - 1][prev_m] + t_cost + emit_cost
                    if cost < best_cost:
                        best_cost = cost
                        best_prev = prev_m

                dp[i][curr_m] = best_cost
                bp[i][curr_m] = best_prev

        best_last_m = min(dp[N_onsets - 1], key=dp[N_onsets - 1].get)
        optimal_pitches = [best_last_m]
        for i in range(N_onsets - 1, 0, -1):
            optimal_pitches.append(bp[i][optimal_pitches[-1]])
        optimal_pitches.reverse()

        # Transient-Valley Sustained Note Merging
        merged_notes = []
        for i in range(N_onsets):
            m = optimal_pitches[i]
            o = raw_onsets[i]
            t = o["time"]
            f = o["frame"]
            d = o["duration"]

            if not merged_notes:
                merged_notes.append({
                    "time": t,
                    "frame": f,
                    "midi": m,
                    "duration": d,
                    "is_muted": o["is_muted"],
                    "high_ratio": o["high_ratio"],
                    "low_ratio": o["low_ratio"]
                })
            else:
                last = merged_notes[-1]
                dt = t - last["time"]
                is_merge = False
                min_distinct_gap = min(0.105, max(0.078, 0.60 * (15.0 / (tempo if tempo > 0 else 120.0))))
                if last["midi"] == m and dt < min_distinct_gap:
                    is_merge = True

                if is_merge:
                    last["duration"] = max(last["duration"], float((t - last["time"]) + d))
                else:
                    merged_notes.append({
                        "time": t,
                        "frame": f,
                        "midi": m,
                        "duration": d,
                        "is_muted": o["is_muted"],
                        "high_ratio": o["high_ratio"],
                        "low_ratio": o["low_ratio"]
                    })
        notes = merged_notes

    print("__PROGRESS__: 音符转录 78%", flush=True)
    # Auto-detect if song requires 5-string bass (prominent notes <= 24 / low B)
    sub_c1_notes = [n for n in notes if n["midi"] <= 24]
    if len(sub_c1_notes) >= 2 or (len(notes) > 0 and len(sub_c1_notes) / len(notes) > 0.015):
        is_5string = True
    elif not is_5string:
        # 4-string bass: preserve Drop D (26/27) and Drop C# (25) naturally without clamping
        for n in notes:
            if n["midi"] < 23:
                n["midi"] = 23

    # Pre-pass: Audio-Based Slide & Legato Pre-Detection before Viterbi
    for n in notes:
        n.setdefault("slide_to_next", False)
        n.setdefault("slide", 0)
        n.setdefault("hopo_origin", False)
        n.setdefault("hopo_dest", False)

    for i in range(len(notes) - 1):
        n1 = notes[i]
        n2 = notes[i + 1]
        t1, dur1, m1 = n1["time"], n1["duration"], n1["midi"]
        t2, dur2, m2 = n2["time"], n2["duration"], n2["midi"]

        pitch_step = m2 - m1
        t_gap = max(0.0, t2 - (t1 + dur1))
        dt_onsets = t2 - t1

        if 1 <= abs(pitch_step) <= 12 and t_gap <= 0.09:
            f1_idx = librosa.time_to_frames(t1 + dur1 * 0.4, sr=sr, hop_length=hop_length)
            f2_idx = librosa.time_to_frames(t2, sr=sr, hop_length=hop_length)

            local_cqt_peak = max(
                1e-6,
                float(np.max(cqt[:, f1_idx])) if (0 <= f1_idx < cqt.shape[1]) else 0.0,
                float(np.max(cqt[:, min(cqt.shape[1]-1, f2_idx)])) if (0 <= f2_idx <= cqt.shape[1]) else 0.0
            )

            if 0 <= f1_idx < cqt.shape[1] and 0 <= f2_idx <= cqt.shape[1] and f2_idx > f1_idx:
                bridge = cqt[:, f1_idx:f2_idx]
                bridge_energy = float(np.mean(np.max(bridge, axis=0))) if bridge.shape[1] > 0 else 0.0
                has_continuous_tone = bool(bridge_energy >= 0.15 * local_cqt_peak)
            else:
                has_continuous_tone = True

            if has_continuous_tone:
                f2_onset = librosa.time_to_frames(t2, sr=sr, hop_length=hop_length)
                onset_str2 = onset_env[f2_onset] if f2_onset < len(onset_env) else 0.0
                local_max_str = np.max(onset_env[max(0, f2_onset - 4):min(len(onset_env), f2_onset + 5)]) + 1e-6
                attack_ratio = onset_str2 / local_max_str

                # Inspect bridge spectral bins to detect glissando frequency sweep
                b1 = min(m1, m2) - base_midi
                b2 = max(m1, m2) - base_midi
                has_glissando = False
                if 0 <= f1_idx < cqt.shape[1] and 0 <= f2_idx <= cqt.shape[1] and f2_idx - f1_idx >= 1:
                    if b2 - b1 >= 2 and b2 < cqt.shape[0]:
                        inter_bins = cqt[b1 + 1:b2, f1_idx:f2_idx]
                        if inter_bins.size > 0 and np.max(inter_bins) >= 0.18 * local_cqt_peak:
                            has_glissando = True

                # Accurate decision: Slide vs Legato / HOPO
                # Only real frequency glissando is marked as slide
                if has_glissando:
                    n1["slide_to_next"] = True
                    n1["slide"] = 2 if attack_ratio < 0.50 else 1
                elif abs(pitch_step) in (1, 2) and attack_ratio < 0.32 and has_continuous_tone:
                    # Soft-attack 1 or 2 fret slide without re-pluck
                    n1["slide_to_next"] = True
                    n1["slide"] = 2
                elif abs(pitch_step) <= 3 and attack_ratio < 0.45:
                    n1["hopo_origin"] = True
                    n2["hopo_dest"] = True

    optimized_notes = optimize_bass_fretboard_viterbi(notes, is_5string=is_5string)
    print("__PROGRESS__: 指板优化 86%", flush=True)

    # Post-pass: Articulations, Slide Validation, and Slap/Pop
    for i in range(len(optimized_notes)):
        n = optimized_notes[i]
        n["popped"] = False
        n["slapped"] = False

    for i in range(len(optimized_notes) - 1):
        n1 = optimized_notes[i]
        n2 = optimized_notes[i + 1]
        s1, f1 = n1.get("string", 0), n1.get("fret", 0)
        s2, f2 = n2.get("string", 0), n2.get("fret", 0)

        if n1.get("slide_to_next", False):
            if s1 == s2 and f1 != f2 and f1 > 0 and f2 > 0:
                # Verified same-string fretted slide
                pass
            else:
                n1["slide"] = 0
                n1["slide_to_next"] = False

        if n1.get("hopo_origin", False) and n2.get("hopo_dest", False):
            if s1 == s2 and f1 != f2:
                # Verified same-string legato slur
                pass
            else:
                n1["hopo_origin"] = False
                n2["hopo_dest"] = False

    return tempo, optimized_notes, is_5string, sync_points, time_signature

def make_check_audio(raw_audio_path: str, bass_wav: str, out_mp3: str, bass_gain: float = 1.5) -> Optional[str]:
    """核对版 audio: the original song with the separated bass added on top (bass clearly louder), peak-safe.
    Both are decoded by ffmpeg at 44.1 kHz, as for separation, so they share one timeline."""
    import soundfile as sf
    ffmpeg_bin = os.path.join(UVR_TOOLS_DIR, "ffmpeg.exe") if os.path.exists(os.path.join(UVR_TOOLS_DIR, "ffmpeg.exe")) else "ffmpeg"
    tmp_in = out_mp3 + ".mix.wav"
    tmp_out = out_mp3 + ".out.wav"
    try:
        subprocess.run([ffmpeg_bin, "-y", "-i", raw_audio_path, "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", tmp_in],
                       capture_output=True)
        mix, sr = sf.read(tmp_in, always_2d=True)
        bass, sr_b = sf.read(bass_wav, always_2d=True)
        if sr_b != sr:
            import librosa
            bass = librosa.resample(bass.T, orig_sr=sr_b, target_sr=sr).T
        if bass.shape[1] != mix.shape[1]:
            bass = np.repeat(bass[:, :1], mix.shape[1], axis=1)
        n = min(len(mix), len(bass))
        out = mix[:n] + bass_gain * bass[:n]
        peak = float(np.max(np.abs(out))) + 1e-9
        if peak > 0.97:
            out *= 0.97 / peak
        sf.write(tmp_out, out, sr)
        subprocess.run([ffmpeg_bin, "-y", "-i", tmp_out, "-b:a", "320k", out_mp3], capture_output=True)
        return out_mp3 if os.path.exists(out_mp3) else None
    except Exception as e:
        print(f"[AI Transcriber] check audio failed: {e}", flush=True)
        return None
    finally:
        for f in (tmp_in, tmp_out):
            if os.path.exists(f):
                os.remove(f)


def build_complete_tab_project_bassnet(title, artist, franchise, raw_audio_path, output_folder, is_5string=False):
    """BassNet pipeline: max-quality separation -> neural transcription -> beat-grid GP."""
    from bassnet import pipeline
    os.makedirs(output_folder, exist_ok=True)
    print("__PROGRESS__: 伴奏分离 20%", flush=True)
    bass_out = os.path.join(output_folder, "extracted_bass.wav")
    nobass_out = os.path.join(output_folder, "backing_nobass.mp3")
    if os.path.exists(bass_out) and os.path.exists(nobass_out) and os.path.getsize(bass_out) > 10000:
        stems = {"bass": bass_out, "backing": nobass_out}
    else:
        stems = separate_stems_roformer(raw_audio_path, output_folder, overlap=8)
    print("__PROGRESS__: 调性分析 55%", flush=True)
    chords, key_runs = [], []
    try:
        from bassnet import key_detect      # full-song chroma + chord recognition (80% vs 64% on the purchased tabs)
        key_sig, chords, key_runs = key_detect.detect(raw_audio_path, segments=True)
    except Exception as e:
        print(f"[AI Transcriber] key detection fallback: {e}", flush=True)
        key_sig = detect_key_signature(raw_audio_path)
    import torch
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    a_down = None
    stems4 = os.path.join(output_folder, "_stems4")
    if os.path.isdir(stems4):
        if pipeline.USE_ALLIN1:
            a_down = pipeline.allin1_downbeat(stems4)     # song-structure model on the stems (None if unavailable)
        shutil.rmtree(stems4, ignore_errors=True)
    res = pipeline.transcribe_stem(stems["bass"], raw_audio_path, dev=dev, progress=lambda m: print(m, flush=True),
                                   a_down=a_down)
    print("__PROGRESS__: 乐谱封包 90%", flush=True)
    gp_path = os.path.join(output_folder, f"[BASS TAB] {title}.gp")
    from gp_guard import is_protected
    if is_protected(gp_path):
        gp_path = os.path.join(output_folder, f"[BASS TAB] {title} [AI].gp")
    check_audio = None
    if not is_protected(os.path.splitext(gp_path)[0] + " [核对版].gp"):
        check_audio = make_check_audio(raw_audio_path, stems["bass"], os.path.join(output_folder, "check_mix.mp3"))
    info = pipeline.build_gp(res, gp_path, title, artist, franchise, stems["backing"], key=key_sig, prefer_5=is_5string,
                             check_audio=check_audio, chords=chords, key_runs=key_runs)
    print("__PROGRESS__: 已就绪 100%", flush=True)
    return {
        "status": "success", "gp_path": gp_path, "backing_path": stems["backing"], "bass_path": stems["bass"],
        "check_gp": info.get("check_gp", ""),
        "tempo": info["tempo"], "key_signature": key_sig, "time_signature": f"{info['meter']}/4",
        "is_5string": info["is_5string"], "note_count": info["note_count"], "sync_points_count": info["n_bars"],
        "engine": "bassnet", "lint_problems": info.get("lint_problems", -1),
    }


def build_complete_tab_project(
    title: str,
    artist: str,
    franchise: str,
    raw_audio_path: str,
    output_folder: str,
    is_5string: bool = False,
    python_exe: Optional[str] = None
) -> Dict[str, Any]:
    """
    End-to-end pipeline:
    Audio -> Stem Separation -> Key Signature -> Tab Transcription -> GP8 Score Generation -> Auto Downbeat Alignment
    """
    fallback_reason = "no BassNet checkpoint"
    try:
        from bassnet import pipeline as _bn
        if _bn.available():
            return build_complete_tab_project_bassnet(title, artist, franchise, raw_audio_path, output_folder, is_5string)
    except Exception as e:
        import traceback
        traceback.print_exc()
        fallback_reason = f"{type(e).__name__}: {e}"
        print(f"[AI Transcriber] BassNet pipeline failed ({fallback_reason}); falling back to legacy engine", flush=True)
    os.makedirs(output_folder, exist_ok=True)
    print("__PROGRESS__: 伴奏分离 30%", flush=True)

    stems = separate_stems(raw_audio_path, output_folder, python_exe=python_exe)
    bass_wav = stems["bass"]
    backing_nobass = stems["backing"]

    print("__PROGRESS__: 调性分析 50%", flush=True)
    key_sig = detect_key_signature(raw_audio_path)

    print("__PROGRESS__: 音符转录 75%", flush=True)
    tempo, notes, detected_5string, sync_points, time_sig = transcribe_bass_audio(
        bass_wav, is_5string=is_5string, key_signature=key_sig
    )
    final_5string = is_5string or detected_5string

    print("__PROGRESS__: 乐谱封包 92%", flush=True)
    from gp_builder import create_clean_gp_project
    from gp_audio_linker import inject_backing_track_to_gp

    import soundfile as sf
    dur = float(sf.info(raw_audio_path).duration)

    gp_filename = f"[BASS TAB] {title}.gp"
    gp_path = os.path.join(output_folder, gp_filename)
    from gp_guard import is_protected
    if is_protected(gp_path):
        gp_path = os.path.join(output_folder, f"[BASS TAB] {title} [AI].gp")

    create_clean_gp_project(
        output_gp_path=gp_path,
        title=title,
        artist=artist,
        franchise=franchise,
        tempo=tempo,
        is_5string=final_5string,
        audio_path=backing_nobass,
        duration=dur,
        detected_onsets=notes,
        key_signature=key_sig,
        sync_points=sync_points,
        time_signature=time_sig
    )

    if os.path.exists(backing_nobass):
        try:
            inject_backing_track_to_gp(gp_path, backing_nobass)
            print("__PROGRESS__: 伴奏对齐 96%", flush=True)
            from audio_aligner import auto_align_backing_track
            align_res = auto_align_backing_track(gp_path, backing_nobass)
            print(f"[AI Transcriber] Auto alignment applied: {align_res.get('success')}")
        except Exception as e:
            print(f"[AI Transcriber] Warning injecting/aligning backing track: {e}")

    print("__PROGRESS__: 已就绪 100%", flush=True)
    return {
        "status": "success",
        "gp_path": gp_path,
        "backing_path": backing_nobass,
        "bass_path": bass_wav,
        "tempo": tempo,
        "key_signature": key_sig,
        "time_signature": f"{time_sig[0]}/{time_sig[1]}",
        "is_5string": final_5string,
        "note_count": len(notes),
        "sync_points_count": len(sync_points),
        "engine": "legacy",
        "fallback_reason": fallback_reason,
    }

if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description="AI Bass Transcriber CLI")
    parser.add_argument("--title", default="Test")
    parser.add_argument("--artist", default="BanG Dream!")
    parser.add_argument("--franchise", default="BanG Dream!")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--5string", dest="is_5string", action="store_true")

    args = parser.parse_args()
    res = build_complete_tab_project(
        title=args.title,
        artist=args.artist,
        franchise=args.franchise,
        raw_audio_path=args.audio,
        output_folder=args.output,
        is_5string=args.is_5string,
        python_exe=sys.executable
    )
    print("\n__JSON_RESULT_START__")
    print(json.dumps(res, ensure_ascii=False))
    print("__JSON_RESULT_END__")

