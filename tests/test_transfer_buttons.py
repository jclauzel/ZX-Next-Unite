"""Both SD Card transfer-arrow buttons act on the WHOLE selection (9.7.42).

The '->:' button read host.left_file_explorer_selection_full_filename_path,
which the local tree's click handler fills from ONE row of the selection,
and ':<-' read host.image_selected_path, the CURRENT row only. Select two
files on either side, press the arrow, and exactly one file moved - while
Copy/Paste, Delete and drag-and-drop between the same two panes had read the
full selection for years.

These drive the REAL closures that build_transfer_clipboard_ops binds on a
fake host (pure-Python stand-ins for the tree, proxy and model - the calls
the readers make are selectedRows / mapToSource / fileName+filePath, plus
rootIndex / parent / isExpanded for the on-screen rule) and record what
reaches HdfTaskWorker in place of the thread pool. Headless: no display, no
hdfmonkey, no image - which is what lets CI see this class of bug, where the
offscreen phases that need hdfmonkey cannot.

The on-screen rule exists because the selection model SURVIVES a re-root
(measured on the real tree in review): a double-click INTO a folder leaves
its row selected as the undrawn root, Up leaves the rows inside the
now-collapsed folder selected. The fake tree expresses both: every row has
a parent id, the tree has a root id and a set of expanded ids.
"""
import inspect
import os
import platform
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import zxnu_sdcard_ops  # noqa: E402
from zxnu_workers import _run_get_task, _run_put_external_task, _run_put_task  # noqa: E402

ok = True
IS_WIN = platform.system() == "Windows"


def check(label, cond, detail=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'} {label}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        ok = False


# ── stand-ins for the Qt pieces the readers touch ───────────────────────────
# A local tree as QFileSystemModel + the proxy present it: every row has a
# parent id, the SHOWN folder is the tree's root id, and nested rows are on
# screen only while every folder between them and the root is expanded.
LOCAL_ROWS = {
    "above": ("C:", "C:/", True, None),             # the drive root
    "other": ("other", "C:/other", True, "above"),   # a sibling of the shown folder
    "work":  ("work", "C:/work", True, "above"),     # the SHOWN folder (root)
    "updir": ("..", "C:/work/..", True, "work"),
    "a":     ("a.nex", "C:/work/a.nex", False, "work"),
    "b":     ("b.nex", "C:/work/b.nex", False, "work"),
    "sub":   ("sub", "C:/work/sub", True, "work"),
    "sub_c": ("c.nex", "C:/work/sub/c.nex", False, "sub"),
    "sub_d": ("d.nex", "C:/work/sub/d.nex", False, "sub"),
}


class _Ix:
    """A stand-in QModelIndex: a row id (None = invalid) with a parent chain."""
    def __init__(self, row):
        self.row_ = row

    def isValid(self):
        return self.row_ is not None

    def parent(self):
        return _Ix(LOCAL_ROWS[self.row_][3] if self.row_ is not None else None)

    def __eq__(self, other):
        return isinstance(other, _Ix) and other.row_ == self.row_

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.row_)


class _SelModel:
    def __init__(self):
        self.rows = []

    def selectedRows(self, column=0):
        return [_Ix(r) for r in self.rows]


class _Tree:
    def __init__(self, root="work", expanded=()):
        self._sel = _SelModel()
        self.root = root
        self.expanded = set(expanded)
        self.current = None

    def selectionModel(self):
        return self._sel

    def rootIndex(self):
        return _Ix(self.root)

    def currentIndex(self):
        return _Ix(self.current)

    def isExpanded(self, ix):
        return ix.row_ in self.expanded

    def show(self):
        pass


class _Proxy:
    def mapToSource(self, ix):
        return ix


class _FsModel:
    """row -> (fileName, filePath, isDir), as QFileSystemModel answers."""
    def fileName(self, ix):
        return LOCAL_ROWS[ix.row_][0]

    def filePath(self, ix):
        return LOCAL_ROWS[ix.row_][1]

    def isDir(self, ix):
        return LOCAL_ROWS[ix.row_][2]


class _Checkbox:
    def isChecked(self):
        return False


class _Pane:
    def __init__(self, dest):
        self.dest = dest
        self.reloaded = []

    def image_dest_dir(self):
        return self.dest

    def image_reload_dir(self, path):
        self.reloaded.append(path)


class _Pool:
    def __init__(self):
        self.started = []

    def start(self, worker):
        self.started.append(worker)


class _Sig:
    """Records the connected slots so a test can fire the signal itself."""
    def __init__(self):
        self.slots = []

    def connect(self, fn, *_):
        self.slots.append(fn)

    def emit(self, *args):
        for fn in list(self.slots):
            fn(*args)


class _Signals:
    def __init__(self):
        self.progress = _Sig()
        self.status = _Sig()
        self.error = _Sig()
        self.cancelled = _Sig()
        self.finished = _Sig()


class RecWorker:
    """Records (fn, args) instead of running anything on a thread pool."""
    def __init__(self, fn, *args, **kwargs):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _Signals()

    def cancel(self):
        pass


class RecDialog:
    def __init__(self, title, parent=None):
        self.title = title
        self.cancel_requested = _Sig()

    def set_progress(self, *_):
        pass

    def set_status(self, *_):
        pass

    def mark_cancelled(self, *_):
        pass

    def exec(self):
        return 0

    def close(self):
        pass


def make_host(image_dir="/home", root="work", expanded=()):
    class Host:
        pass
    host = Host()
    host.treeview = _Tree(root, expanded)
    host.proxy_model = _Proxy()
    host.model = _FsModel()
    host.view_dir = LOCAL_ROWS[root][1]      # what local_current_view_dir answers
    host.right_disk_image_path = "C:/temp/test.img"
    host.settings_warn_image_nearly_full_checkbox = _Checkbox()
    host.sdcard_explorer = _Pane(image_dir)
    host.threadpool = _Pool()
    host.left_file_explorer_selection_file_name = ""
    host.left_file_explorer_selection_full_filename_path = ""
    host.image_selected_path = ""
    host.image_selected_is_dir = False
    host.image_selected_paths = []
    return host


def build(host, log, image_loaded=True):
    params = inspect.signature(zxnu_sdcard_ops.build_transfer_clipboard_ops).parameters
    kwargs = {name: (lambda *a, **k: None) for name, p in params.items()
              if p.kind is inspect.Parameter.KEYWORD_ONLY}
    kwargs["configuration_dictionary"] = {}
    kwargs["_right_disk_content"] = (lambda: "listing") if image_loaded else (lambda: "")
    kwargs["_check_image_writable"] = lambda *a, **k: None
    kwargs["local_current_view_dir"] = lambda: host.view_dir
    kwargs["add_main_log_window"] = lambda *a, **k: log.append(a[0] if a else "")
    gate = {"disabled": 0, "enabled": 0}
    kwargs["set_all_buttons_disabled"] = lambda: gate.__setitem__("disabled", gate["disabled"] + 1)
    kwargs["set_all_buttons_enabled"] = lambda: gate.__setitem__("enabled", gate["enabled"] + 1)
    zxnu_sdcard_ops.build_transfer_clipboard_ops(host, **kwargs)
    return gate


def fwd(p):
    return p.replace("\\", "/")


def run_put(select_rows, slot=("", ""), image_loaded=True, root="work",
            expanded=()):
    """Press '->:' with *select_rows* selected on the local tree (rooted at
    *root*, the folders in *expanded* open) and the click handler's single
    slot set to *slot* = (file_name, full_path)."""
    host = make_host(root=root, expanded=expanded)
    host.treeview.selectionModel().rows = list(select_rows)
    host.left_file_explorer_selection_file_name = slot[0]
    host.left_file_explorer_selection_full_filename_path = slot[1]
    log = []
    gate = build(host, log, image_loaded)
    host.transfert_content_from_disk_to_image()
    return host, gate, log


def run_get(selected_paths, primary=("", False), local_slot=""):
    """Press ':<-' with the image pane reporting *selected_paths* (its whole
    selection), *primary* as the current row and the local tree's single slot
    naming *local_slot* (the destination rule)."""
    host = make_host()
    host.image_selected_paths = list(selected_paths)
    host.image_selected_path, host.image_selected_is_dir = primary
    host.left_file_explorer_selection_full_filename_path = local_slot
    log = []
    gate = build(host, log)
    host.transfert_content_from_image_to_disk()
    return host, gate, log


def main():
    global ok
    real_worker = zxnu_sdcard_ops.HdfTaskWorker
    real_dialog = zxnu_sdcard_ops.HdfProgressDialog
    zxnu_sdcard_ops.HdfTaskWorker = RecWorker
    zxnu_sdcard_ops.HdfProgressDialog = RecDialog
    tmp = ""
    try:
        # ── '->:' local -> image ─────────────────────────────────────────
        host, gate, _ = run_put(["a", "b"], slot=("a.nex", "C:/work/a.nex"))
        check("->: starts one worker for a two-file selection",
              len(host.threadpool.started) == 1, len(host.threadpool.started))
        w = host.threadpool.started[0]
        check("->: runs the multi-item put worker", w.fn is _run_put_external_task,
              getattr(w.fn, "__name__", w.fn))
        items = w.args[-1]
        check("->: BOTH selected files travel",
              [(fwd(p), d) for p, d in items]
              == [("C:/work/a.nex", "/home/a.nex"), ("C:/work/b.nex", "/home/b.nex")],
              items)
        if IS_WIN:
            check("->: local paths are handed over with backslashes on Windows",
                  all("\\" in p and "/" not in p for p, _ in items), items)
        check("->: buttons were gated for the transfer",
              gate == {"disabled": 1, "enabled": 0}, gate)

        host, _, _ = run_put(["a", "sub"], slot=("a.nex", "C:/work/a.nex"))
        items = host.threadpool.started[0].args[-1]
        check("->: a selected FOLDER travels beside a file, named by its basename",
              [(fwd(p), d) for p, d in items]
              == [("C:/work/a.nex", "/home/a.nex"), ("C:/work/sub", "/home/sub")],
              items)

        host, _, _ = run_put(["updir", "b"], slot=("b.nex", "C:/work/b.nex"))
        items = host.threadpool.started[0].args[-1]
        check("->: the '..' row is never uploaded",
              [(fwd(p), d) for p, d in items] == [("C:/work/b.nex", "/home/b.nex")], items)

        host, _, _ = run_put(["b"], slot=("b.nex", "C:/work/b.nex"))
        items = host.threadpool.started[0].args[-1]
        check("->: a single selected file is exactly the old single upload",
              [(fwd(p), d) for p, d in items] == [("C:/work/b.nex", "/home/b.nex")], items)

        host, _, _ = run_put(["b"], slot=("b.nex", "C:/work/b.nex"))
        host2 = make_host(image_dir="/")
        host2.treeview.selectionModel().rows = ["a"]
        build(host2, [])
        host2.transfert_content_from_disk_to_image()
        items = host2.threadpool.started[0].args[-1]
        check("->: an image-root destination never doubles the slash",
              [d for _, d in items] == ["/a.nex"], items)

        # Nothing selected: the legacy slot still applies. After a navigation
        # it names the ROOTED folder with a blank file name, i.e. "upload
        # this folder's contents into the image folder" - kept as it was.
        host, gate, _ = run_put([], slot=("", "C:/work/"))
        check("->: no selection falls back to the legacy single slot",
              len(host.threadpool.started) == 1, len(host.threadpool.started))
        items = host.threadpool.started[0].args[-1]
        check("->: the legacy slot keeps its contents-into-folder destination",
              [(fwd(p), d) for p, d in items] == [("C:/work/", "/home/")], items)

        host, gate, _ = run_put(["updir"], slot=("", ""))
        check("->: only '..' selected and an empty slot starts nothing",
              not host.threadpool.started, host.threadpool.started)
        check("->: ...and the buttons are released again",
              gate == {"disabled": 1, "enabled": 1}, gate)

        host, gate, log = run_put(["a", "b"], slot=("a.nex", "C:/work/a.nex"), image_loaded=False)
        check("->: no image loaded refuses before touching the buttons",
              not host.threadpool.started and gate == {"disabled": 0, "enabled": 0}
              and any("load an image" in m for m in log), (gate, log))

        # ── stale selection after a re-root (found in review) ────────────
        # Double-click INTO 'sub': the pane is rooted at sub and sub's own
        # row is still selected - as the undrawn root. It must not travel;
        # the legacy slot (sub/, blank name) applies and the CONTENTS go in.
        host, _, _ = run_put(["sub"], slot=("", "C:/work/sub/"), root="sub")
        items = host.threadpool.started[0].args[-1]
        check("->: the navigated-into folder's own (undrawn) row never travels",
              [(fwd(p), d) for p, d in items] == [("C:/work/sub/", "/home/")], items)
        # Up from inside 'sub' with c and d selected: they now sit under a
        # COLLAPSED folder, off screen - the legacy slot (work/) applies.
        host, _, _ = run_put(["sub_c", "sub_d"], slot=("", "C:/work/"), root="work")
        items = host.threadpool.started[0].args[-1]
        check("->: rows inside a collapsed folder are off screen and never travel",
              [(fwd(p), d) for p, d in items] == [("C:/work/", "/home/")], items)
        # ...while with 'sub' EXPANDED the same rows are on screen and do.
        host, _, _ = run_put(["sub_c", "sub_d"], slot=("c.nex", "C:/work/sub/c.nex"),
                             root="work", expanded=("sub",))
        items = host.threadpool.started[0].args[-1]
        check("->: rows inside an EXPANDED folder are on screen and travel",
              [(fwd(p), d) for p, d in items]
              == [("C:/work/sub/c.nex", "/home/c.nex"), ("C:/work/sub/d.nex", "/home/d.nex")],
              items)
        host, _, _ = run_put(["a", "sub_c"], slot=("a.nex", "C:/work/a.nex"),
                             root="work", expanded=("sub",))
        items = host.threadpool.started[0].args[-1]
        check("->: a top-level row and an expanded nested row travel together",
              [(fwd(p), d) for p, d in items]
              == [("C:/work/a.nex", "/home/a.nex"), ("C:/work/sub/c.nex", "/home/c.nex")],
              items)
        # 'other' has a real basename, so only the on-screen rule can stop
        # it (the skeptic pass found the drive-root fixture vacuous here:
        # basename('C:/') is '' and the basename guard drops it first).
        host, gate, _ = run_put(["other"], slot=("", ""), root="work")
        check("->: a row ABOVE the shown folder never travels",
              not host.threadpool.started and gate == {"disabled": 1, "enabled": 1},
              (host.threadpool.started, gate))
        host, gate, _ = run_put(["work"], slot=("", ""), root="sub")
        check("->: the shown folder's own PARENT never travels either",
              not host.threadpool.started, host.threadpool.started)
        host, gate, _ = run_put(["above"], slot=("", ""), root="work")
        check("->: a drive root is dropped by the basename guard",
              not host.threadpool.started, host.threadpool.started)

        # ── where the pane is left after the upload ──────────────────────
        # A selection-driven upload leaves the pane on the folder it was
        # SHOWING; the legacy slot keeps its own rule (the slot folder, or a
        # file's parent) - which, for a folder, walks INTO it, and folders
        # sort first, so "file + folder, click the file last" used to end
        # inside the folder.
        tmp = tempfile.mkdtemp(prefix="zxnu-xfer-").replace("\\", "/")
        a_file = tmp + "/a.nex"
        open(a_file, "wb").close()
        sub_dir = tmp + "/sub"
        os.mkdir(sub_dir)
        real_root = zxnu_sdcard_ops.root_tree_at
        rerooted = []
        zxnu_sdcard_ops.root_tree_at = lambda view, proxy, model, path: rerooted.append(path)
        try:
            host, _, _ = run_put(["sub", "a"], slot=("a.nex", "C:/work/a.nex"))
            host.threadpool.started[0].signals.finished.emit()
            check("->: after a selection-driven upload the pane stays on the SHOWN folder",
                  rerooted == ["C:/work"], rerooted)
            check("->: ...and the image folder is re-listed",
                  host.sdcard_explorer.reloaded == ["/home"], host.sdcard_explorer.reloaded)
            rerooted.clear()
            host, _, _ = run_put([], slot=("", sub_dir + "/"))
            host.threadpool.started[0].signals.finished.emit()
            check("->: the legacy slot keeps its own re-root (the slot folder itself)",
                  rerooted == [sub_dir + "/"], rerooted)
            rerooted.clear()
            host, _, _ = run_put([], slot=("a.nex", a_file))
            host.threadpool.started[0].signals.finished.emit()
            check("->: the legacy slot re-roots at a file's parent",
                  rerooted == [tmp + "/"], rerooted)
        finally:
            zxnu_sdcard_ops.root_tree_at = real_root

        # ── ':<-' image -> local ─────────────────────────────────────────
        # The destination rule stats the local slot (a FILE's parent, a
        # FOLDER itself), so these two must exist on disk. Forward slashes,
        # as QFileSystemModel spells every path it hands the slot.
        two = [("/home/a.tap", False), ("/home/B", True)]
        host, gate, _ = run_get(two, primary=("/home/a.tap", False),
                                local_slot=a_file)
        check(":<- starts one worker for a two-entry selection",
              len(host.threadpool.started) == 1, len(host.threadpool.started))
        w = host.threadpool.started[0]
        check(":<- runs the get worker", w.fn is _run_get_task,
              getattr(w.fn, "__name__", w.fn))
        # (execute, image_path, items, dest_dir, dir_nav, is_windows)
        check(":<- BOTH selected entries travel, as (path, base_name)",
              w.args[2] == [("/home/a.tap", "a.tap"), ("/home/B", "B")], w.args[2])
        check(":<- a selected local FILE makes its folder the destination",
              fwd(w.args[3]) == tmp + "/", w.args[3])
        check(":<- buttons were gated for the transfer",
              gate == {"disabled": 1, "enabled": 0}, gate)

        host, _, _ = run_get(two, primary=("/home/B", True), local_slot=sub_dir)
        w = host.threadpool.started[0]
        check(":<- the CURRENT row being a folder changes nothing about what travels",
              w.args[2] == [("/home/a.tap", "a.tap"), ("/home/B", "B")], w.args[2])
        check(":<- a selected local FOLDER is the destination itself",
              fwd(w.args[3]) == sub_dir, w.args[3])

        host, _, _ = run_get([], primary=("/home/a.tap", False), local_slot=a_file)
        w = host.threadpool.started[0]
        check(":<- a host with only the single-row slot still downloads that row",
              w.args[2] == [("/home/a.tap", "a.tap")], w.args[2])

        host, _, _ = run_get([("", False), ("/home/x.tap", False)],
                             primary=("/home/x.tap", False), local_slot=a_file)
        w = host.threadpool.started[0]
        check(":<- a blank path in the selection list is skipped",
              w.args[2] == [("/home/x.tap", "x.tap")], w.args[2])

        host, gate, _ = run_get([], primary=("", False), local_slot=a_file)
        check(":<- nothing selected on the image starts nothing and releases the buttons",
              not host.threadpool.started and gate == {"disabled": 1, "enabled": 1},
              (host.threadpool.started, gate))

        host, gate, _ = run_get(two, primary=("/home/a.tap", False), local_slot="")
        check(":<- no local destination starts nothing and releases the buttons",
              not host.threadpool.started and gate == {"disabled": 1, "enabled": 1},
              (host.threadpool.started, gate))

        # ── Ctrl+C / Ctrl+X on the SD Card local tree ────────────────────
        # The same reader shape, the same stale selection; the clipboard
        # must only ever hold rows the user can see.
        def copy_sd(select_rows, root="work", expanded=(), current=None):
            host = make_host(root=root, expanded=expanded)
            host.treeview.selectionModel().rows = list(select_rows)
            host.treeview.current = current
            host._explorer_clipboard = None
            build(host, [])
            host._local_explorer_copy_selection("copy")
            clip = host._explorer_clipboard
            return [fwd(p) for p, _d in clip["items"]] if clip else None

        check("Ctrl+C copies every selected on-screen row",
              copy_sd(["a", "b"]) == ["C:/work/a.nex", "C:/work/b.nex"], copy_sd(["a", "b"]))
        check("Ctrl+C skips the '..' row",
              copy_sd(["updir", "a"]) == ["C:/work/a.nex"], copy_sd(["updir", "a"]))
        check("Ctrl+C after navigating INTO a folder copies nothing (its row is the undrawn root)",
              copy_sd(["sub"], root="sub") is None, copy_sd(["sub"], root="sub"))
        check("Ctrl+C never copies rows inside a collapsed folder",
              copy_sd(["sub_c", "sub_d"]) is None, copy_sd(["sub_c", "sub_d"]))
        check("Ctrl+C copies rows inside an EXPANDED folder",
              copy_sd(["sub_c"], expanded=("sub",)) == ["C:/work/sub/c.nex"],
              copy_sd(["sub_c"], expanded=("sub",)))
        check("Ctrl+C with no selection falls back to a visible current row",
              copy_sd([], current="b") == ["C:/work/b.nex"], copy_sd([], current="b"))
        check("Ctrl+C's current-row fallback ignores a stale off-screen current row",
              copy_sd([], root="sub", current="sub") is None
              and copy_sd([], current="sub_c") is None,
              (copy_sd([], root="sub", current="sub"), copy_sd([], current="sub_c")))

        # ── Ctrl+C / Ctrl+X on the NextSync Classic tree ─────────────────
        def copy_classic(select_rows, root="work", expanded=(), current=None):
            host = make_host()
            host.nextsync_treeview = _Tree(root, expanded)
            host.nextsync_treeview.selectionModel().rows = list(select_rows)
            host.nextsync_treeview.current = current
            host.nextsync_model = _Proxy()
            host.nextsync_filesystem_model = _FsModel()
            host._explorer_clipboard = None
            build(host, [])
            host._nextsync_explorer_copy_selection("copy")
            clip = host._explorer_clipboard
            return [fwd(p) for p, _d in clip["items"]] if clip else None

        check("Classic Ctrl+C copies every selected on-screen row",
              copy_classic(["a", "b"]) == ["C:/work/a.nex", "C:/work/b.nex"])
        check("Classic Ctrl+C after navigating INTO a folder copies nothing",
              copy_classic(["sub"], root="sub") is None)
        check("Classic Ctrl+C never copies rows inside a collapsed folder",
              copy_classic(["sub_c"]) is None)
        check("Classic Ctrl+C's current-row fallback honours the rule both ways",
              copy_classic([], current="a") == ["C:/work/a.nex"]
              and copy_classic([], current="sub_c") is None)

        # ── the Delete / Zip reader of the SD Card local tree ────────────
        # build_local_explorer_ops' _local_explorer_selected_paths_or is
        # what Delete and Zip act on; exposed on the host for exactly this.
        def delete_reader(select_rows, root="work", expanded=()):
            host = make_host(root=root, expanded=expanded)
            host.treeview.selectionModel().rows = list(select_rows)
            params = inspect.signature(zxnu_sdcard_ops.build_local_explorer_ops).parameters
            kwargs = {name: (lambda *a, **k: None) for name, p in params.items()
                      if p.kind is inspect.Parameter.KEYWORD_ONLY}
            zxnu_sdcard_ops.build_local_explorer_ops(host, **kwargs)
            return [fwd(p) for p in host._local_explorer_selected_paths_or("FALLBACK")]

        check("Delete's reader names every selected on-screen row",
              delete_reader(["a", "sub"]) == ["C:/work/a.nex", "C:/work/sub"])
        check("Delete's reader never names the folder just entered",
              delete_reader(["sub"], root="sub") == ["FALLBACK"], delete_reader(["sub"], root="sub"))
        check("Delete's reader never names rows inside a collapsed folder",
              delete_reader(["sub_c", "sub_d"]) == ["FALLBACK"])
        check("Delete's reader names rows inside an EXPANDED folder",
              delete_reader(["sub_c"], expanded=("sub",)) == ["C:/work/sub/c.nex"])

        # ── source tripwires ─────────────────────────────────────────────
        host = make_host()
        build(host, [])
        put_names = host.transfert_content_from_disk_to_image.__code__.co_names
        check("->: closure routes through _run_put_external_task, never the single-path worker",
              "_run_put_external_task" in put_names and "_run_put_task" not in put_names,
              put_names)
        put_src = inspect.getsource(host.transfert_content_from_disk_to_image)
        check("->: closure reads the selection model, not the click handler's slot alone",
              "_local_tree_selected_paths()" in put_src)
        reader_src = inspect.getsource(zxnu_sdcard_ops.build_transfer_clipboard_ops)
        check("->: the reader applies the on-screen rule to every selected row",
              "if not _local_row_on_screen(ix):" in reader_src)
        check("the arrows' rule IS the shared tree_row_on_screen",
              "return tree_row_on_screen(host.treeview, ix)" in reader_src)
        # Every reader of a local tree's selection asks the shared rule: a
        # reader that reads selectedRows without it is the hole coming back.
        import re as _re
        here = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.dirname(here)

        def _src(name):
            with open(os.path.join(root_dir, name), encoding="utf-8") as f:
                return f.read()

        def _reader_guarded(src, start_marker, rows_call):
            seg = src[src.index(start_marker):]
            seg = seg[:seg.index(rows_call) + 400]
            return "tree_row_on_screen(" in seg or "_local_row_on_screen(" in seg

        ops_src = _src("zxnu_sdcard_ops.py")
        for label, marker in (
            ("SD Card Ctrl+C", "def _local_explorer_copy_selection("),
            ("Classic Ctrl+C", "def _nextsync_explorer_copy_selection("),
            ("SD Card Delete/Zip", "def _local_explorer_selected_paths_or("),
            ("SD Card arrows", "def _local_tree_selected_paths("),
        ):
            check(f"{label} reader asks the on-screen rule",
                  _reader_guarded(ops_src, marker, "selectedRows(0)"))
        check("SD Card drag-out asks the on-screen rule",
              _reader_guarded(_src("zxnu_main.py"), "def _local_start_drag(", "selectedRows(0)"))
        check("Classic drag-out asks the on-screen rule",
              _reader_guarded(_src("zxnu_nextsync_pane.py"), "def _nextsync_drag_paths(", "selectedRows(0)"))
        check("Remote Explorer local pane asks the on-screen rule",
              _reader_guarded(_src("zxnu_remote_explorer.py"), "def _selected_local_paths(", "selectedRows(0)"))
        check("no local-tree reader still reads selectedRows bare",
              not _re.search(r"selectedRows\(0\)\):\n(?!\s+if not (tree_row_on_screen|_local_row_on_screen)\()\s+(src|source_ix) = (host\.|self\.)(proxy_model|nextsync_model|local_proxy)\.mapToSource",
                             ops_src + _src("zxnu_main.py") + _src("zxnu_nextsync_pane.py")))
        get_src = inspect.getsource(host.transfert_content_from_image_to_disk)
        check(":<- closure reads the whole selection list",
              "_image_selected_items()" in get_src
              and "[(host.image_selected_path, base_name)]" not in get_src)
    finally:
        zxnu_sdcard_ops.HdfTaskWorker = real_worker
        zxnu_sdcard_ops.HdfProgressDialog = real_dialog
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
