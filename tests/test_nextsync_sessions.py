"""The "NextSync — Sessions" setting (9.7.20), Qt-free.

The Remote Explorer's -listen worker seats ONE Next when this is Off — a
newcomer evicts the held link as the same machine coming back (behaviourally
tested in test_listen_single_seat.py); this file pins the wiring around it:
the default-ON decoder shared by the restore stanza, the pane hook and this
test, the cfg key's registration, the Settings row's placement by name right
under Verify CRC, the pane/worker seams (the per-dial hook, the seat's
evicted flag gating the shared-queue pop, the pending-put settle), and the
label / tooltip / console-line strings' parity with every catalog.

Run with: python tests/test_nextsync_sessions.py
"""
import ast
import os
import re
import string
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from zxnu_config import (CONFIG_FILE_SETTINGS, SETTING_NEXTSYNC_SESSIONS,  # noqa: E402
                         SETTING_NEXTSYNC_VERIFY_CRC, nextsync_sessions_enabled)
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
check("an absent key reads as ON (the multi-Next roster)", nextsync_sessions_enabled({}) is True)
check("an empty value (the pre-seed / an upgraded cfg) reads as ON",
      nextsync_sessions_enabled({SETTING_NEXTSYNC_SESSIONS: ""}) is True)
check("'true' reads as ON", nextsync_sessions_enabled({SETTING_NEXTSYNC_SESSIONS: "true"}) is True)
check("' TRUE ' reads as ON", nextsync_sessions_enabled({SETTING_NEXTSYNC_SESSIONS: " TRUE "}) is True)
for off in ("false", "0", "no", "False", " NO "):
    check(f"{off!r} reads as OFF (single seat)",
          nextsync_sessions_enabled({SETTING_NEXTSYNC_SESSIONS: off}) is False)
check("no cfg at all (None) reads as ON", nextsync_sessions_enabled(None) is True)
check("Verify CRC's key is untouched by the new one",
      SETTING_NEXTSYNC_VERIFY_CRC == "nextsync_verify_crc")

# ---- the cfg key ----------------------------------------------------------
check("the key is the documented name", SETTING_NEXTSYNC_SESSIONS == "nextsync_sessions")
check("the key is registered in CONFIG_FILE_SETTINGS (persisted + pre-seeded)",
      SETTING_NEXTSYNC_SESSIONS in CONFIG_FILE_SETTINGS)
check("...right after Verify CRC's key",
      CONFIG_FILE_SETTINGS.index(SETTING_NEXTSYNC_VERIFY_CRC) + 1
      == CONFIG_FILE_SETTINGS.index(SETTING_NEXTSYNC_SESSIONS))

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
check("the row name is registered", "nextsync_sessions" in rows)
check("...right after the verify-CRC row",
      "nextsync_sessions" in rows and "nextsync_verify_crc" in rows
      and rows.index("nextsync_verify_crc") + 1 == rows.index("nextsync_sessions"),
      str(rows[rows.index("nextsync_verify_crc"):][:3] if "nextsync_verify_crc" in rows else rows))
check("the checkbox is placed through the registrar",
      'settings_grid_row("nextsync_sessions")' in pane_src)
check("...never by a literal grid index",
      not re.search(r"addWidget\(host\.settings_nextsync_sessions_checkbox,\s*\d", pane_src))
check("the handler persists true/false and saves",
      "configuration_dictionary[SETTING_NEXTSYNC_SESSIONS] = (" in pane_src)

# ---- the seams ------------------------------------------------------------
cio = src("zxnu_config_io.py")
check("the restore stanza reaches the checkbox", "settings_nextsync_sessions_checkbox" in cio)
check("...through the shared decoder", "nextsync_sessions_enabled(" in cio)
check("...with signals blocked (no re-save mid-load)",
      "host.settings_nextsync_sessions_checkbox.blockSignals(True)" in cio)

npane = src("zxnu_nextsync_pane.py")
check("the worker is handed the toggle as a 0-arg hook",
      '"sessions": lambda: nextsync_sessions_enabled(configuration_dictionary)' in npane)
check("/sessions' max_peers reads the setting live (never the worker's lagging copy)",
      "RE_MAX_PEERS if nextsync_sessions_enabled(" in npane
      and "host._re_control.get('max_peers'" not in npane)
check("every roster event drops the bridge's sid-keyed ident cache",
      "host._re_sig.peers.connect(lambda _p: _re_bridge_forget_idents())" in npane)
brg = src("zxnu_http_bridge.py")
check("the bridge exposes forget_idents", "def forget_idents(self):" in brg
      and "self._ident_cache.clear()" in brg)

wk = src("zxnu_workers.py")
sig_block = wk[wk.find("def run_remote_listen_server("):wk.find('"""', wk.find("def run_remote_listen_server("))]
check("run_remote_listen_server takes sessions=None (every caller unchanged)",
      "sessions=None" in sig_block, sig_block.replace("\n", " ")[:160])
check("the hook is read per dial, not once", wk.count("_sessions_on()") >= 3)
check("Sessions Off never answers Busy (the newcomer evicts instead)",
      "full = (not single) and len(peers) >= RE_MAX_PEERS" in wk)
check("the newcomer inherits the DRIVEN seat's sid",
      "sid = (state['active'] if state['active'] in peers" in wk)
check("the evicted flag gates the shared-queue pop (same sid, never active again)",
      "if _evicted() or state['active'] != sid:" in wk)
check("the evicted seat's socket is shut down from the accept thread",
      "_re_drop_link(_p.get('conn'))" in wk and "conn.shutdown(socket.SHUT_RDWR)" in wk)
check("`first` is taken BEFORE the eviction (no second `connected`)",
      wk.find("first = not peers") < wk.find("if single and peers:")
      and wk.find("first = not peers") != -1)
check("a UI put the session died under settles its one put_done (once its retries are spent)",
      "_retry_spent(\"the link went down mid-file\")" in wk
      and "sig.put_done.emit(False, pending[1])" in wk)
check("...and its error line then stays out of the error signal (one step, not two)",
      wk.count("_ui_put_pending()") >= 3 and "_ui_put_pending() or bool(vjobs)" in wk)
check("control['max_peers'] tracks the mode",
      "control['max_peers'] = 1 if single else RE_MAX_PEERS" in wk)
sess = wk[wk.find("def _re_session("):wk.find("def run_remote_listen_server(")]
check("no raw idle send is left in the session (every 'I' rides _idle)",
      '_re_sendpacket(conn, b"I", 0)' not in sess and sess.count("_idle(") >= 25)
check("Later / Ok / Back ride _idle too",
      all(f"_idle({lit})" in sess for lit in ('b"Later"', 'b"Ok"', 'b"Back"')))
check("_ReLinkGone is caught ahead of OSError",
      sess.find("except _ReLinkGone as ex:") != -1
      and sess.find("except _ReLinkGone as ex:") < sess.find("\n    except OSError as ex:"))
check("the finally settles an open rmtree walk once",
      'sig.op_done.emit(False, "delete", _job[\'root\'])' in sess
      and "rmtree_jobs.clear()" in sess)
check("rmtree_jobs is declared pre-try (the finally can see it)",
      sess.find("rmtree_jobs = {}") < sess.find("\n    try:"))
check("the except arms defer to the finally / the bridge sink",
      sess.count("_report_owed_elsewhere()") >= 4)
check("a re-seat asks the returning Next its build again",
      'my_q.put(("version",))' in wk)
check("a re-seat inherits the commands parked on the old seat's queue",
      "my_q.put(_p['q'].get_nowait())" in wk)
# ---- link-loss retries (9.7.20) -------------------------------------------
import zxnu_workers  # noqa: E402
check("three retries, three seconds apart, a 30 s wait for the Next",
      zxnu_workers.RE_LINK_RETRIES == 3 and zxnu_workers.RE_LINK_RETRY_PAUSE_S == 3.0
      and zxnu_workers.RE_LINK_RETRY_WAIT_S == 30.0)
check("retried ops are the idempotent UI ones - never rmtree, never a raw query",
      set(zxnu_workers.RE_LINK_RETRY_OPS) == {"ls", "get", "put", "mkdir", "rmdir", "rm",
                                              "rename", "rcpy", "fsize"})
check("EOF is told apart from garbage by the block reader",
      "return 'EOF'" in wk and "if blk == 'EOF':" in wk)
check("the session shadows _re_reply_call and raises _ReLinkDead when eligible",
      "def _re_reply_call(conn_, handler, timeout=None):" in sess
      and 'raise _ReLinkDead("the link died under "' in sess)
check("_ReLinkDead is an OSError caught ahead of the OSError arm",
      "class _ReLinkDead(OSError):" in wk
      and sess.find("except _ReLinkDead as ex:") != -1
      and sess.find("except _ReLinkDead as ex:") < sess.find("\n    except OSError as ex:"))
check("a held retry is served first and holds the queue behind its pause",
      "r = control.get('retry')" in sess and "elif time.monotonic() < r['due']:" in sess)
check("only a shared-queue pop can be held (never my_q / local_cmds)",
      "from_shared = cmd is not None" in sess
      and "if from_shared and reply is None and op in RE_LINK_RETRY_OPS:" in sess)
check("a pending put is held by the finally, not reported",
      "if _ui_put_pending():\n            if _retry_eligible():" in sess)
check("the accept loop keeps listening while a retry is held",
      "waiting = (r is not None and not _sessions_on()" in wk
      and "time.monotonic() < r['deadline'])" in wk)
check("the worker's finally drops a held retry (stop / error)",
      "control.pop('retry', None)\n        sig.disconnected.emit()" in wk)
check("the pane's cancel drops a held retry AND counts it (the op must not hang)",
      "n = 1 if host._re_control.pop('retry', None) is not None else 0" in npane)
check("the control dict is defaulted above the bind (the finally always has one)",
      wk.find("control = control if control is not None else {}")
      < wk.find("srv = bind_listen_socket(port)"))
check("each worker run stamps a retry generation",
      "re_gen = int(control.get('gen', 0)) + 1" in wk
      and "'gen': shared.get('gen')" in wk)
check("...and a retry from another run is never served",
      "if r is not None and r.get('gen') != shared.get('gen'):" in wk
      and "if r is not None and r.get('gen') != re_gen:" in wk)
check("...while the finally pops only its own",
      "if (control.get('retry') or {}).get('gen') == re_gen:" in wk)
check("the retry log lines are untranslated protocol-style lines (retry1: ...)",
      '"retry%d in %ds: %s (%s%s)"' in sess and '"retry%d: %s"' in sess)
# 9.7.21: that stash line carries how far the transfer got, from the same
# session-level record every failure site reads.
check("...and the stash line carries the transfer's progress",
      "_xfer_note()" in sess and "def _xfer_note():" in sess)
check("the transfer record is command-scoped, not session-scoped",
      "'live': False}" in wk and "xfer['live'] = False" in wk
      and "xfer['kind'] == 'get' and xfer['live'] and not stashed" in wk)
check("the stopped-transfer line is emitted once, by whoever gets there first",
      "xfer['said'] = True" in sess
      and "not xfer['live']" in sess and "or xfer['said']" in sess
      and "not xfer['done']" in sess)
check("...and it reads the console's own KB/MB wording",
      "log_size(xfer['done'])" in sess and "log_size," in wk)
check("the classic Sync3/Sync4 loop is untouched",
      "sessions" not in wk[wk.find("def run_classic_sync_server("):
                           wk.find("def run_classic_sync_server(") + 2000])

rex = src("zxnu_remote_explorer.py")
check("the widget is untouched: it learns nothing of the mode", "nextsync_sessions" not in rex)

# ---- catalog parity: label, tooltip, the two console templates -----------
label = None
tooltip = None
for node in ast.walk(pane_ast):
    if not isinstance(node, ast.Call):
        continue
    f = node.func
    if (isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Attribute)
            and f.value.attr == "settings_nextsync_sessions_checkbox"
            and f.attr == "setToolTip" and node.args
            and isinstance(node.args[0], ast.Constant)):
        tooltip = node.args[0].value
    if (isinstance(f, ast.Name) and f.id == "QCheckBox" and node.args
            and isinstance(node.args[0], ast.Constant)
            and str(node.args[0].value).startswith("NextSync — Sessions")):
        label = node.args[0].value
check("the pane's label literal found",
      label == "NextSync — Sessions: seat several Nexts at once (Remote Explorer)", repr(label))
check("the pane's tooltip literal found (one folded constant)",
      isinstance(tooltip, str) and tooltip.count("\n") == 17, repr(tooltip)[:80])
check("...and it says a cut command is retried, three times, three seconds apart",
      isinstance(tooltip, str) and "retried up to three times, three seconds apart" in tooltip
      and "retry1, retry2" in tooltip)
check("...and it says what a flip to Off with several seats held does",
      isinstance(tooltip, str) and "replaces them all" in tooltip)

EVICT = ("Remote explorer: {address} dialed in while {old} was seated — Sessions is "
         "Off, so it is taken for the same Next coming back: the old link is dropped "
         "and the newcomer takes its place.")
LOST = ("the Next dialed in again while a command was in flight — Sessions is Off, "
        "so the old link was dropped and that command was lost")
check("the worker emits the eviction template as a ui_tr_now literal",
      '"Remote explorer: {address} dialed in while {old} was "' in wk)
check("the worker emits the lost-command template as a ui_tr_now literal",
      '"the Next dialed in again while a command was in flight — "' in wk)


def fields(s):
    return {f for _lit, f, _spec, _conv in string.Formatter().parse(s) if f}


for code in ("es", "pt", "pl", "ru", "cs", "fr"):
    cat = CATALOGS[code]
    for what, key in (("label", label), ("tooltip", tooltip),
                      ("eviction line", EVICT), ("lost-command line", LOST)):
        val = cat.get(key) if key else None
        check(f"{code}: {what} translated", bool(val) and val != key)
        if val:
            check(f"{code}: {what} keeps its placeholders", fields(val) == fields(key),
                  f"{fields(val)} != {fields(key)}")
    if tooltip and cat.get(tooltip):
        check(f"{code}: tooltip keeps the line structure",
              cat[tooltip].count("\n") == tooltip.count("\n"),
              str(cat[tooltip].count("\n")))
        for verbatim in ("'.sync5 -listen'", "'.sync5'", "NextSync",
                         "ZX Next Remote", "1.2.5", "n2n", "Busy"):
            check(f"{code}: tooltip keeps {verbatim} verbatim", verbatim in cat[tooltip])
    for key in (label, tooltip, EVICT, LOST):
        val = cat.get(key) or "" if key else ""
        check(f"{code}: no protocol diagnostic leaked into {key[:24]!r}",
              not any(m in val for m in ("checksums", "packetno", "Packet sequence error",
                                         "Using protocol version", "Unknown command")))

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all NextSync Sessions checks passed")
sys.exit(0)
