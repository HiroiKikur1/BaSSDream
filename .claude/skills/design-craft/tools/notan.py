"""Value-structure (notan) + tone audit for UI screenshots.

usage: python notan.py OUTDIR IMAGE [IMAGE ...]

For each image: downscale to 720 wide, blur, convert to CIE L*, bucket into
dark (<40) / mid (40-75) / light (>=75), measure vivid (chroma>55) and
pale-tint (light, low chroma) share, and save a 3-value thumbnail
OUTDIR/<name>_notan.png. Compare your screen against a reference the user likes.

Rough targets (adjust per project):
  display screens: dark+mid >= 25%; vivid 4-12% (15-25% with one theme plane)
  tool screens:    dark+mid >= 15%; vivid >= 3%
  pale-tint <= 12% everywhere (more reads as "soft")
"""
import os, sys
import numpy as np
from PIL import Image, ImageFilter


def lab_L_C(rgb):
    c = rgb / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = lin @ M.T / np.array([0.9505, 1.0, 1.089])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1]); b = 200 * (f[..., 1] - f[..., 2])
    return L, np.hypot(a, b)


def audit(path, out):
    name = os.path.splitext(os.path.basename(path))[0]
    im = Image.open(path).convert("RGB")
    im = im.resize((720, max(1, round(im.height * 720 / im.width))), Image.LANCZOS)
    L, C = lab_L_C(np.asarray(im, dtype=float))
    Lb, _ = lab_L_C(np.asarray(im.filter(ImageFilter.GaussianBlur(4)), dtype=float))
    dark, mid, light = (Lb < 40).mean(), ((Lb >= 40) & (Lb < 75)).mean(), (Lb >= 75).mean()
    vivid = (C > 55).mean(); pale = ((C > 8) & (C < 30) & (L > 80)).mean()
    print(f"{name:18s} dark {dark:5.1%}  mid {mid:5.1%}  light {light:5.1%} | dark+mid {dark + mid:5.1%} | "
          f"vivid {vivid:5.1%}  pale-tint {pale:5.1%} | L p5/p50/p95 {np.percentile(L, 5):3.0f}/{np.percentile(L, 50):3.0f}/{np.percentile(L, 95):3.0f}")
    n = np.full(Lb.shape + (3,), 255, np.uint8); n[Lb < 75] = (150, 150, 150); n[Lb < 40] = (30, 30, 30)
    Image.fromarray(n).save(os.path.join(out, name + "_notan.png"))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    os.makedirs(sys.argv[1], exist_ok=True)
    for p in sys.argv[2:]:
        audit(p, sys.argv[1])
