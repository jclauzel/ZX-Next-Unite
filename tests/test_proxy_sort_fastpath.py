"""The local trees' sort fast path orders rows EXACTLY as before (9.7.39).

All three local file trees - the SD Card Utility's, the Remote Explorer's and
the NextSync Classic sync tree - sit on ColoredFileSystemModel (a data()
override in Python) under DotDotFirstProxyModel. Painting them made sorting
3-6x slower, because every comparison re-entered that Python data() override
through fileName() and Qt's own DisplayRole comparison. 9.7.39 reads names
with the base-class call and, for the Name and Type columns, compares the
model's sort_text() in Python when both texts are ASCII - the one case where
Python's comparison provably equals QString::compare's.

A faster sort that orders differently is a bug, so this suite pins the new
proxy against a REFERENCE proxy carrying the old lessThan verbatim, over the
SAME source model (so both stable sorts start from the same row order), on a
generated folder built to be awkward: mixed case, dotted and undotted names,
multi-dot names, folders, "..", leading punctuation, digits, non-ASCII names
that exercise every reason the fast path steps aside (İ, ß, final sigma,
emoji, a private-use character), and runs of equal sizes, equal dates and
equal extensions, so ties must keep the stable order Qt gives. Every column,
both orders, both case sensitivities, the locale-aware and other-sort-role
configurations, a nested folder, a live insert under dynamic sorting, and the
name filter are compared row for row.

new and ref are always sorted through the SAME sequence of sorts: Qt stable-
sorts a mapping's CURRENT rows, so the order ties end up in carries the sort
history, and only equal histories may be compared.

It also proves the fast path is actually TAKEN - an equal order would also
come from a fast path that never ran - by counting Python data() calls
during an all-ASCII sort: none with the fast path, many through the old one.

Run with: python tests/test_proxy_sort_fastpath.py
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

from PySide6.QtCore import (QCoreApplication, QDir, QSortFilterProxyModel,  # noqa: E402
                            Qt)
from PySide6.QtWidgets import QApplication, QFileSystemModel  # noqa: E402

app = QApplication.instance() or QApplication([])

from zxnu_remote_explorer import ColoredFileSystemModel  # noqa: E402
from zxnu_workers import DotDotFirstProxyModel  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def wait_until(cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


class ReferenceProxy(DotDotFirstProxyModel):
    """DotDotFirstProxyModel as it sorted and filtered BEFORE the fast path:
    lessThan verbatim (names through fileName(), everything else through
    Qt's own QSortFilterProxyModel.lessThan - called on the BASE class, never
    super(), which would reach the new code), and names in the filter through
    fileName() as well - the only thing the filter's change touched."""

    _source_name = staticmethod(lambda sm, ix: sm.fileName(ix))

    def lessThan(self, left, right):
        source_model = self.sourceModel()
        left_name = source_model.fileName(left)
        right_name = source_model.fileName(right)
        if left_name == "..":
            return True
        if right_name == "..":
            return False
        if isinstance(source_model, QFileSystemModel) and left.column() == 1:
            return source_model.size(left) < source_model.size(right)
        if isinstance(source_model, QFileSystemModel) and left.column() == 3:
            return source_model.lastModified(left) < source_model.lastModified(right)
        return QSortFilterProxyModel.lessThan(self, left, right)


# ── the fixture ──────────────────────────────────────────────────────────
ROOT = tempfile.mkdtemp(prefix="zxnu-sortfast-")
FILES = [
    # ASCII: case, extensions, multi-dot, digits, punctuation, hidden
    "Alpha.txt", "beta.TXT", "Gamma.bin", "gamma2.BIN", "delta", "Epsilon",
    "zeta.tar.gz", "Eta.a.b", "10.txt", "9.txt", "_under.txt", "~tilde.txt",
    ".hidden", "a.b", "A.C", "noext2", "UPPER.MD", "lower.md", "Mixed.Md",
    "x.y", "X.Y2", "same.txt", "SAME2.txt", "b.", "c..d", "space name.txt",
    # non-ASCII: every reason the fast path must step aside for Qt
    "Éclair.txt", "éclair2.txt", "straße.md", "STRASSE2.md", "İstanbul.txt",
    "ıi.txt", "ΟΔΟΣ.txt", "οδος2.txt", "😀.txt", "pua.txt", "日本.txt",
    "naïve.TXT", "ÄÖÜ.dat", "äöü2.dat", "ext.ÄBC", "ext2.äbc",
]
# Case-only twins exist only on a case-sensitive file system (CI's Linux),
# where they are the purest test of a case-insensitive TIE.
CASE_TWINS = ["alpha.txt", "ALPHA.TXT", "Beta.txt"]
FOLDERS = ["Dir1", "dir2", "DIR3.d", "sub.folder", "Ünïcode dir", "zzz"]
NESTED = ["Nb.txt", "na.TXT", "nC", "n.b.a", "Ñu.txt"]
SIZES = [0, 100, 100, 100, 5000, 7, 7, 100000]
STAMPS = [1_700_000_000, 1_700_000_000, 1_600_000_000, 1_700_000_000,
          1_650_000_000]


def _make(path, i):
    with open(path, "wb") as f:
        f.write(b"x" * SIZES[i % len(SIZES)])
    st = STAMPS[i % len(STAMPS)]
    os.utime(path, (st, st))


created = []
for i, name in enumerate(FILES):
    _make(os.path.join(ROOT, name), i)
    created.append(name)
for j, name in enumerate(CASE_TWINS):
    p = os.path.join(ROOT, name)
    if os.path.exists(p):          # case-insensitive FS: it IS another file
        continue
    _make(p, j)
    created.append(name)
for name in FOLDERS:
    os.makedirs(os.path.join(ROOT, name))
    created.append(name)
for i, name in enumerate(NESTED):
    _make(os.path.join(ROOT, "Dir1", name), i)
twins = [n for n in CASE_TWINS if n in created]
print(f"fixture: {len(created)} entries at the root"
      + (f", case-only twins {twins}" if twins else ", no case twins (case-insensitive FS)"))

model = ColoredFileSystemModel({})     # no colours: only text and order matter
model.setFilter(~QDir.NoDotAndDotDot | QDir.NoDot)
model.setRootPath(ROOT)
NESTED_DIR = os.path.join(ROOT, "Dir1")


def root_src():
    # Resolved at every use, never held: a QModelIndex is not persistent,
    # and one taken before the listing settles points at a row that has
    # since moved (the nested folder's did, while its siblings arrived).
    return model.index(ROOT)


def nested_src():
    return model.index(NESTED_DIR)


def _listed(parent, want):
    if model.canFetchMore(parent):
        model.fetchMore(parent)
    return model.rowCount(parent) >= want


check("the fixture folder is listed (with '..')",
      wait_until(lambda: _listed(root_src(), len(created) + 1)),
      str(model.rowCount(root_src())))
check("the nested folder is listed",
      wait_until(lambda: _listed(nested_src(), len(NESTED) + 1)),
      str(model.rowCount(nested_src())))
# Sizes arrive from the gatherer after the names; sorting before they land
# would compare zeros and prove nothing about the Size column.
_want = {os.path.join(ROOT, n): SIZES[i % len(SIZES)] for i, n in enumerate(FILES)}
check("every file's size has landed",
      wait_until(lambda: all(model.size(model.index(p)) == s
                             for p, s in _want.items())))


def _proxy(cls, cs=Qt.CaseInsensitive):
    p = cls(recursiveFilteringEnabled=True,
            filterRole=QFileSystemModel.FileNameRole)
    p.setSourceModel(model)
    p.setSortCaseSensitivity(cs)
    p.setDynamicSortFilter(True)
    return p


def _order(proxy, parent_src):
    parent = proxy.mapFromSource(parent_src)
    return [model.fileName(proxy.mapToSource(proxy.index(r, 0, parent)))
            for r in range(proxy.rowCount(parent))]


def _compare(label, new, ref, parent_of=root_src):
    a, b = _order(new, parent_of()), _order(ref, parent_of())
    first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
    check(label, a == b and len(a) > 1,
          f"first difference at row {first}: {a[first:first + 4] if first is not None else a}"
          f" vs {b[first:first + 4] if first is not None else b}")


# ── 1. sort_text IS the displayed text, for the two text columns ─────────
print("== sort_text equals the DisplayRole text ==")
_bad = []
for parent in (root_src(), nested_src()):
    for r in range(model.rowCount(parent)):
        for c in (0, 2):
            ix = model.index(r, c, parent)
            _shown = model.data(ix, Qt.ItemDataRole.DisplayRole)
            # Both ways the proxy calls it: on its own, and handed the name
            # lessThan has already read for its ".." test.
            for _st in (model.sort_text(ix),
                        model.sort_text(ix, model.fileName(ix))):
                if _st != _shown:
                    _bad.append((model.fileName(ix), c, _st, _shown))
        for c in (1, 3):
            if model.sort_text(model.index(r, c, parent)) is not None:
                _bad.append((r, c, "sort_text answered a numeric column"))
check("sort_text == data(DisplayRole) on every row, columns 0 and 2; "
      "None for 1 and 3", not _bad, str(_bad[:4]))
check("the base-name read equals fileName() on every row and column",
      all(DotDotFirstProxyModel._source_name(model, model.index(r, c, root_src()))
          == model.fileName(model.index(r, c, root_src()))
          for r in range(model.rowCount(root_src())) for c in range(4)))

# ── 2. the order, row for row, in every configuration ───────────────────
print("== identical order: every column, both orders ==")
ORDERS = ((Qt.AscendingOrder, "asc"), (Qt.DescendingOrder, "desc"))
for cs, cs_name in ((Qt.CaseInsensitive, "case-insensitive"),
                    (Qt.CaseSensitive, "case-sensitive")):
    new, ref = _proxy(DotDotFirstProxyModel, cs), _proxy(ReferenceProxy, cs)
    for col in range(4):
        for order, oname in ORDERS:
            new.sort(col, order)
            ref.sort(col, order)
            _compare(f"{cs_name}: column {col} {oname}", new, ref)
            _compare(f"{cs_name}: column {col} {oname}, nested folder",
                     new, ref, nested_src)

print("== configurations the fast path must step aside for ==")
for cfg_name, cfg in (
        ("locale-aware sorting", lambda p: p.setSortLocaleAware(True)),
        ("another sort role", lambda p: p.setSortRole(QFileSystemModel.FileNameRole))):
    new, ref = _proxy(DotDotFirstProxyModel), _proxy(ReferenceProxy)
    cfg(new)
    cfg(ref)
    for col in (0, 2):
        for order, oname in ORDERS:
            new.sort(col, order)
            ref.sort(col, order)
            _compare(f"{cfg_name}: column {col} {oname}", new, ref)

# ── 3. dynamic sorting: a row that ARRIVES lands in the same place ───────
print("== a file created while sorted ==")
new, ref = _proxy(DotDotFirstProxyModel), _proxy(ReferenceProxy)
new.sort(2, Qt.AscendingOrder)
ref.sort(2, Qt.AscendingOrder)
_late = os.path.join(ROOT, "Middle.late")
_make(_late, 3)
check("the new file reaches both proxies",
      wait_until(lambda: "Middle.late" in _order(new, root_src())
                 and "Middle.late" in _order(ref, root_src())))
_compare("dynamic sort: the arrival sorts into the same place", new, ref)

# ── 4. the name filter keeps the same rows ───────────────────────────────
print("== the name filter ==")
for pat in ("a", "TXT", "é", "ß", "zz*", "?.b", "no-such-name-anywhere", ""):
    new, ref = _proxy(DotDotFirstProxyModel), _proxy(ReferenceProxy)
    new.setFilterWildcard(pat)
    ref.setFilterWildcard(pat)
    new.sort(0, Qt.AscendingOrder)
    ref.sort(0, Qt.AscendingOrder)
    if len(_order(ref, root_src())) > 1:
        _compare(f"filter {pat!r}: same rows, same order", new, ref)
    else:
        check(f"filter {pat!r}: same rows",
              _order(new, root_src()) == _order(ref, root_src()))

# ── 5. the fast path is TAKEN ────────────────────────────────────────────
print("== the fast path actually runs ==")
ASCII_ROOT = tempfile.mkdtemp(prefix="zxnu-sortfast-ascii-")
_ascii = [n for n in FILES if n.isascii()] + ["DirA", "dirb"]
for i, n in enumerate(_ascii):
    if n.startswith(("Dir", "dir")):
        os.makedirs(os.path.join(ASCII_ROOT, n))
    else:
        _make(os.path.join(ASCII_ROOT, n), i)

CALLS = [0]


class CountingModel(ColoredFileSystemModel):
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        CALLS[0] += 1
        return super().data(index, role)


cm = CountingModel({})
cm.setFilter(~QDir.NoDotAndDotDot | QDir.NoDot)
cm.setRootPath(ASCII_ROOT)


def _cm_listed():
    ix = cm.index(ASCII_ROOT)
    if cm.canFetchMore(ix):
        cm.fetchMore(ix)
    return cm.rowCount(ix) >= len(_ascii) + 1


check("the all-ASCII folder is listed", wait_until(_cm_listed))


def _counted(cls, col):
    p = cls(recursiveFilteringEnabled=True,
            filterRole=QFileSystemModel.FileNameRole)
    p.setSourceModel(cm)
    p.setSortCaseSensitivity(Qt.CaseInsensitive)
    # A proxy sorts only the parents it has MAPPED, and maps one when it is
    # first asked about it - so ask first, or the sort below compares
    # nothing and the count proves nothing.
    _n = p.rowCount(p.mapFromSource(cm.index(ASCII_ROOT)))
    CALLS[0] = 0
    p.sort(col, Qt.AscendingOrder)
    return CALLS[0], _n


for col in range(4):
    (fast, n), (slow, _) = (_counted(DotDotFirstProxyModel, col),
                            _counted(ReferenceProxy, col))
    check(f"column {col}: no Python data() call sorting {n} ASCII rows "
          f"(the old path made {slow})", fast == 0 and slow > 2 * n,
          f"fast={fast} slow={slow} rows={n}")

shutil.rmtree(ROOT, ignore_errors=True)
shutil.rmtree(ASCII_ROOT, ignore_errors=True)
print()
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILURE(S): " + "; ".join(FAIL))
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
