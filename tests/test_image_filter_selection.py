"""A row the disk-image filter has hidden is not a target.

The SD Card tab's image explorer filters at the VIEW level - setRowHidden over
a QStandardItemModel - because nearly every operation in the pane indexes
image_model DIRECTLY. Hiding a row does NOT deselect it, and the selection
model knows nothing about view-level hiding, so a file the filter had taken
off screen was still handed to Delete, Download, the transfer buttons and
drag-out: the app destroyed or copied something the user could not see.

The Remote Explorer's Next pane, which filters the same way and was modelled
on this very tree, fixed it in 9.7.33 with BOTH halves - deselect as you hide,
AND skip hidden rows when reading the selection. This suite pins both, plus
the tree-shaped part the Next pane does not have: that tree is FLAT, so one
isRowHidden answers it, while a hidden folder here can have descendants whose
own flag says visible.

It drives the REAL methods off a synthetic model/view, the way
tests/test_select_all_updir.py drives its binding helper, so it needs no
hdfmonkey and no disk image - which matters, because CI cannot run offscreen
phases 1-3 and this class of bug has shipped unseen before.

Run with: python tests/test_image_filter_selection.py
"""
import os
import sys
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtCore import QModelIndex as _QMI  # noqa: E402
from PySide6.QtGui import QStandardItem, QStandardItemModel  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QTreeView  # noqa: E402

app = QApplication.instance() or QApplication([])

from zxnu_sdcard_explorer import (  # noqa: E402
    IMG_ISDIR_ROLE, IMG_LOADED_ROLE, IMG_PATH_ROLE, SdCardExplorerPane)

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


# ── a synthetic image tree ───────────────────────────────────────────────
#   /games          (dir, loaded)
#     manic.tap
#     jetpac.tap
#   /readme.txt
def _row(name, path, is_dir, loaded=True, type_text="", size_text=""):
    it = QStandardItem(name)
    it.setData(path, IMG_PATH_ROLE)
    it.setData(is_dir, IMG_ISDIR_ROLE)
    it.setData(loaded, IMG_LOADED_ROLE)
    return [it, QStandardItem(type_text), QStandardItem(size_text)]


def build():
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["Name", "Type", "Size"])
    games = _row("games", "/games", True, type_text="DIR")
    model.appendRow(games)
    games[0].appendRow(_row("manic.tap", "/games/manic.tap", False,
                            type_text="tap", size_text="48 K"))
    games[0].appendRow(_row("jetpac.tap", "/games/jetpac.tap", False,
                            type_text="tap", size_text="16 K"))
    model.appendRow(_row("readme.txt", "/readme.txt", False,
                         type_text="txt", size_text="1 K"))

    view = QTreeView()
    view.setModel(model)
    view.setSelectionMode(QTreeView.ExtendedSelection)
    view.expandAll()

    edit = QLineEdit()
    names = {"last": None}

    pane = types.SimpleNamespace()
    pane.image_model = model
    pane.image_treeview = view
    pane._image_filter_edit = edit
    pane._host = types.SimpleNamespace(
        image_selected_path="", image_selected_is_dir=False,
        image_selected_paths=[])
    pane._hooks = types.SimpleNamespace(
        set_selected_names=lambda n: names.__setitem__("last", list(n)))
    pane.image_update_path_label = lambda: None
    # Where uploads / New Folder / Up / Refresh point (see image_dest_dir).
    pane._image_anchor_dir = "/"
    # The REAL methods, bound to the fixture - the point is to test what
    # ships, not a restatement of it.
    for _m in ("_image_row_hidden", "_selected_image_rows",
               "_on_image_selection_changed", "apply_image_filter",
               "image_dest_dir"):
        setattr(pane, _m, types.MethodType(getattr(SdCardExplorerPane, _m), pane))
    # _dir_of is a staticmethod: taken off the class it is already a plain
    # function, so binding it would pass the fixture as its first argument.
    pane._dir_of = SdCardExplorerPane._dir_of
    return pane, model, view, edit, names


def ix_of(model, path):
    """The column-0 index whose IMG_PATH_ROLE is *path*."""
    found = []

    def walk(parent):
        for r in range(parent.rowCount()):
            it = parent.child(r, 0)
            if it is None:
                continue
            if (it.data(IMG_PATH_ROLE) or "") == path:
                found.append(it.index())
            walk(it)
    walk(model.invisibleRootItem())
    return found[0] if found else None


def select(view, model, *paths):
    sel = view.selectionModel()
    sel.clearSelection()
    for p in paths:
        sel.select(ix_of(model, p),
                   QItemSelectionModel.Select | QItemSelectionModel.Rows)


print("== the filter deselects as it hides ==")
pane, model, view, edit, names = build()
select(view, model, "/games/manic.tap", "/games/jetpac.tap")
pane._on_image_selection_changed()
check("premise: both files are selected",
      sorted(p for p, _d in pane._host.image_selected_paths)
      == ["/games/jetpac.tap", "/games/manic.tap"],
      str(pane._host.image_selected_paths))

edit.setText("manic")
pane.apply_image_filter()
check("the non-matching file is hidden",
      view.isRowHidden(ix_of(model, "/games/jetpac.tap").row(),
                       ix_of(model, "/games/jetpac.tap").parent()))
# THE BUG: without the Deselect it stayed selected and reached Delete.
check("...and is no longer selected",
      not view.selectionModel().isSelected(ix_of(model, "/games/jetpac.tap")))
check("the matching file is still selected",
      view.selectionModel().isSelected(ix_of(model, "/games/manic.tap")))
check("the operation layer sees only the visible file",
      [p for p, _d in pane._host.image_selected_paths] == ["/games/manic.tap"],
      str(pane._host.image_selected_paths))
check("...and so does the legacy name list the transfer paths read",
      names["last"] == ["manic.tap"], str(names["last"]))

print()
print("== the readers skip hidden rows even if something else hid them ==")
pane, model, view, edit, names = build()
select(view, model, "/games/manic.tap", "/readme.txt")
# Hide WITHOUT going through the filter: the guard in the reader is the
# second half of the invariant and must hold on its own.
ix = ix_of(model, "/readme.txt")
view.setRowHidden(ix.row(), ix.parent(), True)
check("a row hidden behind the filter's back is still selected",
      view.selectionModel().isSelected(ix))
check("...but is not returned as a target",
      [i.data(IMG_PATH_ROLE) for i in pane._selected_image_rows()]
      == ["/games/manic.tap"],
      str([i.data(IMG_PATH_ROLE) for i in pane._selected_image_rows()]))

print()
print("== a hidden ANCESTOR hides its descendants (this tree is not flat) ==")
pane, model, view, edit, names = build()
select(view, model, "/games/manic.tap")
gix = ix_of(model, "/games")
view.setRowHidden(gix.row(), gix.parent(), True)
mix = ix_of(model, "/games/manic.tap")
check("premise: the child's OWN flag still says visible",
      not view.isRowHidden(mix.row(), mix.parent()))
# The Next pane can test one row because its list is flat; here a file
# inside a hidden folder is just as invisible as the folder.
check("a file under a hidden folder counts as hidden",
      pane._image_row_hidden(mix))
check("...so it is not a target either", pane._selected_image_rows() == [],
      str([i.data(IMG_PATH_ROLE) for i in pane._selected_image_rows()]))

print()
print("== the primary row and the path label ==")
pane, model, view, edit, names = build()
view.setCurrentIndex(ix_of(model, "/games/jetpac.tap"))
pane._on_image_selection_changed()
check("premise: the current row is the primary target",
      pane._host.image_selected_path == "/games/jetpac.tap",
      pane._host.image_selected_path)
edit.setText("manic")
pane.apply_image_filter()
# currentIndex is NOT part of the selection and survives a Deselect, so
# without clearing it New Folder and the path label kept naming a row the
# user can no longer see.
check("a hidden current row stops being the primary target",
      pane._host.image_selected_path != "/games/jetpac.tap",
      pane._host.image_selected_path)

print()
print("== clearing the filter gives everything back ==")
pane, model, view, edit, names = build()
select(view, model, "/games/manic.tap", "/games/jetpac.tap")
pane._on_image_selection_changed()
edit.setText("manic")
pane.apply_image_filter()
edit.setText("")
pane.apply_image_filter()
check("every row is visible again",
      not view.isRowHidden(ix_of(model, "/games/jetpac.tap").row(),
                           ix_of(model, "/games/jetpac.tap").parent()))
# The deselection is NOT undone, and that is correct: the rows came back,
# the selection did not, exactly as the Next pane behaves.
check("the file that was filtered away stays deselected",
      not view.selectionModel().isSelected(ix_of(model, "/games/jetpac.tap")))
check("...and the still-selected one survived the round trip",
      [p for p, _d in pane._host.image_selected_paths] == ["/games/manic.tap"],
      str(pane._host.image_selected_paths))

print()
print("== the filter does not move where the pane WRITES ==")
# On this pane the SELECTION IS THE NAVIGATION STATE: image_dest_dir derives
# the upload / New Folder / paste / Up / Refresh target from it. Deselecting
# to protect Delete therefore retargeted uploads to the image ROOT - a worse
# bug than the one being fixed, and one the Next pane never had because it
# keeps its folder in its own state.
pane, model, view, edit_box, names = build()
select(view, model, "/games/manic.tap")
view.setCurrentIndex(ix_of(model, "/games/manic.tap"))
pane._on_image_selection_changed()
check("premise: uploads target the selected file's folder",
      pane.image_dest_dir() == "/games", pane.image_dest_dir())
edit_box.setText("zzz-no-such-name")
pane.apply_image_filter()
check("premise: the row is gone as a target",
      pane._host.image_selected_path == "", pane._host.image_selected_path)
check("a filter keystroke does NOT send uploads to the image root",
      pane.image_dest_dir() == "/games", pane.image_dest_dir())
edit_box.setText("")
pane.apply_image_filter()
check("...and clearing the filter leaves it there",
      pane.image_dest_dir() == "/games", pane.image_dest_dir())

# ...but genuinely navigating to the root still targets the root. That path
# clears the selection through the same handler, which is what moves the
# anchor - so the two cases stay distinguishable.
view.selectionModel().clearSelection()
view.setCurrentIndex(_QMI())
pane._on_image_selection_changed()
check("navigating to the root really does target the root",
      pane.image_dest_dir() == "/", pane.image_dest_dir())

print()
print("== a folder whose CHILD matches stays reachable ==")
pane, model, view, edit, names = build()
edit.setText("jetpac")
pane.apply_image_filter()
gix = ix_of(model, "/games")
check("the parent folder is shown because a child matches",
      not view.isRowHidden(gix.row(), gix.parent()))
check("the matching child is shown",
      not view.isRowHidden(ix_of(model, "/games/jetpac.tap").row(),
                           ix_of(model, "/games/jetpac.tap").parent()))
check("its sibling is not",
      view.isRowHidden(ix_of(model, "/games/manic.tap").row(),
                       ix_of(model, "/games/manic.tap").parent()))

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all image-filter selection checks passed")
sys.exit(0)
