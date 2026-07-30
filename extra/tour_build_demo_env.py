"""Build the C:\\Users\\Public\\ZX-Next-Unite-demo capture environment for the
tour GIF: app copy, junctioned emulators, sample sync folder, demo HDF, cfg.

Junction-safe cleanup: downloads/mame and downloads/itchio are junctions into
the repo's downloads — cleanup uses `rmdir /s /q`, which removes reparse
points WITHOUT following them (shutil.rmtree would delete the real files).
"""
import os
import shutil
import subprocess
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = r"C:\Users\Public\ZX-Next-Unite-demo"
HDFMONKEY = os.path.join(
    REPO, r"downloads\itchio\mdf200\cspect\files\CSpect3_3_1_0\hdfmonkey\windows-64\hdfmonkey.exe")

def run(*argv):
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"FAILED {argv}: {r.stdout} {r.stderr}")
    return r.stdout

# -- clean + skeleton -------------------------------------------------------
if os.path.isdir(DEMO):
    subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", DEMO], check=True)
sample = os.path.join(DEMO, "Spectrum Next")
for d in ("downloads", os.path.join("Spectrum Next", "demos"),
          os.path.join("Spectrum Next", "dots"),
          os.path.join("Spectrum Next", "games"),
          os.path.join("Spectrum Next", "screens")):
    os.makedirs(os.path.join(DEMO, d), exist_ok=True)
shutil.copy2(os.path.join(REPO, "zx-next-unite.py"), DEMO)

# -- emulators via junctions (no 700 MB copies) -----------------------------
for name in ("mame", "itchio"):
    run("cmd", "/c", "mklink", "/J",
        os.path.join(DEMO, "downloads", name),
        os.path.join(REPO, "downloads", name))

# -- synthetic demo titles (round sizes, real-game names — screenshot props;
# the previous tour GIF used exactly this trick, no content is distributed) --
games = os.path.join(sample, "games")
demos = os.path.join(sample, "demos")
def dummy(path, size):
    block = b"ZX Next Unite demo file - not a real program.   "
    data = (block * (size // len(block) + 1))[:size]
    open(path, "wb").write(data)

dummy(os.path.join(games, "ALIEN-ANNIHILATION.nex"), 49152)
dummy(os.path.join(games, "GALACTUS.nex"),           98304)
dummy(os.path.join(games, "WarOfTheRing.nex"),       131072)
dummy(os.path.join(demos, "PowerRun.nex"),           65536)
dummy(os.path.join(sample, "screens", "loading.scr"), 6912)   # SCREEN$ size
dummy(os.path.join(sample, "screens", "menu.scr"),    6912)

# -- dots/sync5, autoexec.bas, readme.txt -----------------------------------
shutil.copy2(os.path.join(REPO, r"nextsync\sync\server\dot\syncdev"),
             os.path.join(sample, "dots", "sync5"))

autoexec = ("10 REM ZX Next Unite demo card\n"
            "20 BORDER 0: PAPER 0: INK 7: CLS\n"
            "30 PRINT \"Welcome to the ZX Spectrum Next!\"\n"
            "40 PRINT \"Demo image built with ZX Next Unite\"\n"
            "50 .cd /games\n"
            "60 CAT\n")
autoexec = (autoexec + "9000 REM " + "*" * 512)[:511] + "\n"
assert len(autoexec) == 512, len(autoexec)
readme = ("ZX Next Unite - demo sync folder\n"
          "--------------------------------\n"
          "games/   .nex titles from the GetIt archive (zxnext.uk)\n"
          "demos/   demo-scene productions\n"
          "dots/    the .sync5 dot command for NextSync\n"
          "screens/ SCREEN$ loading screens\n"
          "\nSync it to your Next with the NextSync tab!\n")
readme = (readme + "-" * 1024)[:1023] + "\n"
assert len(readme) == 1024, len(readme)
open(os.path.join(sample, "autoexec.bas"), "w", newline="\n").write(autoexec)
open(os.path.join(sample, "readme.txt"), "w", newline="\n").write(readme)

# -- the demo HDF ------------------------------------------------------------
hdf = os.path.join(DEMO, "NextZXOS-demo.hdf")
run(HDFMONKEY, "create", hdf, "64M", "ZXNEXT")
for d in ("games", "demos", "dot", "nextzxos"):
    run(HDFMONKEY, "mkdir", hdf, "/" + d)
for src, dst in (
        (os.path.join(games, "ALIEN-ANNIHILATION.nex"), "/games/ALIEN-ANNIHILATION.nex"),
        (os.path.join(games, "GALACTUS.nex"),           "/games/GALACTUS.nex"),
        (os.path.join(games, "WarOfTheRing.nex"),       "/games/WarOfTheRing.nex"),
        (os.path.join(demos, "PowerRun.nex"),           "/demos/PowerRun.nex"),
        (os.path.join(sample, "dots", "sync5"),         "/dot/sync5"),
        (os.path.join(sample, "autoexec.bas"),          "/nextzxos/autoexec.bas")):
    if os.path.isfile(src):
        run(HDFMONKEY, "put", hdf, src, dst)
print(run(HDFMONKEY, "ls", hdf, "/games"))

# -- hdfg.cfg ----------------------------------------------------------------
cfg = f"""hddffile={hdf}
explorerpath={sample}
nextsync_explorerpath={sample}
image_explorerpath=/
sdcard_pygame_log=true
nextsync_pygame_mode=true
allinone_pygame_mode=true
help_pygame_log=true
getit_view_mode=gallery
zxdb_view_mode=gallery
zxart_view_mode=gallery
allinone_view_mode=gallery
getit_item_retro=true
zxdb_item_retro=true
zxart_item_retro=true
favorites_item_retro=true
alien_floyd_tab=true
content_disclaimer_agreed=1
wizard_enabled=false
wizard_intro_shown=true
ui_language=en
zxnu_update_check=false
mame_update_check=false
cspect_update_check=false
nextsync_remote_explorer=false
"""
open(os.path.join(DEMO, "hdfg.cfg"), "w", newline="\n").write(cfg)
print("demo environment ready at", DEMO)
print(os.listdir(sample), os.listdir(games))
