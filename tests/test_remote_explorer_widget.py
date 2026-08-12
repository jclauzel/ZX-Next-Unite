"""Offscreen widget-layer tests for zxnu_remote_explorer.RemoteExplorerWidget
(the NextSync tab's dual-pane local <-> Next file manager).

The worker side of the Remote Explorer is covered by test_remote_listen.py;
this file covers the WIDGET side, which previously only the app-wide offscreen
smoke test touched. The widget's seam makes that cheap: every remote command
leaves through one injected ``enqueue`` callable and every result arrives via
plain ``on_*`` methods, so a real QWidget under QT_QPA_PLATFORM=offscreen can
be driven end-to-end with a recorded queue and hand-fed worker replies — no
socket, no worker thread, no display. QInputDialog/QMessageBox are replaced by
scripted fakes inside the module's namespace, so nothing ever blocks.

Covers: connection state + drive combo (getdrives/extra drives/free space),
listing rendering + both panes' sort persistence, navigation (up/double-click/
ls-failure fallback), the operation lifecycle (_run_op/step/cancel/disconnect/
background rcpy overlay), transfers (get/put incl. recursive folder upload),
copy/cut/paste in all four directions (with move markers deleting sources
only after confirmation), the rcpy free-space precheck, sync-root handling,
local file operations (new folder/rename/delete/copy-paste/zip round-trip)
and drag & drop entry points."""
import logging
import faulthandler
import os
import shutil
import sys
import tempfile
import time

# A native crash inside Qt loses the BUFFERED stdout of this process (a CI
# run died exactly that way, leaving no clue which check it was in); the
# faulthandler traceback goes to stderr, which is unbuffered, so the crash
# point survives into the log.
faulthandler.enable()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The compact-button checks print translated labels ("W górę", "Вверх"), which
# a cp1252 console cannot encode — without this the suite dies in its own
# print() rather than on anything it is testing.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PySide6.QtCore import QItemSelectionModel, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import zxnu_remote_explorer as rex
from zxnu_remote_explorer import (RemoteExplorerWidget, RE_PATH_ROLE,
                                  _ext_type_text, _human_size, _norm_remote_dir,
                                  _parse_re_sort, _posix_join, _re_drive_of,
                                  _re_norm_dir, _re_sort_to_str)

ok = True


def check(label, cond, detail=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'} {label}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        ok = False


# ---------------------------------------------------------------------------
# scripted stand-ins for the module's modal dialogs
# ---------------------------------------------------------------------------
class FakeInput:
    """Replaces zxnu_remote_explorer.QInputDialog: getText pops scripted
    (text, ok) answers; an empty script answers a cancel."""
    queue = []

    @classmethod
    def getText(cls, *a, **k):
        return cls.queue.pop(0) if cls.queue else ("", False)


class FakeMsg:
    """Replaces zxnu_remote_explorer.QMessageBox: question/warning return the
    scripted ``answer``; critical/information record their text."""
    Yes = QMessageBox.Yes
    No = QMessageBox.No
    Cancel = QMessageBox.Cancel
    StandardButton = QMessageBox.StandardButton
    answer = QMessageBox.Yes
    criticals = []
    infos = []

    @classmethod
    def question(cls, *a, **k):
        return cls.answer

    @classmethod
    def warning(cls, *a, **k):
        return cls.answer

    @classmethod
    def critical(cls, parent, title, text, *a, **k):
        cls.criticals.append(text)
        return QMessageBox.StandardButton.Close

    @classmethod
    def information(cls, parent, title, text, *a, **k):
        cls.infos.append(text)


rex.QInputDialog = FakeInput
rex.QMessageBox = FakeMsg


# ---------------------------------------------------------------------------
# harness helpers
# ---------------------------------------------------------------------------
def make_widget(**kw):
    """A RemoteExplorerWidget wired to recorders for every host callback."""
    calls = {"q": [], "log": [], "toasts": [], "sync_root": [],
             "remote_cwd": [], "sorts": [], "extra_drives": []}
    w = RemoteExplorerWidget(
        enqueue=calls["q"].append,
        local_start_dir=kw.get("local_start_dir"),
        log=calls["log"].append,
        drain=kw.get("drain"),
        on_sync_root_changed=calls["sync_root"].append,
        remote_start_dir=kw.get("remote_start_dir"),
        on_remote_cwd_changed=calls["remote_cwd"].append,
        local_sort=kw.get("local_sort"),
        next_sort=kw.get("next_sort"),
        on_sort_changed=lambda which, v: calls["sorts"].append((which, v)),
        on_toast=lambda t, m, variant="red": calls["toasts"].append((t, m, variant)),
        extra_drives=kw.get("extra_drives"),
        on_extra_drives_changed=calls["extra_drives"].append,
        emulator_entries=kw.get("emulator_entries"))
    return w, calls


def connect_widget(w, calls, drives=("C", "M"), listing=()):
    """Bring a widget to the usual ready state: connected on drive C, cwd /,
    with ``listing`` shown. Clears the recorders afterwards."""
    w.on_connected()
    w.on_drives("C", list(drives))
    w.on_listing("/", list(listing))
    for rec in calls.values():
        rec.clear()


def drain(calls):
    q = list(calls["q"])
    calls["q"].clear()
    return q


def fwd(p):
    """Forward-slashed form of a local path, as the widget emits them (the
    sync root, QFileSystemModel.filePath and QUrl.toLocalFile all use "/";
    recursive-upload walks use the native separator)."""
    return p.replace("\\", "/")


def samepath(a, b):
    """True when two paths name the same file. _local_dir() hands back
    forward-slashed paths while os.path.join uses the native separator, so a
    raw string compare fails on Windows for paths that are in fact identical."""
    return (os.path.normcase(os.path.realpath(a))
            == os.path.normcase(os.path.realpath(b)))


def normq(q):
    """A queue with every string normalised to forward slashes, so command
    comparisons are separator-agnostic."""
    return [tuple(fwd(x) if isinstance(x, str) else x for x in c) for c in q]


def logged(calls, needle):
    return any(needle in s for s in calls["log"])


def next_names(w):
    return [w.next_model.item(r, 0).text()
            for r in range(w.next_model.rowCount())]


def select_next(w, *names):
    sm = w.next_view.selectionModel()
    sm.clearSelection()
    for r in range(w.next_model.rowCount()):
        if w.next_model.item(r, 0).text() in names:
            sm.select(w.next_model.index(r, 0),
                      QItemSelectionModel.Select | QItemSelectionModel.Rows)


def select_local(w, *paths):
    """Select real files/folders in the local pane, waiting out
    QFileSystemModel's asynchronous directory population."""
    sm = w.local_view.selectionModel()
    sm.clearSelection()
    for p in paths:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            six = w.local_model.index(p)
            if six.isValid():
                ix = w.local_proxy.mapFromSource(six)
                if ix.isValid():
                    sm.select(ix, QItemSelectionModel.Select
                              | QItemSelectionModel.Rows)
                    break
            root = w.local_model.index(w._browse_dir())
            if w.local_model.canFetchMore(root):
                w.local_model.fetchMore(root)
            QApplication.processEvents()
            time.sleep(0.01)
        else:
            raise AssertionError(f"local row never appeared: {p}")


def drop_event(mime, pos=(10, 9000)):
    return QDropEvent(QPointF(*pos), Qt.CopyAction, mime,
                      Qt.LeftButton, Qt.NoModifier)


def url_mime(*paths):
    m = QMimeData()
    m.setUrls([QUrl.fromLocalFile(p) for p in paths])
    return m


TMP = tempfile.mkdtemp(prefix="rexw_")


def tdir(name):
    p = os.path.join(TMP, name)
    os.makedirs(p, exist_ok=True)
    return p


def tfile(reldir, name, data=b"x"):
    p = os.path.join(reldir, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_pure_helpers():
    check("human size", [_human_size(None), _human_size(512), _human_size(7000),
                         _human_size(4 * 1024 * 1024)]
          == ["", "512 B", "6.8 K", "4.0 M"])
    check("ext type", [_ext_type_text("a.tap"), _ext_type_text("a.tap.zip"),
                       _ext_type_text("noext")] == ["tap", "tap", ""])
    check("posix join", _posix_join("/games/", "lev") == "/games/lev"
          and _posix_join("", "x") == "/x")
    check("drive of", [_re_drive_of("M:/x"), _re_drive_of("/x"),
                       _re_drive_of("")] == ["M", "", ""])
    check("norm dir", [_re_norm_dir("M:"), _re_norm_dir(""),
                       _re_norm_dir("/games")] == ["M:/", "/", "/games"])
    check("norm remote", [_norm_remote_dir(None), _norm_remote_dir("games"),
                          _norm_remote_dir("m:games\\lev")]
          == ["/", "/games", "M:/games/lev"])
    check("sort parse fallback", _parse_re_sort("bogus") == ("name", Qt.AscendingOrder))
    check("sort round-trip",
          _parse_re_sort(_re_sort_to_str("size", Qt.DescendingOrder))
          == ("size", Qt.DescendingOrder))
    check("fmt free", [RemoteExplorerWidget._fmt_free(512),
                       RemoteExplorerWidget._fmt_free(1572864)]
          == ["512 bytes", "1.5 MB"])
    colors = {RemoteExplorerWidget._free_color(n * 1024 * 1024)
              for n in (50, 150, 250)}
    check("free color traffic light", len(colors) == 3
          and RemoteExplorerWidget._free_color(250 * 1024 * 1024) == "#2fb344")


def test_initial_and_connection_state():
    root = tdir("init_root")
    w, calls = make_widget(local_start_dir=root)
    check("starts disconnected", not w._connected
          and not w.btn_to_next.isEnabled() and not w.next_view.isEnabled())
    check("waiting label", "waiting for .sync5" in w.next_path_label.text())
    check("Next path box empty and disabled while disconnected",
          not w.next_path_edit.isEnabled() and w.next_path_edit.text() == "")
    check("start dir committed as sync root",
          w.sync_root() == root.replace("\\", "/")
          and calls["sync_root"] == [root.replace("\\", "/")])
    check("path box shows sync root",
          w.local_path_edit.text() == root.replace("\\", "/"))

    w.on_connected()
    check("connect enables the pane", w._connected and w.btn_to_next.isEnabled()
          and w.next_path_edit.isEnabled())
    check("connect asks drives then lists /",
          drain(calls) == [("drives",), ("ls", "/")])

    w.on_drives("C", ["C", "M"])
    combo = [w.next_drive_combo.itemText(i)
             for i in range(w.next_drive_combo.count())]
    check("drive combo filled", combo == ["C", "M"]
          and w.next_drive_combo.isEnabled() and w.next_drive_add.isEnabled())
    check("drives trigger a free-space query", drain(calls) == [("free", "C")])
    check("drives logged", logged(calls, "Next drives: C M"))

    w.on_free_space("C", 300 * 1024 * 1024)
    check("free space in top label", "300.0 MB free" in w.next_path_label.text()
          and "#2fb344" in w.next_path_label.text())
    check("the path lives in the bottom box, not the top label",
          w.next_path_edit.text() == "/")
    w.on_free_space("C", None)
    check("failed free query clears the figure",
          w.next_path_label.text() == "Next: connected")

    w.on_disconnected()
    check("disconnect clears pane", not w._connected
          and w.next_model.rowCount() == 0
          and not w.next_drive_combo.isEnabled()
          and w.next_drive_combo.count() == 0
          and w.next_path_edit.text() == ""
          and not w.next_path_edit.isEnabled())

    # Old dot: no getdrives reply -> lone implicit drive, switcher disabled.
    w.on_connected()
    w.on_drives("", [])
    check("old dot: lone drive, disabled combo",
          [w.next_drive_combo.itemText(i)
           for i in range(w.next_drive_combo.count())] == ["C"]
          and not w.next_drive_combo.isEnabled()
          and not w.next_drive_add.isEnabled())
    # Distrusted current letter (not in the reported list) falls back to C.
    w.on_drives("X", ["C", "M"])
    check("bogus current drive distrusted", w._default_drive == "C")


def test_idle_status_provider():
    # The host can plug a callable that supplies the disconnected "Next:"
    # label (sync-root / start-server / waiting stages); without one the
    # classic waiting text stays. Connected state is never touched.
    root = tdir("idle_status_root")
    w, _calls = make_widget(local_start_dir=root)
    state = {"text": "Next: Select a sync root folder"}
    w.set_idle_status_provider(lambda: state["text"])
    check("provider text applied on install",
          w.next_path_label.text() == "Next: Select a sync root folder")
    state["text"] = "Next: Start NextSync server"
    w.refresh_idle_status()
    check("refresh follows the host state",
          w.next_path_label.text() == "Next: Start NextSync server")
    w.on_connected()
    w.on_listing("/", [])
    connected_label = w.next_path_label.text()
    w.refresh_idle_status()
    check("refresh is a no-op while connected",
          w.next_path_label.text() == connected_label
          and "Next: Start" not in connected_label)
    state["text"] = ""      # provider yielding nothing -> classic fallback
    w.on_disconnected()
    check("empty provider text falls back to the waiting label",
          "waiting for .sync5" in w.next_path_label.text())


def test_idle_details_provider():
    # The host can plug a second callable whose multi-line host/IP block is
    # shown over the empty Next pane while disconnected (the address to give
    # '.sync5' on the Next); it disappears on connect and returns after a
    # disconnect. An empty text means no overlay at all.
    root = tdir("idle_details_root")
    w, _calls = make_widget(local_start_dir=root)
    state = {"text": "Running on host:\n    <pc>\nPrimary IP:\n    1.2.3.4"}
    w.set_idle_details_provider(lambda: state["text"])
    check("details overlay applied on install",
          w._idle_info_overlay is not None
          and "Primary IP" in w._idle_info_overlay.text())
    w.on_connected()
    w.on_listing("/", [])
    check("details overlay removed while connected",
          w._idle_info_overlay is None)
    w.on_disconnected()
    check("details overlay returns after disconnect",
          w._idle_info_overlay is not None
          and "1.2.3.4" in w._idle_info_overlay.text())
    state["text"] = ""
    w.refresh_idle_status()
    check("empty details text removes the overlay",
          w._idle_info_overlay is None)


def test_listing_and_rendering():
    w, calls = make_widget(local_start_dir=tdir("lst_root"))
    connect_widget(w, calls)
    w.on_listing("/", [(True, 0, "GAMES"), (False, 1234, "boot.bas"),
                       (False, 100, "."), (False, 100, "..")])
    check("listing rows (no .. at root, dot rows dropped)",
          next_names(w) == ["boot.bas", "GAMES"])
    row = next_names(w).index("boot.bas")
    check("file row type/size", w.next_model.item(row, 1).text() == "bas"
          and w.next_model.item(row, 2).text() == "1.2 K")
    grow = next_names(w).index("GAMES")
    check("dir row type/size", w.next_model.item(grow, 1).text() == "DIR"
          and w.next_model.item(grow, 2).text() == "")
    check("cwd reported to host once", calls["remote_cwd"] == [])  # "/" is start

    w.on_listing("/GAMES", [(False, 10, "a.tap")])
    check("subdir listing pins ..", next_names(w) == ["..", "a.tap"])
    check("subdir cwd persisted", calls["remote_cwd"] == ["/GAMES"])
    check("path box follows", w.next_path_edit.text() == "/GAMES")

    # Colours: push a custom file colour, the existing row re-tints in place.
    w.set_item_colors({"file_name": QColor("#804020"), "bogus": QColor("red"),
                       "dir_name": None})
    arow = next_names(w).index("a.tap")
    check("set_item_colors re-tints rows",
          w.next_model.item(arow, 0).foreground().color().name() == "#804020")


def test_navigation():
    w, calls = make_widget(local_start_dir=tdir("nav_root"))
    connect_widget(w, calls, listing=[(True, 0, "GAMES")])
    ix = w.next_model.item(next_names(w).index("GAMES"), 0).index()
    w._next_double_clicked(ix)
    check("double-click dir navigates",
          w._cwd == "/GAMES" and drain(calls) == [("ls", "/GAMES")])
    w.on_listing("/GAMES", [(True, 0, "LEV")])
    calls["q"].clear()
    up_ix = w.next_model.item(0, 0).index()   # the ".." row
    check("up row flagged", w.next_model.item(0, 0).data(RE_PATH_ROLE) == "..")
    w._next_double_clicked(up_ix)
    check("double-click .. goes up",
          w._cwd == "/" and drain(calls) == [("ls", "/")])
    w._next_up()
    check("up at root is a no-op", drain(calls) == [])

    w.on_listing("M:/games", [(True, 0, "sub")])
    calls["q"].clear()
    check("drive cwd not at root", not w._at_drive_root())
    w._next_up()
    check("up on a drive path stops at its root",
          w._cwd == "M:/" and drain(calls) == [("ls", "M:/")]
          and w._at_drive_root())

    # _in_cwd drives the "refresh only the visible folder" logic.
    check("_in_cwd drive root", w._in_cwd("M:/file.tap")
          and not w._in_cwd("M:/sub/file.tap") and not w._in_cwd("/file.tap"))
    w._cwd = "/games"
    check("_in_cwd nested", w._in_cwd("/games/x.tap")
          and not w._in_cwd("/games/sub/y.tap") and not w._in_cwd("/other"))

    # Typed navigation: the path box is the fourth way to move (after
    # double-click, Up and the drive combo) — ENTER lists the typed folder.
    calls["q"].clear()
    w.next_path_edit.setText("games/sub")
    w._on_next_path_edit()
    check("typed relative path navigates absolute",
          w._cwd == "/games/sub" and drain(calls) == [("ls", "/games/sub")])
    w.next_path_edit.setText("M:data\\deep")
    w._on_next_path_edit()
    check("typed drive path normalises and navigates",
          w._cwd == "M:/data/deep" and drain(calls) == [("ls", "M:/data/deep")])
    w.next_path_edit.setText("D:/nope")
    w._on_next_path_edit()
    check("unknown drive letter refused (dot-crash guard)",
          w._cwd == "M:/data/deep" and drain(calls) == []
          and logged(calls, "Unknown Next drive D")
          and w.next_path_edit.text() == "M:/data/deep")
    w.next_path_edit.setText("   ")
    w._on_next_path_edit()
    check("blank entry restores the cwd",
          drain(calls) == [] and w.next_path_edit.text() == "M:/data/deep")


def test_drive_switching():
    w, calls = make_widget(local_start_dir=tdir("drv_root"),
                           extra_drives="e")
    connect_widget(w, calls)
    combo = [w.next_drive_combo.itemText(i)
             for i in range(w.next_drive_combo.count())]
    check("saved extra drive merged into combo", combo == ["C", "E", "M"])

    w.next_drive_combo.setCurrentText("M")
    check("combo switch jumps to drive root", w._cwd == "M:/"
          and drain(calls) == [("ls", "M:/"), ("free", "M")])
    w.on_listing("M:/", [])
    check("combo follows cwd", w.next_drive_combo.currentText() == "M")

    # "+ Drive": invalid letter, then declined, then confirmed.
    calls["q"].clear()
    FakeInput.queue = [("q", True)]
    w._add_drive_clicked()
    check("+drive rejects invalid letter",
          logged(calls, "single letter C..P") and drain(calls) == [])
    FakeInput.queue = [("d", True)]
    FakeMsg.answer = QMessageBox.No
    w._add_drive_clicked()
    check("+drive declined does nothing", drain(calls) == []
          and calls["extra_drives"] == [])
    FakeInput.queue = [("d", True)]
    FakeMsg.answer = QMessageBox.Yes
    w._add_drive_clicked()
    check("+drive confirmed persists and switches",
          calls["extra_drives"] == ["DE"] and w._cwd == "D:/"
          and ("ls", "D:/") in drain(calls))
    FakeInput.queue = [("m", True)]
    calls["q"].clear()
    w._add_drive_clicked()
    check("+drive with a known letter just switches",
          w._cwd == "M:/" and ("ls", "M:/") in drain(calls)
          and calls["extra_drives"] == ["DE"])


def test_ls_failed_fallback():
    w, calls = make_widget(local_start_dir=tdir("lsf_root"))
    connect_widget(w, calls)
    w.on_listing("M:/games", [])
    calls["toasts"].clear()
    calls["q"].clear()
    w.on_ls_failed("M:/games")
    check("lost folder falls back to its drive root",
          w._cwd == "M:/" and drain(calls) == [("ls", "M:/")]
          and calls["toasts"] and calls["toasts"][0][2] == "yellow")
    w.on_ls_failed("M:/")
    check("lost drive root falls back to /",
          w._cwd == "/" and drain(calls) == [("ls", "/")])
    calls["toasts"].clear()
    w.on_ls_failed("/")
    check("lost / gives up quietly",
          drain(calls) == [] and calls["toasts"] == [])


def test_sorting():
    entries = [(False, 2048, "b.tap"), (True, 0, "AAA"),
               (False, 10, "a.zzz"), (True, 0, "zdir")]
    w, calls = make_widget(local_start_dir=tdir("srt_root"))
    connect_widget(w, calls)
    w.on_listing("/", entries)
    check("default sort name asc",
          next_names(w) == ["a.zzz", "AAA", "b.tap", "zdir"])
    w._on_next_header_clicked(0)
    check("name header click flips to desc",
          next_names(w) == ["zdir", "b.tap", "AAA", "a.zzz"]
          and ("next", "name:desc") in calls["sorts"])
    w._on_next_header_clicked(2)
    check("size sort groups dirs first",
          next_names(w) == ["AAA", "zdir", "a.zzz", "b.tap"]
          and ("next", "size:asc") in calls["sorts"])
    w._on_next_header_clicked(2)
    check("size sort desc reverses",
          next_names(w) == ["b.tap", "a.zzz", "zdir", "AAA"])

    # Restored sorts: indicators applied, nothing re-saved.
    w2, calls2 = make_widget(local_start_dir=tdir("srt2_root"),
                             next_sort="size:desc", local_sort="type:desc")
    h = w2.next_view.header()
    check("restored next sort indicator", h.sortIndicatorSection() == 2
          and h.sortIndicatorOrder() == Qt.DescendingOrder)
    lh = w2.local_view.header()
    check("restored local sort indicator", lh.sortIndicatorSection() == 2
          and lh.sortIndicatorOrder() == Qt.DescendingOrder)
    check("restoring saved nothing", calls2["sorts"] == [])
    w2.local_view.sortByColumn(1, Qt.AscendingOrder)
    check("local header click persists",
          calls2["sorts"] == [("local", "size:asc")])


def test_op_lifecycle_new_folder_rename_delete():
    w, calls = make_widget(local_start_dir=tdir("op_root"))
    connect_widget(w, calls,
                   listing=[(True, 0, "GAMES"), (False, 1234, "boot.bas")])

    # New Folder: happy path.
    FakeInput.queue = [("Games2", True)]
    w._new_folder()
    check("new folder queues mkdir and blocks the UI",
          drain(calls) == [("mkdir", "/Games2")] and w._op_active
          and not w.isEnabled())
    w.on_op_done(True, "mkdir", "/Games2")
    check("op ends with one refresh + free re-read",
          drain(calls) == [("ls", "/"), ("free", "C")] and not w._op_active
          and w.isEnabled() and calls["toasts"] == [])

    # New Folder: cancelled prompt starts nothing.
    FakeInput.queue = [("", False)]
    w._new_folder()
    check("cancelled new folder is a no-op",
          drain(calls) == [] and not w._op_active)

    # New Folder: a deliberate mkdir failure IS toasted.
    FakeInput.queue = [("bad", True)]
    w._new_folder()
    w.on_op_done(False, "mkdir", "/bad")
    check("failed new folder toasts red",
          calls["toasts"] and "mkdir failed: bad" in calls["toasts"][0][1]
          and calls["toasts"][0][2] == "red")
    calls["toasts"].clear()

    # Rename: exactly one selection, name validation, then the command.
    calls["q"].clear()
    select_next(w, "boot.bas")
    FakeInput.queue = [("a/b", True)]
    w._rename_selected()
    check("rename rejects a path", logged(calls, "name only")
          and drain(calls) == [] and not w._op_active)
    FakeInput.queue = [("boot2.bas", True)]
    w._rename_selected()
    check("rename queues the command",
          drain(calls) == [("rename", "/boot.bas", "/boot2.bas")])
    w.on_op_done(True, "rename", "/boot2.bas")
    calls["q"].clear()
    select_next(w, "boot.bas", "GAMES")
    w._rename_selected()
    check("rename needs exactly one item",
          logged(calls, "exactly one") and drain(calls) == [])

    # Delete: declined, then confirmed (dir -> rmtree, file -> rm).
    FakeMsg.answer = QMessageBox.No
    w._delete_selected()
    check("declined delete is a no-op", drain(calls) == [] and not w._op_active)
    FakeMsg.answer = QMessageBox.Yes
    w._delete_selected()
    q = drain(calls)
    check("delete queues rm for files, rmtree for dirs",
          sorted(q) == [("rm", "/boot.bas"), ("rmtree", "/GAMES")])
    w.on_op_done(True, "rm", "/boot.bas")
    check("op waits for every reply", w._op_active)
    w.on_op_done(True, "delete", "/GAMES")
    check("delete op ends after both replies", not w._op_active)


def test_get_size_dialog():
    w, calls = make_widget(local_start_dir=tdir("sz_root"))
    connect_widget(w, calls, listing=[(True, 0, "GAMES")])
    select_next(w, "GAMES")
    w._get_size_selected()
    check("get size queues fsize", drain(calls) == [("fsize", "/GAMES")])
    FakeMsg.infos.clear()
    w.on_op_done(True, "size", "/GAMES")
    w.on_fsize("/GAMES", {"bytes": 2048, "files": 3, "dirs": 1})
    check("fsize result pops the info dialog",
          FakeMsg.infos and "2,048" in FakeMsg.infos[0] and not w._op_active)
    FakeMsg.infos.clear()
    w.on_fsize("/GAMES", None)
    check("failed fsize shows no dialog", FakeMsg.infos == [])


def test_transfers_get_put():
    root = tdir("xfer_root")
    w, calls = make_widget(local_start_dir=root)
    connect_widget(w, calls,
                   listing=[(True, 0, "GAMES"), (False, 9, "boot.bas")])

    # Download: the folder's local dir is pre-created so empty dirs show.
    select_next(w, "GAMES", "boot.bas")
    w._get_selected()
    q = drain(calls)
    check("get queues one get per item",
          sorted(normq(q)) == sorted([("get", "/GAMES", fwd(root)),
                                      ("get", "/boot.bas", fwd(root))]))
    check("get pre-creates the folder locally",
          os.path.isdir(os.path.join(root, "GAMES")))
    w.on_got("/boot.bas", os.path.join(root, "boot.bas"))
    w.on_got("/GAMES", os.path.join(root, "GAMES"))
    check("get op ends", not w._op_active)

    # Upload: file + recursive folder (mkdir before the puts into it).
    up = tdir("xfer_root/updir")
    fa = tfile(root, "a.txt")
    fx = tfile(up, "x.txt")
    sub = os.path.join(up, "sub")
    os.makedirs(sub, exist_ok=True)
    fy = tfile(sub, "y.txt")
    select_local(w, fa, up)
    calls["q"].clear()
    w._put_selected()
    q = drain(calls)
    nq = normq(q)
    check("put queues files and the folder tree",
          sorted(nq) == sorted([("put", fwd(fa), "/"), ("mkdir", "/updir"),
                                ("put", fwd(fx), "/updir/"),
                                ("mkdir", "/updir/sub"),
                                ("put", fwd(fy), "/updir/sub/")]))
    check("mkdir precedes its puts",
          nq.index(("mkdir", "/updir")) < nq.index(("put", fwd(fx), "/updir/"))
          and nq.index(("mkdir", "/updir/sub"))
          < nq.index(("put", fwd(fy), "/updir/sub/")))
    for cmd in q:
        if cmd[0] == "put":
            w.on_put_done(True, cmd[2] + os.path.basename(cmd[1]))
        else:
            w.on_op_done(True, "mkdir", cmd[1])
    check("put op ends after all replies", not w._op_active)

    # A failed put during the op becomes one red toast at the end.
    calls["toasts"].clear()
    select_local(w, fa)
    w._put_selected()
    calls["q"].clear()
    w.on_put_done(False, "/a.txt")
    check("failed upload toasts once at op end", not w._op_active
          and calls["toasts"] and "Upload failed: a.txt" in calls["toasts"][0][1])

    # Empty selection only logs.
    w.local_view.selectionModel().clearSelection()
    calls["q"].clear()
    w._put_selected()
    check("put with no selection logs", logged(calls, "Select local file")
          and drain(calls) == [])


def test_send_local_paths():
    root = tdir("send_root")
    fa = tfile(root, "send.txt")
    w, calls = make_widget(local_start_dir=root)
    check("send offline", w.send_local_paths([fa]) == "offline")
    connect_widget(w, calls)
    check("send with nothing that exists",
          w.send_local_paths([os.path.join(root, "nope")]) == "empty")
    outcome = []
    check("send queues", w.send_local_paths(
        [fa], on_done=lambda ok_, fails: outcome.append((ok_, fails))) == "queued")
    check("send while busy", w.send_local_paths([fa]) == "busy")
    w.on_put_done(True, "/send.txt")
    check("send reports success", outcome == [(True, [])])


def test_copy_cut_paste_next_to_local():
    root = tdir("n2l_root")
    w, calls = make_widget(local_start_dir=root)
    connect_widget(w, calls,
                   listing=[(True, 0, "GAMES"), (False, 9, "boot.bas")])

    # Copy -> paste: a plain download.
    select_next(w, "boot.bas")
    w._copy_next("copy")
    check("copy next fills the clip", w._clip == ("next", [("/boot.bas", False)],
                                                  "copy"))
    w._paste_into_local()
    check("paste downloads",
          normq(drain(calls)) == [("get", "/boot.bas", fwd(root))])
    w.on_got("/boot.bas", os.path.join(root, "boot.bas"))
    calls["q"].clear()               # drop the end-of-op ls + free re-read

    # Cut -> paste: download + marker, source removed only after the marker.
    select_next(w, "boot.bas")
    w._copy_next("cut")
    w._paste_into_local()
    q = drain(calls)
    check("move queues get then marker",
          normq(q)[0] == ("get", "/boot.bas", fwd(root)) and q[1][0] == "mark"
          and len(w._cut_jobs) == 1)
    token = q[1][1]
    w.on_got("/boot.bas", os.path.join(root, "boot.bas"))
    w.on_marked(token)
    check("confirmed move deletes the Next source",
          drain(calls) == [("rm", "/boot.bas")] and w._cut_jobs == [])
    w.on_op_done(True, "rm", "/boot.bas")
    check("move op ends", not w._op_active)
    calls["q"].clear()

    # A transfer error before the marker keeps the source.
    calls["toasts"].clear()
    select_next(w, "boot.bas")
    w._copy_next("cut")
    w._paste_into_local()
    q = drain(calls)
    w.on_error("wire broke")
    w.on_marked(q[1][1])             # op ends here -> a trailing ls + free
    check("failed move keeps the Next source",
          not any(c[0] in ("rm", "rmdir") for c in drain(calls))
          and logged(calls, "kept /boot.bas") and not w._op_active
          and w._cut_jobs == [])
    check("failure toasted", calls["toasts"]
          and "wire broke" in calls["toasts"][0][1])

    # Cut folder: the downloaded copy's layout drives a bottom-up remote wipe.
    select_next(w, "GAMES")
    w._copy_next("cut")
    w._paste_into_local()
    q = drain(calls)
    gcopy = os.path.join(root, "GAMES")
    tfile(gcopy, "f1.txt")
    inner = os.path.join(gcopy, "inner")
    os.makedirs(inner, exist_ok=True)
    tfile(inner, "f2.txt")
    w.on_got("/GAMES", gcopy)
    w.on_marked(q[1][1])
    check("folder move mirrors the copy bottom-up",
          drain(calls) == [("rm", "/GAMES/inner/f2.txt"),
                           ("rmdir", "/GAMES/inner"),
                           ("rm", "/GAMES/f1.txt"), ("rmdir", "/GAMES")])
    for op, p in (("rm", "/GAMES/inner/f2.txt"), ("rmdir", "/GAMES/inner"),
                  ("rm", "/GAMES/f1.txt"), ("rmdir", "/GAMES")):
        w.on_op_done(True, op, p)
    check("folder move op ends", not w._op_active)


def test_copy_cut_paste_local_to_next():
    root = tdir("l2n_root")
    w, calls = make_widget(local_start_dir=root)
    connect_widget(w, calls)
    fa = tfile(root, "up.bin", b"DATA")
    select_local(w, fa)
    w._copy_local("copy")
    check("copy local fills the clip",
          w._clip == ("local", [fwd(fa)], "copy"))
    calls["q"].clear()
    w._paste_into_next()
    check("paste uploads", normq(drain(calls)) == [("put", fwd(fa), "/")])
    w.on_put_done(True, "/up.bin")

    # Cut -> paste: upload + marker, local file deleted only after the marker.
    fmv = tfile(root, "move.bin", b"MOVE")
    select_local(w, fmv)
    w._copy_local("cut")
    calls["q"].clear()
    w._paste_into_next()
    q = drain(calls)
    check("local move queues put then marker",
          normq(q)[0] == ("put", fwd(fmv), "/") and q[1][0] == "mark"
          and w._clip is None)
    w.on_put_done(True, "/move.bin")
    w.on_marked(q[1][1])
    check("confirmed local move removes the file",
          not os.path.exists(fmv) and logged(calls, "removed local")
          and not w._op_active)

    # Failed upload keeps the local source.
    fkeep = tfile(root, "keep.bin")
    select_local(w, fkeep)
    w._copy_local("cut")
    calls["q"].clear()
    w._paste_into_next()
    q = drain(calls)
    w.on_put_done(False, "/keep.bin")
    w.on_marked(q[1][1])
    check("failed local move keeps the file",
          os.path.exists(fkeep) and not w._op_active)

    # Cut NEXT items cannot paste back into the Next.
    w.on_listing("/", [(False, 9, "boot.bas")])
    select_next(w, "boot.bas")
    w._copy_next("cut")
    calls["q"].clear()
    w._paste_into_next()
    check("cut next items refuse a next paste",
          drain(calls) == [] and logged(calls, "paste into the local pane"))


def test_rcpy_precheck_and_background():
    root = tdir("rcpy_root")
    w, calls = make_widget(local_start_dir=root)
    connect_widget(w, calls, listing=[(True, 0, "GAMES")])

    # Pasting into the source's own folder is refused outright.
    select_next(w, "GAMES")
    w._copy_next("copy")
    calls["q"].clear()
    w._paste_into_next()
    check("self-paste skipped", drain(calls) == [] and w._precheck is None
          and logged(calls, "destination equals or is inside the source"))

    # Into another folder: rfsize precheck, then the rcpy with totals.
    w.on_listing("/dst", [])
    calls["q"].clear()
    w._paste_into_next()
    check("precheck measures the source and re-reads free space",
          drain(calls) == [("fsize", "/GAMES"), ("free", "C")]
          and w._precheck is not None)
    w.on_fsize("/GAMES", {"bytes": 1000, "files": 3, "dirs": 1})
    w.on_op_done(True, "size", "/GAMES")     # stage-1 op ends
    calls["q"].clear()
    w.on_free_space("C", 10_000_000)
    q = drain(calls)
    check("fitting copy launches rcpy",
          ("rcpy", "/GAMES", "/dst/GAMES") in q and w._op_active)

    # Background mode: the dialog button hands the op to the overlay.
    w.on_op_progress("copy", "/dst/GAMES/f1.bin")
    w._on_op_cancel_clicked()
    check("backgrounded: local pane free, Next pane covered",
          w.isEnabled() and w._op_background
          and not w.next_container.isEnabled()
          and w._next_overlay is not None
          and "Remote copy in progress" in w._next_overlay.text())
    calls["toasts"].clear()
    w.on_op_done(True, "copy", "/dst/GAMES")
    check("backgrounded op ends with a green toast",
          not w._op_active and w._next_overlay is None
          and w.next_container.isEnabled()
          and any(t[2] == "green" and "Remote copy complete" in t[0]
                  for t in calls["toasts"]))

    # Not enough space: the copy is refused before any rcpy is queued.
    w.on_listing("/dst2", [])
    calls["q"].clear()
    FakeMsg.criticals.clear()
    w._paste_into_next()
    w.on_fsize("/GAMES", {"bytes": 10_000_000, "files": 3, "dirs": 1})
    w.on_op_done(True, "size", "/GAMES")
    calls["q"].clear()
    w.on_free_space("C", 1000)
    check("too-big copy refused",
          not any(c[0] == "rcpy" for c in drain(calls))
          and FakeMsg.criticals and "not started" in FakeMsg.criticals[0]
          and logged(calls, "rcpy refused"))


def test_cancel_and_disconnect_mid_op():
    root = tdir("cancel_root")
    drained = {"n": 0}

    def drain_hook():
        drained["n"] += 1
        return 1
    w, calls = make_widget(local_start_dir=root, drain=drain_hook)
    connect_widget(w, calls)
    fa = tfile(root, "c1.bin")
    fb = tfile(root, "c2.bin")
    select_local(w, fa, fb)
    w._put_selected()
    check("two puts queued", len(drain(calls)) == 2 and w._op_total == 2)
    w.on_put_done(False, "/c1.bin")          # one failure, then the user cancels
    calls["toasts"].clear()
    w._on_op_cancel()
    check("cancel drains the queue and ends the op",
          drained["n"] == 1 and not w._op_active and w.isEnabled()
          and logged(calls, "Cancelling remote operation"))
    check("a cancelled op never toasts failures", calls["toasts"] == [])

    # Disconnect mid-op: op released, moves keep their sources.
    select_local(w, fa)
    w._copy_local("cut")
    calls["q"].clear()
    w._paste_into_next()
    check("move op running", w._op_active and len(w._cut_jobs) == 1)
    w.on_disconnected()
    check("disconnect ends the op and abandons moves",
          not w._op_active and w.isEnabled() and w._cut_jobs == []
          and os.path.exists(fa)
          and logged(calls, "unfinished moves kept their sources")
          and logged(calls, "stopped the running operation"))


def test_sync_root_and_local_pane():
    root = tdir("sr_root")
    sub = os.path.join(root, "deeper")
    os.makedirs(sub, exist_ok=True)
    w, calls = make_widget(local_start_dir=root)
    check("syncroot button hidden at the root",
          not w.local_set_syncroot_button.isVisible())
    w._set_local_dir(sub, commit=False)
    check("browsing elsewhere offers the button",
          w.local_set_syncroot_button.isVisibleTo(w)
          and w.sync_root() == root.replace("\\", "/"))
    FakeMsg.answer = QMessageBox.Yes
    calls["sync_root"].clear()
    w._on_set_syncroot_clicked()
    check("button commits the browsed folder",
          w.sync_root() == sub.replace("\\", "/")
          and calls["sync_root"] == [sub.replace("\\", "/")]
          and not w.local_set_syncroot_button.isVisibleTo(w))

    w.local_path_edit.setText(os.path.join(TMP, "no-such-dir"))
    w._on_path_edit()
    check("typing a bogus path restores the box",
          w.local_path_edit.text() == sub.replace("\\", "/"))
    w.local_path_edit.setText(root)
    w._on_path_edit()
    check("typing a real path commits it",
          w.sync_root() == root.replace("\\", "/"))

    w.set_local_dir(sub)
    check("public set_local_dir browses without committing",
          w._browse_dir() == sub.replace("\\", "/")
          and w.sync_root() == root.replace("\\", "/"))
    w.set_local_dir(os.path.join(TMP, "no-such-dir"))
    check("set_local_dir ignores a missing folder",
          w._browse_dir() == sub.replace("\\", "/"))
    w._local_up()
    check("local Up browses to the parent",
          w._browse_dir() == root.replace("\\", "/"))
    w._local_refresh()
    check("local refresh keeps the folder",
          w._browse_dir() == root.replace("\\", "/"))


def test_modified_column_local_time():
    """The Modified column must show the OS-LOCAL wall time. QDateTime's
    toString prints whatever spec the stamp carries, and Qt has shipped
    file times in both LocalTime and UTC specs — a UTC-spec'd stamp shown
    raw is hours off (the 9.5.6 hardware report). The expectation is
    computed with datetime.fromtimestamp, the OS-local reference. On a
    UTC-offset machine (the dev box) this FAILS if UTC ever leaks; on a
    UTC-zone runner it still pins the formatting path."""
    import datetime

    root = tdir("mt_root")
    f = tfile(root, "stamped.txt")
    # A fixed local wall moment (mktime = local tuple -> epoch), so the
    # expectation is deterministic and not "now"-flaky.
    when = time.mktime((2026, 1, 15, 10, 30, 0, 0, 0, -1))
    os.utime(f, (when, when))

    w, _calls = make_widget(local_start_dir=root)
    select_local(w, f)                 # waits out the async population
    six = w.local_model.index(f)
    shown = w.local_model.data(six.siblingAtColumn(3),
                               Qt.ItemDataRole.DisplayRole)
    expect = datetime.datetime.fromtimestamp(
        os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
    check("Modified column shows the OS-local wall time",
          shown == expect, f"shown={shown!r} expected={expect!r}")


def test_local_file_operations():
    root = tdir("lop_root")
    w, calls = make_widget(local_start_dir=root)

    FakeInput.queue = [("made", True)]
    w._local_new_folder()
    check("local new folder created", os.path.isdir(os.path.join(root, "made")))
    FakeInput.queue = [("made", True)]
    w._local_new_folder()
    check("duplicate name refused", logged(calls, "already exists"))
    FakeInput.queue = [("a/b", True)]
    w._local_new_folder()
    check("path as name refused", logged(calls, "name only"))

    fr = tfile(root, "old.txt")
    select_local(w, fr)
    FakeInput.queue = [("new.txt", True)]
    w._local_rename_selected()
    check("local rename", not os.path.exists(fr)
          and os.path.isfile(os.path.join(root, "new.txt")))

    fd = tfile(root, "doomed.txt")
    select_local(w, fd)
    FakeMsg.answer = QMessageBox.No
    w._local_delete_selected()
    check("declined local delete keeps the file", os.path.exists(fd))
    FakeMsg.answer = QMessageBox.Yes
    w._local_delete_selected()
    check("confirmed local delete removes it", not os.path.exists(fd))

    # Copy/move engine incl. the Explorer-style " - Copy" names.
    src = tfile(root, "copyme.txt", b"C")
    other = tdir("lop_other")
    w._copy_paths_into_local([src], other)
    check("copy into another folder",
          os.path.isfile(os.path.join(other, "copyme.txt")))
    w._copy_paths_into_local([src], root, dup_in_place=True)
    check("same-folder paste duplicates",
          os.path.isfile(os.path.join(root, "copyme - Copy.txt")))
    w._copy_paths_into_local([src], root, dup_in_place=True)
    check("second duplicate numbered",
          os.path.isfile(os.path.join(root, "copyme - Copy (2).txt")))
    w._copy_paths_into_local([src], root, move=True)
    check("same-folder move is a no-op", os.path.isfile(src))
    calls["toasts"].clear()
    w._copy_paths_into_local([src], other, move=True)
    check("colliding move refused with a toast", os.path.isfile(src)
          and calls["toasts"] and "already exists" in calls["toasts"][0][1])
    calls["toasts"].clear()
    folder = tdir("lop_root/selfcopy")
    w._copy_paths_into_local([folder], folder)
    check("folder into itself refused", calls["toasts"]
          and "into itself" in calls["toasts"][0][1])

    mv = tfile(root, "moveme.txt")
    w._copy_paths_into_local([mv], other, move=True)
    check("move relocates the file", not os.path.exists(mv)
          and os.path.isfile(os.path.join(other, "moveme.txt")))

    # Zip round-trip through the local pane actions.
    zroot = tdir("lop_zip")
    za = tfile(zroot, "art.scr", b"S" * 100)
    zdir = os.path.join(zroot, "bundle")
    os.makedirs(zdir, exist_ok=True)
    tfile(zdir, "inner.txt", b"I")
    calls["toasts"].clear()
    w._local_zip([za, zdir])
    zip_path = os.path.join(zroot, "art.scr.zip")   # first item's name + .zip
    check("local zip created", os.path.isfile(zip_path)
          and any("Zip complete" in t[0] for t in calls["toasts"]))
    os.remove(za)
    shutil.rmtree(zdir)
    calls["toasts"].clear()
    w._local_unzip(zip_path)
    check("local unzip restores the tree",
          os.path.isfile(za) and os.path.isfile(os.path.join(zdir, "inner.txt"))
          and any("Unzip complete" in t[0] for t in calls["toasts"]))


def test_drag_and_drop():
    root = tdir("dnd_root")
    w, calls = make_widget(local_start_dir=root)
    connect_widget(w, calls)

    # NB: keep every QMimeData in a local — QDropEvent does not own it, and a
    # garbage-collected mime turns event.mimeData() into a dangling object.
    fa = tfile(root, "drag.bin", b"D")
    mime_fa = url_mime(fa)
    ev = drop_event(mime_fa)
    w._next_drop(ev)
    check("OS drop on the Next pane uploads",
          ev.isAccepted() and normq(drain(calls)) == [("put", fwd(fa), "/")])
    w.on_put_done(True, "/drag.bin")
    calls["q"].clear()

    mime_empty = QMimeData()
    ev = drop_event(mime_empty)
    w._next_drop(ev)
    check("empty drop ignored", not ev.isAccepted() and drain(calls) == [])

    # Dragging Next entries into the local pane downloads them.
    mime = QMimeData()
    mime.setData("application/x-zxnu-next-entries", b"F\t/boot.bas\nD\t/GAMES")
    ev = drop_event(mime)
    w._local_drop(ev)
    check("next-entry drop downloads incl. empty folders",
          normq(drain(calls)) == [("get", "/boot.bas", fwd(root)),
                                  ("get", "/GAMES", fwd(root))]
          and os.path.isdir(os.path.join(root, "GAMES")))
    w.on_got("/boot.bas", os.path.join(root, "boot.bas"))
    w.on_got("/GAMES", os.path.join(root, "GAMES"))

    # An OS file drop on the local pane copies into the browsed folder.
    other = tdir("dnd_other")
    fb = tfile(other, "dropped.txt", b"T")
    mime_fb = url_mime(fb)
    ev = drop_event(mime_fb)
    w._local_drop(ev)
    check("OS drop on the local pane copies",
          os.path.isfile(os.path.join(root, "dropped.txt")))


def test_arrow_pulse_and_overlay_resize():
    w, calls = make_widget(local_start_dir=tdir("pulse_root"))
    w.resize(800, 500)
    w.show()
    check("show starts the arrow pulse", w._arrow_pulse_timer is not None
          and w._arrow_pulse_timer.isActive())
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not w.btn_to_next.styleSheet():
        QApplication.processEvents()
        time.sleep(0.01)
    check("pulse paints the buttons", "rgba(46,204,113" in w.btn_to_next.styleSheet())
    w.hide()
    check("hide stops the pulse", w._arrow_pulse_timer is None
          and w.btn_to_next.styleSheet() == "")

    # The overlay follows the Next pane through resizes (eventFilter path).
    connect_widget(w, calls)
    w._set_next_overlay("busy")
    w.show()
    w.next_container.resize(300, 200)
    QApplication.processEvents()
    check("overlay tracks the pane size",
          w._next_overlay is not None
          and w._next_overlay.size() == w.next_container.size())
    w._set_next_overlay(None)
    check("overlay removable", w._next_overlay is None
          and w.next_container.isEnabled())
    w.hide()


def test_compact_buttons_fit_translated_labels():
    """The two navigation bars' narrow buttons must follow their own label.

    They used to carry a hard setMaximumWidth, so translating "Up" to "W górę"
    or "Refresh" to "Обновить" truncated the caption. CompactButton re-derives
    the cap from the text on every setText — which is exactly how
    zxnu_i18n.translate_widget_tree re-labels the UI.
    """
    # Assertions stay font-agnostic on purpose: with no QT_QPA_FONTDIR the
    # offscreen platform measures tofu boxes, so only relative widths hold.
    print("\n== compact buttons fit their translated labels ==")
    btn = rex.CompactButton("Up", floor=48)
    check("a compact button is never narrower than its floor",
          btn.maximumWidth() >= 48, str(btn.maximumWidth()))

    # Every shipped translation of "Up" and "Refresh" must fit its button.
    for floor, source, translations in (
            (48, "Up", ["Subir", "W górę", "Вверх", "Nahoru", "Monter"]),
            (72, "Refresh", ["Actualizar", "Atualizar", "Odśwież",
                             "Обновить", "Obnovit", "Actualiser"])):
        probe = rex.CompactButton(source, floor=floor)
        english = probe.maximumWidth()
        for text in translations:
            probe.setText(text)
            needed = probe.fontMetrics().horizontalAdvance(text)
            check(f"{source!r} -> {text!r} is not truncated",
                  probe.maximumWidth() >= needed and probe.maximumWidth() >= floor,
                  f"cap={probe.maximumWidth()} text={needed}")
        probe.setText(source)
        check(f"{source!r} returns to its English width when the language does",
              probe.maximumWidth() == english, str(probe.maximumWidth()))

    # Neither explorer may go back to a hard cap: this bug was reported twice,
    # once per tab, so guard the SD Card pane's navigation rows at the source
    # (building that pane needs a full host, which this suite has no business
    # assembling).
    import ast
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    for fname in ("zxnu_remote_explorer.py", "zxnu_sdcard_explorer.py"):
        tree = ast.parse(open(os.path.join(repo, fname), encoding="utf-8").read())
        hard_capped = []
        for node in ast.walk(tree):
            # QPushButton("Up"|"Refresh"|"+ Drive") — the compact navigation
            # buttons must be built as CompactButton instead.
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "QPushButton" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in ("Up", "Refresh", "+ Drive")):
                hard_capped.append(node.args[0].value)
        check(f"{fname} builds its Up/Refresh buttons as CompactButton",
              not hard_capped, f"still plain QPushButton: {hard_capped}")

    # And the real buttons in a live widget behave the same way.
    w, _calls = make_widget()
    compact = w.findChildren(rex.CompactButton)
    labels = sorted(b.text() for b in compact)
    check("both bars' Up/Refresh (+ Drive) are CompactButtons",
          labels == ["+ Drive", "Refresh", "Refresh", "Up", "Up"], str(labels))
    for button in compact:
        if button.text() != "Up":
            continue
        before = button.maximumWidth()
        button.setText("Вверх")            # what the Russian UI shows
        check("a live Up button widens for the Russian label",
              button.maximumWidth() > before
              and button.maximumWidth()
              >= button.fontMetrics().horizontalAdvance("Вверх"),
              f"{before} -> {button.maximumWidth()}")
    w.deleteLater()


def test_select_all_skips_updir():
    """Ctrl-A (and programmatic selectAll) selects a listing's CONTENTS,
    never the ".." parent row pinned on top — on BOTH panes. A select-all
    fed into delete/copy/drag must not smuggle the folder above in."""
    print("\n== Select All skips the '..' row ==")

    # -- Next pane: below drive root, so the '..' row exists --------------
    w, calls = make_widget()
    connect_widget(w, calls)
    w.on_listing("/GAMES", [(False, 10, "a.tap"), (False, 20, "b.tap"),
                            (True, 0, "SUB")])
    names = [w.next_model.item(r, 0).text()
             for r in range(w.next_model.rowCount())]
    check("next pane fixture shows '..' + 3 entries",
          len(names) == 4 and ".." in names, repr(names))
    # Key events go to a SHOWN widget: this suite normally never shows its
    # widgets, but synthesising keystrokes at a never-realised view is the
    # kind of corner offscreen Qt is allowed to dislike.
    w.show()
    QApplication.processEvents()
    QTest.keyClick(w.next_view, Qt.Key_A, Qt.ControlModifier)
    sel = [ix.data(RE_PATH_ROLE)
           for ix in w.next_view.selectionModel().selectedRows(0)]
    check("next pane Ctrl-A selects the three entries", len(sel) == 3,
          repr(sel))
    check("next pane Ctrl-A leaves '..' out", ".." not in sel, repr(sel))
    w.next_view.clearSelection()
    w.next_view.selectAll()
    sel = [ix.data(RE_PATH_ROLE)
           for ix in w.next_view.selectionModel().selectedRows(0)]
    check("next pane selectAll() skips '..' too",
          len(sel) == 3 and ".." not in sel, repr(sel))

    # -- local pane: a real folder with a parent --------------------------
    root = tdir("ctrla_root")
    for n in ("one.txt", "two.txt"):
        tfile(root, n)
    os.mkdir(os.path.join(root, "sub"))
    w, calls = make_widget(local_start_dir=root)

    # Re-fetch the root index on EVERY poll: QFileSystemModel populates
    # asynchronously and each directoryLoaded invalidates proxy indexes,
    # so a root index captured once and reused across processEvents()
    # dangles — rowCount(stale index) was an access violation that took
    # a CI run (and its buffered stdout) with it.
    def lrows():
        return w.local_proxy.rowCount(w.local_view.rootIndex())

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and lrows() < 4:
        QApplication.processEvents()
        time.sleep(0.02)
    lroot = w.local_view.rootIndex()
    lnames = [w.local_proxy.index(r, 0, lroot).data()
              for r in range(w.local_proxy.rowCount(lroot))]
    check("local pane fixture shows '..' + 3 entries",
          len(lnames) == 4 and ".." in lnames, repr(lnames))
    w.show()
    QApplication.processEvents()
    QTest.keyClick(w.local_view, Qt.Key_A, Qt.ControlModifier)
    lsel = [ix.data() for ix in w.local_view.selectionModel().selectedRows(0)]
    check("local pane Ctrl-A selects the three entries", len(lsel) == 3,
          repr(lsel))
    check("local pane Ctrl-A leaves '..' out", ".." not in lsel, repr(lsel))


def test_os_protection_stops_and_explains():
    """A remote WRITE refused by the far side's OS protection (a ZXNextRemote
    listener, 0.9.0) must STOP the batch and toast the actionable message —
    not count one generic failure and grind through the rest."""
    print("\n== remote OS protection: stop + explain ==")
    root = tdir("osp_root")
    drained = {"n": 0}

    def drain_hook():
        drained["n"] += 1
        return 1          # pretend one queued command was dropped
    w, calls = make_widget(local_start_dir=root, drain=drain_hook)
    connect_widget(w, calls)

    # Stand up a two-command batch (delete two files), then have the FIRST
    # come back OS-protected. The op must end immediately, draining the rest.
    fa = tfile(root, "a.bin")
    fb = tfile(root, "b.bin")
    select_local(w, fa, fb)
    w._put_selected()
    check("two writes queued", w._op_total == 2 and w._op_active)
    calls["toasts"].clear()

    w.on_os_protected("put", "/sys/a.bin")
    check("OS-protection toast fired, red",
          len(calls["toasts"]) == 1
          and calls["toasts"][0][2] == "red")
    check("toast message names the OS protection setting",
          "OS protection" in calls["toasts"][0][1]
          and "restricted directory" in calls["toasts"][0][1])
    check("the rest of the batch was drained and the op ended",
          drained["n"] == 1 and not w._op_active and w.isEnabled())
    check("the block is logged", logged(calls, "BLOCKED by remote OS protection"))


def test_emulator_start_from_next():
    """'Start <emulator> with <file>' on the NEXT pane.

    The emulator runs on the PC and cannot read the Next's storage, so the file
    must be downloaded first and the emulator started on THAT copy — the Next
    pane's counterpart to the SD Card tab extracting from the image. Driven
    through the real widget: the download is a normal queued 'get' op, so the
    recorded queue and on_op_done() are enough to exercise the whole path.
    """
    print("\n== emulator auto-start from the Next pane ==")
    from zxnu_workers import EmulatorAutostart

    launched, staged = [], []
    stage_root = tempfile.mkdtemp(dir=TMP)

    def make_stage():
        staged.append(stage_root)
        return stage_root

    # The download goes to the LOCAL pane's folder, exactly where the Download
    # action puts things, so the file shows up in the left explorer and the
    # user keeps it — not into a temp directory it can never be found in.
    local_dir = tempfile.mkdtemp(dir=TMP)
    entry = EmulatorAutostart("CSpect", "Start CSpect with file beast.nex",
                              launched.append, make_stage)
    w, calls = make_widget(emulator_entries=lambda p: [entry],
                           local_start_dir=local_dir)
    connect_widget(w, calls, listing=[(False, 4096, "beast.nex")])

    w._emulator_start_from_next("/beast.nex", entry)
    q = list(calls["q"])
    check("the Next-pane action queues a download",
          any(c[0] == "get" and c[1] == "/beast.nex" for c in q), str(q))
    check("...into the LOCAL pane's folder, so the file lands where it shows",
          any(len(c) > 2 and os.path.realpath(c[2]) == os.path.realpath(local_dir)
              for c in q), str(q))
    check("...and NOT into a scratch directory the user cannot find",
          staged == [], str(staged))
    check("nothing is launched before the download finishes", launched == [])

    # Finish the download with the file actually present, as the worker would.
    open(os.path.join(local_dir, "beast.nex"), "wb").write(b"\x00" * 8)
    w.on_op_done(True, "get", "/beast.nex")
    QApplication.processEvents()          # the launch is deferred by one tick
    check("the emulator is started once the file has arrived",
          len(launched) == 1 and samepath(launched[0],
                                          os.path.join(local_dir, "beast.nex")),
          str(launched))
    check("it is started on the DOWNLOADED copy, not the Next path",
          launched and not launched[0].startswith("/beast"), str(launched))
    check("the downloaded file is kept (it is the user's now)",
          os.path.isfile(os.path.join(local_dir, "beast.nex")))

    # A failed download must not start anything — and must NEVER delete the
    # folder it downloaded into, which is the user's own browsing directory.
    launched.clear()
    local2 = tempfile.mkdtemp(dir=TMP)
    keeper = os.path.join(local2, "precious.txt")
    open(keeper, "wb").write(b"do not delete me")
    entry2 = EmulatorAutostart("MAME", "Start MAME with file x.nex",
                               launched.append, lambda: stage_root)
    w2, calls2 = make_widget(emulator_entries=lambda p: [entry2],
                             local_start_dir=local2)
    connect_widget(w2, calls2, listing=[(False, 10, "x.nex")])
    w2._emulator_start_from_next("/x.nex", entry2)
    w2.on_op_done(False, "get", "/x.nex")   # download failed
    QApplication.processEvents()
    check("a failed download starts nothing", launched == [], str(launched))
    check("a failed download NEVER deletes the user's local folder",
          os.path.isdir(local2) and os.path.isfile(keeper), local2)
    check("...and says so in the log",
          any("could not be downloaded" in str(s) or "not started" in str(s)
              for s in calls2["log"]), str(calls2["log"][-3:]))

    # Without the host hook the widget must simply offer nothing, never crash.
    w3, _ = make_widget()
    check("no hook -> no entries (the widget knows no emulators itself)",
          w3._emulator_entries("/anything.nex") == [])

    # A genuine blocker (not a missing SD card — booting a file needs none)
    # must be reported BEFORE any transfer, so the user never waits for a
    # download that cannot lead anywhere.
    launched.clear()
    blocked_entry = EmulatorAutostart(
        "CSpect", "Start CSpect with file x.nex", launched.append,
        lambda: tempfile.mkdtemp(dir=TMP),
        lambda: "CSpect could not be found")
    w4, calls4 = make_widget(emulator_entries=lambda p: [blocked_entry],
                             local_start_dir=tempfile.mkdtemp(dir=TMP))
    connect_widget(w4, calls4, listing=[(False, 10, "x.nex")])
    w4._emulator_start_from_next("/x.nex", blocked_entry)
    check("a launch that cannot succeed downloads NOTHING",
          not any(c[0] == "get" for c in calls4["q"]), str(calls4["q"]))
    check("...and says why, visibly (toast, not just the log)",
          len(calls4["toasts"]) == 1
          and "CSpect could not be found" in calls4["toasts"][0][1],
          str(calls4["toasts"]))
    check("...and starts no emulator", launched == [])

    # The worker names the arriving file from what the NEXT reports, not from
    # the path we asked for, so a single-file get can land under a sub-path.
    # It must still be found — BY NAME, since the destination is the user's own
    # folder and "the only file in here" would pick up something unrelated.
    launched.clear()
    local3 = tempfile.mkdtemp(dir=TMP)
    open(os.path.join(local3, "unrelated.bin"), "wb").write(b"x")
    entry3 = EmulatorAutostart("CSpect", "Start CSpect with file y.nex",
                               launched.append, lambda: stage_root)
    w5, calls5 = make_widget(emulator_entries=lambda p: [entry3],
                             local_start_dir=local3)
    connect_widget(w5, calls5, listing=[(False, 10, "y.nex")])
    w5._emulator_start_from_next("/games/y.nex", entry3)
    odd = os.path.join(local3, "games", "y.nex")     # not <dest>/y.nex
    os.makedirs(os.path.dirname(odd), exist_ok=True)
    open(odd, "wb").write(b"\x00" * 4)
    w5.on_op_done(True, "get", "/games/y.nex")
    QApplication.processEvents()
    check("a file that lands under a sub-path is still found and booted",
          len(launched) == 1 and samepath(launched[0], odd), str(launched))
    check("...found by NAME, so an unrelated neighbour is never booted",
          launched and os.path.basename(launched[0]) == "y.nex", str(launched))


def main():
    logging.disable(logging.CRITICAL)
    app = QApplication(sys.argv)  # noqa: F841 — QWidget construction needs it
    try:
        test_pure_helpers()
        test_initial_and_connection_state()
        test_listing_and_rendering()
        test_navigation()
        test_drive_switching()
        test_ls_failed_fallback()
        test_sorting()
        test_op_lifecycle_new_folder_rename_delete()
        test_get_size_dialog()
        test_transfers_get_put()
        test_send_local_paths()
        test_copy_cut_paste_next_to_local()
        test_copy_cut_paste_local_to_next()
        test_rcpy_precheck_and_background()
        test_cancel_and_disconnect_mid_op()
        test_sync_root_and_local_pane()
        test_modified_column_local_time()
        test_local_file_operations()
        test_drag_and_drop()
        test_arrow_pulse_and_overlay_resize()
        test_idle_status_provider()
        test_idle_details_provider()
        test_compact_buttons_fit_translated_labels()
        test_emulator_start_from_next()
        test_select_all_skips_updir()
        test_os_protection_stops_and_explains()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
