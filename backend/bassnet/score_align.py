"""Score-to-audio alignment (any recording version) and derived-GP writing.

1. coarse: subsequence DTW between score-rendered bass/chroma features and audio features
2. fine: per-bar search (+-120 ms) maximising bass-pitch energy at the notated pitches
3. quality: fraction of notes whose pitch is present in the aligned audio
"""
import os
import re
import sys
import uuid
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths  # noqa: E402
from bassnet.gpif_parser import parse_gp, q_to_sec, SAMPLE_RATE  # noqa: E402

SR = 22050
HOP_C = 2048            # coarse DTW hop (~93 ms)
HOP_F = 256             # fine hop (~11.6 ms)
LATENCY_CAL = 0.035     # measured systematic lag of the fine features (s)
QUALITY_MIN = 1.8       # pitch-specificity ratio; ~1.0 means the audio does not match the score
LO_MIDI, N_SEMI = 21, 72


def _cqt_semis(y, hop):
    import librosa
    C = np.abs(librosa.cqt(y, sr=SR, hop_length=hop, fmin=librosa.midi_to_hz(LO_MIDI), n_bins=N_SEMI, bins_per_octave=12))
    return np.log1p(C * 30.0)


def _audio_feats(C):
    bass = C[:30]                                   # MIDI 21..50
    chroma = np.zeros((12, C.shape[1]))
    for i in range(C.shape[0]):
        chroma[(LO_MIDI + i) % 12] += C[i]
    f = np.vstack([bass / (np.linalg.norm(bass, axis=0, keepdims=True) + 1e-6),
                   0.7 * chroma / (np.linalg.norm(chroma, axis=0, keepdims=True) + 1e-6)])
    return f


def _score_feats(notes, n_frames, t_of, hop):
    bass = np.zeros((30, n_frames))
    chroma = np.zeros((12, n_frames))
    fps = SR / hop
    for n in notes:
        a = int(t_of(n.qpos) * fps)
        b = max(a + 1, int(t_of(n.qpos + n.qdur) * fps))
        a, b = max(0, a), min(n_frames, b)
        if a >= b:
            continue
        p = n.midi - LO_MIDI
        if 0 <= p < 30:
            bass[p, a:b] += 1.0
        if 0 <= p + 12 < 30:
            bass[p + 12, a:b] += 0.5
        chroma[n.midi % 12, a:b] += 1.0
    return np.vstack([bass / (np.linalg.norm(bass, axis=0, keepdims=True) + 1e-6),
                      0.7 * chroma / (np.linalg.norm(chroma, axis=0, keepdims=True) + 1e-6)])


def _align_cqt(gp_path: str, audio_path: str, y: Optional[np.ndarray] = None) -> Dict:
    """Legacy CQT-only alignment (fallback when the BassNet models are unavailable)."""
    import librosa
    info = parse_gp(gp_path)
    notes = [n for n in info.notes if not n.grace and not n.dead]
    if y is None:
        y, _ = librosa.load(audio_path, sr=SR, mono=True)

    # nominal score clock (score tempo only, no sync)
    nominal = _nominal_anchors(info)
    t_of = lambda q: q_to_sec(nominal, float(q))  # noqa: E731
    total_q = float(info.bars[-1][2] + info.bars[-1][3])
    n_sf = int(t_of(total_q) * SR / HOP_C) + 1

    Ca = _cqt_semis(y, HOP_C)
    A = _audio_feats(Ca)
    S = _score_feats(notes, n_sf, t_of, HOP_C)
    active = np.where(S.sum(0) > 0)[0]
    s0, s1 = active.min(), active.max() + 1           # align only the part with notes
    Sx = S[:, s0:s1]
    cost = 1.0 - Sx.T @ A                               # cosine distance (unit columns)
    cost[:, A.sum(0) < 1e-3] = 1.0
    try:
        D, wp = librosa.sequence.dtw(C=cost, subseq=True, step_sizes_sigma=np.array([[1, 1], [1, 2], [2, 1]]),
                                     weights_add=np.array([0, 0, 0]), weights_mul=np.array([1, 1, 2]))
    except Exception:
        return {"info": info, "bar_q": [], "bar_t": [], "quality": 0.0, "note_time": lambda n: 0.0}
    wp = wp[::-1]
    sf = (wp[:, 0] + s0) * HOP_C / SR
    af = wp[:, 1] * HOP_C / SR
    # monotone map nominal-score-time -> audio-time
    u_s, idx = np.unique(sf, return_index=True)
    a_med = np.array([np.median(af[wp[:, 0] + s0 == int(round(v * SR / HOP_C))]) for v in u_s])

    def coarse(tn):
        return float(np.interp(tn, u_s, a_med, left=a_med[0] - (u_s[0] - tn), right=a_med[-1] + (tn - u_s[-1])))

    # per-bar anchors from coarse map, smoothed
    bar_q = [float(b[2]) for b in info.bars] + [total_q]
    bar_t = np.array([coarse(t_of(q)) for q in bar_q])
    bar_t = _smooth_monotone(bar_t)

    # fine refinement: per-bar shift chosen by a Viterbi over bars (smooth shifts, global evidence)
    Cf = _cqt_semis(y, HOP_F)
    Ef = Cf / (Cf[:36].max(0, keepdims=True) + 1e-3)
    fps = SR / HOP_F
    flux = np.pad(np.maximum(0.0, Ef[:, 2:] - Ef[:, :-2]), ((0, 0), (1, 1)))
    Sx_ = np.abs(librosa.stft(y, n_fft=1024, hop_length=HOP_F))
    lowf = np.pad(np.maximum(0.0, np.diff(np.log1p(Sx_[3:24]), axis=1)).sum(0), (1, 0))
    lowf = lowf / (np.percentile(lowf, 99) + 1e-6)
    Tn = min(flux.shape[1], len(lowf))
    steps = np.arange(-0.30, 0.3001, 1 / fps)
    nb = len(bar_q) - 1
    obj = np.zeros((nb, len(steps)))
    for i in range(nb):
        q0, q1 = bar_q[i], bar_q[i + 1]
        sel = [n for n in notes if q0 <= float(n.qpos) < q1]
        if not sel:
            continue
        base = np.array([bar_t[i] + (float(n.qpos) - q0) / (q1 - q0) * (bar_t[i + 1] - bar_t[i]) for n in sel])
        ps = np.clip(np.array([n.midi - LO_MIDI for n in sel]), 0, N_SEMI - 13)
        for j, sft in enumerate(steps):
            fr = np.clip(np.round((base + sft) * fps).astype(int), 0, Tn - 1)
            obj[i, j] = (lowf[fr] + 0.5 * flux[ps, fr] + 0.5 * flux[ps + 12, fr]).sum()
        rng = obj[i].max() - obj[i].min()
        obj[i] = (obj[i] - obj[i].min()) / (rng + 1e-9) * min(1.0, len(sel) / 4.0)
    lam = 0.04
    J = len(steps)
    dist = np.abs(np.arange(J)[:, None] - np.arange(J)[None, :]) * lam
    score = obj[0].copy()
    back = np.zeros((nb, J), int)
    for i in range(1, nb):
        m = score[:, None] - dist                       # prev j -> cur k
        back[i] = np.argmax(m, axis=0)
        score = m[back[i], np.arange(J)] + obj[i]
    path = [int(np.argmax(score))]
    for i in range(nb - 1, 0, -1):
        path.append(back[i][path[-1]])
    path = path[::-1]
    shifts = np.array([steps[j] for j in path] + [steps[path[-1]]])
    bar_t = _smooth_monotone(bar_t + shifts)

    # band DTW at fine resolution around the bar-level map: sustained pitch + per-pitch onset features
    def coarse_bar_map(q):
        k = int(np.searchsorted(bar_q, q, side="right") - 1)
        k = min(max(k, 0), len(bar_q) - 2)
        q0, q1 = bar_q[k], bar_q[k + 1]
        return bar_t[k] + (q - q0) / (q1 - q0) * (bar_t[k + 1] - bar_t[k])
    q_first = float(notes[0].qpos) - 1.0
    q_last = float(max(n.qpos + n.qdur for n in notes)) + 0.5
    nom0, nom1 = t_of(q_first), t_of(q_last)
    Ns = int((nom1 - nom0) * fps) + 1
    roll = np.zeros((30, Ns)); onr = np.zeros((30, Ns))
    for n in notes:
        a_ = int((t_of(n.qpos) - nom0) * fps); b_ = max(a_ + 2, int((t_of(n.qpos + n.qdur) - nom0) * fps))
        p_ = n.midi - LO_MIDI
        if 0 <= p_ < 30 and 0 <= a_ < Ns:
            roll[p_, a_:min(Ns, b_)] = 1.0
            onr[p_, a_:min(Ns, a_ + 3)] = 1.0
            if p_ + 12 < 30:
                roll[p_ + 12, a_:min(Ns, b_)] = 0.4
    SF = np.vstack([roll, 1.5 * onr])
    AF = np.vstack([Ef[:30, :Tn], 1.5 * flux[:30, :Tn] / (flux[:30, :Tn].max(0, keepdims=True) + 1e-3)])
    SF = SF / (np.linalg.norm(SF, axis=0, keepdims=True) + 1e-6)
    AF = AF / (np.linalg.norm(AF, axis=0, keepdims=True) + 1e-6)
    # score frame i -> nominal time -> q -> coarse audio time
    nom_q = np.interp(np.arange(Ns) / fps + nom0, [s_ for q_, s_ in info.score_anchors], [q_ for q_, s_ in info.score_anchors])
    centre = np.array([coarse_bar_map(q) for q in nom_q]) * fps
    R = 26
    INF = 1e18
    Dprev = None; lo_prev = 0
    backptr = []
    for i in range(Ns):
        lo = int(max(0, centre[i] - R)); hi = int(min(Tn - 1, centre[i] + R))
        if hi < lo:
            lo, hi = max(0, min(Tn - 1, int(centre[i]))), max(0, min(Tn - 1, int(centre[i])))
        c = 1.0 - SF[:, i] @ AF[:, lo:hi + 1] if SF[:, i].any() else np.full(hi - lo + 1, 0.5)
        D = np.full(hi - lo + 1, INF); bp = np.zeros(hi - lo + 1, np.int8)
        for jj in range(hi - lo + 1):
            j = lo + jj
            best, arg = (0.0 if i == 0 else INF), 0
            if Dprev is not None:
                k = j - 1 - lo_prev
                if 0 <= k < len(Dprev) and Dprev[k] < best:
                    best, arg = Dprev[k], 1           # diagonal
                k = j - lo_prev
                if 0 <= k < len(Dprev) and Dprev[k] + 0.5 * c[jj] < best:
                    best, arg = Dprev[k] + 0.5 * c[jj], 2   # score advances, audio holds
            if jj > 0 and D[jj - 1] + 0.5 * c[jj] < best:
                best, arg = D[jj - 1] + 0.5 * c[jj], 3      # audio advances, score holds
            D[jj] = best + c[jj]; bp[jj] = arg
        backptr.append((lo, bp)); Dprev = D; lo_prev = lo
    # backtrack
    i = Ns - 1; lo, bp = backptr[i]; jj = int(np.argmin(Dprev)); j = lo + jj
    first_audio = np.full(Ns, -1.0)
    while i >= 0:
        first_audio[i] = j
        lo, bp = backptr[i]
        mv = bp[j - lo]
        if mv == 1:
            i -= 1; j -= 1
        elif mv == 2:
            i -= 1
        elif mv == 3:
            j -= 1
        else:
            break
    fine_map_s = np.arange(Ns) / fps + nom0
    fine_map_a = first_audio / fps - LATENCY_CAL
    ok = first_audio >= 0
    fine_map_s, fine_map_a = fine_map_s[ok], np.maximum.accumulate(fine_map_a[ok])

    def note_time(n):
        return float(np.interp(t_of(n.qpos), fine_map_s, fine_map_a))

    # bar starts from the fine map where available
    bar_t = np.array([float(np.interp(t_of(q), fine_map_s, fine_map_a)) if fine_map_s[0] <= t_of(q) <= fine_map_s[-1]
                      else coarse_bar_map(q) for q in bar_q])
    bar_t = _smooth_monotone(bar_t)
    # quality = pitch specificity: bass-band chroma at the notated pitch class vs. transposed pitch classes
    bassb = np.expm1(Cf[2:25]) ** 2                      # linear power, MIDI 23..45
    chroma = np.zeros((12, bassb.shape[1]))
    for i_ in range(bassb.shape[0]):
        chroma[(LO_MIDI + 2 + i_) % 12] += bassb[i_]
    chroma /= chroma.sum(0, keepdims=True) + 1e-6
    v = np.zeros(12)
    cnt = 0
    for n in notes:
        a_ = int((note_time(n) + LATENCY_CAL) * fps) + 1
        if 0 <= a_ < chroma.shape[1] - 4:
            seg = chroma[:, a_:a_ + 4].mean(1)
            v += np.roll(seg, -(n.midi % 12))
            cnt += 1
    hits, tot = (v[0] / (v[1:].mean() + 1e-9), 1.0) if cnt else (0.0, 1.0)
    return {"info": info, "bar_q": bar_q, "bar_t": bar_t.tolist(), "quality": float(hits / tot),
            "note_time": note_time}


# ---------------------------------------------------------------------------------------------
# BassNet-based alignment: model posteriors of the separated bass (pitch / onset / beat) instead
# of raw CQT, a coarse DTW, then a beat-level DP with a smooth-tempo prior.

POST_DIR = paths.cache(r"bassnet\align_post")
PFPS = SR / 256                 # posterior frame rate (bassnet.dataset HOP)
P_LO = 23                       # posterior pitch classes 1..48 = MIDI 23..70
COARSE_H = 8                    # coarse DTW hop in posterior frames (~93 ms)
BEAT_W = 30                     # beat DP search radius around the current estimate (frames, ~350 ms)
LAM = 0.08                      # smoothness: cost per frame of change in beat interval
LAM1 = 0.1                      # cost per frame of deviation from the slowly varying reference tempo
REF_HALF = 16                   # reference tempo = running median over +-16 beats
LAM0 = 0.0                      # cost per frame of deviation from the song-wide audio/score tempo ratio
W_BEAT, W_DOWN = 0.35, 0.35     # weights of the beat / downbeat activations


def _md5(path: str) -> str:
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:16]


def _audio_posteriors(audio_path: str, y: np.ndarray):
    """(fr, on, be, do) BassNet posteriors of the recording, cached by audio md5."""
    md5 = _md5(audio_path)
    cp = os.path.join(POST_DIR, md5 + ".npz")
    if os.path.exists(cp):
        z = np.load(cp)
        return [z[k].astype(np.float32) for k in ("fr", "on", "be", "do")]
    import librosa
    import eval_session
    from bassnet.dataset import compute_cqt, compute_mel
    from bassnet.pipeline import posteriors
    stem = next((p for p in (os.path.join(d, md5 + ".flac") for d in eval_session.STEMS) if os.path.exists(p)), None)
    if stem is None:
        stem = eval_session._separate(audio_path, md5)
    ys, _ = librosa.load(stem, sr=SR, mono=True)
    cqt = compute_cqt(ys)
    mel = compute_mel(y)
    T = cqt.shape[1]
    mel = mel[:, :T] if mel.shape[1] >= T else np.pad(mel, ((0, 0), (0, T - mel.shape[1])))
    fr, on, _de, be, do = posteriors(cqt, mel)
    os.makedirs(POST_DIR, exist_ok=True)
    np.savez_compressed(cp, fr=fr.astype(np.float16), on=on.astype(np.float16), be=be.astype(np.float16),
                        do=do.astype(np.float16))
    return fr, on, be, do


def _events(info):
    """Score onsets grouped by position: (qpos, [midi...], dead_only)."""
    ev = {}
    for n in info.notes:
        if n.grace:
            continue
        e = ev.setdefault(round(float(n.qpos), 6), [[], True])
        if not n.dead:
            e[0].append(n.midi)
            e[1] = False
    return [(q, m, d) for q, (m, d) in sorted(ev.items())]


def _beat_grid(info, total_q):
    """Beat positions (quarters) in playback order, same beat unit as the training labels."""
    g = []
    for bi, oc, q0, ql in info.bars:
        num, den = info.time_sigs[bi]
        step = 1.5 if (den == 8 and num % 3 == 0) else 4.0 / den if den >= 4 else 1.0
        k = 0.0
        while k < float(ql) - 1e-6:
            g.append((float(q0) + k, k == 0))
            k += step
    g.append((total_q, True))
    return np.array([q for q, _ in g]), np.array([d for _, d in g])


def _coarse_map(fr, events, info, t_of, total_q):
    """Subsequence DTW (score at nominal tempo vs. pitch posteriors); returns nominal-sec -> audio-sec."""
    import librosa
    H = COARSE_H
    Tc = fr.shape[0] // H
    P = fr[:Tc * H, 1:].reshape(Tc, H, -1).max(1).T                 # (48, Tc)
    rest = fr[:Tc * H, 0].reshape(Tc, H).mean(1)
    chroma = np.zeros((12, Tc))
    for i in range(P.shape[0]):
        chroma[(P_LO + i) % 12] += P[i]
    fc = PFPS / H

    def unit(x):
        return x / (np.linalg.norm(x, axis=0, keepdims=True) + 1e-6)

    A = unit(np.vstack([unit(P), 0.7 * unit(chroma), 0.5 * rest[None]]))
    n_sf = int(t_of(total_q) * fc) + 1
    roll = np.zeros((P.shape[0], n_sf))
    sch = np.zeros((12, n_sf))
    for n in info.notes:
        if n.grace or n.dead:
            continue
        a = int(t_of(float(n.qpos)) * fc)
        b = max(a + 1, int(t_of(float(n.qpos + n.qdur)) * fc))
        a, b = max(0, a), min(n_sf, b)
        p = min(max(n.midi - P_LO, 0), P.shape[0] - 1)
        roll[p, a:b] = 1.0
        sch[n.midi % 12, a:b] = 1.0
    srest = (roll.sum(0) == 0).astype(float)
    S = unit(np.vstack([unit(roll), 0.7 * unit(sch), 0.5 * srest[None]]))
    qs = np.array([q for q, _, _ in events])
    s0 = max(0, int(t_of(qs.min()) * fc) - 4)
    s1 = min(n_sf, int(t_of(qs.max()) * fc) + 8)
    cost = 1.0 - S[:, s0:s1].T @ A
    D, wp = librosa.sequence.dtw(C=cost, subseq=True, step_sizes_sigma=np.array([[1, 1], [1, 2], [2, 1]]),
                                 weights_add=np.array([0, 0, 0]), weights_mul=np.array([1, 1, 2]))
    wp = wp[::-1]
    sf = wp[:, 0] + s0
    u = np.unique(sf)
    a_med = np.array([np.median(wp[sf == v, 1]) for v in u])
    # trust only path points where the score has notes (rests/intros are unconstrained), then
    # median-filter the audio-score offset over +-3 s against local DTW slips
    act = srest[u] == 0
    if act.sum() >= 8:
        u, a_med = u[act], a_med[act]
    off = a_med - u
    h = int(3.0 * fc)
    off = np.array([np.median(off[max(0, i - h):i + h + 1]) for i in range(len(off))])
    return u / fc, np.maximum.accumulate((u + off) / fc)


def _event_evidence(fr, on):
    """Per-frame onset strength and pitch support (next ~70 ms), octave neighbours at half weight."""
    from scipy.ndimage import maximum_filter1d
    on_s = maximum_filter1d(on, 3)
    P = fr[:, 1:]
    cs = np.vstack([np.zeros((1, P.shape[1])), np.cumsum(P, 0)])
    T = P.shape[0]
    a = np.clip(np.arange(T) + 1, 0, T)
    b = np.clip(np.arange(T) + 7, 0, T)
    ps = (cs[b] - cs[a]) / np.maximum(b - a, 1)[:, None]
    pso = ps.copy()
    pso[:, 12:] += 0.5 * ps[:, :-12]
    pso[:, :-12] += 0.5 * ps[:, 12:]
    return on_s, pso


def _beat_dp(grid_q, grid_down, centre, nom, iref, iglob, lam1, events, on_s, pso, be, do, W=BEAT_W):
    """Beat times (frames) maximising note + beat evidence under a second-order tempo smoothness prior.
    State = (frame of beat b-1, frame of beat b), each within +-W of `centre`; iref = reference interval
    (frames) per beat, pulling against slow drift that the second-order term alone does not see."""
    T = len(on_s)
    nb = len(grid_q)
    D = 2 * W + 1
    offs = np.arange(-W, W + 1)
    base = np.round(centre).astype(int)
    frames = base[:, None] + offs[None, :]                            # (nb, D)
    fcl = np.clip(frames, 0, T - 1)
    beat_ev = W_BEAT * be[fcl] + W_DOWN * do[fcl] * grid_down[:, None]
    beat_ev[(frames < 0) | (frames >= T)] = 0.0
    ev_q = np.array([q for q, _, _ in events])
    ev_lo = np.searchsorted(ev_q, grid_q, side="left")
    NEG = -1e9

    def emit(b):
        """(D, D) evidence of the notes in [grid b, grid b+1) given the two beat frames."""
        fj = frames[b][:, None].astype(float)
        fk = frames[b + 1][None, :].astype(float)
        out = np.zeros((D, D))
        span = grid_q[b + 1] - grid_q[b]
        for e in range(ev_lo[b], ev_lo[b + 1]):
            q, mids, dead = events[e]
            x = (q - grid_q[b]) / span
            f = np.clip(np.round(fj + x * (fk - fj)).astype(int), 0, T - 1)
            if dead or not mids:
                out += 0.5 * on_s[f]
            else:
                p = np.clip(np.array(mids) - P_LO, 0, pso.shape[1] - 1)
                sup = np.minimum(1.0, pso[f][..., p].mean(-1))
                out += on_s[f] * (0.25 + 0.75 * sup)
        ivl = fk - fj
        nomf = nom[b] * PFPS
        out -= lam1 * np.abs(ivl - iref[b]) + LAM0 * np.abs(ivl - iglob[b])
        out[(ivl < max(1.0, 0.4 * nomf)) | (ivl > 2.5 * nomf + 2)] = NEG
        return out

    S = emit(0) + beat_ev[0][:, None] + beat_ev[1][None, :]
    back = np.zeros((nb, D, D), np.int16)
    for b in range(1, nb - 1):
        Iprev = (frames[b][None, :] - frames[b - 1][:, None]).astype(float)       # (i, j)
        Inew = (frames[b + 1][None, :] - frames[b][:, None]).astype(float)        # (j, k)
        ratio = nom[b] / max(nom[b - 1], 1e-6)
        tot = S[:, :, None] - LAM * np.abs(Inew[None, :, :] - ratio * Iprev[:, :, None])
        bi = np.argmax(tot, axis=0)                                                # (j, k)
        S = np.take_along_axis(tot, bi[None], 0)[0] + emit(b) + beat_ev[b + 1][None, :]
        back[b] = bi
    j, k = np.unravel_index(int(np.argmax(S)), S.shape)
    path = [k, j]
    for b in range(nb - 2, 0, -1):
        i = back[b][j, k]
        path.append(i)
        j, k = i, j
    path = np.array(path[::-1])
    return frames[np.arange(nb), path].astype(float), path - W


def _ref_intervals(frames, nom):
    """Beat intervals (frames) of the running-median tempo ratio audio/score over +-REF_HALF beats."""
    nomf = np.asarray(nom) * PFPS
    r = np.diff(frames) / np.maximum(nomf, 1e-6)
    n = len(r)
    sm = np.array([np.median(r[max(0, i - REF_HALF):i + REF_HALF + 1]) for i in range(n)])
    return sm * nomf, np.median(r) * nomf


def _pitch_quality(y, note_times, notes):
    """Pitch specificity: bass-band chroma at the notated pitch class vs. the other classes."""
    Cf = _cqt_semis(y, HOP_F)
    fps = SR / HOP_F
    bassb = np.expm1(Cf[2:25]) ** 2
    chroma = np.zeros((12, bassb.shape[1]))
    for i_ in range(bassb.shape[0]):
        chroma[(LO_MIDI + 2 + i_) % 12] += bassb[i_]
    chroma /= chroma.sum(0, keepdims=True) + 1e-6
    v = np.zeros(12)
    cnt = 0
    for t, n in zip(note_times, notes):
        a_ = int(t * fps) + 1
        if 0 <= a_ < chroma.shape[1] - 4:
            v += np.roll(chroma[:, a_:a_ + 4].mean(1), -(n.midi % 12))
            cnt += 1
    return float(v[0] / (v[1:].mean() + 1e-9)) if cnt else 0.0


def align(gp_path: str, audio_path: str, y: Optional[np.ndarray] = None) -> Dict:
    import librosa
    if y is None:
        y, _ = librosa.load(audio_path, sr=SR, mono=True)
    try:
        fr, on, be, do = _audio_posteriors(audio_path, y)
    except Exception as e:                       # no models / no GPU: legacy CQT alignment
        print(f"[score_align] posteriors unavailable ({e}); using CQT alignment", file=sys.stderr)
        return _align_cqt(gp_path, audio_path, y)
    info = parse_gp(gp_path)
    res = align_posteriors(info, fr, on, be, do)
    notes = [n for n in info.notes if not n.grace and not n.dead]
    res["quality"] = _pitch_quality(y, [res["note_time"](n) for n in notes], notes)
    return res


def align_posteriors(info, fr, on, be, do) -> Dict:
    """Score-to-recording alignment from BassNet posteriors of the recording (see align)."""
    events = _events(info)
    notes = [n for n in info.notes if not n.grace and not n.dead]
    nominal = _nominal_anchors(info)
    t_of = lambda q: q_to_sec(nominal, float(q))  # noqa: E731
    total_q = float(info.bars[-1][2] + info.bars[-1][3])
    T = len(on)

    u, a_med = _coarse_map(fr, events, info, t_of, total_q)
    grid_q, grid_down = _beat_grid(info, total_q)
    grid_nom = np.array([t_of(q) for q in grid_q])
    centre = np.interp(grid_nom, u, a_med)
    # outside the DTW path: continue at nominal tempo from its ends
    lo, hi = grid_nom < u[0], grid_nom > u[-1]
    centre[lo] = a_med[0] - (u[0] - grid_nom[lo])
    centre[hi] = a_med[-1] + (grid_nom[hi] - u[-1])
    centre *= PFPS
    # beat DP over the grid points that fall inside the recording
    inside = np.where((centre >= 0) & (centre < T))[0]
    b0, b1 = int(inside.min()), int(inside.max()) + 1
    evs = [e for e in events if grid_q[b0] <= e[0] < grid_q[b1 - 1]]
    nom = np.diff(grid_nom[b0:b1])
    evid = _event_evidence(fr, on)
    c = centre[b0:b1].copy()
    for lam1 in (0.2 * LAM1, LAM1):
        # pass 1: loose reference from the coarse map; pass 2: reference = pass-1 tempo, median-smoothed
        iref, iglob = _ref_intervals(c, nom)
        for _ in range(4):                               # re-centre while the path hugs the window edge
            bf, d = _beat_dp(grid_q[b0:b1], grid_down[b0:b1], c, nom, iref, iglob, lam1, evs, *evid, be, do)
            if np.abs(d).max() < BEAT_W - 2:
                break
            c = bf
        c = bf
    beat_t = np.full(len(grid_q), np.nan)
    beat_t[b0:b1] = bf / PFPS
    # beyond the recording: continue with the edge tempo (8-beat average)
    k = min(8, b1 - b0 - 1)
    r0 = (beat_t[b0 + k] - beat_t[b0]) / max(grid_nom[b0 + k] - grid_nom[b0], 1e-6) if k > 0 else 1.0
    r1 = (beat_t[b1 - 1] - beat_t[b1 - 1 - k]) / max(grid_nom[b1 - 1] - grid_nom[b1 - 1 - k], 1e-6) if k > 0 else 1.0
    beat_t[:b0] = beat_t[b0] - (grid_nom[b0] - grid_nom[:b0]) * r0
    beat_t[b1:] = beat_t[b1 - 1] + (grid_nom[b1:] - grid_nom[b1 - 1]) * r1

    bar_q = [float(b[2]) for b in info.bars] + [total_q]
    bar_t = np.interp(bar_q, grid_q, beat_t)

    def note_time(n):
        return float(np.interp(float(n.qpos), grid_q, beat_t))

    return {"info": info, "bar_q": bar_q, "bar_t": bar_t.tolist(), "note_time": note_time,
            "beat_q": grid_q.tolist(), "beat_t": beat_t.tolist()}


def _nominal_anchors(info):
    """Score-tempo clock (tempo automations only, backing-track sync ignored)."""
    return info.score_anchors


def _smooth_monotone(t):
    t = np.asarray(t, float).copy()
    for i in range(1, len(t)):
        if t[i] <= t[i - 1] + 0.05:
            t[i] = t[i - 1] + 0.05
    return t


SYNC_TOL = 0.010   # max deviation (s) of any bar start from the straight line between two sync points


def _score_bpm_at(info, q):
    sa = info.score_anchors
    for (q0, t0), (q1, t1) in zip(sa, sa[1:]):
        if q0 <= q < q1 and t1 > t0:
            return (q1 - q0) * 60.0 / (t1 - t0)
    return float(info.tempo0)


def sync_bars(info, bar_q, bar_t, tol=SYNC_TOL):
    """Indices of the bars that get a SyncPoint: as few as possible while every bar start stays within
    `tol` of the aligned time; bars around score tempo changes always get one."""
    bar_q = np.asarray(bar_q, float)
    bar_t = np.asarray(bar_t, float)
    nb = len(bar_q) - 1
    must = {0}
    tq = [q for q, _ in info.score_anchors]
    for q in tq[1:-1]:
        k = int(np.searchsorted(bar_q, q, side="right") - 1)
        if 0 <= k < nb and abs(_score_bpm_at(info, q - 1e-6) - _score_bpm_at(info, q + 1e-6)) > 1e-3:
            must.update({k, min(k + 1, nb)})
    keep = [0]
    k = 0
    while k < nb:
        m = k + 1
        while m + 1 <= nb and not any(i in must for i in range(k + 1, m + 1)):
            cand = m + 1
            qs, ts = bar_q[k:cand + 1], bar_t[k:cand + 1]
            line = ts[0] + (qs - qs[0]) * (ts[-1] - ts[0]) / (qs[-1] - qs[0])
            if np.abs(line - ts).max() > tol:
                break
            m = cand
        keep.append(m)
        k = m
    return sorted(set(i for i in keep if i < nb))


def write_derived_gp(src_gp: str, audio_path: str, res: Dict, out_gp: str) -> str:
    """Copies the score untouched, embeds the audio, and writes SyncPoints where the tempo changes."""
    from gp_guard import derived_meta
    with zipfile.ZipFile(src_gp) as z:
        entries = {i.filename: z.read(i.filename) for i in z.infolist() if not i.filename.startswith("Content/Assets/")}
    text = entries["Content/score.gpif"].decode("utf-8", errors="replace")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    root = ET.fromstring(text)
    info = res["info"]
    bar_t = res["bar_t"]
    t0 = bar_t[0]
    mt = root.find("MasterTrack")
    autos = mt.find("Automations")
    if autos is None:
        autos = ET.SubElement(mt, "Automations")
    for a in list(autos):
        if a.findtext("Type") == "SyncPoint":
            autos.remove(a)
    bar_q = res["bar_q"]
    keep = sync_bars(info, bar_q, bar_t)
    for n, k in enumerate(keep):
        bi, oc, q0, ql = info.bars[k]
        nxt = keep[n + 1] if n + 1 < len(keep) else len(info.bars)
        dq = bar_q[nxt] - bar_q[k]
        dur = max(1e-3, bar_t[nxt] - bar_t[k])
        a = ET.SubElement(autos, "Automation")
        for tag, v in (("Type", "SyncPoint"), ("Linear", "false"), ("Bar", str(bi)), ("Position", "0"), ("Visible", "true")):
            ET.SubElement(a, tag).text = v
        val = ET.SubElement(a, "Value")
        ET.SubElement(val, "BarIndex").text = str(bi)
        ET.SubElement(val, "BarOccurrence").text = str(oc)
        ET.SubElement(val, "ModifiedTempo").text = f"{dq * 60.0 / dur:.5f}"
        ET.SubElement(val, "OriginalTempo").text = str(int(round(_score_bpm_at(info, float(q0)))))
        ET.SubElement(val, "FrameOffset").text = str(int(round((bar_t[k] - t0) * SAMPLE_RATE)))
    # backing track + GP8 layout/CDATA/zip conventions shared with the transcription writer
    from bassnet.gp_writer import attach_backing, save_gp
    attach_backing(root, entries, audio_path, t0)
    entries["meta.json"] = derived_meta(True)
    return save_gp(root, entries, out_gp)


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--gp", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    try:
        r = align(a.gp, a.audio)
        ok = r["quality"] >= QUALITY_MIN and len(r["bar_t"]) > 1
        if ok:
            write_derived_gp(a.gp, a.audio, r, a.out)
        print("__ALIGN_JSON__" + json.dumps({"ok": ok, "quality": round(r["quality"], 3), "out": a.out if ok else ""}))
    except Exception as e:
        print("__ALIGN_JSON__" + json.dumps({"ok": False, "quality": 0.0, "error": str(e)}))
