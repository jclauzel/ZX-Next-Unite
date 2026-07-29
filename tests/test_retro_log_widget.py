"""RetroLogWidget.set_text_color + _decode_text_bytes unit tests.

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

# ---- _decode_text_bytes (item-viewer text console) --------------------------
# Some ZXDB instruction .txt files are UTF-16 with BOM (e.g. Willy's New
# Mansion - Special Edition); without BOM handling they decoded via the
# latin-1 fallback into NUL-riddled mojibake.
from zxnu_pygame import _decode_text_bytes

check("decode: plain utf-8", _decode_text_bytes("héllo\nx".encode("utf-8")) == ["héllo", "x"])
_u8bom = b"\xef\xbb\xbf" + "hi".encode("utf-8")
check("decode: utf-8 BOM stripped", _decode_text_bytes(_u8bom) == ["hi"],
      str(_decode_text_bytes(_u8bom)))
_u16le = b"\xff\xfe" + "Willy's\r\nMansion".encode("utf-16-le")
check("decode: utf-16 LE BOM", _decode_text_bytes(_u16le) == ["Willy's", "Mansion"],
      str(_decode_text_bytes(_u16le)))
_u16be = b"\xfe\xff" + "AB".encode("utf-16-be")
check("decode: utf-16 BE BOM", _decode_text_bytes(_u16be) == ["AB"],
      str(_decode_text_bytes(_u16be)))
check("decode: cp1252 fallback", _decode_text_bytes(b"caf\xe9") == ["café"],
      str(_decode_text_bytes(b"caf\xe9")))
check("decode: empty", _decode_text_bytes(b"") == [])

# ── right-click font stepping (hosted via set_font_step_cb) ──────────────
w3 = RetroLogWidget()
check("no opt-in -> no context menu", w3._build_context_menu() is None)
steps = []
w3.set_font_step_cb(steps.append)
menu = w3._build_context_menu()
labels = [a.text() for a in menu.actions() if not a.isSeparator()]
check("font menu offers increase (with current px) and decrease",
      len(labels) == 2 and labels[0].startswith("Increase font size")
      and str(w3._font_px) in labels[0]
      and labels[1] == "Decrease font size")
menu.actions()[0].trigger()
menu.actions()[1].trigger()
check("menu actions step the host callback +1/-1", steps == [1, -1])
w4 = RetroLogWidget(context_copy=True)
w4.set_font_step_cb(lambda d: None)
m4 = w4._build_context_menu()
check("copy + font entries coexist (separated)",
      [a.text() for a in m4.actions() if not a.isSeparator()][0]
      == "Copy all text"
      and any(a.isSeparator() for a in m4.actions())
      and len([a for a in m4.actions() if not a.isSeparator()]) == 3)

print()
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILURE(S)")
    sys.stdout.flush()
    os._exit(1)
print("RESULT: ALL WIDGET COLOR + TEXT DECODE CHECKS PASSED")
sys.stdout.flush()
# Hard-exit BOTH ways: skip the Qt + pygame interpreter teardown, which
# segfaults intermittently on CI (Linux + Python 3.10 under Xvfb) AFTER every
# check has already passed — the exit status must reflect the checks above,
# never the cleanup.
os._exit(0)
