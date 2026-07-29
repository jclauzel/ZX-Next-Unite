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
check("intro offers tour/later/off actions", len(wiz.bubble._actions) == 3)
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
check("remote branch teaches -listen and -send",
      "-send" in " ".join(wiz.bubble._pages))
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

# Markdown teaser extraction (pure function, no network).
md = "# Title\n\n![badge](x.png)\n\nThe **SD Card** tab lets you [mount](u) images.\n\nMore text."
check("teaser strips markdown to the first paragraph",
      zw._teaser_from_markdown(md) == "The SD Card tab lets you mount images.")

print()
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILURE(S)")
    sys.exit(1)
print("RESULT: ALL WIZARD CHECKS PASSED")
