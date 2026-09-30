"""Checks a generated .gp against what real Guitar Pro 8 files in the library look like.

Our own parser is lenient (it reads MIDI numbers, ignores order), so a file can round-trip perfectly through it
and still be misread by Guitar Pro 8 (e.g. <Accidental>Sharp</Accidental> made GP8 drop every sharp note).
The profile is learned from the library originals:
  * order     - child A must not precede child B if real files only ever put B before A
  * required  - children present in every real instance of a parent tag
  * values    - leaf text for enumerated tags (few distinct values in real files) must be a known value
Usage: python -m bassnet.gp_lint file.gp [...]      (profile cached in cache/bassnet/gp_profile.json)
"""
import paths
import glob
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict

PROFILE = paths.cache(r"bassnet\gp_profile.json")
# purchased originals only: tabs/ also holds files written by older BassStation code
LIB_GLOBS = [os.path.join(paths.ORIGINALS, "*", "*.gp")]
MAX_ENUM = 40
# free text / numeric-like leaves that are never enumerations
FREE = {"Title", "SubTitle", "Artist", "Album", "Words", "Music", "WordsAndMusic", "Copyright", "Tabber",
        "Instructions", "Notices", "FreeText", "Name", "ShortName", "OriginalFilePath", "OriginalFileSha1",
        "EmbeddedFilePath", "Text", "Description", "Time", "LineCount", "Fret", "Number", "String", "Octave",
        "Pitches", "Value", "Bars", "Beats", "Notes", "Voices", "Position", "Bar", "Float", "Int", "Parameters",
        "BarOccurrence", "BarIndex", "FrameOffset", "ModifiedTempo", "OriginalTempo", "AlternateEndings"}
# containers whose children are records keyed by id (order/required rules do not apply)
LISTS = {"MasterBars", "Bars", "Voices", "Beats", "Notes", "Rhythms", "Tracks", "Automations", "Assets",
         "Properties", "XProperties", "Articulations", "Elements", "Sounds", "ScoreViews", "Staves", "Lyrics",
         "Sections", "Strings", "Frets", "Chords", "Items", "Diagrams", "Diagram", "EffectChain", "Effects"}


def _root(path):
    x = zipfile.ZipFile(path).read("Content/score.gpif").decode("utf-8", "ignore")
    x = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", x)
    return ET.fromstring(x.split("?>", 1)[-1] if x.lstrip().startswith("<?xml") else x)


def _key(el):
    return f"Property[{el.get('name')}]" if el.tag == "Property" else el.tag


def _walk(el, path=""):
    p = f"{path}/{el.tag}"
    yield p, el
    for c in el:
        yield from _walk(c, p)


def build_profile(max_files=400):
    before = defaultdict(lambda: defaultdict(int))     # parent -> "a>b" -> count
    present = defaultdict(lambda: defaultdict(int))
    count = defaultdict(int)
    values = defaultdict(set)
    files = [f for g in LIB_GLOBS for f in glob.glob(g)][:max_files]
    for f in files:
        try:
            r = _root(f)
        except Exception:
            continue
        for p, el in _walk(r):
            kids = [_key(c) for c in el]
            count[p] += 1
            for k in set(kids):
                present[p][k] += 1
            seen = []
            for k in kids:
                for s in seen:
                    if s != k:
                        before[p][f"{s}>{k}"] += 1
                seen.append(k)
            if len(el) == 0:
                values[p].add((el.text or "").strip())
    prof = {"count": count, "present": {k: dict(v) for k, v in present.items()},
            "before": {k: dict(v) for k, v in before.items()},
            "values": {k: sorted(v) for k, v in values.items() if len(v) <= MAX_ENUM},
            "n_files": len(files)}
    json.dump(prof, open(PROFILE, "w", encoding="utf-8"), ensure_ascii=False)
    return prof


def load_profile():
    if os.path.exists(PROFILE):
        return json.load(open(PROFILE, encoding="utf-8"))
    return build_profile()


def cdata_problems(path):
    """Text fields GP8 stores as CDATA (a parsed tree cannot see this, so check the raw XML)."""
    from bassnet.gp_writer import CDATA_TAGS
    x = zipfile.ZipFile(path).read("Content/score.gpif").decode("utf-8", "ignore")
    x = re.sub(r"<InstrumentSet>.*?</InstrumentSet>", "", x, flags=re.S)
    tags = "|".join(CDATA_TAGS)
    bad = re.findall(rf"<({tags})>(?!\s*<!\[CDATA\[)", x) + re.findall(rf"<({tags})\s*/>", x)
    c = defaultdict(int)
    for t in bad:
        c[t] += 1
    return dict(c)


def lint(path, prof=None, max_per_rule=3):
    prof = prof or load_profile()
    r = _root(path)
    errs = defaultdict(list)
    for t, n in cdata_problems(path).items():
        errs[f"text not CDATA: {t}"].append(f"x{n}")
    for p, el in _walk(r):
        tag = p.rsplit("/", 1)[-1]
        kids = [_key(c) for c in el]
        if p not in prof["count"]:
            errs[f"unknown element {p}"].append("")
            continue
        bef = prof["before"].get(p, {})
        if tag not in LISTS:
            for i, a in enumerate(kids):
                for b in kids[i + 1:]:
                    if a != b and bef.get(f"{b}>{a}", 0) > 0 and bef.get(f"{a}>{b}", 0) == 0:
                        errs[f"order {p}: {a} before {b}"].append(el.get("id", ""))
            n = prof["count"][p]
            for k, c in prof["present"].get(p, {}).items():
                if c == n and n >= 5 and k not in kids:
                    errs[f"missing {p}/{k}"].append(el.get("id", ""))
        if len(el) == 0 and p in prof["values"] and tag not in FREE:
            v = (el.text or "").strip()
            if v not in prof["values"][p]:
                errs[f"value {p}={v!r} (known: {prof['values'][p][:8]})"].append(el.get("id", ""))
    return {k: v[:max_per_rule] + ([f"... x{len(v)}"] if len(v) > max_per_rule else []) for k, v in errs.items()}


if __name__ == "__main__":
    if "--rebuild" in sys.argv:
        build_profile()
        sys.argv.remove("--rebuild")
    prof = load_profile()
    for f in sys.argv[1:]:
        e = lint(f, prof)
        print(f"{os.path.basename(f)}: {'OK' if not e else str(len(e)) + ' problems'}")
        for k, v in e.items():
            print("  ", k, v)
