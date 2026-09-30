"""Default dreamy stage backdrop (used when a song has no band key visual)."""
import os
import paths
import math
import random

from PIL import Image, ImageDraw, ImageFilter

W, H = 1920, 1080
OUT = os.path.join(paths.ROOT, r"src-native\assets\stage_default.jpg")


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def build():
    rnd = random.Random(11)
    # base: pink (top-left) -> violet -> sky blue (right)
    small = Image.new("RGB", (192, 108))
    px = small.load()
    for y in range(108):
        for x in range(192):
            u, v = x / 191, y / 107
            c = lerp((250, 150, 215), (190, 140, 245), min(1, u * 1.2))
            c = lerp(c, (150, 200, 250), max(0, u - 0.55) * 1.6)
            c = lerp(c, (200, 150, 235), v * 0.35)
            px[x, y] = c
    img = small.resize((W, H), Image.BICUBIC).convert("RGBA")

    # stage light beams from the top
    beams = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(beams)
    for cx in (260, 700, 1160, 1640):
        top_w, bot_w = 60, 420
        d.polygon([(cx - top_w, -20), (cx + top_w, -20), (cx + bot_w, H * 0.75), (cx - bot_w, H * 0.75)], fill=110)
    beams = beams.filter(ImageFilter.GaussianBlur(60))
    img.paste(Image.new("RGBA", (W, H), (255, 245, 255, 255)), (0, 0), beams)

    # hot spots at the top of each beam
    spots = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(spots)
    for cx in (260, 700, 1160, 1640):
        d.ellipse([cx - 140, -120, cx + 140, 90], fill=230)
    img.paste(Image.new("RGBA", (W, H), (255, 120, 220, 255)), (0, 0), spots.filter(ImageFilter.GaussianBlur(50)))

    # blurred stage props (amps / risers) as soft shapes
    props = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(props)
    for x0, y0, x1, y1 in ((120, 420, 520, 820), (1380, 400, 1820, 830), (650, 520, 1270, 760)):
        d.rounded_rectangle([x0, y0, x1, y1], 30, fill=(170, 110, 210, 110))
    for cx, cy, r in ((330, 640, 110), (1600, 620, 120)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(235, 190, 250, 120))
    img.alpha_composite(props.filter(ImageFilter.GaussianBlur(28)))

    # glowing stage floor strip
    floor = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(floor)
    for x0 in (180, 760, 1320):
        d.rectangle([x0, 820, x0 + 420, 930], fill=235)
    img.paste(Image.new("RGBA", (W, H), (255, 255, 255, 255)), (0, 0), floor.filter(ImageFilter.GaussianBlur(26)))

    # diamond pattern on the lower half
    dia = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(dia)
    s = 90
    for j in range(8, 14):
        for i in range(-1, W // s + 2):
            cx, cy = i * s + (j % 2) * s / 2, j * s * 0.6 + 60
            d.polygon([(cx, cy - s * 0.3), (cx + s / 2, cy), (cx, cy + s * 0.3), (cx - s / 2, cy)], outline=60, width=2)
    img.paste(Image.new("RGBA", (W, H), (255, 255, 255, 255)), (0, 0), dia.filter(ImageFilter.GaussianBlur(1.5)))

    # bokeh + small stars
    bok = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(bok)
    for _ in range(40):
        x, y, r = rnd.randint(0, W), rnd.randint(0, H), rnd.randint(10, 46)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, rnd.randint(25, 70)))
    img.alpha_composite(bok.filter(ImageFilter.GaussianBlur(6)))
    d = ImageDraw.Draw(img)
    for _ in range(28):
        x, y, r = rnd.randint(0, W), rnd.randint(0, H), rnd.choice([8, 11, 14, 18])
        pts = [(x + (r if k % 2 == 0 else r * 0.45) * math.cos(-math.pi / 2 + k * math.pi / 5),
                y + (r if k % 2 == 0 else r * 0.45) * math.sin(-math.pi / 2 + k * math.pi / 5)) for k in range(10)]
        d.polygon(pts, fill=(255, 250, 255, rnd.randint(70, 150)))
    img.convert("RGB").save(OUT, quality=92)
    return OUT


if __name__ == "__main__":
    print(build())
