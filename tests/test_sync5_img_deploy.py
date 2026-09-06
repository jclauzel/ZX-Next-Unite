"""The .sync5 auto-deploy into a loaded disk image (zxnu_sync5_img, 9.7.8).

Headless: hdfmonkey is an in-memory fake image speaking the real tool's
shapes (bare-size ``ls`` lines, errors on STDOUT), the binary resolver a
temp file wearing the banner, the thread helper synchronous, the timer a
recorder, the wizard and the toast recorders. Covered: the pure helpers
(banner version, version key, verdict incl. "unreadable", ls parse of both
spellings, hdfmonkey_message), the gate (setting off, image busy via the
write-gate cache AND via the app-launched emulator record, image denied,
declined, in flight, an offer still open), each verdict's outcome
(missing/older/unknown offer; current/newer silent; unreadable and an
unobtainable build logged), who asks (Wizzy when enabled, started and free
— the offer WAITS for a busy wizard, up to the bound, before the toast asks
— else the 15 s yellow toast with its "Deploy now" action), every hdfmonkey
call carrying silent=True / prompt_if_missing=False, and the deploy itself
(the re-probe at Yes, mkdir + put + read-back banner check, the rm-then-put
retry, the resolver's failure, a wrong read-back, a failing put naming
hdfmonkey's stdout message).

Run with: python tests/test_sync5_img_deploy.py
"""
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

import zxnu_sync5_img as s5                          # noqa: E402
from zxnu_config import (                           # noqa: E402
    SETTING_SYNC5_IMG_AUTODEPLOY, sync5_blob_version, sync5_version_key,
)

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def dot(version):
    return b"\x7fDOT" + bytes(range(64)) + b"NextSync " + version.encode() + b" Clauzel/Komppa\x00tail"


def cp(rc, out=b""):
    # hdfmonkey prints its errors on STDOUT (measured, 0.5.7); stderr stays empty.
    return subprocess.CompletedProcess([], rc, out, b"")


class FakeImage:
    """An in-memory image: {"/dot": {"sync5": bytes}} plus a call log."""
    def __init__(self, files=None, refuse_overwrite=False):
        self.fs = {}                                    # dir -> {name: bytes}
        for path, data in (files or {}).items():
            d, _, n = path.rpartition("/")
            self.fs.setdefault(d, {})[n] = data
        self.calls = []
        self.kwargs = []
        self.refuse_overwrite = refuse_overwrite        # defensive branch: real hdfmonkey overwrites
        self.put_fail_always = False
        self.get_fail = False

    def execute(self, cmd, image, additional_args="", silent=False,
                extra_argv=None, prompt_if_missing=True):
        argv = list(extra_argv or [])
        self.calls.append((cmd, tuple(argv)))
        self.kwargs.append((silent, prompt_if_missing))
        if cmd == "ls":
            d = argv[0].rstrip("/") or "/"
            if d not in self.fs:
                return cp(1, b"Error opening dir: Path not found")
            out = b""
            for name, data in sorted(self.fs[d].items()):
                out += (b"[DIR]\t" if data is None else
                        b"%d\t" % len(data)) + name.encode() + b"\n"
            return cp(0, out)
        if cmd == "mkdir":
            d = argv[0].rstrip("/")
            if d in self.fs:
                return cp(1, b"Directory creation failed: File / directory already exists")
            self.fs[d] = {}
            return cp(0)
        if cmd == "get":
            d, _, n = argv[0].rpartition("/")
            data = self.fs.get(d, {}).get(n)
            if data is None or self.get_fail:
                return cp(1, b"Error opening file: Path not found")
            with open(argv[1], "wb") as fh:
                fh.write(data)
            return cp(0)
        if cmd == "put":
            src, dst = argv
            d, _, n = dst.rpartition("/")
            if self.put_fail_always:
                return cp(1, b"Error writing file: Disk full")
            if d not in self.fs:
                return cp(1, b"Error opening file for writing: Path not found")
            if self.refuse_overwrite and n in self.fs[d]:
                return cp(1, b"Error opening file for writing: exists")
            with open(src, "rb") as fh:
                self.fs[d][n] = fh.read()
            return cp(0)
        if cmd == "rm":
            d, _, n = argv[0].rpartition("/")
            self.fs.get(d, {}).pop(n, None)
            return cp(0)
        return cp(1, b"?")

    def all_silent(self):
        return bool(self.kwargs) and all(s is True and p is False for s, p in self.kwargs)


class FakeWizard:
    def __init__(self, enabled=True, busy=False, started=True):
        self._enabled = enabled
        self._started = started
        self.offers = []

        class _B:
            def __init__(s, v):
                s._v = v

            def isVisible(s):
                return s._v
        self.bubble = _B(busy)

    def enabled(self):
        return self._enabled

    def offer_sync5_deploy(self, verdict, old, new, image, on_yes, on_no):
        self.offers.append((verdict, old, new, image, on_yes, on_no))
        self.bubble._v = True                           # the bubble is up now


class FakeProc:
    def __init__(self, alive=True):
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


class Host:
    pass


def make(files=None, *, setting="", wizard=None, reachable=(True, ""),
         resolver_version="5.9.2", resolver_fail="", busy=False, denied=False,
         refuse_overwrite=False, defer_run=False, reprobe=None):
    img = FakeImage(files, refuse_overwrite=refuse_overwrite)
    host = Host()
    host._image_write_state = {}
    host._images_held_by_us = {}
    cfg = {SETTING_SYNC5_IMG_AUTODEPLOY: setting}
    toasts, logs, reloads, deferred = [], [], [], []
    tmp = tempfile.NamedTemporaryFile(prefix="sync5_", delete=False)
    tmp.write(dot(resolver_version)); tmp.close()
    pending = []                                        # deferred thread work

    def run_in_thread(fn, on_result, on_error):
        if defer_run:
            pending.append((fn, on_result, on_error))
            return
        try:
            res = fn()
        except Exception as exc:                        # noqa: BLE001
            on_error((type(exc), exc, ""))
            return
        on_result(res)

    def resolve():
        if resolver_fail:
            return None, None, resolver_fail
        return tmp.name, resolver_version, ""

    if busy:
        host._image_write_state["k:img"] = "busy"
    if denied:
        host._image_write_state["k:img"] = "denied"
    host._image_state_key = lambda p: "k:img"
    s5.build_sync5_img_ops(
        host, configuration_dictionary=cfg, execute_hdf_monkey=img.execute,
        resolve_sync5=resolve, run_in_thread=run_in_thread,
        show_toast=lambda title, msg="", **kw: toasts.append((title, msg, kw)),
        add_log=logs.append, wizard=(lambda: wizard),
        image_reload_dir=reloads.append,
        defer=lambda ms, fn: deferred.append((ms, fn)), reprobe=reprobe,
        source_reachable=lambda: reachable, current_version="5.9.2")
    return host, img, toasts, logs, reloads, pending, deferred, tmp.name


def main():
    # ── pure helpers ─────────────────────────────────────────────────────
    check("sync5_blob_version reads the banner", sync5_blob_version(dot("5.9.2")) == "5.9.2")
    check("...and answers '' without one", sync5_blob_version(b"just bytes") == "" and sync5_blob_version(b"") == "")
    check("sync5_version_key", sync5_version_key("5.10.1") == (5, 10, 1) and sync5_version_key("x") == ()
          and sync5_version_key("5.9.2") < sync5_version_key("5.10.0"))
    v = s5.sync5_img_verdict
    check("verdicts: missing/older/current/newer/unknown/unreadable",
          v("5.9.2", False, None) == "missing" and v("5.9.2", True, dot("5.9.1")) == "older"
          and v("5.9.2", True, dot("5.9.2")) == "current" and v("5.9.2", True, dot("6.0.0")) == "newer"
          and v("5.9.2", True, b"old dot, no banner") == "unknown" and v("5.9.2", True, b"") == "unknown"
          and v("5.9.2", True, None) == "unreadable")
    check("parse_ls: [DIR], the bare size hdfmonkey prints, and the [n bytes] spelling",
          s5.parse_ls(b"[DIR]\tsys\n24576\tsync5\n[12 bytes]\tx.bin\n")
          == [("sys", True), ("sync5", False), ("x.bin", False)])
    check("hdfmonkey_message: stdout first, stderr as the fallback",
          s5.hdfmonkey_message(subprocess.CompletedProcess([], 1, b"Error: boom\n", b"")) == "Error: boom"
          and s5.hdfmonkey_message(subprocess.CompletedProcess([], 1, b"", b"se")) == "se"
          and s5.hdfmonkey_message(subprocess.CompletedProcess([], 1, b"", b"")) == "")

    IMG = "C:/imgs/next.img"

    # ── the gate ─────────────────────────────────────────────────────────
    host, img, toasts, logs, reloads, _p, _d, _t = make(setting="false")
    host._sync5_img_check(IMG)
    check("setting off: no hdfmonkey call, no prompt", img.calls == [] and toasts == [] and logs == [])

    wiz = FakeWizard()
    host, img, toasts, logs, reloads, _p, _d, _t = make(wizard=wiz, busy=True)
    host._sync5_img_check(IMG)
    check("image held by an emulator (write-gate cache): skipped with a log line, nothing asked",
          img.calls == [] and wiz.offers == [] and toasts == []
          and len(logs) == 1 and "in use by an emulator" in logs[0], str(logs))

    wiz = FakeWizard()
    host, img, toasts, logs, reloads, _p, _d, _t = make(wizard=wiz)
    host._images_held_by_us["k:img"] = [FakeProc(alive=True)]
    host._sync5_img_check(IMG)
    check("image held by an emulator this app launched (the POSIX signal): skipped too",
          img.calls == [] and wiz.offers == [] and len(logs) == 1 and "in use" in logs[0], str(logs))
    host._images_held_by_us["k:img"] = [FakeProc(alive=False)]
    logs.clear()
    host._sync5_img_check(IMG)
    check("...a launched emulator that exited no longer counts", len(wiz.offers) == 1, str(logs))

    wiz = FakeWizard()
    host, img, toasts, logs, reloads, _p, _d, _t = make(wizard=wiz, denied=True)
    host._sync5_img_check(IMG)
    check("image the app cannot write (DENIED): skipped with a log line, no offer",
          img.calls == [] and wiz.offers == [] and toasts == []
          and len(logs) == 1 and "cannot be written" in logs[0], str(logs))

    # ── verdicts → who asks ──────────────────────────────────────────────
    wiz = FakeWizard()
    host, img, toasts, logs, reloads, _p, _d, _t = make(wizard=wiz)
    host._sync5_img_check(IMG)
    check("no /dot at all: Wizzy offers 'missing'",
          [c[0] for c in img.calls] == ["ls"] and len(wiz.offers) == 1
          and wiz.offers[0][0] == "missing" and wiz.offers[0][2] == "5.9.2"
          and wiz.offers[0][3] == IMG and toasts == [], str((img.calls, wiz.offers, toasts)))
    # a reload while the offer is still open: not asked twice, no toast
    host._sync5_img_check(IMG)
    check("a reload while Wizzy's offer is open asks nothing more",
          len(wiz.offers) == 1 and toasts == [] and [c[0] for c in img.calls] == ["ls"],
          str((wiz.offers, toasts, img.calls)))
    # Yes: resolve, mkdir, put, read back, verify -> green toast + log + /dot reload
    wiz.offers[0][4]()
    check("Yes: mkdir + put + get-back, the dot lands, green toast, log, /dot reloaded",
          img.fs.get("/dot", {}).get("sync5") == dot("5.9.2")
          and [c[0] for c in img.calls[1:]] == ["mkdir", "put", "ls", "get"]
          and len(toasts) == 1 and toasts[0][0] == ".sync5 deployed"
          and toasts[0][2].get("variant") == "green"
          and any("is now in " + IMG + "/dot/sync5" in l for l in logs)
          and reloads == ["/dot"], str((img.calls, toasts, logs, reloads)))
    check("every hdfmonkey call was silent and never prompted for a missing tool", img.all_silent(), str(img.kwargs))
    # ...and a second load of the same image is silent: it is current now.
    wiz.offers.clear(); toasts.clear(); wiz.bubble._v = False
    host._sync5_img_check(IMG)
    check("after the deploy the image reads as current: silent", wiz.offers == [] and toasts == [])

    wiz = FakeWizard()
    host, img, toasts, logs, reloads, _p, _d, _t = make({"/dot/sync5": dot("5.9.1")}, wizard=wiz)
    host._sync5_img_check(IMG)
    check("an older dot: Wizzy offers 'older' with the old version",
          len(wiz.offers) == 1 and wiz.offers[0][:3] == ("older", "5.9.1", "5.9.2"), str(wiz.offers))
    wiz.offers[0][5]()                                  # No
    wiz.offers.clear(); wiz.bubble._v = False
    host._sync5_img_check(IMG)
    check("No is remembered for the image this session", wiz.offers == [] and toasts == [])

    wiz = FakeWizard()
    host, img, toasts, logs, reloads, _p, _d, _t = make({"/dot/sync5": b"ancient dot, no banner"}, wizard=wiz)
    host._sync5_img_check(IMG)
    check("a dot with no banner: offered as 'unknown'",
          len(wiz.offers) == 1 and wiz.offers[0][0] == "unknown", str(wiz.offers))

    wiz = FakeWizard()
    host, img, toasts, logs, reloads, _p, _d, _t = make({"/dot/sync5": dot("5.9.1")}, wizard=wiz)
    img.get_fail = True
    host._sync5_img_check(IMG)
    check("a sync5 hdfmonkey cannot read back: not called old, not offered, logged",
          wiz.offers == [] and toasts == [] and len(logs) == 1
          and "could not read back" in logs[0], str((wiz.offers, toasts, logs)))

    for ver, label in (("5.9.2", "current"), ("6.0.0", "newer")):
        wiz = FakeWizard()
        host, img, toasts, logs, reloads, _p, _d, _t = make({"/dot/sync5": dot(ver)}, wizard=wiz)
        host._sync5_img_check(IMG)
        check(f"a {label} dot: nothing asked, nothing logged",
              wiz.offers == [] and toasts == [] and logs == [], str((wiz.offers, toasts, logs)))

    wiz = FakeWizard()
    host, img, toasts, logs, reloads, _p, _d, _t = make(wizard=wiz, reachable=(False, "offline"))
    host._sync5_img_check(IMG)
    check("the build unobtainable (offline, no checkout): no prompt, one log line naming why",
          wiz.offers == [] and toasts == [] and len(logs) == 1
          and "cannot be obtained" in logs[0] and "offline" in logs[0], str(logs))

    # ── Wizzy busy: the offer WAITS for the wizard ───────────────────────
    wiz = FakeWizard(busy=True)
    host, img, toasts, logs, reloads, _p, deferred, _t = make({"/dot/sync5": dot("5.9.1")}, wizard=wiz)
    host._sync5_img_check(IMG)
    check("wizard talking: no toast, the offer is deferred by one poll",
          toasts == [] and wiz.offers == [] and len(deferred) == 1
          and deferred[0][0] == s5.SYNC5_IMG_WIZARD_POLL_MS, str((toasts, deferred)))
    host._sync5_img_check(IMG)
    check("...a reload while it waits asks nothing more", len(deferred) == 1 and toasts == [])
    wiz.bubble._v = False                               # the bubble closes
    deferred.pop()[1]()
    check("...and once the bubble is gone Wizzy asks", len(wiz.offers) == 1 and toasts == [], str(toasts))

    wiz = FakeWizard(started=False)
    host, img, toasts, logs, reloads, _p, deferred, _t = make(wizard=wiz)
    host._sync5_img_check(IMG)
    check("wizard not started yet (the intro is due): the offer waits rather than get clobbered",
          toasts == [] and wiz.offers == [] and len(deferred) == 1)
    wiz._started = True
    deferred.pop()[1]()
    check("...and asks once startup() ran", len(wiz.offers) == 1 and toasts == [])

    wiz = FakeWizard(busy=True)
    host, img, toasts, logs, reloads, _p, deferred, _t = make(wizard=wiz)
    host._sync5_img_check(IMG)
    n = 0
    while deferred and n < 500:
        deferred.pop()[1]()
        n += 1
    check("a wizard that never frees: the toast asks after the bound, not forever",
          wiz.offers == [] and len(toasts) == 1 and toasts[0][2].get("variant") == "yellow"
          and n == s5.SYNC5_IMG_WIZARD_WAIT_S * 1000 // s5.SYNC5_IMG_WIZARD_POLL_MS,
          str((n, toasts)))

    # ── the toast fallback ───────────────────────────────────────────────
    for wiz, why in ((None, "no wizard"), (FakeWizard(enabled=False), "wizard off")):
        host, img, toasts, logs, reloads, _p, _d, _t = make({"/dot/sync5": dot("5.9.1")}, wizard=wiz)
        host._sync5_img_check(IMG)
        ok = (len(toasts) == 1 and toasts[0][0] == ".sync5 for this disk image"
              and "v5.9.1" in toasts[0][1] and "v5.9.2" in toasts[0][1]
              and "Settings" in toasts[0][1]
              and toasts[0][2].get("variant") == "yellow"
              and toasts[0][2].get("duration_ms") == 15000
              and toasts[0][2].get("action", ("",))[0] == "Deploy now"
              and (wiz is None or wiz.offers == []))
        check(f"{why}: a 15 s yellow toast with a 'Deploy now' action asks instead", ok, str(toasts))
        if ok and why == "wizard off":
            toasts[0][2]["action"][1]()                  # Deploy now
            check("...Deploy now runs the deploy",
                  img.fs["/dot"]["sync5"] == dot("5.9.2") and toasts[-1][0] == ".sync5 deployed",
                  str(toasts[-1]))
    host, img, toasts, logs, reloads, _p, _d, _t = make({"/dot/sync5": dot("5.9.1")}, wizard=None)
    host._sync5_img_check(IMG)
    toasts.clear()
    host._sync5_img_check(IMG)
    check("a toast that was not acted on counts as No for the session", toasts == [])

    # ── the deploy's own failure paths ───────────────────────────────────
    wiz = FakeWizard()
    host, img, toasts, logs, reloads, _p, _d, _t = make({"/dot/sync5": dot("5.9.1")}, wizard=wiz,
                                                        refuse_overwrite=True)
    host._sync5_img_check(IMG)
    wiz.offers[0][4]()
    check("should hdfmonkey refuse to overwrite: rm, then put again, verified (defensive - the real tool overwrites)",
          img.fs["/dot"]["sync5"] == dot("5.9.2")
          and [c[0] for c in img.calls if c[0] in ("put", "rm")] == ["put", "rm", "put"]
          and toasts[-1][0] == ".sync5 deployed", str((img.calls, toasts)))

    wiz = FakeWizard()
    host, img, toasts, logs, reloads, _p, _d, _t = make(wizard=wiz)
    host._sync5_img_check(IMG)
    img.put_fail_always = True
    wiz.offers[0][4]()
    check("a put that keeps failing: red toast + log naming hdfmonkey's STDOUT message, nothing reloaded",
          toasts[-1][0] == ".sync5 deploy failed" and toasts[-1][2].get("variant") == "red"
          and "Disk full" in toasts[-1][1] and reloads == []
          and any("Disk full" in l for l in logs), str(toasts))

    wiz = FakeWizard()
    host, img, toasts, logs, reloads, _p, _d, _t = make(wizard=wiz, resolver_fail="no release, offline")
    host._sync5_img_check(IMG)
    wiz.offers[0][4]()
    check("the resolver failing: red toast with its reason, the image untouched",
          toasts[-1][0] == ".sync5 deploy failed" and "no release, offline" in toasts[-1][1]
          and "/dot" not in img.fs, str((toasts, img.fs)))

    wiz = FakeWizard()
    host, img, toasts, logs, reloads, _p, _d, _t = make(wizard=wiz, resolver_version="5.9.2")
    real_execute = img.execute

    def lying_execute(cmd, image, **kw):            # the card reads back garbage
        res = real_execute(cmd, image, **kw)
        if cmd == "get" and res.returncode == 0:
            with open(kw["extra_argv"][1], "wb") as fh:
                fh.write(b"garbage")
        return res
    s5.build_sync5_img_ops(
        host, configuration_dictionary={SETTING_SYNC5_IMG_AUTODEPLOY: ""},
        execute_hdf_monkey=lying_execute, resolve_sync5=lambda: (_t, "5.9.2", ""),
        run_in_thread=lambda fn, ok, err: ok(fn()),
        show_toast=lambda title, msg="", **kw: toasts.append((title, msg, kw)),
        add_log=logs.append, wizard=lambda: wiz, image_reload_dir=reloads.append,
        source_reachable=lambda: (True, ""), current_version="5.9.2")
    host._sync5_img_deploy(IMG, "missing")
    check("a read-back without the banner: red toast, deploy reported failed",
          toasts[-1][0] == ".sync5 deploy failed" and "banner" in toasts[-1][1], str(toasts[-1]))

    host, img, toasts, logs, reloads, _p, _d, _t = make(wizard=FakeWizard())
    host._image_write_state["k:img"] = "busy"
    host._sync5_img_deploy(IMG, "missing")
    check("deploy refused while the image is held by an emulator: red toast, no write",
          toasts[-1][0] == ".sync5 for this disk image" and toasts[-1][2].get("variant") == "red"
          and img.calls == [], str((toasts, img.calls)))

    # Yes clicked minutes after the load: the image is RE-PROBED first, and
    # an emulator that mounted it meanwhile refuses the write.
    wiz = FakeWizard()
    probes = []
    host, img, toasts, logs, reloads, _p, _d, _t = make(wizard=wiz,
                                                        reprobe=lambda p: (probes.append(p), False)[1])
    host._sync5_img_check(IMG)
    wiz.offers[0][4]()
    check("Yes re-probes the image; a holder that arrived since the load refuses the write",
          probes == [IMG] and toasts[-1][2].get("variant") == "red"
          and not any(c[0] in ("put", "mkdir") for c in img.calls), str((probes, toasts, img.calls)))

    # ── in-flight guard ──────────────────────────────────────────────────
    wiz = FakeWizard()
    host, img, toasts, logs, reloads, pending, _d, _t = make(wizard=wiz, defer_run=True)
    host._sync5_img_check(IMG)
    host._sync5_img_check(IMG)
    check("a second check while the first is in flight is ignored", len(pending) == 1)
    fn, on_result, _e = pending.pop()
    on_result(fn())
    check("...and the deferred result still asks", len(wiz.offers) == 1)

    print("\nRESULT:", "ALL PASS" if not FAIL else f"FAILURES: {FAIL}")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
