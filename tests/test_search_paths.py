"""9.7.73: additional search paths for the emulators and hdfmonkey.

CSpect, MAME and hdfmonkey were looked for in the app's own folders, under
downloads/ and on the system PATH. Settings (Advanced mode) now holds a list
of folders that are searched as well, BEFORE the system PATH and in the
list's own order - for the user who cannot add to PATH, or will not.

Pinned here, headlessly:

* the list's storage rules (parse / format: tidied, no duplicate, 20 at
  most, a path the cfg cannot hold dropped) and the early read from the cfg;
* the lookup: the folder itself, then ONE level down, never deeper; the
  list's order is the priority; a folder that is gone is skipped, never an
  error; the newest of several CSpect builds; an hdfmonkey folder named for
  another platform is not looked into;
* MAME and CSpect prefer a listed folder to the system PATH;
* the Settings editor, driven with real clicks: Add verifies the folder
  exists, refuses a duplicate and a 21st; Remove; Move up / Move down;
* the wiring, by source: the key persists, the row is Advanced-only, the
  first lookups read the list before they run, and the detection toast says
  which copy came from a custom search path and which paths are not valid.

Run with: python tests/test_search_paths.py
"""
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import zxnu_config as cfg  # noqa: E402
from zxnu_settings_pane import (SETTINGS_BASIC_ROWS, SETTINGS_TAB_ROWS,  # noqa: E402
                                SearchPathsEditor)

FAILS = []
WIN = sys.platform.startswith("win")


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  " + repr(detail)))
    if not cond:
        FAILS.append(name)


def touch(*parts, exe=True):
    path = os.path.join(*parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x")
    if exe and not WIN:
        os.chmod(path, 0o755)
    return path


def same(a, b):
    return bool(a) and bool(b) and (os.path.normcase(os.path.abspath(a))
                                    == os.path.normcase(os.path.abspath(b)))


def fwd(p):
    return p.replace("\\", "/")


TOP = tempfile.mkdtemp(prefix="zxnu-searchpaths-")
MAME = "mame.exe" if WIN else "mame"
HDF = "hdfmonkey.exe" if WIN else "hdfmonkey"


def test_storage():
    a, b = os.path.join(TOP, "a"), os.path.join(TOP, "b")
    os.makedirs(a)
    os.makedirs(b)
    got = cfg.parse_search_paths(a + os.sep + "|" + fwd(a) + "|  |\"" + b + "\"")
    check("parse: tidied (forward slashes, no trailing slash, quotes off), no duplicate",
          got == [fwd(a), fwd(b)], got)
    check("format: what parse reads back", cfg.parse_search_paths(cfg.format_search_paths(got)) == got)
    many = [os.path.join(TOP, "n%02d" % i) for i in range(30)]
    check("at most 20 are kept, the first ones",
          cfg.parse_search_paths("|".join(many)) == [fwd(p) for p in many[:20]]
          and cfg.MAX_EMULATOR_SEARCH_PATHS == 20)
    check("a list is accepted as well as the stored string",
          cfg.parse_search_paths([a, b]) == [fwd(a), fwd(b)])
    check("nothing stored reads as an empty list",
          cfg.parse_search_paths("") == [] and cfg.parse_search_paths(None) == [])
    check("the key is persisted to hdfg.cfg",
          cfg.SETTING_EMULATOR_SEARCH_PATHS == "emulator_search_paths"
          and cfg.SETTING_EMULATOR_SEARCH_PATHS in cfg.CONFIG_FILE_SETTINGS)
    # the early read: the one key, straight from the file
    path = os.path.join(TOP, "hdfg.cfg")
    with open(path, "w", encoding="utf-8") as f:
        f.write("ui_language=en\nemulator_search_paths = %s|%s\nhddfile=x\n" % (a, b))
    check("the early read finds the key in the cfg",
          cfg.read_search_paths_from_cfg(path) == [fwd(a), fwd(b)],
          cfg.read_search_paths_from_cfg(path))
    check("...and answers [] for a missing file or a missing key",
          cfg.read_search_paths_from_cfg(os.path.join(TOP, "none.cfg")) == []
          and cfg.read_search_paths_from_cfg(__file__) == [])
    old = cfg.extra_search_paths()
    try:
        check("the live list is set and read back as a copy",
              cfg.set_extra_search_paths([a, b, a]) == [fwd(a), fwd(b)]
              and cfg.extra_search_paths() == [fwd(a), fwd(b)]
              and cfg.extra_search_paths() is not cfg.extra_search_paths())
    finally:
        cfg.set_extra_search_paths(old)


def test_lookup():
    one = os.path.join(TOP, "one")
    two = os.path.join(TOP, "two")
    gone = os.path.join(TOP, "gone")
    in_root = touch(two, MAME)
    in_sub = touch(one, "tools", MAME)
    touch(one, "deep", "deeper", "tool-x.exe")
    check("found in the folder itself", same(cfg.find_in_search_paths((MAME,), [two]), in_root))
    check("found one level down", same(cfg.find_in_search_paths((MAME,), [one]), in_sub))
    check("never two levels down", cfg.find_in_search_paths(("tool-x.exe",), [one]) is None)
    check("the list's order is the priority",
          same(cfg.find_in_search_paths((MAME,), [one, two]), in_sub)
          and same(cfg.find_in_search_paths((MAME,), [two, one]), in_root))
    check("a folder that is gone is skipped, not an error",
          same(cfg.find_in_search_paths((MAME,), [gone, two]), in_root)
          and cfg.find_in_search_paths((MAME,), [gone]) is None)
    check("invalid_search_paths names exactly the missing ones",
          cfg.invalid_search_paths([gone, two, in_root]) == [gone, in_root],
          cfg.invalid_search_paths([gone, two, in_root]))
    check("a file is not a folder to search", cfg.find_in_search_paths((MAME,), [in_root]) is None)
    check("nothing listed, nothing found", cfg.find_in_search_paths((MAME,), []) is None)
    if WIN:
        check("names match case-blind", same(cfg.find_in_search_paths((MAME.upper(),), [two]), in_root))
    else:
        plain = touch(TOP, "noexec", MAME, exe=False)
        check("POSIX: a native binary without the execute bit is not a hit",
              cfg.find_in_search_paths((MAME,), [os.path.dirname(plain)]) is None)
    # where a hit came from
    check("search_path_of: the file's folder, or its parent, is a listed one",
          cfg.search_path_of(in_root, [two]) == two and cfg.search_path_of(in_sub, [one]) == one)
    check("...and nothing else is",
          cfg.search_path_of(in_sub, [two]) == "" and cfg.search_path_of("", [two]) == ""
          and cfg.search_path_of(os.path.join(one, "deep", "deeper", "tool-x.exe"), [one]) == "")

    # CSpect: of several builds side by side, the newest
    cs = os.path.join(TOP, "cspect")
    touch(cs, "CSpect3_1_4_0", "CSpect.exe")
    newest = touch(cs, "CSpect3_10_0_0", "CSpect.exe")
    touch(cs, "CSpect3_9_9_9", "CSpect.exe")
    old = cfg.extra_search_paths()
    try:
        cfg.set_extra_search_paths([cs])
        got = cfg.find_cspect_executable()
        # the app folder / data root are asked first: only meaningful when
        # neither holds a CSpect.exe of its own
        app_has = any(os.path.isfile(os.path.join(d, "CSpect.exe"))
                      for d in (os.path.dirname(os.path.abspath(sys.argv[0])), cfg.ZXNU_DATA_ROOT))
        check("CSpect: the newest of several builds in a listed folder",
              app_has or same(got, newest), got)

        # MAME and CSpect: a listed folder outranks the system PATH
        real_which = shutil.which
        fake = os.path.join(TOP, "onpath", MAME)
        shutil.which = lambda name, *a, **k: fake
        try:
            cfg.set_extra_search_paths([two])
            check("MAME: a listed folder is preferred to the system PATH",
                  same(cfg.find_mame_executable(), in_root), cfg.find_mame_executable())
            cfg.set_extra_search_paths([gone])
            check("MAME: with nothing in the list, PATH answers as before",
                  cfg.find_mame_executable() == fake, cfg.find_mame_executable())
            cfg.set_extra_search_paths([])
            check("MAME: an empty list changes nothing", cfg.find_mame_executable() == fake)
        finally:
            shutil.which = real_which
    finally:
        cfg.set_extra_search_paths(old)

    # hdfmonkey: only this platform's folder
    hd = os.path.join(TOP, "hdf")
    mine = [d for d, _e in cfg.hdfmonkey_platform_dirs()]
    others = [d for d in ("windows-64", "linux-musl", "macos-intel", "macos-mn") if d not in mine]
    for d in others:
        touch(hd, d, "hdfmonkey")
        touch(hd, d, "hdfmonkey.exe")
    check("hdfmonkey: folders named for other platforms are not looked into",
          cfg.find_hdfmonkey_in_search_paths([hd]) is None, cfg.find_hdfmonkey_in_search_paths([hd]))
    if mine:
        right = touch(hd, mine[0], HDF)
        check("hdfmonkey: this platform's folder is",
              same(cfg.find_hdfmonkey_in_search_paths([hd]), right))
    bare = touch(TOP, "hdfbare", HDF)
    check("hdfmonkey: a bare copy in a listed folder counts",
          same(cfg.find_hdfmonkey_in_search_paths([os.path.dirname(bare)]), bare))


def test_editor():
    """The Settings row's widget, with real clicks."""
    base = os.path.join(TOP, "ed")
    dirs = [os.path.join(base, "d%02d" % i) for i in range(22)]
    for d in dirs:
        os.makedirs(d)
    changes, refusals, picks = [], [], []
    ed = SearchPathsEditor(on_changed=lambda p, r: changes.append((list(p), r)),
                           refused=refusals.append,
                           choose_folder=lambda: picks.pop(0) if picks else "")
    try:
        ed.show()

        def click(b):
            QTest.mouseClick(b, Qt.LeftButton)
            app.processEvents()

        check("empty: only Add is enabled",
              ed.btn_add.isEnabled() and not ed.btn_remove.isEnabled()
              and not ed.btn_up.isEnabled() and not ed.btn_down.isEnabled())
        ed.set_paths([dirs[0], dirs[1]])
        check("set_paths shows the list and reports nothing (the cfg restore)",
              ed.paths() == [fwd(dirs[0]), fwd(dirs[1])] and changes == [])

        picks.append(dirs[2])
        click(ed.btn_add)
        check("Add: the chosen folder goes to the end, and the change is reported",
              ed.paths()[-1] == fwd(dirs[2]) and changes[-1] == (ed.paths(), None), changes[-1:])
        n = len(changes)
        picks.append("")                                  # the dialog was cancelled
        click(ed.btn_add)
        check("Add cancelled: nothing changes", len(changes) == n and len(ed.paths()) == 3)
        picks.append(dirs[1] + os.sep)                    # another spelling of a listed one
        click(ed.btn_add)
        check("Add refuses a folder already in the list",
              len(changes) == n and len(refusals) == 1 and "already" in refusals[-1], refusals)
        picks.append(os.path.join(base, "not-there"))
        click(ed.btn_add)
        check("Add verifies the folder exists",
              len(changes) == n and len(refusals) == 2 and "does not exist" in refusals[-1], refusals)
        if not WIN:
            odd = os.path.join(base, "a|b")
            os.makedirs(odd)
            picks.append(odd)
            click(ed.btn_add)
            check("Add refuses a folder the cfg cannot hold ('|')",
                  len(changes) == n and "cannot be stored" in refusals[-1], refusals)

        # Move up / Move down act on the selected row
        ed.list.setCurrentRow(2)
        click(ed.btn_up)
        check("Move up: the selected folder is searched earlier",
              ed.paths() == [fwd(dirs[0]), fwd(dirs[2]), fwd(dirs[1])]
              and ed.list.currentRow() == 1 and changes[-1] == (ed.paths(), None), ed.paths())
        click(ed.btn_up)
        check("...up to the top, where Move up greys out",
              ed.paths()[0] == fwd(dirs[2]) and not ed.btn_up.isEnabled() and ed.btn_down.isEnabled())
        click(ed.btn_down)
        click(ed.btn_down)
        check("Move down: to the bottom, where Move down greys out",
              ed.paths() == [fwd(dirs[0]), fwd(dirs[1]), fwd(dirs[2])]
              and not ed.btn_down.isEnabled() and ed.btn_up.isEnabled(), ed.paths())

        # Remove names what went
        ed.list.setCurrentRow(1)
        click(ed.btn_remove)
        check("Remove: the selected folder goes, and the change names it",
              ed.paths() == [fwd(dirs[0]), fwd(dirs[2])]
              and changes[-1] == (ed.paths(), fwd(dirs[1])), changes[-1:])

        # the limit
        ed.set_paths(dirs[:19])
        picks.append(dirs[19])
        click(ed.btn_add)
        check("the 20th folder is accepted, and Add greys out",
              len(ed.paths()) == 20 and not ed.btn_add.isEnabled())
        r = len(refusals)
        check("a 21st is refused, even when asked directly",
              ed.add_path(dirs[20]) is False and len(ed.paths()) == 20
              and len(refusals) == r + 1 and "20" in refusals[-1], refusals[-1:])

        # a folder that went away stays listed, in the warning colour
        shutil.rmtree(dirs[0])
        ed.set_paths([dirs[0], dirs[1]])
        check("a folder that is gone stays in the list, marked",
              ed.paths()[0] == fwd(dirs[0])
              and ed.list.item(0).foreground().color().name().lower() == "#ffb300"
              and ed.list.item(0).toolTip()
              and not ed.list.item(1).toolTip())
    finally:
        ed.deleteLater()


def test_wiring():
    def src(name):
        return open(os.path.join(ROOT, name), encoding="utf-8").read()
    check("the Settings row exists, in Advanced mode only",
          "emulator_search_paths" in SETTINGS_TAB_ROWS
          and "emulator_search_paths" not in SETTINGS_BASIC_ROWS)
    pane = src("zxnu_settings_pane.py")
    check("the row persists, updates the live list and has the detection run again",
          "configuration_dictionary[SETTING_EMULATOR_SEARCH_PATHS] = format_search_paths(paths)" in pane
          and "set_extra_search_paths(paths)" in pane and '"_search_paths_redetect"' in pane
          and '_place("emulator_search_paths", host.settings_search_paths_editor, 1)' in pane)
    io_ = src("zxnu_config_io.py")
    check("the cfg restore shows the saved list",
          "host._search_paths_show(_search_paths)" in io_
          and "set_extra_search_paths(_search_paths)" in io_)
    main = src("zxnu_main.py")
    early = main.find("set_extra_search_paths(read_search_paths_from_cfg())")
    check("startup reads the list BEFORE the first lookups",
          0 < early < main.find("self._mame_executable_path = resolve_mame_executable(")
          and early < main.find("self._cspect_executable_path = find_cspect_executable()")
          and early < main.find("self._hdfmonkey_executable_path = find_hdfmonkey_in_search_paths()"))
    toast = main.split("def _show_emulator_detection_toast", 1)[1].split("\n    def ", 1)[0]
    check("the detection toast marks a copy found through a custom search path",
          toast.count("+ _via(") == 3 and 'ui_tr_now(" (custom search path)")' in toast)
    check("...and reports the paths that are not valid, every time it runs",
          "self._warn_invalid_search_paths()" in toast
          and toast.find("self._warn_invalid_search_paths()") < toast.find("found = []"))
    warn = main.split("def _warn_invalid_search_paths", 1)[1].split("\n    def ", 1)[0]
    check("as a yellow toast naming them",
          'variant="yellow"' in warn and "invalid_search_paths()" in warn and "{paths}" in warn)
    redet = main.split("def _search_paths_redetect", 1)[1].split("self._search_paths_redetect = ", 1)[0]
    check("a change in Settings detects again, live, for all three tools",
          "resolve_mame_executable(ZXNU_DATA_ROOT)" in redet
          and "find_hdfmonkey_in_search_paths()" in redet
          and "find_cspect_executable()" in redet
          and "_rescan_emulators_after_uninstall(removed)" in redet
          and "_rescan_emulators_after_install()" in redet)
    conf = src("zxnu_config.py")
    mame = conf.split("def find_mame_executable", 1)[1].split("\ndef ", 1)[0]
    cs = conf.split("def find_cspect_executable", 1)[1].split("\ndef ", 1)[0]
    check("both PATH lookups ask the listed folders first",
          mame.find("find_in_search_paths(") < mame.find("shutil.which(")
          and 0 < cs.find("find_in_search_paths(") < cs.find("shutil.which("))


def main():
    try:
        test_storage()
        test_lookup()
        test_editor()
        test_wiring()
    finally:
        shutil.rmtree(TOP, ignore_errors=True)
    print("RESULT:", "ALL CHECKS PASSED" if not FAILS else "FAILED: %s" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
