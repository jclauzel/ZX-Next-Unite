"""Unit tests for the OpenAL 1.1 detection + guided-install plumbing.

CSpect needs the OpenAL runtime for sound on Windows, so after an itch.io
CSpect install zxnu_main checks ``is_openal_installed()`` and (only when the
runtime is genuinely missing) offers the guided oalinst.exe install built in
zxnu_emulator_ops. Covered here, without Qt and without touching the real
machine state:

  * is_openal_installed(): non-Windows short-circuit, the privilege-free
    System32/SysWOW64 OpenAL32.dll file check (via the windir override), and
    the Add/Remove Programs fallback (stubbed — the real registry scan is
    only smoke-tested for "returns a bool, never raises");
  * extract_oalinst_from_zip(): root and nested/uppercase members, directory
    entries skipped, RuntimeError when the installer is absent;
  * source-level wiring tripwires: the offer closure is exposed on the host
    and the detection toast actually consults the detector.
"""
import os
import sys
import tempfile
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import zxnu_config
from zxnu_config import (DOWNLOADS_OPENAL_DIRNAME, OPENAL_DOWNLOAD_URL,
                         OPENAL_INSTALLER_EXE_FILENAME,
                         OPENAL_INSTALLER_ZIP_FILENAME, OPENAL_WEBSITE_URL,
                         extract_oalinst_from_zip, is_openal_installed)

ok = True


def check(label, cond, detail=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'} {label}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        ok = False


class _FakePlatform:
    """Stands in for zxnu_config.platform so the detection runs the Windows
    code path on any CI host (and the non-Windows path on Windows)."""
    def __init__(self, name):
        self._name = name

    def system(self):
        return self._name

    def machine(self):
        return "AMD64"


def test_constants():
    print("== constants ==")
    check("download URL is the official oalinst.zip",
          OPENAL_DOWNLOAD_URL == "https://www.openal.org/downloads/oalinst.zip")
    check("website URL is openal.org",
          OPENAL_WEBSITE_URL.startswith("https://www.openal.org"))
    check("zip / exe file names match the official installer",
          OPENAL_INSTALLER_ZIP_FILENAME == "oalinst.zip"
          and OPENAL_INSTALLER_EXE_FILENAME == "oalinst.exe")
    check("the install dir lives under the downloads tree",
          DOWNLOADS_OPENAL_DIRNAME == os.path.join("downloads", "openal"))


def test_detection():
    print("\n== is_openal_installed ==")
    real_platform = zxnu_config.platform
    real_registry = zxnu_config._openal_in_add_remove_programs
    try:
        # Non-Windows: always True — Linux/macOS get OpenAL from the system,
        # there is nothing for the app to install.
        zxnu_config.platform = _FakePlatform("Linux")
        check("non-Windows always reports installed", is_openal_installed())

        zxnu_config.platform = _FakePlatform("Windows")
        with tempfile.TemporaryDirectory() as tmp:
            # No DLL anywhere and no registry entry -> missing.
            zxnu_config._openal_in_add_remove_programs = lambda: False
            check("missing DLL + no registry entry -> not installed",
                  not is_openal_installed(windir=tmp))

            # The registry fallback alone is enough (DLL parked elsewhere).
            zxnu_config._openal_in_add_remove_programs = lambda: True
            check("Add/Remove Programs entry alone counts",
                  is_openal_installed(windir=tmp))

            # The System32 router DLL short-circuits before the registry.
            zxnu_config._openal_in_add_remove_programs = lambda: (_ for _ in ()).throw(
                AssertionError("registry must not be consulted"))
            os.makedirs(os.path.join(tmp, "System32"), exist_ok=True)
            with open(os.path.join(tmp, "System32", "OpenAL32.dll"), "wb") as f:
                f.write(b"MZ")
            check("System32/OpenAL32.dll counts (no registry consulted)",
                  is_openal_installed(windir=tmp))

        with tempfile.TemporaryDirectory() as tmp:
            # 32-bit runtime only (SysWOW64) counts too.
            zxnu_config._openal_in_add_remove_programs = lambda: False
            os.makedirs(os.path.join(tmp, "SysWOW64"), exist_ok=True)
            with open(os.path.join(tmp, "SysWOW64", "OpenAL32.dll"), "wb") as f:
                f.write(b"MZ")
            check("SysWOW64/OpenAL32.dll counts", is_openal_installed(windir=tmp))

        # A blown-up registry scan degrades to "not installed", never raises.
        with tempfile.TemporaryDirectory() as tmp:
            zxnu_config._openal_in_add_remove_programs = lambda: (_ for _ in ()).throw(
                OSError("registry unavailable"))
            check("registry scan failure degrades to False, no exception",
                  is_openal_installed(windir=tmp) is False)
    finally:
        zxnu_config.platform = real_platform
        zxnu_config._openal_in_add_remove_programs = real_registry

    # The real registry helper must be callable as a plain user and return a
    # bool (True/False depends on the host, so only the contract is checked).
    result = zxnu_config._openal_in_add_remove_programs()
    check("the real Add/Remove Programs scan returns a bool without raising",
          isinstance(result, bool), repr(result))


def _make_zip(path, members):
    """members: {archive_name: bytes | None}; None writes a directory entry."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in members.items():
            if payload is None:
                zf.writestr(zipfile.ZipInfo(name), b"")
            else:
                zf.writestr(name, payload)


def test_extract():
    print("\n== extract_oalinst_from_zip ==")
    payload = b"MZ-fake-openal-installer"
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "oalinst.zip")
        dest_root = os.path.join(tmp, "downloads", "openal")

        # The official layout: oalinst.exe at the archive root.
        _make_zip(zip_path, {"oalinst.exe": payload})
        out = extract_oalinst_from_zip(zip_path, dest_root)
        check("root member extracts to <dest>/oalinst.exe",
              out == os.path.join(dest_root, "oalinst.exe")
              and open(out, "rb").read() == payload, out)

        # A re-pack under a sub-folder with different case still works, and
        # the result is written flat (no sub-folder reproduced).
        _make_zip(zip_path, {"redist/OALINST.EXE": payload})
        out = extract_oalinst_from_zip(zip_path, dest_root)
        check("nested / uppercase member is found and flattened",
              out == os.path.join(dest_root, "oalinst.exe")
              and open(out, "rb").read() == payload, out)

        # Directory entries must not satisfy the search.
        _make_zip(zip_path, {"oalinst.exe/": None, "readme.txt": b"hi"})
        try:
            extract_oalinst_from_zip(zip_path, dest_root)
            check("a directory entry named oalinst.exe/ is rejected", False)
        except RuntimeError:
            check("a directory entry named oalinst.exe/ is rejected", True)

        # No installer at all -> RuntimeError for the caller to report.
        _make_zip(zip_path, {"readme.txt": b"hi"})
        try:
            extract_oalinst_from_zip(zip_path, dest_root)
            check("an archive without oalinst.exe raises RuntimeError", False)
        except RuntimeError:
            check("an archive without oalinst.exe raises RuntimeError", True)

        # A corrupt file propagates the zipfile error (caller logs it).
        with open(zip_path, "wb") as f:
            f.write(b"not a zip")
        try:
            extract_oalinst_from_zip(zip_path, dest_root)
            check("a non-zip propagates the zipfile error", False)
        except zipfile.BadZipFile:
            check("a non-zip propagates the zipfile error", True)


def test_wiring():
    """Source-level tripwires: the offer chain stays exposed on the host and
    the detection toast actually consults the detector — the two seams the
    feature hangs on (the full UI path is exercised by the offscreen suite)."""
    print("\n== wiring ==")
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    ops_src = open(os.path.join(repo, "zxnu_emulator_ops.py"),
                   encoding="utf-8").read()
    main_src = open(os.path.join(repo, "zxnu_main.py"), encoding="utf-8").read()
    check("build_emulator_ops exposes host._offer_openal_install",
          "host._offer_openal_install = _offer_openal_install" in ops_src)
    check("the install chain downloads from OPENAL_DOWNLOAD_URL",
          "OPENAL_DOWNLOAD_URL" in ops_src)
    check("the detection toast consults is_openal_installed()",
          "is_openal_installed()" in main_src)
    check("the missing-OpenAL toast still offers the guided install",
          "_offer_openal_install" in main_src)

    # The standalone hdfmonkey flow carries OpenAL too: the reveal function
    # relabels the button when the runtime is missing, and a successful
    # install chains into the same offer.
    check("the install button label announces OpenAL when it is missing",
          'ui_tr_now("Download and install HDF Monkey and OpenAL")' in ops_src
          and 'ui_tr_now("Download and install HDF Monkey")' in ops_src)
    check("a finished hdfmonkey install chains into the OpenAL offer",
          "QTimer.singleShot(400, host._offer_openal_install)" in ops_src)

    # Both button labels must exist in every catalog so the runtime setText
    # (which goes through ui_tr_now) can translate them.
    from zxnu_i18n import CATALOGS
    labels = ("Download and install HDF Monkey",
              "Download and install HDF Monkey and OpenAL")
    missing = [f"{code}: {label!r}" for code, cat in sorted(CATALOGS.items())
               for label in labels if label not in cat]
    check("both install-button labels are translated in every language",
          not missing, "; ".join(missing[:4]))

    # The offer dialog is the user's consent point for installing THIRD-PARTY
    # software, so it must credit OpenAL and carry a clickable openal.org
    # link: rich text, and the {url} placeholder (the call site formats in
    # the <a href> anchor) must survive every translation.
    check("the offer dialog uses rich text with a clickable openal.org link",
          "box.setTextFormat(Qt.RichText)" in ops_src
          and '<a href="https://www.openal.org/">' in ops_src)
    offer_body = ("On Windows CSpect needs the <b>OpenAL 1.1</b> audio "
                  "library for sound, and it was not detected on this "
                  "machine — without it CSpect runs silent.<br><br>"
                  "OpenAL is separate, third-party software — many thanks "
                  "to its authors: {url}<br><br>"
                  "Download the official installer (oalinst.exe) from "
                  "openal.org and run it now?<br><br>"
                  "Windows will ask for administrator approval when the "
                  "installer starts — the app itself never runs elevated.")
    check("the offer dialog credits OpenAL as third-party software",
          '"OpenAL is separate, third-party software — many thanks to its "'
          in ops_src)
    bad = [f"{code}: missing" for code, cat in sorted(CATALOGS.items())
           if offer_body not in cat]
    bad += [f"{code}: {{url}} lost" for code, cat in sorted(CATALOGS.items())
            if offer_body in cat and "{url}" not in cat[offer_body]]
    check("the third-party credit body is translated everywhere, {url} intact",
          not bad, "; ".join(bad[:4]))

    # The failure box's manual-install fallback also renders {url} clickable.
    fail_body = ("The OpenAL download failed — see the log for details. "
                 "You can install it manually from {url}")
    bad = [code for code, cat in sorted(CATALOGS.items())
           if fail_body not in cat or "{url}" not in cat[fail_body]]
    check("the download-failed body keeps {url} in every language",
          not bad, str(bad))


def main():
    test_constants()
    test_detection()
    test_extract()
    test_wiring()
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
