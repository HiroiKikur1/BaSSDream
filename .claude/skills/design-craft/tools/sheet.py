"""Contact sheet: references and your renders side by side, optionally with notan rows.

usage: python sheet.py OUT.png IMAGE [IMAGE ...] [--notan] [--cols 3] [--width 640]

Each image is scaled to the same width and labelled with its file name.
With --notan every image gets its 3-value (dark / mid / light) version underneath,
so the value structure of your screen can be compared with the reference at a glance.
Put references first, then your directions; look at the sheet as one image.
"""
import argparse, os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from notan import lab_L_C


def notan_img(im):
    L, _ = lab_L_C(np.asarray(im.filter(ImageFilter.GaussianBlur(4)), dtype=float))
    n = np.full(L.shape + (3,), 245, np.uint8); n[L < 75] = (150, 146, 156); n[L < 40] = (38, 34, 44)
    return Image.fromarray(n)


def font(size):
    for f in ("NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "C:/Windows/Fonts/msyh.ttc", "DejaVuSans.ttf"):
        try: return ImageFont.truetype(f, size)
        except OSError: pass
    return ImageFont.load_default()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out"); ap.add_argument("images", nargs="+")
    ap.add_argument("--notan", action="store_true"); ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--width", type=int, default=640)
    a = ap.parse_args()
    W, pad, lab = a.width, 16, 26
    tiles = []
    for p in a.images:
        im = Image.open(p).convert("RGB")
        im = im.resize((W, round(im.height * W / im.width)), Image.LANCZOS)
        parts = [im] + ([notan_img(im)] if a.notan else [])
        h = sum(x.height for x in parts) + 4 * (len(parts) - 1)
        t = Image.new("RGB", (W, h + lab), (250, 248, 244)); y = lab
        ImageDraw.Draw(t).text((0, 4), os.path.basename(p), fill=(60, 52, 66), font=font(15))
        for x in parts: t.paste(x, (0, y)); y += x.height + 4
        tiles.append(t)
    cols = min(a.cols, len(tiles)); rows = -(-len(tiles) // cols)
    rh = [max(t.height for t in tiles[r * cols:(r + 1) * cols]) for r in range(rows)]
    sheet = Image.new("RGB", (cols * (W + pad) + pad, sum(rh) + pad * (rows + 1)), (250, 248, 244))
    y = pad
    for r in range(rows):
        for c, t in enumerate(tiles[r * cols:(r + 1) * cols]):
            sheet.paste(t, (pad + c * (W + pad), y))
        y += rh[r] + pad
    sheet.save(a.out)
    print(f"sheet {a.out} ({len(tiles)} images)")


if __name__ == "__main__":
    main()
