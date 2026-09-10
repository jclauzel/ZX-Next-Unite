"""Offscreen tests for Wizzy, the onboarding wizard (zxnu_wizard.py).

* Translation tripwire: every dialogue key in zxnu_wizard_content.TEXTS must
  carry every language in WIZARD_LANGS, and the jokes/stories lists must be
  the same length in every language — an added line can't silently ship
  untranslated.
* Tour script: every step's dialogue key exists and every wiki page name is
  a known user-manual page (one per tab).
* Sprite: every gesture builds non-empty frames (incl. the mirrored
  walk_left) and the artwork grid is rectangular with palette-only chars.
* Manager: first-run startup marks the intro shown, offers the tour, the
  turn-off flow persists SETTING_WIZARD_ENABLED=false, and the tour walks
  the visible tabs of a stub host. No network is touched (teaser fetches
  are stubbed out).

Run with: python tests/test_wizard.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget  # noqa: E402
app = QApplication.instance() or QApplication([])

import zxnu_wizard as zw                                             # noqa: E402
import zxnu_wizard_content as wc                                     # noqa: E402
from zxnu_config import (SETTING_WIZARD_ENABLED,                     # noqa: E402
                         SETTING_WIZARD_INTRO_SHOWN)

FAIL = []
def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


# ── translations ─────────────────────────────────────────────────────────
langs = set(wc.WIZARD_LANGS)
missing = [f"{key}:{lang}" for key, entry in wc.TEXTS.items()
           for lang in langs if not (entry.get(lang) or "").strip()]
check("every dialogue key carries every language", not missing,
      ", ".join(missing[:8]))

for name, table in (("JOKES", wc.JOKES), ("STORIES", wc.STORIES)):
    lens = {lang: len(table.get(lang, [])) for lang in langs}
    check(f"{name} lists same length in every language",
          len(set(lens.values())) == 1 and min(lens.values()) > 0, str(lens))

check("fallback to English works",
      wc.wizard_tr("intro.hello", "xx") == wc.TEXTS["intro.hello"]["en"])

# ── tour script ──────────────────────────────────────────────────────────
known_pages = {
    "SD-Card-Utility-tab", "NextSync-tab", "GetIt-tab", "zxArt-tab",
    "ZXDB-tab", "Favorites-tab", "Unite-tab", "itch-io-tab",
    "Settings-tab", "Help-tab", "Alien-Floyds-tab", "Home",
    "Installation", "User-Manual",
}
bad_steps = [s for s in wc.TOUR_STEPS
             if s[1] not in wc.TEXTS or s[2] not in known_pages]
check("tour steps reference known keys and wiki pages", not bad_steps,
      str(bad_steps))

# ── sprite artwork ───────────────────────────────────────────────────────
check("base art grid is rectangular",
      len({len(r) for r in zw._BASE}) == 1 and len(zw._BASE) >= 20)
stray = set("".join(zw._BASE)) - set(zw._PAL) - {"."}
check("base art uses palette chars only", not stray, str(stray))

frames = zw.build_wizard_frames(px=2)
for name in ("idle", "look", "wave", "point", "cast", "talk",
             "walk", "walk_left"):
    pix_list = frames.get(name) or []
    ok = bool(pix_list) and all((not p.isNull()) and p.width() > 0
                                for p in pix_list)
    check(f"gesture '{name}' builds frames", ok)
check("walk has two stepping frames", len(frames["walk"]) == 2)

# ── manager on a stub host ───────────────────────────────────────────────
class StubHost(QMainWindow):
    pass

host = StubHost()
host.resize(900, 600)
host.show()     # children can only report visible under a shown window
tabs = QTabWidget(host)
for title in ("TOOL: SD Card Utility", "TOOL: NextSync", "🌍 GetIt",
              "Settings 🔩", "?"):
    from PySide6.QtWidgets import QWidget
    tabs.addTab(QWidget(), title)
host._tab_widget = tabs
saved = {"n": 0}
host._save_configuration_file = lambda: saved.__setitem__("n", saved["n"] + 1)

cfg = {}
wiz = zw.build_wizard(host, configuration_dictionary=cfg)
check("build_wizard exposes host._wizard", host._wizard is wiz)
wiz._request_teaser = lambda page: None          # no network in tests

wiz.startup()
check("first-run marks the intro as shown",
      cfg.get(SETTING_WIZARD_INTRO_SHOWN) == "true")
check("intro bubble is visible", wiz.bubble.isVisible())
check("intro offers quickstart/tour/later/off actions",
      len(wiz.bubble._actions) == 4
      and wiz.bubble._actions[0][0] == wc.wizard_tr("btn.quickstart", "en"))
check("intro paginates with nav buttons",
      len(wiz.bubble._pages) > 1
      and any(b.text() == "▶" for b in wiz.bubble._buttons))
wiz.bubble._flip(+1)
check("page flip advances", wiz.bubble._page == 1)
wiz.bubble._flip(-1)
check("page flip goes back", wiz.bubble._page == 0)

wiz.start_tour()
check("tour opens on Settings with the language step",
      tabs.tabText(tabs.currentIndex()).startswith("Settings")
      and wiz.bubble.label.text() == wc.wizard_tr("tour.language", "en"))
wiz.next_tour_step()
check("then the SD-card tab and step",
      tabs.currentIndex() == 0
      and wiz.bubble.label.text() == wc.wizard_tr("tour.sdcard", "en"))
wiz.next_tour_step()
check("tour advanced to NextSync", tabs.currentIndex() == 1)
wiz.next_tour_step()
check("tour advanced to GetIt", tabs.currentIndex() == 2)
check("catalogue step softly recalls the rights reminder",
      wc.wizard_tr("tour.disclaimer", "en")[:40]
      in " ".join(wiz.bubble._pages))
check("no page ever ends in a stranded ellipsis",
      all(not p.endswith("…") for p in wiz.bubble._pages))
# zxArt/ZXDB/Favorites/Unite/itch.io are absent from the stub -> the tour
# must skip them gracefully: the next steps land on Settings then Help.
wiz.next_tour_step()
check("hidden tabs are skipped (Settings next)",
      tabs.tabText(tabs.currentIndex()).startswith("Settings"))
wiz.next_tour_step()
check("last step is the Help tab", tabs.tabText(tabs.currentIndex()) == "?")
wiz.next_tour_step()
_finale = " ".join(wiz.bubble._pages)
check("tour finale sends the kudos",
      "em00k" in _finale and "the Gary(s)" in _finale
      and "Tim" in _finale)
check("tour finishes with the outro",
      wc.wizard_tr("tour.done", "en") in _finale)
check("kudos template carries {names} in every language",
      all("{names}" in wc.TEXTS["tour.kudos"][lang]
          for lang in wc.WIZARD_LANGS))

# Jokes rotate through the whole bag without repeats.
seen = set()
for _ in range(len(wc.JOKES["en"])):
    wiz.tell_joke()
    seen.add(wiz.bubble.label.text())
check("jokes rotate without repeats", len(seen) == len(wc.JOKES["en"]))

# Language follows the i18n setting live.
import zxnu_i18n
zxnu_i18n.set_current_ui_language("es")
wiz.show_menu()
check("menu speaks the active language",
      wiz.bubble.label.text() == wc.wizard_tr("menu.title", "es"))

# A live language switch re-composes whatever is currently on screen.
zxnu_i18n.set_current_ui_language("fr")
wiz.on_language_changed()
check("open menu re-speaks in the new language",
      wiz.bubble.label.text() == wc.wizard_tr("menu.title", "fr"))
zxnu_i18n.set_current_ui_language("en")
wiz.start_tour()          # opens on the Settings/language step
_lang_tab = tabs.currentIndex()
zxnu_i18n.set_current_ui_language("pl")
wiz.on_language_changed()
check("open tour step re-speaks in the new language",
      wiz.bubble.label.text() == wc.wizard_tr("tour.language", "pl"))
check("re-speak stays on the same tab", tabs.currentIndex() == _lang_tab)
zxnu_i18n.set_current_ui_language("en")
wiz.tell_joke()
_joke_idx = wc.JOKES["en"].index(wiz.bubble.label.text())
zxnu_i18n.set_current_ui_language("cs")
wiz.on_language_changed()
check("the SAME joke re-tells in the new language",
      wiz.bubble.label.text() == wc.JOKES["cs"][_joke_idx])
zxnu_i18n.set_current_ui_language("en")
wiz._dismiss()

# Turn-off persists and hides (the farewell hides after a delay; force it).
wiz.set_enabled(False)
check("turn-off persists the setting",
      cfg.get(SETTING_WIZARD_ENABLED) == "false" and saved["n"] > 0)
check("wizard hidden when disabled",
      not wiz.sprite.isVisible() and not wiz.bubble.isVisible())
wiz.set_enabled(True)
check("re-enable shows the sprite again", wiz.sprite.isVisible())

# ── in-depth guides ──────────────────────────────────────────────────────
# Graph integrity: every node id / linux_extra is a translated TEXTS key,
# every target exists (or "close"), pages and tab constants are real.
import zxnu_config  # noqa: E402
for gid, guide in wc.GUIDES.items():
    nodes = guide["nodes"]
    bad = []
    if guide["start"] not in nodes:
        bad.append("start")
    if guide["page"] not in known_pages:
        bad.append("page")
    if not hasattr(zxnu_config, guide["tab"]):
        bad.append("tab")
    for nid, node in nodes.items():
        if nid not in wc.TEXTS:
            bad.append(f"{nid}:text")
        extra = node.get("linux_extra")
        if extra and extra not in wc.TEXTS:
            bad.append(f"{nid}:linux_extra")
        goto = node.get("goto")
        if goto and not hasattr(zxnu_config, goto):
            bad.append(f"{nid}:goto")
        for _bk, target in node["buttons"]:
            if target != "close" and target not in nodes:
                bad.append(f"{nid}->{target}")
    check(f"guide '{gid}' graph is sound", not bad, str(bad))

# Offer trigger: first visit to the NextSync tab offers the guide, with
# Manual/GitHub links present; a second visit stays quiet.
wiz._dismiss()
tabs.setCurrentIndex(1)      # TOOL: NextSync
check("guided tab visit triggers the offer",
      wiz.bubble.isVisible()
      and wiz.bubble.label.text() == wc.wizard_tr("guide.offer", "en"))
check("offer carries Manual + GitHub links",
      len(wiz.bubble._links) == 2
      and wiz.bubble._link_buttons[1].text() == "GitHub")
wiz.bubble._actions[0][1]()  # "Tell me more" -> first guide node
check("in-depth starts on ns.what",
      wiz.bubble.label.text() == wc.wizard_tr("ns.what", "en"))
wiz.bubble._actions[0][1]()  # Next -> the three-way branch
check("ns.compat offers the three branches",
      [a[0] for a in wiz.bubble._actions] ==
      [wc.wizard_tr(k, "en")
       for k in ("btn.setup", "btn.remotexp", "btn.classic")])
wiz.bubble._actions[0][1]()  # Set up -> ns.setup1
wiz.bubble._actions[0][1]()  # Next -> ns.setup2
wiz.bubble._actions[0][1]()  # Next -> the spellbook
check("setup branch ends on the .sync5 spellbook",
      "-listen" in " ".join(wiz.bubble._pages))
wiz.bubble._actions[0][1]()              # Close
check("closing the last node dismisses", not wiz.bubble.isVisible())
wiz.start_guide("nextsync")
wiz.bubble._actions[0][1]()
wiz.bubble._actions[1][1]()  # Remote Explorer branch
_re_setup = " ".join(wiz.bubble._pages)
check("remote branch starts with the three setup moves",
      "sync root" in _re_setup and "-l" in _re_setup)
wiz.bubble._actions[0][1]()  # Next -> capabilities page
_re_caps = " ".join(wiz.bubble._pages)
check("remote capabilities teach -send and both graceful exits",
      "-send" in _re_caps and "BREAK" in _re_caps
      and "Caps Shift" in _re_caps
      and "Stop Remote Explorer NextSync server" in _re_caps)
wiz.start_guide("nextsync")
wiz.bubble._actions[0][1]()
wiz.bubble._actions[2][1]()  # Classic Sync branch
check("classic branch starts on ns.classic",
      wiz.bubble._pages[0].startswith(wc.wizard_tr("ns.classic", "en")[:40]))
wiz.bubble._actions[0][1]()  # Next -> ns.root
wiz.bubble._actions[0][1]()  # Next -> ns.server
wiz.bubble._actions[0][1]()  # Close
check("classic branch closes cleanly", not wiz.bubble.isVisible())
tabs.setCurrentIndex(2)                  # GetIt: a tour-step tab
check("manual switch to a tour tab offers quick help",
      wiz.bubble.isVisible()
      and wiz.bubble.label.text() == wc.wizard_tr("help.offer", "en"))
wiz.bubble._actions[0][1]()              # Yes
check("tab help shows the GetIt blurb + rights reminder",
      wiz.bubble._pages[0].startswith(wc.wizard_tr("tour.getit", "en")[:40])
      and wc.wizard_tr("tour.disclaimer", "en")[:30]
      in " ".join(wiz.bubble._pages))
wiz._dismiss()
tabs.setCurrentIndex(1)
check("no second offer for the same tab this session",
      not wiz.bubble.isVisible())
tabs.setCurrentIndex(2)
check("tab help offered once per session too",
      not wiz.bubble.isVisible())

# Font size: the wizard's own A-/A+ dialog, persisted and clamped.
from zxnu_config import SETTING_WIZARD_FONT_SIZE  # noqa: E402
wiz.adjust_font(0)
check("font dialog speaks",
      wiz.bubble.label.text().startswith(
          wc.wizard_tr("wizard.font", "en")[:30]))
wiz.bubble._actions[1][1]()              # A+
check("font grows and persists",
      wiz.bubble._font_px == 13
      and cfg.get(SETTING_WIZARD_FONT_SIZE) == "13")
wiz.bubble._actions[0][1]()              # A-
check("font shrinks back", wiz.bubble._font_px == 12
      and cfg.get(SETTING_WIZARD_FONT_SIZE) == "12")
wiz._dismiss()

# "About this tab" in the menu re-opens tab help even after the automatic
# once-per-session offers were spent.
tabs.setCurrentIndex(1)                  # NextSync: offer already consumed
wiz._dismiss()
wiz.show_menu()
check("menu leads with About this tab",
      wiz.bubble._actions[0][0] == wc.wizard_tr("btn.abouttab", "en"))
wiz.bubble._actions[0][1]()
check("About this tab opens the NextSync guide",
      wiz.bubble.label.text() == wc.wizard_tr("ns.what", "en"))
wiz._dismiss()
tabs.setCurrentIndex(2)                  # GetIt: help offer spent too
wiz._dismiss()
wiz.show_menu()
wiz.bubble._actions[0][1]()
check("About this tab shows GetIt help after the offer was spent",
      wiz.bubble._pages[0].startswith(wc.wizard_tr("tour.getit", "en")[:40]))
wiz._dismiss()

# SD guide: the CSpect branch has a Take-me-there jump; the MAME node
# appends the Flatpak note only on Linux.
wiz.start_guide("sdcard")
wiz.bubble._actions[0][1]()  # Next -> hdfmonkey
wiz.bubble._actions[0][1]()  # Next -> cspect
wiz.bubble._actions[0][1]()  # Yes  -> cspect_steps
check("CSpect steps offer Take me there",
      wiz.bubble._actions[0][0] == wc.wizard_tr("btn.takeme", "en"))
_old_linux = zw._is_linux
zw._is_linux = lambda: True
wiz.start_guide("sdcard")
wiz.bubble._actions[0][1]()
wiz.bubble._actions[0][1]()
wiz.bubble._actions[1][1]()  # No -> sd.mame
check("MAME node appends the Flatpak note on Linux",
      wc.wizard_tr("sd.mame.linux", "en") in " ".join(wiz.bubble._pages))
zw._is_linux = _old_linux
wiz._dismiss()

# An offer left open across a tab switch must retarget to the NEW tab —
# and its buttons must open the CURRENT tab's content, never the tab the
# offer was created on. Real (non-offer) content is never hijacked.
tabs.setCurrentIndex(3)              # Settings: first visit -> help offer
check("Settings visit offers help",
      wiz.bubble.isVisible()
      and wiz.bubble.label.text() == wc.wizard_tr("help.offer", "en"))
tabs.setCurrentIndex(0)              # switch WHILE the offer is open
check("stale offer replaced by the SD Card guide offer",
      wiz.bubble.label.text() == wc.wizard_tr("guide.offer", "en"))
wiz.bubble._actions[0][1]()          # "Tell me more" clicked NOW
check("Tell me more opens the CURRENT tab's guide (SD Card)",
      wiz.bubble.label.text() == wc.wizard_tr("sd.images", "en"))
tabs.setCurrentIndex(4)              # switch during REAL content
check("real guide content is never hijacked by a tab switch",
      wiz.bubble.label.text() == wc.wizard_tr("sd.images", "en"))
wiz._dismiss()

# ── Quick Start: three clicks to a booting Next ──────────────────────────
calls_qs = []
host.download_nextzxos_image = lambda: calls_qs.append("download")
host._launch_cspect_fn = lambda: calls_qs.append("launch")
wiz.start_quickstart()
check("QS opens on the image step",
      wiz.bubble._pages[0].startswith(wc.wizard_tr("qs.image", "en")[:30]))
wiz.bubble._actions[0][1]()          # Do it!
check("QS triggered the NextZXOS download and waits",
      calls_qs == ["download"]
      and wiz.bubble.label.text() == wc.wizard_tr("qs.image.wait", "en"))
host.right_disk_image_path = "C:/img.hdf"
wiz._qs_tick()                       # poll finds the image loaded
check("QS advances to the emulator step",
      wiz.bubble._pages[0].startswith(wc.wizard_tr("qs.emulator", "en")[:30]))
host._cspect_executable_path = "cspect.exe"
wiz.bubble._actions[-1][1]()         # Skip this step -> launch
check("QS launch step reached",
      wiz.bubble._pages[0].startswith(wc.wizard_tr("qs.launch", "en")[:30]))
wiz.bubble._actions[0][1]()          # Do it! -> boots + celebration
check("QS launched the emulator and celebrates",
      "launch" in calls_qs
      and wiz.bubble._pages[0].startswith(wc.wizard_tr("qs.done", "en")[:30]))
check("QS finale offers the 🎁 Starter pack",
      wiz.bubble._actions[0][0] == wc.wizard_tr("btn.starterpack", "en"))
wiz._dismiss()
wiz.show_menu()
check("menu offers Quick Start",
      any(a[0] == wc.wizard_tr("btn.quickstart", "en")
          for a in wiz.bubble._actions))
check("menu links row offers the starter pack",
      any(lnk[0] == wc.wizard_tr("btn.starterpack", "en")
          for lnk in wiz.bubble._links))
wiz._dismiss()

# ── Starter pack: explainer, Do-it shortcut, image-loaded offer ──────────
from zxnu_config import SETTING_WIZARD_SP_OFFERED  # noqa: E402

sp_clicks = []


class _SpBtn:                       # stand-in for the GetIt 🎁 QPushButton
    def click(self):
        sp_clicks.append(1)


host.getit_starter_button = _SpBtn()
wiz.show_starter_pack()
check("starter-pack explainer says exactly where the button lives",
      "GetIt" in " ".join(wiz.bubble._pages)
      and "/games/StarterPack" in " ".join(wiz.bubble._pages))
wiz.bubble._actions[0][1]()          # 🚀 Do it!
check("Do it jumps to GetIt and presses the 🎁 button",
      sp_clicks == [1]
      and tabs.tabText(tabs.currentIndex()).startswith("🌍 GetIt"))
wiz._dismiss()
wiz._sp_offered_session = False
cfg[SETTING_WIZARD_SP_OFFERED] = ""
wiz.on_image_loaded()
check("image-loaded hook offers the starter pack",
      wiz.bubble.isVisible()
      and wiz.bubble._pages[0].startswith(wc.wizard_tr("sp.offer", "en")[:30]))
check("the offer persists its once-ever flag",
      cfg.get(SETTING_WIZARD_SP_OFFERED) == "true")
wiz._dismiss()
wiz._sp_offered_session = False      # new session, but the flag persisted
wiz.on_image_loaded()
check("the offer never repeats once made", not wiz.bubble.isVisible())
wiz._sp_offered_session = False
cfg[SETTING_WIZARD_SP_OFFERED] = ""
wiz.show_menu()                      # bubble busy with something else
wiz.on_image_loaded()
check("the offer never interrupts an open bubble",
      wiz.bubble.label.text() == wc.wizard_tr("menu.title", "en")
      and cfg.get(SETTING_WIZARD_SP_OFFERED) == "")
wiz._dismiss()

# ── Health check (wizard menu links row) ─────────────────────────────────
host._hdfmonkey_binary_found = lambda: False
wiz._health_ip = "192.168.1.50"          # probe result already cached
wiz._health_ip_pending = True            # ...and no probe thread: keeps the
wiz.show_health()                        # test offline and its exit quiet
txt = "\n".join(wiz.bubble._pages)
check("health check lists all six items",
      all(wc.wizard_tr(k, "en") in txt
          for k in ("health.network", "health.hdfmonkey",
                    "health.emulators", "health.image",
                    "health.syncroot", "health.localip")))
check("health check flags missing hdfmonkey with a warning",
      "⚠️  " + wc.wizard_tr("health.hdfmonkey", "en") in txt)
check("health check shows the found emulator and the cached local IP",
      "✅  " + wc.wizard_tr("health.emulators", "en") + ": CSpect" in txt
      and "192.168.1.50" in txt)
host._hdfmonkey_binary_found = lambda: True
wiz.bubble._actions[0][1]()              # 🩺 button doubles as refresh
check("health refresh flips hdfmonkey to green",
      "✅  " + wc.wizard_tr("health.hdfmonkey", "en")
      in "\n".join(wiz.bubble._pages))
wiz._on_health_ip("10.0.0.7")            # late daemon-probe result lands
check("late IP probe result refreshes the open bubble",
      "10.0.0.7" in "\n".join(wiz.bubble._pages))
wiz._dismiss()
wiz.show_menu()
check("menu links row offers the health check",
      any(lnk[0] == wc.wizard_tr("btn.health", "en")
          for lnk in wiz.bubble._links))
wiz._dismiss()

# ── offline behaviour (zxnu_network gate) ────────────────────────────────
host._network_online = lambda: False
zw.WizardManager._request_teaser(wiz, "Some-Page")   # the real method
check("offline: teaser fetch skipped and not cached",
      "Some-Page" not in wiz._teaser_cache)
host._network_online = lambda: True
wiz._on_teaser("Some-Page", "")
check("failed teaser is not cached (retried once online)",
      "Some-Page" not in wiz._teaser_cache)
wiz._on_teaser("Some-Page", "hello")
check("successful teaser is cached",
      wiz._teaser_cache.get("Some-Page") == "hello")
del host._network_online

# ── teaser re-entry while a fetch is in flight (9.7.16) ──────────────────
# The bubble is two clicks from a re-entry: open a tab, accept the help
# offer, then click Wizzy and pick "About this tab" again before the 6 s
# wiki fetch answers. Every caller asks _request_teaser unconditionally, so
# that used to start a daemon thread each — and EVERY landing appends, so
# the bubble grew the "From the manual" paragraph twice as two separate
# pages. Stubbed at the Thread boundary: no network, no timing.
class _StubThread:
    started = []

    def __init__(self, target=None, daemon=None, **kw):
        self._target = target       # deliberately never run (it fetches)

    def start(self):
        _StubThread.started.append(self._target)


class _StubThreading:
    Thread = _StubThread


TEASE = "Mount it and go."
_real_threading = zw.threading
zw.threading = _StubThreading
del wiz._request_teaser              # drop the no-op stub: use the real one
try:
    tabs.setCurrentIndex(2)                     # 🌍 GetIt — a "help" tab
    wiz.about_current_tab()                     # first click
    check("tab help opens on the GetIt page",
          wiz._tour_active_page == "GetIt-tab" and wiz.bubble.isVisible())
    check("the first ask starts exactly one fetch, marked in flight",
          len(_StubThread.started) == 1
          and "GetIt-tab" in wiz._teaser_inflight)

    wiz.about_current_tab()                     # re-entry, fetch still out
    check("re-entry while a fetch is in flight starts no second thread",
          len(_StubThread.started) == 1, str(len(_StubThread.started)))

    wiz._on_teaser("GetIt-tab", TEASE)          # the one thread lands
    joined = "\n".join(wiz.bubble._pages)
    check("the landing appends the teaser exactly once",
          joined.count(TEASE) == 1, str(joined.count(TEASE)))
    check("the landing releases the in-flight slot",
          "GetIt-tab" not in wiz._teaser_inflight)

    # Belt and braces: even a stray second landing (what the second thread
    # used to deliver) must not append to a bubble that already has it.
    wiz._on_teaser("GetIt-tab", TEASE)
    check("a second landing does not append the teaser again",
          "\n".join(wiz.bubble._pages).count(TEASE) == 1)

    # Same guard from the other entrance: a network flap re-asks, and the
    # teaser is cached by now, so _on_teaser runs synchronously.
    host._network_online = lambda: True
    wiz._on_network_changed(True)
    check("a network flap does not re-append the cached teaser",
          "\n".join(wiz.bubble._pages).count(TEASE) == 1)
    del host._network_online

    # ...but a FRESH bubble does get its own copy of the cached teaser.
    wiz.about_current_tab()
    check("a fresh bubble still gets the cached teaser, once",
          "\n".join(wiz.bubble._pages).count(TEASE) == 1)
finally:
    zw.threading = _real_threading
    wiz._request_teaser = lambda page: None      # no network for what follows
    wiz._teaser_cache.pop("GetIt-tab", None)
    wiz._dismiss()

# Markdown teaser extraction (pure function, no network).
md = "# Title\n\n![badge](x.png)\n\nThe **SD Card** tab lets you [mount](u) images.\n\nMore text."
check("teaser strips markdown to the first paragraph",
      zw._teaser_from_markdown(md) == "The SD Card tab lets you mount images.")

# ── the .sync5 auto-deploy offer (9.7.8) ──────────────────────────────────
# The real method: a Yes/No bubble in the wizard's language whose text
# names the image and the versions, Yes/No dismissing the bubble and then
# running the caller's callbacks; startup() stamps _started, the flag the
# offer's caller waits on so the first-run intro can never clobber it.
check("startup() stamps _started for the .sync5 offer's caller",
      getattr(wiz, "_started", False) is True)
answers = []
wiz.offer_sync5_deploy("older", "5.9.1", "5.9.2", "C:/imgs/next.img",
                       lambda: answers.append("yes"), lambda: answers.append("no"))
body = wiz.bubble._text if hasattr(wiz.bubble, "_text") else ""
labels = [a[0] for a in wiz.bubble._actions]
check("the offer shows a Yes/No bubble",
      wiz.bubble.isVisible() and labels == [wc.wizard_tr("btn.yes", "en"),
                                             wc.wizard_tr("btn.no", "en")], str(labels))
check("...whose text names the image and both versions",
      all(s in wc.wizard_tr("sync5.older", "en").format(image="next.img", old="5.9.1", new="5.9.2")
          for s in ("next.img", "v5.9.1", "v5.9.2", "Settings")))
wiz.bubble._actions[1][1]()                                # No
check("No dismisses the bubble and runs the No callback",
      not wiz.bubble.isVisible() and answers == ["no"], str(answers))
wiz.offer_sync5_deploy("missing", "", "5.9.2", "C:/imgs/next.img",
                       lambda: answers.append("yes"), lambda: answers.append("no"))
wiz.bubble._actions[0][1]()                                # Yes
check("Yes dismisses the bubble and runs the Yes callback",
      not wiz.bubble.isVisible() and answers == ["no", "yes"], str(answers))
check("every sync5.* text carries {image} and {new} in every language",
      all("{image}" in wc.TEXTS[k][lang] and "{new}" in wc.TEXTS[k][lang]
          for k in ("sync5.missing", "sync5.older", "sync5.unknown")
          for lang in wc.WIZARD_LANGS)
      and all("{old}" in wc.TEXTS["sync5.older"][lang] for lang in wc.WIZARD_LANGS))

# ── the ZX Next Remote "did you know" pitch (9.7.9) ──────────────────────
# A friendly word about the companion app: the pitch bubble carries a
# "Give me more information" button and the itch.io link in its reference
# row; the deep dive walks three pages, the link on every one; the idle
# tick brings the pitch up once a session; the click menu always can; the
# NextSync guide's Remote Explorer nodes link to itch.io as well.
opened = []
wiz._open_url = opened.append
wiz._dismiss()
wiz.pitch_zxnr()
labels = [a[0] for a in wiz.bubble._actions]
link_labels = [b.text() for b in wiz.bubble._link_buttons]
check("the pitch shows with 'Give me more information' and 'Not now'",
      wiz.bubble.isVisible() and labels == [wc.wizard_tr("btn.moreinfo", "en"),
                                             wc.wizard_tr("btn.later", "en")], str(labels))
check("...and the itch.io link leads the reference row",
      link_labels and link_labels[0] == wc.wizard_tr("btn.itch", "en"), str(link_labels))
wiz.bubble._link_buttons[0].click()
check("...clicking it opens the ZX Next Remote itch.io page",
      opened == [wc.ZXNR_ITCH_URL], str(opened))
wiz.bubble._actions[0][1]()                                # Give me more information
check("the deep dive opens on page 1 with Next/Close and the itch.io link",
      wc.wizard_tr("zxnr.more1", "en")[:40] in "".join(wiz.bubble._pages)
      and [a[0] for a in wiz.bubble._actions] == [wc.wizard_tr("btn.next", "en"),
                                                   wc.wizard_tr("btn.close", "en")]
      and wiz.bubble._link_buttons[0].text() == wc.wizard_tr("btn.itch", "en"))
wiz.bubble._actions[0][1]()                                # Next -> page 2
wiz.bubble._actions[0][1]()                                # Next -> page 3
check("...page 3 is the last: Close only, itch.io link still there",
      wc.wizard_tr("zxnr.more3", "en")[:40] in "".join(wiz.bubble._pages)
      and [a[0] for a in wiz.bubble._actions] == [wc.wizard_tr("btn.close", "en")]
      and wiz.bubble._link_buttons[0].text() == wc.wizard_tr("btn.itch", "en"))
wiz.bubble._actions[0][1]()                                # Close
check("Close dismisses the deep dive", not wiz.bubble.isVisible())
wiz.show_menu()
entry = next((a for a in wiz.bubble._actions
              if a[0] == wc.wizard_tr("btn.zxnr", "en")), None)
check("the click menu offers the pitch", entry is not None,
      str([a[0] for a in wiz.bubble._actions]))
if entry is not None:
    entry[1]()                                             # About ZX Next Remote
    check("...and choosing it opens the pitch bubble",
          wiz.bubble.isVisible()
          and [a[0] for a in wiz.bubble._actions][0] == wc.wizard_tr("btn.moreinfo", "en")
          and wiz.bubble._link_buttons[0].text() == wc.wizard_tr("btn.itch", "en"))
wiz._dismiss()
# The idle tick: with the pitch not yet given this session and a low roll,
# the wizard speaks it; once given, the same roll idles as before.
import random as _random
_saved_random = _random.random
wiz._zxnr_pitched = False
_random.random = lambda: 0.0
wiz._idle_act()
check("a quiet idle tick brings up the pitch once a session",
      wiz.bubble.isVisible() and wiz._zxnr_pitched
      and [a[0] for a in wiz.bubble._actions][0] == wc.wizard_tr("btn.moreinfo", "en"))
wiz._dismiss()
wiz._idle_act()
check("...and never twice in one session", not wiz.bubble.isVisible())
_random.random = _saved_random
check("every zxnr.* text exists in every language and names the product",
      all("ZX Next Remote" in wc.TEXTS["zxnr.didyouknow"][lang] for lang in wc.WIZARD_LANGS)
      and all((wc.TEXTS[f"zxnr.more{i}"].get(lang) or "").strip()
              for i in (1, 2, 3) for lang in wc.WIZARD_LANGS))
# The NextSync guide's Remote Explorer nodes carry the itch.io link.
opened.clear()
wiz._show_guide_node("nextsync", "ns.remote")
labels = [b.text() for b in wiz.bubble._link_buttons]
check("the Remote Explorer guide node links to ZX Next Remote on itch.io",
      wc.wizard_tr("btn.itch", "en") in labels, str(labels))
wiz.bubble._link_buttons[labels.index(wc.wizard_tr("btn.itch", "en"))].click()
check("...and the link opens the itch.io page", opened == [wc.ZXNR_ITCH_URL], str(opened))
wiz._dismiss()

# ── corner: bottom-RIGHT since 9.7.11 ────────────────────────────────────
# Wizzy used to live bottom-LEFT, on top of the SD Card / NextSync tabs'
# vertical emulator strips and their Launch CSpect / Launch Mame buttons.
# Moving it right traded that for two new neighbours, both pinned here: the
# vertical scrollbar of every scrollable tab (the sprite is opaque to hit
# testing over its whole rect and ATE the down-arrow's clicks), and the
# bottom-right toast corner.
from PySide6.QtWidgets import (QScrollArea, QVBoxLayout,             # noqa: E402
                               QLabel, QWidget, QStyle)

_cw = QWidget()
_cl = QVBoxLayout(_cw)
_cl.setContentsMargins(9, 9, 9, 9)
_area = QScrollArea()
_in = QWidget()
_il = QVBoxLayout(_in)
for _i in range(200):
    _il.addWidget(QLabel("row %d" % _i))
_area.setWidget(_in)
_area.setWidgetResizable(True)
_cl.addWidget(_area)

_mgr = zw.WizardManager.__new__(zw.WizardManager)
_mgr._host = _cw
_mgr.sprite = zw.WizardSprite(_cw)
_mgr.bubble = zw.WizardBubble(_cw)
_mgr.bubble.show_message("Corner check.", [("Yes", lambda: None),
                                           ("No", lambda: None)])

_bad_bar, _bad_edge, _bad_bubble = [], [], []
for _w, _h in ((1400, 900), (1000, 700), (900, 650), (420, 500)):
    _cw.resize(_w, _h)
    _cw.show()
    _mgr.bubble.show()
    _mgr.sprite.show()
    app.processEvents()
    _mgr._reposition()
    app.processEvents()
    _sp, _bb = _mgr.sprite.geometry(), _mgr.bubble.geometry()
    if _sp.x() + _sp.width() > _w or _sp.y() + _sp.height() > _h:
        _bad_edge.append((_w, _h))
    if _bb.x() < 0 or _bb.y() < 0:
        _bad_bubble.append(("offscreen", _w, _h, _bb.x(), _bb.y()))
    elif (_sp.x() >= _bb.width() + 6
            and _bb.x() + _bb.width() > _sp.x() + 1):
        # Only assert "left of the sprite" where there is ROOM for it: below
        # the app's 900 px minimum width the clamp deliberately wins, and a
        # bubble sliding under the sprite beats one sliding off-screen.
        _bad_bubble.append(("overlap", _w, _h, _bb.x(), _bb.width(), _sp.x()))
    _vsb = _area.verticalScrollBar()
    if _vsb.isVisible():
        _bar = _vsb.rect().translated(_vsb.mapTo(_cw, _vsb.rect().topLeft()))
        if _sp.intersects(_bar):
            _bad_bar.append((_w, _h, str(_sp), str(_bar)))

check("wizard corner: the sprite stays inside the window at every size",
      not _bad_edge, str(_bad_edge))
check("wizard corner: the bubble opens to the LEFT of the sprite and never "
      "off-screen (a wide bubble on a narrow window slides into view)",
      not _bad_bubble, str(_bad_bubble))
check("wizard corner: the sprite never covers a scrollable tab's vertical "
      "scrollbar (its whole rect eats clicks, not just its artwork)",
      not _bad_bar, str(_bad_bar))
check("wizard corner: the right margin clears a scrollbar by the STYLE's "
      "metric, not a guess",
      _mgr._right_margin() > QApplication.style().pixelMetric(
          QStyle.PixelMetric.PM_ScrollBarExtent),
      str(_mgr._right_margin()))

# The promenade mirrors: it roams LEFT from the right-hand home and never
# leaves the window.
import random as _rnd                                                # noqa: E402
_rnd.seed(11)
_cw.resize(1400, 900)
_mgr._reposition()
_mgr._stroll_timer = type("T", (), {"start": lambda s: None,
                                    "stop": lambda s: None,
                                    "isActive": lambda s: False})()
_home = _mgr._stroll_home()
_bad_stroll = []
for _ in range(300):
    _mgr._start_stroll()
    _t = _mgr._stroll_target
    if _t < 0 or _t + _mgr.sprite.width() > 1400 or _t > _home:
        _bad_stroll.append(_t)
        break
check("wizard corner: strolls roam LEFT of home and stay in the window",
      not _bad_stroll, str(_bad_stroll))
check("wizard corner: _reposition and _stroll_home use the SAME right "
      "margin (they drift apart if one hardcodes it)",
      _mgr.sprite.x() == _home, "%d vs %d" % (_mgr.sprite.x(), _home))


print()
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILURE(S)")
    sys.exit(1)
print("RESULT: ALL WIZARD CHECKS PASSED")
