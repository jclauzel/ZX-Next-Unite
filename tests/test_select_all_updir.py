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

This file exercises the helper on the SD-card local pane's exact
construction (QFileSystemModel + DotDotFirstProxyModel + QTreeView); the
Remote Explorer's two panes are covered on the real widget in
test_remote_explorer_widget.py."""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtCore import QCoreApplication, QDir, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractItemView, QApplication,
                               QFileSystemModel, QTreeView)

from zxnu_workers import DotDotFirstProxyModel, bind_select_all_except_updir

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

# The SD Card tab's local pane construction, line for line
# (zxnu_sdcard_explorer._build_local_pane).
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
view.setRootIndex(proxy.mapFromSource(model.index(root)))
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

print("RESULT: ALL PASS" if ok else "RESULT: FAILURES")
sys.exit(0 if ok else 1)
