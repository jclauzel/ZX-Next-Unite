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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QGroupBox,
                               QLabel, QLineEdit, QPushButton, QRadioButton,
                               QTabWidget, QVBoxLayout, QWidget)

from zxnu_i18n import (CATALOGS, DEFAULT_UI_LANGUAGE, UI_LANGUAGES,
                       normalize_ui_language, translate_widget_tree, ui_tr)

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


def main():
    QApplication(sys.argv)
    test_catalog_integrity()
    test_helpers()
    test_widget_walk()
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
