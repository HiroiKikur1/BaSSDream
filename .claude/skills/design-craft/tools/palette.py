"""Pull a working palette out of source art (cover, MV still, stage photo).

usage: python palette.py IMAGE [-k 8] [-o swatch.png]

k-means in CIELAB on a downscaled copy. Prints each cluster as
hex, share of the image, L* (lightness) and C* (chroma), sorted by share,
then suggests roles:
  ink    darkest cluster with some hue   -> text / heavy block / tinted shadow
  paper  lightest low-chroma cluster     -> carrier surface
  theme  most chromatic cluster with real area (>= 3%) -> the one theme plane
  accent most chromatic small cluster    -> focus point only
Roles are a starting point: pick against the real ground (Albers), not on a swatch card.
Saves a swatch strip (width proportional to share) next to the image or at -o.
"""
import argparse, os
import numpy as np
from PIL import Image, ImageDraw


def srgb_to_lab(rgb):
    c = rgb / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = lin @ M.T / np.array([0.9505, 1.0, 1.089])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], 1)


def kmeans(x, k, iters=30, seed=0):
    rng = np.random.default_rng(seed)
    cent = x[rng.choice(len(x), 1)]
    for _ in range(1, k):  # k-means++ init
        d = ((x[:, None] - cent[None]) ** 2).sum(-1).min(1)
        cent = np.vstack([cent, x[rng.choice(len(x), 1, p=d / d.sum())]])
    for _ in range(iters):
        lab = ((x[:, None] - cent[None]) ** 2).sum(-1).argmin(1)
        new = np.array([x[lab == i].mean(0) if (lab == i).any() else cent[i] for i in range(k)])
        if np.allclose(new, cent): break
        cent = new
    return lab


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image"); ap.add_argument("-k", type=int, default=8); ap.add_argument("-o")
    a = ap.parse_args()
    im = Image.open(a.image).convert("RGB")
    im.thumbnail((200, 200))
    rgb = np.asarray(im, dtype=float).reshape(-1, 3)
    lab = srgb_to_lab(rgb)
    idx = kmeans(lab, a.k)
    rows = []
    for i in range(a.k):
        m = idx == i
        if not m.any(): continue
        mean_rgb = rgb[m].mean(0)
        L, A, B = lab[m].mean(0)
        rows.append(dict(hex="#%02X%02X%02X" % tuple(int(round(v)) for v in mean_rgb),
                         share=m.mean(), L=L, C=float(np.hypot(A, B))))
    rows.sort(key=lambda r: -r["share"])
    for r in rows:
        print(f"{r['hex']}  {r['share']:5.1%}  L {r['L']:5.1f}  C {r['C']:5.1f}")
    hue = [r for r in rows if r["C"] > 6] or rows
    ink = min(hue, key=lambda r: r["L"])
    paper = max(rows, key=lambda r: r["L"] - 0.8 * r["C"])
    big = [r for r in rows if r["share"] >= 0.03] or rows
    theme = max(big, key=lambda r: r["C"])
    accent = max(rows, key=lambda r: r["C"] - 40 * r["share"])
    print(f"\nink {ink['hex']}  paper {paper['hex']}  theme {theme['hex']}  accent {accent['hex']}")

    out = a.o or os.path.splitext(a.image)[0] + "_palette.png"
    W, H = 720, 90
    sw = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(sw); x = 0
    for r in rows:
        w = max(1, round(r["share"] * W))
        d.rectangle([x, 0, x + w, H], fill=r["hex"]); x += w
    sw.save(out)
    print(f"swatch {out}")


if __name__ == "__main__":
    main()
