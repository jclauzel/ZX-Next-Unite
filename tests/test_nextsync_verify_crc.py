"""The "NextSync — Verify CRC" setting (9.7.3), Qt-free.

The Remote Explorer's -listen worker verifies every UI put with the dot's 'K'
op (behaviourally tested in test_remote_listen.py); this file pins the wiring
around it: the default-ON decoder shared by the restore stanza, the pane hook
and this test, the cfg key's registration, the Settings row's placement by
name, the pane/worker/log seams, and the tooltip + red-verdict strings' parity
with every catalog.

Run with: python tests/test_nextsync_verify_crc.py
"""
import ast
import os
import re
import string
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from zxnu_config import (CONFIG_FILE_SETTINGS, SETTING_NEXTSYNC_VERIFY_CRC,  # noqa: E402
                         nextsync_verify_crc_enabled)
import zxnu_i18n  # noqa: E402
from zxnu_i18n import CATALOGS  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def src(name):
    return open(os.path.join(REPO, name), encoding="utf-8").read()


# ---- the default-ON decoder ----------------------------------------------
check("an absent key reads as ON", nextsync_verify_crc_enabled({}) is True)
check("an empty value (the pre-seed) reads as ON",
      nextsync_verify_crc_enabled({SETTING_NEXTSYNC_VERIFY_CRC: ""}) is True)
check("'true' reads as ON", nextsync_verify_crc_enabled({SETTING_NEXTSYNC_VERIFY_CRC: "true"}) is True)
check("' TRUE ' reads as ON", nextsync_verify_crc_enabled({SETTING_NEXTSYNC_VERIFY_CRC: " TRUE "}) is True)
for off in ("false", "0", "no", "False"):
    check(f"{off!r} reads as OFF",
          nextsync_verify_crc_enabled({SETTING_NEXTSYNC_VERIFY_CRC: off}) is False)
check("no cfg at all (None) reads as ON", nextsync_verify_crc_enabled(None) is True)

# ---- the cfg key ----------------------------------------------------------
check("the key is the documented name", SETTING_NEXTSYNC_VERIFY_CRC == "nextsync_verify_crc")
check("the key is registered in CONFIG_FILE_SETTINGS (persisted + pre-seeded)",
      SETTING_NEXTSYNC_VERIFY_CRC in CONFIG_FILE_SETTINGS)

# ---- the Settings row, placed by NAME ------------------------------------
pane_src = src("zxnu_settings_pane.py")
pane_ast = ast.parse(pane_src)
rows = None
for node in pane_ast.body:
    if (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "SETTINGS_TAB_ROWS"):
        rows = ast.literal_eval(node.value)
check("SETTINGS_TAB_ROWS parsed", rows is not None)
rows = rows or ()
check("the row name is registered", "nextsync_verify_crc" in rows)
check("...right after the send-conflict row",
      "nextsync_verify_crc" in rows and "nextsync_send_conflict" in rows
      and rows.index("nextsync_send_conflict") + 1 == rows.index("nextsync_verify_crc"),
      str(rows[rows.index("nextsync_send_conflict"):][:3] if "nextsync_send_conflict" in rows else rows))
check("the checkbox is placed through the registrar",
      'settings_grid_row("nextsync_verify_crc")' in pane_src)
check("...never by a literal grid index",
      not re.search(r"addWidget\(host\.settings_nextsync_verify_crc_checkbox,\s*\d", pane_src))

# ---- the seams ------------------------------------------------------------
cio = src("zxnu_config_io.py")
check("the restore stanza reaches the checkbox", "settings_nextsync_verify_crc_checkbox" in cio)
check("...through the shared decoder", "nextsync_verify_crc_enabled(" in cio)

npane = src("zxnu_nextsync_pane.py")
check("the worker is handed the toggle as a 0-arg hook",
      '"verify_crc": lambda: nextsync_verify_crc_enabled(configuration_dictionary)' in npane)
check("the red verdict signal is wired to the pane slot", "put_verify_failed.connect(" in npane)

rex = src("zxnu_remote_explorer.py")
check("the widget is untouched: no crc command", '("crc"' not in rex)
check("the widget is untouched: no verify plumbing", "put_verify" not in rex)

wk = src("zxnu_workers.py")
a = wk.find('elif op == "put_verify":')
b = wk.find('elif op == "drives":', a)
check("the verify arms exist, ahead of drives", a != -1 and b != -1 and a < b)
check("the internal 'X' never reports an op_done (the widget counted nothing)",
      a != -1 and b != -1 and "sig.op_done" not in wk[a:b])
sig_line = next((ln for ln in wk.splitlines() if ln.startswith("def run_remote_listen_server(")), "")
sig_block = wk[wk.find("def run_remote_listen_server("):wk.find('"""', wk.find("def run_remote_listen_server("))]
check("run_remote_listen_server takes verify_crc=None (every caller unchanged)",
      "verify_crc=None" in sig_block, sig_line)
check("...and stores it in shared for the sessions", "'verify_crc': verify_crc" in wk)

sd = src("zxnu_sdcard_ops.py")
d = sd.find("def add_nextsync_log_window(")
e = sd.find("):", d)
check("add_nextsync_log_window takes an optional color", d != -1 and "color=None" in sd[d:e])

# ---- catalog parity: label, tooltip, red templates, toast title -----------
label = None
tooltip = None
for node in ast.walk(pane_ast):
    if not isinstance(node, ast.Call):
        continue
    f = node.func
    if (isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Attribute)
            and f.value.attr == "settings_nextsync_verify_crc_checkbox"
            and f.attr == "setToolTip" and node.args
            and isinstance(node.args[0], ast.Constant)):
        tooltip = node.args[0].value
    if (isinstance(f, ast.Name) and f.id == "QCheckBox" and node.args
            and isinstance(node.args[0], ast.Constant)
            and str(node.args[0].value).startswith("NextSync — Verify CRC")):
        label = node.args[0].value
check("the pane's label literal found",
      label == "NextSync — Verify CRC of every file sent to the Next (Remote Explorer)", repr(label))
check("the pane's tooltip literal found (one folded constant)",
      isinstance(tooltip, str) and tooltip.count("\n") == 7, repr(tooltip)[:80])

RED_DEL = ("CRC-32 verification FAILED for {path}: {sent} was sent but the Next holds "
           "{got}. The corrupted copy has been deleted from the Next — send the file again.")
RED_KEPT = ("CRC-32 verification FAILED for {path}: {sent} was sent but the Next holds "
            "{got}. The corrupted copy could NOT be deleted from the Next ({reason}) — "
            "remove it by hand and send the file again.")
TOAST = "❌  NextSync CRC-32 verification failed"
check("the worker emits both red templates as literals",
      wk.count('"CRC-32 verification FAILED for {path}: {sent} was "') >= 2
      and "has been deleted from the Next" in wk
      and "could NOT be deleted from the Next ({reason})" in wk)
check("the pane toasts the documented title", TOAST in npane)


def fields(s):
    return {f for _lit, f, _spec, _conv in string.Formatter().parse(s) if f}


toasts = getattr(zxnu_i18n, "_TOAST_CATALOGS", {})
for code in ("es", "pt", "pl", "ru", "cs", "fr"):
    cat = CATALOGS[code]
    for what, key in (("label", label), ("tooltip", tooltip),
                      ("red-deleted", RED_DEL), ("red-kept", RED_KEPT)):
        val = cat.get(key) if key else None
        check(f"{code}: {what} translated", bool(val) and val != key)
        if val:
            check(f"{code}: {what} keeps its placeholders", fields(val) == fields(key),
                  f"{fields(val)} != {fields(key)}")
    if tooltip and cat.get(tooltip):
        check(f"{code}: tooltip keeps the line structure",
              cat[tooltip].count("\n") == tooltip.count("\n"),
              str(cat[tooltip].count("\n")))
        for verbatim in ("'.sync5 -listen'", "'.sync5'", "CRC-32", "NextSync",
                         "ZX Next Remote", "v5.9.2+", "1.0.8+"):
            check(f"{code}: tooltip keeps {verbatim} verbatim", verbatim in cat[tooltip])
    tv = cat.get(TOAST) or toasts.get(code, {}).get(TOAST)
    check(f"{code}: toast title translated", bool(tv) and tv != TOAST and tv.startswith("❌  "),
          repr(tv))
    for key in (label, tooltip, RED_DEL, RED_KEPT, TOAST):
        val = (cat.get(key) or toasts.get(code, {}).get(key) or "") if key else ""
        check(f"{code}: no protocol diagnostic leaked into {key[:24]!r}",
              not any(m in val for m in ("checksums", "packetno", "Packet sequence error",
                                         "Using protocol version", "Unknown command")))

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all NextSync verify-CRC checks passed")
sys.exit(0)
