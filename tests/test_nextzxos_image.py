"""Create NextZXOS Image and Make this image MAME-bootable (zxnu_nextzxos_image,
9.7.65), headless. hdfmonkey is a fake answering ``ls``/``get`` from an
in-memory tree and recording ``create``/``putdir``; the network is a fake
serving a captured fragment of the latest-distribution page and a zip built
in memory; the dialogs, the progress-dialog runner, the toasts and the log
are recorders. Covered: the page parser (flavor preference, the SHA-1/MD5
under each link, a .dsu never mistaken for a zip), the verdict and the size
arithmetic, the tree root of a nested zip, the image walk, the create task
(``--fat32`` always, the page's SHA-1 enforced and a bad download discarded,
the cache reused, the write gate asked before the file is touched, cancel),
the rebuild task (every file read before the target is touched, a failing
read aborting with nothing written), the host-facing operations (no
hdfmonkey, cancelled dialogs, success and failure toasts, the "fine for
MAME" answer, the too-small size, the same-file refusal) and the load-time
offer (once per image, only for the FAT32-under-65,525 shape), plus source
tripwires on the app wiring.
Run with: python tests/test_nextzxos_image.py
"""
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import threading
import types
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

import zxnu_nextzxos_image as nz                    # noqa: E402
from zxnu_config import FatUsage, HDFMONKEY_WRITE_VERBS   # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


# A fragment of https://www.specnext.com/latestdistro/ as served 2026-11-11
# (the three distribution zips, the .dsu and the emulator image), with the
# SHA-1 of the nosrc zip swapped for the fake archive's at test time.
PAGE = (
    '<li>Complete System/Next distribution: <strong><a href="https://www.specnext.com/distro/26.11a/sn-complete-26.11a.zip">'
    '<span style="color: #23ff23;">sn-complete-26.11a.zip</span></a></strong><br /><small>MD5: 432c4cd964421f59d55ea32c93e0e93c'
    '<br />SHA-1: 62c3b42c5240307eca8e5f3650f343a5afe36610</small></li>\n'
    '<li>System/Next distribution, without source code: <strong><a href="https://www.specnext.com/distro/26.11a/sn-nosrc-26.11a.zip">'
    '<span style="color: #23ff23;">sn-nosrc-26.11a.zip</span></a></strong><br /><small>MD5: 63dea55a0f88f844b28f13161dbde446'
    '<br />SHA-1: {NOSRC_SHA1}</small></li>\n'
    '<li>System/Next distribution, lightweight version: <strong><a href="https://www.specnext.com/distro/26.11a/sn-lite-26.11a.zip">'
    '<span style="color: #23ff23;">sn-lite-26.11a.zip</span></a></strong><br /><small>MD5: df6a1640538247fb7905b4f1ca3b10d4'
    '<br />SHA-1: f0df76d0fa1c2097d1fc5896ff5aa29c21cb7bd2</small></li>\n'
    '<li>System/Next distribution, differential update: <strong><a href="https://www.specnext.com/distro/26.11a/sn-update-26.11a.dsu">'
    '<span style="color: #23ff23;">sn-update-26.11a.dsu </span></a></strong><em>(recommended)</em><br /><small>MD5: c5cd04d3af0f1f801ab2a143beaf86e4'
    '<br />SHA-1: 8c8a02b5509f19ae7d6c78970425798c888c14f1</small></li>\n'
    '<li>System/Next distribution, image for emulators (1Gb image): <strong> <a href="https://www.specnext.com/distro/26.11a/sn-emulator-26.11a.zip">'
    '<span style="color: #23ff23;">sn-emulator-26.11a.zip</span></a></strong><br /><small>MD5: 2ac05944f3c715329e18ede787e9f120'
    '<br />SHA-1: d720d6ea9a75e76c4017e6916ec445b438b1c144</small></li>\n'
)

DISTRO_FILES = {
    "TBBLUE.FW": b"firmware",
    "machines/next/CONFIG.INI": b"timing=8\n",
    "machines/next/MENU.DEF": b"menu=ZX Spectrum Next (standard),2,8,enNextZX.rom\n",
    "dot/sync5": b"\x7fDOT",
}


def make_zip(files, nested=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr((nested + "/" + name) if nested else name, data)
    return buf.getvalue()


def cp(rc=0, out=b"", err=b""):
    return subprocess.CompletedProcess([], rc, out, err)


class FakeHdfmonkey:
    """``ls`` and ``get`` from an in-memory tree {image_path: bytes};
    ``create`` / ``putdir`` (and anything else) recorded and successful
    unless told to fail."""

    def __init__(self, tree=None, fail=()):
        self.tree = dict(tree or {})
        self.fail = set(fail)
        self.calls = []

    def __call__(self, command, image, additional_args="", silent=False,
                 extra_argv=None, prompt_if_missing=True, options=None):
        extra = list(extra_argv or [])
        self.calls.append((command, image, list(options or []), extra, silent, prompt_if_missing))
        if command in self.fail:
            return cp(1, b"", b"hdfmonkey: boom")
        if command == "ls":
            root = extra[0].rstrip("/") or ""
            names = {}
            for path in self.tree:
                if not path.startswith(root + "/"):
                    continue
                rest = path[len(root) + 1:]
                head = rest.split("/", 1)[0]
                names[head] = "D" if "/" in rest else "F"
            lines = [f"{'[DIR]' if t == 'D' else len(self.tree.get(root + '/' + n, b''))}\t{n}"
                     for n, t in sorted(names.items())]
            return cp(0, ("\n".join(lines) + "\n").encode())
        if command == "get":
            src, dst = extra
            if src not in self.tree:
                return cp(1, b"", b"not found")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(self.tree[src])
            return cp(0)
        return cp(0)


def fake_net(page, archive):
    calls = {"text": 0, "file": 0}

    def fetch_text(url, timeout=30):
        calls["text"] += 1
        return page

    def fetch_file(url, path, progress=None, cancel_event=None, timeout=60):
        calls["file"] += 1
        with open(path, "wb") as f:
            f.write(archive)
        if progress:
            progress(100)
    return fetch_text, fetch_file, calls


def main():
    print("== parse_latest_distro ==")
    archive = make_zip(DISTRO_FILES)
    sha1 = hashlib.sha1(archive).hexdigest()
    page = PAGE.replace("{NOSRC_SHA1}", sha1)
    info = nz.parse_latest_distro(page)
    check("nosrc is preferred and carries version, url, sha1 and md5",
          info is not None and info.name == "sn-nosrc-26.11a.zip" and info.version == "26.11a"
          and info.url.endswith("/distro/26.11a/sn-nosrc-26.11a.zip") and info.sha1 == sha1
          and info.md5 == "63dea55a0f88f844b28f13161dbde446", str(info))
    lite = nz.parse_latest_distro(page.replace("sn-nosrc", "sn-xxxxx"))
    check("without nosrc the lite zip is taken, with ITS sha1",
          lite is not None and lite.name == "sn-lite-26.11a.zip"
          and lite.sha1 == "f0df76d0fa1c2097d1fc5896ff5aa29c21cb7bd2", str(lite))
    only_complete = nz.parse_latest_distro(page.replace("sn-nosrc", "x").replace("sn-lite", "y"))
    check("then the complete one", only_complete is not None and only_complete.name == "sn-complete-26.11a.zip")
    check("preference order can be given",
          nz.parse_latest_distro(page, flavors=("sn-lite",)).name == "sn-lite-26.11a.zip")
    check("the .dsu and the emulator image are never taken",
          nz.parse_latest_distro(page.replace(".zip", ".7z")) is None)
    check("a link with no SHA-1 under it still answers, with an empty sha1",
          nz.parse_latest_distro('<a href="https://x/distro/1/sn-lite-1.zip">z</a>').sha1 == "")
    check("no page, no answer", nz.parse_latest_distro("") is None and nz.parse_latest_distro(None) is None)

    print("\n== verdicts and sizes ==")
    small = FatUsage(27974, 32758, 32768, 32)           # the 26.11a emulator image
    check("FAT32 under 65,525 clusters is the MAME-breaking shape",
          nz.mame_bootable_verdict(small) == "fat32-small")
    check("FAT32 at the floor is fine", nz.mame_bootable_verdict(FatUsage(1, 65525, 8192, 32)) == "ok")
    check("a FAT16 volume is fine", nz.mame_bootable_verdict(FatUsage(1, 65517, 16384, 16)) == "ok")
    check("unreadable is unknown", nz.mame_bootable_verdict(None) == "unknown")
    check("bytes in use", nz.image_bytes_in_use(small) == (32758 - 27974) * 32768
          and nz.image_bytes_in_use(None) == 0)
    check("size choices and fit", nz.size_choice("2 GB") == ("2 GB", "2G", 2 * 1024 ** 3)
          and nz.size_choice("1 GB") is None and nz.size_fits(small, 2 * 1024 ** 3)
          and not nz.size_fits(FatUsage(0, 100000, 32768, 32), 2 * 1024 ** 3)
          and nz.size_fits(None, 1))
    check("human sizes", nz.human_size(512) == "512 bytes" and nz.human_size(1536) == "1.5 KB"
          and nz.human_size(3 * 1024 ** 3) == "3.0 GB")
    check("no 1 GB choice (the size whose FAT32 falls under the floor)",
          all(c[2] >= 2 * 1024 ** 3 for c in nz.NEXTZXOS_IMAGE_SIZE_CHOICES))

    print("\n== distro_tree_root ==")
    with tempfile.TemporaryDirectory() as td:
        flat = os.path.join(td, "flat"); os.makedirs(flat)
        open(os.path.join(flat, "TBBLUE.FW"), "wb").close()
        nested = os.path.join(td, "nested", "sn-nosrc-26.11a"); os.makedirs(nested)
        open(os.path.join(nested, "tbblue.fw"), "wb").close()
        check("a flat zip is its own root", nz.distro_tree_root(flat) == flat)
        check("a zip nested one folder down is unwrapped (case-blind)",
              nz.distro_tree_root(os.path.join(td, "nested")) == nested)
        other = os.path.join(td, "other"); os.makedirs(os.path.join(other, "a")); os.makedirs(os.path.join(other, "b"))
        check("two folders and no TBBLUE.FW: the folder itself", nz.distro_tree_root(other) == other)

    print("\n== list_image_tree ==")
    tree = {"/TBBLUE.FW": b"fw", "/machines/next/CONFIG.INI": b"c", "/machines/next/MENU.DEF": b"m",
            "/dot/sync5": b"d"}
    hdf = FakeHdfmonkey(tree)
    dirs, files = nz.list_image_tree(hdf, "x.img")
    check("every folder and file is found, folders before their contents",
          dirs == ["/dot", "/machines", "/machines/next"]
          and sorted(files) == sorted(tree) and dirs.index("/machines") < dirs.index("/machines/next"),
          str((dirs, files)))
    check("every listing is silent and never prompts",
          all(c[4] is True and c[5] is False for c in hdf.calls))
    bad = FakeHdfmonkey(tree, fail=("ls",))
    try:
        nz.list_image_tree(bad, "x.img"); check("a failing listing raises", False)
    except RuntimeError as ex:
        check("a failing listing raises", "could not list" in str(ex))

    print("\n== create_nextzxos_image_task ==")
    with tempfile.TemporaryDirectory() as td:
        cache = os.path.join(td, "cache")
        target = os.path.join(td, "new.img")
        fetch_text, fetch_file, calls = fake_net(page, archive)
        hdf = FakeHdfmonkey()
        logs = []
        kw = dict(execute=hdf, target=target, size_token="2G", cache_dir=cache,
                  progress=lambda p: None, status=lambda s: None, cancel_event=threading.Event(),
                  log=logs.append, fetch_text=fetch_text, fetch_file=fetch_file)
        info = nz.create_nextzxos_image_task(**kw)
        verbs = [(c[0], c[2], c[3]) for c in hdf.calls]
        check("create --fat32 <image> <size>, then putdir <tree> /",
              len(verbs) == 2 and verbs[0] == ("create", ["--fat32"], ["2G"]) and hdf.calls[0][1] == target
              and verbs[1][0] == "putdir" and verbs[1][2][1] == "/" and hdf.calls[1][1] == target, str(verbs))
        check("the putdir tree held the distribution and is gone afterwards",
              not os.path.exists(verbs[1][2][0]) and info.version == "26.11a")
        check("the zip is cached under its own name",
              os.path.isfile(os.path.join(cache, "sn-nosrc-26.11a.zip")) and calls["file"] == 1)
        nz.create_nextzxos_image_task(**kw)
        check("a second image downloads nothing (cache verified by SHA-1)",
              calls["file"] == 1 and any("cached" in line for line in logs))
        # a corrupt download is refused and discarded
        bad_text, bad_file, _c = fake_net(page, archive + b"x")
        os.remove(os.path.join(cache, "sn-nosrc-26.11a.zip"))
        try:
            nz.create_nextzxos_image_task(**dict(kw, fetch_text=bad_text, fetch_file=bad_file))
            check("a download that does not match the page's SHA-1 is refused", False)
        except RuntimeError as ex:
            check("a download that does not match the page's SHA-1 is refused",
                  "SHA-1" in str(ex) and not os.path.exists(os.path.join(cache, "sn-nosrc-26.11a.zip")))
        # the write gate is asked before the file is touched
        hdf2 = FakeHdfmonkey()
        open(target, "wb").write(b"held")
        try:
            nz.create_nextzxos_image_task(**dict(kw, execute=hdf2, blocker=lambda p: "MAME holds it"))
            check("a held target is refused", False)
        except RuntimeError as ex:
            check("a held target is refused before anything is written",
                  "MAME holds it" in str(ex) and open(target, "rb").read() == b"held" and not hdf2.calls)
        # the page can lie
        try:
            nz.create_nextzxos_image_task(**dict(kw, fetch_text=lambda u, timeout=30: "<p>nothing</p>"))
            check("a page with no zip is an error", False)
        except RuntimeError as ex:
            check("a page with no zip is an error", "no distribution zip" in str(ex))
        # cancel
        ev = threading.Event(); ev.set()
        try:
            nz.create_nextzxos_image_task(**dict(kw, cancel_event=ev))
            check("cancel raises", False)
        except RuntimeError as ex:
            check("cancel raises 'cancelled'", str(ex) == "cancelled")
        # a nested zip is unwrapped
        nested_zip = make_zip(DISTRO_FILES, nested="sn-nosrc-26.11a")
        nested_page = PAGE.replace("{NOSRC_SHA1}", hashlib.sha1(nested_zip).hexdigest())
        n_text, n_file, _c = fake_net(nested_page, nested_zip)
        hdf3 = FakeHdfmonkey()
        nz.create_nextzxos_image_task(**dict(kw, execute=hdf3, fetch_text=n_text, fetch_file=n_file))
        check("a zip nested one folder down is put from that folder",
              hdf3.calls[1][3][0].replace("\\", "/").endswith("/sn-nosrc-26.11a"))

    print("\n== make_image_mame_bootable_task ==")
    with tempfile.TemporaryDirectory() as td:
        hdf = FakeHdfmonkey(tree)
        target = os.path.join(td, "fixed.img")
        count = nz.make_image_mame_bootable_task(
            execute=hdf, source="old.img", target=target, size_token="2G", work_dir=td,
            progress=lambda p: None, status=lambda s: None, cancel_event=threading.Event())
        gets = [c for c in hdf.calls if c[0] == "get"]
        tail = [(c[0], c[2], c[3]) for c in hdf.calls if c[0] in ("create", "putdir")]
        check("every file is read out, then create --fat32 and putdir",
              count == 4 and len(gets) == 4 and sorted(g[3][0] for g in gets) == sorted(tree)
              and tail[0] == ("create", ["--fat32"], ["2G"]) and tail[1][0] == "putdir"
              and hdf.calls[-1][1] == target, str((count, tail)))
        check("the reads keep the folder structure",
              all(g[3][1].replace("\\", "/").endswith(g[3][0]) for g in gets))
        failing = FakeHdfmonkey(tree, fail=("get",))
        try:
            nz.make_image_mame_bootable_task(
                execute=failing, source="old.img", target=target, size_token="2G", work_dir=td,
                progress=lambda p: None, status=lambda s: None, cancel_event=threading.Event())
            check("a file that cannot be read aborts", False)
        except RuntimeError as ex:
            check("a file that cannot be read aborts before the target is touched",
                  "reading" in str(ex) and not any(c[0] in ("create", "putdir") for c in failing.calls))
        check("the scratch trees are gone", not any(n.startswith("rebuild-") for n in os.listdir(td)))

    print("\n== the host-facing operations ==")

    def make(present=True, size="2 GB", save="auto", usage=small, blocker=None):
        host = types.SimpleNamespace(_image_state_key=lambda p: os.path.normcase(p))
        toasts, logs, loads, tasks = [], [], [], []

        def run_task(title, fn, on_done):
            tasks.append(title)
            try:
                result = fn(progress=lambda p: None, status=lambda s: None,
                            cancel_event=threading.Event())
            except Exception as exc:                      # noqa: BLE001
                on_done(None, exc, False)
            else:
                on_done(result, None, False)
        hdf = FakeHdfmonkey(tree)
        fetch_text, fetch_file, _c = fake_net(page, archive)
        td = tempfile.mkdtemp()
        nz.build_nextzxos_image_ops(
            host, execute_hdf_monkey=hdf, add_log=logs.append,
            show_toast=lambda *a, **k: toasts.append((a, k)),
            ask_size=lambda default: size, ask_save_path=lambda s: (os.path.join(td, "out.img") if save == "auto" else save),
            run_task=run_task, hdfmonkey_present=lambda: present,
            load_image_path=loads.append, cache_dir=td, image_write_blocker=blocker,
            read_usage=lambda p: usage, fetch_text=fetch_text, fetch_file=fetch_file,
            loaded_image=lambda: os.path.join(td, "loaded.img"))
        return host, hdf, toasts, logs, loads, tasks, td

    host, hdf, toasts, logs, loads, tasks, td = make(present=False)
    host.create_nextzxos_image()
    check("no hdfmonkey: a yellow toast and a log line, nothing runs",
          not tasks and toasts and toasts[0][1].get("variant") == "yellow" and "hdfmonkey" in logs[-1])
    host.make_image_mame_bootable()
    check("...for the rebuild too", len(toasts) == 2 and not tasks)

    host, hdf, toasts, logs, loads, tasks, td = make(size=None)
    host.create_nextzxos_image()
    check("a cancelled size question does nothing", not tasks and not toasts and not hdf.calls)
    host, hdf, toasts, logs, loads, tasks, td = make(save="")
    host.create_nextzxos_image()
    check("a cancelled save dialog does nothing", not tasks and not toasts and not hdf.calls)

    host, hdf, toasts, logs, loads, tasks, td = make()
    host.create_nextzxos_image()
    target = os.path.join(td, "out.img")
    check("create: the task ran, the image was loaded, a green toast says so",
          tasks == ["Creating NextZXOS image…"] and loads == [os.path.abspath(target)]
          and toasts[-1][1].get("variant") == "green" and "26.11a" in toasts[-1][0][1]
          and any("System/Next 26.11a" in line for line in logs), str((tasks, loads, toasts)))

    host, hdf, toasts, logs, loads, tasks, td = make(blocker=lambda p: "CSpect is running on it")
    host.create_nextzxos_image()
    check("create: a refused write is a red toast and a log line, nothing loaded",
          toasts[-1][1].get("variant") == "red" and "CSpect is running" in toasts[-1][0][1]
          and not loads and not any(c[0] == "create" for c in hdf.calls))

    host, hdf, toasts, logs, loads, tasks, td = make(usage=FatUsage(1, 131005, 16384, 32))
    host.make_image_mame_bootable(os.path.join(td, "fine.img"))
    check("rebuild: an image whose FAT is fine gets a green answer and no work",
          not tasks and toasts[-1][1].get("variant") == "green" and "131,005" in toasts[-1][0][1])

    # 60,000 clusters of 32 KB: the MAME-breaking shape AND 1.9 GB in use
    host, hdf, toasts, logs, loads, tasks, td = make(usage=FatUsage(0, 60000, 32768, 32))
    host.make_image_mame_bootable(os.path.join(td, "big.img"))
    check("rebuild: a size that cannot hold the files is refused",
          not tasks and toasts[-1][1].get("variant") == "red" and "cannot hold" in toasts[-1][0][1])

    host, hdf, toasts, logs, loads, tasks, td = make(save=os.path.join("X", "loaded.img"))
    host.make_image_mame_bootable(os.path.join("X", "loaded.img"))
    check("rebuild: the target may not be the source",
          not tasks and toasts[-1][1].get("variant") == "red" and "must not be" in toasts[-1][0][1])

    host, hdf, toasts, logs, loads, tasks, td = make()
    host.make_image_mame_bootable()
    act = toasts[-1][1].get("action")
    check("rebuild: the loaded image is read, a fresh FAT32 image written, the toast offers to load it",
          tasks == ["Rebuilding the image for MAME…"] and toasts[-1][1].get("variant") == "green"
          and act and act[0] == "Load it" and not loads
          and [c[0] for c in hdf.calls if c[0] in ("create", "putdir")] == ["create", "putdir"], str(toasts))
    act[1]()
    check("...and 'Load it' loads it", loads == [os.path.abspath(os.path.join(td, "out.img"))])

    print("\n== the load-time offer ==")
    host, hdf, toasts, logs, loads, tasks, td = make()
    img = os.path.join(td, "cspect-next-1gb.img")
    host._nextzxos_mame_check(img)
    check("a FAT32 image under 65,525 clusters gets ONE yellow toast with a 'Rebuild for MAME' action",
          len(toasts) == 1 and toasts[0][1].get("variant") == "yellow"
          and toasts[0][1]["action"][0] == "Rebuild for MAME" and "32,758" in toasts[0][0][1]
          and any("65,525" in line for line in logs))
    host._nextzxos_mame_check(img)
    check("a reload of the same image is not asked again", len(toasts) == 1)
    toasts[0][1]["action"][1]()
    check("the action runs the rebuild", tasks == ["Rebuilding the image for MAME…"])
    host, hdf, toasts, logs, loads, tasks, td = make(usage=FatUsage(1, 131005, 16384, 32))
    host._nextzxos_mame_check(os.path.join(td, "ok.img"))
    check("a fine image gets no offer", not toasts)
    host, hdf, toasts, logs, loads, tasks, td = make(present=False)
    host._nextzxos_mame_check(os.path.join(td, "x.img"))
    check("no hdfmonkey, no offer (nothing could act on it)", not toasts)
    host, hdf, toasts, logs, loads, tasks, td = make(usage=None)
    host._nextzxos_mame_check(os.path.join(td, "x.img"))
    check("an unreadable image gets no offer", not toasts)

    print("\n== source tripwires ==")
    main_src = open(os.path.join(REPO, "zxnu_main.py"), encoding="utf-8").read()
    ops_src = open(os.path.join(REPO, "zxnu_sdcard_ops.py"), encoding="utf-8").read()
    check("the Create button follows the other hdfmonkey controls at every show/hide site",
          main_src.count("self.createimage.setVisible(") == main_src.count("self.button_new_folder.setVisible(")
          and main_src.count("self.createimage.setVisible(") >= 5)
    check("the button sits on the image row and calls the op",
          "self.horizontal1.addWidget(self.createimage)" in main_src
          and "self.create_nextzxos_image()" in main_src)
    check("the load pipeline schedules the MAME check after the .sync5 one",
          "_nextzxos_mame_check" in ops_src and ops_src.index("_sync5_img_check") < ops_src.index("_nextzxos_mame_check"))
    check("the image explorer's menu offers the rebuild",
          'ui_tr_now("Make this image MAME-bootable…")' in ops_src)
    check("execute_hdf_monkey takes the options that go before the image",
          "options=None" in ops_src and "list(options or [])" in ops_src)
    check("create and putdir pass through the write gate",
          {"create", "putdir"} <= HDFMONKEY_WRITE_VERBS)

    print("\nRESULT:", "ALL PASS" if not FAIL else f"FAILURES: {FAIL}")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
