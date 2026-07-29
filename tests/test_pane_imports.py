"""Import/free-variable tripwire for the extracted gallery-pane modules.

zxnu_zxdb_pane.py and zxnu_zxart_pane.py were strangled out of
MainWindow.__init__: each is a ~3k-line build_<pane>_pane(host, ...) function
whose only external names come from (a) its keyword-only hook params and
(b) module-level imports. Some of those imports are underscore-prefixed helpers
that `from zxnu_gallery import *` / `from zxnu_media import *` do NOT re-export
(those modules carry no catch-all __all__), so they must be listed explicitly.
Drop one and the pane breaks silently at runtime inside a worker whose errors
are swallowed — exactly the trap that once killed all gallery image loading.

This test reparses each module and asserts the build function has ZERO
unresolved free variables: every Name it loads resolves to a param, a
module-level binding, a star-exported name, or a builtin. It also confirms the
modules import cleanly and expose their builder + the host outputs.

Run with: python tests/test_pane_imports.py
"""
import ast
import builtins
import importlib
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

# A QApplication may be needed before importing Qt-backed modules that touch
# QPixmap at import time; create one defensively.
from PySide6.QtWidgets import QApplication  # noqa: E402
QApplication.instance() or QApplication([])

FAIL = []
def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def _all_bound(nodes):
    b = set()
    for root in nodes:
        for n in ast.walk(root):
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                b.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                b.add(n.name)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                b.add(n.name)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    b.add((a.asname or a.name).split(".")[0])
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                b.update(n.names)
        for n in ast.walk(root):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                a = n.args
                for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
                    b.add(arg.arg)
                if a.vararg:
                    b.add(a.vararg.arg)
                if a.kwarg:
                    b.add(a.kwarg.arg)
    return b


def _loads(nodes):
    s = set()
    for root in nodes:
        for n in ast.walk(root):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                s.add(n.id)
    return s


def unresolved_free_vars(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    # Module dunders always resolve at runtime (__file__ is used by
    # build_hdfmonkey_install_ops to locate the app directory).
    mod_bound = set(dir(builtins)) | {"__file__", "__name__"}
    star_mods = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            star_mods.append(node.module)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                mod_bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            mod_bound.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for nn in ast.walk(t):
                    if isinstance(nn, ast.Name):
                        mod_bound.add(nn.id)
    star_names = set()
    for m in star_mods:
        mod = importlib.import_module(m)
        exported = getattr(mod, "__all__", None)
        if exported is None:
            exported = [n for n in dir(mod) if not n.startswith("_")]
        star_names.update(exported)
    missing = []
    for fn in [n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name.startswith("build_")]:
        params = {a.arg for a in (fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs)}
        avail = mod_bound | star_names | params | _all_bound(fn.body)
        missing += sorted(f"{fn.name}:{n}" for n in _loads(fn.body)
                          if n not in avail and n != "self")
    return missing


PANES = [
    ("zxnu_getit_pane", "build_getit_pane",
     ["getit_run_search", "getit_on_latest", "getit_on_random",
      "_getit_open_gallery_viewer"]),
    ("zxnu_zxdb_pane", "build_zxdb_pane",
     ["zxdb_run_search", "zxdb_on_latest", "zxdb_on_random",
      "_zxdb_open_gallery_viewer"]),
    ("zxnu_zxart_pane", "build_zxart_pane",
     ["zxart_run_search", "zxart_on_latest", "zxart_on_random",
      "_zxart_open_gallery_viewer"]),
    # Extraction #7: the Unite! (AllInOne) tab — two builders in one module
    # (widget layer at the tab-construction spot, ops layer after itch.io).
    ("zxnu_unite_pane", ["build_unite_pane", "build_unite_ops"], []),
    # Extraction #8: the Settings tab.
    ("zxnu_settings_pane", "build_settings_pane", []),
    # Extraction #9: the NextSync tab (widgets + wiring; op closures injected).
    ("zxnu_nextsync_pane", "build_nextsync_pane", []),
    # Extraction #10: the optional itch.io tab (built only when itch-dl is
    # importable; the builder itself must always import cleanly).
    ("zxnu_itchio_pane", "build_itchio_pane", []),
    # Extraction #11: the Favorites tab + per-pane Classic/Retro routing —
    # three builders, each called at its chunk's historical position.
    ("zxnu_favorites_pane",
     ["build_favorites_helpers", "build_favorites_pane",
      "build_favorites_ops"], []),
    # Extraction #12: the emulator + self-update operation layer (CSpect/MAME
    # setters + launchers, MAME/CSpect/app update chains, viewer wiring).
    ("zxnu_emulator_ops", "build_emulator_ops", []),
    # Extraction #13: the hdfg.cfg restore/save pipeline.
    ("zxnu_config_io", "build_config_io", []),
    # Extraction #15: the NextSync op layer (server prepare/start, classic
    # explorer ops, server job/warnings/conflict) — three builders.
    ("zxnu_nextsync_ops",
     ["build_nextsync_server_start", "build_nextsync_explorer_ops",
      "build_nextsync_server_job"], []),
    # Extraction #16: the SD-card op layer (utils/load pipeline, image
    # delete/rename, local-pane ops, transfers + shared clipboard) — four
    # builders.
    ("zxnu_sdcard_ops",
     ["build_sdcard_utils", "build_image_edit_ops",
      "build_local_explorer_ops", "build_transfer_clipboard_ops"], []),
    # Extraction #17: cross-tab ops (autocomplete, cross-search fan-out,
    # tab badges/spinners, autocomplete animation, on_tab_changed).
    ("zxnu_tab_ops", "build_tab_ops", []),
]

for modname, funcnames, _outputs in PANES:
    if isinstance(funcnames, str):
        funcnames = [funcnames]
    path = os.path.join(REPO, modname + ".py")
    check(f"{modname}.py exists", os.path.isfile(path))
    mod = importlib.import_module(modname)
    for funcname in funcnames:
        check(f"{modname} imports and exposes {funcname}",
              callable(getattr(mod, funcname, None)))
    missing = unresolved_free_vars(path)
    check(f"{modname}: build function(s) have no unresolved free vars",
          not missing, ", ".join(missing))

print()
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILURE(S)")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
