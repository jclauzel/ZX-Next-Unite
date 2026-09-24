"""The displayed folder survives the name filter (DotDotFirstProxyModel).

The SD Card / NextSync / Remote Explorer local panes root their tree with
``view.setRootIndex(proxy.mapFromSource(model.index(path)))``. That index has
to EXIST, so a filter that rejects the displayed folder does not merely hide
rows - it destroys the view's root, and Qt reads an invalid root index as
"root at the model root". Typing a filter that matched neither the folder nor
anything inside it therefore emptied the pane, and CLEARING the filter did not
bring it back: a root index that has ceased to exist cannot be restored.

That shipped from 9.7.2 to 9.7.37 because the only coverage was offscreen UI
phase 1, which needs hdfmonkey and SKIPS on CI. This suite needs nothing but
PySide6 and a temp folder, so it runs everywhere - which is the point.

The same accident lived on one level down: recursive filtering accepts a
folder whose child is accepted, and every listed folder has a ".." child, so
a subfolder the user had once expanded was RESCUED by its own ".." under any
filter while an identical one never expanded was not. The proxy now shows
only the SHOWN folder's ".." (_is_shown_updir), and the last block below
pins what a filter shows no longer depending on where the user has been.

Run with: python tests/test_filter_keeps_root.py
"""
import os
import shutil
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from PySide6.QtCore import QDir, QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileSystemModel, QTreeView  # noqa: E402

app = QApplication.instance() or QApplication([])

from zxnu_workers import DotDotFirstProxyModel, root_tree_at  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


# ── the pure ancestor test ───────────────────────────────────────────────
print("== the kept-path comparison ==")
p = DotDotFirstProxyModel()
_WIN = os.name == "nt"

p.set_keep_path("C:/a/b/c" if _WIN else "/a/b/c")
base = "C:" if _WIN else ""
check("the kept folder itself is kept", p._is_kept(base + "/a/b/c"))
check("...and every ancestor of it", p._is_kept(base + "/a/b") and p._is_kept(base + "/a"))
check("a trailing slash does not matter", p._is_kept(base + "/a/b/"))
check("a DESCENDANT is not kept - contents are still filtered",
      not p._is_kept(base + "/a/b/c/d"))
# A bare startswith would make "/a/b" keep "/a/bb": the separator is what
# makes it an ancestor rather than a name that merely starts the same.
check("a sibling sharing a prefix is NOT kept",
      not p._is_kept(base + "/a/bb") and not p._is_kept(base + "/a/b/cc"))
check("an unrelated path is not kept", not p._is_kept(base + "/x/y"))
check("backslashes compare the same as forward slashes",
      p._is_kept((base + "/a/b").replace("/", "\\")))

if _WIN:
    check("Windows compares case-insensitively",
          p._is_kept("c:/A/B") and p._is_kept("C:/a/B/C"))
else:
    p2 = DotDotFirstProxyModel()
    p2.set_keep_path("/a/b/c")
    check("POSIX does NOT fold case (a sibling must not masquerade)",
          not p2._is_kept("/A/B"))

p.set_keep_path("")
check("no kept path: nothing is kept", not p._is_kept(base + "/a/b/c"))
check("...and the name gate is empty, so the hot path stays cheap",
      not p._keep_names)
p.set_keep_path(None)
check("None is accepted like an empty path", not p._is_kept("/a"))

# A bare ROOT must survive the trailing-slash strip. "/" reduced to "" turned
# the keep OFF entirely at the POSIX filesystem root - the fix was inert on
# Linux and macOS - and "/" was never recognised as the ancestor it is of
# every absolute path. Asserted on BOTH platforms: the root-preservation is
# not platform-dependent, and writing this as `"C:/" if _WIN else "/"` is how
# the hole reached CI unseen in the first place.
check("a bare POSIX root normalises to '/', not ''",
      DotDotFirstProxyModel._cmp_path("/") == "/",
      repr(DotDotFirstProxyModel._cmp_path("/")))
p.set_keep_path("/")
check("...and keeps itself", p._is_kept("/"))
p.set_keep_path("/home/someone")
check("the POSIX root is an ANCESTOR of a deeper keep",
      p._is_kept("/") and p._is_kept("/home"))
check("...and the name gate does not skip it (a root row is named '/')",
      "/" in p._keep_names, str(p._keep_names))
check("a sibling of the deeper keep is still refused",
      not p._is_kept("/home/someone-else"))

p.set_keep_path("C:/a/b")
check("a Windows drive is an ancestor of a keep below it", p._is_kept("C:/"))
# QFileSystemModel names a drive row "C:/", not "C:" - leaving that spelling
# out of the gate skipped the exemption for the row the whole mapping hangs
# off, and only recursive filtering happened to rescue it.
# Assert the PROPERTY, not a spelling: _cmp_path folds case on Windows and
# must not on POSIX, so the entry reads "c:/" here and "C:/" on the CI runner.
# Pinning one of them is the same platform-conditional trap that put the POSIX
# root hole past CI - and it is what made this line fail there.
check("...and the gate carries the drive row's own spelling",
      any(n.endswith("/") and n not in ("/",) for n in p._keep_names),
      str(p._keep_names))

# ── the real thing: a model, a proxy, a view ─────────────────────────────
print()
print("== a real tree, filtered ==")
ROOT = tempfile.mkdtemp(prefix="zxnu-keeproot")
try:
    HERE = os.path.join(ROOT, "pastedir")
    os.makedirs(os.path.join(HERE, "sub"))
    with open(os.path.join(HERE, "afile.txt"), "w") as f:
        f.write("x")
    HERE_FWD = HERE.replace("\\", "/")

    model = QFileSystemModel()
    model.setFilter(~QDir.NoDotAndDotDot | QDir.NoDot)
    proxy = DotDotFirstProxyModel(recursiveFilteringEnabled=True,
                                  filterRole=QFileSystemModel.FileNameRole)
    proxy.setSourceModel(model)
    view = QTreeView()
    view.setModel(proxy)
    model.setRootPath(HERE_FWD)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if model.rowCount(model.index(HERE_FWD)) >= 2:
            break
        time.sleep(0.01)

    def view_dir():
        return model.filePath(proxy.mapToSource(view.rootIndex()))

    check("root_tree_at roots the view",
          root_tree_at(view, proxy, model, HERE_FWD)
          and view_dir() == HERE_FWD, view_dir())

    # THE BUG: this used to invalidate the root index outright.
    proxy.setFilterWildcard("zzz-no-such-name")
    QCoreApplication.processEvents()
    check("a filter matching nothing leaves the view on its folder",
          view_dir() == HERE_FWD, view_dir())
    check("...on a VALID root index", view.rootIndex().isValid())
    def shown(parent):
        """Row names under *parent*, excluding the always-visible ".." row."""
        return [model.fileName(proxy.mapToSource(proxy.index(r, 0, parent)))
                for r in range(proxy.rowCount(parent))
                if model.fileName(
                    proxy.mapToSource(proxy.index(r, 0, parent))) != ".."]

    check("...while its contents are filtered away", not shown(view.rootIndex()),
          str(shown(view.rootIndex())))

    # ...and the recovery that used to be impossible.
    proxy.setFilterWildcard("")
    QCoreApplication.processEvents()
    check("clearing the filter stays on the same folder",
          view_dir() == HERE_FWD, view_dir())
    check("...with its contents back", len(shown(view.rootIndex())) >= 2,
          str(shown(view.rootIndex())))

    # A filter the folder's CONTENTS match must still work normally.
    proxy.setFilterWildcard("afile")
    QCoreApplication.processEvents()
    check("a matching filter keeps the folder and shows just the match",
          view_dir() == HERE_FWD and shown(view.rootIndex()) == ["afile.txt"],
          f"{view_dir()} rows={shown(view.rootIndex())}")
    proxy.setFilterWildcard("")

    # Navigating WHILE filtered: the keep-path has to be set before the map,
    # or the destination is unreachable for exactly the same reason.
    proxy.setFilterWildcard("zzz-no-such-name")
    sub = os.path.join(HERE, "sub").replace("\\", "/")
    check("navigating to a non-matching folder while filtered works",
          root_tree_at(view, proxy, model, sub) and view_dir() == sub,
          view_dir())
    proxy.setFilterWildcard("")

    # root_tree_at must not hand setRootIndex an invalid index.
    before = view_dir()
    gone = os.path.join(ROOT, "no-such-folder").replace("\\", "/")
    check("a missing folder is refused, not rooted on",
          root_tree_at(view, proxy, model, gone) is False)
    check("...and the pane stays where it was", view_dir() == before, view_dir())

    # A REFUSED move must not strip the protection from the folder still on
    # screen. Moving the keep first and only then discovering the mapping
    # failed reintroduced the original bug through the helper's error path.
    root_tree_at(view, proxy, model, HERE_FWD)
    proxy.setFilterWildcard("zzz-no-such-name")
    QCoreApplication.processEvents()
    kept_before = proxy.keep_path()
    check("premise: the pane is filtered and still on its folder",
          view_dir() == HERE_FWD and view.rootIndex().isValid(), view_dir())
    check("a refused move leaves the keep alone",
          root_tree_at(view, proxy, model, gone) is False
          and proxy.keep_path() == kept_before,
          f"{proxy.keep_path()!r} was {kept_before!r}")
    check("...so the folder on screen is still there",
          view_dir() == HERE_FWD and view.rootIndex().isValid(), view_dir())
    proxy.setFilterWildcard("")

    # The keep is stored in the MODEL's spelling, not the caller's: a keep
    # that does not compare equal to what filePath() reports protects nothing.
    root_tree_at(view, proxy, model, HERE.replace("/", "\\"))
    check("a backslash-spelled path is kept under the model's spelling",
          proxy.keep_path() == model.filePath(model.index(HERE_FWD)),
          f"{proxy.keep_path()!r}")

    # A LISTED subfolder is not rescued by its own "..". Recursive filtering
    # accepts a folder whose child is accepted; ".." used to be accepted
    # everywhere, so a subfolder that had been expanded once (its ".." in
    # the model) survived EVERY filter, while an identical one never
    # expanded did not. Only the shown folder's ".." is accepted now.
    print()
    print("== a listed subfolder under the filter ==")
    SUB = HERE_FWD + "/sub"
    EMPTY = HERE_FWD + "/empty"          # a sibling: listed, holding nothing
    os.makedirs(EMPTY)
    with open(os.path.join(HERE, "sub", "inner.txt"), "w") as f:
        f.write("x")
    root_tree_at(view, proxy, model, HERE_FWD)

    def src_names(path):
        ix = model.index(path)
        return [model.fileName(model.index(r, 0, ix))
                for r in range(model.rowCount(ix))]

    def listed(path, want):
        # Re-resolved per poll: an index taken before a re-list dangles.
        ix = model.index(path)
        if model.canFetchMore(ix):
            model.fetchMore(ix)
        return set(want) <= set(src_names(path))

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (
            listed(SUB, ("..", "inner.txt")) and listed(EMPTY, ("..",))
            and "empty" in src_names(HERE_FWD)):
        QCoreApplication.processEvents()
        time.sleep(0.01)
    check("premise: both subfolders are listed, each with a '..' in the model",
          ".." in src_names(SUB) and ".." in src_names(EMPTY),
          f"sub={src_names(SUB)} empty={src_names(EMPTY)}")

    def all_names(parent):
        return [model.fileName(proxy.mapToSource(proxy.index(r, 0, parent)))
                for r in range(proxy.rowCount(parent))]

    check("with no filter a subfolder shows no '..' of its own",
          sorted(all_names(proxy.mapFromSource(model.index(SUB)))) == ["inner.txt"]
          and all_names(proxy.mapFromSource(model.index(EMPTY))) == [],
          f"sub={all_names(proxy.mapFromSource(model.index(SUB)))} "
          f"empty={all_names(proxy.mapFromSource(model.index(EMPTY)))}")
    check("...while the shown folder keeps its '..'",
          ".." in all_names(view.rootIndex()), str(all_names(view.rootIndex())))

    proxy.setFilterWildcard("zzz-no-such-name")
    QCoreApplication.processEvents()
    check("a filter matching nothing hides the LISTED subfolders too",
          not shown(view.rootIndex()), str(shown(view.rootIndex())))
    check("...and keeps the shown folder's '..'",
          ".." in all_names(view.rootIndex()), str(all_names(view.rootIndex())))

    # Recursive filtering itself must still work for real content.
    proxy.setFilterWildcard("inner")
    QCoreApplication.processEvents()
    check("a match INSIDE a subfolder still shows that subfolder",
          shown(view.rootIndex()) == ["sub"], str(shown(view.rootIndex())))
    check("...with just the match under it, no '..'",
          all_names(proxy.mapFromSource(model.index(SUB))) == ["inner.txt"],
          str(all_names(proxy.mapFromSource(model.index(SUB)))))
    proxy.setFilterWildcard("")
    QCoreApplication.processEvents()

    # The empty folder, rooted: its ".." is the shown one now - the only way
    # back up out of a folder with nothing in it.
    check("rooting at an empty folder shows its '..'",
          root_tree_at(view, proxy, model, EMPTY)
          and all_names(view.rootIndex()) == [".."],
          str(all_names(view.rootIndex())))
    proxy.setFilterWildcard("zzz-no-such-name")
    QCoreApplication.processEvents()
    check("...and a filter never takes it away",
          view_dir() == EMPTY and all_names(view.rootIndex()) == [".."],
          f"{view_dir()} rows={all_names(view.rootIndex())}")
    proxy.setFilterWildcard("")
finally:
    shutil.rmtree(ROOT, ignore_errors=True)

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all kept-root filter checks passed")
sys.exit(0)
