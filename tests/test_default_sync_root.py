"""A run with no NextSync sync root saved gets one by default (9.7.52).

Until 9.7.52 the Remote Explorer's Start button stayed disabled on a first
run until the user browsed to a folder and pressed "Set current folder as new
sync root folder". The folder the app was started from is the default now -
Jari Komppa's original NextSync server synced the folder it was started in -
so the server can start straight away.

The ONE sync root serves Classic sync too, which sends everything under it,
recursively: a folder that would make that a disaster (the home folder itself,
a drive root, a system folder, an unwritable one) is never proposed, and the
app's data folder stands in for it.

Covered here, headlessly (CI runs this): the chooser and its guard on real
temporary folders, and source tripwires on the restore pipeline (filled only
when EMPTY, before the restore, and on the first-run path the restore never
reaches).

Run with: python tests/test_default_sync_root.py
"""
import ast
import os
import re
import stat
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

import zxnu_config as zc  # noqa: E402

FAIL = []
WIN = sys.platform == "win32"


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


base = tempfile.mkdtemp(prefix="zxnu-syncroot-")
start = os.path.join(base, "started here")       # a space, on purpose
data = os.path.join(base, "data")
home = os.path.join(base, "home")
sysroot = os.path.join(base, "Windows")
for d in (start, data, home, sysroot, os.path.join(sysroot, "System32")):
    os.makedirs(d)
ENV = {"SystemRoot": sysroot}            # a system folder the test owns


def norm(p):
    p = os.path.abspath(p).replace("\\", "/")
    return p if p.endswith("/") else p + "/"


# ---- the guard -------------------------------------------------------------------
U = zc.nextsync_root_unsuitable
check("an ordinary writable folder is suitable", U(start, home=home, env=ENV) == "",
      U(start, home=home, env=ENV))
check("a missing folder is not", U(os.path.join(base, "gone"), home=home, env=ENV) != "")
check("an empty path is not", U("", home=home, env=ENV) != "")
check("the home folder itself is not (a Classic sync would send all of it)",
      U(home, home=home, env=ENV) == "the home folder itself")
check("...but a folder INSIDE home is fine",
      U(start, home=base, env=ENV) == "", U(start, home=base, env=ENV))
check("a folder ABOVE home is not (C:\\Users, /home: every user's home)",
      U(base, home=home, env=ENV) == "a folder above the home folder",
      U(base, home=home, env=ENV))
root = os.path.abspath(os.sep)
check("a drive or filesystem root is not", U(root, home=home, env=ENV)
      == "a drive or filesystem root", U(root, home=home, env=ENV))
check("the Windows folder is not", U(sysroot, home=home, env=ENV) == "a system folder")
check("...nor anything under it (System32 is where some launchers start a program)",
      U(os.path.join(sysroot, "System32"), home=home, env=ENV) == "a system folder")
check("a folder merely NAMED like it next door is fine",
      U(sysroot + "Old", home=home, env=ENV) in ("not a folder", ""),
      U(sysroot + "Old", home=home, env=ENV))
os.makedirs(sysroot + "Old")
check("...and once it exists it is suitable",
      U(sysroot + "Old", home=home, env=ENV) == "", U(sysroot + "Old", home=home, env=ENV))
check("Program Files is a system folder too",
      U(start, home=home, env={"ProgramFiles": base}) == "a system folder")
if not WIN and os.geteuid() != 0:
    ro = os.path.join(base, "readonly")
    os.makedirs(ro)
    os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
    check("an unwritable folder is not", U(ro, home=home, env=ENV) == "not writable",
          U(ro, home=home, env=ENV))
    os.chmod(ro, stat.S_IRWXU)

# Windows' os.access(W_OK) ignores a folder's ACL (it answers True for
# System32), so the write test there is a real file that deletes itself.
_src_cfg = open(os.path.join(REPO, "zxnu_config.py"), encoding="utf-8").read()
_g = _src_cfg[_src_cfg.index("def nextsync_root_unsuitable"):]
_g = _g[:_g.index("\ndef ")]
check("the Windows write test is a real probe, not os.access",
      'os.name == "nt"' in _g and "fd = os.open(probe" in _g
      and _g.index("fd = os.open(probe") < _g.index("elif not os.access(real"))
# ONE attempt: tempfile's Windows retry loop goes round while os.access says
# yes - always, for a folder - and TMP_MAX is 2**31-1 before Python 3.13, so
# a TemporaryFile in an unwritable folder hung startup (measured, 3.10/3.12).
check("...a single bounded attempt, never tempfile's retry loop",
      "TemporaryFile(" not in _g and "mkstemp(" not in _g
      and "os.O_EXCL" in _g and "O_TEMPORARY" in _g)
_probe_dir = tempfile.mkdtemp(prefix="zxnu-probe-")
check("the probe leaves nothing behind",
      U(_probe_dir, home=home, env=ENV) == "" and os.listdir(_probe_dir) == [],
      repr(os.listdir(_probe_dir)))

# ---- the chooser -----------------------------------------------------------------
D = zc.default_nextsync_sync_root
got = D(start, data, home=home, env=ENV)
check("the start folder is the default", got == norm(start), got)
check("...spelled the way every sync-root writer stores it",
      got.endswith("/") and "\\" not in got, got)
check("a start folder that is the home folder falls back to the data folder",
      D(home, data, home=home, env=ENV) == norm(data))
check("a start folder that is a system folder falls back too",
      D(os.path.join(sysroot, "System32"), data, home=home, env=ENV) == norm(data))
check("a vanished start folder falls back too",
      D(os.path.join(base, "gone"), data, home=home, env=ENV) == norm(data))
check("with neither usable there is no default (the old behaviour)",
      D(home, root, home=home, env=ENV) == "")
check("the real start folder was captured at import",
      isinstance(zc.ZXNU_START_DIR, str))

# ---- Classic sync never sends the app's own files -----------------------------------
# The default root is the start folder, which in the common cases IS the data
# folder: hdfg.cfg (the itch.io API key, the bridge token), the logs and the
# gigabytes in downloads/ must never reach the Next, syncignore.txt or not.
app_root = os.path.join(base, "portable app")
for rel in ("downloads/mame/mame.exe", "downloads/itchio/x/files/CSpect.exe",
            "hdfg.cfg", "zx-next-unite.log", "zx-next-unite.log.2",
            "zx-next-unite-crash.log", "zx-next-unite-v9.7.52.exe",
            "games/beast.nex", "games/hdfg.cfg", "games/zx-next-unite.txt",
            "readme.txt"):
    full = os.path.join(app_root, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write("x")
M = zc.app_state_matcher
m = M(app_root + "/", data_root=app_root)
check("the matcher applies when the data folder IS the sync root", m is not None)
outer = M(base + "/", data_root=app_root)
check("...and when it sits UNDER the sync root", outer is not None
      and outer(os.path.join(app_root, "hdfg.cfg"))
      and not outer(os.path.join(base, "hdfg.cfg")))
check("...and not at all when it is elsewhere",
      M(os.path.join(app_root, "games") + "/", data_root=app_root) is None)

import zxnu_workers  # noqa: E402

files = sorted(os.path.relpath(f, app_root).replace("\\", "/")
               for f, _size in zxnu_workers.getFileList(app_root + "/", True,
                                                        data_root=app_root))
check("Classic sync sends the user's files, nested names included",
      files == ["games/beast.nex", "games/hdfg.cfg", "games/zx-next-unite.txt",
                "readme.txt"], repr(files))
check("...and never the app's config, logs, binaries or downloads",
      not any(f.startswith("downloads/") or f in ("hdfg.cfg", "zx-next-unite.log")
              or f.startswith("zx-next-unite") for f in files), repr(files))
other = os.path.join(base, "elsewhere")
os.makedirs(other)
with open(os.path.join(other, "hdfg.cfg"), "w") as fh:
    fh.write("x")
check("a hdfg.cfg in a folder that is not the data folder is the user's own file",
      [os.path.basename(f) for f, _ in zxnu_workers.getFileList(
          other + "/", True, data_root=app_root)] == ["hdfg.cfg"])

# ---- ...and a Sync4 upload (.sync -send) never writes them -------------------------
# The Classic server takes an upload from any machine on the network, and the
# default root is now often the data folder: a file the app reads, runs or
# would load as code must not be writable through it.
G = zc.app_upload_guard
g = G(app_root + "/", data_root=app_root)
check("the upload guard applies when the data folder is the sync root", g is not None)
for rel, refused in (("hdfg.cfg", True), ("zx-next-unite.log", True),
                     ("downloads/hdfmonkey/windows-64/hdfmonkey.exe", True),
                     ("zipfile.py", True), ("version.dll", True), ("evil.bat", True),
                     ("game.nex", False), ("readme.txt", False),
                     ("games/tool.py", False), ("games/hdfg.cfg", False)):
    check(f"an upload of {rel} is {'refused' if refused else 'allowed'}",
          bool(g(os.path.join(app_root, *rel.split("/")))) == refused)
check("no guard when the data folder is elsewhere",
      G(other + "/", data_root=app_root) is None)
wk = open(os.path.join(REPO, "zxnu_workers.py"), encoding="utf-8").read()
check("the Sync4 upload builds the guard once per Send",
      "upload_refused = app_upload_guard(upload_root)" in wk)
check("...and asks it BEFORE the conflict policy can overwrite",
      "if not refused and os.path.exists(cur_path):" in wk
      and wk.index("refused = (upload_refused is not None")
      < wk.index("if not refused and os.path.exists(cur_path):"))

# ---- the Retro toggle leaves the Remote Explorer view alone ------------------------
# On a first run started with -start-remote-explorer-listener the first-run
# pygame default flipped the toggle after the view opened, and the Classic
# retro log replaced the whole Remote Explorer view.
pane = open(os.path.join(REPO, "zxnu_nextsync_pane.py"), encoding="utf-8").read()
tog = pane[pane.index("def _nextsync_on_pygame_toggled"):]
tog = tog[:tog.index("host.nextsync_pygame_button.toggled.connect")]
check("both faces of the Retro toggle switch the stack only off the Remote Explorer",
      tog.count("if _classic_log_showing():") == 2
      and tog.count("host.nextsync_log_stack.setCurrentWidget(") == 2)

# ---- the restore pipeline (source tripwires) ---------------------------------------
src = open(os.path.join(REPO, "zxnu_config_io.py"), encoding="utf-8").read()
tree = ast.parse(src)


def fn(name):
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(src, node)


fill = fn("_fill_default_nextsync_root")
check("only an EMPTY setting is ever filled (a chosen root is never replaced)",
      "(configuration_dictionary.get(SETTING_NEXTSYNC_EXPLORERPATH)" in fill
      and fill.index(".strip():") < fill.index("default_nextsync_sync_root()"))
check("the default is written into the shared sync-root setting",
      "configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] = root" in fill)
check("the user is told, with the button named as it reads on screen",
      re.search(r'button=ui_tr_now\(\s*"Set current folder as new sync root folder"\)',
                fill) is not None)
load = fn("load_configuration_file")
check("the default is filled BEFORE the restore pushes it into the UI",
      "_fill_default_nextsync_root()\n            _restore_nextsync_root_ui()" in
      load.replace("\r\n", "\n"))
check("...and before the Remote Explorer autostart further down",
      load.index("_fill_default_nextsync_root()") < load.index("_re_auto_on"))
first = load[load.rindex("if not config_loaded_with_success:"):]
check("a first run (no hdfg.cfg) gets the default too",
      "_fill_default_nextsync_root()" in first and "_restore_nextsync_root_ui()" in first)
check("...and honours -start-remote-explorer-listener, which it ignored before",
      "_zxnu_start_re_listener() and" in first and "_start_re_view_and_listener()" in first)
check("the console line is said once the UI language is applied (deferred)",
      "QTimer.singleShot(0, _say)" in fill
      and fill.index("def _say(") < fill.index("QTimer.singleShot(0, _say)"))
sd_src = open(os.path.join(REPO, "zxnu_sdcard_ops.py"), encoding="utf-8").read()
_ls = sd_src[sd_src.index("def _local_start_re_server"):]
_ls = _ls[:_ls.index("\n    def ")]
check("the SD Card pane opens the Remote Explorer view BEFORE testing the root",
      _ls.index("tabs.setCurrentIndex(TRANSFER_SUBTAB_REMOTE)")
      < _ls.index('if not getattr(host, "_re_sync_root", ""):'))

# The console line is catalogued in every language, placeholders kept.
import zxnu_i18n  # noqa: E402

KEY = ("NextSync sync root set to {path}: none had been chosen yet. "
       "To use another folder, browse to it and press '{button}'.")
# (ast folds the implicit concatenation into one constant: the exact key.)
check("the console line in the code is the catalogued template",
      any(isinstance(n, ast.Constant) and n.value == KEY for n in ast.walk(tree)))
for code in ("es", "pt", "pl", "ru", "cs", "fr"):
    out = zxnu_i18n.ui_tr(KEY, code)
    check(f"[{code}] translated, placeholders kept",
          out != KEY and "{path}" in out and "{button}" in out, out)

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all default sync root checks passed")
sys.exit(0)
