"""9.7.73: three small UI rules, checked headlessly (this runs in CI, where
the offscreen phases that need an image cannot).

1. The PATH rows are each pane's FIRST row, above Up / Refresh - on the SD
   Card tab (right under the image row) and on the Remote Explorer. They
   were the last row, under the trees.
2. "Random" picks ONE background when the app starts and keeps it. It moved
   to the next image every five seconds, for as long as the app ran.
3. "Alien Floyd's - animated background on all tabs" is OFF on a first run.
   It was flipped on with the other pygame options.
"""
import ast
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  " + repr(detail)))
    if not cond:
        FAILS.append(name)


def src(name):
    return open(os.path.join(ROOT, name), encoding="utf-8").read()


def order(text, *needles):
    """True when the needles appear in *text* once each, in this order."""
    at = [text.find(n) for n in needles]
    return all(a >= 0 for a in at) and at == sorted(at) and all(text.count(n) == 1 for n in needles)


def test_sdcard_rows():
    from zxnu_sdcard_explorer import SDCARD_IMAGE_TREE_ROW, SdCardExplorerPane
    check("the image tree's grid row has a name, and it is the third", SDCARD_IMAGE_TREE_ROW == 2)
    s = src("zxnu_sdcard_explorer.py")
    check("local column: path row, nav row, tree",
          order(s, "_local_col.addWidget(self.local_path_row_container)",
                "_local_col.addWidget(self.local_nav_row_container)",
                "_local_col.addWidget(self.local_tree_row_container, 1)"))
    check("image side: path row (0), nav row (1), tree (2)",
          "addWidget(self.image_path_row_container, 0, 1)" in s
          and "addWidget(self.image_nav_row_container, 1, 1)" in s
          and "addWidget(self.image_explorer_container, 2, 1)" in s
          and "setRowStretch(SDCARD_IMAGE_TREE_ROW, 1)" in s
          and "setRowStretch(1, 1)" not in s)
    m = src("zxnu_main.py")
    check("the transfer arrows are placed beside the tree, by name",
          "self.centralbuttonscontainer, SDCARD_IMAGE_TREE_ROW, 0)" in m
          and "addWidget(self.centralbuttonscontainer, 1, 0)" not in m)
    check("the pane class is importable with the constant", SdCardExplorerPane is not None)


def test_remote_explorer_rows():
    # The real widget, built the way its own suite builds it.
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import test_remote_explorer_widget as tw
    made = tw.make_widget()
    w = made[0] if isinstance(made, tuple) else made
    try:
        # the local pane: path row, then the nav bar, then the tree
        lay = None
        for cand in w.findChildren(QWidget):
            l = cand.layout()
            if l is not None and l.indexOf(w.local_path_row_container) >= 0:
                lay = l
                break
        check("local pane: the path row is its first row",
              lay is not None and lay.indexOf(w.local_path_row_container) == 0,
              lay.indexOf(w.local_path_row_container) if lay is not None else None)
        # the Next pane: the path box's row first, the nav bar under it
        nl = w.next_container.layout()
        first = nl.itemAt(0).layout()
        check("Next pane: the path row is its first row",
              first is not None and first.indexOf(w.next_path_edit) == 0
              and first.indexOf(w.btn_delete) == first.count() - 1,
              None if first is None else first.count())
        second = nl.itemAt(1).layout()
        check("Next pane: the nav bar (with the machine list) comes second",
              second is not None and second.indexOf(w.next_machine_combo) >= 0)
    finally:
        w.deleteLater()
    s = src("zxnu_remote_explorer.py")
    check("source: local path row added before the nav bar and the tree",
          order(s, "local_box.addWidget(self.local_path_row_container)",
                "local_box.addLayout(local_bar)", "local_box.addLayout(local_tree_row)"))
    check("source: the Next path row is inserted first",
          "next_box.insertLayout(0, next_tools)" in s and "next_box.addLayout(next_tools)" not in s)


def test_random_background():
    from zxnu_gallery import BackgroundWidget
    w = BackgroundWidget()
    try:
        check("no cycling timer is left on the widget",
              not hasattr(w, "_cycle_timer") and not hasattr(w, "_cycle_next")
              and not hasattr(BackgroundWidget, "BG_CYCLE_INTERVAL_MS")
              and not [t for t in w.findChildren(QTimer) if t.isActive()])
        paths = w.bg_paths()
        picked = w.random_bg_path()
        check("Random shows one of the discovered images (or none exist)",
              (picked in paths) if paths else picked == "", picked)
        app = QApplication.instance()
        for _ in range(20):
            app.processEvents()
        check("and it is still the same one after the event loop has turned",
              w.random_bg_path() == picked)
        if paths:
            w.set_bg_image(paths[0])
            check("a fixed image is not a random pick", w.random_bg_path() == "" and w._bg_fixed)
            w.set_bg_image("")
            check("choosing Random again picks one, once",
                  w.random_bg_path() in paths and not w._bg_fixed)
    finally:
        w.deleteLater()
    s = src("zxnu_settings_pane.py")
    check("Settings no longer follows a cycle",
          "_cycle_timer" not in s and 'ui_tr_now("(cycling)")' not in s
          and "every 5 seconds" not in s
          and "host._bg_widget.random_bg_path()" in s)


def test_alien_floyd_default():
    tree = ast.parse(src("zxnu_main.py"))
    body = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_apply_first_run_pygame_defaults":
            body = node
    check("the first-run defaults function is still there", body is not None)
    names = {n.id for n in ast.walk(body) if isinstance(n, ast.Name)} if body else set()
    consts = {n.value for n in ast.walk(body) if isinstance(n, ast.Constant) and isinstance(n.value, str)} if body else set()
    check("it does not switch Alien Floyd's background on",
          "SETTING_ALIEN_FLOYD_BG" not in names
          and "settings_alien_floyd_bg_checkbox" not in consts)
    check("it still defaults the other pygame options on",
          "SETTING_NEXTSYNC_PYGAME_MODE" in names and "SETTING_GETIT_ITEM_RETRO" in names)
    from zxnu_config import SETTING_ALIEN_FLOYD_BG
    check("an unset value reads as off", "".strip().lower() not in ("true", "1", "yes")
          and SETTING_ALIEN_FLOYD_BG == "alien_floyd_bg")


def test_inverted_selection():
    """A selected row of an explorer tree is a solid bar in the row's own
    colour - rendered offscreen and read back pixel by pixel."""
    from PySide6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
    from PySide6.QtWidgets import QAbstractItemView, QTreeView
    from zxnu_config import NEXT_CHROME_QSS, views_background_qss
    from zxnu_workers import (InvertedSelectionDelegate, bind_inverted_selection,
                              bind_tree_font_zoom)
    app = QApplication.instance()
    old = app.styleSheet()
    app.setStyleSheet(NEXT_CHROME_QSS + views_background_qss("#0d0d20"))
    view = QTreeView()
    try:
        model = QStandardItemModel(0, 2)
        green, red = QColor(0, 255, 0), QColor(255, 0, 0)
        for name, col in (("alpha.nex", green), ("beta.nex", green), ("folder", red)):
            a = QStandardItem(name)
            a.setForeground(QBrush(col))
            b = QStandardItem("12")            # no brush of its own
            model.appendRow([a, b])
        view.setModel(model)
        view.setSelectionBehavior(QAbstractItemView.SelectRows)
        view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        view.setRootIsDecorated(False)
        view.resize(400, 200)
        view.setColumnWidth(0, 200)
        bind_tree_font_zoom(view)
        check("every explorer tree's shared setup installs the delegate",
              isinstance(view.itemDelegate(), InvertedSelectionDelegate))
        view.show()
        view.selectionModel().select(
            model.index(1, 0), view.selectionModel().SelectionFlag.Select
            | view.selectionModel().SelectionFlag.Rows)
        view.selectionModel().select(
            model.index(2, 0), view.selectionModel().SelectionFlag.Select
            | view.selectionModel().SelectionFlag.Rows)
        for _ in range(10):
            app.processEvents()
        img = view.viewport().grab().toImage()

        def at(row, column, dx):
            r = view.visualRect(model.index(row, column))
            return img.pixelColor(r.right() - dx, r.center().y())

        def near(c, want):
            return abs(c.red() - want.red()) + abs(c.green() - want.green()) + abs(c.blue() - want.blue()) < 40

        check("a selected file's name cell is a solid bar of its own colour",
              near(at(1, 0, 4), green), at(1, 0, 4).name())
        check("the bar runs through a cell the model left unpainted",
              near(at(1, 1, 4), green), at(1, 1, 4).name())
        check("a selected folder's bar is the folder's colour",
              near(at(2, 0, 4), red) and near(at(2, 1, 4), red), at(2, 0, 4).name())
        check("an unselected row keeps the dark ground",
              not near(at(0, 0, 4), green) and at(0, 0, 4).lightness() < 90, at(0, 0, 4).name())
        # the text on the bar: dark on green, light on red
        def has(row, pred):
            r = view.visualRect(model.index(row, 0))
            return any(pred(img.pixelColor(x, y))
                       for x in range(r.left(), r.left() + 90)
                       for y in range(r.top(), r.bottom()))
        check("the selected file's name is written dark on its bar",
              has(1, lambda c: c.lightness() < 60))
        check("the selected folder's name is written light on its bar",
              has(2, lambda c: c.lightness() > 200))
        check("bind_inverted_selection returns a delegate parented to the tree",
              bind_inverted_selection(view).parent() is view)
    finally:
        view.deleteLater()
        app.setStyleSheet(old)
    wk = src("zxnu_workers.py")
    check("the five explorer trees still go through the one shared setup",
          sum(src(f).count("bind_tree_font_zoom(") for f in
              ("zxnu_main.py", "zxnu_nextsync_pane.py")) == 5
          and "bind_inverted_selection(tree)" in wk)


def test_flat_image_explorer_wiring():
    """The image explorer shows one folder (9.7.73): what the rest of the
    app must agree on, by source. tests/test_image_filter_selection.py
    drives the pane's own methods."""
    m = src("zxnu_main.py")
    drop = m.split("def _image_drop_target_dir(pos):", 1)[1].split("def _image_drag_acceptable", 1)[0]
    check("a drop lands in the folder row it hits, else in the folder on show",
          'shown = image_dest_dir() or "/"' in drop
          and "if path and bool(name_item.data(IMG_ISDIR_ROLE)):" in drop
          and drop.count("return shown") == 3 and 'return "/"' not in drop, drop)
    ops = src("zxnu_sdcard_ops.py")
    menu = ops.split("def image_tree_context_menu(pos):", 1)[1].split("\n    def ", 1)[0]
    check("a right-click on the '..' row is a right-click on the folder",
          'if _it is None or not (_it.data(IMG_PATH_ROLE) or ""):' in menu
          and "index = QModelIndex()" in menu)
    check("one New Folder label: it is made in the folder on show",
          "New Folder Here" not in ops)
    pane = src("zxnu_sdcard_explorer.py")
    check("the view is rooted in ONE place, which clears the selection as it moves",
          pane.count("tv.setRootIndex(") == 1
          and "sel.clearSelection()" in pane.split("def _image_show(self", 1)[1].split("\n    def ", 1)[0])
    check("the target is the folder on show, whatever is selected",
          'return getattr(self, "_image_anchor_dir", "/")'
          in pane.split("def image_dest_dir", 1)[1].split("\n    def ", 1)[0]
          and "image_selected_path" not in pane.split("def image_dest_dir", 1)[1].split("\n    def ", 1)[0])


def test_os_clipboard_paste():
    """Files copied in the operating system paste into BOTH Remote Explorer
    panes (the SD Card explorers always took them)."""
    import shutil
    import tempfile
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtGui import QGuiApplication
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import test_remote_explorer_widget as tw
    app = QApplication.instance()
    cb = QGuiApplication.clipboard()
    top = tempfile.mkdtemp(prefix="zxnu-osclip-")
    src_dir = os.path.join(top, "elsewhere")
    root = os.path.join(top, "root")
    os.makedirs(os.path.join(src_dir, "pack"))
    os.makedirs(root)
    for rel in ("one.nex", "two.tap", os.path.join("pack", "in.bin")):
        with open(os.path.join(src_dir, rel), "wb") as f:
            f.write(b"x" * 64)

    def put_on_clipboard(paths, cut=False):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        if cut:
            md.setData("Preferred DropEffect", (2).to_bytes(4, "little"))
        cb.setMimeData(md)
        for _ in range(5):
            app.processEvents()

    w, calls = tw.make_widget(local_start_dir=root)
    try:
        tw.connect_widget(w, calls)
        cb.clear()
        for _ in range(5):
            app.processEvents()
        check("nothing on the clipboard: nothing to paste", w._os_clip() is None)
        one, two, pack = (os.path.join(src_dir, n) for n in ("one.nex", "two.tap", "pack"))
        put_on_clipboard([one, pack])
        got = w._os_clip()
        check("files copied in the OS are the paste source",
              got is not None and got[1] == "copy"
              and [os.path.normcase(os.path.abspath(p)) for p in got[0]]
              == [os.path.normcase(one), os.path.normcase(pack)], got)

        # --- into the LOCAL pane: a copy into the folder it shows
        w._paste_into_local()
        check("local paste: the file and the folder were copied",
              os.path.isfile(os.path.join(root, "one.nex"))
              and os.path.isfile(os.path.join(root, "pack", "in.bin"))
              and os.path.isfile(one))
        check("a copy leaves the clipboard for a second paste", w._os_clip() is not None)

        # --- into the NEXT pane: puts and a mkdir, in the folder it shows
        w._paste_into_next()
        q = tw.drain(calls)
        puts = [c for c in q if c[0] == "put"]
        check("Next paste: one put per file, the folder made first",
              sorted(os.path.basename(c[1]) for c in puts) == ["in.bin", "one.nex"]
              and any(c[0] == "mkdir" and c[1].rstrip("/").endswith("pack") for c in q), q)
        # settle the operation so the widget is free again
        for c in q:
            if c[0] == "put":
                w.on_put_done(True, c[2] if len(c) > 2 else "")
            elif c[0] == "mkdir":
                w.on_op_done(True, "mkdir", c[1])
        for _ in range(5):
            app.processEvents()
        check("the upload ran as an ordinary operation and ended", not w._op_active)

        # --- this widget's own Copy is the newer one from then on
        w._clip = ("local", [os.path.join(root, "one.nex")], "copy")
        w._os_clip_newer = False
        check("a Copy made in the panes wins over an older OS copy", w._os_clip() is None)
        put_on_clipboard([two])
        got = w._os_clip()
        check("until the OS clipboard changes again",
              w._os_clip_newer and got is not None
              and os.path.basename(got[0][0]) == "two.tap", got)

        # --- a CUT moves, and is consumed by its paste
        put_on_clipboard([two], cut=True)
        got = w._os_clip()
        check("a cut in the OS reads as a move", got is not None and got[1] == "cut", got)
        w._paste_into_local()
        for _ in range(5):
            app.processEvents()
        check("local paste of a cut: the file moved",
              os.path.isfile(os.path.join(root, "two.tap")) and not os.path.exists(two))
        check("and the clipboard was emptied, so a second paste moves nothing",
              w._os_clip() is None or not QGuiApplication.clipboard().mimeData().hasUrls())

        # --- a path that no longer exists is not a paste source
        w._clip = None
        put_on_clipboard([os.path.join(src_dir, "gone.bin")])
        check("files that are gone are ignored", w._os_clip() is None)
    finally:
        cb.clear()
        w.deleteLater()
        shutil.rmtree(top, ignore_errors=True)
    s = src("zxnu_remote_explorer.py")
    check("both context menus offer Paste for OS files",
          s.count("act_paste.setEnabled(bool(self._clip) or self._os_clip() is not None)") == 2)


def main():
    QApplication.instance() or QApplication(sys.argv)
    test_sdcard_rows()
    test_os_clipboard_paste()
    test_flat_image_explorer_wiring()
    test_inverted_selection()
    test_remote_explorer_rows()
    test_random_background()
    test_alien_floyd_default()
    print("RESULT:", "ALL CHECKS PASSED" if not FAILS else "FAILED: %s" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
