"""Offline-tolerance tests for zxnu_network.py (no real sockets).

* probe_online: True on a reachable host, False when every connect
  raises — and it must never raise itself.
* detect_local_ipv4: fully offline (resolver AND UDP connect failing)
  returns blanks instead of the startup crash a Linux box with no
  network route used to hit in nextsync_show_ip_info.
* NetworkWatcher: emits online_changed on transitions only (first probe
  included), is_online() stays optimistic until an outage is CONFIRMED.
* build_network_watch: yellow toast on outage, green toast + a re-run of
  on_tab_changed for the current tab on recovery, host._network_online
  gate exposed.

Run with: python tests/test_network.py
"""
import os
import socket
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QWidget  # noqa: E402
app = QApplication.instance() or QApplication([])

import zxnu_network as zn  # noqa: E402

FAIL = []
def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


# ── probe_online ─────────────────────────────────────────────────────────
class _FakeSock:
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False

_real_create = socket.create_connection
socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(OSError("down"))
check("probe_online is False when every connect fails and never raises",
      zn.probe_online() is False)
socket.create_connection = lambda *a, **k: _FakeSock()
check("probe_online is True when a host answers", zn.probe_online() is True)
socket.create_connection = _real_create

# ── detect_local_ipv4 fully offline ──────────────────────────────────────
_real_ghbne = socket.gethostbyname_ex
_real_socket = socket.socket
socket.gethostbyname_ex = lambda *_a: (_ for _ in ()).throw(
    socket.gaierror("no resolver"))

class _DeadUdp:
    def __init__(self, *a, **k):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def connect(self, *_a):
        raise OSError(101, "Network is unreachable")

socket.socket = _DeadUdp
try:
    result = zn.detect_local_ipv4()
except Exception as ex:      # noqa: BLE001 - the whole point of the test
    result = f"RAISED {ex!r}"
finally:
    socket.gethostbyname_ex = _real_ghbne
    socket.socket = _real_socket
check("detect_local_ipv4 survives a machine with no network at all",
      result == ("", [], [], None), str(result))

# ── NetworkWatcher transitions ───────────────────────────────────────────
_probes = []
_real_probe = zn.probe_online
zn.probe_online = lambda *a, **k: _probes.pop(0)

def _spin(watcher):
    t0 = time.time()
    while watcher._probing and time.time() - t0 < 5:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()

w = zn.NetworkWatcher(interval_ms=3600_000)
emitted = []
w.online_changed.connect(emitted.append)
check("watcher is optimistic before the first probe", w.is_online())
_probes[:] = [False]
w.check_now(); _spin(w)
check("first probe: confirmed outage emitted and gates",
      emitted == [False] and not w.is_online())
_probes[:] = [False]
w.check_now(); _spin(w)
check("steady state does not re-emit", emitted == [False])
_probes[:] = [True]
w.check_now(); _spin(w)
check("recovery emits True and re-opens the gate",
      emitted == [False, True] and w.is_online())
_probes[:] = [False]
w.check_now(); _spin(w)
check("a later loss emits again", emitted == [False, True, False])

# ── build_network_watch wiring ───────────────────────────────────────────
class StubHost(QMainWindow):
    pass

host = StubHost()
tabs = QTabWidget(host)
tabs.addTab(QWidget(), "🌍 GetIt")
host._tab_widget = tabs
toasts = []
host._show_toast = lambda title, message="", **kw: toasts.append(
    (kw.get("variant", "green"), title))
refreshed = []

_probes[:] = [False]
watch = zn.build_network_watch(host, on_tab_changed=refreshed.append)
_spin(watch)
check("outage shows the yellow advisory",
      toasts and toasts[-1][0] == "yellow")
check("the gate is exposed on the host and closed",
      host._network_online() is False)
_probes[:] = [True]
watch.check_now(); _spin(watch)
check("recovery shows the green toast",
      toasts[-1][0] == "green")
check("recovery re-runs the current tab's activation",
      refreshed == [tabs.currentIndex()])
check("the gate re-opened", host._network_online() is True)

zn.probe_online = _real_probe

print()
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILURE(S)")
    sys.exit(1)
print("RESULT: ALL NETWORK CHECKS PASSED")
