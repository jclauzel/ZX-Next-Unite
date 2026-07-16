"""Background worker, signal and progress-dialog classes for zx-next-unite.

Extracted from zx-next-unite.py."""

import errno
import os
import queue
import socket
import threading
import time
from PySide6.QtCore import (
    QObject, QPoint, QRect, QRunnable, QSize, QSortFilterProxyModel, QTimer,
    Qt, Signal, Slot,
)
from PySide6.QtGui import QFontInfo
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLayout, QProgressBar, QPushButton, QVBoxLayout,
)


class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto a new row when the available width
    runs out, instead of squeezing items past their minimum size.

    A plain ``QHBoxLayout`` toolbar overlaps its widgets when the window is made
    narrower than the row's combined minimum width: the box layout shrinks each
    item's allocated slot below its minimum, but ``QWidget.setGeometry`` clamps
    the widget back up to its minimum, so neighbours get drawn on top of each
    other (e.g. the Search button overlapping the search box).  Wrapping avoids
    that entirely -- items that no longer fit move to the next row.

    Adapted from the Qt "Flow Layout" example, with two additions used by the
    search/toolbar rows:

    * hidden widgets (``item.isEmpty()``) reserve no space, and
    * any item whose horizontal size policy is Expanding/MinimumExpanding grows
      to share the leftover width on its row -- the flow-layout equivalent of a
      ``QBoxLayout`` stretch factor, so a search input can still fill the bar.
    """

    def __init__(self, parent=None, margin=0, hspacing=6, vspacing=4):
        super().__init__(parent)
        self._items = []
        self._hspace = hspacing
        self._vspace = vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    # --- QLayout plumbing -------------------------------------------------
    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if item.isEmpty():
                continue
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # --- layout core ------------------------------------------------------
    @staticmethod
    def _expanding(item):
        return bool(item.expandingDirections() & Qt.Horizontal)

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        y = area.y()
        line = []          # [(item, width, height), ...] for the current row
        line_w = 0         # widths + interior spacing accumulated so far

        def flush(line, y):
            if not line:
                return 0
            used = sum(w for _, w, _h in line) + self._hspace * (len(line) - 1)
            extra = max(0, area.width() - used)
            growers = [t for t in line if self._expanding(t[0])]
            per = extra // len(growers) if growers else 0
            x = area.x()
            line_h = 0
            for it, w, h in line:
                ww = w + (per if self._expanding(it) else 0)
                if not test_only:
                    it.setGeometry(QRect(QPoint(x, y), QSize(ww, h)))
                x += ww + self._hspace
                line_h = max(line_h, h)
            return line_h

        for item in self._items:
            if item.isEmpty():            # hidden widget -> no space reserved
                continue
            hint = item.sizeHint()
            w, h = hint.width(), hint.height()
            projected = line_w + (self._hspace if line else 0) + w
            if line and projected > area.width():
                y += flush(line, y) + self._vspace
                line, line_w = [], 0
            line.append((item, w, h))
            line_w += (self._hspace if len(line) > 1 else 0) + w
        y += flush(line, y)
        return y - rect.y() + m.bottom()


def is_address_in_use(ex):
    """True when an OSError from ``bind()`` means the TCP port is already taken.

    Covers Windows (WSAEADDRINUSE 10048, and WSAEACCES 10013 which is what an
    exclusive-use bind raises against a port another socket already holds) and
    POSIX (EADDRINUSE / EACCES). Used to turn a NextSync-server port clash into a
    friendly "another instance is probably running" warning instead of a crash.
    """
    if not isinstance(ex, OSError):
        return False
    if getattr(ex, "winerror", None) in (10048, 10013):
        return True
    return ex.errno in (errno.EADDRINUSE, errno.EACCES)


def bind_listen_socket(port):
    """Create a listening TCP socket on ``port`` (all interfaces).

    Uses SO_EXCLUSIVEADDRUSE on Windows so a second bind fails cleanly instead of
    silently "stealing" the port from another instance (SO_REUSEADDR has that
    surprising behaviour on Windows); on POSIX it uses SO_REUSEADDR so the server
    can be restarted without waiting out TIME_WAIT. Raises OSError on failure
    (``is_address_in_use`` classifies a port clash).
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):        # Windows
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:                                             # POSIX
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", port))
        srv.listen()
    except OSError:
        srv.close()
        raise
    return srv


class DotDotFirstProxyModel(QSortFilterProxyModel):
    """Proxy model that always keeps the '..' parent directory entry at the top."""
    def lessThan(self, left, right):
        left_name = self.sourceModel().fileName(left)
        right_name = self.sourceModel().fileName(right)
        if left_name == "..":
            return True
        if right_name == "..":
            return False
        return super().lessThan(left, right)

    def filterAcceptsRow(self, source_row, source_parent):
        source_model = self.sourceModel()
        index = source_model.index(source_row, 0, source_parent)
        # Always show the parent-directory entry
        if source_model.fileName(index) == "..":
            return True
        pattern = self.filterRegularExpression().pattern()
        if not pattern:
            return True
        name = source_model.fileName(index)
        return pattern.lower() in name.lower()

class WorkerSignals(QObject):

    finished = Signal()
    error = Signal(tuple)
    result = Signal(object)
    progress = Signal(int)


class NextSyncSignals(QObject):
    """Signals used to marshal nextsync progress back to the main thread."""
    progress = Signal(int)   # 0-100 per-file progress
    status   = Signal(str)   # single-line status message
    finished = Signal()      # emitted when the job thread exits
    cancelled = Signal()     # emitted when job stopped due to cancel request
    port_in_use = Signal(int)  # bind failed: the port is already taken


class RemoteExplorerSignals(QObject):
    """Signals marshalling results of the NextSync ".sync4 -listen" remote file
    server back to the UI thread. The session runs in a worker thread; the UI
    feeds it commands via a queue and receives results through these."""
    connected    = Signal()               # a Next connected in -listen mode
    disconnected = Signal()               # the listen session ended
    # (path, entries) where entries is a list of (is_dir: bool, size: int, name: str)
    listing      = Signal(str, object)    # result of an "ls"
    got          = Signal(str, str)       # (remote, local_path) a "get" finished
    put_done     = Signal(bool, str)      # (ok, remote) a "put" finished
    op_done      = Signal(bool, str, str) # (ok, op, path) mkdir/rmdir/rm result
    marked       = Signal(str)            # a queued ("mark", token) was reached
    log          = Signal(str)            # a human-readable log line
    error        = Signal(str)            # a human-readable error
    port_in_use  = Signal(int)            # bind failed: the port is already taken


def _re_checksums(payload):
    c0 = c1 = 0
    for x in payload:
        c0 = (c0 ^ x) & 0xff
        c1 = (c1 + c0) & 0xff
    return c0, c1


def _re_sendpacket(conn, payload, pktno):
    c0, c1 = _re_checksums(payload)
    conn.sendall((len(payload) + 5).to_bytes(2, "big") + bytes(payload) +
                 bytes([c0, c1, pktno & 0xff]))


def _re_recv_exact(conn, n):
    buf = b''
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _re_recv_block(conn):
    hdr = _re_recv_exact(conn, 2)
    if hdr is None:
        return None
    total = (hdr[0] << 8) | hdr[1]
    if total < 5 or total > 4096:
        return None
    rest = _re_recv_exact(conn, total - 2)
    if rest is None:
        return None
    payload, (cs0, cs1) = rest[:-3], (rest[-3], rest[-2])
    c0, c1 = _re_checksums(payload)
    if c0 != cs0 or c1 != cs1:
        return 'BADCS'
    return (payload, rest[-1])


def _re_recv_reply(conn, handler):
    """Read the framed blocks the Next pushes in reply to a command, acking each
    with "Ok". handler(payload) returns True to stop. Returns True on clean
    completion, False on drop."""
    expected = 0
    while True:
        blk = _re_recv_block(conn)
        if blk is None:
            return False
        if blk == 'BADCS':
            _re_sendpacket(conn, b"Resend", expected)
            continue
        payload, pktno = blk
        if pktno == ((expected - 1) & 0xff):
            _re_sendpacket(conn, b"Ok", pktno)
            continue
        if pktno != expected:
            _re_sendpacket(conn, b"Err seq", pktno)
            return False
        stop = handler(payload)
        _re_sendpacket(conn, b"Ok", pktno)
        expected = (expected + 1) & 0xff
        if stop:
            return True


def _re_sanitize_incoming_path(root, name):
    """Map a filename reported by the Next to a safe path under ``root``.

    A "get" of a directory streams every file back with its path relative to
    the fetched folder (e.g. ``GAMES/level1/boot.tap``); this preserves that
    sub-structure locally instead of flattening it to the basename. Strips any
    drive letter and leading slashes, drops '.'/'..' segments, and guarantees
    the result stays inside ``root``. Mirrors nextsync4.sanitize_incoming_path.
    """
    name = name.replace('\\', '/')
    if len(name) >= 2 and name[1] == ':':
        name = name[2:]
    name = name.lstrip('/')
    parts = [p for p in name.split('/') if p not in ('', '.', '..')]
    rel = os.path.join(*parts) if parts else 'received.bin'
    dest = os.path.normpath(os.path.join(root, rel))
    root_abs = os.path.abspath(root)
    if not (os.path.abspath(dest) == root_abs or
            os.path.abspath(dest).startswith(root_abs + os.sep)):
        dest = os.path.join(root, os.path.basename(rel) or 'received.bin')
    return dest


def _re_relname_under(remote, name):
    """Return the Next-reported *name* relative to the fetched *remote* item.

    A directory 'get' streams every file with its full Next path (e.g. fetching
    '/games/lev' yields '/games/lev/boot.tap'). Stripping the *parent* of the
    fetched item ('/games') leaves 'lev/boot.tap', so the download recreates the
    fetched file/folder on its own rather than nested under a copy of its whole
    Next path. A single-file get ('/games/boot.tap') likewise reduces to
    'boot.tap'. Names with no common parent are returned unchanged.
    """
    def _strip(s):
        s = s.replace('\\', '/')
        if len(s) >= 2 and s[1] == ':':
            s = s[2:]
        return s
    name = _strip(name).lstrip('/')
    r = _strip(remote).rstrip('/')
    slash = r.rfind('/')
    parent = r[:slash].lstrip('/') if slash > 0 else ''
    if parent and (name == parent or name.startswith(parent + '/')):
        return name[len(parent):].lstrip('/')
    return name


def run_remote_listen_server(sig, cmd_queue, stop_event, port=2048,
                             max_payload=1024):
    """Run the NextSync ``.sync4 -listen`` remote file server in a worker thread.

    Waits for a Next running ``.sync4 -listen`` to connect, then drives it from
    commands pulled off ``cmd_queue`` (a queue.Queue), emitting results through
    ``sig`` (a RemoteExplorerSignals). Commands are tuples:
        ("ls",    remote_path)
        ("get",   remote_path, local_dest_dir)
        ("put",   local_file,  remote_path)
        ("mkdir", remote_path)
        ("rmdir", remote_path)
        ("rm",    remote_path)
        ("rename", old_path, new_path)
        ("mark",  token)          -> echoes back via sig.marked once reached
        ("quit",)
    ``stop_event`` (threading.Event) ends the session/thread.

    ``mark`` is a client-side barrier: it touches nothing on the Next, it just
    emits ``marked(token)`` the moment the queue drains down to it. Because the
    queue is a single-consumer FIFO, everything enqueued before the marker has
    finished by then -- the UI uses this to know a cut/move's transfer completed
    before deleting the source.

    This is the app-side twin of nextsync4.py's listen_session: same wire
    protocol, but driven by the UI queue and reporting via Qt signals instead of
    a console CLI. It never touches the Sync3/Sync4 sync paths.
    """
    def log(msg):
        sig.log.emit(msg)

    srv = None
    try:
        try:
            srv = bind_listen_socket(port)
        except OSError as ex:
            # Port already taken - almost always another ZX-Next-Unite (or a
            # standalone NextSync server) already listening on it. Signal the UI
            # to warn (yellow toast) instead of failing with a cryptic error, and
            # bail cleanly so the Next just sees "no server" rather than us
            # half-starting.
            if is_address_in_use(ex):
                sig.port_in_use.emit(port)
            else:
                sig.error.emit(f"Remote explorer server error: {ex}")
            return
        srv.settimeout(1.0)
        log(f"Remote explorer: waiting for '.sync4 -listen' on port {port}…")

        conn = None
        while not stop_event.is_set():
            try:
                conn, addr = srv.accept()
                break
            except socket.timeout:
                continue
        if conn is None:
            return

        with conn:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # The Next opens the session with the "Listen" handshake keyword.
            data = conn.recv(1024)
            if data != b"Listen":
                sig.error.emit("Connected client did not request -listen mode.")
                return
            _re_sendpacket(conn, b"Listening", 0)
            log(f"Remote explorer: connected to {addr[0]}")
            sig.connected.emit()

            put_data = b''
            put_ofs = 0
            put_pkt = 0
            last_packet = b''
            pending = None   # the command awaiting its pushed reply

            while not stop_event.is_set():
                try:
                    conn.settimeout(1.0)
                    data = conn.recv(1024)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break

                if data == b"Poll":
                    try:
                        cmd = cmd_queue.get_nowait()
                    except queue.Empty:
                        _re_sendpacket(conn, b"I", 0)   # idle
                        continue
                    op = cmd[0]
                    if op == "quit":
                        _re_sendpacket(conn, b"Q", 0)
                        break
                    elif op == "mark":
                        # Client-side barrier: nothing goes to the Next, we just
                        # report that the queue reached this point, then idle so
                        # the Next keeps polling.
                        sig.marked.emit(str(cmd[1]))
                        _re_sendpacket(conn, b"I", 0)
                    elif op == "ls":
                        path = cmd[1] or "."
                        entries = []

                        def _h(payload, _e=entries):
                            o = payload[0:1]
                            if o == b'E':
                                return True
                            if o == b'D':
                                i = 1
                                while i + 6 <= len(payload):
                                    flags = payload[i]
                                    size = (payload[i+1] | (payload[i+2] << 8) |
                                            (payload[i+3] << 16) | (payload[i+4] << 24))
                                    nl = payload[i+5]
                                    name = payload[i+6:i+6+nl].decode(errors='replace')
                                    i += 6 + nl
                                    _e.append((bool(flags & 1), size, name))
                            return False
                        _re_sendpacket(conn, b"L" + path.encode(), 0)
                        if _re_recv_reply(conn, _h):
                            entries.sort(key=lambda e: (0 if e[0] else 1, e[2].lower()))
                            sig.listing.emit(path, entries)
                        else:
                            sig.error.emit(f"ls {path}: connection dropped")
                    elif op == "get":
                        # Works for a single file or a whole directory: the Next
                        # streams every file back (N/D/E per file, B at the end)
                        # with a path relative to the fetched item, which we keep
                        # so sub-folders are recreated locally intact.
                        remote, dest_dir = cmd[1], cmd[2]
                        os.makedirs(dest_dir, exist_ok=True)
                        st = {'f': None, 'name': None, 'bytes': 0, 'last': None,
                              'count': 0}

                        def _h(payload, _st=st, _dd=dest_dir, _remote=remote):
                            o = payload[0:1]
                            if o == b'N':
                                namelen = payload[5] if len(payload) > 5 else 0
                                name = payload[6:6+namelen].decode(errors='replace')
                                rel = (_re_relname_under(_remote, name) or
                                       os.path.basename(name.replace('\\', '/').rstrip('/')))
                                path = _re_sanitize_incoming_path(_dd, rel)
                                if _st['f']:
                                    _st['f'].close()
                                parent = os.path.dirname(path)
                                if parent:
                                    os.makedirs(parent, exist_ok=True)
                                _st['f'] = open(path, 'wb')
                                _st['name'] = name
                                _st['last'] = path
                                _st['bytes'] = 0
                                _st['count'] += 1
                            elif o == b'D':
                                if _st['f']:
                                    _st['f'].write(payload[1:])
                                    _st['bytes'] += len(payload) - 1
                            elif o == b'E':
                                if _st['f']:
                                    _st['f'].close()
                                    _st['f'] = None
                            elif o == b'B':
                                if _st['f']:
                                    _st['f'].close()
                                    _st['f'] = None
                                return True
                            return False
                        _re_sendpacket(conn, b"G" + remote.encode(), 0)
                        ok = _re_recv_reply(conn, _h)
                        if st['f']:
                            st['f'].close()
                        if ok:
                            sig.got.emit(remote, st['last'] or dest_dir)
                        else:
                            sig.error.emit(f"get {remote}: failed")
                    elif op == "put":
                        local, remote = cmd[1], cmd[2]
                        try:
                            with open(local, 'rb') as fh:
                                put_data = fh.read()
                        except OSError as ex:
                            sig.error.emit(f"put {local}: {ex}")
                            continue
                        put_ofs = 0
                        put_pkt = 0
                        if remote.endswith('/') or remote.endswith('\\'):
                            remote = remote + os.path.basename(local)
                        pending = ("put", remote)
                        _re_sendpacket(conn, b"P" + remote.encode(), 0)
                        # the Next now pulls the bytes via "Get" (served below)
                    elif op in ("mkdir", "rmdir", "rm"):
                        opc = {"mkdir": b"M", "rmdir": b"R", "rm": b"X"}[op]
                        path = cmd[1]
                        res = {'ok': None}

                        def _h(payload, _r=res):
                            _r['ok'] = (payload[0:1] == b'O')
                            return True
                        _re_sendpacket(conn, opc + path.encode(), 0)
                        if _re_recv_reply(conn, _h):
                            sig.op_done.emit(bool(res['ok']), op, path)
                        else:
                            sig.error.emit(f"{op} {path}: connection dropped")
                    elif op == "rename":
                        old, new = cmd[1], cmd[2]
                        res = {'ok': None}

                        def _h(payload, _r=res):
                            _r['ok'] = (payload[0:1] == b'O')
                            return True
                        # 'V' + old + NUL + new, in one length-framed block.
                        _re_sendpacket(conn, b"V" + old.encode() + b"\x00" + new.encode(), 0)
                        if _re_recv_reply(conn, _h):
                            sig.op_done.emit(bool(res['ok']), "rename", old)
                        else:
                            sig.error.emit(f"rename {old}: connection dropped")

                elif data == b"Get" or data == b"Gee":
                    n = min(max_payload, len(put_data) - put_ofs)
                    last_packet = put_data[put_ofs:put_ofs + n]
                    _re_sendpacket(conn, last_packet, put_pkt)
                    put_ofs += n
                    put_pkt += 1
                    if put_ofs >= len(put_data) and pending and pending[0] == "put":
                        sig.put_done.emit(True, pending[1])
                        pending = None
                elif data == b"Retry":
                    _re_sendpacket(conn, last_packet, (put_pkt - 1) & 0xff)
                elif data == b"Restart":
                    put_ofs = 0
                    put_pkt = 0
                    _re_sendpacket(conn, b"Back", 0)
                elif data == b"Bye":
                    _re_sendpacket(conn, b"Later", 0)
                    break
                else:
                    _re_sendpacket(conn, b"I", 0)   # keep the Next polling
    except OSError as ex:
        sig.error.emit(f"Remote explorer server error: {ex}")
    finally:
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass
        sig.disconnected.emit()


class HdfTaskSignals(QObject):
    """Signals for background hdfmonkey task workers."""
    progress  = Signal(int)   # 0-100
    status    = Signal(str)   # "action line\nfilename line"
    finished  = Signal()
    error     = Signal(str)   # human-readable error message
    cancelled = Signal()      # emitted when the worker stopped early due to cancel


class HdfMonkeyMissingSignals(QObject):
    """Emitted (possibly from a worker thread) when hdfmonkey appears to be
    missing/unrunnable, so the UI thread can offer to download/install it."""
    missing = Signal()


class NextSyncConflictSignals(QObject):
    """Marshals a 'received file/dir already exists locally' prompt from the
    NextSync receive worker thread to the UI thread. The worker emits ``prompt``
    with a result holder + a threading.Event and blocks on the event until the
    UI slot records the user's choice and sets it."""
    prompt = Signal(str, str, object, object)   # (name, local_path, result_holder, done_event)


class MameProcessSignals(QObject):
    """Signals used to marshal output from a detached MAME process back to the
    main (UI) thread. A background reader thread emits ``output`` for every
    captured line and ``finished`` with the process exit code when MAME ends."""
    output   = Signal(str)   # one captured stdout/stderr line
    finished = Signal(int)   # process return code


class MameInstallSignals(QObject):
    """Marshals updates from the MAME auto-install worker thread to the UI
    thread, so each step of the download-then-extract job can be reported as it
    happens. Connect with ``Qt.QueuedConnection`` (the emits originate on a
    worker thread). The owner must keep a reference to the instance until the job
    finishes, otherwise pending queued emits are cancelled when it is GC'd."""
    status   = Signal(str)   # human-readable phase line for the log window
    progress = Signal(int)   # 0-100 download progress (button text)


class HdfTaskWorker(QRunnable):
    """Generic QRunnable that runs a callable on the thread pool.
    The callable receives (signals, cancel_event, *args, **kwargs).
    Call worker.cancel() from the UI thread to request early termination."""

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn           = fn
        self.args         = args
        self.kwargs       = kwargs
        self.signals      = HdfTaskSignals()
        self.cancel_event = threading.Event()
        self.setAutoDelete(True)

    def cancel(self):
        self.cancel_event.set()

    @Slot()
    def run(self):
        try:
            self.fn(self.signals, self.cancel_event, *self.args, **self.kwargs)
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            if self.cancel_event.is_set():
                self.signals.cancelled.emit()
            self.signals.finished.emit()


class HdfProgressDialog(QDialog):
    """Modal progress dialog with live status, progress bar, spinner, and Cancel button."""

    cancel_requested = Signal()

    def __init__(self, title, parent=None, cancel_label="Cancel"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(540)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Spinner + action label on one row
        action_row = QHBoxLayout()
        self._spinner_label = QLabel("")
        self._spinner_label.setFixedWidth(22)
        action_row.addWidget(self._spinner_label)
        self._action_label = QLabel("Starting\u2026")
        self._action_label.setWordWrap(True)
        action_row.addWidget(self._action_label, 1)
        layout.addLayout(action_row)

        # Current filename (smaller, muted)
        self._file_label = QLabel("")
        self._file_label.setWordWrap(True)
        _font = self._file_label.font()
        _ps = _font.pointSize()
        if _ps <= 0:
            _ps = max(QFontInfo(_font).pointSize(), 9)
        _font.setPointSize(max(_ps - 1, 8))
        self._file_label.setFont(_font)
        layout.addWidget(self._file_label)

        # Progress bar
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(True)
        layout.addWidget(self._bar)

        # Cancel button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = QPushButton(cancel_label)
        self._cancel_btn.setFixedWidth(90)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

        self._cancelled = False
        self._spinner_frames = ["\u25f4", "\u25f7", "\u25f6", "\u25f5"]
        self._spinner_idx    = 0

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(120)
        self._anim_timer.timeout.connect(self._tick_spinner)
        self._anim_timer.start()

    # ------------------------------------------------------------------
    @Slot()
    def _on_cancel_clicked(self):
        self._cancelled = True
        self._cancel_btn.setEnabled(False)
        self._action_label.setText("Cancelling\u2026")
        self._file_label.setText("")
        self.cancel_requested.emit()

    @Slot()
    def _tick_spinner(self):
        # Never repaint once the dialog is hidden: a tick after accept()/hide()
        # would schedule an update on a top-level window whose native handle is
        # already gone, which Qt flushes with the fatal "QBackingStore::flush()
        # called for QWidgetWindow ... which does not have a handle" and crashes.
        if not self.isVisible():
            return
        self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner_frames)
        self._spinner_label.setText(self._spinner_frames[self._spinner_idx])

    @Slot(int)
    def set_progress(self, value: int):
        """value == -1 activates the indeterminate (busy) marquee animation."""
        if value < 0:
            self._bar.setRange(0, 0)   # Qt marquee mode
        else:
            if self._bar.maximum() == 0:
                self._bar.setRange(0, 100)
            self._bar.setValue(value)

    @Slot(str)
    def set_status(self, text: str):
        """Expects 'Action description\nFilename or detail'."""
        if self._cancelled:
            return
        lines = text.split("\n", 1)
        self._action_label.setText(lines[0])
        self._file_label.setText(lines[1] if len(lines) > 1 else "")

    @Slot()
    def mark_cancelled(self):
        """Called when the worker confirms it stopped early."""
        self._action_label.setText("Cancelled.")
        self._file_label.setText("")

    def done(self, result):
        # done() is the single funnel for accept()/reject()/close(), whereas
        # closeEvent() fires only on close() (not on accept()/reject()). This
        # dialog is normally dismissed with accept(), so stopping the spinner
        # timer here — not just in closeEvent — guarantees it can never tick
        # after the window is hidden and loses its native handle.
        self._anim_timer.stop()
        super().done(result)

    def closeEvent(self, event):
        self._anim_timer.stop()
        super().closeEvent(event)


# Export every public/private module-level name (including the
# underscore-prefixed helpers and caches) so `from <module> import *`
# in the main file picks them all up.
__all__ = [_n for _n in dir() if not _n.startswith('__')]
