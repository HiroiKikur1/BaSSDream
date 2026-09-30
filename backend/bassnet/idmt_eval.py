"""Real DI bass lines with string/fret annotations (IDMT-SMT-Bass-Single-Tracks, 17 tracks, evaluation only).

No separation involved: the recorded bass itself goes through BassNet. Scores
  notes      onset (50 ms) + pitch F1 against the annotation
  fingering  on onset+pitch matches, the share where our fret assignment (fretboard.assign_frets, learned from the
             tab library) picks the player's string, and the share within the same hand position (|fret diff| <= 2
             on the same string or the same note class of position)
python -m bassnet.idmt_eval
"""
import glob
import os
import sys
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.join("D:" + os.sep, "BassData", "IDMT-SMT-BASS-SINGLE-TRACKS")


class _N:
    def __init__(self, midi, t):
        self.midi, self.t, self.string, self.fret = midi, t, 0, 0


def main():
    import librosa
    from bassnet.dataset import compute_cqt, compute_mel
    from bassnet.pipeline import posteriors
    from bassnet.decode import decode_notes, note_metrics
    from bassnet.fretboard import assign_frets
    tot = {"F1": [], "pitch": [], "string": [], "fret": []}
    for x in sorted(glob.glob(os.path.join(ROOT, "annotation", "*.xml"))):
        r = ET.parse(x).getroot()
        wav = os.path.join(ROOT, "audio", r.findtext("globalParameter/audioFileName"))
        tuning = [int(v) for v in r.findtext("globalParameter/instrumentTuning").split(",")]
        ref = [{"time": float(e.findtext("onsetSec")), "end": float(e.findtext("offsetSec")),
                "midi": int(e.findtext("pitch")), "string": int(e.findtext("stringNumber")),
                "fret": int(e.findtext("fretNumber")), "dead": e.findtext("excitationStyle") == "DN", "grace": False}
               for e in r.findall("transcription/event")]
        y, _ = librosa.load(wav, sr=22050, mono=True)
        cqt, mel = compute_cqt(y), compute_mel(y)
        T = min(cqt.shape[1], mel.shape[1])
        fr, on, de, be, do = posteriors(cqt[:, :T], mel[:, :T])
        est = decode_notes(fr, on, de)
        m = note_metrics(ref, est)
        # fingering: assign frets to our notes, compare on matched notes (IDMT strings: 1 = lowest)
        objs = [_N(n["midi"], n["time"]) for n in est if not n.get("dead")]
        assign_frets(objs, tuning, times=[o.t for o in objs])
        rt = np.array([n["time"] for n in ref])
        s_ok = f_ok = k = 0
        for o in objs:
            j = int(np.argmin(np.abs(rt - o.t)))
            if abs(rt[j] - o.t) > 0.05 or ref[j]["midi"] != o.midi or ref[j]["dead"]:
                continue
            k += 1
            s_ok += (o.string + 1) == ref[j]["string"]
            f_ok += (o.string + 1) == ref[j]["string"] and o.fret == ref[j]["fret"]
        tot["F1"].append(m["F1"])
        tot["pitch"].append(m["pitch_acc_on_matched"])
        tot["string"].append(s_ok / max(1, k))
        tot["fret"].append(f_ok / max(1, k))
        print(f"{os.path.basename(x)} notes {len(ref):4d} F1 {m['F1']:.3f} pitch {m['pitch_acc_on_matched']:.3f} "
              f"string {s_ok / max(1, k):.3f}", flush=True)
    print({k: round(float(np.mean(v)), 3) for k, v in tot.items()})


if __name__ == "__main__":
    main()
