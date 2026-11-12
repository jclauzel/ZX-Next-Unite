"""Create a NextZXOS SD card image from the official System/Next distribution,
and rebuild an image MAME cannot boot (9.7.65).

Two actions on the SD Card tab, both needing hdfmonkey:

* **Create NextZXOS Image** (the button beside "Download NextZXOS Image"):
  asks for a size (2, 4 or 8 GB) and a file name, reads the latest
  distribution page (``SPECNEXT_LATEST_DISTRO_URL``), downloads the
  distribution zip it names (``sn-nosrc-<version>.zip`` by preference - the
  full card without the ``src/`` tree; the lite or complete one when that is
  what the page lists), checks it against the SHA-1 the page prints, unpacks
  it, ``hdfmonkey create --fat32`` the image and ``putdir`` the tree into it,
  then loads the new image. The zip is cached under ``downloads/nextzxos/``
  so a second image of another size downloads nothing.

* **Make this image MAME-bootable** (the image explorer's menu, and a
  load-time offer): the fix for the 26.11a "image for emulators", which is
  FAT32 with only 32,758 clusters - below the 65,525 the FAT specification
  requires for FAT32 - so the Next firmware's card driver types it by cluster
  count, reads it as FAT16 and stops at ``Error opening 'menu.ini/.def'``
  under MAME, while hdfmonkey and CSpect (which never runs that firmware)
  read it fine. The rebuild lists the image with hdfmonkey, ``get``s every
  file into a scratch tree, creates a fresh FAT32 image of the chosen size
  and ``putdir``s the tree back. Measured on MAME 0.289: every FAT32 volume
  hdfmonkey creates at 2 GB and up boots the 26.11a firmware.

**Why ``--fat32`` is always passed.** hdfmonkey's own choice of FAT type
depends on the exact size: at ``2G`` it happens to pick FAT32 (131,005
clusters, measured 2026-11-12 with v0.5.7 jjjs), but a size just under
2 GiB such as ``2147450000B`` gets a FAT16 of exactly 65,525 clusters - the
one count a cluster-counting driver calls FAT32, the mirror image of the bug
this fixes. Passing the flag takes the type out of hdfmonkey's hands. FAT32
at 2 GB gives 131,005 clusters of 16 KB; 4 and 8 GB more.

Qt-free: every side effect is injected into ``build_nextzxos_image_ops``
(the dialogs, the progress-dialog runner, the toasts, the log, the loader),
so ``tests/test_nextzxos_image.py`` drives the whole thing headlessly with
fakes, as ``zxnu_sync5_img`` is tested. The Qt glue lives in
``zxnu_sdcard_ops.build_sdcard_utils`` beside the download wizard.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from collections import namedtuple

from zxnu_config import is_filetype_a_directory, read_image_fat_usage
from zxnu_i18n import ui_tr_now

# The page that always carries the newest distribution (its own words:
# "This page is where the latest distribution is always found").
SPECNEXT_LATEST_DISTRO_URL = "https://www.specnext.com/latestdistro/"

# Which distribution zip to put on the image, in order of preference. The
# "nosrc" one is the complete card without the sources of the dot commands
# (useless on an SD card); "lite" drops demos and docs; "complete" has it all.
NEXTZXOS_DISTRO_FLAVORS = ("sn-nosrc", "sn-lite", "sn-complete")

# (label, hdfmonkey size token, bytes). 1 GB is deliberately absent: it is
# the size whose FAT32 falls under the cluster floor, and hdfmonkey's FAT16
# for it sits at 65,517 clusters - fine for MAME, but a card nobody asked for.
NEXTZXOS_IMAGE_SIZE_CHOICES = (
    ("2 GB", "2G", 2 * 1024 ** 3),
    ("4 GB", "4G", 4 * 1024 ** 3),
    ("8 GB", "8G", 8 * 1024 ** 3),
)
NEXTZXOS_IMAGE_DEFAULT_SIZE = "2 GB"

# A FAT32 volume with fewer clusters than this is not FAT32 to a driver that
# types volumes by cluster count (Microsoft's rule, which the Next firmware
# follows): it reads it as FAT16, and nothing on it can be found.
FAT32_MIN_CLUSTERS = 65525

# The distribution zip cache, under the data root's downloads folder.
NEXTZXOS_CACHE_DIRNAME = "nextzxos"

# How full the new image may be with the copied files: a card is for
# adding to, and FAT needs slack for its directories.
IMAGE_FILL_LIMIT = 0.9

DistroDownload = namedtuple("DistroDownload", "name version url sha1 md5")

_HREF_RE = re.compile(
    r'href="(?P<url>https?://[^"]+/distro/[^"/]+/'
    r'(?P<name>sn-(?P<flavor>[a-z]+)-(?P<version>[^"/]+?)\.zip))"',
    re.IGNORECASE)
_SHA1_RE = re.compile(r"SHA-?1\s*:?\s*(?:</?[^>]+>\s*)*([0-9a-fA-F]{40})")
_MD5_RE = re.compile(r"MD5\s*:?\s*(?:</?[^>]+>\s*)*([0-9a-fA-F]{32})")


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def parse_latest_distro(html, flavors=NEXTZXOS_DISTRO_FLAVORS):
    """The distribution zip the latest-distro page lists, as a
    ``DistroDownload``, or None. *flavors* is the preference order; the
    first flavor the page carries wins. The SHA-1 and MD5 are read from the
    text that follows the link (the page prints them under each download);
    a download the page gives no SHA-1 for still answers, with ``""``."""
    found = {}
    for m in _HREF_RE.finditer(html or ""):
        flavor = "sn-" + m.group("flavor").lower()
        if flavor in found:
            continue
        tail = html[m.end():m.end() + 600]
        sha1 = _SHA1_RE.search(tail)
        md5 = _MD5_RE.search(tail)
        found[flavor] = DistroDownload(
            name=m.group("name"), version=m.group("version"), url=m.group("url"),
            sha1=sha1.group(1).lower() if sha1 else "",
            md5=md5.group(1).lower() if md5 else "")
    for flavor in flavors:
        if flavor in found:
            return found[flavor]
    return None


def mame_bootable_verdict(usage):
    """What a ``FatUsage`` (``read_image_fat_usage``) says about MAME:
    ``"ok"``, ``"fat32-small"`` (FAT32 under ``FAT32_MIN_CLUSTERS``: the
    Next firmware reads it as FAT16 and boots nothing) or ``"unknown"``
    (the image could not be read)."""
    if usage is None:
        return "unknown"
    try:
        if int(usage.fat_bits) == 32 and int(usage.total_clusters) < FAT32_MIN_CLUSTERS:
            return "fat32-small"
    except (TypeError, ValueError, AttributeError):
        return "unknown"
    return "ok"


def image_bytes_in_use(usage):
    """Bytes the files on the image occupy, from its FAT usage (0 when
    unknown)."""
    if usage is None:
        return 0
    try:
        return max(0, int(usage.total_clusters) - int(usage.free_clusters)) \
            * int(usage.cluster_bytes)
    except (TypeError, ValueError, AttributeError):
        return 0


def size_choice(label):
    """The ``(label, token, bytes)`` of ``NEXTZXOS_IMAGE_SIZE_CHOICES`` for a
    label such as ``"2 GB"``, or None."""
    for choice in NEXTZXOS_IMAGE_SIZE_CHOICES:
        if choice[0] == label:
            return choice
    return None


def size_fits(usage, size_bytes):
    """Whether an image of *size_bytes* can take what *usage* says is in
    use, with ``IMAGE_FILL_LIMIT`` slack. Unknown usage fits."""
    return image_bytes_in_use(usage) <= size_bytes * IMAGE_FILL_LIMIT


def human_size(n):
    """``1.5 GB`` style, for toasts and log lines."""
    n = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return (f"{int(n)} {unit}" if unit == "bytes"
                    else f"{n:.1f} {unit}")
        n /= 1024
    return f"{n:.1f} GB"


def sha1_of_file(path, chunk=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def distro_tree_root(folder):
    """The folder that holds the card's root (``TBBLUE.FW``) inside an
    unpacked distribution: *folder* itself, or its single sub-folder when
    the zip nested everything one level down."""
    names = os.listdir(folder)
    if any(n.upper() == "TBBLUE.FW" for n in names):
        return folder
    dirs = [n for n in names if os.path.isdir(os.path.join(folder, n))]
    if len(names) == 1 and len(dirs) == 1:
        inner = os.path.join(folder, dirs[0])
        if any(n.upper() == "TBBLUE.FW" for n in os.listdir(inner)):
            return inner
    return folder


def list_image_tree(execute, image_path, root="/", status=None, cancel_event=None):
    """Every folder and file under *root* of the image, through
    ``hdfmonkey ls`` (``type<TAB>name`` lines), as ``(dirs, files)`` of
    image paths in listing order, folders before their contents. Raises
    RuntimeError when a listing fails."""
    dirs, files = [], []

    def walk(path):
        if cancel_event is not None and cancel_event.is_set():
            return
        if status is not None:
            status(f"Listing…\n{path}")
        result = execute("ls", image_path, extra_argv=[path], silent=True,
                         prompt_if_missing=False)
        if result.returncode != 0:
            raise RuntimeError(f"hdfmonkey could not list {path} in {image_path}")
        out = result.stdout
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        for line in out.splitlines():
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue
            ftype, name = parts
            child = (path.rstrip("/") + "/" + name) if path != "/" else "/" + name
            if is_filetype_a_directory(ftype):
                dirs.append(child)
                walk(child)
            else:
                files.append(child)

    walk(root)
    return dirs, files


# --------------------------------------------------------------------------- #
# Network (injectable)
# --------------------------------------------------------------------------- #
def fetch_text(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "ZX-Next-Unite"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def fetch_file(url, path, progress=None, cancel_event=None, timeout=60):
    """Download *url* to *path* in 64 KB chunks; ``progress(pct)`` when the
    server says the size. A cancel leaves no file behind."""
    request = urllib.request.Request(url, headers={"User-Agent": "ZX-Next-Unite"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        total = int(response.getheader("Content-Length") or 0)
        done = 0
        try:
            with open(path, "wb") as out:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("cancelled")
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total and progress is not None:
                        progress(int(done * 100 / total))
        except BaseException:
            try:
                os.remove(path)
            except OSError:
                pass
            raise


# --------------------------------------------------------------------------- #
# The two tasks (run off the UI thread by the glue's progress-dialog runner)
# --------------------------------------------------------------------------- #
def _check(result, what):
    if result.returncode != 0:
        err = result.stderr
        if isinstance(err, bytes):
            err = err.decode(errors="replace")
        raise RuntimeError(f"{what} failed: {(err or '').strip() or 'hdfmonkey error'}")


def _fresh_image(execute, target, size_token, blocker, status):
    """``hdfmonkey create --fat32 target size``, after the write gate and
    after removing a file of that name (the save dialog has asked)."""
    why = blocker(target) if blocker is not None else ""
    if why:
        raise RuntimeError(why)
    if os.path.lexists(target):
        os.remove(target)
    status(f"Creating the {size_token} image…\n{target}")
    _check(execute("create", target, options=["--fat32"], extra_argv=[size_token],
                   silent=True, prompt_if_missing=False), "hdfmonkey create")


def _fill_image(execute, target, tree, status):
    status(f"Copying the files into the image…\n{target}")
    _check(execute("putdir", target, extra_argv=[tree, "/"], silent=True,
                   prompt_if_missing=False), "hdfmonkey putdir")


def create_nextzxos_image_task(*, execute, target, size_token, cache_dir,
                               progress, status, cancel_event, log=None,
                               blocker=None, fetch_text=fetch_text,
                               fetch_file=fetch_file,
                               page_url=SPECNEXT_LATEST_DISTRO_URL):
    """Build *target* (``size_token``, e.g. ``"2G"``) from the latest
    distribution. Returns the ``DistroDownload`` used. Raises on any
    failure; a cancel raises RuntimeError("cancelled")."""
    log = log or (lambda _line: None)
    os.makedirs(cache_dir, exist_ok=True)

    status("Reading the latest distribution page…\n" + page_url)
    info = parse_latest_distro(fetch_text(page_url))
    if info is None:
        raise RuntimeError("the latest-distribution page lists no distribution zip")

    zip_path = os.path.join(cache_dir, info.name)
    cached = os.path.isfile(zip_path) and info.sha1 and sha1_of_file(zip_path) == info.sha1
    if cached:
        log(f"Using the cached {info.name} ({zip_path})")
    else:
        status(f"Downloading {info.name}…\n{info.url}")
        progress(0)
        fetch_file(info.url, zip_path, progress=progress, cancel_event=cancel_event)
        if info.sha1:
            got = sha1_of_file(zip_path)
            if got != info.sha1:
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
                raise RuntimeError(
                    f"{info.name} did not match the SHA-1 the page gives "
                    f"({got} instead of {info.sha1}); download discarded")
        log(f"Downloaded {info.name} ({human_size(os.path.getsize(zip_path))})")
    if cancel_event.is_set():
        raise RuntimeError("cancelled")

    work = tempfile.mkdtemp(prefix="sn-", dir=cache_dir)
    try:
        status(f"Unpacking {info.name}…")
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
            total = max(len(members), 1)
            for i, member in enumerate(members):
                if cancel_event.is_set():
                    raise RuntimeError("cancelled")
                archive.extract(member, work)
                if i % 25 == 0:
                    progress(int(i * 100 / total))
        progress(100)
        tree = distro_tree_root(work)
        progress(-1)
        _fresh_image(execute, target, size_token, blocker, status)
        _fill_image(execute, target, tree, status)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return info


def make_image_mame_bootable_task(*, execute, source, target, size_token,
                                  work_dir, progress, status, cancel_event,
                                  log=None, blocker=None):
    """Copy every file of *source* into a fresh FAT32 *target* of
    ``size_token``. Returns the number of files copied. Raises on any
    failure, before the target is touched when a file cannot be read."""
    log = log or (lambda _line: None)
    os.makedirs(work_dir, exist_ok=True)
    progress(-1)
    dirs, files = list_image_tree(execute, source, status=status,
                                  cancel_event=cancel_event)
    if cancel_event.is_set():
        raise RuntimeError("cancelled")
    work = tempfile.mkdtemp(prefix="rebuild-", dir=work_dir)
    try:
        for d in dirs:
            os.makedirs(os.path.join(work, *d.strip("/").split("/")), exist_ok=True)
        total = max(len(files), 1)
        for i, f in enumerate(files):
            if cancel_event.is_set():
                raise RuntimeError("cancelled")
            status(f"Copying out ({i + 1}/{total})\n{f}")
            progress(int(i * 100 / total))
            local = os.path.join(work, *f.strip("/").split("/"))
            _check(execute("get", source, extra_argv=[f, local.replace("\\", "/")],
                           silent=True, prompt_if_missing=False),
                   f"reading {f}")
            if not os.path.isfile(local):
                raise RuntimeError(f"reading {f} failed: nothing was written")
        progress(-1)
        _fresh_image(execute, target, size_token, blocker, status)
        _fill_image(execute, target, work, status)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log(f"Rebuilt {source} as {target}: {len(files)} files, {len(dirs)} folders")
    return len(files)


# --------------------------------------------------------------------------- #
# The host-facing operations
# --------------------------------------------------------------------------- #
def build_nextzxos_image_ops(host, *, execute_hdf_monkey, add_log, show_toast,
                             ask_size, ask_save_path, run_task,
                             hdfmonkey_present, load_image_path, cache_dir,
                             image_write_blocker=None, read_usage=read_image_fat_usage,
                             fetch_text=fetch_text, fetch_file=fetch_file,
                             loaded_image=None):
    """Define ``host.create_nextzxos_image()``,
    ``host.make_image_mame_bootable(path=None)`` and
    ``host._nextzxos_mame_check(path)`` (the load-time offer).

    Hooks: ``execute_hdf_monkey(cmd, image, extra_argv=, options=, silent=,
    prompt_if_missing=)`` → CompletedProcess; ``add_log(line)``;
    ``show_toast(title, body, variant=, duration_ms=, action=)``;
    ``ask_size(default_label)`` → a label of NEXTZXOS_IMAGE_SIZE_CHOICES or
    None; ``ask_save_path(suggested_name)`` → path or ""; ``run_task(title,
    fn, on_done)`` runs ``fn(progress=, status=, cancel_event=)`` off the UI
    thread behind a progress dialog and calls ``on_done(result, error,
    cancelled)`` on the UI thread; ``hdfmonkey_present()`` → bool;
    ``load_image_path(path)`` selects and loads an image in the SD Card tab;
    ``image_write_blocker(path)`` → why a write is refused, or "";
    ``loaded_image()`` → the loaded image's path or ""."""
    host._nextzxos_mame_offered = set()

    def _blocker(path):
        fn = image_write_blocker or getattr(host, "_image_write_blocker", None)
        try:
            return fn(path) if fn is not None else ""
        except Exception:                                   # noqa: BLE001
            return ""

    def _key(path):
        keyer = getattr(host, "_image_state_key", None)
        try:
            return keyer(path) if keyer else os.path.normcase(os.path.abspath(path))
        except Exception:                                   # noqa: BLE001
            return path

    def _need_hdfmonkey():
        if hdfmonkey_present():
            return True
        add_log(ui_tr_now(
            "hdfmonkey is needed to create or rebuild an image. Install it with the "
            "'Download and install HDF Monkey' button (bottom right of the SD Card tab)."))
        show_toast("⚠  hdfmonkey needed", ui_tr_now(
            "hdfmonkey is needed to create or rebuild an image. Install it with the "
            "'Download and install HDF Monkey' button (bottom right of the SD Card tab)."),
            variant="yellow", duration_ms=10000)
        return False

    def create_nextzxos_image():
        if not _need_hdfmonkey():
            return
        label = ask_size(NEXTZXOS_IMAGE_DEFAULT_SIZE)
        choice = size_choice(label) if label else None
        if choice is None:
            return
        size_label, token, _size_bytes = choice
        target = ask_save_path(f"nextzxos-{token.lower()}b.img")
        if not target:
            return
        target = os.path.abspath(target)
        add_log(ui_tr_now("Creating a {size} NextZXOS image: {path}").format(
            size=size_label, path=target))

        def _work(progress, status, cancel_event):
            return create_nextzxos_image_task(
                execute=execute_hdf_monkey, target=target, size_token=token,
                cache_dir=cache_dir, progress=progress, status=status,
                cancel_event=cancel_event, log=add_log, blocker=_blocker,
                fetch_text=fetch_text, fetch_file=fetch_file)

        def _done(info, error, cancelled):
            if cancelled or error is not None:
                reason = ui_tr_now("cancelled") if cancelled else str(error)
                add_log(ui_tr_now("Failed creating the NextZXOS image: {error}").format(
                    error=reason))
                show_toast("❌  NextZXOS image not created", reason,
                           variant="red", duration_ms=12000)
                return
            add_log(ui_tr_now(
                "NextZXOS image created: {path} (System/Next {version}, {size})").format(
                    path=target, version=info.version, size=size_label))
            show_toast("✅  NextZXOS image created", ui_tr_now(
                "{path}\r\nSystem/Next {version}, {size}, FAT32 - loading it now.").format(
                    path=target, version=info.version, size=size_label),
                variant="green", duration_ms=12000)
            load_image_path(target)

        run_task("Creating NextZXOS image…", _work, _done)

    def make_image_mame_bootable(path=None):
        if not _need_hdfmonkey():
            return
        source = path or (loaded_image() if loaded_image else "") or ""
        if not source:
            add_log(ui_tr_now("Load an image first."))
            return
        source = os.path.abspath(source)
        usage = read_usage(source)
        verdict = mame_bootable_verdict(usage)
        if verdict == "ok":
            show_toast("✅  This image is fine for MAME", ui_tr_now(
                "{path}\r\nFAT{bits} with {clusters} clusters: the Next firmware under "
                "MAME reads it as it is.").format(
                    path=source, bits=usage.fat_bits, clusters=f"{usage.total_clusters:,}"),
                variant="green", duration_ms=10000)
            return
        label = ask_size(NEXTZXOS_IMAGE_DEFAULT_SIZE)
        choice = size_choice(label) if label else None
        if choice is None:
            return
        size_label, token, size_bytes = choice
        if not size_fits(usage, size_bytes):
            msg = ui_tr_now("A {size} image cannot hold the {used} in use on {path}.").format(
                size=size_label, used=human_size(image_bytes_in_use(usage)), path=source)
            add_log(msg)
            show_toast("❌  Image not rebuilt", msg, variant="red", duration_ms=12000)
            return
        stem, _ext = os.path.splitext(os.path.basename(source))
        target = ask_save_path(os.path.join(os.path.dirname(source), f"{stem}-mame.img"))
        if not target:
            return
        target = os.path.abspath(target)
        if _key(target) == _key(source):
            msg = ui_tr_now("The new image must not be the one being rebuilt: {path}").format(
                path=source)
            add_log(msg)
            show_toast("❌  Image not rebuilt", msg, variant="red", duration_ms=12000)
            return
        add_log(ui_tr_now("Rebuilding {source} as a {size} FAT32 image: {path}").format(
            source=source, size=size_label, path=target))

        def _work(progress, status, cancel_event):
            return make_image_mame_bootable_task(
                execute=execute_hdf_monkey, source=source, target=target,
                size_token=token, work_dir=cache_dir, progress=progress,
                status=status, cancel_event=cancel_event, log=add_log,
                blocker=_blocker)

        def _done(count, error, cancelled):
            if cancelled or error is not None:
                reason = ui_tr_now("cancelled") if cancelled else str(error)
                add_log(ui_tr_now("Failed rebuilding the image: {error}").format(
                    error=reason))
                show_toast("❌  Image not rebuilt", reason, variant="red",
                           duration_ms=12000)
                return
            add_log(ui_tr_now(
                "Image rebuilt for MAME: {path} ({count} files, {size})").format(
                    path=target, count=count, size=size_label))
            show_toast("✅  Image rebuilt for MAME", ui_tr_now(
                "{path}\r\n{count} files copied into a fresh {size} FAT32 image.").format(
                    path=target, count=count, size=size_label),
                variant="green", duration_ms=15000,
                action=("Load it", lambda: load_image_path(target)))

        run_task("Rebuilding the image for MAME…", _work, _done)

    def _nextzxos_mame_check(path):
        """The load-time offer: once per image per run, only when hdfmonkey
        is there to act and the FAT shape is the one MAME trips on."""
        if not path or not hdfmonkey_present():
            return
        key = _key(path)
        if key in host._nextzxos_mame_offered:
            return
        usage = read_usage(path)
        if mame_bootable_verdict(usage) != "fat32-small":
            return
        host._nextzxos_mame_offered.add(key)
        add_log(ui_tr_now(
            "{path} is FAT32 with {clusters} clusters, fewer than the 65,525 FAT32 "
            "needs: the Next firmware under MAME reads it as FAT16 and stops at "
            "'Error opening menu.ini/.def'. CSpect boots it. Rebuild it for MAME from "
            "the image explorer's menu.").format(path=path, clusters=f"{usage.total_clusters:,}"))
        show_toast("⚠  MAME can't boot this image", ui_tr_now(
            "FAT32 with {clusters} clusters, fewer than the 65,525 FAT32 needs: the "
            "Next firmware under MAME reads it as FAT16 and stops. CSpect boots it.\r\n"
            "Rebuild it as a fresh FAT32 image for MAME?").format(
                clusters=f"{usage.total_clusters:,}"),
            variant="yellow", duration_ms=15000,
            action=("Rebuild for MAME", lambda p=path: make_image_mame_bootable(p)))

    host.create_nextzxos_image = create_nextzxos_image
    host.make_image_mame_bootable = make_image_mame_bootable
    host._nextzxos_mame_check = _nextzxos_mame_check
    return create_nextzxos_image, make_image_mame_bootable, _nextzxos_mame_check
