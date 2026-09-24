"""Offscreen regression tests for bind_select_all_except_updir
(zxnu_workers) — the Ctrl-A / Select All override that keeps the ".."
parent-directory row OUT of the selection.

Two bugs drove this (both found on the same day, both user-reported):
  * the Remote Explorer's panes and the SD Card tab's local tree selected
    the ".." row along with everything else on Ctrl-A, so "select all and
    delete/drag" quietly included the folder above;
  * on the SD Card tab Ctrl-A did nothing AT ALL — set_treeview_properties
    (zxnu_sdcard_ops) re-applied SingleSelection on every refresh, silently
    downgrading the ExtendedSelection the pane had asked for. That function
    now re-applies ExtendedSelection; the widget-level proof lives here as
    a construction-equivalent check.

A third, found in review on 2026-09-24: an EXPANDED subfolder lists a
".." of its own in the file-system model, and the helper only deselects the
top-level one - so Ctrl-A selected every expanded subfolder's "..". The
proxy now filters those away (DotDotFirstProxyModel._is_shown_updir: only
the SHOWN folder's ".." is navigation), which this file pins through the
selection: nested rows selected, no ".." at any level, the shown folder's
".." still on top, and navigating in and back out swapping which ".." shows.

This file exercises the helper on the SD-card local pane's exact
construction (QFileSystemModel + DotDotFirstProxyModel + QTreeView, rooted
through root_tree_at as every pane is); the Remote Explorer's two panes are
covered on the real widget in test_remote_explorer_widget.py."""
import faulthandler
import os
import sys
import tempfile
import time

# stderr is unbuffered; a native Qt crash would otherwise eat the buffered
# stdout and with it any clue of which check was running (seen once on CI).
faulthandler.enable()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtCore import QCoreApplication, QDir, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractItemView, QApplication,
                               QFileSystemModel, QTreeView)

from zxnu_workers import (DotDotFirstProxyModel, bind_select_all_except_updir,
                          root_tree_at)

ok = True


def check(label, cond, detail=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'} {label}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        ok = False


def wait_until(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


app = QApplication(sys.argv)

# A folder WITH a parent, so the ".." row exists (a drive root has none).
root = tempfile.mkdtemp(prefix="ctrla-")
for n in ("aaa.txt", "bbb.txt", "ccc.txt"):
    with open(os.path.join(root, n), "w") as f:
        f.write("x")
os.mkdir(os.path.join(root, "sub"))
for n in ("inner1.txt", "inner2.txt"):
    with open(os.path.join(root, "sub", n), "w") as f:
        f.write("x")

# The SD Card tab's local pane construction, line for line
# (zxnu_sdcard_explorer._build_local_pane), rooted the way it roots.
model = QFileSystemModel()
model.setRootPath("/")
model.setFilter(~QDir.NoDotAndDotDot | QDir.NoDot)

view = QTreeView()
view.setSortingEnabled(True)
view.setSelectionMode(QAbstractItemView.ExtendedSelection)

proxy = DotDotFirstProxyModel(recursiveFilteringEnabled=True,
                              filterRole=QFileSystemModel.FileNameRole)
proxy.setSourceModel(model)
proxy.setSortCaseSensitivity(Qt.CaseInsensitive)
proxy.setDynamicSortFilter(True)
view.setModel(proxy)
ROOT_FWD = root.replace("\\", "/")
SUB_FWD = ROOT_FWD + "/sub"
check("fixture: the view is rooted through root_tree_at",
      root_tree_at(view, proxy, model, ROOT_FWD))
bind_select_all_except_updir(
    view, lambda ix: model.fileName(proxy.mapToSource(ix)) == "..")
view.show()

wait_until(lambda: proxy.rowCount(view.rootIndex()) >= 5)
names = [proxy.index(r, 0, view.rootIndex()).data()
         for r in range(proxy.rowCount(view.rootIndex()))]
check("fixture: 5 rows including '..'", len(names) == 5 and ".." in names,
      repr(names))

# --- Ctrl-A: everything except ".." ----------------------------------------
QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
QCoreApplication.processEvents()
sel = sorted(ix.data() for ix in view.selectionModel().selectedRows(0))
check("Ctrl-A selects the four entries", len(sel) == 4, repr(sel))
check("Ctrl-A leaves '..' unselected", ".." not in sel, repr(sel))

# --- the programmatic path takes the same override --------------------------
view.clearSelection()
view.selectAll()
QCoreApplication.processEvents()
sel = sorted(ix.data() for ix in view.selectionModel().selectedRows(0))
check("selectAll() also skips '..'", len(sel) == 4 and ".." not in sel,
      repr(sel))

# --- '..' stays clickable / selectable by hand ------------------------------
updir_ix = next(proxy.index(r, 0, view.rootIndex())
                for r in range(proxy.rowCount(view.rootIndex()))
                if proxy.index(r, 0, view.rootIndex()).data() == "..")
view.clearSelection()
view.setCurrentIndex(updir_ix)
check("'..' can still be selected by hand (only Select All skips it)",
      [ix.data() for ix in view.selectionModel().selectedRows(0)] == [".."])

# --- Ctrl-A in ExtendedSelection is what the SD tab relies on ---------------
# (the set_treeview_properties regression: SingleSelection made Ctrl-A a
# no-op — assert the mode the pane asks for actually keeps Ctrl-A alive)
view.setSelectionMode(QAbstractItemView.SingleSelection)
view.clearSelection()
QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
QCoreApplication.processEvents()
single = len(view.selectionModel().selectedRows(0))
view.setSelectionMode(QAbstractItemView.ExtendedSelection)
view.clearSelection()
QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
QCoreApplication.processEvents()
extended = len(view.selectionModel().selectedRows(0))
check("SingleSelection kills Ctrl-A (the SD-tab bug), Extended restores it",
      single <= 1 < extended, f"single={single} extended={extended}")

# --- an EXPANDED subfolder: its own ".." is not shown, so never selected ----
# Every index is re-resolved from a path at each use and never held across
# a wait: QFileSystemModel re-lists asynchronously, and a proxy index names
# a ROW, which a re-list can hand to something else.


def pix(path):
    return proxy.mapFromSource(model.index(path))


def src_names(path):
    ix = model.index(path)
    return [model.fileName(model.index(r, 0, ix)) for r in range(model.rowCount(ix))]


def shown_names(parent):
    return [proxy.index(r, 0, parent).data() for r in range(proxy.rowCount(parent))]


def _sub_listed():
    ix = model.index(SUB_FWD)
    if model.canFetchMore(ix):
        model.fetchMore(ix)
    return {"..", "inner1.txt", "inner2.txt"} <= set(src_names(SUB_FWD))


view.expand(pix(SUB_FWD))
check("fixture: the subfolder is listed WITH a '..' of its own in the model",
      wait_until(_sub_listed), repr(src_names(SUB_FWD)))
QCoreApplication.processEvents()
check("an expanded subfolder shows its contents and no '..'",
      sorted(shown_names(pix(SUB_FWD))) == ["inner1.txt", "inner2.txt"],
      repr(shown_names(pix(SUB_FWD))))
check("...while the SHOWN folder keeps its '..', on top",
      shown_names(view.rootIndex())[:1] == [".."],
      repr(shown_names(view.rootIndex())))
check("premise: the subfolder really is expanded", view.isExpanded(pix(SUB_FWD)))

view.clearSelection()
QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
QCoreApplication.processEvents()
sel = sorted(ix.data() for ix in view.selectionModel().selectedRows(0))
check("Ctrl-A with a subfolder expanded selects the nested rows too",
      sel == ["aaa.txt", "bbb.txt", "ccc.txt", "inner1.txt", "inner2.txt", "sub"],
      repr(sel))
check("...and no '..' at ANY level", ".." not in sel, repr(sel))

# --- navigating in and back out swaps which ".." is shown ------------------
check("navigating INTO the subfolder roots the view there",
      root_tree_at(view, proxy, model, SUB_FWD))
QCoreApplication.processEvents()
check("...and ITS '..' is now the shown one, on top",
      shown_names(view.rootIndex())[:1] == [".."],
      repr(shown_names(view.rootIndex())))
check("navigating back up roots the view on the folder again",
      root_tree_at(view, proxy, model, ROOT_FWD))
QCoreApplication.processEvents()
check("...where the folder's '..' is back on top",
      shown_names(view.rootIndex())[:1] == [".."],
      repr(shown_names(view.rootIndex())))
check("...and the subfolder's '..' is hidden again",
      ".." not in shown_names(pix(SUB_FWD)), repr(shown_names(pix(SUB_FWD))))

# --- a proxy no view was rooted through keeps the old answer ---------------
# (nothing to compare the ".." rows' folders against; tests that build a bare
# proxy, such as test_proxy_sort_fastpath, rely on seeing every "..")
bare = DotDotFirstProxyModel(recursiveFilteringEnabled=True,
                             filterRole=QFileSystemModel.FileNameRole)
bare.setSourceModel(model)
_bare_sub = bare.mapFromSource(model.index(SUB_FWD))
check("an unrooted proxy still shows every '..'",
      ".." in [bare.index(r, 0, _bare_sub).data()
               for r in range(bare.rowCount(_bare_sub))])

print("RESULT: ALL PASS" if ok else "RESULT: FAILURES")
sys.exit(0 if ok else 1)
