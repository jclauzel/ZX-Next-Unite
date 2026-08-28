"""The ZxNextRemote PowerShell module (extra/ZxNextRemote/) and
PS-Send-ToNext.ps1, tested against REAL NextSyncHttpBridge instances over
fake adapters - the same adapter-level seam test_http_bridge.py's token/OSP
phases use, so the module is exercised against the actual bridge code
(routing, JSON shapes, the two distinct 401s), not against a mock of it.

Four bridges:
  PLAIN  - two-seat roster (sids 1/2, distinct in-memory filesystems),
           session-header routing, every op verb.
  TOKEN  - bearer-token protected (the TokenRequired 401).
  OSP    - writes answer 401 os-protected, rmtree 501, ren 503, and a
           'slow' /free that outlasts a 1 s client timeout (OsProtected /
           Unsupported / NoNextConnected / Timeout classification).
  (dead) - a port nothing listens on (BridgeUnreachable).

The PS driver (test_powershell_module.ps1) runs under EVERY PowerShell
found - Windows PowerShell 5.1 (the documented floor) and pwsh 7 (what
mac/linux users run). PS-Send-ToNext.ps1 is then exercised end-to-end under
the first shell: plain push+verify+forceexit, the three -autoexec actions,
and the exit-2 token path.

Skips cleanly when Flask or every PowerShell is missing.
Run with: python test_powershell_module.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

PLAIN_PORT = 18590
TOKEN_PORT = 18591
OSP_PORT = 18592
DEAD_PORT = 18593
TOKEN = "t0ken-t0ken-t0ken"

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        ok = False


def skip(msg):
    print("SKIP:", msg)
    sys.exit(0)


try:
    import flask  # noqa: F401
except ImportError:
    skip("flask not installed - the HTTP bridge cannot start")

from zxnu_http_bridge import NextSyncHttpBridge  # noqa: E402
from zxnu_workers import RE_OSP_ERROR            # noqa: E402


# ---------------------------------------------------------------------------
# Fake adapters. Result-dict dialect exactly as the bridge's routes read it
# (see zxnu_http_bridge._install_routes): ls -> entries[(dir,size,name)],
# get -> data, drives -> current+letters, free -> free, rcpy -> files,
# rfsize -> files/dirs/bytes; errors -> {"ok": False, "http": N, "error": s}.
# ---------------------------------------------------------------------------
class SeatFs:
    """One seat's SD card: dirs + path->bytes files, '/' separated,
    case-preserving. A leading 'c:' drive prefix is accepted and dropped -
    the same forgiving path handling the Next-side commands have."""

    def __init__(self, files):
        self.files = dict(files)
        self.dirs = {"/", "/games", "/incoming", "/nextzxos", "/home"}

    @staticmethod
    def norm(path):
        p = path.replace("\\", "/")
        if len(p) >= 2 and p[1] == ":":
            p = p[2:]
        if not p.startswith("/"):
            p = "/" + p
        while "//" in p:
            p = p.replace("//", "/")
        return p if p == "/" else p.rstrip("/")

    def ls(self, path):
        base = self.norm(path)
        prefix = "/" if base == "/" else base + "/"
        seen, out = set(), []
        for d in sorted(self.dirs):
            if d != base and d.startswith(prefix) and "/" not in d[len(prefix):]:
                out.append((True, 0, d[len(prefix):]))
                seen.add(d[len(prefix):])
        for f, data in sorted(self.files.items()):
            if f.startswith(prefix) and "/" not in f[len(prefix):]:
                out.append((False, len(data), f[len(prefix):]))
        if base not in self.dirs and base not in ("/",):
            return None
        return out

    def run(self, op, a1="", a2="", body=None):
        if op == "ls":
            entries = self.ls(a1)
            if entries is None:
                return {"ok": False, "http": 502, "error": f"no such dir {a1}"}
            return {"ok": True, "entries": entries}
        if op == "get":
            p = self.norm(a1)
            if p in self.dirs:
                return {"ok": False, "http": 400, "error": f"{a1} is a folder"}
            if p not in self.files:
                return {"ok": False, "http": 502, "error": f"no such file {a1}"}
            return {"ok": True, "data": self.files[p]}
        if op == "put":
            self.files[self.norm(a1)] = bytes(body or b"")
            return {"ok": True}
        if op == "mkdir":
            self.dirs.add(self.norm(a1))
            return {"ok": True}
        if op == "rmdir":
            self.dirs.discard(self.norm(a1))
            return {"ok": True}
        if op == "rmtree":
            base = self.norm(a1)
            self.dirs = {d for d in self.dirs if d != base and not d.startswith(base + "/")}
            self.files = {f: d for f, d in self.files.items()
                          if not f.startswith(base + "/")}
            return {"ok": True}
        if op == "rm":
            p = self.norm(a1)
            if p not in self.files:
                return {"ok": False, "http": 502, "error": f"no such file {a1}"}
            del self.files[p]
            return {"ok": True}
        if op == "ren":
            src, dst = self.norm(a1), self.norm(a2)
            if src not in self.files:
                return {"ok": False, "http": 502, "error": f"no such file {a1}"}
            if dst in self.files:
                return {"ok": False, "http": 502, "error": f"{a2} exists"}
            self.files[dst] = self.files.pop(src)
            return {"ok": True}
        if op == "rcpy":
            src = self.norm(a1)
            hits = [f for f in self.files if f == src or f.startswith(src + "/")]
            if not hits:
                return {"ok": False, "http": 502, "error": f"no such path {a1}"}
            return {"ok": True, "files": len(hits)}
        if op == "rfsize":
            base = self.norm(a1)
            hits = {f: d for f, d in self.files.items()
                    if f == base or f.startswith(base + "/")}
            return {"ok": True, "files": len(hits), "dirs": 1,
                    "bytes": sum(len(d) for d in hits.values())}
        if op == "free":
            if a1 == "slow":
                time.sleep(3)      # outlast the driver's 1 s client timeout
            return {"ok": True, "free": 1048576, "drive": a1 or "C"}
        if op == "drives":
            return {"ok": True, "current": "C", "letters": ["C", "M"]}
        if op == "forceexit":
            return {"ok": True}
        return {"ok": False, "http": 501, "error": f"unsupported op {op}"}


class PlainAdapter:
    """Two seats with distinct filesystems; a stale sid answers 410."""

    def __init__(self):
        self.seats = {
            1: SeatFs({"/one.txt": b"seat one", "/games/boot.bas": b"ten chars!" * 5}),
            2: SeatFs({"/two.txt": b"seat two"}),
        }
        self.forceexits = 0

    def state(self):
        return {"listening": True, "connected": True,
                "current": "C", "drives": ["C", "M"]}

    def roster(self):
        return (1, [(1, "10.0.0.185", "Next"), (2, "10.0.0.42", "N-Go")], 4)

    def run(self, op, a1="", a2="", body=None, timeout=None, session=None):
        if op == "forceexit":
            self.forceexits += 1     # a broadcast: no per-seat routing
            return {"ok": True}
        sid = 1 if session is None else session
        seat = self.seats.get(sid)
        if seat is None:
            return {"ok": False, "http": 410, "error": f"session {sid} is gone"}
        return seat.run(op, a1, a2, body=body)


class OspAdapter:
    """The OTHER 401: reads work, writes are os-protected. Plus one verb per
    remaining classification - rmtree 501, ren 503, free('slow') hangs."""

    def state(self):
        return {"listening": True, "connected": True,
                "current": "C", "drives": ["C"]}

    def run(self, op, a1="", a2="", body=None, timeout=None, session=None):
        if op in ("put", "mkdir", "rmdir", "rm", "rcpy"):
            return {"ok": False, "http": 401, "error": RE_OSP_ERROR}
        if op == "rmtree":
            return {"ok": False, "http": 501, "error": "rmtree unsupported here"}
        if op == "ren":
            return {"ok": False, "http": 503, "error": "no next connected"}
        if op == "free":
            if a1 == "slow":
                time.sleep(3)
            return {"ok": True, "free": 4096, "drive": a1 or "C"}
        return {"ok": True}


def find_shells():
    """Every PowerShell on this machine: (label, exe). Windows PowerShell
    5.1 is the module's documented floor, pwsh 7 what mac/linux users run -
    test both when both exist."""
    shells = []
    for label, exe in (("powershell-5.1", "powershell"), ("pwsh-7", "pwsh")):
        path = shutil.which(exe)
        if path:
            shells.append((label, path))
    return shells


def run_ps(exe, script, args, timeout=300):
    cmd = [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script] + args
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def main():
    shells = find_shells()
    if not shells:
        skip("no PowerShell found (powershell/pwsh)")

    module = os.path.join(REPO, "extra", "ZxNextRemote", "ZxNextRemote.psd1")
    driver = os.path.join(HERE, "test_powershell_module.ps1")
    sender = os.path.join(REPO, "extra", "PS-Send-ToNext.ps1")
    check("module manifest exists", os.path.isfile(module), module)
    check("driver exists", os.path.isfile(driver), driver)
    check("PS-Send-ToNext.ps1 exists", os.path.isfile(sender), sender)

    plain = PlainAdapter()
    bridges = [
        NextSyncHttpBridge(plain, port=PLAIN_PORT),
        NextSyncHttpBridge(PlainAdapter(), port=TOKEN_PORT, auth_token=TOKEN),
        NextSyncHttpBridge(OspAdapter(), port=OSP_PORT),
    ]
    for b in bridges:
        okd, err = b.start()
        check(f"bridge on {b.port} started", okd, err)

    tmp = tempfile.mkdtemp(prefix="zxnr-psmod-")
    try:
        # ---- the module driver, under every shell found -----------------
        for label, exe in shells:
            print(f"\n=== module driver under {label} ===")
            r = run_ps(exe, driver, [
                "-ModulePath", module,
                "-PlainPort", str(PLAIN_PORT), "-TokenPort", str(TOKEN_PORT),
                "-OspPort", str(OSP_PORT), "-DeadPort", str(DEAD_PORT),
                "-Token", TOKEN, "-TmpDir", tmp])
            sys.stdout.write(r.stdout)
            if r.stderr.strip():
                print("stderr:", r.stderr.strip()[:2000])
            check(f"{label}: driver all-pass", r.returncode == 0,
                  f"exit {r.returncode}")

        # ---- PS-Send-ToNext.ps1 end-to-end, first shell -----------------
        label, exe = shells[0]
        print(f"\n=== PS-Send-ToNext.ps1 under {label} ===")
        build = os.path.join(tmp, "game.nex")
        payload = bytes(range(256)) * 64
        with open(build, "wb") as f:
            f.write(payload)
        cfgfile = os.path.join(tmp, "send.cfg")
        with open(cfgfile, "w") as f:
            f.write(f"bridge_ip = 127.0.0.1\nbridge_port = {PLAIN_PORT}\n"
                    f"file = {build}\nremote_path = /home/incoming.nex\n"
                    "forceexit_after_send = yes\nwait_timeout = 15\n")

        seat1 = plain.seats[1]
        # The module drivers above each broadcast one /forceexit against
        # this same adapter - count DELTAS from here, not absolutes.
        base = plain.forceexits
        r = run_ps(exe, sender, ["-Config", cfgfile])
        check("push: exit 0", r.returncode == 0,
              f"exit {r.returncode}\n{r.stdout[-1500:]}\n{r.stderr[-500:]}")
        check("push: the exact bytes landed",
              seat1.files.get("/home/incoming.nex") == payload)
        check("push: forceexit was broadcast", plain.forceexits == base + 1,
              plain.forceexits - base)
        check("push: reported verified", "SENT AND VERIFIED" in r.stdout)

        # -autoexec:Deploy - installs extra/autoexec.bas, does NOT push
        with open(os.path.join(REPO, "extra", "autoexec.bas"), "rb") as f:
            autoexec_bytes = f.read()
        r = run_ps(exe, sender, ["-Config", cfgfile, "-autoexec", "Deploy"])
        check("deploy: exit 0", r.returncode == 0,
              f"exit {r.returncode}\n{r.stdout[-1500:]}")
        check("deploy: autoexec.bas landed verified",
              seat1.files.get("/nextzxos/autoexec.bas") == autoexec_bytes)
        check("deploy: alone means NO push (forceexit count unchanged)",
              plain.forceexits == base + 1, plain.forceexits - base)

        r = run_ps(exe, sender, ["-Config", cfgfile, "-autoexec", "Deploy"])
        check("deploy again: idempotent, exit 0",
              r.returncode == 0 and "already deployed" in r.stdout,
              r.stdout[-400:])

        # Deploy over a parked-only state: sends the fresh loop AND removes
        # the stale parked copy (the script's own artifact), so a later
        # -autoexec:Off can never hit the both-files-exist refusal.
        r = run_ps(exe, sender, ["-Config", cfgfile, "-autoexec", "Off"])
        check("park before re-deploy", r.returncode == 0
              and "/nextzxos/autoexec_.bas" in seat1.files)
        r = run_ps(exe, sender, ["-Config", cfgfile, "-autoexec", "Deploy"])
        check("deploy over a parked copy: live restored, parked removed",
              r.returncode == 0
              and seat1.files.get("/nextzxos/autoexec.bas") == autoexec_bytes
              and "/nextzxos/autoexec_.bas" not in seat1.files,
              sorted(f for f in seat1.files if "nextzxos" in f))

        r = run_ps(exe, sender, ["-Config", cfgfile, "-autoexec", "Off"])
        check("off: parked to autoexec_.bas", r.returncode == 0
              and "/nextzxos/autoexec_.bas" in seat1.files
              and "/nextzxos/autoexec.bas" not in seat1.files,
              sorted(f for f in seat1.files if "nextzxos" in f))
        r = run_ps(exe, sender, ["-Config", cfgfile, "-autoexec", "Off"])
        check("off again: idempotent", r.returncode == 0
              and "already Off" in r.stdout, r.stdout[-400:])

        r = run_ps(exe, sender, ["-Config", cfgfile, "-autoexec", "On"])
        check("on: renamed back to autoexec.bas", r.returncode == 0
              and "/nextzxos/autoexec.bas" in seat1.files
              and "/nextzxos/autoexec_.bas" not in seat1.files,
              sorted(f for f in seat1.files if "nextzxos" in f))
        r = run_ps(exe, sender, ["-Config", cfgfile, "-autoexec", "On"])
        check("on again: idempotent", r.returncode == 0
              and "already On" in r.stdout, r.stdout[-400:])

        # -autoexec + -AndSend pushes too
        r = run_ps(exe, sender, ["-Config", cfgfile, "-autoexec", "On", "-AndSend"])
        check("-AndSend: autoexec action AND a push", r.returncode == 0
              and plain.forceexits == base + 2,
              (r.returncode, plain.forceexits - base))

        # exit 2: token-protected bridge, no token in the cfg
        cfg2 = os.path.join(tmp, "send-tok.cfg")
        with open(cfg2, "w") as f:
            f.write(f"bridge_ip = 127.0.0.1\nbridge_port = {TOKEN_PORT}\n"
                    f"file = {build}\nwait_timeout = 10\n")
        r = run_ps(exe, sender, ["-Config", cfg2])
        check("token refusal: exit 2 (immediately, not a poll-for-ever)",
              r.returncode == 2, f"exit {r.returncode}\n{r.stdout[-600:]}")
    finally:
        for b in bridges:
            b.stop()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
