"""Starting a .nex in MAME's ZX Spectrum Next through NextZXOS (9.7.55).

Through 9.7.54 "Start MAME with file X" passed MAME "-snapshot X". On every
Next driver (MAME_ROM_CHOICE is all Next machines) the snapshot device has no
delay, so the program was poked into RAM at power-on, BEFORE the TBBlue
firmware ran; the firmware then loaded the ROMs over it and hung after its own
reset into NextZXOS - a corrupted firmware screen or a black one (measured on
MAME 0.289, specnext_ks2 and tbblue alike, PC parked at $6011). Loading the
file later is not enough either: ZX Next Remote asks NextZXOS for memory and
finds its own files on the card, so a .nex poked in from the PC hangs.

So the file goes ON THE CARD (in place when the SD Card tab knows where it
is, else into /zxnu-start) and NextZXOS starts it: a generated Lua autoboot
script waits for the firmware's reset, lets NextZXOS settle, opens the
Command Line and types `cd "c:/<folder>"` and `.nexload <name>` on the
emulated membrane (SYMBOL SHIFT chords - MAME's natural keyboard drops every
symbol on the Next).

Covered here:
  * the pure helpers: card path -> command lines, the staged name, the
    autoboot-option strip, the Lua string literal, the script generator;
  * the Lua typist knows every character a generated line can hold;
  * the REAL launch_mame, built by build_emulator_ops on a fake host with a
    fake hdfmonkey and Popen replaced by a recorder: the argv, the hdfmonkey
    calls, the script on disk, and every refusal;
  * the up-front blocker the Remote Explorer asks before downloading;
  * a live smoke run of the generated script in a real MAME when one is
    found under downloads/mame (skipped on CI): it must parse, find every
    key it types with, and report that NextZXOS never started (no card).

Hardware-free end to end (a real NextZXOS card, ZX Next Remote and a game
starting through .nexload) was checked by hand on MAME 0.289; it needs a
licensed card image and is not part of the suite.

Run with: python tests/test_mame_nextzxos_start.py
"""
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

import zxnu_config  # noqa: E402
import zxnu_emulator_ops  # noqa: E402
from zxnu_config import (  # noqa: E402
    MAME_NEXT_CARD_STAGING_DIR,
    NEXTZXOS_TYPED_CHARS,
    SETTING_MAME_COMMAND_LINE_PARAMETERS,
    SETTING_MAME_ROM_CHOICE,
    lua_string_literal,
    mame_nextzxos_autostart_lua,
    nextzxos_card_launch_lines,
    nextzxos_staged_name,
    strip_mame_autoboot_options,
)

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


# ---- card path -> the two Command Line entries ---------------------------
print("== nextzxos_card_launch_lines ==")
check("a file in a folder: cd into it, .nexload by name",
      nextzxos_card_launch_lines("/games/foo.nex")
      == ('cd "c:/games"', ".nexload foo.nex"))
check("a file at the card root: cd c:/",
      nextzxos_card_launch_lines("/foo.nex") == ('cd "c:/"', ".nexload foo.nex"))
check("no leading slash, backslashes and doubled slashes are normalised",
      nextzxos_card_launch_lines("games\\sub//Foo_1-2.NEX")
      == ('cd "c:/games/sub"', ".nexload Foo_1-2.NEX"))
check("a folder with a space is fine (the cd line quotes it)",
      nextzxos_card_launch_lines("/my games/x.nex")
      == ('cd "c:/my games"', ".nexload x.nex"))
for bad, why in (
        ("/games/foo bar.nex", "space in the file name"),
        ('/games/fo"o.nex', "quote in the file name"),
        ("/games/fo:o.nex", "colon in the file name"),
        ("/games/caf\u00e9.nex", "non-ASCII file name"),
        ("/jeux/\u00e9t\u00e9/x.nex", "non-ASCII folder"),
        ('/a"b/x.nex', "quote in the folder"),
        ("/games/-x.nex", "a name that reads as an option"),
        ("/games/readme.txt", "not a .nex"),
        ("/games/../x.nex", "a .. component"),
        ("/games/ x.nex", "a component with outer spaces"),
        ("/" + "d" * 130 + "/x.nex", "a cd line over the limit"),
        ("", "empty"),
        (None, "None")):
    check(f"refused: {why}", nextzxos_card_launch_lines(bad) is None, repr(bad))

# ---- the staged name -----------------------------------------------------
print("== nextzxos_staged_name ==")
check("a typeable name is kept as is (ZXNR finds its files by name)",
      nextzxos_staged_name("zxnextremote-zxnt.nex") == "zxnextremote-zxnt.nex")
check("a host path is reduced to its name",
      nextzxos_staged_name("C:\\Users\\me\\Santa Wagon.nex") == "Santa_Wagon.nex")
check("untypeable characters become _",
      nextzxos_staged_name('a"b:c d\u00e9.nex') == "a_b_c_d_.nex")
check("a leading - cannot make an option", nextzxos_staged_name("-x.nex") == "_x.nex")
check("an empty stem still makes a name", nextzxos_staged_name(".nex") == "start.nex")
check("a long name is shortened, extension kept",
      nextzxos_staged_name("a" * 200 + ".nex") == "a" * 48 + ".nex")
nasty = ["Santa Wagon (v2).nex", "  spaced  .nex", "\u00fcber.NEX", "-.nex",
         'q"u:o;t/e.nex', "x" * 300 + ".nex", "..nex", "a.b.c.nex"]
check("every staged name makes a typeable card path",
      all(nextzxos_card_launch_lines(MAME_NEXT_CARD_STAGING_DIR + "/"
                                     + nextzxos_staged_name(n)) for n in nasty),
      str([n for n in nasty if not nextzxos_card_launch_lines(
          MAME_NEXT_CARD_STAGING_DIR + "/" + nextzxos_staged_name(n))]))

# ---- autoboot options in the user's MAME parameters ----------------------
print("== strip_mame_autoboot_options ==")
kept, dropped = strip_mame_autoboot_options(
    ["-window", "-autoboot_script", "x.lua", "-SCRIPT", "y.lua",
     "-ab", "PRINT 1", "-autoboot_command", "z", "-autoboot_delay", "5",
     "-aspect", "2:1"])
check("every autoboot option and its value is dropped, aliases and case too",
      kept == ["-window", "-aspect", "2:1"], str(kept))
check("the dropped pairs are reported",
      dropped == ["-autoboot_script x.lua", "-SCRIPT y.lua", "-ab PRINT 1",
                  "-autoboot_command z", "-autoboot_delay 5"], str(dropped))
check("a trailing option with no value is dropped alone",
      strip_mame_autoboot_options(["-window", "-script"])
      == (["-window"], ["-script"]))

# ---- the Lua string literal ----------------------------------------------
print("== lua_string_literal ==")


def lua_unquote(lit):
    """Just enough of Lua's double-quoted string rules to read ours back."""
    assert lit[0] == lit[-1] == '"'
    out, i, body = bytearray(), 0, lit[1:-1]
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            nxt = body[i + 1]
            if nxt.isdigit():
                out.append(int(body[i + 1:i + 4]))
                i += 4
                continue
            out += nxt.encode()
            i += 2
            continue
        out += ch.encode()
        i += 1
    return out.decode("utf-8")


for sample in ('cd "c:/games"', "back\\slash", "new\nline", "caf\u00e9 12",
               "\u0417\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439",
               "tab\there", "7\x01" + "23"):
    lit = lua_string_literal(sample)
    check(f"round-trips {sample!r}", lua_unquote(lit) == sample, lit)
    check(f"is plain printable ASCII: {sample!r}",
          all(32 <= ord(c) < 127 for c in lit), lit)

# ---- the generated script ------------------------------------------------
print("== mame_nextzxos_autostart_lua ==")
lines = ('cd "c:/zxnu test/Sub-1"', ".nexload Santa_Magic-22.nex")
lua = mame_nextzxos_autostart_lua(lines, "Santa_Magic-22.nex", settle=123, give_up=456)
check("both lines are embedded as Lua literals",
      "local LINES = { %s, %s }" % tuple(lua_string_literal(x) for x in lines) in lua)
check("the settle and give-up counts are embedded",
      "local SETTLE, GIVE_UP = 123, 456" in lua)
check("no placeholder is left", "@@" not in lua)
tricky = mame_nextzxos_autostart_lua(("cd \"c:/\"",), "@@LINES@@\nx")
check("a label spelling a placeholder is substituted once only",
      tricky.count("local LINES = {") == 1 and '"@@LINES@@\\010x"' in tricky
      and "start @@LINES@@?x through" in tricky.splitlines()[0],
      tricky.splitlines()[0])
try:
    mame_nextzxos_autostart_lua(("cd \"c:/caf\u00e9\"",), "x")
    check("a line the typist cannot type is refused", False)
except ValueError:
    check("a line the typist cannot type is refused", True)
# The Lua typist's own table must cover every symbol a line can hold: letters
# (capitals through CAPS SHIFT), digits and space are the generic keys.
sym = re.search(r"local SYM = \{(.*?)\}", zxnu_config._MAME_NEXTZXOS_LUA, re.S).group(1)
sym_keys = set(re.findall(r"\[(?:'(.)'|\"(.)\")\]", sym))
sym_keys = {a or b for a, b in sym_keys}
symbols = {c for c in NEXTZXOS_TYPED_CHARS if not c.isalnum() and c != " "}
check("the Lua typist has a chord for every symbol a line can hold",
      symbols <= sym_keys, str(sorted(symbols - sym_keys)))
check("the generator's charset is the launcher's charset (lines are typeable)",
      all(set(x) <= NEXTZXOS_TYPED_CHARS for x in lines))

# ---- the real launch_mame on a fake host ---------------------------------
print("== launch_mame (real closure, fake host) ==")
work = tempfile.mkdtemp(prefix="zxnu-nextzxos-test-")
image = os.path.join(work, "next.img")
with open(image, "wb") as fh:
    fh.write(b"\0" * 4096)
nex = os.path.join(work, "Santa Wagon.nex")
with open(nex, "wb") as fh:
    fh.write(b"Next" + b"\0" * 508)


class _Combo:
    def __init__(self, text=""):
        self.text = text

    def currentText(self):
        return self.text


class FakeHost:
    """Only what launch_mame and the blocker read; anything else raises, so a
    new dependency shows up as an AttributeError rather than a silent pass."""

    def __init__(self):
        self.imageinput = _Combo(image)
        self._mame_executable_path = os.path.join(work, "mame", "mame.exe")
        self.right_disk_image_path = image
        self.hdf_calls = []
        self.put_result = 0
        self.toasts = []

    def _mame_flatpak_enabled(self):
        return False

    def _mame_usable(self):
        return True

    def execute_hdf_monkey(self, verb, img, additional_args="", silent=False,
                           extra_argv=None, prompt_if_missing=True):
        self.hdf_calls.append((verb, img, list(extra_argv or [])))
        rc = self.put_result if verb == "put" else 0
        return subprocess.CompletedProcess(
            args=[verb], returncode=rc,
            stdout=b"Error: disk full" if rc else b"", stderr=b"")

    def _show_toast(self, title, body, **_kw):
        self.toasts.append((title, body))


log = []
config = {SETTING_MAME_ROM_CHOICE: "specnext_ks2"}
host = FakeHost()
zxnu_emulator_ops.build_emulator_ops(
    host, configuration_dictionary=config, save_configuration_file=lambda: None,
    set_all_buttons_disabled=lambda: None, set_all_buttons_enabled=lambda: None,
    _update_mame_controls=lambda: None, _right_disk_content=lambda: [("x", 0)],
    getit_run_in_thread=lambda *a, **k: None,
    _start_load_image_hint_animation=lambda: None,
    add_main_log_window=log.append, execute_shell_command=lambda *a, **k: None)
launch = host._launch_mame_fn
started = []
_real_popen = subprocess.Popen


def _recording_popen(argv, **_kw):
    started.append(list(argv))
    raise OSError("test: MAME is not really started")


def run(*args, **kwargs):
    """One launch: returns (argv or None, hdfmonkey calls, script text)."""
    del started[:], host.hdf_calls[:], host.toasts[:], log[:]
    zxnu_emulator_ops.subprocess.Popen = _recording_popen
    logging.disable(logging.CRITICAL)      # the recorder's "failed to launch"
    try:
        launch(*args, **kwargs)
    finally:
        zxnu_emulator_ops.subprocess.Popen = _real_popen
        logging.disable(logging.NOTSET)
    argv = started[0] if started else None
    script = ""
    if argv and "-autoboot_script" in argv:
        _path = argv[argv.index("-autoboot_script") + 1]
        with open(_path, encoding="utf-8") as fh:
            script = fh.read()
        os.remove(_path)                 # the launcher's own; do not leak it
    return argv, list(host.hdf_calls), script


# (a) a file only on the PC: staged into /zxnu-start, under a typeable name
argv, calls, script = run(nex)
staged = MAME_NEXT_CARD_STAGING_DIR + "/Santa_Wagon.nex"
check("MAME was started", argv is not None, str(host.toasts))
check("the file is put onto the card, in the staging folder",
      calls == [("mkdir", image, [MAME_NEXT_CARD_STAGING_DIR]),
                ("put", image, [nex.replace("\\", "/"), staged])], str(calls))
check("no -snapshot (it loads at power-on, under the firmware)",
      argv is not None and "-snapshot" not in argv, str(argv))
check("the autoboot script runs from power-on",
      argv is not None and argv[argv.index("-autoboot_delay") + 1] == "0")
check("-hard1 <image> stays the final pair",
      argv is not None and argv[-2:] == ["-hard1", os.path.abspath(image)],
      str(argv[-4:] if argv else None))
check("the script types cd into the staging folder and .nexload by name",
      lua_string_literal('cd "c:%s"' % MAME_NEXT_CARD_STAGING_DIR) in script
      and lua_string_literal(".nexload Santa_Wagon.nex") in script)
check("the console says where NextZXOS will start it from",
      any(staged in line for line in log), str(log))

# (b) already on the card at a typeable path: started in place, nothing written
argv, calls, script = run(nex, card_path="/games/Foo-1.nex")
check("a typeable card path is started in place: no hdfmonkey write",
      argv is not None and calls == [], str(calls))
check("...from its own folder",
      lua_string_literal('cd "c:/games"') in script
      and lua_string_literal(".nexload Foo-1.nex") in script)

# (b2) the card path names the EXPLORER's image; MAME mounts the image box's.
# When they differ the in-place path would .nexload from the wrong card.
other = os.path.join(work, "other.img")
with open(other, "wb") as fh:
    fh.write(b"\0" * 4096)
host.right_disk_image_path = other
argv, calls, script = run(nex, card_path="/games/Foo-1.nex")
host.right_disk_image_path = image
check("a card path on another image than the mounted one is not trusted",
      argv is not None and calls and calls[-1][0] == "put"
      and calls[-1][1] == image and calls[-1][2][1] == staged, str(calls))

# (c) on the card at an untypeable path: the host copy is staged instead
argv, calls, script = run(nex, card_path="/my games/Santa Wagon.nex")
check("an untypeable card path falls back to staging the host copy",
      argv is not None and calls and calls[-1][0] == "put"
      and calls[-1][2][1] == staged, str(calls))

# (d) autoboot options the user typed are dropped for this launch
config[SETTING_MAME_COMMAND_LINE_PARAMETERS] = (
    "-window -autoboot_script mine.lua -autoboot_delay 7")
argv, calls, script = run(nex, card_path="/games/Foo-1.nex")
config.pop(SETTING_MAME_COMMAND_LINE_PARAMETERS)
check("only the launcher's autoboot options remain",
      argv is not None and argv.count("-autoboot_script") == 1
      and "mine.lua" not in argv and argv.count("-autoboot_delay") == 1
      and argv[argv.index("-autoboot_delay") + 1] == "0", str(argv))
check("...and the console says which were left out",
      any("-autoboot_script mine.lua" in line for line in log), str(log))

# (e) a failed put refuses the launch and says why
host.put_result = 1
argv, calls, script = run(nex)
host.put_result = 0
check("a failed copy onto the card does not start MAME", argv is None)
check("...and the toast carries hdfmonkey's own words",
      any("disk full" in body for _t, body in host.toasts), str(host.toasts))

# (f) no image selected: MAME's Next cannot boot, so a file is refused
host.imageinput.text = ""
argv, calls, script = run(nex)
check("no image: refused before anything is written",
      argv is None and calls == [], str(calls))
check("...with the reason (NextZXOS boots from the card)",
      any("NextZXOS" in body for _t, body in host.toasts), str(host.toasts))
check("the Remote Explorer's up-front question says the same for MAME",
      "NextZXOS" in host._emulator_launch_blocker("MAME", autostart=True))
check("...while CSpect still starts a file with no image",
      host._emulator_launch_blocker("CSpect", autostart=True) == "")
host.imageinput.text = image

# (g) the Launch button: Qt's clicked(bool), no file, no script
argv, calls, script = run(False)
check("the plain Launch button adds no autoboot script and writes nothing",
      argv is not None and "-autoboot_script" not in argv and calls == [],
      str(argv))

# (h) a file NextZXOS's .nexload cannot start is not handed over at all
sna = os.path.join(work, "x.sna")
open(sna, "wb").close()
argv, calls, script = run(sna)
check("a non-.nex file starts MAME without it, and says so",
      argv is not None and "-autoboot_script" not in argv and calls == []
      and any("cannot load x.sna" in line for line in log), str(log))

# ---- the generated script in a real MAME (skipped without one) -----------
print("== live: the script in a real MAME ==")
mame_dir = os.path.join(REPO, "downloads", "mame")
mame_exe = os.path.join(mame_dir, "mame.exe" if os.name == "nt" else "mame")
if not (os.path.isfile(mame_exe)
        and os.path.isfile(os.path.join(mame_dir, "roms", "tbblue.zip"))):
    print("SKIP  no MAME with roms/tbblue.zip under downloads/mame")
else:
    live = os.path.join(work, "live")
    os.makedirs(live)
    script_path = os.path.join(live, "t.lua")
    with open(script_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(mame_nextzxos_autostart_lua(
            ('cd "c:/zxnu-start"', ".nexload Foo_1-2.nex"), "Foo_1-2.nex",
            give_up=60))
    # No card: the firmware never resets, so the script must say it gave up.
    # Private cfg/nvram folders: MAME writes both on exit.
    try:
        res = subprocess.run(
            [mame_exe, "specnext_ks2", "-rompath", "roms", "-video", "none",
             "-sound", "none", "-nothrottle", "-skip_gameinfo",
             "-seconds_to_run", "4",
             "-cfg_directory", os.path.join(live, "cfg"),
             "-nvram_directory", os.path.join(live, "nvram"),
             "-snapshot_directory", os.path.join(live, "snap"),
             "-autoboot_delay", "0", "-autoboot_script", script_path],
            cwd=mame_dir, capture_output=True, timeout=90)
        out = (res.stdout + res.stderr).decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        out = f"<MAME did not run: {exc}>"
    check("MAME runs the script and it reaches its give-up report",
          "ZX-Next-Unite: NextZXOS did not start" in out, out[-600:])
    check("the script found every key it types with (no 'cannot type')",
          "cannot type" not in out, out[-600:])
    check("no Lua error", "error" not in out.lower().replace(
        "error loading", "").replace("errors", ""), out[-600:])

shutil.rmtree(work, ignore_errors=True)
print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all NextZXOS start checks passed")
sys.exit(0)
