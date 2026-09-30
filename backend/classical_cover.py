"""Shared jacket for the classical etude category (drawn once, reused by every classical song)."""
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = r"C:\Windows\Fonts"
OUT = r"E:\BassStation\assets\classical_cover.jpg"
S = 1200


def _grad_radial(size, inner, outer, center=(0.42, 0.40)):
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    cx, cy = w * center[0], h * center[1]
    maxd = math.hypot(max(cx, w - cx), max(cy, h - cy))
    for y in range(h):
        for x in range(w):
            t = min(1.0, math.hypot(x - cx, y - cy) / maxd) ** 1.3
            px[x, y] = tuple(int(inner[i] + (outer[i] - inner[i]) * t) for i in range(3))
    return img


def _gold(size, box):
    """Vertical gold gradient image used as a fill for masks."""
    g = Image.new("RGB", size)
    d = ImageDraw.Draw(g)
    top, bot = box[1], box[3]
    stops = [(0.0, (255, 236, 178)), (0.45, (226, 178, 88)), (0.7, (176, 122, 48)), (1.0, (240, 204, 128))]
    for y in range(size[1]):
        t = min(1, max(0, (y - top) / max(1, bot - top)))
        for k in range(len(stops) - 1):
            if stops[k][0] <= t <= stops[k + 1][0]:
                a, b = stops[k], stops[k + 1]
                u = (t - a[0]) / (b[0] - a[0])
                c = tuple(int(a[1][i] + (b[1][i] - a[1][i]) * u) for i in range(3))
                break
        d.line([(0, y), (size[0], y)], fill=c)
    return g


def _star(d, cx, cy, r, fill):
    pts = []
    for k in range(10):
        ang = -math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    d.polygon(pts, fill=fill)


def build():
    rnd = random.Random(7)
    small = _grad_radial((S // 4, S // 4), (92, 30, 52), (16, 10, 24))
    img = small.resize((S, S), Image.BICUBIC).convert("RGBA")

    # fine grain
    noise = Image.effect_noise((S, S), 18).convert("L").point(lambda v: int(v * 0.10))
    img = Image.composite(Image.new("RGBA", (S, S), (255, 235, 210, 255)), img, noise)

    # staff with bass clef and the opening of BWV 1007 (G2 D3 B3 A3 B3 D3 B3 D3), slightly tilted
    L = Image.new("L", (S, S), 0)
    dl = ImageDraw.Draw(L)
    sp = 54                                   # staff space
    top = 380                                 # y of line 5 (A3)
    x0, x1 = 70, S - 70
    line_y = {k: top + (5 - k) * sp for k in range(1, 6)}      # line1 = G2 ... line5 = A3
    for k in range(1, 6):
        dl.line([(x0, line_y[k]), (x1, line_y[k])], fill=150, width=4)
    # clef: scale so its body spans line5 .. slightly below line2, dots straddle line 4 (F3)
    clef_font = ImageFont.truetype(os.path.join(FONTS, "seguisym.ttf"), 400)
    tmp = Image.new("L", (S, S), 0)
    ImageDraw.Draw(tmp).text((0, 0), "𝄢", font=clef_font, fill=255)
    cb = tmp.getbbox()
    clef = tmp.crop(cb)
    target_h = int(sp * 3.35)
    clef = clef.resize((int(clef.width * target_h / clef.height), target_h), Image.LANCZOS)
    cx, cy = x0 + 30, line_y[5] - int(sp * 0.12)
    clef_mask = Image.new("L", (S, S), 0)
    clef_mask.paste(clef, (cx, cy))
    # notes: step = staff position in half-spaces above line1 (G2)
    steps = [0, 4, 9, 8, 9, 4, 9, 4]          # G2 D3 B3 A3 B3 D3 B3 D3
    xs = [cx + clef.width + 150 + i * 86 + (36 if i >= 4 else 0) for i in range(8)]
    heads = []
    for x, st in zip(xs, steps):
        y = line_y[1] - st * sp / 2
        dl.ellipse([x - 25, y - 18, x + 25, y + 18], fill=235)
        heads.append((x, y))
    for g in (heads[:4], heads[4:]):
        beam_y = min(y for _, y in g) - sp * 3.1
        for x, y in g:
            dl.line([(x + 22, y - 4), (x + 22, beam_y)], fill=235, width=6)
        xa, xb = g[0][0] + 19, g[-1][0] + 25
        dl.rectangle([xa, beam_y, xb, beam_y + 13], fill=235)
        dl.rectangle([xa, beam_y + 26, xb, beam_y + 39], fill=235)
    # time signature C (common time)
    ts_font = ImageFont.truetype(os.path.join(FONTS, "georgiab.ttf"), int(sp * 2.6))
    dl.text((cx + clef.width + 28, line_y[4] - sp * 0.2), "C", font=ts_font, fill=220, anchor="lm")
    ang = 6
    L = L.rotate(ang, resample=Image.BICUBIC, center=(S / 2, 520))
    clef_mask = clef_mask.rotate(ang, resample=Image.BICUBIC, center=(S / 2, 520))
    glow = Image.eval(L, lambda v: v).filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.35))
    img.paste(Image.new("RGBA", (S, S), (255, 190, 110, 255)), (0, 0), glow)
    cglow = clef_mask.filter(ImageFilter.GaussianBlur(22)).point(lambda v: int(v * 0.6))
    img.paste(Image.new("RGBA", (S, S), (255, 190, 110, 255)), (0, 0), cglow)
    img.paste(_gold((S, S), (0, 250, S, 800)), (0, 0), L)
    img.paste(_gold((S, S), clef_mask.getbbox()), (0, 0), clef_mask)

    # frame
    d = ImageDraw.Draw(img)
    gold_line = (228, 186, 104, 255)
    d.rectangle([34, 34, S - 35, S - 35], outline=gold_line, width=4)
    d.rectangle([50, 50, S - 51, S - 51], outline=(228, 186, 104, 150), width=2)

    # sparkles
    for _ in range(26):
        x, y = rnd.randint(80, S - 80), rnd.choice([rnd.randint(80, 230), rnd.randint(720, S - 330)])
        _star(d, x, y, rnd.choice([5, 7, 9, 12]), (255, 226, 160, rnd.randint(90, 200)))

    # title band
    band = Image.new("RGBA", (S, 300), (0, 0, 0, 0))
    db = ImageDraw.Draw(band)
    for y in range(300):
        db.line([(0, y), (S, y)], fill=(12, 6, 16, int(210 * min(1, y / 150))))
    img.alpha_composite(band, (0, S - 300))
    title_font = ImageFont.truetype(os.path.join(FONTS, "STKAITI.TTF"), 150)
    sub_font = ImageFont.truetype(os.path.join(FONTS, "palai.ttf"), 50)
    d = ImageDraw.Draw(img)
    t = "古典练习曲"
    w = d.textlength(t, font=title_font)
    tm = Image.new("L", (S, S), 0)
    ImageDraw.Draw(tm).text(((S - w) / 2, S - 290), t, font=title_font, fill=255)
    img.paste(Image.new("RGBA", (S, S), (20, 8, 16, 255)), (6, 6), tm.filter(ImageFilter.GaussianBlur(4)))
    img.paste(_gold((S, S), tm.getbbox()), (0, 0), tm)
    sub = "Études  for  Bass"
    sw = d.textlength(sub, font=sub_font)
    d.text(((S - sw) / 2, S - 120), sub, font=sub_font, fill=(236, 214, 170, 230))
    d.line([((S - sw) / 2 - 90, S - 92), ((S - sw) / 2 - 20, S - 92)], fill=gold_line, width=2)
    d.line([((S + sw) / 2 + 20, S - 92), ((S + sw) / 2 + 90, S - 92)], fill=gold_line, width=2)

    out = img.convert("RGB").resize((800, 800), Image.LANCZOS)
    out.save(OUT, quality=93)
    return OUT


def apply_to(song_ids, covers_dir=r"E:\BassStation\cache\covers"):
    import shutil
    src = OUT if os.path.exists(OUT) else build()
    for sid in song_ids:
        shutil.copy2(src, os.path.join(covers_dir, f"{sid}.jpg"))


if __name__ == "__main__":
    print(build())
