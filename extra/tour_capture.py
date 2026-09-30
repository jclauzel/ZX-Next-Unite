"""Drive the demo-environment app through a tour and grab the GIF frames.

Two tours share this driver, and above all its host masking:

    python extra/tour_capture.py            the tabs tour  -> tour_frames/
    python extra/tour_capture.py --wizzy    Wizzy's tour   -> wizzy_frames/

Runs the REAL Qt platform (retro pygame panes crash under offscreen), so a
window appears during capture. Host identity is masked: socket.gethostname /
gethostbyname_ex are patched before the app imports, and detect_local_ipv4 is
rebound at its source before the app imports anything (see below).
"""
import collections
import os
import runpy
import socket
import sys
import tempfile
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = r"C:\Users\Public\ZX-Next-Unite-demo"
# Read here: sys.argv is handed over to the app further down.
WIZZY = "--wizzy" in sys.argv[1:]
WORK = os.environ.get("ZXNU_TOUR_WORK") or os.path.join(tempfile.gettempdir(), "zxnu-tour")
OUT = os.path.join(WORK, "wizzy_frames" if WIZZY else "tour_frames")
STATUS = os.path.join(WORK, "capture_status_wizzy.txt" if WIZZY else "capture_status.txt")
os.makedirs(OUT, exist_ok=True)
for f in os.listdir(OUT):
    os.remove(os.path.join(OUT, f))

MASK_HOST = "<your PC name>"
MASK_IPS = ["<your LAN address 1>", "<your LAN address 2>"]
MASK_PRIMARY = "<your primary IP>"
socket.gethostname = lambda: MASK_HOST
socket.gethostbyname_ex = lambda name=None: (MASK_HOST, [], list(MASK_IPS))

sys.path.insert(0, REPO)
from PySide6.QtCore import QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox  # noqa: E402

# The GIF plays at the capture rate (tour_assemble_gif.py matches it). Wizzy's
# tour runs slower and longer per stop: its bubbles are there to be READ.
FRAME_MS = 200 if WIZZY else 140
FRAMES = 24 if WIZZY else 16
WINDOW = (1440, 974) if WIZZY else (1500, 950)

# The Wizzy tour steps that are recorded. The others are stepped through
# unrecorded, which keeps the GIF near the size of the one it replaces.
WIZZY_GRAB = ("tour.language", "tour.sdcard", "tour.nextsync", "tour.getit",
              "tour.zxdb", "tour.itchio")

def log(msg):
    with open(STATUS, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")

def fake_ipv4():
    return (MASK_HOST, [], list(MASK_IPS), MASK_PRIMARY)

# Patch it at the SOURCE, before the app imports anything.
#
# The socket patches above only cover the host name and the addresses
# gethostbyname_ex reports; detect_local_ipv4 works the PRIMARY address out
# by opening a UDP socket and reading getsockname(), which no socket patch
# here touches. The orchestrator's own patch_ip step (below) is too late for
# it: the NextSync tab prints its host/IP banner once at STARTUP, so the real
# address was already in the captured log — "Primary IP: 10.0.0.31" shipped
# in the frames (caught while regenerating the 9.6.0 GIF). Rebinding it on
# zxnu_network here means every `from zxnu_network import detect_local_ipv4`
# in the app binds the fake instead.
import zxnu_network                       # noqa: E402
zxnu_network.detect_local_ipv4 = fake_ipv4

# A step that holds the tour until pred() is true, or timeout_ms has passed.
Wait = collections.namedtuple("Wait", "pred timeout_ms poll_ms")

_orig_exec = QApplication.exec

def orchestrate():
    app = QApplication.instance()
    win = next(w for w in app.topLevelWidgets() if isinstance(w, QMainWindow))
    tab = win._tab_widget

    def tab_index(fragment):
        for i in range(tab.count()):
            if fragment.lower() in tab.tabText(i).lower():
                return i
        return -1

    # Auto-accept any modal (the "Yes, start NextSync server" prompt etc.).
    def kill_modals():
        for w in app.topLevelWidgets():
            if isinstance(w, QMessageBox) and w.isVisible():
                btn = w.defaultButton() or (w.buttons()[0] if w.buttons() else None)
                log(f"modal: {w.windowTitle()!r} -> clicking {btn.text() if btn else 'accept'}")
                if btn is not None:
                    btn.click()
                else:
                    w.accept()
    killer = QTimer(win)
    killer.timeout.connect(kill_modals)
    killer.start(400)

    steps = []          # (delay_ms_after_previous, fn or Wait, name)
    def step(delay, fn, name=""):
        steps.append((delay, fn, name))

    def wait_until(pred, timeout_ms, name, poll_ms=250):
        steps.append((0, Wait(pred, timeout_ms, poll_ms), name))

    seg_counter = {"n": 0}
    def capture_segment(seg_name):
        """Queue FRAMES grabs, FRAME_MS apart, as one segment."""
        seg = seg_counter["n"]
        seg_counter["n"] += 1
        for i in range(FRAMES):
            def grab(seg=seg, i=i, seg_name=seg_name):
                pix = win.grab()
                pix.save(os.path.join(OUT, f"seg{seg}_{seg_name}_{i:02d}.png"))
            step(FRAME_MS, grab, f"grab {seg_name} {i}")

    # The SD Card Utility and both NextSync experiences are sub-tabs of the
    # one "Transfer tools" tab since 9.7.38. tab_index() still finds that tab
    # from either old fragment - its title names both tools - so what the
    # three segments below really need is the SUB-tab.
    from zxnu_config import (TRANSFER_SUBTAB_CLASSIC, TRANSFER_SUBTAB_REMOTE,
                             TRANSFER_SUBTAB_SDCARD,
                             ZX_NEXT_UNITE_TAB_TITLE_TRANSFER)

    def transfer_subtab(index):
        # Sub-tab FIRST. Selecting the main tab runs the entry work of
        # whichever sub-tab happens to be current, and at the first call that
        # is still the construction default (Classic sync) - whose prepare
        # scan prints the host name and the local IP into the log these
        # frames capture.
        win.nextsync_mode_tabs.setCurrentIndex(index)
        tab.setCurrentIndex(tab_index(ZX_NEXT_UNITE_TAB_TITLE_TRANSFER))

    def patch_ip():
        for mod in ("zxnu_network", "zxnu_nextsync_ops", "zxnu_nextsync_pane",
                    "zxnu_main"):
            m = sys.modules.get(mod)
            if m is not None and hasattr(m, "detect_local_ipv4"):
                m.detect_local_ipv4 = fake_ipv4

    # --- galleries -----------------------------------------------------------
    # How far a gallery page has loaded, over its first twelve entries - what
    # the window shows: three rows of four, the third cut by the viewport
    # edge. Two renderers can be on screen and only the live one loads, so
    # both are read and the better answer wins:
    #   Classic - the Qt GalleryView: a cell marks itself _loaded_ok once a
    #     real picture (or a playing gif) is on it, _placeholder_shown once it
    #     settled for a typed "FILE" / "TAP" tile (the entry has no picture).
    #   Retro - the pygame GalleryScene the demo cfg turns on: a delivered
    #     thumbnail lands in _surfs (a playing gif in _gif_players), and an
    #     entry that only had a typed tile is in _no_image_ids.
    # Reading the Classic cells alone - the first cut - saw nothing at all in
    # Retro mode: every wait ran to its timeout and ZXDB was re-rolled twice
    # while its pictures were on screen the whole time.
    def load_state(src):
        """(entries, pictures, settled) over the first twelve entries."""
        best = (0, 0, 0)
        view = getattr(win, "allinone_gallery_view" if src == "unite"
                       else f"{src}_gallery_view", None)
        cells = list(getattr(view, "_cells", []))[:12]
        if cells:
            pics = sum(1 for c in cells if getattr(c, "_loaded_ok", False))
            done = sum(1 for c in cells if getattr(c, "_loaded_ok", False)
                       or getattr(c, "_placeholder_shown", False))
            best = max(best, (len(cells), pics, done), key=lambda t: t[1:])
        if src == "unite":
            scene = getattr(win, "_allinone_pygame_gallery", None)
        else:
            built = (getattr(win, "_pane_retro_galleries", None) or {}).get(src)
            scene = built[2] if built else None
        entries = list(getattr(scene, "_entries", []))[:12]
        if entries:
            surfs = getattr(scene, "_surfs", {})
            gifs = getattr(scene, "_gif_players", {})
            no_image = getattr(scene, "_no_image_ids", set())
            done = [i for i in range(len(entries)) if i in surfs or i in gifs]
            pics = sum(1 for i in done if id(entries[i]) not in no_image)
            best = max(best, (len(entries), pics, len(done)), key=lambda t: t[1:])
        return best

    def pictures(src):
        return load_state(src)[1]

    def settled(src):
        n, _pics, done = load_state(src)
        return n > 0 and done >= n

    def gallery(fragment, label, loader, capture=True):
        step(300, lambda: tab.setCurrentIndex(tab_index(fragment)), f"{label} tab")
        step(1000, loader, f"{label} latest")
        step(9000, lambda: None, f"{label} thumbnails")
        # The fixed wait above is the one that always worked; this only
        # extends it when the thumbnails are slower than that today.
        wait_until(lambda: settled(label), 20000, f"{label} thumbnails settled")
        step(0, lambda: log(f"{label}: {pictures(label)}/12 pictures"), "")
        if capture:
            capture_segment(label)

    # ZXDB is the exception: its stop loads a RANDOM page, never Latest.
    # ZXDB's newest rows are entries created before anyone uploads media for
    # them, so a Latest page is twelve typed "FILE" tiles. Measured on
    # 2026-09-30: 0 of the 12 visible Latest entries had a screenshot, 12 of
    # 12 on each of three Random pages. Every tour GIF up to 9.7.40 showed
    # that FILE grid (zxnu_zxdb_pane.zxdb_startup_initial_load documents the
    # same trap for the app's own restart). Waiting longer cannot help, so
    # the wait below insists on pictures and re-rolls a page short of them.
    ZXDB_MIN_PICTURES = 10

    def zxdb_ready():
        return (not getattr(win, "_zxdb_search_loading", False)
                and pictures("zxdb") >= ZXDB_MIN_PICTURES)

    def zxdb(capture=True):
        step(300, lambda: tab.setCurrentIndex(tab_index("ZXDB")), "ZXDB tab")
        for attempt in range(3):
            if attempt == 0:
                roll = win.zxdb_on_random
            else:
                def roll():
                    if not zxdb_ready():
                        log(f"ZXDB: {pictures('zxdb')} pictures "
                            "- rolling another random page")
                        win.zxdb_on_random()
            step(1000, roll, f"ZXDB random ({attempt + 1})")
            wait_until(zxdb_ready, 25000, f"ZXDB pictures ({attempt + 1})")
        step(1500, lambda: log(f"zxdb: {pictures('zxdb')}/12 pictures"), "settle")
        if capture:
            capture_segment("zxdb")

    def unite(capture=True):
        # Unite!'s merge fans out to all three sources; its own wait is the
        # longest (it used to be a fixed 12 s). The fan-out drives the ZXDB
        # pane through zxdb_on_latest, so it puts the FILE grid back on ZXDB:
        # nothing may record ZXDB after this without re-rolling it first.
        step(300, lambda: tab.setCurrentIndex(tab_index("Unite")), "Unite tab")
        step(1000, lambda: win._allinone_on_latest(), "Unite latest")
        step(12000, lambda: None, "Unite merge")
        wait_until(lambda: settled("unite"), 20000, "Unite thumbnails settled")
        step(0, lambda: log(f"unite: {pictures('unite')}/12 pictures"), "")
        if capture:
            capture_segment("unite")

    def all_galleries(capture=True):
        if capture:
            # The tabs tour's order: ZXDB is recorded before Unite! resets it.
            gallery("GetIt", "getit", win.getit_on_latest)
            gallery("ZXArt", "zxart", win.zxart_on_latest)
            zxdb()
            unite()
        else:
            # Wizzy's unrecorded warm-up: Unite! first, ZXDB's Random page
            # last, so it is still on the pane when his tour gets there.
            unite(False)
            gallery("GetIt", "getit", win.getit_on_latest, False)
            gallery("ZXArt", "zxart", win.zxart_on_latest, False)
            zxdb(False)

    # --- the tabs tour -------------------------------------------------------
    def tabs_tour():
        step(400, lambda: win.resize(*WINDOW), "resize")
        # Mask the local IP before ANY tab entry: the SD Card and NextSync views
        # share one tab now, so entering it can run NextSync's prepare scan.
        step(200, lambda: patch_ip(), "mask IPs (early)")
        step(9000, lambda: transfer_subtab(TRANSFER_SUBTAB_SDCARD), "SD tab")
        step(1200, lambda: None, "settle")
        capture_segment("sdcard")

        # Re-apply: modules imported since the early pass get masked too. The
        # Classic view auto-runs the prepare/perform-checks (host/IP info + the
        # "Ready to sync" scan), so the patch must already be in place - and no
        # explicit prepare click is needed (it would just duplicate the scan
        # block in the log).
        step(300, patch_ip, "mask IPs")
        step(300, lambda: transfer_subtab(TRANSFER_SUBTAB_CLASSIC), "classic view")
        step(4000, lambda: None, "server log settles")
        capture_segment("nextsync_classic")

        step(600, lambda: transfer_subtab(TRANSFER_SUBTAB_REMOTE), "remote explorer")
        step(1800, lambda: None, "RE settles")
        capture_segment("nextsync_re")

        all_galleries()

        step(300, lambda: tab.setCurrentIndex(tab_index("Floyd")), "Alien Floyd's tab")
        step(2500, lambda: None, "attract mode")
        capture_segment("alienfloyds")

    # --- Wizzy's tour (docs/media/wizzy-tour.gif) ----------------------------
    # The wizard's OWN tour, driven through its own methods: start_tour() and
    # next_tour_step() are what his "Next" button calls. The demo cfg keeps him
    # off (the tabs tour must not show him), so he is switched on here for
    # this run only - set_enabled(persist=False) leaves the cfg alone.
    def wizzy_tour():
        from zxnu_wizard_content import GUIDES, TOUR_STEPS
        wiz = win._wizard
        ui_lang = win.settings_ui_language_combo

        def set_language(code):
            # The Settings combo, exactly as a user flips it: it re-translates
            # the window live, and a speaking wizard re-speaks mid-sentence.
            ui_lang.setCurrentIndex(ui_lang.findData(code))

        def quiet():
            # No idle gestures, no once-a-session ZX Next Remote pitch and no
            # "want a guided tour of this tab?" offers: only what this script
            # asks for may appear in the frames.
            # An offer fires once per topic, and a topic is a guide id or a
            # tour key (_tab_topic): mark them all as spent. Not by walking
            # the tab titles - the Transfer tools tab answers with whichever
            # sub-tab is selected, which would leave the other one armed.
            wiz._idle_timer.stop()
            wiz._offered_tabs.update(GUIDES)
            wiz._offered_tabs.update(s[1] for s in TOUR_STEPS)
            # Fetch every step's "From the manual" teaser now, while no tour
            # is running (a landing then only fills the cache): each step
            # appends it at once instead of growing a page mid-recording.
            for s in TOUR_STEPS:
                wiz._request_teaser(s[2])

        def stroll():
            # Walk like an Egyptian, the full length of the promenade: the
            # idle timer that normally starts it (at random) is stopped.
            wiz._dismiss()
            home = wiz._stroll_home()
            wiz.sprite.move(home, wiz.sprite.y())
            wiz._stroll_target = max(0, home - int(win.width() * 0.45))
            wiz.sprite.set_gesture("walk_left")
            wiz._stroll_timer.start()

        step(400, lambda: win.resize(*WINDOW), "resize")
        step(200, lambda: patch_ip(), "mask IPs (early)")
        # The wizard's own startup() runs 2.2 s in (and stays silent: he is
        # off). Visit the galleries unrecorded meanwhile, so each tour step
        # finds its pictures already in place.
        step(9000, patch_ip, "mask IPs")
        all_galleries(capture=False)
        step(300, lambda: transfer_subtab(TRANSFER_SUBTAB_SDCARD), "SD tab")
        step(300, quiet, "wizard: quiet + teasers")
        step(4000, lambda: wiz.set_enabled(True, persist=False), "wizard on")
        step(1500, wiz.start_tour, "tour start")
        step(2000, lambda: None, "bubble settles")
        plan = [s[1] for s in TOUR_STEPS if wiz._resolve_step(s) is not None]
        log(f"tour plan: {plan}")
        for key in plan:
            if key == "tour.language":
                # The opening step invites a language pick: take it up, in
                # French, and back.
                capture_segment("language_en")
                step(300, lambda: set_language("fr"), "language: fr")
                step(2500, lambda: None, "re-translated")
                capture_segment("language_fr")
                step(300, lambda: set_language("en"), "language: en")
                step(2500, lambda: None, "re-translated")
            elif key in WIZZY_GRAB:
                capture_segment(key.split(".", 1)[1])
            step(300, wiz.next_tour_step, f"next after {key}")
            step(2500, lambda: None, "bubble settles")
        capture_segment("finale")        # the kudos + goodbye
        step(300, lambda: tab.setCurrentIndex(tab_index("GetIt")), "GetIt tab")
        step(1200, stroll, "stroll")
        capture_segment("stroll")

    if WIZZY:
        wizzy_tour()
    else:
        tabs_tour()
    step(500, app.quit, "quit")

    def poll(w, name, waited, then):
        try:
            ok = bool(w.pred())
        except Exception:
            log(f"WAIT FAILED: {name}\n" + traceback.format_exc())
            ok = True
        if ok or waited >= w.timeout_ms:
            log(f"wait: {name} -> {'ok' if ok else 'TIMED OUT'} after {waited} ms")
            then()
            return
        QTimer.singleShot(w.poll_ms, lambda: poll(w, name, waited + w.poll_ms, then))

    def run_next(idx=0):
        if idx >= len(steps):
            return
        delay, fn, name = steps[idx]
        if isinstance(fn, Wait):
            QTimer.singleShot(delay, lambda: poll(fn, name, 0,
                                                  lambda: run_next(idx + 1)))
            return
        def fire():
            try:
                if name and not name.startswith("grab"):
                    log(f"step: {name}")
                fn()
            except Exception:
                log(f"STEP FAILED: {name}\n" + traceback.format_exc())
            run_next(idx + 1)
        QTimer.singleShot(delay, fire)
    run_next()

def patched_exec(*args, **kwargs):
    # Called as app.exec() — PySide's exec takes no arguments, drop `self`.
    QTimer.singleShot(600, lambda: _try(orchestrate))
    return _orig_exec()

def _try(fn):
    try:
        fn()
    except Exception:
        log("ORCHESTRATE FAILED\n" + traceback.format_exc())
        QApplication.instance().quit()

QApplication.exec = patched_exec

open(STATUS, "w").write("capture starting" + (" (wizzy)" if WIZZY else "") + "\n")
script = os.path.join(DEMO, "zx-next-unite.py")
sys.argv = [script]
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit:
    pass
log(f"done, frames: {len(os.listdir(OUT))}")
print("frames captured:", len(os.listdir(OUT)))
