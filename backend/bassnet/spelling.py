"""Enharmonic spelling of bass notes under a key signature (Layer 3, engraving).

Diatonic pitch classes take the letter of the signature's scale; chromatic ones follow the signature direction
(sharps in sharp keys and C, flats in flat keys). The tabs agree with this for 99.5% of 244k notes (the old
always-sharp writer: 82%); a raised-leading-tone rule for minor keys made it worse (the tabs write Db in D minor),
so `mode` is accepted but unused. Checked against the purchased tabs: `python -m bassnet.spelling`.
"""
import os
import re
import sys
import zipfile

LETTERS = "CDEFGAB"
NAT_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ACC_OFS = {"": 0, "#": 1, "b": -1, "##": 2, "bb": -2}
MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)


def tonic_name(sig: int):
    """Major tonic of a signature: (letter, accidental)."""
    names = {-7: ("C", "b"), -6: ("G", "b"), -5: ("D", "b"), -4: ("A", "b"), -3: ("E", "b"), -2: ("B", "b"),
             -1: ("F", ""), 0: ("C", ""), 1: ("G", ""), 2: ("D", ""), 3: ("A", ""), 4: ("E", ""), 5: ("B", ""),
             6: ("F", "#"), 7: ("C", "#")}
    return names[max(-7, min(7, sig))]


def _spell(letter, pc):
    d = (pc - NAT_PC[letter]) % 12
    d = d - 12 if d > 6 else d
    return letter, {0: "", 1: "#", -1: "b", 2: "##", -2: "bb"}.get(d)


def pc_table(sig: int, mode: str = "Major"):
    """{pitch class: (step, accidental)} for all 12 pitch classes."""
    tl, ta = tonic_name(sig)
    tpc = (NAT_PC[tl] + ACC_OFS[ta]) % 12
    li = LETTERS.index(tl)
    table = {}
    for k, st in enumerate(MAJOR_STEPS):
        pc = (tpc + st) % 12
        table[pc] = _spell(LETTERS[(li + k) % 7], pc)
    sharp = sig >= 0
    for pc in range(12):
        if pc in table:
            continue
        # chromatic: raise the letter below (sharp keys) or lower the letter above (flat keys)
        cands = [_spell(L, pc) for L in LETTERS]
        cands = sorted((c for c in cands if c[1] in (("", "#") if sharp else ("", "b"))), key=lambda c: c[1] != "")
        table[pc] = cands[0]
    return table


def spell(midi: int, sig: int = 0, mode: str = "Major", table=None):
    """-> (step, accidental, GP concert octave). GP octave: MIDI 36 = C3, i.e. octave of the natural letter."""
    step, acc = (table or pc_table(sig, mode))[midi % 12]
    nat = midi - ACC_OFS[acc]
    return step, acc, nat // 12


# ------------------------------------------------------------------------- check against the purchased tabs
def tab_spellings(gp):
    """[(midi, signature, mode, step, accidental)] for the bass track of a tab."""
    import xml.etree.ElementTree as ET
    from bassnet.gpif_parser import _pick_bass_track, _note_pitch
    text = zipfile.ZipFile(gp).read("Content/score.gpif").decode("utf-8", "replace")
    root = ET.fromstring(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text))
    t_idx, tuning = _pick_bass_track(root)
    notes_el = {n.get("id"): n for n in root.findall("Notes/Note")}
    beats_el = {b.get("id"): b for b in root.findall("Beats/Beat")}
    voices_el = {v.get("id"): (v.findtext("Beats") or "").split() for v in root.findall("Voices/Voice")}
    bars_el = {b.get("id"): b for b in root.findall("Bars/Bar")}
    out = []
    key = (0, "Major")
    for mb in root.findall("MasterBars/MasterBar"):
        k = mb.find("Key")
        if k is not None:
            key = (int(k.findtext("AccidentalCount") or 0), k.findtext("Mode") or "Major")
        bar_ids = (mb.findtext("Bars") or "").split()
        if t_idx >= len(bar_ids):
            continue
        bar = bars_el.get(bar_ids[t_idx])
        for vid in (bar.findtext("Voices") or "").split() if bar is not None else []:
            for bid in voices_el.get(vid, []):
                b = beats_el.get(bid)
                if b is None:
                    continue
                for nid in (b.findtext("Notes") or "").split():
                    n = notes_el.get(nid)
                    if n is None:
                        continue
                    p = n.find("Properties/Property[@name='ConcertPitch']/Pitch")
                    midi = _note_pitch(n, tuning, 0)[0]
                    if p is None or midi is None:
                        continue
                    out.append((midi, key[0], key[1], p.findtext("Step"), p.findtext("Accidental") or ""))
    return out


def main():
    import collections
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from bassnet.build_stems import build_index
    ok = tot = ok_sharp = 0
    miss = collections.Counter()
    for gp in build_index():
        for midi, sig, mode, step, acc in tab_spellings(gp):
            s, a, _ = spell(midi, sig, mode)
            tot += 1
            ok += (s, a) == (step, acc)
            sharp_s = ["C", "C", "D", "D", "E", "F", "F", "G", "G", "A", "A", "B"][midi % 12]
            ok_sharp += (sharp_s, "#" if midi % 12 in (1, 3, 6, 8, 10) else "") == (step, acc)
            if (s, a) != (step, acc):
                miss[(sig, mode, s + a, step + acc)] += 1
    print(f"notes {tot}  key-aware spelling agrees {ok / tot:.4f}  always-sharp (old writer) {ok_sharp / tot:.4f}")
    for k, n in miss.most_common(15):
        print(k, n)


if __name__ == "__main__":
    main()
