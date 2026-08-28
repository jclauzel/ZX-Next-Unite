"""Offscreen end-to-end UI suite for zx-next-unite.

Run everything:   python test_ui_offscreen.py
Run one phase:    python test_ui_offscreen.py <1..5>

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
  4  NextSync classic local explorer: drag & drop configuration and an
     OS-style drop that imports the file.                (no hdfmonkey needed)
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

PHASE = int(sys.argv[1]) if len(sys.argv) > 1 else None
ALL_PHASES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15)

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
    for ph in ALL_PHASES:
        print(f"\n=== UI offscreen phase {ph} ===", flush=True)
        try:
            rc = subprocess.call([sys.executable, os.path.abspath(__file__), str(ph)],
                                 timeout=900)
        except subprocess.TimeoutExpired:
            print(f"PHASE {ph} TIMED OUT (possible UI hang)")
            rc = 1
        if rc != 0:
            failed.append(ph)
    print()
    if failed:
        print(f"UI SUITE RESULT: FAILED phase(s): {failed}")
        sys.exit(1)
    print("UI SUITE RESULT: ALL PHASES PASSED (or skipped cleanly)")
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
                # A hand-picked ground, with the Custom mode that picking one
                # leaves behind - the only mode in which a pick SURVIVES a
                # restart (every other mode recomputes the palette on load).
                + "color_background=#204060\ndesktop_theme=custom\n")
elif PHASE == 4:
    ensure_scratch(fresh=False)
    with open(CFG, "w") as f:
        f.write(BASE_CFG)
    if os.path.isdir(DROPZONE):
        shutil.rmtree(DROPZONE)
    os.makedirs(os.path.join(DROPZONE, "subdir"))
    with open(DROPSRC, "w") as f:
        f.write("drop me")
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
elif PHASE == 11:
    # NextSync Remote Explorer with pygame absent (every phase blocks pygame —
    # see _NoPygame). The retro log needs pygame; the Remote Explorer's dual
    # explorer must NOT, so it has to build and show all the same. The cfg
    # pre-selects the Remote Explorer view so the tab opens straight into it,
    # which is also how a user who last used it gets there.
    ensure_scratch(fresh=False)
    with open(CFG, "w") as f:
        f.write(BASE_CFG + "nextsync_remote_explorer=true\n")
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
sys.path.insert(0, REPO)

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit
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
    check("local box is QLineEdit", isinstance(win.local_file_explorer_path, QLineEdit))
    check("image box is QLineEdit", isinstance(win.diskimageexplorerpathinput, QLineEdit))
    check("image label text", win.diskimageexplorerlabel.text() == "Disk Image Explorer: ",
          win.diskimageexplorerlabel.text())
    grid = win.sdcard_explorer_grid
    def pos(widget):
        i = grid.indexOf(widget)
        return None if i < 0 else grid.getItemPosition(i)[:2]
    # The Remote Explorer mirroring (9.5.19): nav bars above the trees,
    # the path boxes BELOW them, the image buttons at the very bottom.
    check("local nav bar at grid (0,0)", pos(win.local_nav_row_container) == (0, 0), str(pos(win.local_nav_row_container)))
    check("image nav bar at grid (0,2)", pos(win.image_nav_row_container) == (0, 2), str(pos(win.image_nav_row_container)))
    _lnav = win.local_nav_row_container.layout()
    check("local nav = Up|Refresh",
          _lnav.indexOf(win.local_explorer_up_button) == 0
          and _lnav.indexOf(win.local_explorer_refresh_button) == 1)
    _inav = win.image_nav_row_container.layout()
    check("image nav = Up|Refresh",
          _inav.indexOf(win.image_explorer_up_button) == 0
          and _inav.indexOf(win.image_explorer_refresh_button) == 1)
    check("local path row at grid (2,0)", pos(win.local_path_row_container) == (2, 0), str(pos(win.local_path_row_container)))
    check("image path row at grid (2,2)", pos(win.image_path_row_container) == (2, 2), str(pos(win.image_path_row_container)))
    _lrow = win.local_path_row_container.layout()
    check("local path row = label|path box",
          _lrow.indexOf(win.localexplorerlabel) == 0
          and _lrow.indexOf(win.local_file_explorer_path) == 1)
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
    check("local explorer row at grid (1,0)",
          pos(_pane.local_tree_row_container) == (1, 0),
          str(pos(_pane.local_tree_row_container)))
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
    check("image explorer at grid (1,2)", pos(win.image_explorer_container) == (1, 2), str(pos(win.image_explorer_container)))
    check("button cluster no longer a grid row of its own",
          pos(win.imageexplorerbuttonscontainer) is None,
          str(pos(win.imageexplorerbuttonscontainer)))
    check("old widgets out of top row",
          win.horizontal2.indexOf(win.diskimageexplorerlabel) == -1
          and win.horizontal2.indexOf(win.diskimageexplorerpathinput) == -1)
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

    win.local_file_explorer_path.setText(r"Q:\definitely_not_there_xyz")
    win.local_file_explorer_path.editingFinished.emit()
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

        win.image_explorer_refresh_button.click()
        ok7 = wait_until(lambda: win.image_selected_path == "/games/sub"
                         and win.diskimageexplorerpathinput.text() == "/games/sub",
                         timeout=15, what="image Refresh keeps target")
        check("image Refresh keeps target", ok7,
              f"sel={win.image_selected_path!r} box={win.diskimageexplorerpathinput.text()!r}")

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
    from PySide6.QtWidgets import QAbstractItemView
    from PySide6.QtCore import QMimeData, QUrl, QPointF, Qt
    from PySide6.QtGui import QDropEvent
    app = QApplication.instance()
    win = find_win()
    check("MainWindow found", win is not None)
    if win is None:
        app.quit(); return
    tv = win.nextsync_treeview
    check("nextsync tree accepts drops", tv.acceptDrops())
    check("nextsync tree drag enabled", tv.dragEnabled())
    check("nextsync tree mode is DragDrop",
          tv.dragDropMode() == QAbstractItemView.DragDrop, str(tv.dragDropMode()))
    check("nextsync tree default action Copy",
          tv.defaultDropAction() == Qt.CopyAction, str(tv.defaultDropAction()))

    # Navigate the classic explorer to the drop zone via the sync-root box,
    # then synthesize an OS-style drop (source None = external drag). The
    # import dialog is modal but closes itself when the worker finishes.
    win.nextsync_file_explorer_path.setText(DROPZONE)
    win.nextsync_file_explorer_path.editingFinished.emit()
    QCoreApplication.processEvents()
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(DROPSRC)])
    ev = QDropEvent(QPointF(5.0, 5.0), Qt.CopyAction, md,
                    Qt.LeftButton, Qt.NoModifier)
    tv.dropEvent(ev)
    def _landed():
        return (os.path.isfile(os.path.join(DROPZONE, "dropsrc.txt"))
                or os.path.isfile(os.path.join(DROPZONE, "subdir", "dropsrc.txt")))
    ok = wait_until(_landed, timeout=30, what="dropped file lands in drop zone")
    check("OS drop imports the file", ok)
    check("source file untouched (copy, not move)", os.path.isfile(DROPSRC))
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
    check("update-check toggle at settings row 0",
          spos(cb) == (settings_row("zxnu_update_check"), 0)
          and settings_row("zxnu_update_check") == 0, str(spos(cb)))
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
    # The ground carries a default TEXT colour with it. The two plain
    # QFileSystemModel local explorers (SD Card, NextSync classic) set no
    # foreground brush, so they take this - and on a light Windows theme the
    # OS palette drew them black, which went invisible the moment the ground
    # turned dark. It follows the ground, so a light pick flips it back.
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
    check("language combo on its own visible row (1,1)", _pos == (1, 1),
          str(_pos))
    check("button translated at startup",
          win.selectimage.text() == "Seleccionar imagen de disco NextZXOS",
          win.selectimage.text())
    check("checkbox translated at startup",
          win.settings_no_prompt_on_deletion_checkbox.text()
          == "No pedir confirmación al eliminar.",
          win.settings_no_prompt_on_deletion_checkbox.text())
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
    from zxnu_config import ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC
    main_tabs = win._bg_widget.tab
    idx = next((i for i in range(main_tabs.count())
                if main_tabs.tabText(i) == ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC), None)
    check("NextSync tab present", idx is not None,
          str([main_tabs.tabText(i) for i in range(main_tabs.count())]))
    if idx is None:
        app.quit()
        return
    main_tabs.setCurrentIndex(idx)
    check("the app window is shown", wait_until(win.isVisible, 20, "window shown"))

    tabs = win.nextsync_mode_tabs
    # Tab 0 = Remote Explorer. The cfg pre-selected it, but drive it explicitly
    # so the phase does not depend on the restore having happened yet.
    tabs.setCurrentIndex(0)
    ok = wait_until(lambda: getattr(win, "_re_widget", None) is not None, 20,
                    "Remote Explorer widget built")
    check("Remote Explorer widget is built without pygame", ok)
    re_widget = getattr(win, "_re_widget", None)
    if re_widget is None:
        app.quit()
        return

    # The stack page is the CONTAINER (explorer + mini log) since the RE
    # view grew its own log strip; the explorer widget lives inside it.
    check("the Remote Explorer container is the visible page of the log stack",
          win.nextsync_log_stack.currentWidget() is win._re_container
          and re_widget.parent() is win._re_container,
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

    # The navigation buttons (Up / Refresh / + Drive) and the transfer arrows.
    labels = sorted(b.text() for b in re_widget.findChildren(CompactButton))
    check("both navigation bars' buttons rendered",
          labels == ["+ Drive", "Disconnect", "Refresh", "Refresh",
                     "Up", "Up"], str(labels))
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
    tabs.setCurrentIndex(1)
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
    tabs.setCurrentIndex(0)
    QCoreApplication.processEvents()
    check("returning to the Remote Explorer still shows it",
          win.nextsync_log_stack.currentWidget() is win._re_container
          and re_widget.isVisible())
    app.quit()


def inspect_phase12():
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

            win.imageinput.setCurrentText("")
            win.right_disk_image_path = ""
            win._image_write_state.pop(key, None)
            try:
                os.remove(busy_img)
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


INSPECTORS = {1: inspect_phase1, 2: inspect_phase2, 3: inspect_phase3,
              4: inspect_phase4, 5: inspect_phase5, 6: inspect_phase6,
              7: inspect_phase7, 8: inspect_phase8, 9: inspect_phase9,
              10: inspect_phase10, 11: inspect_phase11, 12: inspect_phase12,
              13: inspect_phase13, 14: inspect_phase14, 15: inspect_phase15}

_orig_exec = QApplication.exec
def _patched_exec(*_a):
    QTimer.singleShot(0, INSPECTORS[PHASE])
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
