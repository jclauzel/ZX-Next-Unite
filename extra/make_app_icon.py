"""Regenerate zx-next-unite.ico from zx-next-unite.png (needs Pillow).

    python extra/make_app_icon.py

The .ico is what PyInstaller embeds in the Windows executable
(`--icon zx-next-unite.ico`, see .github/workflows/release.yml), i.e. the
icon Explorer, the taskbar and a pinned shortcut show. The source PNG is a
24x24 pixel-art square, so each size is made from a x10 nearest-neighbour
copy (the chunky pixels stay crisp) scaled down once, never from a smooth
upscale, which only blurs it.
"""
import os

from PIL import Image

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "zx-next-unite.png")
DST = os.path.join(HERE, "zx-next-unite.ico")


def main():
    src = Image.open(SRC).convert("RGBA")
    big = src.resize((src.width * 10, src.height * 10), Image.NEAREST)
    frames = []
    for size in SIZES:
        if size == src.width:
            frames.append(src.copy())
        elif size % src.width == 0:
            frames.append(src.resize((size, size), Image.NEAREST))
        else:
            frames.append(big.resize((size, size), Image.LANCZOS))
    # Pillow writes the frames it is GIVEN only through append_images;
    # with sizes= alone it would rescale the first frame itself.
    frames[-1].save(DST, format="ICO", sizes=[(s, s) for s in SIZES],
                    append_images=frames[:-1])
    print("wrote", DST, os.path.getsize(DST), "bytes")


if __name__ == "__main__":
    main()
