"""Background worker, signal and progress-dialog classes for zx-next-unite.

Extracted from zx-next-unite.py."""

import errno
import os
import queue
import socket
import threading
import time
from collections import deque
from PySide6.QtCore import (
    QObject, QPoint, QRect, QRunnable, QSize, QSortFilterProxyModel, QTimer,
    Qt, Signal, Slot,
)
from PySide6.QtGui import QFontInfo
from PySide6.QtWidgets import (
    QDialog, QFileSystemModel, QHBoxLayout, QLabel, QLayout, QProgressBar,
    QPushButton, QVBoxLayout,
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
        source_model = self.sourceModel()
        left_name = source_model.fileName(left)
        right_name = source_model.fileName(right)
        if left_name == "..":
            return True
        if right_name == "..":
            return False
        # The Size column's display text is human-readable ("512 B", "2.0 K"), so
        # the default DisplayRole comparison would sort it as a string ("2.0 K"
        # before "512 B"). Compare the real byte count instead. QFileSystemModel's
        # Size is logical column 1.
        if isinstance(source_model, QFileSystemModel) and left.column() == 1:
            return source_model.size(left) < source_model.size(right)
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


# The HTTP bridge's result sink: a command tuple whose LAST element is a
# BridgeReply is a "bridge command" — the worker fills the reply with a result
# dict INSTEAD of emitting its usual signals, so bridge traffic can never
# hijack the Remote Explorer pane (its on_listing adopts any path it is
# handed). zxnu_http_bridge imports only the stdlib at module level.
from zxnu_http_bridge import BridgeReply


class RemoteExplorerSignals(QObject):
    """Signals marshalling results of the NextSync ".sync5 -listen" remote file
    server back to the UI thread. The session runs in a worker thread; the UI
    feeds it commands via a queue and receives results through these."""
    connected    = Signal()               # a Next connected in -listen mode
    disconnected = Signal()               # the listen session ended
    # (path, entries) where entries is a list of (is_dir: bool, size: int, name: str)
    listing      = Signal(str, object)    # result of an "ls"
    ls_failed    = Signal(str)            # an "ls" path could not be opened on the Next (gone)
    got          = Signal(str, str)       # (remote, local_path) a "get" finished
    put_done     = Signal(bool, str)      # (ok, remote) a "put" finished
    op_done      = Signal(bool, str, str) # (ok, op, path) mkdir/rmdir/rm result
    # (current, letters): the Next's default drive + every mounted drive letter,
    # e.g. ("C", ["C", "M"]). ("", []) when the dot predates the 'W' command.
    drives       = Signal(str, object)
    # (drive, free_bytes): result of a ("free", drive) query ('Z', dot v5.2+).
    # free_bytes is an int, or None when the query failed on the Next ('F') or
    # the dot predates 'Z' -- the log line says which. Free space is the ONLY
    # storage metric a dotN can obtain safely (total partition size needs
    # +3DOS/IDEDOS calls that crash a dotN), so psize/pfull both present it.
    free_space   = Signal(str, object)
    # (path, data): result of a ("fsize", path) query ('S', rfsize, dot
    # v5.2+). data is {'files': int, 'dirs': int, 'bytes': int}, or None on
    # failure / pre-v5.2 dots. Emitted AFTER the matching op_done(ok, "size",
    # path), so a modal progress op has closed by the time the UI shows the
    # result dialog.
    fsize        = Signal(str, object)
    # (op, name): one 'D' progress block arrived while a long command runs.
    # op is "copy" (rcpy) or "size" (rfsize). name is the item the Next just
    # reported - the destination path of the file now being copied / the
    # directory now being scanned - or "" for an empty keepalive (rcpy sends
    # one per 64 KB inside a big file, so these pulse a byte estimate). The
    # UI uses them to drive the progress dialog instead of leaving the bar
    # parked at 0% for the whole copy.
    op_progress  = Signal(str, str)
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


def _re_is_fail_block(data):
    """True if ``data`` is the framed 1-byte 'F' status block a dotN pushes when a
    put fails (couldn't create the file, or the transfer gave up).

    Framing is [0x00 0x06]['F'][cs0][cs1][pktno]; the checksum of 'F' is 0x46/0x46.
    Older dots don't send this - they just stop pulling - so callers keep the
    "abandoned upload" fallback as well.
    """
    if len(data) < 6 or data[0] != 0x00 or data[1] != 0x06 or data[2:3] != b'F':
        return False
    c0, c1 = _re_checksums(b'F')
    return data[3] == c0 and data[4] == c1


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
    the result stays inside ``root``. Mirrors nextsync5.sanitize_incoming_path.
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
    """Run the NextSync ``.sync5 -listen`` remote file server in a worker thread.

    Waits for a Next running ``.sync5 -listen`` to connect, then drives it from
    commands pulled off ``cmd_queue`` (a queue.Queue), emitting results through
    ``sig`` (a RemoteExplorerSignals). Commands are tuples:
        ("ls",    remote_path)
        ("get",   remote_path, local_dest_dir)
        ("put",   local_file,  remote_path)
        ("mkdir", remote_path)
        ("rmdir", remote_path)
        ("rm",    remote_path)
        ("rmtree", remote_path)   -> recursive folder delete (see below)
        ("drives",)               -> query mounted drives (see below)
        ("free",  drive_letter)   -> query a partition's free space (see below)
        ("rcpy",  src, dst)       -> copy locally ON the Next (see below)
        ("fsize", remote_path)    -> total size of a file/tree (see below)
        ("rename", old_path, new_path)
        ("mark",  token)          -> echoes back via sig.marked once reached
        ("quit",)
    ``stop_event`` (threading.Event) ends the session/thread.

    ``mark`` is a client-side barrier: it touches nothing on the Next, it just
    emits ``marked(token)`` the moment the queue drains down to it. Because the
    queue is a single-consumer FIFO, everything enqueued before the marker has
    finished by then -- the UI uses this to know a cut/move's transfer completed
    before deleting the source.

    ``rmtree`` deletes a whole folder on the Next: esxDOS rmdir only removes
    *empty* directories, so the worker walks the tree itself over the ordinary
    protocol -- ls each directory, rm its files, recurse into its sub-folders,
    then rmdir it once empty (bottom-up). The walk runs as internally queued
    sub-commands served one per "Poll", exactly like user commands, and reports
    a single op_done(ok, "delete", root) when the root folder is gone (ok only
    if every file and folder inside deleted cleanly). A user cancel drains the
    host queue only, so an rmtree already underway finishes on its own -- same
    "stop after the current item" semantics as a cancelled transfer.

    ``drives`` sends the 'W' (getdrives) command; the Next replies with one
    status block 'O' + <current drive letter> + <mounted letters>, emitted as
    ``drives(current, [letters])``. Every remote path in the other commands may
    carry a drive prefix ("M:/games"); a path without one lands on the dot's
    current drive, so nothing changes for pre-drive-aware flows. A dot older
    than v5.1 ignores the unknown 'W' and just re-polls: its raw "Poll" fails
    the block parse, ``drives("", [])`` is emitted as the fallback, and the
    stray bytes are re-synced by the outer loop's catch-all idle reply.

    ``free`` sends the 'Z' command (dot v5.2+) with an optional drive letter
    ("" = the dot's current drive); the Next replies with one status block
    'O' + 4 bytes little-endian = free 512-byte blocks (F_GETFREE), or 'F'
    when the drive can't be measured, emitted as ``free_space(drive, bytes)``
    (bytes None on failure / pre-v5.2 dots, which degrade exactly like
    ``drives``).

    ``rcpy`` sends the 'C' command (dot v5.2+) with the source and destination
    paths NUL-separated (like ``rename``); the whole copy - a file or a
    recursive directory tree, across partitions too - runs ON the Next, no
    data through the PC. The Next pushes 'D' progress blocks (a named one per
    file, empty keepalives every 64KB inside big files) and ends with one
    'O'/'F', reported as ``op_done(ok, "copy", src)``. The reply socket
    timeout is temporarily widened: SD-card copies are slow and the
    keepalives only bound the gaps to ~64KB of local I/O.

    ``fsize`` sends the 'S' command (rfsize, dot v5.2+): the Next measures a
    file or a whole directory tree (rcpy's "will it fit" companion), pushing
    'D' progress blocks (one per directory + keepalives) and a terminal
    'O' + [4B files][4B dirs][4B size_lo][2B size_hi] or 'F'. Reported as
    ``op_done(ok, "size", path)`` followed by ``fsize(path, data)`` (data
    None on failure). Socket timeout widened like ``rcpy``.

    This is the app-side twin of nextsync5.py's listen_session: same wire
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
        log(f"Remote explorer: waiting for '.sync5 -listen' on port {port}…")

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
            pending = None   # ("put", remote, bridge_reply|None) awaiting completion

            def _put_finish(ok):
                # Resolve the pending put: to its bridge reply when it has
                # one, to the UI signal otherwise (reads `pending` live).
                if pending[2] is not None:
                    pending[2].put({'ok': bool(ok)} if ok else
                                   {'ok': False,
                                    'error': 'put failed on the Next'})
                else:
                    sig.put_done.emit(bool(ok), pending[1])
            # rmtree walk state: sub-commands the worker generates for itself
            # (rmtree_ls/rmtree_rm/rmtree_rmdir) are served before the host
            # queue, so a recursive delete runs as one contiguous batch.
            local_cmds = deque()
            rmtree_jobs = {}   # job id -> {'root': path, 'fails': 0}
            rmtree_seq = 0

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

                # A put in flight is served by the Next pulling the bytes with
                # "Get"/"Gee" (or asking to resend with "Retry"/"Restart"). A newer
                # dotN instead pushes an explicit 'F' status block when the put
                # fails (couldn't create the file, or the transfer gave up); ack it
                # so the dot's send_block_rt doesn't burn its retries, and report
                # the failure. Ack even with no pending put (a rare late 'F' after
                # the last byte already counted) so the dot isn't left retrying.
                if _re_is_fail_block(data):
                    _re_sendpacket(conn, b"Ok", 0)
                    if pending and pending[0] == "put":
                        _put_finish(False)
                        pending = None
                        put_data = b''
                        put_ofs = 0
                        put_pkt = 0
                    continue
                # Older dots don't send 'F'; they just stop pulling and go back to
                # "Poll". Treat any other non-pull frame during a pending put as an
                # abandoned upload so the UI operation still completes and its
                # transfer dialog closes instead of waiting forever for a "Get".
                if (pending and pending[0] == "put" and
                        data not in (b"Get", b"Gee", b"Retry", b"Restart")):
                    _put_finish(False)
                    pending = None
                    put_data = b''
                    put_ofs = 0
                    put_pkt = 0

                if data == b"Poll":
                    if local_cmds:
                        cmd = local_cmds.popleft()
                    else:
                        try:
                            cmd = cmd_queue.get_nowait()
                        except queue.Empty:
                            _re_sendpacket(conn, b"I", 0)   # idle
                            continue
                    op = cmd[0]
                    # A command from the HTTP bridge carries its result sink as
                    # the last element: fill that instead of emitting signals
                    # (bridge traffic must be silent to the Remote Explorer UI).
                    reply = cmd[-1] if isinstance(cmd[-1], BridgeReply) else None
                    if op == "rmtree":
                        # Recursive folder delete: open a walk job and start with
                        # the root's listing (handled below, on this same poll).
                        rmtree_seq += 1
                        rmtree_jobs[rmtree_seq] = {'root': cmd[1], 'fails': 0,
                                                   'reply': reply}
                        cmd = ("rmtree_ls", rmtree_seq, cmd[1])
                        op = cmd[0]
                        reply = None
                    if op == "quit":
                        _re_sendpacket(conn, b"Q", 0)
                        # A bridge-driven quit (/forceexit) must fill its reply
                        # BEFORE we break: the HTTP thread is blocked on it.
                        if reply is not None:
                            reply.put({'ok': True})
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
                        # The Next answers a listing with 'D' blocks then 'E', or a
                        # single 'F' status block if opendir failed (the folder is
                        # gone). Track which so a missing folder isn't mistaken for
                        # an empty one - and so the 'F' block is consumed instead of
                        # desyncing the stream.
                        st = {'failed': False}

                        def _h(payload, _e=entries, _st=st):
                            o = payload[0:1]
                            if o == b'E':
                                return True
                            if o == b'F':
                                _st['failed'] = True
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
                            if st['failed']:
                                if reply:
                                    reply.put({'ok': False,
                                               'error': f"ls {path} failed "
                                                        "(missing folder?)"})
                                else:
                                    sig.ls_failed.emit(path)
                            else:
                                entries.sort(key=lambda e: (0 if e[0] else 1, e[2].lower()))
                                if reply:
                                    reply.put({'ok': True, 'entries': entries})
                                else:
                                    sig.listing.emit(path, entries)
                        elif reply:
                            reply.put({'ok': False, 'error': 'connection dropped'})
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
                        if reply:
                            reply.put({'ok': bool(ok), 'count': st['count'],
                                       'last': st['last']}
                                      if ok else {'ok': False, 'error': 'get failed'})
                        elif ok:
                            sig.got.emit(remote, st['last'] or dest_dir)
                        else:
                            sig.error.emit(f"get {remote}: failed")
                    elif op == "put":
                        local, remote = cmd[1], cmd[2]
                        try:
                            with open(local, 'rb') as fh:
                                put_data = fh.read()
                        except OSError as ex:
                            if reply:
                                reply.put({'ok': False, 'error': str(ex)})
                            else:
                                sig.error.emit(f"put {local}: {ex}")
                            continue
                        put_ofs = 0
                        put_pkt = 0
                        if remote.endswith('/') or remote.endswith('\\'):
                            remote = remote + os.path.basename(local)
                        pending = ("put", remote, reply)
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
                            if reply:
                                reply.put({'ok': bool(res['ok'])})
                            else:
                                sig.op_done.emit(bool(res['ok']), op, path)
                        elif reply:
                            reply.put({'ok': False, 'error': 'connection dropped'})
                        else:
                            sig.error.emit(f"{op} {path}: connection dropped")
                    elif op == "rmtree_ls":
                        # rmtree step 1: list one folder of the walk, then queue
                        # deleting its files, walking its sub-folders and finally
                        # removing the (now empty) folder itself, ahead of
                        # anything else -- so the tree comes down bottom-up.
                        jid, path = cmd[1], cmd[2]
                        entries = []
                        st = {'failed': False}

                        def _h(payload, _e=entries, _st=st):
                            o = payload[0:1]
                            if o == b'E':
                                return True
                            if o == b'F':
                                _st['failed'] = True
                                return True
                            if o == b'D':
                                i = 1
                                while i + 6 <= len(payload):
                                    flags = payload[i]
                                    nl = payload[i+5]
                                    name = payload[i+6:i+6+nl].decode(errors='replace')
                                    i += 6 + nl
                                    _e.append((bool(flags & 1), name))
                            return False
                        _re_sendpacket(conn, b"L" + path.encode(), 0)
                        if _re_recv_reply(conn, _h):
                            subs = []
                            if not st['failed']:
                                base = path.rstrip("/")
                                for is_dir, name in entries:
                                    if name in (".", ".."):
                                        continue
                                    child = base + "/" + name
                                    subs.append(("rmtree_ls", jid, child) if is_dir
                                                else ("rmtree_rm", jid, child))
                            # On a failed listing (gone, or not a folder) still try
                            # the rmdir: it reports the failure if the folder is
                            # really stuck, instead of stalling the job.
                            subs.append(("rmtree_rmdir", jid, path))
                            local_cmds.extendleft(reversed(subs))
                        else:
                            job = rmtree_jobs.pop(jid, None)
                            if job is not None and job.get('reply'):
                                job['reply'].put({'ok': False,
                                                  'error': 'connection dropped'})
                            else:
                                sig.error.emit(f"delete {path}: connection dropped")
                    elif op in ("rmtree_rm", "rmtree_rmdir"):
                        # rmtree steps 2/3: delete one file / one emptied folder.
                        # Only the root folder's rmdir reports back to the UI --
                        # one op_done for the whole job, matching the single
                        # command the UI enqueued.
                        jid, path = cmd[1], cmd[2]
                        opc = b"X" if op == "rmtree_rm" else b"R"
                        res = {'ok': None}

                        def _h(payload, _r=res):
                            _r['ok'] = (payload[0:1] == b'O')
                            return True
                        _re_sendpacket(conn, opc + path.encode(), 0)
                        if _re_recv_reply(conn, _h):
                            job = rmtree_jobs.get(jid)
                            if job is not None:
                                if not res['ok']:
                                    job['fails'] += 1
                                    log(f"delete: could not remove {path}")
                                if op == "rmtree_rmdir" and path == job['root']:
                                    rmtree_jobs.pop(jid, None)
                                    if job.get('reply'):
                                        job['reply'].put({'ok': job['fails'] == 0})
                                    else:
                                        sig.op_done.emit(job['fails'] == 0, "delete", path)
                        else:
                            job = rmtree_jobs.pop(jid, None)
                            if job is not None and job.get('reply'):
                                job['reply'].put({'ok': False,
                                                  'error': 'connection dropped'})
                            else:
                                sig.error.emit(f"delete {path}: connection dropped")
                    elif op == "drives":
                        # getdrives: one pushed status block, 'O' + current
                        # drive letter + one letter per mounted drive. An old
                        # dot (pre v5.1) ignores 'W' and re-polls; its raw
                        # "Poll" fails the block parse below, which lands in
                        # the ("", []) fallback -- the widget then offers no
                        # drive switching, exactly the pre-drives behaviour.
                        res = {'cur': "", 'letters': []}

                        def _h(payload, _r=res):
                            if payload[0:1] == b'O' and len(payload) >= 2:
                                _r['cur'] = chr(payload[1])
                                _r['letters'] = [chr(b) for b in payload[2:]]
                            return True
                        _re_sendpacket(conn, b"W", 0)
                        # A timeout here (old dot slow to re-poll) must not
                        # kill the session like other commands' drops would:
                        # drives is an optional nicety, so degrade instead.
                        try:
                            got_reply = _re_recv_reply(conn, _h)
                        except socket.timeout:
                            got_reply = False
                        if got_reply and res['cur']:
                            if reply:
                                reply.put({'ok': True, 'current': res['cur'],
                                           'letters': res['letters']})
                            else:
                                sig.drives.emit(res['cur'], res['letters'])
                        elif reply:
                            reply.put({'ok': False,
                                       'error': 'drives not supported '
                                                '(needs .sync v5.1+)'})
                        else:
                            log("This .sync dot does not report drives "
                                "(pre v5.1); staying on the default drive.")
                            sig.drives.emit("", [])
                    elif op == "free":
                        # free space ('Z', dot v5.2+): optional drive letter,
                        # answered with one status block 'O' + 4 bytes
                        # little-endian = free 512-byte blocks (F_GETFREE), or
                        # 'F' when the drive can't be measured. Free space is
                        # the only storage metric the dotN can obtain safely
                        # (total size needs +3DOS calls that crash a dotN).
                        # Optional nicety like drives: an old dot ignores 'Z'
                        # and re-polls, so degrade instead of dropping.
                        drv = ((cmd[1] or "").strip().rstrip(':').upper()
                               if len(cmd) > 1 else "")
                        res = {'blocks': None, 'fail': False}

                        def _h(payload, _r=res):
                            if payload[0:1] == b'F':
                                _r['fail'] = True
                            elif payload[0:1] == b'O' and len(payload) >= 5:
                                _r['blocks'] = int.from_bytes(payload[1:5], 'little')
                            return True
                        _re_sendpacket(conn, b"Z" + drv.encode(), 0)
                        try:
                            got_reply = _re_recv_reply(conn, _h)
                        except socket.timeout:
                            got_reply = False
                        if got_reply and res['blocks'] is not None:
                            if reply:
                                reply.put({'ok': True, 'drive': drv,
                                           'free': res['blocks'] * 512})
                            else:
                                sig.free_space.emit(drv, res['blocks'] * 512)
                        else:
                            err = (f"free space {drv or '(current drive)'}: "
                                   "FAILED on the Next" if res['fail'] else
                                   "free space not supported (needs .sync v5.2+)")
                            if reply:
                                reply.put({'ok': False, 'error': err})
                            else:
                                log(err)
                                sig.free_space.emit(drv, None)
                    elif op == "rcpy":
                        # Local copy ON the Next ('C', dot v5.2+): src and dst
                        # travel NUL-separated like rename's paths. The Next
                        # answers with 'D' progress blocks then one 'O'/'F'.
                        # Widen the socket timeout for the reply: the copy is
                        # local SD I/O and the keepalives only bound the
                        # silent gaps (per file / per 64KB). Always emit an
                        # op_done - the UI counts one per queued command, so
                        # even the old-dot fallback must complete the op.
                        src, dst = cmd[1], cmd[2]
                        res = {'ok': None, 'files': 0}

                        def _h(payload, _r=res):
                            if payload[0:1] == b'D':
                                # Progress: named = a file copy just started
                                # (the name is its destination path), empty =
                                # the per-64KB / per-256-entries keepalive.
                                if len(payload) > 1:
                                    _r['files'] += 1
                                sig.op_progress.emit(
                                    "copy", payload[1:].decode(errors='replace'))
                                return False
                            _r['ok'] = (payload[0:1] == b'O')
                            return True
                        _re_sendpacket(conn, b"C" + src.encode() + b"\x00" +
                                       dst.encode(), 0)
                        try:
                            conn.settimeout(60.0)
                            got_reply = _re_recv_reply(conn, _h)
                        except socket.timeout:
                            got_reply = False
                        finally:
                            try:
                                conn.settimeout(1.0)
                            except OSError:
                                pass
                        if got_reply and res['ok'] is not None:
                            if reply:
                                reply.put({'ok': bool(res['ok']),
                                           'files': res['files']} if res['ok'] else
                                          {'ok': False, 'files': res['files'],
                                           'error': 'rcpy FAILED on the Next '
                                                    '(copied files stay)'})
                            else:
                                sig.op_done.emit(bool(res['ok']), "copy", src)
                        elif reply:
                            reply.put({'ok': False,
                                       'error': 'rcpy needs .sync v5.2+ '
                                                '(or the link dropped)'})
                        else:
                            log("rcpy needs .sync v5.2+ (or the link dropped).")
                            sig.op_done.emit(False, "copy", src)
                    elif op == "fsize":
                        # Tree/file size ON the Next ('S', rfsize, dot v5.2+).
                        # 'D' blocks are progress (named per directory) and
                        # keepalives; the terminal 'O' carries the totals.
                        # Emit op_done FIRST (closes the UI's progress op),
                        # THEN fsize with the data for the result dialog.
                        path = cmd[1]
                        res = {'data': None}

                        def _h(payload, _r=res):
                            o = payload[0:1]
                            if o == b'D':
                                # Named = the directory the walk just entered.
                                sig.op_progress.emit(
                                    "size", payload[1:].decode(errors='replace'))
                                return False
                            if o == b'O' and len(payload) >= 15:
                                _r['data'] = {
                                    'files': int.from_bytes(payload[1:5], 'little'),
                                    'dirs': int.from_bytes(payload[5:9], 'little'),
                                    'bytes': (int.from_bytes(payload[13:15], 'little') << 32)
                                             | int.from_bytes(payload[9:13], 'little'),
                                }
                            return True         # 'O' or 'F' both end the reply
                        _re_sendpacket(conn, b"S" + path.encode(), 0)
                        try:
                            conn.settimeout(60.0)
                            got_reply = _re_recv_reply(conn, _h)
                        except socket.timeout:
                            got_reply = False
                        finally:
                            try:
                                conn.settimeout(1.0)
                            except OSError:
                                pass
                        if reply:
                            if res['data'] is not None:
                                reply.put({'ok': True, **res['data']})
                            else:
                                reply.put({'ok': False,
                                           'error': 'rfsize failed (missing '
                                                    'path, or needs .sync '
                                                    'v5.2+)'})
                        else:
                            if not got_reply:
                                log("rfsize needs .sync v5.2+ (or the link dropped).")
                            sig.op_done.emit(res['data'] is not None, "size", path)
                            sig.fsize.emit(path, res['data'])
                    elif op == "rename":
                        old, new = cmd[1], cmd[2]
                        res = {'ok': None}

                        def _h(payload, _r=res):
                            _r['ok'] = (payload[0:1] == b'O')
                            return True
                        # 'V' + old + NUL + new, in one length-framed block.
                        _re_sendpacket(conn, b"V" + old.encode() + b"\x00" + new.encode(), 0)
                        if _re_recv_reply(conn, _h):
                            if reply:
                                reply.put({'ok': bool(res['ok'])})
                            else:
                                sig.op_done.emit(bool(res['ok']), "rename", old)
                        elif reply:
                            reply.put({'ok': False, 'error': 'connection dropped'})
                        else:
                            sig.error.emit(f"rename {old}: connection dropped")

                elif data == b"Get" or data == b"Gee":
                    n = min(max_payload, len(put_data) - put_ofs)
                    last_packet = put_data[put_ofs:put_ofs + n]
                    _re_sendpacket(conn, last_packet, put_pkt)
                    put_ofs += n
                    put_pkt += 1
                    if put_ofs >= len(put_data) and pending and pending[0] == "put":
                        _put_finish(True)
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
        # Minimum, not fixed: the Remote Explorer's background-copy dialog uses
        # a long label ("Close this window and continue in the background").
        self._cancel_btn.setMinimumWidth(90)
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


# ----------------------------------------------------------------------
#  Local zip helpers — shared by the Remote Explorer (local pane AND the
#  Next-side Remote Zip/Unzip staging) and the SD Card tab (local explorer
#  and the image explorer's Remote Zip/Unzip). Both run ON THE UI THREAD
#  over local files only: they show a cancellable HdfProgressDialog naming
#  every file, pumping the event loop per entry (local zip work is fast;
#  the surrounding transfers have their own progress machinery).
# ----------------------------------------------------------------------

def _zip_dialog(parent, title):
    from PySide6.QtWidgets import QApplication
    dlg = HdfProgressDialog(title, parent)
    state = {"cancel": False}
    dlg.cancel_requested.connect(lambda: state.__setitem__("cancel", True))
    dlg.set_progress(0)
    dlg.show()
    QApplication.processEvents()
    return dlg, state


def zip_extract_with_dialog(parent, zip_path, dest_dir, log=None):
    """Extract *zip_path* into *dest_dir* with a cancellable progress dialog
    that names every file as it comes out. Zip-slip entries ('..' segments,
    absolute or drive-prefixed paths) are skipped and logged, not extracted.
    Returns {'ok', 'files', 'skipped', 'bytes', 'cancelled', 'error'};
    'ok' is True only when the archive extracted without cancel or error
    (skipped entries alone don't clear it), 'bytes' totals the uncompressed
    sizes. RuntimeError from zipfile = encrypted members."""
    import shutil
    import zipfile
    from PySide6.QtWidgets import QApplication
    log = log or (lambda s: None)
    res = {"ok": False, "files": 0, "skipped": 0, "bytes": 0,
           "cancelled": False, "error": None}
    dlg, state = _zip_dialog(parent, "Unzip: extracting…")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.infolist()
            for i, m in enumerate(members):
                if state["cancel"]:
                    res["cancelled"] = True
                    return res
                mname = m.filename.replace("\\", "/")
                parts = [p for p in mname.split("/") if p not in ("", ".")]
                if (not parts or any(p == ".." for p in parts)
                        or ":" in parts[0]):
                    res["skipped"] += 1
                    log(f"Unzip: skipped unsafe entry {m.filename!r}")
                    continue
                dlg.set_status(f"Extracting…\n{mname}")
                dlg.set_progress(int(100 * (i + 1) / max(len(members), 1)))
                QApplication.processEvents()
                target = os.path.join(dest_dir, *parts)
                if m.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(m) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                res["files"] += 1
                res["bytes"] += m.file_size
        res["ok"] = True
    except (zipfile.BadZipFile, RuntimeError, OSError) as ex:
        res["error"] = str(ex)
    finally:
        dlg.close()
    return res


def zip_create_with_dialog(parent, src_paths, zip_local, log=None):
    """Build *zip_local* (deflated) from the local files/folders *src_paths*,
    each archived under its base name (folders recursively, empty folders
    preserved), with a cancellable per-file progress dialog. Returns
    {'ok', 'files', 'cancelled', 'error'}; on cancel or error the
    half-written zip is removed."""
    import zipfile
    from PySide6.QtWidgets import QApplication
    log = log or (lambda s: None)
    res = {"ok": False, "files": 0, "cancelled": False, "error": None}
    # Flatten the work list first so the progress bar can be determinate.
    todo = []          # (full_local_path, arcname, is_dir_entry)
    for src in src_paths:
        base = os.path.basename(src.rstrip("/\\"))
        if not base:
            continue
        if os.path.isdir(src):
            for root, dirs, fnames in os.walk(src):
                dirs.sort()
                rel = os.path.relpath(root, src)
                arc_root = base if rel in (".", "") else \
                    base + "/" + rel.replace(os.sep, "/")
                if not dirs and not fnames:
                    todo.append((root, arc_root + "/", True))
                for fn in sorted(fnames):
                    todo.append((os.path.join(root, fn),
                                 arc_root + "/" + fn, False))
        elif os.path.isfile(src):
            todo.append((src, base, False))
    if not todo:
        res["error"] = "nothing to zip"
        return res
    dlg, state = _zip_dialog(parent, "Zip: compressing…")
    try:
        with zipfile.ZipFile(zip_local, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, (full, arc, is_dir) in enumerate(todo):
                if state["cancel"]:
                    res["cancelled"] = True
                    break
                dlg.set_status(f"Compressing…\n{arc}")
                dlg.set_progress(int(100 * (i + 1) / len(todo)))
                QApplication.processEvents()
                if is_dir:
                    zf.writestr(zipfile.ZipInfo(arc), b"")   # empty folder
                else:
                    zf.write(full, arc)
                    res["files"] += 1
        res["ok"] = not res["cancelled"]
    except OSError as ex:
        res["error"] = str(ex)
    finally:
        dlg.close()
    if not res["ok"]:
        try:
            os.remove(zip_local)
        except OSError:
            pass
    return res


def zip_unique_name(first_name, taken_lower):
    """The zip name for a selection: the FIRST item's name + '.zip',
    uniquified Explorer-style against *taken_lower* (a lower-cased set of
    existing names): 'name.zip', 'name (2).zip', …"""
    name = first_name + ".zip"
    n = 1
    while name.lower() in taken_lower:
        n += 1
        name = f"{first_name} ({n}).zip"
    return name


# Export every public/private module-level name (including the
# underscore-prefixed helpers and caches) so `from <module> import *`
# in the main file picks them all up.
__all__ = [_n for _n in dir() if not _n.startswith('__')]
