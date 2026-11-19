"""Draw the built-in background images (Pillow + numpy).

    python extra/make_backgrounds.py [out_dir]

Writes the sixteen zx-unite-*.png files into *out_dir* (default: the repo
root, where rc_backgrounds.qrc lists them). After a change here:

    pyside6-rcc rc_backgrounds.qrc -o rc_backgrounds.py

Every image is 1920x1080 on its own dark ground, drawn at 2x and reduced.
The four colours (red, yellow, green, blue) appear as bands, blocks, arcs
and bars - never as the slanted four-stripe corner band of the Sinclair logo.
"""
import math
import os
import random
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H, S = 1920, 1080, 2
RED = (232, 48, 58)
YEL = (247, 208, 56)
GRN = (61, 190, 92)
BLU = (47, 143, 232)
CYN = (64, 208, 224)
FOUR = [RED, YEL, GRN, BLU]
INK = (190, 200, 235)


def px(v):
    return int(round(v * S))


def ground(top=(9, 11, 28), bot=(22, 26, 58), vignette=0.35):
    w, h = W * S, H * S
    t = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    x = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
    y = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
    shade = 1 - vignette * np.clip((x * x + y * y) / 2, 0, 1)
    arr = np.empty((h, w, 3), np.float32)
    for i in range(3):
        arr[..., i] = (top[i] + (bot[i] - top[i]) * t) * shade
    return Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")


def layer():
    im = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def over(base, top, alpha=1.0):
    if alpha < 1.0:
        a = top.getchannel("A").point(lambda v: int(v * alpha))
        top = top.copy()
        top.putalpha(a)
    return Image.alpha_composite(base, top)


def glow(base, top, radius, alpha=0.6):
    return over(base, top.filter(ImageFilter.GaussianBlur(px(radius))), alpha)


def stars(base, seed, n=140, y0=0.0, y1=1.0, coloured=0.45):
    rnd = random.Random(seed)
    im, d = layer()
    for _ in range(n):
        x = rnd.uniform(0, W)
        y = rnd.uniform(y0 * H, y1 * H)
        s = rnd.choice([2, 2, 3, 3, 4, 6])
        c = rnd.choice(FOUR) if rnd.random() < coloured else INK
        d.rectangle([px(x), px(y), px(x + s), px(y + s)],
                    fill=c + (rnd.randint(70, 210),))
    return over(base, im)


def finish(img, out_dir, name):
    img = img.resize((W, H), Image.LANCZOS).convert("RGB")
    path = os.path.join(out_dir, name + ".png")
    img.save(path, optimize=True)
    return path


# ---------------------------------------------------------------- designs

def horizon():
    """A four-band sun setting over a wireframe floor."""
    img = ground((8, 9, 26), (34, 22, 62))
    img = stars(img, 11, n=150, y1=0.58)
    cx, hy, r = W * 0.5, H * 0.64, H * 0.30
    cy = hy - r * 0.28
    top = cy - r
    span = hy - top
    sun, d = layer()
    for i, c in enumerate(FOUR):
        d.rectangle([px(cx - r), px(top + span * i / 4),
                     px(cx + r), px(top + span * (i + 1) / 4)], fill=c + (255,))
    for k in range(8):                     # the slits, wider towards the floor
        y = top + span * (0.42 + 0.58 * k / 8)
        d.rectangle([px(cx - r), px(y), px(cx + r), px(y + 3 + k * 2.2)],
                    fill=(0, 0, 0, 0))
    mask = Image.new("L", sun.size, 0)
    ImageDraw.Draw(mask).ellipse([px(cx - r), px(cy - r), px(cx + r), px(cy + r)],
                                 fill=255)
    ImageDraw.Draw(mask).rectangle([0, px(hy), W * S, H * S], fill=0)
    sun.putalpha(Image.composite(sun.getchannel("A"), Image.new("L", sun.size, 0), mask))
    img = glow(img, sun, 60, 0.55)
    img = over(img, sun)
    floor, d = layer()
    d.rectangle([0, px(hy), W * S, H * S], fill=(10, 10, 30, 235))
    line = BLU + (120,)
    for k in range(1, 15):
        y = hy + (H - hy) * (k / 14) ** 2.1
        d.line([0, px(y), W * S, px(y)], fill=line, width=px(1.6))
    for m in range(-22, 23):
        d.line([px(cx + m * 16), px(hy), px(cx + m * 230), px(H)],
               fill=line, width=px(1.6))
    d.line([0, px(hy), W * S, px(hy)], fill=CYN + (230,), width=px(3))
    img = over(img, floor)
    return img


def loading():
    """The tape-loading border: pilot tone above, data below."""
    rnd = random.Random(2)
    img, d = layer()
    y, split = 0.0, H * 0.36
    i = 0
    while y < split:                        # pilot: wide, even
        d.rectangle([0, px(y), W * S, px(y + 44)],
                    fill=(RED if i % 2 == 0 else CYN) + (255,))
        y += 44
        i += 1
    while y < H:                            # data: thin, uneven
        h = rnd.choice([5, 5, 8, 8, 11, 16, 22])
        d.rectangle([0, px(y), W * S, px(y + h)],
                    fill=(YEL if i % 2 == 0 else BLU) + (255,))
        y += h
        i += 1
    shade, sd = layer()
    sd.rectangle([0, 0, W * S, H * S], fill=(8, 9, 24, 70))
    img = over(img, shade)
    bx, by = W * 0.105, H * 0.14
    paper = ground((10, 12, 30), (20, 24, 54), 0.2).crop(
        (px(bx), px(by), px(W - bx), px(H - by)))
    img.paste(paper, (px(bx), px(by)))
    d = ImageDraw.Draw(img)
    cx, cy, c = bx + 28, H - by - 28 - 40, 40           # the "L" cursor
    d.rectangle([px(cx), px(cy), px(cx + c), px(cy + c)], fill=INK + (255,))
    d.rectangle([px(cx + 12), px(cy + 8), px(cx + 17), px(cy + 32)], fill=(12, 14, 34, 255))
    d.rectangle([px(cx + 12), px(cy + 27), px(cx + 29), px(cy + 32)], fill=(12, 14, 34, 255))
    return img


def _keys(d, x0, y0, rows, key=78, gap=14, fill=(34, 38, 70, 255),
          edge=INK + (150,), tints=None):
    """Rows of keys; *rows* = list of (indent, [key widths in units])."""
    y = y0
    n = 0
    for indent, widths in rows:
        x = x0 + indent
        for wu in widths:
            w = key * wu + gap * (wu - 1)
            f = fill
            if tints and n in tints:
                f = tints[n] + (230,)
            d.rounded_rectangle([px(x), px(y), px(x + w), px(y + key)],
                                radius=px(10), fill=f, outline=edge, width=px(2.5))
            x += w + gap
            n += 1
        y += key + gap


def _place(base, art, angle, dx, dy, scale=1.0, halo=BLU):
    if scale != 1.0:
        w, h = art.size
        small = art.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        art = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        art.paste(small, ((w - small.width) // 2, (h - small.height) // 2))
    art = art.rotate(angle, resample=Image.BICUBIC)
    moved = Image.new("RGBA", art.size, (0, 0, 0, 0))
    moved.paste(art, (px(dx), px(dy)))
    tint = Image.new("RGBA", art.size, halo + (0,))
    tint.putalpha(moved.getchannel("A").point(lambda v: v // 3))
    base = glow(base, tint, 70, 0.9)
    return over(base, moved)


def next_machine():
    """A Spectrum Next in line art, four dots on its corner."""
    img = ground((9, 11, 28), (20, 26, 60))
    img = stars(img, 31, n=110)
    art, d = layer()
    bw, bh = 1500, 600
    x0, y0 = (W - bw) / 2, (H - bh) / 2
    d.rounded_rectangle([px(x0), px(y0), px(x0 + bw), px(y0 + bh)], radius=px(34),
                        fill=(16, 18, 38, 245), outline=INK + (190,), width=px(4))
    d.line([px(x0 + 30), px(y0 + 74), px(x0 + bw - 30), px(y0 + 74)],
           fill=INK + (90,), width=px(2))
    rows = [(0, [1] * 14), (0, [1.5] + [1] * 12 + [1.5]),
            (0, [1.8] + [1] * 11 + [2.2]), (0, [2.4] + [1] * 10 + [2.6]),
            (0, [1, 1, 1, 1, 6.2, 1, 1, 1, 1])]
    _keys(d, x0 + 62, y0 + 104, rows, key=78, gap=14)
    for i, c in enumerate(FOUR):            # four dots, stacked on the corner
        cx, cy = x0 + bw - 40, y0 + 22 + i * 0
        d.ellipse([px(x0 + bw - 190 + i * 40), px(y0 + 26),
                   px(x0 + bw - 190 + i * 40 + 24), px(y0 + 50)], fill=c + (255,))
    return _place(img, art, 9, 330, 250, 0.92)


def ql_machine():
    """A Sinclair QL: the ridged top, two microdrives, five function keys."""
    img = ground((7, 8, 20), (20, 22, 46))
    ridges, d = layer()                     # the ridges run on across the wall
    for k in range(64):
        y = 20 + k * 17
        a = int(46 * (1 - k / 64) ** 1.4) + 6
        d.line([0, px(y), W * S, px(y)], fill=INK + (a,), width=px(2))
    img = over(img, ridges)
    art, d = layer()
    bw, bh = 1620, 600
    x0, y0 = (W - bw) / 2, (H - bh) / 2
    d.rounded_rectangle([px(x0), px(y0), px(x0 + bw), px(y0 + bh)], radius=px(14),
                        fill=(10, 11, 22, 250), outline=INK + (190,), width=px(4))
    for k in range(13):                     # ridged upper deck
        y = y0 + 22 + k * 13
        d.line([px(x0 + 22), px(y), px(x0 + bw - 22), px(y)],
               fill=INK + (110,), width=px(3))
    for i in range(2):                      # microdrive slots
        sx = x0 + bw - 520 + i * 250
        d.rounded_rectangle([px(sx), px(y0 + 48), px(sx + 210), px(y0 + 150)],
                            radius=px(8), fill=(10, 11, 22, 255),
                            outline=INK + (170,), width=px(3))
        d.rectangle([px(sx + 26), px(y0 + 86), px(sx + 184), px(y0 + 112)],
                    fill=(3, 3, 8, 255), outline=INK + (120,), width=px(2))
    d.ellipse([px(x0 + bw - 560), px(y0 + 92), px(x0 + bw - 544), px(y0 + 108)],
              fill=RED + (255,))
    ky, key, gap = y0 + 216, 70, 12
    fkeys = [RED, YEL, GRN, BLU, INK]       # F1-F5, the column on the left
    for i, c in enumerate(fkeys[:4] + [None]):
        y = ky + i * (key + gap) * 0.93
        f = (c + (235,)) if c else (34, 38, 70, 255)
        d.rounded_rectangle([px(x0 + 34), px(y), px(x0 + 34 + key * 1.3), px(y + key * 0.86)],
                            radius=px(8), fill=f, outline=INK + (150,), width=px(2.5))
    rows = [(0, [1] * 15), (0, [1.5] + [1] * 13 + [0.5]),
            (0, [1.9] + [1] * 11 + [2.1]), (0, [2.4] + [1] * 10 + [2.6]),
            (0, [1, 1, 1, 7.5, 1, 1, 1, 1])]
    _keys(d, x0 + 168, ky, rows, key=key, gap=gap, fill=(26, 28, 52, 255))
    return _place(img, art, -7, -110, 250, 0.94, halo=RED)


def mosaic():
    """Attribute cells lighting up from two corners; the middle stays clear."""
    img = ground()
    rnd = random.Random(5)
    im, d = layer()
    cell = 40
    for gy in range(H // cell):
        for gx in range(W // cell):
            u, v = (gx + .5) * cell / W, (gy + .5) * cell / H
            a = math.hypot(u, 1 - v)
            b = math.hypot(1 - u, v)
            p = max(0.0, 1 - min(a, b) / 0.72) ** 1.7
            x, y = gx * cell, gy * cell
            d.rectangle([px(x), px(y), px(x + cell), px(y + cell)],
                        outline=INK + (9,), width=px(1))
            if rnd.random() < p * 0.85:
                c = rnd.choice(FOUR)
                al = int(rnd.uniform(60, 235) * min(1, p * 1.6))
                d.rectangle([px(x + 3), px(y + 3), px(x + cell - 3), px(y + cell - 3)],
                            fill=c + (al,))
    return over(glow(img, im, 18, 0.5), im)


def equalizer():
    """A spectrum analyser: blue at the foot, red at the peaks."""
    img = ground((9, 10, 26), (16, 20, 48))
    img = stars(img, 61, n=90, y1=0.45)
    rnd = random.Random(6)
    im, d = layer()
    n, seg, sgap = 48, 16, 6
    bw = W / n
    base = H - 26
    levels = 30
    for i in range(n):
        t = i / (n - 1)
        f = (0.46 + 0.22 * math.sin(t * 7.3 + 0.6) + 0.16 * math.sin(t * 17.1 + 2.0)
             + 0.10 * math.sin(t * 31.0) + rnd.uniform(-0.07, 0.07))
        f *= 0.55 + 0.45 * math.sin(t * math.pi) ** 0.5     # quieter at the edges
        k = max(3, min(levels, int(f * 1.75 * levels)))
        for s in range(k):
            q = s / levels
            c = BLU if q < 0.30 else GRN if q < 0.52 else YEL if q < 0.70 else RED
            y = base - s * (seg + sgap)
            d.rectangle([px(i * bw + 5), px(y - seg), px((i + 1) * bw - 5), px(y)],
                        fill=c + (235,))
        y = base - (k + 2) * (seg + sgap)                   # peak-hold marker
        d.rectangle([px(i * bw + 5), px(y - 5), px((i + 1) * bw - 5), px(y)],
                    fill=INK + (200,))
    img = glow(img, im, 26, 0.55)
    return over(img, im)


def ribbons():
    """Four chunky waves crossing the screen side by side."""
    img = ground((9, 11, 28), (24, 22, 56))
    img = stars(img, 71, n=100)
    block, q, th = 24, 12, 40

    def wave(phase, amp, alpha, y_mid):
        im, d = layer()
        for bx in range(0, W, block):
            t = bx / W
            y = (y_mid + amp * math.sin(t * 5.2 + phase)
                 + amp * 0.45 * math.sin(t * 11.7 + phase * 1.7))
            y = round(y / q) * q
            for i, c in enumerate(FOUR):
                d.rectangle([px(bx), px(y + i * th), px(bx + block - 2), px(y + (i + 1) * th - 2)],
                            fill=c + (alpha,))
        return im

    img = over(img, wave(2.4, 150, 46, H * 0.30))
    main = wave(0.4, 170, 240, H * 0.48)
    img = glow(img, main, 30, 0.5)
    return over(img, main)


def signal():
    """Two corners calling each other in four-colour arcs."""
    img = ground()
    img = stars(img, 81, n=90)
    im, d = layer()

    def arcs(ox, oy, start, end, groups, fade):
        for g in range(groups):
            for i, c in enumerate(FOUR):
                r = 150 + g * 290 + i * 46
                al = int(235 * fade ** g)
                box = [px(ox - r), px(oy - r), px(ox + r), px(oy + r)]
                d.arc(box, start, end, fill=c + (al,), width=px(24))

    arcs(0, H, 270, 360, 4, 0.62)           # bottom-left, calling up and right
    arcs(W, 0, 90, 180, 3, 0.45)            # top-right, answering
    d.rectangle([0, px(H - 70), px(70), px(H)], fill=INK + (235,))
    d.rectangle([px(W - 70), 0, px(W), px(70)], fill=INK + (235,))
    img = glow(img, im, 24, 0.45)
    return over(img, im)


def band_disc(cx, cy, r, clip_y=None, slits=8, order=FOUR):
    """A disc in four horizontal bands with slits widening downwards."""
    im, d = layer()
    bottom = cy + r if clip_y is None else clip_y
    top = cy - r
    span = bottom - top
    for i, c in enumerate(order):
        d.rectangle([px(cx - r), px(top + span * i / 4),
                     px(cx + r), px(top + span * (i + 1) / 4)], fill=c + (255,))
    for k in range(slits):
        y = top + span * (0.42 + 0.58 * k / slits)
        d.rectangle([px(cx - r), px(y), px(cx + r), px(y + 3 + k * r / 150)],
                    fill=(0, 0, 0, 0))
    mask = Image.new("L", im.size, 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([px(cx - r), px(cy - r), px(cx + r), px(cy + r)], fill=255)
    if clip_y is not None:
        md.rectangle([0, px(clip_y), W * S, H * S], fill=0)
    im.putalpha(Image.composite(im.getchannel("A"), Image.new("L", im.size, 0), mask))
    return im


def mountains():
    """The banded sun behind stepped pixel ridges, each edged in a colour."""
    img = ground((8, 9, 26), (36, 24, 66))
    img = stars(img, 101, n=150, y1=0.6)
    sun = band_disc(W * 0.34, H * 0.36, H * 0.23)
    img = over(glow(img, sun, 60, 0.5), sun)
    rnd = random.Random(101)
    block, q = 24, 12
    fills = [(30, 30, 74), (22, 24, 60), (15, 17, 44), (9, 10, 28)]
    for li, (base, amp, edge) in enumerate([(0.66, 210, BLU), (0.76, 170, GRN),
                                             (0.87, 130, YEL), (0.96, 90, RED)]):
        im, d = layer()
        ph = rnd.uniform(0, 6)
        for bx in range(0, W, block):
            t = bx / W
            y = (H * base - amp * abs(math.sin(t * (7.0 + li * 3) + ph))
                 - amp * 0.35 * math.sin(t * (9.3 + li * 2) + ph * 2)
                 + rnd.uniform(-10, 10))
            y = round(y / q) * q
            d.rectangle([px(bx), px(y), px(bx + block), H * S], fill=fills[li] + (255,))
            d.rectangle([px(bx), px(y), px(bx + block), px(y + 5)], fill=edge + (220,))
        img = over(img, im)
    return img


def sea():
    """The sun going down into the sea, its four bands broken on the water."""
    img = ground((8, 9, 26), (40, 24, 64))
    img = stars(img, 102, n=140, y1=0.5)
    hy, cx, r = H * 0.54, W * 0.5, H * 0.27
    sun = band_disc(cx, hy - r * 0.2, r, clip_y=hy)
    img = over(glow(img, sun, 60, 0.55), sun)
    water, d = layer()
    d.rectangle([0, px(hy), W * S, H * S], fill=(8, 9, 28, 240))
    rnd = random.Random(102)
    for k in range(26):                     # faint swell lines
        y = hy + (H - hy) * (k / 26) ** 1.6
        x = rnd.uniform(0, W)
        for _ in range(6):
            w = rnd.uniform(60, 260)
            d.rectangle([px(x), px(y), px(x + w), px(y + 2)], fill=BLU + (60,))
            x = (x + w + rnd.uniform(120, 520)) % W
    rows = 30
    for k in range(rows):                   # the reflection, mirrored: blue first
        f = k / rows
        c = [BLU, GRN, YEL, RED][min(3, int(f * 4))]
        y = hy + 8 + (H - hy - 20) * f ** 1.25
        th = 5 + f * 12
        half = r * (0.95 - 0.35 * f) + rnd.uniform(-20, 20)
        x = cx - half + rnd.uniform(-30, 30) * f
        al = int(235 * (1 - f) ** 0.8) + 15
        while x < cx + half:
            w = rnd.uniform(40, 150) * (1 - 0.5 * f)
            d.rectangle([px(x), px(y), px(min(x + w, cx + half)), px(y + th)], fill=c + (al,))
            x += w + rnd.uniform(6, 16 + 70 * f)
    d.line([0, px(hy), W * S, px(hy)], fill=CYN + (200,), width=px(3))
    return over(img, water)


def tunnel():
    """Four-colour frames receding into a dark middle."""
    img = ground()
    im, d = layer()
    cx, cy = W * 0.5, H * 0.5
    for k in range(17):
        s = 1.27 ** k
        hw, hh = 150 * s, 84 * s
        al = int(min(240, 40 + k * 22))
        d.rectangle([px(cx - hw), px(cy - hh), px(cx + hw), px(cy + hh)],
                    outline=FOUR[k % 4] + (al,), width=px(2 + s * 1.5))
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):     # corner rails
        d.line([px(cx + sx * 150), px(cy + sy * 84), px(cx + sx * W), px(cy + sy * W * 0.56)],
               fill=INK + (40,), width=px(2))
    img = glow(img, im, 22, 0.5)
    return stars(over(img, im), 103, n=60)


def rain():
    """Pixel rain in four colours, heavier down the sides."""
    img = ground()
    rnd = random.Random(104)
    im, d = layer()
    block, gap = 24, 4
    cols = W // block
    for _ in range(120):
        u = rnd.random() ** 1.9             # 0 = at an edge
        gx = int(u * cols / 2)
        if rnd.random() < 0.5:
            gx = cols - 1 - gx
        c = rnd.choice(FOUR)
        n = rnd.randint(5, 18)
        head = rnd.randint(0, H // block + 6)
        strength = rnd.uniform(0.45, 1.0)
        for j in range(n):
            y = (head - j) * block
            if y < -block or y > H:
                continue
            al = int(235 * strength * (1 - j / n) ** 1.3)
            col = INK if j == 0 else c
            d.rectangle([px(gx * block + gap), px(y + gap),
                         px((gx + 1) * block - gap), px(y + block - gap)], fill=col + (al,))
    return over(glow(img, im, 16, 0.55), im)


def helix():
    """Two four-colour strands winding round each other."""
    img = ground((9, 11, 28), (24, 22, 56))
    img = stars(img, 105, n=110)
    im, d = layer()
    block, q, th = 24, 12, 22
    mid, amp, freq = H * 0.5, 190, 9.5
    for bx in range(0, W, block):
        t = bx / W
        a = freq * t + 0.5
        drift = 60 * math.sin(t * 2.6)
        ya = mid + drift + amp * math.sin(a)
        yb = mid + drift - amp * math.sin(a)
        strands = [(ya, FOUR), (yb, FOUR[::-1])]
        if math.cos(a) < 0:
            strands.reverse()               # strands[0] is the one behind
        for depth, (y, cols) in enumerate(strands):
            y = round(y / q) * q - 2 * th
            al = 95 if depth == 0 else 240
            for i, c in enumerate(cols):
                d.rectangle([px(bx), px(y + i * th), px(bx + block - 2), px(y + (i + 1) * th - 2)],
                            fill=c + (al,))
    return over(glow(img, im, 28, 0.5), im)


def pixels():
    """Attribute cells crowding in from every edge."""
    img = ground()
    rnd = random.Random(106)
    im, d = layer()
    cell = 40
    for gy in range(H // cell):
        for gx in range(W // cell):
            x, y = gx * cell, gy * cell
            e = min(x + cell / 2, W - x - cell / 2, y + cell / 2, H - y - cell / 2)
            p = max(0.0, 1 - e / (H * 0.30)) ** 2.2
            d.rectangle([px(x), px(y), px(x + cell), px(y + cell)],
                        outline=INK + (9,), width=px(1))
            if rnd.random() < p * 0.9:
                al = int(rnd.uniform(70, 235) * min(1, p * 1.5))
                d.rectangle([px(x + 3), px(y + 3), px(x + cell - 3), px(y + cell - 3)],
                            fill=rnd.choice(FOUR) + (al,))
    return over(glow(img, im, 18, 0.5), im)


def ripples():
    """Two stones dropped in a pond: rings in four colours, crossing."""
    img = ground()
    img = stars(img, 107, n=80)
    for (ox, oy, n, fade, shift) in ((W * 0.20, H * 0.72, 22, 0.90, 0),
                                     (W * 0.82, H * 0.24, 18, 0.87, 2)):
        im, d = layer()
        for k in range(n):
            r = 46 + k * 50
            al = int(240 * fade ** k)
            d.ellipse([px(ox - r), px(oy - r), px(ox + r), px(oy + r)],
                      outline=FOUR[(k + shift) % 4] + (al,), width=px(11))
        d.ellipse([px(ox - 14), px(oy - 14), px(ox + 14), px(oy + 14)], fill=INK + (235,))
        img = over(glow(img, im, 18, 0.45), im)
    return img


def city():
    """A pixel skyline at night, windows lit in the four colours."""
    img = ground((8, 9, 26), (30, 24, 62))
    img = stars(img, 108, n=150, y1=0.7)
    moon = band_disc(W * 0.78, H * 0.26, H * 0.13, slits=6)
    img = over(glow(img, moon, 40, 0.5), moon)
    rnd = random.Random(108)
    for back in (True, False):
        im, d = layer()
        x = -20
        while x < W:
            w = rnd.choice([60, 80, 100, 120, 160])
            side = abs((x + w / 2) / W - 0.5) * 2          # taller towards the sides
            h = rnd.uniform(90, 230) + 250 * side ** 1.5
            if back:
                h = h * 1.25 + 60
            top = H - h
            fill = (20, 22, 54) if back else (11, 12, 32)
            d.rectangle([px(x), px(top), px(x + w), H * S], fill=fill + (255,))
            if not back:
                d.rectangle([px(x), px(top), px(x + w), px(top + 4)], fill=INK + (70,))
                wy = top + 18
                while wy < H - 16:
                    wx = x + 10
                    while wx + 12 <= x + w - 8:
                        lit = rnd.random() < 0.22
                        c = rnd.choice(FOUR) + (225,) if lit else (22, 25, 56, 255)
                        d.rectangle([px(wx), px(wy), px(wx + 12), px(wy + 16)], fill=c)
                        wx += 20
                    wy += 28
            x += w + (rnd.choice([0, 0, 6, 14]) if not back else rnd.choice([0, 20, 60]))
        img = over(img, im)
    return img


DESIGNS = [
    ("zx-unite-horizon", horizon),
    ("zx-unite-loading", loading),
    ("zx-unite-next", next_machine),
    ("zx-unite-ql", ql_machine),
    ("zx-unite-mosaic", mosaic),
    ("zx-unite-equalizer", equalizer),
    ("zx-unite-waves", ribbons),
    ("zx-unite-signal", signal),

    ("zx-unite-mountains", mountains),
    ("zx-unite-sea", sea),
    ("zx-unite-tunnel", tunnel),
    ("zx-unite-rain", rain),
    ("zx-unite-helix", helix),
    ("zx-unite-pixels", pixels),
    ("zx-unite-ripples", ripples),
    ("zx-unite-city", city),
]


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name, fn in DESIGNS:
        p = finish(fn(), out_dir, name)
        print(name, os.path.getsize(p) // 1024, "KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1
         else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
