"""tree_row_on_screen (zxnu_workers) against a REAL QTreeView.

The rule every reader of a local tree's selection asks since 9.7.42: a row
is a target only while the user can SEE it - under the view's current root,
with every folder between them expanded. It exists because the selection
model SURVIVES a re-root: a double-click INTO a folder leaves that folder's
row selected as the undrawn root, Up leaves the rows inside the now-collapsed
folder selected, collapsing a folder leaves its selected children selected.

This drives a real offscreen QTreeView over QFileSystemModel +
DotDotFirstProxyModel, rooted through root_tree_at exactly as the panes
root themselves, and compares the rule's verdict for every selected row with
the view's OWN layout: QTreeView.visualRect(ix) has a height only for rows
in its laid-out viewItems (height, not isEmpty - a deeply indented row under
an invalid root was measured to get a negative WIDTH while drawn). Headless,
no hdfmonkey: this runs in CI, where the offscreen phases that need an image
cannot.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDir, QItemSelectionModel, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractItemView, QApplication,  # noqa: E402
                               QFileSystemModel, QTreeView)

from zxnu_workers import DotDotFirstProxyModel, root_tree_at, tree_row_on_screen  # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'} {label}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        ok = False


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    tmp = tempfile.mkdtemp(prefix="zxnu-onscreen-")
    base = tmp.replace("\\", "/") + "/fx"
    work, sub, sib = base + "/work", base + "/work/sub", base + "/work/sib"
    deep = sub + "/deep"
    for d in (work, sub, sib, deep):
        os.makedirs(d)
    for f in (work + "/a.nex", work + "/b.nex", sub + "/c.nex", sub + "/d.nex",
              deep + "/e.nex", sib + "/s.nex"):
        open(f, "wb").close()

    # The tree as SdCardExplorerPane / the Classic pane / the Remote Explorer
    # build theirs: QFileSystemModel with the ".." row, the proxy on top.
    model = QFileSystemModel()
    model.setRootPath("/")
    model.setFilter(~QDir.NoDotAndDotDot | QDir.NoDot)
    proxy = DotDotFirstProxyModel(recursiveFilteringEnabled=True,
                                  filterRole=QFileSystemModel.FileNameRole)
    proxy.setSourceModel(model)
    proxy.setSortCaseSensitivity(Qt.CaseInsensitive)
    proxy.setDynamicSortFilter(True)
    view = QTreeView()
    view.setSortingEnabled(True)
    view.setSelectionMode(QAbstractItemView.ExtendedSelection)
    view.setModel(proxy)
    view.resize(800, 600)
    view.show()

    def load(path):
        six = model.index(path)
        assert six.isValid(), path
        for _ in range(100):
            if model.canFetchMore(six):
                model.fetchMore(six)
            QTest.qWait(30)
            if model.rowCount(six) > 0 and not model.canFetchMore(six):
                break
        QTest.qWait(40)

    for p in (base, work, sub, sib, deep):
        load(p)

    def pix(path):
        ix = proxy.mapFromSource(model.index(path))
        assert ix.isValid(), path
        return ix

    def root_at(path):
        assert root_tree_at(view, proxy, model, path), path
        QTest.qWait(30)
        app.processEvents()

    def select(*paths, clear=True):
        sm = view.selectionModel()
        if clear:
            sm.clearSelection()
        for p in paths:
            sm.select(pix(p), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        app.processEvents()

    def expand(path, on=True):
        view.setExpanded(pix(path), on)
        QTest.qWait(30)
        app.processEvents()

    def selected():
        return {model.filePath(proxy.mapToSource(i)): i
                for i in view.selectionModel().selectedRows(0)}

    def verdicts():
        """{path: (rule, oracle)} for every selected row."""
        view.doItemsLayout()
        app.processEvents()
        return {p: (tree_row_on_screen(view, i), view.visualRect(i).height() > 0)
                for p, i in selected().items()}

    def expect(label, want):
        """*want* maps path -> expected verdict; every selected row must be
        in it, the rule must say what is expected, and the oracle must agree."""
        got = verdicts()
        check(f"{label}: the selection is {sorted(os.path.basename(p) for p in want)}",
              set(got) == set(want), sorted(got))
        for p, exp in want.items():
            rule, oracle = got.get(p, (None, None))
            check(f"{label}: {os.path.basename(p)} -> {exp}", rule is exp, (rule, oracle))
            check(f"{label}: {os.path.basename(p)} agrees with the view's layout",
                  rule == oracle, (rule, oracle))

    # 1. top-level rows of the shown folder
    root_at(work)
    select(work + "/a.nex", work + "/b.nex")
    expect("top-level rows", {work + "/a.nex": True, work + "/b.nex": True})

    # 2/3. rows inside an expanded folder; then the folder collapses under them
    expand(sub)
    select(sub + "/c.nex", sub + "/d.nex")
    expect("rows under an EXPANDED folder", {sub + "/c.nex": True, sub + "/d.nex": True})
    expand(sub, False)
    check("premise: collapsing keeps the rows selected", len(selected()) == 2, len(selected()))
    expect("rows under a COLLAPSED folder", {sub + "/c.nex": False, sub + "/d.nex": False})

    # 4. double-click INTO a folder: its own row stays selected as the root
    select(sub)
    root_at(sub)
    check("premise: the folder's row is still selected after rooting into it",
          set(selected()) == {sub}, sorted(selected()))
    expect("the shown folder's own row", {sub: False})

    # 5. Up from inside it with files selected: the folder is collapsed above
    select(sub + "/c.nex", sub + "/d.nex")
    root_at(work)
    check("premise: the rows are still selected after rooting above them",
          len(selected()) == 2, len(selected()))
    check("premise: the folder is collapsed in the parent view", not view.isExpanded(pix(sub)))
    expect("rows inside the folder just left (collapsed)",
           {sub + "/c.nex": False, sub + "/d.nex": False})
    expand(sub)
    expect("...the same rows once it is expanded", {sub + "/c.nex": True, sub + "/d.nex": True})
    expand(sub, False)

    # 6. a selected row left behind when the view roots at a SIBLING folder
    select(work + "/a.nex")
    root_at(sib)
    expect("a row of a sibling folder", {work + "/a.nex": False})

    # 7. a deep row under two folders: every folder between must be expanded
    root_at(sub)
    expand(deep)
    select(deep + "/e.nex")
    expect("a deep row with its folder expanded, rooted at its grandparent", {deep + "/e.nex": True})
    root_at(work)
    expand(sub)
    expect("the deep row from one level up, both folders expanded", {deep + "/e.nex": True})
    expand(sub, False)
    expect("...with the middle folder collapsed", {deep + "/e.nex": False})
    expand(sub)
    expand(deep, False)
    expect("...with the innermost folder collapsed", {deep + "/e.nex": False})
    expand(deep)
    root_at(base)
    check("premise: 'work' is collapsed when first shown from above", not view.isExpanded(pix(work)))
    expect("...from two levels up with the top folder collapsed", {deep + "/e.nex": False})
    expand(work)
    expect("...from two levels up with every folder expanded", {deep + "/e.nex": True})

    # 8. the shown folder's PARENT (selected while visible, then rooted under it)
    select(work)
    root_at(sub)
    check("premise: the parent folder's row is still selected", set(selected()) == {work})
    expect("the shown folder's parent", {work: False})

    # 9. a '..' row is on screen (the rule does not judge names - readers do)
    root_at(work)
    up = next((proxy.index(r, 0, view.rootIndex())
               for r in range(proxy.rowCount(view.rootIndex()))
               if proxy.index(r, 0, view.rootIndex()).data() == ".."), None)
    check("premise: the shown folder lists a '..' row", up is not None)
    if up is not None:
        check("the '..' row counts as on screen (readers skip it by name)",
              tree_row_on_screen(view, up) is True)

    view.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
