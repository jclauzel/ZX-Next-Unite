"""CSpect runs in the BACKGROUND (9.7.51) - and nothing writes under it.

Reported 2026-10-19: the whole Unite window sat "(Not Responding)" for as long
as CSpect ran, and came back the moment CSpect was closed. launch_cspect
started CSpect with a blocking ``subprocess.run(..., shell=True)`` on the UI
thread: cmd.exe /c waits for the console-subsystem CSpect.exe, and run() waits
for cmd.exe. It now starts it with Popen and watches it from a reader thread,
the way launch_mame always has.

The freeze had been doing a second job nobody wrote down: while it lasted no
write into the disk image could start. CSpect opens -mmc with FULL read/write
sharing, so the OS does not refuse hdfmonkey either, and a live window would
let a transfer rewrite the FAT under a running NextZXOS. Hence the write gate
(_image_write_blocker), consulted by execute_hdf_monkey for every write verb.

Covered here, headlessly (CI runs this):
  * the pure helpers: Popen kwargs, exit-code classification and wording,
    console decoding, the listener-relisten rule;
  * the shell contract MEASURED with a real child process: the Popen handle
    tracks the child's whole lifetime through the shell, the exit code passes
    through, stderr is folded into stdout, and a missing program answers the
    codes cspect_exit_kind calls "not-found";
  * source tripwires on every seam (no blocking call left in launch_cspect,
    the record, the reader thread, each write gate placed BEFORE its write).

The real launch_cspect with a fake Popen is driven end to end in offscreen
phase 12 (tests/test_ui_offscreen.py).

Run with: python tests/test_cspect_launch.py
"""
import ast
import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from zxnu_config import (  # noqa: E402
    CSPECT_NOT_FOUND_EXIT_CODES_POSIX,
    CSPECT_NOT_FOUND_EXIT_CODES_WINDOWS,
    CSPECT_QUICK_EXIT_S,
    HDFMONKEY_WRITE_VERBS,
    TRANSFER_SUBTAB_CLASSIC,
    TRANSFER_SUBTAB_REMOTE,
    TRANSFER_SUBTAB_SDCARD,
    cspect_exit_kind,
    cspect_popen_kwargs,
    decode_console_line,
    describe_exit_code,
    transfer_relisten_allowed,
)

FAIL = []
WIN = sys.platform == "win32"


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as fh:
        return fh.read()


def _function_node(src, name):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def function_source(src, name):
    """The source of the (first) def called *name*, nested defs included."""
    node = _function_node(src, name)
    return (ast.get_source_segment(src, node) or "") if node else ""


def function_code(src, name):
    """The same function as CODE only: comments and every docstring gone.

    For the absence checks - the comments that explain a removed call name
    it, and must not read as the call being back."""
    node = _function_node(src, name)
    if node is None:
        return ""
    for sub in ast.walk(node):
        if (isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and sub.body and isinstance(sub.body[0], ast.Expr)
                and isinstance(getattr(sub.body[0], "value", None), ast.Constant)
                and isinstance(sub.body[0].value.value, str)):
            sub.body = sub.body[1:] or [ast.Pass()]
    return ast.unparse(node)


# ---- Popen kwargs -----------------------------------------------------------
kw_win = cspect_popen_kwargs("C:/cs", windows=True)
kw_posix = cspect_popen_kwargs(None, windows=False)
check("the launch keeps going through the shell (same command string)",
      kw_win["shell"] is True and kw_posix["shell"] is True)
check("the working directory is passed through", kw_win["cwd"] == "C:/cs"
      and kw_posix["cwd"] is None)
check("stdout is a pipe the reader drains, stderr folded into it",
      kw_win["stdout"] == subprocess.PIPE
      and kw_win["stderr"] == subprocess.STDOUT)
check("stdin is left exactly as the old launch had it",
      "stdin" not in kw_win and "stdin" not in kw_posix)
check("Windows detaches CSpect from Unite's console (new process group)",
      kw_win.get("creationflags") == 0x00000200, repr(kw_win.get("creationflags")))
check("...and never hides it: no CREATE_NO_WINDOW, no SW_HIDE startupinfo",
      not (kw_win.get("creationflags", 0) & 0x08000000)
      and "startupinfo" not in kw_win)
check("POSIX runs it in its own session", kw_posix.get("start_new_session") is True
      and "creationflags" not in kw_posix)

# ---- exit classification ------------------------------------------------------
check("exit code 0 is a normal close", cspect_exit_kind(0, 0.1, windows=True) == "ok"
      and cspect_exit_kind(0, 999, windows=False) == "ok")
for code in sorted(CSPECT_NOT_FOUND_EXIT_CODES_WINDOWS):
    check(f"cmd.exe {code} right after the launch means not found",
          cspect_exit_kind(code, 0.2, windows=True) == "not-found")
for code in sorted(CSPECT_NOT_FOUND_EXIT_CODES_POSIX):
    check(f"sh {code} right after the launch means not found",
          cspect_exit_kind(code, 0.2, windows=False) == "not-found")
check("cmd's measured not-found code is 1 (see the live measurement below)",
      1 in CSPECT_NOT_FOUND_EXIT_CODES_WINDOWS)
check("a quick exit 1 from a CSpect.exe known to exist is CSpect's own failure",
      cspect_exit_kind(1, 0.5, windows=True, program_known=True) == "early")
check("not-found is only ever a QUICK exit (a session ending 1 is a crash)",
      cspect_exit_kind(1, CSPECT_QUICK_EXIT_S + 5, windows=True) == "error"
      and cspect_exit_kind(127, CSPECT_QUICK_EXIT_S + 5, windows=False) == "error")
check("9009 is not special on POSIX", cspect_exit_kind(9009, 0.1, windows=False) == "early")
check("127 is not special on Windows", cspect_exit_kind(127, 0.1, windows=True) == "early")
check("a non-zero exit inside the quick window did not start",
      cspect_exit_kind(3221225477, CSPECT_QUICK_EXIT_S - 0.1, windows=True) == "early")
check("a non-zero exit after a real session is a crash",
      cspect_exit_kind(3221225477, CSPECT_QUICK_EXIT_S + 1, windows=True) == "error")
check("an unreadable elapsed time counts as quick, never raises",
      cspect_exit_kind(5, None, windows=True) == "early")

check("an access violation is named and shown in hex",
      describe_exit_code(3221225477) == "3221225477 (0xC0000005, access violation)",
      describe_exit_code(3221225477))
check("an unhandled .NET exception is named",
      "unhandled .NET exception" in describe_exit_code(0xE0434352))
check("an unknown NTSTATUS still gets its hex", describe_exit_code(0xC0001234)
      == f"{0xC0001234} (0xC0001234)", describe_exit_code(0xC0001234))
check("a POSIX signal death says so", describe_exit_code(-11) == "-11 (killed by signal 11)")
check("a plain exit code stays plain", describe_exit_code(1) == "1")
check("None and junk never raise", describe_exit_code(None) == "unknown"
      and describe_exit_code("x") == "x")

# ---- console decoding -----------------------------------------------------------
check("a CRLF line loses its line end", decode_console_line(b"hello\r\n") == "hello")
check("a stray byte never raises, it becomes U+FFFD",
      "\ufffd" in decode_console_line(b"bad \xff\xfe byte\n", windows=False))
check("text input passes through", decode_console_line("already text\n") == "already text")
check("the Windows OEM decode falls back cleanly where 'oem' does not exist",
      decode_console_line(b"abc\n", windows=True) == "abc")
if WIN:
    check("the OEM code page is what decodes a Windows console byte",
          decode_console_line(b"\x82\n", windows=True) == b"\x82".decode("oem"))

# ---- relisten rule (the 9.7.38 regression) ----------------------------------------
check("on the Remote Explorer page the listener comes back",
      transfer_relisten_allowed(TRANSFER_SUBTAB_REMOTE))
check("on the SD Card page it comes back too (the regression)",
      transfer_relisten_allowed(TRANSFER_SUBTAB_SDCARD, "remote"))
check("...unless Classic was the experience last shown",
      not transfer_relisten_allowed(TRANSFER_SUBTAB_SDCARD, "classic"))
check("on the Classic page it never does",
      not transfer_relisten_allowed(TRANSFER_SUBTAB_CLASSIC, "remote"))
check("a nonsense index never relistens", not transfer_relisten_allowed(-1))

# ---- the shell contract, MEASURED ------------------------------------------------
# A real child behind the exact kwargs: prints to stdout AND stderr, stays up
# a moment, exits 7. The handle must still be running right after Popen (the
# launch returned without waiting) and must report the child's own code.
scratch = tempfile.mkdtemp(prefix="zxnu-cspect-launch-")
script = os.path.join(scratch, "fake_cspect.py")
with open(script, "w", encoding="utf-8") as fh:
    fh.write("import sys, time\n"
             "print('fake cspect up', flush=True)\n"
             "print('fake cspect err', file=sys.stderr, flush=True)\n"
             "time.sleep(1.0)\n"
             "sys.exit(7)\n")
cmd = f'"{sys.executable}" "{script}"'
t0 = time.monotonic()
proc = subprocess.Popen(cmd, **cspect_popen_kwargs(scratch))
spawn_s = time.monotonic() - t0
check("Popen returns without waiting for the child",
      spawn_s < 0.9 and proc.poll() is None, f"{spawn_s:.2f}s poll={proc.poll()}")
out = proc.stdout.read()
rc = proc.wait()
text = [decode_console_line(x) for x in out.splitlines()]
check("the handle tracks the child's whole life through the shell", rc == 7, f"rc={rc}")
check("stdout reaches the reader", "fake cspect up" in text, repr(text))
check("stderr reaches it too (a crash's stack trace is the useful part)",
      "fake cspect err" in text, repr(text))

# A program the shell cannot find answers a code cspect_exit_kind calls
# "not-found" - measured, because the pre-9.7.51 code assumed 1 and was wrong.
missing = os.path.join(scratch, "no-such-dir", "CSpect.exe")
for label, command in (("a missing folder", f'"{missing}" -zxnext'),
                       ("a missing program", "zxnu-no-such-cspect-xyz -zxnext")):
    p = subprocess.Popen(command, **cspect_popen_kwargs(scratch))
    p.stdout.read()
    code = p.wait()
    check(f"{label} is classified not-found (measured exit code {code})",
          cspect_exit_kind(code, 0.1) == "not-found", f"code={code}")

# ---- launch_cspect: no blocking call left, the record and the reader ---------------
ops = read("zxnu_emulator_ops.py")
lc = function_source(ops, "launch_cspect")
check("launch_cspect is found", bool(lc))
check("launch_cspect no longer calls the blocking shell helper",
      "execute_shell_command(" not in function_code(ops, "launch_cspect"))
check("...nor subprocess.run", "subprocess.run(" not in function_code(ops, "launch_cspect"))
check("it starts CSpect with Popen and the shared kwargs",
      "subprocess.Popen(_cmd, **cspect_popen_kwargs(cspect_cwd))" in lc)
check("the command string is the one run() was given",
      '_cmd = cspect_executable + " " + cspect_arguments' in lc)
check("the image is recorded as held once Popen succeeded",
      "host._images_held_by_us.setdefault(_held_key, []).append(cspect_proc)" in lc
      and lc.index("subprocess.Popen(_cmd") < lc.index("_images_held_by_us.setdefault"))
check("the record is never released by hand (poll() prunes it)",
      ".remove(cspect_proc)" not in lc and "_images_held_by_us.pop" not in lc)
lc_code = function_code(ops, "launch_cspect")
check("the launch no longer brackets itself with the SD Card lock",
      "set_all_buttons_disabled(" not in lc_code
      and "set_all_buttons_enabled(" not in lc_code)
check("the reader is a plain daemon thread, never a pool task",
      "threading.Thread(target=_read_cspect_output" in lc and "daemon=True" in lc
      and "QThreadPool" not in lc_code and "QRunnable" not in lc_code)
check("the exit kind knows whether the program was already found on disk",
      "program_known=bool(use_bundled)" in lc)
reader = function_source(ops, "_read_cspect_output")
check("the reader drains to EOF, then waits, then reports - in a finally",
      "finally:" in reader and reader.index("finally:") < reader.index("proc.wait()")
      < reader.index("signals.finished.emit"))
check("a bad line cannot end the drain loop",
      reader.count("except Exception:") >= 2)
fin = function_source(ops, "_on_cspect_finished")
fin_code = function_code(ops, "_on_cspect_finished")
check("the exit handler re-probes the image THIS launch mounted",
      "_probe_image_write_access(_held_image)" in fin
      and "right_disk_image_path" not in fin_code)
check("the exit handler never unlocks the SD Card tab",
      "set_all_buttons_enabled" not in fin_code)
check("the signals object is kept alive until finished arrives",
      "host._cspect_signals_live.add(cspect_signals)" in lc
      and "host._cspect_signals_live.discard(_sig)" in fin)
check("the finished signal is queued to the UI thread",
      "cspect_signals.finished.connect(_on_cspect_finished, Qt.QueuedConnection)" in lc)

# ---- the write gate --------------------------------------------------------------------
wb = function_source(ops, "_image_write_blocker")
check("the write blocker exists and reads the record", "_images_held_by_us" in wb)
check("...without pruning it (workers call it; a write-back would race a launch)",
      "_images_held_by_us[" not in wb and ".pop(" not in wb
      and "_image_held_by_us(" not in wb)
check("the write verbs cover every hdfmonkey write the app uses",
      {"put", "mkdir", "rm", "rmdir", "rename"} <= HDFMONKEY_WRITE_VERBS
      and not ({"ls", "get"} & HDFMONKEY_WRITE_VERBS))

sd = read("zxnu_sdcard_ops.py")
ehm = function_source(sd, "execute_hdf_monkey")
check("execute_hdf_monkey refuses a write verb BEFORE running hdfmonkey",
      "if command_to_execute in HDFMONKEY_WRITE_VERBS:" in ehm
      and ehm.index("HDFMONKEY_WRITE_VERBS") < ehm.index("subprocess.run("))
check("the refusal is reported through a queued signal, not from the worker",
      "_image_write_refused_signals" in ehm and "add_main_log_window" not in
      ehm[ehm.index("HDFMONKEY_WRITE_VERBS"):ehm.index("execution_cmd =")])
check("the refusal reporter is queued to the UI thread",
      "refused.connect(\n        _on_image_write_refused, Qt.QueuedConnection)" in sd.replace("\r\n", "\n"))
ciw = function_source(sd, "_check_image_writable")
check("_check_image_writable asks the blocker before its r+b open",
      "_image_write_blocker" in ciw
      and ciw.index("_image_write_blocker") < ciw.index("open(clean, 'r+b')"))
dl = sd.replace("\r\n", "\n")
_ex = dl.find('open(target, "wb") as dst')
check("the NextZXOS extraction asks the blocker before truncating its target",
      _ex != -1 and dl.rfind("_blocker(target)", 0, _ex) != -1
      and _ex - dl.rfind("_blocker(target)", 0, _ex) < 900)

gp = read("zxnu_getit_pane.py")
sp = function_source(gp, "getit_on_starter_pack")
check("the starter pack refuses once, before its confirm, on a held card",
      "_image_write_blocker" in sp
      and sp.index("_image_write_blocker") < sp.index("QMessageBox.question("))

wz = read("zxnu_wizard.py")
sd_fn = function_source(wz, "_sp_do")
check("Wizzy's starter pack waits for the emulator to let go of the card",
      "_sp_image_held()" in sd_fn and "sp.wait_free" in sd_fn
      and sd_fn.index("_sp_image_held") < sd_fn.index("self._dismiss()"))

# The 'start with file' blocker must mirror the launcher, which still mounts
# (and re-probes) a loaded image on a FILE launch.
bl = function_source(ops, "_emulator_launch_blocker")
check("a file launch reports a held loaded image up front",
      "if autostart:" in bl and "_image_busy_reason(emulator)" in
      bl[bl.index("if autostart:"):bl.index('if emulator == "CSpect":')])

# ---- the listener restart and the config-file open --------------------------------------
pane = read("zxnu_nextsync_pane.py")
rl = function_code(pane, "_re_auto_relisten")
check("the relisten asks the shared rule, not a raw index compare",
      "transfer_relisten_allowed(" in rl and "TRANSFER_SUBTAB_REMOTE" not in rl)
tg = function_source(pane, "_nextsync_toggle_remote_explorer")
check("the experience last shown is remembered in memory on every toggle",
      'host._nextsync_last_experience = "remote" if checked else "classic"' in tg
      and tg.index("_nextsync_last_experience") < tg.index("if checked:"))

oc = function_code(ops, "open_cspect_configuration_file")
check("opening the config file no longer blocks on the editor",
      "execute_shell_command" not in oc and "subprocess.run" not in oc
      and "subprocess.Popen(['notepad.exe', path])" in oc)
check("...and no longer runs vim on a './'-prefixed absolute path",
      "vim" not in oc and "'./'" not in oc and "open_path_with_system_shell(path)" in oc)

# ---- every new message is translated -----------------------------------------------------
import zxnu_i18n  # noqa: E402

NEW = (
    "The disk image {path} is in use by an emulator started from ZX-Next-Unite. "
    "Close the emulator before changing the files on the image.",
    "CSpect exited with code {code}.",
    "CSpect stopped right after starting (exit code {code}).",
    "CSpect ended with an error (exit code {code}).",
    "Could not open the configuration file {path}.",
    "Disk image in use",
)
def _tr_literals(*names):
    """Every literal ui_tr_now() template in those modules, as Python reads
    it (implicit concatenation folded) - the exact catalog keys."""
    out = set()
    for name in names:
        for node in ast.walk(ast.parse(read(name))):
            if (isinstance(node, ast.Call) and node.args
                    and getattr(node.func, "id", getattr(node.func, "attr", "")) == "ui_tr_now"
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                out.add(node.args[0].value)
    return out


USED = _tr_literals("zxnu_emulator_ops.py", "zxnu_sdcard_ops.py")
for text in NEW:
    check(f"the code really asks for this exact template: {text[:40]}…",
          text in USED)
    for code in ("es", "pt", "pl", "ru", "cs", "fr"):
        out = zxnu_i18n.ui_tr(text, code)
        check(f"[{code}] translated: {text[:40]}…", out != text, out)
        for ph in ("{path}", "{code}"):
            if ph in text:
                check(f"[{code}] keeps {ph}: {text[:30]}…", ph in out, out)

# ---- review round 1 (9.7.51): the seams the reviewers found -----------------
# A Windows crash code does not fit Qt's signed int: through Signal(int) the
# slot saw -1073741819 for 0xC0000005 and called it "killed by signal". The
# signal is emitted from the reader THREAD and queued, so drive it that way.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import threading  # noqa: E402

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from zxnu_workers import CSpectProcessSignals, zip_extract_with_dialog  # noqa: E402

got = []
sig = CSpectProcessSignals()
sig.finished.connect(lambda rc: got.append(rc), Qt.QueuedConnection)
t = threading.Thread(target=lambda: sig.finished.emit(3221225477))
t.start()
t.join()
deadline = time.monotonic() + 5
while not got and time.monotonic() < deadline:
    QCoreApplication.processEvents()
    time.sleep(0.01)
check("a Windows crash code crosses the thread unchanged (no signed wrap)",
      got == [3221225477], repr(got))
check("...and is described as the access violation it is",
      bool(got) and "access violation" in describe_exit_code(got[0]),
      describe_exit_code(got[0]) if got else "")

# The local panes' Unzip asks the write gate per member: re-unzipping the
# archive a held image came from must not truncate it under the emulator.
import zipfile  # noqa: E402

zdir = os.path.join(scratch, "unzip")
os.makedirs(zdir)
zpath = os.path.join(zdir, "pack.zip")
with zipfile.ZipFile(zpath, "w") as zf:
    zf.writestr("held.img", b"FRESH COPY")
    zf.writestr("readme.txt", b"hello")
held = os.path.join(zdir, "held.img")
with open(held, "wb") as fh:
    fh.write(b"MOUNTED IMAGE")
lines = []
res = zip_extract_with_dialog(
    None, zpath, zdir, log=lines.append,
    refuse=lambda p: "in use" if os.path.basename(p) == "held.img" else "")
with open(held, "rb") as fh:
    held_after = fh.read()
check("Unzip leaves a held image untouched", held_after == b"MOUNTED IMAGE",
      repr(held_after))
check("...extracts the rest", os.path.isfile(os.path.join(zdir, "readme.txt"))
      and res["files"] == 1 and res["skipped"] == 1, repr(res))
check("...and says why it skipped the image",
      any("held.img" in ln and "in use" in ln for ln in lines), repr(lines))
for caller, src_name in (("_local_unzip_file", "zxnu_sdcard_ops.py"),
                         ("_local_unzip", "zxnu_remote_explorer.py")):
    body = function_source(read(src_name), caller)
    check(f"the {caller} Unzip passes the write gate",
          "refuse=" in body and "_image_write_blocker" in body, src_name)

# Cutting image -> PC ends in deletes from the image: asked BEFORE the copy.
pil = function_source(sd, "_explorer_paste_into_local")
check("a cut from the image is refused before the copy when the image is held",
      "if is_cut:" in pil and "_check_image_writable" in pil
      and pil.index("_check_image_writable") < pil.index("image_get_paths_to_local"))

# Review round 2: an emulator this app did NOT start (one an earlier Unite
# left running) is seen by the Windows probe; the write check re-probes that
# cached verdict, never a blind fresh probe (its own hdfmonkey would read busy).
ciw = function_source(sd, "_check_image_writable")
check("_check_image_writable re-probes a cached BUSY and refuses while it holds",
      "== IMAGE_WRITE_BUSY" in ciw and "if not _free:" in ciw
      and ciw.index("if not _free:") < ciw.index("open(clean, 'r+b')"))
check("...re-gating the launch surfaces when it runs on the UI thread",
      "_free = _regate(clean)" in ciw and "threading.main_thread()" in ciw)
check("...and never writing the UI thread's cache from a worker",
      "_free = probe_image_write_access(clean) != IMAGE_WRITE_BUSY" in ciw)
check("the write blocker compares file identity when the text key misses",
      "os.path.samefile(clean, other)" in wb and "if not hit and held:" in wb)
check("...against live holders only (a dead one neither costs a stat nor ends the search)",
      "if other == key or not _alive(plist):" in wb)

# The click-time questions re-probe a cached BUSY, as the launcher does.
check("the file-launch blocker re-probes a stale cached BUSY",
      "_probe_image_write_access(path)" in bl[bl.index("if autostart:"):])
sar = function_source(sd, "_send_and_start_refused")
check("Send-and-start re-probes before asking the cached gate",
      "reprobe(path)" in sar and sar.index("reprobe(path)") < sar.index("blocker(emulator)"))

# Wizzy: a wait belongs to its bubble and to Wizzy being on.
say = function_code(wz, "_say")
check("a new bubble cancels a pending wait", "self._cancel_qs_wait()" in say)
# The RAW source here, not function_code: ast.unparse spells a tuple
# assignment differently across Python versions (3.10 vs 3.13), and CI runs
# both. "self._say(" occurs in this function's code only.
qw = function_source(wz, "_qs_wait")
_arm = "self._qs_pred, self._qs_then = predicate, then"
check("a wait is armed only after its own bubble is said",
      _arm in qw and qw.index("self._say(") < qw.index(_arm))
se = function_source(wz, "set_enabled")
check("turning Wizzy off cancels a pending wait",
      "self._cancel_qs_wait()" in se[se.index("else:"):])
check("a tick never fires a wait while Wizzy is off",
      "not self.enabled()" in function_code(wz, "_qs_tick"))
gs = function_code(wz, "_goto_subtab")
check("a tour stop on Classic does not change the remembered experience",
      "_nextsync_last_experience" in gs and "_prev_exp" in gs)

# Viewers opened while CSpect ran get their Launch buttons back on exit.
rel = function_code(ops, "_refresh_emulator_launchability")
check("every re-gate re-wires the gallery viewers still open",
      "_rewire_open_viewers" in rel)
wve = function_source(ops, "_wire_viewer_emulators")
check("a wired viewer is remembered weakly", "host._wired_viewers[viewer] = allow" in wve
      and "host._wired_viewers = weakref.WeakKeyDictionary()" in ops)

# The crash report keeps the exception line and reaches the file log.
check("the first exception line is kept apart from the tail",
      '_run["first_error"] = line' in reader and "shown = [first, " in fin)
check("a long run's crash output is copied to the file log",
      'if _run.get("lines", 0) > CSPECT_OUTPUT_LOG_LINES:' in fin)

import zxnu_wizard_content as wc  # noqa: E402

check("Wizzy's wait line exists in all seven languages",
      set(wc.TEXTS.get("sp.wait_free", {})) >= {"en", "es", "pt", "pl", "ru", "cs", "fr"})

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all CSpect launch checks passed")
sys.exit(0)
