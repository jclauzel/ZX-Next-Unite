"""The merged "Transfer tools" tab (9.7.38).

"TOOL: SD Card Utility" and "TOOL: NextSync" used to be two sibling main
tabs. They are one tab now, whose sub-tab bar carries the SD Card Utility
ahead of the two NextSync experiences. Four things had to be true for that
to be safe, and each of them is silent when it breaks - which is what this
file exists for:

1. COPY/CUT/PASTE AND DRAG & DROP survive because the merge REPARENTS the
   existing containers rather than rebuilding them. Both ride assigned
   event-handler attributes on the views plus setAcceptDrops, and there is
   not one QShortcut in the app - the single construct a reparent would
   break. A future "simplification" that rebuilds a view, or a QShortcut
   added with a window context, would break them without a visible symptom.

2. THE SUB-TAB INDEXES MOVED. Every historical `currentIndex() == 0` on
   nextsync_mode_tabs meant "Remote Explorer"; index 0 is the SD Card
   Utility now. A missed site does not crash - it quietly does the wrong
   thing, on the wrong view.

3. THE SAVED TAB IS MIGRATED. Every existing user has a raw index in their
   cfg, and every index from 2 up moved one to the left.

4. THE WIZARD ADDRESSES BOTH TOOLS BY TAB. Two tour steps and two in-depth
   guides pointed at the two old tabs; merged, they must be told apart by
   the sub-tab or one silently shadows the other.

Run with: python tests/test_transfer_tab.py
"""
import ast
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def src(name):
    return io.open(os.path.join(REPO, name), encoding="utf-8").read()


# The application modules (not tests, not the vendored/stale copies).
APP_MODULES = [f for f in sorted(os.listdir(REPO))
               if f.endswith(".py") and (f.startswith("zxnu_")
                                         or f in ("zx-next-unite.py", "espemu.py",
                                                  "nextsync5.py"))]

print("== constants ==")
import zxnu_config as zc  # noqa: E402

check("the merged tab has its own title constant",
      getattr(zc, "ZX_NEXT_UNITE_TAB_TITLE_TRANSFER", "").strip() != "")
# Qt reads a lone "&" in a tab label as a mnemonic and DRAWS IT AWAY. The
# escape must live in the constant, because tabText() returns what was set
# and every dispatch compares against this same constant.
check("a literal ampersand in the tab title is Qt-escaped",
      "&" not in zc.ZX_NEXT_UNITE_TAB_TITLE_TRANSFER.replace("&&", ""),
      zc.ZX_NEXT_UNITE_TAB_TITLE_TRANSFER)
check("...with a plain spelling for anything shown outside a tab label",
      zc.ZX_NEXT_UNITE_TAB_TITLE_TRANSFER_PLAIN
      == zc.ZX_NEXT_UNITE_TAB_TITLE_TRANSFER.replace("&&", "&")
      and "&&" not in zc.ZX_NEXT_UNITE_TAB_TITLE_TRANSFER_PLAIN)
# The two old constants are NOT dead: the wizard's guides and the legacy
# migration still need to name the two tools.
check("the two old tab titles survive as identities",
      bool(getattr(zc, "ZX_NEXT_UNITE_TAB_TITLE_GOOEY", ""))
      and bool(getattr(zc, "ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC", "")))
check("the SD Card sub-tab comes FIRST, as asked",
      zc.TRANSFER_SUBTAB_SDCARD == 0
      and zc.TRANSFER_SUBTAB_REMOTE == 1
      and zc.TRANSFER_SUBTAB_CLASSIC == 2,
      f"{zc.TRANSFER_SUBTAB_SDCARD}/{zc.TRANSFER_SUBTAB_REMOTE}/"
      f"{zc.TRANSFER_SUBTAB_CLASSIC}")
check("the persisted tokens round-trip",
      all(zc.TRANSFER_SUBTAB_BY_TOKEN[t] == i
          for i, t in zc.TRANSFER_SUBTAB_TOKENS.items())
      and len(zc.TRANSFER_SUBTAB_TOKENS) == 3)
# A new SETTING_* key that is not registered makes save_configuration_file
# raise KeyError and silently lose the WHOLE cfg write.
check("SETTING_TRANSFER_SUBTAB is registered in CONFIG_FILE_SETTINGS",
      zc.SETTING_TRANSFER_SUBTAB in zc.CONFIG_FILE_SETTINGS)
check("...and it is NOT the same key as the NextSync experience flag",
      zc.SETTING_TRANSFER_SUBTAB != zc.SETTING_NEXTSYNC_REMOTE_EXPLORER)

print()
print("== the legacy saved-tab migration (every user runs this once) ==")
m = zc.migrate_legacy_tab_index
check("old index 0 (SD Card) -> the merged tab, SD Card sub-tab",
      m(0, False) == ("transfer", "sdcard") and m(0, True) == ("transfer", "sdcard"),
      str(m(0, True)))
check("old index 1 (NextSync) -> the merged tab, honouring the saved experience",
      m(1, False) == ("transfer", "classic")
      and m(1, True) == ("transfer", "remote"),
      f"{m(1, False)} / {m(1, True)}")
# The -1 shift is the whole migration for every other tab.
for legacy in range(2, 12):
    if m(legacy)[1] != legacy - 1:
        check(f"old index {legacy} shifts one left", False, str(m(legacy)))
        break
else:
    check("every old index from 2 up shifts exactly one to the left", True)
check("a string index is accepted (the cfg stores text)",
      m("4") == ("index", 3), str(m("4")))
check("garbage and negatives are rejected, not guessed",
      m("nonsense")[1] < 0 and m(-3)[1] < 0 and m(None)[1] < 0)
check("the mapping never returns a sub-tab token that is not persistable",
      all(m(i, p)[1] in zc.TRANSFER_SUBTAB_BY_TOKEN
          for i in (0, 1) for p in (True, False)))

print()
print("== the saved tab is an IDENTITY now, not an index ==")
main_src = src("zxnu_main.py")
check("the saver writes tab_name_private, not currentIndex()",
      "tab_name_private" in main_src
      and "SETTING_DEFAULT_TAB_WHEN_OPENING] = wid_inner.tab.currentIndex()"
      not in main_src)
# Every page must carry one, or the saver silently falls back to a tab TEXT
# that badges and spinners rewrite ("GetIt (18)").
io_src = src("zxnu_config_io.py")
check("the restore resolves identities through _restore_selected_tab",
      "_restore_selected_tab" in io_src and "def _restore_selected_tab" in io_src)
check("...and it no longer setCurrentIndex(get_int_value(...)) blindly",
      "setCurrentIndex(get_int_value(configuration_dictionary[SETTING_DEFAULT_TAB_WHEN_OPENING]))"
      not in io_src)
_tab_mods = ("zxnu_main.py", "zxnu_settings_pane.py", "zxnu_favorites_pane.py",
             "zxnu_unite_pane.py", "zxnu_itchio_pane.py")
_all_tab_src = "\n".join(src(_m) for _m in _tab_mods)
_pages_without_identity = []
for mod in _tab_mods:
    for _m in re.finditer(r"\.addTab\(\s*(\w+)\s*,", src(mod)):
        widget = _m.group(1)
        if widget in ("QWidget", "page"):
            continue
        # The stamp may live in a different module from the addTab: the
        # Settings page is added in zxnu_main and stamped in its own builder.
        if f"{widget}.tab_name_private" not in _all_tab_src:
            _pages_without_identity.append(f"{mod}:{widget}")
check("every main-tab page carries a tab_name_private identity",
      not _pages_without_identity, str(_pages_without_identity))

print()
print("== sub-tab index 0 no longer means Remote Explorer ==")
# A bare 0/1 on nextsync_mode_tabs is now almost certainly a missed rename.
_bare = []
for mod in APP_MODULES:
    for i, line in enumerate(src(mod).split("\n"), 1):
        if "nextsync_mode_tabs" not in line and "_mode_tabs" not in line:
            continue
        if re.search(r"currentIndex\(\)\s*[!=]=\s*[012]\b", line) or \
           re.search(r"setCurrentIndex\(\s*[012]\s*\)", line) or \
           re.search(r"setTabTextColor\(\s*[012]\s*,", line):
            _bare.append(f"{mod}:{i}: {line.strip()[:70]}")
check("no sub-tab index is spelled as a bare literal any more",
      not _bare, str(_bare))
check("the renumbered sites use the named constants",
      src("zxnu_nextsync_pane.py").count("TRANSFER_SUBTAB_REMOTE") >= 2
      and "TRANSFER_SUBTAB_REMOTE" in src("zxnu_config_io.py")
      and "TRANSFER_SUBTAB_REMOTE" in src("zxnu_retro_ui.py")
      and "TRANSFER_SUBTAB_REMOTE" in src("zxnu_sdcard_ops.py")
      and "TRANSFER_SUBTAB_REMOTE" in src("zxnu_settings_pane.py"))

print()
print("== selecting the SD Card page must not rewrite the NextSync choice ==")
pane_src = src("zxnu_nextsync_pane.py")
_handler = re.search(
    r"def _nextsync_on_mode_tab_changed\(index\):(.*?)\n    host\.nextsync_mode_tabs\.currentChanged",
    pane_src, re.S)
check("the sub-tab handler is found", _handler is not None)
if _handler:
    body = _handler.group(1)
    # _nextsync_toggle_remote_explorer persists SETTING_NEXTSYNC_REMOTE_EXPLORER
    # on EVERY call, so running it for the SD Card page would rewrite the
    # user's experience choice to "classic" every time they glanced at the
    # SD card - and that choice is exactly what they must come back to.
    check("the toggle runs only for the two NextSync sub-tabs",
          "TRANSFER_SUBTAB_SDCARD" in body
          and "_nextsync_toggle_remote_explorer" in body
          and body.index("TRANSFER_SUBTAB_SDCARD")
          < body.index("_nextsync_toggle_remote_explorer"))
    check("the sub-tab persist has its OWN restore flag",
          "_transfer_subtab_restoring" in body)
# ...and that flag must NOT be the one the SD Card pane's "Start Remote
# Explorer server" sets, or that deliberate move stops being remembered.
check("the SD Card pane's start-server action sets only the experience flag",
      "_transfer_subtab_restoring" not in src("zxnu_sdcard_ops.py"))
check("the startup restores set BOTH flags",
      io_src.count("_transfer_subtab_restoring = True") >= 3,
      str(io_src.count("_transfer_subtab_restoring = True")))

print()
print("== the sub-tab restore must not be clobbered by the RE restore ==")
_re_at = io_src.find("Restore the Remote Explorer view if it was open")
_sub_at = io_src.find("SETTING_TRANSFER_SUBTAB, \"\") or \"\").strip().lower()")
_auto_at = io_src.find("-start-remote-explorer-listener: the command-line switch")
check("the sub-tab restore sits BELOW the Remote Explorer restore",
      _re_at != -1 and _sub_at != -1 and _re_at < _sub_at,
      f"re={_re_at} sub={_sub_at}")
check("...and ABOVE the autostart block, which is entitled to win",
      _auto_at != -1 and _sub_at < _auto_at, f"sub={_sub_at} auto={_auto_at}")

print()
print("== one body owns both tools' entry work ==")
ops_src = src("zxnu_tab_ops.py")
check("_activate_transfer_subtab exists and is exported on the host",
      "def _activate_transfer_subtab" in ops_src
      and "host._activate_transfer_subtab = _activate_transfer_subtab" in ops_src)
check("on_tab_changed dispatches on the merged title",
      "ZX_NEXT_UNITE_TAB_TITLE_TRANSFER" in ops_src)
check("...and no longer on the two retired ones",
      "ZX_NEXT_UNITE_TAB_TITLE_GOOEY" not in ops_src
      and "ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC" not in ops_src)
# All three entry paths must go through the one helper, or they drift.
check("the sub-tab handler calls it", "_activate_transfer_subtab" in pane_src)
check("the deferred startup activation calls it",
      "_activate_transfer_subtab()" in main_src)
check("it guards on the main tab being on screen (both animations it owns "
      "exist to not run on a hidden tab)",
      "_on_screen" in ops_src)

print()
print("== the sprite sidebar keeps the sync-activity animation ==")
gal = src("zxnu_gallery.py")
_match = re.search(r"_MATCH\s*=\s*\((.*?)\)\n", gal, re.S)
check("the sidebar matcher is found", _match is not None)
if _match:
    body = _match.group(1)
    # "sd card" would otherwise claim "Transfer tools (SD card & NextSync)"
    # and the packet animation - which only rides a "sync"/"transfer" sprite
    # - would vanish the moment the tabs merged.
    check("'transfer tools' is matched BEFORE 'sd card'",
          '"transfer tools"' in body
          and body.index('"transfer tools"') < body.index('"sd card"'))
check("a 'transfer' sprite exists and the packets ride it",
      '"transfer": ([' in gal
      and re.search(r'packets_on\s*=\s*\(key in \("sync", "transfer"\)', gal)
      is not None)

print()
print("== the two properties copy/paste and drag & drop rely on ==")
# 1. No QShortcut anywhere: shortcuts with a window/application context are
#    the ONLY construct a reparent would break. Ctrl-C/X/V reach the views
#    through assigned keyPressEvent attributes instead, which move with the
#    widget.
def _mentions_code(mod, needle):
    """True when *needle* appears outside comments and docstrings.

    Checking the raw text would match the comments that EXPLAIN why the
    construct is absent - which is exactly what happened the first time this
    ran.
    """
    tree = ast.parse(src(mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == needle:
            return True
        if isinstance(node, ast.Attribute) and node.attr == needle:
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any(a.name == needle for a in node.names):
                return True
    return False


_shortcuts = [mod for mod in APP_MODULES if _mentions_code(mod, "QShortcut")]
check("no QShortcut exists anywhere in the app (reparenting cannot break "
      "an assigned keyPressEvent, but it can break a window-context shortcut)",
      not _shortcuts, str(_shortcuts))
# 2. Every explorer view still assigns its own handlers, i.e. nothing was
#    rebuilt in place of a reparent.
for mod, view in (("zxnu_main.py", "self.treeview"),
                  ("zxnu_main.py", "self.image_treeview"),
                  ("zxnu_nextsync_pane.py", "host.nextsync_treeview"),
                  ("zxnu_remote_explorer.py", "self.local_view"),
                  ("zxnu_remote_explorer.py", "self.next_view")):
    _s = src(mod)
    check(f"{view} keeps its own drop + key handlers",
          f"{view}.dropEvent = " in _s and f"{view}.keyPressEvent = " in _s
          and f"{view}.setAcceptDrops(True)" in _s)
# 3. The sub-tab bar must NOT accept drags. QTabBar calls setAcceptDrops on
#    itself only for changeCurrentOnDrag, which would put the Remote
#    Explorer's staged drag-out and the SD Card views inside one gesture -
#    where _StagedDragMime.retrieveData blocks the drag loop off Windows.
_cod = [mod for mod in APP_MODULES
        if _mentions_code(mod, "setChangeCurrentOnDrag")]
check("the sub-tab bar never enables setChangeCurrentOnDrag",
      not _cod, str(_cod))

print()
print("== the wizard reaches both tools ==")
import zxnu_wizard_content as wc  # noqa: E402

_steps = {s[1]: s for s in wc.TOUR_STEPS}
check("both transfer tour steps survive",
      "tour.sdcard" in _steps and "tour.nextsync" in _steps)
check("...both on the merged tab",
      _steps["tour.sdcard"][0] == "ZX_NEXT_UNITE_TAB_TITLE_TRANSFER"
      and _steps["tour.nextsync"][0] == "ZX_NEXT_UNITE_TAB_TITLE_TRANSFER")
check("...distinguished by a sub-tab, or one would shadow the other",
      len(_steps["tour.sdcard"]) > 3 and len(_steps["tour.nextsync"]) > 3
      and _steps["tour.sdcard"][3] != _steps["tour.nextsync"][3],
      f"{_steps['tour.sdcard']} / {_steps['tour.nextsync']}")
check("both in-depth guides point at the merged tab with distinct sub-tabs",
      wc.GUIDES["sdcard"]["tab"] == "ZX_NEXT_UNITE_TAB_TITLE_TRANSFER"
      and wc.GUIDES["nextsync"]["tab"] == "ZX_NEXT_UNITE_TAB_TITLE_TRANSFER"
      and wc.GUIDES["sdcard"].get("subtab") != wc.GUIDES["nextsync"].get("subtab")
      and wc.GUIDES["sdcard"].get("subtab"),
      f"{wc.GUIDES['sdcard'].get('subtab')} / "
      f"{wc.GUIDES['nextsync'].get('subtab')}")
# Every sub-tab token the content names must be one the engine understands.
_tokens = {s[3] for s in wc.TOUR_STEPS if len(s) > 3}
_tokens |= {g.get("subtab") for g in wc.GUIDES.values() if g.get("subtab")}
check("every sub-tab token the content uses is one the engine handles",
      _tokens <= set(zc.TRANSFER_SUBTAB_BY_TOKEN) | {"nextsync"}, str(_tokens))
wiz = src("zxnu_wizard.py")
check("the engine resolves 3- and 4-element steps",
      "step[:3]" in wiz and "len(step) > 3" in wiz)
check("a sub-tab change is treated as a topic change",
      "_on_subtab_switched" in wiz and "currentChanged.connect(self._on_subtab_switched)" in wiz)
check("guided navigation does not persist the user's choices",
      "_transfer_subtab_restoring = True" in wiz)

print()
print("== the tour capture script ==")
tour = src(os.path.join("extra", "tour_capture.py"))
check("it drives sub-tabs by constant",
      "TRANSFER_SUBTAB_SDCARD" in tour and "TRANSFER_SUBTAB_CLASSIC" in tour
      and "TRANSFER_SUBTAB_REMOTE" in tour)
# Selecting the main tab runs the CURRENT sub-tab's entry work, and at the
# first call that is still the construction default (Classic), whose prepare
# scan prints the host name and local IP into frames that get published.
_fn = re.search(r"def transfer_subtab\(index\):(.*?)\n\n", tour, re.S)
check("the capture helper is found", _fn is not None)
if _fn:
    b = _fn.group(1)
    check("...and it selects the SUB-tab before the main tab (IP masking)",
          b.index("nextsync_mode_tabs.setCurrentIndex") < b.index("tab.setCurrentIndex"))

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all Transfer tools tab checks passed")
sys.exit(0)
