"""Posteriorgram -> note events, beat decoding, and note-level metrics."""
import numpy as np

from bassnet.model import PITCH_LO, N_PITCH

FPS = 22050 / 256


def _peaks(x, thr, min_dist):
    idx = []
    last = -10 ** 9
    for t in range(1, len(x) - 1):
        if x[t] >= thr and x[t] >= x[t - 1] and x[t] > x[t + 1]:
            if t - last < min_dist:
                if x[t] > x[idx[-1]]:
                    idx[-1] = t
                    last = t
                continue
            idx.append(t)
            last = t
    return idx


def grid_slots(beats, subdivs=(2, 4, 3)):
    """Candidate note positions (frames): 8th/16th/triplet slots of every beat."""
    out = set()
    beats = np.asarray(beats, float)
    for a, b in zip(beats[:-1], beats[1:]):
        for d in subdivs:
            for j in range(d):
                out.add(int(round((a + (b - a) * j / d) * FPS)))
    return np.array(sorted(out))


def decode_notes(frame_prob, onset_prob, dead_prob=None, onset_thr=0.5, min_dist=4, legato_thr=0.8,
                 beats=None, weak_thr=0.25, grid_tol=0.035, pw=(2, 10), end_thr=0.5, rep_thr=None, onsets=None):
    """frame_prob (T, 49) softmax, onset_prob (T,). Returns notes with time/end/midi/dead in seconds.
    With beats given, weaker onset peaks are accepted only when they sit on the beat grid.
    onsets: note-start frames chosen elsewhere (bassnet/onset_rescore.py) instead of the threshold rules."""
    T = len(onset_prob)
    pitch_prob = frame_prob[:, 1:]
    rest_prob = frame_prob[:, 0]
    frame_arg = np.argmax(frame_prob, axis=1)          # 0 = rest
    if onsets is not None:
        return _notes_from_onsets(sorted(set(int(o) for o in onsets)), frame_prob, onset_prob, dead_prob, pw, end_thr)

    onsets = set(_peaks(onset_prob, onset_thr, min_dist))
    if beats is not None and len(beats) > 4:
        slots = grid_slots(beats)
        tol = int(round(grid_tol * FPS))
        for t in _peaks(onset_prob, weak_thr, min_dist):
            if t in onsets:
                continue
            k = np.searchsorted(slots, t)
            near = min(abs(t - slots[min(k, len(slots) - 1)]), abs(t - slots[max(k - 1, 0)]))
            if near <= tol and not any(abs(t - o) < min_dist for o in onsets):
                onsets.add(t)
    # legato changes (hammer-on / slide) without a strong onset: pitch class switches and stays
    t = 1
    while t < T - 3:
        a, b = frame_arg[t - 1], frame_arg[t]
        if a > 0 and b > 0 and a != b and all(frame_arg[t + k] == b for k in range(3)):
            if pitch_prob[t:t + 3, b - 1].mean() > legato_thr and not any(abs(t - o) <= min_dist for o in onsets):
                onsets.add(t)
        t += 1
    onsets = sorted(onsets)

    notes = []
    for i, on in enumerate(onsets):
        nxt = onsets[i + 1] if i + 1 < len(onsets) else T
        win = pitch_prob[min(on + pw[0], nxt - 1):min(nxt, on + pw[1])]
        if len(win) == 0:
            continue
        p = int(np.argmax(win.mean(axis=0)))
        # note ends when rest dominates or at next onset
        end = on + 1
        while end < nxt and rest_prob[end] < end_thr:
            end += 1
        dead = bool(dead_prob is not None and dead_prob[on:on + 3].max() > 0.5)
        if rest_prob[on:min(nxt, on + 3)].mean() > 0.7 and not dead:
            continue   # spurious onset on silence
        notes.append({"time": on / FPS, "end": max(end, on + 1) / FPS, "midi": PITCH_LO + p,
                      "dead": dead, "conf": float(win[:, p].mean()), "on_peak": float(onset_prob[on])})
    if rep_thr is not None:
        # weak re-attack of the same pitch while the previous note is still sounding -> one sustained note
        merged = []
        for n in notes:
            prev = merged[-1] if merged else None
            if (prev is not None and not n["dead"] and not prev["dead"] and n["midi"] == prev["midi"]
                    and prev["end"] >= n["time"] - 1.5 / FPS and n["on_peak"] < rep_thr):
                prev["end"] = n["end"]
                continue
            merged.append(n)
        notes = merged
    return notes


def _notes_from_onsets(onsets, frame_prob, onset_prob, dead_prob, pw=(2, 10), end_thr=0.5):
    T = len(onset_prob)
    pitch_prob, rest_prob = frame_prob[:, 1:], frame_prob[:, 0]
    notes = []
    for i, on in enumerate(onsets):
        nxt = onsets[i + 1] if i + 1 < len(onsets) else T
        win = pitch_prob[min(on + pw[0], nxt - 1):min(nxt, on + pw[1])]
        if len(win) == 0:
            continue
        p = int(np.argmax(win.mean(axis=0)))
        end = on + 1
        while end < nxt and rest_prob[end] < end_thr:
            end += 1
        dead = bool(dead_prob is not None and dead_prob[on:on + 3].max() > 0.5)
        notes.append({"time": on / FPS, "end": max(end, on + 1) / FPS, "midi": PITCH_LO + p,
                      "dead": dead, "conf": float(win[:, p].mean()), "on_peak": float(onset_prob[on])})
    return notes


def bass_change_evidence(notes, beat_times, tol=0.07):
    """Per beat: +1 when a note starts on the beat with a pitch class different from the note sounding just
    before (bass lines move to a new root on bar starts far more often than inside bars), centred."""
    ns = sorted((n for n in notes if not n.get("dead")), key=lambda n: n["time"])
    t = np.array([n["time"] for n in ns])
    ev = np.zeros(len(beat_times))
    for i, bt in enumerate(beat_times):
        if not len(t):
            break
        j = int(np.argmin(np.abs(t - bt)))
        if abs(t[j] - bt) > tol or j == 0:
            continue
        ev[i] = float(ns[j]["midi"] % 12 != ns[j - 1]["midi"] % 12)
    return ev - ev.mean()


def bass_entry_evidence(notes, beat_times, tol=0.07, min_rest_beats=1.5):
    """Per beat: 1 when the bass comes back in on this beat after at least `min_rest_beats` of silence (the start of
    the song or of a phrase: players and tabs put such entries on the bar line), centred."""
    bt = np.asarray(beat_times, float)
    ev = np.zeros(len(bt))
    if len(bt) < 3:
        return ev
    per = float(np.median(np.diff(bt)))
    ns = sorted(notes, key=lambda n: n["time"])
    last_end = -1e9
    for n in ns:
        if n["time"] - last_end >= min_rest_beats * per:
            i = int(np.argmin(np.abs(bt - n["time"])))
            if abs(bt[i] - n["time"]) <= tol:
                ev[i] = 1.0
        last_end = max(last_end, n["end"])
    return ev - ev.mean()


def repeat_weights(notes, beats_s, alpha=0.5, span=4):
    """Per-beat evidence weight n^-alpha, n = how many beats start the same `span`-beat bass figure (pitch class
    at every eighth). A riff played 40 times gives one opinion about where the bar starts, not 40 independent ones;
    without this a long repeated passage can outvote the rest of the song and force a phase jump (Henceforth)."""
    bt = np.asarray(beats_s, float)
    n = len(bt)
    if n < 2 or not notes:
        return np.ones(n)
    ns = sorted((x for x in notes if not x.get("dead")), key=lambda x: x["time"])
    on = np.array([x["time"] for x in ns])
    per = np.diff(bt, append=bt[-1] + (bt[-1] - bt[-2]))

    def pc_at(t):
        j = int(np.searchsorted(on, t + 0.04)) - 1
        return ns[j]["midi"] % 12 if j >= 0 and ns[j]["end"] > t - 0.02 else -1
    grid = [pc_at(t + h * p) for t, p in zip(bt, per) for h in (0.0, 0.5)]
    sig = [tuple(grid[2 * i:2 * i + 2 * span]) for i in range(n)]
    from collections import Counter
    cnt = Counter(sig)
    return np.array([float(cnt[s]) ** -alpha if s.count(-1) < span else 1.0 for s in sig])


def downbeats_hmm(dv, meters=(4, 3), p_meter=4.0, p_jump=6.0, w=1.0, eps=0.02, ev=None, ev_w=0.0, emit_w=None):
    """Viterbi over bar positions (meter, k) along the beat sequence.
    k advances by one each beat; a bar may end early/late (phase jump) or switch meter at a cost (in nats).
    emit_w: per-beat weight on the evidence (repeat_weights).
    Returns (downbeat beat indices, prevailing meter)."""
    N = len(dv)
    states = [(m, k) for m in meters for k in range(m)]
    S = len(states)
    lp_on = w * np.log(dv + eps)
    lp_off = w * np.log(1 - dv + eps)
    em = np.array([[lp_on[i] if k == 0 else lp_off[i] for (m, k) in states] for i in range(N)])
    if ev is not None and ev_w:
        # extra per-beat downbeat evidence (centred), e.g. bass root changes
        for s, (m, k) in enumerate(states):
            if k == 0:
                em[:, s] += ev_w * ev
    if emit_w is not None:
        em = em * np.asarray(emit_w, float)[:, None]
    first = [s for s, (m, k) in enumerate(states) if k == 0]
    score = em[0].copy()
    back = np.zeros((N, S), int)
    for i in range(1, N):
        new = np.full(S, -1e18)
        arg = np.zeros(S, int)
        best_prev = int(np.argmax(score))
        for s, (m, k) in enumerate(states):
            if k > 0:
                src = states.index((m, k - 1))
                cands = [(score[src], src)]
            else:
                # regular bar end of the same meter, meter switch at bar end, or a phase jump from anywhere
                cands = [(score[states.index((m, m - 1))], states.index((m, m - 1)))]
                for m2 in meters:
                    if m2 != m:
                        cands.append((score[states.index((m2, m2 - 1))] - p_meter, states.index((m2, m2 - 1))))
                cands.append((score[best_prev] - p_jump, best_prev))
            v, a = max(cands)
            new[s], arg[s] = v + em[i, s], a
        score, back[i] = new, arg
    s = int(np.argmax(score))
    path = [s]
    for i in range(N - 1, 0, -1):
        s = back[i, s]
        path.append(s)
    path = path[::-1]
    downs = [i for i, s in enumerate(path) if states[s][1] == 0]
    ms = [states[s][0] for s in path]
    meter = int(np.bincount(ms).argmax())
    return downs, meter


def decode_beats(beat_prob, down_prob, min_bpm=40, max_bpm=280, tight=60.0, bias=0.25, jump=1.2,
                 down_mode="hmm", hmm_kw=None, strong_frac=0.8, notes=None, chg_w=0.0, entry_w=0.0,
                 phase_lock=0.0, down_fn=None, redundancy=0.0):
    """Ellis-style DP beat tracker with local tempo continuity and a weak global-period prior.
    down_fn(beat times in s) -> per-beat downbeat probability, replacing the raw posterior (bassnet/downbeat_ctx)."""
    T = len(beat_prob)
    min_gap = max(2, int(FPS * 60 / max_bpm))
    max_gap = int(FPS * 60 / min_bpm)
    act = beat_prob - bias
    # global period from autocorrelation of the activation
    x = beat_prob - beat_prob.mean()
    ac = np.correlate(x, x, mode="full")[T - 1:T - 1 + max_gap + 1]
    seg = ac[min_gap:max_gap + 1]
    # smallest lag with a strong local peak (multiples of the true period score almost as high)
    peaks = [i for i in range(1, len(seg) - 1) if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1]]
    strong = [i for i in peaks if seg[i] >= strong_frac * seg.max()]
    P = min_gap + (strong[0] if strong else int(np.argmax(seg)))
    score = np.full(T, -1e9)
    back = -np.ones(T, int)
    gap_of = np.full(T, float(P))
    for t in range(T):
        best, arg = 0.0, -1          # starting a new chain costs nothing
        lo, hi = max(0, t - max_gap), t - min_gap
        if hi >= lo:
            cand = np.arange(lo, hi + 1)
            g = t - cand
            pen = -np.minimum(tight * np.log(g / gap_of[cand]) ** 2, jump) - np.minimum(0.25 * tight * np.log(g / P) ** 2, 0.5)
            v = score[cand] + pen
            j = int(np.argmax(v))
            if v[j] > best:
                best, arg = v[j], cand[j]
        score[t] = act[t] + best
        if arg >= 0:
            back[t] = arg
            gap_of[t] = t - arg
    end_lo = max(0, T - max_gap)
    t = end_lo + int(np.argmax(score[end_lo:]))
    # allow trailing silence: take the best-scoring frame overall among chain ends
    t = int(np.argmax(score)) if score.max() > score[t] + 1.0 else t
    path = []
    while t >= 0:
        path.append(t)
        t = back[t]
    beats = np.array(path[::-1])
    if len(beats) < 4:
        return beats / FPS, beats[:1] / FPS, 4
    dv = np.array([down_prob[max(0, b - 2):b + 3].max() for b in beats])
    if down_fn is not None:
        dv = np.clip(np.asarray(down_fn(beats / FPS), float), 1e-3, 1 - 1e-3)
    if down_mode == "hmm":
        kw = dict(hmm_kw or {})
        if notes and redundancy:
            kw["emit_w"] = repeat_weights(notes, beats / FPS, redundancy)
        if notes and (chg_w or entry_w):
            ev = np.zeros(len(beats))
            if chg_w:
                ev += chg_w * bass_change_evidence(notes, beats / FPS)
            if entry_w:
                ev += entry_w * bass_entry_evidence(notes, beats / FPS)
            kw.update(ev=ev, ev_w=1.0)
        filled, meter = downbeats_hmm(dv, **kw)
        if phase_lock and len(filled) > 4:
            # song-level phase: the majority phase of the first pass is right for ~all songs, while backbeat
            # ambiguity makes whole passages drift by half a bar -> reward that phase on every bar and re-decode
            votes = np.bincount(np.array(filled) % meter, minlength=meter)
            p = int(votes.argmax())
            lock = np.zeros(len(beats))
            lock[p::meter] = 1.0
            ev0 = kw.get("ev", np.zeros(len(beats))) * kw.get("ev_w", 1.0)
            kw.update(ev=ev0 + phase_lock * (lock - lock.mean()), ev_w=1.0)
            filled, meter = downbeats_hmm(dv, **kw)
        return beats / FPS, beats[np.array(filled)] / FPS, meter
    # global meter/phase as fallback grid
    best = (4, 0, -1.0)
    for meter in (4, 3, 2):
        for ph in range(meter):
            v = float(dv[ph::meter].mean())
            if v > best[2] + (0.03 if meter != 4 else 0):
                best = (meter, ph, v)
    meter, ph, _ = best
    # locally: take confident downbeats, fill gaps with the prevailing meter
    idx = [i for i in range(len(beats)) if dv[i] > 0.45]
    if len(idx) < 4:
        idx = list(range(ph, len(beats), meter))
    filled = [idx[0]]
    for i in idx[1:]:
        gap = i - filled[-1]
        if gap < 2:
            continue
        while gap > meter + meter // 2:
            filled.append(filled[-1] + meter)
            gap = i - filled[-1]
        filled.append(i)
    while filled[0] - meter >= 0:
        filled.insert(0, filled[0] - meter)
    while filled[-1] + meter < len(beats):
        filled.append(filled[-1] + meter)
    lens = np.diff(filled)
    meter = int(np.bincount(lens).argmax()) if len(lens) else meter
    return beats / FPS, beats[np.array(filled)] / FPS, meter


def note_metrics(ref, est, onset_tol=0.05):
    """Greedy onset matching. Returns precision/recall/F1 (onset+pitch) and pitch accuracy on onset matches."""
    ref = sorted(ref, key=lambda n: n["time"])
    est = sorted(est, key=lambda n: n["time"])
    rt = np.array([n["time"] for n in ref])
    used = np.zeros(len(ref), bool)
    m_onset = m_pitch = 0
    j0 = 0
    for e in est:
        while j0 < len(rt) and rt[j0] < e["time"] - onset_tol:
            j0 += 1
        best, bd = -1, 1e9
        j = j0
        while j < len(rt) and rt[j] <= e["time"] + onset_tol:
            if not used[j]:
                d = abs(rt[j] - e["time"]) - (0.02 if ref[j]["midi"] == e["midi"] else 0)
                if d < bd:
                    best, bd = j, d
            j += 1
        if best >= 0:
            used[best] = True
            m_onset += 1
            m_pitch += int(ref[best]["midi"] == e["midi"])
    P = m_pitch / max(1, len(est))
    R = m_pitch / max(1, len(ref))
    return {
        "P": P, "R": R, "F1": 2 * P * R / max(1e-9, P + R),
        "onset_F1": 2 * m_onset / max(1, len(est) + len(ref)),
        "pitch_acc_on_matched": m_pitch / max(1, m_onset),
        "n_ref": len(ref), "n_est": len(est),
    }
