"""Compare the approved mock with the real implementation screenshot.

usage: python overlay.py MOCK.png REAL.png OUT.png [--grid 8]

Scales REAL to MOCK's size and writes one image with three panels:
  mock | real | difference heat (bright = differs)
and prints the mean difference plus the grid cells that differ most, as
row/col and pixel boxes, so you can say "the header drifted", not "looks close".
Use it after translating a mock to WPF (BassStation.exe --render-* out.png).
"""
import argparse
import numpy as np
from PIL import Image


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mock"); ap.add_argument("real"); ap.add_argument("out")
    ap.add_argument("--grid", type=int, default=8)
    a = ap.parse_args()
    m = Image.open(a.mock).convert("RGB")
    r = Image.open(a.real).convert("RGB").resize(m.size, Image.LANCZOS)
    A, B = np.asarray(m, float), np.asarray(r, float)
    d = np.abs(A - B).mean(2)
    heat = np.clip(d * 3, 0, 255).astype(np.uint8)
    heat_rgb = np.stack([heat, (heat * 0.35).astype(np.uint8), (255 - heat) // 6], 2)
    W, H = m.size
    out = Image.new("RGB", (W * 3 + 32, H), (250, 248, 244))
    out.paste(m, (0, 0)); out.paste(r, (W + 16, 0)); out.paste(Image.fromarray(heat_rgb), (2 * W + 32, 0))
    out.save(a.out)
    g = a.grid
    cells = []
    for i in range(g):
        for j in range(g):
            y0, y1, x0, x1 = H * i // g, H * (i + 1) // g, W * j // g, W * (j + 1) // g
            cells.append((d[y0:y1, x0:x1].mean(), i, j, (x0, y0, x1, y1)))
    cells.sort(reverse=True)
    print(f"mean diff {d.mean():.1f}/255   ({(d > 24).mean():.1%} of pixels differ clearly)")
    for v, i, j, box in cells[:6]:
        print(f"  row {i} col {j}  box {box}  diff {v:.1f}")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
