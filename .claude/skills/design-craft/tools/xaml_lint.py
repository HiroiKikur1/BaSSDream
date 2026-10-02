"""Smell report for WPF XAML (and C# that builds visuals).

usage: python xaml_lint.py PATH [PATH ...] [--tokens Themes/Palette.xaml]

Reports, per file and in total:
  literal colours outside the token file      -> name it in the palette, or say why not
  DropShadowEffect: zero depth (glow), blur >= 16, pure black/grey colour
  CornerRadius values and how many distinct     -> one value everywhere = card kit
  FontSize values and neighbouring steps < 1.15x -> "middle mush"
  uppercase English labels in Text/Content       -> AGENTS.md rule 3
  emoji / dingbat characters used as icons
  Margin/Padding numbers that are not multiples of 2
  BevelBitmapEffect / BitmapEffect (obsolete, software-rendered)
These are smells. A smell with a written reason is fine; one without is a bug.
"""
import argparse, collections, os, re, sys

HEX = re.compile(r'(?<![\w&])#(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b')
CS_COLOR = re.compile(r'Color\.FromA?Rgb\(|Colors\.(Black|White|Gray|Grey)\b|Brushes\.(Black|White|Gray)\b')
SHADOW = re.compile(r'<DropShadowEffect\b[^>]*>', re.S)
RADIUS = re.compile(r'CornerRadius="([^"]+)"')
FONTSIZE = re.compile(r'FontSize="([\d.]+)"')
TEXT = re.compile(r'(?:Text|Content|Header|Title)="([^"{]+)"')
SPACING = re.compile(r'(?:Margin|Padding)="([^"{]+)"')
EMOJI = re.compile('[☀-➿\U0001F300-\U0001FAFF⭐✅]')


def files(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, _, fs in os.walk(p):
                if any(x in root for x in ("bin", "obj", "node_modules")): continue
                for f in fs:
                    if f.endswith((".xaml", ".cs")): yield os.path.join(root, f)
        else:
            yield p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+"); ap.add_argument("--tokens", default="Palette.xaml")
    a = ap.parse_args()
    tot = collections.Counter(); radii = collections.Counter(); sizes = collections.Counter()
    for f in files(a.paths):
        try: s = open(f, encoding="utf-8-sig").read()
        except (OSError, UnicodeDecodeError): continue
        notes = []
        xaml = f.endswith(".xaml")
        if not f.endswith(os.path.basename(a.tokens)):
            n = len(HEX.findall(s)) if xaml else len(CS_COLOR.findall(s))
            if n: notes.append(f"{n} literal colours"); tot["literal colours"] += n
        for sh in SHADOW.findall(s):
            tags = []
            if re.search(r'ShadowDepth="0(\.0+)?"', sh): tags.append("zero depth")
            m = re.search(r'BlurRadius="([\d.]+)"', sh)
            if m and float(m.group(1)) >= 16: tags.append(f"blur {m.group(1)}")
            if re.search(r'Color="(Black|#(FF)?000000|Gray|#(FF)?808080)"', sh): tags.append("black/grey")
            if tags: notes.append("shadow: " + ", ".join(tags)); tot["shadow smells"] += 1
        for v in RADIUS.findall(s): radii[v] += 1
        for v in FONTSIZE.findall(s): sizes[float(v)] += 1
        caps = [t for t in TEXT.findall(s) if re.fullmatch(r"[A-Z][A-Z0-9 &·/\-]{2,}", t.strip())]
        if caps: notes.append("uppercase labels: " + " | ".join(sorted(set(caps))[:6])); tot["uppercase labels"] += len(caps)
        emo = set(EMOJI.findall(s))
        if emo: notes.append("emoji/dingbats: " + " ".join(sorted(emo))); tot["emoji icons"] += len(emo)
        odd = [v for sp in SPACING.findall(s) for v in re.split(r"[ ,]+", sp.strip())
               if re.fullmatch(r"-?\d+(\.\d+)?", v) and float(v) % 2]
        if odd: notes.append(f"{len(odd)} odd spacing values ({' '.join(sorted(set(odd))[:8])})"); tot["odd spacing"] += len(odd)
        if "BitmapEffect" in s: notes.append("BitmapEffect (obsolete)"); tot["BitmapEffect"] += 1
        if notes: print(f"{f}\n  " + "\n  ".join(notes))
    print("\n== totals")
    for k, v in tot.items(): print(f"  {k}: {v}")
    if radii:
        print(f"  CornerRadius: {len(radii)} distinct; top: " + ", ".join(f"{k}×{v}" for k, v in radii.most_common(6)))
    if sizes:
        ss = sorted(sizes)
        tight = [f"{x:g}/{y:g}" for x, y in zip(ss, ss[1:]) if y / x < 1.15]
        print(f"  FontSize: {len(ss)} distinct [{' '.join(f'{x:g}' for x in ss)}]")
        if tight: print(f"    steps closer than 1.15x: {' '.join(tight)}")


if __name__ == "__main__":
    sys.exit(main())
