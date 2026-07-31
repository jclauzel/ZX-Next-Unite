"""Startup tab-activation tests (zxnu_main._deferred_startup_tab_activation).

When the app is restored onto a tab, currentChanged has not been connected
yet, so __init__ activates that tab by hand. Two ways that went wrong:

1. It compared the live tab text to the tab-title CONSTANT with ==. Tab titles
   carry runtime suffixes — _set_tab_badge appends " (N)" and the spinner
   replaces the leading globe glyph — so the moment a badge is present the
   comparison fails and the restored tab is never initialised at all. The
   currentChanged handler has always used startswith(); the startup path must
   match it, otherwise the behaviour depends on whether a background fetch
   happened to finish first.

2. The ZXDB branch called the plain first-visit activation. Every launch also
   kicks off the Unite! "Latest" fan-out, which drives the same pane through
   zxdb_on_latest; ZXDB's newest rows are created before anyone uploads media,
   so that page renders as blank cells. The branch must go through
   _zxdb_startup_initial_load, which waits for the fan-out and then loads the
   pane's own (picture-bearing) page.

Both are asserted at the source level: the block is a closure inside
MainWindow.__init__, so it cannot be imported, and exercising it for real
would need the network.

Run with: python tests/test_startup_tab_activation.py
"""
import ast
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from zxnu_config import (  # noqa: E402
    ZX_NEXT_UNITE_TAB_TITLE_GETIT,
    ZX_NEXT_UNITE_TAB_TITLE_ZXART,
    ZX_NEXT_UNITE_TAB_TITLE_ZXDB,
)

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


source = open(os.path.join(REPO, "zxnu_main.py"), encoding="utf-8").read()
tree = ast.parse(source)
startup = find_function(tree, "_deferred_startup_tab_activation")
check("the deferred startup activation exists", startup is not None)
if startup is None:
    sys.exit(1)

# ---- 1. tab titles must be matched by PREFIX, never by equality ----------
eq_compares = []
prefix_calls = 0
for node in ast.walk(startup):
    if isinstance(node, ast.Compare) and any(
            isinstance(op, ast.Eq) for op in node.ops):
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        if any(x.startswith("ZX_NEXT_UNITE_TAB_TITLE_") for x in names):
            eq_compares.append(node.lineno)
    if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"):
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        if any(x.startswith("ZX_NEXT_UNITE_TAB_TITLE_") for x in names):
            prefix_calls += 1

check("no tab title is matched with == (badges/spinners break equality)",
      not eq_compares, f"== at lines {eq_compares}")
check("every restored tab is matched with startswith()",
      prefix_calls >= 5, f"only {prefix_calls} prefix matches")

# The suffixes that make equality fail, straight from the badge formatter's
# own format string ({base} ({count})).
for base in (ZX_NEXT_UNITE_TAB_TITLE_ZXDB, ZX_NEXT_UNITE_TAB_TITLE_GETIT,
             ZX_NEXT_UNITE_TAB_TITLE_ZXART):
    badged = f"{base} (20)"
    check(f"a badged title still prefix-matches {base[:18]!r}",
          badged.startswith(base) and badged != base, badged)

# ---- 2. the ZXDB branch must use the fan-out-aware startup load ----------
called = set()
for node in ast.walk(startup):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        called.add(node.func.attr)
check("the ZXDB branch uses _zxdb_startup_initial_load",
      "_zxdb_startup_initial_load" in called, str(sorted(called)))
check("...and not the plain first-visit activation, which the Unite! "
      "'Latest' fan-out would overwrite",
      "_zxdb_on_tab_activated" not in called, str(sorted(called)))

# ---- 3. that startup load must actually exist and be exported -----------
pane = ast.parse(open(os.path.join(REPO, "zxnu_zxdb_pane.py"),
                      encoding="utf-8").read())
fn = find_function(pane, "zxdb_startup_initial_load")
check("zxdb_startup_initial_load is defined in the ZXDB pane", fn is not None)
check("it is exported on the host as _zxdb_startup_initial_load",
      "host._zxdb_startup_initial_load" in open(
          os.path.join(REPO, "zxnu_zxdb_pane.py"), encoding="utf-8").read())
if fn is not None:
    body = ast.dump(fn)
    check("it waits for the in-flight fan-out rather than superseding it "
          "(a superseded fetch never fires on_complete, which Unite! awaits)",
          "_zxdb_search_loading" in body and "singleShot" in body)
    check("it leaves a user-driven query alone",
          "zxdb_search_input" in body)
    check("it has a bounded wait so the tab is never left empty",
          "_ticks" in body)

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all startup tab-activation checks passed")
sys.exit(0)
