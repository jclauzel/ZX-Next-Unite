"""Assemble tour_frames/ into the tour GIF: 140 ms frames, 5-frame
crossfades between segments and a wrap-around fade for a seamless loop.
ffmpeg palettegen/paletteuse keeps the retro colors clean at native size.

--wizzy assembles wizzy_frames/ (tour_capture.py --wizzy) into
wizzy-tour.gif instead: 200 ms frames, the capture's own rate, halved to
720x487 like the README's published copy.

--sheet assembles nothing: it writes tour-sheet.png (or wizzy-sheet.png with
--wizzy) - one mid frame of every stop, half size, two to a row - so the
verification look before assembling is one image instead of eight."""
import os
import re
import shutil
import subprocess
import sys
from PIL import Image

import tempfile
WORK = os.environ.get("ZXNU_TOUR_WORK") or os.path.join(tempfile.gettempdir(), "zxnu-tour")
WIZZY = "--wizzy" in sys.argv[1:]
SRC = os.path.join(WORK, "wizzy_frames" if WIZZY else "tour_frames")
BUILD = os.path.join(WORK, "gif_build")
OUT = os.path.join(WORK, "wizzy-tour.gif" if WIZZY else "zx-next-unite-tour.gif")
FADE = 4
PER_SEG = 22 if WIZZY else 12
FRAMERATE = "5" if WIZZY else "50/7"        # 200 ms / 140 ms per frame
SCALE = 0.5 if WIZZY else 1.0

shutil.rmtree(BUILD, ignore_errors=True)
os.makedirs(BUILD)

segs = {}
for f in sorted(os.listdir(SRC)):
    m = re.match(r"seg(\d+)_", f)
    segs.setdefault(int(m.group(1)), []).append(os.path.join(SRC, f))
order = [segs[k] for k in sorted(segs)]
print("segments:", [(k, len(v)) for k, v in sorted(segs.items())])

if "--sheet" in sys.argv[1:]:
    picks = [Image.open(seg[min(6, len(seg) - 1)]).convert("RGB") for seg in order]
    w, h = picks[0].width // 2, picks[0].height // 2
    sheet = Image.new("RGB", (2 * w, h * ((len(picks) + 1) // 2)))
    for i, im in enumerate(picks):
        sheet.paste(im.resize((w, h), Image.LANCZOS), ((i % 2) * w, (i // 2) * h))
    sheet_path = os.path.join(WORK, ("wizzy" if WIZZY else "tour") + "-sheet.png")
    sheet.save(sheet_path)
    print("contact sheet ->", sheet_path)
    sys.exit(0)

order = [seg[:PER_SEG] for seg in order]
frames = []
for i, seg in enumerate(order):
    frames.extend(seg)
    nxt = order[(i + 1) % len(order)][0]
    a = Image.open(seg[-1]).convert("RGB")
    b = Image.open(nxt).convert("RGB")
    for j in range(1, FADE + 1):
        blend = Image.blend(a, b, j / (FADE + 1))
        p = os.path.join(BUILD, f"fade_{i}_{j}.png")
        blend.save(p)
        frames.append(p)

for n, f in enumerate(frames):
    dst = os.path.join(BUILD, f"frame_{n:04d}.png")
    if SCALE == 1.0:
        shutil.copyfile(f, dst)
    else:
        im = Image.open(f).convert("RGB")
        im.resize((round(im.width * SCALE), round(im.height * SCALE)),
                  Image.LANCZOS).save(dst)

pattern = os.path.join(BUILD, "frame_%04d.png")
palette = os.path.join(BUILD, "palette.png")
subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", pattern,
                "-vf", "palettegen=stats_mode=diff:max_colors=128", palette], check=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", FRAMERATE,
                "-i", pattern, "-i", palette,
                "-lavfi", "paletteuse=dither=none:diff_mode=rectangle",
                "-loop", "0", OUT], check=True)
print("frames:", len(frames), "->", OUT, f"{os.path.getsize(OUT)/1e6:.1f} MB")
