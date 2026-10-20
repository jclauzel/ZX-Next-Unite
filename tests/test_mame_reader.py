"""MAME's output reader survives what it reads (9.7.52).

launch_mame started MAME with Popen(text=True), i.e. the STRICT locale codec -
cp1252 on Windows, where bytes such as 0x81 and 0x9D are undefined - and read
it with a plain ``for line in proc.stdout``. One undecodable byte raised out of
that loop, and the reader's finally reported MAME FINISHED while it was still
running: the ESP proxy reference was released under it, and MAME then blocked
for good on the pipe nobody drained any more. Its finished signal was also a
Signal(int), which wraps a Windows crash code such as 3221225477 to a negative
number (measured, the CSpect twin of this fixed in 9.7.51).

Covered here, headlessly (CI runs this): the module-level _read_mame_output
driven with fake processes, the real MameProcessSignals across a thread, the
UTF-8 decode option, and source tripwires on the launch.

Run with: python tests/test_mame_reader.py
"""
import ast
import io
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import zxnu_emulator_ops as ops  # noqa: E402
from zxnu_config import decode_console_line, describe_exit_code  # noqa: E402
from zxnu_workers import MameProcessSignals  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


class FakeSignals:
    """Records what the reader reports, in order."""

    def __init__(self, events):
        self.events = events
        outer = self

        class _Sig:
            def __init__(self, kind):
                self.kind = kind

            def emit(self, value):
                outer.events.append((self.kind, value))

        self.output = _Sig("line")
        self.finished = _Sig("finished")


class FakeProc:
    def __init__(self, data, code, events, stream=None):
        self.stdout = stream if stream is not None else io.BytesIO(data)
        self.returncode = None
        self._code = code
        self._events = events

    def wait(self, timeout=None):
        self._events.append(("wait", None))
        self.returncode = self._code
        return self._code


# ---- the decode option ---------------------------------------------------------
check("UTF-8 text decodes as UTF-8, not cp1252 mojibake",
      decode_console_line("Démarrage\r\n".encode("utf-8"), windows=True,
                          encoding="utf-8") == "Démarrage")
check("a byte cp1252 has no character for never raises",
      "�" in decode_console_line(b"bad \x81\x9d end\n", windows=True,
                                      encoding="utf-8"))
check("an unknown codec name falls back instead of raising",
      decode_console_line(b"plain\n", windows=False,
                          encoding="no-such-codec") == "plain")
check("without an encoding the CSpect behaviour is unchanged",
      decode_console_line(b"abc\r\n", windows=False) == "abc")

# ---- the reader with a stream a strict cp1252 decode would die on ----------------
# 0x81 / 0x9D are undefined in cp1252: the old text-mode loop raised on the
# second line and reported "finished" before MAME had exited.
events = []
data = ("MAME 0.289 starting\r\n".encode("utf-8")
        + b"bad bytes \x81\x9d here\r\n"
        + "Système prêt\r\n".encode("utf-8")
        + b"last line\r\n")
ops._read_mame_output(FakeProc(data, 0, events), FakeSignals(events))
lines = [v for k, v in events if k == "line"]
check("every line is forwarded, the undecodable one included",
      len(lines) == 4 and lines[0] == "MAME 0.289 starting"
      and lines[2] == "Système prêt" and lines[3] == "last line", repr(lines))
check("the reader waits for MAME BEFORE reporting it finished",
      [k for k, _ in events][-2:] == ["wait", "finished"], repr(events[-2:]))
check("finished is reported exactly once, with the exit code",
      [v for k, v in events if k == "finished"] == [0])

# ---- a line the forwarder chokes on does not end the drain -----------------------
events = []


class Choosy(FakeSignals):
    def __init__(self, events):
        super().__init__(events)
        real = self.output

        class _Sig:
            def emit(self, value):
                if "boom" in value:
                    raise RuntimeError("forwarder failed")
                real.emit(value)

        self.output = _Sig()


ops._read_mame_output(FakeProc(b"one\nboom\nthree\n", 3, events), Choosy(events))
check("a line that fails to forward is skipped, the drain carries on",
      [v for k, v in events if k == "line"] == ["one", "three"], repr(events))
check("...and the exit still follows the wait",
      [k for k, _ in events][-2:] == ["wait", "finished"])


# ---- a stream that breaks mid-read still ends in wait + one finished -------------
class Broken(io.BytesIO):
    def __init__(self):
        super().__init__(b"")
        self.calls = 0

    def readline(self, size=-1):
        self.calls += 1
        if self.calls == 1:
            return b"first\n"
        raise OSError("pipe broke")


events = []
ops._read_mame_output(FakeProc(b"", 7, events, stream=Broken()), FakeSignals(events))
check("a broken pipe is reported, then waited for, then finished once",
      [k for k, _ in events].count("finished") == 1
      and [k for k, _ in events][-2:] == ["wait", "finished"]
      and any(k == "line" and "ERROR reading MAME output" in v for k, v in events),
      repr(events))

# ---- the real signal across a thread ---------------------------------------------
got = []
sig = MameProcessSignals()
sig.finished.connect(lambda rc: got.append(rc), Qt.QueuedConnection)
t = threading.Thread(target=lambda: sig.finished.emit(3221225477))
t.start()
t.join()
deadline = time.monotonic() + 5
while not got and time.monotonic() < deadline:
    QCoreApplication.processEvents()
    time.sleep(0.01)
check("a Windows crash code crosses the thread unchanged (no signed wrap)",
      got == [3221225477], repr(got))
check("...and is named in the log", bool(got)
      and "access violation" in describe_exit_code(got[0]))

# ---- source tripwires on the launch ----------------------------------------------
src = open(os.path.join(REPO, "zxnu_emulator_ops.py"), encoding="utf-8").read()
start = src.find("def launch_mame(")
end = src.find("\n    def ", start + 1)
body = src[start:end]
check("launch_mame is found and bounded", start > 0 and end > start)
check("MAME's output is read as bytes (no strict text mode)",
      "text=True" not in body and "bufsize=1" not in body)
check("the reader thread runs the module-level reader",
      "target=_read_mame_output," in body and "def _read_mame_output" not in body)
tree = ast.parse(src)
reader = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_read_mame_output")
rsrc = ast.get_source_segment(src, reader)
check("the reader decodes MAME as UTF-8",
      'decode_console_line(raw, encoding="utf-8")' in rsrc)
check("the wait sits in the finally, ahead of the finished emit",
      rsrc.index("finally:") < rsrc.index("proc.wait()")
      < rsrc.index("signals.finished.emit"))
check("the names other tests pin are still there",
      "_launch_state" in body and "_mame_signals_live" in body)

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all MAME reader checks passed")
sys.exit(0)
