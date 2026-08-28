"""Disk-image "already in use" gating (9.6.2).

An emulator holds the .img/.hdf it was handed for its whole run, so a second
one pointed at the same file dies mounting it. Every launch affordance now
asks first, greys itself out and says which file is busy.

WHAT THE PROBE CAN ACTUALLY SEE — measured against both emulators on Windows
11, not assumed, because the obvious implementation misses half of it:

  * MAME opens -hard1 with FILE_SHARE_READ, so it refuses a second writer.
    A plain open(path, "r+b") is enough to notice.
  * CSpect opens -mmc with full read/write sharing, so it refuses nobody.
    A write-open SUCCEEDS while CSpect is running — the gate would simply
    never fire. Only an EXCLUSIVE request (share mode 0) fails, because
    CSpect's handle exists at all.

Hence probe_image_write_access asks for exclusive access, and the two
Windows cases below pin exactly that: a share=READ holder AND a share=
READ|WRITE holder must both read as BUSY. A regression to open(path, "r+b")
still passes the first and fails the second.

On Linux and macOS the probe cannot answer at all — POSIX has no mandatory
locking and neither emulator takes an advisory flock — so those cases are
SKIPPED rather than failed, and the app falls back to its record of the
emulators it launched itself.

Run with: python tests/test_image_lock.py
"""
import logging
import os
import platform
import re
import stat
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from zxnu_config import (  # noqa: E402
    IMAGE_WRITE_BUSY,
    IMAGE_WRITE_DENIED,
    IMAGE_WRITE_MISSING,
    IMAGE_WRITE_OK,
    probe_image_write_access,
)
from zxnu_i18n import CATALOGS  # noqa: E402
from zxnu_workers import (  # noqa: E402
    EmulatorLaunch,
    as_emulator_launch,
    emulator_launch_entries,
)

FAIL = []
IS_WINDOWS = platform.system() == "Windows"


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def skip(label, why):
    print(f"SKIP  {label}  [{why}]")


# ---- the probe ----------------------------------------------------------
tmp = tempfile.mkdtemp(prefix="zxnu-imglock-")
img = os.path.join(tmp, "test.img")
with open(img, "wb") as fh:
    fh.write(b"\0" * 512)

check("a free image probes OK", probe_image_write_access(img) == IMAGE_WRITE_OK,
      probe_image_write_access(img))
check("a path that does not exist probes MISSING",
      probe_image_write_access(img + ".nope") == IMAGE_WRITE_MISSING)
check("an empty path probes MISSING",
      probe_image_write_access("") == IMAGE_WRITE_MISSING
      and probe_image_write_access(None) == IMAGE_WRITE_MISSING)
# The combo hands over quoted, forward-slashed paths; the probe must clean
# them the same way everything else does or it reports MISSING on a real file.
check("a quoted / forward-slashed path is normalised first",
      probe_image_write_access('"' + img.replace("\\", "/") + '"')
      == IMAGE_WRITE_OK)
# Nothing may be written: this runs against the user's real disk images.
check("probing does not modify the file", os.path.getsize(img) == 512)

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
    FILE_SHARE_READ, FILE_SHARE_WRITE = 0x00000001, 0x00000002
    OPEN_EXISTING, INVALID = 3, wintypes.HANDLE(-1).value
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.LPVOID,
                                 wintypes.DWORD, wintypes.DWORD,
                                 wintypes.HANDLE]

    def hold(share, access=GENERIC_READ | GENERIC_WRITE):
        handle = _k32.CreateFileW(img, access, share, None, OPEN_EXISTING, 0,
                                  None)
        return None if handle == INVALID else handle

    handle = hold(FILE_SHARE_READ)                 # how MAME holds -hard1
    check("a MAME-style holder (share=READ) reads as BUSY",
          handle is not None and probe_image_write_access(img) == IMAGE_WRITE_BUSY)
    if handle:
        _k32.CloseHandle(handle)

    handle = hold(FILE_SHARE_READ | FILE_SHARE_WRITE, GENERIC_READ)
    # THE regression tripwire: a plain open(path, "r+b") succeeds here and
    # would report OK, which is exactly how CSpect used to slip through.
    check("a CSpect-style holder (share=READ|WRITE) reads as BUSY",
          handle is not None and probe_image_write_access(img) == IMAGE_WRITE_BUSY)
    if handle:
        _k32.CloseHandle(handle)

    check("the verdict clears once the holder lets go",
          probe_image_write_access(img) == IMAGE_WRITE_OK)

    os.chmod(img, stat.S_IREAD)
    # DENIED, never BUSY: a read-only image is a different failure with its
    # own message, and "already in use" would be a grey-out with no way out.
    check("a read-only image reads as DENIED, not BUSY",
          probe_image_write_access(img) == IMAGE_WRITE_DENIED,
          probe_image_write_access(img))
    os.chmod(img, stat.S_IWRITE)
else:
    skip("holder cases", "POSIX has no mandatory locking; the probe cannot see"
                         " a running emulator at all")

os.remove(img)
os.rmdir(tmp)


# ---- the launch-entry contract ------------------------------------------
def _launch():
    return "launched"


check("as_emulator_launch accepts a bare 2-tuple (the old shape)",
      as_emulator_launch(("Mame", _launch))
      == EmulatorLaunch("Mame", _launch, ""))
check("as_emulator_launch passes a 3-tuple through",
      as_emulator_launch(("Mame", _launch, "why")).blocked == "why")
check("as_emulator_launch is idempotent on an EmulatorLaunch",
      as_emulator_launch(EmulatorLaunch("CSpect", _launch, "x")).name == "CSpect")
check("blocked defaults to empty, i.e. launchable",
      EmulatorLaunch("Mame", _launch).blocked == "")


class FakeHost:
    """The three attributes emulator_launch_entries actually reads."""

    def __init__(self, blocker=None):
        self._launch_mame_fn = _launch
        self._launch_cspect_fn = _launch
        self._cspect_executable_path = "/somewhere/CSpect.exe"
        if blocker is not None:
            self._emulator_launch_blocker = blocker

    def _mame_usable(self):
        return True


entries = emulator_launch_entries(FakeHost())
check("both emulators are listed when installed", len(entries) == 2,
      str(entries))
check("with no blocker at all, nothing is blocked",
      all(e.blocked == "" for e in entries))

# The strip label is "Mame" but the blocker's contract spells it "MAME";
# passing the wrong spelling silently blocks nothing, so pin the mapping.
seen = []


def _blocker(emulator, autostart=False):
    seen.append(emulator)
    return "busy!" if emulator == "MAME" else ""


entries = emulator_launch_entries(FakeHost(_blocker))
check("the blocker is asked with its own spelling (MAME, not Mame)",
      "MAME" in seen and "CSpect" in seen, str(seen))
by_name = {e.name: e for e in entries}
check("a blocked emulator is still LISTED, just with a reason",
      set(by_name) == {"Mame", "CSpect"})
check("the blocker's reason reaches the entry",
      by_name["Mame"].blocked == "busy!" and by_name["CSpect"].blocked == "")


def _raising_blocker(emulator, autostart=False):
    raise RuntimeError("boom")


# The blocker logs the traceback it swallowed, which is right in the app and
# just noise here - this exception is the point of the check.
logging.disable(logging.ERROR)
entries = emulator_launch_entries(FakeHost(_raising_blocker))
logging.disable(logging.NOTSET)
# A strip that vanished because a gate raised would look like "your emulators
# are gone", which is a worse failure than the one being reported.
check("a blocker that raises degrades to 'not blocked'",
      len(entries) == 2 and all(e.blocked == "" for e in entries))


# ---- the message is translated, and keeps its placeholder ----------------
NEW_STRINGS = (".img file {path} already in use.",)
missing = [(lg, s) for s in NEW_STRINGS for lg in CATALOGS
           if s not in CATALOGS[lg]]
check("the busy message is in every catalog", not missing, str(missing[:3]))

broken = []
for s in NEW_STRINGS:
    for lg in CATALOGS:
        translated = CATALOGS[lg].get(s) or s
        try:
            translated.format(path="C:/temp/next.img")
        except (KeyError, IndexError):
            broken.append((lg, s))
        # Dropping {path} would lose the one thing the message exists to say:
        # WHICH image is busy.
        if (set(re.findall(r"\{(\w+)\}", translated))
                != set(re.findall(r"\{(\w+)\}", s))):
            broken.append((lg, s + "  [placeholder set differs]"))
check("every translation renders and names the file", not broken,
      str(broken[:3]))

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all image-lock checks passed")
sys.exit(0)
