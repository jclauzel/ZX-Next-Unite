"""Emulator auto-start across the NextSync tab.

The SD Card tab's "Start <emulator> with <file>" actions are mirrored on all
three NextSync explorers, so the same entries appear everywhere a bootable file
can be seen:

  * Classic Sync, local explorer      -> launch the local path as-is
  * Remote Explorer, local pane       -> launch the local path as-is
  * Remote Explorer, Next pane        -> DOWNLOAD first, then launch the copy

All five explorers (these three plus the SD Card tab's two) build their entries
from one helper, zxnu_workers.emulator_autostart_entries, so an emulator cannot
end up offered in one pane and missing in another. This file tests that helper
behaviourally and pins the three NextSync call sites.

The Next pane's download-then-launch path is driven end-to-end against a real
widget in test_remote_explorer_widget.py::test_emulator_start_from_next.

Run with: python tests/test_nextsync_autostart.py
"""
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

import zxnu_workers  # noqa: E402
from zxnu_workers import emulator_autostart_entries  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


class Host:
    """A stand-in MainWindow exposing only what the helper reads."""

    def __init__(self, cspect=True, mame=True, flatpak=False):
        self._cspect_executable_path = "/opt/CSpect/CSpect.exe" if cspect else None
        self._launch_cspect_fn = lambda p: ("cspect", p)
        self._launch_mame_fn = lambda p: ("mame", p)
        self._mame = mame
        self._flatpak = flatpak

    def _mame_usable(self):
        return self._mame

    def _mame_flatpak_enabled(self):
        return self._flatpak


def names(host, path, is_dir=False):
    return [e.name for e in emulator_autostart_entries(host, path, is_dir)]


# ---- which emulators are offered -----------------------------------------
both = Host()
check("a .nex offers both emulators when both are available",
      names(both, "/games/beast.nex") == ["CSpect", "MAME"])
check("the order is stable (CSpect, then MAME) in every explorer",
      names(both, "/a.nex") == names(both, "C:\\b.nex") == ["CSpect", "MAME"])
check("no CSpect installed -> only MAME",
      names(Host(cspect=False), "/a.nex") == ["MAME"])
check("no usable MAME -> only CSpect",
      names(Host(mame=False), "/a.nex") == ["CSpect"])
check("neither available -> no entries at all",
      names(Host(cspect=False, mame=False), "/a.nex") == [])
check("a directory is never offered", names(both, "/games", True) == [])
check("a readme is never offered", names(both, "/docs/readme.txt") == [])
check("empty / None are safe",
      names(both, "") == [] and names(both, None) == [])
# The two emulators genuinely take different media, and the helper must respect
# that rather than assuming what one can boot the other can too.
check("a .dsk is offered for CSpect only (MAME's Next drivers take no disks)",
      names(both, "/games/disk.dsk") == ["CSpect"])
check("a .scr is offered for MAME only (CSpect does not boot screens)",
      names(both, "/loading.scr") == ["MAME"])
check("a .tap is offered for both", names(both, "/tape.tap") == ["CSpect", "MAME"])

# ---- the entries are usable as menu items --------------------------------
entries = emulator_autostart_entries(both, "/games/beast.nex")
check("each entry carries a ready-to-use label naming the file",
      all("beast.nex" in e.label for e in entries),
      str([e.label for e in entries]))
check("the label names its emulator",
      entries[0].label.startswith("Start CSpect")
      and entries[1].label.startswith("Start MAME"),
      str([e.label for e in entries]))
check("launching passes the host path straight through",
      entries[0].launch("/tmp/x.nex") == ("cspect", "/tmp/x.nex"))
check("a Windows path is labelled by base name, not the whole path",
      "C:\\" not in emulator_autostart_entries(
          both, "C:\\games\\beast.nex")[0].label)

# ---- staging directories --------------------------------------------------
# Only callers that must FETCH the file first use these (the SD image and the
# Next). They differ per emulator because Flatpak MAME cannot see our /tmp.
sysroot = os.path.realpath(tempfile.gettempdir())
cspect_dir = entries[0].staging_dir()
check("CSpect stages into a fresh temp directory",
      os.path.isdir(cspect_dir)
      and os.path.realpath(cspect_dir).startswith(sysroot), cspect_dir)
mame_dir = entries[1].staging_dir()
check("MAME stages into a fresh temp directory when not on Flatpak",
      os.path.isdir(mame_dir)
      and os.path.realpath(mame_dir).startswith(sysroot), mame_dir)
check("each call gets its own directory (two launches cannot collide)",
      entries[0].staging_dir() != cspect_dir)
shutil.rmtree(cspect_dir, ignore_errors=True)
shutil.rmtree(mame_dir, ignore_errors=True)

# Flatpak MAME: a fixed directory under the user's home, cleared each time.
fake_home = tempfile.mkdtemp(prefix="zxnu-fakehome-")
staged = os.path.join(fake_home, ".cache", "zx-next-unite", "mame-autostart")
_real = zxnu_workers.mame_autostart_staging_dir
zxnu_workers.mame_autostart_staging_dir = lambda: staged
try:
    fp = emulator_autostart_entries(Host(flatpak=True), "/a.nex")
    got = fp[-1].staging_dir()
    # The property that distinguishes it from the mkdtemp path: one fixed
    # directory, not a fresh one per launch. (That this location is outside
    # /tmp is a property of mame_autostart_staging_dir itself and is tested
    # against the real function in test_mame_autostart.py — the fake home
    # used here necessarily lives under the temp dir.)
    check("Flatpak MAME does NOT get a fresh per-launch temp directory",
          fp[-1].name == "MAME"
          and os.path.realpath(fp[-1].staging_dir()) == os.path.realpath(got))
    check("...it uses the home staging directory",
          os.path.realpath(got) == os.path.realpath(staged), got)
    # Nothing under ~ is reaped by the OS, so the directory is reused and
    # emptied rather than accumulating one copy per launch.
    open(os.path.join(got, "stale.nex"), "wb").write(b"x")
    again = fp[-1].staging_dir()
    check("...and is cleared before each use, so copies do not pile up",
          os.path.realpath(again) == os.path.realpath(staged)
          and os.listdir(again) == [], str(os.listdir(again)))
    check("Flatpak MAME staging is reached through the shared helper only",
          "mame_autostart_staging_dir" in open(
              os.path.join(REPO, "zxnu_workers.py"), encoding="utf-8").read())
finally:
    zxnu_workers.mame_autostart_staging_dir = _real
    shutil.rmtree(fake_home, ignore_errors=True)

# ---- the three NextSync call sites ---------------------------------------
ops = open(os.path.join(REPO, "zxnu_nextsync_ops.py"), encoding="utf-8").read()
check("the classic explorer builds its entries from the shared helper",
      "emulator_autostart_entries(host, file_path, is_dir)" in ops)
check("the classic explorer launches the local path directly (no fetch)",
      "_e.launch(file_path)" in ops,
      "these files are already on the PC")
# Top of the menu, as on the SD Card tab.
emu_at = ops.find("emulator_autostart_entries(host, file_path, is_dir)")
first_at = ops.find("menu.addAction(action_copy_text)")
check("the classic explorer's entries sit at the top of its menu",
      emu_at != -1 and first_at != -1 and emu_at < first_at,
      f"emu={emu_at} first={first_at}")

rex = open(os.path.join(REPO, "zxnu_remote_explorer.py"), encoding="utf-8").read()
check("the Remote Explorer takes the emulators as an injected hook",
      "emulator_entries=None" in rex,
      "the widget must not import the emulator ops layer")
check("...and offers nothing when the hook is absent",
      "self._emulator_entries = emulator_entries or (lambda path: [])" in rex)
check("the local pane launches its path directly (no fetch)",
      "e.launch(p)" in rex)
check("the Next pane routes through the download-first path",
      "self._emulator_start_from_next(_sel_now[0][0], entry)" in rex)
seg = rex[rex.find("def _emulator_start_from_next"):]
seg = seg[:seg.find("def _remote_unzip")]
check("the Next pane downloads with the same 'get' op as Download",
      '("get", remote_path, dest)' in seg)
check("...into the LOCAL pane's folder, where the user can see and keep it",
      "dest = self._local_dir()" in seg,
      "a temp dir made the file vanish; Download puts it here")
check("...falling back to a scratch dir only if that folder is unwritable",
      "entry.staging_dir()" in seg and "os.access(dest, os.W_OK)" in seg)
check("...and launches the DOWNLOADED copy, never the Next path",
      "entry.launch(local)" in seg and "entry.launch(remote_path)" not in seg)
check("a failed download starts nothing and says so",
      "could not be downloaded from " in seg)
# The destination is normally the user's own browsing folder, so the failure
# path must never rmtree it — only a scratch dir this code created itself.
check("a failed download NEVER deletes the user's local folder",
      "if scratch:" in seg
      and seg.count("shutil.rmtree(dest, ignore_errors=True)") == 1
      and seg.index("if scratch:") < seg.index("shutil.rmtree(dest"),
      "rmtree must be reachable only for a directory we made")
# Booting a file needs no mounted image (MAME: no -hard1; CSpect: -mmc=<dir>),
# so the pre-flight check must not turn a missing SD card into a red toast.
check("a missing SD card does not block booting a file",
      "autostart=True" in open(
          os.path.join(REPO, "zxnu_workers.py"), encoding="utf-8").read())
# Both panes act on exactly one selected FILE — a folder or a multi-selection
# has no single file to boot.
check("the Next pane only offers the entries for a single selected file",
      "len(_sel_now) == 1 and not _sel_now[0][1]" in rex)
check("the local pane only offers the entries for a single selected file",
      "len(sel) == 1 and os.path.isfile(sel[0])" in rex)

pane = open(os.path.join(REPO, "zxnu_nextsync_pane.py"), encoding="utf-8").read()
check("the Remote Explorer is actually given the hook at construction",
      "emulator_entries=lambda path: emulator_autostart_entries(host, path)" in pane,
      "without this the panes silently offer nothing")

# ---- every new string is translated --------------------------------------
from zxnu_i18n import CATALOGS  # noqa: E402

NEW_STRINGS = (
    "Could not start {emulator}",
    "Could not prepare a folder for {name}: {error}",
    "Start {emulator}: {name} could not be downloaded from the Next, "
    "{emulator} was not started.",
    "Downloading {name} from the Next, then starting {emulator}…",
    "Downloading {name}…",
)
missing = [(lg, s) for s in NEW_STRINGS for lg in CATALOGS if not CATALOGS[lg].get(s)]
check("every new string is translated in all languages",
      not missing, f"{len(missing)} gap(s): {missing[:2]}")
sample = {"name": "beast.nex", "emulator": "MAME", "error": "denied"}
broken = []
for s in NEW_STRINGS:
    for lg in CATALOGS:
        t = CATALOGS[lg].get(s) or s
        try:
            t.format(**sample)
        except (KeyError, IndexError):
            broken.append((lg, s))
check("every translation renders with its placeholders", not broken, str(broken[:2]))
# The emulator name is interpolated, never translated — "MAME" must survive.
check("the emulator name is never translated away",
      all("MAME" in (CATALOGS[lg].get(NEW_STRINGS[2]) or "").format(**sample)
          for lg in CATALOGS))

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all NextSync emulator auto-start checks passed")
sys.exit(0)
