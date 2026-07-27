"""Headless unit tests for the hdfmonkey transfer/delete worker bodies in
zxnu_workers (_run_get_task / _run_put_task / _run_put_external_task /
_run_delete_task, their scan helpers, is_directory and the small path/zip
helpers). This is the SD-Card tab's operation layer — the code HdfTaskWorker
runs on the thread pool — which until now was only exercised end-to-end by
the offscreen UI suite, and only with a real hdfmonkey on a real image.

Here hdfmonkey is replaced by an in-memory fake image (a nested dict behind
an execute_hdf_monkey-compatible callable), so every branch — including the
full-disk detection, the mkdir-already-exists ls fallback and the
cancel/error paths a healthy image never hits — runs deterministically with
no external tool, no HDF file and no display."""
import logging
import os
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PySide6.QtCore import QCoreApplication, Qt
from zxnu_config import UP_DIRECTORY
from zxnu_workers import (HdfTaskSignals, _run_delete_task, _run_get_task,
                          _run_put_external_task, _run_put_task,
                          get_parent_root_directory_splited, is_directory,
                          zip_unique_name)

ok = True


def check(label, cond, detail=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'} {label}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        ok = False


class FakeResult:
    def __init__(self, returncode, stdout=b""):
        self.returncode = returncode
        self.stdout = stdout


class FakeImage:
    """In-memory stand-in for a FAT image driven through hdfmonkey. Dirs are
    dicts, files are bytes. Mimics the behaviours the worker bodies rely on:
    ls prints "[DIR]\\tname" / "<size>\\tname" lines, mkdir cannot create
    intermediate directories, rm refuses a non-empty directory, and a full
    disk answers "Access denied" to put/mkdir."""

    def __init__(self, tree=None, full_disk=False):
        self.tree = {} if tree is None else tree
        self.full_disk = full_disk
        self.calls = []           # (command, *extra_argv) in execution order
        self.fail_paths = set()   # rm/put on these: rc=1 with a generic error
        self.raise_paths = set()  # any command on these: raises

    def node(self, path):
        node = self.tree
        for part in [p for p in path.split("/") if p]:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def parent(self, path):
        parts = [p for p in path.split("/") if p]
        if not parts:
            return None, None
        node = self.tree
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                return None, None
            node = node[part]
        return (node, parts[-1]) if isinstance(node, dict) else (None, None)

    # Signature-compatible with MainWindow's execute_hdf_monkey.
    def execute(self, command, image_path, additional_args="", silent=False,
                extra_argv=None, prompt_if_missing=True):
        argv = list(extra_argv or [])
        self.calls.append((command,) + tuple(argv))
        path = argv[0] if argv else ""
        if path in self.raise_paths:
            raise RuntimeError(f"boom on {path}")
        if command == "ls":
            node = self.node(path)
            if not isinstance(node, dict):
                return FakeResult(1, b"Not a directory\n")
            lines = [f"[DIR]\t{n}" if isinstance(c, dict) else f"{len(c)}\t{n}"
                     for n, c in sorted(node.items())]
            return FakeResult(0, ("\n".join(lines) + "\n").encode())
        if command == "rm":
            if path in self.fail_paths:
                return FakeResult(1, b"Input/output error\n")
            node = self.node(path)
            if isinstance(node, dict) and node:
                return FakeResult(1, b"Directory not empty\n")
            parent, leaf = self.parent(path)
            if parent is None or leaf not in parent:
                return FakeResult(1, b"Not found\n")
            del parent[leaf]
            return FakeResult(0)
        if command == "mkdir":
            if self.full_disk:
                return FakeResult(1, b"Access denied\n")
            parent, leaf = self.parent(path)
            if parent is None:
                return FakeResult(1, b"Not found\n")   # no intermediate dirs
            if leaf in parent:
                return FakeResult(1, b"File exists\n")
            parent[leaf] = {}
            return FakeResult(0)
        if command == "get":
            src, dst = argv
            node = self.node(src)
            if not isinstance(node, bytes):
                return FakeResult(1, b"Not found\n")
            with open(dst, "wb") as f:
                f.write(node)
            return FakeResult(0)
        if command == "put":
            src, dst = argv
            if self.full_disk:
                return FakeResult(1, b"Access denied\n")
            if dst in self.fail_paths:
                return FakeResult(1, b"Input/output error\n")
            parent, leaf = self.parent(dst)
            if parent is None:
                return FakeResult(1, b"Not found\n")
            with open(src, "rb") as f:
                parent[leaf] = f.read()
            return FakeResult(0)
        raise AssertionError(f"unexpected hdfmonkey command: {command}")


def make_signals():
    """An HdfTaskSignals plus a dict recording every emission (direct
    connections: the worker bodies run synchronously right here)."""
    sig = HdfTaskSignals()
    rec = {"progress": [], "status": [], "error": []}
    sig.progress.connect(rec["progress"].append, Qt.DirectConnection)
    sig.status.connect(rec["status"].append, Qt.DirectConnection)
    sig.error.connect(rec["error"].append, Qt.DirectConnection)
    return sig, rec


IS_WINDOWS = os.name == "nt"
DIR_NAV = "\\" if IS_WINDOWS else "/"
IMG = "fake.hdf"


def test_path_helpers():
    check("split nested", get_parent_root_directory_splited("/games/sub/f.txt")
          == ("/games/sub", "f.txt"))
    check("split root file", get_parent_root_directory_splited("/f.txt")
          == ("", "f.txt"))
    check("zip name free", zip_unique_name("demo", set()) == "demo.zip")
    check("zip name taken", zip_unique_name("demo", {"demo.zip"}) == "demo (2).zip")
    check("zip name taken twice",
          zip_unique_name("demo", {"demo.zip", "demo (2).zip"}) == "demo (3).zip")


def test_is_directory():
    img = FakeImage({"games": {"lev": {"a.bin": b"AA"}}, "boot.bas": b"10 GO"})
    check("dir at root", is_directory(img.execute, IMG, "/games") is True)
    check("file at root", is_directory(img.execute, IMG, "/boot.bas") is False)
    check("nested dir", is_directory(img.execute, IMG, "/games/lev") is True)
    check("nested file", is_directory(img.execute, IMG, "/games/lev/a.bin") is False)
    check("missing entry", is_directory(img.execute, IMG, "/nowhere") is False)


def test_delete():
    # Full recursive delete: files first, then dirs deepest-first, so every
    # rm hits an already-empty directory. UP_DIRECTORY rows are filtered and
    # doubled slashes collapsed.
    img = FakeImage({"games": {"a.txt": b"A",
                               "lev": {"b.txt": b"BB", "deep": {"c.txt": b"C"}}},
                     "top.txt": b"T"})
    sig, rec = make_signals()
    _run_delete_task(sig, threading.Event(), img.execute, IMG,
                     [UP_DIRECTORY, "/games", "//top.txt"])
    check("delete empties tree", img.tree == {}, repr(img.tree))
    rm_order = [c[1] for c in img.calls if c[0] == "rm"]
    check("delete rm order (files, then dirs deepest-first)",
          rm_order == ["/games/a.txt", "/games/lev/b.txt",
                       "/games/lev/deep/c.txt", "/top.txt",
                       "/games/lev/deep", "/games/lev", "/games"],
          repr(rm_order))
    check("delete no errors", rec["error"] == [])
    check("delete progress marquee then 100",
          rec["progress"][0] == -1 and rec["progress"][-1] == 100,
          repr(rec["progress"]))

    # An rm that RAISES must emit error and keep deleting the rest; the
    # parents of the survivor then legitimately fail rm (not empty) — which
    # the worker treats as silent best-effort, exactly like real hdfmonkey.
    img = FakeImage({"games": {"a.txt": b"A", "lev": {"b.txt": b"BB"}}})
    img.raise_paths.add("/games/lev/b.txt")
    sig, rec = make_signals()
    _run_delete_task(sig, threading.Event(), img.execute, IMG, ["/games"])
    check("delete error emitted once",
          len(rec["error"]) == 1 and "Failed deleting" in rec["error"][0],
          repr(rec["error"]))
    check("delete continues past error",
          img.tree == {"games": {"lev": {"b.txt": b"BB"}}}, repr(img.tree))

    # Cancel mid-phase-2: items 1 and 2 are deleted (cancel lands during
    # item 2's status), the rest survive.
    img = FakeImage({f"f{i}.txt": b"X" for i in range(4)})
    sig, rec = make_signals()
    cancel = threading.Event()
    sig.status.connect(lambda s: cancel.set() if s.startswith("Deleting (2/") else None,
                       Qt.DirectConnection)
    _run_delete_task(sig, cancel, img.execute, IMG,
                     ["/f0.txt", "/f1.txt", "/f2.txt", "/f3.txt"])
    check("delete cancel stops early",
          sorted(img.tree) == ["f2.txt", "f3.txt"], repr(sorted(img.tree)))


def test_get():
    img = FakeImage({"games": {"a.bin": b"AAA", "lev": {"b.bin": b"BB"}},
                     "boot.bas": b"BOOT"})
    dest = tempfile.mkdtemp(prefix="hdfw_get_")
    try:
        sig, rec = make_signals()
        _run_get_task(sig, threading.Event(), img.execute, IMG,
                      [("/boot.bas", "boot.bas"), ("/games", "games")],
                      dest, DIR_NAV, IS_WINDOWS)

        def rd(*parts):
            p = os.path.join(dest, *parts)
            return open(p, "rb").read() if os.path.isfile(p) else None
        check("get root file", rd("boot.bas") == b"BOOT")
        check("get dir file", rd("games", "a.bin") == b"AAA")
        check("get nested file", rd("games", "lev", "b.bin") == b"BB")
        check("get no errors", rec["error"] == [])
        check("get progress marquee then 100",
              rec["progress"][0] == -1 and rec["progress"][-1] == 100,
              repr(rec["progress"]))
        get_dsts = [c[2] for c in img.calls if c[0] == "get"]
        check("get local paths forward-slashed",
              get_dsts and all("\\" not in d for d in get_dsts), repr(get_dsts))
    finally:
        shutil.rmtree(dest, ignore_errors=True)

    # A get that raises must emit error and keep downloading the rest.
    img = FakeImage({"a.bin": b"A", "b.bin": b"B"})
    img.raise_paths.add("/a.bin")
    dest = tempfile.mkdtemp(prefix="hdfw_get2_")
    try:
        sig, rec = make_signals()
        _run_get_task(sig, threading.Event(), img.execute, IMG,
                      [("/a.bin", "a.bin"), ("/b.bin", "b.bin")],
                      dest, DIR_NAV, IS_WINDOWS)
        check("get error emitted once",
              len(rec["error"]) == 1 and "Failed downloading" in rec["error"][0],
              repr(rec["error"]))
        check("get continues past error",
              open(os.path.join(dest, "b.bin"), "rb").read() == b"B")
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def test_put_single():
    local = tempfile.mkdtemp(prefix="hdfw_put_")
    try:
        src = os.path.join(local, "up.bin")
        with open(src, "wb") as f:
            f.write(b"PAYLOAD")

        img = FakeImage()
        sig, rec = make_signals()
        _run_put_task(sig, threading.Event(), img.execute, lambda p: None,
                      IMG, src, "/up.bin")
        check("put single lands", img.tree == {"up.bin": b"PAYLOAD"},
              repr(img.tree))
        check("put single progress", rec["progress"] == [0, 100],
              repr(rec["progress"]))
        check("put single no errors", rec["error"] == [])

        # Generic failure (no "Access denied"): plain error, no cancel.
        img = FakeImage()
        img.fail_paths.add("/up.bin")
        sig, rec = make_signals()
        cancel = threading.Event()
        _run_put_task(sig, cancel, img.execute,
                      lambda p: "SHOULD NOT BE CONSULTED", IMG, src, "/up.bin")
        check("put generic failure error",
              rec["error"] == ["Failed uploading: up.bin"], repr(rec["error"]))
        check("put generic failure no cancel", not cancel.is_set())

        # Access denied + the probe confirms a full disk: its message is THE
        # error, and the whole task is cancelled.
        img = FakeImage(full_disk=True)
        sig, rec = make_signals()
        cancel = threading.Event()
        _run_put_task(sig, cancel, img.execute, lambda p: "SD image is full",
                      IMG, src, "/up.bin")
        check("put full-disk error", rec["error"] == ["SD image is full"],
              repr(rec["error"]))
        check("put full-disk cancels", cancel.is_set())

        # Access denied but the probe is inconclusive (None): fall through to
        # the generic error, no cancel.
        img = FakeImage(full_disk=True)
        sig, rec = make_signals()
        cancel = threading.Event()
        _run_put_task(sig, cancel, img.execute, lambda p: None,
                      IMG, src, "/up.bin")
        check("put denied-but-not-full error",
              rec["error"] == ["Failed uploading: up.bin"], repr(rec["error"]))
        check("put denied-but-not-full no cancel", not cancel.is_set())
    finally:
        shutil.rmtree(local, ignore_errors=True)


def test_put_directory():
    local = tempfile.mkdtemp(prefix="hdfw_putd_")
    try:
        os.makedirs(os.path.join(local, "updir", "sub", "deeper"))
        for rel, data in (("one.txt", b"1"), (os.path.join("sub", "two.txt"), b"22"),
                          (os.path.join("sub", "deeper", "three.txt"), b"333")):
            with open(os.path.join(local, "updir", rel), "wb") as f:
                f.write(data)

        # /games already exists in the image: its mkdir fails "File exists"
        # and must be rescued by the ls fallback, then the rest is created.
        img = FakeImage({"games": {}})
        sig, rec = make_signals()
        _run_put_task(sig, threading.Event(), img.execute, lambda p: None,
                      IMG, os.path.join(local, "updir"), "/games/updir")
        check("put dir tree lands",
              img.tree == {"games": {"updir": {
                  "one.txt": b"1",
                  "sub": {"two.txt": b"22",
                          "deeper": {"three.txt": b"333"}}}}},
              repr(img.tree))
        check("put dir no errors", rec["error"] == [])
        mkdirs = [c[1] for c in img.calls if c[0] == "mkdir"]
        check("put dir mkdir each segment once (dedupe)",
              sorted(mkdirs) == ["/games", "/games/updir", "/games/updir/sub",
                                 "/games/updir/sub/deeper"]
              and len(mkdirs) == len(set(mkdirs)), repr(mkdirs))
        check("put dir ls fallback rescued existing /games",
              ("ls", "/games") in img.calls)
        put_dsts = [c[2] for c in img.calls if c[0] == "put"]
        check("put dir image paths forward-slashed",
              len(put_dsts) == 3 and all("\\" not in d for d in put_dsts),
              repr(put_dsts))

        # Full disk detected during the mkdir phase: cancel before any put.
        img = FakeImage(full_disk=True)
        sig, rec = make_signals()
        cancel = threading.Event()
        _run_put_task(sig, cancel, img.execute, lambda p: "SD image is full",
                      IMG, os.path.join(local, "updir"), "/dst")
        check("put dir full-disk error", rec["error"] == ["SD image is full"],
              repr(rec["error"]))
        check("put dir full-disk cancels before uploads",
              cancel.is_set() and not [c for c in img.calls if c[0] == "put"])
    finally:
        shutil.rmtree(local, ignore_errors=True)


def test_put_external():
    local = tempfile.mkdtemp(prefix="hdfw_pute_")
    try:
        for n in ("a.bin", "b.bin"):
            with open(os.path.join(local, n), "wb") as f:
                f.write(n.encode())

        img = FakeImage()
        sig, rec = make_signals()
        _run_put_external_task(sig, threading.Event(), img.execute,
                               lambda p: None, IMG,
                               [(os.path.join(local, "a.bin"), "/a.bin"),
                                (os.path.join(local, "b.bin"), "/b.bin")])
        check("put external both land",
              img.tree == {"a.bin": b"a.bin", "b.bin": b"b.bin"},
              repr(img.tree))

        # Cancel during the first item's status: its upload is skipped and
        # the second item never starts.
        img = FakeImage()
        sig, rec = make_signals()
        cancel = threading.Event()
        sig.status.connect(lambda s: cancel.set(), Qt.DirectConnection)
        _run_put_external_task(sig, cancel, img.execute, lambda p: None, IMG,
                               [(os.path.join(local, "a.bin"), "/a.bin"),
                                (os.path.join(local, "b.bin"), "/b.bin")])
        check("put external cancel skips everything",
              img.tree == {} and not [c for c in img.calls if c[0] == "put"],
              repr(img.calls))
    finally:
        shutil.rmtree(local, ignore_errors=True)


def main():
    QCoreApplication(sys.argv)   # signals only — no display, no event loop
    # The failure-path tests make the workers call logging.error on purpose;
    # keep the console output to the PASS/FAIL lines.
    logging.disable(logging.CRITICAL)
    test_path_helpers()
    test_is_directory()
    test_delete()
    test_get()
    test_put_single()
    test_put_directory()
    test_put_external()
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
