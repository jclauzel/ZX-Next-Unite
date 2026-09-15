# -*- coding: utf-8 -*-
"""Every module the app imports must actually ship in the wheel.

`[tool.setuptools] py-modules` in pyproject.toml is an EXPLICIT list, which
turns off setuptools' auto-discovery: a new top-level module that nobody adds
to it is simply absent from the wheel and the sdist. Nothing local notices —
a source checkout imports it from the working directory — so the failure
surfaces only as a `ModuleNotFoundError` at startup for someone who installed
from PyPI (`pipx install zx-next-unite`).

That is not hypothetical: `zxnu_sync5_img` was added in 9.7.8 and left off the
list, and since `zxnu_main` imports it unconditionally at module level, every
wheel from 9.7.8 to 9.7.21 failed to start. Found in 9.7.22 while adding
`zxnu_pathhistorycombo` to the same list; this test is the tripwire that pins
it, by parsing the REAL import statements rather than guessing from filenames.
"""
import ast
import glob
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

FAILURES = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def listed_modules():
    """The names in pyproject.toml's py-modules, parsed as text.

    Deliberately not via a TOML parser: the point is to read the file the
    build back end reads, with no interpretation in between."""
    txt = io.open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8").read()
    block = re.search(r"py-modules\s*=\s*\[(.*?)\]", txt, re.S)
    assert block, "py-modules not found in pyproject.toml"
    return set(re.findall(r'"([^"]+)"', block.group(1)))


def local_module_names():
    """Top-level .py files in the repo root — the candidates for shipping."""
    out = set()
    for path in glob.glob(os.path.join(REPO, "*.py")):
        name = os.path.splitext(os.path.basename(path))[0]
        # The launcher shim is the PyInstaller entry point, not an importable
        # module, and setup.py-style files are not shipped either.
        if name in ("zx-next-unite", "setup"):
            continue
        out.add(name)
    return out


def imported_top_level(names):
    """Which of *names* are imported by another shipped module."""
    imported = set()
    for path in glob.glob(os.path.join(REPO, "*.py")):
        try:
            tree = ast.parse(io.open(path, encoding="utf-8").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in names:
                        imported.add(root)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                root = node.module.split(".")[0]
                if root in names:
                    imported.add(root)
    return imported


def test_py_modules_covers_every_import():
    print("\n== pyproject.toml py-modules vs the real imports ==")
    listed = listed_modules()
    local = local_module_names()
    imported = imported_top_level(local)

    check("py-modules lists a plausible number of modules", len(listed) >= 30,
          str(len(listed)))

    missing = sorted(imported - listed)
    check("every locally-imported top-level module ships in the wheel",
          not missing,
          "absent from py-modules, so a PyPI install cannot import them: "
          + ", ".join(missing))

    # The reverse: a name left behind after a module is deleted or renamed
    # would make the build fail outright, so catch it here instead.
    stale = sorted(m for m in listed if m not in local)
    check("py-modules names no module that no longer exists", not stale,
          ", ".join(stale))


def test_version_agrees_everywhere():
    print("\n== the version is the same in both places ==")
    # The release workflow gates on these matching (and on the git tag), so a
    # mismatch fails CI long after the commit that caused it.
    from zxnu_config import ZX_NEXT_UNITE_VERSION
    txt = io.open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8").read()
    m = re.search(r'^version = "([^"]+)"', txt, re.M)
    check("pyproject.toml carries a version", m is not None)
    if m:
        check("pyproject.toml matches ZX_NEXT_UNITE_VERSION",
              m.group(1) == ZX_NEXT_UNITE_VERSION,
              f"{m.group(1)} vs {ZX_NEXT_UNITE_VERSION}")


if __name__ == "__main__":
    test_py_modules_covers_every_import()
    test_version_agrees_everywhere()
    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S): " + "; ".join(FAILURES))
        sys.exit(1)
    print("RESULT: ALL PASS")
