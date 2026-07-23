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
CFG = os.path.join(SCRATCH, "hdfg.cfg")
HDF = os.path.join(SCRATCH, "test.hdf")
PASTE_SUB = os.path.join(SCRATCH, "pastedir", "sub")
PASTE_FILE = os.path.join(PASTE_SUB, "afile.txt")
DROPZONE = os.path.join(SCRATCH, "dropzone")
DROPSRC = os.path.join(SCRATCH, "dropsrc.txt")
DELZONE = os.path.join(SCRATCH, "delzone")

PHASE = int(sys.argv[1]) if len(sys.argv) > 1 else None
ALL_PHASES = (1, 2, 3, 4, 5, 6, 7)

# Base cfg for the isolated app copy: update checks off (MAME/CSpect AND the
# app's own GitHub release check) so no phase ever hits the network.
BASE_CFG = ("mame_update_check=false\ncspect_update_check=false\n"
            "zxnu_update_check=false\n")


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
    ensure_scratch(fresh=False)
    saved = "/games/sub" if PHASE == 2 else "/gone"
    with open(CFG, "w") as f:
        f.write(BASE_CFG
                + f"hddffile={HDF}\nimage_explorerpath={saved}\n"
                + "color_retro_log=#112233\n")
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
            f.write("mame_update_check=false\ncspect_update_check=false\n")
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

from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtCore import QTimer, QCoreApplication

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
    check("local path row at grid (0,0)", pos(win.local_path_row_container) == (0, 0), str(pos(win.local_path_row_container)))
    check("image path row at grid (0,2)", pos(win.image_path_row_container) == (0, 2), str(pos(win.image_path_row_container)))
    _lrow = win.local_path_row_container.layout()
    check("local row = Up|Refresh|label|path box",
          _lrow.indexOf(win.local_explorer_up_button) == 0
          and _lrow.indexOf(win.local_explorer_refresh_button) == 1
          and _lrow.indexOf(win.localexplorerlabel) == 2
          and _lrow.indexOf(win.local_file_explorer_path) == 3)
    check("local label text", win.localexplorerlabel.text() == "Local path: ",
          win.localexplorerlabel.text())
    _irow = win.image_path_row_container.layout()
    check("image row = Up|Refresh|label|path box",
          _irow.indexOf(win.image_explorer_up_button) == 0
          and _irow.indexOf(win.image_explorer_refresh_button) == 1
          and _irow.indexOf(win.diskimageexplorerlabel) == 2
          and _irow.indexOf(win.diskimageexplorerpathinput) == 3)
    check("local explorer at grid (1,0)", pos(win.treeview) == (1, 0), str(pos(win.treeview)))
    check("image explorer at grid (1,2)", pos(win.image_explorer_container) == (1, 2), str(pos(win.image_explorer_container)))
    check("image buttons at grid (2,2)", pos(win.imageexplorerbuttonscontainer) == (2, 2), str(pos(win.imageexplorerbuttonscontainer)))
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
    check("general-text swatch at settings (22,1)",
          spos(win.settings_btn_color_general_text) == (22, 1), str(spos(win.settings_btn_color_general_text)))
    check("retro-log swatch right under it (23,1)",
          spos(win.settings_btn_color_retro_log) == (23, 1), str(spos(win.settings_btn_color_retro_log)))
    check("retro font combo pushed to (24,1)",
          spos(win.settings_retro_log_font_combo) == (24, 1), str(spos(win.settings_retro_log_font_combo)))
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
    check("repo has no stray zx-next-unite.log",
          not os.path.isfile(os.path.join(REPO, "zx-next-unite.log")))

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
    check("update-check toggle at settings row 0", spos(cb) == (0, 0), str(spos(cb)))
    check("desktop theme pushed to row 1",
          spos(win.settings_desktop_theme_combo) == (1, 1),
          str(spos(win.settings_desktop_theme_combo)))
    check("cfg 'false' restored as unchecked", not cb.isChecked())
    cb.setChecked(True)
    QCoreApplication.processEvents()
    check("toggle persists to cfg", "zxnu_update_check=true" in cfg_lines(),
          str([l for l in cfg_lines() if l.startswith("zxnu_update_check")]))

    # Recycle Bin deletes toggle: sits right under the no-prompt checkbox.
    rb = win.settings_delete_to_recycle_bin_checkbox
    check("recycle toggle at settings (4,0)", spos(rb) == (4, 0), str(spos(rb)))
    check("no-prompt checkbox above it (3,0)",
          spos(win.settings_no_prompt_on_deletion_checkbox) == (3, 0),
          str(spos(win.settings_no_prompt_on_deletion_checkbox)))
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

INSPECTORS = {1: inspect_phase1, 2: inspect_phase2, 3: inspect_phase3,
              4: inspect_phase4, 5: inspect_phase5, 6: inspect_phase6,
              7: inspect_phase7}

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
