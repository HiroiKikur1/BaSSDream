"""Audio evidence for tab notes, computed from the separated bass stem CQT (no model involved).

d_down(L): peakiness at the odd harmonics of L-12 (L-12, L+7, L+16) -> the note actually sounds an octave lower
            than written (typical 4-string arrangement of a 5-string / low part).
Used to (1) audit / clean training labels and (2) score how well any tab matches its recording.
"""
import numpy as np

from bassnet.dataset import FPS

MIDI_LO = 21


def semitone_map(cqt):
    """(264, T) CQT (3 bins / semitone from A0) -> (88, T) max over each semitone's bins."""
    return cqt.reshape(88, 3, -1).max(1)


def _peak(S, p, a, b):
    i = p - MIDI_LO
    if i < 1 or i + 1 >= S.shape[0]:
        return 0.0
    seg = S[:, a:b].mean(1)
    return float(seg[i] - 0.5 * (seg[i - 1] + seg[i + 1]))


def d_down(S, L, t, win=(2, 10)):
    a = int(t * FPS) + win[0]
    b = min(S.shape[1], int(t * FPS) + win[1])
    if b <= a:
        return 0.0
    return sum(_peak(S, L + k, a, b) for k in (-12, 7, 16))


def d_here(S, L, t, win=(2, 10)):
    """Evidence that pitch L itself sounds (its fundamental and odd harmonics L, L+19, L+28)."""
    a = int(t * FPS) + win[0]
    b = min(S.shape[1], int(t * FPS) + win[1])
    if b <= a:
        return 0.0
    return sum(_peak(S, L + k, a, b) for k in (0, 19, 28))
