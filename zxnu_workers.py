"""Background worker, signal and progress-dialog classes for zx-next-unite.

Extracted from zx-next-unite.py."""

import threading
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

    def closeEvent(self, event):
        self._anim_timer.stop()
        super().closeEvent(event)


# Export every public/private module-level name (including the
# underscore-prefixed helpers and caches) so `from <module> import *`
# in the main file picks them all up.
__all__ = [_n for _n in dir() if not _n.startswith('__')]
