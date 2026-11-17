"""The disk-image explorer: ONE folder at a time, and a row the user cannot
see is never a target.

Two things are pinned here.

1. The view is FLAT since 9.7.73. The SD Card tab's image explorer showed
   every level at once behind expand arrows, while the local explorer beside
   it and the Remote Explorer's Next pane show one folder and move with a
   double-click, '..' and Up. It works their way now: the model is still the
   nested, lazily listed tree, the VIEW is rooted at the folder on show, and
   the folder on show - not the selection - is where uploads, New Folder and
   paste go.

2. A row that is not on screen is not a target. The pane filters at the VIEW
   level (setRowHidden over a QStandardItemModel) because nearly every
   operation indexes image_model directly; hiding a row does NOT deselect it,
   and the selection model SURVIVES a re-root. So a file the filter had taken
   off screen, or one selected in the folder just left, would still reach
   Delete, Download, the transfer buttons and drag-out. Both halves are
   needed and stated twice on purpose: the filter deselects as it hides and
   moving clears the selection, AND the one reader every operation goes
   through skips rows that are hidden or not in the folder on show.

It drives the REAL methods off a synthetic model/view, so it needs no
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

from PySide6.QtCore import QItemSelectionModel, Qt  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QStandardItem, QStandardItemModel  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QTreeView  # noqa: E402

app = QApplication.instance() or QApplication([])

from zxnu_sdcard_explorer import (  # noqa: E402
    IMG_ISDIR_ROLE, IMG_LOADED_ROLE, IMG_PATH_ROLE, IMG_UPDIR_ROLE,
    SdCardExplorerPane)

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


# ── a synthetic image tree ───────────────────────────────────────────────
#   /games              (dir, loaded)
#     /games/sub        (dir, loaded)
#       deep.nex
#     manic.tap
#     jetpac.tap
#   /docs               (dir, loaded, empty)
#   /readme.txt
def _row(name, path, is_dir, loaded=True, type_text="", size_text=""):
    it = QStandardItem(name)
    it.setData(path, IMG_PATH_ROLE)
    it.setData(is_dir, IMG_ISDIR_ROLE)
    it.setData(loaded, IMG_LOADED_ROLE)
    return [it, QStandardItem(type_text), QStandardItem(size_text)]


REAL = ("_image_row_hidden", "_selected_image_rows",
        "_on_image_selection_changed", "apply_image_filter",
        "image_dest_dir", "_set_image_anchor", "_image_shown_item",
        "_image_shown_parent", "_image_drop_updir", "_image_ensure_updir",
        "_image_pin_updir", "_image_show", "image_explorer_up",
        "_on_image_double_clicked", "image_navigate_to_path",
        "image_find_item")


def build():
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["Name", "Type", "Size"])
    games = _row("games", "/games", True, type_text="DIR")
    model.appendRow(games)
    sub = _row("sub", "/games/sub", True, type_text="DIR")
    games[0].appendRow(sub)
    sub[0].appendRow(_row("deep.nex", "/games/sub/deep.nex", False,
                          type_text="nex", size_text="9 K"))
    games[0].appendRow(_row("manic.tap", "/games/manic.tap", False,
                            type_text="tap", size_text="48 K"))
    games[0].appendRow(_row("jetpac.tap", "/games/jetpac.tap", False,
                            type_text="tap", size_text="16 K"))
    model.appendRow(_row("docs", "/docs", True, type_text="DIR"))
    model.appendRow(_row("readme.txt", "/readme.txt", False,
                         type_text="txt", size_text="1 K"))

    view = QTreeView()
    view.setModel(model)
    view.setSelectionMode(QTreeView.ExtendedSelection)
    view.setRootIsDecorated(False)
    view.setItemsExpandable(False)

    edit = QLineEdit()
    names = {"last": None}
    logs = []

    pane = types.SimpleNamespace()
    pane.image_model = model
    pane.image_treeview = view
    pane._image_filter_edit = edit
    pane._img_folder_icon = QIcon()
    pane._image_load_generation = 0
    pane._host = types.SimpleNamespace(
        image_selected_path="", image_selected_is_dir=False,
        image_selected_paths=[],
        img_color_dir_name=QColor("#ffaa00"), img_color_dir_type=QColor("#ff0000"))
    pane._hooks = types.SimpleNamespace(
        set_selected_names=lambda n: names.__setitem__("last", list(n)),
        is_image_loaded=lambda: True, log=logs.append)
    pane.image_update_path_label = lambda: None
    pane._image_anchor_dir = "/"
    # The REAL methods, bound to the fixture - the point is to test what
    # ships, not a restatement of it.
    for _m in REAL:
        setattr(pane, _m, types.MethodType(getattr(SdCardExplorerPane, _m), pane))
    # staticmethods: taken off the class they are plain functions already.
    pane._image_updir_row = SdCardExplorerPane._image_updir_row
    pane._dir_of = SdCardExplorerPane._dir_of
    model.layoutChanged.connect(lambda *_a: pane._image_pin_updir())
    pane.logs = logs
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


def shown_names(view, model):
    """The row names the view shows, top to bottom, hidden ones left out."""
    root = view.rootIndex()
    return [model.index(r, 0, root).data() for r in range(model.rowCount(root))
            if not view.isRowHidden(r, root)]


def targets(pane):
    return [i.data(IMG_PATH_ROLE) for i in pane._selected_image_rows()]


print("== one folder at a time ==")
pane, model, view, edit, names = build()
check("the view has no expand arrows and cannot be expanded",
      not view.rootIsDecorated() and not view.itemsExpandable())
check("at the image root there is no '..' row",
      shown_names(view, model) == ["games", "docs", "readme.txt"],
      str(shown_names(view, model)))
check("...and the target is the root", pane.image_dest_dir() == "/")
pane.image_navigate_to_path("/games")
check("entering a folder shows ITS rows, '..' first",
      shown_names(view, model) == ["..", "sub", "manic.tap", "jetpac.tap"],
      str(shown_names(view, model)))
check("the folder on show is the target", pane.image_dest_dir() == "/games",
      pane.image_dest_dir())
check("nothing is selected on arrival",
      pane._host.image_selected_path == "" and targets(pane) == [])
view.selectionModel().select(ix_of(model, "/games/sub"),
                             QItemSelectionModel.Select | QItemSelectionModel.Rows)
pane._on_image_selection_changed()
check("selecting a folder row does NOT move the target into it",
      pane.image_dest_dir() == "/games" and pane._host.image_selected_path == "/games/sub",
      pane.image_dest_dir())
pane._on_image_double_clicked(ix_of(model, "/games/sub"))
check("a double-click on a folder enters it",
      pane.image_dest_dir() == "/games/sub"
      and shown_names(view, model) == ["..", "deep.nex"], str(shown_names(view, model)))
check("the '..' row lives only in the folder on show",
      SdCardExplorerPane._image_updir_row(model.itemFromIndex(ix_of(model, "/games"))) == -1)
pane._on_image_double_clicked(view.model().index(0, 0, view.rootIndex()))
check("a double-click on '..' goes up",
      pane.image_dest_dir() == "/games"
      and shown_names(view, model) == ["..", "sub", "manic.tap", "jetpac.tap"],
      str(shown_names(view, model)))
check("going up selects nothing (a selected row is what Delete acts on)",
      pane._host.image_selected_path == "" and targets(pane) == []
      and not view.currentIndex().isValid())
pane.image_explorer_up()
check("Up from a top-level folder shows the root, without a '..' row",
      pane.image_dest_dir() == "/" and not view.rootIndex().isValid()
      and shown_names(view, model) == ["games", "docs", "readme.txt"],
      str(shown_names(view, model)))
pane.image_explorer_up()
check("Up at the root does nothing", pane.image_dest_dir() == "/")
check("the model is back to its own rows (no '..' left anywhere)",
      ix_of(model, "") is None)

print()
print("== a path names a folder, or a file in it ==")
pane, model, view, edit, names = build()
pane.image_navigate_to_path("/GAMES/Sub")          # FAT: case-blind
check("a typed folder path is shown (case-blind)",
      pane.image_dest_dir() == "/games/sub", pane.image_dest_dir())
pane.image_navigate_to_path("/games/manic.tap")
check("a file path shows its folder with the file selected",
      pane.image_dest_dir() == "/games"
      and pane._host.image_selected_path == "/games/manic.tap"
      and targets(pane) == ["/games/manic.tap"], str(targets(pane)))
pane.image_navigate_to_path("/games/sub/..")
check("'..' in a path is resolved", pane.image_dest_dir() == "/games")
pane.image_navigate_to_path("/games/nope/x")
check("a path that does not exist leaves the view where it is, and says so",
      pane.image_dest_dir() == "/games"
      and any("not found" in m for m in pane.logs), str(pane.logs))
pane.image_navigate_to_path("/games/nope/x", nearest=True)
check("...or, after a reload, lands on the deepest folder that exists",
      pane.image_dest_dir() == "/games")
pane.image_navigate_to_path("/docs")
check("an empty folder shows only '..'", shown_names(view, model) == [".."],
      str(shown_names(view, model)))
pane.image_navigate_to_path("/")
check("'/' shows the root", pane.image_dest_dir() == "/"
      and not view.rootIndex().isValid())

print()
print("== a sort keeps '..' first ==")
pane, model, view, edit, names = build()
pane.image_navigate_to_path("/games")
for order in (Qt.AscendingOrder, Qt.DescendingOrder):
    model.sort(0, order)
    got = shown_names(view, model)
    check(f"'..' is row 0 after a sort ({order.name})",
          got[0] == ".." and sorted(got[1:]) == ["jetpac.tap", "manic.tap", "sub"]
          and got.count("..") == 1, str(got))
check("the view is still on the folder", pane.image_dest_dir() == "/games"
      and model.itemFromIndex(view.rootIndex()).data(IMG_PATH_ROLE) == "/games")

print()
print("== the '..' row is never a target ==")
pane, model, view, edit, names = build()
pane.image_navigate_to_path("/games")
view.selectAll()
pane._on_image_selection_changed()
check("Ctrl-A: every real row, never '..'",
      sorted(p for p, _d in pane._host.image_selected_paths)
      == ["/games/jetpac.tap", "/games/manic.tap", "/games/sub"],
      str(pane._host.image_selected_paths))
view.selectionModel().clearSelection()
view.setCurrentIndex(model.index(0, 0, view.rootIndex()))
pane._on_image_selection_changed()
check("a click on '..' alone selects nothing for the operation layer",
      pane._host.image_selected_path == "" and pane._host.image_selected_paths == []
      and model.index(0, 0, view.rootIndex()).data(IMG_UPDIR_ROLE))

print()
print("== the filter works on the folder on show ==")
pane, model, view, edit, names = build()
pane.image_navigate_to_path("/games")
select(view, model, "/games/manic.tap", "/games/jetpac.tap")
pane._on_image_selection_changed()
check("premise: both files are selected",
      sorted(p for p, _d in pane._host.image_selected_paths)
      == ["/games/jetpac.tap", "/games/manic.tap"],
      str(pane._host.image_selected_paths))
edit.setText("manic")
pane.apply_image_filter()
check("only the matching row and '..' stay",
      shown_names(view, model) == ["..", "manic.tap"], str(shown_names(view, model)))
# THE BUG (9.7.33): without the Deselect it stayed selected and reached Delete.
check("the hidden file is no longer selected",
      not view.selectionModel().isSelected(ix_of(model, "/games/jetpac.tap")))
check("the matching file is still selected",
      view.selectionModel().isSelected(ix_of(model, "/games/manic.tap")))
check("the operation layer sees only the visible file",
      [p for p, _d in pane._host.image_selected_paths] == ["/games/manic.tap"],
      str(pane._host.image_selected_paths))
check("...and so does the legacy name list the transfer paths read",
      names["last"] == ["manic.tap"], str(names["last"]))
check("a filter keystroke does not move the target", pane.image_dest_dir() == "/games")
edit.setText("16 k")
pane.apply_image_filter()
check("the Size column is matched too",
      shown_names(view, model) == ["..", "jetpac.tap"], str(shown_names(view, model)))
edit.setText("dir")
pane.apply_image_filter()
check("...and the Type column: 'dir' lists the folders",
      shown_names(view, model) == ["..", "sub"], str(shown_names(view, model)))
edit.setText("deep")
pane.apply_image_filter()
check("a folder is matched by its own row, not by what is inside it",
      shown_names(view, model) == [".."], str(shown_names(view, model)))
edit.setText("")
pane.apply_image_filter()
check("clearing the filter gives every row back",
      shown_names(view, model) == ["..", "sub", "manic.tap", "jetpac.tap"],
      str(shown_names(view, model)))
# The deselection is NOT undone, and that is correct: the rows came back,
# the selection did not, exactly as the Next pane behaves.
check("the file that was filtered away stays deselected",
      not view.selectionModel().isSelected(ix_of(model, "/games/jetpac.tap")))

print()
print("== the filter follows you from folder to folder ==")
pane, model, view, edit, names = build()
edit.setText("zzz")
pane.apply_image_filter()
check("premise: the filter hides every root row",
      shown_names(view, model) == [], str(shown_names(view, model)))
# The root's 'games' row is hidden. A folder reached by its typed path must
# not inherit that: its rows are judged on their own.
pane.image_navigate_to_path("/games")
check("a folder whose own row is filtered away can still be entered",
      pane.image_dest_dir() == "/games" and shown_names(view, model) == [".."],
      str(shown_names(view, model)))
edit.setText("tap")
pane.apply_image_filter()
check("...and its rows are filtered on their own text",
      shown_names(view, model) == ["..", "manic.tap", "jetpac.tap"],
      str(shown_names(view, model)))
select(view, model, "/games/manic.tap")
check("a row shown there is a target although its folder's row is hidden above",
      targets(pane) == ["/games/manic.tap"], str(targets(pane)))
pane.image_explorer_up()
check("going up re-applies the filter to the folder arrived in",
      shown_names(view, model) == [], str(shown_names(view, model)))

print()
print("== the readers skip what is not on screen, whatever hid it ==")
pane, model, view, edit, names = build()
pane.image_navigate_to_path("/games")
select(view, model, "/games/manic.tap", "/games/jetpac.tap")
# Hide WITHOUT going through the filter: the guard in the reader is the
# second half of the invariant and must hold on its own.
ix = ix_of(model, "/games/jetpac.tap")
view.setRowHidden(ix.row(), ix.parent(), True)
check("a row hidden behind the filter's back is still selected",
      view.selectionModel().isSelected(ix))
check("...but is not returned as a target",
      targets(pane) == ["/games/manic.tap"], str(targets(pane)))
# Re-root WITHOUT going through _image_show: the selection survives it.
view.setRootIndex(ix_of(model, "/games/sub"))
check("a row selected in another folder survives a bare re-root",
      view.selectionModel().isSelected(ix_of(model, "/games/manic.tap")))
check("...but is not a target once its folder is not the one on show",
      targets(pane) == [], str(targets(pane)))
pane._on_image_selection_changed()
check("...nor the primary row",
      pane._host.image_selected_path == "" and pane._host.image_selected_paths == [])

print()
print("== moving always starts with nothing selected ==")
pane, model, view, edit, names = build()
pane.image_navigate_to_path("/games")
select(view, model, "/games/manic.tap")
view.setCurrentIndex(ix_of(model, "/games/manic.tap"))
pane._on_image_selection_changed()
pane.image_navigate_to_path("/games/sub")
check("the row selected in the folder left is deselected",
      not view.selectionModel().hasSelection() and not view.currentIndex().isValid())
check("the host's records are empty",
      pane._host.image_selected_path == "" and pane._host.image_selected_paths == []
      and names["last"] == [])

print()
print("== the primary row ==")
pane, model, view, edit, names = build()
pane.image_navigate_to_path("/games")
view.setCurrentIndex(ix_of(model, "/games/jetpac.tap"))
pane._on_image_selection_changed()
check("premise: the current row is the primary target",
      pane._host.image_selected_path == "/games/jetpac.tap",
      pane._host.image_selected_path)
edit.setText("manic")
pane.apply_image_filter()
# currentIndex is NOT part of the selection and survives a Deselect, so
# without clearing it Rename and Delete kept naming a row the user can no
# longer see.
check("a hidden current row stops being the primary target",
      pane._host.image_selected_path != "/games/jetpac.tap",
      pane._host.image_selected_path)

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all image-explorer checks passed")
sys.exit(0)
