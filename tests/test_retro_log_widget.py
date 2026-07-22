"""RetroLogWidget.set_text_color unit test.

Runs with the REAL Qt platform (no window is ever shown): pygame-dependent
classes crash natively only under QT_QPA_PLATFORM=offscreen, so unlike the
UI suite this one must NOT set offscreen.

Run with: python test_retro_log_widget.py
"""
import os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
from zxnu_pygame import RetroLogWidget

FAIL = []
def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)

w = RetroLogWidget()
check("default is phosphor green", w._C_LOG == (120, 255, 140), str(w._C_LOG))
w.set_text_color("#112233")
check("hex string applied", w._C_LOG == (0x11, 0x22, 0x33), str(w._C_LOG))
w.set_text_color((1, 2, 3))
check("rgb tuple applied", w._C_LOG == (1, 2, 3), str(w._C_LOG))
w.set_text_color((300, -5, 128))
check("tuple clamped to 0..255", w._C_LOG == (255, 0, 128), str(w._C_LOG))
w.set_text_color("garbage")
check("bad string falls back to green", w._C_LOG == (120, 255, 140), str(w._C_LOG))
w.set_text_color("#112233")
w.set_text_color(None)
check("None falls back to green", w._C_LOG == (120, 255, 140), str(w._C_LOG))
w2 = RetroLogWidget()
w.set_text_color("#ff0000")
check("per-instance tint (other widget untouched)",
      w2._C_LOG == (120, 255, 140) and w._C_LOG == (255, 0, 0),
      f"{w._C_LOG} / {w2._C_LOG}")

print()
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILURE(S)")
    sys.exit(1)
print("RESULT: ALL WIDGET COLOR CHECKS PASSED")
