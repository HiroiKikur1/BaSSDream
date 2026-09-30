"""SVG path data -> WPF-safe path markup (absolute M/L/C/Q/A/Z, explicit separators, no exponents).

Inkscape writes compact relative paths (packed arc flags, exponents, implicit repeats); WPF's mini-language accepts
most of it but not all, and a parse error at startup would take the app down. Used for
src-native/assets/boot/logo_glyphs.json.
"""
import json
import re
import sys

NUM = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
ARGS = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7, 'Z': 0}


def tokens(d):
    i = 0
    while i < len(d):
        c = d[i]
        if c.isalpha():
            yield c
            i += 1
        elif c in ' ,\t\n\r':
            i += 1
        else:
            m = NUM.match(d, i)
            if not m:
                raise ValueError(f'bad path near {d[i:i + 20]!r}')
            yield m.group(0)
            i = m.end()


def f(x):
    s = f'{x:.3f}'.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def normalize(d):
    toks = list(tokens(d))
    out, i = [], 0
    cx = cy = sx = sy = 0.0
    last_c = last_q = None
    cmd = None
    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd in 'Zz':
                out.append('Z')
                cx, cy = sx, sy
                last_c = last_q = None
                continue
        up, rel = cmd.upper(), cmd.islower()
        n = ARGS[up]
        if up == 'A':                                    # flags may be packed: "0 011" = 0, 0, 1 ...
            a = []
            while len(a) < 7:
                t = toks[i]
                if len(a) in (3, 4) and len(t) > 1 and t[0] in '01' and not t.startswith(('0.', '1.')):
                    a.append(t[0])
                    toks[i] = t[1:]
                    continue
                a.append(t)
                i += 1
            v = [float(x) for x in a]
        else:
            v = [float(x) for x in toks[i:i + n]]
            i += n
        ox, oy = (cx, cy) if rel else (0.0, 0.0)
        if up == 'M':
            cx, cy = v[0] + ox, v[1] + oy
            sx, sy = cx, cy
            out.append(f'M{f(cx)},{f(cy)}')
            cmd = 'l' if rel else 'L'                    # implicit lineto after a moveto
            last_c = last_q = None
        elif up == 'L':
            cx, cy = v[0] + ox, v[1] + oy
            out.append(f'L{f(cx)},{f(cy)}')
            last_c = last_q = None
        elif up == 'H':
            cx = v[0] + (cx if rel else 0)
            out.append(f'L{f(cx)},{f(cy)}')
            last_c = last_q = None
        elif up == 'V':
            cy = v[0] + (cy if rel else 0)
            out.append(f'L{f(cx)},{f(cy)}')
            last_c = last_q = None
        elif up in 'CS':
            if up == 'C':
                x1, y1, x2, y2, x, y = v[0] + ox, v[1] + oy, v[2] + ox, v[3] + oy, v[4] + ox, v[5] + oy
            else:
                x1, y1 = (2 * cx - last_c[0], 2 * cy - last_c[1]) if last_c else (cx, cy)
                x2, y2, x, y = v[0] + ox, v[1] + oy, v[2] + ox, v[3] + oy
            out.append(f'C{f(x1)},{f(y1)} {f(x2)},{f(y2)} {f(x)},{f(y)}')
            last_c, last_q = (x2, y2), None
            cx, cy = x, y
        elif up in 'QT':
            if up == 'Q':
                x1, y1, x, y = v[0] + ox, v[1] + oy, v[2] + ox, v[3] + oy
            else:
                x1, y1 = (2 * cx - last_q[0], 2 * cy - last_q[1]) if last_q else (cx, cy)
                x, y = v[0] + ox, v[1] + oy
            out.append(f'Q{f(x1)},{f(y1)} {f(x)},{f(y)}')
            last_q, last_c = (x1, y1), None
            cx, cy = x, y
        elif up == 'A':
            rx, ry, rot, large, sweep, x, y = v[0], v[1], v[2], int(v[3]), int(v[4]), v[5] + ox, v[6] + oy
            out.append(f'A{f(rx)},{f(ry)} {f(rot)} {large} {sweep} {f(x)},{f(y)}')
            cx, cy = x, y
            last_c = last_q = None
    return ' '.join(out)


if __name__ == '__main__':
    path = sys.argv[1]
    data = json.load(open(path, encoding='utf-8'))
    for g in data:
        g['d'] = normalize(g['d'])
    json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(len(data), 'paths normalised')
