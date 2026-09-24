"""Offscreen end-to-end UI suite for zx-next-unite.

Run everything:   python test_ui_offscreen.py
Run one phase:    python test_ui_offscreen.py <1..16>

Phases:
  1  SD Card tab: explorer path rows (Up / Refresh / labels / editable path
     boxes), local path box paste/file/invalid navigation, disk-image path
     navigation e2e on a generated test HDF (nested folder, file, root,
     unknown path), in-image path + retro-log color persistence to hdfg.cfg,
     and the Settings color-picker layout.                  (needs hdfmonkey)
  2  Startup restore: image_explorerpath and color_retro_log from hdfg.cfg
     are applied after the startup image load.    (needs hdfmonkey + phase 1)
  3  Startup fallback: a saved in-image path missing from the image logs the
     advisory, stays at "/" and re-persists "/".  (needs hdfmonkey + phase 1)
  4  Both local explorers (NextSync Classic sync + SD Card local): drag &
     drop configuration, an OS-style drop that imports the file, and the
     painted model's VISUAL column order (Name/Type/Size/Modified) plus no
     in-place editing; the startup drag arm on the Classic, SD local and SD
     image trees (DropOnly while disarmed, DragDrop once armed). On the
     Classic sync tree also the painted model itself (Size/Type/Modified
     texts, item colours read live off the host, the repaint on a colour
     change), drag & drop through Qt's REAL event delivery - an OS drop on
     empty space, on a folder row's Type cell, on a file row inside an
     expanded subfolder and on the ".." row, an intra-tree copy, the
     same-folder no-op, non-local URLs and a non-file drag refused - the
     tree's OWN copy-only startDrag (9.7.39) recorded through a QDrag
     subclass (the selected file only, ".." never dragged, also via a real
     press-move-move gesture), and its Ctrl+wheel font zoom (restored from
     nextsync_tree_font, persisted per change).        (no hdfmonkey needed)
  5  Watched-folder delete regression on BOTH local explorers: expanding
     subfolders makes QFileSystemModel watch them; deleting the tree must
     fully remove it with ZERO 'FindNextChangeNotification failed' watcher
     warnings (the Windows UI-freeze bug).               (no hdfmonkey needed)
  6  Self-update Settings toggle (top row, cfg restore off, persist on) and
     the ".sync5 dot updated" advisory popup when dotn_last_version in the
     cfg is older than the bundled dotN.                 (no hdfmonkey needed)
  7  dotN advisory first-run silent persist (no popup) + the update-check
     toggle defaulting ON when the cfg has no key.       (no hdfmonkey needed)
  8  "Load an image" hint pulse: with an emulator (CSpect/MAME) detected and
     no image loaded, 'Select NextZXOS disk Image' + 'Download NextZXOS
     Image' breathe
     amber; loading the test HDF stops the pulse and restores their look.
     Without any emulator the pulse must stay off.   (needs hdfmonkey + phase 1)
  9  UI language (zxnu_i18n): ui_language=es in the cfg starts the app with
     the static UI in Spanish (button/checkbox/placeholder translated, tab
     titles untouched, Settings combo on Español); switching the combo back
     to English live restores the originals and persists ui_language=en.
                                                         (no hdfmonkey needed)
 10  First-run OS-language adoption: with NO saved ui_language and the OS
     locale forced to Spanish (ZX_NEXT_UNITE_UI_LANGUAGE=es), the app starts
     translated, persists ui_language=es once, and shows the 15 s advisory
     toast in the BOTTOM-LEFT corner (in Spanish).      (no hdfmonkey needed)
 11  NextSync Remote Explorer WITHOUT pygame: pygame drives the optional retro
     log, and the two share the NextSync tab's stacked widget, so this proves
     the dual explorer, its Up/Refresh/+ Drive buttons, the transfer arrows
     and the server-control button all render with pygame absent — and that
     arming the retro toggle then declines instead of crashing. Every phase
     blocks pygame (below), but this is the one that exercises that view.
                                                        (no hdfmonkey needed)
 12  SD Card tab with NO image loaded: the LOCAL (left) explorer must stay
     usable — it browses the PC and its right-click menu carries the "Start
     <emulator> with <file>" actions, neither of which needs an image. The
     image-side half must stay disabled, so this pins a targeted fix rather
     than "enable everything".                          (no hdfmonkey needed)

Every phase cfg carries zxnu_update_check=false (except phase 7, which quits
before the delayed check can fire) so the suite never talks to GitHub.

Isolation: each phase runs a COPY of zx-next-unite.py from a scratch dir
under the OS temp folder with its own hdfg.cfg (the app resolves its cfg and
downloads/ from argv[0]'s directory), so the real configuration is never
touched. pygame is import-blocked (it crashes natively under offscreen Qt).
Phases that need hdfmonkey SKIP cleanly (exit 0, "SKIPPED" in the output)
when none can be found — e.g. on a fresh checkout or CI, where downloads/
(gitignored) doesn't exist.
"""
import os, sys, shutil, subprocess, runpy, time, tempfile, importlib.machinery

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SCRATCH = os.path.join(tempfile.gettempdir(), "zxnu-ui-tests")

# The app writes its always-on log next to argv[0] — the SCRATCH copy — so it
# must never touch the repo. A developer who ran the real app from the repo
# will legitimately have a zx-next-unite.log there already, so we snapshot it
# BEFORE launching the scratch app and later assert this run left it untouched
# (rather than asserting the file is simply absent).
_REPO_LOG = os.path.join(REPO, "zx-next-unite.log")
_REPO_LOG_MTIME0 = os.path.getmtime(_REPO_LOG) if os.path.isfile(_REPO_LOG) else None
CFG = os.path.join(SCRATCH, "hdfg.cfg")
HDF = os.path.join(SCRATCH, "test.hdf")
PASTE_SUB = os.path.join(SCRATCH, "pastedir", "sub")
PASTE_FILE = os.path.join(PASTE_SUB, "afile.txt")
DROPZONE = os.path.join(SCRATCH, "dropzone")
DROPSRC = os.path.join(SCRATCH, "dropsrc.txt")
DELZONE = os.path.join(SCRATCH, "delzone")
# Phase 4's Classic sync fixtures (9.7.39): a folder the painted tree is
# pointed at, a subfolder to drop onto, and files whose Size / Type /
# Modified texts are known in advance. The mtime is a local wall-clock time
# well clear of any DST change, so the expected string is exact everywhere.
CLASSIC_ZONE = os.path.join(DROPZONE, "classic")
CLASSIC_INTO = os.path.join(CLASSIC_ZONE, "into")
# A second drop source, for the drop on the ".." row: its own name, so where
# it lands is unambiguous (the tree's own folder - never the parent).
UPDIR_SRC = os.path.join(SCRATCH, "updir-drop.txt")
# ...and one for the drop on a FILE row inside an expanded subfolder, where
# "that file's folder" and the tree's root fallback give different answers.
FILEROW_SRC = os.path.join(SCRATCH, "filerow-drop.txt")
CLASSIC_MTIME = time.mktime((2024, 1, 2, 3, 4, 0, 0, 0, -1))
CLASSIC_FILE_COLOR = "#a1b2c3"
CLASSIC_FONT_PT = 15        # well clear of any platform's default item font

PHASE = int(sys.argv[1]) if len(sys.argv) > 1 else None
ALL_PHASES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16)

# Base cfg for the isolated app copy: update checks off (MAME/CSpect AND the
# app's own GitHub release check) so no phase ever hits the network, and the
# UI language pinned to English so the first-run OS-language adoption can't
# translate the texts these phases compare (phase 10 covers that flow).
BASE_CFG = ("mame_update_check=false\ncspect_update_check=false\n"
            "zxnu_update_check=false\nui_language=en\n"
            # The startup tab activation shows the one-time content disclaimer
            # for the online panes. It is a MODAL dialog, so a phase that has
            # not agreed to it blocks forever. (Before the tab titles were
            # matched by prefix, a badge on the restored tab made that branch
            # unreachable and the dialog never appeared — which is exactly the
            # bug that left ZXDB uninitialised on restart.)
            "content_disclaimer_agreed=1\n")


def find_hdfmonkey():
    """hdfmonkey from PATH, or via the app's own downloads-discovery helpers
    (downloads/ is gitignored, so this can legitimately come up empty).

    CRITICAL: zxnu_config computes ZX_NEXT_UNITE_CONFIG_FILE_NAME at IMPORT
    time from sys.argv[0]. Importing it here caches the module with the cfg
    path pointing at tests/hdfg.cfg; the app run by runpy later would reuse
    that cached module and read/write the WRONG cfg (this exact bug cost a
    debugging round). So purge every zxnu* module after the lookup — the app
    then re-imports them fresh with argv[0] already rewritten to its scratch
    copy."""
    p = shutil.which("hdfmonkey")
    if p:
        return p
    sys.path.insert(0, REPO)
    try:
        from zxnu_config import (find_hdfmonkey_in_downloads,
                                 find_emulators_in_downloads)
        p = find_hdfmonkey_in_downloads(REPO)
        if not p:
            _cspect, p = find_emulators_in_downloads(REPO, scan_for_cspect=False)
        return p
    except Exception:
        return None
    finally:
        for _m in [k for k in sys.modules if k.startswith("zxnu")]:
            del sys.modules[_m]


def skip(reason):
    print(f"PHASE {PHASE} SKIPPED: {reason}")
    sys.exit(0)


# ---- runner mode: no phase argument = run every phase in a subprocess ------
if PHASE is None:
    failed = []
    skipped = []
    for ph in ALL_PHASES:
        print(f"\n=== UI offscreen phase {ph} ===", flush=True)
        # TEE, do not capture: a phase can hang, and the output printed
        # before it hung is the only evidence of where. Reading line by line
        # keeps that live on the console (and in CI's log) while still
        # letting the runner see the SKIPPED marker.
        proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), str(ph)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        saw_skip = False
        try:
            for line in proc.stdout:
                print(line, end="", flush=True)
                if f"PHASE {ph} SKIPPED" in line:
                    saw_skip = True
            rc = proc.wait(timeout=900)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            print(f"PHASE {ph} TIMED OUT (possible UI hang)")
            rc = 1
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
        if saw_skip:
            skipped.append(ph)
        if rc != 0:
            failed.append(ph)
    print()
    # Name the phases that did NOT run. A skip is not a pass, and hiding it
    # behind "(or skipped cleanly)" is how the SD Card filter regression
    # stayed green for ~35 commits: CI has no hdfmonkey, so phases 1-3 -
    # the only coverage of the local explorer's real navigate/filter/persist
    # pipeline - never executed there.
    if skipped:
        print(f"UI SUITE: {len(skipped)} phase(s) SKIPPED and therefore NOT "
              f"covered: {skipped}")
        print("          (phases 1-3 need hdfmonkey on PATH or in downloads/)")
    if failed:
        print(f"UI SUITE RESULT: FAILED phase(s): {failed}")
        sys.exit(1)
    if skipped:
        print(f"UI SUITE RESULT: {len(ALL_PHASES) - len(skipped)} phase(s) "
              f"passed, {len(skipped)} skipped")
    else:
        print("UI SUITE RESULT: ALL PHASES PASSED")
    sys.exit(0)


# ---- per-phase scratch setup ------------------------------------------------
def ensure_scratch(fresh):
    """(Re)create the isolated scratch dir: app copy, base cfg, and a junction
    to the repo's downloads/ (when it exists) so the app's hdfmonkey/emulator
    discovery works. The app copy is ALWAYS refreshed — later phases must run
    the current source, never a stale copy from an earlier phase."""
    if fresh and os.path.isdir(SCRATCH):
        j = os.path.join(SCRATCH, "downloads")
        if os.path.isdir(j):
            os.rmdir(j)          # junction: removes the link only, not the target
        shutil.rmtree(SCRATCH)
    os.makedirs(SCRATCH, exist_ok=True)
    shutil.copy(os.path.join(REPO, "zx-next-unite.py"),
                os.path.join(SCRATCH, "zx-next-unite.py"))
    if fresh or not os.path.isfile(CFG):
        with open(CFG, "w") as f:
            f.write(BASE_CFG)
    j = os.path.join(SCRATCH, "downloads")
    repo_dl = os.path.join(REPO, "downloads")
    if os.path.isdir(repo_dl) and not os.path.isdir(j):
        subprocess.run(["cmd", "/c", "mklink", "/J", j, repo_dl],
                       check=True, capture_output=True)


if PHASE == 1:
    HDFMONKEY = find_hdfmonkey()
    if not HDFMONKEY:
        skip("hdfmonkey not found (PATH or downloads/) — phases 1-3 need it")
    ensure_scratch(fresh=True)
    with open(CFG, "a") as f:                # a saved local | image split (9.7.2)
        f.write("sdcard_hsplitter_sizes=520,680\n")
    os.makedirs(PASTE_SUB)
    with open(PASTE_FILE, "w") as f:
        f.write("x")
    subprocess.run([HDFMONKEY, "create", HDF, "64M"], check=True, capture_output=True)
    subprocess.run([HDFMONKEY, "mkdir", HDF, "/games"], check=True, capture_output=True)
    subprocess.run([HDFMONKEY, "mkdir", HDF, "/games/sub"], check=True, capture_output=True)
    subprocess.run([HDFMONKEY, "put", HDF, PASTE_FILE, "/games/sub/hello.txt"],
                   check=True, capture_output=True)
elif PHASE in (2, 3):
    # Reuse phase 1's scratch (test HDF + junction) with a cfg that points at
    # the HDF and pre-seeds the state whose startup restore is under test.
    if not os.path.isfile(HDF):
        skip("no test HDF (phase 1 did not run or was skipped)")
    # A STALE HDF from an earlier session must not un-skip these phases: when
    # hdfmonkey has since gone away (e.g. the itch.io CSpect install that
    # bundled it was removed), the app cannot list the image, the assertions
    # fail, and the missing-hdfmonkey install prompt — a modal — can hang the
    # offscreen run until the phase timeout.
    if not find_hdfmonkey():
        skip("hdfmonkey not found (PATH or downloads/) — phases 2-3 need it")
    ensure_scratch(fresh=False)
    saved = "/games/sub" if PHASE == 2 else "/gone"
    with open(CFG, "w") as f:
        f.write(BASE_CFG
                + f"hddffile={HDF}\nimage_explorerpath={saved}\n"
                + "color_retro_log=#112233\n"
                + "general_font_size=8\n"            # the application font (9.7.2)
                + "re_update_prompt=false\n"         # the connect-time update offer, off
                + "nextsync_verify_crc=false\n"   # the verify-after-put check, off (9.7.3)
                + "nextsync_sessions=false\n"     # the single-seat -listen mode (9.7.20)
                # A hand-picked ground, with the Custom mode that picking one
                # leaves behind - the only mode in which a pick SURVIVES a
                # restart (every other mode recomputes the palette on load).
                + "color_background=#204060\ndesktop_theme=custom\n")
elif PHASE == 4:
    ensure_scratch(fresh=False)
    with open(CFG, "w") as f:
        # A restored file-name colour, with the Custom mode that lets a pick
        # survive a restart. The Classic sync tree is built long BEFORE the
        # cfg is read, so seeing this colour on it proves the tree reads the
        # host's colours live rather than a snapshot taken at build time.
        f.write(BASE_CFG + f"color_file_name={CLASSIC_FILE_COLOR}\n"
                + "desktop_theme=custom\n"
                # The Classic tree's Ctrl+wheel font size (9.7.39), restored
                # at startup like the SD Card pair's.
                + f"nextsync_tree_font={CLASSIC_FONT_PT}\n")
    if os.path.isdir(DROPZONE):
        shutil.rmtree(DROPZONE)
    os.makedirs(os.path.join(DROPZONE, "subdir"))
    with open(DROPSRC, "w") as f:
        f.write("drop me")
    with open(UPDIR_SRC, "w") as f:
        f.write("updir")
    with open(FILEROW_SRC, "w") as f:
        f.write("filerow")
    # Written BEFORE the app starts, so the model's file-info gatherer sees
    # the final sizes and times on its first listing.
    os.makedirs(CLASSIC_INTO)
    for _name, _size in (("size512.bin", 512), ("size1100.bin", 1100),
                         ("noext", 1), ("a.b.c", 1), ("moveme.txt", 7)):
        with open(os.path.join(CLASSIC_ZONE, _name), "wb") as f:
            f.write(b"x" * _size)
    os.utime(os.path.join(CLASSIC_ZONE, "size512.bin"),
             (CLASSIC_MTIME, CLASSIC_MTIME))
elif PHASE == 5:
    ensure_scratch(fresh=False)
    with open(CFG, "w") as f:
        f.write(BASE_CFG)
    if os.path.isdir(DELZONE):
        shutil.rmtree(DELZONE)
    for victim, sub in (("victim", "sub"), ("victim2", "sub2")):
        deep = os.path.join(DELZONE, victim, sub, "subsub")
        os.makedirs(deep)
        with open(os.path.join(DELZONE, victim, sub, "a.txt"), "w") as f:
            f.write("x")
        with open(os.path.join(deep, "b.txt"), "w") as f:
            f.write("y")
elif PHASE == 8:
    # Emulator present + NO image in the cfg -> the amber "load an image"
    # hint pulse. Loading the phase-1 HDF then stops it, so the phase needs
    # both the test HDF and hdfmonkey (skip cleanly like phases 2-3).
    if not os.path.isfile(HDF):
        skip("no test HDF (phase 1 did not run or was skipped)")
    # Same stale-HDF guard as phases 2-3: loading the image needs hdfmonkey.
    if not find_hdfmonkey():
        skip("hdfmonkey not found (PATH or downloads/) — phase 8 loads the HDF")
    ensure_scratch(fresh=False)
    with open(CFG, "w") as f:
        f.write(BASE_CFG)
elif PHASE == 9:
    # Saved non-English UI language -> the startup walk must translate the
    # static UI. Needs no image and no hdfmonkey. (BASE_CFG pins en; the es
    # line written after it wins — the cfg loader is last-value-wins.)
    ensure_scratch(fresh=False)
    with open(CFG, "w", encoding="utf-8") as f:
        f.write(BASE_CFG + "ui_language=es\n")
elif PHASE == 10:
    # First-run OS-language adoption: NO ui_language key at all, and the OS
    # locale forced to Spanish via the env override the detection honours
    # (Qt ignores LANG/LC_ALL on Windows). Expect: UI in Spanish, the choice
    # persisted once, and the 15 s advisory toast in the BOTTOM-LEFT corner.
    ensure_scratch(fresh=False)
    with open(CFG, "w", encoding="utf-8") as f:
        f.write("mame_update_check=false\ncspect_update_check=false\n"
                "zxnu_update_check=false\ncontent_disclaimer_agreed=1\n")
    os.environ["ZX_NEXT_UNITE_UI_LANGUAGE"] = "es"
elif PHASE == 12:
    # No image loaded at all: the base cfg names no image, which is exactly
    # the resting state this phase is about.
    ensure_scratch(fresh=False)
    with open(CFG, "w") as f:
        f.write(BASE_CFG)
elif PHASE == 13:
    # REGRESSION (reported): the "Download NextZXOS Image" wizard downloaded
    # the zip but the user saw no extracted image, no path selected, and no
    # load — because the extracted image kept the ARCHIVE's internal path
    # (2gb/cspect-next-2gb.img), so a renamed save produced no artifact
    # carrying the chosen name, every download overwrote the same hidden
    # file, and the 2 GB single-call extract froze the UI. The wizard must
    # now extract the image NEXT TO the zip NAMED AFTER IT. The phase feeds
    # the wizard a locally built zip (urlopen patched — no network) whose
    # image member sits under the stock archives' internal folder layout.
    import zipfile as _zf
    ensure_scratch(fresh=False)
    with open(CFG, "w") as f:
        f.write(BASE_CFG)
    DLZIP_SRC = os.path.join(SCRATCH, "wizard-feed.zip")
    with _zf.ZipFile(DLZIP_SRC, "w", _zf.ZIP_DEFLATED) as z:
        z.writestr("2gb/cspect-next-2gb.img", b"\x00" * 65536)
        z.writestr("2gb/version.txt", "test feed")
elif PHASE == 14:
    # REGRESSION (reported): clicking the image-history dropdown arrow
    # populated the list and instantly closed it again, reloading the
    # already-loaded image — on Windows the opening click's own release
    # can "activate" the current entry when the (long-path-widened) popup
    # lands under the cursor. The fix: activating the ALREADY-LOADED image
    # is a no-op, and when it arrives within the opening half-second the
    # dropdown is put straight back up. The phantom itself needs real
    # cursor geometry, so this phase drives the GUARD directly.
    # NO hddffile on purpose: the startup load of a cfg image would pop
    # the MODAL missing-hdfmonkey install prompt on a runner without
    # hdfmonkey (CI) before the inspector could suppress it — the phases
    # 2-3 lesson, which cost this phase a 900 s hang on its first CI
    # run. The inspector fakes the loaded state directly instead.
    ensure_scratch(fresh=False)
    with open(CFG, "w") as f:
        f.write(BASE_CFG
                + "image_history=C:/imgs/one.img|C:/imgs/two.img\n")
elif PHASE == 15:
    # REGRESSION (reported): "I don't find a way to remove it from the list"
    # — the image-history dropdown was write-only, every successful load put
    # a path in and nothing ever took one out, so a stale entry (a deleted /
    # renamed / moved image) stayed for good; deleting the text and pressing
    # Enter only unloaded. 9.6.0 added the '✕' button, Delete on a dropdown
    # row and a right-click menu, all routed through one removal closure.
    # NO hddffile on purpose, exactly as in phase 14: a startup load would
    # pop the MODAL missing-hdfmonkey prompt on a runner without hdfmonkey.
    ensure_scratch(fresh=False)
    with open(CFG, "w") as f:
        f.write(BASE_CFG
                + "image_history=C:/imgs/one.img|C:/imgs/two.img|C:/imgs/three.img\n")
elif PHASE == 16:
    # 9.7.22: the SD Card tab's local path box remembers its folders, the
    # way the image box above it has since 9.6.0 (reported: "do the same
    # for the Local Path"). Needs phase 1's scratch for a real folder to
    # navigate to; NO hddffile, so nothing can reach the modal
    # missing-hdfmonkey prompt (the phase 14/15 lesson).
    if not os.path.isdir(PASTE_SUB):
        skip("no scratch folders (phase 1 did not run or was skipped)")
    ensure_scratch(fresh=False)
    with open(CFG, "w") as f:
        f.write(BASE_CFG
                + "explorerpath_history=C:/hist/one|C:/hist/two|C:/hist/three\n"
                # The Remote Explorer's own, separate key - the explicit
                # ask was two distinct values, so one must survive the
                # other being emptied.
                + "nextsync_explorerpath_history=C:/re/root\n")
elif PHASE == 11:
    # NextSync Remote Explorer with pygame absent (every phase blocks pygame —
    # see _NoPygame). The retro log needs pygame; the Remote Explorer's dual
    # explorer must NOT, so it has to build and show all the same. The cfg
    # pre-selects the Remote Explorer view so the tab opens straight into it,
    # which is also how a user who last used it gets there.
    ensure_scratch(fresh=False)
    with open(CFG, "w") as f:
        f.write(BASE_CFG + "nextsync_remote_explorer=true\n"
                + "nextsync_re_splitter_sizes=520,680\n"    # a saved local | Next split (9.7.2)
                + "nextsync_re_log_splitter_sizes=430,250\n")  # explorers | log (9.7.21)
elif PHASE in (6, 7):
    # Phase 6: dotn_last_version older than the bundled dotN -> the ".sync5
    # needs updating on your Next" advisory popup must fire, and the Settings
    # toggle must restore a saved "false" and persist a re-check. Phase 7:
    # NO dotn key (first-run silent persist, no popup) and NO
    # zxnu_update_check key (the toggle must default ON); the phase quits
    # long before the 3.4s-delayed release check could fire, so it still
    # never talks to GitHub.
    ensure_scratch(fresh=False)
    with open(CFG, "w") as f:
        if PHASE == 6:
            # delete_to_recycle_bin=false also exercises the OFF restore path.
            f.write(BASE_CFG + "dotn_last_version=1.0\n"
                    + "delete_to_recycle_bin=false\n")
        else:
            f.write("mame_update_check=false\ncspect_update_check=false\n"
                    "ui_language=en\ncontent_disclaimer_agreed=1\n")
else:
    print(f"Unknown phase {PHASE}")
    sys.exit(2)

# ---- block pygame (crashes natively under offscreen Qt) --------------------
class _NoPygame(importlib.machinery.PathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "pygame" or name.startswith("pygame."):
            raise ModuleNotFoundError(name)
        return None
sys.meta_path.insert(0, _NoPygame())

os.environ["QT_QPA_PLATFORM"] = "offscreen"
# Keep the offscreen platform's STUB drag, which returns at once. Qt 6.13
# switches offscreen to a real in-process QSimpleDrag whose nested event loop
# ends only on a mouse release or Escape, so any real QDrag.exec reached by a
# phase would block (phase 4 records its drags through a QDrag subclass that
# never calls exec - this is the backstop). Older Qt ignores the variable.
os.environ.setdefault("QT_QPA_OFFSCREEN_NO_DND", "1")
sys.path.insert(0, REPO)

from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                               QLineEdit)
from PySide6.QtCore import QTimer, QCoreApplication

def settings_row(name):
    """The Settings tab's named row order — the position checks assert
    through it, so a widget placed with a hardcoded index (bypassing the
    registrar) fails the suite. Imported LATE on purpose: zxnu_config
    resolves its data root / cfg path at IMPORT time from sys.argv[0]
    (see find_hdfmonkey's CRITICAL note), so importing any zxnu module at
    this module's top level freezes the cfg onto tests/ before the phases
    point argv at the scratch copy — every phase then reads defaults and
    saves into the void (CI run 31783370146). By the time a phase calls
    this, the app import machinery has already cached the module with the
    right argv."""
    from zxnu_settings_pane import settings_grid_row
    return settings_grid_row(name)


FAILURES = []
def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)

def wait_until(cond, timeout=60.0, what=""):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    print(f"TIMEOUT waiting for: {what}")
    return False

def cfg_lines():
    with open(CFG, encoding="utf-8") as f:
        return f.read().splitlines()

def recent_log(win, needle, n=10):
    return any(needle in win.listWidgetLog.item(i).text()
               for i in range(min(n, win.listWidgetLog.count())))

def find_win():
    for w in QApplication.instance().topLevelWidgets():
        if w.__class__.__name__ == "MainWindow":
            return w
    return None

def inspect_phase1():
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return

    # Let the background emulator/hdfmonkey scan finish first: its callback
    # re-runs load_image once, which would otherwise race the checks below.
    wait_until(lambda: not getattr(win, "_emulator_scan_pending", False),
               what="emulator scan settled")

    # ---- layout ---------------------------------------------------------
    from zxnu_pathhistorycombo import FolderHistoryCombo
    # 9.7.22: a history combo, not a plain line edit. Both construction
    # properties are load-bearing and neither is visible on screen, so they
    # are pinned here: NoInsert is what stops QComboBox appending the typed
    # text as a row of its own every time Enter commits a path.
    check("local box is a FolderHistoryCombo",
          isinstance(win.local_file_explorer_path, FolderHistoryCombo))
    check("local box is editable and never self-inserts",
          win.local_file_explorer_path.isEditable()
          and win.local_file_explorer_path.insertPolicy()
          == QComboBox.InsertPolicy.NoInsert,
          str(win.local_file_explorer_path.insertPolicy()))
    check("image box is QLineEdit", isinstance(win.diskimageexplorerpathinput, QLineEdit))
    check("image label text", win.diskimageexplorerlabel.text() == "Disk Image Explorer: ",
          win.diskimageexplorerlabel.text())
    grid = win.sdcard_explorer_grid
    def pos(widget):
        i = grid.indexOf(widget)
        return None if i < 0 else grid.getItemPosition(i)[:2]
    # The Remote Explorer mirroring (9.5.19): nav bars above the trees,
    # the path boxes BELOW them, the image buttons at the very bottom.
    # 9.7.2: the local column is ONE widget on the left of a horizontal
    # splitter (nav bar / tree row / path row stacked), and the image side
    # keeps the grid, re-indexed - column 0 the transfer buttons, column 1
    # the image widgets - so the two explorers' split is draggable.
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QSplitter
    _lcol = win.sdcard_explorer.local_column.layout()
    check("local nav bar tops the left column", _lcol.indexOf(win.local_nav_row_container) == 0, str(_lcol.indexOf(win.local_nav_row_container)))
    check("image nav bar at image-side grid (0,1)", pos(win.image_nav_row_container) == (0, 1), str(pos(win.image_nav_row_container)))
    _hs = getattr(win, "sdcard_hsplitter", None)
    check("SD Card local | image splitter exists", isinstance(_hs, QSplitter))
    if _hs is not None:
        check("SD Card split is horizontal", _hs.orientation() == Qt.Horizontal)
        check("SD Card split holds the local column and the image side",
              _hs.count() == 2 and _hs.widget(0) is win.sdcard_explorer.local_column
              and _hs.widget(1) is win.sdcard_explorer.image_side)
        check("transfer buttons at image-side grid (1,0)",
              pos(win.centralbuttonscontainer) == (1, 0), str(pos(win.centralbuttonscontainer)))
        # Restored from the cfg (520,680 -> 43% left): compare the RATIO,
        # QSplitter rescales requested sizes to the actual width.
        wait_until(lambda: sum(_hs.sizes()) > 0 and _hs.handle(1).width() > 0, 10, "SD Card split laid out")
        _sz = _hs.sizes(); _ratio = (_sz[0] / float(sum(_sz))) if sum(_sz) else 0
        check("SD Card split restores its saved position on startup", abs(_ratio - 520 / 1200.0) < 0.08, str(_sz))
        # The caption keeps its text when there is room and clips when the
        # splitter squeezes the row - it must never resolve to 0px (review).
        _cap = win.sdcard_explorer.diskimageexplorerlabel
        _row = win.sdcard_explorer.image_path_row_container
        _row.resize(1200, 30); _row.layout().activate(); QApplication.processEvents()
        _wide = _cap.width()
        check("the Disk Image Explorer caption shows its text when there is room",
              _wide >= _cap.sizeHint().width() - 2 and _wide > 60, f"{_wide}px, hint {_cap.sizeHint().width()}px")
        # A layout-managed row cannot be resized below its minimum from a
        # test, so measure the minimum itself: the caption must add a pixel
        # to it (its explicit minimum), never its text width.
        _lay = _row.layout()
        _others = sum(_lay.itemAt(i).minimumSize().width() for i in range(_lay.count())
                      if _lay.itemAt(i).widget() is not _cap)
        _m = _lay.contentsMargins()
        _share = (_lay.minimumSize().width() - _others - _m.left() - _m.right()
                  - max(_lay.spacing(), 0) * (_lay.count() - 1))
        check("...but never holds the row open: its share of the row minimum is a pixel, not the text",
              _cap.minimumWidth() == 1 and _share <= 40 < _cap.sizeHint().width(),
              f"share {_share}px, hint {_cap.sizeHint().width()}px")
        check("SD Card split handle is a thin bar, not a square", 1 <= _hs.handle(1).width() <= 12, str(_hs.handle(1).width()))
        _hs.splitterMoved.emit(123, 1)          # what a real drag emits
        check("dragging the SD Card split persists it to the cfg",
              wait_until(lambda: any(l.startswith("sdcard_hsplitter_sizes=") for l in cfg_lines()),
                         3, "the debounced splitter save"),
              str([l for l in cfg_lines() if "splitter" in l]))
    # ONE ROW PER PANE (9.7.33). The drive combo and both filter boxes used
    # to sit on horizontal2, a full-width form row above the splitter, so
    # the tab spent two rows on one row of controls. Pinning indices 0 and 1
    # alone - which is all this used to do - would let the whole moved bar
    # ship untested, the same hole the path row's check below names.
    _lnav = win.local_nav_row_container.layout()
    _lorder = [_lnav.indexOf(w) for w in (win.local_explorer_up_button,
                                          win.local_explorer_refresh_button,
                                          win.zx_next_unite_diskdrive,
                                          win.filterlabel,
                                          win.filtertext)]
    # The drive combo is Windows-only and is simply not built elsewhere, so
    # it reports -1 there; every other item must be present and in order.
    check("local nav = Up|Refresh|drive|Search:|filter, in that order",
          _lorder[0] == 0 and _lorder[1] == 1
          and (_lorder[2] == -1 or _lorder[2] == 2)
          and _lorder[3] > _lorder[1] and _lorder[4] > _lorder[3],
          str(_lorder))
    # The box takes the slack and the row keeps a trailing stretch - without
    # one Qt hands the slack to the widgets and the combo and label balloon.
    check("local filter box owns the row's slack, with a stretch behind it",
          _lnav.stretch(_lorder[4]) == 3
          and _lnav.count() == _lorder[4] + 2
          and _lnav.itemAt(_lnav.count() - 1).widget() is None,
          "stretch=%d count=%d" % (_lnav.stretch(_lorder[4]), _lnav.count()))
    _inav = win.image_nav_row_container.layout()
    _iorder = [_inav.indexOf(w) for w in (win.image_explorer_up_button,
                                          win.image_explorer_refresh_button,
                                          win.image_filterlabel,
                                          win.image_filtertext)]
    check("image nav = Up|Refresh|...|Filter:|filter, in that order",
          _iorder[0] == 0 and _iorder[1] == 1
          and _iorder[2] > _iorder[1] and _iorder[3] == _iorder[2] + 1,
          str(_iorder))
    # Its stretch sits BEFORE the label, which is what keeps this box hard
    # right over the image explorer, where it has always been.
    check("image filter is right-anchored by a stretch before it",
          _inav.itemAt(_iorder[2] - 1).widget() is None,
          str(_iorder))
    # A MAXIMUM AND NO MINIMUM on both boxes. Inside the splitter a minimum
    # is added straight onto the pane's floor, and setChildrenCollapsible
    # (False) then makes the saved split unrestorable - measured, the local
    # pane's floor went 358 -> 684 with the old min+max pair.
    check("neither filter box sets a width FLOOR inside the splitter",
          win.filtertext.minimumWidth() == 0
          and win.image_filtertext.minimumWidth() == 0
          and win.filtertext.maximumWidth() > 0
          and win.image_filtertext.maximumWidth()
          == win.filtertext.maximumWidth(),
          "%d/%d" % (win.filtertext.minimumWidth(),
                     win.image_filtertext.minimumWidth()))
    check("local path row bottoms the left column", _lcol.indexOf(win.local_path_row_container) == 2, str(_lcol.indexOf(win.local_path_row_container)))
    check("image path row at image-side grid (2,1)", pos(win.image_path_row_container) == (2, 1), str(pos(win.image_path_row_container)))
    _lrow = win.local_path_row_container.layout()
    # The index check alone PASSES with the button appended, which would
    # ship it untested - so it is named, and the stretch pair with it
    # (mirroring the image row's own check below).
    check("local path row = label|path box|clear button",
          _lrow.indexOf(win.localexplorerlabel) == 0
          and _lrow.indexOf(win.local_file_explorer_path) == 1
          and _lrow.indexOf(win.local_path_clear) == 2,
          f"{_lrow.indexOf(win.localexplorerlabel)}/"
          f"{_lrow.indexOf(win.local_file_explorer_path)}/"
          f"{_lrow.indexOf(win.local_path_clear)}")
    check("local path box owns the row's slack (stretch 1, clear 0)",
          _lrow.stretch(1) == 1 and _lrow.stretch(2) == 0,
          f"{_lrow.stretch(1)}/{_lrow.stretch(2)}")
    check("local label text", win.localexplorerlabel.text() == "Local path: ",
          win.localexplorerlabel.text())
    _irow = win.image_path_row_container.layout()
    check("image path row = label|path box|buttons (one bottom row)",
          _irow.indexOf(win.diskimageexplorerlabel) == 0
          and _irow.indexOf(win.diskimageexplorerpathinput) == 1
          and _irow.indexOf(win.imageexplorerbuttonscontainer) == 2)
    check("path box owns the row's slack (stretch 1, buttons 0)",
          _irow.stretch(1) == 1 and _irow.stretch(2) == 0,
          f"{_irow.stretch(1)}/{_irow.stretch(2)}")
    check("buttons keep natural width (no 190px minimum)",
          win.button_new_folder.minimumWidth() < 100
          and win.button_rename.minimumWidth() < 100
          and win.button_delete_files.minimumWidth() < 100)
    check("button cluster flush against the box (no spacer labels, no margins)",
          win.imageexplorerbuttons.indexOf(win.hiddenspacelabel1) == -1
          and win.imageexplorerbuttons.indexOf(win.hiddenspacelabel2) == -1
          and win.imageexplorerbuttons.contentsMargins().left() == 0)
    # The local tree shares its grid cell with the emulator strip (9.5.30),
    # so the CONTAINER is what sits at (1,0) now - same cell, same column
    # stretch, one row inside it.
    _pane = win.sdcard_explorer
    check("local explorer row is the left column's middle (stretch) row",
          _pane.local_column.layout().indexOf(_pane.local_tree_row_container) == 1,
          str(_pane.local_column.layout().indexOf(_pane.local_tree_row_container)))
    _ltree = _pane.local_tree_row_container.layout()
    check("local tree row = emulator strip|tree (strip on the OUTER edge)",
          _ltree.indexOf(_pane.local_emulator_strip) == 0
          and _ltree.indexOf(win.treeview) == 1,
          f"{_ltree.indexOf(_pane.local_emulator_strip)}"
          f"/{_ltree.indexOf(win.treeview)}")
    check("the tree owns the row's slack, the strip keeps its width",
          _ltree.stretch(0) == 0 and _ltree.stretch(1) == 1,
          f"{_ltree.stretch(0)}/{_ltree.stretch(1)}")

    # ---- emulator strip (9.5.30) -------------------------------------------
    # The NextSync tab's Remote Explorer strip, mirrored onto this pane: one
    # tab per INSTALLED emulator, a click launching it exactly as this tab's
    # own Launch button does, and NO strip at all when nothing is installed
    # (hidden, so a machine without an emulator loses no width). isHidden()
    # rather than isVisible(): the answer must not depend on which tab
    # happens to be showing while the suite runs.
    _found, _launched = [], []
    _saved_launchers = _pane._emulator_launchers
    _pane._emulator_launchers = lambda: [
        (n, (lambda name=n: _launched.append(name))) for n in _found]
    _pane.refresh_emulator_strip()
    check("emulator strip hidden while nothing is installed",
          _pane.local_emulator_strip.isHidden() and not _pane._emulator_tabs)
    _found.extend(["Mame", "CSpect"])
    _pane.refresh_emulator_strip()
    check("a tab per detected emulator, same order as the Remote Explorer",
          [t._text for t in _pane._emulator_tabs] == ["Mame", "CSpect"],
          str([t._text for t in _pane._emulator_tabs]))
    check("and the strip shows once there is something to launch",
          not _pane.local_emulator_strip.isHidden())
    _pane._emulator_tabs[0]._on_click()
    QApplication.processEvents()          # the launch is deferred by a timer
    check("clicking a tab launches THAT emulator, with no arguments",
          _launched == ["Mame"], str(_launched))
    _found.clear()
    _pane.refresh_emulator_strip()
    check("the strip retires when the last emulator goes",
          _pane.local_emulator_strip.isHidden() and not _pane._emulator_tabs)
    _pane._emulator_launchers = lambda: 1 / 0
    _pane.refresh_emulator_strip()
    check("a broken detection hook leaves the strip empty, not crashed",
          not _pane._emulator_tabs)
    _pane._emulator_launchers = _saved_launchers
    _pane.refresh_emulator_strip()

    # ---- hovering a GREYED launch surface re-checks its image (9.7.2) ----
    # The busy verdict is a cache; an emulator killed from outside leaves
    # it stale. Pointing at a greyed tab (or a disabled Launch button)
    # asks the host to re-probe THAT emulator - deferred, so a rebuild of
    # the strip never happens from inside the tab's own event. A working
    # tab and an enabled button ask nothing.
    from PySide6.QtCore import QEvent as _QEvent, QPointF as _QPointF
    from PySide6.QtGui import QEnterEvent as _QEnterEvent
    _rechecks = []
    _saved_recheck = win._recheck_emulator_launchability
    win._recheck_emulator_launchability = lambda n=None: _rechecks.append(n)
    _pane._emulator_launchers = lambda: [("Mame", lambda: None, "busy"),
                                         ("CSpect", lambda: None, "")]
    _pane.refresh_emulator_strip()
    _busy_tab, _free_tab = _pane._emulator_tabs

    def _enter(w):
        QApplication.sendEvent(w, _QEnterEvent(_QPointF(3, 3), _QPointF(3, 3), _QPointF(3, 3)))
        QApplication.processEvents()          # the re-check is deferred
    check("a busy emulator's tab is greyed, a free one is not",
          _busy_tab._blocked and not _free_tab._blocked)
    _enter(_free_tab)
    check("hovering a working tab asks nothing", _rechecks == [], str(_rechecks))
    _enter(_busy_tab)
    check("hovering a greyed tab re-checks THAT emulator", _rechecks == ["Mame"], str(_rechecks))
    check("both Launch buttons carry the hover re-check filter",
          set(getattr(win, "_launch_hover_filters", {}) or {}) == {"button_start_mame", "button_start_cspect"},
          str(sorted(getattr(win, "_launch_hover_filters", {}) or {})))
    _mame_was = win.button_start_mame.isEnabled()
    win.button_start_mame.setEnabled(False)
    _rechecks.clear()
    QApplication.sendEvent(win.button_start_mame, _QEvent(_QEvent.Type.Enter))
    QApplication.processEvents()
    check("hovering the greyed Launch Mame button re-checks MAME", _rechecks == ["MAME"], str(_rechecks))
    win.button_start_mame.setEnabled(True)
    _rechecks.clear()
    QApplication.sendEvent(win.button_start_mame, _QEvent(_QEvent.Type.Enter))
    QApplication.processEvents()
    check("an enabled Launch button does not re-check", _rechecks == [], str(_rechecks))
    win.button_start_mame.setEnabled(_mame_was)
    win._recheck_emulator_launchability = _saved_recheck
    _pane._emulator_launchers = _saved_launchers
    _pane.refresh_emulator_strip()

    # Both strips are drawn from ONE list, so they can never disagree.
    check("the host refreshes BOTH strips from one entry point",
          callable(getattr(win, "_refresh_emulator_strips", None)))

    # ---- per-emulator colour (9.6.0) --------------------------------------
    # The request was explicit that the three surfaces agree: the colour
    # picked for CSpect on the SD Card strip is the colour the Remote
    # Explorer's strip AND "Launch CSpect" wear. One host map, keyed by the
    # emulator rather than the label, persisted to hdfg.cfg.
    win.set_emulator_color("CSpect", "#33cc55")
    check("the picked colour is readable back under EVERY label that "
          "emulator wears",
          win.emulator_color_for("CSpect")
          == win.emulator_color_for("🕹  Launch CSpect") == "#33cc55",
          f"{win.emulator_color_for('CSpect')!r} / "
          f"{win.emulator_color_for('🕹  Launch CSpect')!r}")
    check("the Launch button is painted with it",
          "#33cc55" in win.button_start_cspect.styleSheet(),
          win.button_start_cspect.styleSheet()[:90])
    check("the other emulator is left on the app theme",
          win.button_start_mame.styleSheet() == "",
          win.button_start_mame.styleSheet()[:90])
    check("the SD Card strip reads the same map",
          _pane._emulator_color("CSpect") is not None
          and _pane._emulator_color("CSpect").name() == "#33cc55"
          and _pane._emulator_color("Mame") is None)
    check("and it reached hdfg.cfg",
          any(ln.startswith("emulator_colors=") and "#33cc55" in ln
              for ln in cfg_lines()),
          str([ln for ln in cfg_lines() if ln.startswith("emulator_colors=")]))
    win.set_emulator_color("CSpect", "")
    check("resetting puts the button back on the app theme and forgets it",
          win.button_start_cspect.styleSheet() == ""
          and win.emulator_color_for("CSpect") is None)
    check("image explorer at image-side grid (1,1)", pos(win.image_explorer_container) == (1, 1), str(pos(win.image_explorer_container)))
    check("button cluster no longer a grid row of its own",
          pos(win.imageexplorerbuttonscontainer) is None,
          str(pos(win.imageexplorerbuttonscontainer)))
    # The 'top row' this used to guard (horizontal2) no longer exists: its
    # last three occupants moved into the two nav rows at 9.7.33 and the
    # layout went with them. Keeping the check would have been worse than
    # useless - dereferencing the dead attribute raises inside a QTimer slot,
    # where Qt PRINTS the traceback and swallows it, so exec() never returns
    # and the phase hangs to the runner's timeout instead of failing.
    check("the two-row top bar is gone", not hasattr(win, "horizontal2"))
    check("no old attribute left", not hasattr(win, "diskimageexplorerlabelpath"))

    def view_dir():
        return win.model.filePath(win.proxy_model.mapToSource(win.treeview.rootIndex()))

    # ---- local box ---------------------------------------------------------
    check("local box seeded with drive root",
          win.local_file_explorer_path.text() == view_dir() and len(win.local_file_explorer_path.text()) >= 2,
          win.local_file_explorer_path.text())
    check("image box says load an image",
          win.diskimageexplorerpathinput.text() == "Please load an image.",
          win.diskimageexplorerpathinput.text())

    win.diskimageexplorerpathinput.setText("/games")
    win.diskimageexplorerpathinput.editingFinished.emit()
    QCoreApplication.processEvents()
    check("image box edit without image restores advisory",
          win.diskimageexplorerpathinput.text() == "Please load an image.",
          win.diskimageexplorerpathinput.text())

    win.local_file_explorer_path.setText(PASTE_SUB)
    win.local_file_explorer_path.editingFinished.emit()
    QCoreApplication.processEvents()
    want = PASTE_SUB.replace("\\", "/")
    check("paste folder navigates explorer", view_dir() == want, view_dir())
    check("paste folder updates box", win.local_file_explorer_path.text() == want,
          win.local_file_explorer_path.text())
    check("drive selector matches", win.zx_next_unite_diskdrive.currentText()[:1].upper() == want[0].upper(),
          win.zx_next_unite_diskdrive.currentText())

    win.local_file_explorer_path.setText(PASTE_FILE)
    win.local_file_explorer_path.editingFinished.emit()
    QCoreApplication.processEvents()
    check("paste file lands on parent folder", view_dir() == want, view_dir())

    _before = win.local_file_explorer_path.count()
    win.local_file_explorer_path.setText(r"Q:\definitely_not_there_xyz")
    win.local_file_explorer_path.editingFinished.emit()
    # A path that does not exist is never remembered - and NoInsert is what
    # stops QComboBox remembering it behind our back on the Enter key.
    check("a bogus path adds no history entry",
          win.local_file_explorer_path.count() == _before,
          f"{_before} -> {win.local_file_explorer_path.count()}")
    QCoreApplication.processEvents()
    check("invalid path restores box", win.local_file_explorer_path.text() == want,
          win.local_file_explorer_path.text())
    check("invalid path leaves explorer put", view_dir() == want, view_dir())

    # ---- image box e2e ----------------------------------------------------
    win.imageinput.setCurrentText(HDF)
    win.imageinput.lineEdit().returnPressed.emit()
    ok = wait_until(lambda: win.diskimageexplorerpathinput.text() == "/",
                    what="image load -> path box '/'")
    check("image loaded, box shows /", ok, win.diskimageexplorerpathinput.text())

    # The real re-check (9.7.2): a stale BUSY verdict on the selected image
    # greys MAME; the hover re-probe finds the file free, clears it and
    # re-gates (True); asked again with nothing changed it does nothing
    # (False). The clock is reset between calls: hovers are throttled.
    from zxnu_config import IMAGE_WRITE_BUSY as _BUSY
    _img_key = win._image_state_key(win.imageinput.currentText())
    win._image_write_state[_img_key] = _BUSY
    check("a cached BUSY verdict greys MAME", bool(win._image_busy_reason("MAME")))
    # While a load holds every SD Card control, a hover must not re-gate:
    # the buttons are grey for the lock's sake, not the verdict's (review).
    win._sdcard_controls_locked = True
    win._launch_recheck_clock = 0.0
    check("hover re-check: skipped while the SD Card controls are locked",
          win._recheck_emulator_launchability("Mame") is False
          and win._image_write_state.get(_img_key) == _BUSY)
    win._sdcard_controls_locked = False
    win._launch_recheck_clock = 0.0
    check("hover re-check: a free file clears the stale verdict and re-gates",
          win._recheck_emulator_launchability("Mame") is True
          and not win._image_busy_reason("MAME"),
          str(win._image_busy_reason("MAME")))
    win._launch_recheck_clock = 0.0
    check("hover re-check: an unchanged verdict does not re-gate",
          win._recheck_emulator_launchability("Mame") is False)
    check("hover re-check: throttled within half a second",
          win._recheck_emulator_launchability("Mame") is False)

    if ok:
        win.diskimageexplorerpathinput.setText("/games/sub")
        win.diskimageexplorerpathinput.editingFinished.emit()
        ok2 = wait_until(lambda: win.image_selected_path == "/games/sub",
                         what="navigate to /games/sub")
        check("navigate to nested image folder", ok2, win.image_selected_path)
        check("box shows nested folder", win.diskimageexplorerpathinput.text() == "/games/sub",
              win.diskimageexplorerpathinput.text())
        check("tree selection valid", win.image_treeview.currentIndex().isValid())

        win.diskimageexplorerpathinput.setText("/games/sub/hello.txt")
        win.diskimageexplorerpathinput.editingFinished.emit()
        ok3 = wait_until(lambda: win.image_selected_path == "/games/sub/hello.txt",
                         what="navigate to file in image")
        check("navigate to file selects it", ok3, win.image_selected_path)
        check("box shows file's folder", win.diskimageexplorerpathinput.text() == "/games/sub",
              win.diskimageexplorerpathinput.text())

        win.diskimageexplorerpathinput.setText("/")
        win.diskimageexplorerpathinput.editingFinished.emit()
        ok4 = wait_until(lambda: win.image_selected_path == "" and win.diskimageexplorerpathinput.text() == "/",
                         what="navigate back to image root")
        check("root path clears selection", ok4,
              f"sel={win.image_selected_path!r} box={win.diskimageexplorerpathinput.text()!r}")

        win.diskimageexplorerpathinput.setText("/nope")
        win.diskimageexplorerpathinput.editingFinished.emit()
        ok5 = wait_until(lambda: recent_log(win, "Image path not found: /nope"),
                         timeout=15.0, what="unknown-path advisory in log")
        check("unknown image path logs advisory", ok5)
        check("unknown image path restores box", win.diskimageexplorerpathinput.text() == "/",
              win.diskimageexplorerpathinput.text())

        # ---- persistence -------------------------------------------------
        win.diskimageexplorerpathinput.setText("/games/sub")
        win.diskimageexplorerpathinput.editingFinished.emit()
        ok6 = wait_until(lambda: win.image_selected_path == "/games/sub",
                         what="re-navigate for persistence")
        check("re-navigate for persistence", ok6, win.image_selected_path)
        check("image path persisted to cfg", "image_explorerpath=/games/sub" in cfg_lines(),
              str([l for l in cfg_lines() if l.startswith("image_explorerpath")]))

        # ---- Up / Refresh buttons (enabled now that an image is loaded) ----
        check("buttons enabled with image loaded",
              win.local_explorer_up_button.isEnabled()
              and win.local_explorer_refresh_button.isEnabled()
              and win.image_explorer_up_button.isEnabled()
              and win.image_explorer_refresh_button.isEnabled())

        win.local_explorer_up_button.click()
        QCoreApplication.processEvents()
        parent1 = os.path.dirname(PASTE_SUB).replace("\\", "/")
        check("local Up navigates to parent", view_dir() == parent1, view_dir())
        check("local Up updates box", win.local_file_explorer_path.text() == parent1,
              win.local_file_explorer_path.text())
        check("local Up persists to cfg",
              any(l.startswith("explorerpath=") and l.rstrip("/").endswith("pastedir") for l in cfg_lines()),
              str([l for l in cfg_lines() if l.startswith("explorerpath")]))
        win.local_explorer_refresh_button.click()
        QCoreApplication.processEvents()
        check("local Refresh keeps folder", view_dir() == parent1, view_dir())

        # TYPING a filter that matches nothing - not Refresh - was the bug.
        # The pane re-roots through the proxy, and mapFromSource on a FILTERED
        # proxy is invalid whenever the displayed folder's own name does not
        # match (and nothing inside it does either), so the root index died
        # the moment the filter applied and the pane lost its folder. Refresh
        # then had nothing to restore, because local_current_view_dir() was
        # already empty. The proxy KEEPS the displayed folder now
        # (DotDotFirstProxyModel.set_keep_path), so the view never moves.
        win.filtertext.setText("zzz-no-such-name")
        QCoreApplication.processEvents()
        check("typing a filter that matches nothing keeps the folder",
              view_dir() == parent1, view_dir())
        check("...and the tree is still rooted on a valid index",
              win.treeview.rootIndex().isValid())
        # Everything INSIDE it is still filtered - the folder is exempt, its
        # contents are not. The ".." row is always shown (it is how you go up),
        # and whether QFileSystemModel has produced it yet varies, so count
        # only the rows that are not it.
        def _shown_rows():
            _p, _r = win.proxy_model, win.treeview.rootIndex()
            _names = [win.model.fileName(_p.mapToSource(_p.index(_i, 0, _r)))
                      for _i in range(_p.rowCount(_r))]
            return [_n for _n in _names if _n != ".."]
        check("...while its contents are still filtered away",
              not _shown_rows(), str(_shown_rows()))
        win.local_explorer_refresh_button.click()
        QCoreApplication.processEvents()
        check("local Refresh keeps the folder with a filter on",
              view_dir() == parent1, view_dir())
        check("local Refresh keeps the filter itself",
              win.filtertext.text() == "zzz-no-such-name", win.filtertext.text())
        # Clearing the filter must bring the contents back. This is what made
        # the old behaviour unrecoverable: a root index that has ceased to
        # exist cannot be restored by clearing the filter, so the folder was
        # gone until the user navigated to it by hand.
        win.filtertext.setText("")
        QCoreApplication.processEvents()
        check("clearing the filter leaves the pane on the same folder",
              view_dir() == parent1, view_dir())
        check("...with its contents back", bool(_shown_rows()),
              str(_shown_rows()))
        QCoreApplication.processEvents()

        win.image_explorer_refresh_button.click()
        ok7 = wait_until(lambda: win.image_selected_path == "/games/sub"
                         and win.diskimageexplorerpathinput.text() == "/games/sub",
                         timeout=15, what="image Refresh keeps target")
        check("image Refresh keeps target", ok7,
              f"sel={win.image_selected_path!r} box={win.diskimageexplorerpathinput.text()!r}")

        # THE Refresh-with-filter regression: a Refresh with nothing selected
        # re-lists the ROOT, which drops every folder back to a single
        # placeholder child. A filter matching only something INSIDE a folder
        # then had nothing left to match and hid the whole tree. An unlisted
        # folder's contents are unknown, so it must stay reachable.
        from zxnu_sdcard_explorer import IMG_ISDIR_ROLE as _ISDIR
        win.image_filtertext.setText("zzz-matches-nothing-visible")
        QCoreApplication.processEvents()
        win.sdcard_explorer.image_load_root()
        wait_until(lambda: win.image_model.invisibleRootItem().rowCount() > 0,
                   timeout=15, what="root re-listed under a filter")
        QCoreApplication.processEvents()
        _root = win.image_model.invisibleRootItem()
        _all = [_root.child(r, 0) for r in range(_root.rowCount())]
        _all = [i for i in _all if i is not None]
        _dirs = [i for i in _all if i.data(_ISDIR)]
        _shown = [i for i in _dirs
                  if not win.image_treeview.isRowHidden(i.row(), i.index().parent())]
        check("a filtered root Refresh still shows unlisted folders",
              bool(_dirs) and len(_shown) == len(_dirs),
              f"{len(_shown)}/{len(_dirs)} folders visible")
        # ...and non-matching FILES are still hidden, so this stays a
        # "cannot rule it out" exemption rather than a filter bypass.
        _files = [i for i in _all if not i.data(_ISDIR)]
        _fshown = [i for i in _files
                   if not win.image_treeview.isRowHidden(i.row(), i.index().parent())]
        check("a filtered Refresh still hides non-matching files",
              not _fshown, f"{len(_fshown)} file(s) wrongly visible")
        win.image_filtertext.setText("")
        QCoreApplication.processEvents()

        # A row the filter hides must stop being a TARGET. This tree filters
        # in the VIEW (setRowHidden), and hiding a row does NOT deselect it,
        # so a file taken off screen was still handed to Delete, Download and
        # drag-out - destroying or copying something the user cannot see.
        # tests/test_image_filter_selection.py covers the rule in full and
        # headlessly (CI cannot run this phase); this is the same invariant
        # end to end, which is what proves the pane's own widgets are wired
        # to it.
        from zxnu_sdcard_explorer import IMG_PATH_ROLE as _PATHROLE

        def _image_index_for(path):
            out = []

            def walk(item):
                for r in range(item.rowCount()):
                    child = item.child(r, 0)
                    if child is None:
                        continue
                    if (child.data(_PATHROLE) or "") == path:
                        out.append(child.index())
                    walk(child)
            walk(win.image_model.invisibleRootItem())
            return out[0] if out else None

        win.diskimageexplorerpathinput.setText("/games/sub/hello.txt")
        win.diskimageexplorerpathinput.editingFinished.emit()
        _okf = wait_until(
            lambda: win.image_selected_path == "/games/sub/hello.txt",
            what="select the nested file for the filter check")
        check("premise: the nested file is the selected target", _okf,
              win.image_selected_path)
        _hello = _image_index_for("/games/sub/hello.txt")
        check("premise: its row is findable", _hello is not None)
        if _okf and _hello is not None:
            win.image_filtertext.setText("zzz-no-such-name")
            QCoreApplication.processEvents()
            check("a filtered-away file stops being a Delete/Download target",
                  win.image_selected_path != "/games/sub/hello.txt"
                  and all(p != "/games/sub/hello.txt"
                          for p, _d in win.image_selected_paths),
                  f"{win.image_selected_path!r} {win.image_selected_paths!r}")
            check("...and is deselected, not merely skipped",
                  not win.image_treeview.selectionModel().isSelected(_hello))
            win.image_filtertext.setText("")
            QCoreApplication.processEvents()
        # The root reload above cleared the selection; the Up checks
        # below start from /games/sub, so put it back before handing over.
        win.sdcard_explorer.image_navigate_to_path("/games/sub")
        wait_until(lambda: win.image_selected_path == "/games/sub",
                   timeout=15, what="restore selection after the filter test")
        QCoreApplication.processEvents()

        win.image_explorer_up_button.click()
        ok8 = wait_until(lambda: win.image_selected_path == "/games",
                         timeout=15, what="image Up selects parent")
        check("image Up selects parent", ok8, win.image_selected_path)
        check("image Up updates box", win.diskimageexplorerpathinput.text() == "/games",
              win.diskimageexplorerpathinput.text())

        win.image_explorer_up_button.click()
        ok9 = wait_until(lambda: win.image_selected_path == ""
                         and win.diskimageexplorerpathinput.text() == "/",
                         timeout=15, what="image Up back to root")
        check("image Up to root clears selection", ok9,
              f"sel={win.image_selected_path!r} box={win.diskimageexplorerpathinput.text()!r}")

        win.image_explorer_up_button.click()
        QCoreApplication.processEvents()
        check("image Up at root is a no-op",
              win.image_selected_path == "" and win.diskimageexplorerpathinput.text() == "/",
              win.diskimageexplorerpathinput.text())

    # The SD Card page zeroes only its TOP margin, so both tools start at the
    # same height under the sub-tab bar. Reading contentsMargins() before
    # setLayout() (where an unparented layout answers 0) zeroed the other three
    # as well and the whole page lost its padding.
    _sdm = win.zx_next_unite_form.contentsMargins()
    check("SD Card page keeps its left/right/bottom margins, top zeroed",
          _sdm.top() == 0 and _sdm.left() > 0 and _sdm.right() > 0
          and _sdm.bottom() > 0,
          f"{_sdm.left()},{_sdm.top()},{_sdm.right()},{_sdm.bottom()}")

    # ---- retro log console color picker (Settings tab) ---------------------
    lay = win.settings_btn_color_retro_log.parentWidget().layout()
    def spos(w):
        i = lay.indexOf(w)
        return None if i < 0 else lay.getItemPosition(i)[:2]
    check("general-text swatch at its named row",
          spos(win.settings_btn_color_general_text)
          == (settings_row("color_general_text"), 1),
          str(spos(win.settings_btn_color_general_text)))
    check("retro-log swatch right under it",
          spos(win.settings_btn_color_retro_log)
          == (settings_row("color_retro_log"), 1),
          str(spos(win.settings_btn_color_retro_log)))
    check("retro font combo at its named row",
          spos(win.settings_retro_log_font_combo)
          == (settings_row("retro_log_font"), 1),
          str(spos(win.settings_retro_log_font_combo)))
    check("default retro color is phosphor green",
          win.img_color_retro_log.name().lower() == "#78ff8c", win.img_color_retro_log.name())
    check("default swatch shows phosphor green",
          "#78ff8c" in win.settings_btn_color_retro_log.styleSheet().lower(),
          win.settings_btn_color_retro_log.styleSheet())
    check("retro color persisted to cfg", "color_retro_log=#78ff8c" in cfg_lines(),
          str([l for l in cfg_lines() if l.startswith("color_retro_log")]))

    app.quit()

def inspect_phase2():
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return
    ok = wait_until(lambda: win.image_selected_path == "/games/sub", timeout=90,
                    what="startup restore of /games/sub")
    check("startup restores saved image path", ok, win.image_selected_path)
    check("box shows restored path", win.diskimageexplorerpathinput.text() == "/games/sub",
          win.diskimageexplorerpathinput.text())
    check("tree selection valid", win.image_treeview.currentIndex().isValid())
    check("general font size restored from cfg (8 pt applied)",
          win.settings_general_font_combo.font().pointSize() == 8
          and win.settings_zxnu_update_check_checkbox.font().pointSize() == 8
          and win.settings_general_font_combo.currentData() == 8,
          f"{win.settings_general_font_combo.font().pointSize()} / {win.settings_general_font_combo.currentData()}")
    check("update-on-connect prompt restored unchecked from cfg",
          not win.settings_re_update_prompt_checkbox.isChecked())
    check("verify-CRC toggle restored unchecked from cfg",
          not win.settings_nextsync_verify_crc_checkbox.isChecked())
    check("NextSync Sessions toggle restored unchecked from cfg",
          not win.settings_nextsync_sessions_checkbox.isChecked())
    check("retro color restored from cfg",
          win.img_color_retro_log.name().lower() == "#112233", win.img_color_retro_log.name())
    check("retro swatch shows restored color",
          "#112233" in win.settings_btn_color_retro_log.styleSheet().lower(),
          win.settings_btn_color_retro_log.styleSheet())
    # The ground round-trips too, and reaches the app-wide stylesheet that the
    # two Remote Explorer panes read - through the Custom path, which returns
    # early and would otherwise never apply it.
    check("background colour restored from cfg",
          win.img_color_background.name().lower() == "#204060",
          win.img_color_background.name())
    check("background swatch shows restored color",
          "#204060" in win.settings_btn_color_background.styleSheet().lower(),
          win.settings_btn_color_background.styleSheet())
    check("restored ground reached the explorer viewports",
          "rgba(32, 64, 96, 216)" in QApplication.instance().styleSheet(),
          QApplication.instance().styleSheet()[-160:])
    check("restored ground reached the window fill",
          win._bg_widget._bg_color is not None
          and win._bg_widget._bg_color.name().lower() == "#204060",
          str(win._bg_widget._bg_color))
    app.quit()

def inspect_phase3():
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return
    ok = wait_until(lambda: recent_log(win, "Image path not found: /gone"), timeout=90,
                    what="missing-path advisory at startup")
    check("missing saved path logs advisory", ok)
    check("box falls back to image root", win.diskimageexplorerpathinput.text() == "/",
          win.diskimageexplorerpathinput.text())
    check("selection stays clear", win.image_selected_path == "", win.image_selected_path)
    ok2 = wait_until(lambda: "image_explorerpath=/" in cfg_lines(), timeout=15,
                     what="root re-persisted to cfg")
    check("stale path re-persisted as /", ok2,
          str([l for l in cfg_lines() if l.startswith("image_explorerpath")]))
    app.quit()

def inspect_phase4():
    import re
    from PySide6.QtWidgets import QAbstractItemView
    from PySide6.QtCore import (QMimeData, QUrl, QPoint, QPointF, Qt,
                                QItemSelectionModel)
    from PySide6.QtGui import QColor, QWheelEvent
    # LATE zxnu imports are safe here: settings_row's rule is about
    # MODULE-LEVEL ones, and by now the app has cached these modules with
    # the scratch argv.
    from zxnu_remote_explorer import ColoredFileSystemModel
    from zxnu_workers import DotDotFirstProxyModel
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return
    tv = win.nextsync_treeview
    proxy = win.nextsync_model             # the PROXY (naming quirk)
    model = win.nextsync_filesystem_model  # the SOURCE
    check("nextsync tree accepts drops", tv.acceptDrops())
    # The VIEWPORT is the widget Qt actually delivers drag events to.
    check("nextsync tree viewport accepts drops", tv.viewport().acceptDrops())

    # --- the startup drag guard (9.7.29, made to work in 9.7.39). This
    # inspector runs from a singleShot(0) as exec() starts, normally well
    # before the 3600 ms arm_drag_views timer, so every drag-enabled tree
    # must still be DISARMED here - it used to read armed, because each of
    # them called setDragDropMode(DragDrop) (which re-enables dragging) on
    # the line after register_drag_view. That ordering is not timing luck:
    # singleShot(0) POSTS the inspector as an event, every Qt dispatcher
    # sends posted events before it fires timers, and nothing pumps the loop
    # between the arm timer being scheduled and exec() - so even an OVERDUE
    # arm timer runs after this (measured with one 3.3 s overdue). Hence a
    # hard check, not a branch: an armed start means something now pumps the
    # loop during startup, which is worth failing on.
    import zxnu_config as _zc
    _drag_trees = (("classic", tv), ("sdcard local", win.treeview),
                   ("sdcard image", win.image_treeview))
    check("the inspector runs before the startup drag arm",
          not _zc._DRAG_ARMED)
    for _lbl, _v in _drag_trees:
        check(f"{_lbl} tree is still drag-disarmed at startup",
              not _v.dragEnabled(), str(_v.dragEnabled()))
        # Disarmed is DRAG-out only: Qt derives the mode from the two
        # flags, and drops must keep working throughout.
        check(f"{_lbl} tree still takes drops while disarmed",
              _v.acceptDrops()
              and _v.dragDropMode() == QAbstractItemView.DropOnly,
              str(_v.dragDropMode()))
    check("the arm timer fires once the app is up",
          wait_until(lambda: _zc._DRAG_ARMED, 15, "drag views armed"))
    for _lbl, _v in _drag_trees:
        check(f"{_lbl} tree drag enabled once armed", _v.dragEnabled())
        check(f"{_lbl} tree mode is DragDrop once armed",
              _v.dragDropMode() == QAbstractItemView.DragDrop,
              str(_v.dragDropMode()))
    check("nextsync tree default action Copy",
          tv.defaultDropAction() == Qt.CopyAction, str(tv.defaultDropAction()))
    # The drag & drop handlers are the pane's own closures, still on the
    # VIEW after the 9.7.39 model swap (an assigned closure reports its own
    # __name__; an unassigned attribute is the bound Qt method) - drag-OUT
    # included, which has its own copy-only startDrag since 9.7.39.
    for _attr, _name in (("dropEvent", "_nextsync_drop"),
                         ("dragEnterEvent", "_nextsync_drag_enter"),
                         ("dragMoveEvent", "_nextsync_drag_move"),
                         ("keyPressEvent", "_nextsync_tree_key_press"),
                         ("startDrag", "_nextsync_start_drag")):
        _got = getattr(getattr(tv, _attr), "__name__", "")
        check(f"classic {_attr} is the pane's closure", _got == _name, _got)

    # --- the PAINTED model (9.7.39): the Classic sync tree now shares the
    # other two local trees' ColoredFileSystemModel.
    check("classic source model is the painted one",
          isinstance(model, ColoredFileSystemModel), type(model).__name__)
    check("naming quirk intact: nextsync_model is the proxy over it",
          isinstance(proxy, DotDotFirstProxyModel)
          and proxy.sourceModel() is model and tv.model() is proxy)
    # Columns stay LOGICAL - only the header's visual order moved.
    hdr = tv.header()
    _visual = [hdr.visualIndex(i) for i in range(4)]
    check("classic tree shows Name/Type/Size/Modified",
          _visual == [0, 2, 1, 3], str(_visual))
    check("classic tree still sorts on the logical Name column",
          hdr.sortIndicatorSection() == 0, str(hdr.sortIndicatorSection()))
    check("classic tree is not in-place editable",
          tv.editTriggers() == QAbstractItemView.NoEditTriggers,
          str(tv.editTriggers()))
    check("classic tree uses uniform row heights", tv.uniformRowHeights())
    check("classic tree font restored from the cfg",
          tv.font().pointSize() == CLASSIC_FONT_PT, str(tv.font().pointSize()))
    check("classic model stays read-only (dropMimeData would refuse a drop)",
          model.isReadOnly())

    # A colour change repaints the tree. The premise is what makes this bite:
    # the Remote Explorer is built lazily and _re_apply_item_colors returns
    # early without it, so a repaint placed after that return would never run
    # for a user who only uses Classic sync.
    check("premise: the Remote Explorer was never built in this phase",
          getattr(win, "_re_widget", None) is None)
    _vp = tv.viewport()
    _hits = []
    _orig_update = _vp.update
    _vp.update = lambda *a: (_hits.append(a), _orig_update(*a))
    try:
        win._image_recolor_all()
        win._re_apply_item_colors()   # exactly what the Settings picker calls
    finally:
        del _vp.update
    check("a colour change repaints the classic tree", len(_hits) >= 1,
          str(len(_hits)))

    # Put the Classic sync view on screen: geometry (visualRect / indexAt)
    # means nothing on a view that was never shown and laid out. Classic
    # FIRST, then the main tab, so the Remote Explorer is never selected on
    # the way and never built.
    from zxnu_config import (TRANSFER_SUBTAB_CLASSIC,
                             ZX_NEXT_UNITE_TAB_TITLE_TRANSFER)
    win.nextsync_mode_tabs.setCurrentIndex(TRANSFER_SUBTAB_CLASSIC)
    main_tabs = win._bg_widget.tab
    _idx = next((i for i in range(main_tabs.count())
                 if main_tabs.tabText(i).startswith(
                     ZX_NEXT_UNITE_TAB_TITLE_TRANSFER)), None)
    check("Transfer tools tab present", _idx is not None)
    if _idx is not None:
        main_tabs.setCurrentIndex(_idx)
    check("the Classic sync tree is on screen",
          wait_until(tv.isVisible, 20, "classic tree visible"))

    # Every drop below goes through Qt's REAL delivery (DragEnter -> DragMove
    # -> Drop to the viewport) rather than calling the closure directly: that
    # is the path that also checks the viewport accepts drops, the widget is
    # enabled and the DragEnter was accepted. Empty space (10, 9000) resolves
    # to the folder the tree is showing, deterministically - unlike a point
    # near the top, which can land on the ".." row or a subfolder.
    check("classic tree shows the drop zone", _classic_goto(win, DROPZONE))
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(DROPSRC)])
    enter, drop = _sim_drag(tv, md, (10, 9000))
    check("OS drag is accepted on enter", enter.isAccepted())
    check("OS drop is delivered and accepted",
          drop is not None and drop.isAccepted())
    ok = wait_until(lambda: os.path.isfile(os.path.join(DROPZONE, "dropsrc.txt")),
                    timeout=30, what="dropped file lands in drop zone")
    check("OS drop imports the file", ok)
    check("source file untouched (copy, not move)", os.path.isfile(DROPSRC))

    # --- display texts, read through the proxy the view reads (logical
    # columns: 1=Size, 2=Type, 3=Modified).
    check("classic tree shows the fixture folder", _classic_goto(win, CLASSIC_ZONE))
    s512 = os.path.join(CLASSIC_ZONE, "size512.bin")
    s1100 = os.path.join(CLASSIC_ZONE, "size1100.bin")
    for _p in (s512, s1100, CLASSIC_INTO,
               os.path.join(CLASSIC_ZONE, "noext"),
               os.path.join(CLASSIC_ZONE, "a.b.c")):
        check(f"classic row listed: {os.path.basename(_p)}",
              _classic_row(win, _p) is not None)

    def _txt(p, col, role=Qt.ItemDataRole.DisplayRole):
        ix = proxy.mapFromSource(model.index(p, col))
        return ix.data(role) if ix.isValid() else "<no row>"

    wait_until(lambda: _txt(s512, 1) == "512 B", 20, "size512 listed with its size")
    check("Size: bytes under 1 KB", _txt(s512, 1) == "512 B", repr(_txt(s512, 1)))
    check("Size: one decimal in K", _txt(s1100, 1) == "1.1 K", repr(_txt(s1100, 1)))
    check("Type: the extension", _txt(s512, 2) == "bin", repr(_txt(s512, 2)))
    _p = os.path.join(CLASSIC_ZONE, "noext")
    check("Type: blank without an extension", _txt(_p, 2) == "", repr(_txt(_p, 2)))
    _p = os.path.join(CLASSIC_ZONE, "a.b.c")
    check("Type: the first extension segment", _txt(_p, 2) == "b", repr(_txt(_p, 2)))
    _want = time.strftime("%Y-%m-%d %H:%M", time.localtime(CLASSIC_MTIME))
    check("Modified: ISO stamp in local time", _txt(s512, 3) == _want,
          f"{_txt(s512, 3)!r} != {_want!r}")
    check("folder: blank Size", _txt(CLASSIC_INTO, 1) == "", repr(_txt(CLASSIC_INTO, 1)))
    check("folder: Type DIR", _txt(CLASSIC_INTO, 2) == "DIR", repr(_txt(CLASSIC_INTO, 2)))
    check("folder: real date",
          bool(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}",
                            str(_txt(CLASSIC_INTO, 3)))),
          repr(_txt(CLASSIC_INTO, 3)))
    _root = tv.rootIndex()
    _updir = next((proxy.index(r, 0, _root)
                   for r in range(proxy.rowCount(_root))
                   if proxy.index(r, 0, _root).data() == ".."), None)
    check("'..' row present and pinned first",
          _updir is not None and _updir.row() == 0)
    if _updir is not None:
        _cells = tuple(_updir.siblingAtColumn(c).data() for c in (1, 2, 3))
        check("'..' row reads blank / DIR / blank", _cells == ("", "DIR", ""),
              str(_cells))

    # '..' stays first through the VIEW's own sort (what a header click
    # ends in), every column, both orders: Qt's descending sort calls
    # lessThan(right, left), and a fixed "'..' is less" answer used to sink
    # it to the BOTTOM of every descending sort. The original sort is put
    # back afterwards. The root is read into a name of its OWN: `_root`
    # above is a plain QModelIndex that the colour check below still reads,
    # and a proxy index taken mid-loop names a ROW, which the restoring
    # sort can hand to another folder (timestamps tie on Linux, so the
    # Modified sort leaves the shown folder where the stable order put it).
    _hdr = tv.header()
    _was = (_hdr.sortIndicatorSection(), _hdr.sortIndicatorOrder())
    _sunk = []
    for _col in range(4):
        for _ord in (Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder):
            tv.sortByColumn(_col, _ord)
            _sroot = tv.rootIndex()
            _first = proxy.index(0, 0, _sroot).data() if proxy.rowCount(_sroot) else None
            if _first != "..":
                _sunk.append((_col, _ord.name, _first))
    tv.sortByColumn(*_was)
    # ...and `_root` itself is re-read from the view, whose root index IS
    # persistent: even the restored sort only reproduces the old rows while
    # the Name column has no ties. `_updir` is found again by NAME for the
    # same reason; the colour checks below read both.
    _root = tv.rootIndex()
    _updir = next((proxy.index(r, 0, _root)
                   for r in range(proxy.rowCount(_root))
                   if proxy.index(r, 0, _root).data() == ".."), None)
    check("'..' row stays first in every column, both orders", not _sunk,
          str(_sunk))

    # --- item colours: the one restored from the cfg AFTER the tree was
    # built, every column's colour family, and a REBIND followed live.
    FG = Qt.ItemDataRole.ForegroundRole

    def _fg(p, col):
        v = _txt(p, col, FG)
        return QColor(v).name() if v is not None and v != "<no row>" else None

    check("the cfg's file-name colour shows on the classic tree",
          _fg(s512, 0) == CLASSIC_FILE_COLOR, str(_fg(s512, 0)))
    check("file name uses the file-name colour",
          _fg(s512, 0) == win.img_color_file_name.name(), str(_fg(s512, 0)))
    check("folder name uses the folder colour",
          _fg(CLASSIC_INTO, 0) == win.img_color_dir_name.name(),
          str(_fg(CLASSIC_INTO, 0)))
    check("file Type uses the extension colour",
          _fg(s512, 2) == win.img_color_file_ext.name(), str(_fg(s512, 2)))
    check("folder Type uses the DIR colour",
          _fg(CLASSIC_INTO, 2) == win.img_color_dir_type.name(),
          str(_fg(CLASSIC_INTO, 2)))
    check("Size and Modified use the size colour",
          _fg(s512, 1) == _fg(s512, 3) == win.img_color_file_size.name(),
          f"{_fg(s512, 1)} {_fg(s512, 3)}")
    if _updir is not None:
        _v = _updir.data(FG)
        check("'..' uses the up-directory colour",
              _v is not None and QColor(_v).name()
              == win.img_color_up_directory.name(), str(_v))
    _bad = [(r, c) for r in range(proxy.rowCount(_root)) for c in range(4)
            if (lambda v: v is not None and not QColor(v).isValid())(
                proxy.index(r, c, _root).data(FG))]
    check("no cell paints with an invalid (black) colour", not _bad, str(_bad))
    _saved = win.img_color_file_name
    try:
        # Settings REBINDS the attribute (never mutates it) - a snapshot
        # would keep answering the old colour here.
        win.img_color_file_name = QColor("#13579b")
        check("a rebound host colour is followed live, no hook, no re-list",
              _fg(s512, 0) == "#13579b", str(_fg(s512, 0)))
    finally:
        win.img_color_file_name = _saved

    # --- the swapped Type column is drawn LEFT of Size, and a drop on it
    # still resolves to its row: the handlers never read the column. Every
    # row lookup below is a CHECK, not just a guard: a lookup that timed out
    # would otherwise skip its block and still report the phase green.
    pix = _classic_row(win, CLASSIC_INTO)
    check("classic folder row found for the Type-cell drop", pix is not None)
    if pix is not None:
        _rt = tv.visualRect(pix.siblingAtColumn(2))
        _rs = tv.visualRect(pix.siblingAtColumn(1))
        check("Type cell is drawn left of the Size cell",
              not _rt.isEmpty() and not _rs.isEmpty() and _rt.left() < _rs.left(),
              f"type={_rt} size={_rs}")
        md_into = QMimeData()
        md_into.setUrls([QUrl.fromLocalFile(DROPSRC)])
        enter, drop = _sim_drag(tv, md_into, (_rt.center().x(), _rt.center().y()))
        check("OS drop on a folder's Type cell is delivered",
              drop is not None and drop.isAccepted())
        check("...and imports INTO that folder",
              wait_until(lambda: os.path.isfile(
                  os.path.join(CLASSIC_INTO, "dropsrc.txt")), 30,
                  "drop lands in the folder row"))

    # --- intra-tree drag: a COPY into the folder it lands on, never a move.
    # A synthesized drop has no source(), so _DropFrom supplies the tree.
    moveme = os.path.join(CLASSIC_ZONE, "moveme.txt")
    check("classic tree back on the fixture folder", _classic_goto(win, CLASSIC_ZONE))
    pix = _classic_row(win, CLASSIC_INTO)
    check("classic folder row re-listed after the drop's refresh",
          pix is not None)
    if pix is not None:
        _r = tv.visualRect(pix)
        md_in = QMimeData()
        md_in.setUrls([QUrl.fromLocalFile(moveme)])
        enter, drop = _sim_drag(tv, md_in, (_r.center().x(), _r.center().y()),
                                source=tv)
        check("intra-tree drop onto a folder is delivered",
              drop is not None and drop.isAccepted())
        check("...and copies the file into it",
              wait_until(lambda: os.path.isfile(
                  os.path.join(CLASSIC_INTO, "moveme.txt")), 30,
                  "intra-tree copy lands"))
        check("...leaving the original where it was", os.path.isfile(moveme))

    # Dropping an item back into its own folder is a no-op, keyed on the
    # drag coming FROM this tree - the control proves an outside drag of the
    # same file does make the "-(copy)" duplicate, so the no-op is real.
    _dup = os.path.join(CLASSIC_ZONE, "moveme-(copy).txt")
    check("classic tree back on the fixture folder", _classic_goto(win, CLASSIC_ZONE))
    md_same = QMimeData()
    md_same.setUrls([QUrl.fromLocalFile(moveme)])
    enter, drop = _sim_drag(tv, md_same, (10, 9000), source=tv)
    _settle = time.monotonic() + 0.5
    while time.monotonic() < _settle:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    check("same-folder intra-tree drop is refused",
          drop is not None and not drop.isAccepted())
    check("...and makes no duplicate", not os.path.exists(_dup))
    md_ctl = QMimeData()
    md_ctl.setUrls([QUrl.fromLocalFile(moveme)])
    enter, drop = _sim_drag(tv, md_ctl, (10, 9000))
    check("control: the same drop from OUTSIDE makes the duplicate",
          wait_until(lambda: os.path.isfile(_dup), 30, "outside drop duplicates"))
    # ...and EXACTLY one: a no-op that still imported late (after the 0.5 s
    # settle above) would leave a second "-(copy)" sibling here, which the
    # control's own duplicate would otherwise hide.
    _settle = time.monotonic() + 0.5
    while time.monotonic() < _settle:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    _mm = sorted(n for n in os.listdir(CLASSIC_ZONE) if n.startswith("moveme"))
    check("...exactly one duplicate, the control's",
          _mm == ["moveme-(copy).txt", "moveme.txt"], str(_mm))

    # A drag carrying no files is refused at the door: no Drop is delivered.
    md_txt = QMimeData()
    md_txt.setText("not a file")
    enter, drop = _sim_drag(tv, md_txt, (10, 9000))
    check("a non-file drag is refused on enter",
          not enter.isAccepted() and drop is None)

    # A drop on a FILE row lands in that file's folder - the dirname branch
    # of the drop target, which the folder-row drops above never reach. The
    # file sits in an EXPANDED subfolder: a file at the root would land in
    # the root either way, so it could not tell that branch from the
    # root fallback.
    check("classic tree back on the fixture folder", _classic_goto(win, CLASSIC_ZONE))
    _nested = os.path.join(CLASSIC_INTO, "moveme.txt")   # the intra-tree copy's
    check("subfolder expanded for the file-row drop",
          _expand_and_watch(tv, proxy, model, CLASSIC_INTO))
    pix = _classic_row(win, _nested)
    check("nested file row found for the file-row drop", pix is not None)
    if pix is not None:
        _r = tv.visualRect(pix)
        md_file = QMimeData()
        md_file.setUrls([QUrl.fromLocalFile(FILEROW_SRC)])
        enter, drop = _sim_drag(tv, md_file, (_r.center().x(), _r.center().y()))
        check("OS drop on a file row is delivered",
              drop is not None and drop.isAccepted())
        check("...and lands in THAT file's folder, not the tree's root",
              wait_until(lambda: os.path.isfile(
                  os.path.join(CLASSIC_INTO, "filerow-drop.txt")), 30,
                  "file-row drop lands beside the file")
              and not os.path.exists(
                  os.path.join(CLASSIC_ZONE, "filerow-drop.txt")))
    _into_ix = proxy.mapFromSource(model.index(CLASSIC_INTO))
    if _into_ix.isValid():
        tv.collapse(_into_ix)

    # A drop on the ".." row lands in the folder the tree is SHOWING - ".."
    # is the way up, never a drop target for the parent.
    check("classic tree back on the fixture folder", _classic_goto(win, CLASSIC_ZONE))
    _root = tv.rootIndex()
    _updir = next((proxy.index(r, 0, _root)
                   for r in range(proxy.rowCount(_root))
                   if proxy.index(r, 0, _root).data() == ".."), None)
    check("'..' row found for the '..' drop", _updir is not None)
    if _updir is not None:
        _r = tv.visualRect(_updir)
        md_up = QMimeData()
        md_up.setUrls([QUrl.fromLocalFile(UPDIR_SRC)])
        enter, drop = _sim_drag(tv, md_up, (_r.center().x(), _r.center().y()))
        check("OS drop on the '..' row is delivered",
              drop is not None and drop.isAccepted())
        check("...and lands in the folder shown, not its parent",
              wait_until(lambda: os.path.isfile(
                  os.path.join(CLASSIC_ZONE, "updir-drop.txt")), 30,
                  "'..' drop lands in the shown folder")
              and not os.path.exists(os.path.join(DROPZONE, "updir-drop.txt")))

    # URLs that are not local files (a browser link): accepted as a drag -
    # they ARE urls - but the drop itself must refuse and copy nothing.
    check("classic tree back on the fixture folder", _classic_goto(win, CLASSIC_ZONE))
    md_web = QMimeData()
    md_web.setUrls([QUrl("https://example.invalid/web-drop.txt")])
    enter, drop = _sim_drag(tv, md_web, (10, 9000))
    _settle = time.monotonic() + 0.5
    while time.monotonic() < _settle:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    check("a drop of non-local URLs is refused",
          drop is not None and not drop.isAccepted())
    check("...and copies nothing",
          not os.path.exists(os.path.join(CLASSIC_ZONE, "web-drop.txt")))

    # --- drag-OUT (9.7.39): the Classic tree's own startDrag carries only
    # real selected rows, and only as a COPY. Qt's default, which it ran
    # before, offered Copy|Move|Link (a same-drive Explorer drop could MOVE
    # the file out of the sync root) and carried the ".." row as the PARENT
    # folder. The module's QDrag is swapped for a recording subclass, so
    # what is asserted is exactly what the app hands Qt - the URLs and the
    # actions given to exec() - and no platform drag loop ever runs.
    check("classic tree back on the fixture folder", _classic_goto(win, CLASSIC_ZONE))
    _dir = _classic_row(win, CLASSIC_INTO)
    check("classic folder row found for the drop-flag check", _dir is not None)
    if _dir is not None:
        # A FOLDER row, because QFileSystemModel only ever makes a folder
        # drop-enabled (and only once it is not read-only) - a file row
        # would pass this whether or not the invariant held.
        check("a folder row is not drop-enabled (drops ride the view's handlers)",
              not proxy.flags(_dir) & Qt.ItemFlag.ItemIsDropEnabled)
    pix = _classic_row(win, s512)
    check("classic file row found for the drag-out checks", pix is not None)
    _nsp = sys.modules.get("zxnu_nextsync_pane")
    check("the pane module is loaded (to record its drags)", _nsp is not None)
    if pix is not None and _nsp is not None:
        # Qt starts a drag only on rows the MODEL flags draggable.
        check("a file row is drag-enabled",
              bool(proxy.flags(pix) & Qt.ItemFlag.ItemIsDragEnabled))
        _drags = []
        _RealDrag = _nsp.QDrag

        class _RecDrag(_RealDrag):
            def exec(self, *a):
                md = self.mimeData()
                _drags.append((
                    [os.path.normcase(os.path.abspath(u.toLocalFile()))
                     for u in md.urls()] if md is not None else [],
                    a[0] if a else None, self.parent()))
                return Qt.DropAction.IgnoreAction

        def _select(ix):
            tv.setCurrentIndex(ix)
            tv.selectionModel().select(
                ix, QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows)

        _want = [os.path.normcase(os.path.abspath(s512))]
        _nsp.QDrag = _RecDrag
        try:
            # 1. A direct call, with the actions Qt's mouseMoveEvent passes.
            _select(pix)
            _sel = [i.row() for i in tv.selectionModel().selectedRows()]
            check("the file row is what is selected for the drag",
                  _sel == [pix.row()], str(_sel))
            tv.startDrag(proxy.supportedDragActions())
            check("drag-out starts exactly one drag", len(_drags) == 1,
                  str(_drags))
            if _drags:
                _urls, _acts, _src = _drags[-1]
                check("drag-out payload is exactly that file", _urls == _want,
                      str(_urls))
                check("drag-out offers a COPY only - never Move or Link",
                      _acts == Qt.DropAction.CopyAction, str(_acts))
                check("the drag's source is the tree (the intra-tree no-op "
                      "recognises its own drags by it)", _src is tv)
            # 2. The ".." row never travels: dragging it starts nothing.
            _root = tv.rootIndex()
            _up = next((proxy.index(r, 0, _root)
                        for r in range(proxy.rowCount(_root))
                        if proxy.index(r, 0, _root).data() == ".."), None)
            check("'..' row found for the drag-out check", _up is not None)
            if _up is not None:
                _n = len(_drags)
                _select(_up)
                tv.startDrag(proxy.supportedDragActions())
                check("dragging the '..' row starts no drag",
                      len(_drags) == _n, str(_drags[_n:]))
            # 3. A REAL mouse gesture - press, then two moves past the start
            # distance, which is how QAbstractItemView enters DraggingState
            # and calls startDrag - now that the drag views are armed.
            from PySide6.QtTest import QTest
            pix = _classic_row(win, s512)
            check("classic file row found for the drag gesture",
                  pix is not None)
            if pix is not None:
                _select(pix)
                _c = tv.visualRect(pix).center()
                _step = QApplication.startDragDistance() + 5
                _n = len(_drags)
                QTest.mousePress(tv.viewport(), Qt.MouseButton.LeftButton,
                                 Qt.KeyboardModifier.NoModifier, _c)
                QTest.mouseMove(tv.viewport(), _c + QPoint(_step, 0))
                QTest.mouseMove(tv.viewport(), _c + QPoint(2 * _step, 0))
                QTest.mouseRelease(tv.viewport(), Qt.MouseButton.LeftButton,
                                   Qt.KeyboardModifier.NoModifier,
                                   _c + QPoint(2 * _step, 0))
                check("a real mouse gesture starts the tree's own drag",
                      len(_drags) == _n + 1, str(_drags[_n:]))
                if len(_drags) > _n:
                    check("...carrying that file, as a copy only",
                          _drags[-1][0] == _want
                          and _drags[-1][1] == Qt.DropAction.CopyAction,
                          str(_drags[-1][:2]))
        finally:
            _nsp.QDrag = _RealDrag
        check("...and the dragged file is still there", os.path.isfile(s512))

    # --- Ctrl + mouse-wheel font zoom (9.7.39): one point per notch over
    # the rows or the header, persisted to the cfg at once; a plain wheel
    # is left to scroll.
    def _wheel(target, dy, ctrl=True):
        QApplication.sendEvent(target, QWheelEvent(
            QPointF(20, 20), QPointF(20, 20), QPoint(), QPoint(0, dy),
            Qt.NoButton, Qt.ControlModifier if ctrl else Qt.NoModifier,
            Qt.NoScrollPhase, False))

    _wheel(tv.viewport(), 120)
    check("Ctrl+wheel-up over the classic rows grows the font one point",
          tv.font().pointSize() == CLASSIC_FONT_PT + 1,
          str(tv.font().pointSize()))
    check("...and persists it to the cfg",
          wait_until(lambda: f"nextsync_tree_font={CLASSIC_FONT_PT + 1}"
                     in cfg_lines(), 10, "classic tree font persisted"),
          str([ln for ln in cfg_lines() if ln.startswith("nextsync_tree_font")]))
    # A plain wheel must not zoom AND must still reach the tree to scroll: a
    # filter swallowing every wheel would pass the first half alone. The
    # viewport's wheel reaches the view's (Python-dispatched) wheelEvent.
    _wh = []
    tv.wheelEvent = lambda e: _wh.append(e)
    try:
        _wheel(tv.viewport(), 120, ctrl=False)
    finally:
        del tv.wheelEvent
    check("a plain wheel does not zoom the classic tree",
          tv.font().pointSize() == CLASSIC_FONT_PT + 1,
          str(tv.font().pointSize()))
    check("...and still reaches the tree to scroll", len(_wh) == 1,
          str(len(_wh)))
    _wheel(tv.header().viewport(), -120)
    _wheel(tv.header().viewport(), -120)
    check("Ctrl+wheel-down over the classic header shrinks it",
          tv.font().pointSize() == CLASSIC_FONT_PT - 1,
          str(tv.font().pointSize()))
    check("...and the cfg follows",
          wait_until(lambda: f"nextsync_tree_font={CLASSIC_FONT_PT - 1}"
                     in cfg_lines(), 10, "classic tree font re-persisted"))

    # --- the SD Card Utility's LOCAL tree: its drop properties (drag enabled
    # and the DragDrop mode are asserted by the arm checks at the top), a drop
    # through Qt's real delivery like the Classic tree's, and the 9.7.37
    # styling. That pane swapped its plain QFileSystemModel for the Remote
    # Explorer's ColoredFileSystemModel and gained header().swapSections(1, 2)
    # - a cosmetic change, but it landed on a widget whose drag & drop had no
    # test at all, so this is the net under it. The drag/drop wiring lives in
    # zxnu_main.py and is applied AFTER the pane is built, so what these pin
    # is that the pane never starts setting them itself and silently losing
    # to (or fighting) that wiring.
    sd = win.treeview
    check("sdcard local tree accepts drops", sd.acceptDrops())
    check("sdcard local tree viewport accepts drops", sd.viewport().acceptDrops())
    check("sdcard local tree default action Copy",
          sd.defaultDropAction() == Qt.CopyAction, str(sd.defaultDropAction()))

    # Columns stay LOGICAL - only the header's visual order moved - so a
    # failure here means either the swap was lost or someone reordered the
    # columns for real, which would break every selectedRows(0) in the app.
    hdr = sd.header()
    _visual = [hdr.visualIndex(i) for i in range(4)]
    check("sdcard local tree shows Name/Type/Size/Modified",
          _visual == [0, 2, 1, 3], str(_visual))
    check("sdcard local tree is not in-place editable",
          sd.editTriggers() == QAbstractItemView.NoEditTriggers,
          str(sd.editTriggers()))

    sd_drop = os.path.join(DROPZONE, "sdcard")
    os.makedirs(sd_drop, exist_ok=True)
    win.local_file_explorer_path.setText(sd_drop)
    win.local_file_explorer_path.editingFinished.emit()
    QCoreApplication.processEvents()
    md2 = QMimeData()
    md2.setUrls([QUrl.fromLocalFile(DROPSRC)])
    # Empty space, through Qt's real delivery (the SD page is not the one on
    # screen here, which neither delivery nor the drop target depends on).
    enter2, drop2 = _sim_drag(sd, md2, (10, 9000))
    check("sdcard local OS drop is delivered and accepted",
          drop2 is not None and drop2.isAccepted())
    ok2 = wait_until(lambda: os.path.isfile(os.path.join(sd_drop, "dropsrc.txt")),
                     timeout=30, what="dropped file lands in the sdcard drop zone")
    check("sdcard local tree OS drop imports the file", ok2)
    check("source file still untouched after sdcard drop", os.path.isfile(DROPSRC))
    # The SD pane's painted model (9.7.37), which nothing pinned: it now
    # reads its colours through the HostItemColors that moved beside the
    # model in 9.7.39, so the file-name colour restored from the cfg must
    # show here too.
    check("sdcard local source model is the painted one",
          isinstance(win.model, ColoredFileSystemModel), type(win.model).__name__)
    _sd_file = os.path.join(sd_drop, "dropsrc.txt")
    _sd_ix = _row_in(win.proxy_model, win.model, _sd_file)
    check("sdcard local row found for the painted-model check", _sd_ix is not None)
    if _sd_ix is not None:
        # Waited for: the model can list a just-copied file before its size
        # lands (on Linux inotify's directory watch reports no IN_MODIFY).
        def _sd_size():
            ix = _row_in(win.proxy_model, win.model, _sd_file)
            return ix.siblingAtColumn(1).data() if ix is not None else None
        wait_until(lambda: _sd_size() == "7 B", 10, "sdcard size listed")
        _sz = _sd_size()
        check("sdcard local Size uses the unified text", _sz == "7 B", repr(_sz))
        # RE-resolved, never the index taken before the wait: a QModelIndex
        # is not persistent, and a re-list in between (the watcher seeing
        # the copy land) leaves the old one answering None for every role.
        _sd_ix = _row_in(win.proxy_model, win.model, _sd_file)
        _v = (_sd_ix.data(Qt.ItemDataRole.ForegroundRole)
              if _sd_ix is not None else None)
        check("sdcard local file name uses the cfg's file-name colour",
              _v is not None and QColor(_v).name() == CLASSIC_FILE_COLOR, str(_v))
    app.quit()

def _expand_and_watch(tv, proxy, model, path):
    """Expand *path* in the tree and wait until its children are listed
    (which is what makes QFileSystemModel watch it). Offscreen the view never
    reaches the layout phase that calls fetchMore, so kick the source model
    directly — the gatherer's listing is also what attaches the watcher."""
    if not wait_until(lambda: model.index(path).isValid(), 20,
                      f"index for {path}"):
        return False
    tv.expand(proxy.mapFromSource(model.index(path)))

    def _fetched():
        ix = model.index(path)
        if model.canFetchMore(ix):
            model.fetchMore(ix)
        return model.rowCount(ix) >= 1
    return wait_until(_fetched, 20, f"children of {path}")

def _press_delete(tv):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                   Qt.KeyboardModifier.NoModifier)
    tv.keyPressEvent(ev)

def _sim_drag(view, mime, pos, source=None):
    """Deliver a drag to *view* the way Qt does, and return (enter, drop).

    DragEnter -> DragMove -> Drop, each sent to the VIEWPORT through
    QApplication.sendEvent. Qt delivers a Drop only to the widget whose
    DragEnter it ACCEPTED, and only while that widget is enabled and its
    viewport accepts drops; calling view.dropEvent(ev) directly skips all
    three gates, which is how a broken drop can stay green. The platform
    then drops only if the LAST DragMove was still accepted (Qt pre-accepts
    it with the enter's action in processDrag; Windows OLE never calls Drop
    after a DragOver of DROPEFFECT_NONE) - so a move handler that refuses
    kills the drop here as well. drop is None when the enter or the move
    was refused (a DragLeave is still sent, as Qt does, to keep enter and
    leave balanced). *source* stands in for QDropEvent.source(), which a
    synthesized event leaves None - pass the view itself to act as a drag
    from inside it. The caller keeps *mime* alive: the events do not own it."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import (QDragEnterEvent, QDragLeaveEvent,
                               QDragMoveEvent, QDropEvent)

    class _DropFrom(QDropEvent):
        def __init__(self, src, *a):
            super().__init__(*a)
            self._zx_src = src

        def source(self):
            return self._zx_src

    vp = view.viewport()
    p = QPoint(int(pos[0]), int(pos[1]))
    enter = QDragEnterEvent(p, Qt.CopyAction, mime, Qt.LeftButton,
                            Qt.NoModifier)
    QApplication.sendEvent(vp, enter)
    if not enter.isAccepted():
        QApplication.sendEvent(vp, QDragLeaveEvent())
        return enter, None
    move = QDragMoveEvent(p, Qt.CopyAction, mime, Qt.LeftButton,
                          Qt.NoModifier)
    move.setDropAction(enter.dropAction())
    move.accept()
    QApplication.sendEvent(vp, move)
    if not move.isAccepted():
        QApplication.sendEvent(vp, QDragLeaveEvent())
        return enter, None
    args = (QPointF(p), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    drop = _DropFrom(source, *args) if source is not None else QDropEvent(*args)
    QApplication.sendEvent(vp, drop)
    return enter, drop

def _classic_goto(win, folder):
    """Point the Classic sync tree at *folder* through the sync-root box (the
    user's own route) and wait until the tree is rooted there."""
    win.nextsync_file_explorer_path.setText(folder)
    win.nextsync_file_explorer_path.editingFinished.emit()
    tv, proxy, model = (win.nextsync_treeview, win.nextsync_model,
                        win.nextsync_filesystem_model)
    want = os.path.normcase(os.path.abspath(folder))
    return wait_until(
        lambda: os.path.normcase(os.path.abspath(model.filePath(
            proxy.mapToSource(tv.rootIndex())) or ".")) == want,
        20, f"classic tree rooted at {folder}")

def _row_in(proxy, model, path):
    """The PROXY index of *path* in a local file tree once it is listed, else
    None. Offscreen the view may never reach the layout pass that calls
    fetchMore, so the parent folder's listing is kicked directly (the same
    trick _expand_and_watch uses). Callers record the lookup with check():
    a None silently skipping a block would otherwise read as a pass."""
    def _found():
        parent = model.index(os.path.dirname(path))
        if parent.isValid() and model.canFetchMore(parent):
            model.fetchMore(parent)
        ix = model.index(path)
        return ix.isValid() and proxy.mapFromSource(ix).isValid()
    if not wait_until(_found, 20, f"tree row for {path}"):
        return None
    return proxy.mapFromSource(model.index(path))

def _classic_row(win, path):
    """_row_in on the Classic sync tree (proxy = nextsync_model)."""
    return _row_in(win.nextsync_model, win.nextsync_filesystem_model, path)

def inspect_phase5():
    from PySide6.QtCore import qInstallMessageHandler
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return

    # ---- always-on rotating diagnostic log --------------------------------
    # The app resolves its log path from argv[0]'s dir, i.e. the scratch copy,
    # so it must NOT touch the repo. It is created eagerly with a startup line.
    import logging as _logging
    _logging.getLogger().info("offscreen-test-marker-line")
    for _h in _logging.getLogger().handlers:
        try:
            _h.flush()
        except Exception:
            pass
    log_path = os.path.join(SCRATCH, "zx-next-unite.log")
    check("rotating log file created next to the app", os.path.isfile(log_path))
    if os.path.isfile(log_path):
        body = open(log_path, encoding="utf-8", errors="replace").read()
        check("log carries the startup banner", "starting" in body, body[:200])
        check("log captures live log lines", "offscreen-test-marker-line" in body)
    _repo_now = os.path.getmtime(_REPO_LOG) if os.path.isfile(_REPO_LOG) else None
    check("scratch app did not write to a repo log",
          _repo_now == _REPO_LOG_MTIME0,
          f"repo log mtime changed: {_REPO_LOG_MTIME0} -> {_repo_now}")

    # ---- delete-confirmation wording (Recycle Bin vs permanent) ------------
    # The sweeper CLOSES each dialog (= answers No), so nothing is deleted and
    # nothing ever lands in the user's real Recycle Bin.
    win.settings_no_prompt_on_deletion_checkbox.setChecked(False)
    victim = os.path.join(DELZONE, "victim")
    win.nextsync_file_explorer_path.setText(DELZONE)
    win.nextsync_file_explorer_path.editingFinished.emit()
    QCoreApplication.processEvents()
    tv, proxy, model = (win.nextsync_treeview, win.nextsync_model,
                        win.nextsync_filesystem_model)
    wait_until(lambda: model.index(victim).isValid(), 20, "victim index")
    tv.setCurrentIndex(proxy.mapFromSource(model.index(victim)))
    texts = []
    wtimer = _arm_msgbox_autoclose([], texts=texts)
    rb = win.settings_delete_to_recycle_bin_checkbox
    if rb.isEnabled():
        rb.setChecked(True)
        _press_delete(tv)
        ok = wait_until(lambda: any("Recycle Bin" in t for t in texts),
                        10, "recycle-bin wording")
        check("confirm dialog mentions the Recycle Bin when on", ok, str(texts[-1:]))
        check("no 'cannot be undone' while recycle is on",
              not any("cannot be undone" in t for t in texts), str(texts[-1:]))
        texts.clear()
    else:
        print("NOTE: Send2Trash not installed — recycle wording check skipped")
    rb.setChecked(False)
    _press_delete(tv)
    ok = wait_until(lambda: any("cannot be undone" in t for t in texts),
                    10, "permanent wording")
    check("confirm dialog warns permanent when off", ok, str(texts[-1:]))
    wtimer.stop()
    check("victim survived the rejected confirmations", os.path.exists(victim))

    # Permanent-delete assertions below: no prompts, recycle stays OFF so the
    # files are really removed (and the user's Recycle Bin stays untouched).
    win.settings_no_prompt_on_deletion_checkbox.setChecked(True)

    # Capture Qt warnings: the bug's signature is the watcher thread spamming
    # 'FindNextChangeNotification failed ... (Access is denied.)' when watched
    # directories get deleted under it. Post-fix there must be none.
    watcher_errs = []
    def _mh(_mode, _ctx, msg):
        if "FindNextChangeNotification" in msg:
            watcher_errs.append(msg)
    qInstallMessageHandler(_mh)

    # --- classic NextSync explorer: delete victim (sub + subsub watched) ---
    victim = os.path.join(DELZONE, "victim")
    win.nextsync_file_explorer_path.setText(DELZONE)
    win.nextsync_file_explorer_path.editingFinished.emit()
    QCoreApplication.processEvents()
    tv, proxy, model = (win.nextsync_treeview, win.nextsync_model,
                        win.nextsync_filesystem_model)
    ok = (_expand_and_watch(tv, proxy, model, victim)
          and _expand_and_watch(tv, proxy, model, os.path.join(victim, "sub"))
          and _expand_and_watch(tv, proxy, model,
                                os.path.join(victim, "sub", "subsub")))
    check("classic: victim subtree listed/watched", ok)
    end = time.monotonic() + 1.0
    while time.monotonic() < end:      # let the watcher attach its handles
        QCoreApplication.processEvents()
    tv.setCurrentIndex(proxy.mapFromSource(model.index(victim)))
    _press_delete(tv)
    ok = wait_until(lambda: not os.path.exists(victim), 30,
                    "classic delete removes watched tree")
    check("classic: watched folder tree fully deleted", ok,
          "left behind: " + str(os.path.exists(victim)))

    # --- SD Card local explorer: delete victim2 (sub2 + subsub watched) ----
    victim2 = os.path.join(DELZONE, "victim2")
    win.local_file_explorer_path.setText(DELZONE)
    win.local_file_explorer_path.editingFinished.emit()
    QCoreApplication.processEvents()
    tv, proxy, model = win.treeview, win.proxy_model, win.model
    ok = (_expand_and_watch(tv, proxy, model, victim2)
          and _expand_and_watch(tv, proxy, model, os.path.join(victim2, "sub2"))
          and _expand_and_watch(tv, proxy, model,
                                os.path.join(victim2, "sub2", "subsub")))
    check("sd-tab: victim2 subtree listed/watched", ok)
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        QCoreApplication.processEvents()
    tv.setCurrentIndex(proxy.mapFromSource(model.index(victim2)))
    _press_delete(tv)
    ok = wait_until(lambda: not os.path.exists(victim2), 30,
                    "sd-tab delete removes watched tree")
    check("sd-tab: watched folder tree fully deleted", ok,
          "left behind: " + str(os.path.exists(victim2)))

    check("delzone parent intact", os.path.isdir(DELZONE))
    end = time.monotonic() + 1.0
    while time.monotonic() < end:      # give the watcher thread time to spam
        QCoreApplication.processEvents()
    check("no watcher access-denied spam", not watcher_errs,
          f"{len(watcher_errs)} warning(s), first: {watcher_errs[:1]}")
    qInstallMessageHandler(None)
    app.quit()

def _arm_msgbox_autoclose(seen, texts=None):
    """Poll for visible QMessageBoxes, record their window titles (and, when
    *texts* is given, their body text) and close them. Modal boxes run their
    own event loop, so without this the inspector would deadlock the moment
    one opens — QTimer callbacks keep firing inside modal loops, which is what
    lets the sweep reach the box. Closing a QMessageBox.question answers No."""
    from PySide6.QtWidgets import QMessageBox
    t = QTimer()
    def _sweep():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QMessageBox) and w.isVisible():
                seen.append(w.windowTitle())
                if texts is not None:
                    texts.append(w.text())
                w.close()
    t.timeout.connect(_sweep)
    t.start(100)
    return t

def inspect_phase6():
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return
    seen = []
    timer = _arm_msgbox_autoclose(seen)

    cb = win.settings_zxnu_update_check_checkbox
    lay = cb.parentWidget().layout()
    def spos(w):
        i = lay.indexOf(w)
        return None if i < 0 else lay.getItemPosition(i)[:2]

    # ---- general font size + update-on-connect prompt rows (9.7.2) --------
    check("general font combo at its named row",
          spos(win.settings_general_font_combo) == (settings_row("general_font"), 1),
          str(spos(win.settings_general_font_combo)))
    check("general font row sits directly above the Background colour row",
          settings_row("general_font") + 1 == settings_row("color_background"))
    # Measured on BUILT widgets, not QApplication.font(): with the app
    # stylesheet installed, existing widgets only follow a setFont after a
    # re-polish (review) - which is exactly what these pins prove.
    _pt0 = win.settings_general_font_combo.font().pointSize()
    win.settings_general_font_combo.setCurrentIndex(win.settings_general_font_combo.findData(9))
    QApplication.processEvents()
    check("picking 9 applies the application font live, to widgets already built",
          win.settings_general_font_combo.font().pointSize() == 9
          and win.settings_zxnu_update_check_checkbox.font().pointSize() == 9
          and win.button_start_mame.font().pointSize() == 9,
          str((win.settings_general_font_combo.font().pointSize(),
               win.settings_zxnu_update_check_checkbox.font().pointSize(),
               win.button_start_mame.font().pointSize())))
    check("general font size persists to cfg", "general_font_size=9" in cfg_lines(),
          str([l for l in cfg_lines() if l.startswith("general_font")]))
    win.settings_general_font_combo.setCurrentIndex(0)
    QApplication.processEvents()
    check("Default restores the startup size and clears the cfg value",
          win.settings_general_font_combo.font().pointSize() == _pt0 and "general_font_size=" in cfg_lines(),
          f"{win.settings_general_font_combo.font().pointSize()} vs {_pt0}")
    check("update-on-connect prompt toggle sits directly below the ZXNU update check",
          spos(win.settings_re_update_prompt_checkbox) == (settings_row("re_update_prompt"), 0)
          and settings_row("re_update_prompt") == settings_row("zxnu_update_check") + 1,
          str(spos(win.settings_re_update_prompt_checkbox)))
    win.settings_re_update_prompt_checkbox.setChecked(False)
    QApplication.processEvents()
    check("update-on-connect prompt toggle persists to cfg", "re_update_prompt=false" in cfg_lines(),
          str([l for l in cfg_lines() if l.startswith("re_update")]))
    win.settings_re_update_prompt_checkbox.setChecked(True)
    # Row 0 is the ZXNextRemote itch.io check since 9.6.5; the GitHub one
    # sits right under it. Both resolved BY NAME - the pane's own rule
    # (SETTINGS_TAB_ROWS), so inserting a row never renumbers this test.
    check("update-check toggles at the top of Settings",
          spos(win.settings_zxnextremote_update_check_checkbox)
          == (settings_row("zxnextremote_update_check"), 0)
          and settings_row("zxnextremote_update_check") == 0
          and spos(cb) == (settings_row("zxnu_update_check"), 0)
          and settings_row("zxnu_update_check")
              > settings_row("zxnextremote_update_check"),
          str(spos(cb)))
    check("UI language row right under it",
          spos(win.settings_ui_language_combo)
          == (settings_row("ui_language"), 1),
          str(spos(win.settings_ui_language_combo)))
    check("wizard toggle right under the language row",
          spos(win.settings_wizard_checkbox)
          == (settings_row("wizard"), 0)
          and settings_row("wizard")
          == settings_row("ui_language") + 1,
          str(spos(win.settings_wizard_checkbox)))
    check("desktop theme at its named row",
          spos(win.settings_desktop_theme_combo)
          == (settings_row("desktop_theme"), 1),
          str(spos(win.settings_desktop_theme_combo)))

    # ---- "Background" colour + "Reset theme" (the white-panes fix) -------
    # The ground used to be whatever the platform painted behind the window:
    # on a light/classic OS theme that was white, under item colours tuned
    # for a dark ground (green file names on white). It is a setting now, and
    # it is applied under EVERY desktop-theme variant — including on this
    # runner, whose variant is whatever the OS reports.
    check("background swatch at its named row",
          spos(win.settings_btn_color_background)
          == (settings_row("color_background"), 1),
          str(spos(win.settings_btn_color_background)))
    check("background sits directly above the up-directory row",
          settings_row("color_background") + 1
          == settings_row("color_up_directory"),
          f'{settings_row("color_background")} vs {settings_row("color_up_directory")}')
    check("reset-theme button at its named row",
          spos(win.settings_btn_reset_theme)
          == (settings_row("reset_theme"), 1),
          str(spos(win.settings_btn_reset_theme)))
    check("default background is the dark ground",
          win.img_color_background.name().lower() == "#0d0d20",
          win.img_color_background.name())
    check("background swatch shows it",
          "#0d0d20" in win.settings_btn_color_background.styleSheet().lower(),
          win.settings_btn_color_background.styleSheet())
    # The ground reaches BOTH surfaces: the explorer viewports (app-wide QSS,
    # which is what the two Remote Explorer panes read) and the window fill.
    check("explorer viewports carry the ground",
          "rgba(13, 13, 32, 216)" in QApplication.instance().styleSheet(),
          QApplication.instance().styleSheet()[-160:])
    check("window fill carries the ground",
          win._bg_widget._bg_color is not None
          and win._bg_widget._bg_color.name().lower() == "#0d0d20",
          str(win._bg_widget._bg_color))
    # A hand-picked colour reaches both surfaces the same way.
    from PySide6.QtGui import QColor
    win.img_color_background = QColor("#123456")
    win._apply_background_color()
    QCoreApplication.processEvents()
    check("picked colour repaints the viewports",
          "rgba(18, 52, 86, 216)" in QApplication.instance().styleSheet(),
          QApplication.instance().styleSheet()[-160:])
    check("picked colour repaints the window fill",
          win._bg_widget._bg_color.name().lower() == "#123456",
          str(win._bg_widget._bg_color))
    # The ground carries a default TEXT colour with it. It was added for the
    # two local explorers that were plain QFileSystemModels (SD Card, NextSync
    # classic) and set no foreground brush - on a light Windows theme the OS
    # palette drew them black, invisible the moment the ground turned dark.
    # Both are painted by ColoredFileSystemModel now (9.7.37 / 9.7.39), but
    # the rule still colours whatever a view leaves unpainted, and it follows
    # the ground, so a light pick flips it back.
    check("a dark ground carries light item text",
          "color: #e8e8e8" in QApplication.instance().styleSheet(),
          QApplication.instance().styleSheet()[-160:])
    win.img_color_background = QColor("#f0f0f0")
    win._apply_background_color()
    QCoreApplication.processEvents()
    check("a light ground flips the item text to black",
          "color: #000000" in QApplication.instance().styleSheet(),
          QApplication.instance().styleSheet()[-160:])
    # "Reset theme": Custom is the mode a pick leaves behind, and it FREEZES
    # the palette — so the button has to leave the mode as well as the colours.
    win._desktop_theme_mode = "custom"
    win.img_color_retro_log = QColor("#ff00ff")
    win.settings_btn_reset_theme.click()
    QCoreApplication.processEvents()
    check("reset restores the default theme mode",
          win._desktop_theme_mode == "automatic", win._desktop_theme_mode)
    check("reset drops the hand-picked ground",
          win.img_color_background.name().lower() in ("#0d0d20", "#ffffff"),
          win.img_color_background.name())
    check("reset restores the phosphor-green retro log",
          win.img_color_retro_log.name().lower() == "#78ff8c",
          win.img_color_retro_log.name())
    check("reset persists the theme mode",
          "desktop_theme=automatic" in cfg_lines(),
          str([l for l in cfg_lines() if l.startswith("desktop_theme")]))
    # Also proves the key reached CONFIG_FILE_SETTINGS: a colour that is not
    # in that tuple is simply never written, and looks fine until a restart.
    check("reset persists the ground to cfg",
          f"color_background={win.img_color_background.name().lower()}"
          in cfg_lines(),
          str([l for l in cfg_lines() if l.startswith("color_background")]))
    # THE regression this row exists for: the White/light variant used to skip
    # the view rule altogether, leaving the explorer viewports on the platform's
    # stock white under item colours tuned for a dark ground - green file names
    # on white. Selecting it must now keep the ground.
    for _i in range(win.settings_desktop_theme_combo.count()):
        if win.settings_desktop_theme_combo.itemData(_i) == "white":
            win.settings_desktop_theme_combo.setCurrentIndex(_i)
            break
    QCoreApplication.processEvents()
    check("White theme still grounds the explorer viewports",
          "rgba(13, 13, 32, 216)" in QApplication.instance().styleSheet(),
          QApplication.instance().styleSheet()[-160:])
    check("White theme still fills the window",
          win._bg_widget._bg_color is not None
          and win._bg_widget._bg_color.name().lower() == "#0d0d20",
          str(win._bg_widget._bg_color))
    check("White theme still keeps the local explorers readable",
          "color: #e8e8e8" in QApplication.instance().styleSheet(),
          QApplication.instance().styleSheet()[-160:])
    check("cfg 'false' restored as unchecked", not cb.isChecked())
    cb.setChecked(True)
    QCoreApplication.processEvents()
    check("toggle persists to cfg", "zxnu_update_check=true" in cfg_lines(),
          str([l for l in cfg_lines() if l.startswith("zxnu_update_check")]))

    # Recycle Bin deletes toggle: sits right under the no-prompt checkbox.
    rb = win.settings_delete_to_recycle_bin_checkbox
    check("recycle toggle at its named row",
          spos(rb) == (settings_row("delete_to_recycle_bin"), 0),
          str(spos(rb)))
    check("no-prompt checkbox directly above it",
          spos(win.settings_no_prompt_on_deletion_checkbox)
          == (settings_row("no_prompt_on_deletion"), 0)
          and settings_row("no_prompt_on_deletion") + 1
          == settings_row("delete_to_recycle_bin"),
          str(spos(win.settings_no_prompt_on_deletion_checkbox)))
    # The RE-autostart toggle sits directly above the send-conflict row —
    # both placed by name through the SETTINGS_TAB_ROWS registrar.
    ac = win.settings_re_autostart_checkbox
    check("RE-autostart toggle at its named row",
          spos(ac) == (settings_row("re_autostart"), 0),
          str(spos(ac)))
    check("send-conflict combo directly under it",
          spos(win.settings_nextsync_send_conflict_combo)
          == (settings_row("nextsync_send_conflict"), 1)
          and settings_row("re_autostart") + 1
          == settings_row("nextsync_send_conflict"),
          str(spos(win.settings_nextsync_send_conflict_combo)))
    # Verify-after-put toggle (9.7.3): the row right under the send-conflict
    # combo, persisted both ways, and the red log line it uses.
    vc = win.settings_nextsync_verify_crc_checkbox
    check("verify-CRC toggle directly under the send-conflict row",
          spos(vc) == (settings_row("nextsync_verify_crc"), 0)
          and settings_row("nextsync_send_conflict") + 1 == settings_row("nextsync_verify_crc"),
          str(spos(vc)))
    vc.setChecked(False)
    QApplication.processEvents()
    check("verify-CRC toggle persists off to cfg", "nextsync_verify_crc=false" in cfg_lines(),
          str([l for l in cfg_lines() if l.startswith("nextsync_verify")]))
    vc.setChecked(True)
    QApplication.processEvents()
    check("verify-CRC toggle persists on to cfg", "nextsync_verify_crc=true" in cfg_lines())
    # NextSync Sessions (9.7.20): the row right under Verify CRC, persisted
    # both ways, and the worker hook that reads it per dial.
    ss = win.settings_nextsync_sessions_checkbox
    check("Sessions toggle directly under the verify-CRC row",
          spos(ss) == (settings_row("nextsync_sessions"), 0)
          and settings_row("nextsync_verify_crc") + 1 == settings_row("nextsync_sessions"),
          str(spos(ss)))
    ss.setChecked(False)
    QApplication.processEvents()
    check("Sessions toggle persists off to cfg", "nextsync_sessions=false" in cfg_lines(),
          str([l for l in cfg_lines() if l.startswith("nextsync_sessions")]))
    # Through the cfg file, the way the worker's hook will read it on the next
    # launch (zxnu_main is a runpy copy here - its globals are not reachable).
    _ss_val = next((l.split("=", 1)[1] for l in cfg_lines()
                    if l.startswith("nextsync_sessions=")), None)
    check("Sessions toggle off reads as single-seat through the shared decoder",
          _ss_val is not None and not sys.modules["zxnu_config"]
          .nextsync_sessions_enabled({"nextsync_sessions": _ss_val}),
          repr(_ss_val))
    ss.setChecked(True)
    QApplication.processEvents()
    check("Sessions toggle persists on to cfg", "nextsync_sessions=true" in cfg_lines())
    _red = sys.modules["zxnu_config"].FONT_RED
    win.add_nextsync_log_window("crc pin", color=_red)
    _it = win.nextsync_log.item(0)
    check("NextSync log line takes a colour", _it.text() == "crc pin"
          and _it.foreground().color().name() == "#ff0000",
          f"{_it.text()!r} {_it.foreground().color().name()}")
    check("RE-autostart default off", not ac.isChecked())
    # Ticking it WITHOUT a sync root must refuse: the box reverts to off
    # (with a toast advising to set one) and nothing lands in the cfg.
    ac.setChecked(True)
    QCoreApplication.processEvents()
    check("RE-autostart tick without a sync root reverts to off",
          not ac.isChecked())
    check("...and is not persisted",
          not any(l.startswith("nextsync_re_autostart=true")
                  for l in cfg_lines()),
          str([l for l in cfg_lines()
               if l.startswith("nextsync_re_autostart")]))
    # With a sync root on record the tick sticks and persists.
    win._re_sync_root = os.path.dirname(CFG)
    ac.setChecked(True)
    QCoreApplication.processEvents()
    check("RE-autostart tick with a sync root sticks", ac.isChecked())
    check("...and persists to cfg",
          "nextsync_re_autostart=true" in cfg_lines(),
          str([l for l in cfg_lines()
               if l.startswith("nextsync_re_autostart")]))
    win._re_sync_root = ""
    ac.setChecked(False)
    QCoreApplication.processEvents()
    check("unticking persists off", "nextsync_re_autostart=false" in cfg_lines(),
          str([l for l in cfg_lines()
               if l.startswith("nextsync_re_autostart")]))

    if rb.isEnabled():
        check("cfg 'false' restored as unchecked (recycle)", not rb.isChecked())
        rb.setChecked(True)
        QCoreApplication.processEvents()
        check("recycle toggle persists to cfg",
              "delete_to_recycle_bin=true" in cfg_lines(),
              str([l for l in cfg_lines() if l.startswith("delete_to_recycle_bin")]))
    else:
        print("NOTE: Send2Trash not installed — recycle restore/persist checks skipped")

    # The advisory fires ~1.2s after startup; the sweep timer closes it and
    # records its title. The bundled dotN version is read from the app's own
    # zxnu_config module (imported by runpy — safe to touch AFTER launch).
    dotv = sys.modules["zxnu_config"].ZX_NEXT_UNITE_DOTN_VERSION
    ok = wait_until(lambda: any(".sync5" in t for t in seen), timeout=15,
                    what=".sync5 advisory popup")
    check("dotN advisory popup shown", ok, str(seen))
    ok2 = wait_until(lambda: f"dotn_last_version={dotv}" in cfg_lines(),
                     timeout=10, what="dotn_last_version bumped in cfg")
    check("dotn_last_version bumped in cfg", ok2,
          str([l for l in cfg_lines() if l.startswith("dotn_last_version")]))
    check("advisory logged", recent_log(win, ".sync5 dot command updated", n=20))
    timer.stop()
    app.quit()

def inspect_phase7():
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return
    seen = []
    timer = _arm_msgbox_autoclose(seen)
    check("update-check toggle defaults ON (no cfg key)",
          win.settings_zxnu_update_check_checkbox.isChecked())
    check("verify-CRC toggle defaults ON (no cfg key)",
          win.settings_nextsync_verify_crc_checkbox.isChecked())
    check("NextSync Sessions toggle defaults ON (no cfg key)",
          win.settings_nextsync_sessions_checkbox.isChecked())
    check(".sync5 image auto-deploy toggle defaults ON (no cfg key)",
          win.settings_sync5_img_autodeploy_checkbox.isChecked())
    if win.settings_delete_to_recycle_bin_checkbox.isEnabled():
        check("recycle toggle defaults ON (no cfg key)",
              win.settings_delete_to_recycle_bin_checkbox.isChecked())
    dotv = sys.modules["zxnu_config"].ZX_NEXT_UNITE_DOTN_VERSION
    ok = wait_until(lambda: f"dotn_last_version={dotv}" in cfg_lines(),
                    timeout=15, what="first-run silent dotN persist")
    check("first run persists dotN version silently", ok,
          str([l for l in cfg_lines() if l.startswith("dotn_last_version")]))
    check("no advisory popup on first run",
          not any(".sync5" in t for t in seen), str(seen))
    timer.stop()
    app.quit()   # well before the 3.4s-delayed release check could fire

def inspect_phase8():
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return
    # Wait out the async emulator scan first: a CSpect adopted from
    # downloads/cspect is one of the pulse's start sites.
    wait_until(lambda: not getattr(win, "_emulator_scan_pending", False),
               what="emulator scan settled")
    emulator = (getattr(win, "_cspect_executable_path", None) is not None
                or win._mame_usable())
    if not emulator:
        # No emulator on this machine: the hint must stay dark. (The pulse's
        # start/stop transitions need an emulator, so they are only covered
        # on machines that have one — same spirit as the hdfmonkey skips.)
        check("no emulator: hint pulse stays off",
              getattr(win, "_load_image_hint_anim_timer", None) is None
              and win.selectimage.styleSheet() == "")
        app.quit(); return

    ok = wait_until(lambda: getattr(win, "_load_image_hint_anim_timer", None)
                    is not None, timeout=10, what="hint pulse running")
    check("emulator + no image: hint pulse running", ok)
    ok2 = wait_until(lambda: "241,196,15" in win.selectimage.styleSheet()
                     and "241,196,15" in win.downloadimage.styleSheet(),
                     timeout=5, what="amber styling on both image buttons")
    check("pulse paints both image-picking buttons amber", ok2,
          f"sel={win.selectimage.styleSheet()!r}")

    # Loading an image must stop the pulse and restore the buttons' look.
    win.imageinput.setCurrentText(HDF)
    win.imageinput.lineEdit().returnPressed.emit()
    ok3 = wait_until(lambda: win.diskimageexplorerpathinput.text() == "/",
                     what="image load -> path box '/'")
    check("test HDF loaded", ok3, win.diskimageexplorerpathinput.text())
    ok4 = wait_until(lambda: getattr(win, "_load_image_hint_anim_timer", None)
                     is None and win.selectimage.styleSheet() == ""
                     and win.downloadimage.styleSheet() == "",
                     timeout=5, what="hint pulse stopped after load")
    check("loaded image stops the pulse and restores the look", ok4,
          f"timer={getattr(win, '_load_image_hint_anim_timer', None)} "
          f"sel={win.selectimage.styleSheet()!r} "
          f"dl={win.downloadimage.styleSheet()!r}")
    app.quit()

def inspect_phase9():
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return
    check("saved language restored in the combo",
          win.settings_ui_language_combo.currentData() == "es",
          win.settings_ui_language_combo.currentData())
    # The picker must live in the visible 0/1 column band (a column-2+ cell
    # sits outside the pane width unless the window is enlarged — the bug
    # this check pins down).
    _lay = win.settings_ui_language_combo.parentWidget().layout()
    _pos = _lay.getItemPosition(_lay.indexOf(win.settings_ui_language_combo))[:2]
    check("language combo on its own visible row",
          _pos == (settings_row("ui_language"), 1),
          str(_pos))
    check("button translated at startup",
          win.selectimage.text() == "Seleccionar imagen de disco NextZXOS",
          win.selectimage.text())
    check("checkbox translated at startup",
          win.settings_no_prompt_on_deletion_checkbox.text()
          == "No pedir confirmación al eliminar.",
          win.settings_no_prompt_on_deletion_checkbox.text())
    from zxnu_i18n import CATALOGS   # late: the settings_row rule
    check("verify-CRC checkbox translated at startup",
          win.settings_nextsync_verify_crc_checkbox.text()
          == CATALOGS["es"]["NextSync — Verify CRC of every file sent to the Next (Remote Explorer)"],
          win.settings_nextsync_verify_crc_checkbox.text())
    check("NextSync Sessions checkbox translated at startup",
          win.settings_nextsync_sessions_checkbox.text()
          == CATALOGS["es"]["NextSync — Sessions: seat several Nexts at once (Remote Explorer)"],
          win.settings_nextsync_sessions_checkbox.text())
    check("placeholder translated at startup",
          win.filtertext.placeholderText() == "Filtrar por nombre…",
          win.filtertext.placeholderText())
    check("tab titles untouched (dispatch keys)",
          any(win._tab_widget.tabText(i).startswith("Settings")
              for i in range(win._tab_widget.count())))
    # Live switch back to English via the Settings combo.
    win.settings_ui_language_combo.setCurrentIndex(
        win.settings_ui_language_combo.findData("en"))
    QCoreApplication.processEvents()
    check("live switch restores English",
          win.selectimage.text() == "Select NextZXOS disk Image",
          win.selectimage.text())
    check("live switch restores placeholders",
          win.filtertext.placeholderText() == "Filter by name...",
          win.filtertext.placeholderText())
    ok2 = wait_until(lambda: "ui_language=en" in cfg_lines(),
                     timeout=10, what="ui_language persisted")
    check("language change persisted", ok2,
          str([l for l in cfg_lines() if l.startswith("ui_language")]))
    app.quit()

def inspect_phase10():
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return
    check("OS language adopted in the combo",
          win.settings_ui_language_combo.currentData() == "es",
          win.settings_ui_language_combo.currentData())
    check("UI translated on first run",
          win.selectimage.text() == "Seleccionar imagen de disco NextZXOS",
          win.selectimage.text())
    check("adoption persisted once", "ui_language=es" in cfg_lines(),
          str([l for l in cfg_lines() if l.startswith("ui_language")]))
    check("adoption logged", recent_log(win, "UI language set to 'es'", n=30))

    def find_toast():
        # Several toasts can be up at once (emulator detection is bottom-right
        # and English); the language advisory is the bottom-left one.
        for w in QApplication.instance().topLevelWidgets():
            try:
                if (w.objectName() == "zxnu_toast" and w.isVisible()
                        and w.property("zxnu_toast_corner") == "bottom-left"):
                    return w
            except RuntimeError:
                pass
        return None
    ok_toast = wait_until(lambda: find_toast() is not None, timeout=15,
                          what="language advisory toast")
    check("advisory toast shown", ok_toast)
    t = find_toast()
    if t is not None:
        check("toast in the BOTTOM-LEFT corner",
              t.x() < win.frameGeometry().center().x()
              and t.geometry().bottom() > win.frameGeometry().center().y(),
              f"toast={t.geometry()} win={win.frameGeometry()}")
        check("toast is in Spanish",
              any("Idioma ajustado a tu sistema" in c.text()
                  for c in t.findChildren(QLabel)),
              str([c.text() for c in t.findChildren(QLabel)]))
    app.quit()

def inspect_phase11():
    """The NextSync Remote Explorer must render with pygame absent.

    pygame drives the optional retro LOG, not the file manager, but both live
    in the same QStackedWidget behind the same NextSync tab — so a pygame
    import escaping into the Remote Explorer path would leave a user without
    pygame-ce staring at an empty tab. Every phase here runs with pygame
    blocked (see _NoPygame), so simply exercising the view proves it."""
    from PySide6.QtWidgets import QTreeView
    # Imported HERE, not at module scope: importing zxnu_* before runpy runs
    # the app would cache them with the wrong argv[0]-derived cfg path (see
    # find_hdfmonkey). By now the app has imported them itself.
    from zxnu_workers import CompactButton
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit()
        return

    # Guard the premise: if pygame were importable here the phase proves
    # nothing, so assert the block is actually in force.
    pygame_blocked = False
    try:
        import pygame            # noqa: F401
    except Exception:
        pygame_blocked = True
    check("premise: pygame really is unavailable in this phase", pygame_blocked)

    # Bring the NextSync tab to the front: children of a non-current tab are
    # never isVisible(), so every visibility check below would be meaningless.
    from zxnu_config import (TRANSFER_SUBTAB_CLASSIC, TRANSFER_SUBTAB_REMOTE,
                             ZX_NEXT_UNITE_TAB_TITLE_TRANSFER)
    main_tabs = win._bg_widget.tab
    # Match by PREFIX, never by equality: badges and spinners rewrite tabText
    # (the same rule test_startup_tab_activation pins for the startup path).
    idx = next((i for i in range(main_tabs.count())
                if main_tabs.tabText(i).startswith(
                    ZX_NEXT_UNITE_TAB_TITLE_TRANSFER)), None)
    check("Transfer tools tab present", idx is not None,
          str([main_tabs.tabText(i) for i in range(main_tabs.count())]))
    if idx is None:
        app.quit()
        return
    main_tabs.setCurrentIndex(idx)
    check("the app window is shown", wait_until(win.isVisible, 20, "window shown"))

    tabs = getattr(win, "nextsync_mode_tabs", None)
    # Guarded: an AttributeError here would be raised inside a QTimer slot,
    # where Qt prints the traceback and swallows it - exec() never returns
    # and the phase HANGS to the runner's timeout instead of failing.
    check("the Transfer tools sub-tab bar exists", tabs is not None)
    if tabs is None:
        app.quit()
        return
    check("...carrying SD Card Utility, Remote Explorer and Classic sync",
          tabs.count() == 3, str(tabs.count()))
    # The cfg pre-selected the Remote Explorer, but drive it explicitly so the
    # phase does not depend on the restore having happened yet.
    tabs.setCurrentIndex(TRANSFER_SUBTAB_REMOTE)
    ok = wait_until(lambda: getattr(win, "_re_widget", None) is not None, 20,
                    "Remote Explorer widget built")
    check("Remote Explorer widget is built without pygame", ok)
    re_widget = getattr(win, "_re_widget", None)

    # The explorers | log splitter (9.7.21). The mini log used to be pinned
    # at setFixedHeight(110) with no way to make it bigger; it is the bottom
    # half of a Qt.Vertical splitter now, restored from the cfg on build
    # (this container is created lazily, long after load_configuration_file's
    # own splitter loop has run) and saved on every drag.
    from PySide6.QtWidgets import QSplitter as _QSplitter
    from PySide6.QtCore import Qt as _Qt
    _log_split = getattr(win, "_re_log_splitter", None)
    check("the Remote Explorer has an explorers/log splitter",
          isinstance(_log_split, _QSplitter))
    if isinstance(_log_split, _QSplitter):
        check("...running top/bottom, so its handle is horizontal",
              _log_split.orientation() == _Qt.Vertical,
              str(_log_split.orientation()))
        check("...holding the dual-pane widget over the log stack",
              _log_split.count() == 2
              and _log_split.widget(0) is re_widget
              and _log_split.widget(1) is getattr(win, "_re_mini_stack", None))
        # Qt SCALES restored sizes to the widget's real height, so the
        # exact pair is not the contract: what the saved value buys is the
        # LOG keeping the height it was saved with (stretchFactor(1, 0)
        # gives new height to the panes above instead).
        check("...with the saved log height restored from the cfg",
              _log_split.sizes()[1] == 250 and _log_split.sizes()[0] > 0,
              str(_log_split.sizes()))
        check("...and neither half collapsible to nothing",
              not _log_split.childrenCollapsible())
        # The log must be RESIZABLE now: a fixed height would pin it whatever
        # the splitter says. (Qt reports no maximum as QWIDGETSIZE_MAX.)
        _stack = getattr(win, "_re_mini_stack", None)
        check("...and the log stack is no longer a fixed height",
              _stack is not None
              and _stack.minimumHeight() != _stack.maximumHeight(),
              f"{_stack.minimumHeight()}..{_stack.maximumHeight()}"
              if _stack is not None else "no stack")
        # The handle's tooltip must be able to FOLLOW a language switch,
        # in both directions. It only can if its English source was cached,
        # which means it must have been set in English and translated by a
        # walk - set through ui_tr_now on this lazily built container it
        # would be cached as its own source and frozen for the session
        # (found in review, 9.7.21). Drive the real walk to prove it.
        from zxnu_i18n import CATALOGS as _CATS, translate_widget_tree as _tw
        _tip_en = "Drag to resize the file explorers / log window split."
        _handle = _log_split.handle(1)
        check("the splitter handle carries the shared drag tooltip",
              _handle is not None and _handle.toolTip() == _tip_en,
              _handle.toolTip() if _handle is not None else "no handle")
        _tw(win._re_container, "es")
        check("...which follows a switch to Spanish",
              _handle.toolTip() == _CATS["es"][_tip_en], _handle.toolTip())
        _tw(win._re_container, "en")
        check("...and comes back to English",
              _handle.toolTip() == _tip_en, _handle.toolTip())
        # That round trip runs in an ENGLISH session, where ui_tr_now is the
        # identity - so it would pass even with the bug it guards against.
        # What makes the freeze impossible is the tooltip being set from a
        # bare literal and translated by a walk over the whole lazily built
        # CONTAINER, so pin both at the source.
        _pane_src = open(os.path.join(REPO, "zxnu_nextsync_pane.py"),
                         encoding="utf-8").read()
        check("the handle tooltip is set in English, never pre-translated",
              'setToolTip(_re_log_tip)' in _pane_src
              and 'setToolTip(ui_tr_now(' not in _pane_src)
        check("...and the lazily built container is what gets walked",
              "translate_widget_tree(container, current_ui_language())"
              in _pane_src)
        # A corrupt saved size must not break the view: past a C int Qt's
        # setSizes raises OverflowError, an ArithmeticError that the
        # TypeError/ValueError clause would let escape the lazy build.
        check("a corrupt saved size is bounded before Qt sees it",
              "SPLITTER_MAX_PANE_PX" in _pane_src
              and "except (TypeError, ValueError, OverflowError):" in _pane_src)
        # The local/Next split beside it takes the same value from the
        # same file and was the one restore left unbounded (9.7.33). Two
        # separate things guard it and the tripwire must name both: the
        # BOUND in _parse_splitter_sizes, which is what keeps a corrupt
        # value away from Qt, and a try around the setSizes CALL, which
        # is the only place OverflowError can actually be raised.
        # Pinning the clause alone was the first cut and it pinned dead
        # code - nothing in that parse can overflow, because Python ints
        # are arbitrary precision.
        _rex_src = open(os.path.join(REPO, "zxnu_remote_explorer.py"),
                        encoding="utf-8").read()
        check("the local/Next split is bounded before Qt sees it",
              "SPLITTER_MAX_PANE_PX" in _rex_src)
        check("...and the setSizes call itself catches OverflowError",
              "self.hsplitter.setSizes(_sizes)" in _rex_src
              and _rex_src.split("self.hsplitter.setSizes(_sizes)")[0]
              .rstrip().endswith("try:"),
              "the setSizes call is not inside a try")

        # A drag must reach the cfg. splitterMoved only fires for real
        # drags, so emit it the way a drag does after moving the panes -
        # and read back the sizes Qt actually settled on, not the ones
        # asked for. The write itself is behind the shared 300 ms flush.
        _log_split.setSizes([360, 320])
        QApplication.processEvents()
        _want = "nextsync_re_log_splitter_sizes=" + ",".join(
            str(_s) for _s in _log_split.sizes())
        _log_split.splitterMoved.emit(360, 1)
        check("a drag persists the new split to hdfg.cfg",
              wait_until(lambda: _want in cfg_lines(), 5,
                         "the splitter flush to reach the cfg"),
              _want + " | " + str([ln for ln in cfg_lines()
                                   if ln.startswith("nextsync_re_log_splitter")]))
    if re_widget is None:
        app.quit()
        return

    # 9.7.2: the local | Next split is a horizontal splitter whose one
    # handle sits between the local pane and the arrows + Next pane; a drag
    # persists "left,right" to the cfg (restored on the next build).
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QSplitter as _QSplitter
    _hs = getattr(re_widget, "hsplitter", None)
    check("Remote Explorer local | Next splitter exists", isinstance(_hs, _QSplitter))
    if _hs is not None:
        check("Remote Explorer split is horizontal with two sides",
              _hs.orientation() == _Qt.Horizontal and _hs.count() == 2)
        check("Remote Explorer split: local pane left, arrows + Next pane right",
              _hs.widget(0).isAncestorOf(re_widget.local_view)
              and _hs.widget(1).isAncestorOf(re_widget.next_view)
              and _hs.widget(1).isAncestorOf(re_widget.btn_to_next))
        from PySide6.QtWidgets import QSizePolicy as _QSizePolicy
        wait_until(lambda: sum(_hs.sizes()) > 0 and _hs.handle(1).width() > 0, 10, "Remote Explorer split laid out")
        _sz = _hs.sizes(); _ratio = (_sz[0] / float(sum(_sz))) if sum(_sz) else 0
        check("Remote Explorer split restores its saved position on startup", abs(_ratio - 520 / 1200.0) < 0.08, str(_sz))
        check("Remote Explorer split handle is a thin bar, not a square", 1 <= _hs.handle(1).width() <= 12, str(_hs.handle(1).width()))
        # The status label's text grows on connect; it must not set the
        # Next pane's floor (that is what stopped the handle when connected).
        check("Next status label does not dictate the pane's minimum width",
              re_widget.next_path_label.sizePolicy().horizontalPolicy() == _QSizePolicy.Ignored)
        _hs.splitterMoved.emit(200, 1)
        check("dragging the Remote Explorer split persists it to the cfg",
              wait_until(lambda: any(l.startswith("nextsync_re_splitter_sizes=") for l in cfg_lines()),
                         3, "the debounced splitter save"),
              str([l for l in cfg_lines() if "splitter" in l]))
    # Del is the right-click "Delete" on BOTH panes: each handler prompts
    # before touching anything, so here they are stubbed to prove the key
    # ROUTES to them (the prompts themselves are covered by phase 5's
    # confirm-dialog pins on the SD Card tree).
    _calls = []
    re_widget._local_delete_selected = lambda: _calls.append("local")
    re_widget._delete_selected = lambda: _calls.append("next")
    _press_delete(re_widget.local_view)
    _was_connected = re_widget._connected
    re_widget._connected = True
    try:
        _press_delete(re_widget.next_view)
    finally:
        re_widget._connected = _was_connected
    check("Del on the Remote Explorer's local pane reaches its delete-with-prompt", "local" in _calls, str(_calls))
    check("Del on the Remote Explorer's Next pane reaches its delete-with-prompt", "next" in _calls, str(_calls))

    # Hovering a greyed emulator tab on THIS strip reaches the same host
    # re-check as the SD Card tab's (9.7.2), through the widget's hook.
    from PySide6.QtCore import QPointF as _QPointF
    from PySide6.QtGui import QEnterEvent as _QEnterEvent
    _rechecks = []
    _saved_recheck = win._recheck_emulator_launchability
    win._recheck_emulator_launchability = lambda n=None: _rechecks.append(n)
    _saved_launchers = re_widget._emulator_launchers
    re_widget._emulator_launchers = lambda: [("CSpect", lambda: None, "busy")]
    re_widget.refresh_emulator_strip()
    _tab = re_widget._emulator_tabs[0]
    QApplication.sendEvent(_tab, _QEnterEvent(_QPointF(3, 3), _QPointF(3, 3), _QPointF(3, 3)))
    QApplication.processEvents()
    check("Remote Explorer: hovering a greyed emulator tab re-checks it through the host",
          _rechecks == ["CSpect"], str(_rechecks))
    win._recheck_emulator_launchability = _saved_recheck
    re_widget._emulator_launchers = _saved_launchers
    re_widget.refresh_emulator_strip()

    # A toast can carry an offer (9.7.2): a second button that closes the
    # toast and then runs the callback from the event loop.
    from PySide6.QtWidgets import QPushButton as _QPushButton
    _hits = []
    win._show_toast("⚠  Update available for this Next", "body", variant="yellow",
                    duration_ms=5000, action=("Update now", lambda: _hits.append(1)))
    QApplication.processEvents()
    _toast = win._live_toasts[-1] if getattr(win, "_live_toasts", None) else None
    _btns = {b.text(): b for b in _toast.findChildren(_QPushButton)} if _toast is not None else {}
    check("the offer toast shows an Update now button beside Cancel (the refusal)",
          set(_btns) >= {"Update now", "Cancel"} and "OK" not in _btns, str(sorted(_btns)))
    if "Update now" in _btns:
        _btns["Update now"].click()
        wait_until(lambda: _hits == [1], 3, "the toast's accept callback")
    check("clicking it runs the accept callback", _hits == [1], str(_hits))

    # The stack page is the CONTAINER (explorer + mini log) since the RE
    # view grew its own log strip; the explorer widget lives inside it -
    # one level deeper since 9.7.21, which put the two on either side of a
    # splitter, so the test walks the ancestry rather than naming a parent.
    def _inside(child, ancestor):
        while child is not None:
            if child is ancestor:
                return True
            child = child.parent()
        return False
    check("the Remote Explorer container is the visible page of the log stack",
          win.nextsync_log_stack.currentWidget() is win._re_container
          and _inside(re_widget, win._re_container),
          str(win.nextsync_log_stack.currentWidget()))
    check("the Remote Explorer is actually visible", re_widget.isVisible())
    check("the mini log is built and visible below the panes",
          win._re_mini_log is not None and win._re_mini_log.isVisible())

    # Both file panes: the local tree and the Next tree.
    trees = re_widget.findChildren(QTreeView)
    check("both explorer panes rendered", len(trees) >= 2, f"{len(trees)} trees")
    check("both explorer panes are visible and have a width",
          all(t.isVisible() and t.width() > 0 for t in trees),
          str([(t.isVisible(), t.width()) for t in trees]))

    # The navigation buttons (Up / Refresh / + Drive) and the transfer arrows,
    # plus the sync-root box's history clear button (9.7.22) - a
    # CompactButton for the same reason as the rest, and this exhaustive
    # list is its guard here: a relapse to a plain QPushButton drops it.
    labels = sorted(b.text() for b in re_widget.findChildren(CompactButton))
    check("both navigation bars' buttons rendered",
          labels == ["+ Drive", "Disconnect", "Refresh", "Refresh",
                     "Up", "Up", "✕"], str(labels))
    # Both panes carry a name filter (9.7.33). This is the only place the
    # widget is built inside the REAL app, so it is the only place the
    # lazy container's translate_widget_tree walk runs over it and the
    # only place the bar has a real width - a box that was built but
    # never added to a layout, or squeezed to nothing by the status
    # label's stretch, passes every headless check and fails on screen.
    check("each explorer bar carries its own name filter",
          re_widget.next_filter_edit.isVisible()
          and re_widget.local_filter_edit.isVisible()
          and re_widget.next_filter_edit
          is not re_widget.local_filter_edit,
          "next=%d local=%d" % (re_widget.next_filter_edit.width(),
                                re_widget.local_filter_edit.width()))
    # PLACEMENT, not just existence. width() > 0 was the first cut and it
    # could not fail for either mode this comment names: the box is parented
    # to the widget 45 lines before it is added to the bar, so DELETING the
    # addWidget leaves an orphan at its unmanaged 100x30 default - visible,
    # non-zero, and painted under the local pane where nobody can see it.
    _bar = None
    for _b in re_widget.findChildren(QHBoxLayout):
        if any(_b.itemAt(_i).widget() is re_widget.next_filter_edit
               for _i in range(_b.count())):
            _bar = _b
            break
    check("the Next filter is really IN the Next bar, at its end",
          _bar is not None
          and _bar.itemAt(_bar.count() - 1).widget()
          is re_widget.next_filter_edit
          and any(_bar.itemAt(_i).widget() is re_widget.next_path_label
                  for _i in range(_bar.count())),
          "no bar" if _bar is None else "last=%r" % (
              _bar.itemAt(_bar.count() - 1).widget(),))
    # ...and a REAL width at a known window size. At the harness's default
    # 900px both stretch 0 and stretch 1 floor the box at ~31px, so the
    # resize is what makes this discriminate at all; it is undone straight
    # after so the rest of the phase sees the geometry it expects.
    #
    # BOTH floors are deliberately low. This bar carries Up, Refresh, the
    # machine combo, the rename button, Disconnect, the drive list and
    # + Drive before either of these two gets a pixel, so at a 1920 window
    # with the default split they measure ~53 and ~105. The check is here
    # to catch one of them being STARVED TO NOTHING by the other - which is
    # what stretch 0 on the box did (label 0 from 1600 down) - not to assert
    # a comfortable layout. Dragging the local/Next splitter is the real
    # recovery and it is persisted: measured at a 1400 pane, moving the
    # handle from 50/50 to 25/75 takes the box 62 -> 187 and the label
    # 123 -> 374.
    _was = win.size()
    win.resize(1920, 1000)
    QApplication.processEvents()
    check("neither the Next filter nor the status label is starved out",
          re_widget.next_filter_edit.width() >= 45
          and re_widget.next_path_label.width() >= 45,
          "box=%d label=%d" % (re_widget.next_filter_edit.width(),
                               re_widget.next_path_label.width()))
    win.resize(_was)
    QApplication.processEvents()
    # Enabled while OFFLINE, unlike every other Next-side control: the
    # machine switch runs _set_connected(False), so a box greyed there
    # would be wiped by the exact event the filter must survive.
    check("the Next filter stays live while nothing is connected",
          re_widget.next_filter_edit.isEnabled())
    # Both bars say the same word, and the label actually RENDERS - the
    # first cut shipped the Next box bare and it was reported at once.
    check("both bars are labelled Filter: , and both labels render",
          re_widget.next_filter_label.text() == "Filter: "
          and re_widget.local_filter_label.text() == "Filter: "
          and re_widget.next_filter_label.isVisible()
          and re_widget.next_filter_label.width() > 0,
          "%r/%d  %r" % (re_widget.next_filter_label.text(),
                         re_widget.next_filter_label.width(),
                         re_widget.local_filter_label.text()))

    # Disconnect (9.5.24) sits in the Next bar between the machine's name
    # and its drive, and is dead until a Next is actually connected.
    check("Disconnect is present but disabled while offline",
          re_widget.btn_disconnect.isVisible()
          and not re_widget.btn_disconnect.isEnabled())
    check("the transfer arrow buttons rendered",
          re_widget.btn_to_next.isVisible() and re_widget.btn_to_local.isVisible())
    check("the Remote Explorer server-control button is shown",
          win.nextsync_re_start_button.isVisible())
    # The retro toggle is the one thing pygame owns: it is hidden in this view
    # (and would be disabled anyway without pygame) — never a crash.
    check("the retro-log toggle is hidden in Remote Explorer mode",
          not win.nextsync_pygame_button.isVisible())

    # Switching back to Classic must not need pygame either.
    tabs.setCurrentIndex(TRANSFER_SUBTAB_CLASSIC)
    QCoreApplication.processEvents()
    check("Classic view falls back to the plain list log without pygame",
          win.nextsync_log_stack.currentWidget() is win.nextsync_log,
          str(win.nextsync_log_stack.currentWidget()))
    check("the retro toggle is back but cannot be armed without pygame",
          win.nextsync_pygame_button.isVisible())
    win.nextsync_pygame_button.setChecked(True)
    QCoreApplication.processEvents()
    check("arming the retro toggle without pygame declines instead of crashing",
          not win.nextsync_pygame_button.isChecked()
          and win.nextsync_log_stack.currentWidget() is win.nextsync_log,
          f"checked={win.nextsync_pygame_button.isChecked()}")

    # And back into the Remote Explorer once more (the widget is now cached).
    tabs.setCurrentIndex(TRANSFER_SUBTAB_REMOTE)
    QCoreApplication.processEvents()
    check("returning to the Remote Explorer still shows it",
          win.nextsync_log_stack.currentWidget() is win._re_container
          and re_widget.isVisible())
    app.quit()


def inspect_phase12():
    # The resting state (no image loaded, or a failed load) must not hold
    # the SD Card lock: it would leave the hover re-check dead (review).
    _rw = find_win()
    check("SD Card controls are not locked in the resting state",
          _rw is not None and not getattr(_rw, "_sdcard_controls_locked", False))
    """With NO disk image loaded, the SD Card tab's LOCAL explorer must stay
    usable.

    The left pane browses the PC: it needs neither an image nor hdfmonkey, and
    its right-click menu carries the "Start <emulator> with <file>" actions,
    which boot a local file with no transfer. It was greyed out all the same,
    because the no-image resting state reuses set_all_buttons_disabled() — the
    blunt lock meant for transfers — so the actions were unreachable until an
    image happened to be loaded.

    The image-side half must STILL be disabled here: with no image there is
    nothing for it to act on, and that is what makes this a targeted fix rather
    than "enable everything"."""
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit()
        return

    # Guard the premise: this phase is only meaningful with no image loaded.
    img = (win.imageinput.currentText() or "").strip().strip('"')
    check("premise: no disk image is loaded", not img or not os.path.isfile(img),
          f"imageinput={img!r}")

    local_widgets = (
        ("local file tree", win.treeview),
        ("local Up button", win.local_explorer_up_button),
        ("local Refresh button", win.local_explorer_refresh_button),
        ("local filter box", win.filtertext),
        ("local filter label", win.filterlabel),
        ("local drive combo", win.zx_next_unite_diskdrive),
    )
    for label, w in local_widgets:
        check(f"the {label} is usable without an image", w.isEnabled())

    # The image picker itself must obviously stay reachable, or no image could
    # ever be loaded.
    check("the image picker is still reachable",
          win.imageinput.isEnabled() and win.selectimage.isEnabled())

    # ...and the image-dependent half stays disabled: this is a targeted fix.
    for label, w in (("image tree", win.image_treeview),
                     ("->image transfer button", win.button_to_image),
                     ("->disk transfer button", win.button_to_disk),
                     ("in-image new-folder button", win.button_new_folder)):
        check(f"the {label} stays disabled with no image", not w.isEnabled())

    # A refusal to launch must be VISIBLE, not just logged. MAME needs an
    # image, and with the local explorer now usable without one, "Start MAME
    # with <file>" is reachable in exactly this state — from the NextSync tab
    # too, whose user never sees the SD Card tab's log window. So the refusal
    # has to toast. Driven through the real launcher, on the real refusal path.
    toasts = []
    real_toast = win._show_toast
    win._show_toast = lambda title, message="", **kw: toasts.append(
        (title, message, kw.get("variant")))
    try:
        launch_mame = getattr(win, "_launch_mame_fn", None)
        check("the MAME launcher is exposed on the window", launch_mame is not None)
        if launch_mame is not None:
            launch_mame()          # no image loaded -> must refuse
            check("refusing to launch MAME raises a toast, not just a log line",
                  len(toasts) == 1, f"{len(toasts)} toast(s)")
            if toasts:
                title, message, variant = toasts[0]
                check("the toast names the emulator", "MAME" in title, title)
                check("the toast body says what to do about it",
                      "image" in message.lower(), message)
                check("the toast is styled as a failure", variant == "red", str(variant))

        # REGRESSION (reported): "Start CSpect with file X" downloaded the file
        # and then nothing happened. launch_cspect wrapped its whole body in a
        # bare `if _right_disk_content():` with no else, so with no image
        # mounted it returned in total silence — no launch, no log, no toast.
        toasts.clear()
        launch_cspect = getattr(win, "_launch_cspect_fn", None)
        check("the CSpect launcher is exposed on the window",
              launch_cspect is not None)
        if launch_cspect is not None:
            launch_cspect()        # no image loaded -> must refuse, not vanish
            check("refusing to launch CSpect is never silent",
                  len(toasts) == 1, f"{len(toasts)} toast(s)")
            if toasts:
                check("the CSpect toast names the emulator",
                      "CSpect" in toasts[0][0], toasts[0][0])
                check("the CSpect toast says an image is needed",
                      "image" in toasts[0][1].lower(), toasts[0][1])

        # And the pre-flight check both emulators share reports the same thing,
        # so the Remote Explorer can ask BEFORE downloading anything.
        blocker = getattr(win, "_emulator_launch_blocker", None)
        check("a shared launch-precondition check is exposed",
              blocker is not None)
        if blocker is not None:
            # Launching the IMAGE needs an image — that is the Launch CSpect /
            # Launch Mame buttons' job, unchanged.
            check("with no image, launching the image itself is blocked (CSpect)",
                  bool(blocker("CSpect")), repr(blocker("CSpect")))
            check("with no image, launching the image itself is blocked (MAME)",
                  bool(blocker("MAME")), repr(blocker("MAME")))
            # Launching a downloaded FILE does not: the Remote Explorer fetches
            # a program off the Next to the local disk and runs it, with no SD
            # image involved. Gating that on an image is what produced the
            # "Could not start CSpect — load a disk image first" toast on a
            # perfectly valid launch.
            check("running a downloaded FILE is never gated on an image (CSpect)",
                  blocker("CSpect", autostart=True) == "",
                  repr(blocker("CSpect", autostart=True)))
            check("running a downloaded FILE is never gated on an image (MAME)",
                  blocker("MAME", autostart=True) == "",
                  repr(blocker("MAME", autostart=True)))
            check("an unknown emulator is not reported as blocked",
                  blocker("Nonesuch") == "")

            # ---- the image is there, but another emulator holds it -------
            # An emulator keeps its .img open for its whole run, so a second
            # one handed the same file dies mounting it. Drive the cache
            # directly (a real holder would need a real emulator) and check
            # every launch surface goes grey with a reason naming the file.
            busy_img = os.path.join(SCRATCH, "phase12-busy.img")
            with open(busy_img, "wb") as fh:
                fh.write(b"\0" * 512)
            win.imageinput.setCurrentText(busy_img)
            win.right_disk_image_path = busy_img
            key = win._image_state_key(busy_img)
            from zxnu_config import IMAGE_WRITE_BUSY, IMAGE_WRITE_OK

            # On Windows take the file FOR REAL, the way MAME takes -hard1
            # (share=READ), and let the app's own probe discover it. That
            # exercises the whole chain - probe, cache, re-gate, tooltip -
            # rather than the UI half with a hand-set verdict. Elsewhere the
            # OS refuses nobody and no probe can see a holder, so the cache
            # is seeded instead and only the UI half is under test.
            holder = None
            if sys.platform == "win32":
                import ctypes
                from ctypes import wintypes
                _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                _k32.CreateFileW.restype = wintypes.HANDLE
                _k32.CreateFileW.argtypes = [
                    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                    wintypes.HANDLE]
                handle = _k32.CreateFileW(busy_img, 0xC0000000, 0x00000001,
                                          None, 3, 0, None)
                if handle != wintypes.HANDLE(-1).value:
                    holder = handle
            if holder is not None:
                win._reprobe_and_regate(busy_img)
                check("a real MAME-style lock is discovered by the probe",
                      win._image_write_state.get(key) == IMAGE_WRITE_BUSY,
                      repr(win._image_write_state.get(key)))
            else:
                win._image_write_state[key] = IMAGE_WRITE_BUSY
                win._refresh_emulator_launchability()

            why = blocker("MAME")
            check("a busy image blocks the MAME launch", bool(why), repr(why))
            # The whole point of naming the file: with several images in the
            # history, "in use" alone does not say WHICH one to swap away from.
            check("the reason names the busy file",
                  os.path.basename(busy_img) in why, repr(why))
            check("a busy image does NOT block running a downloaded file",
                  blocker("MAME", autostart=True) == "")
            check("the greyed MAME button explains itself in its tooltip",
                  win.button_start_mame.toolTip() == why,
                  repr(win.button_start_mame.toolTip()))
            if win._mame_usable():
                check("the greyed MAME button is actually disabled",
                      not win.button_start_mame.isEnabled())

            # The strips are built from the same answer, so a tab there
            # cannot disagree with the button here.
            from zxnu_workers import emulator_launch_entries
            entries = {e.name: e for e in emulator_launch_entries(win)}
            if "Mame" in entries:
                check("the emulator strip carries the same reason",
                      entries["Mame"].blocked == why,
                      repr(entries["Mame"].blocked))

            # And it clears: this is the state the user gets out of by
            # picking another image, or by closing the emulator and re-picking.
            if holder is not None:
                _k32.CloseHandle(holder)
                holder = None
                # Re-probing the SAME path is what re-picking it from the
                # history dropdown does - the "I closed MAME, try again"
                # gesture. It has to be enough on its own.
                win._reprobe_and_regate(busy_img)
                check("releasing the real lock clears the verdict",
                      win._image_write_state.get(key) == IMAGE_WRITE_OK,
                      repr(win._image_write_state.get(key)))
            else:
                win._image_write_state[key] = IMAGE_WRITE_OK
                win._refresh_emulator_launchability()
            check("clearing the verdict un-blocks the launch",
                  blocker("MAME") == "", repr(blocker("MAME")))
            if win._mame_usable():
                check("and the Launch button comes back",
                      win.button_start_mame.isEnabled())

            # An emulator THIS APP LAUNCHED is tracked separately - the only
            # signal that exists on Linux/macOS, where the probe is blind.
            # Holders are PROCESSES, polled for liveness, not a count that an
            # exit handler has to remember to decrement.
            class _Holder:
                def __init__(self):
                    self.gone = False

                def poll(self):
                    return 0 if self.gone else None

            holder_proc = _Holder()
            win._images_held_by_us[key] = [holder_proc]
            check("an emulator we launched ourselves also blocks it",
                  bool(blocker("MAME")), repr(blocker("MAME")))
            # The click-time guard has to see it too. On POSIX the probe is
            # blind, so consulting the probe alone let a second emulator boot
            # the image the first one had mounted.
            check("the launch-time re-check also refuses our own holder",
                  win._reprobe_and_regate(busy_img) is False)
            # A holder that has exited stops counting on its own - no exit
            # handler required, and no window in which a probe can wrongly
            # clear a live one.
            holder_proc.gone = True
            check("a holder that has exited releases the image by itself",
                  blocker("MAME") == "", repr(blocker("MAME")))
            check("and the exited holder is pruned from the record",
                  key not in win._images_held_by_us)

            # The key must survive a non-canonical spelling: the image box is
            # free text ("Type a path directly"), and the launchers abspath
            # what they hand the emulator. Two spellings of one file that key
            # differently are a gate that silently never fires.
            odd = os.path.join(os.path.dirname(busy_img), ".",
                               os.path.basename(busy_img))
            check("one file has one cache key however it is spelled",
                  win._image_state_key(odd) == key,
                  f"{win._image_state_key(odd)!r} != {key!r}")
            check("a blank path never keys to the working directory",
                  win._image_state_key("") == ""
                  and win._image_state_key('""') == "")

            # ---- right-click: pick a writable image instead ------------
            # The way OUT of a greyed-out launch. Two images in the history,
            # one of them held; only the free one may be offered.
            free_img = os.path.join(SCRATCH, "phase12-free.img")
            with open(free_img, "wb") as fh:
                fh.write(b"\0" * 512)
            win.imageinput.clear()
            win.imageinput.addItem(busy_img)
            win.imageinput.addItem(free_img)
            win.imageinput.addItem(os.path.join(SCRATCH, "phase12-gone.img"))
            win.imageinput.setCurrentText(busy_img)
            win.right_disk_image_path = busy_img

            holder2 = None
            if sys.platform == "win32":
                holder2 = _k32.CreateFileW(busy_img, 0xC0000000, 0x00000001,
                                           None, 3, 0, None)
                if holder2 == wintypes.HANDLE(-1).value:
                    holder2 = None
            if holder2 is None:
                win._images_held_by_us[key] = [_Holder()]

            offered = [p for p, _cur in win.writable_image_choices()]
            check("the busy image is not offered as a choice",
                  busy_img not in offered, str(offered))
            check("a writable image IS offered", free_img in offered,
                  str(offered))
            check("an entry whose file has gone is skipped",
                  not any("phase12-gone" in p for p in offered), str(offered))

            # The menu must open from a DISABLED button. Qt drops mouse and
            # context-menu events for disabled widgets inside QWidget.event(),
            # so setContextMenuPolicy(CustomContextMenu) never fires there -
            # which is exactly when this menu is needed. An event filter runs
            # before event(); this is the tripwire against a relapse.
            from PySide6.QtCore import Qt as _Qt
            check("the Launch buttons do not rely on CustomContextMenu",
                  win.button_start_mame.contextMenuPolicy()
                  != _Qt.ContextMenuPolicy.CustomContextMenu,
                  str(win.button_start_mame.contextMenuPolicy()))

            import zxnu_main as _zm
            from PySide6.QtGui import QContextMenuEvent
            seen_menu = []
            _real_menu = _zm.emulator_color_menu

            def _spy(parent, label, current, on_picked, gpos,
                     image_choices=None, on_image_picked=None):
                seen_menu.append((label, list(image_choices or ()),
                                  on_image_picked))

            _zm.emulator_color_menu = _spy
            try:
                _btn = win.button_start_mame
                # Shown-but-disabled is the state under test. The button is
                # HIDDEN outright when MAME is not installed (which is the
                # case on CI), and a hidden button legitimately answers
                # nothing - so show it for the duration rather than skipping
                # the tripwire on every runner without MAME.
                _was_hidden = _btn.isHidden()
                _btn.setVisible(True)
                _btn.setEnabled(False)
                _centre = _btn.mapToGlobal(_btn.rect().center())

                def _right_click():
                    seen_menu.clear()
                    app.sendEvent(_btn, QContextMenuEvent(
                        QContextMenuEvent.Reason.Mouse,
                        _btn.mapFromGlobal(_centre), _centre))
                    return bool(seen_menu)

                check("right-clicking a GREYED-OUT Launch button opens the menu",
                      _right_click(), str(seen_menu))
                if seen_menu:
                    _label, _choices, _picker = seen_menu[0]
                    check("the menu offers the writable image",
                          any(p == free_img for p, _c in _choices),
                          str([p for p, _c in _choices]))
                    check("and knows how to act on the pick", _picker is not None)
                # ... and the guard's own rule: a button the app has HIDDEN
                # (no MAME installed) must not claim the click, because
                # whatever is drawn in its place now owns it.
                _btn.setVisible(False)
                check("a HIDDEN Launch button does not claim the right-click",
                      not _right_click())
            finally:
                _zm.emulator_color_menu = _real_menu
                # Release the lock here, not further down: a check that
                # raises would otherwise leave the handle open and the
                # phase's own cleanup could not delete the file.
                if holder2 is not None:
                    _k32.CloseHandle(holder2)
                    holder2 = None
                _btn.setVisible(not _was_hidden)   # leave the tab as found

            # Picking one makes it the loaded image - the same two steps the
            # history dropdown performs. A runner without hdfmonkey answers
            # that load with the MODAL install prompt, which a headless run
            # can never click away; the app's own once-flag suppresses it.
            win._hdfmonkey_prompt_shown = True
            win.select_emulator_image(free_img)
            check("picking an image puts it in the image box",
                  win._image_state_key(win.imageinput.currentText())
                  == win._image_state_key(free_img),
                  win.imageinput.currentText())

            win._images_held_by_us.pop(key, None)

            win.imageinput.clear()
            win.imageinput.setCurrentText("")
            win.right_disk_image_path = ""
            win._image_write_state.pop(key, None)
            for _stale in (busy_img, free_img):
                try:
                    os.remove(_stale)
                except OSError:
                    pass
    finally:
        win._show_toast = real_toast
    app.quit()


def inspect_phase13():
    """The Download-NextZXOS-Image wizard, fed a local zip through a patched
    urlopen and a renamed save target: the image must be extracted NEXT TO
    the zip NAMED AFTER IT (not at the archive's internal 2gb/... path),
    selected into the image input, and no error box may appear."""
    from PySide6.QtWidgets import (QDialog, QFileDialog, QMessageBox,
                                   QPushButton)
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return

    wait_until(lambda: not getattr(win, "_emulator_scan_pending", False),
               what="emulator scan settled")

    # A runner without hdfmonkey (CI) answers the wizard's automatic load
    # with the MODAL install prompt — a headless run can never click it
    # away, so the phase hung 900 s on its first CI outing. The app's own
    # once-flag suppresses the prompt.
    win._hdfmonkey_prompt_shown = True

    feed = os.path.join(SCRATCH, "wizard-feed.zip")
    save_as = os.path.join(SCRATCH, "my-renamed-download.zip")
    expected_img = os.path.join(SCRATCH, "my-renamed-download.img")
    for stale in (save_as, expected_img,
                  os.path.join(SCRATCH, "2gb", "cspect-next-2gb.img")):
        if os.path.isfile(stale):
            os.remove(stale)

    boxes = []
    def _record(kind):
        def fn(*a, **k):
            boxes.append((kind, a[2] if len(a) > 2 else "?"))
            return QMessageBox.StandardButton.Ok
        return staticmethod(fn)
    QMessageBox.critical = _record("critical")
    QMessageBox.warning = _record("warning")
    QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (save_as, "Zip Archives (*.zip)"))

    class _FeedResponse:
        def __init__(self):
            self._f = open(feed, "rb")
            self._size = os.path.getsize(feed)
        def __enter__(self): return self
        def __exit__(self, *exc): self._f.close(); return False
        def read(self, n=-1): return self._f.read(n)
        def getheader(self, name, default=None):
            return (str(self._size)
                    if name.lower() == "content-length" else default)
    import urllib.request
    urllib.request.urlopen = lambda *a, **k: _FeedResponse()

    def click_download():
        dlg = next((w for w in app.topLevelWidgets()
                    if isinstance(w, QDialog) and w.isVisible()
                    and "Download NextZXOS" in w.windowTitle()), None)
        check("wizard dialog opened", dlg is not None)
        if dlg is None:
            return
        btn = next((b for b in dlg.findChildren(QPushButton)
                    if b.text() == "Download"), None)
        check("wizard has a Download button", btn is not None)
        if btn is not None:
            btn.click()

    QTimer.singleShot(300, click_download)
    win.download_nextzxos_image()

    check("the zip landed at the RENAMED save path", os.path.isfile(save_as))
    check("the image is extracted NEXT TO the zip, NAMED AFTER IT",
          os.path.isfile(expected_img),
          expected_img)
    check("the archive's internal folder path is NOT recreated",
          not os.path.isfile(os.path.join(SCRATCH, "2gb",
                                          "cspect-next-2gb.img")))
    check("the extracted image is selected into the image input",
          win.imageinput.currentText()
          and os.path.normcase(win.imageinput.currentText().strip('"'))
          == os.path.normcase(expected_img),
          win.imageinput.currentText())
    check("no error box appeared", not boxes, str(boxes))
    # The feed image is not a real FAT volume, so the automatic load is
    # allowed to FAIL — the wizard's contract ends at "selected and load
    # attempted". Let the async load settle so it cannot outlive the app.
    settle_end = time.monotonic() + 1.5
    while time.monotonic() < settle_end:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    app.quit()


def inspect_phase14():
    """The image-history dropdown's phantom-activation guard: activating
    the ALREADY-LOADED entry right after the popup opened must not reload
    and must put the popup back up; activating a DIFFERENT entry must
    load it. The Windows phantom itself needs real cursor geometry, so the
    guard is driven directly via the combo's activated signal."""
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return

    wait_until(lambda: not getattr(win, "_emulator_scan_pending", False),
               what="emulator scan settled")

    combo = win.imageinput
    # A runner without hdfmonkey (CI) turns any load into the MODAL
    # install prompt — a headless run can never click it away. The
    # genuine-pick check below DOES load, so suppress the prompt via the
    # app's own once-flag before anything can trigger it.
    win._hdfmonkey_prompt_shown = True
    # No image is really loaded (the history paths don't exist), so mark
    # history entry 0 as the loaded image by hand — taken from the combo
    # itself so the guard's normalized comparison matches on every
    # platform (normalize keeps '/' on POSIX, flips to '\' on Windows).
    win.right_disk_image_path = combo.itemText(0)
    combo.setCurrentIndex(0)
    win.diskimageexplorerpathinput.setText("(sentinel)")

    # Phantom: activation of the loaded entry, popup freshly opened.
    combo._popup_shown_at = time.monotonic()
    combo.activated.emit(0)
    ok = wait_until(lambda: combo.view().isVisible(), timeout=5,
                    what="popup re-shown after phantom")
    check("phantom activation re-opens the dropdown", ok)
    check("phantom activation does not reload",
          win.diskimageexplorerpathinput.text() == "(sentinel)",
          win.diskimageexplorerpathinput.text())
    combo.hidePopup()

    # Same no-op pick long after opening: no reload AND no re-open.
    combo._popup_shown_at = time.monotonic() - 5.0
    combo.activated.emit(0)
    settle_end = time.monotonic() + 0.7
    while time.monotonic() < settle_end:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    check("late no-op pick neither reloads nor re-opens",
          win.diskimageexplorerpathinput.text() == "(sentinel)"
          and not combo.view().isVisible())

    # Genuine pick of a DIFFERENT entry loads it (timing irrelevant).
    combo._popup_shown_at = time.monotonic()
    combo.setCurrentIndex(1)
    combo.activated.emit(1)
    check("picking another entry loads it",
          win.diskimageexplorerpathinput.text() != "(sentinel)",
          win.diskimageexplorerpathinput.text())
    settle_end = time.monotonic() + 1.0
    while time.monotonic() < settle_end:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    app.quit()


def inspect_phase15():
    """Forgetting a remembered image path (9.6.0).

    The reported hole: the history dropdown had no removal at all - the
    reporter's stale "C:\\temp\\cspect-next-2gb.img" could not be got rid of,
    and clearing the line edit + Enter only unloaded the image. This drives
    all three affordances: the '=' button beside the box, DELETE on the
    highlighted dropdown row, and the right-button gesture on a dropdown row
    (which must NOT be read as a pick). Plus the shared plumbing: the
    case-insensitive lookup, the '=' gating, and that a removal reaches
    hdfg.cfg.

    The two dropdown affordances get real synthesised events on purpose.
    Both were shipped broken first: `view.keyPressEvent = ...` is dead code
    on a combo popup (PySide6 only dispatches virtuals to Python attributes
    for objects built FROM Python, and the popup view is made in C++), and
    QComboBoxPrivateContainer selects a row on ANY button release - so a
    right-click LOADED the image it was offering to forget. A test that only
    emits the signals would have called both of those green.
    """
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QKeyEvent, QMouseEvent
    from PySide6.QtWidgets import QMessageBox
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return

    wait_until(lambda: not getattr(win, "_emulator_scan_pending", False),
               what="emulator scan settled")
    # Nothing is loaded here so nothing should reach the modal hdfmonkey
    # install prompt - belt and braces anyway (the phase 2-3 / 14 lesson).
    win._hdfmonkey_prompt_shown = True

    combo = win.imageinput
    check("the cfg's three history entries were restored", combo.count() == 3,
          [combo.itemText(i) for i in range(combo.count())])

    # ---- the button is in the row, right after the path box ----------------
    check("the clear button sits between the path box and 'Select NextZXOS "
          "disk Image'",
          win.horizontal1.indexOf(win.imageclear)
          == win.horizontal1.indexOf(win.imageinput) + 1
          and win.horizontal1.indexOf(win.imageclear)
          < win.horizontal1.indexOf(win.selectimage),
          f"clear={win.horizontal1.indexOf(win.imageclear)} "
          f"input={win.horizontal1.indexOf(win.imageinput)} "
          f"select={win.horizontal1.indexOf(win.selectimage)}")

    # ---- DELETE on the highlighted dropdown row ----------------------------
    # The affordance that was dead: assign-the-virtual never ran, so this
    # drives the real event through the real popup view.
    combo.showPopup()
    view = combo.view()
    view.setCurrentIndex(combo.model().index(1, combo.modelColumn()))
    doomed = combo.itemText(1)
    app.sendEvent(view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                                  Qt.KeyboardModifier.NoModifier))
    check("Delete on a dropdown row forgets that row",
          combo.count() == 2
          and all(combo.itemText(i) != doomed for i in range(combo.count())),
          [combo.itemText(i) for i in range(combo.count())])

    # ---- a RIGHT-click on a dropdown row must not be read as a pick --------
    # The container selects on any release; without the filter this loaded
    # the image the menu was about to offer to forget.
    combo.showPopup()
    view = combo.view()
    picks = []
    combo.activated.connect(picks.append)
    # Inside the settle window on purpose: the menu is refused there, so
    # nothing modal can open while the swallowing itself is under test.
    combo._popup_shown_at = time.monotonic()
    spot = QPointF(view.viewport().rect().center())
    for etype in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        app.sendEvent(view.viewport(), QMouseEvent(
            etype, spot, view.viewport().mapToGlobal(spot.toPoint()).toPointF(),
            Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier))
    combo.activated.disconnect(picks.append)
    check("a right-click on a dropdown row does not activate/load it",
          not picks, picks)
    combo.hidePopup()

    # ---- the button forgets the path that is SHOWN -------------------------
    victim = combo.itemText(0)
    combo.setCurrentText(victim)
    check("the clear button is live while the box names something",
          win.imageclear.isEnabled())
    win.imageclear.click()
    check("the shown entry is gone from the list",
          combo.count() == 1
          and all(combo.itemText(i) != victim for i in range(combo.count())),
          [combo.itemText(i) for i in range(combo.count())])
    check("the box is emptied, not silently swapped for a neighbouring path",
          combo.currentText() == "", combo.currentText())
    check("the removal is announced in the log", recent_log(win, "one.img"))
    check("the clear button greys out once the box names nothing",
          not win.imageclear.isEnabled())

    # ---- Windows paths differing only in case are ONE path -----------------
    survivor = combo.itemText(0)
    if os.name == "nt":
        check("history_index matches case-insensitively",
              combo.history_index(survivor.upper()) == 0,
              f"{survivor.upper()!r} -> {combo.history_index(survivor.upper())}")

    # ---- the removals reached hdfg.cfg -------------------------------------
    line = next((ln for ln in cfg_lines() if ln.startswith("image_history=")), "")
    check("the forgotten paths are gone from hdfg.cfg",
          "one.img" not in line and "two.img" not in line
          and "three.img" in line, line)

    # ---- 'Clear the whole list' forgets the LIST, not the mounted image ----
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)
    combo.setCurrentText(survivor)
    combo.clearHistoryRequested.emit()
    check("clearHistoryRequested empties the list",
          combo.count() == 0, combo.count())
    check("and leaves the shown path alone - forgetting the history is not "
          "an unmount",
          combo.currentText() == survivor, combo.currentText())
    line = next((ln for ln in cfg_lines() if ln.startswith("image_history=")), "")
    check("hdfg.cfg's image history is empty too", line == "image_history=", line)

    settle_end = time.monotonic() + 0.5
    while time.monotonic() < settle_end:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    app.quit()


def inspect_phase16():
    """Remembering local FOLDERS in the SD Card tab's path box (9.7.22).

    The reported ask: the image box above has remembered its paths since
    9.6.0 - "do the same for the Local path". This is the folder twin, and
    the interesting half is what must NOT happen: the box MIRRORS the tree,
    so if plain navigation fed the list, a 15-entry history would fill with
    folders merely passed through. Only a deliberate choice counts.

    The two dropdown affordances get real synthesised events for the same
    reason phase 15 does - both were shipped broken first on the image box
    (a combo popup view is made in C++, so assigning keyPressEvent is dead
    code, and QComboBoxPrivateContainer selects a row on ANY button
    release). The base class is now shared, so this proves the folder box
    inherited the working versions rather than a second broken copy.
    """
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QKeyEvent, QMouseEvent
    from PySide6.QtWidgets import QMessageBox
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return
    wait_until(lambda: not getattr(win, "_emulator_scan_pending", False),
               what="emulator scan settled")
    win._hdfmonkey_prompt_shown = True          # nothing modal (phase 14's lesson)

    def view_dir():
        return win.model.filePath(
            win.proxy_model.mapToSource(win.treeview.rootIndex()))

    combo = win.local_file_explorer_path
    check("the cfg's three folder entries were restored", combo.count() == 3,
          [combo.itemText(i) for i in range(combo.count())])
    # The restore must not have stolen the box from the tree: addItem into an
    # empty editable combo overwrites the line edit, so without the capture in
    # set_history_from_cfg row 0 would sit here masquerading as the real
    # folder (and this box is the one MAME reads its start folder from).
    check("the restore left the box showing the folder the tree is in",
          combo.text() == view_dir() and len(combo.text()) >= 2,
          f"{combo.text()!r} vs {view_dir()!r}")

    # ---- the '=' is in the row and gated on the LIST ----------------------
    clear = win.local_path_clear
    check("the clear button is dead while the box shows an unlisted folder",
          not clear.isEnabled(), combo.text())

    # ---- walking never remembers -----------------------------------------
    before = combo.count()
    win.local_explorer_up_button.click()
    check("'Up' adds no history entry", combo.count() == before,
          f"{before} -> {combo.count()}")
    before = combo.count()
    win.local_explorer_refresh_button.click()
    check("'Refresh' adds no history entry", combo.count() == before,
          f"{before} -> {combo.count()}")

    # ---- typing a path DOES remember -------------------------------------
    want = PASTE_SUB.replace("\\", "/")
    combo.setText(PASTE_SUB)
    combo.editingFinished.emit()
    check("a typed path navigates there", view_dir().rstrip("/") == want,
          f"{view_dir()!r} vs {want!r}")
    check("...and is remembered, at the top",
          combo.itemText(0).rstrip("/") == want,
          [combo.itemText(i) for i in range(combo.count())])
    check("...and the clear button woke up for it", clear.isEnabled())
    check("...and it reached hdfg.cfg",
          "pastedir" in next((ln for ln in cfg_lines()
                              if ln.startswith("explorerpath_history=")), ""),
          next((ln for ln in cfg_lines()
                if ln.startswith("explorerpath_history=")), ""))

    # Re-committing the same folder must not duplicate it or grow the list.
    before = combo.count()
    combo.setText(PASTE_SUB)
    combo.editingFinished.emit()
    check("re-committing the same folder does not duplicate it",
          combo.count() == before, f"{before} -> {combo.count()}")

    # ---- 'Remember this folder' - the only way in for a walked-to folder --
    win.local_explorer_up_button.click()
    walked = view_dir().rstrip("/")
    before = combo.count()
    combo.rememberRequested.emit()
    check("'Remember this folder' adds exactly one entry",
          combo.count() == before + 1 and combo.itemText(0).rstrip("/") == walked,
          [combo.itemText(i) for i in range(combo.count())])

    # ---- DELETE on the highlighted dropdown row ---------------------------
    combo.showPopup()
    view = combo.view()
    view.setCurrentIndex(combo.model().index(1, combo.modelColumn()))
    doomed = combo.itemText(1)
    was_showing, was_rooted = combo.text(), view_dir()
    app.sendEvent(view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                                  Qt.KeyboardModifier.NoModifier))
    check("Delete on a dropdown row forgets that row",
          all(combo.itemText(i) != doomed for i in range(combo.count())),
          [combo.itemText(i) for i in range(combo.count())])
    combo.hidePopup()

    # ---- a RIGHT-click on a dropdown row must not be read as a pick -------
    combo.showPopup()
    view = combo.view()
    picks = []
    combo.pathActivated.connect(picks.append)
    combo._popup_shown_at = time.monotonic()      # inside the settle window
    spot = QPointF(view.viewport().rect().center())
    for etype in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        app.sendEvent(view.viewport(), QMouseEvent(
            etype, spot, view.viewport().mapToGlobal(spot.toPoint()).toPointF(),
            Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier))
    combo.pathActivated.disconnect(picks.append)
    check("a right-click on a dropdown row does not navigate", not picks, picks)
    combo.hidePopup()

    # ---- the '=' forgets the row, and moves NOTHING ------------------------
    # The opposite of the image box, whose '=' also has to unload the disk:
    # forgetting a folder must leave the tree and the box exactly as they are.
    combo.setText(was_showing)
    if combo.history_index(was_showing) < 0:      # make sure there IS a row
        combo.remember(was_showing)
        combo.setText(was_showing)
    check("the clear button is live while the box names a remembered folder",
          clear.isEnabled(), combo.text())
    victim = combo.text()
    n_before = combo.count()
    rooted_before = view_dir()
    clear.click()
    check("the '=' forgot exactly that entry",
          combo.count() == n_before - 1
          and combo.history_index(victim) < 0, combo.count())
    check("...and left the box showing it", combo.text() == victim, combo.text())
    check("...and did not move the tree", view_dir() == rooted_before,
          f"{view_dir()!r} vs {rooted_before!r}")
    check("...and greyed itself out again", not clear.isEnabled())
    check("...and the removal was announced in the log",
          recent_log(win, "from the list"))

    # ---- picking a remembered folder navigates there ----------------------
    combo.remember(want)
    idx = combo.history_index(want)
    check("the typed folder is back in the list", idx >= 0, idx)
    if idx >= 0 and view_dir().rstrip("/") != want:
        combo._pick_armed = True     # a bare activated() is not a pick
        combo.activated.emit(idx)
        check("picking it from the dropdown navigates there",
              view_dir().rstrip("/") == want,
              f"{view_dir()!r} vs {want!r}")

    # ---- 'Clear the whole list' keeps the folder the tree is in -----------
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)
    shown_before = combo.text()
    rooted_before = view_dir()
    combo.clearHistoryRequested.emit()
    check("clearHistoryRequested empties the list", combo.count() == 0,
          combo.count())
    check("...and leaves the box and the tree alone",
          combo.text() == shown_before and view_dir() == rooted_before,
          f"{combo.text()!r} vs {shown_before!r}")
    line = next((ln for ln in cfg_lines()
                 if ln.startswith("explorerpath_history=")), "")
    check("hdfg.cfg's folder history is empty too",
          line == "explorerpath_history=", line)

    # ---- the Remote Explorer's box is the same widget, its own list -------
    # Two DISTINCT cfg keys was the explicit ask; the widget is built lazily,
    # so this only checks the key exists and stayed independent of the one
    # just emptied (phase 11 drives the Remote Explorer itself).
    check("the Remote Explorer keeps its own, separate history key",
          any(ln.startswith("nextsync_explorerpath_history=")
              for ln in cfg_lines()),
          [ln for ln in cfg_lines() if "explorerpath_history" in ln])

    settle_end = time.monotonic() + 0.5
    while time.monotonic() < settle_end:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    app.quit()


INSPECTORS = {1: inspect_phase1, 2: inspect_phase2, 3: inspect_phase3,
              4: inspect_phase4, 5: inspect_phase5, 6: inspect_phase6,
              7: inspect_phase7, 8: inspect_phase8, 9: inspect_phase9,
              10: inspect_phase10, 11: inspect_phase11, 12: inspect_phase12,
              13: inspect_phase13, 14: inspect_phase14, 15: inspect_phase15,
              16: inspect_phase16}

_orig_exec = QApplication.exec
def _run_inspector():
    """The phase's inspector, GUARDED. It runs inside a QTimer slot, where
    an exception is printed and swallowed by the app's excepthook: exec()
    never returns, and the runner's stdout loop keeps waiting on a process
    that will never end - one stray AttributeError used to cost the whole
    run_all budget with phases 5-16 never run. Now it is a FAILURE and the
    phase ends."""
    try:
        INSPECTORS[PHASE]()
    except BaseException:
        import traceback
        traceback.print_exc()
        check(f"phase {PHASE} inspector ran without raising", False)
        QApplication.instance().quit()

def _patched_exec(*_a):
    QTimer.singleShot(0, _run_inspector)
    return _orig_exec()
QApplication.exec = _patched_exec

try:
    runpy.run_path(os.path.join(SCRATCH, "zx-next-unite.py"), run_name="__main__")
except SystemExit:
    pass

print()
if FAILURES:
    print(f"PHASE {PHASE} RESULT: {len(FAILURES)} FAILURE(S): " + "; ".join(FAILURES))
    sys.exit(1)
print(f"PHASE {PHASE} RESULT: ALL CHECKS PASSED")
