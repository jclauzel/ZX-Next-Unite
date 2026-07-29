"""zxnu_network.py — offline-tolerant network state for the app.

The app must start and stay usable with no Internet at all (emulators,
hdfmonkey and the SD-card tools are fully local): a Linux box without a
network route used to crash at startup inside nextsync_show_ip_info's raw
``connect(("8.8.8.8", 80))``. This module centralises every "is the
network there?" concern:

* probe_online()      — a short TCP probe against public DNS anycast
                        addresses; never raises.
* detect_local_ipv4() — the NextSync tab's best-effort local address
                        detection; never raises (offline returns blanks).
* NetworkWatcher      — a QObject polling probe_online() on a background
                        thread every 30 s (non-blocking for the UI) and
                        emitting online_changed(bool) on TRANSITIONS only.
* build_network_watch(host, ...) — wires the watcher to the MainWindow:
  a yellow toast when the network is missing/lost (features degrade but
  emulators still work), a green toast + a re-run of on_tab_changed for
  the current tab when it returns (so the online tabs' skipped
  auto-fetches fire again), and host._network_online — the gate
  zxnu_tab_ops consults before letting GetIt/ZXDB/zxArt reach out.

Tested by tests/test_network.py (probes and watcher transitions are
exercised with a patched probe — no real sockets).
"""
from __future__ import annotations

import logging
import socket
import threading

from PySide6.QtCore import QObject, QTimer, Signal

from zxnu_i18n import ui_tr_now

# Public anycast DNS endpoints: connecting (TCP, port 53) succeeds iff the
# machine has a working route to the Internet. Two providers so a single
# unreachable host can't fake an outage.
PROBE_HOSTS = (("8.8.8.8", 53), ("1.1.1.1", 53))
PROBE_TIMEOUT_S = 3.0
POLL_INTERVAL_MS = 30_000


def probe_online(hosts=PROBE_HOSTS, timeout=PROBE_TIMEOUT_S):
    """True when the Internet is reachable. Never raises."""
    for host_port in hosts:
        try:
            with socket.create_connection(host_port, timeout=timeout):
                return True
        except OSError:
            continue
    return False


def detect_local_ipv4():
    """Best-effort local address info for the NextSync tab: returns
    (hostname, aliases, ips, primary_ip_or_None) and NEVER raises — with
    no network every field simply comes back empty."""
    try:
        hostname, aliases, ips = socket.gethostbyname_ex(socket.gethostname())
    except OSError:
        hostname, aliases, ips = "", [], []
    primary = None
    if not ips or len(ips) > 1 or ips[0].startswith("127"):
        # Ambiguous or loopback-only: learn the outbound interface's
        # address from a UDP "connect" (no packet is sent, but a machine
        # with no route raises Network is unreachable — the old crash).
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                primary = s.getsockname()[0]
        except OSError:
            primary = None
    return hostname, aliases, ips, primary


class NetworkWatcher(QObject):
    """Polls probe_online() off the UI thread; emits on state changes.

    The very first probe also emits (previous state "unknown"), so the
    startup no-network advisory rides the same signal.
    """

    online_changed = Signal(bool)
    _probe_done = Signal(bool)          # worker thread -> UI thread

    def __init__(self, parent=None, interval_ms=POLL_INTERVAL_MS):
        super().__init__(parent)
        self._online = None             # unknown until the first probe
        self._probing = False
        self._probe_done.connect(self._on_probe_done)
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.check_now)

    def is_online(self):
        """Optimistic while unknown: only a CONFIRMED outage gates."""
        return self._online is not False

    def start(self):
        self._timer.start()
        self.check_now()

    def check_now(self):
        if self._probing:
            return
        self._probing = True

        def _run(sig=self._probe_done):
            try:
                sig.emit(probe_online())
            except Exception:           # belt and braces: never kill the app
                logging.exception("network probe failed unexpectedly")
                sig.emit(False)

        threading.Thread(target=_run, daemon=True,
                         name="zxnu-network-probe").start()

    def _on_probe_done(self, online):
        self._probing = False
        previous, self._online = self._online, bool(online)
        if previous != self._online:
            self.online_changed.emit(self._online)


def build_network_watch(host, *, on_tab_changed):
    """Create + start the watcher on *host* (the MainWindow) and wire the
    degrade/restore behaviour."""
    watcher = NetworkWatcher(host)
    host._network = watcher
    host._network_online = watcher.is_online
    state = {"first": True}

    def _changed(online):
        first, state["first"] = state["first"], False
        if online:
            if first:
                return              # normal startup: no toast needed
            host._show_toast(
                ui_tr_now("✅  Network restored"),
                ui_tr_now("Online features are back."),
                variant="green")
            # Re-run the current tab's activation side effects so the
            # auto-fetch that was skipped while offline fires now.
            try:
                tabw = host._tab_widget
                on_tab_changed(tabw.currentIndex())
            except Exception:
                logging.exception("network restore: tab refresh failed")
        else:
            host._show_toast(
                ui_tr_now("⚠  No network connection"),
                ui_tr_now("Online features are paused until the "
                          "connection returns — emulators and the "
                          "SD Card tools still work."),
                variant="yellow")

    watcher.online_changed.connect(_changed)
    watcher.start()
    return watcher
