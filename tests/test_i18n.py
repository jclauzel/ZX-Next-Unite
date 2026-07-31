"""Unit tests for zxnu_i18n — the UI translation catalogs and the
widget-tree retranslation walk behind Settings → "Application language:".

Catalog side (no Qt needed): every language carries exactly the same key
set, no translation is empty, and strings with markers the UI relies on
(%, emoji, trailing spaces/colons) keep them. Walk side (offscreen
QApplication): a synthetic widget tree round-trips en -> es -> fr -> en
losslessly, dynamic rewrites are adopted rather than clobbered, unknown
texts pass through untouched, and the two deliberate exclusions hold —
QTabWidget titles (dispatch keys) and QComboBox item texts (option values)
are never rewritten."""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QGroupBox,
                               QLabel, QLineEdit, QPushButton, QRadioButton,
                               QTabWidget, QVBoxLayout, QWidget)

from zxnu_i18n import (CATALOGS, DEFAULT_UI_LANGUAGE, UI_LANGUAGES,
                       _ui_language_from_locale, mark_combo_items_translatable,
                       normalize_ui_language, set_current_ui_language,
                       system_ui_language, translate_widget_tree, ui_tr,
                       ui_tr_now)

ok = True


def check(label, cond, detail=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'} {label}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        ok = False


def test_catalog_integrity():
    codes = [c for c, _n in UI_LANGUAGES]
    check("en is the default and first", codes[0] == DEFAULT_UI_LANGUAGE == "en")
    check("catalog per non-English language",
          sorted(CATALOGS) == sorted(c for c in codes if c != "en"))
    base = set(CATALOGS["es"])
    for lang, cat in CATALOGS.items():
        missing = base - set(cat)
        extra = set(cat) - base
        check(f"{lang}: same keys as es", not missing and not extra,
              f"missing={sorted(missing)[:3]} extra={sorted(extra)[:3]}")
        empty = [k for k, v in cat.items() if not v.strip()]
        check(f"{lang}: no empty translations", not empty, str(empty[:3]))
        # Markers the UI relies on must survive translation.
        bad_pct = [k for k, v in cat.items() if ("%" in k) != ("%" in v)]
        check(f"{lang}: % markers preserved", not bad_pct, str(bad_pct[:3]))
        bad_ws = [k for k, v in cat.items()
                  if (k.startswith(" ") and not v.startswith(" "))
                  or (k.endswith(" ") and not v.endswith(" "))]
        check(f"{lang}: leading/trailing spaces preserved", not bad_ws,
              str(bad_ws[:3]))
        bad_emoji = [k for k, v in cat.items()
                     if k[:1] in "▶⬇🎮💾🔁🕹" and k[:1] not in v]
        check(f"{lang}: emoji prefixes preserved", not bad_emoji,
              str(bad_emoji[:3]))


def test_helpers():
    check("normalize: known codes pass",
          [normalize_ui_language(c) for c, _n in UI_LANGUAGES]
          == [c for c, _n in UI_LANGUAGES])
    check("normalize: junk falls back to en",
          normalize_ui_language("xx") == "en"
          and normalize_ui_language(None) == "en"
          and normalize_ui_language("  ES ") == "es")
    check("ui_tr: en passthrough", ui_tr("Search", "en") == "Search")
    check("ui_tr: translates a known key", ui_tr("Search", "es") == "Buscar")
    check("ui_tr: unknown text passes through",
          ui_tr("Totally dynamic text", "fr") == "Totally dynamic text")
    check("ui_tr: empty-safe", ui_tr("", "es") == "" and ui_tr(None, "es") is None)

    # First-run OS-language detection: locale-name mapping + env override.
    check("locale mapping",
          [_ui_language_from_locale(n) for n in
           ("es_ES", "pt_BR", "pl_PL", "ru_RU", "cs_CZ", "fr-FR",
            "en_GB", "de_DE", "")]
          == ["es", "pt", "pl", "ru", "cs", "fr", "en", "en", "en"])
    os.environ["ZX_NEXT_UNITE_UI_LANGUAGE"] = "ru"
    try:
        check("env override wins", system_ui_language() == "ru")
        os.environ["ZX_NEXT_UNITE_UI_LANGUAGE"] = "klingon"
        check("bad override falls back to en", system_ui_language() == "en")
    finally:
        del os.environ["ZX_NEXT_UNITE_UI_LANGUAGE"]
    check("system detection returns a supported code",
          system_ui_language() in [c for c, _n in UI_LANGUAGES])


def build_tree():
    root = QWidget()
    lay = QVBoxLayout(root)
    w = {
        "label": QLabel("Local path: "),
        "button": QPushButton("Select NextZXOS disk Image"),
        "check": QCheckBox("Slow transfer"),
        "radio": QRadioButton("Sync once"),
        "group": QGroupBox("Sync mode"),
        "edit": QLineEdit(),
        "combo": QComboBox(),
        "dyn": QLabel("C:/some/dynamic/path.hdf"),
        "tabs": QTabWidget(),
    }
    w["edit"].setPlaceholderText("Filter by name...")
    w["edit"].setToolTip("Browse mode")
    w["combo"].setEditable(True)
    w["combo"].lineEdit().setPlaceholderText("SD card image path...")
    w["combo"].addItem("Cancel")           # option VALUES must never change
    w["tabs"].addTab(QWidget(), "Settings 🔩")   # dispatch key: hands off
    for x in w.values():
        if x is not w["tabs"]:
            lay.addWidget(x)
    lay.addWidget(w["tabs"])
    return root, w


def test_widget_walk():
    root, w = build_tree()

    translate_widget_tree(root, "es")
    check("walk translates label", w["label"].text() == "Ruta local: ")
    check("walk translates button",
          w["button"].text() == "Seleccionar imagen de disco NextZXOS")
    check("walk translates checkbox", w["check"].text() == "Transferencia lenta")
    check("walk translates radio", w["radio"].text() == "Sincronizar una vez")
    check("walk translates groupbox", w["group"].title() == "Modo de sincronización")
    check("walk translates placeholder",
          w["edit"].placeholderText() == "Filtrar por nombre…")
    check("walk translates combo placeholder",
          w["combo"].lineEdit().placeholderText() == "Ruta de la imagen de tarjeta SD…")
    check("walk translates tooltip", w["edit"].toolTip() == "Modo exploración")
    check("walk leaves dynamic text alone",
          w["dyn"].text() == "C:/some/dynamic/path.hdf")
    check("walk never touches tab titles", w["tabs"].tabText(0) == "Settings 🔩")
    check("walk never touches combo items", w["combo"].itemText(0) == "Cancel")

    translate_widget_tree(root, "fr")
    check("es -> fr switches from the source",
          w["label"].text() == "Chemin local : "
          and w["check"].text() == "Transfert lent")

    translate_widget_tree(root, "en")
    check("back to en restores originals",
          w["label"].text() == "Local path: "
          and w["button"].text() == "Select NextZXOS disk Image"
          and w["edit"].placeholderText() == "Filter by name..."
          and w["edit"].toolTip() == "Browse mode")

    # A text the app rewrites at runtime is ADOPTED as the new source.
    translate_widget_tree(root, "es")
    w["label"].setText("Next: /games")            # app-driven dynamic rewrite
    translate_widget_tree(root, "es")
    check("app-rewritten text survives a re-walk",
          w["label"].text() == "Next: /games")
    w["label"].setText("Local path: ")            # app writes a catalogued text
    translate_widget_tree(root, "es")
    check("re-written catalogued text is translated again",
          w["label"].text() == "Ruta local: ")

    # A widget whose toolTip method is shadowed by a str attribute (it has
    # happened in the monolith) must not break the walk.
    w["button"].toolTip = "shadowed"
    translate_widget_tree(root, "fr")
    check("walk survives a shadowed toolTip attribute",
          w["check"].text() == "Transfert lent")


def test_runtime_language():
    """ui_tr_now follows the language most recently applied — the runtime
    side (toasts) of the catalogs."""
    set_current_ui_language("fr")
    check("ui_tr_now translates after set_current_ui_language",
          ui_tr_now("NextSync server started") == "Serveur NextSync démarré",
          ui_tr_now("NextSync server started"))
    check("ui_tr_now translates templates before .format()",
          ui_tr_now("Found: {emulators}.").format(emulators="CSpect")
          == "Détecté : CSpect.",
          ui_tr_now("Found: {emulators}.").format(emulators="CSpect"))
    check("ui_tr_now passes unknown text through",
          ui_tr_now("no such catalog key !?") == "no such catalog key !?")
    set_current_ui_language("en")
    check("ui_tr_now back to English is identity",
          ui_tr_now("NextSync server started") == "NextSync server started")


def test_toast_tripwire():
    """Every LITERAL string handed to a toast (title or body of _show_toast /
    _on_toast / _show_sd_notification) and every literal ui_tr_now(...)
    template must exist in EVERY language catalog — otherwise a new toast
    silently stays English for translated UIs (the exact bug this guards)."""
    import ast

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    toast_funcs = {"_show_toast", "_on_toast", "_show_sd_notification"}
    literals = set()
    for fname in ("zxnu_main.py", "zxnu_nextsync_pane.py",
                  "zxnu_remote_explorer.py", "zxnu_getit_pane.py",
                  "zxnu_zxart_pane.py", "zxnu_zxdb_pane.py",
                  # runtime context menus + confirm/failure dialogs:
                  "zxnu_sdcard_ops.py", "zxnu_nextsync_ops.py",
                  "zxnu_favorites_pane.py", "zxnu_pygame.py",
                  # buttons relabelled by setText long after the widget tree
                  # was translated (Classic/Retro toggles, itch.io Connect):
                  "zxnu_retro_ui.py", "zxnu_unite_pane.py",
                  "zxnu_itchio_pane.py",
                  # NextSync console: the user-facing sync-server log lines:
                  "zxnu_workers.py"):
        tree = ast.parse(open(os.path.join(repo, fname), encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else node.func.id if isinstance(node.func, ast.Name)
                    else "")
            if name in toast_funcs:
                for arg in node.args[:2]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        literals.add(arg.value)
            elif name == "ui_tr_now" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    literals.add(arg.value)
    # The pre-toast strings ui_tr/ui_tr_now already handled elsewhere (the
    # first-run language toast) live in the general catalogs; empty/whitespace
    # separators are not translatable content.
    literals = {s for s in literals if s.strip()}
    check("tripwire found a plausible number of toast strings",
          len(literals) >= 40, f"only {len(literals)} found")
    for code, _n in UI_LANGUAGES:
        if code == DEFAULT_UI_LANGUAGE:
            continue
        missing = sorted(s for s in literals if s not in CATALOGS[code])
        check(f"every toast string is translated in '{code}'",
              not missing, "missing: " + "; ".join(repr(m) for m in missing[:4]))


def test_emulator_option_combos():
    """The SD Card tab's CSpect/MAME option combos are the one opt-in to combo
    ITEM translation, and the launch arguments must survive it.

    Translating those labels is only safe because the selection is read by
    index: a label lookup would find nothing once the UI is in Spanish and the
    option would silently vanish from the command line. Cover both halves —
    the items do get translated, and the arguments stay English."""
    print("\n== emulator option combos ==")
    from zxnu_config import (CSPECT_ESC, CSPECT_JOYSTICK, CSPECT_SCREEN_SIZES,
                             CSPECT_SOUND, MAME_ESC, MAME_JOYSTICK, MAME_SOUND,
                             emulator_option_argument)

    check("option argument comes from the index",
          emulator_option_argument(CSPECT_SOUND, 1) == "-sound"
          and emulator_option_argument(CSPECT_ESC, 1) == "-esc"
          and emulator_option_argument(MAME_ESC, 1) == "-confirm_quit"
          and emulator_option_argument(MAME_SOUND, 4) == "-sound none")
    check("index 0 of an 'on' option passes nothing",
          emulator_option_argument(CSPECT_SOUND, 0) == ""
          and emulator_option_argument(CSPECT_ESC, 0) == "")
    check("an out-of-range or junk index is empty, never an exception",
          emulator_option_argument(CSPECT_SOUND, 99) == ""
          and emulator_option_argument(CSPECT_SOUND, None) == ""
          and emulator_option_argument((), 0) == "")

    # A marked combo gets its items translated; an unmarked one must not.
    host = QWidget()
    layout = QVBoxLayout(host)
    marked = QComboBox()
    for label, _arg in CSPECT_SOUND:
        marked.addItem(label)
    mark_combo_items_translatable(marked)
    unmarked = QComboBox()
    for label, _arg in CSPECT_SOUND:
        unmarked.addItem(label)
    layout.addWidget(marked)
    layout.addWidget(unmarked)

    marked.setCurrentIndex(1)                  # "Sound Off" -> "-sound"
    translate_widget_tree(host, "es")
    check("a marked combo's items are translated",
          marked.itemText(0) == "Sonido activado"
          and marked.itemText(1) == "Sonido desactivado",
          f"{marked.itemText(0)!r}, {marked.itemText(1)!r}")
    check("an unmarked combo's items are left alone",
          unmarked.itemText(0) == "Sound On", unmarked.itemText(0))
    check("translating items never moves the selection",
          marked.currentIndex() == 1, str(marked.currentIndex()))
    check("the launch argument survives translation",
          emulator_option_argument(CSPECT_SOUND, marked.currentIndex()) == "-sound")

    translate_widget_tree(host, "fr")
    check("switching language again re-translates from the source",
          marked.itemText(1) == "Son désactivé", marked.itemText(1))
    translate_widget_tree(host, "en")
    check("back to English restores the original item texts",
          [marked.itemText(i) for i in range(marked.count())]
          == [label for label, _a in CSPECT_SOUND],
          str([marked.itemText(i) for i in range(marked.count())]))

    # Every label the marked combos can show must exist in every catalog.
    labels = {label for options in (CSPECT_SCREEN_SIZES, CSPECT_SOUND,
                                    CSPECT_JOYSTICK, CSPECT_ESC, MAME_SOUND,
                                    MAME_JOYSTICK, MAME_ESC)
              for label, _arg in options}
    # Purely technical/numeric entries stay English on purpose.
    labels -= {"50Hz", "60Hz", "Aspect 2:1", "Aspect 1:1"}
    for code, _n in UI_LANGUAGES:
        if code == DEFAULT_UI_LANGUAGE:
            continue
        missing = sorted(s for s in labels if s not in CATALOGS[code])
        check(f"every emulator option label is translated in '{code}'",
              not missing, "missing: " + "; ".join(repr(m) for m in missing[:4]))
    set_current_ui_language(DEFAULT_UI_LANGUAGE)


def test_log_line_placeholders():
    """Every {placeholder} in a NextSync log template must survive translation.

    These lines are built as ui_tr_now("… {x} …").format(x=…) inside a RUNNING
    sync, so a translation that drops, renames or mistypes a field raises
    KeyError/IndexError mid-transfer — in one language only, which is exactly
    the kind of bug that never shows up in testing. Also guards the two rules
    the localisation was written under: Next commands stay literal, and
    protocol diagnostics stay English."""
    import ast
    import re
    print("\n== NextSync log line templates ==")
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    field = re.compile(r"\{([a-z_]+)\}")

    templates = set()
    for fname in ("zxnu_workers.py", "zxnu_nextsync_ops.py",
                  "zxnu_nextsync_pane.py"):
        tree = ast.parse(open(os.path.join(repo, fname), encoding="utf-8").read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id",
                                getattr(node.func, "attr", "")) == "ui_tr_now"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                templates.add(node.args[0].value)
    templates = {t for t in templates if t.strip()}
    check("found the NextSync log templates", len(templates) >= 50,
          f"only {len(templates)}")

    bad = []
    for code, _n in UI_LANGUAGES:
        if code == DEFAULT_UI_LANGUAGE:
            continue
        for src in templates:
            translated = CATALOGS[code].get(src)
            if translated is None:
                bad.append(f"{code}: MISSING {src[:44]!r}")
                continue
            if sorted(field.findall(src)) != sorted(field.findall(translated)):
                bad.append(f"{code}: fields differ in {src[:44]!r}")
    check("every log template keeps its placeholders in every language",
          not bad, "; ".join(bad[:4]))

    # Each template must actually render with its own fields — the real
    # .format() call, not just a regex comparison.
    render_bad = []
    for code, _n in UI_LANGUAGES:
        set_current_ui_language(code)
        for src in templates:
            fields = {f: "X" for f in field.findall(src)}
            try:
                ui_tr_now(src).format(**fields)
            except Exception as exc:
                render_bad.append(f"{code}: {src[:40]!r} -> {exc!r}")
    set_current_ui_language(DEFAULT_UI_LANGUAGE)
    check("every log template renders with .format() in every language",
          not render_bad, "; ".join(render_bad[:4]))

    # Commands the user types on the Next must reach them verbatim. New log
    # lines interpolate them ({command}) so they can't be translated at all;
    # a few older strings embed them, which is fine ONLY while every
    # translation reproduces the command exactly — a localised '.sync5' would
    # be a command the Next rejects.
    commands = (".sync5 -listen", ".sync5 -send", ".sync5", ".http")
    mangled = []
    for src in templates:
        present = [c for c in commands if c in src]
        if not present:
            continue
        for code, _n in UI_LANGUAGES:
            if code == DEFAULT_UI_LANGUAGE:
                continue
            translated = CATALOGS[code].get(src)
            if translated is None:
                continue          # untranslated: the English command stands
            for cmd in present:
                if cmd not in translated:
                    mangled.append(f"{code}: {cmd!r} lost in {src[:36]!r}")
    check("every Next command survives translation verbatim",
          not mangled, "; ".join(mangled[:4]))

    # Protocol diagnostics stay English (the agreed scope).
    debug_markers = ("packetno", "checksums", "Packet sequence error",
                     "Using protocol version", "Unknown command")
    leaked = sorted(t for t in templates
                    if any(m in t for m in debug_markers))
    check("protocol diagnostics were not translated", not leaked,
          str(leaked[:3]))


def test_gallery_item_viewer():
    """Both item viewers must come up translated.

    They are built when the user OPENS an item — long after the startup walk
    translated the main window — so their action buttons stayed English even
    though the catalogs had the strings all along. The Qt viewer now walks its
    own finished subtree; the pygame one translates its labels at construction
    (nothing else can, since it draws them itself)."""
    import ast
    print("\n== gallery item viewers ==")
    from zxnu_gallery import GalleryItemViewer

    set_current_ui_language("fr")
    # extra_fetch_cb is the async image loader; no screenshots => never called.
    viewer = GalleryItemViewer("Test title", [], [], lambda *a, **k: None)
    labels = {
        "download": viewer.btn_download.text(),
        "send_sd": viewer.btn_send_sd.text(),
        "cspect": viewer.btn_launch_cspect.text(),
        "mame": viewer.btn_launch_mame.text(),
        "send_ns": viewer.btn_send_ns.text(),
        "uninstall": viewer.btn_uninstall.text(),
        "open_folder": viewer.btn_open_folder.text(),
    }
    check("the Classic viewer's buttons are translated on construction",
          labels["download"] == ui_tr_now("⬇  Download")
          and labels["send_sd"] == ui_tr_now("💾  Send to SD card")
          and labels["send_ns"] == ui_tr_now("🔁  Send via NextSync"),
          str(labels))
    check("...and none of them is still the English source",
          labels["download"] != "⬇  Download"
          and labels["uninstall"] != "🗑  Uninstall"
          and labels["open_folder"] != "📂  Open install folder",
          str(labels))

    # The site-name template keeps the site as data. Cover each real caller:
    # zxArt, ZXDB and itch.io all route through set_open_web_url.
    for site in ("zxart.ee", "zxinfo.dk", "itch.io"):
        viewer.set_open_web_url(f"https://{site}/x", site)
        text = viewer.btn_open_web.text()
        check(f"'Open on {site}' is translated around the site name",
              site in text and text != f"🌐  Open on {site}", text)
    check("the globe tooltip is translated too",
          viewer._web_btn.toolTip() == ui_tr_now("Open on {site}").format(
              site="itch.io"),
          viewer._web_btn.toolTip())

    # itch.io relabels the shared buttons through _itchio_label_button, which
    # feeds BOTH viewers — those captions must be catalogued.
    itch_labels = ["⬇  Install", "⬇  Installing…", "✓  Re-install",
                   "📂  Open download folder", "About"]
    for label in itch_labels:
        check(f"itch.io label {label!r} translates",
              ui_tr_now(label) != label, ui_tr_now(label))
    viewer.deleteLater()
    set_current_ui_language(DEFAULT_UI_LANGUAGE)

    # The pygame viewer draws its own labels, so assert at the source level
    # that every one of them goes through ui_tr_now (it cannot be constructed
    # here — it needs a live pygame surface).
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    tree = ast.parse(open(os.path.join(repo, "zxnu_pygame.py"),
                          encoding="utf-8").read())
    raw = []
    for node in ast.walk(tree):
        # <something>.label = "literal" / f"..." / "a" if c else "b"
        # must all go through ui_tr_now, never straight to the drawn text.
        if not (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Attribute) and t.attr == "label"
                        for t in node.targets)):
            continue
        candidates = ([node.value.body, node.value.orelse]
                      if isinstance(node.value, ast.IfExp) else [node.value])
        for value in candidates:
            if (isinstance(value, ast.Constant) and isinstance(value.value, str)
                    and value.value.strip()):
                raw.append(value.value)
            elif isinstance(value, ast.JoinedStr):      # an f-string label
                raw.append("f-string at line %d" % node.lineno)
    check("every Retro viewer button label goes through ui_tr_now",
          not raw, f"still raw: {raw[:4]}")

    # Every viewer button string must exist in all six catalogs.
    viewer_labels = ["🌐  Open on website", "🌐  Open on {site}", "⬇  Download",
                     "💾  Send to SD card", "🕹  Launch CSpect",
                     "🕹  Launch Mame", "🔁  Send via NextSync",
                     "🗑  Uninstall", "📂  Open install folder"]
    for code, _n in UI_LANGUAGES:
        if code == DEFAULT_UI_LANGUAGE:
            continue
        missing = [s for s in viewer_labels if s not in CATALOGS[code]]
        check(f"every item-viewer button is translated in '{code}'",
              not missing, str(missing))


def main():
    QApplication(sys.argv)
    test_catalog_integrity()
    test_helpers()
    test_runtime_language()
    test_toast_tripwire()
    test_widget_walk()
    test_emulator_option_combos()
    test_log_line_placeholders()
    test_gallery_item_viewer()
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
