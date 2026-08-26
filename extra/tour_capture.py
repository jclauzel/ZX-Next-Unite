"""Drive the demo-environment app through the tour and grab the GIF frames.

Runs the REAL Qt platform (retro pygame panes crash under offscreen), so a
window appears during capture. Host identity is masked: socket.gethostname /
gethostbyname_ex are patched before the app imports, and detect_local_ipv4
is re-patched in the loaded modules right before the NextSync server starts.
"""
import os
import runpy
import socket
import sys
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = r"C:\Users\Public\ZX-Next-Unite-demo"
import tempfile
WORK = os.environ.get("ZXNU_TOUR_WORK") or os.path.join(tempfile.gettempdir(), "zxnu-tour")
OUT = os.path.join(WORK, "tour_frames")
STATUS = os.path.join(WORK, "capture_status.txt")
os.makedirs(OUT, exist_ok=True)
for f in os.listdir(OUT):
    os.remove(os.path.join(OUT, f))

MASK_HOST = "<your PC name>"
MASK_IPS = ["<your LAN address 1>", "<your LAN address 2>"]
MASK_PRIMARY = "<your primary IP>"
socket.gethostname = lambda: MASK_HOST
socket.gethostbyname_ex = lambda name=None: (MASK_HOST, [], list(MASK_IPS))

sys.path.insert(0, REPO)
from PySide6.QtCore import QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox  # noqa: E402

FRAME_MS = 140
FRAMES = 16

def log(msg):
    with open(STATUS, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")

def fake_ipv4():
    return (MASK_HOST, [], list(MASK_IPS), MASK_PRIMARY)

# Patch it at the SOURCE, before the app imports anything.
#
# The socket patches above only cover the host name and the addresses
# gethostbyname_ex reports; detect_local_ipv4 works the PRIMARY address out
# by opening a UDP socket and reading getsockname(), which no socket patch
# here touches. The orchestrator's own patch_ip step (below) is too late for
# it: the NextSync tab prints its host/IP banner once at STARTUP, so the real
# address was already in the captured log — "Primary IP: 10.0.0.31" shipped
# in the frames (caught while regenerating the 9.6.0 GIF). Rebinding it on
# zxnu_network here means every `from zxnu_network import detect_local_ipv4`
# in the app binds the fake instead.
import zxnu_network                       # noqa: E402
zxnu_network.detect_local_ipv4 = fake_ipv4

_orig_exec = QApplication.exec

def orchestrate():
    app = QApplication.instance()
    win = next(w for w in app.topLevelWidgets() if isinstance(w, QMainWindow))
    tab = win._tab_widget

    def tab_index(fragment):
        for i in range(tab.count()):
            if fragment.lower() in tab.tabText(i).lower():
                return i
        return -1

    # Auto-accept any modal (the "Yes, start NextSync server" prompt etc.).
    def kill_modals():
        for w in app.topLevelWidgets():
            if isinstance(w, QMessageBox) and w.isVisible():
                btn = w.defaultButton() or (w.buttons()[0] if w.buttons() else None)
                log(f"modal: {w.windowTitle()!r} -> clicking {btn.text() if btn else 'accept'}")
                if btn is not None:
                    btn.click()
                else:
                    w.accept()
    killer = QTimer(win)
    killer.timeout.connect(kill_modals)
    killer.start(400)

    steps = []          # (delay_ms_after_previous, fn)
    def step(delay, fn, name=""):
        steps.append((delay, fn, name))

    seg_counter = {"n": 0}
    def capture_segment(seg_name):
        """Queue FRAMES grabs, FRAME_MS apart, as one segment."""
        seg = seg_counter["n"]
        seg_counter["n"] += 1
        for i in range(FRAMES):
            def grab(seg=seg, i=i, seg_name=seg_name):
                pix = win.grab()
                pix.save(os.path.join(OUT, f"seg{seg}_{seg_name}_{i:02d}.png"))
            step(FRAME_MS, grab, f"grab {seg_name} {i}")

    # --- the tour ----------------------------------------------------------
    step(400, lambda: win.resize(1500, 950), "resize")
    step(9000, lambda: tab.setCurrentIndex(tab_index("SD Card")), "SD tab")
    step(1200, lambda: None, "settle")
    capture_segment("sdcard")

    def patch_ip():
        for mod in ("zxnu_network", "zxnu_nextsync_ops", "zxnu_nextsync_pane",
                    "zxnu_main"):
            m = sys.modules.get(mod)
            if m is not None and hasattr(m, "detect_local_ipv4"):
                m.detect_local_ipv4 = fake_ipv4
    # Mask BEFORE entering the tab: switching to NextSync auto-runs the
    # prepare/perform-checks (host/IP info + the "Ready to sync" scan), so
    # the patch must already be in place — and no explicit prepare click is
    # needed (it would just duplicate the scan block in the log).
    step(300, patch_ip, "mask IPs")
    step(300, lambda: tab.setCurrentIndex(tab_index("NextSync")), "NextSync tab")
    step(600, lambda: win.nextsync_mode_tabs.setCurrentIndex(1), "classic view")
    step(4000, lambda: None, "server log settles")
    capture_segment("nextsync_classic")

    step(600, lambda: win.nextsync_mode_tabs.setCurrentIndex(0), "remote explorer")
    step(1800, lambda: None, "RE settles")
    capture_segment("nextsync_re")

    step(300, lambda: tab.setCurrentIndex(tab_index("GetIt")), "GetIt tab")
    step(1000, lambda: win.getit_on_latest(), "GetIt latest")
    step(9000, lambda: None, "GetIt thumbnails")
    capture_segment("getit")

    step(300, lambda: tab.setCurrentIndex(tab_index("ZXArt")), "zxArt tab")
    step(1000, lambda: win.zxart_on_latest(), "zxArt latest")
    step(9000, lambda: None, "zxArt thumbnails")
    capture_segment("zxart")

    step(300, lambda: tab.setCurrentIndex(tab_index("ZXDB")), "ZXDB tab")
    step(1000, lambda: win.zxdb_on_latest(), "ZXDB latest")
    step(9000, lambda: None, "ZXDB thumbnails")
    capture_segment("zxdb")

    step(300, lambda: tab.setCurrentIndex(tab_index("Unite")), "Unite tab")
    step(1000, lambda: win._allinone_on_latest(), "Unite latest")
    step(12000, lambda: None, "Unite merge")
    capture_segment("unite")

    step(300, lambda: tab.setCurrentIndex(tab_index("Floyd")), "Alien Floyd's tab")
    step(2500, lambda: None, "attract mode")
    capture_segment("alienfloyds")

    step(500, app.quit, "quit")

    def run_next(idx=0):
        if idx >= len(steps):
            return
        delay, fn, name = steps[idx]
        def fire():
            try:
                if name and not name.startswith("grab"):
                    log(f"step: {name}")
                fn()
            except Exception:
                log(f"STEP FAILED: {name}\n" + traceback.format_exc())
            run_next(idx + 1)
        QTimer.singleShot(delay, fire)
    run_next()

def patched_exec(*args, **kwargs):
    # Called as app.exec() — PySide's exec takes no arguments, drop `self`.
    QTimer.singleShot(600, lambda: _try(orchestrate))
    return _orig_exec()

def _try(fn):
    try:
        fn()
    except Exception:
        log("ORCHESTRATE FAILED\n" + traceback.format_exc())
        QApplication.instance().quit()

QApplication.exec = patched_exec

open(STATUS, "w").write("capture starting\n")
script = os.path.join(DEMO, "zx-next-unite.py")
sys.argv = [script]
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit:
    pass
log(f"done, frames: {len(os.listdir(OUT))}")
print("frames captured:", len(os.listdir(OUT)))
