"""Assemble tour_frames/ into the tour GIF: 140 ms frames, 5-frame
crossfades between segments and a wrap-around fade for a seamless loop.
ffmpeg palettegen/paletteuse keeps the retro colors clean at native size."""
import os
import re
import shutil
import subprocess
from PIL import Image

import tempfile
WORK = os.environ.get("ZXNU_TOUR_WORK") or os.path.join(tempfile.gettempdir(), "zxnu-tour")
SRC = os.path.join(WORK, "tour_frames")
BUILD = os.path.join(WORK, "gif_build")
OUT = os.path.join(WORK, "zx-next-unite-tour.gif")
FADE = 4

shutil.rmtree(BUILD, ignore_errors=True)
os.makedirs(BUILD)

segs = {}
for f in sorted(os.listdir(SRC)):
    m = re.match(r"seg(\d+)_", f)
    segs.setdefault(int(m.group(1)), []).append(os.path.join(SRC, f))
order = [segs[k] for k in sorted(segs)]
print("segments:", [(k, len(v)) for k, v in sorted(segs.items())])

PER_SEG = 12
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
    shutil.copyfile(f, os.path.join(BUILD, f"frame_{n:04d}.png"))

pattern = os.path.join(BUILD, "frame_%04d.png")
palette = os.path.join(BUILD, "palette.png")
subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", pattern,
                "-vf", "palettegen=stats_mode=diff:max_colors=128", palette], check=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", "50/7",
                "-i", pattern, "-i", palette,
                "-lavfi", "paletteuse=dither=none:diff_mode=rectangle",
                "-loop", "0", OUT], check=True)
print("frames:", len(frames), "->", OUT, f"{os.path.getsize(OUT)/1e6:.1f} MB")
