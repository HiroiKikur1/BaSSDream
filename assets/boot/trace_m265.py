"""Atelier Z M#265 -> flat vector layers in its own colours (semantic trace).

Mask from a fitted background (cast shadow removed), pixels classed as body (metallic pink), maple, chrome
(light / dark) and black by colour; each class smoothed into clean shapes and filled with the class's median
photo colour; the body gets its own light-to-deep metallic gradient sampled from the photo.
"""
import sys

import cv2
import numpy as np

SRC = '/root/.claude/uploads/f9d4672c-5c75-5e87-96d7-bae65a62ead0/b395475f-image.jpg'
OUT = sys.argv[1] if len(sys.argv) > 1 else '.'

img = cv2.imread(SRC)[657:1707, 15:1065]
h, w = img.shape[:2]
lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
L, A, B = lab[..., 0] * 100 / 255, lab[..., 1] - 128, lab[..., 2] - 128

yy, xx = np.mgrid[0:h, 0:w]
frame = np.zeros((h, w), bool)
frame[:12], frame[-12:], frame[:, :12], frame[:, -12:] = True, True, True, True
X = np.stack([np.ones(h * w), xx.ravel() / w, yy.ravel() / h, (xx.ravel() / w) ** 2, (yy.ravel() / h) ** 2, xx.ravel() * yy.ravel() / (w * h)], 1)
bg = np.zeros_like(lab)
for c in range(3):
    idx = np.where(frame.ravel())[0]
    Am, b = X[idx], lab[..., c].ravel()[idx]
    for _ in range(3):
        coef, *_ = np.linalg.lstsq(Am, b, rcond=None)
        r = np.abs(Am @ coef - b)
        keep = r < max(2.0, np.percentile(r, 85))
        Am, b = Am[keep], b[keep]
    bg[..., c] = (X @ coef).reshape(h, w)
bL, bA, bB = bg[..., 0] * 100 / 255, bg[..., 1] - 128, bg[..., 2] - 128
dC = np.sqrt((A - bA) ** 2 + (B - bB) ** 2)             # chroma difference to the background
dL = L - bL
# foreground: a colour change, or a strong lightness change; the cast shadow is a mild darkening at background hue
fg = (dC > 7) | (dL < -22) | (dL > 6)
fg = fg.astype(np.uint8)
fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
n, lbl, st, _ = cv2.connectedComponentsWithStats(fg)
big = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
bx, by, bw_, bh_ = st[big, :4]
keep = [i for i in range(1, n) if i == big or (st[i, cv2.CC_STAT_AREA] > 120 and st[i, 0] > 400 and st[i, 1] < 260)]  # tuners near the head
fg = np.isin(lbl, keep).astype(np.uint8)
inv = (1 - fg).copy()
cv2.floodFill(inv, np.zeros((h + 2, w + 2), np.uint8), (0, 0), 2)
fg[inv == 1] = 1

C = np.sqrt(A ** 2 + B ** 2)
hue = np.degrees(np.arctan2(B, A))
black = (L < 34) & (fg > 0)
pink = (A > 9) & (hue < 40) & (hue > -40) & ~black & (fg > 0)
maple = (B > 9) & (hue >= 40) & (hue < 110) & ~black & (fg > 0) & ~pink
chrome = (fg > 0) & ~pink & ~maple & ~black
chrome_dark = chrome & (L < 62)
chrome_light = chrome & ~chrome_dark

def smooth(m, k=5, t=.5):
    f = cv2.GaussianBlur(m.astype(np.float32), (0, 0), k / 2.0)
    return (f > t).astype(np.uint8)

def med(m):
    px = img[m > 0]
    c = np.median(px, 0)
    return '#%02X%02X%02X' % (int(c[2]), int(c[1]), int(c[0]))

def pct(m, q):
    ls = L[m > 0]
    thr = np.percentile(ls, q)
    sel = (m > 0) & (np.abs(L - thr) < 2)
    return med(sel)

def path_of(m, eps=1.1, min_area=30, mode=cv2.RETR_CCOMP):
    cs, _ = cv2.findContours(m, mode, cv2.CHAIN_APPROX_NONE)
    d = []
    for c in cs:
        if abs(cv2.contourArea(c)) < min_area:
            continue
        a = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
        if len(a) >= 3:
            d.append('M' + ' '.join(f'{x} {y}' for x, y in a) + 'Z')
    return ''.join(d)

sil = smooth(fg, 4)
body = smooth(pink, 5) & sil
neck = cv2.morphologyEx(maple.astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
wood = smooth(neck, 3) & sil & (1 - smooth(pink, 5))
cd = smooth(chrome_dark, 1.2, .45) & sil
cl = smooth(chrome_light, 1.4) & sil
bk = smooth(black, 2) & sil

# body gradient along the body's long axis (upper right = lit, lower left = deep)
g_hi, g_mid, g_lo = pct(pink, 88), pct(pink, 55), pct(pink, 12)
layers = [
    ('url(#bodyGrad)', path_of(sil, 1.3, 400, cv2.RETR_EXTERNAL)),     # base silhouette, no gaps between layers
    (pct(maple, 86), path_of(wood, 1.0, 60)),
    (med(chrome_dark), path_of(cd, 0.8, 12)),
    (pct(chrome_light, 70), path_of(cl, 0.8, 12)),
    (med(black), path_of(bk, 0.9, 25)),
]
grad = (f'<linearGradient id="bodyGrad" gradientUnits="userSpaceOnUse" x1="{w*0.62:.0f}" y1="{h*0.30:.0f}" x2="{w*0.05:.0f}" y2="{h*0.90:.0f}">'
        f'<stop offset="0" stop-color="{g_hi}"/><stop offset=".5" stop-color="{g_mid}"/><stop offset="1" stop-color="{g_lo}"/></linearGradient>')
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}"><defs>{grad}</defs>'
       + ''.join(f'<path fill="{c}" fill-rule="evenodd" d="{p}"/>' for c, p in layers if p)
       + f'<path fill="none" stroke="{g_lo}" stroke-width="2" stroke-linejoin="round" d="{layers[0][1]}"/>' + '</svg>')
open(f'{OUT}/bass_trace2.svg', 'w').write(svg)
dbg = img.copy()
dbg[pink] = (160, 120, 255); dbg[maple] = (120, 200, 230); dbg[chrome_light] = (230, 230, 230); dbg[chrome_dark] = (120, 120, 120); dbg[black] = (0, 0, 0); dbg[fg == 0] = (255, 255, 255)
cv2.imwrite(f'{OUT}/classes.png', cv2.resize(dbg, (525, 525)))
print(len(svg), [c for c, _ in layers], g_hi, g_mid, g_lo)
