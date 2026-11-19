"""The gallery sends ask WHERE to send when there is a choice.

"Send to SD card" used to go to whichever disk image was loaded and "Send via
NextSync" to whichever Next the Remote Explorer was driving. Both can be one
of several now (the image box remembers images; several Nexts can be seated),
so with two or more candidates the user picks from a list first:

  * host._pick_send_image(then)       (zxnu_emulator_ops) - the real closure
    on a fake host: no question below two writable images, the loaded image
    preselected, a different pick LOADED before the send runs, Cancel and a
    failed load send nothing.
  * _re_pick_send_session(then) and _re_try_send_folder(folder)
    (zxnu_nextsync_pane) - the real function bodies, taken out of the builder
    by AST and run against a fake widget: no question below two Nexts, the
    driven one preselected, a different pick asks for the baton and waits
    for that machine's listing, a switch that never lands sends nothing.
  * every gallery pane's send-to-image goes through the image picker, and
    every new text is in all six catalogs.
"""
import ast
import os
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import zxnu_emulator_ops  # noqa: E402
import zxnu_i18n  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ((" - " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def src(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


# ---- the image picker: real closure, fake host ----------------------------
print("== host._pick_send_image ==")
work = tempfile.mkdtemp(prefix="zxnu-send-pick-")
images = []
for n in ("a.img", "b.img", "c.img"):
    p = os.path.join(work, n)
    with open(p, "wb") as fh:
        fh.write(b"\0" * 512)
    images.append(zxnu_emulator_ops.normalize_sd_image_path(p))


class _Combo:
    def __init__(self, items):
        self.items = list(items)
        self.text = self.items[0] if self.items else ""

    def count(self):
        return len(self.items)

    def itemText(self, i):
        return self.items[i]

    def currentText(self):
        return self.text

    def setCurrentText(self, text):
        self.text = text


class FakeHost:
    def __init__(self, items):
        self.imageinput = _Combo(items)
        self.right_disk_image_path = items[0] if items else ""
        self.toasts = []
        self.loads = []
        self.load_ok = True

    def load_image(self, on_done=None):
        self.loads.append(self.imageinput.currentText())
        if self.load_ok:
            self.right_disk_image_path = self.imageinput.currentText()
        if on_done is not None:
            on_done(self.load_ok)

    def _show_toast(self, title, body="", **_kw):
        self.toasts.append((title, body))


class FakeInput:
    """QInputDialog.getItem, scripted: answer = an index, or None = Cancel."""
    answer = None
    calls = []

    @classmethod
    def getItem(cls, _parent, title, label, items, current, editable):
        cls.calls.append((title, list(items), current, editable))
        if cls.answer is None:
            return ("", False)
        return (items[cls.answer], True)


def make_host(items):
    host = FakeHost(items)
    zxnu_emulator_ops.build_emulator_ops(
        host, configuration_dictionary={}, save_configuration_file=lambda: None,
        set_all_buttons_disabled=lambda: None, set_all_buttons_enabled=lambda: None,
        _update_mame_controls=lambda: None, _right_disk_content=lambda: [("x", 0)],
        getit_run_in_thread=lambda *a, **k: None,
        _start_load_image_hint_animation=lambda: None,
        add_main_log_window=lambda *_a, **_k: None,
        execute_shell_command=lambda *a, **k: None)
    return host


_real_input = zxnu_emulator_ops.QInputDialog
zxnu_emulator_ops.QInputDialog = FakeInput
try:
    sent = []
    host = make_host(images[:1])
    del FakeInput.calls[:]
    host._pick_send_image(lambda: sent.append(host.right_disk_image_path))
    check("one image: no question, the send runs",
          not FakeInput.calls and sent == [images[0]], str((FakeInput.calls, sent)))

    host = make_host(images)
    del FakeInput.calls[:], sent[:]
    FakeInput.answer = 0
    host._pick_send_image(lambda: sent.append(host.right_disk_image_path))
    check("three images: the question lists them all, not editable",
          len(FakeInput.calls) == 1 and FakeInput.calls[0][1] == images
          and FakeInput.calls[0][3] is False, str(FakeInput.calls))
    check("the loaded image is preselected", FakeInput.calls[0][2] == 0)
    check("picking the loaded image sends without a reload",
          sent == [images[0]] and not host.loads, str((sent, host.loads)))

    host.right_disk_image_path = images[1]
    del FakeInput.calls[:]
    FakeInput.answer = None
    host._pick_send_image(lambda: sent.append("x"))
    check("the preselection follows the loaded image",
          FakeInput.calls and FakeInput.calls[0][2] == 1, str(FakeInput.calls))
    check("Cancel sends nothing and loads nothing",
          sent == [images[0]] and not host.loads, str((sent, host.loads)))

    del sent[:]
    FakeInput.answer = 2
    host._pick_send_image(lambda: sent.append(host.right_disk_image_path))
    check("another pick is loaded FIRST, then the send runs on it",
          host.loads == [images[2]] and sent == [images[2]],
          str((host.loads, sent)))

    host = make_host(images)
    host.load_ok = False
    del sent[:]
    FakeInput.answer = 1
    host._pick_send_image(lambda: sent.append("x"))
    check("a pick that fails to load sends nothing and says so",
          not sent and len(host.toasts) == 1 and images[1] in host.toasts[0][1],
          str((sent, host.toasts)))

    os.remove(images[2])
    host = make_host(images)
    del FakeInput.calls[:]
    FakeInput.answer = 0
    host._pick_send_image(lambda: None)
    check("an image whose file is gone is not offered",
          FakeInput.calls and FakeInput.calls[0][1] == images[:2],
          str(FakeInput.calls))
finally:
    zxnu_emulator_ops.QInputDialog = _real_input

# ---- the Next picker: real bodies, fake widget ----------------------------
print("== _re_pick_send_session / _re_try_send_folder ==")
pane_src = src("zxnu_nextsync_pane.py")
tree = ast.parse(pane_src)
wanted = {}
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name in (
            "_re_pick_send_session", "_re_try_send_folder"):
        wanted[node.name] = textwrap.dedent(
            ast.get_source_segment(pane_src, node))
check("both functions exist in zxnu_nextsync_pane", len(wanted) == 2,
      str(sorted(wanted)))


class FakeWidget:
    def __init__(self, peers, active):
        self._connected = True
        self._peer_map = list(peers)
        self._peer_active = active
        self._op_active = False
        self._precheck = None
        self._listing_seq = 7
        self.requests = []
        self.grant = True

    def _machine_label(self, sid, addr):
        return "%s #%s" % (addr, sid)

    def _request_machine(self, sid):
        self.requests.append(sid)
        if self.grant:
            self._peer_active = sid
            self._listing_seq += 1


class NsHost:
    def __init__(self, widget):
        self._re_widget = widget
        self._re_running = True
        self.toasts = []

    def _show_toast(self, title, body="", **_kw):
        self.toasts.append((title, body))


class Clock:
    now = 0.0

    @classmethod
    def monotonic(cls):
        cls.now += 5.0
        return cls.now


class Timer:
    @staticmethod
    def singleShot(_ms, fn):
        fn()


def ns_env(widget):
    logs, direct = [], []
    env = {"host": NsHost(widget), "QInputDialog": FakeInput, "QTimer": Timer,
           "time": Clock, "os": os, "ui_tr_now": lambda s: s,
           "add_nextsync_log_window": logs.append,
           "_RE_SEND_SWITCH_WAIT_S": 20,
           "_re_send_folder_now": lambda f: direct.append(f) or "sent"}
    exec(wanted["_re_pick_send_session"], env)
    exec(wanted["_re_try_send_folder"], env)
    return env, logs, direct


PEERS = [(1, "192.168.1.20"), (2, "192.168.1.21"), (3, "192.168.1.22")]

w = FakeWidget(PEERS[:1], 1)
env, logs, direct = ns_env(w)
del FakeInput.calls[:]
ran = []
env["_re_pick_send_session"](lambda: ran.append(1))
check("one Next: no question", ran == [1] and not FakeInput.calls)
check("one Next: _re_try_send_folder hands straight to the send",
      env["_re_try_send_folder"](work) == "sent" and direct == [work])

w = FakeWidget(PEERS, 2)
env, logs, direct = ns_env(w)
del FakeInput.calls[:], ran[:]
FakeInput.answer = 1
env["_re_pick_send_session"](lambda: ran.append(1))
check("three Nexts: the question lists the session labels",
      len(FakeInput.calls) == 1 and FakeInput.calls[0][1] ==
      ["192.168.1.20 #1", "192.168.1.21 #2", "192.168.1.22 #3"],
      str(FakeInput.calls))
check("the driven Next is preselected", FakeInput.calls[0][2] == 1)
check("picking the driven Next sends with no baton request",
      ran == [1] and not w.requests)

del ran[:]
FakeInput.answer = None
env["_re_pick_send_session"](lambda: ran.append(1))
check("Cancel sends nothing", not ran and not w.requests)

FakeInput.answer = 2
env["_re_pick_send_session"](lambda: ran.append(w._peer_active))
check("another pick asks for the baton, then sends once it is driven",
      w.requests == [3] and ran == [3] and len(logs) == 1,
      str((w.requests, ran, logs)))

w = FakeWidget(PEERS, 1)
w.grant = False
env, logs, direct = ns_env(w)
del ran[:]
Clock.now = 0.0
FakeInput.answer = 1
env["_re_pick_send_session"](lambda: ran.append(1))
check("a switch that never lands sends nothing and says so",
      not ran and w.requests == [2] and len(env["host"].toasts) == 1,
      str((ran, env["host"].toasts)))

w = FakeWidget(PEERS, 1)
w._op_active = True
env, logs, direct = ns_env(w)
del FakeInput.calls[:], ran[:]
env["_re_pick_send_session"](lambda: ran.append(1))
check("mid-operation: no question (the send reports busy itself)",
      ran == [1] and not FakeInput.calls)

w = FakeWidget(PEERS, 1)
env, logs, direct = ns_env(w)
FakeInput.answer = 0
check("several Nexts: _re_try_send_folder asks, and counts as handled",
      env["_re_try_send_folder"](work) is True and direct == [work],
      str(direct))
FakeInput.answer = None
del direct[:]
check("several Nexts, Cancel: handled, nothing sent",
      env["_re_try_send_folder"](work) is True and not direct)

# ---- wiring and catalogs --------------------------------------------------
print("== wiring ==")
for mod, fn in (("zxnu_getit_pane.py", "_getit_send_to_image"),
                ("zxnu_zxdb_pane.py", "_zxdb_send_to_image"),
                ("zxnu_zxart_pane.py", "_zxart_send_to_image")):
    check("%s: %s goes through the image picker" % (mod, fn),
          ("host._pick_send_image(lambda: %s_now(" % fn) in src(mod))
itch = src("zxnu_itchio_pane.py")
check("itch.io: send-to-image goes through the image picker",
      "host._pick_send_image(_go)" in itch)
check("itch.io: the ZX Next Remote send asks which Next first",
      "_pick(_itchio_send_zxnr)" in itch)
check("the widget counts its listings",
      "self._listing_seq += 1" in src("zxnu_remote_explorer.py"))

KEYS = [
    "Choose the disk image",
    "Several disk images can be written to.\nChoose the one to send to\n"
    "(the loaded image is selected by default):",
    "Disk image not loaded",
    "{path} could not be loaded, so nothing was sent.",
    "Choose the Next",
    "Several Nexts are connected to the Remote Explorer.\n"
    "Choose the one to send to\n"
    "(the one the Remote Explorer shows is selected by default):",
    "Send via NextSync: switching the Remote Explorer to {machine} …",
    "Could not switch to that Next",
    "The Remote Explorer got no listing from {machine} in time, so nothing "
    "was sent.",
]
consts = set()
for text in (src("zxnu_emulator_ops.py"), pane_src):
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            consts.add(node.value)
for key in KEYS:
    for lang, cat in zxnu_i18n._TOAST_CATALOGS.items():
        tr = cat.get(key)
        ok = bool(tr) and tr != key
        if ok and "{" in key:
            ok = all(("{%s}" % f) in tr for f in ("path", "machine")
                     if ("{%s}" % f) in key)
        if not ok:
            check("catalog %s has %r" % (lang, key[:40]), False)
check("every new text is translated in all six catalogs, placeholders kept",
      not any(n.startswith("catalog ") for n in FAILS))
check("the source carries each catalogued text",
      all(k in consts for k in KEYS), str([k for k in KEYS if k not in consts]))

print()
print("RESULT: " + ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
sys.exit(1 if FAILS else 0)
