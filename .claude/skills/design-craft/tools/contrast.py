"""WCAG 2 contrast ratio between colours.

usage: python contrast.py FG BG [FG BG ...]      e.g. python contrast.py "#FFFDF7" "#FF3B72"
Targets: body text >= 4.5, large text (>=24px or >=19px bold) and UI parts >= 3.
"""
import sys


def lum(hexcol):
    h = hexcol.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2 or len(args) % 2:
        sys.exit(__doc__)
    for fg, bg in zip(args[::2], args[1::2]):
        r = ratio(fg, bg)
        verdict = "body ok" if r >= 4.5 else "large/UI only" if r >= 3 else "FAIL"
        print(f"{fg} on {bg}: {r:4.2f}  {verdict}")
