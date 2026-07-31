"""MAME self-extractor watchdog tests (zxnu_emulator_ops).

MAME's official Windows package is a GUI 7-Zip self-extractor. Run hidden it
unpacks silently and exits 0 — but when it cannot write a file (usually
because MAME itself is running, so mame.exe is locked) it parks on a
confirmation dialog nobody can see and never exits. These cover the guards
that turn that hang into a clear failure:

  - _mame_dest_fingerprint: the (files, bytes) progress signal,
  - _mame_file_is_locked: the pre-flight that refuses to start on a busy
    mame.exe (and never trips on a missing or writable file),
  - _mame_extract_sfx: exits cleanly, propagates a non-zero status, kills and
    reports a stalled extractor, honours the absolute timeout, and does NOT
    stall while the extractor is still writing files.

Run with: python tests/test_mame_install.py
"""
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

import zxnu_emulator_ops as ops  # noqa: E402

FAIL = []
def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


BASE = tempfile.mkdtemp(prefix="zxnu-mame-test-")


def write(path, payload=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(payload)
    return path


# ── _mame_dest_fingerprint ───────────────────────────────────────────────
d = os.path.join(BASE, "fingerprint")
os.makedirs(d)
check("fingerprint of an empty tree is (0, 0)",
      ops._mame_dest_fingerprint(d) == (0, 0))

write(os.path.join(d, "mame.exe"), b"1234")
write(os.path.join(d, "hash", "nested.xml"), b"12345")
check("fingerprint counts files at every depth and sums their bytes",
      ops._mame_dest_fingerprint(d) == (2, 9),
      repr(ops._mame_dest_fingerprint(d)))

before = ops._mame_dest_fingerprint(d)
write(os.path.join(d, "mame.exe"), b"1234567890")
check("fingerprint changes when a file grows (an extractor is working)",
      ops._mame_dest_fingerprint(d) != before)


# ── _mame_file_is_locked ─────────────────────────────────────────────────
check("a file that does not exist is not 'locked'",
      ops._mame_file_is_locked(os.path.join(BASE, "nope.exe")) is False)

plain = write(os.path.join(BASE, "plain.exe"))
check("a writable file is not locked",
      ops._mame_file_is_locked(plain) is False)

busy = write(os.path.join(BASE, "busy.exe"))
if sys.platform.startswith("win"):
    # A running executable is held with share mode 0 (deny all), so opening it
    # for writing fails with a sharing violation — the exact situation the
    # pre-flight exists for. Reproduce it with CreateFileW rather than a
    # byte-range lock, which would not stop the open at all.
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    GENERIC_READ, OPEN_EXISTING, INVALID = 0x80000000, 3, wintypes.HANDLE(-1).value
    handle = kernel32.CreateFileW(busy, GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
    if handle == INVALID:
        print("SKIP  running-executable case (CreateFileW failed: "
              f"{ctypes.get_last_error()})")
    else:
        check("a file held like a running executable reads as locked",
              ops._mame_file_is_locked(busy) is True)
        kernel32.CloseHandle(wintypes.HANDLE(handle))
    check("...and it is writable again once the holder lets go",
          ops._mame_file_is_locked(busy) is False)
elif os.geteuid() != 0:
    os.chmod(busy, 0o444)
    check("a read-only file reads as locked (it cannot be replaced either)",
          ops._mame_file_is_locked(busy) is True)
    os.chmod(busy, 0o644)
else:
    print("SKIP  locked-file case (running as root: no file is unwritable)")


# ── _mame_extract_sfx ────────────────────────────────────────────────────
class FakeClock:
    """Monotonic clock the tests drive by hand, so the watchdog's minutes-long
    windows can be crossed instantly."""
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class FakeProc:
    """Stand-in for the self-extractor: raises TimeoutExpired from wait()
    until it is told to exit, and records whether it was killed."""
    def __init__(self, exit_after=None, returncode=0, on_wait=None):
        self.exit_after = exit_after
        self.returncode_value = returncode
        self.on_wait = on_wait
        self.waits = 0
        self.killed = False

    def wait(self, timeout=None):
        self.waits += 1
        if self.on_wait:
            self.on_wait(self.waits)
        if self.exit_after is not None and self.waits >= self.exit_after:
            return self.returncode_value
        raise subprocess.TimeoutExpired("sfx", timeout)

    def kill(self):
        # A real process reaps immediately afterwards; mirroring that keeps
        # _mame_sfx_kill's "still alive after kill()" warning out of the way.
        self.killed = True
        self.exit_after = self.waits
        self.returncode_value = -9


class FakeSubprocess:
    """Module shim so _mame_extract_sfx talks to FakeProc without ever
    spawning anything."""
    DEVNULL = subprocess.DEVNULL
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, proc):
        self.proc = proc
        self.popen_args = None

    def Popen(self, args, **kwargs):
        self.popen_args = args
        return self.proc


def run_extract(proc, clock, stall=30, timeout=100000):
    """Call _mame_extract_sfx with the module's subprocess/time/limits patched.
    Returns (raised_exception_or_None, fake_subprocess)."""
    dest = tempfile.mkdtemp(prefix="zxnu-mame-dest-", dir=BASE)
    fake_sub, real_sub = FakeSubprocess(proc), ops.subprocess
    real_time, real_stall, real_timeout = (
        ops.time, ops.MAME_SFX_STALL_SECONDS, ops.MAME_SFX_TIMEOUT_SECONDS)
    ops.subprocess, ops.time = fake_sub, clock
    ops.MAME_SFX_STALL_SECONDS, ops.MAME_SFX_TIMEOUT_SECONDS = stall, timeout
    try:
        ops._mame_extract_sfx(os.path.join(dest, "installer.exe"), dest)
        return None, fake_sub
    except Exception as exc:
        return exc, fake_sub
    finally:
        ops.subprocess, ops.time = real_sub, real_time
        ops.MAME_SFX_STALL_SECONDS = real_stall
        ops.MAME_SFX_TIMEOUT_SECONDS = real_timeout


# 1. Clean run: the extractor exits 0 on the first wait.
clock = FakeClock()
proc = FakeProc(exit_after=1, returncode=0)
err, sub = run_extract(proc, clock)
check("a self-extractor that exits 0 is a success", err is None, repr(err))
check("the extractor is invoked with -o<dir> and -y",
      sub.popen_args is not None
      and any(str(a).startswith("-o") for a in sub.popen_args)
      and "-y" in sub.popen_args,
      repr(sub.popen_args))

# 2. Non-zero exit is surfaced, not swallowed.
clock = FakeClock()
err, _ = run_extract(FakeProc(exit_after=1, returncode=3), clock)
check("a non-zero exit status raises",
      isinstance(err, RuntimeError) and "status 3" in str(err), repr(err))

# 3. The hang: alive, but not a single new byte written.
clock = FakeClock()
stalled = FakeProc(on_wait=lambda n: setattr(clock, "now", clock.now + 10))
err, _ = run_extract(stalled, clock, stall=30)
check("an extractor that writes nothing is cancelled",
      isinstance(err, RuntimeError), repr(err))
check("the stall message names the cause the user can act on",
      isinstance(err, RuntimeError) and "MAME is still running" in str(err),
      repr(err))
check("the stalled extractor is killed (it holds its own .exe locked)",
      stalled.killed is True)

# 4. Still writing => no stall, however long it takes.
clock = FakeClock()
dest_seen = {}

def keep_writing(n):
    clock.now += 10                       # each slice costs more than the window
    write(os.path.join(dest_seen["dir"], f"file{n}.bin"), b"y" * n)

busy_proc = FakeProc(exit_after=20, returncode=0, on_wait=keep_writing)
# run_extract makes the dest dir itself, so hand the writer the same path.
_real_fingerprint = ops._mame_dest_fingerprint
def _remember(root):
    dest_seen["dir"] = root
    return _real_fingerprint(root)
ops._mame_dest_fingerprint = _remember
try:
    err, _ = run_extract(busy_proc, clock, stall=30)
finally:
    ops._mame_dest_fingerprint = _real_fingerprint
check("an extractor still writing files is never cancelled",
      err is None, repr(err))
check("...and it was given all the slices it asked for",
      busy_proc.waits == 20, str(busy_proc.waits))
check("...and was not killed", busy_proc.killed is False)

# 5. The absolute cap still applies to an extractor that writes forever.
clock = FakeClock()
ops._mame_dest_fingerprint = _remember
try:
    forever = FakeProc(on_wait=keep_writing)
    err, _ = run_extract(forever, clock, stall=100000, timeout=100)
finally:
    ops._mame_dest_fingerprint = _real_fingerprint
check("the absolute timeout cancels an extractor that never finishes",
      isinstance(err, RuntimeError), repr(err))
check("...and kills it too", forever.killed is True)


print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all MAME install watchdog tests passed")
sys.exit(0)
