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

# Python 3.14 + PySide6: a cyclic-GC pass that fires INSIDE a Qt call can
# finalize dead widgets whose teardown touches a lazily-materialised Qt
# enum, and that intermittently segfaults (observed twice in the machine-
# colour block, "Garbage-collecting" + enum.py in the faulthandler trace).
# This suite churns hundreds of short-lived widgets by design, so keep the
# collector out of the run entirely - the process lives ~2 s, and the
# retro-log suite already handles its cousin of this flake the same
# pragmatic way (teardown hard-exit).
import gc
gc.disable()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The compact-button checks print translated labels ("W górę", "Вверх"), which
# a cp1252 console cannot encode — without this the suite dies in its own
# print() rather than on anything it is testing.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PySide6.QtCore import (QEvent, QItemSelectionModel, QMimeData, QPoint,
                            QPointF, Qt, QTimer, QUrl)
from PySide6.QtGui import QColor, QDropEvent, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

import zxnu_config
from zxnu_config import TREE_FONT_MIN_PT
from zxnu_workers import bind_tree_font_zoom
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
    seen = []          # every getText call's positional args (9.7.2 pins)

    @classmethod
    def getText(cls, *a, **k):
        cls.seen.append(a)
        return cls.queue.pop(0) if cls.queue else ("", False)


class FakeIdentity:
    """Replaces zxnu_remote_explorer.MachineIdentityDialog (9.5.27, the
    name+colour editor): pops a scripted (name, QColor-or-None, accepted)
    answer and records what it was OPENED with, so a test can assert both
    halves. An empty script answers a cancel, like FakeInput."""
    queue = []
    seen = []

    def __init__(self, addr, name="", color=None, parent=None):
        FakeIdentity.seen.append((addr, name, color))
        self._answer = (FakeIdentity.queue.pop(0)
                        if FakeIdentity.queue else (name, color, False))

    def exec(self):
        return (QDialog.DialogCode.Accepted if self._answer[2]
                else QDialog.DialogCode.Rejected)

    def result_values(self):
        return self._answer[0], self._answer[1]


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
rex.MachineIdentityDialog = FakeIdentity


# ---------------------------------------------------------------------------
# harness helpers
# ---------------------------------------------------------------------------
def make_widget(**kw):
    """A RemoteExplorerWidget wired to recorders for every host callback."""
    calls = {"q": [], "log": [], "toasts": [], "sync_root": [],
             "remote_cwd": [], "sorts": [], "extra_drives": [], "q_to": []}
    w = RemoteExplorerWidget(
        enqueue=calls["q"].append,
        local_start_dir=kw.get("local_start_dir"),
        log=calls["log"].append,
        drain=kw.get("drain"),
        on_sync_root_changed=calls["sync_root"].append,
        remote_start_dir=kw.get("remote_start_dir"),
        # (path, addr) since the per-machine folders: the widget reports the
        # active peer's address too. The tests only assert on the PATH, so
        # the default recorder keeps storing just that; the multi-Next test
        # substitutes a host-like per-address store for both halves.
        on_remote_cwd_changed=kw.get(
            "on_remote_cwd_changed",
            lambda p, a=None: calls["remote_cwd"].append(p)),
        remote_cwd_for=kw.get("remote_cwd_for"),
        machine_name_for=kw.get("machine_name_for"),
        on_machine_name_changed=kw.get("on_machine_name_changed"),
        machine_color_for=kw.get("machine_color_for"),
        on_machine_color_changed=kw.get("on_machine_color_changed"),
        # The session strip's targeted commands (right-click Disconnect on a
        # BENCHED machine) leave through their own hook, never the shared
        # queue - record them separately so a test can tell the two apart.
        enqueue_to=kw.get(
            "enqueue_to",
            lambda sid, cmd: (calls["q_to"].append((sid, cmd)) or True)),
        local_sort=kw.get("local_sort"),
        next_sort=kw.get("next_sort"),
        on_sort_changed=lambda which, v: calls["sorts"].append((which, v)),
        on_toast=lambda t, m, variant="red": calls["toasts"].append((t, m, variant)),
        extra_drives=kw.get("extra_drives"),
        on_extra_drives_changed=calls["extra_drives"].append,
        emulator_entries=kw.get("emulator_entries"),
        emulator_launchers=kw.get("emulator_launchers"),
        emulator_color_for=kw.get("emulator_color_for"),
        on_emulator_color_changed=kw.get("on_emulator_color_changed"),
        local_drives=kw.get("local_drives"),
        sync5_update_source=kw.get("sync5_update_source"),
        zxnr_update_source=kw.get("zxnr_update_source"),
        update_prompt_enabled=kw.get("update_prompt_enabled"),
        on_update_prompt=kw.get("on_update_prompt"))
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
    check("connect asks version, drives, then lists /",
          drain(calls) == [("version",), ("drives",), ("ls", "/")])

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

    # The 'Y' version reply lands AFTER the free-space figure (1.0.2 wire
    # ident); ("", "") = an old listener, the label shows no version.
    w.on_ident("httpbridge", "1.0.2")
    check("ident after the free space",
          "httpbridge 1.0.2" in w.next_path_label.text()
          and w.next_path_label.text().index("free")
              < w.next_path_label.text().index("httpbridge"))
    w.on_ident("", "")
    check("old listener shows no version",
          "httpbridge" not in w.next_path_label.text())
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


def test_multi_next_folders_follow_the_baton():
    """Two Nexts, one pane (9.5.15): each machine's folder is keyed by its
    ADDRESS, and every baton change — a user pick or a departure — restores
    the incoming machine's own folder. The field bug this pins down: the
    widget read its peer roster without ever storing it, so all machines
    shared one folder and the survivor of a disconnect woke up in the
    departed machine's directory."""
    saved = {}   # the host's per-machine store: addr -> folder
    w, calls = make_widget(
        local_start_dir=tdir("mnx_root"),
        remote_cwd_for=saved.get,
        on_remote_cwd_changed=lambda p, a=None: (
            saved.__setitem__(a, p) if a else None))
    w.on_peers((1, [(1, "192.168.1.10")]))   # the roster precedes connected
    connect_widget(w, calls)
    w.on_listing("/A", [])                   # browsing the first Next
    check("a confirmed listing is saved under the machine's address",
          saved.get("192.168.1.10") == "/A", str(saved))

    # An N-Go joins and the user picks it (the worker confirms via peers).
    w.on_peers((2, [(1, "192.168.1.10"), (2, "192.168.1.20")]))
    check("machine combo carries both", w.next_machine_combo.count() == 2)
    check("the baton move re-reads the new card", ("drives",) in drain(calls))
    w.on_drives("C", ["C"])
    w.on_listing("/Home", [])                # browsing the N-Go

    # The N-Go drops off; the worker hands the baton back to the survivor.
    w.on_peers((1, [(1, "192.168.1.10")]))
    check("the departing machine's folder was saved on the way out",
          saved.get("192.168.1.20") == "/Home", str(saved))
    check("the survivor returns to ITS folder, not the departed one's",
          w._cwd == "/A", w._cwd)
    check("...and the queued listing asks for it",
          ("ls", "/A") in drain(calls))
    check("combo shrinks to the survivor",
          w.next_machine_combo.count() == 1
          and w.next_machine_combo.itemData(0) == 1)


def test_machine_names_follow_the_address():
    """The machine combo's friendly names (9.5.18): the round ✎ button
    names the machine the combo shows, the host persists addr -> name, and
    every entry of that ADDRESS — this session's or a later one's, any
    session id — greets by name: "10.0.0.185 #1 - N-Go"."""
    names = {}   # the host's store
    w, calls = make_widget(
        local_start_dir=tdir("mnames_root"),
        machine_name_for=names.get,
        on_machine_name_changed=lambda a, n: (
            names.__setitem__(a, n) if n else names.pop(a, None)))
    w.on_peers((1, [(1, "10.0.0.185"), (2, "10.0.0.185")]))
    connect_widget(w, calls)
    check("unnamed machines show addr #sid",
          w.next_machine_combo.itemText(0) == "10.0.0.185 #1"
          and w.next_machine_combo.itemText(1) == "10.0.0.185 #2",
          str([w.next_machine_combo.itemText(i) for i in range(2)]))
    check("the ✎ name button rides along with the combo",
          not w.next_machine_name_btn.isHidden())

    # Name the active machine; BOTH sessions of that address adopt it.
    _w0 = w.next_machine_combo.sizeHint().width()
    FakeIdentity.queue = [("  N-Go  ", None, True)]
    w._on_machine_name_edit()
    check("name saved for the ADDRESS (whitespace collapsed)",
          names.get("10.0.0.185") == "N-Go", str(names))
    check("the combo re-measures for the longer label (no clipping)",
          w.next_machine_combo.sizeHint().width() > _w0,
          f"{_w0} -> {w.next_machine_combo.sizeHint().width()}")
    check("every session of the address shows the name",
          w.next_machine_combo.itemText(0) == "10.0.0.185 #1 - N-Go"
          and w.next_machine_combo.itemText(1) == "10.0.0.185 #2 - N-Go",
          str([w.next_machine_combo.itemText(i) for i in range(2)]))

    # A cancelled dialog changes nothing.
    FakeIdentity.queue = []
    w._on_machine_name_edit()
    check("cancel keeps the name", names.get("10.0.0.185") == "N-Go")

    # The machine reconnects later under a fresh session id: the name is
    # keyed by address, so the new roster greets it by name at once.
    w.on_peers((7, [(7, "10.0.0.185")]))
    check("a later session id keeps the friendly name",
          w.next_machine_combo.itemText(0) == "10.0.0.185 #7 - N-Go",
          w.next_machine_combo.itemText(0))

    # An empty name removes it.
    FakeIdentity.queue = [("", None, True)]
    w._on_machine_name_edit()
    check("empty name forgets it",
          "10.0.0.185" not in names
          and w.next_machine_combo.itemText(0) == "10.0.0.185 #7",
          w.next_machine_combo.itemText(0))


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


def test_local_open_with_shell():
    """Context-menu 'Open' on the local pane: the selected item goes to the
    OS shell's associated application. The hand-off itself is faked — the
    test asserts WHAT is handed over and that a refusal is reported, without
    launching anything on the test machine."""
    root = tdir("open_root")
    w, calls = make_widget(local_start_dir=root)
    page = tfile(root, "page.html", b"<html></html>")
    opened = []
    orig = rex.open_path_with_system_shell
    rex.open_path_with_system_shell = lambda p: (opened.append(p), True)[1]
    try:
        w._local_open_selected()
        check("no selection -> nothing handed to the shell", opened == [])
        select_local(w, page)
        w._local_open_selected()
        check("Open hands the selected item to the OS shell",
              len(opened) == 1 and samepath(opened[0], page), str(opened))
        rex.open_path_with_system_shell = lambda p: False
        w._local_open_selected()
        check("a refused open is reported in the log",
              logged(calls, "could not open"), str(calls["log"]))
    finally:
        rex.open_path_with_system_shell = orig
    # The real helper's existence guard needs no OS: a vanished path answers
    # False before any shell is involved.
    from zxnu_config import open_path_with_system_shell
    check("helper refuses a vanished path without touching the shell",
          open_path_with_system_shell(os.path.join(root, "gone.html")) is False
          and open_path_with_system_shell("") is False)


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


def test_drag_out_staging():
    """Dragging OUT of the panes: the Next pane starts a BACKGROUND staging
    download and hands the drag a lazy mime whose text/uri-list resolves at
    drop time (waiting for the download if the drop comes first) - the drag
    gesture itself never blocks, which is how a real UART-speed fetch and a
    natural press-drag-release can coexist. Also covered: the hover-probe
    discriminator, cache reuse + expiry + vanished-file restage, stage-folder
    aging + the day-old sweep, the timeout path, dialog suppression for quiet
    ops, the busy guard on every enabled entry point, the local pane's
    staged-copy shortcut, the Next pane's own-drag refusal, and the local
    pane's URL drag-out. tempfile.tempdir is sandboxed for the whole test so
    staging folders (and the sweep) never touch the real system temp."""
    root = tdir("dndout_root")
    fake_tmp = tdir("dndout_fake_temp")
    w, calls = make_widget(local_start_dir=root)
    connect_widget(w, calls, listing=[(False, 8, "boot.bas")])
    w._drag_stage_wait_ms = 5000     # every wait below must stay bounded
    orig_tmpdir = tempfile.tempdir
    tempfile.tempdir = fake_tmp      # mkdtemp AND the day-old sweep sandbox
    orig_btn = rex._drag_button_down
    try:
        _drag_out_staging_body(w, calls, root, fake_tmp)
    finally:
        tempfile.tempdir = orig_tmpdir
        rex._drag_button_down = orig_btn


def _staged_mime(w, st, payload):
    """A drag payload exactly as _next_start_drag builds it: the lazy mime,
    the custom entry payload, and the staged paths named UP FRONT (the
    finished stage's real files when it has them, else the predicted ones)."""
    m = rex._StagedDragMime(w, st)
    m.setData("application/x-zxnu-next-entries", payload)
    paths = st['staged'] if st.get('ok') else st.get('expect', [])
    m.setUrls([QUrl.fromLocalFile(p) for p in paths])
    return m


def _drag_out_staging_body(w, calls, root, fake_tmp):
    REUSE_S = rex.RemoteExplorerWidget.DRAG_STAGE_REUSE_S
    KEEP_S = rex.RemoteExplorerWidget.DRAG_STAGE_KEEP_S

    # -- staging starts WITHOUT blocking: the drag can begin immediately --
    st = w._begin_drag_stage([("/boot.bas", False)])
    gets = [c for c in drain(calls) if c and c[0] == "get"]
    check("staging starts async and enqueues the get",
          st is not None and not st['done'] and w._op_active
          and len(gets) == 1 and gets[0][1] == "/boot.bas")
    check("quiet staging op leaves the widget enabled (no dialog, no freeze)",
          w.isEnabled() and w._op_dialog is None)

    check("the stage names its files before a byte is downloaded",
          st['expect'] and all(p.startswith(st['dir']) for p in st['expect'])
          and [os.path.basename(p) for p in st['expect']] == ["boot.bas"])

    mime = _staged_mime(w, st, b"F\t/boot.bas")
    check("drag mime advertises URLs before the download ends",
          mime.hasFormat("text/uri-list") and mime.hasUrls()
          and "text/uri-list" in mime.formats())

    # -- THE RED-CIRCLE REGRESSION (9.5.29 field report): Windows decides
    #    the drop cursor by asking for CF_HDROP *while hovering*, which Qt
    #    answers with urls(). An empty answer there = "no drop" cursor over
    #    Explorer, so a mid-hover pull MUST return the (predicted) paths
    #    immediately, without waiting for the download --
    rex._drag_button_down = lambda: True
    t0 = time.monotonic()
    hover = mime.urls()
    check("a mid-hover pull serves the named files instantly (drop cursor)",
          len(hover) == 1
          and samepath(hover[0].toLocalFile(), st['expect'][0])
          and time.monotonic() - t0 < 1.0, str(hover))
    check("...even though the download has not finished yet",
          not st['done'] and not os.path.exists(st['expect'][0]))
    rex._drag_button_down = lambda: False

    # -- FIELD REPORT (9.5.29): passing the drag over our OWN panes broke the
    #    drop. Their drag handlers ask the payload questions on every
    #    mouse-move event, from inside the live drag loop: that must never
    #    wait for the download, and must never resolve the mime empty --
    for pane, enter in (("Next", w._next_drag_enter),
                        ("local", w._local_drag_enter)):
        ev_hover = drop_event(mime)
        t0 = time.monotonic()
        enter(ev_hover)                       # dragEnter/dragMove
        check(f"a drag-move over the {pane} pane never stalls the gesture",
              time.monotonic() - t0 < 1.0
              and w._drag_over_own_panes())
        w._pane_drag_leave(ev_hover)
        check(f"leaving the {pane} pane clears the hover flag",
              not w._drag_over_own_panes())
    # ...and crossing our panes must not poison what comes after: the
    # payload still names its files, so Explorer still offers a drop.
    ev_hover = drop_event(mime)
    w._local_drag_enter(ev_hover)
    t0 = time.monotonic()
    check("a payload pull mid-hover is instant and still names the files",
          len(mime.urls()) == 1 and time.monotonic() - t0 < 1.0)
    w._pane_drag_leave(ev_hover)
    rex._drag_button_down = lambda: True
    check("after crossing our panes the drag still advertises files",
          len(mime.urls()) == 1 and mime.hasUrls())
    rex._drag_button_down = lambda: False

    # -- a drop BEFORE the download ends waits for it (lazy retrieveData);
    #    the get's completion is fed from a timer, as the worker would --
    def feed_get():
        dest = gets[0][2]
        with open(os.path.join(dest, "boot.bas"), "wb") as fh:
            fh.write(b"10 PRINT")
        w.on_got("/boot.bas", os.path.join(dest, "boot.bas"))

    QTimer.singleShot(80, feed_get)
    urls = mime.urls()               # the "drop": pulls text/uri-list
    check("the drop waits for staging and receives the real URL",
          len(urls) == 1 and urls[0].toLocalFile().endswith("boot.bas")
          and os.path.isfile(urls[0].toLocalFile()), str(urls))
    check("stage completed ok", st['done'] and st['ok']
          and [os.path.basename(p) for p in st['staged']] == ["boot.bas"])
    calls["q"].clear()

    # -- a hover probe AFTER completion serves the URLs right away --
    rex._drag_button_down = lambda: True
    mime_done = _staged_mime(w, st, b"F\t/boot.bas")
    t0 = time.monotonic()
    check("hover probe with a finished stage serves URLs immediately",
          len(mime_done.urls()) == 1 and time.monotonic() - t0 < 1.0)
    rex._drag_button_down = lambda: False

    # -- a fresh drag of the SAME selection reuses the stage: no re-fetch --
    st2 = w._begin_drag_stage([("/boot.bas", False)])
    check("re-drag reuses the finished stage (no second download)",
          st2 is st and [c for c in drain(calls) if c and c[0] == "get"] == [])

    # -- the Next pane refuses its own drag (custom format present) --
    ev = drop_event(mime)
    w._next_drop(ev)
    check("Next pane refuses its own drag",
          not ev.isAccepted() and drain(calls) == [])

    # -- staged URLs short-circuit a drop on the local pane --
    ev = drop_event(mime)
    w._local_drop(ev)
    check("staged drop on the local pane copies, no second download",
          os.path.isfile(os.path.join(root, "boot.bas"))
          and [c for c in drain(calls) if c and c[0] == "get"] == [])

    # -- a busy widget stages nothing NEW (the drag falls back to
    #    entries-only); reusing an already-finished stage stays allowed --
    w._op_active = True
    check("no NEW staging while an operation runs",
          w._begin_drag_stage([("/other.bin", False)]) is None)
    check("a finished stage is still reusable while an operation runs",
          w._begin_drag_stage([("/boot.bas", False)]) is st)
    w._op_active = False

    # -- an EXPIRED cache restages: backdate past DRAG_STAGE_REUSE_S --
    st['ts'] -= (REUSE_S + 1)
    st_dir = st['dir']

    def feed_get2():
        q = [c for c in drain(calls) if c and c[0] == "get"]
        if not q:
            QTimer.singleShot(20, feed_get2)
            return
        with open(os.path.join(q[-1][2], "boot.bas"), "wb") as fh:
            fh.write(b"NEW")
        w.on_got("/boot.bas", os.path.join(q[-1][2], "boot.bas"))

    QTimer.singleShot(20, feed_get2)
    st5 = w._begin_drag_stage([("/boot.bas", False)])
    deadline = time.monotonic() + 5.0
    while w._op_active and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    check("an expired stage is replaced by a fresh download",
          st5 is not None and st5 is not st and st5['done'] and st5['ok'])
    check("the expired stage folder was retired, not deleted",
          any(h['dir'] == st_dir for h in w._drag_stage_hist)
          and os.path.isdir(st_dir))
    calls["q"].clear()

    # -- a stage whose file VANISHED restages too --
    os.remove(st5['staged'][0])
    st6 = w._begin_drag_stage([("/boot.bas", False)])
    check("a stage with vanished files restages",
          st6 is not None and st6 is not st5
          and [c for c in calls["q"] if c and c[0] == "get"] != [])
    w.on_error("end it")             # close st6's op
    calls["q"].clear()

    # -- failed staging: the drop resolves to no URLs --
    def feed_fail():
        if [c for c in drain(calls) if c and c[0] == "get"]:
            w.on_error("get failed")
        else:
            QTimer.singleShot(20, feed_fail)

    QTimer.singleShot(50, feed_fail)
    st3 = w._begin_drag_stage([("/gone.bin", False)])
    mime3 = _staged_mime(w, st3, b"F\t/gone.bin")
    urls3 = mime3.urls()
    check("failed staging leaves the named file missing on disk",
          st3 is not None and st3['done'] and not st3['ok']
          and len(urls3) == 1 and not os.path.exists(urls3[0].toLocalFile()))
    calls["q"].clear()

    # -- a drop that outlives the wait window gives up waiting while the
    #    download keeps running; the wait spans the 250 ms dialog window, so
    #    it also proves quiet ops never pop the progress dialog --
    w._drag_stage_wait_ms = 400
    st4 = w._begin_drag_stage([("/slow.bin", False)])
    mime4 = _staged_mime(w, st4, b"F\t/slow.bin")
    t0 = time.monotonic()
    urls4 = mime4.urls()
    check("a too-early drop stops waiting, download still running",
          st4 is not None and not st4['done'] and w._op_active
          and logged(calls, "timed out") and time.monotonic() - t0 >= 0.3)
    check("...and the file it named is not there yet (Explorer will say so)",
          len(urls4) == 1 and not os.path.exists(urls4[0].toLocalFile()))
    check("no dialog appeared although the quiet op outlived the 250 ms delay",
          w._op_dialog is None)
    w._show_op_dialog_if_running()   # the stale-timer path, fired by hand
    check("a stale dialog timer firing mid-quiet-op is refused",
          w._op_dialog is None)

    # -- while that op is still running, every enabled entry point must
    #    refuse LOUDLY and without side effects --
    tot = w._op_total
    w.on_connected()                 # e.g. the multi-Next baton moving here
    check("on_connected's drives ride the raw queue (op accounting intact)",
          w._op_total == tot
          and [c for c in drain(calls) if c and c[0] == "drives"] != [])
    FakeMsg.answer = QMessageBox.Yes
    w._disconnect_session()
    check("disconnect's quit_app rides the raw queue (op accounting intact)",
          w._op_total == tot
          and [c for c in drain(calls) if c and c[0] == "quit_app"] != [])
    keep = tfile(root, "keepme.txt", b"K")
    w._clip = ("local", [keep], "cut")
    w._paste_into_next()
    check("a cut clipboard survives a busy-refused paste",
          w._clip is not None and logged(calls, "Busy"))
    w._clip = None
    mime_keep = url_mime(keep)       # NB: a local — QDropEvent doesn't own it
    ev = drop_event(mime_keep)
    w._next_drop(ev)
    check("a busy Next pane refuses the OS drop instead of eating it",
          not ev.isAccepted()
          and [c for c in drain(calls) if c and c[0] == "put"] == [])
    select_next(w, "boot.bas")
    w._delete_selected()
    check("a busy delete refuses before the confirm dialog",
          [c for c in drain(calls) if c and c[0] in ("rm", "rmtree")] == [])
    w.on_error("late")               # let the op end so the widget is clean
    calls["q"].clear()
    w._drag_stage_wait_ms = 5000

    # -- stage-folder AGING: inside DRAG_STAGE_KEEP_S survives, past it dies --
    dir_old = tdir("dndout_aged_old")
    dir_new = tdir("dndout_aged_new")
    w._drag_stage_hist = [
        {'dir': dir_old, 'ts': time.monotonic() - (KEEP_S + 1)},
        {'dir': dir_new, 'ts': time.monotonic()},
    ]
    w._retire_drag_stages(None)
    check("retired stage folders age out on schedule",
          not os.path.isdir(dir_old) and os.path.isdir(dir_new)
          and [h['dir'] for h in w._drag_stage_hist] == [dir_new])

    # -- the once-per-session sweep reaps day-old leftovers of PRIOR runs --
    sweep_old = os.path.join(fake_tmp, "zxnu_drag_leftover")
    sweep_new = os.path.join(fake_tmp, "zxnu_drag_fresh")
    os.makedirs(sweep_old); os.makedirs(sweep_new)
    two_days_ago = time.time() - 2 * 86400
    os.utime(sweep_old, (two_days_ago, two_days_ago))
    w._drag_swept = False
    w._retire_drag_stages(None)
    check("the session sweep reaps only day-old zxnu_drag_ leftovers",
          not os.path.isdir(sweep_old) and os.path.isdir(sweep_new)
          and w._drag_swept)

    # -- the real startDrag entry point wires the staged lazy mime --
    class _FakeDrag:
        last = None

        def __init__(self, src):
            _FakeDrag.last = self
            self.mime = None

        def setMimeData(self, m):
            self.mime = m

        def exec(self, *a, **k):
            return Qt.IgnoreAction

    def feed_get3():
        q = [c for c in drain(calls) if c and c[0] == "get"]
        if not q:
            QTimer.singleShot(20, feed_get3)
            return
        with open(os.path.join(q[-1][2], "boot.bas"), "wb") as fh:
            fh.write(b"x")
        w.on_got("/boot.bas", os.path.join(q[-1][2], "boot.bas"))

    select_next(w, "boot.bas")
    QTimer.singleShot(20, feed_get3)
    orig_drag = rex.QDrag
    rex.QDrag = _FakeDrag
    try:
        w._next_start_drag(Qt.CopyAction)
    finally:
        rex.QDrag = orig_drag
    m = _FakeDrag.last.mime if _FakeDrag.last is not None else None
    check("startDrag hands out the lazy staged mime + custom payload",
          isinstance(m, rex._StagedDragMime)
          and bytes(m.data("application/x-zxnu-next-entries")))
    deadline = time.monotonic() + 5.0
    while w._op_active and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    check("background staging finishes after the (instant) drag",
          not w._op_active and w._drag_stage['done'] and w._drag_stage['ok'])
    calls["q"].clear()

    # -- local pane drag-out carries real file URLs --
    fa = tfile(root, "outbound.txt", b"T")
    select_local(w, fa)
    _FakeDrag.last = None
    rex.QDrag = _FakeDrag
    try:
        w._local_start_drag(Qt.CopyAction)
    finally:
        rex.QDrag = orig_drag
    got = ([u.toLocalFile() for u in _FakeDrag.last.mime.urls()]
           if _FakeDrag.last is not None and _FakeDrag.last.mime else [])
    check("local drag-out carries the file as a URL",
          len(got) == 1 and samepath(got[0], fa), str(got))


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
    # Disconnect (9.5.24) joined the Next bar as a CompactButton for the
    # same reason the others are: its label is translated, and a hard
    # setMaximumWidth truncates the longer languages.
    check("both bars' Up/Refresh (+ Drive) are CompactButtons",
          labels == ["+ Drive", "Disconnect", "Refresh", "Refresh",
                     "Up", "Up"], str(labels))
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


def test_font_zoom():
    """Ctrl + mouse-wheel zooms a bound explorer tree's item font (the
    shared zxnu_workers.bind_tree_font_zoom used by all four explorer
    panes): one point per notch, clamped, persisted per change; a plain
    wheel is left alone (it must keep scrolling)."""
    w, _calls = make_widget(local_start_dir=tdir("zoom_root"))
    saved = []
    bind_tree_font_zoom(w.next_view, saved.append)

    def wheel(dy, ctrl=True):
        ev = QWheelEvent(QPointF(20, 20), QPointF(20, 20), QPoint(),
                         QPoint(0, dy), Qt.NoButton,
                         Qt.ControlModifier if ctrl else Qt.NoModifier,
                         Qt.NoScrollPhase, False)
        QApplication.sendEvent(w.next_view.viewport(), ev)
        return ev

    base = w.next_view.font().pointSize()
    wheel(120)
    check("Ctrl+wheel-up grows the font by one point",
          w.next_view.font().pointSize() == base + 1 and saved == [base + 1])
    wheel(120, ctrl=False)
    check("a plain wheel changes nothing (left to the scroll machinery)",
          w.next_view.font().pointSize() == base + 1 and saved == [base + 1])
    # Trackpad-style partial deltas only add up to a step at a full notch.
    wheel(60)
    check("half a notch does nothing yet",
          w.next_view.font().pointSize() == base + 1)
    wheel(60)
    check("the second half completes the step",
          w.next_view.font().pointSize() == base + 2)
    for _ in range(40):
        wheel(-120)
    check("wheel-down clamps at the minimum",
          w.next_view.font().pointSize() == TREE_FONT_MIN_PT
          and saved[-1] == TREE_FONT_MIN_PT)


def test_session_strip():
    """The session strip (9.5.25): one vertical tab per connected Next
    down the Next pane's outer edge, shown only when there are two or
    more to choose between. A tab shows the machine's NAME when the user
    gave its address one, the bare address otherwise, and clicking it is
    the combo pick it stands for - same request, same guards.

    9.6.0: the SESSION ID rides on the tail, the way the combo composes
    it - one machine can hold several '.sync5 -L' sessions, and a MAME
    per disk image makes that ordinary, so without the id two sessions of
    one named machine were two identical tabs (reported). The full combo
    label goes in the tooltip."""
    names = {"10.0.0.7": "N-GO"}
    w, calls = make_widget(local_start_dir=tdir("strip_root"),
                           machine_name_for=names.get)

    w.on_peers((1, [(1, "10.0.0.5")]))
    check("hidden with a single Next (nothing to switch between)",
          not w.next_session_strip.isVisible() and not w._session_tabs)

    w.on_peers((1, [(1, "10.0.0.5"), (2, "10.0.0.7")]))
    connect_widget(w, calls)
    check("a tab per machine once two are on the line",
          len(w._session_tabs) == 2)
    check("named machines show the NAME, unnamed the address, both with "
          "the session id on the tail",
          [t._text for t in w._session_tabs] == ["10.0.0.5 #1", "N-GO #2"],
          [t._text for t in w._session_tabs])
    check("the tooltip carries the full combo label",
          [t.toolTip() for t in w._session_tabs]
          == ["10.0.0.5 #1", "10.0.0.7 #2 - N-GO"],
          [t.toolTip() for t in w._session_tabs])
    check("the driven machine's tab is the lit one",
          [t._active for t in w._session_tabs] == [True, False])

    # Clicking the other tab must make the same request the combo makes.
    w._session_tabs[1]._on_click()
    check("a tab click asks the worker to hand the baton over",
          ("select_next", 2) in drain(calls))

    # Naming a machine has to reach the tabs, not just the combo.
    names["10.0.0.5"] = "Attic Next"
    w._rebuild_session_strip()
    check("a new name reaches the tab",
          w._session_tabs[0]._text == "Attic Next #1",
          w._session_tabs[0]._text)

    # Down to one machine: nothing left to choose, so the strip goes away.
    w.on_peers((2, [(2, "10.0.0.7")]))
    check("the strip retires when only one Next remains",
          not w.next_session_strip.isVisible() and not w._session_tabs)


def test_disconnect_button():
    """The Disconnect button (9.5.24) is the bridge's /forceexit on the
    pane: it asks the DRIVEN Next to leave listen mode and end its
    application, sending the MARKED quit a plain server stop must never
    send. Enabled only while connected, confirmed before it fires."""
    w, calls = make_widget(local_start_dir=tdir("disc_root"))
    check("disabled while disconnected", not w.btn_disconnect.isEnabled())
    check("it sits between the machine name and the drive",
          w.btn_disconnect is not None)
    connect_widget(w, calls)
    check("enabled once a Next is connected", w.btn_disconnect.isEnabled())

    # Cancelling must send nothing at all.
    FakeMsg.answer = QMessageBox.Cancel
    w._disconnect_peer()
    check("a cancelled confirm queues nothing", drain(calls) == [])

    FakeMsg.answer = QMessageBox.Yes
    w._disconnect_peer()
    check("confirmed: the MARKED quit goes out, alone",
          drain(calls) == [("quit_app",)])
    check("and it is logged for the console",
          logged(calls, "leave listen mode and exit"))

    # A dropped link disables it again, so it can never fire into nothing.
    w.on_disconnected()
    check("disabled again after a disconnect",
          not w.btn_disconnect.isEnabled())
    FakeMsg.answer = QMessageBox.Yes
    w._disconnect_peer()
    check("and the action itself refuses while offline", drain(calls) == [])


def test_session_tab_menu():
    """Right-clicking a session tab (9.5.27) acts on THAT machine, not on
    whichever one the pane happens to drive.

    The prize is the benched case: "Disconnect" on a tab that is not the
    baton holder must leave through the per-session channel, because the
    shared queue is drained by the active session and would send the wrong
    Next away."""
    print("\n== session tab context menu ==")
    # The targeted hook mimics the worker's: it validates the sid under its
    # own roster and answers False for a machine that has left (which is
    # how zxnu_workers._enqueue_to behaves, and what the bridge maps to 410).
    seats, sent = {1, 2}, []

    def enqueue_to(sid, cmd):
        if sid not in seats:
            return False
        sent.append((sid, cmd))
        return True

    w, calls = make_widget(local_start_dir=tdir("tabmenu_root"),
                           enqueue_to=enqueue_to)
    w.on_peers((1, [(1, "10.0.0.5"), (2, "10.0.0.7")]))
    connect_widget(w, calls)
    check("a tab carries a right-click handler",
          all(t._on_menu is not None for t in w._session_tabs))

    # The BENCHED machine (sid 2): confirmed disconnect must ride the
    # targeted hook and leave the shared queue untouched.
    FakeMsg.answer = QMessageBox.Yes
    w._disconnect_session(2)
    check("a benched Next is disconnected through ITS OWN queue",
          sent == [(2, ("quit_app",))], str(sent))
    check("and nothing at all goes to the shared queue",
          drain(calls) == [])
    sent.clear()

    # The DRIVEN machine (sid 1) still uses the shared queue, exactly as
    # the top Disconnect button always has.
    w._disconnect_session(1)
    check("the driven Next still goes through the shared queue",
          drain(calls) == [("quit_app",)] and sent == [])

    # Cancelling sends nothing on either channel.
    FakeMsg.answer = QMessageBox.Cancel
    w._disconnect_session(2)
    check("a cancelled confirm sends nothing anywhere",
          drain(calls) == [] and sent == [])
    FakeMsg.answer = QMessageBox.Yes

    # The machine left between the right-click and the answer: the hook
    # refuses, and that must be SAID rather than silently dropped.
    seats.discard(2)
    calls["log"].clear()
    w._disconnect_session(2)
    check("a departed Next is reported, not silently dropped",
          sent == [] and logged(calls, "no longer on the line"))

    # No targeted hook wired at all (an older host): same honest refusal.
    seats.add(2)
    w._enqueue_to_raw = None
    calls["log"].clear()
    w._disconnect_session(2)
    check("without a targeted channel it refuses instead of misfiring",
          drain(calls) == [] and sent == []
          and logged(calls, "no longer on the line"))


def test_machine_colors():
    """The per-machine colour (9.5.27): picked in the name dialog, keyed by
    ADDRESS like the name, and painted on BOTH surfaces that identify a
    machine - the combo the pane drives from and the session tab."""
    print("\n== per-machine colours ==")
    names, colors = {}, {}
    w, calls = make_widget(
        local_start_dir=tdir("mcolor_root"),
        machine_name_for=names.get,
        on_machine_name_changed=lambda a, n: (
            names.__setitem__(a, n) if n else names.pop(a, None)),
        machine_color_for=colors.get,
        on_machine_color_changed=lambda a, c: (
            colors.__setitem__(a, c) if c else colors.pop(a, None)))
    w.on_peers((1, [(1, "10.0.0.5"), (2, "10.0.0.7")]))
    connect_widget(w, calls)
    check("untinted machines leave the tabs on the palette",
          [t._tint for t in w._session_tabs] == [None, None])
    check("and the combo keeps the app chrome",
          w.next_machine_combo.styleSheet() == "")

    # Pick a colour for the driven machine.
    FakeIdentity.queue = [("", QColor("#ff8800"), True)]
    w._on_machine_name_edit()
    check("the colour is saved for the ADDRESS",
          colors.get("10.0.0.5") == "#ff8800", str(colors))
    check("the machine's tab wears it",
          w._session_tabs[0]._tint is not None
          and w._session_tabs[0]._tint.name() == "#ff8800")
    check("the other machine's tab is untouched",
          w._session_tabs[1]._tint is None)
    check("the closed combo wears the driven machine's colour",
          "#ff8800" in w.next_machine_combo.styleSheet(),
          w.next_machine_combo.styleSheet())
    check("the popup entry is tinted too",
          w.next_machine_combo.itemData(
              0, Qt.ItemDataRole.BackgroundRole) is not None)
    check("a light tint gets dark text, so the label still reads",
          w.next_machine_combo.itemData(
              0, Qt.ItemDataRole.ForegroundRole).color().value() < 128)

    # The dialog opens on what is already stored, so a re-edit starts from
    # the current colour rather than from nothing.
    FakeIdentity.seen.clear()
    FakeIdentity.queue = []
    w._on_machine_name_edit()
    check("the editor opens on the stored colour",
          FakeIdentity.seen and FakeIdentity.seen[0][2] is not None
          and FakeIdentity.seen[0][2].name() == "#ff8800",
          str(FakeIdentity.seen))

    # The colour survives a reconnect under a fresh session id - it is
    # keyed by address, exactly like the name.
    w.on_peers((7, [(7, "10.0.0.5")]))
    check("a later session id keeps the colour",
          "#ff8800" in w.next_machine_combo.styleSheet())

    # Clearing it puts both surfaces back on the palette/chrome.
    FakeIdentity.queue = [("", None, True)]
    w._on_machine_name_edit()
    check("a cleared colour is forgotten",
          "10.0.0.5" not in colors and w.next_machine_combo.styleSheet() == "")

    # A garbled stored value must never make the pane unpaintable.
    colors["10.0.0.5"] = "not-a-colour"
    w.on_peers((7, [(7, "10.0.0.5")]))
    check("an unparseable stored colour is treated as no colour",
          w.next_machine_combo.styleSheet() == "")


def _local_bar_widgets(w):
    """The local nav row's widgets, in layout order.

    Found by hunting the QHBoxLayout that holds the name-filter box: the
    row is nested inside the local pane's container widget, so walking
    w.layout() alone never reaches it."""
    from PySide6.QtWidgets import QHBoxLayout
    for bar in w.findChildren(QHBoxLayout):
        items = [bar.itemAt(i).widget() for i in range(bar.count())]
        if w.local_filter_edit in items:
            return items
    return []


def _bar_order_detail(w):
    return str([(type(x).__name__, x.text() if hasattr(x, "text") else "")
                for x in _local_bar_widgets(w)])


def test_emulator_colors():
    """The per-emulator colour (9.6.0): right-click an emulator tab, pick a
    colour, and it is the SAME colour on the other strip and on the SD Card
    tab's Launch buttons - one map on the host, keyed by the emulator rather
    than by the label it happens to wear on a given surface."""
    print("\n== per-emulator colours ==")
    colors = {}
    w, calls = make_widget(
        local_start_dir=tdir("ecolor_root"),
        emulator_launchers=lambda: [("Mame", lambda: None),
                                    ("CSpect", lambda: None)],
        emulator_color_for=lambda n: colors.get(zxnu_config.emulator_color_key(n)),
        on_emulator_color_changed=lambda n, c: (
            colors.__setitem__(zxnu_config.emulator_color_key(n), c) if c
            else colors.pop(zxnu_config.emulator_color_key(n), None)))

    w.refresh_emulator_strip()
    check("untinted emulators leave the tabs on the palette",
          [t._tint for t in w._emulator_tabs] == [None, None])
    check("and every tab carries the right-click channel",
          all(t._on_menu is not None for t in w._emulator_tabs))

    # The host remembers a colour for MAME (whichever surface picked it).
    colors["mame"] = "#33cc55"
    w.refresh_emulator_strip()
    check("the emulator's tab wears the picked colour",
          w._emulator_tabs[0]._tint is not None
          and w._emulator_tabs[0]._tint.name() == "#33cc55",
          str(w._emulator_tabs[0]._tint))
    check("the other emulator is untouched", w._emulator_tabs[1]._tint is None)

    # A hand-edited cfg must never make the strip unpaintable.
    colors["mame"] = "not-a-colour"
    w.refresh_emulator_strip()
    check("an unparseable stored value reads as 'no colour', not a crash",
          w._emulator_tabs[0]._tint is None)

    # One colour per emulator whatever the surface calls it: the strip says
    # "Mame", Linux Flatpak mode says "Mame (flatpak)" and the SD Card tab's
    # button says "Launch Mame" - all one key, or the three surfaces would
    # each keep their own colour.
    check("every label an emulator wears resolves to one key",
          {zxnu_config.emulator_color_key(n) for n in
           ("Mame", "mame", "Mame (flatpak)", "Launch Mame")} == {"mame"}
          and zxnu_config.emulator_color_key("Launch CSpect") == "cspect")

    # The Launch buttons take the same colour as a stylesheet (they are real
    # QPushButtons, not the hand-painted tabs).
    css = zxnu_config.emulator_button_stylesheet("#33cc55")
    check("the Launch-button rule carries the colour and a readable label",
          "#33cc55" in css and "color:" in css, css[:80])
    check("and no colour means no rule at all - back to the app theme",
          zxnu_config.emulator_button_stylesheet(None) == ""
          and zxnu_config.emulator_button_stylesheet("not-a-colour") == "")


def test_local_drive_combo():
    """The local drive switcher (9.6.0) lives in THIS pane's nav row now.

    It used to be the NextSync tab's classic combo, left behind on a
    full-width row above the whole view: it stretched across the window,
    cost a row of height and pushed this pane out of line with the Next
    pane (reported). Here it sits where the SD Card tab's local pane has
    always had one - between Refresh and the Search label."""
    print("\n== local drive combo ==")
    root = tdir("drive_root")
    w, calls = make_widget(local_start_dir=root,
                           local_drives=["C:" + os.sep, "D:" + os.sep])
    combo = w.local_drive_combo
    check("the drives the host offered are in the combo",
          [combo.itemText(i) for i in range(combo.count())]
          == ["C:" + os.sep, "D:" + os.sep],
          str([combo.itemText(i) for i in range(combo.count())]))

    widgets = _local_bar_widgets(w)
    i_combo = widgets.index(combo) if combo in widgets else -1
    i_label = (widgets.index(w.local_filter_label)
               if w.local_filter_label in widgets else -1)
    check("it sits between Refresh and the Search label",
          i_combo >= 1 and i_combo == i_label - 1
          and isinstance(widgets[i_combo - 1], rex.CompactButton)
          and widgets[i_combo - 1].text() == "Refresh",
          _bar_order_detail(w))

    # Picking a drive navigates; it must NOT commit a new sync root.
    before_root = w._sync_root
    drive = os.path.splitdrive(os.path.abspath(root))[0]
    ix = combo.findText(drive + os.sep) if drive else -1
    if ix >= 0:
        combo.setCurrentIndex(ix)
        w._on_local_drive_picked()
        check("picking a drive browses it without touching the sync root",
              w._sync_root == before_root, repr(w._sync_root))

    # Navigating any other way keeps the combo honest.
    w.set_local_dir(root)
    if drive:
        check("the combo follows wherever the pane navigates",
              combo.currentText().rstrip("\\/").lower()
              == drive.rstrip("\\/").lower(), combo.currentText())

    # A host with nothing to switch between keeps it out of the way.
    w2, _ = make_widget(local_start_dir=tdir("drive_root2"))
    check("no drive list means no combo on screen",
          not w2.local_drive_combo.isVisible()
          and w2.local_drive_combo.count() == 0)


def test_emulator_strip():
    """The emulator strip (9.5.27): the session strip's mirror down the
    LEFT edge of the local pane, one tab per INSTALLED emulator, clicking
    one launching it exactly as the SD Card tab's button does.

    The rule that matters is "only what is detected": the strip itself is
    hidden when nothing is installed, so a machine without an emulator
    loses no width at all."""
    print("\n== emulator strip ==")
    found, launched = [], []
    w, calls = make_widget(
        local_start_dir=tdir("emu_root"),
        emulator_launchers=lambda: [
            (n, (lambda name=n: launched.append(name))) for n in found])

    w.refresh_emulator_strip()
    check("hidden while no emulator is installed",
          not w.local_emulator_strip.isVisible() and not w._emulator_tabs)

    # MAME only.
    found.append("Mame")
    w.refresh_emulator_strip()
    check("one tab for the one detected emulator",
          [t._text for t in w._emulator_tabs] == ["Mame"],
          str([t._text for t in w._emulator_tabs]))

    # Both.
    found.append("CSpect")
    w.refresh_emulator_strip()
    check("a tab each once both are found",
          [t._text for t in w._emulator_tabs] == ["Mame", "CSpect"],
          str([t._text for t in w._emulator_tabs]))
    check("the tabs are EmulatorTabs, never lit like a driven machine",
          all(isinstance(t, rex.EmulatorTab) and not t._active
              for t in w._emulator_tabs))

    # A click launches - deferred through a zero-timer, so pump the loop.
    w._emulator_tabs[1]._on_click()
    QApplication.processEvents()
    check("clicking a tab launches THAT emulator, with no arguments",
          launched == ["CSpect"], str(launched))

    # A fast DOUBLE click must not launch twice: QWidget's default
    # re-dispatches the double into mousePressEvent, and two launches
    # mount one disk image twice (review finding, 9.5.27).
    launched.clear()
    tab = w._emulator_tabs[1]
    for _ev_type in (QEvent.Type.MouseButtonPress,
                     QEvent.Type.MouseButtonRelease,
                     QEvent.Type.MouseButtonDblClick,
                     QEvent.Type.MouseButtonRelease):
        _ev = QMouseEvent(_ev_type, QPointF(5, 5),
                          tab.mapToGlobal(QPoint(5, 5)),
                          Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(tab, _ev)
    QApplication.processEvents()
    check("a double click launches exactly once",
          launched == ["CSpect"], str(launched))

    # An emulator that goes away takes its tab with it.
    found.remove("Mame")
    w.refresh_emulator_strip()
    check("an uninstalled emulator loses its tab",
          [t._text for t in w._emulator_tabs] == ["CSpect"])
    found.clear()
    w.refresh_emulator_strip()
    check("and the strip retires when the last one goes",
          not w.local_emulator_strip.isVisible() and not w._emulator_tabs)

    # A host hook that throws must not take the pane down with it.
    w._emulator_launchers = lambda: 1 / 0
    w.refresh_emulator_strip()
    check("a broken detection hook leaves the strip empty, not crashed",
          not w._emulator_tabs)


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


def test_sync5_resolve_task_survives_teardown():
    """_Sync5ResolveTask's one emit tolerates a signals object the widget's
    teardown has already destroyed - the app quitting while the resolve is
    still on the pool, which PySide otherwise prints as "Signal source has
    been deleted" out of QRunnable::run - while a live owner still gets its
    payload. Same shutdown race and same guard as HdfTaskWorker, pinned in
    tests/test_hdf_workers.py."""
    from shiboken6 import delete as _cpp_delete

    def failing():
        raise OSError("net")

    # Live owner: the answer arrives as ONE (sid, old_version, path, version,
    # reason) tuple, and a raising resolver becomes the internal-error payload
    # instead of an exception out of run().
    heard = []
    signals = rex._Sync5ResolveSignals()
    signals.resolved.connect(heard.append, Qt.DirectConnection)
    rex._Sync5ResolveTask(lambda: ("/tmp/sync5.dot", "5.2", ""), 1, "5.1",
                          signals).run()
    check("sync5 resolve: a live owner gets the payload",
          heard == [(1, "5.1", "/tmp/sync5.dot", "5.2", "")], str(heard))
    heard.clear()
    rex._Sync5ResolveTask(failing, 2, "5.1", signals).run()
    check("sync5 resolve: a raising resolver is reported, not raised",
          len(heard) == 1 and heard[0][:3] == (2, "5.1", None)
          and "internal error" in heard[0][4], str(heard))

    # Owner gone: the QObject is destroyed while the task is still running,
    # on both the happy and the failing path.
    for label, source in (("ok", lambda: ("/tmp/sync5.dot", "5.2", "")),
                          ("failing", failing)):
        gone = rex._Sync5ResolveSignals()
        task = rex._Sync5ResolveTask(source, 3, "5.1", gone)
        _cpp_delete(gone)
        what = f"sync5 resolve ({label}): emit after the owner's teardown does not raise"
        try:
            task.run()
            check(what, True)
        except RuntimeError as exc:
            check(what, False, str(exc))


def test_update_targets():
    """Where the remote updates go (9.7.2): the .sync5 dot always to
    c:/dot with no prompt at all for a known, older dot, and a yes/no
    only for a dot of unknown version; a ZX Next Remote .nex through the
    prompt, defaulting to c:/home, its explanation wrapped so the dialog
    cannot outgrow a laptop screen."""
    _nex = tempfile.NamedTemporaryFile(suffix=".nex", delete=False); _nex.close()
    w, calls = make_widget(
        sync5_update_source=lambda: ("C:/x/sync5", "5.9.1", ""),
        zxnr_update_source=lambda flavor: (_nex.name, "1.0.9", ""))
    connect_widget(w, calls)
    w.on_peers((1, [(1, "10.0.0.5")]))
    FakeInput.queue = []; FakeInput.seen = []
    w._on_sync5_resolved((1, "5.9.0", "C:/x/sync5", "5.9.1", ""))
    check("a known older .sync5 is updated straight into c:/dot, no prompt",
          calls["q_to"] == [(1, ("update_dot", "C:/x/sync5", "c:/dot", "5.9.1"))] and FakeInput.seen == [],
          str((calls["q_to"], FakeInput.seen)))
    check("...and the log says where it went and what happens next",
          any("c:/dot" in l and "sync5.bak" in l for l in calls["log"]), str(calls["log"][-1:]))
    calls["q_to"].clear()
    FakeMsg.answer = FakeMsg.No
    w._on_sync5_resolved((1, "", "C:/x/sync5", "5.9.1", ""))
    check("an unknown-version push still asks yes/no, and No sends nothing",
          calls["q_to"] == [] and FakeInput.seen == [], str(calls["q_to"]))
    FakeMsg.answer = FakeMsg.Yes
    w._on_sync5_resolved((1, "", "C:/x/sync5", "5.9.1", ""))
    check("...and Yes sends it to c:/dot as well",
          calls["q_to"] == [(1, ("update_dot", "C:/x/sync5", "c:/dot", "5.9.1"))] and FakeInput.seen == [],
          str(calls["q_to"]))
    calls["q_to"].clear()
    FakeInput.queue = [("c:/home/zxnextremote-n2n.nex", True)]; FakeInput.seen = []
    w._update_zxnr_on_session(1, "n2n", "1.0.5")
    check("a ZX Next Remote update still prompts, defaulting to c:/home",
          len(FakeInput.seen) == 1 and FakeInput.seen[0][4] == "c:/home/zxnextremote-n2n.nex",
          str(FakeInput.seen[0][4] if FakeInput.seen else None))
    body = FakeInput.seen[0][2] if FakeInput.seen else ""
    longest = max((len(l) for l in body.split(chr(10))), default=0)
    check("...with its explanation wrapped to dialog width",
          bool(body) and longest <= 80 and body.count(chr(10)) >= 6, f"longest line {longest}")
    check("...and the .nex file name never split at its hyphen",
          any("zxnextremote-n2n.nex" in l for l in body.split(chr(10))), body)
    check("...and the answer is split into folder and file for the macro",
          calls["q_to"] == [(1, ("update_dot", _nex.name, "c:/home", "1.0.9",
                                 "zxnextremote-n2n.nex", "ZXNextRemote", True))],
          str(calls["q_to"]))
    FakeInput.seen = []
    try:
        os.unlink(_nex.name)
    except OSError:
        pass


def test_update_prompt():
    """The connect-time update offer (9.7.2): once the ident arrives and
    this PC holds a newer .sync5 / ZXNR than the driven Next reported, the
    widget asks the host for ONE prompt per session+version, whose accept
    callable rides the top-bar link's update path. Silent when the toggle
    is off, when nothing newer is held, and for a listener that predates
    self-update; a new connection may be offered again."""
    from zxnu_config import ZX_NEXT_UNITE_DOTN_VERSION as _LOCAL
    _nex = tempfile.NamedTemporaryFile(suffix=".nex", delete=False); _nex.close()
    prompts, links = [], []
    w, calls = make_widget(
        sync5_update_source=lambda: ("C:/x/sync5", _LOCAL, ""),
        zxnr_update_source=lambda flavor: (_nex.name, "1.0.9", ""),
        update_prompt_enabled=lambda: True,
        on_update_prompt=lambda body, accept: prompts.append((body, accept)))
    connect_widget(w, calls)
    w.on_peers((1, [(1, "10.0.0.5")]))
    w._update_dot_on_session = lambda sid, old: links.append(("dot", sid, old))
    w._update_zxnr_on_session = lambda sid, flavor, old: links.append(("zxnr", sid, flavor, old))
    w.on_ident("sync", "5.9.0")
    check("an older .sync5 earns one prompt", len(prompts) == 1, str(len(prompts)))
    body = prompts[0][0] if prompts else ""
    check("...naming the old and new versions and the flavour",
          "5.9.0" in body and _LOCAL in body and ".sync5" in body, body)
    w.on_ident("sync", "5.9.0")
    check("the same session+version is not asked twice", len(prompts) == 1, str(len(prompts)))
    if prompts:
        prompts[0][1]()
    check("accepting updates the session it was offered for, through the dot-update path",
          links == [("dot", 1, "5.9.0")], str(links))
    # The accept is bound to the session it was OFFERED for: after the
    # baton moves to another Next, A's offer still updates A; once A has
    # left the roster, accepting says so instead of touching anyone.
    links.clear()
    w.on_peers((2, [(1, "10.0.0.5"), (2, "10.0.0.7")]))     # baton to B
    if prompts:
        prompts[0][1]()
    check("accepting A's offer after the baton moved still updates A",
          links == [("dot", 1, "5.9.0")], str(links))
    links.clear(); calls["log"].clear()
    w.on_peers((2, [(2, "10.0.0.7")]))                        # A left
    if prompts:
        prompts[0][1]()
    check("accepting an offer for a Next that left does nothing but say so",
          links == [] and any("no longer applies" in l for l in calls["log"]),
          str((links, calls["log"][-1:])))
    w.on_peers((1, [(1, "10.0.0.5")]))                        # A back, driven
    w.on_ident("sync", _LOCAL)
    check("an up-to-date dot is left alone", len(prompts) == 1, str(len(prompts)))
    w.on_ident("n2n", "1.0.5")
    check("an older ZX Next Remote earns its own prompt",
          len(prompts) == 2 and "ZX Next Remote n2n" in prompts[-1][0] and "1.0.9" in prompts[-1][0],
          prompts[-1][0] if prompts else "")
    if len(prompts) == 2:
        prompts[-1][1]()
    check("...whose accept updates that session through the ZXNR update path",
          links[-1:] == [("zxnr", 1, "n2n", "1.0.5")], str(links))
    w.on_ident("n2n", "1.0.2")
    check("a listener below the self-update floor is not offered one", len(prompts) == 2, str(len(prompts)))
    w.on_disconnected()
    w.on_connected()
    w.on_peers((1, [(1, "10.0.0.5")]))
    w.on_ident("sync", "5.9.0")
    check("a new connection is offered again", len(prompts) == 3, str(len(prompts)))
    # The Settings toggle, read at ident time: off means no prompt, while
    # the top bar's own link still offers the update.
    w2, calls2 = make_widget(
        sync5_update_source=lambda: ("C:/x/sync5", _LOCAL, ""),
        update_prompt_enabled=lambda: False,
        on_update_prompt=lambda body, accept: prompts.append((body, accept)))
    connect_widget(w2, calls2)
    w2.on_peers((1, [(1, "10.0.0.5")]))
    w2.on_ident("sync", "5.9.0")
    check("the toggle off means no prompt", len(prompts) == 3, str(len(prompts)))
    check("...while the top-bar link still offers the update",
          w2._sync5_update_offer() == ("5.9.0", _LOCAL) and "sync5-update" in w2._sync5_update_link_html())
    # A modal dialog up at ident time DEFERS the offer (a toast under it
    # could not be clicked): it surfaces once the dialog is gone.
    w3, calls3 = make_widget(
        sync5_update_source=lambda: ("C:/x/sync5", _LOCAL, ""),
        update_prompt_enabled=lambda: True,
        on_update_prompt=lambda body, accept: prompts.append((body, accept)))
    connect_widget(w3, calls3)
    w3.on_peers((1, [(1, "10.0.0.5")]))
    dlg = QDialog(); dlg.setModal(True); dlg.show()
    QApplication.processEvents()
    n0 = len(prompts)
    w3.on_ident("sync", "5.9.0")
    check("no offer while a modal dialog is up",
          len(prompts) == n0 and QApplication.activeModalWidget() is not None,
          str((len(prompts) - n0, QApplication.activeModalWidget())))
    dlg.close()
    end = time.monotonic() + 3.0
    while len(prompts) == n0 and time.monotonic() < end:
        QApplication.processEvents(); time.sleep(0.02)
    check("...and it surfaces once the dialog is gone", len(prompts) == n0 + 1, str(len(prompts) - n0))
    try:
        os.unlink(_nex.name)
    except OSError:
        pass


def main():
    logging.disable(logging.CRITICAL)
    app = QApplication(sys.argv)  # noqa: F841 — QWidget construction needs it
    try:
        test_pure_helpers()
        test_initial_and_connection_state()
        test_listing_and_rendering()
        test_navigation()
        test_multi_next_folders_follow_the_baton()
        test_machine_names_follow_the_address()
        test_session_tab_menu()
        test_update_prompt()
        test_update_targets()
        test_machine_colors()
        test_emulator_strip()
        test_emulator_colors()
        test_local_drive_combo()
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
        test_local_open_with_shell()
        test_drag_and_drop()
        test_drag_out_staging()
        test_arrow_pulse_and_overlay_resize()
        test_idle_status_provider()
        test_idle_details_provider()
        test_compact_buttons_fit_translated_labels()
        test_emulator_start_from_next()
        test_sync5_resolve_task_survives_teardown()
        test_select_all_skips_updir()
        test_os_protection_stops_and_explains()
        test_font_zoom()
        test_disconnect_button()
        test_session_strip()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
