"""hdfmonkey downloads-folder discovery tests (zxnu_config).

Covers the lenient scan added for the Flatpak/manual-install cases:
  - the canonical downloads/hdfmonkey/<platform>/<exe> layout,
  - a bare binary dropped straight into downloads/hdfmonkey/,
  - a hand-extracted archive whose per-platform folder sits deeper down,
  - NOT matching a loose binary under another platform's folder name,
  - the exec bit being restored on POSIX,
  - the Flatpak stray root ~/.var/app/<id>/downloads (binary and jjjs zip),
    which is only consulted when FLATPAK_ID is set.

Run with: python tests/test_hdfmonkey_discovery.py
"""
import os
import shutil
import sys
import tempfile
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from zxnu_config import (  # noqa: E402
    DOWNLOADS_HDFMONKEY_DIRNAME,
    find_hdfmonkey_in_downloads,
    find_hdfmonkey_jjjs_zip_in_downloads,
    flatpak_stray_download_root,
    hdfmonkey_platform_dirs,
)

FAIL = []
def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


PLAT_DIR, EXE = hdfmonkey_platform_dirs()[0]
# A per-platform folder name that is never valid for the CURRENT platform.
FOREIGN_DIR = "linux-musl" if PLAT_DIR != "linux-musl" else "macos-intel"

BASE = tempfile.mkdtemp(prefix="zxnu-hdfm-test-")

def make_base(name, rel_binary_path):
    """Create <BASE>/<name>/downloads/hdfmonkey/<rel_binary_path> and return
    the base dir (what find_hdfmonkey_in_downloads takes)."""
    base = os.path.join(BASE, name)
    path = os.path.join(base, DOWNLOADS_HDFMONKEY_DIRNAME, rel_binary_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"#!fake hdfmonkey\n")
    return base

# 1. Canonical layout (what the auto-download produces).
b = make_base("canonical", os.path.join(PLAT_DIR, EXE))
found = find_hdfmonkey_in_downloads(b)
check("canonical <plat>/<exe> layout is found",
      found is not None and found.endswith(os.path.join(PLAT_DIR, EXE)),
      repr(found))

# 2. Bare binary dropped straight into downloads/hdfmonkey/.
b = make_base("flat", EXE)
found = find_hdfmonkey_in_downloads(b)
check("bare binary in downloads/hdfmonkey/ is found",
      found is not None and os.path.basename(found) == EXE, repr(found))

# 3. Hand-extracted archive: platform folder at a deeper level.
b = make_base("nested", os.path.join("hdfmonkey-2jjjs", PLAT_DIR, EXE))
found = find_hdfmonkey_in_downloads(b)
check("hand-extracted nested <plat>/<exe> is found",
      found is not None and found.endswith(os.path.join(PLAT_DIR, EXE)),
      repr(found))

# 4. A binary under ANOTHER platform's folder must not be trusted (the bare
#    file name is shared across the Linux and macOS builds).
b = make_base("foreign", os.path.join("hdfmonkey-2jjjs", FOREIGN_DIR, EXE))
found = find_hdfmonkey_in_downloads(b)
check("foreign platform folder is not matched", found is None, repr(found))

# 5. POSIX: the exec bit lost by zip extraction is restored on adoption.
if sys.platform != "win32":
    b = make_base("execbit", os.path.join(PLAT_DIR, EXE))
    path = os.path.join(b, DOWNLOADS_HDFMONKEY_DIRNAME, PLAT_DIR, EXE)
    os.chmod(path, 0o644)
    found = find_hdfmonkey_in_downloads(b)
    check("exec bit is restored on POSIX",
          found is not None and os.access(found, os.X_OK), repr(found))

# 6. Flatpak stray root: with FLATPAK_ID set, ~/.var/app/<id>/downloads is
#    scanned too (both the binary and the jjjs-zip scans); without it, not.
fake_home = os.path.join(BASE, "home")
app_id = "io.test.ZXNU"
stray_base = os.path.join(fake_home, ".var", "app", app_id)
stray_bin = os.path.join(stray_base, DOWNLOADS_HDFMONKEY_DIRNAME, PLAT_DIR, EXE)
os.makedirs(os.path.dirname(stray_bin), exist_ok=True)
with open(stray_bin, "wb") as fh:
    fh.write(b"#!fake hdfmonkey\n")

# A minimal outer jjjs zip: nested inner zip + password.txt.
stray_downloads = os.path.join(stray_base, "downloads")
inner = os.path.join(BASE, "inner.zip")
with zipfile.ZipFile(inner, "w") as zf:
    zf.writestr("dummy.txt", "x")
with zipfile.ZipFile(os.path.join(stray_downloads, "hdfmonkey-2jjjs.zip"), "w") as zf:
    zf.write(inner, "hdfmonkey-2jjjs.zip")
    zf.writestr("password.txt", "jjjs")

empty_base = os.path.join(BASE, "empty"); os.makedirs(empty_base)
_saved = {k: os.environ.get(k) for k in ("FLATPAK_ID", "HOME", "USERPROFILE")}
try:
    os.environ["FLATPAK_ID"] = app_id
    os.environ["HOME"] = fake_home            # POSIX expanduser
    os.environ["USERPROFILE"] = fake_home     # Windows expanduser
    check("flatpak_stray_download_root points into ~/.var/app/<id>",
          flatpak_stray_download_root() == stray_base,
          repr(flatpak_stray_download_root()))
    found = find_hdfmonkey_in_downloads(empty_base)
    check("stray ~/.var/app/<id>/downloads binary is found when sandboxed",
          found is not None and os.path.normcase(found) == os.path.normcase(stray_bin),
          repr(found))
    found_zip = find_hdfmonkey_jjjs_zip_in_downloads(empty_base)
    check("stray ~/.var/app/<id>/downloads jjjs zip is found when sandboxed",
          found_zip is not None
          and os.path.normcase(os.path.dirname(found_zip)) == os.path.normcase(stray_downloads),
          repr(found_zip))
finally:
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

check("stray root is ignored when FLATPAK_ID is unset",
      flatpak_stray_download_root() is None
      and find_hdfmonkey_in_downloads(empty_base) is None)

shutil.rmtree(BASE, ignore_errors=True)

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: {FAIL}")
    sys.exit(1)
print("all hdfmonkey discovery tests passed")
