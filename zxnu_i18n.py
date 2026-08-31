"""UI internationalisation for ZX-Next-Unite (strangler-friendly, no Qt
Linguist toolchain needed).

The app's UI text is plain English literals spread across the monolith and
the pane modules, so instead of wrapping thousands of call sites in tr(),
translation happens on the CONSTRUCTED widget tree: `translate_widget_tree`
walks every widget and swaps the texts it finds through the catalogs below,
which are keyed by the exact English source string. Each widget's current
source text is cached the first time it is touched, so switching languages
(including back to English) is lossless and idempotent, and a text the app
rewrites at runtime is adopted as the new source on the next walk instead of
being clobbered.

What is translated: QLabel/QPushButton/QCheckBox/QRadioButton texts,
QGroupBox titles, QLineEdit (and editable QComboBox) placeholders, and any
widget tooltip present in the catalog. What is deliberately NOT touched:

  * QTabWidget tab titles — they are dispatch keys (`tabText(i).startswith`
    / `"Unite!" in tabText`) in the monolith AND several pane modules, and
    mostly proper names anyway;
  * QComboBox item texts — many handlers compare `currentText()` against the
    English literals (option values). The one exception is opt-in: a combo
    marked with `mark_combo_items_translatable()` DOES get its items
    translated, which is only safe once nothing reads its `currentText()` as
    a key (the SD Card tab's CSpect/MAME option combos qualify — they are read
    by index, see `emulator_option_argument`);
  * strings generated at runtime (log lines, toasts, message boxes, context
    menus built at popup time) — those keep English until their call sites
    adopt `ui_tr` in a later sitting.

Only exact catalog matches are ever replaced, so dynamic content (paths,
game titles, counters) passes through untouched. A missing key simply stays
English — add the string to every language dict below and it is picked up on
the next walk; no other code changes needed. The catalogs are plain dicts on
purpose: community corrections are one-line diffs.

Languages: Spanish (es), Portuguese (pt), Polish (pl), Russian (ru),
Czech (cs), French (fr); English (en) is the source and needs no catalog.
"""

import os
from weakref import WeakKeyDictionary

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGroupBox, QLabel, QLineEdit, QPushButton,
    QRadioButton, QWidget,
)

# (code, native name) — the Settings combo shows the native names and stores
# the code (itemData), so the combo itself never needs translating.
UI_LANGUAGES = [
    ("en", "English"),
    ("es", "Español"),
    ("pt", "Português"),
    ("pl", "Polski"),
    ("ru", "Русский"),
    ("cs", "Čeština"),
    ("fr", "Français"),
]

DEFAULT_UI_LANGUAGE = "en"


def normalize_ui_language(code):
    """A valid language code from a saved config value ('' / bad -> 'en')."""
    code = (code or "").strip().lower()
    return code if any(code == c for c, _n in UI_LANGUAGES) else DEFAULT_UI_LANGUAGE


def _ui_language_from_locale(locale_name):
    """Map a Qt locale name ("es_ES", "pt_BR", "ru", …) to a supported UI
    language code; anything we don't ship falls back to English."""
    lang = (locale_name or "").replace("-", "_").split("_")[0].strip().lower()
    return normalize_ui_language(lang)


def system_ui_language():
    """The UI language matching the OS locale, or "en" when we don't ship it.

    Used on the very first run (no saved ``ui_language`` yet) to start the
    app in the user's own language. The ``ZX_NEXT_UNITE_UI_LANGUAGE``
    environment variable overrides the OS locale when set (power users and
    the offscreen test suite; Qt ignores LANG/LC_ALL on Windows)."""
    override = os.environ.get("ZX_NEXT_UNITE_UI_LANGUAGE", "").strip()
    if override:
        return normalize_ui_language(override)
    return _ui_language_from_locale(QLocale.system().name())


def ui_tr(text, lang):
    """Translate one exact UI string into *lang* (English/unknown pass through)."""
    if not text or lang in ("", None, DEFAULT_UI_LANGUAGE):
        return text
    return CATALOGS.get(lang, {}).get(text, text)


# Language most recently applied via translate_widget_tree — the runtime side
# of the catalogs: lets strings generated AFTER construction (toast titles and
# bodies, .format() templates) translate without threading `lang` through
# every call site.
_current_ui_language = DEFAULT_UI_LANGUAGE


def set_current_ui_language(lang):
    """Record *lang* as the active UI language for ui_tr_now()."""
    global _current_ui_language
    _current_ui_language = normalize_ui_language(lang)


def current_ui_language():
    """The language most recently applied via translate_widget_tree."""
    return _current_ui_language


def ui_tr_now(text):
    """Translate a runtime-generated string into the currently applied UI
    language. For dynamic content, translate the TEMPLATE (the catalogs key
    templates with {placeholders}) and .format() afterwards — never the
    already-formatted result, which cannot exact-match a catalog key."""
    return ui_tr(text, _current_ui_language)


# Per-widget source-text cache: {widget: {attr: {"src": str, "applied": str}}}.
# Weak keys so destroyed widgets drop out on their own.
_SRC_CACHE = WeakKeyDictionary()

# Opt-in marker for QComboBox ITEM translation. Item texts are dispatch keys
# across the app (see the module docstring), so the walk translates them only
# for combos explicitly marked with this Qt property.
TRANSLATABLE_ITEMS_PROPERTY = "zxnuTranslatableItems"


def mark_combo_items_translatable(combo):
    """Let :func:`translate_widget_tree` translate *combo*'s item texts.

    Only mark a combo whose selection is read by INDEX — never one whose
    ``currentText()`` is compared against an English literal or persisted as a
    value, because the displayed text stops matching as soon as the UI is
    translated. Returns *combo* so it can be marked inline.
    """
    combo.setProperty(TRANSLATABLE_ITEMS_PROPERTY, True)
    return combo


def _retranslate(widget, attr, getter, setter, lang):
    """Re-apply one text attribute of one widget for *lang*.

    The first touch caches the current text as the source. If a later walk
    finds the text changed by the app (not by us), that new text becomes the
    source — dynamic rewrites are adopted, never clobbered."""
    try:
        cur = getter()
    except Exception:
        return
    if not cur:
        return
    entry = _SRC_CACHE.setdefault(widget, {}).get(attr)
    if entry is not None and cur == entry["applied"]:
        src = entry["src"]
    else:
        src = cur                      # first visit, or app-rewritten text
    new = ui_tr(src, lang)
    try:
        if new != cur:
            setter(new)
    except Exception:
        return
    _SRC_CACHE[widget][attr] = {"src": src, "applied": new}


def translate_widget_tree(root, lang):
    """Walk *root* and every child widget, applying the *lang* catalog.

    Safe to call repeatedly and with any language (including "en", which
    restores the original texts). Unknown texts are left untouched."""
    lang = normalize_ui_language(lang)
    # Every full-tree application also becomes the language for runtime
    # strings (toasts etc.) — see ui_tr_now().
    set_current_ui_language(lang)
    widgets = [root] + root.findChildren(QWidget)
    for w in widgets:
        try:
            if isinstance(w, (QPushButton, QCheckBox, QRadioButton, QLabel)):
                _retranslate(w, "text", w.text, w.setText, lang)
            elif isinstance(w, QGroupBox):
                _retranslate(w, "title", w.title, w.setTitle, lang)
            elif isinstance(w, QLineEdit):
                _retranslate(w, "placeholder", w.placeholderText,
                             w.setPlaceholderText, lang)
            elif isinstance(w, QComboBox):
                le = w.lineEdit()
                if le is not None:
                    _retranslate(le, "placeholder", le.placeholderText,
                                 le.setPlaceholderText, lang)
                if w.property(TRANSLATABLE_ITEMS_PROPERTY):
                    # Opt-in only (see TRANSLATABLE_ITEMS_PROPERTY). setItemText
                    # changes no selection, so currentIndexChanged — the only
                    # signal the marked combos connect — never fires here.
                    for i in range(w.count()):
                        _retranslate(w, f"item{i}",
                                     lambda w=w, i=i: w.itemText(i),
                                     lambda t, w=w, i=i: w.setItemText(i, t),
                                     lang)
            # Tooltips on every widget type. The unbound form dodges any
            # instance attribute shadowing the method (it has happened).
            _retranslate(w, "tooltip", lambda w=w: QWidget.toolTip(w),
                         lambda t, w=w: QWidget.setToolTip(w, t), lang)
        except RuntimeError:
            continue                   # C++ side already deleted


# ---------------------------------------------------------------------------
# Catalogs. Keys are the EXACT English strings (including leading/trailing
# spaces and emoji). Keep every language dict carrying the same keys — the
# test suite (tests/test_i18n.py) enforces it.
# ---------------------------------------------------------------------------

CATALOGS = {
    "es": {
        # ---- 9.6.0: per-emulator button colour ----
        "Set the {emulator} color…":
            "Establecer el color de {emulator}…",
        "Reset the {emulator} color":
            "Restablecer el color de {emulator}",
        # ---- 9.6.0: forgetting a remembered SD image path ----
        "Remove \"{path}\" from the list":
            "Quitar \"{path}\" de la lista",
        "Clear the whole list":
            "Vaciar toda la lista",
        "Removed {path} from the image list — the image file itself was not deleted.":
            "Se quitó {path} de la lista de imágenes — el archivo de imagen en sí no se ha eliminado.",
        "Cleared the image list — no image files were deleted.":
            "Lista de imágenes vaciada — no se ha eliminado ningún archivo de imagen.",
        "Clear the image list?":
            "¿Vaciar la lista de imágenes?",
        "Forget all {count} remembered image paths? The image files themselves are not deleted.":
            "¿Olvidar las {count} rutas de imagen recordadas? Los archivos de imagen en sí no se eliminan.",
        "Remove the image path shown on the left from the list.\nThe image file itself is not deleted.":
            "Quitar de la lista la ruta de imagen mostrada a la izquierda.\nEl archivo de imagen en sí no se elimina.",
        "Path to the SD card image (.img / .hdf).\nType a path directly, click the arrow to pick from recently loaded images,\nor use the 'Select NextZXOS disk Image' button to browse.\nRight-click the box for list options, or press Delete on a dropdown entry to forget it.":
            "Ruta de la imagen de la tarjeta SD (.img / .hdf).\nEscribe una ruta directamente, pulsa la flecha para elegir entre las imágenes recientes,\no usa el botón 'Seleccionar imagen de disco NextZXOS' para examinar.\nHaz clic derecho en el cuadro para ver opciones de lista, o pulsa Supr en una entrada del desplegable para olvidarla.",
        # ---- GetIt starter pack ----
        'Assembling the starter pack…': 'Preparando el pack de inicio…',
        'Downloading {title} ({idx}/{total})…': 'Descargando {title} ({idx}/{total})…',
        ('Everything on GetIt is freely distributable; the files are written to {dir} '
         'inside the loaded image.'):
            ('Todo lo que hay en GetIt es de libre distribución; los archivos se '
             'escriben en {dir} dentro de la imagen cargada.'),
        'Failed: {names}': 'Fallaron: {names}',
        'Fetching the GetIt catalogue…': 'Obteniendo el catálogo GetIt…',
        ('Fill the loaded SD image with a hand-picked selection of modern homebrew '
         'from the GetIt catalogue — everything on GetIt is freely distributable.'):
            ('Llena la imagen SD cargada con una selección hecha a mano de homebrew '
             'moderno del catálogo GetIt: todo lo que hay en GetIt es de libre '
             'distribución.'),
        'Fill your SD image with {count} hand-picked homebrew titles from the GetIt catalogue?': '¿Llenar tu imagen SD con {count} títulos homebrew seleccionados a mano del catálogo GetIt?',
        'Load a disk image first (SD Card tab), then try again.': 'Carga primero una imagen de disco (pestaña SD Card) y vuelve a intentarlo.',
        'No network connection.': 'Sin conexión de red.',
        'Not in the catalogue right now: {names}': 'Ahora mismo no está(n) en el catálogo: {names}',
        'Starter pack': 'Pack de inicio',
        'Starter pack cancelled — {done} title(s) were installed.': 'Pack de inicio cancelado: se instalaron {done} título(s).',
        'Starter pack complete: {done} of {total} titles installed to {dir}.': 'Pack de inicio completado: {done} de {total} títulos instalados en {dir}.',
        'Starter pack failed: {error}': 'El pack de inicio falló: {error}',
        '🎁 Starter pack': '🎁 Pack de inicio',
        # ---- context menus & runtime dialogs ----
        '"{name}" already exists in this folder.': '"{name}" ya existe en esta carpeta.',
        'Add drive {letter}: to the list?': '¿Añadir la unidad {letter}: a la lista?',
        'Add Next drive': 'Añadir unidad del Next',
        'Confirm deletion': 'Confirmar eliminación',
        'Copy': 'Copiar',
        'Copy all text': 'Copiar todo el texto',
        'Copy path to clipboard': 'Copiar ruta al portapapeles',
        'Copy text to clipboard': 'Copiar texto al portapapeles',
        'Could not create {name}:': 'No se pudo crear {name}:',
        'Could not create:': 'No se pudo crear:',
        'Could not delete:': 'No se pudo eliminar:',
        'Could not extract {name}:': 'No se pudo extraer {name}:',
        'Could not rename:': 'No se pudo renombrar:',
        'Create directory failed': 'Error al crear el directorio',
        'Create new directory': 'Crear nuevo directorio',
        'Create new directory…': 'Crear nuevo directorio…',
        'Cut': 'Cortar',
        'Decrease font size': 'Reducir tamaño de letra',
        'Delete failed': 'Error al eliminar',
        'Delete from the local disk?': '¿Eliminar del disco local?',
        'Delete on the Next? Folders are deleted with everything inside them.': '¿Eliminar en el Next? Las carpetas se eliminan con todo su contenido.',
        'Delete the file "{name}"?': '¿Eliminar el archivo "{name}"?',
        'Delete the folder "{name}" and all of its contents?': '¿Eliminar la carpeta "{name}" y todo su contenido?',
        'Delete these {count} items? Folders are deleted with all of their contents.': '¿Eliminar estos {count} elementos? Las carpetas se eliminan con todo su contenido.',
        'Deleted files are sent to the Recycle Bin.': 'Los archivos eliminados se envían a la Papelera de reciclaje.',
        'Download (:<-)': 'Descargar (:<-)',
        'Download content': 'Descargar contenido',
        'Download Failed': 'Descarga fallida',
        'Drive letter of the additional SD reader/partition (D..P):': 'Letra de unidad del lector SD/partición adicional (D..P):',
        'Extraction Failed': 'Extracción fallida',
        'Failed to download the NextZXOS image:': 'No se pudo descargar la imagen de NextZXOS:',
        'Fetch issue info for this magazine': 'Obtener información de números de esta revista',
        'Fetch single magazine by name': 'Buscar una revista por nombre',
        'file': 'el archivo',
        'Files:  {files}\nFolders:  {folders}\nTotal size:  {size} bytes  ({pretty})': 'Archivos:  {files}\nCarpetas:  {folders}\nTamaño total:  {size} bytes  ({pretty})',
        'folder': 'la carpeta',
        'Get size': 'Obtener tamaño',
        'Image not writable': 'La imagen no admite escritura',
        'Increase font size (now {px}px)': 'Aumentar tamaño de letra (ahora {px}px)',
        'More like this': 'Más como esto',
        'New directory name in': 'Nombre del nuevo directorio en',
        'Name this Next': 'Nombrar este Next',
        'Friendly name for {addr} (empty removes it):':
            'Nombre descriptivo para {addr} (vacío lo elimina):',
        'New folder in {path}:': 'Nueva carpeta en {path}:',
        'New Folder…': 'Nueva carpeta…',
        'New name for the {kind}:': 'Nuevo nombre para {kind}:',
        'Not enough space on the Next': 'No hay espacio suficiente en el Next',
        ('Only add a drive that really exists on your Next (an extra SD card reader or '
         'partition). Selecting a drive that is not mounted CRASHES the Next.'):
            ('Añade solo una unidad que exista de verdad en tu Next (un lector SD o '
             'partición adicional). Seleccionar una unidad no montada BLOQUEA el Next.'),
        'Open': 'Abrir',
        'Open in {source}': 'Abrir en {source}',
        'Open: the system could not open {name}.':
            'Abrir: el sistema no pudo abrir {name}.',
        'Open on website (zxart.ee)': 'Abrir en el sitio web (zxart.ee)',
        'Open on website (zxinfo.dk)': 'Abrir en el sitio web (zxinfo.dk)',
        'Paste': 'Pegar',
        'Remote Unzip file': 'Descomprimir archivo remoto',
        'Remote Zip': 'Comprimir remoto',
        'Remove from Favorites': 'Quitar de Favoritos',
        "Rename '{name}' to:": "Renombrar '{name}' a:",
        'Rename failed': 'Error al renombrar',
        'Rename…': 'Renombrar…',
        'Retrieve all issues': 'Obtener todos los números',
        'Send to SD card (image)  →  {dest}': 'Enviar a la tarjeta SD (imagen)  →  {dest}',
        'Send using NextSync  →  {dest}': 'Enviar con NextSync  →  {dest}',
        'Set sync root': 'Establecer raíz de sincronización',
        'Set this folder as the new sync root?': '¿Establecer esta carpeta como nueva raíz de sincronización?',
        'Size on the Next': 'Tamaño en el Next',
        'The image was downloaded but could not be extracted:': 'La imagen se descargó pero no se pudo extraer:',
        "The name cannot contain '/' or '\\'.": "El nombre no puede contener '/' ni '\\'.",
        'This cannot be undone.': 'Esta acción no se puede deshacer.',
        ('This copy needs {need} bytes ({need_h}), but drive {drive}: only has {free} '
         'bytes ({free_h}) free.\n\nIt exceeds the available remote space by {over} '
         'bytes ({over_h}).\n\nThe copy was not started.'):
            ('Esta copia necesita {need} bytes ({need_h}), pero la unidad {drive}: '
             'solo tiene {free} bytes ({free_h}) libres.\n\nSupera el espacio remoto '
             'disponible en {over} bytes ({over_h}).\n\nLa copia no se inició.'),
        'Unzip file': 'Descomprimir archivo',
        'Zip': 'Comprimir',
        '… and {n} more': '… y {n} más',
        # ---- labels ----
        "  Background": "  Fondo",
        "  Directory name": "  Nombre de directorio",
        "  Directory type label": "  Etiqueta de tipo de directorio",
        "  File extension": "  Extensión de archivo",
        "  File name": "  Nombre de archivo",
        "  File size": "  Tamaño de archivo",
        "  Filter: ": "  Filtro: ",
        "  General UI text": "  Texto general de la interfaz",
        "  Retro logs console": "  Consola de registro retro",
        "  Up Directory item": "  Elemento «Subir directorio»",
        "Background image opacity (%):": "Opacidad de la imagen de fondo (%):",
        "Background image:": "Imagen de fondo:",
        "CSpect default launch parameters:": "Parámetros de arranque de CSpect:",
        "Collection:": "Colección:",
        "Desktop Theme:": "Tema de escritorio:",
        "Disk Image Explorer: ": "Explorador de la imagen de disco: ",
        "Gallery animation:": "Animación de la galería:",
        "Gallery image size:": "Tamaño de imagen de la galería:",
        "Gallery items per row:": "Elementos por fila en la galería:",
        "Gallery rows per page (min):": "Filas por página en la galería (mín):",
        "Gallery search sort ordering preference:": "Orden de los resultados de búsqueda de la galería:",
        "Gallery slideshow pause time:": "Pausa del pase de diapositivas:",
        "Language:": "Idioma:",
        "Application language:": "Idioma de la aplicación:",
        "Local file explorers & App Text Colors:": "Exploradores locales y colores del texto:",
        "Local path: ": "Ruta local: ",
        "MAME ROM / system:": "ROM / sistema de MAME:",
        "MAME default launch parameters:": "Parámetros de arranque de MAME:",
        "Max connections:": "Conexiones máx.:",
        "NextSync — Automatically start Remote Explorer server on startup":
            "NextSync — Iniciar automáticamente el servidor del Remote Explorer al arrancar",
        "NextSync — when a sent file or directory exists locally:":
            "NextSync — si un archivo o directorio recibido ya existe localmente:",
        "Page:": "Página:",
        "Port:": "Puerto:",
        "Reset theme": "Restablecer tema",
        "Retro log font size:": "Tamaño de letra del registro retro:",
        "Search:": "Buscar:",
        "Search: ": "Buscar: ",
        "View:": "Vista:",
        "itch.io API key:": "Clave API de itch.io:",
        # ---- buttons ----
        "< Prev": "< Ant.",
        "Cancel": "Cancelar",
        "Cancel sync": "Cancelar sincronización",
        "Connect": "Conectar",
        "Create Directory": "Crear directorio",
        "Create SyncIgnore File": "Crear archivo SyncIgnore",
        "Delete": "Eliminar",
        "Delete SyncIgnore File": "Eliminar archivo SyncIgnore",
        "Delete SyncPoint File": "Eliminar archivo SyncPoint",
        "Disconnect": "Desconectar",
        "RS232 ESP emulation is already running on port {port} for another emulator; this MAME joins it. The new port applies once every MAME has exited.":
            "La emulación RS232 ESP ya está en marcha en el puerto {port} para otro emulador; este MAME se une a ella. El nuevo puerto se aplicará cuando hayan salido todos los MAME.",
        "RS232 ESP emulation: {count} emulators are now sharing it (port {port}).":
            "Emulación RS232 ESP: {count} emuladores la están compartiendo ahora (puerto {port}).",
        "RS232 ESP emulation could not start (port {port} in use?). MAME starts without it.":
            "La emulación RS232 ESP no pudo iniciarse (¿puerto {port} en uso?). MAME se inicia sin ella.",
        "RS232 ESP emulation inspired by jesperl - by Janko Stamenović":
            "Emulación RS232 ESP inspirada en jesperl - por Janko Stamenović",
        "The optional RS232 ESP Emulation for MAME (Settings) is a clean full reimplementation in Python of an idea from jesperl by Janko Stamenović - an ESP-AT emulator bridging MAME's emulated Wi-Fi module to the real network. Many thanks for the inspirational idea - see https://sourceforge.net/projects/jesperl/.":
            "La Emulación RS232 ESP opcional para MAME (Ajustes) es una reimplementación completa y limpia en Python de una idea de jesperl, de Janko Stamenović: un emulador ESP-AT que conecta el módulo Wi-Fi emulado de MAME con la red real. Muchas gracias por la idea inspiradora - ver https://sourceforge.net/projects/jesperl/.",
        "One RS232 ESP emulation serves every running MAME: launch a second MAME on another disk image and it joins the same emulation with its own separate session, so several emulated Nexts can be on the network at once. The emulation stops when the last MAME exits. When two of them ask for the same server port (a Next listening for incoming connections), the second one is moved to the next free port and the log says which port to connect to.":
            "Una sola emulación RS232 ESP sirve a todos los MAME en marcha: lanza un segundo MAME con otra imagen de disco y se unirá a la misma emulación con su propia sesión independiente, de modo que varios Next emulados pueden estar en la red a la vez. La emulación se detiene cuando sale el último MAME. Cuando dos de ellos piden el mismo puerto de servidor (un Next a la escucha de conexiones entrantes), el segundo se traslada al siguiente puerto libre y el registro indica a qué puerto conectarse.",
        "Transfers through the RS232 ESP emulation need the Next side on its SLOW pacing: use '.sync5 -s' for the dot, or set UART speed to Slow in ZX Next Remote's settings.":
            "Las transferencias a través de la emulación RS232 ESP necesitan el lado Next en su ritmo LENTO: usa '.sync5 -s' para el dot, o pon la velocidad UART en Slow en los ajustes de ZX Next Remote.",
        "Start {emulator}": "Iniciar {emulator}",
        "Color:": "Color:",
        "Pick a color for this Next. It tints the machine list and this machine's tab in the session strip.":
            "Elige un color para este Next. Tiñe la lista de máquinas y la pestaña de esta máquina en la tira de sesiones.",
        "Clear the color": "Quitar el color",
        "Switch to this Next": "Cambiar a este Next",
        "Name and color…": "Nombre y color…",
        "That Next is no longer on the line.":
            "Ese Next ya no está en la línea.",
        "Tell this Next to leave listen mode and exit? ZX Next Remote closes its application; a '.sync5' dot returns to BASIC. The server keeps listening, so it can connect again.":
            "¿Pedir a este Next que salga del modo escucha y se cierre? ZX Next Remote cierra su aplicación; un punto '.sync5' vuelve a BASIC. El servidor sigue escuchando, así que puede volver a conectarse.",
        "Asked the Next to leave listen mode and exit.":
            "Se ha pedido al Next que salga del modo escucha y se cierre.",
        "Remote .sync5 update": "Actualización remota de .sync5",
        "Update .sync5 on this Next ({old} → {new})…":
            "Actualizar .sync5 en este Next ({old} → {new})…",
        "Push new .sync5 to this Next…":
            "Enviar el nuevo .sync5 a este Next…",
        ".sync5 version unknown — switch to this Next first":
            "Versión de .sync5 desconocida — cambia primero a este Next",
        ".sync5 v{old} predates self-update — copy the new dot to the Next by hand once":
            ".sync5 v{old} es anterior a la autoactualización — copia el nuevo dot al Next a mano una vez",
        "Locating the .sync5 build to send…":
            "Localizando el .sync5 a enviar…",
        "Still locating the .sync5 build to send — one moment.":
            "Todavía localizando el .sync5 a enviar — un momento.",
        "Could not obtain the .sync5 build to send: {reason}":
            "No se pudo obtener el .sync5 a enviar: {reason}",
        ("Update .sync5 on {machine}: v{old} → v{new}.\n\nThe new dot is "
         "staged on the Next's SD card, read back and verified, then "
         "swapped in; the previous dot is kept as sync5.bak (renaming it "
         "back to sync5 is the one-step recovery). The session ends when "
         "the update completes — run {command} on the Next again "
         "afterwards.\n\nTarget directory on the Next:"):
            ("Actualizar .sync5 en {machine}: v{old} → v{new}.\n\nEl nuevo "
             "dot se prepara en la tarjeta SD del Next, se relee y "
             "verifica, y después se sustituye; el dot anterior se "
             "conserva como sync5.bak (renombrarlo de nuevo a sync5 es la "
             "recuperación en un paso). La sesión termina cuando la "
             "actualización se completa — ejecuta {command} de nuevo en "
             "el Next después.\n\nDirectorio de destino en el Next:"),
        ("Push the new .sync5 (v{new}) to {machine}?\n\nThis machine's "
         "version is unknown (an older dot, or an old ZX Next Remote "
         "build — the two cannot be told apart), and the swap itself "
         "only works when the far side is a .sync dot v5.9 or newer: on "
         "anything older the staged sync5.new is left on the card and "
         "nothing is swapped. The previous dot is kept as sync5.bak "
         "(renaming it back to sync5 is the one-step recovery). The "
         "session ends when the update completes — run {command} on the "
         "Next again afterwards.\n\nTarget directory on the Next:"):
            ("¿Enviar el nuevo .sync5 (v{new}) a {machine}?\n\nLa versión "
             "de esta máquina es desconocida (un dot antiguo, o una "
             "versión antigua de ZX Next Remote — no se pueden "
             "distinguir), y la propia sustitución solo funciona cuando "
             "el otro lado es un dot .sync v5.9 o más reciente: con algo "
             "más antiguo el sync5.new preparado se queda en la tarjeta y "
             "no se sustituye nada. El dot anterior se conserva como "
             "sync5.bak (renombrarlo de nuevo a sync5 es la recuperación "
             "en un paso). La sesión termina cuando la actualización se "
             "completa — ejecuta {command} de nuevo en el Next "
             "después.\n\nDirectorio de destino en el Next:"),
        "Download File": "Descargar archivo",
        "Download NextZXOS Image": "Descargar imagen NextZXOS",
        "Download and install HDF Monkey": "Descargar e instalar HDF Monkey",
        "Download and install HDF Monkey and OpenAL": "Descargar e instalar HDF Monkey y OpenAL",
        "Generate": "Generar",
        "Get API key…": "Obtener clave API…",
        "Latest": "Novedades",
        "New Folder": "Nueva carpeta",
        "Next >": "Sig. >",
        "OK": "Aceptar",
        "Open config file": "Abrir archivo de configuración",
        "Prepare Classic NextSync server": "Preparar servidor NextSync clásico",
        "Random": "Aleatorio",
        "Refresh": "Actualizar",
        "Rename": "Renombrar",
        "Search": "Buscar",
        "Select NextZXOS disk Image": "Seleccionar imagen de disco NextZXOS",
        "Set current folder as new sync root folder":
            "Usar la carpeta actual como nueva raíz de sincronización",
        "Up": "Subir",
        "▶ Start Classic NextSync server": "▶ Iniciar servidor NextSync clásico",
        "▶ Start Remote Explorer NextSync server":
            "▶ Iniciar servidor NextSync del Explorador remoto",
        "⬇  Download": "⬇  Descargar",
        "⬇  Install MAME": "⬇  Instalar MAME",
        "🎮 Retro": "🎮 Retro",
        "💾  Send to SD card": "💾  Enviar a la tarjeta SD",
        "🔁  Send via NextSync": "🔁  Enviar por NextSync",
        "🕹  Launch CSpect": "🕹  Iniciar CSpect",
        "🕹  Launch Mame": "🕹  Iniciar Mame",
        "🖼 Switch to 'Classic' view mode": "🖼 Cambiar a vista 'Clásica'",
        # ---- emulator option combos (SD Card tab; selected by index) ----
        "Screen Size X1": "Tamaño de pantalla X1",
        "Screen Size X2": "Tamaño de pantalla X2",
        "Screen Size X3": "Tamaño de pantalla X3",
        "Screen Size X4": "Tamaño de pantalla X4",
        "Fullscreen": "Pantalla completa",
        "Sound On": "Sonido activado",
        "Sound Off": "Sonido desactivado",
        "Sound WASAPI": "Sonido WASAPI",
        "Sound XAudio2": "Sonido XAudio2",
        "Sound PortAudio": "Sonido PortAudio",
        "VSync On": "VSync activado",
        "VSync Off": "VSync desactivado",
        "Joystick On": "Joystick activado",
        "Joystick Off": "Joystick desactivado",
        "Mouse On": "Ratón activado",
        "Mouse Off": "Ratón desactivado",
        "Disable ESC Key Off": "Desactivar tecla ESC: no",
        "Disable ESC Key On": "Desactivar tecla ESC: sí",
        "ZX Next Unite update check: could not parse the versions (latest tag {tag}); skipping.":
            "Comprobación de ZX Next Unite: no se pudieron interpretar las versiones (última etiqueta {tag}); se omite.",
        "ZX Next Unite {latest} is available, but the release has no package for this platform — opening the releases page instead.":
            "ZX Next Unite {latest} está disponible, pero la versión no incluye un paquete para esta plataforma; se abrirá la página de versiones.",
        # ---- long guidance prompts (final) ----
        "A newer version of CSpect is available on itch.io.\n\nInstalled: {installed}\nLatest: {latest}\n\nDownload and install the newest version now?":
            "Hay una versión más reciente de CSpect en itch.io.\n\nInstalada: {installed}\nÚltima: {latest}\n\n¿Descargar e instalar ahora la más reciente?",
        "CSpect update ▸ SUCCESS — {name} extracted to: {path}":
            "Actualización de CSpect ▸ CORRECTA — {name} extraído en: {path}",
        "CSpect update ▸ Starting download + install of {name} ({file}) from itch.io into {folder}.":
            "Actualización de CSpect ▸ iniciando descarga e instalación de {name} ({file}) desde itch.io en {folder}.",
        "ERROR: could not build {name}: {error}":
            "ERROR: no se pudo crear {name}: {error}",
        "MAME can't start: the ZX Spectrum Next boot ROM (TBBLUE) is missing. This step is manual — see {url} and follow \"Get TBBLUE (the Next 'boot ROM')\". Put the file tbblue.zip into MAME's roms folder (downloads\\mame\\roms) — DON'T extract it — and try again. You must provide a legally acquired, licensed ROM.":
            "MAME no puede arrancar: falta la ROM de arranque del ZX Spectrum Next (TBBLUE). Este paso es manual — consulta {url} y sigue \"Get TBBLUE (the Next 'boot ROM')\". Pon el archivo tbblue.zip en la carpeta roms de MAME (downloads\\mame\\roms) — NO lo extraigas — e inténtalo de nuevo. Debes usar una ROM adquirida legalmente y con licencia.",
        "MAME install ▸ NEXT STEP (manual): add the TBBLUE boot ROM. See {url} → \"Get TBBLUE (the Next 'boot ROM')\". Put the file tbblue.zip into MAME's roms folder ({roms}) — DON'T extract it. You must provide a legally acquired, licensed ROM.":
            "Instalación de MAME ▸ SIGUIENTE PASO (manual): añade la ROM de arranque TBBLUE. Consulta {url} → \"Get TBBLUE (the Next 'boot ROM')\". Pon el archivo tbblue.zip en la carpeta roms de MAME ({roms}) — NO lo extraigas. Debes usar una ROM adquirida legalmente y con licencia.",
        "NextSync .sync5 dot command updated: v{old} -> v{new} — please copy the new build to your Next (it cannot be deployed automatically).":
            "Comando dot .sync5 de NextSync actualizado: v{old} -> v{new} — copia la nueva versión a tu Next (no puede desplegarse automáticamente).",
        "TIP: Did you know that if you have purchased CSpect from itch.io you can do a full end-to-end CSpect install from there?\n\nCSpect ships with hdfmonkey bundled inside it, so that route needs no separate hdfmonkey install — the app finds and uses the bundled copy automatically.\n\nSimply log into your itch.io account in the itch.io tab, navigate to CSpect and click Install.\n\nDo you still want to install hdfmonkey only, or abort and then make an end-to-end install of CSpect using itch.io?":
            "CONSEJO: ¿sabías que si has comprado CSpect en itch.io puedes hacer una instalación completa de CSpect desde allí?\n\nCSpect lleva hdfmonkey incluido en su interior, así que esa vía no necesita una instalación aparte de hdfmonkey — la aplicación encuentra y usa la copia incluida automáticamente.\n\nInicia sesión en tu cuenta de itch.io en la pestaña itch.io, ve a CSpect y pulsa Instalar.\n\n¿Aún quieres instalar solo hdfmonkey, o prefieres cancelar y hacer la instalación completa de CSpect con itch.io?",
        "The automatic hdfmonkey download from specnext.com failed — the forum may be asking for a login or an anti-robot confirmation before the download can start (see the log for details).\n\nYou can install it manually instead:\n1. Click 'Open download page' below (or browse to\n    {url} ).\n2. Download the hdfmonkey .zip file.\n3. Drop the downloaded .zip into this EXACT folder — the app has already created it, and the 'Open downloads folder' button below opens it so nothing needs to be typed:\n    {folder}\n4. Click \"I've dropped the zip - try again\".":
            "La descarga automática de hdfmonkey desde specnext.com falló — puede que el foro pida iniciar sesión o una confirmación anti-robot antes de permitir la descarga (mira el registro para más detalles).\n\nPuedes instalarlo manualmente:\n1. Pulsa 'Abrir la página de descarga' abajo (o abre\n    {url} ).\n2. Descarga el archivo .zip de hdfmonkey.\n3. Copia el .zip descargado EXACTAMENTE en esta carpeta — la aplicación ya la ha creado, y el botón 'Abrir la carpeta de descargas' de abajo la abre para que no tengas que escribir nada:\n    {folder}\n4. Pulsa \"Ya he puesto el zip: inténtalo de nuevo\".",
        "ZX Next Unite update: downloaded {name} to {folder}":
            "Actualización de ZX Next Unite: {name} descargado en {folder}",
        "ZX Next Unite update: downloaded {path} but could not unpack it: {error}":
            "Actualización de ZX Next Unite: se descargó {path} pero no se pudo descomprimir: {error}",
        "ZX Next Unite {latest} is available (you are running {installed}).\n\nYou appear to be running from source (git clone), so the\nrecommended way to update is:\n\n    git pull\n\ninstead of downloading the Windows binary.":
            "ZX Next Unite {latest} está disponible (estás usando {installed}).\n\nParece que lo ejecutas desde el código fuente (git clone), así que la\nforma recomendada de actualizar es:\n\n    git pull\n\nen lugar de descargar el binario de Windows.",
        "ZX Next Unite {latest} is available — download?\n\nInstalled: {installed}\nLatest: {latest}\nPackage: {asset} (~{size})\n\nThe new version is saved next to the current one — you choose\nwhen to switch (you'll be offered a restart after the download).":
            "ZX Next Unite {latest} está disponible, ¿descargar?\n\nInstalada: {installed}\nÚltima: {latest}\nPaquete: {asset} (~{size})\n\nLa nueva versión se guarda junto a la actual — tú eliges\ncuándo cambiar (se te ofrecerá reiniciar tras la descarga).",
        "ZX Next Unite {latest} is available — running from source, so update with 'git pull' instead of the Windows binary.":
            "ZX Next Unite {latest} está disponible; como se ejecuta desde el código fuente, actualiza con 'git pull' en lugar del binario de Windows.",
        # ---- emulator update prompts (bodies + buttons) ----
        "A newer version of MAME is available.\n\nInstalled: 0.{installed}\nLatest: {latest}  (0.{latest_num})\nPackage: {asset}\n\nDownload (~{size}) and update your MAME install now?\nThe existing files in the downloads MAME folder will be overwritten.":
            "Hay una versión más reciente de MAME.\n\nInstalada: 0.{installed}\nÚltima: {latest}  (0.{latest_num})\nPaquete: {asset}\n\n¿Descargar (~{size}) y actualizar tu instalación de MAME ahora?\nLos archivos existentes en la carpeta MAME se sobrescribirán.",
        "Close and start {name}":
            "Cerrar e iniciar {name}",
        "Continue hdfmonkey standalone install":
            "Continuar con la instalación independiente de hdfmonkey",
        "I've dropped the zip - try again":
            "Ya he puesto el zip: inténtalo de nuevo",
        "MAME release: {tag}\nPackage: {asset} ({arch})\n\nDownload (~{size}) and install it into the downloads folder?\nNote: the fully extracted install is large (~500 MB).":
            "Versión de MAME: {tag}\nPaquete: {asset} ({arch})\n\n¿Descargar (~{size}) e instalarlo en la carpeta de descargas?\nNota: la instalación completa ocupa bastante (~500 MB).",
        "Open download page":
            "Abrir la página de descarga",
        "Open downloads folder":
            "Abrir la carpeta de descargas",
        "The new version was saved as:\n\n{path}\n\nClose ZX Next Unite now and start the new version ({name})?\nYour settings (hdfg.cfg) and downloads are picked up as-is —\nboth versions run from the same folder.":
            "La nueva versión se guardó como:\n\n{path}\n\n¿Cerrar ZX Next Unite ahora e iniciar la nueva versión ({name})?\nTus ajustes (hdfg.cfg) y descargas se reutilizan tal cual —\nambas versiones se ejecutan desde la misma carpeta.",
        "What's changed:":
            "Novedades:",
        # ---- emulator / config console (final batch) ----
        "CSpect update check: {reason}.":
            "Comprobación de CSpect: {reason}.",
        "CSpect update ▸ FAILED — {error}":
            "Actualización de CSpect ▸ FALLÓ — {error}",
        "CSpect update ▸ newer build available: installed {installed}, latest {latest}.":
            "Actualización de CSpect ▸ hay una compilación más reciente: instalada {installed}, última {latest}.",
        "CSpect update ▸ user chose to update to {name}.":
            "Actualización de CSpect ▸ el usuario eligió actualizar a {name}.",
        "Could not list the MAME releases: {error}":
            "No se pudieron listar las versiones de MAME: {error}",
        "ERROR: Failed to launch MAME: {error}":
            "ERROR: no se pudo iniciar MAME: {error}",
        "ERROR: Failed to launch CSpect: {error}":
            "ERROR: No se pudo iniciar CSpect: {error}",
        "ERROR: could not extract {name}: {error}":
            "ERROR: no se pudo extraer {name}: {error}",
        "ERROR: hdfmonkey failed - A file can't be opened this is commonly caused by strange characters such as quotes and signs":
            "ERROR: hdfmonkey falló - no se puede abrir un archivo; esto suele deberse a caracteres extraños como comillas y signos",
        "ERROR: hdfmonkey failed - A file can't be opened: {command} this is commonly caused by strange characters such as quotes and signs":
            "ERROR: hdfmonkey falló - no se puede abrir un archivo: {command}; esto suele deberse a caracteres extraños como comillas y signos",
        "Failed to save configuration file with IOError: {error}":
            "Error al guardar el archivo de configuración (IOError): {error}",
        "Found hdfmonkey alongside CSpect: {path}":
            "hdfmonkey encontrado junto a CSpect: {path}",
        "MAME exited with code {code}.":
            "MAME finalizó con el código {code}.",
        "MAME install ▸ SUCCESS — MAME detected at: {path}":
            "Instalación de MAME ▸ CORRECTA — MAME detectado en: {path}",
        "Pygame mode unavailable — run: pip install pygame-ce":
            "Modo pygame no disponible — ejecuta: pip install pygame-ce",
        "Remote unzip: fetching {path} from the image …":
            "Descompresión remota: obteniendo {path} de la imagen …",
        "Remote zip: fetching {count} item(s) from the image …":
            "Compresión remota: obteniendo {count} elemento(s) de la imagen …",
        "Saved configuration file.":
            "Archivo de configuración guardado.",
        "UI language set to '{lang}' to match the system language — change it on the Settings tab.":
            "Idioma de la interfaz ajustado a '{lang}' para coincidir con el del sistema; puedes cambiarlo en la pestaña Ajustes.",
        "ZX Next Unite update available: {latest} (installed {installed}).":
            "Actualización de ZX Next Unite disponible: {latest} (instalada {installed}).",
        "ZX Next Unite update ▸ downloading {asset}…":
            "Actualización de ZX Next Unite ▸ descargando {asset}…",
        "ZX Next Unite update: could not start {name}: {error}":
            "Actualización de ZX Next Unite: no se pudo iniciar {name}: {error}",
        "ZX Next Unite update: download FAILED: {error}":
            "Actualización de ZX Next Unite: la descarga FALLÓ: {error}",
        "ZX Next Unite update: downloaded — start it any time: {path}":
            "Actualización de ZX Next Unite: descargada; puedes iniciarla cuando quieras: {path}",
        "ZX Next Unite update: starting {name} and closing…":
            "Actualización de ZX Next Unite: iniciando {name} y cerrando…",
        "ZX Next Unite update: unpacked to {path}":
            "Actualización de ZX Next Unite: descomprimida en {path}",
        "Extracting {name} from the image, then starting CSpect…":
            "Extrayendo {name} de la imagen y luego iniciando CSpect…",
        "Start CSpect: {name} could not be read from the image, CSpect was not started.":
            "Iniciar CSpect: no se pudo leer {name} de la imagen; CSpect no se ha iniciado.",
        "Extracting {name} from the image, then sending it via NextSync…":
            "Extrayendo {name} de la imagen y luego enviándolo por NextSync…",
        "Send via NextSync: {name} could not be read from the image, nothing was sent.":
            "Enviar por NextSync: no se pudo leer {name} de la imagen; no se ha enviado nada.",
        "Send via NextSync {name}":
            "Enviar {name} por NextSync",
        "Start NextSync Remote Explorer":
            "Iniciar el Remote Explorer de NextSync",
        "Stop NextSync Remote Explorer":
            "Detener el Remote Explorer de NextSync",
        "Please select a sync root first on the NextSync Remote Explorer tab and retry.":
            "Selecciona primero una carpeta raíz de sincronización en la pestaña Remote Explorer de NextSync y vuelve a intentarlo.",
        # ---- CSpect auto-start actions (SD Card tab menus) ----
        "Send to SD Card and start CSpect with file {name}":
            "Enviar a la tarjeta SD e iniciar CSpect con el archivo {name}",
        "Send to SD Card and start CSpect: the transfer failed, CSpect was not started.":
            "Enviar a la tarjeta SD e iniciar CSpect: la transferencia falló; CSpect no se ha iniciado.",
        "Sending {name} to the SD card image, then starting CSpect…":
            "Enviando {name} a la imagen de la tarjeta SD y luego iniciando CSpect…",
        "Start CSpect with file {name}":
            "Iniciar CSpect con el archivo {name}",
        "Start MAME with file {name}":
            "Iniciar MAME con el archivo {name}",
        "Could not start {emulator}":
            "No se pudo iniciar {emulator}",
        "Could not prepare a folder for {name}: {error}":
            "No se pudo preparar una carpeta para {name}: {error}",
        "Start {emulator}: {name} could not be downloaded from the Next, "
        "{emulator} was not started.":
            "Iniciar {emulator}: no se pudo descargar {name} del Next; "
            "{emulator} no se ha iniciado.",
        "Downloading {name} from the Next, then starting {emulator}…":
            "Descargando {name} del Next y luego iniciando {emulator}…",
        "Downloading {name}…":
            "Descargando {name}…",
        "Start MAME: could not prepare the staging folder {path} ({error}).":
            "Iniciar MAME: no se pudo preparar la carpeta temporal {path} ({error}).",
        "Send to SD Card and start MAME with file {name}":
            "Enviar a la tarjeta SD e iniciar MAME con el archivo {name}",
        "Extracting {name} from the image, then starting MAME…":
            "Extrayendo {name} de la imagen y luego iniciando MAME…",
        "Start MAME: {name} could not be read from the image, MAME was not started.":
            "Iniciar MAME: no se pudo leer {name} de la imagen; MAME no se ha iniciado.",
        "Send to SD Card and start MAME: the transfer failed, MAME was not started.":
            "Enviar a la tarjeta SD e iniciar MAME: la transferencia falló; MAME no se ha iniciado.",
        "Sending {name} to the SD card image, then starting MAME…":
            "Enviando {name} a la imagen de la tarjeta SD y luego iniciando MAME…",
        "MAME cannot load {name} directly; starting MAME without it.":
            "MAME no puede cargar {name} directamente; se iniciará MAME sin ese archivo.",
        # ---- dialogs (message boxes) ----
        "CSpect update available":
            "Actualización de CSpect disponible",
        "Choose another release…":
            "Elegir otra versión…",
        "Close":
            "Cerrar",
        "Download and install":
            "Descargar e instalar",
        "File or directory already exists locally.":
            "El archivo o directorio ya existe localmente.",
        "File or directory exists":
            "El archivo o directorio ya existe",
        "Ignore (always in this sync)":
            "Ignorar (siempre en esta sincronización)",
        "Ignore (one time)":
            "Ignorar (una vez)",
        "Install from .zip…":
            "Instalar desde .zip…",
        "Install hdfmonkey":
            "Instalar hdfmonkey",
        "Later":
            "Más tarde",
        "MAME update available":
            "Actualización de MAME disponible",
        "Open itch.io page":
            "Abrir la página de itch.io",
        "Open the releases page":
            "Abrir la página de versiones",
        "Overwrite local file (always in this sync)":
            "Sobrescribir el archivo local (siempre en esta sincronización)",
        "Overwrite local file (one time)":
            "Sobrescribir el archivo local (una vez)",
        "The automated download failed.":
            "La descarga automática falló.",
        "This is going to completely delete the files in {path} and its sub folders, so they will be unrecoverable.\n\nAre you sure want to continue?":
            "Esto eliminará por completo los archivos de {path} y sus subcarpetas, de forma irrecuperable.\n\n¿Seguro que quieres continuar?",
        "Tip: set a default for this in Settings → \"NextSync — when a sent file or directory exists locally\".":
            "Consejo: define un valor predeterminado en Ajustes → \"NextSync — when a sent file or directory exists locally\".",
        "Uninstall":
            "Desinstalar",
        "Update":
            "Actualizar",
        "Update downloaded":
            "Actualización descargada",
        "Yes":
            "Sí",
        "You can download it manually from the itch.io page in your browser, then install it from the downloaded .zip.":
            "Puedes descargarlo manualmente desde la página de itch.io en tu navegador y luego instalarlo desde el .zip descargado.",
        "ZX Next Unite update available":
            "Actualización de ZX Next Unite disponible",
        "hdfmonkey download failed":
            "Falló la descarga de hdfmonkey",
        "itch.io download":
            "Descarga de itch.io",
        # ---- emulator install / update console ----
        "CSpect update check skipped: {reason}":
            "Comprobación de CSpect omitida: {reason}",
        "CSpect update ▸ user cancelled the update.":
            "Actualización de CSpect ▸ el usuario canceló la actualización.",
        "ERROR: CSpect.exe is not present in the same local directory as zx-next-unite. Please install it from http://cspect.org":
            "ERROR: CSpect.exe no está en el mismo directorio local que zx-next-unite. Instálalo desde http://cspect.org",
        "ERROR: MAME executable not found on PATH. Cannot launch MAME.":
            "ERROR: no se encontró el ejecutable de MAME en el PATH. No se puede iniciar MAME.",
        "Listing the available MAME releases…":
            "Listando las versiones de MAME disponibles…",
        "MAME install ▸ FAILED — the download and extraction finished, but no mame.exe could be found in downloads/mame.":
            "Instalación de MAME ▸ FALLÓ — la descarga y extracción terminaron, pero no se encontró mame.exe en downloads/mame.",
        "MAME install ▸ FAILED — {error}. You can download it manually from https://www.mamedev.org/release.html":
            "Instalación de MAME ▸ FALLÓ — {error}. Puedes descargarlo manualmente desde https://www.mamedev.org/release.html",
        "MAME install ▸ Starting: {tag} ({asset}, ~{size}).":
            "Instalación de MAME ▸ iniciando: {tag} ({asset}, ~{size}).",
        "MAME install ▸ release picker cancelled.":
            "Instalación de MAME ▸ selección de versión cancelada.",
        "MAME is ready to launch now — no restart needed. Use the '🕹  Launch Mame' button.":
            "MAME ya se puede iniciar, sin reiniciar. Usa el botón '🕹  Launch Mame'.",
        "MAME update check: could not determine the installed MAME version; skipping.":
            "Comprobación de MAME: no se pudo determinar la versión instalada; se omite.",
        "MAME update check: could not determine the latest release; skipping.":
            "Comprobación de MAME: no se pudo determinar la última versión; se omite.",
        "MAME update check: could not reach the release site; skipping.":
            "Comprobación de MAME: no se pudo acceder al sitio de versiones; se omite.",
        "MAME update ▸ user chose to pick a release manually.":
            "Actualización de MAME ▸ el usuario eligió seleccionar una versión manualmente.",
        "MAME update ▸ user chose to update to {tag}.":
            "Actualización de MAME ▸ el usuario eligió actualizar a {tag}.",
        "On MacOS and Linux mono is required as it runs under it. Please make sure mono is installed.":
            "En MacOS y Linux se necesita mono, ya que se ejecuta sobre él. Asegúrate de que mono está instalado.",
        "Running as a Flatpak: mono must be installed on the HOST system — the launch is delegated there via flatpak-spawn.":
            "Ejecutándose como Flatpak: mono debe estar instalado en el sistema ANFITRIÓN; el lanzamiento se delega allí mediante flatpak-spawn.",
        "Select a valid ZX Spectrum Next disk image (.img/.hdf) before launching MAME.":
            "Selecciona una imagen de disco ZX Spectrum Next válida (.img/.hdf) antes de iniciar MAME.",
        "ZX Next Unite update check: could not reach GitHub (offline, or no release published yet); skipping.":
            "Comprobación de ZX Next Unite: no se pudo acceder a GitHub (sin conexión o sin versiones publicadas); se omite.",
        "ZX Next Unite update check: running as a Flatpak — updates come from your software center, skipping.":
            "Comprobación de ZX Next Unite: ejecutándose como Flatpak; las actualizaciones vienen de tu centro de software, se omite.",
        "ZX Next Unite update ▸ skipped by user.":
            "Actualización de ZX Next Unite ▸ omitida por el usuario.",
        "ZX Next Unite update: download cancelled.":
            "Actualización de ZX Next Unite: descarga cancelada.",
        # ---- SD Card tab: console + dialogs ----
        "Confirm Deletion":
            "Confirmar eliminación",
        "Create":
            "Crear",
        "Create New Folder":
            "Crear carpeta nueva",
        "Created {name} in {folder} on the image ({count} file(s), {bytes} bytes).":
            "Creado {name} en {folder} en la imagen ({count} archivo(s), {bytes} bytes).",
        "Delete files from the image to free space, or switch to a larger image.\nLarger SD card images can be downloaded from:":
            "Elimina archivos de la imagen para liberar espacio, o cambia a una imagen más grande.\nPuedes descargar imágenes de tarjeta SD más grandes desde:",
        "Download":
            "Descargar",
        "Download failed: no valid destination folder.":
            "Error de descarga: no hay una carpeta de destino válida.",
        "Downloading {name} from {url}":
            "Descargando {name} desde {url}",
        "ERROR: hdfmonkey could not be found. Use the 'Download and install HDF Monkey' button (bottom right of the SD Card tab) to install it automatically, or do a full CSpect install from the itch.io tab, which also bundles hdfmonkey. It can also be installed manually from https://github.com/gasman/hdfmonkey — restart the app once installed.":
            "ERROR: no se encontró hdfmonkey. Usa el botón 'Descargar e instalar HDF Monkey' (abajo a la derecha de la pestaña SD Card) para instalarlo automáticamente, o haz una instalación completa de CSpect desde la pestaña itch.io, que también incluye hdfmonkey. También se puede instalar manualmente desde https://github.com/gasman/hdfmonkey — reinicia la aplicación una vez instalado.",
        "Extracted disk image: {path}":
            "Imagen de disco extraída: {path}",
        "Extracted {count} file(s) from {name} into {folder} on the image.":
            "Extraído(s) {count} archivo(s) de {name} en {folder} en la imagen.",
        "Extracting image... %p%":
            "Extrayendo imagen... %p%",
        "Failed downloading NextZXOS image: {error}":
            "Error al descargar la imagen NextZXOS: {error}",
        "Load Failed":
            "Error de carga",
        "The image was extracted but could not be loaded:":
            "La imagen se extrajo pero no se pudo cargar:",
        "Failed executing hdfmonkey, please make sure it is installed in the same local directory as zx-next-unite.":
            "Error al ejecutar hdfmonkey; asegúrate de que está instalado en el mismo directorio local que zx-next-unite.",
        "Failed extracting NextZXOS image: {error}":
            "Error al extraer la imagen NextZXOS: {error}",
        "Failed loading image: {path}.":
            "Error al cargar la imagen: {path}.",
        "No SD-card disk image selected — pick or create a .img/.hdf at the top of this tab to unlock the emulator Launch buttons.":
            "No hay ninguna imagen de tarjeta SD seleccionada: elige o crea un .img/.hdf en la parte superior de esta pestaña para desbloquear los botones de inicio del emulador.",
        "Nothing to move: items are already in this folder.":
            "Nada que mover: los elementos ya están en esta carpeta.",
        "Only {free} MB free out of {total} MB ({used} % used, {pct} % free).":
            "Solo {free} MB libres de {total} MB ({used} % usado, {pct} % libre).",
        "Please load an image file first !":
            "¡Carga primero un archivo de imagen!",
        "Please load an image first!":
            "¡Carga primero una imagen!",
        "Please select an image file or folder first to delete!":
            "¡Selecciona primero un archivo o carpeta de la imagen para eliminar!",
        "Please select an image file or folder first to rename!":
            "¡Selecciona primero un archivo o carpeta de la imagen para renombrar!",
        "Remote unzip cancelled — the image is unchanged.":
            "Descompresión remota cancelada: la imagen no se ha modificado.",
        "Remote unzip: download from the image failed or was cancelled — the image is unchanged.":
            "Descompresión remota: la descarga desde la imagen falló o se canceló; la imagen no se ha modificado.",
        "Remote unzip: upload into the image failed or was cancelled.":
            "Descompresión remota: la subida a la imagen falló o se canceló.",
        "Remote zip cancelled — no zip was created.":
            "Compresión remota cancelada: no se creó ningún zip.",
        "Remote zip: download from the image failed or was cancelled — no zip was created.":
            "Compresión remota: la descarga desde la imagen falló o se canceló; no se creó ningún zip.",
        "Remote zip: upload into the image failed or was cancelled.":
            "Compresión remota: la subida a la imagen falló o se canceló.",
        "SD Image Nearly Full":
            "Imagen SD casi llena",
        "The SD card image is nearly full.":
            "La imagen de la tarjeta SD está casi llena.",
        "The hdfmonkey provided by the CSpect itch.io package is not executable. Make it executable by running:":
            "El hdfmonkey incluido en el paquete de CSpect de itch.io no es ejecutable. Hazlo ejecutable ejecutando:",
        "The image is completely full ({total} MB capacity, 0 MB free).":
            "La imagen está completamente llena (capacidad {total} MB, 0 MB libres).",
        # ---- SD Card console: banner, detection, update checks ----
        "CSpect - by Mike Dailly http://cspect.org":
            "CSpect - por Mike Dailly http://cspect.org",
        "CSpect is up to date (installed {installed}, latest {latest}).":
            "CSpect está actualizado (instalada {installed}, última {latest}).",
        "Checking for a newer MAME release…":
            "Buscando una versión más reciente de MAME…",
        "Checking for a newer ZX Next Unite release on GitHub…":
            "Buscando en GitHub una versión más reciente de ZX Next Unite…",
        "Checking itch.io for a newer CSpect release…":
            "Buscando en itch.io una versión más reciente de CSpect…",
        "HDF Monkey - by Matt Westcott":
            "HDF Monkey - por Matt Westcott",
        "Inspired by HDFM-GOOEY - by em00k":
            "Inspirado en HDFM-GOOEY - por em00k",
        "Loaded configuration file.":
            "Archivo de configuración cargado.",
        "MAME - ZX Spectrum Next support by Holub https://wiki.specnext.dev/MAME:Installing":
            "MAME - compatibilidad con ZX Spectrum Next por Holub https://wiki.specnext.dev/MAME:Installing",
        "MAME is up-to-date (installed 0.{installed}, latest 0.{latest}).":
            "MAME está actualizado (instalada 0.{installed}, última 0.{latest}).",
        "MAME is up-to-date with a patched version (installed 0.{installed}, latest 0.{latest}).":
            "MAME está actualizado con una versión parcheada (instalada 0.{installed}, última 0.{latest}).",
        "MAME version: {version}":
            "Versión de MAME: {version}",
        "NextSync - by Jari Komppa and Julien Clauzel":
            "NextSync - por Jari Komppa y Julien Clauzel",
        "Using CSpect under downloads/cspect: {path}":
            "Usando CSpect en downloads/cspect: {path}",
        "Using MAME under: {path}":
            "Usando MAME en: {path}",
        "Using hdfmonkey bundled with CSpect: {path}":
            "Usando el hdfmonkey incluido con CSpect: {path}",
        "Welcome to ZX Next Unite {version}":
            "Bienvenido a ZX Next Unite {version}",
        "ZX Next Unite is up to date (installed {installed}, latest {latest}).":
            "ZX Next Unite está actualizado (instalada {installed}, última {latest}).",
        "zx-next-unite - by Julien Clauzel 2024":
            "zx-next-unite - por Julien Clauzel 2024",
        "No image loaded": "No hay imagen cargada",
        # ---- itch.io item viewer + web-link labels ----
        "About": "Acerca de",
        "Open on {site}": "Abrir en {site}",
        "Open {url}": "Abrir {url}",
        "✓  Re-install": "✓  Reinstalar",
        "⬇  Install": "⬇  Instalar",
        "⬇  Installing…": "⬇  Instalando…",
        "📂  Open download folder": "📂  Abrir carpeta de descargas",
        # ---- gallery item viewer (Classic + Retro) ----
        "🌐  Open on website": "🌐  Abrir en el sitio web",
        "🌐  Open on {site}": "🌐  Abrir en {site}",
        "📂  Open install folder": "📂  Abrir carpeta de instalación",
        "🗑  Uninstall": "🗑  Desinstalar",
        # ---- NextSync log lines (user-facing; protocol diagnostics stay English) ----
        "(-send saves received files under: {folder})":
            "(-send guarda los archivos recibidos en: {folder})",
        "Aliases:":
            "Alias:",
        "Cancel requested — stopping after current file":
            "Cancelación solicitada: se detendrá tras el archivo actual",
        "Cannot create {path}: {error}":
            "No se puede crear {path}: {error}",
        "Closing connection":
            "Cerrando la conexión",
        "Connected by {address} port {port}":
            "Conectado desde {address} puerto {port}",
        "Disconnected":
            "Desconectado",
        "Existing-file policy: {policy} (change in Settings -> 'NextSync - when a sent file or directory exists locally').":
            "Política para archivos existentes: {policy} (se cambia en Ajustes -> 'NextSync - when a sent file or directory exists locally').",
        "Failed to rename {path}: {error}":
            "No se pudo renombrar {path}: {error}",
        "IP addresses:":
            "Direcciones IP:",
        "Import failed: no valid destination folder.":
            "Error de importación: no hay una carpeta de destino válida.",
        "Navigate to a folder in the left local file explorer, press 'Set current folder as new sync root folder' to choose a sync root and then press the 'Start Classic NextSync server' button.":
            "Navega a una carpeta en el explorador local izquierdo, pulsa 'Set current folder as new sync root folder' para elegir la raíz de sincronización y luego pulsa el botón 'Start Classic NextSync server'.",
        "NextSync HTTP bridge NOT started: {error}":
            "El puente HTTP de NextSync NO se ha iniciado: {error}",
        "NextSync HTTP bridge listening on port {port}":
            "Puente HTTP de NextSync escuchando en el puerto {port}",
        "NextSync HTTP bridge stopped.":
            "Puente HTTP de NextSync detenido.",
        "NextSync HTTP bridge: bearer-token protection is ON (requests must carry the {header} header; others get HTTP 401)":
            "Puente HTTP de NextSync: la protección por token está ACTIVADA (las peticiones deben incluir la cabecera {header}; el resto recibe HTTP 401)",
        "NextSync is already running — please wait for it to finish.":
            "NextSync ya se está ejecutando: espera a que termine.",
        "NextSync listening to port {port}":
            "NextSync escuchando en el puerto {port}",
        "NextSync server, protocol version: {version}":
            "Servidor NextSync, versión del protocolo: {version}",
        "No network detected - connect to Wi-Fi/Ethernet to see the address your Next should sync to.":
            "No se detectó red: conéctate a Wi-Fi/Ethernet para ver la dirección con la que debe sincronizar tu Next.",
        "Note":
            "Nota",
        "Nothing (more) to sync":
            "No hay (más) nada que sincronizar",
        "Now run one of these commands on your Next:":
            "Ahora ejecuta uno de estos comandos en tu Next:",
        "Primary IP:":
            "IP principal:",
        "Received {name} ({bytes} bytes)":
            "Recibido {name} ({bytes} bytes)",
        "Receiving files from the Next...":
            "Recibiendo archivos desde el Next...",
        "Receiving: {name} -> {path}":
            "Recibiendo: {name} -> {path}",
        "Remote .sync5 update failed while reading {path}: {error} — nothing was sent.":
            "Actualización remota de .sync5 fallida al leer {path}: {error} — no se ha enviado nada.",
        "Remote .sync5 update refused: {path} does not carry the expected 'NextSync {version}' banner — wrong or stale file.":
            "Actualización remota de .sync5 rechazada: {path} no lleva el distintivo 'NextSync {version}' esperado — archivo equivocado u obsoleto.",
        "Remote .sync5 update: staging {path} ({size} bytes)…":
            "Actualización remota de .sync5: preparando {path} ({size} bytes)…",
        "Remote .sync5 update: staged copy verified ({size} bytes) — swapping it in…":
            "Actualización remota de .sync5: copia preparada verificada ({size} bytes) — sustituyéndola…",
        "Remote .sync5 update FAILED mid-swap: the Next may be missing {target}. If .sync5 no longer starts, rename {backup} back to sync5 in the NextZXOS Browser (the staged {staged} can be deleted).":
            "Actualización remota de .sync5 FALLIDA en plena sustitución: puede que el Next no tenga {target}. Si .sync5 ya no arranca, renombra {backup} de nuevo a sync5 en el Browser de NextZXOS (el {staged} preparado puede borrarse).",
        "Remote .sync5 update complete: {version} is on the card. The session will now close — run {command} on the Next to start the new dot.":
            "Actualización remota de .sync5 completada: {version} ya está en la tarjeta. La sesión se cerrará ahora — ejecuta {command} en el Next para iniciar el nuevo dot.",
        "Remote .sync5 update failed: {reason}. Nothing was swapped — the Next still runs its current dot.":
            "Actualización remota de .sync5 fallida: {reason}. No se ha cambiado nada — el Next sigue ejecutando su dot actual.",
        "Remote explorer: connected to {address}":
            "Explorador remoto: conectado a {address}",
        "Remote explorer: connection error from the Next ({error}) — session over.":
            "Explorador remoto: error de conexión con el Next ({error}); sesión terminada.",
        "Remote explorer: the Next closed the connection.":
            "Explorador remoto: el Next cerró la conexión.",
        "Remote explorer: no word from the Next for {seconds}s — assuming it is gone (powered off? Wi-Fi dropped?)":
            "Explorador remoto: sin noticias del Next durante {seconds}s: se da por perdido (¿apagado? ¿Wi-Fi caído?)",
        "Remote explorer: turned away a second Next at {address} — a session is already active (Busy).":
            "Explorador remoto: se rechazó un segundo Next desde {address}: ya hay una sesión activa (Busy).",
        "Remote explorer: server keeps running in the background — stop it from the Remote Explorer view.":
            "Explorador remoto: el servidor sigue ejecutándose en segundo plano; deténlo desde la vista Remote Explorer.",
        "Remote explorer: navigate to a folder in the left file explorer, press 'Set current folder as new sync root folder', click 'Start Remote Explorer NextSync server', then run {command} on your Next.":
            "Explorador remoto: navega a una carpeta en el explorador izquierdo, pulsa 'Set current folder as new sync root folder', haz clic en 'Start Remote Explorer NextSync server' y luego ejecuta {command} en tu Next.",
        "Remote explorer: port {port} is already in use — is another ZX-Next-Unite (or NextSync server) already running?":
            "Explorador remoto: el puerto {port} ya está en uso, ¿hay otro ZX-Next-Unite (o servidor NextSync) en ejecución?",
        "Remote explorer: the Next disconnected (BREAK / Bye) — restarting the listen server; run {command} on your Next to reconnect.":
            "Explorador remoto: el Next se desconectó (BREAK / Bye); reiniciando el servidor de escucha. Ejecuta {command} en tu Next para reconectar.",
        "Remote explorer: the Next disconnected (BREAK / Bye). Press 'Start Remote Explorer NextSync server' to accept a new connection.":
            "Explorador remoto: el Next se desconectó (BREAK / Bye). Pulsa 'Start Remote Explorer NextSync server' para aceptar una nueva conexión.",
        "Remote explorer: waiting for {command} on port {port}…":
            "Explorador remoto: esperando {command} en el puerto {port}…",
        "Renamed: {old} -> {new}":
            "Renombrado: {old} -> {new}",
        "Running on host:":
            "Ejecutándose en el equipo:",
        "Saving incoming files under: {folder}":
            "Guardando los archivos entrantes en: {folder}",
        "Send via NextSync: nothing to send in {folder}.":
            "Enviar por NextSync: no hay nada que enviar en {folder}.",
        "Sending {folder} via Remote Explorer (-listen) → {target} …":
            "Enviando {folder} por el Explorador remoto (-listen) → {target} …",
        "Set a sync root folder first: navigate to the folder in the left local file explorer and press 'Set current folder as new sync root folder'.":
            "Elige primero una carpeta raíz de sincronización: navega a ella en el explorador local izquierdo y pulsa 'Set current folder as new sync root folder'.",
        "Skipped (already exists): {path}":
            "Omitido (ya existe): {path}",
        "Skipped {path}: cannot import a folder into itself.":
            "Omitido {path}: no se puede importar una carpeta dentro de sí misma.",
        "Stop the running sync before starting the remote server.":
            "Detén la sincronización en curso antes de iniciar el servidor remoto.",
        "Sync file list has {count} files.":
            "La lista de sincronización tiene {count} archivos.",
        "Sync point file {name} not found, syncing all files regardless of timestamp.":
            "No se encontró el archivo de punto de sincronización {name}; se sincronizarán todos los archivos sin tener en cuenta la fecha.",
        "Sync point updated with {count} received file(s)":
            "Punto de sincronización actualizado con {count} archivo(s) recibido(s)",
        "Upload connection closed":
            "Conexión de subida cerrada",
        "Upload finished, {count} file(s) received":
            "Subida terminada, {count} archivo(s) recibido(s)",
        "Using {folder} as sync root":
            "Usando {folder} como raíz de sincronización",
        "WARNING":
            "AVISO",
        "Warning":
            "Aviso",
        "Warning! Ignore file {name} not found in directory. All files will be synced, possibly including this file.":
            "¡Atención! No se encontró el archivo de exclusiones {name} en la carpeta. Se sincronizarán todos los archivos, posiblemente incluido este.",
        "{kb} kilobytes payload, {rate} kBps effective speed":
            "{kb} kilobytes de datos útiles, velocidad efectiva {rate} kBps",
        "{kb} kilobytes transferred in {seconds} seconds, {rate} kBps":
            "{kb} kilobytes transferidos en {seconds} segundos, {rate} kBps",
        "{severity}: Ready to sync {count} files, {kb} kilobytes.":
            "{severity}: listo para sincronizar {count} archivos, {kb} kilobytes.",
        # ---- checkboxes ----
        "Alien Floyd's — animated background on all tabs (Retro/pygame)":
            "Alien Floyd's — fondo animado en todas las pestañas (Retro/pygame)",
        "Alien Floyd's — show the full-window 'Alien Floyd's' tab (Retro/pygame)":
            "Alien Floyd's — mostrar la pestaña «Alien Floyd's» a pantalla completa (Retro/pygame)",
        "Check for CSpect update on itch.io on startup":
            "Buscar actualizaciones de CSpect en itch.io al iniciar",
        "Check for ZX Next Unite updates at startup on Github":
            "Buscar actualizaciones de ZX Next Unite en GitHub al iniciar",
        "Check for a newer MAME version at startup":
            "Buscar una versión más reciente de MAME al iniciar",
        "Disable 'No emulators detected' message at startup":
            "Desactivar el aviso «No se detectaron emuladores» al iniciar",
        "Do not prompt for confirmation on deletion.":
            "No pedir confirmación al eliminar.",
        "Enable NextSync HTTP bridge (web server for the Next's .http command)":
            "Activar el puente HTTP de NextSync (servidor web para el comando .http del Next)",
        "Enable crash log file generation":
            "Activar la generación del registro de fallos",
        "Enable multi API endpoints search (GetIt, ZXDB & zxArt search together).":
            "Activar la búsqueda multi-API (GetIt, ZXDB y zxArt a la vez).",
        "Enable search autocompletion.": "Activar el autocompletado de búsqueda.",
        "NextSync — starfield log animation (Retro/pygame mode)":
            "NextSync — animación de estrellas en el registro (modo Retro/pygame)",
        "Perform pre-availability check on Downloads (ZXDB & zxArt).":
            "Comprobar la disponibilidad de las descargas (ZXDB y zxArt).",
        "Require bearer token": "Exigir token de acceso",
        "SD Card - Warn when an image is nearly full.":
            "Tarjeta SD — Avisar cuando una imagen esté casi llena.",
        "Send deleted files to the Recycle Bin (local file explorers).":
            "Enviar los archivos eliminados a la Papelera (exploradores locales).",
        "Show the itch.io tab (browse & install your itch.io collections)":
            "Mostrar la pestaña itch.io (explorar e instalar tus colecciones)",
        "Slow transfer": "Transferencia lenta",
        "Unite! — Invaders background animation (Retro/pygame mode)":
            "Unite! — animación de fondo Invaders (modo Retro/pygame)",
        # ---- radio buttons / group boxes ----
        "Always sync (send everything)": "Sincronizar siempre (enviar todo)",
        "Sync changed files (continuous)": "Sincronizar cambios (continuo)",
        "Sync once": "Sincronizar una vez",
        "Sync mode": "Modo de sincronización",
        # ---- placeholders ----
        "Filter by name, type or size...": "Filtrar por nombre, tipo o tamaño…",
        "Filter by name...": "Filtrar por nombre…",
        "Local folder path...": "Ruta de la carpeta local…",
        "New directory name ...": "Nombre del nuevo directorio…",
        "Paste your personal API key (itch.io → Settings → API keys)":
            "Pega tu clave API personal (itch.io → Settings → API keys)",
        "Path inside the SD card image...": "Ruta dentro de la imagen SD…",
        "SD card image path...": "Ruta de la imagen de tarjeta SD…",
        "Search ZXDB games... (leave empty for random selection)":
            "Buscar juegos en ZXDB… (vacío = selección aleatoria)",
        "Search across GetIt + ZXDB + zxArt...":
            "Buscar en GetIt + ZXDB + zxArt…",
        "Search files... (leave empty for latest 20)":
            "Buscar archivos… (vacío = últimos 20)",
        "Search your itch.io library (collections + purchases)…":
            "Buscar en tu biblioteca de itch.io (colecciones y compras)…",
        "Search zxART productions... (leave empty to browse latest)":
            "Buscar producciones en zxART… (vacío = ver novedades)",
        "Sync root folder...": "Carpeta raíz de sincronización…",
        "bearer token (generated when you enable the checkbox)":
            "token de acceso (se genera al activar la casilla)",
        # ---- short tooltips ----
        "Browse mode": "Modo exploración",
        "Search mode": "Modo búsqueda",
        "Pick a letter": "Elige una letra",
        "Double-click to enlarge": "Doble clic para ampliar",
        "Double-click to open full view": "Doble clic para abrir la vista completa",
        "Generate a new random 64-character bearer token":
            "Generar un nuevo token aleatorio de 64 caracteres",
        "Select a disk image to be loaded.": "Selecciona la imagen de disco a cargar.",
        "No SD card image is currently loaded.":
            "No hay ninguna imagen de tarjeta SD cargada.",
        "Re-read the current local folder from disk.":
            "Volver a leer la carpeta local actual desde el disco.",
        "Drag to resize the file explorers / log window split.":
            "Arrastra para redimensionar la división exploradores / registro.",
        "Drag to resize the results / MOTD split.":
            "Arrastra para redimensionar la división resultados / MOTD.",
        "Drag to set the background image opacity (0–100 %).":
            "Arrastra para ajustar la opacidad del fondo (0–100 %).",
        "Type a value 0–100 to set the background image opacity.":
            "Escribe un valor 0–100 para la opacidad del fondo.",
        "Preview of the selected background image.":
            "Vista previa de la imagen de fondo seleccionada.",
        "Open https://itch.io/ in your browser":
            "Abrir https://itch.io/ en tu navegador",
        "Connect to itch.io using the API key above.":
            "Conectar a itch.io con la clave API de arriba.",
        "Disconnect from itch.io and clear the listed items.":
            "Desconectar de itch.io y borrar los elementos listados.",
        "Open https://itch.io/user/settings/api-keys in your browser":
            "Abrir https://itch.io/user/settings/api-keys en tu navegador",
        "Color used for directory name entries in the image explorer.":
            "Color de los nombres de directorio en el explorador de la imagen.",
        "Color used for file name entries in the image explorer.":
            "Color de los nombres de archivo en el explorador de la imagen.",
        "Color used for the 'DIR' type label column of directory entries.":
            "Color de la columna de tipo «DIR» de los directorios.",
        "Color used for the '[Up Directory..]' navigation row in the image explorer.":
            "Color de la fila de navegación «[Up Directory..]».",
        "Color used for the file extension column in the image explorer.":
            "Color de la columna de extensión de archivo.",
        "Color used for the file size column in the image explorer.":
            "Color de la columna de tamaño de archivo.",
        "Background color behind the file explorers and the whole app window.":
            "Color de fondo detrás de los exploradores de archivos y de toda la ventana de la aplicación.",
        "Discard the hand-picked colors above and restore the themed defaults.":
            "Descarta los colores elegidos arriba y restaura los valores predeterminados del tema.",
        "MAME display aspect ratio (-aspect).":
            "Relación de aspecto de MAME (-aspect).",
        "Perform a single sync and then stop the server.":
            "Realizar una única sincronización y detener el servidor.",
        "Keep listening and send every file each time, ignoring the sync point.":
            "Seguir a la escucha y enviar todos los archivos cada vez, ignorando el punto de sincronización.",
        "Keep listening and send only files that are new or changed since the\nlast sync (skips files recorded in the sync point). The default mode.":
            "Seguir a la escucha y enviar solo los archivos nuevos o modificados\ndesde la última sincronización (omite los registrados en el punto de\nsincronización). Modo predeterminado.",
        "Make the folder currently shown in the explorer above the new sync root.":
            "Usar la carpeta mostrada en el explorador como nueva raíz de sincronización.",
        "Pick a random page from the full GetIt catalogue and show its entries.":
            "Elegir una página al azar del catálogo GetIt y mostrar sus entradas.",
        "Pick a random page of zxART productions and show its entries.":
            "Elegir una página al azar de producciones de zxART y mostrarla.",
        "Show the most recent zxART productions/pictures (sorted by date).":
            "Mostrar las producciones/imágenes más recientes de zxART (por fecha).",
        "Show the most recently added/updated ZXDB games.":
            "Mostrar los juegos añadidos o actualizados más recientemente en ZXDB.",
        "Fetch random entries from GetIt + ZXDB + zxArt and merge them here":
            "Obtener entradas aleatorias de GetIt + ZXDB + zxArt y combinarlas aquí",
        "Fetch the latest releases from GetIt + ZXDB + zxArt and merge them here":
            "Obtener las últimas novedades de GetIt + ZXDB + zxArt y combinarlas aquí",
        "zxART catalog display language.\nPersisted across sessions in the config file.":
            "Idioma del catálogo de zxART.\nSe guarda en el archivo de configuración.",
        "Requires the optional 'pygame-ce' package.\nInstall with: pip install pygame-ce":
            "Requiere el paquete opcional «pygame-ce».\nInstálalo con: pip install pygame-ce",
        "Load a ZX Spectrum Next disk image first — then CSpect can boot it from the mounted SD card.":
            "Carga primero una imagen de disco de ZX Spectrum Next — CSpect podrá arrancarla desde la tarjeta SD montada.",
        "Select emulator image file: {path}":
            "Seleccionar imagen para el emulador: {path}",
        "No writable disk image available.":
            "No hay ninguna imagen de disco grabable disponible.",
        ".img file {path} already in use.":
            "El archivo .img {path} ya está en uso.",
        "The disk image {path} can no longer be found — it may have been moved, renamed or deleted.":
            "La imagen de disco {path} ya no se encuentra — puede que se haya movido, renombrado o eliminado.",
        "Select a ZX Spectrum Next disk image (.img/.hdf) first — then MAME can boot it as the Next's hard disk.":
            "Selecciona primero una imagen de disco (.img/.hdf) de ZX Spectrum Next — MAME podrá arrancarla como disco duro del Next.",
        "Select the parent folder inside the SD card image\n(at the top level this returns the target to the image root).":
            "Seleccionar la carpeta superior dentro de la imagen SD\n(en el nivel más alto vuelve a la raíz de la imagen).",
        "Go up one folder in the local file explorer\n(same as double-clicking its '..' entry).":
            "Subir una carpeta en el explorador local\n(igual que hacer doble clic en su entrada «..»).",
        "Re-list the current image folder from the SD card image\n(runs 'hdfmonkey ls' again).":
            "Volver a listar la carpeta actual de la imagen SD\n(ejecuta «hdfmonkey ls» de nuevo).",
        "Download a ready-to-use NextZXOS SD card image from zxnext.uk,\nsave it to disk, and load it automatically.":
            "Descargar una imagen SD de NextZXOS lista para usar desde zxnext.uk,\nguardarla en disco y cargarla automáticamente.",
        "Switch between the classic table view and the picture (gallery)\nview. Persisted across sessions in the config file.":
            "Alternar entre la vista de tabla clásica y la vista de galería.\nSe guarda en el archivo de configuración.",
        "Switch between the classic table view and the picture (gallery) view.\nPersisted across sessions in the config file.":
            "Alternar entre la vista de tabla clásica y la vista de galería.\nSe guarda en el archivo de configuración.",
        "Language of the application's buttons, labels and checkboxes.\nApplies immediately; texts written while the app runs (logs, dialogs)\nfollow after a restart. Saved to the configuration file.":
            "Idioma de los botones, etiquetas y casillas de la aplicación.\nSe aplica al instante; los textos generados en ejecución (registros,\ndiálogos) cambian tras reiniciar. Se guarda en la configuración.",
        "🌐  Language set to match your system":
            "🌐  Idioma ajustado a tu sistema",
        "The interface language was set to match your system language.\nYou can change it anytime in the Settings tab (\"Application language:\").":
            "El idioma de la interfaz se ha ajustado al idioma de tu sistema.\nPuedes cambiarlo cuando quieras en la pestaña Settings\n(«Idioma de la aplicación:»).",
        # ---- Help tab (INIT_HELP; rebuilt per language by _repopulate_help) ----
        "Welcome to zx-next-unite {version} help":
            "Bienvenido a la ayuda de zx-next-unite {version}",
        "Introduction:":
            "Introducción:",
        "HdfmGooey was initially created by em00k and NextSync by Jari Komppa.":
            "HdfmGooey fue creado originalmente por em00k y NextSync por Jari Komppa.",
        "A while back I rambled with the idea of an all in one bootstrapper transfer tool to":
            "Hace un tiempo le daba vueltas a la idea de una herramienta de transferencia y arranque todo en uno para",
        "avoid manipulating SD cards for the Spectrum Next and that was the initial idea of it.":
            "evitar manipular tarjetas SD para el Spectrum Next, y esa fue su idea inicial.",
        "Last but not least some source code was lost from HDFM Gooey and the tool was stuck back in that time,":
            "Por último, pero no menos importante, parte del código fuente de HDFM Gooey se perdió y la herramienta quedó anclada en aquella época;",
        "with the agreement of em00k I started a rewrite in Python and later with Jari.":
            "con el acuerdo de em00k comencé una reescritura en Python y más tarde con Jari.",
        "The point of using Python was that it would also provide MacOS and Linux portability.":
            "La idea de usar Python era que además ofrecería portabilidad a MacOS y Linux.",
        "Later down the line I then extended the NextSync functionality from Sync3 to Sync4.":
            "Más adelante amplié la funcionalidad de NextSync de Sync3 a Sync4.",
        "The new .sync5 command for the Next speaks Sync4 and therefore allows sending files and directories using the -send command line option.":
            "El nuevo comando .sync5 para el Next habla Sync4 y por tanto permite enviar archivos y directorios usando la opción de línea de comandos -send.",
        "There is as well a new nextsync5.py command line located at the root of the repository that supports the new Sync4 protocol.":
            "También hay una nueva línea de comandos nextsync5.py, situada en la raíz del repositorio, que soporta el nuevo protocolo Sync4.",
        "Here we are now you have it!":
            "¡Aquí estamos, ya lo tienes!",
        "Keyboard shortcuts":
            "Atajos de teclado",
        "The three file explorers (SD Card local, SD Card disk image and NextSync local) share these shortcuts. Copy / Cut / Paste work across all three explorers and also exchange with the operating-system clipboard (e.g. copy in Windows Explorer, paste into the disk image, and vice-versa):":
            "Los tres exploradores de archivos (local de SD Card, imagen de disco de SD Card y local de NextSync) comparten estos atajos. Copiar / Cortar / Pegar funcionan entre los tres exploradores y también intercambian con el portapapeles del sistema operativo (p. ej., copia en el Explorador de Windows, pega en la imagen de disco, y viceversa):",
        "    Ctrl+C  -  Copy the selected file(s)/folder(s) to the shared clipboard.":
            "    Ctrl+C  -  Copiar los archivos/carpetas seleccionados al portapapeles compartido.",
        "    Ctrl+X  -  Cut the selection (moved to the destination on the next paste).":
            "    Ctrl+X  -  Cortar la selección (se mueve al destino en el siguiente pegado).",
        "    Ctrl+V  -  Paste into the selected / currently shown folder.":
            "    Ctrl+V  -  Pegar en la carpeta seleccionada o mostrada actualmente.",
        "    F2      -  Rename the selected file or folder.":
            "    F2      -  Renombrar el archivo o carpeta seleccionado.",
        "    Delete  -  Delete the selected file or folder (disk-image & NextSync explorers).":
            "    Delete  -  Eliminar el archivo o carpeta seleccionado (exploradores de imagen de disco y NextSync).",
        "In the picture (gallery) item viewer (double-click an item in the GetIt, ZXDB, zxArt or itch.io tabs):":
            "En el visor de elementos de imágenes (galería) (doble clic en un elemento de las pestañas GetIt, ZXDB, zxArt o itch.io):",
        "    Esc           -  Close the viewer and return to the gallery.":
            "    Esc           -  Cerrar el visor y volver a la galería.",
        "    Left / Right  -  Show the previous / next screenshot.":
            "    Left / Right  -  Mostrar la captura anterior / siguiente.",
        "Third party license":
            "Licencias de terceros",
        "zx-next-unite is released under the MIT license. It is a Qt Application using PySide6 (Qt for Python) on top of Qt6, used under the GNU LGPL v3.":
            "zx-next-unite se publica bajo la licencia MIT. Es una aplicación Qt que usa PySide6 (Qt para Python) sobre Qt6, utilizado bajo la GNU LGPL v3.",
        "Please refer to the LICENSE and THIRD-PARTY-NOTICES.md files on github: https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE and https://github.com/jclauzel/ZX-Next-Unite/blob/main/THIRD-PARTY-NOTICES.md.":
            "Consulta los archivos LICENSE y THIRD-PARTY-NOTICES.md en github: https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE y https://github.com/jclauzel/ZX-Next-Unite/blob/main/THIRD-PARTY-NOTICES.md.",
        "PySide6 is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The pre-built release executables do include PySide6/Qt; since the app's full source code is published, they can be rebuilt with a modified Qt.":
            "PySide6 no viene incluido al hacer una instalación manual de python y debe instalarse por separado (ver instrucciones de instalación). Los ejecutables precompilados de las releases sí incluyen PySide6/Qt; como el código fuente completo de la aplicación está publicado, pueden reconstruirse con un Qt modificado.",
        "zx-next-unite also uses pygame-ce (the community edition of pygame) for its animated backgrounds and visualizations (e.g. the 'Alien Floyd's' effects). Many thanks to the pygame and pygame-ce communities - see https://pyga.me and https://www.pygame.org.":
            "zx-next-unite también usa pygame-ce (la edición comunitaria de pygame) para sus fondos animados y visualizaciones (p. ej., los efectos de 'Alien Floyd's'). Muchas gracias a las comunidades de pygame y pygame-ce - ver https://pyga.me y https://www.pygame.org.",
        "pygame-ce is distributed under the GNU LGPL v2.1 license and, like PySide6, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions).":
            "pygame-ce se distribuye bajo la licencia GNU LGPL v2.1 y, como PySide6, no viene incluido al hacer una instalación manual de python y debe instalarse por separado (ver instrucciones de instalación).",
        "zx-next-unite optionally uses itch-dl by Dragoon Aethis to power the itch.io tab (browsing and installing your itch.io collections). Many thanks to its author - see https://github.com/DragoonAethis/itch-dl.":
            "zx-next-unite usa opcionalmente itch-dl de Dragoon Aethis para la pestaña itch.io (navegar e instalar tus colecciones de itch.io). Muchas gracias a su autor - ver https://github.com/DragoonAethis/itch-dl.",
        "itch-dl is distributed under the MIT license (Copyright (c) 2022 Dragoon Aethis) and, like PySide6 and pygame-ce, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The itch.io tab is only shown when itch-dl is installed.":
            "itch-dl se distribuye bajo la licencia MIT (Copyright (c) 2022 Dragoon Aethis) y, como PySide6 y pygame-ce, no viene incluido al hacer una instalación manual de python y debe instalarse por separado (ver instrucciones de instalación). La pestaña itch.io solo se muestra cuando itch-dl está instalado.",
        "zx-next-unite optionally uses Flask by the Pallets team to power the NextSync HTTP bridge - the web server behind the Next's .http dot command that lets one Next drive another Next's SD card. Many thanks to its authors - see https://flask.palletsprojects.com and https://github.com/pallets/flask.":
            "zx-next-unite usa opcionalmente Flask, del equipo Pallets, para el puente HTTP de NextSync - el servidor web detrás del comando dot .http del Next que permite que un Next maneje la tarjeta SD de otro Next. Muchas gracias a sus autores - ver https://flask.palletsprojects.com y https://github.com/pallets/flask.",
        "Flask is distributed under the BSD-3-Clause license and, like the other optional packages, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The HTTP bridge toggle in Settings is greyed out until Flask is installed.":
            "Flask se distribuye bajo la licencia BSD-3-Clause y, como los demás paquetes opcionales, no viene incluido al hacer una instalación manual de python y debe instalarse por separado (ver instrucciones de instalación). El interruptor del puente HTTP en Settings está desactivado hasta que Flask esté instalado.",
        "zx-next-unite optionally uses Send2Trash by Andrew Senetar and contributors (originally by Virgil Dupras) to send files deleted in the local file explorers to the system Recycle Bin / Trash instead of removing them permanently. Many thanks to its authors - see https://github.com/arsenetar/send2trash.":
            "zx-next-unite usa opcionalmente Send2Trash, de Andrew Senetar y colaboradores (originalmente de Virgil Dupras), para enviar los archivos eliminados en los exploradores locales a la Papelera de reciclaje del sistema en lugar de borrarlos permanentemente. Muchas gracias a sus autores - ver https://github.com/arsenetar/send2trash.",
        "Send2Trash is distributed under the BSD license and, like the other optional packages, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The 'Send deleted files to the Recycle Bin' toggle in Settings is greyed out until Send2Trash is installed.":
            "Send2Trash se distribuye bajo la licencia BSD y, como los demás paquetes opcionales, no viene incluido al hacer una instalación manual de python y debe instalarse por separado (ver instrucciones de instalación). El interruptor 'Enviar los archivos eliminados a la Papelera' de Settings está desactivado hasta que Send2Trash esté instalado.",
        "zx-next-unite's optional pre-compiled Windows binary is built with PyInstaller by the PyInstaller Development Team, which bundles the app and all of its dependencies into a single standalone executable. Many thanks to its authors - see https://pyinstaller.org and https://github.com/pyinstaller/pyinstaller.":
            "El binario opcional precompilado de Windows de zx-next-unite se construye con PyInstaller, del PyInstaller Development Team, que empaqueta la aplicación y todas sus dependencias en un único ejecutable independiente. Muchas gracias a sus autores - ver https://pyinstaller.org y https://github.com/pyinstaller/pyinstaller.",
        "PyInstaller is distributed under its GPL 2.0 license with a special exception that explicitly permits packaging applications of any license. It is a build-time tool only - used to produce the pre-compiled binary - and is not needed when running zx-next-unite from source with a manual python install.":
            "PyInstaller se distribuye bajo su licencia GPL 2.0 con una excepción especial que permite explícitamente empaquetar aplicaciones de cualquier licencia. Es solo una herramienta de construcción - usada para producir el binario precompilado - y no se necesita al ejecutar zx-next-unite desde el código fuente con una instalación manual de python.",
        "The pre-compiled Windows binary is additionally compressed with UPX (the Ultimate Packer for eXecutables) by Markus Oberhumer, Laszlo Molnar and John Reiser. Many thanks to its authors - see https://upx.github.io and https://github.com/upx/upx.":
            "El binario precompilado de Windows se comprime además con UPX (the Ultimate Packer for eXecutables), de Markus Oberhumer, Laszlo Molnar y John Reiser. Muchas gracias a sus autores - ver https://upx.github.io y https://github.com/upx/upx.",
        "UPX is distributed under its own liberal license (based on the GPL, with a special exception covering the compressed executables it produces). Like PyInstaller it is a build-time tool only and is not needed when running from source.":
            "UPX se distribuye bajo su propia licencia liberal (basada en la GPL, con una excepción especial que cubre los ejecutables comprimidos que produce). Como PyInstaller, es solo una herramienta de construcción y no se necesita al ejecutar desde el código fuente.",
        "Setup & How to:":
            "Instalación y guía:",
        "Check out the main setup & demo video available at: https://youtu.be/-gUxV4fM1yo  (and the full python install is covered in the old py-hdfm-gooey since ZX-Next-Unite is an evolution of it : https://youtu.be/FJG-Z0DCIjQ )":
            "Mira el vídeo principal de instalación y demostración disponible en: https://youtu.be/-gUxV4fM1yo  (la instalación completa de python se cubre en el antiguo py-hdfm-gooey, ya que ZX-Next-Unite es una evolución de él : https://youtu.be/FJG-Z0DCIjQ )",
        "NextSync Head Over Heels demo: https://www.youtube.com/watch?v=D3_WqTPvjOE":
            "Demo de NextSync con Head Over Heels: https://www.youtube.com/watch?v=D3_WqTPvjOE",
        "NextSync Night Knight demo: https://www.youtube.com/watch?v=eN1eMIqMCm4":
            "Demo de NextSync con Night Knight: https://www.youtube.com/watch?v=eN1eMIqMCm4",
        "Is a required external component developed by Matt Westcott that allows browsing the image.":
            "Es un componente externo obligatorio desarrollado por Matt Westcott que permite navegar por la imagen.",
        "You will need to install it to get this application up and fully running.":
            "Tendrás que instalarlo para que esta aplicación funcione por completo.",
        "If hdfmonkey is not present you will see an error message in the main log window as it is missing.":
            "Si hdfmonkey no está presente verás un mensaje de error en la ventana de registro principal indicando que falta.",
        "If that is the case you will see a 'Download and install HDF Monkey' button bottom right,":
            "Si es el caso verás un botón 'Descargar e instalar HDF Monkey' abajo a la derecha;",
        "once clicked it will download a pre-compiled hdfmonkey build for your platform (Windows/Linux/macOS) and install it under the app's downloads folder.":
            "al pulsarlo descargará una compilación precompilada de hdfmonkey para tu plataforma (Windows/Linux/macOS) y la instalará en la carpeta downloads de la aplicación.",
        "If the above automated install is successful, you should then be able to select an image and navigate it.":
            "Si la instalación automática anterior tiene éxito, deberías poder seleccionar una imagen y navegar por ella.",
        "hdfmonkey can also be installed manually based on the instructions for your platform that can be found at: https://github.com/gasman/hdfmonkey":
            "hdfmonkey también puede instalarse manualmente siguiendo las instrucciones para tu plataforma disponibles en: https://github.com/gasman/hdfmonkey",
        "zx-next-unite implements the <Server> side code and protocol of NextSync by Jari Komppa.":
            "zx-next-unite implementa el código y protocolo del lado <servidor> de NextSync de Jari Komppa.",
        "It does not require any dot .sync modification and it uses the same very close python logic as nextsync.py.":
            "No requiere ninguna modificación del dot .sync y usa la misma lógica python, muy cercana, que nextsync.py.",
        "Initial release on specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8":
            "Publicación inicial en specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8",
        "As a result you will need to run the same dot .sync command on your Next as with the console version and the same network protocol.":
            "Por tanto tendrás que ejecutar el mismo comando dot .sync en tu Next que con la versión de consola y el mismo protocolo de red.",
        "The latest release v1.2 of the .sync command can be found here https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 .":
            "La última versión v1.2 del comando .sync puede encontrarse aquí https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 .",
        "You may follow the same instructions as provided in the readme.txt of that release.":
            "Puedes seguir las mismas instrucciones que se incluyen en el readme.txt de esa versión.",
        "On your Spectrum Next, clone or image copy the SYNC command that is located in the above release zip file into your Next dot folder.":
            "En tu Spectrum Next, clona o copia en la imagen el comando SYNC que se encuentra en el zip de esa versión dentro de tu carpeta dot del Next.",
        "Navigate to the NextSync tab, select the root folder to sync on the left.":
            "Ve a la pestaña NextSync y selecciona a la izquierda la carpeta raíz a sincronizar.",
        "Once you have selected the folder hit the 'Prepare Classic NextSync server' button, check the NextSync log window on the right.":
            "Una vez seleccionada la carpeta pulsa el botón 'Preparar servidor NextSync clásico' y comprueba la ventana de registro de NextSync a la derecha.",
        "The first time you run .sync on your Next you will be prompted to select the <server> IP address, this machine running NextSync.":
            "La primera vez que ejecutes .sync en tu Next se te pedirá seleccionar la dirección IP del <servidor>, esta máquina que ejecuta NextSync.",
        "From the log window pick the IP address from this machine you want to use and type it on your Next.":
            "En la ventana de registro elige la dirección IP de esta máquina que quieras usar y tecléala en tu Next.",
        "Then start the sync server on this machine using the 'Start Classic NextSync server' button and then run the .sync command on your Next.":
            "Después inicia el servidor de sincronización en esta máquina con el botón 'Iniciar servidor NextSync clásico' y ejecuta el comando .sync en tu Next.",
        "At this point your Spectrum Next will connect to your machine using a network socket and the files will be sent to your Next.":
            "En ese momento tu Spectrum Next se conectará a tu máquina mediante un socket de red y los archivos se enviarán a tu Next.",
        "As it is your Next that will connect to this machine check your firewall allows inbound calls to this machine on port: 2048 by default.":
            "Como es tu Next el que se conecta a esta máquina, comprueba que tu cortafuegos permite conexiones entrantes a esta máquina en el puerto: 2048 por defecto.",
        "The same syncignore.txt and syncpoint.dat file logic applies and allows you to control the sync (please check Jari's documentation).":
            "Se aplica la misma lógica de archivos syncignore.txt y syncpoint.dat, que te permite controlar la sincronización (consulta la documentación de Jari).",
        "NextSync source code can be found here: https://github.com/jarikomppa/specnext/tree/master/sync":
            "El código fuente de NextSync está aquí: https://github.com/jarikomppa/specnext/tree/master/sync",
        "If you run into any type of issue using the NextSync integration please run first Jari's command line version to see if it works as expected.":
            "Si tienes cualquier tipo de problema con la integración de NextSync, prueba primero la versión de línea de comandos de Jari para ver si funciona como se espera.",
        "OpenAL sound engine (on Windows)":
            "Motor de sonido OpenAL (en Windows)",
        "The OpenAL library is required on Windows for CSpect to play sound, you may download it here: https://openal.org/":
            "La biblioteca OpenAL es necesaria en Windows para que CSpect reproduzca sonido; puedes descargarla aquí: https://openal.org/",
        "Mono (on Linux & MacOS Only)":
            "Mono (solo en Linux y MacOS)",
        "You will also need to manually install the mono-complete package for example using: sudo apt-get install mono-complete":
            "También tendrás que instalar manualmente el paquete mono-complete, por ejemplo con: sudo apt-get install mono-complete",
        "Third-Party Content Sources (GetIt / ZXDB / zxArt):":
            "Fuentes de contenido de terceros (GetIt / ZXDB / zxArt):",
        "zx-next-unite integrates three external databases to let you browse and download":
            "zx-next-unite integra tres bases de datos externas para que puedas explorar y descargar",
        "Spectrum-related software and artwork directly from within the application.":
            "software y arte relacionados con el Spectrum directamente desde la aplicación.",
        "The application consumes their public APIs — it does not host, mirror, or":
            "La aplicación consume sus APIs públicas — no aloja, replica ni",
        "redistribute any of the files itself.":
            "redistribuye ninguno de los archivos por sí misma.",
        "  GetIt is a community-maintained archive of ZX Spectrum Next software.":
            "  GetIt es un archivo de software para ZX Spectrum Next mantenido por la comunidad.",
        "  The application queries the GetIt API to list and search files, then":
            "  La aplicación consulta la API de GetIt para listar y buscar archivos, y luego",
        "  downloads them directly from the URLs returned by that API.":
            "  los descarga directamente desde las URLs devueltas por esa API.",
        "  ZXDB is an open-source database of ZX Spectrum and related software,":
            "  ZXDB es una base de datos de código abierto de software de ZX Spectrum y afines,",
        "  maintained by the community at https://github.com/zxdb/ZXDB .":
            "  mantenida por la comunidad en https://github.com/zxdb/ZXDB .",
        "  The application queries the ZXDB REST API for titles, releases, screenshots":
            "  La aplicación consulta la API REST de ZXDB para títulos, ediciones, capturas",
        "  and inlays, then downloads files directly from the URLs returned by that API.":
            "  y carátulas, y luego descarga los archivos directamente desde las URLs devueltas por esa API.",
        "  zxArt (https://zxart.ee) is a gallery and archive dedicated to ZX Spectrum":
            "  zxArt (https://zxart.ee) es una galería y archivo dedicados al arte visual,",
        "  visual art, music, and productions.":
            "  la música y las producciones del ZX Spectrum.",
        "  The application sends requests to the zxArt API to search productions and":
            "  La aplicación envía peticiones a la API de zxArt para buscar producciones e",
        "  pictures, retrieve metadata and preview images, and download productions":
            "  imágenes, obtener metadatos y vistas previas, y descargar producciones",
        "  directly from the URLs returned by that API.":
            "  directamente desde las URLs devueltas por esa API.",
        "  Mame emulator brought to you by Holub for the ZX Spectrum Next can be installed following this documentation: https://wiki.specnext.dev/MAME:Installing":
            "  El emulador Mame llevado al ZX Spectrum Next por Holub puede instalarse siguiendo esta documentación: https://wiki.specnext.dev/MAME:Installing",
        "  Official Windows Binary Packages can be found here: https://www.mamedev.org/release.html":
            "  Los paquetes binarios oficiales para Windows están aquí: https://www.mamedev.org/release.html",
        "  Put the file tbblue.zip that can be found here: https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip into MAME's roms folder.":
            "  Coloca el archivo tbblue.zip, que puedes encontrar aquí: https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip en la carpeta roms de MAME.",
        "  Important note: Don't extract the tbblue.zip file; MAME will look for the zip file when the 'tbblue' machine is selected.":
            "  Nota importante: no extraigas el archivo tbblue.zip; MAME buscará el zip cuando se seleccione la máquina 'tbblue'.",
        "  Mike Dailly's CSpect is a downloadable emulator for Windows, macOS, and Linux":
            "  CSpect, de Mike Dailly, es un emulador descargable para Windows, macOS y Linux",
        "  Sites and links:":
            "  Sitios y enlaces:",
        "Legal disclaimer:":
            "Aviso legal:",
        "  The author of zx-next-unite does NOT distribute any files, ROMs, games,":
            "  El autor de zx-next-unite NO distribuye archivos, ROMs, juegos,",
        "  demos, graphics, music, or any other content obtained through these APIs.":
            "  demos, gráficos, música ni ningún otro contenido obtenido a través de estas APIs.",
        "  All content is served exclusively by the respective third-party services.":
            "  Todo el contenido lo sirven exclusivamente los respectivos servicios de terceros.",
        "  This application and author do not control third-party content.":
            "  Esta aplicación y su autor no controlan el contenido de terceros.",
        "  It is the sole responsibility of the end user to ensure that any content":
            "  Es responsabilidad exclusiva del usuario final asegurarse de que cualquier contenido",
        "  they download or use through this application complies with the applicable":
            "  que descargue o use a través de esta aplicación cumple con los requisitos aplicables",
        "  copyright, licensing, and legal requirements in their jurisdiction.":
            "  de copyright, licencias y legislación de su jurisdicción.",
        "  If in doubt, consult the terms of service of the relevant platform and":
            "  En caso de duda, consulta los términos de servicio de la plataforma correspondiente y",
        "  seek appropriate legal advice before downloading or using any content.":
            "  busca asesoramiento legal apropiado antes de descargar o usar cualquier contenido.",
        "  For inquiries you may reach out to me on my github page: https://github.com/jclauzel/ZX-Next-Unite":
            "  Para consultas puedes escribirme en mi página de github: https://github.com/jclauzel/ZX-Next-Unite",
        "Enjoy!":
            "¡Que lo disfrutes!",
        # ---- OpenAL guided install (CSpect sound on Windows) ----
        "OpenAL 1.1 detected — CSpect sound is ready.":
            "OpenAL 1.1 detectado — el sonido de CSpect está listo.",
        "Install OpenAL?":
            "¿Instalar OpenAL?",
        ("On Windows CSpect needs the <b>OpenAL 1.1</b> audio library "
         "for sound, and it was not detected on this machine — without "
         "it CSpect runs silent.<br><br>"
         "OpenAL is separate, third-party software — many thanks to its "
         "authors: {url}<br><br>"
         "Download the official installer (oalinst.exe) from openal.org "
         "and run it now?<br><br>"
         "Windows will ask for administrator approval when the installer "
         "starts — the app itself never runs elevated."):
            ("En Windows CSpect necesita la biblioteca de audio "
             "<b>OpenAL 1.1</b> para el sonido, y no se ha detectado en "
             "esta máquina — sin ella CSpect funciona sin audio.<br><br>"
             "OpenAL es software independiente de terceros — muchas gracias "
             "a sus autores: {url}<br><br>"
             "¿Descargar el instalador oficial (oalinst.exe) de openal.org "
             "y ejecutarlo ahora?<br><br>"
             "Windows pedirá aprobación de administrador cuando arranque el "
             "instalador — la propia aplicación nunca se ejecuta elevada."),
        "Download and run the OpenAL installer":
            "Descargar y ejecutar el instalador de OpenAL",
        "Open openal.org":
            "Abrir openal.org",
        "The OpenAL download failed — see the log for details. You can install it manually from {url}":
            "La descarga de OpenAL falló — consulta el registro para más detalles. Puedes instalarlo manualmente desde {url}",
    },
    "pt": {
        # ---- 9.6.0: per-emulator button colour ----
        "Set the {emulator} color…":
            "Definir a cor do {emulator}…",
        "Reset the {emulator} color":
            "Repor a cor do {emulator}",
        # ---- 9.6.0: forgetting a remembered SD image path ----
        "Remove \"{path}\" from the list":
            "Remover \"{path}\" da lista",
        "Clear the whole list":
            "Limpar toda a lista",
        "Removed {path} from the image list — the image file itself was not deleted.":
            "Removido {path} da lista de imagens — o próprio ficheiro de imagem não foi eliminado.",
        "Cleared the image list — no image files were deleted.":
            "Lista de imagens limpa — nenhum ficheiro de imagem foi eliminado.",
        "Clear the image list?":
            "Limpar a lista de imagens?",
        "Forget all {count} remembered image paths? The image files themselves are not deleted.":
            "Esquecer todos os {count} caminhos de imagem memorizados? Os próprios ficheiros de imagem não são eliminados.",
        "Remove the image path shown on the left from the list.\nThe image file itself is not deleted.":
            "Remover da lista o caminho de imagem mostrado à esquerda.\nO próprio ficheiro de imagem não é eliminado.",
        "Path to the SD card image (.img / .hdf).\nType a path directly, click the arrow to pick from recently loaded images,\nor use the 'Select NextZXOS disk Image' button to browse.\nRight-click the box for list options, or press Delete on a dropdown entry to forget it.":
            "Caminho da imagem do cartão SD (.img / .hdf).\nEscreva um caminho diretamente, clique na seta para escolher entre as imagens recentes,\nou use o botão 'Selecionar imagem de disco NextZXOS' para procurar.\nClique com o botão direito na caixa para as opções da lista, ou prima Delete numa entrada da lista pendente para a esquecer.",
        # ---- GetIt starter pack ----
        'Assembling the starter pack…': 'A montar o pacote inicial…',
        'Downloading {title} ({idx}/{total})…': 'A transferir {title} ({idx}/{total})…',
        ('Everything on GetIt is freely distributable; the files are written to {dir} '
         'inside the loaded image.'):
            ('Tudo no GetIt é de distribuição livre; os ficheiros são escritos em '
             '{dir} dentro da imagem carregada.'),
        'Failed: {names}': 'Falharam: {names}',
        'Fetching the GetIt catalogue…': 'A obter o catálogo GetIt…',
        ('Fill the loaded SD image with a hand-picked selection of modern homebrew '
         'from the GetIt catalogue — everything on GetIt is freely distributable.'):
            ('Preenche a imagem SD carregada com uma seleção escolhida a dedo de '
             'homebrew moderno do catálogo GetIt — tudo no GetIt é de distribuição '
             'livre.'),
        'Fill your SD image with {count} hand-picked homebrew titles from the GetIt catalogue?': 'Preencher a tua imagem SD com {count} títulos homebrew escolhidos a dedo do catálogo GetIt?',
        'Load a disk image first (SD Card tab), then try again.': 'Carrega primeiro uma imagem de disco (separador SD Card) e tenta novamente.',
        'No network connection.': 'Sem ligação de rede.',
        'Not in the catalogue right now: {names}': 'De momento não está(ão) no catálogo: {names}',
        'Starter pack': 'Pacote inicial',
        'Starter pack cancelled — {done} title(s) were installed.': 'Pacote inicial cancelado — foram instalados {done} título(s).',
        'Starter pack complete: {done} of {total} titles installed to {dir}.': 'Pacote inicial concluído: {done} de {total} títulos instalados em {dir}.',
        'Starter pack failed: {error}': 'O pacote inicial falhou: {error}',
        '🎁 Starter pack': '🎁 Pacote inicial',
        # ---- context menus & runtime dialogs ----
        '"{name}" already exists in this folder.': '"{name}" já existe nesta pasta.',
        'Add drive {letter}: to the list?': 'Adicionar a unidade {letter}: à lista?',
        'Add Next drive': 'Adicionar unidade do Next',
        'Confirm deletion': 'Confirmar eliminação',
        'Copy': 'Copiar',
        'Copy all text': 'Copiar todo o texto',
        'Copy path to clipboard': 'Copiar caminho para a área de transferência',
        'Copy text to clipboard': 'Copiar texto para a área de transferência',
        'Could not create {name}:': 'Não foi possível criar {name}:',
        'Could not create:': 'Não foi possível criar:',
        'Could not delete:': 'Não foi possível eliminar:',
        'Could not extract {name}:': 'Não foi possível extrair {name}:',
        'Could not rename:': 'Não foi possível renomear:',
        'Create directory failed': 'Falha ao criar o diretório',
        'Create new directory': 'Criar novo diretório',
        'Create new directory…': 'Criar novo diretório…',
        'Cut': 'Cortar',
        'Decrease font size': 'Diminuir tamanho da letra',
        'Delete failed': 'Falha ao eliminar',
        'Delete from the local disk?': 'Eliminar do disco local?',
        'Delete on the Next? Folders are deleted with everything inside them.': 'Eliminar no Next? As pastas são eliminadas com todo o seu conteúdo.',
        'Delete the file "{name}"?': 'Eliminar o ficheiro "{name}"?',
        'Delete the folder "{name}" and all of its contents?': 'Eliminar a pasta "{name}" e todo o seu conteúdo?',
        'Delete these {count} items? Folders are deleted with all of their contents.': 'Eliminar estes {count} itens? As pastas são eliminadas com todo o seu conteúdo.',
        'Deleted files are sent to the Recycle Bin.': 'Os ficheiros eliminados vão para a Reciclagem.',
        'Download (:<-)': 'Transferir (:<-)',
        'Download content': 'Transferir conteúdo',
        'Download Failed': 'Falha na transferência',
        'Drive letter of the additional SD reader/partition (D..P):': 'Letra da unidade do leitor SD/partição adicional (D..P):',
        'Extraction Failed': 'Falha na extração',
        'Failed to download the NextZXOS image:': 'Não foi possível transferir a imagem NextZXOS:',
        'Fetch issue info for this magazine': 'Obter informação de edições desta revista',
        'Fetch single magazine by name': 'Procurar uma revista pelo nome',
        'file': 'o ficheiro',
        'Files:  {files}\nFolders:  {folders}\nTotal size:  {size} bytes  ({pretty})': 'Ficheiros:  {files}\nPastas:  {folders}\nTamanho total:  {size} bytes  ({pretty})',
        'folder': 'a pasta',
        'Get size': 'Obter tamanho',
        'Image not writable': 'A imagem não permite escrita',
        'Increase font size (now {px}px)': 'Aumentar tamanho da letra (agora {px}px)',
        'More like this': 'Mais como este',
        'New directory name in': 'Nome do novo diretório em',
        'Name this Next': 'Dar nome a este Next',
        'Friendly name for {addr} (empty removes it):':
            'Nome amigável para {addr} (vazio remove-o):',
        'New folder in {path}:': 'Nova pasta em {path}:',
        'New Folder…': 'Nova pasta…',
        'New name for the {kind}:': 'Novo nome para {kind}:',
        'Not enough space on the Next': 'Espaço insuficiente no Next',
        ('Only add a drive that really exists on your Next (an extra SD card reader or '
         'partition). Selecting a drive that is not mounted CRASHES the Next.'):
            ('Adiciona apenas uma unidade que exista mesmo no teu Next (um leitor SD '
             'ou partição adicional). Selecionar uma unidade não montada BLOQUEIA o '
             'Next.'),
        'Open': 'Abrir',
        'Open in {source}': 'Abrir em {source}',
        'Open: the system could not open {name}.':
            'Abrir: o sistema não conseguiu abrir {name}.',
        'Open on website (zxart.ee)': 'Abrir no site (zxart.ee)',
        'Open on website (zxinfo.dk)': 'Abrir no site (zxinfo.dk)',
        'Paste': 'Colar',
        'Remote Unzip file': 'Descompactar ficheiro remoto',
        'Remote Zip': 'Compactar remoto',
        'Remove from Favorites': 'Remover dos Favoritos',
        "Rename '{name}' to:": "Renomear '{name}' para:",
        'Rename failed': 'Falha ao renomear',
        'Rename…': 'Renomear…',
        'Retrieve all issues': 'Obter todas as edições',
        'Send to SD card (image)  →  {dest}': 'Enviar para o cartão SD (imagem)  →  {dest}',
        'Send using NextSync  →  {dest}': 'Enviar via NextSync  →  {dest}',
        'Set sync root': 'Definir raiz de sincronização',
        'Set this folder as the new sync root?': 'Definir esta pasta como nova raiz de sincronização?',
        'Size on the Next': 'Tamanho no Next',
        'The image was downloaded but could not be extracted:': 'A imagem foi transferida mas não pôde ser extraída:',
        "The name cannot contain '/' or '\\'.": "O nome não pode conter '/' nem '\\'.",
        'This cannot be undone.': 'Esta ação não pode ser anulada.',
        ('This copy needs {need} bytes ({need_h}), but drive {drive}: only has {free} '
         'bytes ({free_h}) free.\n\nIt exceeds the available remote space by {over} '
         'bytes ({over_h}).\n\nThe copy was not started.'):
            ('Esta cópia precisa de {need} bytes ({need_h}), mas a unidade {drive}: só '
             'tem {free} bytes ({free_h}) livres.\n\nExcede o espaço remoto disponível '
             'em {over} bytes ({over_h}).\n\nA cópia não foi iniciada.'),
        'Unzip file': 'Descompactar ficheiro',
        'Zip': 'Compactar',
        '… and {n} more': '… e mais {n}',
        # ---- labels ----
        "  Background": "  Fundo",
        "  Directory name": "  Nome do diretório",
        "  Directory type label": "  Etiqueta de tipo de diretório",
        "  File extension": "  Extensão do ficheiro",
        "  File name": "  Nome do ficheiro",
        "  File size": "  Tamanho do ficheiro",
        "  Filter: ": "  Filtro: ",
        "  General UI text": "  Texto geral da interface",
        "  Retro logs console": "  Consola de registo retro",
        "  Up Directory item": "  Item «Subir diretório»",
        "Background image opacity (%):": "Opacidade da imagem de fundo (%):",
        "Background image:": "Imagem de fundo:",
        "CSpect default launch parameters:": "Parâmetros de arranque do CSpect:",
        "Collection:": "Coleção:",
        "Desktop Theme:": "Tema do ambiente de trabalho:",
        "Disk Image Explorer: ": "Explorador da imagem de disco: ",
        "Gallery animation:": "Animação da galeria:",
        "Gallery image size:": "Tamanho das imagens da galeria:",
        "Gallery items per row:": "Itens por linha na galeria:",
        "Gallery rows per page (min):": "Linhas por página na galeria (mín):",
        "Gallery search sort ordering preference:": "Ordenação dos resultados de pesquisa da galeria:",
        "Gallery slideshow pause time:": "Pausa da apresentação de diapositivos:",
        "Language:": "Idioma:",
        "Application language:": "Idioma da aplicação:",
        "Local file explorers & App Text Colors:": "Exploradores locais e cores do texto:",
        "Local path: ": "Caminho local: ",
        "MAME ROM / system:": "ROM / sistema do MAME:",
        "MAME default launch parameters:": "Parâmetros de arranque do MAME:",
        "Max connections:": "Ligações máx.:",
        "NextSync — Automatically start Remote Explorer server on startup":
            "NextSync — Iniciar automaticamente o servidor do Remote Explorer no arranque",
        "NextSync — when a sent file or directory exists locally:":
            "NextSync — se um ficheiro ou diretório recebido já existir localmente:",
        "Page:": "Página:",
        "Port:": "Porta:",
        "Reset theme": "Repor tema",
        "Retro log font size:": "Tamanho da letra do registo retro:",
        "Search:": "Pesquisar:",
        "Search: ": "Pesquisar: ",
        "View:": "Vista:",
        "itch.io API key:": "Chave API do itch.io:",
        # ---- buttons ----
        "< Prev": "< Ant.",
        "Cancel": "Cancelar",
        "Cancel sync": "Cancelar sincronização",
        "Connect": "Ligar",
        "Create Directory": "Criar diretório",
        "Create SyncIgnore File": "Criar ficheiro SyncIgnore",
        "Delete": "Eliminar",
        "Delete SyncIgnore File": "Eliminar ficheiro SyncIgnore",
        "Delete SyncPoint File": "Eliminar ficheiro SyncPoint",
        "Disconnect": "Desligar",
        "RS232 ESP emulation is already running on port {port} for another emulator; this MAME joins it. The new port applies once every MAME has exited.":
            "A emulação RS232 ESP já está a correr na porta {port} para outro emulador; este MAME junta-se a ela. A nova porta aplica-se assim que todos os MAME tiverem saído.",
        "RS232 ESP emulation: {count} emulators are now sharing it (port {port}).":
            "Emulação RS232 ESP: {count} emuladores estão agora a partilhá-la (porta {port}).",
        "RS232 ESP emulation could not start (port {port} in use?). MAME starts without it.":
            "A emulação RS232 ESP não pôde iniciar (porta {port} em uso?). O MAME inicia sem ela.",
        "RS232 ESP emulation inspired by jesperl - by Janko Stamenović":
            "Emulação RS232 ESP inspirada em jesperl - por Janko Stamenović",
        "The optional RS232 ESP Emulation for MAME (Settings) is a clean full reimplementation in Python of an idea from jesperl by Janko Stamenović - an ESP-AT emulator bridging MAME's emulated Wi-Fi module to the real network. Many thanks for the inspirational idea - see https://sourceforge.net/projects/jesperl/.":
            "A Emulação RS232 ESP opcional para o MAME (Definições) é uma reimplementação completa e limpa em Python de uma ideia do jesperl, de Janko Stamenović: um emulador ESP-AT que liga o módulo Wi-Fi emulado do MAME à rede real. Muito obrigado pela ideia inspiradora - ver https://sourceforge.net/projects/jesperl/.",
        "One RS232 ESP emulation serves every running MAME: launch a second MAME on another disk image and it joins the same emulation with its own separate session, so several emulated Nexts can be on the network at once. The emulation stops when the last MAME exits. When two of them ask for the same server port (a Next listening for incoming connections), the second one is moved to the next free port and the log says which port to connect to.":
            "Uma única emulação RS232 ESP serve todos os MAME em execução: inicie um segundo MAME com outra imagem de disco e ele junta-se à mesma emulação com a sua própria sessão separada, de modo que vários Next emulados podem estar na rede ao mesmo tempo. A emulação para quando o último MAME sai. Quando dois deles pedem a mesma porta de servidor (um Next à escuta de ligações de entrada), o segundo é movido para a porta livre seguinte e o registo indica a que porta ligar.",
        "Transfers through the RS232 ESP emulation need the Next side on its SLOW pacing: use '.sync5 -s' for the dot, or set UART speed to Slow in ZX Next Remote's settings.":
            "As transferências através da emulação RS232 ESP precisam do lado Next no ritmo LENTO: usa '.sync5 -s' no dot, ou define a velocidade UART como Slow nas definições do ZX Next Remote.",
        "Start {emulator}": "Iniciar {emulator}",
        "Color:": "Cor:",
        "Pick a color for this Next. It tints the machine list and this machine's tab in the session strip.":
            "Escolhe uma cor para este Next. Colore a lista de máquinas e o separador desta máquina na barra de sessões.",
        "Clear the color": "Remover a cor",
        "Switch to this Next": "Mudar para este Next",
        "Name and color…": "Nome e cor…",
        "That Next is no longer on the line.":
            "Esse Next já não está na linha.",
        "Tell this Next to leave listen mode and exit? ZX Next Remote closes its application; a '.sync5' dot returns to BASIC. The server keeps listening, so it can connect again.":
            "Pedir a este Next para sair do modo de escuta e terminar? O ZX Next Remote fecha a sua aplicação; um ponto '.sync5' volta ao BASIC. O servidor continua à escuta, por isso pode ligar-se de novo.",
        "Asked the Next to leave listen mode and exit.":
            "Pedido ao Next para sair do modo de escuta e terminar.",
        "Remote .sync5 update": "Atualização remota do .sync5",
        "Update .sync5 on this Next ({old} → {new})…":
            "Atualizar o .sync5 neste Next ({old} → {new})…",
        "Push new .sync5 to this Next…":
            "Enviar o novo .sync5 para este Next…",
        ".sync5 version unknown — switch to this Next first":
            "Versão do .sync5 desconhecida — muda primeiro para este Next",
        ".sync5 v{old} predates self-update — copy the new dot to the Next by hand once":
            ".sync5 v{old} é anterior à autoatualização — copia o novo dot para o Next à mão uma vez",
        "Locating the .sync5 build to send…":
            "A localizar o .sync5 a enviar…",
        "Still locating the .sync5 build to send — one moment.":
            "Ainda a localizar o .sync5 a enviar — um momento.",
        "Could not obtain the .sync5 build to send: {reason}":
            "Não foi possível obter o .sync5 a enviar: {reason}",
        ("Update .sync5 on {machine}: v{old} → v{new}.\n\nThe new dot is "
         "staged on the Next's SD card, read back and verified, then "
         "swapped in; the previous dot is kept as sync5.bak (renaming it "
         "back to sync5 is the one-step recovery). The session ends when "
         "the update completes — run {command} on the Next again "
         "afterwards.\n\nTarget directory on the Next:"):
            ("Atualizar o .sync5 em {machine}: v{old} → v{new}.\n\nO novo "
             "dot é preparado no cartão SD do Next, relido e verificado, "
             "e depois substituído; o dot anterior fica guardado como "
             "sync5.bak (renomeá-lo de volta para sync5 é a recuperação "
             "num passo). A sessão termina quando a atualização se "
             "completa — executa {command} de novo no Next "
             "depois.\n\nDiretório de destino no Next:"),
        ("Push the new .sync5 (v{new}) to {machine}?\n\nThis machine's "
         "version is unknown (an older dot, or an old ZX Next Remote "
         "build — the two cannot be told apart), and the swap itself "
         "only works when the far side is a .sync dot v5.9 or newer: on "
         "anything older the staged sync5.new is left on the card and "
         "nothing is swapped. The previous dot is kept as sync5.bak "
         "(renaming it back to sync5 is the one-step recovery). The "
         "session ends when the update completes — run {command} on the "
         "Next again afterwards.\n\nTarget directory on the Next:"):
            ("Enviar o novo .sync5 (v{new}) para {machine}?\n\nA versão "
             "desta máquina é desconhecida (um dot antigo, ou uma versão "
             "antiga do ZX Next Remote — não é possível distingui-los), "
             "e a própria substituição só funciona quando o outro lado é "
             "um dot .sync v5.9 ou mais recente: com algo mais antigo o "
             "sync5.new preparado fica no cartão e nada é substituído. O "
             "dot anterior fica guardado como sync5.bak (renomeá-lo de "
             "volta para sync5 é a recuperação num passo). A sessão "
             "termina quando a atualização se completa — executa "
             "{command} de novo no Next depois.\n\nDiretório de destino "
             "no Next:"),
        "Download File": "Transferir ficheiro",
        "Download NextZXOS Image": "Transferir imagem NextZXOS",
        "Download and install HDF Monkey": "Transferir e instalar o HDF Monkey",
        "Download and install HDF Monkey and OpenAL": "Transferir e instalar o HDF Monkey e o OpenAL",
        "Generate": "Gerar",
        "Get API key…": "Obter chave API…",
        "Latest": "Novidades",
        "New Folder": "Nova pasta",
        "Next >": "Seg. >",
        "OK": "OK",
        "Open config file": "Abrir ficheiro de configuração",
        "Prepare Classic NextSync server": "Preparar servidor NextSync clássico",
        "Random": "Aleatório",
        "Refresh": "Atualizar",
        "Rename": "Mudar o nome",
        "Search": "Pesquisar",
        "Select NextZXOS disk Image": "Selecionar imagem de disco NextZXOS",
        "Set current folder as new sync root folder":
            "Usar a pasta atual como nova raiz de sincronização",
        "Up": "Subir",
        "▶ Start Classic NextSync server": "▶ Iniciar servidor NextSync clássico",
        "▶ Start Remote Explorer NextSync server":
            "▶ Iniciar servidor NextSync do Explorador remoto",
        "⬇  Download": "⬇  Transferir",
        "⬇  Install MAME": "⬇  Instalar o MAME",
        "🎮 Retro": "🎮 Retro",
        "💾  Send to SD card": "💾  Enviar para o cartão SD",
        "🔁  Send via NextSync": "🔁  Enviar via NextSync",
        "🕹  Launch CSpect": "🕹  Iniciar o CSpect",
        "🕹  Launch Mame": "🕹  Iniciar o Mame",
        "🖼 Switch to 'Classic' view mode": "🖼 Mudar para vista 'Clássica'",
        # ---- emulator option combos (SD Card tab; selected by index) ----
        "Screen Size X1": "Tamanho do ecrã X1",
        "Screen Size X2": "Tamanho do ecrã X2",
        "Screen Size X3": "Tamanho do ecrã X3",
        "Screen Size X4": "Tamanho do ecrã X4",
        "Fullscreen": "Ecrã inteiro",
        "Sound On": "Som ligado",
        "Sound Off": "Som desligado",
        "Sound WASAPI": "Som WASAPI",
        "Sound XAudio2": "Som XAudio2",
        "Sound PortAudio": "Som PortAudio",
        "VSync On": "VSync ligado",
        "VSync Off": "VSync desligado",
        "Joystick On": "Joystick ligado",
        "Joystick Off": "Joystick desligado",
        "Mouse On": "Rato ligado",
        "Mouse Off": "Rato desligado",
        "Disable ESC Key Off": "Desativar tecla ESC: não",
        "Disable ESC Key On": "Desativar tecla ESC: sim",
        "ZX Next Unite update check: could not parse the versions (latest tag {tag}); skipping.":
            "Verificação do ZX Next Unite: não foi possível interpretar as versões (etiqueta mais recente {tag}); a ignorar.",
        "ZX Next Unite {latest} is available, but the release has no package for this platform — opening the releases page instead.":
            "O ZX Next Unite {latest} está disponível, mas a versão não inclui um pacote para esta plataforma; será aberta a página de versões.",
        # ---- long guidance prompts (final) ----
        "A newer version of CSpect is available on itch.io.\n\nInstalled: {installed}\nLatest: {latest}\n\nDownload and install the newest version now?":
            "Existe uma versão mais recente do CSpect no itch.io.\n\nInstalada: {installed}\nMais recente: {latest}\n\nTransferir e instalar agora a mais recente?",
        "CSpect update ▸ SUCCESS — {name} extracted to: {path}":
            "Atualização do CSpect ▸ CONCLUÍDA — {name} extraído para: {path}",
        "CSpect update ▸ Starting download + install of {name} ({file}) from itch.io into {folder}.":
            "Atualização do CSpect ▸ a iniciar transferência e instalação de {name} ({file}) do itch.io para {folder}.",
        "ERROR: could not build {name}: {error}":
            "ERRO: não foi possível criar {name}: {error}",
        "MAME can't start: the ZX Spectrum Next boot ROM (TBBLUE) is missing. This step is manual — see {url} and follow \"Get TBBLUE (the Next 'boot ROM')\". Put the file tbblue.zip into MAME's roms folder (downloads\\mame\\roms) — DON'T extract it — and try again. You must provide a legally acquired, licensed ROM.":
            "O MAME não consegue arrancar: falta a ROM de arranque do ZX Spectrum Next (TBBLUE). Este passo é manual — consulta {url} e segue \"Get TBBLUE (the Next 'boot ROM')\". Coloca o ficheiro tbblue.zip na pasta roms do MAME (downloads\\mame\\roms) — NÃO o extraias — e tenta de novo. Tens de usar uma ROM adquirida legalmente e licenciada.",
        "MAME install ▸ NEXT STEP (manual): add the TBBLUE boot ROM. See {url} → \"Get TBBLUE (the Next 'boot ROM')\". Put the file tbblue.zip into MAME's roms folder ({roms}) — DON'T extract it. You must provide a legally acquired, licensed ROM.":
            "Instalação do MAME ▸ PASSO SEGUINTE (manual): adiciona a ROM de arranque TBBLUE. Consulta {url} → \"Get TBBLUE (the Next 'boot ROM')\". Coloca o ficheiro tbblue.zip na pasta roms do MAME ({roms}) — NÃO o extraias. Tens de usar uma ROM adquirida legalmente e licenciada.",
        "NextSync .sync5 dot command updated: v{old} -> v{new} — please copy the new build to your Next (it cannot be deployed automatically).":
            "Comando dot .sync5 do NextSync atualizado: v{old} -> v{new} — copia a nova versão para o teu Next (não pode ser implantada automaticamente).",
        "TIP: Did you know that if you have purchased CSpect from itch.io you can do a full end-to-end CSpect install from there?\n\nCSpect ships with hdfmonkey bundled inside it, so that route needs no separate hdfmonkey install — the app finds and uses the bundled copy automatically.\n\nSimply log into your itch.io account in the itch.io tab, navigate to CSpect and click Install.\n\nDo you still want to install hdfmonkey only, or abort and then make an end-to-end install of CSpect using itch.io?":
            "DICA: sabias que se compraste o CSpect no itch.io podes fazer uma instalação completa do CSpect a partir daí?\n\nO CSpect traz o hdfmonkey incluído, por isso essa via não precisa de uma instalação separada do hdfmonkey — a aplicação encontra e usa a cópia incluída automaticamente.\n\nInicia sessão na tua conta itch.io no separador itch.io, vai a CSpect e clica em Instalar.\n\nAinda queres instalar apenas o hdfmonkey, ou preferes cancelar e fazer a instalação completa do CSpect com o itch.io?",
        "The automatic hdfmonkey download from specnext.com failed — the forum may be asking for a login or an anti-robot confirmation before the download can start (see the log for details).\n\nYou can install it manually instead:\n1. Click 'Open download page' below (or browse to\n    {url} ).\n2. Download the hdfmonkey .zip file.\n3. Drop the downloaded .zip into this EXACT folder — the app has already created it, and the 'Open downloads folder' button below opens it so nothing needs to be typed:\n    {folder}\n4. Click \"I've dropped the zip - try again\".":
            "A transferência automática do hdfmonkey a partir de specnext.com falhou — o fórum pode estar a pedir início de sessão ou uma confirmação anti-robô antes de permitir a transferência (vê o registo para detalhes).\n\nPodes instalá-lo manualmente:\n1. Clica em 'Abrir a página de transferência' abaixo (ou abre\n    {url} ).\n2. Transfere o ficheiro .zip do hdfmonkey.\n3. Coloca o .zip transferido EXATAMENTE nesta pasta — a aplicação já a criou, e o botão 'Abrir a pasta de transferências' abaixo abre-a para não teres de escrever nada:\n    {folder}\n4. Clica em \"Já coloquei o zip - tenta de novo\".",
        "ZX Next Unite update: downloaded {name} to {folder}":
            "Atualização do ZX Next Unite: {name} transferido para {folder}",
        "ZX Next Unite update: downloaded {path} but could not unpack it: {error}":
            "Atualização do ZX Next Unite: {path} foi transferido mas não foi possível descompactá-lo: {error}",
        "ZX Next Unite {latest} is available (you are running {installed}).\n\nYou appear to be running from source (git clone), so the\nrecommended way to update is:\n\n    git pull\n\ninstead of downloading the Windows binary.":
            "O ZX Next Unite {latest} está disponível (estás a usar {installed}).\n\nParece que o executas a partir do código-fonte (git clone), por isso a\nforma recomendada de atualizar é:\n\n    git pull\n\nem vez de transferir o binário de Windows.",
        "ZX Next Unite {latest} is available — download?\n\nInstalled: {installed}\nLatest: {latest}\nPackage: {asset} (~{size})\n\nThe new version is saved next to the current one — you choose\nwhen to switch (you'll be offered a restart after the download).":
            "O ZX Next Unite {latest} está disponível — transferir?\n\nInstalada: {installed}\nMais recente: {latest}\nPacote: {asset} (~{size})\n\nA nova versão é guardada junto da atual — escolhes\nquando mudar (será oferecido um reinício após a transferência).",
        "ZX Next Unite {latest} is available — running from source, so update with 'git pull' instead of the Windows binary.":
            "O ZX Next Unite {latest} está disponível; como corre a partir do código-fonte, atualiza com 'git pull' em vez do binário de Windows.",
        # ---- emulator update prompts (bodies + buttons) ----
        "A newer version of MAME is available.\n\nInstalled: 0.{installed}\nLatest: {latest}  (0.{latest_num})\nPackage: {asset}\n\nDownload (~{size}) and update your MAME install now?\nThe existing files in the downloads MAME folder will be overwritten.":
            "Existe uma versão mais recente do MAME.\n\nInstalada: 0.{installed}\nMais recente: {latest}  (0.{latest_num})\nPacote: {asset}\n\nTransferir (~{size}) e atualizar a instalação do MAME agora?\nOs ficheiros existentes na pasta MAME serão substituídos.",
        "Close and start {name}":
            "Fechar e iniciar {name}",
        "Continue hdfmonkey standalone install":
            "Continuar a instalação autónoma do hdfmonkey",
        "I've dropped the zip - try again":
            "Já coloquei o zip - tenta de novo",
        "MAME release: {tag}\nPackage: {asset} ({arch})\n\nDownload (~{size}) and install it into the downloads folder?\nNote: the fully extracted install is large (~500 MB).":
            "Versão do MAME: {tag}\nPacote: {asset} ({arch})\n\nTransferir (~{size}) e instalar na pasta de transferências?\nNota: a instalação extraída é grande (~500 MB).",
        "Open download page":
            "Abrir a página de transferência",
        "Open downloads folder":
            "Abrir a pasta de transferências",
        "The new version was saved as:\n\n{path}\n\nClose ZX Next Unite now and start the new version ({name})?\nYour settings (hdfg.cfg) and downloads are picked up as-is —\nboth versions run from the same folder.":
            "A nova versão foi guardada como:\n\n{path}\n\nFechar o ZX Next Unite agora e iniciar a nova versão ({name})?\nAs tuas definições (hdfg.cfg) e transferências são reutilizadas tal como estão —\nambas as versões correm a partir da mesma pasta.",
        "What's changed:":
            "Novidades:",
        # ---- emulator / config console (final batch) ----
        "CSpect update check: {reason}.":
            "Verificação do CSpect: {reason}.",
        "CSpect update ▸ FAILED — {error}":
            "Atualização do CSpect ▸ FALHOU — {error}",
        "CSpect update ▸ newer build available: installed {installed}, latest {latest}.":
            "Atualização do CSpect ▸ existe uma versão mais recente: instalada {installed}, mais recente {latest}.",
        "CSpect update ▸ user chose to update to {name}.":
            "Atualização do CSpect ▸ o utilizador escolheu atualizar para {name}.",
        "Could not list the MAME releases: {error}":
            "Não foi possível listar as versões do MAME: {error}",
        "ERROR: Failed to launch MAME: {error}":
            "ERRO: não foi possível iniciar o MAME: {error}",
        "ERROR: Failed to launch CSpect: {error}":
            "ERRO: Não foi possível iniciar o CSpect: {error}",
        "ERROR: could not extract {name}: {error}":
            "ERRO: não foi possível extrair {name}: {error}",
        "ERROR: hdfmonkey failed - A file can't be opened this is commonly caused by strange characters such as quotes and signs":
            "ERRO: o hdfmonkey falhou - não é possível abrir um ficheiro; normalmente deve-se a caracteres estranhos como aspas e sinais",
        "ERROR: hdfmonkey failed - A file can't be opened: {command} this is commonly caused by strange characters such as quotes and signs":
            "ERRO: o hdfmonkey falhou - não é possível abrir um ficheiro: {command}; normalmente deve-se a caracteres estranhos como aspas e sinais",
        "Failed to save configuration file with IOError: {error}":
            "Falha ao guardar o ficheiro de configuração (IOError): {error}",
        "Found hdfmonkey alongside CSpect: {path}":
            "hdfmonkey encontrado junto ao CSpect: {path}",
        "MAME exited with code {code}.":
            "O MAME terminou com o código {code}.",
        "MAME install ▸ SUCCESS — MAME detected at: {path}":
            "Instalação do MAME ▸ CONCLUÍDA — MAME detetado em: {path}",
        "Pygame mode unavailable — run: pip install pygame-ce":
            "Modo pygame indisponível — executa: pip install pygame-ce",
        "Remote unzip: fetching {path} from the image …":
            "Descompressão remota: a obter {path} da imagem …",
        "Remote zip: fetching {count} item(s) from the image …":
            "Compressão remota: a obter {count} item(ns) da imagem …",
        "Saved configuration file.":
            "Ficheiro de configuração guardado.",
        "UI language set to '{lang}' to match the system language — change it on the Settings tab.":
            "Idioma da interface definido para '{lang}' para corresponder ao do sistema; podes alterá-lo no separador Definições.",
        "ZX Next Unite update available: {latest} (installed {installed}).":
            "Atualização do ZX Next Unite disponível: {latest} (instalada {installed}).",
        "ZX Next Unite update ▸ downloading {asset}…":
            "Atualização do ZX Next Unite ▸ a transferir {asset}…",
        "ZX Next Unite update: could not start {name}: {error}":
            "Atualização do ZX Next Unite: não foi possível iniciar {name}: {error}",
        "ZX Next Unite update: download FAILED: {error}":
            "Atualização do ZX Next Unite: a transferência FALHOU: {error}",
        "ZX Next Unite update: downloaded — start it any time: {path}":
            "Atualização do ZX Next Unite: transferida; podes iniciá-la quando quiseres: {path}",
        "ZX Next Unite update: starting {name} and closing…":
            "Atualização do ZX Next Unite: a iniciar {name} e a fechar…",
        "ZX Next Unite update: unpacked to {path}":
            "Atualização do ZX Next Unite: descompactada em {path}",
        "Extracting {name} from the image, then starting CSpect…":
            "A extrair {name} da imagem e depois a iniciar o CSpect…",
        "Start CSpect: {name} could not be read from the image, CSpect was not started.":
            "Iniciar o CSpect: não foi possível ler {name} da imagem; o CSpect não foi iniciado.",
        "Extracting {name} from the image, then sending it via NextSync…":
            "A extrair {name} da imagem e depois a enviá-lo via NextSync…",
        "Send via NextSync: {name} could not be read from the image, nothing was sent.":
            "Enviar via NextSync: não foi possível ler {name} da imagem; nada foi enviado.",
        "Send via NextSync {name}":
            "Enviar {name} via NextSync",
        "Start NextSync Remote Explorer":
            "Iniciar o Remote Explorer do NextSync",
        "Stop NextSync Remote Explorer":
            "Parar o Remote Explorer do NextSync",
        "Please select a sync root first on the NextSync Remote Explorer tab and retry.":
            "Escolhe primeiro uma pasta raiz de sincronização no separador Remote Explorer do NextSync e tenta novamente.",
        # ---- CSpect auto-start actions (SD Card tab menus) ----
        "Send to SD Card and start CSpect with file {name}":
            "Enviar para o cartão SD e iniciar o CSpect com o ficheiro {name}",
        "Send to SD Card and start CSpect: the transfer failed, CSpect was not started.":
            "Enviar para o cartão SD e iniciar o CSpect: a transferência falhou; o CSpect não foi iniciado.",
        "Sending {name} to the SD card image, then starting CSpect…":
            "A enviar {name} para a imagem do cartão SD e depois a iniciar o CSpect…",
        "Start CSpect with file {name}":
            "Iniciar o CSpect com o ficheiro {name}",
        "Start MAME with file {name}":
            "Iniciar o MAME com o ficheiro {name}",
        "Could not start {emulator}":
            "Não foi possível iniciar o {emulator}",
        "Could not prepare a folder for {name}: {error}":
            "Não foi possível preparar uma pasta para {name}: {error}",
        "Start {emulator}: {name} could not be downloaded from the Next, "
        "{emulator} was not started.":
            "Iniciar o {emulator}: não foi possível transferir {name} do Next; "
            "o {emulator} não foi iniciado.",
        "Downloading {name} from the Next, then starting {emulator}…":
            "A transferir {name} do Next e depois a iniciar o {emulator}…",
        "Downloading {name}…":
            "A transferir {name}…",
        "Start MAME: could not prepare the staging folder {path} ({error}).":
            "Iniciar o MAME: não foi possível preparar a pasta temporária {path} ({error}).",
        "Send to SD Card and start MAME with file {name}":
            "Enviar para o cartão SD e iniciar o MAME com o ficheiro {name}",
        "Extracting {name} from the image, then starting MAME…":
            "A extrair {name} da imagem e depois a iniciar o MAME…",
        "Start MAME: {name} could not be read from the image, MAME was not started.":
            "Iniciar o MAME: não foi possível ler {name} da imagem; o MAME não foi iniciado.",
        "Send to SD Card and start MAME: the transfer failed, MAME was not started.":
            "Enviar para o cartão SD e iniciar o MAME: a transferência falhou; o MAME não foi iniciado.",
        "Sending {name} to the SD card image, then starting MAME…":
            "A enviar {name} para a imagem do cartão SD e depois a iniciar o MAME…",
        "MAME cannot load {name} directly; starting MAME without it.":
            "O MAME não consegue carregar {name} diretamente; o MAME será iniciado sem esse ficheiro.",
        # ---- dialogs (message boxes) ----
        "CSpect update available":
            "Atualização do CSpect disponível",
        "Choose another release…":
            "Escolher outra versão…",
        "Close":
            "Fechar",
        "Download and install":
            "Transferir e instalar",
        "File or directory already exists locally.":
            "O ficheiro ou diretório já existe localmente.",
        "File or directory exists":
            "O ficheiro ou diretório já existe",
        "Ignore (always in this sync)":
            "Ignorar (sempre nesta sincronização)",
        "Ignore (one time)":
            "Ignorar (uma vez)",
        "Install from .zip…":
            "Instalar a partir de .zip…",
        "Install hdfmonkey":
            "Instalar o hdfmonkey",
        "Later":
            "Mais tarde",
        "MAME update available":
            "Atualização do MAME disponível",
        "Open itch.io page":
            "Abrir a página do itch.io",
        "Open the releases page":
            "Abrir a página de versões",
        "Overwrite local file (always in this sync)":
            "Substituir o ficheiro local (sempre nesta sincronização)",
        "Overwrite local file (one time)":
            "Substituir o ficheiro local (uma vez)",
        "The automated download failed.":
            "A transferência automática falhou.",
        "This is going to completely delete the files in {path} and its sub folders, so they will be unrecoverable.\n\nAre you sure want to continue?":
            "Isto vai eliminar completamente os ficheiros em {path} e nas suas subpastas, de forma irrecuperável.\n\nTens a certeza de que queres continuar?",
        "Tip: set a default for this in Settings → \"NextSync — when a sent file or directory exists locally\".":
            "Dica: define um valor predefinido em Definições → \"NextSync — when a sent file or directory exists locally\".",
        "Uninstall":
            "Desinstalar",
        "Update":
            "Atualizar",
        "Update downloaded":
            "Atualização transferida",
        "Yes":
            "Sim",
        "You can download it manually from the itch.io page in your browser, then install it from the downloaded .zip.":
            "Podes transferi-lo manualmente a partir da página do itch.io no teu navegador e depois instalá-lo a partir do .zip transferido.",
        "ZX Next Unite update available":
            "Atualização do ZX Next Unite disponível",
        "hdfmonkey download failed":
            "Falhou a transferência do hdfmonkey",
        "itch.io download":
            "Transferência do itch.io",
        # ---- emulator install / update console ----
        "CSpect update check skipped: {reason}":
            "Verificação do CSpect ignorada: {reason}",
        "CSpect update ▸ user cancelled the update.":
            "Atualização do CSpect ▸ o utilizador cancelou a atualização.",
        "ERROR: CSpect.exe is not present in the same local directory as zx-next-unite. Please install it from http://cspect.org":
            "ERRO: o CSpect.exe não está na mesma pasta local que o zx-next-unite. Instala-o a partir de http://cspect.org",
        "ERROR: MAME executable not found on PATH. Cannot launch MAME.":
            "ERRO: o executável do MAME não foi encontrado no PATH. Não é possível iniciar o MAME.",
        "Listing the available MAME releases…":
            "A listar as versões do MAME disponíveis…",
        "MAME install ▸ FAILED — the download and extraction finished, but no mame.exe could be found in downloads/mame.":
            "Instalação do MAME ▸ FALHOU — a transferência e extração terminaram, mas não se encontrou o mame.exe em downloads/mame.",
        "MAME install ▸ FAILED — {error}. You can download it manually from https://www.mamedev.org/release.html":
            "Instalação do MAME ▸ FALHOU — {error}. Podes transferi-lo manualmente a partir de https://www.mamedev.org/release.html",
        "MAME install ▸ Starting: {tag} ({asset}, ~{size}).":
            "Instalação do MAME ▸ a iniciar: {tag} ({asset}, ~{size}).",
        "MAME install ▸ release picker cancelled.":
            "Instalação do MAME ▸ seleção de versão cancelada.",
        "MAME is ready to launch now — no restart needed. Use the '🕹  Launch Mame' button.":
            "O MAME já pode ser iniciado, sem reiniciar. Usa o botão '🕹  Launch Mame'.",
        "MAME update check: could not determine the installed MAME version; skipping.":
            "Verificação do MAME: não foi possível determinar a versão instalada; a ignorar.",
        "MAME update check: could not determine the latest release; skipping.":
            "Verificação do MAME: não foi possível determinar a versão mais recente; a ignorar.",
        "MAME update check: could not reach the release site; skipping.":
            "Verificação do MAME: não foi possível aceder ao site das versões; a ignorar.",
        "MAME update ▸ user chose to pick a release manually.":
            "Atualização do MAME ▸ o utilizador escolheu selecionar uma versão manualmente.",
        "MAME update ▸ user chose to update to {tag}.":
            "Atualização do MAME ▸ o utilizador escolheu atualizar para {tag}.",
        "On MacOS and Linux mono is required as it runs under it. Please make sure mono is installed.":
            "No MacOS e no Linux é necessário o mono, pois é executado sobre ele. Certifica-te de que o mono está instalado.",
        "Running as a Flatpak: mono must be installed on the HOST system — the launch is delegated there via flatpak-spawn.":
            "A executar como Flatpak: o mono tem de estar instalado no sistema ANFITRIÃO; o arranque é delegado aí através do flatpak-spawn.",
        "Select a valid ZX Spectrum Next disk image (.img/.hdf) before launching MAME.":
            "Seleciona uma imagem de disco ZX Spectrum Next válida (.img/.hdf) antes de iniciar o MAME.",
        "ZX Next Unite update check: could not reach GitHub (offline, or no release published yet); skipping.":
            "Verificação do ZX Next Unite: não foi possível aceder ao GitHub (sem ligação ou sem versões publicadas); a ignorar.",
        "ZX Next Unite update check: running as a Flatpak — updates come from your software center, skipping.":
            "Verificação do ZX Next Unite: a executar como Flatpak; as atualizações vêm do teu centro de software, a ignorar.",
        "ZX Next Unite update ▸ skipped by user.":
            "Atualização do ZX Next Unite ▸ ignorada pelo utilizador.",
        "ZX Next Unite update: download cancelled.":
            "Atualização do ZX Next Unite: transferência cancelada.",
        # ---- SD Card tab: console + dialogs ----
        "Confirm Deletion":
            "Confirmar eliminação",
        "Create":
            "Criar",
        "Create New Folder":
            "Criar nova pasta",
        "Created {name} in {folder} on the image ({count} file(s), {bytes} bytes).":
            "Criado {name} em {folder} na imagem ({count} ficheiro(s), {bytes} bytes).",
        "Delete files from the image to free space, or switch to a larger image.\nLarger SD card images can be downloaded from:":
            "Elimina ficheiros da imagem para libertar espaço, ou muda para uma imagem maior.\nPodes transferir imagens de cartão SD maiores a partir de:",
        "Download":
            "Transferir",
        "Download failed: no valid destination folder.":
            "Falha na transferência: não há pasta de destino válida.",
        "Downloading {name} from {url}":
            "A transferir {name} de {url}",
        "ERROR: hdfmonkey could not be found. Use the 'Download and install HDF Monkey' button (bottom right of the SD Card tab) to install it automatically, or do a full CSpect install from the itch.io tab, which also bundles hdfmonkey. It can also be installed manually from https://github.com/gasman/hdfmonkey — restart the app once installed.":
            "ERRO: o hdfmonkey não foi encontrado. Usa o botão 'Transferir e instalar o HDF Monkey' (canto inferior direito do separador SD Card) para o instalar automaticamente, ou faz uma instalação completa do CSpect a partir do separador itch.io, que também inclui o hdfmonkey. Também pode ser instalado manualmente a partir de https://github.com/gasman/hdfmonkey — reinicia a aplicação depois de instalado.",
        "Extracted disk image: {path}":
            "Imagem de disco extraída: {path}",
        "Extracted {count} file(s) from {name} into {folder} on the image.":
            "Extraído(s) {count} ficheiro(s) de {name} para {folder} na imagem.",
        "Extracting image... %p%":
            "Extraindo imagem... %p%",
        "Failed downloading NextZXOS image: {error}":
            "Falha ao transferir a imagem NextZXOS: {error}",
        "Load Failed":
            "Falha ao carregar",
        "The image was extracted but could not be loaded:":
            "A imagem foi extraída mas não pôde ser carregada:",
        "Failed executing hdfmonkey, please make sure it is installed in the same local directory as zx-next-unite.":
            "Falha ao executar o hdfmonkey; certifica-te de que está instalado na mesma pasta local que o zx-next-unite.",
        "Failed extracting NextZXOS image: {error}":
            "Falha ao extrair a imagem NextZXOS: {error}",
        "Failed loading image: {path}.":
            "Falha ao carregar a imagem: {path}.",
        "No SD-card disk image selected — pick or create a .img/.hdf at the top of this tab to unlock the emulator Launch buttons.":
            "Nenhuma imagem de cartão SD selecionada: escolhe ou cria um .img/.hdf no topo deste separador para desbloquear os botões de arranque do emulador.",
        "Nothing to move: items are already in this folder.":
            "Nada para mover: os itens já estão nesta pasta.",
        "Only {free} MB free out of {total} MB ({used} % used, {pct} % free).":
            "Apenas {free} MB livres de {total} MB ({used} % usado, {pct} % livre).",
        "Please load an image file first !":
            "Carrega primeiro um ficheiro de imagem!",
        "Please load an image first!":
            "Carrega primeiro uma imagem!",
        "Please select an image file or folder first to delete!":
            "Seleciona primeiro um ficheiro ou pasta da imagem para eliminar!",
        "Please select an image file or folder first to rename!":
            "Seleciona primeiro um ficheiro ou pasta da imagem para renomear!",
        "Remote unzip cancelled — the image is unchanged.":
            "Descompressão remota cancelada: a imagem não foi alterada.",
        "Remote unzip: download from the image failed or was cancelled — the image is unchanged.":
            "Descompressão remota: a transferência a partir da imagem falhou ou foi cancelada; a imagem não foi alterada.",
        "Remote unzip: upload into the image failed or was cancelled.":
            "Descompressão remota: o envio para a imagem falhou ou foi cancelado.",
        "Remote zip cancelled — no zip was created.":
            "Compressão remota cancelada: não foi criado nenhum zip.",
        "Remote zip: download from the image failed or was cancelled — no zip was created.":
            "Compressão remota: a transferência a partir da imagem falhou ou foi cancelada; não foi criado nenhum zip.",
        "Remote zip: upload into the image failed or was cancelled.":
            "Compressão remota: o envio para a imagem falhou ou foi cancelado.",
        "SD Image Nearly Full":
            "Imagem SD quase cheia",
        "The SD card image is nearly full.":
            "A imagem do cartão SD está quase cheia.",
        "The hdfmonkey provided by the CSpect itch.io package is not executable. Make it executable by running:":
            "O hdfmonkey incluído no pacote CSpect do itch.io não é executável. Torna-o executável executando:",
        "The image is completely full ({total} MB capacity, 0 MB free).":
            "A imagem está completamente cheia (capacidade {total} MB, 0 MB livres).",
        # ---- SD Card console: banner, detection, update checks ----
        "CSpect - by Mike Dailly http://cspect.org":
            "CSpect - por Mike Dailly http://cspect.org",
        "CSpect is up to date (installed {installed}, latest {latest}).":
            "O CSpect está atualizado (instalada {installed}, mais recente {latest}).",
        "Checking for a newer MAME release…":
            "A procurar uma versão mais recente do MAME…",
        "Checking for a newer ZX Next Unite release on GitHub…":
            "A procurar no GitHub uma versão mais recente do ZX Next Unite…",
        "Checking itch.io for a newer CSpect release…":
            "A procurar no itch.io uma versão mais recente do CSpect…",
        "HDF Monkey - by Matt Westcott":
            "HDF Monkey - por Matt Westcott",
        "Inspired by HDFM-GOOEY - by em00k":
            "Inspirado em HDFM-GOOEY - por em00k",
        "Loaded configuration file.":
            "Ficheiro de configuração carregado.",
        "MAME - ZX Spectrum Next support by Holub https://wiki.specnext.dev/MAME:Installing":
            "MAME - suporte para ZX Spectrum Next por Holub https://wiki.specnext.dev/MAME:Installing",
        "MAME is up-to-date (installed 0.{installed}, latest 0.{latest}).":
            "O MAME está atualizado (instalada 0.{installed}, mais recente 0.{latest}).",
        "MAME is up-to-date with a patched version (installed 0.{installed}, latest 0.{latest}).":
            "O MAME está atualizado com uma versão modificada (instalada 0.{installed}, mais recente 0.{latest}).",
        "MAME version: {version}":
            "Versão do MAME: {version}",
        "NextSync - by Jari Komppa and Julien Clauzel":
            "NextSync - por Jari Komppa e Julien Clauzel",
        "Using CSpect under downloads/cspect: {path}":
            "A usar o CSpect em downloads/cspect: {path}",
        "Using MAME under: {path}":
            "A usar o MAME em: {path}",
        "Using hdfmonkey bundled with CSpect: {path}":
            "A usar o hdfmonkey incluído no CSpect: {path}",
        "Welcome to ZX Next Unite {version}":
            "Bem-vindo ao ZX Next Unite {version}",
        "ZX Next Unite is up to date (installed {installed}, latest {latest}).":
            "O ZX Next Unite está atualizado (instalada {installed}, mais recente {latest}).",
        "zx-next-unite - by Julien Clauzel 2024":
            "zx-next-unite - por Julien Clauzel 2024",
        "No image loaded": "Nenhuma imagem carregada",
        # ---- itch.io item viewer + web-link labels ----
        "About": "Sobre",
        "Open on {site}": "Abrir em {site}",
        "Open {url}": "Abrir {url}",
        "✓  Re-install": "✓  Reinstalar",
        "⬇  Install": "⬇  Instalar",
        "⬇  Installing…": "⬇  A instalar…",
        "📂  Open download folder": "📂  Abrir pasta de transferências",
        # ---- gallery item viewer (Classic + Retro) ----
        "🌐  Open on website": "🌐  Abrir no site",
        "🌐  Open on {site}": "🌐  Abrir em {site}",
        "📂  Open install folder": "📂  Abrir pasta de instalação",
        "🗑  Uninstall": "🗑  Desinstalar",
        # ---- NextSync log lines (user-facing; protocol diagnostics stay English) ----
        "(-send saves received files under: {folder})":
            "(-send guarda os ficheiros recebidos em: {folder})",
        "Aliases:":
            "Aliases:",
        "Cancel requested — stopping after current file":
            "Cancelamento pedido: para após o ficheiro atual",
        "Cannot create {path}: {error}":
            "Não é possível criar {path}: {error}",
        "Closing connection":
            "A fechar a ligação",
        "Connected by {address} port {port}":
            "Ligado a partir de {address} porta {port}",
        "Disconnected":
            "Desligado",
        "Existing-file policy: {policy} (change in Settings -> 'NextSync - when a sent file or directory exists locally').":
            "Política para ficheiros existentes: {policy} (altera em Definições -> 'NextSync - when a sent file or directory exists locally').",
        "Failed to rename {path}: {error}":
            "Não foi possível renomear {path}: {error}",
        "IP addresses:":
            "Endereços IP:",
        "Import failed: no valid destination folder.":
            "Falha na importação: não há pasta de destino válida.",
        "Navigate to a folder in the left local file explorer, press 'Set current folder as new sync root folder' to choose a sync root and then press the 'Start Classic NextSync server' button.":
            "Navega até uma pasta no explorador local esquerdo, prime 'Set current folder as new sync root folder' para escolher a raiz de sincronização e depois prime o botão 'Start Classic NextSync server'.",
        "NextSync HTTP bridge NOT started: {error}":
            "A ponte HTTP do NextSync NÃO foi iniciada: {error}",
        "NextSync HTTP bridge listening on port {port}":
            "Ponte HTTP do NextSync a escutar na porta {port}",
        "NextSync HTTP bridge stopped.":
            "Ponte HTTP do NextSync parada.",
        "NextSync HTTP bridge: bearer-token protection is ON (requests must carry the {header} header; others get HTTP 401)":
            "Ponte HTTP do NextSync: a proteção por token está ATIVA (os pedidos têm de incluir o cabeçalho {header}; os restantes recebem HTTP 401)",
        "NextSync is already running — please wait for it to finish.":
            "O NextSync já está em execução: aguarda que termine.",
        "NextSync listening to port {port}":
            "NextSync a escutar na porta {port}",
        "NextSync server, protocol version: {version}":
            "Servidor NextSync, versão do protocolo: {version}",
        "No network detected - connect to Wi-Fi/Ethernet to see the address your Next should sync to.":
            "Nenhuma rede detetada: liga-te a Wi-Fi/Ethernet para veres o endereço com que o teu Next deve sincronizar.",
        "Note":
            "Nota",
        "Nothing (more) to sync":
            "Não há (mais) nada para sincronizar",
        "Now run one of these commands on your Next:":
            "Executa agora um destes comandos no teu Next:",
        "Primary IP:":
            "IP principal:",
        "Received {name} ({bytes} bytes)":
            "Recebido {name} ({bytes} bytes)",
        "Receiving files from the Next...":
            "A receber ficheiros do Next...",
        "Receiving: {name} -> {path}":
            "A receber: {name} -> {path}",
        "Remote .sync5 update failed while reading {path}: {error} — nothing was sent.":
            "A atualização remota do .sync5 falhou ao ler {path}: {error} — nada foi enviado.",
        "Remote .sync5 update refused: {path} does not carry the expected 'NextSync {version}' banner — wrong or stale file.":
            "Atualização remota do .sync5 recusada: {path} não contém o distintivo 'NextSync {version}' esperado — ficheiro errado ou desatualizado.",
        "Remote .sync5 update: staging {path} ({size} bytes)…":
            "Atualização remota do .sync5: a preparar {path} ({size} bytes)…",
        "Remote .sync5 update: staged copy verified ({size} bytes) — swapping it in…":
            "Atualização remota do .sync5: cópia preparada verificada ({size} bytes) — a substituí-la…",
        "Remote .sync5 update FAILED mid-swap: the Next may be missing {target}. If .sync5 no longer starts, rename {backup} back to sync5 in the NextZXOS Browser (the staged {staged} can be deleted).":
            "A atualização remota do .sync5 FALHOU a meio da troca: o Next pode não ter {target}. Se o .sync5 já não arrancar, renomeia {backup} de volta para sync5 no Browser do NextZXOS (o {staged} preparado pode ser apagado).",
        "Remote .sync5 update complete: {version} is on the card. The session will now close — run {command} on the Next to start the new dot.":
            "Atualização remota do .sync5 concluída: {version} já está no cartão. A sessão vai agora fechar — executa {command} no Next para iniciar o novo dot.",
        "Remote .sync5 update failed: {reason}. Nothing was swapped — the Next still runs its current dot.":
            "A atualização remota do .sync5 falhou: {reason}. Nada foi trocado — o Next continua a executar o seu dot atual.",
        "Remote explorer: connected to {address}":
            "Explorador remoto: ligado a {address}",
        "Remote explorer: connection error from the Next ({error}) — session over.":
            "Explorador remoto: erro de ligação com o Next ({error}); sessão terminada.",
        "Remote explorer: the Next closed the connection.":
            "Explorador remoto: o Next fechou a ligação.",
        "Remote explorer: no word from the Next for {seconds}s — assuming it is gone (powered off? Wi-Fi dropped?)":
            "Explorador remoto: sem resposta do Next há {seconds}s: assume-se que desapareceu (desligado? Wi-Fi caiu?)",
        "Remote explorer: turned away a second Next at {address} — a session is already active (Busy).":
            "Explorador remoto: recusou-se um segundo Next em {address}: já há uma sessão ativa (Busy).",
        "Remote explorer: server keeps running in the background — stop it from the Remote Explorer view.":
            "Explorador remoto: o servidor continua a correr em segundo plano; pára-o na vista Remote Explorer.",
        "Remote explorer: navigate to a folder in the left file explorer, press 'Set current folder as new sync root folder', click 'Start Remote Explorer NextSync server', then run {command} on your Next.":
            "Explorador remoto: navega até uma pasta no explorador esquerdo, prime 'Set current folder as new sync root folder', clica em 'Start Remote Explorer NextSync server' e depois executa {command} no teu Next.",
        "Remote explorer: port {port} is already in use — is another ZX-Next-Unite (or NextSync server) already running?":
            "Explorador remoto: a porta {port} já está em uso — está outro ZX-Next-Unite (ou servidor NextSync) em execução?",
        "Remote explorer: the Next disconnected (BREAK / Bye) — restarting the listen server; run {command} on your Next to reconnect.":
            "Explorador remoto: o Next desligou-se (BREAK / Bye); a reiniciar o servidor de escuta. Executa {command} no teu Next para voltar a ligar.",
        "Remote explorer: the Next disconnected (BREAK / Bye). Press 'Start Remote Explorer NextSync server' to accept a new connection.":
            "Explorador remoto: o Next desligou-se (BREAK / Bye). Prime 'Start Remote Explorer NextSync server' para aceitar uma nova ligação.",
        "Remote explorer: waiting for {command} on port {port}…":
            "Explorador remoto: à espera de {command} na porta {port}…",
        "Renamed: {old} -> {new}":
            "Renomeado: {old} -> {new}",
        "Running on host:":
            "A executar no anfitrião:",
        "Saving incoming files under: {folder}":
            "A guardar os ficheiros recebidos em: {folder}",
        "Send via NextSync: nothing to send in {folder}.":
            "Enviar por NextSync: não há nada para enviar em {folder}.",
        "Sending {folder} via Remote Explorer (-listen) → {target} …":
            "A enviar {folder} pelo Explorador remoto (-listen) → {target} …",
        "Set a sync root folder first: navigate to the folder in the left local file explorer and press 'Set current folder as new sync root folder'.":
            "Escolhe primeiro uma pasta raiz de sincronização: navega até ela no explorador local esquerdo e prime 'Set current folder as new sync root folder'.",
        "Skipped (already exists): {path}":
            "Ignorado (já existe): {path}",
        "Skipped {path}: cannot import a folder into itself.":
            "Ignorado {path}: não é possível importar uma pasta para dentro de si própria.",
        "Stop the running sync before starting the remote server.":
            "Para a sincronização em curso antes de iniciar o servidor remoto.",
        "Sync file list has {count} files.":
            "A lista de sincronização tem {count} ficheiros.",
        "Sync point file {name} not found, syncing all files regardless of timestamp.":
            "O ficheiro de ponto de sincronização {name} não foi encontrado; serão sincronizados todos os ficheiros independentemente da data.",
        "Sync point updated with {count} received file(s)":
            "Ponto de sincronização atualizado com {count} ficheiro(s) recebido(s)",
        "Upload connection closed":
            "Ligação de envio fechada",
        "Upload finished, {count} file(s) received":
            "Envio concluído, {count} ficheiro(s) recebido(s)",
        "Using {folder} as sync root":
            "A usar {folder} como raiz de sincronização",
        "WARNING":
            "AVISO",
        "Warning":
            "Aviso",
        "Warning! Ignore file {name} not found in directory. All files will be synced, possibly including this file.":
            "Atenção! O ficheiro de exclusões {name} não foi encontrado na pasta. Todos os ficheiros serão sincronizados, possivelmente incluindo este.",
        "{kb} kilobytes payload, {rate} kBps effective speed":
            "{kb} kilobytes de dados úteis, velocidade efetiva {rate} kBps",
        "{kb} kilobytes transferred in {seconds} seconds, {rate} kBps":
            "{kb} kilobytes transferidos em {seconds} segundos, {rate} kBps",
        "{severity}: Ready to sync {count} files, {kb} kilobytes.":
            "{severity}: pronto para sincronizar {count} ficheiros, {kb} kilobytes.",
        # ---- checkboxes ----
        "Alien Floyd's — animated background on all tabs (Retro/pygame)":
            "Alien Floyd's — fundo animado em todos os separadores (Retro/pygame)",
        "Alien Floyd's — show the full-window 'Alien Floyd's' tab (Retro/pygame)":
            "Alien Floyd's — mostrar o separador «Alien Floyd's» em janela completa (Retro/pygame)",
        "Check for CSpect update on itch.io on startup":
            "Procurar atualizações do CSpect no itch.io ao arrancar",
        "Check for ZX Next Unite updates at startup on Github":
            "Procurar atualizações do ZX Next Unite no GitHub ao arrancar",
        "Check for a newer MAME version at startup":
            "Procurar uma versão mais recente do MAME ao arrancar",
        "Disable 'No emulators detected' message at startup":
            "Desativar o aviso «Nenhum emulador detetado» ao arrancar",
        "Do not prompt for confirmation on deletion.":
            "Não pedir confirmação ao eliminar.",
        "Enable NextSync HTTP bridge (web server for the Next's .http command)":
            "Ativar a ponte HTTP do NextSync (servidor web para o comando .http do Next)",
        "Enable crash log file generation":
            "Ativar a geração do registo de falhas",
        "Enable multi API endpoints search (GetIt, ZXDB & zxArt search together).":
            "Ativar a pesquisa multi-API (GetIt, ZXDB e zxArt em conjunto).",
        "Enable search autocompletion.": "Ativar a conclusão automática da pesquisa.",
        "NextSync — starfield log animation (Retro/pygame mode)":
            "NextSync — animação de estrelas no registo (modo Retro/pygame)",
        "Perform pre-availability check on Downloads (ZXDB & zxArt).":
            "Verificar a disponibilidade das transferências (ZXDB e zxArt).",
        "Require bearer token": "Exigir token de acesso",
        "SD Card - Warn when an image is nearly full.":
            "Cartão SD — Avisar quando uma imagem estiver quase cheia.",
        "Send deleted files to the Recycle Bin (local file explorers).":
            "Enviar os ficheiros eliminados para a Reciclagem (exploradores locais).",
        "Show the itch.io tab (browse & install your itch.io collections)":
            "Mostrar o separador itch.io (explorar e instalar as suas coleções)",
        "Slow transfer": "Transferência lenta",
        "Unite! — Invaders background animation (Retro/pygame mode)":
            "Unite! — animação de fundo Invaders (modo Retro/pygame)",
        # ---- radio buttons / group boxes ----
        "Always sync (send everything)": "Sincronizar sempre (enviar tudo)",
        "Sync changed files (continuous)": "Sincronizar alterações (contínuo)",
        "Sync once": "Sincronizar uma vez",
        "Sync mode": "Modo de sincronização",
        # ---- placeholders ----
        "Filter by name, type or size...": "Filtrar por nome, tipo ou tamanho…",
        "Filter by name...": "Filtrar por nome…",
        "Local folder path...": "Caminho da pasta local…",
        "New directory name ...": "Nome do novo diretório…",
        "Paste your personal API key (itch.io → Settings → API keys)":
            "Cole a sua chave API pessoal (itch.io → Settings → API keys)",
        "Path inside the SD card image...": "Caminho dentro da imagem SD…",
        "SD card image path...": "Caminho da imagem do cartão SD…",
        "Search ZXDB games... (leave empty for random selection)":
            "Pesquisar jogos no ZXDB… (vazio = seleção aleatória)",
        "Search across GetIt + ZXDB + zxArt...":
            "Pesquisar em GetIt + ZXDB + zxArt…",
        "Search files... (leave empty for latest 20)":
            "Pesquisar ficheiros… (vazio = últimos 20)",
        "Search your itch.io library (collections + purchases)…":
            "Pesquisar na sua biblioteca itch.io (coleções e compras)…",
        "Search zxART productions... (leave empty to browse latest)":
            "Pesquisar produções no zxART… (vazio = ver novidades)",
        "Sync root folder...": "Pasta raiz de sincronização…",
        "bearer token (generated when you enable the checkbox)":
            "token de acesso (gerado ao ativar a opção)",
        # ---- short tooltips ----
        "Browse mode": "Modo de navegação",
        "Search mode": "Modo de pesquisa",
        "Pick a letter": "Escolha uma letra",
        "Double-click to enlarge": "Duplo clique para ampliar",
        "Double-click to open full view": "Duplo clique para abrir a vista completa",
        "Generate a new random 64-character bearer token":
            "Gerar um novo token aleatório de 64 caracteres",
        "Select a disk image to be loaded.": "Selecione a imagem de disco a carregar.",
        "No SD card image is currently loaded.":
            "Nenhuma imagem de cartão SD está carregada.",
        "Re-read the current local folder from disk.":
            "Voltar a ler a pasta local atual a partir do disco.",
        "Drag to resize the file explorers / log window split.":
            "Arraste para redimensionar a divisão exploradores / registo.",
        "Drag to resize the results / MOTD split.":
            "Arraste para redimensionar a divisão resultados / MOTD.",
        "Drag to set the background image opacity (0–100 %).":
            "Arraste para ajustar a opacidade do fundo (0–100 %).",
        "Type a value 0–100 to set the background image opacity.":
            "Escreva um valor 0–100 para a opacidade do fundo.",
        "Preview of the selected background image.":
            "Pré-visualização da imagem de fundo selecionada.",
        "Open https://itch.io/ in your browser":
            "Abrir https://itch.io/ no seu navegador",
        "Connect to itch.io using the API key above.":
            "Ligar ao itch.io com a chave API acima.",
        "Disconnect from itch.io and clear the listed items.":
            "Desligar do itch.io e limpar os itens listados.",
        "Open https://itch.io/user/settings/api-keys in your browser":
            "Abrir https://itch.io/user/settings/api-keys no seu navegador",
        "Color used for directory name entries in the image explorer.":
            "Cor dos nomes de diretório no explorador da imagem.",
        "Color used for file name entries in the image explorer.":
            "Cor dos nomes de ficheiro no explorador da imagem.",
        "Color used for the 'DIR' type label column of directory entries.":
            "Cor da coluna de tipo «DIR» dos diretórios.",
        "Color used for the '[Up Directory..]' navigation row in the image explorer.":
            "Cor da linha de navegação «[Up Directory..]».",
        "Color used for the file extension column in the image explorer.":
            "Cor da coluna de extensão do ficheiro.",
        "Color used for the file size column in the image explorer.":
            "Cor da coluna de tamanho do ficheiro.",
        "Background color behind the file explorers and the whole app window.":
            "Cor de fundo atrás dos exploradores de ficheiros e de toda a janela da aplicação.",
        "Discard the hand-picked colors above and restore the themed defaults.":
            "Descarta as cores escolhidas acima e restaura os valores predefinidos do tema.",
        "MAME display aspect ratio (-aspect).":
            "Relação de aspeto do MAME (-aspect).",
        "Perform a single sync and then stop the server.":
            "Fazer uma única sincronização e parar o servidor.",
        "Keep listening and send every file each time, ignoring the sync point.":
            "Continuar à escuta e enviar todos os ficheiros de cada vez, ignorando o ponto de sincronização.",
        "Keep listening and send only files that are new or changed since the\nlast sync (skips files recorded in the sync point). The default mode.":
            "Continuar à escuta e enviar apenas os ficheiros novos ou alterados\ndesde a última sincronização (ignora os registados no ponto de\nsincronização). Modo predefinido.",
        "Make the folder currently shown in the explorer above the new sync root.":
            "Usar a pasta mostrada no explorador acima como nova raiz de sincronização.",
        "Pick a random page from the full GetIt catalogue and show its entries.":
            "Escolher uma página aleatória do catálogo GetIt e mostrar as entradas.",
        "Pick a random page of zxART productions and show its entries.":
            "Escolher uma página aleatória de produções zxART e mostrá-la.",
        "Show the most recent zxART productions/pictures (sorted by date).":
            "Mostrar as produções/imagens mais recentes do zxART (por data).",
        "Show the most recently added/updated ZXDB games.":
            "Mostrar os jogos adicionados ou atualizados mais recentemente no ZXDB.",
        "Fetch random entries from GetIt + ZXDB + zxArt and merge them here":
            "Obter entradas aleatórias de GetIt + ZXDB + zxArt e combiná-las aqui",
        "Fetch the latest releases from GetIt + ZXDB + zxArt and merge them here":
            "Obter as últimas novidades de GetIt + ZXDB + zxArt e combiná-las aqui",
        "zxART catalog display language.\nPersisted across sessions in the config file.":
            "Idioma do catálogo zxART.\nGuardado no ficheiro de configuração.",
        "Requires the optional 'pygame-ce' package.\nInstall with: pip install pygame-ce":
            "Requer o pacote opcional «pygame-ce».\nInstale com: pip install pygame-ce",
        "Load a ZX Spectrum Next disk image first — then CSpect can boot it from the mounted SD card.":
            "Carregue primeiro uma imagem de disco ZX Spectrum Next — o CSpect poderá arrancá-la a partir do cartão SD montado.",
        "Select emulator image file: {path}":
            "Selecionar imagem para o emulador: {path}",
        "No writable disk image available.":
            "Não há nenhuma imagem de disco gravável disponível.",
        ".img file {path} already in use.":
            "O ficheiro .img {path} já está a ser utilizado.",
        "The disk image {path} can no longer be found — it may have been moved, renamed or deleted.":
            "A imagem de disco {path} já não se encontra — pode ter sido movida, mudada de nome ou eliminada.",
        "Select a ZX Spectrum Next disk image (.img/.hdf) first — then MAME can boot it as the Next's hard disk.":
            "Selecione primeiro uma imagem de disco (.img/.hdf) ZX Spectrum Next — o MAME poderá arrancá-la como disco rígido do Next.",
        "Select the parent folder inside the SD card image\n(at the top level this returns the target to the image root).":
            "Selecionar a pasta superior dentro da imagem SD\n(no nível de topo volta à raiz da imagem).",
        "Go up one folder in the local file explorer\n(same as double-clicking its '..' entry).":
            "Subir uma pasta no explorador local\n(igual a fazer duplo clique na entrada «..»).",
        "Re-list the current image folder from the SD card image\n(runs 'hdfmonkey ls' again).":
            "Voltar a listar a pasta atual da imagem SD\n(executa «hdfmonkey ls» novamente).",
        "Download a ready-to-use NextZXOS SD card image from zxnext.uk,\nsave it to disk, and load it automatically.":
            "Transferir uma imagem SD NextZXOS pronta a usar de zxnext.uk,\nguardá-la no disco e carregá-la automaticamente.",
        "Switch between the classic table view and the picture (gallery)\nview. Persisted across sessions in the config file.":
            "Alternar entre a vista de tabela clássica e a vista de galeria.\nGuardado no ficheiro de configuração.",
        "Switch between the classic table view and the picture (gallery) view.\nPersisted across sessions in the config file.":
            "Alternar entre a vista de tabela clássica e a vista de galeria.\nGuardado no ficheiro de configuração.",
        "Language of the application's buttons, labels and checkboxes.\nApplies immediately; texts written while the app runs (logs, dialogs)\nfollow after a restart. Saved to the configuration file.":
            "Idioma dos botões, etiquetas e opções da aplicação.\nAplica-se de imediato; os textos gerados em execução (registos,\ndiálogos) mudam após reiniciar. Guardado na configuração.",
        "🌐  Language set to match your system":
            "🌐  Idioma ajustado ao seu sistema",
        "The interface language was set to match your system language.\nYou can change it anytime in the Settings tab (\"Application language:\").":
            "O idioma da interface foi ajustado ao idioma do seu sistema.\nPode alterá-lo quando quiser no separador Settings\n(«Idioma da aplicação:»).",
        # ---- Help tab (INIT_HELP; rebuilt per language by _repopulate_help) ----
        "Welcome to zx-next-unite {version} help":
            "Bem-vindo à ajuda do zx-next-unite {version}",
        "Introduction:":
            "Introdução:",
        "HdfmGooey was initially created by em00k and NextSync by Jari Komppa.":
            "O HdfmGooey foi criado originalmente por em00k e o NextSync por Jari Komppa.",
        "A while back I rambled with the idea of an all in one bootstrapper transfer tool to":
            "Há uns tempos andava a matutar na ideia de uma ferramenta de transferência e arranque tudo-em-um para",
        "avoid manipulating SD cards for the Spectrum Next and that was the initial idea of it.":
            "evitar manusear cartões SD para o Spectrum Next, e essa foi a ideia inicial.",
        "Last but not least some source code was lost from HDFM Gooey and the tool was stuck back in that time,":
            "Por último, mas não menos importante, parte do código fonte do HDFM Gooey perdeu-se e a ferramenta ficou presa nessa época;",
        "with the agreement of em00k I started a rewrite in Python and later with Jari.":
            "com o acordo do em00k comecei uma reescrita em Python e mais tarde com o Jari.",
        "The point of using Python was that it would also provide MacOS and Linux portability.":
            "A vantagem de usar Python era que também traria portabilidade para MacOS e Linux.",
        "Later down the line I then extended the NextSync functionality from Sync3 to Sync4.":
            "Mais tarde estendi a funcionalidade do NextSync de Sync3 para Sync4.",
        "The new .sync5 command for the Next speaks Sync4 and therefore allows sending files and directories using the -send command line option.":
            "O novo comando .sync5 para o Next fala Sync4 e por isso permite enviar ficheiros e diretórios usando a opção de linha de comandos -send.",
        "There is as well a new nextsync5.py command line located at the root of the repository that supports the new Sync4 protocol.":
            "Há também uma nova linha de comandos nextsync5.py, na raiz do repositório, que suporta o novo protocolo Sync4.",
        "Here we are now you have it!":
            "E cá estamos, aqui o tens!",
        "Keyboard shortcuts":
            "Atalhos de teclado",
        "The three file explorers (SD Card local, SD Card disk image and NextSync local) share these shortcuts. Copy / Cut / Paste work across all three explorers and also exchange with the operating-system clipboard (e.g. copy in Windows Explorer, paste into the disk image, and vice-versa):":
            "Os três exploradores de ficheiros (local do SD Card, imagem de disco do SD Card e local do NextSync) partilham estes atalhos. Copiar / Cortar / Colar funcionam entre os três exploradores e também trocam com a área de transferência do sistema operativo (p. ex., copia no Explorador do Windows, cola na imagem de disco, e vice-versa):",
        "    Ctrl+C  -  Copy the selected file(s)/folder(s) to the shared clipboard.":
            "    Ctrl+C  -  Copiar os ficheiros/pastas selecionados para a área de transferência partilhada.",
        "    Ctrl+X  -  Cut the selection (moved to the destination on the next paste).":
            "    Ctrl+X  -  Cortar a seleção (movida para o destino no próximo colar).",
        "    Ctrl+V  -  Paste into the selected / currently shown folder.":
            "    Ctrl+V  -  Colar na pasta selecionada / atualmente mostrada.",
        "    F2      -  Rename the selected file or folder.":
            "    F2      -  Renomear o ficheiro ou pasta selecionado.",
        "    Delete  -  Delete the selected file or folder (disk-image & NextSync explorers).":
            "    Delete  -  Eliminar o ficheiro ou pasta selecionado (exploradores da imagem de disco e NextSync).",
        "In the picture (gallery) item viewer (double-click an item in the GetIt, ZXDB, zxArt or itch.io tabs):":
            "No visor de itens de imagens (galeria) (duplo clique num item dos separadores GetIt, ZXDB, zxArt ou itch.io):",
        "    Esc           -  Close the viewer and return to the gallery.":
            "    Esc           -  Fechar o visor e voltar à galeria.",
        "    Left / Right  -  Show the previous / next screenshot.":
            "    Left / Right  -  Mostrar a captura anterior / seguinte.",
        "Third party license":
            "Licenças de terceiros",
        "zx-next-unite is released under the MIT license. It is a Qt Application using PySide6 (Qt for Python) on top of Qt6, used under the GNU LGPL v3.":
            "O zx-next-unite é publicado sob a licença MIT. É uma aplicação Qt que usa PySide6 (Qt para Python) sobre Qt6, utilizado sob a GNU LGPL v3.",
        "Please refer to the LICENSE and THIRD-PARTY-NOTICES.md files on github: https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE and https://github.com/jclauzel/ZX-Next-Unite/blob/main/THIRD-PARTY-NOTICES.md.":
            "Consulta os ficheiros LICENSE e THIRD-PARTY-NOTICES.md no github: https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE e https://github.com/jclauzel/ZX-Next-Unite/blob/main/THIRD-PARTY-NOTICES.md.",
        "PySide6 is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The pre-built release executables do include PySide6/Qt; since the app's full source code is published, they can be rebuilt with a modified Qt.":
            "O PySide6 não vem incluído numa instalação manual de python e tem de ser instalado à parte (ver instruções de instalação). Os executáveis pré-compilados das releases incluem PySide6/Qt; como o código fonte completo da aplicação está publicado, podem ser reconstruídos com um Qt modificado.",
        "zx-next-unite also uses pygame-ce (the community edition of pygame) for its animated backgrounds and visualizations (e.g. the 'Alien Floyd's' effects). Many thanks to the pygame and pygame-ce communities - see https://pyga.me and https://www.pygame.org.":
            "O zx-next-unite também usa pygame-ce (a edição comunitária do pygame) para os fundos animados e visualizações (p. ex., os efeitos 'Alien Floyd's'). Muito obrigado às comunidades pygame e pygame-ce - ver https://pyga.me e https://www.pygame.org.",
        "pygame-ce is distributed under the GNU LGPL v2.1 license and, like PySide6, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions).":
            "O pygame-ce é distribuído sob a licença GNU LGPL v2.1 e, como o PySide6, não vem incluído numa instalação manual de python e tem de ser instalado à parte (ver instruções de instalação).",
        "zx-next-unite optionally uses itch-dl by Dragoon Aethis to power the itch.io tab (browsing and installing your itch.io collections). Many thanks to its author - see https://github.com/DragoonAethis/itch-dl.":
            "O zx-next-unite usa opcionalmente o itch-dl de Dragoon Aethis para o separador itch.io (navegar e instalar as tuas coleções itch.io). Muito obrigado ao autor - ver https://github.com/DragoonAethis/itch-dl.",
        "itch-dl is distributed under the MIT license (Copyright (c) 2022 Dragoon Aethis) and, like PySide6 and pygame-ce, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The itch.io tab is only shown when itch-dl is installed.":
            "O itch-dl é distribuído sob a licença MIT (Copyright (c) 2022 Dragoon Aethis) e, como o PySide6 e o pygame-ce, não vem incluído numa instalação manual de python e tem de ser instalado à parte (ver instruções de instalação). O separador itch.io só aparece quando o itch-dl está instalado.",
        "zx-next-unite optionally uses Flask by the Pallets team to power the NextSync HTTP bridge - the web server behind the Next's .http dot command that lets one Next drive another Next's SD card. Many thanks to its authors - see https://flask.palletsprojects.com and https://github.com/pallets/flask.":
            "O zx-next-unite usa opcionalmente o Flask, da equipa Pallets, para a ponte HTTP do NextSync - o servidor web por trás do comando dot .http do Next que permite a um Next controlar o cartão SD de outro Next. Muito obrigado aos autores - ver https://flask.palletsprojects.com e https://github.com/pallets/flask.",
        "Flask is distributed under the BSD-3-Clause license and, like the other optional packages, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The HTTP bridge toggle in Settings is greyed out until Flask is installed.":
            "O Flask é distribuído sob a licença BSD-3-Clause e, como os outros pacotes opcionais, não vem incluído numa instalação manual de python e tem de ser instalado à parte (ver instruções de instalação). O interruptor da ponte HTTP em Settings fica desativado até o Flask estar instalado.",
        "zx-next-unite optionally uses Send2Trash by Andrew Senetar and contributors (originally by Virgil Dupras) to send files deleted in the local file explorers to the system Recycle Bin / Trash instead of removing them permanently. Many thanks to its authors - see https://github.com/arsenetar/send2trash.":
            "O zx-next-unite usa opcionalmente o Send2Trash, de Andrew Senetar e colaboradores (originalmente de Virgil Dupras), para enviar os ficheiros eliminados nos exploradores locais para a Reciclagem do sistema em vez de os remover permanentemente. Muito obrigado aos autores - ver https://github.com/arsenetar/send2trash.",
        "Send2Trash is distributed under the BSD license and, like the other optional packages, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The 'Send deleted files to the Recycle Bin' toggle in Settings is greyed out until Send2Trash is installed.":
            "O Send2Trash é distribuído sob a licença BSD e, como os outros pacotes opcionais, não vem incluído numa instalação manual de python e tem de ser instalado à parte (ver instruções de instalação). O interruptor 'Enviar os ficheiros eliminados para a Reciclagem' em Settings fica desativado até o Send2Trash estar instalado.",
        "zx-next-unite's optional pre-compiled Windows binary is built with PyInstaller by the PyInstaller Development Team, which bundles the app and all of its dependencies into a single standalone executable. Many thanks to its authors - see https://pyinstaller.org and https://github.com/pyinstaller/pyinstaller.":
            "O binário opcional pré-compilado para Windows do zx-next-unite é construído com o PyInstaller, do PyInstaller Development Team, que empacota a aplicação e todas as dependências num único executável autónomo. Muito obrigado aos autores - ver https://pyinstaller.org e https://github.com/pyinstaller/pyinstaller.",
        "PyInstaller is distributed under its GPL 2.0 license with a special exception that explicitly permits packaging applications of any license. It is a build-time tool only - used to produce the pre-compiled binary - and is not needed when running zx-next-unite from source with a manual python install.":
            "O PyInstaller é distribuído sob a sua licença GPL 2.0 com uma exceção especial que permite explicitamente empacotar aplicações de qualquer licença. É apenas uma ferramenta de build - usada para produzir o binário pré-compilado - e não é necessária ao executar o zx-next-unite a partir do código fonte com uma instalação manual de python.",
        "The pre-compiled Windows binary is additionally compressed with UPX (the Ultimate Packer for eXecutables) by Markus Oberhumer, Laszlo Molnar and John Reiser. Many thanks to its authors - see https://upx.github.io and https://github.com/upx/upx.":
            "O binário pré-compilado para Windows é ainda comprimido com o UPX (the Ultimate Packer for eXecutables), de Markus Oberhumer, Laszlo Molnar e John Reiser. Muito obrigado aos autores - ver https://upx.github.io e https://github.com/upx/upx.",
        "UPX is distributed under its own liberal license (based on the GPL, with a special exception covering the compressed executables it produces). Like PyInstaller it is a build-time tool only and is not needed when running from source.":
            "O UPX é distribuído sob a sua própria licença liberal (baseada na GPL, com uma exceção especial que cobre os executáveis comprimidos que produz). Como o PyInstaller, é apenas uma ferramenta de build e não é necessário ao executar a partir do código fonte.",
        "Setup & How to:":
            "Instalação e guia:",
        "Check out the main setup & demo video available at: https://youtu.be/-gUxV4fM1yo  (and the full python install is covered in the old py-hdfm-gooey since ZX-Next-Unite is an evolution of it : https://youtu.be/FJG-Z0DCIjQ )":
            "Vê o vídeo principal de instalação e demonstração disponível em: https://youtu.be/-gUxV4fM1yo  (a instalação completa de python é coberta no antigo py-hdfm-gooey, já que o ZX-Next-Unite é uma evolução dele : https://youtu.be/FJG-Z0DCIjQ )",
        "NextSync Head Over Heels demo: https://www.youtube.com/watch?v=D3_WqTPvjOE":
            "Demo do NextSync com Head Over Heels: https://www.youtube.com/watch?v=D3_WqTPvjOE",
        "NextSync Night Knight demo: https://www.youtube.com/watch?v=eN1eMIqMCm4":
            "Demo do NextSync com Night Knight: https://www.youtube.com/watch?v=eN1eMIqMCm4",
        "Is a required external component developed by Matt Westcott that allows browsing the image.":
            "É um componente externo obrigatório desenvolvido por Matt Westcott que permite navegar pela imagem.",
        "You will need to install it to get this application up and fully running.":
            "Terás de o instalar para pôr esta aplicação totalmente a funcionar.",
        "If hdfmonkey is not present you will see an error message in the main log window as it is missing.":
            "Se o hdfmonkey não estiver presente verás uma mensagem de erro na janela de registo principal a indicar que falta.",
        "If that is the case you will see a 'Download and install HDF Monkey' button bottom right,":
            "Se for o caso verás um botão 'Transferir e instalar o HDF Monkey' em baixo à direita;",
        "once clicked it will download a pre-compiled hdfmonkey build for your platform (Windows/Linux/macOS) and install it under the app's downloads folder.":
            "ao clicar, transfere uma build pré-compilada do hdfmonkey para a tua plataforma (Windows/Linux/macOS) e instala-a na pasta downloads da aplicação.",
        "If the above automated install is successful, you should then be able to select an image and navigate it.":
            "Se a instalação automática acima correr bem, deverás conseguir selecionar uma imagem e navegar por ela.",
        "hdfmonkey can also be installed manually based on the instructions for your platform that can be found at: https://github.com/gasman/hdfmonkey":
            "O hdfmonkey também pode ser instalado manualmente seguindo as instruções para a tua plataforma em: https://github.com/gasman/hdfmonkey",
        "zx-next-unite implements the <Server> side code and protocol of NextSync by Jari Komppa.":
            "O zx-next-unite implementa o código e protocolo do lado <servidor> do NextSync de Jari Komppa.",
        "It does not require any dot .sync modification and it uses the same very close python logic as nextsync.py.":
            "Não requer nenhuma modificação do dot .sync e usa a mesma lógica python, muito próxima, do nextsync.py.",
        "Initial release on specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8":
            "Lançamento inicial no specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8",
        "As a result you will need to run the same dot .sync command on your Next as with the console version and the same network protocol.":
            "Por isso terás de executar no teu Next o mesmo comando dot .sync que na versão de consola e com o mesmo protocolo de rede.",
        "The latest release v1.2 of the .sync command can be found here https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 .":
            "A última versão v1.2 do comando .sync está aqui https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 .",
        "You may follow the same instructions as provided in the readme.txt of that release.":
            "Podes seguir as mesmas instruções fornecidas no readme.txt dessa versão.",
        "On your Spectrum Next, clone or image copy the SYNC command that is located in the above release zip file into your Next dot folder.":
            "No teu Spectrum Next, clona ou copia para a imagem o comando SYNC que está no zip dessa versão para a pasta dot do teu Next.",
        "Navigate to the NextSync tab, select the root folder to sync on the left.":
            "Vai ao separador NextSync e seleciona à esquerda a pasta raiz a sincronizar.",
        "Once you have selected the folder hit the 'Prepare Classic NextSync server' button, check the NextSync log window on the right.":
            "Depois de selecionada a pasta carrega no botão 'Preparar servidor NextSync clássico' e verifica a janela de registo do NextSync à direita.",
        "The first time you run .sync on your Next you will be prompted to select the <server> IP address, this machine running NextSync.":
            "Na primeira vez que executares .sync no teu Next ser-te-á pedido para selecionar o endereço IP do <servidor>, esta máquina que corre o NextSync.",
        "From the log window pick the IP address from this machine you want to use and type it on your Next.":
            "Na janela de registo escolhe o endereço IP desta máquina que queres usar e escreve-o no teu Next.",
        "Then start the sync server on this machine using the 'Start Classic NextSync server' button and then run the .sync command on your Next.":
            "Depois inicia o servidor de sincronização nesta máquina com o botão 'Iniciar servidor NextSync clássico' e executa o comando .sync no teu Next.",
        "At this point your Spectrum Next will connect to your machine using a network socket and the files will be sent to your Next.":
            "Nesse momento o teu Spectrum Next liga-se à tua máquina através de um socket de rede e os ficheiros são enviados para o teu Next.",
        "As it is your Next that will connect to this machine check your firewall allows inbound calls to this machine on port: 2048 by default.":
            "Como é o teu Next que se liga a esta máquina, confirma que a firewall permite ligações de entrada a esta máquina na porta: 2048 por omissão.",
        "The same syncignore.txt and syncpoint.dat file logic applies and allows you to control the sync (please check Jari's documentation).":
            "Aplica-se a mesma lógica dos ficheiros syncignore.txt e syncpoint.dat, que te permite controlar a sincronização (consulta a documentação do Jari).",
        "NextSync source code can be found here: https://github.com/jarikomppa/specnext/tree/master/sync":
            "O código fonte do NextSync está aqui: https://github.com/jarikomppa/specnext/tree/master/sync",
        "If you run into any type of issue using the NextSync integration please run first Jari's command line version to see if it works as expected.":
            "Se tiveres qualquer tipo de problema com a integração NextSync, corre primeiro a versão de linha de comandos do Jari para ver se funciona como esperado.",
        "OpenAL sound engine (on Windows)":
            "Motor de som OpenAL (no Windows)",
        "The OpenAL library is required on Windows for CSpect to play sound, you may download it here: https://openal.org/":
            "A biblioteca OpenAL é necessária no Windows para o CSpect reproduzir som; podes transferi-la aqui: https://openal.org/",
        "Mono (on Linux & MacOS Only)":
            "Mono (apenas em Linux e MacOS)",
        "You will also need to manually install the mono-complete package for example using: sudo apt-get install mono-complete":
            "Também terás de instalar manualmente o pacote mono-complete, por exemplo com: sudo apt-get install mono-complete",
        "Third-Party Content Sources (GetIt / ZXDB / zxArt):":
            "Fontes de conteúdo de terceiros (GetIt / ZXDB / zxArt):",
        "zx-next-unite integrates three external databases to let you browse and download":
            "O zx-next-unite integra três bases de dados externas para poderes explorar e transferir",
        "Spectrum-related software and artwork directly from within the application.":
            "software e arte relacionados com o Spectrum diretamente a partir da aplicação.",
        "The application consumes their public APIs — it does not host, mirror, or":
            "A aplicação consome as APIs públicas deles — não aloja, replica nem",
        "redistribute any of the files itself.":
            "redistribui nenhum dos ficheiros por si própria.",
        "  GetIt is a community-maintained archive of ZX Spectrum Next software.":
            "  O GetIt é um arquivo de software para ZX Spectrum Next mantido pela comunidade.",
        "  The application queries the GetIt API to list and search files, then":
            "  A aplicação consulta a API do GetIt para listar e pesquisar ficheiros, e depois",
        "  downloads them directly from the URLs returned by that API.":
            "  transfere-os diretamente a partir dos URLs devolvidos por essa API.",
        "  ZXDB is an open-source database of ZX Spectrum and related software,":
            "  A ZXDB é uma base de dados de código aberto de software ZX Spectrum e afins,",
        "  maintained by the community at https://github.com/zxdb/ZXDB .":
            "  mantida pela comunidade em https://github.com/zxdb/ZXDB .",
        "  The application queries the ZXDB REST API for titles, releases, screenshots":
            "  A aplicação consulta a API REST da ZXDB para títulos, edições, capturas",
        "  and inlays, then downloads files directly from the URLs returned by that API.":
            "  e capas, e depois transfere os ficheiros diretamente dos URLs devolvidos por essa API.",
        "  zxArt (https://zxart.ee) is a gallery and archive dedicated to ZX Spectrum":
            "  O zxArt (https://zxart.ee) é uma galeria e arquivo dedicados à arte visual,",
        "  visual art, music, and productions.":
            "  música e produções do ZX Spectrum.",
        "  The application sends requests to the zxArt API to search productions and":
            "  A aplicação envia pedidos à API do zxArt para pesquisar produções e",
        "  pictures, retrieve metadata and preview images, and download productions":
            "  imagens, obter metadados e pré-visualizações, e transferir produções",
        "  directly from the URLs returned by that API.":
            "  diretamente a partir dos URLs devolvidos por essa API.",
        "  Mame emulator brought to you by Holub for the ZX Spectrum Next can be installed following this documentation: https://wiki.specnext.dev/MAME:Installing":
            "  O emulador Mame trazido ao ZX Spectrum Next por Holub pode ser instalado seguindo esta documentação: https://wiki.specnext.dev/MAME:Installing",
        "  Official Windows Binary Packages can be found here: https://www.mamedev.org/release.html":
            "  Os pacotes binários oficiais para Windows estão aqui: https://www.mamedev.org/release.html",
        "  Put the file tbblue.zip that can be found here: https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip into MAME's roms folder.":
            "  Coloca o ficheiro tbblue.zip, que encontras aqui: https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip na pasta roms do MAME.",
        "  Important note: Don't extract the tbblue.zip file; MAME will look for the zip file when the 'tbblue' machine is selected.":
            "  Nota importante: não extraias o ficheiro tbblue.zip; o MAME procura o zip quando a máquina 'tbblue' é selecionada.",
        "  Mike Dailly's CSpect is a downloadable emulator for Windows, macOS, and Linux":
            "  O CSpect, de Mike Dailly, é um emulador transferível para Windows, macOS e Linux",
        "  Sites and links:":
            "  Sites e ligações:",
        "Legal disclaimer:":
            "Aviso legal:",
        "  The author of zx-next-unite does NOT distribute any files, ROMs, games,":
            "  O autor do zx-next-unite NÃO distribui ficheiros, ROMs, jogos,",
        "  demos, graphics, music, or any other content obtained through these APIs.":
            "  demos, gráficos, música nem qualquer outro conteúdo obtido através destas APIs.",
        "  All content is served exclusively by the respective third-party services.":
            "  Todo o conteúdo é servido exclusivamente pelos respetivos serviços de terceiros.",
        "  This application and author do not control third-party content.":
            "  Esta aplicação e o autor não controlam conteúdos de terceiros.",
        "  It is the sole responsibility of the end user to ensure that any content":
            "  É da exclusiva responsabilidade do utilizador final garantir que qualquer conteúdo",
        "  they download or use through this application complies with the applicable":
            "  que transfira ou use através desta aplicação cumpre os requisitos aplicáveis",
        "  copyright, licensing, and legal requirements in their jurisdiction.":
            "  de direitos de autor, licenciamento e legislação da sua jurisdição.",
        "  If in doubt, consult the terms of service of the relevant platform and":
            "  Em caso de dúvida, consulta os termos de serviço da plataforma em causa e",
        "  seek appropriate legal advice before downloading or using any content.":
            "  procura aconselhamento jurídico adequado antes de transferir ou usar qualquer conteúdo.",
        "  For inquiries you may reach out to me on my github page: https://github.com/jclauzel/ZX-Next-Unite":
            "  Para questões podes contactar-me na minha página do github: https://github.com/jclauzel/ZX-Next-Unite",
        "Enjoy!":
            "Diverte-te!",
        # ---- OpenAL guided install (CSpect sound on Windows) ----
        "OpenAL 1.1 detected — CSpect sound is ready.":
            "OpenAL 1.1 detetado — o som do CSpect está pronto.",
        "Install OpenAL?":
            "Instalar o OpenAL?",
        ("On Windows CSpect needs the <b>OpenAL 1.1</b> audio library "
         "for sound, and it was not detected on this machine — without "
         "it CSpect runs silent.<br><br>"
         "OpenAL is separate, third-party software — many thanks to its "
         "authors: {url}<br><br>"
         "Download the official installer (oalinst.exe) from openal.org "
         "and run it now?<br><br>"
         "Windows will ask for administrator approval when the installer "
         "starts — the app itself never runs elevated."):
            ("No Windows o CSpect precisa da biblioteca de áudio "
             "<b>OpenAL 1.1</b> para o som, e ela não foi detetada nesta "
             "máquina — sem ela o CSpect corre sem áudio.<br><br>"
             "O OpenAL é software independente de terceiros — muito "
             "obrigado aos seus autores: {url}<br><br>"
             "Transferir o instalador oficial (oalinst.exe) de openal.org e "
             "executá-lo agora?<br><br>"
             "O Windows pedirá aprovação de administrador quando o instalador "
             "arrancar — a própria aplicação nunca corre elevada."),
        "Download and run the OpenAL installer":
            "Transferir e executar o instalador do OpenAL",
        "Open openal.org":
            "Abrir openal.org",
        "The OpenAL download failed — see the log for details. You can install it manually from {url}":
            "A transferência do OpenAL falhou — vê o registo para detalhes. Podes instalá-lo manualmente a partir de {url}",
    },
    "pl": {
        # ---- 9.6.0: per-emulator button colour ----
        "Set the {emulator} color…":
            "Ustaw kolor {emulator}…",
        "Reset the {emulator} color":
            "Przywróć domyślny kolor {emulator}",
        # ---- 9.6.0: forgetting a remembered SD image path ----
        "Remove \"{path}\" from the list":
            "Usuń \"{path}\" z listy",
        "Clear the whole list":
            "Wyczyść całą listę",
        "Removed {path} from the image list — the image file itself was not deleted.":
            "Usunięto {path} z listy obrazów — sam plik obrazu nie został usunięty z dysku.",
        "Cleared the image list — no image files were deleted.":
            "Wyczyszczono listę obrazów — nie usunięto żadnych plików obrazów.",
        "Clear the image list?":
            "Wyczyścić listę obrazów?",
        "Forget all {count} remembered image paths? The image files themselves are not deleted.":
            "Zapomnieć wszystkie zapamiętane ścieżki obrazów ({count})? Same pliki obrazów nie zostaną usunięte.",
        "Remove the image path shown on the left from the list.\nThe image file itself is not deleted.":
            "Usuń z listy ścieżkę obrazu pokazaną po lewej.\nSam plik obrazu nie zostanie usunięty.",
        "Path to the SD card image (.img / .hdf).\nType a path directly, click the arrow to pick from recently loaded images,\nor use the 'Select NextZXOS disk Image' button to browse.\nRight-click the box for list options, or press Delete on a dropdown entry to forget it.":
            "Ścieżka do obrazu karty SD (.img / .hdf).\nWpisz ścieżkę bezpośrednio, kliknij strzałkę, aby wybrać spośród ostatnio wczytanych obrazów,\nalbo użyj przycisku 'Wybierz obraz dysku NextZXOS', aby przeglądać dysk.\nKliknij pole prawym przyciskiem, aby zobaczyć opcje listy, albo naciśnij Delete na pozycji rozwijanej listy, aby ją zapomnieć.",
        # ---- GetIt starter pack ----
        'Assembling the starter pack…': 'Składanie pakietu startowego…',
        'Downloading {title} ({idx}/{total})…': 'Pobieranie {title} ({idx}/{total})…',
        ('Everything on GetIt is freely distributable; the files are written to {dir} '
         'inside the loaded image.'):
            ('Wszystko w GetIt można swobodnie rozpowszechniać; pliki są zapisywane w '
             '{dir} wewnątrz załadowanego obrazu.'),
        'Failed: {names}': 'Nie powiodło się: {names}',
        'Fetching the GetIt catalogue…': 'Pobieranie katalogu GetIt…',
        ('Fill the loaded SD image with a hand-picked selection of modern homebrew '
         'from the GetIt catalogue — everything on GetIt is freely distributable.'):
            ('Wypełnia załadowany obraz SD starannie wybranym nowoczesnym homebrew z '
             'katalogu GetIt — wszystko w GetIt można swobodnie rozpowszechniać.'),
        'Fill your SD image with {count} hand-picked homebrew titles from the GetIt catalogue?': 'Wypełnić obraz SD {count} starannie wybranymi tytułami homebrew z katalogu GetIt?',
        'Load a disk image first (SD Card tab), then try again.': 'Najpierw załaduj obraz dysku (karta SD Card), potem spróbuj ponownie.',
        'No network connection.': 'Brak połączenia sieciowego.',
        'Not in the catalogue right now: {names}': 'Obecnie brak w katalogu: {names}',
        'Starter pack': 'Pakiet startowy',
        'Starter pack cancelled — {done} title(s) were installed.': 'Pakiet startowy anulowany — zainstalowano tytułów: {done}.',
        'Starter pack complete: {done} of {total} titles installed to {dir}.': 'Pakiet startowy ukończony: zainstalowano {done} z {total} tytułów w {dir}.',
        'Starter pack failed: {error}': 'Pakiet startowy nie powiódł się: {error}',
        '🎁 Starter pack': '🎁 Pakiet startowy',
        # ---- context menus & runtime dialogs ----
        '"{name}" already exists in this folder.': '"{name}" już istnieje w tym folderze.',
        'Add drive {letter}: to the list?': 'Dodać dysk {letter}: do listy?',
        'Add Next drive': 'Dodaj dysk Nexta',
        'Confirm deletion': 'Potwierdź usunięcie',
        'Copy': 'Kopiuj',
        'Copy all text': 'Kopiuj cały tekst',
        'Copy path to clipboard': 'Kopiuj ścieżkę do schowka',
        'Copy text to clipboard': 'Kopiuj tekst do schowka',
        'Could not create {name}:': 'Nie można utworzyć {name}:',
        'Could not create:': 'Nie można utworzyć:',
        'Could not delete:': 'Nie można usunąć:',
        'Could not extract {name}:': 'Nie można wyodrębnić {name}:',
        'Could not rename:': 'Nie można zmienić nazwy:',
        'Create directory failed': 'Nie udało się utworzyć katalogu',
        'Create new directory': 'Utwórz nowy katalog',
        'Create new directory…': 'Utwórz nowy katalog…',
        'Cut': 'Wytnij',
        'Decrease font size': 'Zmniejsz rozmiar czcionki',
        'Delete failed': 'Usuwanie nie powiodło się',
        'Delete from the local disk?': 'Usunąć z dysku lokalnego?',
        'Delete on the Next? Folders are deleted with everything inside them.': 'Usunąć na Nexcie? Foldery są usuwane wraz z całą zawartością.',
        'Delete the file "{name}"?': 'Usunąć plik "{name}"?',
        'Delete the folder "{name}" and all of its contents?': 'Usunąć folder "{name}" i całą jego zawartość?',
        'Delete these {count} items? Folders are deleted with all of their contents.': 'Usunąć te elementy ({count})? Foldery są usuwane wraz z całą zawartością.',
        'Deleted files are sent to the Recycle Bin.': 'Usunięte pliki trafiają do Kosza.',
        'Download (:<-)': 'Pobierz (:<-)',
        'Download content': 'Pobierz zawartość',
        'Download Failed': 'Pobieranie nie powiodło się',
        'Drive letter of the additional SD reader/partition (D..P):': 'Litera dysku dodatkowego czytnika SD/partycji (D..P):',
        'Extraction Failed': 'Wyodrębnianie nie powiodło się',
        'Failed to download the NextZXOS image:': 'Nie udało się pobrać obrazu NextZXOS:',
        'Fetch issue info for this magazine': 'Pobierz informacje o numerach tego czasopisma',
        'Fetch single magazine by name': 'Pobierz czasopismo według nazwy',
        'file': 'plik',
        'Files:  {files}\nFolders:  {folders}\nTotal size:  {size} bytes  ({pretty})': 'Pliki:  {files}\nFoldery:  {folders}\nRozmiar łączny:  {size} bajtów  ({pretty})',
        'folder': 'folder',
        'Get size': 'Pobierz rozmiar',
        'Image not writable': 'Obraz nie jest zapisywalny',
        'Increase font size (now {px}px)': 'Zwiększ rozmiar czcionki (teraz {px}px)',
        'More like this': 'Więcej podobnych',
        'New directory name in': 'Nazwa nowego katalogu w',
        'Name this Next': 'Nazwij tego Nexta',
        'Friendly name for {addr} (empty removes it):':
            'Przyjazna nazwa dla {addr} (pusta ją usuwa):',
        'New folder in {path}:': 'Nowy folder w {path}:',
        'New Folder…': 'Nowy folder…',
        'New name for the {kind}:': 'Nowa nazwa ({kind}):',
        'Not enough space on the Next': 'Za mało miejsca na Nexcie',
        ('Only add a drive that really exists on your Next (an extra SD card reader or '
         'partition). Selecting a drive that is not mounted CRASHES the Next.'):
            ('Dodawaj tylko dysk, który naprawdę istnieje w Twoim Nexcie (dodatkowy '
             'czytnik SD lub partycja). Wybranie niezamontowanego dysku ZAWIESZA Nexta.'),
        'Open': 'Otwórz',
        'Open in {source}': 'Otwórz w {source}',
        'Open: the system could not open {name}.':
            'Otwórz: system nie mógł otworzyć {name}.',
        'Open on website (zxart.ee)': 'Otwórz na stronie (zxart.ee)',
        'Open on website (zxinfo.dk)': 'Otwórz na stronie (zxinfo.dk)',
        'Paste': 'Wklej',
        'Remote Unzip file': 'Rozpakuj plik zdalnie',
        'Remote Zip': 'Spakuj zdalnie',
        'Remove from Favorites': 'Usuń z Ulubionych',
        "Rename '{name}' to:": "Zmień nazwę '{name}' na:",
        'Rename failed': 'Zmiana nazwy nie powiodła się',
        'Rename…': 'Zmień nazwę…',
        'Retrieve all issues': 'Pobierz wszystkie numery',
        'Send to SD card (image)  →  {dest}': 'Wyślij na kartę SD (obraz)  →  {dest}',
        'Send using NextSync  →  {dest}': 'Wyślij przez NextSync  →  {dest}',
        'Set sync root': 'Ustaw katalog główny synchronizacji',
        'Set this folder as the new sync root?': 'Ustawić ten folder jako nowy katalog główny synchronizacji?',
        'Size on the Next': 'Rozmiar na Nexcie',
        'The image was downloaded but could not be extracted:': 'Obraz został pobrany, ale nie udało się go wyodrębnić:',
        "The name cannot contain '/' or '\\'.": "Nazwa nie może zawierać '/' ani '\\'.",
        'This cannot be undone.': 'Tej operacji nie można cofnąć.',
        ('This copy needs {need} bytes ({need_h}), but drive {drive}: only has {free} '
         'bytes ({free_h}) free.\n\nIt exceeds the available remote space by {over} '
         'bytes ({over_h}).\n\nThe copy was not started.'):
            ('Ta kopia wymaga {need} bajtów ({need_h}), ale dysk {drive}: ma tylko '
             '{free} bajtów ({free_h}) wolnych.\n\nPrzekracza dostępne zdalne miejsce '
             'o {over} bajtów ({over_h}).\n\nKopiowanie nie zostało rozpoczęte.'),
        'Unzip file': 'Rozpakuj plik',
        'Zip': 'Spakuj',
        '… and {n} more': '… i jeszcze {n}',
        # ---- labels ----
        "  Background": "  Tło",
        "  Directory name": "  Nazwa katalogu",
        "  Directory type label": "  Etykieta typu katalogu",
        "  File extension": "  Rozszerzenie pliku",
        "  File name": "  Nazwa pliku",
        "  File size": "  Rozmiar pliku",
        "  Filter: ": "  Filtr: ",
        "  General UI text": "  Ogólny tekst interfejsu",
        "  Retro logs console": "  Konsola dziennika retro",
        "  Up Directory item": "  Pozycja «katalog wyżej»",
        "Background image opacity (%):": "Nieprzezroczystość tła (%):",
        "Background image:": "Obraz tła:",
        "CSpect default launch parameters:": "Parametry uruchamiania CSpect:",
        "Collection:": "Kolekcja:",
        "Desktop Theme:": "Motyw pulpitu:",
        "Disk Image Explorer: ": "Eksplorator obrazu dysku: ",
        "Gallery animation:": "Animacja galerii:",
        "Gallery image size:": "Rozmiar obrazków w galerii:",
        "Gallery items per row:": "Liczba pozycji w wierszu galerii:",
        "Gallery rows per page (min):": "Wiersze na stronę galerii (min):",
        "Gallery search sort ordering preference:": "Kolejność wyników wyszukiwania w galerii:",
        "Gallery slideshow pause time:": "Czas pauzy pokazu slajdów:",
        "Language:": "Język:",
        "Application language:": "Język aplikacji:",
        "Local file explorers & App Text Colors:": "Lokalne eksploratory i kolory tekstu:",
        "Local path: ": "Ścieżka lokalna: ",
        "MAME ROM / system:": "ROM / system MAME:",
        "MAME default launch parameters:": "Parametry uruchamiania MAME:",
        "Max connections:": "Maks. połączeń:",
        "NextSync — Automatically start Remote Explorer server on startup":
            "NextSync — Automatycznie uruchamiaj serwer Remote Explorera przy starcie",
        "NextSync — when a sent file or directory exists locally:":
            "NextSync — gdy odebrany plik lub katalog już istnieje lokalnie:",
        "Page:": "Strona:",
        "Port:": "Port:",
        "Reset theme": "Resetuj motyw",
        "Retro log font size:": "Rozmiar czcionki dziennika retro:",
        "Search:": "Szukaj:",
        "Search: ": "Szukaj: ",
        "View:": "Widok:",
        "itch.io API key:": "Klucz API itch.io:",
        # ---- buttons ----
        "< Prev": "< Wstecz",
        "Cancel": "Anuluj",
        "Cancel sync": "Anuluj synchronizację",
        "Connect": "Połącz",
        "Create Directory": "Utwórz katalog",
        "Create SyncIgnore File": "Utwórz plik SyncIgnore",
        "Delete": "Usuń",
        "Delete SyncIgnore File": "Usuń plik SyncIgnore",
        "Delete SyncPoint File": "Usuń plik SyncPoint",
        "Disconnect": "Rozłącz",
        "RS232 ESP emulation is already running on port {port} for another emulator; this MAME joins it. The new port applies once every MAME has exited.":
            "Emulacja RS232 ESP działa już na porcie {port} dla innego emulatora; ten MAME do niej dołącza. Nowy port zostanie użyty, gdy wszystkie MAME zostaną zamknięte.",
        "RS232 ESP emulation: {count} emulators are now sharing it (port {port}).":
            "Emulacja RS232 ESP: korzysta z niej teraz {count} emulatorów (port {port}).",
        "RS232 ESP emulation could not start (port {port} in use?). MAME starts without it.":
            "Emulacja RS232 ESP nie mogła wystartować (port {port} zajęty?). MAME uruchamia się bez niej.",
        "RS232 ESP emulation inspired by jesperl - by Janko Stamenović":
            "Emulacja RS232 ESP zainspirowana projektem jesperl - autorstwa Janko Stamenovića",
        "The optional RS232 ESP Emulation for MAME (Settings) is a clean full reimplementation in Python of an idea from jesperl by Janko Stamenović - an ESP-AT emulator bridging MAME's emulated Wi-Fi module to the real network. Many thanks for the inspirational idea - see https://sourceforge.net/projects/jesperl/.":
            "Opcjonalna emulacja RS232 ESP dla MAME (Ustawienia) to czysta, pełna reimplementacja w Pythonie pomysłu z projektu jesperl Janko Stamenovića - emulatora ESP-AT łączącego emulowany moduł Wi-Fi MAME z prawdziwą siecią. Wielkie dzięki za inspirujący pomysł - zobacz https://sourceforge.net/projects/jesperl/.",
        "One RS232 ESP emulation serves every running MAME: launch a second MAME on another disk image and it joins the same emulation with its own separate session, so several emulated Nexts can be on the network at once. The emulation stops when the last MAME exits. When two of them ask for the same server port (a Next listening for incoming connections), the second one is moved to the next free port and the log says which port to connect to.":
            "Jedna emulacja RS232 ESP obsługuje wszystkie uruchomione MAME: uruchom drugi MAME z inną obrazową kopią dysku, a dołączy on do tej samej emulacji z własną, oddzielną sesją, dzięki czemu kilka emulowanych Nextów może być jednocześnie w sieci. Emulacja zatrzymuje się, gdy zamknięty zostanie ostatni MAME. Gdy dwa z nich poproszą o ten sam port serwera (Next nasłuchujący połączeń przychodzących), drugi zostanie przeniesiony na następny wolny port, a dziennik poda, do którego portu się podłączyć.",
        "Transfers through the RS232 ESP emulation need the Next side on its SLOW pacing: use '.sync5 -s' for the dot, or set UART speed to Slow in ZX Next Remote's settings.":
            "Transfery przez emulację RS232 ESP wymagają WOLNEGO tempa po stronie Next: użyj '.sync5 -s' dla dota albo ustaw prędkość UART na Slow w ustawieniach ZX Next Remote.",
        "Start {emulator}": "Uruchom {emulator}",
        "Color:": "Kolor:",
        "Pick a color for this Next. It tints the machine list and this machine's tab in the session strip.":
            "Wybierz kolor dla tego Nexta. Zabarwia listę maszyn i zakładkę tej maszyny na pasku sesji.",
        "Clear the color": "Usuń kolor",
        "Switch to this Next": "Przełącz na tego Nexta",
        "Name and color…": "Nazwa i kolor…",
        "That Next is no longer on the line.":
            "Tego Nexta już nie ma na linii.",
        "Tell this Next to leave listen mode and exit? ZX Next Remote closes its application; a '.sync5' dot returns to BASIC. The server keeps listening, so it can connect again.":
            "Poprosić tego Nexta o wyjście z trybu nasłuchu i zakończenie? ZX Next Remote zamyka swoją aplikację; kropka '.sync5' wraca do BASIC-a. Serwer nadal nasłuchuje, więc można połączyć się ponownie.",
        "Asked the Next to leave listen mode and exit.":
            "Poproszono Nexta o wyjście z trybu nasłuchu i zakończenie.",
        "Remote .sync5 update": "Zdalna aktualizacja .sync5",
        "Update .sync5 on this Next ({old} → {new})…":
            "Zaktualizuj .sync5 na tym Nexcie ({old} → {new})…",
        "Push new .sync5 to this Next…":
            "Wyślij nowy .sync5 na tego Nexta…",
        ".sync5 version unknown — switch to this Next first":
            "Nieznana wersja .sync5 — najpierw przełącz na tego Nexta",
        ".sync5 v{old} predates self-update — copy the new dot to the Next by hand once":
            ".sync5 v{old} jest starszy niż samoaktualizacja — raz skopiuj nowy dot na Nexta ręcznie",
        "Locating the .sync5 build to send…":
            "Szukanie pliku .sync5 do wysłania…",
        "Still locating the .sync5 build to send — one moment.":
            "Wciąż trwa szukanie pliku .sync5 do wysłania — chwileczkę.",
        "Could not obtain the .sync5 build to send: {reason}":
            "Nie udało się uzyskać pliku .sync5 do wysłania: {reason}",
        ("Update .sync5 on {machine}: v{old} → v{new}.\n\nThe new dot is "
         "staged on the Next's SD card, read back and verified, then "
         "swapped in; the previous dot is kept as sync5.bak (renaming it "
         "back to sync5 is the one-step recovery). The session ends when "
         "the update completes — run {command} on the Next again "
         "afterwards.\n\nTarget directory on the Next:"):
            ("Aktualizacja .sync5 na {machine}: v{old} → v{new}.\n\nNowe "
             "polecenie dot jest przygotowywane na karcie SD Nexta, "
             "odczytywane z powrotem i weryfikowane, a następnie "
             "podmieniane; poprzednie polecenie dot zostaje zachowane "
             "jako sync5.bak (zmiana jego nazwy z powrotem na sync5 to "
             "odzyskanie w jednym kroku). Sesja kończy się po zakończeniu "
             "aktualizacji — uruchom potem {command} na Nexcie "
             "ponownie.\n\nKatalog docelowy na Nexcie:"),
        ("Push the new .sync5 (v{new}) to {machine}?\n\nThis machine's "
         "version is unknown (an older dot, or an old ZX Next Remote "
         "build — the two cannot be told apart), and the swap itself "
         "only works when the far side is a .sync dot v5.9 or newer: on "
         "anything older the staged sync5.new is left on the card and "
         "nothing is swapped. The previous dot is kept as sync5.bak "
         "(renaming it back to sync5 is the one-step recovery). The "
         "session ends when the update completes — run {command} on the "
         "Next again afterwards.\n\nTarget directory on the Next:"):
            ("Wysłać nowy .sync5 (v{new}) na {machine}?\n\nWersja tej "
             "maszyny jest nieznana (stare polecenie dot albo stara "
             "wersja ZX Next Remote — nie da się ich rozróżnić), a sama "
             "podmiana działa tylko wtedy, gdy po drugiej stronie jest "
             "polecenie dot .sync w wersji v5.9 lub nowszej: przy czymś "
             "starszym przygotowany sync5.new zostaje na karcie i nic "
             "nie jest podmieniane. Poprzednie polecenie dot zostaje "
             "zachowane jako sync5.bak (zmiana jego nazwy z powrotem na "
             "sync5 to odzyskanie w jednym kroku). Sesja kończy się po "
             "zakończeniu aktualizacji — uruchom potem {command} na "
             "Nexcie ponownie.\n\nKatalog docelowy na Nexcie:"),
        "Download File": "Pobierz plik",
        "Download NextZXOS Image": "Pobierz obraz NextZXOS",
        "Download and install HDF Monkey": "Pobierz i zainstaluj HDF Monkey",
        "Download and install HDF Monkey and OpenAL": "Pobierz i zainstaluj HDF Monkey i OpenAL",
        "Generate": "Generuj",
        "Get API key…": "Uzyskaj klucz API…",
        "Latest": "Najnowsze",
        "New Folder": "Nowy folder",
        "Next >": "Dalej >",
        "OK": "OK",
        "Open config file": "Otwórz plik konfiguracji",
        "Prepare Classic NextSync server": "Przygotuj klasyczny serwer NextSync",
        "Random": "Losowo",
        "Refresh": "Odśwież",
        "Rename": "Zmień nazwę",
        "Search": "Szukaj",
        "Select NextZXOS disk Image": "Wybierz obraz dysku NextZXOS",
        "Set current folder as new sync root folder":
            "Ustaw bieżący folder jako nowy katalog główny synchronizacji",
        "Up": "W górę",
        "▶ Start Classic NextSync server": "▶ Uruchom klasyczny serwer NextSync",
        "▶ Start Remote Explorer NextSync server":
            "▶ Uruchom serwer NextSync zdalnego eksploratora",
        "⬇  Download": "⬇  Pobierz",
        "⬇  Install MAME": "⬇  Zainstaluj MAME",
        "🎮 Retro": "🎮 Retro",
        "💾  Send to SD card": "💾  Wyślij na kartę SD",
        "🔁  Send via NextSync": "🔁  Wyślij przez NextSync",
        "🕹  Launch CSpect": "🕹  Uruchom CSpect",
        "🕹  Launch Mame": "🕹  Uruchom Mame",
        "🖼 Switch to 'Classic' view mode": "🖼 Przełącz na widok 'Klasyczny'",
        # ---- emulator option combos (SD Card tab; selected by index) ----
        "Screen Size X1": "Rozmiar ekranu X1",
        "Screen Size X2": "Rozmiar ekranu X2",
        "Screen Size X3": "Rozmiar ekranu X3",
        "Screen Size X4": "Rozmiar ekranu X4",
        "Fullscreen": "Pełny ekran",
        "Sound On": "Dźwięk włączony",
        "Sound Off": "Dźwięk wyłączony",
        "Sound WASAPI": "Dźwięk WASAPI",
        "Sound XAudio2": "Dźwięk XAudio2",
        "Sound PortAudio": "Dźwięk PortAudio",
        "VSync On": "VSync włączony",
        "VSync Off": "VSync wyłączony",
        "Joystick On": "Joystick włączony",
        "Joystick Off": "Joystick wyłączony",
        "Mouse On": "Mysz włączona",
        "Mouse Off": "Mysz wyłączona",
        "Disable ESC Key Off": "Blokada klawisza ESC: wył.",
        "Disable ESC Key On": "Blokada klawisza ESC: wł.",
        "ZX Next Unite update check: could not parse the versions (latest tag {tag}); skipping.":
            "Sprawdzanie ZX Next Unite: nie udało się odczytać wersji (najnowszy tag {tag}) — pomijanie.",
        "ZX Next Unite {latest} is available, but the release has no package for this platform — opening the releases page instead.":
            "Dostępny jest ZX Next Unite {latest}, ale wydanie nie zawiera pakietu dla tej platformy — zostanie otwarta strona wydań.",
        # ---- long guidance prompts (final) ----
        "A newer version of CSpect is available on itch.io.\n\nInstalled: {installed}\nLatest: {latest}\n\nDownload and install the newest version now?":
            "Na itch.io dostępna jest nowsza wersja CSpect.\n\nZainstalowana: {installed}\nNajnowsza: {latest}\n\nPobrać i zainstalować teraz najnowszą?",
        "CSpect update ▸ SUCCESS — {name} extracted to: {path}":
            "Aktualizacja CSpect ▸ SUKCES — {name} wypakowano do: {path}",
        "CSpect update ▸ Starting download + install of {name} ({file}) from itch.io into {folder}.":
            "Aktualizacja CSpect ▸ rozpoczynanie pobierania i instalacji {name} ({file}) z itch.io do {folder}.",
        "ERROR: could not build {name}: {error}":
            "BŁĄD: nie udało się utworzyć {name}: {error}",
        "MAME can't start: the ZX Spectrum Next boot ROM (TBBLUE) is missing. This step is manual — see {url} and follow \"Get TBBLUE (the Next 'boot ROM')\". Put the file tbblue.zip into MAME's roms folder (downloads\\mame\\roms) — DON'T extract it — and try again. You must provide a legally acquired, licensed ROM.":
            "MAME nie może wystartować: brakuje ROM-u rozruchowego ZX Spectrum Next (TBBLUE). Ten krok jest ręczny — zobacz {url} i wykonaj \"Get TBBLUE (the Next 'boot ROM')\". Umieść plik tbblue.zip w folderze roms MAME (downloads\\mame\\roms) — NIE rozpakowuj go — i spróbuj ponownie. Musisz użyć legalnie nabytego, licencjonowanego ROM-u.",
        "MAME install ▸ NEXT STEP (manual): add the TBBLUE boot ROM. See {url} → \"Get TBBLUE (the Next 'boot ROM')\". Put the file tbblue.zip into MAME's roms folder ({roms}) — DON'T extract it. You must provide a legally acquired, licensed ROM.":
            "Instalacja MAME ▸ NASTĘPNY KROK (ręczny): dodaj ROM rozruchowy TBBLUE. Zobacz {url} → \"Get TBBLUE (the Next 'boot ROM')\". Umieść plik tbblue.zip w folderze roms MAME ({roms}) — NIE rozpakowuj go. Musisz użyć legalnie nabytego, licencjonowanego ROM-u.",
        "NextSync .sync5 dot command updated: v{old} -> v{new} — please copy the new build to your Next (it cannot be deployed automatically).":
            "Polecenie dot .sync5 NextSync zaktualizowane: v{old} -> v{new} — skopiuj nową wersję na swojego Next (nie da się jej wdrożyć automatycznie).",
        "TIP: Did you know that if you have purchased CSpect from itch.io you can do a full end-to-end CSpect install from there?\n\nCSpect ships with hdfmonkey bundled inside it, so that route needs no separate hdfmonkey install — the app finds and uses the bundled copy automatically.\n\nSimply log into your itch.io account in the itch.io tab, navigate to CSpect and click Install.\n\nDo you still want to install hdfmonkey only, or abort and then make an end-to-end install of CSpect using itch.io?":
            "WSKAZÓWKA: czy wiesz, że jeśli kupiłeś CSpect na itch.io, możesz wykonać pełną instalację CSpect właśnie stamtąd?\n\nCSpect ma hdfmonkey dołączony w środku, więc ta droga nie wymaga osobnej instalacji hdfmonkey — aplikacja automatycznie znajduje i używa dołączonej kopii.\n\nZaloguj się na swoje konto itch.io w karcie itch.io, przejdź do CSpect i kliknij Zainstaluj.\n\nCzy nadal chcesz zainstalować tylko hdfmonkey, czy przerwać i wykonać pełną instalację CSpect przez itch.io?",
        "The automatic hdfmonkey download from specnext.com failed — the forum may be asking for a login or an anti-robot confirmation before the download can start (see the log for details).\n\nYou can install it manually instead:\n1. Click 'Open download page' below (or browse to\n    {url} ).\n2. Download the hdfmonkey .zip file.\n3. Drop the downloaded .zip into this EXACT folder — the app has already created it, and the 'Open downloads folder' button below opens it so nothing needs to be typed:\n    {folder}\n4. Click \"I've dropped the zip - try again\".":
            "Automatyczne pobranie hdfmonkey ze specnext.com nie powiodło się — forum może wymagać zalogowania lub potwierdzenia anty-robot przed rozpoczęciem pobierania (szczegóły w dzienniku).\n\nMożesz zainstalować go ręcznie:\n1. Kliknij 'Otwórz stronę pobierania' poniżej (albo otwórz\n    {url} ).\n2. Pobierz plik .zip hdfmonkey.\n3. Umieść pobrany .zip DOKŁADNIE w tym folderze — aplikacja już go utworzyła, a przycisk 'Otwórz folder pobierania' poniżej otwiera go, więc nic nie trzeba wpisywać:\n    {folder}\n4. Kliknij \"Wrzuciłem plik zip — spróbuj ponownie\".",
        "ZX Next Unite update: downloaded {name} to {folder}":
            "Aktualizacja ZX Next Unite: pobrano {name} do {folder}",
        "ZX Next Unite update: downloaded {path} but could not unpack it: {error}":
            "Aktualizacja ZX Next Unite: pobrano {path}, ale nie udało się rozpakować: {error}",
        "ZX Next Unite {latest} is available (you are running {installed}).\n\nYou appear to be running from source (git clone), so the\nrecommended way to update is:\n\n    git pull\n\ninstead of downloading the Windows binary.":
            "Dostępny jest ZX Next Unite {latest} (używasz {installed}).\n\nWygląda na to, że uruchamiasz program ze źródeł (git clone), więc\nzalecany sposób aktualizacji to:\n\n    git pull\n\nzamiast pobierania binarium dla Windows.",
        "ZX Next Unite {latest} is available — download?\n\nInstalled: {installed}\nLatest: {latest}\nPackage: {asset} (~{size})\n\nThe new version is saved next to the current one — you choose\nwhen to switch (you'll be offered a restart after the download).":
            "Dostępny jest ZX Next Unite {latest} — pobrać?\n\nZainstalowana: {installed}\nNajnowsza: {latest}\nPakiet: {asset} (~{size})\n\nNowa wersja zapisywana jest obok obecnej — sam decydujesz,\nkiedy się przełączyć (po pobraniu zaproponujemy restart).",
        "ZX Next Unite {latest} is available — running from source, so update with 'git pull' instead of the Windows binary.":
            "Dostępny jest ZX Next Unite {latest} — program działa ze źródeł, więc zaktualizuj poleceniem 'git pull' zamiast pobierać binarium Windows.",
        # ---- emulator update prompts (bodies + buttons) ----
        "A newer version of MAME is available.\n\nInstalled: 0.{installed}\nLatest: {latest}  (0.{latest_num})\nPackage: {asset}\n\nDownload (~{size}) and update your MAME install now?\nThe existing files in the downloads MAME folder will be overwritten.":
            "Dostępna jest nowsza wersja MAME.\n\nZainstalowana: 0.{installed}\nNajnowsza: {latest}  (0.{latest_num})\nPakiet: {asset}\n\nPobrać (~{size}) i zaktualizować instalację MAME teraz?\nIstniejące pliki w folderze MAME zostaną nadpisane.",
        "Close and start {name}":
            "Zamknij i uruchom {name}",
        "Continue hdfmonkey standalone install":
            "Kontynuuj samodzielną instalację hdfmonkey",
        "I've dropped the zip - try again":
            "Wrzuciłem plik zip — spróbuj ponownie",
        "MAME release: {tag}\nPackage: {asset} ({arch})\n\nDownload (~{size}) and install it into the downloads folder?\nNote: the fully extracted install is large (~500 MB).":
            "Wersja MAME: {tag}\nPakiet: {asset} ({arch})\n\nPobrać (~{size}) i zainstalować w folderze pobierania?\nUwaga: w pełni wypakowana instalacja jest duża (~500 MB).",
        "Open download page":
            "Otwórz stronę pobierania",
        "Open downloads folder":
            "Otwórz folder pobierania",
        "The new version was saved as:\n\n{path}\n\nClose ZX Next Unite now and start the new version ({name})?\nYour settings (hdfg.cfg) and downloads are picked up as-is —\nboth versions run from the same folder.":
            "Nowa wersja została zapisana jako:\n\n{path}\n\nZamknąć ZX Next Unite i uruchomić nową wersję ({name})?\nTwoje ustawienia (hdfg.cfg) i pobrane pliki są używane bez zmian —\nobie wersje działają z tego samego folderu.",
        "What's changed:":
            "Co nowego:",
        # ---- emulator / config console (final batch) ----
        "CSpect update check: {reason}.":
            "Sprawdzanie CSpect: {reason}.",
        "CSpect update ▸ FAILED — {error}":
            "Aktualizacja CSpect ▸ NIEPOWODZENIE — {error}",
        "CSpect update ▸ newer build available: installed {installed}, latest {latest}.":
            "Aktualizacja CSpect ▸ dostępna nowsza kompilacja: zainstalowana {installed}, najnowsza {latest}.",
        "CSpect update ▸ user chose to update to {name}.":
            "Aktualizacja CSpect ▸ użytkownik wybrał aktualizację do {name}.",
        "Could not list the MAME releases: {error}":
            "Nie udało się pobrać listy wersji MAME: {error}",
        "ERROR: Failed to launch MAME: {error}":
            "BŁĄD: nie udało się uruchomić MAME: {error}",
        "ERROR: Failed to launch CSpect: {error}":
            "BŁĄD: Nie udało się uruchomić CSpect: {error}",
        "ERROR: could not extract {name}: {error}":
            "BŁĄD: nie udało się wypakować {name}: {error}",
        "ERROR: hdfmonkey failed - A file can't be opened this is commonly caused by strange characters such as quotes and signs":
            "BŁĄD: hdfmonkey zawiódł - nie można otworzyć pliku; zwykle powodują to nietypowe znaki, np. cudzysłowy i symbole",
        "ERROR: hdfmonkey failed - A file can't be opened: {command} this is commonly caused by strange characters such as quotes and signs":
            "BŁĄD: hdfmonkey zawiódł - nie można otworzyć pliku: {command}; zwykle powodują to nietypowe znaki, np. cudzysłowy i symbole",
        "Failed to save configuration file with IOError: {error}":
            "Nie udało się zapisać pliku konfiguracyjnego (IOError): {error}",
        "Found hdfmonkey alongside CSpect: {path}":
            "Znaleziono hdfmonkey obok CSpect: {path}",
        "MAME exited with code {code}.":
            "MAME zakończył działanie z kodem {code}.",
        "MAME install ▸ SUCCESS — MAME detected at: {path}":
            "Instalacja MAME ▸ SUKCES — MAME wykryty w: {path}",
        "Pygame mode unavailable — run: pip install pygame-ce":
            "Tryb pygame niedostępny — uruchom: pip install pygame-ce",
        "Remote unzip: fetching {path} from the image …":
            "Zdalne rozpakowanie: pobieranie {path} z obrazu …",
        "Remote zip: fetching {count} item(s) from the image …":
            "Zdalne pakowanie: pobieranie {count} element(ów) z obrazu …",
        "Saved configuration file.":
            "Zapisano plik konfiguracyjny.",
        "UI language set to '{lang}' to match the system language — change it on the Settings tab.":
            "Język interfejsu ustawiono na '{lang}', zgodnie z językiem systemu — możesz go zmienić w karcie Ustawienia.",
        "ZX Next Unite update available: {latest} (installed {installed}).":
            "Dostępna aktualizacja ZX Next Unite: {latest} (zainstalowana {installed}).",
        "ZX Next Unite update ▸ downloading {asset}…":
            "Aktualizacja ZX Next Unite ▸ pobieranie {asset}…",
        "ZX Next Unite update: could not start {name}: {error}":
            "Aktualizacja ZX Next Unite: nie udało się uruchomić {name}: {error}",
        "ZX Next Unite update: download FAILED: {error}":
            "Aktualizacja ZX Next Unite: pobieranie NIE POWIODŁO SIĘ: {error}",
        "ZX Next Unite update: downloaded — start it any time: {path}":
            "Aktualizacja ZX Next Unite: pobrana — możesz ją uruchomić w dowolnej chwili: {path}",
        "ZX Next Unite update: starting {name} and closing…":
            "Aktualizacja ZX Next Unite: uruchamianie {name} i zamykanie…",
        "ZX Next Unite update: unpacked to {path}":
            "Aktualizacja ZX Next Unite: rozpakowano do {path}",
        "Extracting {name} from the image, then starting CSpect…":
            "Wypakowywanie {name} z obrazu, potem uruchomienie CSpect…",
        "Start CSpect: {name} could not be read from the image, CSpect was not started.":
            "Uruchom CSpect: nie udało się odczytać {name} z obrazu — CSpect nie został uruchomiony.",
        "Extracting {name} from the image, then sending it via NextSync…":
            "Wypakowywanie {name} z obrazu, potem wysłanie przez NextSync…",
        "Send via NextSync: {name} could not be read from the image, nothing was sent.":
            "Wyślij przez NextSync: nie udało się odczytać {name} z obrazu — nic nie zostało wysłane.",
        "Send via NextSync {name}":
            "Wyślij {name} przez NextSync",
        "Start NextSync Remote Explorer":
            "Uruchom Remote Explorer NextSync",
        "Stop NextSync Remote Explorer":
            "Zatrzymaj Remote Explorer NextSync",
        "Please select a sync root first on the NextSync Remote Explorer tab and retry.":
            "Najpierw wybierz folder główny synchronizacji na karcie Remote Explorer NextSync i spróbuj ponownie.",
        # ---- CSpect auto-start actions (SD Card tab menus) ----
        "Send to SD Card and start CSpect with file {name}":
            "Wyślij na kartę SD i uruchom CSpect z plikiem {name}",
        "Send to SD Card and start CSpect: the transfer failed, CSpect was not started.":
            "Wyślij na kartę SD i uruchom CSpect: transfer nie powiódł się — CSpect nie został uruchomiony.",
        "Sending {name} to the SD card image, then starting CSpect…":
            "Wysyłanie {name} do obrazu karty SD, potem uruchomienie CSpect…",
        "Start CSpect with file {name}":
            "Uruchom CSpect z plikiem {name}",
        "Start MAME with file {name}":
            "Uruchom MAME z plikiem {name}",
        "Could not start {emulator}":
            "Nie udało się uruchomić {emulator}",
        "Could not prepare a folder for {name}: {error}":
            "Nie udało się przygotować folderu dla {name}: {error}",
        "Start {emulator}: {name} could not be downloaded from the Next, "
        "{emulator} was not started.":
            "Uruchom {emulator}: nie udało się pobrać {name} z Nexta — "
            "{emulator} nie został uruchomiony.",
        "Downloading {name} from the Next, then starting {emulator}…":
            "Pobieranie {name} z Nexta, potem uruchomienie {emulator}…",
        "Downloading {name}…":
            "Pobieranie {name}…",
        "Start MAME: could not prepare the staging folder {path} ({error}).":
            "Uruchom MAME: nie udało się przygotować folderu tymczasowego {path} ({error}).",
        "Send to SD Card and start MAME with file {name}":
            "Wyślij na kartę SD i uruchom MAME z plikiem {name}",
        "Extracting {name} from the image, then starting MAME…":
            "Wypakowywanie {name} z obrazu, potem uruchomienie MAME…",
        "Start MAME: {name} could not be read from the image, MAME was not started.":
            "Uruchom MAME: nie udało się odczytać {name} z obrazu — MAME nie zostało uruchomione.",
        "Send to SD Card and start MAME: the transfer failed, MAME was not started.":
            "Wyślij na kartę SD i uruchom MAME: transfer nie powiódł się — MAME nie zostało uruchomione.",
        "Sending {name} to the SD card image, then starting MAME…":
            "Wysyłanie {name} do obrazu karty SD, potem uruchomienie MAME…",
        "MAME cannot load {name} directly; starting MAME without it.":
            "MAME nie może załadować {name} bezpośrednio — MAME zostanie uruchomione bez tego pliku.",
        # ---- dialogs (message boxes) ----
        "CSpect update available":
            "Dostępna aktualizacja CSpect",
        "Choose another release…":
            "Wybierz inną wersję…",
        "Close":
            "Zamknij",
        "Download and install":
            "Pobierz i zainstaluj",
        "File or directory already exists locally.":
            "Plik lub katalog już istnieje lokalnie.",
        "File or directory exists":
            "Plik lub katalog już istnieje",
        "Ignore (always in this sync)":
            "Pomiń (zawsze w tej synchronizacji)",
        "Ignore (one time)":
            "Pomiń (raz)",
        "Install from .zip…":
            "Zainstaluj z pliku .zip…",
        "Install hdfmonkey":
            "Zainstaluj hdfmonkey",
        "Later":
            "Później",
        "MAME update available":
            "Dostępna aktualizacja MAME",
        "Open itch.io page":
            "Otwórz stronę itch.io",
        "Open the releases page":
            "Otwórz stronę wydań",
        "Overwrite local file (always in this sync)":
            "Nadpisz plik lokalny (zawsze w tej synchronizacji)",
        "Overwrite local file (one time)":
            "Nadpisz plik lokalny (raz)",
        "The automated download failed.":
            "Automatyczne pobieranie nie powiodło się.",
        "This is going to completely delete the files in {path} and its sub folders, so they will be unrecoverable.\n\nAre you sure want to continue?":
            "Spowoduje to całkowite usunięcie plików w {path} i jego podfolderach — bez możliwości odzyskania.\n\nCzy na pewno chcesz kontynuować?",
        "Tip: set a default for this in Settings → \"NextSync — when a sent file or directory exists locally\".":
            "Wskazówka: ustaw domyślne zachowanie w Ustawieniach → \"NextSync — when a sent file or directory exists locally\".",
        "Uninstall":
            "Odinstaluj",
        "Update":
            "Aktualizuj",
        "Update downloaded":
            "Aktualizacja pobrana",
        "Yes":
            "Tak",
        "You can download it manually from the itch.io page in your browser, then install it from the downloaded .zip.":
            "Możesz pobrać go ręcznie ze strony itch.io w przeglądarce, a następnie zainstalować z pobranego pliku .zip.",
        "ZX Next Unite update available":
            "Dostępna aktualizacja ZX Next Unite",
        "hdfmonkey download failed":
            "Pobieranie hdfmonkey nie powiodło się",
        "itch.io download":
            "Pobieranie z itch.io",
        # ---- emulator install / update console ----
        "CSpect update check skipped: {reason}":
            "Pominięto sprawdzanie CSpect: {reason}",
        "CSpect update ▸ user cancelled the update.":
            "Aktualizacja CSpect ▸ użytkownik anulował aktualizację.",
        "ERROR: CSpect.exe is not present in the same local directory as zx-next-unite. Please install it from http://cspect.org":
            "BŁĄD: CSpect.exe nie znajduje się w tym samym katalogu co zx-next-unite. Zainstaluj go z http://cspect.org",
        "ERROR: MAME executable not found on PATH. Cannot launch MAME.":
            "BŁĄD: nie znaleziono pliku wykonywalnego MAME w PATH. Nie można uruchomić MAME.",
        "Listing the available MAME releases…":
            "Pobieranie listy dostępnych wersji MAME…",
        "MAME install ▸ FAILED — the download and extraction finished, but no mame.exe could be found in downloads/mame.":
            "Instalacja MAME ▸ NIEPOWODZENIE — pobieranie i wypakowanie zakończone, ale nie znaleziono mame.exe w downloads/mame.",
        "MAME install ▸ FAILED — {error}. You can download it manually from https://www.mamedev.org/release.html":
            "Instalacja MAME ▸ NIEPOWODZENIE — {error}. Możesz pobrać go ręcznie z https://www.mamedev.org/release.html",
        "MAME install ▸ Starting: {tag} ({asset}, ~{size}).":
            "Instalacja MAME ▸ rozpoczynanie: {tag} ({asset}, ~{size}).",
        "MAME install ▸ release picker cancelled.":
            "Instalacja MAME ▸ anulowano wybór wersji.",
        "MAME is ready to launch now — no restart needed. Use the '🕹  Launch Mame' button.":
            "MAME jest gotowy do uruchomienia — bez restartu. Użyj przycisku '🕹  Launch Mame'.",
        "MAME update check: could not determine the installed MAME version; skipping.":
            "Sprawdzanie MAME: nie udało się ustalić zainstalowanej wersji — pomijanie.",
        "MAME update check: could not determine the latest release; skipping.":
            "Sprawdzanie MAME: nie udało się ustalić najnowszej wersji — pomijanie.",
        "MAME update check: could not reach the release site; skipping.":
            "Sprawdzanie MAME: nie udało się połączyć z witryną wydań — pomijanie.",
        "MAME update ▸ user chose to pick a release manually.":
            "Aktualizacja MAME ▸ użytkownik wybrał ręczny wybór wersji.",
        "MAME update ▸ user chose to update to {tag}.":
            "Aktualizacja MAME ▸ użytkownik wybrał aktualizację do {tag}.",
        "On MacOS and Linux mono is required as it runs under it. Please make sure mono is installed.":
            "W systemach MacOS i Linux wymagany jest mono, ponieważ program działa pod nim. Upewnij się, że mono jest zainstalowany.",
        "Running as a Flatpak: mono must be installed on the HOST system — the launch is delegated there via flatpak-spawn.":
            "Uruchomiono jako Flatpak: mono musi być zainstalowany w systemie GOSPODARZA — uruchomienie jest tam delegowane przez flatpak-spawn.",
        "Select a valid ZX Spectrum Next disk image (.img/.hdf) before launching MAME.":
            "Wybierz prawidłowy obraz dysku ZX Spectrum Next (.img/.hdf) przed uruchomieniem MAME.",
        "ZX Next Unite update check: could not reach GitHub (offline, or no release published yet); skipping.":
            "Sprawdzanie ZX Next Unite: nie udało się połączyć z GitHubem (brak sieci lub brak wydań) — pomijanie.",
        "ZX Next Unite update check: running as a Flatpak — updates come from your software center, skipping.":
            "Sprawdzanie ZX Next Unite: uruchomiono jako Flatpak — aktualizacje pochodzą z centrum oprogramowania, pomijanie.",
        "ZX Next Unite update ▸ skipped by user.":
            "Aktualizacja ZX Next Unite ▸ pominięta przez użytkownika.",
        "ZX Next Unite update: download cancelled.":
            "Aktualizacja ZX Next Unite: pobieranie anulowane.",
        # ---- SD Card tab: console + dialogs ----
        "Confirm Deletion":
            "Potwierdź usunięcie",
        "Create":
            "Utwórz",
        "Create New Folder":
            "Utwórz nowy folder",
        "Created {name} in {folder} on the image ({count} file(s), {bytes} bytes).":
            "Utworzono {name} w {folder} na obrazie ({count} plik(ów), {bytes} bajtów).",
        "Delete files from the image to free space, or switch to a larger image.\nLarger SD card images can be downloaded from:":
            "Usuń pliki z obrazu, aby zwolnić miejsce, lub przełącz się na większy obraz.\nWiększe obrazy kart SD można pobrać z:",
        "Download":
            "Pobierz",
        "Download failed: no valid destination folder.":
            "Pobieranie nie powiodło się: brak prawidłowego folderu docelowego.",
        "Downloading {name} from {url}":
            "Pobieranie {name} z {url}",
        "ERROR: hdfmonkey could not be found. Use the 'Download and install HDF Monkey' button (bottom right of the SD Card tab) to install it automatically, or do a full CSpect install from the itch.io tab, which also bundles hdfmonkey. It can also be installed manually from https://github.com/gasman/hdfmonkey — restart the app once installed.":
            "BŁĄD: nie znaleziono hdfmonkey. Użyj przycisku 'Pobierz i zainstaluj HDF Monkey' (prawy dolny róg karty SD Card), aby zainstalować go automatycznie, albo wykonaj pełną instalację CSpect z karty itch.io, która również zawiera hdfmonkey. Można go też zainstalować ręcznie z https://github.com/gasman/hdfmonkey — po instalacji uruchom aplikację ponownie.",
        "Extracted disk image: {path}":
            "Wypakowano obraz dysku: {path}",
        "Extracted {count} file(s) from {name} into {folder} on the image.":
            "Wypakowano {count} plik(ów) z {name} do {folder} na obrazie.",
        "Extracting image... %p%":
            "Wypakowywanie obrazu... %p%",
        "Failed downloading NextZXOS image: {error}":
            "Nie udało się pobrać obrazu NextZXOS: {error}",
        "Load Failed":
            "Błąd wczytywania",
        "The image was extracted but could not be loaded:":
            "Obraz został wypakowany, ale nie udało się go wczytać:",
        "Failed executing hdfmonkey, please make sure it is installed in the same local directory as zx-next-unite.":
            "Nie udało się uruchomić hdfmonkey — upewnij się, że jest zainstalowany w tym samym katalogu co zx-next-unite.",
        "Failed extracting NextZXOS image: {error}":
            "Nie udało się wypakować obrazu NextZXOS: {error}",
        "Failed loading image: {path}.":
            "Nie udało się wczytać obrazu: {path}.",
        "No SD-card disk image selected — pick or create a .img/.hdf at the top of this tab to unlock the emulator Launch buttons.":
            "Nie wybrano obrazu karty SD — wybierz lub utwórz plik .img/.hdf u góry tej karty, aby odblokować przyciski uruchamiania emulatora.",
        "Nothing to move: items are already in this folder.":
            "Nie ma czego przenosić: elementy są już w tym folderze.",
        "Only {free} MB free out of {total} MB ({used} % used, {pct} % free).":
            "Tylko {free} MB wolnych z {total} MB ({used} % zajęte, {pct} % wolne).",
        "Please load an image file first !":
            "Najpierw wczytaj plik obrazu!",
        "Please load an image first!":
            "Najpierw wczytaj obraz!",
        "Please select an image file or folder first to delete!":
            "Najpierw wybierz plik lub folder obrazu do usunięcia!",
        "Please select an image file or folder first to rename!":
            "Najpierw wybierz plik lub folder obrazu do zmiany nazwy!",
        "Remote unzip cancelled — the image is unchanged.":
            "Zdalne rozpakowanie anulowane — obraz bez zmian.",
        "Remote unzip: download from the image failed or was cancelled — the image is unchanged.":
            "Zdalne rozpakowanie: pobieranie z obrazu nie powiodło się lub zostało anulowane — obraz bez zmian.",
        "Remote unzip: upload into the image failed or was cancelled.":
            "Zdalne rozpakowanie: wysyłanie do obrazu nie powiodło się lub zostało anulowane.",
        "Remote zip cancelled — no zip was created.":
            "Zdalne pakowanie anulowane — nie utworzono pliku zip.",
        "Remote zip: download from the image failed or was cancelled — no zip was created.":
            "Zdalne pakowanie: pobieranie z obrazu nie powiodło się lub zostało anulowane — nie utworzono pliku zip.",
        "Remote zip: upload into the image failed or was cancelled.":
            "Zdalne pakowanie: wysyłanie do obrazu nie powiodło się lub zostało anulowane.",
        "SD Image Nearly Full":
            "Obraz SD prawie pełny",
        "The SD card image is nearly full.":
            "Obraz karty SD jest prawie pełny.",
        "The hdfmonkey provided by the CSpect itch.io package is not executable. Make it executable by running:":
            "hdfmonkey dołączony do pakietu CSpect z itch.io nie jest wykonywalny. Nadaj mu prawo wykonywania poleceniem:",
        "The image is completely full ({total} MB capacity, 0 MB free).":
            "Obraz jest całkowicie pełny (pojemność {total} MB, 0 MB wolnych).",
        # ---- SD Card console: banner, detection, update checks ----
        "CSpect - by Mike Dailly http://cspect.org":
            "CSpect - autor: Mike Dailly http://cspect.org",
        "CSpect is up to date (installed {installed}, latest {latest}).":
            "CSpect jest aktualny (zainstalowana {installed}, najnowsza {latest}).",
        "Checking for a newer MAME release…":
            "Sprawdzanie nowszej wersji MAME…",
        "Checking for a newer ZX Next Unite release on GitHub…":
            "Sprawdzanie w GitHubie nowszej wersji ZX Next Unite…",
        "Checking itch.io for a newer CSpect release…":
            "Sprawdzanie w itch.io nowszej wersji CSpect…",
        "HDF Monkey - by Matt Westcott":
            "HDF Monkey - autor: Matt Westcott",
        "Inspired by HDFM-GOOEY - by em00k":
            "Zainspirowane przez HDFM-GOOEY - autor: em00k",
        "Loaded configuration file.":
            "Wczytano plik konfiguracyjny.",
        "MAME - ZX Spectrum Next support by Holub https://wiki.specnext.dev/MAME:Installing":
            "MAME - obsługa ZX Spectrum Next: Holub https://wiki.specnext.dev/MAME:Installing",
        "MAME is up-to-date (installed 0.{installed}, latest 0.{latest}).":
            "MAME jest aktualny (zainstalowana 0.{installed}, najnowsza 0.{latest}).",
        "MAME is up-to-date with a patched version (installed 0.{installed}, latest 0.{latest}).":
            "MAME jest aktualny w wersji ze zmianami (zainstalowana 0.{installed}, najnowsza 0.{latest}).",
        "MAME version: {version}":
            "Wersja MAME: {version}",
        "NextSync - by Jari Komppa and Julien Clauzel":
            "NextSync - autorzy: Jari Komppa i Julien Clauzel",
        "Using CSpect under downloads/cspect: {path}":
            "Używany CSpect w downloads/cspect: {path}",
        "Using MAME under: {path}":
            "Używany MAME w: {path}",
        "Using hdfmonkey bundled with CSpect: {path}":
            "Używany hdfmonkey dołączony do CSpect: {path}",
        "Welcome to ZX Next Unite {version}":
            "Witaj w ZX Next Unite {version}",
        "ZX Next Unite is up to date (installed {installed}, latest {latest}).":
            "ZX Next Unite jest aktualny (zainstalowana {installed}, najnowsza {latest}).",
        "zx-next-unite - by Julien Clauzel 2024":
            "zx-next-unite - autor: Julien Clauzel 2024",
        "No image loaded": "Nie wczytano obrazu",
        # ---- itch.io item viewer + web-link labels ----
        "About": "Informacje",
        "Open on {site}": "Otwórz w {site}",
        "Open {url}": "Otwórz {url}",
        "✓  Re-install": "✓  Zainstaluj ponownie",
        "⬇  Install": "⬇  Zainstaluj",
        "⬇  Installing…": "⬇  Instalowanie…",
        "📂  Open download folder": "📂  Otwórz folder pobierania",
        # ---- gallery item viewer (Classic + Retro) ----
        "🌐  Open on website": "🌐  Otwórz w witrynie",
        "🌐  Open on {site}": "🌐  Otwórz w {site}",
        "📂  Open install folder": "📂  Otwórz folder instalacji",
        "🗑  Uninstall": "🗑  Odinstaluj",
        # ---- NextSync log lines (user-facing; protocol diagnostics stay English) ----
        "(-send saves received files under: {folder})":
            "(-send zapisuje odebrane pliki w: {folder})",
        "Aliases:":
            "Aliasy:",
        "Cancel requested — stopping after current file":
            "Zażądano anulowania — zatrzymanie po bieżącym pliku",
        "Cannot create {path}: {error}":
            "Nie można utworzyć {path}: {error}",
        "Closing connection":
            "Zamykanie połączenia",
        "Connected by {address} port {port}":
            "Połączono z {address} port {port}",
        "Disconnected":
            "Rozłączono",
        "Existing-file policy: {policy} (change in Settings -> 'NextSync - when a sent file or directory exists locally').":
            "Zasada dla istniejących plików: {policy} (zmień w Ustawieniach -> 'NextSync - when a sent file or directory exists locally').",
        "Failed to rename {path}: {error}":
            "Nie udało się zmienić nazwy {path}: {error}",
        "IP addresses:":
            "Adresy IP:",
        "Import failed: no valid destination folder.":
            "Import nie powiódł się: brak prawidłowego folderu docelowego.",
        "Navigate to a folder in the left local file explorer, press 'Set current folder as new sync root folder' to choose a sync root and then press the 'Start Classic NextSync server' button.":
            "Przejdź do folderu w lewym eksploratorze, naciśnij 'Set current folder as new sync root folder', aby wybrać katalog główny synchronizacji, a potem naciśnij 'Start Classic NextSync server'.",
        "NextSync HTTP bridge NOT started: {error}":
            "Mostek HTTP NextSync NIE został uruchomiony: {error}",
        "NextSync HTTP bridge listening on port {port}":
            "Mostek HTTP NextSync nasłuchuje na porcie {port}",
        "NextSync HTTP bridge stopped.":
            "Mostek HTTP NextSync zatrzymany.",
        "NextSync HTTP bridge: bearer-token protection is ON (requests must carry the {header} header; others get HTTP 401)":
            "Mostek HTTP NextSync: ochrona tokenem jest WŁĄCZONA (żądania muszą zawierać nagłówek {header}; pozostałe otrzymają HTTP 401)",
        "NextSync is already running — please wait for it to finish.":
            "NextSync już działa — poczekaj na zakończenie.",
        "NextSync listening to port {port}":
            "NextSync nasłuchuje na porcie {port}",
        "NextSync server, protocol version: {version}":
            "Serwer NextSync, wersja protokołu: {version}",
        "No network detected - connect to Wi-Fi/Ethernet to see the address your Next should sync to.":
            "Nie wykryto sieci — połącz się z Wi-Fi/Ethernet, aby zobaczyć adres, z którym ma synchronizować się Next.",
        "Note":
            "Uwaga",
        "Nothing (more) to sync":
            "Nie ma (już) nic do synchronizacji",
        "Now run one of these commands on your Next:":
            "Teraz uruchom jedno z tych poleceń na swoim Next:",
        "Primary IP:":
            "Główny adres IP:",
        "Received {name} ({bytes} bytes)":
            "Odebrano {name} ({bytes} bajtów)",
        "Receiving files from the Next...":
            "Odbieranie plików z Nexta...",
        "Receiving: {name} -> {path}":
            "Odbieranie: {name} -> {path}",
        "Remote .sync5 update failed while reading {path}: {error} — nothing was sent.":
            "Zdalna aktualizacja .sync5 nie powiodła się przy odczycie {path}: {error} — nic nie zostało wysłane.",
        "Remote .sync5 update refused: {path} does not carry the expected 'NextSync {version}' banner — wrong or stale file.":
            "Zdalna aktualizacja .sync5 odrzucona: {path} nie zawiera oczekiwanego oznaczenia 'NextSync {version}' — zły lub przestarzały plik.",
        "Remote .sync5 update: staging {path} ({size} bytes)…":
            "Zdalna aktualizacja .sync5: przygotowywanie {path} ({size} bajtów)…",
        "Remote .sync5 update: staged copy verified ({size} bytes) — swapping it in…":
            "Zdalna aktualizacja .sync5: przygotowana kopia zweryfikowana ({size} bajtów) — podmienianie…",
        "Remote .sync5 update FAILED mid-swap: the Next may be missing {target}. If .sync5 no longer starts, rename {backup} back to sync5 in the NextZXOS Browser (the staged {staged} can be deleted).":
            "Zdalna aktualizacja .sync5 NIE POWIODŁA SIĘ w trakcie podmiany: Next może nie mieć {target}. Jeśli .sync5 już się nie uruchamia, zmień nazwę {backup} z powrotem na sync5 w przeglądarce NextZXOS (przygotowany {staged} można usunąć).",
        "Remote .sync5 update complete: {version} is on the card. The session will now close — run {command} on the Next to start the new dot.":
            "Zdalna aktualizacja .sync5 zakończona: {version} jest już na karcie. Sesja zostanie teraz zamknięta — wpisz {command} na Next, aby uruchomić nowe polecenie dot.",
        "Remote .sync5 update failed: {reason}. Nothing was swapped — the Next still runs its current dot.":
            "Zdalna aktualizacja .sync5 nie powiodła się: {reason}. Nic nie zostało podmienione — Next nadal używa dotychczasowego polecenia dot.",
        "Remote explorer: connected to {address}":
            "Eksplorator zdalny: połączono z {address}",
        "Remote explorer: connection error from the Next ({error}) — session over.":
            "Eksplorator zdalny: błąd połączenia z Nextem ({error}) — sesja zakończona.",
        "Remote explorer: the Next closed the connection.":
            "Eksplorator zdalny: Next zamknął połączenie.",
        "Remote explorer: no word from the Next for {seconds}s — assuming it is gone (powered off? Wi-Fi dropped?)":
            "Eksplorator zdalny: brak sygnału od Nexta od {seconds}s — uznano za utracony (wyłączony? zerwane Wi-Fi?)",
        "Remote explorer: turned away a second Next at {address} — a session is already active (Busy).":
            "Eksplorator zdalny: odrzucono drugiego Nexta z {address} — sesja jest już aktywna (Busy).",
        "Remote explorer: server keeps running in the background — stop it from the Remote Explorer view.":
            "Eksplorator zdalny: serwer nadal działa w tle — zatrzymaj go w widoku Remote Explorer.",
        "Remote explorer: navigate to a folder in the left file explorer, press 'Set current folder as new sync root folder', click 'Start Remote Explorer NextSync server', then run {command} on your Next.":
            "Eksplorator zdalny: przejdź do folderu w lewym eksploratorze, naciśnij 'Set current folder as new sync root folder', kliknij 'Start Remote Explorer NextSync server', a następnie uruchom {command} na swoim Next.",
        "Remote explorer: port {port} is already in use — is another ZX-Next-Unite (or NextSync server) already running?":
            "Eksplorator zdalny: port {port} jest już zajęty — czy działa inny ZX-Next-Unite (lub serwer NextSync)?",
        "Remote explorer: the Next disconnected (BREAK / Bye) — restarting the listen server; run {command} on your Next to reconnect.":
            "Eksplorator zdalny: Next się rozłączył (BREAK / Bye) — ponowne uruchamianie serwera nasłuchu; uruchom {command} na Next, aby połączyć ponownie.",
        "Remote explorer: the Next disconnected (BREAK / Bye). Press 'Start Remote Explorer NextSync server' to accept a new connection.":
            "Eksplorator zdalny: Next się rozłączył (BREAK / Bye). Naciśnij 'Start Remote Explorer NextSync server', aby przyjąć nowe połączenie.",
        "Remote explorer: waiting for {command} on port {port}…":
            "Eksplorator zdalny: oczekiwanie na {command} na porcie {port}…",
        "Renamed: {old} -> {new}":
            "Zmieniono nazwę: {old} -> {new}",
        "Running on host:":
            "Działa na komputerze:",
        "Saving incoming files under: {folder}":
            "Zapisywanie przychodzących plików w: {folder}",
        "Send via NextSync: nothing to send in {folder}.":
            "Wyślij przez NextSync: nie ma czego wysłać w {folder}.",
        "Sending {folder} via Remote Explorer (-listen) → {target} …":
            "Wysyłanie {folder} przez Eksplorator zdalny (-listen) → {target} …",
        "Set a sync root folder first: navigate to the folder in the left local file explorer and press 'Set current folder as new sync root folder'.":
            "Najpierw wybierz folder główny synchronizacji: przejdź do niego w lewym eksploratorze i naciśnij 'Set current folder as new sync root folder'.",
        "Skipped (already exists): {path}":
            "Pominięto (już istnieje): {path}",
        "Skipped {path}: cannot import a folder into itself.":
            "Pominięto {path}: nie można zaimportować folderu do niego samego.",
        "Stop the running sync before starting the remote server.":
            "Zatrzymaj trwającą synchronizację przed uruchomieniem serwera zdalnego.",
        "Sync file list has {count} files.":
            "Lista synchronizacji zawiera {count} plików.",
        "Sync point file {name} not found, syncing all files regardless of timestamp.":
            "Nie znaleziono pliku punktu synchronizacji {name} — synchronizowane będą wszystkie pliki niezależnie od daty.",
        "Sync point updated with {count} received file(s)":
            "Punkt synchronizacji zaktualizowany o {count} odebranych plików",
        "Upload connection closed":
            "Połączenie wysyłania zamknięte",
        "Upload finished, {count} file(s) received":
            "Wysyłanie zakończone, odebrano {count} plik(ów)",
        "Using {folder} as sync root":
            "Używany katalog główny synchronizacji: {folder}",
        "WARNING":
            "OSTRZEŻENIE",
        "Warning":
            "Ostrzeżenie",
        "Warning! Ignore file {name} not found in directory. All files will be synced, possibly including this file.":
            "Uwaga! Nie znaleziono pliku wykluczeń {name} w katalogu. Zsynchronizowane zostaną wszystkie pliki, być może łącznie z tym.",
        "{kb} kilobytes payload, {rate} kBps effective speed":
            "{kb} kilobajtów danych, prędkość efektywna {rate} kBps",
        "{kb} kilobytes transferred in {seconds} seconds, {rate} kBps":
            "Przesłano {kb} kilobajtów w {seconds} s, {rate} kBps",
        "{severity}: Ready to sync {count} files, {kb} kilobytes.":
            "{severity}: gotowe do synchronizacji {count} plików, {kb} kilobajtów.",
        # ---- checkboxes ----
        "Alien Floyd's — animated background on all tabs (Retro/pygame)":
            "Alien Floyd's — animowane tło na wszystkich kartach (Retro/pygame)",
        "Alien Floyd's — show the full-window 'Alien Floyd's' tab (Retro/pygame)":
            "Alien Floyd's — pokaż pełnoekranową kartę «Alien Floyd's» (Retro/pygame)",
        "Check for CSpect update on itch.io on startup":
            "Sprawdzaj aktualizacje CSpect na itch.io przy starcie",
        "Check for ZX Next Unite updates at startup on Github":
            "Sprawdzaj aktualizacje ZX Next Unite na GitHubie przy starcie",
        "Check for a newer MAME version at startup":
            "Sprawdzaj nowszą wersję MAME przy starcie",
        "Disable 'No emulators detected' message at startup":
            "Wyłącz komunikat «Nie wykryto emulatorów» przy starcie",
        "Do not prompt for confirmation on deletion.":
            "Nie pytaj o potwierdzenie przy usuwaniu.",
        "Enable NextSync HTTP bridge (web server for the Next's .http command)":
            "Włącz mostek HTTP NextSync (serwer WWW dla polecenia .http Nexta)",
        "Enable crash log file generation":
            "Włącz zapisywanie dziennika awarii",
        "Enable multi API endpoints search (GetIt, ZXDB & zxArt search together).":
            "Włącz wyszukiwanie w wielu API naraz (GetIt, ZXDB i zxArt).",
        "Enable search autocompletion.": "Włącz autouzupełnianie wyszukiwania.",
        "NextSync — starfield log animation (Retro/pygame mode)":
            "NextSync — animacja gwiazd w dzienniku (tryb Retro/pygame)",
        "Perform pre-availability check on Downloads (ZXDB & zxArt).":
            "Sprawdzaj dostępność plików do pobrania (ZXDB i zxArt).",
        "Require bearer token": "Wymagaj tokenu dostępu",
        "SD Card - Warn when an image is nearly full.":
            "Karta SD — ostrzegaj, gdy obraz jest prawie pełny.",
        "Send deleted files to the Recycle Bin (local file explorers).":
            "Przenoś usuwane pliki do Kosza (lokalne eksploratory).",
        "Show the itch.io tab (browse & install your itch.io collections)":
            "Pokaż kartę itch.io (przeglądaj i instaluj swoje kolekcje)",
        "Slow transfer": "Wolny transfer",
        "Unite! — Invaders background animation (Retro/pygame mode)":
            "Unite! — animacja tła Invaders (tryb Retro/pygame)",
        # ---- radio buttons / group boxes ----
        "Always sync (send everything)": "Synchronizuj zawsze (wysyłaj wszystko)",
        "Sync changed files (continuous)": "Synchronizuj zmiany (ciągle)",
        "Sync once": "Synchronizuj raz",
        "Sync mode": "Tryb synchronizacji",
        # ---- placeholders ----
        "Filter by name, type or size...": "Filtruj wg nazwy, typu lub rozmiaru…",
        "Filter by name...": "Filtruj wg nazwy…",
        "Local folder path...": "Ścieżka folderu lokalnego…",
        "New directory name ...": "Nazwa nowego katalogu…",
        "Paste your personal API key (itch.io → Settings → API keys)":
            "Wklej swój osobisty klucz API (itch.io → Settings → API keys)",
        "Path inside the SD card image...": "Ścieżka wewnątrz obrazu SD…",
        "SD card image path...": "Ścieżka obrazu karty SD…",
        "Search ZXDB games... (leave empty for random selection)":
            "Szukaj gier w ZXDB… (puste = wybór losowy)",
        "Search across GetIt + ZXDB + zxArt...":
            "Szukaj w GetIt + ZXDB + zxArt…",
        "Search files... (leave empty for latest 20)":
            "Szukaj plików… (puste = ostatnie 20)",
        "Search your itch.io library (collections + purchases)…":
            "Przeszukaj swoją bibliotekę itch.io (kolekcje i zakupy)…",
        "Search zxART productions... (leave empty to browse latest)":
            "Szukaj produkcji w zxART… (puste = najnowsze)",
        "Sync root folder...": "Katalog główny synchronizacji…",
        "bearer token (generated when you enable the checkbox)":
            "token dostępu (generowany po włączeniu opcji)",
        # ---- short tooltips ----
        "Browse mode": "Tryb przeglądania",
        "Search mode": "Tryb wyszukiwania",
        "Pick a letter": "Wybierz literę",
        "Double-click to enlarge": "Kliknij dwukrotnie, aby powiększyć",
        "Double-click to open full view": "Kliknij dwukrotnie, aby otworzyć pełny widok",
        "Generate a new random 64-character bearer token":
            "Wygeneruj nowy losowy token (64 znaki)",
        "Select a disk image to be loaded.": "Wybierz obraz dysku do wczytania.",
        "No SD card image is currently loaded.":
            "Żaden obraz karty SD nie jest wczytany.",
        "Re-read the current local folder from disk.":
            "Odczytaj ponownie bieżący folder lokalny z dysku.",
        "Drag to resize the file explorers / log window split.":
            "Przeciągnij, aby zmienić podział eksploratory / dziennik.",
        "Drag to resize the results / MOTD split.":
            "Przeciągnij, aby zmienić podział wyniki / MOTD.",
        "Drag to set the background image opacity (0–100 %).":
            "Przeciągnij, aby ustawić nieprzezroczystość tła (0–100 %).",
        "Type a value 0–100 to set the background image opacity.":
            "Wpisz wartość 0–100 dla nieprzezroczystości tła.",
        "Preview of the selected background image.":
            "Podgląd wybranego obrazu tła.",
        "Open https://itch.io/ in your browser":
            "Otwórz https://itch.io/ w przeglądarce",
        "Connect to itch.io using the API key above.":
            "Połącz z itch.io przy użyciu powyższego klucza API.",
        "Disconnect from itch.io and clear the listed items.":
            "Rozłącz z itch.io i wyczyść wyświetlone pozycje.",
        "Open https://itch.io/user/settings/api-keys in your browser":
            "Otwórz https://itch.io/user/settings/api-keys w przeglądarce",
        "Color used for directory name entries in the image explorer.":
            "Kolor nazw katalogów w eksploratorze obrazu.",
        "Color used for file name entries in the image explorer.":
            "Kolor nazw plików w eksploratorze obrazu.",
        "Color used for the 'DIR' type label column of directory entries.":
            "Kolor kolumny typu «DIR» dla katalogów.",
        "Color used for the '[Up Directory..]' navigation row in the image explorer.":
            "Kolor wiersza nawigacji «[Up Directory..]».",
        "Color used for the file extension column in the image explorer.":
            "Kolor kolumny rozszerzenia pliku.",
        "Color used for the file size column in the image explorer.":
            "Kolor kolumny rozmiaru pliku.",
        "Background color behind the file explorers and the whole app window.":
            "Kolor tła za eksploratorami plików i całym oknem aplikacji.",
        "Discard the hand-picked colors above and restore the themed defaults.":
            "Odrzuca wybrane powyżej kolory i przywraca domyślne wartości motywu.",
        "MAME display aspect ratio (-aspect).":
            "Proporcje obrazu MAME (-aspect).",
        "Perform a single sync and then stop the server.":
            "Wykonaj jedną synchronizację i zatrzymaj serwer.",
        "Keep listening and send every file each time, ignoring the sync point.":
            "Nasłuchuj dalej i wysyłaj za każdym razem wszystkie pliki, ignorując punkt synchronizacji.",
        "Keep listening and send only files that are new or changed since the\nlast sync (skips files recorded in the sync point). The default mode.":
            "Nasłuchuj dalej i wysyłaj tylko pliki nowe lub zmienione od ostatniej\nsynchronizacji (pomija pliki zapisane w punkcie synchronizacji).\nTryb domyślny.",
        "Make the folder currently shown in the explorer above the new sync root.":
            "Ustaw folder pokazany w eksploratorze powyżej jako nowy katalog główny synchronizacji.",
        "Pick a random page from the full GetIt catalogue and show its entries.":
            "Wybierz losową stronę katalogu GetIt i pokaż jej pozycje.",
        "Pick a random page of zxART productions and show its entries.":
            "Wybierz losową stronę produkcji zxART i pokaż ją.",
        "Show the most recent zxART productions/pictures (sorted by date).":
            "Pokaż najnowsze produkcje/obrazy zxART (wg daty).",
        "Show the most recently added/updated ZXDB games.":
            "Pokaż ostatnio dodane lub zaktualizowane gry w ZXDB.",
        "Fetch random entries from GetIt + ZXDB + zxArt and merge them here":
            "Pobierz losowe pozycje z GetIt + ZXDB + zxArt i połącz je tutaj",
        "Fetch the latest releases from GetIt + ZXDB + zxArt and merge them here":
            "Pobierz najnowsze wydania z GetIt + ZXDB + zxArt i połącz je tutaj",
        "zxART catalog display language.\nPersisted across sessions in the config file.":
            "Język katalogu zxART.\nZapisywany w pliku konfiguracji.",
        "Requires the optional 'pygame-ce' package.\nInstall with: pip install pygame-ce":
            "Wymaga opcjonalnego pakietu «pygame-ce».\nInstalacja: pip install pygame-ce",
        "Load a ZX Spectrum Next disk image first — then CSpect can boot it from the mounted SD card.":
            "Najpierw wczytaj obraz dysku ZX Spectrum Next — CSpect uruchomi go z zamontowanej karty SD.",
        "Select emulator image file: {path}":
            "Wybierz obraz dysku dla emulatora: {path}",
        "No writable disk image available.":
            "Brak dostępnego zapisywalnego obrazu dysku.",
        ".img file {path} already in use.":
            "Plik .img {path} jest już używany.",
        "The disk image {path} can no longer be found — it may have been moved, renamed or deleted.":
            "Nie można już znaleźć obrazu dysku {path} — mógł zostać przeniesiony, zmieniony lub usunięty.",
        "Select a ZX Spectrum Next disk image (.img/.hdf) first — then MAME can boot it as the Next's hard disk.":
            "Najpierw wybierz obraz dysku (.img/.hdf) ZX Spectrum Next — MAME uruchomi go jako dysk twardy Nexta.",
        "Select the parent folder inside the SD card image\n(at the top level this returns the target to the image root).":
            "Przejdź do folderu nadrzędnego w obrazie SD\n(na najwyższym poziomie wraca do katalogu głównego obrazu).",
        "Go up one folder in the local file explorer\n(same as double-clicking its '..' entry).":
            "Przejdź folder wyżej w eksploratorze lokalnym\n(jak dwuklik na pozycji «..»).",
        "Re-list the current image folder from the SD card image\n(runs 'hdfmonkey ls' again).":
            "Wylistuj ponownie bieżący folder obrazu SD\n(uruchamia «hdfmonkey ls» jeszcze raz).",
        "Download a ready-to-use NextZXOS SD card image from zxnext.uk,\nsave it to disk, and load it automatically.":
            "Pobierz gotowy obraz karty SD NextZXOS z zxnext.uk,\nzapisz go na dysku i wczytaj automatycznie.",
        "Switch between the classic table view and the picture (gallery)\nview. Persisted across sessions in the config file.":
            "Przełączaj między klasycznym widokiem tabeli a widokiem galerii.\nZapisywane w pliku konfiguracji.",
        "Switch between the classic table view and the picture (gallery) view.\nPersisted across sessions in the config file.":
            "Przełączaj między klasycznym widokiem tabeli a widokiem galerii.\nZapisywane w pliku konfiguracji.",
        "Language of the application's buttons, labels and checkboxes.\nApplies immediately; texts written while the app runs (logs, dialogs)\nfollow after a restart. Saved to the configuration file.":
            "Język przycisków, etykiet i pól wyboru aplikacji.\nDziała od razu; teksty tworzone w trakcie działania (dziennik,\nokna dialogowe) zmienią się po ponownym uruchomieniu.\nZapisywany w pliku konfiguracji.",
        "🌐  Language set to match your system":
            "🌐  Język dopasowany do systemu",
        "The interface language was set to match your system language.\nYou can change it anytime in the Settings tab (\"Application language:\").":
            "Język interfejsu został dopasowany do języka systemu.\nMożesz go zmienić w każdej chwili na karcie Settings\n(«Język aplikacji:»).",
        # ---- Help tab (INIT_HELP; rebuilt per language by _repopulate_help) ----
        "Welcome to zx-next-unite {version} help":
            "Witaj w pomocy zx-next-unite {version}",
        "Introduction:":
            "Wprowadzenie:",
        "HdfmGooey was initially created by em00k and NextSync by Jari Komppa.":
            "HdfmGooey został pierwotnie stworzony przez em00k, a NextSync przez Jariego Komppę.",
        "A while back I rambled with the idea of an all in one bootstrapper transfer tool to":
            "Jakiś czas temu chodził mi po głowie pomysł narzędzia wszystko-w-jednym do transferu i uruchamiania, aby",
        "avoid manipulating SD cards for the Spectrum Next and that was the initial idea of it.":
            "uniknąć żonglowania kartami SD dla Spectrum Next — i taki był początkowy zamysł.",
        "Last but not least some source code was lost from HDFM Gooey and the tool was stuck back in that time,":
            "Na koniec, co nie mniej ważne, część kodu źródłowego HDFM Gooey zaginęła i narzędzie utknęło w tamtych czasach;",
        "with the agreement of em00k I started a rewrite in Python and later with Jari.":
            "za zgodą em00k zacząłem przepisywać je w Pythonie, a później dołączył Jari.",
        "The point of using Python was that it would also provide MacOS and Linux portability.":
            "Zaletą użycia Pythona było to, że zapewni też przenośność na MacOS i Linux.",
        "Later down the line I then extended the NextSync functionality from Sync3 to Sync4.":
            "Później rozszerzyłem funkcjonalność NextSync z Sync3 do Sync4.",
        "The new .sync5 command for the Next speaks Sync4 and therefore allows sending files and directories using the -send command line option.":
            "Nowe polecenie .sync5 dla Nexta mówi w Sync4, a więc pozwala wysyłać pliki i katalogi za pomocą opcji wiersza poleceń -send.",
        "There is as well a new nextsync5.py command line located at the root of the repository that supports the new Sync4 protocol.":
            "Jest też nowy program wiersza poleceń nextsync5.py, w katalogu głównym repozytorium, obsługujący nowy protokół Sync4.",
        "Here we are now you have it!":
            "I oto jesteśmy — proszę bardzo!",
        "Keyboard shortcuts":
            "Skróty klawiszowe",
        "The three file explorers (SD Card local, SD Card disk image and NextSync local) share these shortcuts. Copy / Cut / Paste work across all three explorers and also exchange with the operating-system clipboard (e.g. copy in Windows Explorer, paste into the disk image, and vice-versa):":
            "Trzy eksploratory plików (lokalny SD Card, obraz dysku SD Card i lokalny NextSync) współdzielą te skróty. Kopiuj / Wytnij / Wklej działają między wszystkimi trzema eksploratorami i wymieniają się też ze schowkiem systemu operacyjnego (np. skopiuj w Eksploratorze Windows, wklej do obrazu dysku i odwrotnie):",
        "    Ctrl+C  -  Copy the selected file(s)/folder(s) to the shared clipboard.":
            "    Ctrl+C  -  Kopiuje zaznaczone pliki/foldery do wspólnego schowka.",
        "    Ctrl+X  -  Cut the selection (moved to the destination on the next paste).":
            "    Ctrl+X  -  Wycina zaznaczenie (przeniesione do celu przy następnym wklejeniu).",
        "    Ctrl+V  -  Paste into the selected / currently shown folder.":
            "    Ctrl+V  -  Wkleja do zaznaczonego / aktualnie pokazanego folderu.",
        "    F2      -  Rename the selected file or folder.":
            "    F2      -  Zmienia nazwę zaznaczonego pliku lub folderu.",
        "    Delete  -  Delete the selected file or folder (disk-image & NextSync explorers).":
            "    Delete  -  Usuwa zaznaczony plik lub folder (eksploratory obrazu dysku i NextSync).",
        "In the picture (gallery) item viewer (double-click an item in the GetIt, ZXDB, zxArt or itch.io tabs):":
            "W przeglądarce elementów z obrazkami (galerii) (dwuklik na elemencie w kartach GetIt, ZXDB, zxArt lub itch.io):",
        "    Esc           -  Close the viewer and return to the gallery.":
            "    Esc           -  Zamyka przeglądarkę i wraca do galerii.",
        "    Left / Right  -  Show the previous / next screenshot.":
            "    Left / Right  -  Pokazuje poprzedni / następny zrzut ekranu.",
        "Third party license":
            "Licencje stron trzecich",
        "zx-next-unite is released under the MIT license. It is a Qt Application using PySide6 (Qt for Python) on top of Qt6, used under the GNU LGPL v3.":
            "zx-next-unite jest wydany na licencji MIT. To aplikacja Qt używająca PySide6 (Qt for Python) na Qt6, wykorzystywanym na licencji GNU LGPL v3.",
        "Please refer to the LICENSE and THIRD-PARTY-NOTICES.md files on github: https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE and https://github.com/jclauzel/ZX-Next-Unite/blob/main/THIRD-PARTY-NOTICES.md.":
            "Zobacz pliki LICENSE i THIRD-PARTY-NOTICES.md na githubie: https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE oraz https://github.com/jclauzel/ZX-Next-Unite/blob/main/THIRD-PARTY-NOTICES.md.",
        "PySide6 is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The pre-built release executables do include PySide6/Qt; since the app's full source code is published, they can be rebuilt with a modified Qt.":
            "PySide6 nie jest dołączany przy ręcznej instalacji pythona i trzeba go zainstalować osobno (zobacz instrukcję instalacji). Prekompilowane wydania zawierają PySide6/Qt; ponieważ pełny kod źródłowy aplikacji jest opublikowany, można je zbudować ponownie ze zmodyfikowanym Qt.",
        "zx-next-unite also uses pygame-ce (the community edition of pygame) for its animated backgrounds and visualizations (e.g. the 'Alien Floyd's' effects). Many thanks to the pygame and pygame-ce communities - see https://pyga.me and https://www.pygame.org.":
            "zx-next-unite używa też pygame-ce (społecznościowej edycji pygame) do animowanych teł i wizualizacji (np. efektów 'Alien Floyd's'). Wielkie dzięki dla społeczności pygame i pygame-ce - zobacz https://pyga.me i https://www.pygame.org.",
        "pygame-ce is distributed under the GNU LGPL v2.1 license and, like PySide6, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions).":
            "pygame-ce jest rozpowszechniany na licencji GNU LGPL v2.1 i, podobnie jak PySide6, nie jest dołączany przy ręcznej instalacji pythona — trzeba go zainstalować osobno (zobacz instrukcję instalacji).",
        "zx-next-unite optionally uses itch-dl by Dragoon Aethis to power the itch.io tab (browsing and installing your itch.io collections). Many thanks to its author - see https://github.com/DragoonAethis/itch-dl.":
            "zx-next-unite opcjonalnie używa itch-dl autorstwa Dragoon Aethis do obsługi karty itch.io (przeglądanie i instalowanie twoich kolekcji itch.io). Wielkie dzięki dla autora - zobacz https://github.com/DragoonAethis/itch-dl.",
        "itch-dl is distributed under the MIT license (Copyright (c) 2022 Dragoon Aethis) and, like PySide6 and pygame-ce, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The itch.io tab is only shown when itch-dl is installed.":
            "itch-dl jest rozpowszechniany na licencji MIT (Copyright (c) 2022 Dragoon Aethis) i, podobnie jak PySide6 i pygame-ce, nie jest dołączany przy ręcznej instalacji pythona — trzeba go zainstalować osobno (zobacz instrukcję instalacji). Karta itch.io pojawia się tylko, gdy itch-dl jest zainstalowany.",
        "zx-next-unite optionally uses Flask by the Pallets team to power the NextSync HTTP bridge - the web server behind the Next's .http dot command that lets one Next drive another Next's SD card. Many thanks to its authors - see https://flask.palletsprojects.com and https://github.com/pallets/flask.":
            "zx-next-unite opcjonalnie używa Flaska od zespołu Pallets do mostka HTTP NextSync - serwera web stojącego za poleceniem dot .http Nexta, które pozwala jednemu Nextowi sterować kartą SD drugiego. Wielkie dzięki dla autorów - zobacz https://flask.palletsprojects.com i https://github.com/pallets/flask.",
        "Flask is distributed under the BSD-3-Clause license and, like the other optional packages, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The HTTP bridge toggle in Settings is greyed out until Flask is installed.":
            "Flask jest rozpowszechniany na licencji BSD-3-Clause i, jak pozostałe opcjonalne pakiety, nie jest dołączany przy ręcznej instalacji pythona — trzeba go zainstalować osobno (zobacz instrukcję instalacji). Przełącznik mostka HTTP w Settings jest wyszarzony, dopóki Flask nie zostanie zainstalowany.",
        "zx-next-unite optionally uses Send2Trash by Andrew Senetar and contributors (originally by Virgil Dupras) to send files deleted in the local file explorers to the system Recycle Bin / Trash instead of removing them permanently. Many thanks to its authors - see https://github.com/arsenetar/send2trash.":
            "zx-next-unite opcjonalnie używa Send2Trash autorstwa Andrew Senetara i współtwórców (pierwotnie Virgila Duprasa), aby pliki usuwane w lokalnych eksploratorach trafiały do systemowego Kosza zamiast być usuwane bezpowrotnie. Wielkie dzięki dla autorów - zobacz https://github.com/arsenetar/send2trash.",
        "Send2Trash is distributed under the BSD license and, like the other optional packages, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The 'Send deleted files to the Recycle Bin' toggle in Settings is greyed out until Send2Trash is installed.":
            "Send2Trash jest rozpowszechniany na licencji BSD i, jak pozostałe opcjonalne pakiety, nie jest dołączany przy ręcznej instalacji pythona — trzeba go zainstalować osobno (zobacz instrukcję instalacji). Przełącznik 'Przenoś usuwane pliki do Kosza' w Settings jest wyszarzony, dopóki Send2Trash nie zostanie zainstalowany.",
        "zx-next-unite's optional pre-compiled Windows binary is built with PyInstaller by the PyInstaller Development Team, which bundles the app and all of its dependencies into a single standalone executable. Many thanks to its authors - see https://pyinstaller.org and https://github.com/pyinstaller/pyinstaller.":
            "Opcjonalny prekompilowany plik binarny Windows zx-next-unite jest budowany PyInstallerem od PyInstaller Development Team, który pakuje aplikację i wszystkie zależności w jeden samodzielny plik wykonywalny. Wielkie dzięki dla autorów - zobacz https://pyinstaller.org i https://github.com/pyinstaller/pyinstaller.",
        "PyInstaller is distributed under its GPL 2.0 license with a special exception that explicitly permits packaging applications of any license. It is a build-time tool only - used to produce the pre-compiled binary - and is not needed when running zx-next-unite from source with a manual python install.":
            "PyInstaller jest rozpowszechniany na licencji GPL 2.0 ze specjalnym wyjątkiem, który wprost pozwala pakować aplikacje na dowolnej licencji. To narzędzie wyłącznie do budowania - użyte do stworzenia prekompilowanego binarium - i nie jest potrzebne przy uruchamianiu zx-next-unite ze źródeł przy ręcznej instalacji pythona.",
        "The pre-compiled Windows binary is additionally compressed with UPX (the Ultimate Packer for eXecutables) by Markus Oberhumer, Laszlo Molnar and John Reiser. Many thanks to its authors - see https://upx.github.io and https://github.com/upx/upx.":
            "Prekompilowane binarium Windows jest dodatkowo kompresowane UPX-em (the Ultimate Packer for eXecutables) autorstwa Markusa Oberhumera, Laszlo Molnara i Johna Reisera. Wielkie dzięki dla autorów - zobacz https://upx.github.io i https://github.com/upx/upx.",
        "UPX is distributed under its own liberal license (based on the GPL, with a special exception covering the compressed executables it produces). Like PyInstaller it is a build-time tool only and is not needed when running from source.":
            "UPX jest rozpowszechniany na własnej liberalnej licencji (opartej na GPL, ze specjalnym wyjątkiem obejmującym skompresowane pliki wykonywalne, które tworzy). Jak PyInstaller, jest narzędziem wyłącznie do budowania i nie jest potrzebny przy uruchamianiu ze źródeł.",
        "Setup & How to:":
            "Instalacja i porady:",
        "Check out the main setup & demo video available at: https://youtu.be/-gUxV4fM1yo  (and the full python install is covered in the old py-hdfm-gooey since ZX-Next-Unite is an evolution of it : https://youtu.be/FJG-Z0DCIjQ )":
            "Obejrzyj główny film o instalacji i demo dostępny pod: https://youtu.be/-gUxV4fM1yo  (pełna instalacja pythona jest omówiona w starym py-hdfm-gooey, bo ZX-Next-Unite to jego ewolucja : https://youtu.be/FJG-Z0DCIjQ )",
        "NextSync Head Over Heels demo: https://www.youtube.com/watch?v=D3_WqTPvjOE":
            "Demo NextSync z Head Over Heels: https://www.youtube.com/watch?v=D3_WqTPvjOE",
        "NextSync Night Knight demo: https://www.youtube.com/watch?v=eN1eMIqMCm4":
            "Demo NextSync z Night Knight: https://www.youtube.com/watch?v=eN1eMIqMCm4",
        "Is a required external component developed by Matt Westcott that allows browsing the image.":
            "To wymagany zewnętrzny komponent stworzony przez Matta Westcotta, który umożliwia przeglądanie obrazu.",
        "You will need to install it to get this application up and fully running.":
            "Musisz go zainstalować, aby ta aplikacja działała w pełni.",
        "If hdfmonkey is not present you will see an error message in the main log window as it is missing.":
            "Jeśli hdfmonkey nie jest obecny, w głównym oknie dziennika zobaczysz komunikat o jego braku.",
        "If that is the case you will see a 'Download and install HDF Monkey' button bottom right,":
            "W takim wypadku zobaczysz przycisk 'Pobierz i zainstaluj HDF Monkey' w prawym dolnym rogu;",
        "once clicked it will download a pre-compiled hdfmonkey build for your platform (Windows/Linux/macOS) and install it under the app's downloads folder.":
            "po kliknięciu pobierze prekompilowaną wersję hdfmonkey dla twojej platformy (Windows/Linux/macOS) i zainstaluje ją w folderze downloads aplikacji.",
        "If the above automated install is successful, you should then be able to select an image and navigate it.":
            "Jeśli powyższa automatyczna instalacja się powiedzie, powinno się dać wybrać obraz i po nim nawigować.",
        "hdfmonkey can also be installed manually based on the instructions for your platform that can be found at: https://github.com/gasman/hdfmonkey":
            "hdfmonkey można też zainstalować ręcznie według instrukcji dla twojej platformy dostępnych pod: https://github.com/gasman/hdfmonkey",
        "zx-next-unite implements the <Server> side code and protocol of NextSync by Jari Komppa.":
            "zx-next-unite implementuje kod i protokół strony <serwera> NextSync Jariego Komppy.",
        "It does not require any dot .sync modification and it uses the same very close python logic as nextsync.py.":
            "Nie wymaga żadnych zmian w docie .sync i używa tej samej, bardzo zbliżonej logiki pythona co nextsync.py.",
        "Initial release on specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8":
            "Pierwsze wydanie na specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8",
        "As a result you will need to run the same dot .sync command on your Next as with the console version and the same network protocol.":
            "W związku z tym musisz uruchomić na swoim Nexcie to samo polecenie dot .sync co w wersji konsolowej i z tym samym protokołem sieciowym.",
        "The latest release v1.2 of the .sync command can be found here https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 .":
            "Najnowsze wydanie v1.2 polecenia .sync znajdziesz tutaj https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 .",
        "You may follow the same instructions as provided in the readme.txt of that release.":
            "Możesz postępować według tych samych instrukcji z pliku readme.txt tego wydania.",
        "On your Spectrum Next, clone or image copy the SYNC command that is located in the above release zip file into your Next dot folder.":
            "Na swoim Spectrum Next sklonuj lub skopiuj do obrazu polecenie SYNC z zipa tego wydania do folderu dot twojego Nexta.",
        "Navigate to the NextSync tab, select the root folder to sync on the left.":
            "Przejdź do karty NextSync i wybierz po lewej folder główny do synchronizacji.",
        "Once you have selected the folder hit the 'Prepare Classic NextSync server' button, check the NextSync log window on the right.":
            "Po wybraniu folderu naciśnij przycisk 'Przygotuj klasyczny serwer NextSync' i sprawdź okno dziennika NextSync po prawej.",
        "The first time you run .sync on your Next you will be prompted to select the <server> IP address, this machine running NextSync.":
            "Przy pierwszym uruchomieniu .sync na Nexcie zostaniesz poproszony o wybór adresu IP <serwera>, czyli tej maszyny z NextSync.",
        "From the log window pick the IP address from this machine you want to use and type it on your Next.":
            "Z okna dziennika wybierz adres IP tej maszyny, którego chcesz użyć, i wpisz go na swoim Nexcie.",
        "Then start the sync server on this machine using the 'Start Classic NextSync server' button and then run the .sync command on your Next.":
            "Następnie uruchom serwer synchronizacji na tej maszynie przyciskiem 'Uruchom klasyczny serwer NextSync' i wykonaj polecenie .sync na swoim Nexcie.",
        "At this point your Spectrum Next will connect to your machine using a network socket and the files will be sent to your Next.":
            "W tym momencie twój Spectrum Next połączy się z tą maszyną przez gniazdo sieciowe i pliki zostaną wysłane na twojego Nexta.",
        "As it is your Next that will connect to this machine check your firewall allows inbound calls to this machine on port: 2048 by default.":
            "Ponieważ to twój Next łączy się z tą maszyną, upewnij się, że zapora przepuszcza połączenia przychodzące do tej maszyny na porcie: domyślnie 2048.",
        "The same syncignore.txt and syncpoint.dat file logic applies and allows you to control the sync (please check Jari's documentation).":
            "Obowiązuje ta sama logika plików syncignore.txt i syncpoint.dat, pozwalająca sterować synchronizacją (zajrzyj do dokumentacji Jariego).",
        "NextSync source code can be found here: https://github.com/jarikomppa/specnext/tree/master/sync":
            "Kod źródłowy NextSync znajdziesz tutaj: https://github.com/jarikomppa/specnext/tree/master/sync",
        "If you run into any type of issue using the NextSync integration please run first Jari's command line version to see if it works as expected.":
            "Jeśli napotkasz jakikolwiek problem z integracją NextSync, uruchom najpierw wersję konsolową Jariego, aby sprawdzić, czy działa zgodnie z oczekiwaniami.",
        "OpenAL sound engine (on Windows)":
            "Silnik dźwięku OpenAL (w Windows)",
        "The OpenAL library is required on Windows for CSpect to play sound, you may download it here: https://openal.org/":
            "Biblioteka OpenAL jest wymagana w Windows, aby CSpect odtwarzał dźwięk; możesz ją pobrać tutaj: https://openal.org/",
        "Mono (on Linux & MacOS Only)":
            "Mono (tylko Linux i MacOS)",
        "You will also need to manually install the mono-complete package for example using: sudo apt-get install mono-complete":
            "Musisz też ręcznie zainstalować pakiet mono-complete, na przykład poleceniem: sudo apt-get install mono-complete",
        "Third-Party Content Sources (GetIt / ZXDB / zxArt):":
            "Źródła treści stron trzecich (GetIt / ZXDB / zxArt):",
        "zx-next-unite integrates three external databases to let you browse and download":
            "zx-next-unite integruje trzy zewnętrzne bazy danych, aby można było przeglądać i pobierać",
        "Spectrum-related software and artwork directly from within the application.":
            "oprogramowanie i grafikę związane ze Spectrum bezpośrednio z aplikacji.",
        "The application consumes their public APIs — it does not host, mirror, or":
            "Aplikacja korzysta z ich publicznych API — sama nie hostuje, nie mirroruje ani",
        "redistribute any of the files itself.":
            "nie redystrybuuje żadnych plików.",
        "  GetIt is a community-maintained archive of ZX Spectrum Next software.":
            "  GetIt to utrzymywane przez społeczność archiwum oprogramowania ZX Spectrum Next.",
        "  The application queries the GetIt API to list and search files, then":
            "  Aplikacja odpytuje API GetIt, aby wylistować i wyszukać pliki, a następnie",
        "  downloads them directly from the URLs returned by that API.":
            "  pobiera je bezpośrednio z adresów URL zwróconych przez to API.",
        "  ZXDB is an open-source database of ZX Spectrum and related software,":
            "  ZXDB to otwartoźródłowa baza danych oprogramowania ZX Spectrum i pokrewnych,",
        "  maintained by the community at https://github.com/zxdb/ZXDB .":
            "  utrzymywana przez społeczność pod https://github.com/zxdb/ZXDB .",
        "  The application queries the ZXDB REST API for titles, releases, screenshots":
            "  Aplikacja odpytuje REST API ZXDB o tytuły, wydania, zrzuty ekranu",
        "  and inlays, then downloads files directly from the URLs returned by that API.":
            "  i okładki, a następnie pobiera pliki bezpośrednio z adresów URL zwróconych przez to API.",
        "  zxArt (https://zxart.ee) is a gallery and archive dedicated to ZX Spectrum":
            "  zxArt (https://zxart.ee) to galeria i archiwum poświęcone sztuce wizualnej,",
        "  visual art, music, and productions.":
            "  muzyce i produkcjom ZX Spectrum.",
        "  The application sends requests to the zxArt API to search productions and":
            "  Aplikacja wysyła zapytania do API zxArt, aby wyszukiwać produkcje i",
        "  pictures, retrieve metadata and preview images, and download productions":
            "  obrazki, pobierać metadane i podglądy oraz pobierać produkcje",
        "  directly from the URLs returned by that API.":
            "  bezpośrednio z adresów URL zwróconych przez to API.",
        "  Mame emulator brought to you by Holub for the ZX Spectrum Next can be installed following this documentation: https://wiki.specnext.dev/MAME:Installing":
            "  Emulator Mame przeniesiony na ZX Spectrum Next przez Holuba można zainstalować według tej dokumentacji: https://wiki.specnext.dev/MAME:Installing",
        "  Official Windows Binary Packages can be found here: https://www.mamedev.org/release.html":
            "  Oficjalne pakiety binarne dla Windows znajdziesz tutaj: https://www.mamedev.org/release.html",
        "  Put the file tbblue.zip that can be found here: https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip into MAME's roms folder.":
            "  Umieść plik tbblue.zip, dostępny tutaj: https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip w folderze roms MAME.",
        "  Important note: Don't extract the tbblue.zip file; MAME will look for the zip file when the 'tbblue' machine is selected.":
            "  Ważna uwaga: nie rozpakowuj pliku tbblue.zip; MAME szuka zipa, gdy wybrana jest maszyna 'tbblue'.",
        "  Mike Dailly's CSpect is a downloadable emulator for Windows, macOS, and Linux":
            "  CSpect Mike'a Dailly'ego to emulator do pobrania dla Windows, macOS i Linuksa",
        "  Sites and links:":
            "  Strony i odnośniki:",
        "Legal disclaimer:":
            "Zastrzeżenie prawne:",
        "  The author of zx-next-unite does NOT distribute any files, ROMs, games,":
            "  Autor zx-next-unite NIE rozpowszechnia żadnych plików, ROM-ów, gier,",
        "  demos, graphics, music, or any other content obtained through these APIs.":
            "  dem, grafik, muzyki ani innych treści pozyskanych przez te API.",
        "  All content is served exclusively by the respective third-party services.":
            "  Wszystkie treści są serwowane wyłącznie przez odpowiednie serwisy stron trzecich.",
        "  This application and author do not control third-party content.":
            "  Ta aplikacja i jej autor nie kontrolują treści stron trzecich.",
        "  It is the sole responsibility of the end user to ensure that any content":
            "  Wyłączną odpowiedzialnością użytkownika końcowego jest upewnienie się, że wszelkie treści,",
        "  they download or use through this application complies with the applicable":
            "  które pobiera lub używa poprzez tę aplikację, są zgodne z obowiązującymi",
        "  copyright, licensing, and legal requirements in their jurisdiction.":
            "  wymogami prawa autorskiego, licencji i przepisów jego jurysdykcji.",
        "  If in doubt, consult the terms of service of the relevant platform and":
            "  W razie wątpliwości zapoznaj się z warunkami usługi danej platformy i",
        "  seek appropriate legal advice before downloading or using any content.":
            "  zasięgnij odpowiedniej porady prawnej przed pobraniem lub użyciem jakichkolwiek treści.",
        "  For inquiries you may reach out to me on my github page: https://github.com/jclauzel/ZX-Next-Unite":
            "  W razie pytań możesz się ze mną skontaktować przez moją stronę github: https://github.com/jclauzel/ZX-Next-Unite",
        "Enjoy!":
            "Miłej zabawy!",
        # ---- OpenAL guided install (CSpect sound on Windows) ----
        "OpenAL 1.1 detected — CSpect sound is ready.":
            "Wykryto OpenAL 1.1 — dźwięk CSpect jest gotowy.",
        "Install OpenAL?":
            "Zainstalować OpenAL?",
        ("On Windows CSpect needs the <b>OpenAL 1.1</b> audio library "
         "for sound, and it was not detected on this machine — without "
         "it CSpect runs silent.<br><br>"
         "OpenAL is separate, third-party software — many thanks to its "
         "authors: {url}<br><br>"
         "Download the official installer (oalinst.exe) from openal.org "
         "and run it now?<br><br>"
         "Windows will ask for administrator approval when the installer "
         "starts — the app itself never runs elevated."):
            ("W Windows CSpect potrzebuje do dźwięku biblioteki audio "
             "<b>OpenAL 1.1</b>, a nie wykryto jej na tej maszynie — bez "
             "niej CSpect działa bezgłośnie.<br><br>"
             "OpenAL to osobne oprogramowanie stron trzecich — wielkie "
             "dzięki dla jego autorów: {url}<br><br>"
             "Pobrać oficjalny instalator (oalinst.exe) z openal.org i "
             "uruchomić go teraz?<br><br>"
             "Windows poprosi o zgodę administratora przy starcie "
             "instalatora — sama aplikacja nigdy nie działa z podniesionymi "
             "uprawnieniami."),
        "Download and run the OpenAL installer":
            "Pobierz i uruchom instalator OpenAL",
        "Open openal.org":
            "Otwórz openal.org",
        "The OpenAL download failed — see the log for details. You can install it manually from {url}":
            "Pobieranie OpenAL nie powiodło się — szczegóły w dzienniku. Możesz zainstalować go ręcznie z {url}",
    },
    "ru": {
        # ---- 9.6.0: per-emulator button colour ----
        "Set the {emulator} color…":
            "Задать цвет {emulator}…",
        "Reset the {emulator} color":
            "Сбросить цвет {emulator}",
        # ---- 9.6.0: forgetting a remembered SD image path ----
        "Remove \"{path}\" from the list":
            "Убрать \"{path}\" из списка",
        "Clear the whole list":
            "Очистить весь список",
        "Removed {path} from the image list — the image file itself was not deleted.":
            "Убрано из списка образов: {path} — сам файл образа не удалён.",
        "Cleared the image list — no image files were deleted.":
            "Список образов очищен — ни один файл образа не удалён.",
        "Clear the image list?":
            "Очистить список образов?",
        "Forget all {count} remembered image paths? The image files themselves are not deleted.":
            "Забыть все запомненные пути к образам ({count})? Сами файлы образов не удаляются.",
        "Remove the image path shown on the left from the list.\nThe image file itself is not deleted.":
            "Убрать показанный слева путь к образу из списка.\nСам файл образа не удаляется.",
        "Path to the SD card image (.img / .hdf).\nType a path directly, click the arrow to pick from recently loaded images,\nor use the 'Select NextZXOS disk Image' button to browse.\nRight-click the box for list options, or press Delete on a dropdown entry to forget it.":
            "Путь к образу SD-карты (.img / .hdf).\nВведите путь вручную, нажмите стрелку, чтобы выбрать из недавно загруженных образов,\nили выберите файл кнопкой «Выбрать образ диска NextZXOS».\nПравый щелчок по полю — параметры списка; клавиша Delete на записи выпадающего списка забывает её.",
        # ---- GetIt starter pack ----
        'Assembling the starter pack…': 'Сборка стартового набора…',
        'Downloading {title} ({idx}/{total})…': 'Скачивание {title} ({idx}/{total})…',
        ('Everything on GetIt is freely distributable; the files are written to {dir} '
         'inside the loaded image.'):
            ('Всё на GetIt свободно распространяется; файлы записываются в {dir} '
             'внутри загруженного образа.'),
        'Failed: {names}': 'Не удалось: {names}',
        'Fetching the GetIt catalogue…': 'Загрузка каталога GetIt…',
        ('Fill the loaded SD image with a hand-picked selection of modern homebrew '
         'from the GetIt catalogue — everything on GetIt is freely distributable.'):
            ('Заполняет загруженный образ SD отобранным вручную современным homebrew '
             'из каталога GetIt — всё на GetIt свободно распространяется.'),
        'Fill your SD image with {count} hand-picked homebrew titles from the GetIt catalogue?': 'Заполнить образ SD {count} отобранными вручную homebrew-играми из каталога GetIt?',
        'Load a disk image first (SD Card tab), then try again.': 'Сначала загрузите образ диска (вкладка SD Card), затем повторите.',
        'No network connection.': 'Нет сетевого подключения.',
        'Not in the catalogue right now: {names}': 'Сейчас нет в каталоге: {names}',
        'Starter pack': 'Стартовый набор',
        'Starter pack cancelled — {done} title(s) were installed.': 'Стартовый набор отменён — установлено игр: {done}.',
        'Starter pack complete: {done} of {total} titles installed to {dir}.': 'Стартовый набор готов: установлено {done} из {total} игр в {dir}.',
        'Starter pack failed: {error}': 'Сбой стартового набора: {error}',
        '🎁 Starter pack': '🎁 Стартовый набор',
        # ---- context menus & runtime dialogs ----
        '"{name}" already exists in this folder.': '"{name}" уже существует в этой папке.',
        'Add drive {letter}: to the list?': 'Добавить диск {letter}: в список?',
        'Add Next drive': 'Добавить диск Next',
        'Confirm deletion': 'Подтвердите удаление',
        'Copy': 'Копировать',
        'Copy all text': 'Копировать весь текст',
        'Copy path to clipboard': 'Копировать путь в буфер обмена',
        'Copy text to clipboard': 'Копировать текст в буфер обмена',
        'Could not create {name}:': 'Не удалось создать {name}:',
        'Could not create:': 'Не удалось создать:',
        'Could not delete:': 'Не удалось удалить:',
        'Could not extract {name}:': 'Не удалось извлечь {name}:',
        'Could not rename:': 'Не удалось переименовать:',
        'Create directory failed': 'Не удалось создать каталог',
        'Create new directory': 'Создать новый каталог',
        'Create new directory…': 'Создать новый каталог…',
        'Cut': 'Вырезать',
        'Decrease font size': 'Уменьшить размер шрифта',
        'Delete failed': 'Не удалось удалить',
        'Delete from the local disk?': 'Удалить с локального диска?',
        'Delete on the Next? Folders are deleted with everything inside them.': 'Удалить на Next? Папки удаляются со всем содержимым.',
        'Delete the file "{name}"?': 'Удалить файл "{name}"?',
        'Delete the folder "{name}" and all of its contents?': 'Удалить папку "{name}" со всем содержимым?',
        'Delete these {count} items? Folders are deleted with all of their contents.': 'Удалить эти элементы ({count})? Папки удаляются со всем содержимым.',
        'Deleted files are sent to the Recycle Bin.': 'Удалённые файлы отправляются в корзину.',
        'Download (:<-)': 'Скачать (:<-)',
        'Download content': 'Скачать содержимое',
        'Download Failed': 'Ошибка загрузки',
        'Drive letter of the additional SD reader/partition (D..P):': 'Буква диска дополнительного SD-ридера/раздела (D..P):',
        'Extraction Failed': 'Ошибка извлечения',
        'Failed to download the NextZXOS image:': 'Не удалось скачать образ NextZXOS:',
        'Fetch issue info for this magazine': 'Получить сведения о номерах этого журнала',
        'Fetch single magazine by name': 'Найти журнал по названию',
        'file': 'файл',
        'Files:  {files}\nFolders:  {folders}\nTotal size:  {size} bytes  ({pretty})': 'Файлы:  {files}\nПапки:  {folders}\nОбщий размер:  {size} байт  ({pretty})',
        'folder': 'папка',
        'Get size': 'Узнать размер',
        'Image not writable': 'Образ недоступен для записи',
        'Increase font size (now {px}px)': 'Увеличить размер шрифта (сейчас {px}px)',
        'More like this': 'Похожие',
        'New directory name in': 'Имя нового каталога в',
        'Name this Next': 'Назовите этот Next',
        'Friendly name for {addr} (empty removes it):':
            'Понятное имя для {addr} (пустое удаляет его):',
        'New folder in {path}:': 'Новая папка в {path}:',
        'New Folder…': 'Новая папка…',
        'New name for the {kind}:': 'Новое имя ({kind}):',
        'Not enough space on the Next': 'Недостаточно места на Next',
        ('Only add a drive that really exists on your Next (an extra SD card reader or '
         'partition). Selecting a drive that is not mounted CRASHES the Next.'):
            ('Добавляйте только диск, который действительно есть в вашем Next '
             '(дополнительный SD-ридер или раздел). Выбор несмонтированного диска '
             'ПРИВОДИТ К СБОЮ Next.'),
        'Open': 'Открыть',
        'Open in {source}': 'Открыть в {source}',
        'Open: the system could not open {name}.':
            'Открыть: система не смогла открыть {name}.',
        'Open on website (zxart.ee)': 'Открыть на сайте (zxart.ee)',
        'Open on website (zxinfo.dk)': 'Открыть на сайте (zxinfo.dk)',
        'Paste': 'Вставить',
        'Remote Unzip file': 'Распаковать файл удалённо',
        'Remote Zip': 'Запаковать удалённо',
        'Remove from Favorites': 'Убрать из избранного',
        "Rename '{name}' to:": "Переименовать '{name}' в:",
        'Rename failed': 'Не удалось переименовать',
        'Rename…': 'Переименовать…',
        'Retrieve all issues': 'Получить все номера',
        'Send to SD card (image)  →  {dest}': 'Отправить на SD-карту (образ)  →  {dest}',
        'Send using NextSync  →  {dest}': 'Отправить через NextSync  →  {dest}',
        'Set sync root': 'Задать корень синхронизации',
        'Set this folder as the new sync root?': 'Сделать эту папку новым корнем синхронизации?',
        'Size on the Next': 'Размер на Next',
        'The image was downloaded but could not be extracted:': 'Образ скачан, но извлечь его не удалось:',
        "The name cannot contain '/' or '\\'.": "Имя не может содержать '/' или '\\'.",
        'This cannot be undone.': 'Это действие нельзя отменить.',
        ('This copy needs {need} bytes ({need_h}), but drive {drive}: only has {free} '
         'bytes ({free_h}) free.\n\nIt exceeds the available remote space by {over} '
         'bytes ({over_h}).\n\nThe copy was not started.'):
            ('Для копирования нужно {need} байт ({need_h}), но на диске {drive}: '
             'свободно только {free} байт ({free_h}).\n\nНе хватает {over} байт '
             '({over_h}).\n\nКопирование не начато.'),
        'Unzip file': 'Распаковать файл',
        'Zip': 'Запаковать',
        '… and {n} more': '… и ещё {n}',
        # ---- labels ----
        "  Background": "  Фон",
        "  Directory name": "  Имя каталога",
        "  Directory type label": "  Метка типа каталога",
        "  File extension": "  Расширение файла",
        "  File name": "  Имя файла",
        "  File size": "  Размер файла",
        "  Filter: ": "  Фильтр: ",
        "  General UI text": "  Общий текст интерфейса",
        "  Retro logs console": "  Ретро-консоль журнала",
        "  Up Directory item": "  Пункт «вверх по каталогу»",
        "Background image opacity (%):": "Непрозрачность фона (%):",
        "Background image:": "Фоновое изображение:",
        "CSpect default launch parameters:": "Параметры запуска CSpect:",
        "Collection:": "Коллекция:",
        "Desktop Theme:": "Тема оформления:",
        "Disk Image Explorer: ": "Проводник образа диска: ",
        "Gallery animation:": "Анимация галереи:",
        "Gallery image size:": "Размер картинок галереи:",
        "Gallery items per row:": "Элементов в ряду галереи:",
        "Gallery rows per page (min):": "Рядов на страницу галереи (мин):",
        "Gallery search sort ordering preference:": "Порядок результатов поиска в галерее:",
        "Gallery slideshow pause time:": "Пауза слайд-шоу:",
        "Language:": "Язык:",
        "Application language:": "Язык приложения:",
        "Local file explorers & App Text Colors:": "Локальные проводники и цвета текста:",
        "Local path: ": "Локальный путь: ",
        "MAME ROM / system:": "ROM / система MAME:",
        "MAME default launch parameters:": "Параметры запуска MAME:",
        "Max connections:": "Макс. подключений:",
        "NextSync — Automatically start Remote Explorer server on startup":
            "NextSync — Автоматически запускать сервер Remote Explorer при запуске",
        "NextSync — when a sent file or directory exists locally:":
            "NextSync — если принятый файл или каталог уже существует локально:",
        "Page:": "Страница:",
        "Port:": "Порт:",
        "Reset theme": "Сбросить тему",
        "Retro log font size:": "Размер шрифта ретро-журнала:",
        "Search:": "Поиск:",
        "Search: ": "Поиск: ",
        "View:": "Вид:",
        "itch.io API key:": "Ключ API itch.io:",
        # ---- buttons ----
        "< Prev": "< Назад",
        "Cancel": "Отмена",
        "Cancel sync": "Отменить синхронизацию",
        "Connect": "Подключить",
        "Create Directory": "Создать каталог",
        "Create SyncIgnore File": "Создать файл SyncIgnore",
        "Delete": "Удалить",
        "Delete SyncIgnore File": "Удалить файл SyncIgnore",
        "Delete SyncPoint File": "Удалить файл SyncPoint",
        "Disconnect": "Отключить",
        "RS232 ESP emulation is already running on port {port} for another emulator; this MAME joins it. The new port applies once every MAME has exited.":
            "Эмуляция RS232 ESP уже работает на порту {port} для другого эмулятора; этот MAME подключается к ней. Новый порт будет использован после выхода из всех MAME.",
        "RS232 ESP emulation: {count} emulators are now sharing it (port {port}).":
            "Эмуляция RS232 ESP: её сейчас совместно используют {count} эмуляторов (порт {port}).",
        "RS232 ESP emulation could not start (port {port} in use?). MAME starts without it.":
            "Эмуляция RS232 ESP не смогла запуститься (порт {port} занят?). MAME запускается без неё.",
        "RS232 ESP emulation inspired by jesperl - by Janko Stamenović":
            "Эмуляция RS232 ESP вдохновлена проектом jesperl - автор Janko Stamenović",
        "The optional RS232 ESP Emulation for MAME (Settings) is a clean full reimplementation in Python of an idea from jesperl by Janko Stamenović - an ESP-AT emulator bridging MAME's emulated Wi-Fi module to the real network. Many thanks for the inspirational idea - see https://sourceforge.net/projects/jesperl/.":
            "Необязательная эмуляция RS232 ESP для MAME (Настройки) - это чистая полная реализация на Python идеи из проекта jesperl Janko Stamenović - эмулятора ESP-AT, соединяющего эмулируемый Wi-Fi модуль MAME с реальной сетью. Большое спасибо за вдохновляющую идею - см. https://sourceforge.net/projects/jesperl/.",
        "One RS232 ESP emulation serves every running MAME: launch a second MAME on another disk image and it joins the same emulation with its own separate session, so several emulated Nexts can be on the network at once. The emulation stops when the last MAME exits. When two of them ask for the same server port (a Next listening for incoming connections), the second one is moved to the next free port and the log says which port to connect to.":
            "Одна эмуляция RS232 ESP обслуживает все запущенные MAME: запустите второй MAME с другим образом диска, и он подключится к той же эмуляции со своим отдельным сеансом, так что несколько эмулируемых Next могут быть в сети одновременно. Эмуляция останавливается, когда закрывается последний MAME. Если два из них запросят один и тот же серверный порт (Next, ожидающий входящих подключений), второй будет перенесён на следующий свободный порт, и в журнале будет указано, к какому порту подключаться.",
        "Transfers through the RS232 ESP emulation need the Next side on its SLOW pacing: use '.sync5 -s' for the dot, or set UART speed to Slow in ZX Next Remote's settings.":
            "Передачи через эмуляцию RS232 ESP требуют МЕДЛЕННОго темпа на стороне Next: используйте '.sync5 -s' для дот-команды или установите скорость UART в Slow в настройках ZX Next Remote.",
        "Start {emulator}": "Запустить {emulator}",
        "Color:": "Цвет:",
        "Pick a color for this Next. It tints the machine list and this machine's tab in the session strip.":
            "Выберите цвет для этого Next. Он окрашивает список машин и вкладку этой машины на панели сеансов.",
        "Clear the color": "Убрать цвет",
        "Switch to this Next": "Переключиться на этот Next",
        "Name and color…": "Имя и цвет…",
        "That Next is no longer on the line.":
            "Этого Next уже нет на линии.",
        "Tell this Next to leave listen mode and exit? ZX Next Remote closes its application; a '.sync5' dot returns to BASIC. The server keeps listening, so it can connect again.":
            "Попросить этот Next выйти из режима прослушивания и завершить работу? ZX Next Remote закроет своё приложение; точка '.sync5' вернётся в BASIC. Сервер продолжает слушать, поэтому можно подключиться снова.",
        "Asked the Next to leave listen mode and exit.":
            "Next-у отправлен запрос выйти из режима прослушивания и завершить работу.",
        "Remote .sync5 update": "Удалённое обновление .sync5",
        "Update .sync5 on this Next ({old} → {new})…":
            "Обновить .sync5 на этом Next ({old} → {new})…",
        "Push new .sync5 to this Next…":
            "Отправить новый .sync5 на этот Next…",
        ".sync5 version unknown — switch to this Next first":
            "Версия .sync5 неизвестна — сначала переключитесь на этот Next",
        ".sync5 v{old} predates self-update — copy the new dot to the Next by hand once":
            ".sync5 v{old} появился до самообновления — один раз скопируйте новый dot на Next вручную",
        "Locating the .sync5 build to send…":
            "Поиск сборки .sync5 для отправки…",
        "Still locating the .sync5 build to send — one moment.":
            "Сборка .sync5 для отправки ещё ищется — минутку.",
        "Could not obtain the .sync5 build to send: {reason}":
            "Не удалось получить файл .sync5 для отправки: {reason}",
        ("Update .sync5 on {machine}: v{old} → v{new}.\n\nThe new dot is "
         "staged on the Next's SD card, read back and verified, then "
         "swapped in; the previous dot is kept as sync5.bak (renaming it "
         "back to sync5 is the one-step recovery). The session ends when "
         "the update completes — run {command} on the Next again "
         "afterwards.\n\nTarget directory on the Next:"):
            ("Обновление .sync5 на {machine}: v{old} → v{new}.\n\nНовая "
             "dot-команда записывается на SD-карту Next, считывается "
             "обратно и проверяется, затем подменяется; прежняя "
             "dot-команда сохраняется как sync5.bak (переименование "
             "обратно в sync5 — восстановление в один шаг). Сессия "
             "завершится по окончании обновления — после этого снова "
             "запустите {command} на Next.\n\nЦелевой каталог на Next:"),
        ("Push the new .sync5 (v{new}) to {machine}?\n\nThis machine's "
         "version is unknown (an older dot, or an old ZX Next Remote "
         "build — the two cannot be told apart), and the swap itself "
         "only works when the far side is a .sync dot v5.9 or newer: on "
         "anything older the staged sync5.new is left on the card and "
         "nothing is swapped. The previous dot is kept as sync5.bak "
         "(renaming it back to sync5 is the one-step recovery). The "
         "session ends when the update completes — run {command} on the "
         "Next again afterwards.\n\nTarget directory on the Next:"):
            ("Отправить новый .sync5 (v{new}) на {machine}?\n\nВерсия "
             "этой машины неизвестна (старая dot-команда или старая "
             "версия ZX Next Remote — их невозможно различить), а сама "
             "подмена работает только тогда, когда на той стороне "
             "dot-команда .sync версии v5.9 или новее: на чём-то более "
             "старом подготовленный sync5.new останется на карте и "
             "ничего не будет подменено. Прежняя dot-команда сохраняется "
             "как sync5.bak (переименование обратно в sync5 — "
             "восстановление в один шаг). Сессия завершится по окончании "
             "обновления — после этого снова запустите {command} на "
             "Next.\n\nЦелевой каталог на Next:"),
        "Download File": "Скачать файл",
        "Download NextZXOS Image": "Скачать образ NextZXOS",
        "Download and install HDF Monkey": "Скачать и установить HDF Monkey",
        "Download and install HDF Monkey and OpenAL": "Скачать и установить HDF Monkey и OpenAL",
        "Generate": "Создать",
        "Get API key…": "Получить ключ API…",
        "Latest": "Новинки",
        "New Folder": "Новая папка",
        "Next >": "Далее >",
        "OK": "ОК",
        "Open config file": "Открыть файл конфигурации",
        "Prepare Classic NextSync server": "Подготовить классический сервер NextSync",
        "Random": "Случайно",
        "Refresh": "Обновить",
        "Rename": "Переименовать",
        "Search": "Искать",
        "Select NextZXOS disk Image": "Выбрать образ диска NextZXOS",
        "Set current folder as new sync root folder":
            "Сделать текущую папку корнем синхронизации",
        "Up": "Вверх",
        "▶ Start Classic NextSync server": "▶ Запустить классический сервер NextSync",
        "▶ Start Remote Explorer NextSync server":
            "▶ Запустить сервер NextSync удалённого проводника",
        "⬇  Download": "⬇  Скачать",
        "⬇  Install MAME": "⬇  Установить MAME",
        "🎮 Retro": "🎮 Ретро",
        "💾  Send to SD card": "💾  Отправить на SD-карту",
        "🔁  Send via NextSync": "🔁  Отправить через NextSync",
        "🕹  Launch CSpect": "🕹  Запустить CSpect",
        "🕹  Launch Mame": "🕹  Запустить Mame",
        "🖼 Switch to 'Classic' view mode": "🖼 Вернуться к виду 'Классический'",
        # ---- emulator option combos (SD Card tab; selected by index) ----
        "Screen Size X1": "Размер экрана X1",
        "Screen Size X2": "Размер экрана X2",
        "Screen Size X3": "Размер экрана X3",
        "Screen Size X4": "Размер экрана X4",
        "Fullscreen": "Полный экран",
        "Sound On": "Звук вкл.",
        "Sound Off": "Звук выкл.",
        "Sound WASAPI": "Звук WASAPI",
        "Sound XAudio2": "Звук XAudio2",
        "Sound PortAudio": "Звук PortAudio",
        "VSync On": "VSync вкл.",
        "VSync Off": "VSync выкл.",
        "Joystick On": "Джойстик вкл.",
        "Joystick Off": "Джойстик выкл.",
        "Mouse On": "Мышь вкл.",
        "Mouse Off": "Мышь выкл.",
        "Disable ESC Key Off": "Блокировка ESC: выкл.",
        "Disable ESC Key On": "Блокировка ESC: вкл.",
        "ZX Next Unite update check: could not parse the versions (latest tag {tag}); skipping.":
            "Проверка ZX Next Unite: не удалось разобрать версии (последний тег {tag}) — пропуск.",
        "ZX Next Unite {latest} is available, but the release has no package for this platform — opening the releases page instead.":
            "Доступен ZX Next Unite {latest}, но в релизе нет пакета для этой платформы — откроется страница релизов.",
        # ---- long guidance prompts (final) ----
        "A newer version of CSpect is available on itch.io.\n\nInstalled: {installed}\nLatest: {latest}\n\nDownload and install the newest version now?":
            "На itch.io доступна более новая версия CSpect.\n\nУстановлена: {installed}\nПоследняя: {latest}\n\nСкачать и установить новейшую версию сейчас?",
        "CSpect update ▸ SUCCESS — {name} extracted to: {path}":
            "Обновление CSpect ▸ УСПЕШНО — {name} распакован в: {path}",
        "CSpect update ▸ Starting download + install of {name} ({file}) from itch.io into {folder}.":
            "Обновление CSpect ▸ начало загрузки и установки {name} ({file}) с itch.io в {folder}.",
        "ERROR: could not build {name}: {error}":
            "ОШИБКА: не удалось создать {name}: {error}",
        "MAME can't start: the ZX Spectrum Next boot ROM (TBBLUE) is missing. This step is manual — see {url} and follow \"Get TBBLUE (the Next 'boot ROM')\". Put the file tbblue.zip into MAME's roms folder (downloads\\mame\\roms) — DON'T extract it — and try again. You must provide a legally acquired, licensed ROM.":
            "MAME не может запуститься: отсутствует загрузочная ROM ZX Spectrum Next (TBBLUE). Этот шаг выполняется вручную — см. {url} и раздел \"Get TBBLUE (the Next 'boot ROM')\". Поместите файл tbblue.zip в папку roms MAME (downloads\\mame\\roms) — НЕ распаковывайте его — и повторите попытку. Используйте только легально приобретённую лицензионную ROM.",
        "MAME install ▸ NEXT STEP (manual): add the TBBLUE boot ROM. See {url} → \"Get TBBLUE (the Next 'boot ROM')\". Put the file tbblue.zip into MAME's roms folder ({roms}) — DON'T extract it. You must provide a legally acquired, licensed ROM.":
            "Установка MAME ▸ СЛЕДУЮЩИЙ ШАГ (вручную): добавьте загрузочную ROM TBBLUE. См. {url} → \"Get TBBLUE (the Next 'boot ROM')\". Поместите файл tbblue.zip в папку roms MAME ({roms}) — НЕ распаковывайте его. Используйте только легально приобретённую лицензионную ROM.",
        "NextSync .sync5 dot command updated: v{old} -> v{new} — please copy the new build to your Next (it cannot be deployed automatically).":
            "Dot-команда NextSync .sync5 обновлена: v{old} -> v{new} — скопируйте новую сборку на Next (автоматическое развёртывание невозможно).",
        "TIP: Did you know that if you have purchased CSpect from itch.io you can do a full end-to-end CSpect install from there?\n\nCSpect ships with hdfmonkey bundled inside it, so that route needs no separate hdfmonkey install — the app finds and uses the bundled copy automatically.\n\nSimply log into your itch.io account in the itch.io tab, navigate to CSpect and click Install.\n\nDo you still want to install hdfmonkey only, or abort and then make an end-to-end install of CSpect using itch.io?":
            "СОВЕТ: знали ли вы, что если CSpect куплен на itch.io, можно выполнить полную установку CSpect прямо оттуда?\n\nCSpect поставляется со встроенным hdfmonkey, поэтому при этом пути отдельная установка hdfmonkey не нужна — приложение автоматически находит и использует встроенную копию.\n\nВойдите в свою учётную запись itch.io на вкладке itch.io, найдите CSpect и нажмите «Установить».\n\nВсё ещё установить только hdfmonkey или прервать и выполнить полную установку CSpect через itch.io?",
        "The automatic hdfmonkey download from specnext.com failed — the forum may be asking for a login or an anti-robot confirmation before the download can start (see the log for details).\n\nYou can install it manually instead:\n1. Click 'Open download page' below (or browse to\n    {url} ).\n2. Download the hdfmonkey .zip file.\n3. Drop the downloaded .zip into this EXACT folder — the app has already created it, and the 'Open downloads folder' button below opens it so nothing needs to be typed:\n    {folder}\n4. Click \"I've dropped the zip - try again\".":
            "Автоматическая загрузка hdfmonkey с specnext.com не удалась — форум может требовать входа или анти-робот подтверждения перед началом загрузки (подробности в журнале).\n\nМожно установить вручную:\n1. Нажмите 'Открыть страницу загрузки' ниже (или откройте\n    {url} ).\n2. Скачайте .zip-файл hdfmonkey.\n3. Положите скачанный .zip ИМЕННО в эту папку — приложение уже создало её, а кнопка 'Открыть папку загрузок' ниже открывает её, так что вводить ничего не нужно:\n    {folder}\n4. Нажмите \"Я положил zip — попробовать снова\".",
        "ZX Next Unite update: downloaded {name} to {folder}":
            "Обновление ZX Next Unite: {name} загружен в {folder}",
        "ZX Next Unite update: downloaded {path} but could not unpack it: {error}":
            "Обновление ZX Next Unite: {path} загружен, но распаковать не удалось: {error}",
        "ZX Next Unite {latest} is available (you are running {installed}).\n\nYou appear to be running from source (git clone), so the\nrecommended way to update is:\n\n    git pull\n\ninstead of downloading the Windows binary.":
            "Доступен ZX Next Unite {latest} (у вас {installed}).\n\nПохоже, вы запускаете программу из исходников (git clone), поэтому\nрекомендуемый способ обновления:\n\n    git pull\n\nвместо загрузки бинарника для Windows.",
        "ZX Next Unite {latest} is available — download?\n\nInstalled: {installed}\nLatest: {latest}\nPackage: {asset} (~{size})\n\nThe new version is saved next to the current one — you choose\nwhen to switch (you'll be offered a restart after the download).":
            "Доступен ZX Next Unite {latest} — скачать?\n\nУстановлена: {installed}\nПоследняя: {latest}\nПакет: {asset} (~{size})\n\nНовая версия сохраняется рядом с текущей — вы сами решаете,\nкогда переключиться (после загрузки будет предложен перезапуск).",
        "ZX Next Unite {latest} is available — running from source, so update with 'git pull' instead of the Windows binary.":
            "Доступен ZX Next Unite {latest} — запуск из исходников, поэтому обновляйтесь через 'git pull', а не бинарником для Windows.",
        # ---- emulator update prompts (bodies + buttons) ----
        "A newer version of MAME is available.\n\nInstalled: 0.{installed}\nLatest: {latest}  (0.{latest_num})\nPackage: {asset}\n\nDownload (~{size}) and update your MAME install now?\nThe existing files in the downloads MAME folder will be overwritten.":
            "Доступна более новая версия MAME.\n\nУстановлена: 0.{installed}\nПоследняя: {latest}  (0.{latest_num})\nПакет: {asset}\n\nСкачать (~{size}) и обновить установку MAME сейчас?\nСуществующие файлы в папке MAME будут перезаписаны.",
        "Close and start {name}":
            "Закрыть и запустить {name}",
        "Continue hdfmonkey standalone install":
            "Продолжить отдельную установку hdfmonkey",
        "I've dropped the zip - try again":
            "Я положил zip — попробовать снова",
        "MAME release: {tag}\nPackage: {asset} ({arch})\n\nDownload (~{size}) and install it into the downloads folder?\nNote: the fully extracted install is large (~500 MB).":
            "Версия MAME: {tag}\nПакет: {asset} ({arch})\n\nСкачать (~{size}) и установить в папку загрузок?\nПримечание: полностью распакованная установка занимает ~500 МБ.",
        "Open download page":
            "Открыть страницу загрузки",
        "Open downloads folder":
            "Открыть папку загрузок",
        "The new version was saved as:\n\n{path}\n\nClose ZX Next Unite now and start the new version ({name})?\nYour settings (hdfg.cfg) and downloads are picked up as-is —\nboth versions run from the same folder.":
            "Новая версия сохранена как:\n\n{path}\n\nЗакрыть ZX Next Unite и запустить новую версию ({name})?\nВаши настройки (hdfg.cfg) и загрузки используются как есть —\nобе версии запускаются из одной папки.",
        "What's changed:":
            "Что нового:",
        # ---- emulator / config console (final batch) ----
        "CSpect update check: {reason}.":
            "Проверка CSpect: {reason}.",
        "CSpect update ▸ FAILED — {error}":
            "Обновление CSpect ▸ ОШИБКА — {error}",
        "CSpect update ▸ newer build available: installed {installed}, latest {latest}.":
            "Обновление CSpect ▸ доступна более новая сборка: установлена {installed}, последняя {latest}.",
        "CSpect update ▸ user chose to update to {name}.":
            "Обновление CSpect ▸ пользователь выбрал обновление до {name}.",
        "Could not list the MAME releases: {error}":
            "Не удалось получить список версий MAME: {error}",
        "ERROR: Failed to launch MAME: {error}":
            "ОШИБКА: не удалось запустить MAME: {error}",
        "ERROR: Failed to launch CSpect: {error}":
            "ОШИБКА: Не удалось запустить CSpect: {error}",
        "ERROR: could not extract {name}: {error}":
            "ОШИБКА: не удалось извлечь {name}: {error}",
        "ERROR: hdfmonkey failed - A file can't be opened this is commonly caused by strange characters such as quotes and signs":
            "ОШИБКА: сбой hdfmonkey - не удаётся открыть файл; обычно причина в необычных символах, например кавычках и знаках",
        "ERROR: hdfmonkey failed - A file can't be opened: {command} this is commonly caused by strange characters such as quotes and signs":
            "ОШИБКА: сбой hdfmonkey - не удаётся открыть файл: {command}; обычно причина в необычных символах, например кавычках и знаках",
        "Failed to save configuration file with IOError: {error}":
            "Не удалось сохранить файл конфигурации (IOError): {error}",
        "Found hdfmonkey alongside CSpect: {path}":
            "hdfmonkey найден рядом с CSpect: {path}",
        "MAME exited with code {code}.":
            "MAME завершился с кодом {code}.",
        "MAME install ▸ SUCCESS — MAME detected at: {path}":
            "Установка MAME ▸ УСПЕШНО — MAME найден: {path}",
        "Pygame mode unavailable — run: pip install pygame-ce":
            "Режим pygame недоступен — выполните: pip install pygame-ce",
        "Remote unzip: fetching {path} from the image …":
            "Удалённая распаковка: получение {path} из образа …",
        "Remote zip: fetching {count} item(s) from the image …":
            "Удалённая упаковка: получение элементов из образа: {count} …",
        "Saved configuration file.":
            "Файл конфигурации сохранён.",
        "UI language set to '{lang}' to match the system language — change it on the Settings tab.":
            "Язык интерфейса установлен на '{lang}' в соответствии с системным — изменить можно на вкладке Настройки.",
        "ZX Next Unite update available: {latest} (installed {installed}).":
            "Доступно обновление ZX Next Unite: {latest} (установлена {installed}).",
        "ZX Next Unite update ▸ downloading {asset}…":
            "Обновление ZX Next Unite ▸ загрузка {asset}…",
        "ZX Next Unite update: could not start {name}: {error}":
            "Обновление ZX Next Unite: не удалось запустить {name}: {error}",
        "ZX Next Unite update: download FAILED: {error}":
            "Обновление ZX Next Unite: загрузка НЕ УДАЛАСЬ: {error}",
        "ZX Next Unite update: downloaded — start it any time: {path}":
            "Обновление ZX Next Unite: загружено — запустить можно в любой момент: {path}",
        "ZX Next Unite update: starting {name} and closing…":
            "Обновление ZX Next Unite: запуск {name} и закрытие…",
        "ZX Next Unite update: unpacked to {path}":
            "Обновление ZX Next Unite: распаковано в {path}",
        "Extracting {name} from the image, then starting CSpect…":
            "Извлечение {name} из образа, затем запуск CSpect…",
        "Start CSpect: {name} could not be read from the image, CSpect was not started.":
            "Запуск CSpect: не удалось прочитать {name} из образа — CSpect не запущен.",
        "Extracting {name} from the image, then sending it via NextSync…":
            "Извлечение {name} из образа, затем отправка через NextSync…",
        "Send via NextSync: {name} could not be read from the image, nothing was sent.":
            "Отправка через NextSync: не удалось прочитать {name} из образа — ничего не отправлено.",
        "Send via NextSync {name}":
            "Отправить {name} через NextSync",
        "Start NextSync Remote Explorer":
            "Запустить Remote Explorer NextSync",
        "Stop NextSync Remote Explorer":
            "Остановить Remote Explorer NextSync",
        "Please select a sync root first on the NextSync Remote Explorer tab and retry.":
            "Сначала выберите корневую папку синхронизации на вкладке Remote Explorer NextSync и повторите попытку.",
        # ---- CSpect auto-start actions (SD Card tab menus) ----
        "Send to SD Card and start CSpect with file {name}":
            "Отправить на SD-карту и запустить CSpect с файлом {name}",
        "Send to SD Card and start CSpect: the transfer failed, CSpect was not started.":
            "Отправка на SD-карту и запуск CSpect: передача не удалась — CSpect не запущен.",
        "Sending {name} to the SD card image, then starting CSpect…":
            "Отправка {name} в образ SD-карты, затем запуск CSpect…",
        "Start CSpect with file {name}":
            "Запустить CSpect с файлом {name}",
        "Start MAME with file {name}":
            "Запустить MAME с файлом {name}",
        "Could not start {emulator}":
            "Не удалось запустить {emulator}",
        "Could not prepare a folder for {name}: {error}":
            "Не удалось подготовить папку для {name}: {error}",
        "Start {emulator}: {name} could not be downloaded from the Next, "
        "{emulator} was not started.":
            "Запуск {emulator}: не удалось скачать {name} с Next — {emulator} "
            "не запущен.",
        "Downloading {name} from the Next, then starting {emulator}…":
            "Загрузка {name} с Next, затем запуск {emulator}…",
        "Downloading {name}…":
            "Загрузка {name}…",
        "Start MAME: could not prepare the staging folder {path} ({error}).":
            "Запуск MAME: не удалось подготовить временную папку {path} ({error}).",
        "Send to SD Card and start MAME with file {name}":
            "Отправить на SD-карту и запустить MAME с файлом {name}",
        "Extracting {name} from the image, then starting MAME…":
            "Извлечение {name} из образа, затем запуск MAME…",
        "Start MAME: {name} could not be read from the image, MAME was not started.":
            "Запуск MAME: не удалось прочитать {name} из образа — MAME не запущен.",
        "Send to SD Card and start MAME: the transfer failed, MAME was not started.":
            "Отправка на SD-карту и запуск MAME: передача не удалась — MAME не запущен.",
        "Sending {name} to the SD card image, then starting MAME…":
            "Отправка {name} в образ SD-карты, затем запуск MAME…",
        "MAME cannot load {name} directly; starting MAME without it.":
            "MAME не может загрузить {name} напрямую — MAME будет запущен без этого файла.",
        # ---- dialogs (message boxes) ----
        "CSpect update available":
            "Доступно обновление CSpect",
        "Choose another release…":
            "Выбрать другую версию…",
        "Close":
            "Закрыть",
        "Download and install":
            "Скачать и установить",
        "File or directory already exists locally.":
            "Файл или папка уже существует локально.",
        "File or directory exists":
            "Файл или папка уже существует",
        "Ignore (always in this sync)":
            "Пропускать (всегда в этой синхронизации)",
        "Ignore (one time)":
            "Пропустить (один раз)",
        "Install from .zip…":
            "Установить из .zip…",
        "Install hdfmonkey":
            "Установить hdfmonkey",
        "Later":
            "Позже",
        "MAME update available":
            "Доступно обновление MAME",
        "Open itch.io page":
            "Открыть страницу itch.io",
        "Open the releases page":
            "Открыть страницу релизов",
        "Overwrite local file (always in this sync)":
            "Перезаписывать локальный файл (всегда в этой синхронизации)",
        "Overwrite local file (one time)":
            "Перезаписать локальный файл (один раз)",
        "The automated download failed.":
            "Автоматическая загрузка не удалась.",
        "This is going to completely delete the files in {path} and its sub folders, so they will be unrecoverable.\n\nAre you sure want to continue?":
            "Это полностью удалит файлы в {path} и вложенных папках без возможности восстановления.\n\nПродолжить?",
        "Tip: set a default for this in Settings → \"NextSync — when a sent file or directory exists locally\".":
            "Совет: задайте поведение по умолчанию в Настройках → \"NextSync — when a sent file or directory exists locally\".",
        "Uninstall":
            "Удалить",
        "Update":
            "Обновить",
        "Update downloaded":
            "Обновление загружено",
        "Yes":
            "Да",
        "You can download it manually from the itch.io page in your browser, then install it from the downloaded .zip.":
            "Вы можете скачать его вручную со страницы itch.io в браузере, а затем установить из загруженного .zip.",
        "ZX Next Unite update available":
            "Доступно обновление ZX Next Unite",
        "hdfmonkey download failed":
            "Не удалось скачать hdfmonkey",
        "itch.io download":
            "Загрузка с itch.io",
        # ---- emulator install / update console ----
        "CSpect update check skipped: {reason}":
            "Проверка CSpect пропущена: {reason}",
        "CSpect update ▸ user cancelled the update.":
            "Обновление CSpect ▸ пользователь отменил обновление.",
        "ERROR: CSpect.exe is not present in the same local directory as zx-next-unite. Please install it from http://cspect.org":
            "ОШИБКА: CSpect.exe отсутствует в том же каталоге, что и zx-next-unite. Установите его с http://cspect.org",
        "ERROR: MAME executable not found on PATH. Cannot launch MAME.":
            "ОШИБКА: исполняемый файл MAME не найден в PATH. Запуск MAME невозможен.",
        "Listing the available MAME releases…":
            "Получение списка доступных версий MAME…",
        "MAME install ▸ FAILED — the download and extraction finished, but no mame.exe could be found in downloads/mame.":
            "Установка MAME ▸ ОШИБКА — загрузка и распаковка завершены, но mame.exe не найден в downloads/mame.",
        "MAME install ▸ FAILED — {error}. You can download it manually from https://www.mamedev.org/release.html":
            "Установка MAME ▸ ОШИБКА — {error}. Скачать вручную можно с https://www.mamedev.org/release.html",
        "MAME install ▸ Starting: {tag} ({asset}, ~{size}).":
            "Установка MAME ▸ запуск: {tag} ({asset}, ~{size}).",
        "MAME install ▸ release picker cancelled.":
            "Установка MAME ▸ выбор версии отменён.",
        "MAME is ready to launch now — no restart needed. Use the '🕹  Launch Mame' button.":
            "MAME готов к запуску — перезапуск не нужен. Нажмите кнопку '🕹  Launch Mame'.",
        "MAME update check: could not determine the installed MAME version; skipping.":
            "Проверка MAME: не удалось определить установленную версию — пропуск.",
        "MAME update check: could not determine the latest release; skipping.":
            "Проверка MAME: не удалось определить последнюю версию — пропуск.",
        "MAME update check: could not reach the release site; skipping.":
            "Проверка MAME: не удалось связаться с сайтом релизов — пропуск.",
        "MAME update ▸ user chose to pick a release manually.":
            "Обновление MAME ▸ пользователь выбрал версию вручную.",
        "MAME update ▸ user chose to update to {tag}.":
            "Обновление MAME ▸ пользователь выбрал обновление до {tag}.",
        "On MacOS and Linux mono is required as it runs under it. Please make sure mono is installed.":
            "В MacOS и Linux требуется mono, так как запуск происходит через него. Убедитесь, что mono установлен.",
        "Running as a Flatpak: mono must be installed on the HOST system — the launch is delegated there via flatpak-spawn.":
            "Запуск в виде Flatpak: mono должен быть установлен в ОСНОВНОЙ системе — запуск делегируется туда через flatpak-spawn.",
        "Select a valid ZX Spectrum Next disk image (.img/.hdf) before launching MAME.":
            "Выберите корректный образ диска ZX Spectrum Next (.img/.hdf) перед запуском MAME.",
        "ZX Next Unite update check: could not reach GitHub (offline, or no release published yet); skipping.":
            "Проверка ZX Next Unite: не удалось связаться с GitHub (нет сети или релизы не опубликованы) — пропуск.",
        "ZX Next Unite update check: running as a Flatpak — updates come from your software center, skipping.":
            "Проверка ZX Next Unite: запуск в виде Flatpak — обновления приходят из центра приложений, пропуск.",
        "ZX Next Unite update ▸ skipped by user.":
            "Обновление ZX Next Unite ▸ пропущено пользователем.",
        "ZX Next Unite update: download cancelled.":
            "Обновление ZX Next Unite: загрузка отменена.",
        # ---- SD Card tab: console + dialogs ----
        "Confirm Deletion":
            "Подтвердите удаление",
        "Create":
            "Создать",
        "Create New Folder":
            "Создать новую папку",
        "Created {name} in {folder} on the image ({count} file(s), {bytes} bytes).":
            "Создан {name} в {folder} на образе ({count} файл(ов), {bytes} байт).",
        "Delete files from the image to free space, or switch to a larger image.\nLarger SD card images can be downloaded from:":
            "Удалите файлы из образа, чтобы освободить место, или выберите образ большего размера.\nОбразы SD-карт большего размера можно скачать здесь:",
        "Download":
            "Скачать",
        "Download failed: no valid destination folder.":
            "Загрузка не удалась: нет допустимой папки назначения.",
        "Downloading {name} from {url}":
            "Загрузка {name} с {url}",
        "ERROR: hdfmonkey could not be found. Use the 'Download and install HDF Monkey' button (bottom right of the SD Card tab) to install it automatically, or do a full CSpect install from the itch.io tab, which also bundles hdfmonkey. It can also be installed manually from https://github.com/gasman/hdfmonkey — restart the app once installed.":
            "ОШИБКА: hdfmonkey не найден. Нажмите кнопку 'Скачать и установить HDF Monkey' (внизу справа на вкладке SD Card), чтобы установить его автоматически, или выполните полную установку CSpect со вкладки itch.io — она также включает hdfmonkey. Его можно установить и вручную с https://github.com/gasman/hdfmonkey — после установки перезапустите приложение.",
        "Extracted disk image: {path}":
            "Образ диска распакован: {path}",
        "Extracted {count} file(s) from {name} into {folder} on the image.":
            "Извлечено файлов: {count} из {name} в {folder} на образе.",
        "Extracting image... %p%":
            "Извлечение образа... %p%",
        "Failed downloading NextZXOS image: {error}":
            "Не удалось скачать образ NextZXOS: {error}",
        "Load Failed":
            "Ошибка загрузки",
        "The image was extracted but could not be loaded:":
            "Образ был распакован, но не удалось его загрузить:",
        "Failed executing hdfmonkey, please make sure it is installed in the same local directory as zx-next-unite.":
            "Не удалось запустить hdfmonkey — убедитесь, что он установлен в том же каталоге, что и zx-next-unite.",
        "Failed extracting NextZXOS image: {error}":
            "Не удалось распаковать образ NextZXOS: {error}",
        "Failed loading image: {path}.":
            "Не удалось загрузить образ: {path}.",
        "No SD-card disk image selected — pick or create a .img/.hdf at the top of this tab to unlock the emulator Launch buttons.":
            "Образ SD-карты не выбран — выберите или создайте .img/.hdf вверху этой вкладки, чтобы разблокировать кнопки запуска эмулятора.",
        "Nothing to move: items are already in this folder.":
            "Нечего перемещать: элементы уже в этой папке.",
        "Only {free} MB free out of {total} MB ({used} % used, {pct} % free).":
            "Свободно всего {free} МБ из {total} МБ ({used} % занято, {pct} % свободно).",
        "Please load an image file first !":
            "Сначала загрузите файл образа!",
        "Please load an image first!":
            "Сначала загрузите образ!",
        "Please select an image file or folder first to delete!":
            "Сначала выберите файл или папку образа для удаления!",
        "Please select an image file or folder first to rename!":
            "Сначала выберите файл или папку образа для переименования!",
        "Remote unzip cancelled — the image is unchanged.":
            "Удалённая распаковка отменена — образ не изменён.",
        "Remote unzip: download from the image failed or was cancelled — the image is unchanged.":
            "Удалённая распаковка: загрузка из образа не удалась или была отменена — образ не изменён.",
        "Remote unzip: upload into the image failed or was cancelled.":
            "Удалённая распаковка: запись в образ не удалась или была отменена.",
        "Remote zip cancelled — no zip was created.":
            "Удалённая упаковка отменена — zip не создан.",
        "Remote zip: download from the image failed or was cancelled — no zip was created.":
            "Удалённая упаковка: загрузка из образа не удалась или была отменена — zip не создан.",
        "Remote zip: upload into the image failed or was cancelled.":
            "Удалённая упаковка: запись в образ не удалась или была отменена.",
        "SD Image Nearly Full":
            "Образ SD почти заполнен",
        "The SD card image is nearly full.":
            "Образ SD-карты почти заполнен.",
        "The hdfmonkey provided by the CSpect itch.io package is not executable. Make it executable by running:":
            "hdfmonkey из пакета CSpect с itch.io не является исполняемым. Сделайте его исполняемым командой:",
        "The image is completely full ({total} MB capacity, 0 MB free).":
            "Образ полностью заполнен (объём {total} МБ, свободно 0 МБ).",
        # ---- SD Card console: banner, detection, update checks ----
        "CSpect - by Mike Dailly http://cspect.org":
            "CSpect - автор: Mike Dailly http://cspect.org",
        "CSpect is up to date (installed {installed}, latest {latest}).":
            "CSpect актуален (установлена {installed}, последняя {latest}).",
        "Checking for a newer MAME release…":
            "Проверка новой версии MAME…",
        "Checking for a newer ZX Next Unite release on GitHub…":
            "Проверка на GitHub новой версии ZX Next Unite…",
        "Checking itch.io for a newer CSpect release…":
            "Проверка на itch.io новой версии CSpect…",
        "HDF Monkey - by Matt Westcott":
            "HDF Monkey - автор: Matt Westcott",
        "Inspired by HDFM-GOOEY - by em00k":
            "Вдохновлено HDFM-GOOEY - автор: em00k",
        "Loaded configuration file.":
            "Файл конфигурации загружен.",
        "MAME - ZX Spectrum Next support by Holub https://wiki.specnext.dev/MAME:Installing":
            "MAME - поддержка ZX Spectrum Next: Holub https://wiki.specnext.dev/MAME:Installing",
        "MAME is up-to-date (installed 0.{installed}, latest 0.{latest}).":
            "MAME актуален (установлена 0.{installed}, последняя 0.{latest}).",
        "MAME is up-to-date with a patched version (installed 0.{installed}, latest 0.{latest}).":
            "MAME актуален (используется модифицированная сборка; установлена 0.{installed}, последняя 0.{latest}).",
        "MAME version: {version}":
            "Версия MAME: {version}",
        "NextSync - by Jari Komppa and Julien Clauzel":
            "NextSync - авторы: Jari Komppa и Julien Clauzel",
        "Using CSpect under downloads/cspect: {path}":
            "Используется CSpect из downloads/cspect: {path}",
        "Using MAME under: {path}":
            "Используется MAME: {path}",
        "Using hdfmonkey bundled with CSpect: {path}":
            "Используется hdfmonkey из комплекта CSpect: {path}",
        "Welcome to ZX Next Unite {version}":
            "Добро пожаловать в ZX Next Unite {version}",
        "ZX Next Unite is up to date (installed {installed}, latest {latest}).":
            "ZX Next Unite актуален (установлена {installed}, последняя {latest}).",
        "zx-next-unite - by Julien Clauzel 2024":
            "zx-next-unite - автор: Julien Clauzel 2024",
        "No image loaded": "Образ не загружен",
        # ---- itch.io item viewer + web-link labels ----
        "About": "Описание",
        "Open on {site}": "Открыть на {site}",
        "Open {url}": "Открыть {url}",
        "✓  Re-install": "✓  Переустановить",
        "⬇  Install": "⬇  Установить",
        "⬇  Installing…": "⬇  Установка…",
        "📂  Open download folder": "📂  Открыть папку загрузок",
        # ---- gallery item viewer (Classic + Retro) ----
        "🌐  Open on website": "🌐  Открыть на сайте",
        "🌐  Open on {site}": "🌐  Открыть на {site}",
        "📂  Open install folder": "📂  Открыть папку установки",
        "🗑  Uninstall": "🗑  Удалить",
        # ---- NextSync log lines (user-facing; protocol diagnostics stay English) ----
        "(-send saves received files under: {folder})":
            "(-send сохраняет полученные файлы в: {folder})",
        "Aliases:":
            "Псевдонимы:",
        "Cancel requested — stopping after current file":
            "Запрошена отмена — остановка после текущего файла",
        "Cannot create {path}: {error}":
            "Не удалось создать {path}: {error}",
        "Closing connection":
            "Закрытие соединения",
        "Connected by {address} port {port}":
            "Подключение с {address}, порт {port}",
        "Disconnected":
            "Отключено",
        "Existing-file policy: {policy} (change in Settings -> 'NextSync - when a sent file or directory exists locally').":
            "Правило для существующих файлов: {policy} (изменяется в Настройках -> 'NextSync - when a sent file or directory exists locally').",
        "Failed to rename {path}: {error}":
            "Не удалось переименовать {path}: {error}",
        "IP addresses:":
            "IP-адреса:",
        "Import failed: no valid destination folder.":
            "Импорт не удался: нет допустимой папки назначения.",
        "Navigate to a folder in the left local file explorer, press 'Set current folder as new sync root folder' to choose a sync root and then press the 'Start Classic NextSync server' button.":
            "Перейдите к папке в левом проводнике, нажмите 'Set current folder as new sync root folder', чтобы выбрать корень синхронизации, затем нажмите 'Start Classic NextSync server'.",
        "NextSync HTTP bridge NOT started: {error}":
            "HTTP-мост NextSync НЕ запущен: {error}",
        "NextSync HTTP bridge listening on port {port}":
            "HTTP-мост NextSync слушает порт {port}",
        "NextSync HTTP bridge stopped.":
            "HTTP-мост NextSync остановлен.",
        "NextSync HTTP bridge: bearer-token protection is ON (requests must carry the {header} header; others get HTTP 401)":
            "HTTP-мост NextSync: защита токеном ВКЛЮЧЕНА (запросы должны содержать заголовок {header}; остальные получат HTTP 401)",
        "NextSync is already running — please wait for it to finish.":
            "NextSync уже запущен — дождитесь завершения.",
        "NextSync listening to port {port}":
            "NextSync слушает порт {port}",
        "NextSync server, protocol version: {version}":
            "Сервер NextSync, версия протокола: {version}",
        "No network detected - connect to Wi-Fi/Ethernet to see the address your Next should sync to.":
            "Сеть не обнаружена — подключитесь к Wi-Fi/Ethernet, чтобы увидеть адрес для синхронизации с Next.",
        "Note":
            "Примечание",
        "Nothing (more) to sync":
            "Больше нечего синхронизировать",
        "Now run one of these commands on your Next:":
            "Теперь выполните одну из этих команд на Next:",
        "Primary IP:":
            "Основной IP:",
        "Received {name} ({bytes} bytes)":
            "Получено {name} ({bytes} байт)",
        "Receiving files from the Next...":
            "Получение файлов с Next...",
        "Receiving: {name} -> {path}":
            "Получение: {name} -> {path}",
        "Remote .sync5 update failed while reading {path}: {error} — nothing was sent.":
            "Удалённое обновление .sync5 не удалось при чтении {path}: {error} — ничего не отправлено.",
        "Remote .sync5 update refused: {path} does not carry the expected 'NextSync {version}' banner — wrong or stale file.":
            "Удалённое обновление .sync5 отклонено: {path} не содержит ожидаемой строки 'NextSync {version}' — неверный или устаревший файл.",
        "Remote .sync5 update: staging {path} ({size} bytes)…":
            "Удалённое обновление .sync5: передача {path} ({size} байт)…",
        "Remote .sync5 update: staged copy verified ({size} bytes) — swapping it in…":
            "Удалённое обновление .sync5: промежуточная копия проверена ({size} байт) — выполняется замена…",
        "Remote .sync5 update FAILED mid-swap: the Next may be missing {target}. If .sync5 no longer starts, rename {backup} back to sync5 in the NextZXOS Browser (the staged {staged} can be deleted).":
            "Удалённое обновление .sync5 НЕ УДАЛОСЬ в середине замены: возможно, на Next сейчас нет {target}. Если .sync5 больше не запускается, переименуйте {backup} обратно в sync5 в браузере NextZXOS (промежуточный файл {staged} можно удалить).",
        "Remote .sync5 update complete: {version} is on the card. The session will now close — run {command} on the Next to start the new dot.":
            "Удалённое обновление .sync5 завершено: {version} уже на карте. Сессия сейчас будет закрыта — выполните {command} на Next, чтобы запустить новую dot-команду.",
        "Remote .sync5 update failed: {reason}. Nothing was swapped — the Next still runs its current dot.":
            "Удалённое обновление .sync5 не удалось: {reason}. Ничего не заменено — Next по-прежнему использует текущую dot-команду.",
        "Remote explorer: connected to {address}":
            "Удалённый проводник: подключено к {address}",
        "Remote explorer: connection error from the Next ({error}) — session over.":
            "Удалённый проводник: ошибка соединения с Next ({error}) — сеанс завершён.",
        "Remote explorer: the Next closed the connection.":
            "Удалённый проводник: Next закрыл соединение.",
        "Remote explorer: no word from the Next for {seconds}s — assuming it is gone (powered off? Wi-Fi dropped?)":
            "Удалённый проводник: от Next нет данных {seconds}с — считаем, что он пропал (выключен? пропал Wi-Fi?)",
        "Remote explorer: turned away a second Next at {address} — a session is already active (Busy).":
            "Удалённый проводник: второй Next с {address} отклонён — сеанс уже активен (Busy).",
        "Remote explorer: server keeps running in the background — stop it from the Remote Explorer view.":
            "Удалённый проводник: сервер продолжает работать в фоне — остановите его в виде Remote Explorer.",
        "Remote explorer: navigate to a folder in the left file explorer, press 'Set current folder as new sync root folder', click 'Start Remote Explorer NextSync server', then run {command} on your Next.":
            "Удалённый проводник: перейдите к папке в левом проводнике, нажмите 'Set current folder as new sync root folder', затем 'Start Remote Explorer NextSync server' и выполните {command} на Next.",
        "Remote explorer: port {port} is already in use — is another ZX-Next-Unite (or NextSync server) already running?":
            "Удалённый проводник: порт {port} уже занят — возможно, запущен другой ZX-Next-Unite (или сервер NextSync)?",
        "Remote explorer: the Next disconnected (BREAK / Bye) — restarting the listen server; run {command} on your Next to reconnect.":
            "Удалённый проводник: Next отключился (BREAK / Bye) — перезапуск сервера прослушивания; выполните {command} на Next для повторного подключения.",
        "Remote explorer: the Next disconnected (BREAK / Bye). Press 'Start Remote Explorer NextSync server' to accept a new connection.":
            "Удалённый проводник: Next отключился (BREAK / Bye). Нажмите 'Start Remote Explorer NextSync server', чтобы принять новое подключение.",
        "Remote explorer: waiting for {command} on port {port}…":
            "Удалённый проводник: ожидание {command} на порту {port}…",
        "Renamed: {old} -> {new}":
            "Переименовано: {old} -> {new}",
        "Running on host:":
            "Запущено на хосте:",
        "Saving incoming files under: {folder}":
            "Входящие файлы сохраняются в: {folder}",
        "Send via NextSync: nothing to send in {folder}.":
            "Отправка через NextSync: в {folder} нечего отправлять.",
        "Sending {folder} via Remote Explorer (-listen) → {target} …":
            "Отправка {folder} через удалённый проводник (-listen) → {target} …",
        "Set a sync root folder first: navigate to the folder in the left local file explorer and press 'Set current folder as new sync root folder'.":
            "Сначала выберите корневую папку синхронизации: перейдите к ней в левом проводнике и нажмите 'Set current folder as new sync root folder'.",
        "Skipped (already exists): {path}":
            "Пропущено (уже существует): {path}",
        "Skipped {path}: cannot import a folder into itself.":
            "Пропущено {path}: нельзя импортировать папку саму в себя.",
        "Stop the running sync before starting the remote server.":
            "Остановите текущую синхронизацию перед запуском удалённого сервера.",
        "Sync file list has {count} files.":
            "В списке синхронизации {count} файлов.",
        "Sync point file {name} not found, syncing all files regardless of timestamp.":
            "Файл точки синхронизации {name} не найден — синхронизируются все файлы независимо от даты.",
        "Sync point updated with {count} received file(s)":
            "Точка синхронизации обновлена: получено файлов {count}",
        "Upload connection closed":
            "Соединение передачи закрыто",
        "Upload finished, {count} file(s) received":
            "Передача завершена, получено файлов: {count}",
        "Using {folder} as sync root":
            "Корень синхронизации: {folder}",
        "WARNING":
            "ВНИМАНИЕ",
        "Warning":
            "Предупреждение",
        "Warning! Ignore file {name} not found in directory. All files will be synced, possibly including this file.":
            "Внимание! Файл исключений {name} не найден в папке. Будут синхронизированы все файлы, возможно включая этот.",
        "{kb} kilobytes payload, {rate} kBps effective speed":
            "{kb} килобайт полезных данных, эффективная скорость {rate} кБ/с",
        "{kb} kilobytes transferred in {seconds} seconds, {rate} kBps":
            "Передано {kb} килобайт за {seconds} с, {rate} кБ/с",
        "{severity}: Ready to sync {count} files, {kb} kilobytes.":
            "{severity}: готово к синхронизации {count} файлов, {kb} килобайт.",
        # ---- checkboxes ----
        "Alien Floyd's — animated background on all tabs (Retro/pygame)":
            "Alien Floyd's — анимированный фон на всех вкладках (Retro/pygame)",
        "Alien Floyd's — show the full-window 'Alien Floyd's' tab (Retro/pygame)":
            "Alien Floyd's — показывать полноэкранную вкладку «Alien Floyd's» (Retro/pygame)",
        "Check for CSpect update on itch.io on startup":
            "Проверять обновления CSpect на itch.io при запуске",
        "Check for ZX Next Unite updates at startup on Github":
            "Проверять обновления ZX Next Unite на GitHub при запуске",
        "Check for a newer MAME version at startup":
            "Проверять новую версию MAME при запуске",
        "Disable 'No emulators detected' message at startup":
            "Отключить сообщение «Эмуляторы не найдены» при запуске",
        "Do not prompt for confirmation on deletion.":
            "Не запрашивать подтверждение при удалении.",
        "Enable NextSync HTTP bridge (web server for the Next's .http command)":
            "Включить HTTP-мост NextSync (веб-сервер для команды .http на Next)",
        "Enable crash log file generation":
            "Включить запись журнала сбоев",
        "Enable multi API endpoints search (GetIt, ZXDB & zxArt search together).":
            "Включить поиск сразу по нескольким API (GetIt, ZXDB и zxArt).",
        "Enable search autocompletion.": "Включить автодополнение поиска.",
        "NextSync — starfield log animation (Retro/pygame mode)":
            "NextSync — анимация звёздного поля в журнале (режим Retro/pygame)",
        "Perform pre-availability check on Downloads (ZXDB & zxArt).":
            "Проверять доступность файлов перед скачиванием (ZXDB и zxArt).",
        "Require bearer token": "Требовать токен доступа",
        "SD Card - Warn when an image is nearly full.":
            "SD-карта — предупреждать, когда образ почти заполнен.",
        "Send deleted files to the Recycle Bin (local file explorers).":
            "Отправлять удалённые файлы в Корзину (локальные проводники).",
        "Show the itch.io tab (browse & install your itch.io collections)":
            "Показывать вкладку itch.io (просмотр и установка ваших коллекций)",
        "Slow transfer": "Медленная передача",
        "Unite! — Invaders background animation (Retro/pygame mode)":
            "Unite! — фоновая анимация Invaders (режим Retro/pygame)",
        # ---- radio buttons / group boxes ----
        "Always sync (send everything)": "Всегда синхронизировать (отправлять всё)",
        "Sync changed files (continuous)": "Синхронизировать изменения (постоянно)",
        "Sync once": "Синхронизировать один раз",
        "Sync mode": "Режим синхронизации",
        # ---- placeholders ----
        "Filter by name, type or size...": "Фильтр по имени, типу или размеру…",
        "Filter by name...": "Фильтр по имени…",
        "Local folder path...": "Путь к локальной папке…",
        "New directory name ...": "Имя нового каталога…",
        "Paste your personal API key (itch.io → Settings → API keys)":
            "Вставьте свой ключ API (itch.io → Settings → API keys)",
        "Path inside the SD card image...": "Путь внутри образа SD…",
        "SD card image path...": "Путь к образу SD-карты…",
        "Search ZXDB games... (leave empty for random selection)":
            "Поиск игр в ZXDB… (пусто = случайный выбор)",
        "Search across GetIt + ZXDB + zxArt...":
            "Поиск в GetIt + ZXDB + zxArt…",
        "Search files... (leave empty for latest 20)":
            "Поиск файлов… (пусто = последние 20)",
        "Search your itch.io library (collections + purchases)…":
            "Поиск по вашей библиотеке itch.io (коллекции и покупки)…",
        "Search zxART productions... (leave empty to browse latest)":
            "Поиск работ в zxART… (пусто = смотреть новинки)",
        "Sync root folder...": "Корневая папка синхронизации…",
        "bearer token (generated when you enable the checkbox)":
            "токен доступа (создаётся при включении флажка)",
        # ---- short tooltips ----
        "Browse mode": "Режим просмотра",
        "Search mode": "Режим поиска",
        "Pick a letter": "Выберите букву",
        "Double-click to enlarge": "Двойной щелчок — увеличить",
        "Double-click to open full view": "Двойной щелчок — открыть во весь экран",
        "Generate a new random 64-character bearer token":
            "Создать новый случайный токен из 64 символов",
        "Select a disk image to be loaded.": "Выберите образ диска для загрузки.",
        "No SD card image is currently loaded.":
            "Образ SD-карты сейчас не загружен.",
        "Re-read the current local folder from disk.":
            "Перечитать текущую локальную папку с диска.",
        "Drag to resize the file explorers / log window split.":
            "Перетащите, чтобы изменить разделение проводники / журнал.",
        "Drag to resize the results / MOTD split.":
            "Перетащите, чтобы изменить разделение результаты / MOTD.",
        "Drag to set the background image opacity (0–100 %).":
            "Перетащите, чтобы задать непрозрачность фона (0–100 %).",
        "Type a value 0–100 to set the background image opacity.":
            "Введите значение 0–100 для непрозрачности фона.",
        "Preview of the selected background image.":
            "Предпросмотр выбранного фонового изображения.",
        "Open https://itch.io/ in your browser":
            "Открыть https://itch.io/ в браузере",
        "Connect to itch.io using the API key above.":
            "Подключиться к itch.io с помощью ключа API выше.",
        "Disconnect from itch.io and clear the listed items.":
            "Отключиться от itch.io и очистить список элементов.",
        "Open https://itch.io/user/settings/api-keys in your browser":
            "Открыть https://itch.io/user/settings/api-keys в браузере",
        "Color used for directory name entries in the image explorer.":
            "Цвет имён каталогов в проводнике образа.",
        "Color used for file name entries in the image explorer.":
            "Цвет имён файлов в проводнике образа.",
        "Color used for the 'DIR' type label column of directory entries.":
            "Цвет столбца типа «DIR» у каталогов.",
        "Color used for the '[Up Directory..]' navigation row in the image explorer.":
            "Цвет строки навигации «[Up Directory..]».",
        "Color used for the file extension column in the image explorer.":
            "Цвет столбца расширения файла.",
        "Color used for the file size column in the image explorer.":
            "Цвет столбца размера файла.",
        "Background color behind the file explorers and the whole app window.":
            "Цвет фона под файловыми проводниками и всем окном приложения.",
        "Discard the hand-picked colors above and restore the themed defaults.":
            "Отменяет выбранные выше цвета и возвращает значения темы по умолчанию.",
        "MAME display aspect ratio (-aspect).":
            "Соотношение сторон экрана MAME (-aspect).",
        "Perform a single sync and then stop the server.":
            "Выполнить одну синхронизацию и остановить сервер.",
        "Keep listening and send every file each time, ignoring the sync point.":
            "Продолжать слушать и каждый раз отправлять все файлы, игнорируя точку синхронизации.",
        "Keep listening and send only files that are new or changed since the\nlast sync (skips files recorded in the sync point). The default mode.":
            "Продолжать слушать и отправлять только новые или изменённые с\nпоследней синхронизации файлы (пропускает записанные в точке\nсинхронизации). Режим по умолчанию.",
        "Make the folder currently shown in the explorer above the new sync root.":
            "Сделать папку, показанную в проводнике выше, корнем синхронизации.",
        "Pick a random page from the full GetIt catalogue and show its entries.":
            "Открыть случайную страницу каталога GetIt и показать её записи.",
        "Pick a random page of zxART productions and show its entries.":
            "Открыть случайную страницу работ zxART и показать её.",
        "Show the most recent zxART productions/pictures (sorted by date).":
            "Показать самые свежие работы/картинки zxART (по дате).",
        "Show the most recently added/updated ZXDB games.":
            "Показать недавно добавленные или обновлённые игры ZXDB.",
        "Fetch random entries from GetIt + ZXDB + zxArt and merge them here":
            "Получить случайные записи из GetIt + ZXDB + zxArt и объединить их здесь",
        "Fetch the latest releases from GetIt + ZXDB + zxArt and merge them here":
            "Получить последние новинки из GetIt + ZXDB + zxArt и объединить их здесь",
        "zxART catalog display language.\nPersisted across sessions in the config file.":
            "Язык каталога zxART.\nСохраняется в файле конфигурации.",
        "Requires the optional 'pygame-ce' package.\nInstall with: pip install pygame-ce":
            "Требуется необязательный пакет «pygame-ce».\nУстановка: pip install pygame-ce",
        "Load a ZX Spectrum Next disk image first — then CSpect can boot it from the mounted SD card.":
            "Сначала загрузите образ диска ZX Spectrum Next — CSpect запустит его с подключённой SD-карты.",
        "Select emulator image file: {path}":
            "Выбрать образ для эмулятора: {path}",
        "No writable disk image available.":
            "Нет доступного образа диска для записи.",
        ".img file {path} already in use.":
            "Файл .img {path} уже используется.",
        "The disk image {path} can no longer be found — it may have been moved, renamed or deleted.":
            "Образ диска {path} больше не найден — возможно, он был перемещён, переименован или удалён.",
        "Select a ZX Spectrum Next disk image (.img/.hdf) first — then MAME can boot it as the Next's hard disk.":
            "Сначала выберите образ диска (.img/.hdf) ZX Spectrum Next — MAME запустит его как жёсткий диск Next.",
        "Select the parent folder inside the SD card image\n(at the top level this returns the target to the image root).":
            "Перейти в родительскую папку внутри образа SD\n(на верхнем уровне возвращает к корню образа).",
        "Go up one folder in the local file explorer\n(same as double-clicking its '..' entry).":
            "Подняться на папку вверх в локальном проводнике\n(как двойной щелчок по записи «..»).",
        "Re-list the current image folder from the SD card image\n(runs 'hdfmonkey ls' again).":
            "Заново получить список текущей папки образа SD\n(повторно запускает «hdfmonkey ls»).",
        "Download a ready-to-use NextZXOS SD card image from zxnext.uk,\nsave it to disk, and load it automatically.":
            "Скачать готовый образ SD-карты NextZXOS с zxnext.uk,\nсохранить на диск и загрузить автоматически.",
        "Switch between the classic table view and the picture (gallery)\nview. Persisted across sessions in the config file.":
            "Переключение между классической таблицей и видом галереи.\nСохраняется в файле конфигурации.",
        "Switch between the classic table view and the picture (gallery) view.\nPersisted across sessions in the config file.":
            "Переключение между классической таблицей и видом галереи.\nСохраняется в файле конфигурации.",
        "Language of the application's buttons, labels and checkboxes.\nApplies immediately; texts written while the app runs (logs, dialogs)\nfollow after a restart. Saved to the configuration file.":
            "Язык кнопок, надписей и флажков приложения.\nПрименяется сразу; тексты, создаваемые во время работы (журнал,\nдиалоги), сменятся после перезапуска. Сохраняется в конфигурации.",
        "🌐  Language set to match your system":
            "🌐  Язык подобран по вашей системе",
        "The interface language was set to match your system language.\nYou can change it anytime in the Settings tab (\"Application language:\").":
            "Язык интерфейса установлен по языку вашей системы.\nЕго можно в любой момент изменить на вкладке Settings\n(«Язык приложения:»).",
        # ---- Help tab (INIT_HELP; rebuilt per language by _repopulate_help) ----
        "Welcome to zx-next-unite {version} help":
            "Добро пожаловать в справку zx-next-unite {version}",
        "Introduction:":
            "Введение:",
        "HdfmGooey was initially created by em00k and NextSync by Jari Komppa.":
            "HdfmGooey изначально создал em00k, а NextSync — Jari Komppa.",
        "A while back I rambled with the idea of an all in one bootstrapper transfer tool to":
            "Когда-то я вынашивал идею инструмента «всё в одном» для переноса и запуска, чтобы",
        "avoid manipulating SD cards for the Spectrum Next and that was the initial idea of it.":
            "не возиться с SD-картами для Spectrum Next — с этого всё и началось.",
        "Last but not least some source code was lost from HDFM Gooey and the tool was stuck back in that time,":
            "И не в последнюю очередь: часть исходного кода HDFM Gooey была утеряна, и инструмент застрял в том времени;",
        "with the agreement of em00k I started a rewrite in Python and later with Jari.":
            "с согласия em00k я начал переписывать его на Python, позже к работе подключился Jari.",
        "The point of using Python was that it would also provide MacOS and Linux portability.":
            "Смысл выбора Python был в том, что он заодно даст переносимость на MacOS и Linux.",
        "Later down the line I then extended the NextSync functionality from Sync3 to Sync4.":
            "Позже я расширил функциональность NextSync с Sync3 до Sync4.",
        "The new .sync5 command for the Next speaks Sync4 and therefore allows sending files and directories using the -send command line option.":
            "Новая команда .sync5 для Next говорит на Sync4 и потому позволяет отправлять файлы и каталоги параметром командной строки -send.",
        "There is as well a new nextsync5.py command line located at the root of the repository that supports the new Sync4 protocol.":
            "Есть также новая консольная утилита nextsync5.py в корне репозитория, поддерживающая новый протокол Sync4.",
        "Here we are now you have it!":
            "Вот мы и здесь — пользуйтесь!",
        "Keyboard shortcuts":
            "Горячие клавиши",
        "The three file explorers (SD Card local, SD Card disk image and NextSync local) share these shortcuts. Copy / Cut / Paste work across all three explorers and also exchange with the operating-system clipboard (e.g. copy in Windows Explorer, paste into the disk image, and vice-versa):":
            "Три файловых менеджера (локальный SD Card, образ диска SD Card и локальный NextSync) используют одни и те же сочетания. Копировать / Вырезать / Вставить работают между всеми тремя менеджерами и обмениваются с буфером обмена операционной системы (например, скопируйте в Проводнике Windows, вставьте в образ диска, и наоборот):",
        "    Ctrl+C  -  Copy the selected file(s)/folder(s) to the shared clipboard.":
            "    Ctrl+C  -  Скопировать выбранные файлы/папки в общий буфер обмена.",
        "    Ctrl+X  -  Cut the selection (moved to the destination on the next paste).":
            "    Ctrl+X  -  Вырезать выделение (переносится в место назначения при следующей вставке).",
        "    Ctrl+V  -  Paste into the selected / currently shown folder.":
            "    Ctrl+V  -  Вставить в выбранную / текущую папку.",
        "    F2      -  Rename the selected file or folder.":
            "    F2      -  Переименовать выбранный файл или папку.",
        "    Delete  -  Delete the selected file or folder (disk-image & NextSync explorers).":
            "    Delete  -  Удалить выбранный файл или папку (менеджеры образа диска и NextSync).",
        "In the picture (gallery) item viewer (double-click an item in the GetIt, ZXDB, zxArt or itch.io tabs):":
            "В просмотрщике элементов с картинками (галерее) (двойной щелчок по элементу на вкладках GetIt, ZXDB, zxArt или itch.io):",
        "    Esc           -  Close the viewer and return to the gallery.":
            "    Esc           -  Закрыть просмотрщик и вернуться в галерею.",
        "    Left / Right  -  Show the previous / next screenshot.":
            "    Left / Right  -  Показать предыдущий / следующий снимок экрана.",
        "Third party license":
            "Лицензии третьих сторон",
        "zx-next-unite is released under the MIT license. It is a Qt Application using PySide6 (Qt for Python) on top of Qt6, used under the GNU LGPL v3.":
            "zx-next-unite выпускается под лицензией MIT. Это Qt-приложение на PySide6 (Qt for Python) поверх Qt6, используемого по GNU LGPL v3.",
        "Please refer to the LICENSE and THIRD-PARTY-NOTICES.md files on github: https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE and https://github.com/jclauzel/ZX-Next-Unite/blob/main/THIRD-PARTY-NOTICES.md.":
            "См. файлы LICENSE и THIRD-PARTY-NOTICES.md на github: https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE и https://github.com/jclauzel/ZX-Next-Unite/blob/main/THIRD-PARTY-NOTICES.md.",
        "PySide6 is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The pre-built release executables do include PySide6/Qt; since the app's full source code is published, they can be rebuilt with a modified Qt.":
            "PySide6 не входит в ручную установку python и ставится отдельно (см. инструкцию по установке). Готовые сборки релизов включают PySide6/Qt; поскольку полный исходный код приложения опубликован, их можно пересобрать с изменённым Qt.",
        "zx-next-unite also uses pygame-ce (the community edition of pygame) for its animated backgrounds and visualizations (e.g. the 'Alien Floyd's' effects). Many thanks to the pygame and pygame-ce communities - see https://pyga.me and https://www.pygame.org.":
            "zx-next-unite также использует pygame-ce (общественную редакцию pygame) для анимированных фонов и визуализаций (например, эффектов 'Alien Floyd's'). Большое спасибо сообществам pygame и pygame-ce - см. https://pyga.me и https://www.pygame.org.",
        "pygame-ce is distributed under the GNU LGPL v2.1 license and, like PySide6, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions).":
            "pygame-ce распространяется по лицензии GNU LGPL v2.1 и, как и PySide6, не входит в ручную установку python — ставится отдельно (см. инструкцию по установке).",
        "zx-next-unite optionally uses itch-dl by Dragoon Aethis to power the itch.io tab (browsing and installing your itch.io collections). Many thanks to its author - see https://github.com/DragoonAethis/itch-dl.":
            "zx-next-unite опционально использует itch-dl от Dragoon Aethis для вкладки itch.io (просмотр и установка ваших коллекций itch.io). Большое спасибо автору - см. https://github.com/DragoonAethis/itch-dl.",
        "itch-dl is distributed under the MIT license (Copyright (c) 2022 Dragoon Aethis) and, like PySide6 and pygame-ce, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The itch.io tab is only shown when itch-dl is installed.":
            "itch-dl распространяется по лицензии MIT (Copyright (c) 2022 Dragoon Aethis) и, как PySide6 и pygame-ce, не входит в ручную установку python — ставится отдельно (см. инструкцию по установке). Вкладка itch.io видна, только когда itch-dl установлен.",
        "zx-next-unite optionally uses Flask by the Pallets team to power the NextSync HTTP bridge - the web server behind the Next's .http dot command that lets one Next drive another Next's SD card. Many thanks to its authors - see https://flask.palletsprojects.com and https://github.com/pallets/flask.":
            "zx-next-unite опционально использует Flask от команды Pallets для HTTP-моста NextSync - веб-сервера за dot-командой .http, позволяющей одному Next управлять SD-картой другого Next. Большое спасибо авторам - см. https://flask.palletsprojects.com и https://github.com/pallets/flask.",
        "Flask is distributed under the BSD-3-Clause license and, like the other optional packages, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The HTTP bridge toggle in Settings is greyed out until Flask is installed.":
            "Flask распространяется по лицензии BSD-3-Clause и, как остальные необязательные пакеты, не входит в ручную установку python — ставится отдельно (см. инструкцию по установке). Переключатель HTTP-моста в Settings недоступен, пока Flask не установлен.",
        "zx-next-unite optionally uses Send2Trash by Andrew Senetar and contributors (originally by Virgil Dupras) to send files deleted in the local file explorers to the system Recycle Bin / Trash instead of removing them permanently. Many thanks to its authors - see https://github.com/arsenetar/send2trash.":
            "zx-next-unite опционально использует Send2Trash от Andrew Senetar и соавторов (изначально Virgil Dupras), чтобы файлы, удалённые в локальных менеджерах, отправлялись в системную Корзину, а не удалялись безвозвратно. Большое спасибо авторам - см. https://github.com/arsenetar/send2trash.",
        "Send2Trash is distributed under the BSD license and, like the other optional packages, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The 'Send deleted files to the Recycle Bin' toggle in Settings is greyed out until Send2Trash is installed.":
            "Send2Trash распространяется по лицензии BSD и, как остальные необязательные пакеты, не входит в ручную установку python — ставится отдельно (см. инструкцию по установке). Переключатель 'Отправлять удалённые файлы в Корзину' в Settings недоступен, пока Send2Trash не установлен.",
        "zx-next-unite's optional pre-compiled Windows binary is built with PyInstaller by the PyInstaller Development Team, which bundles the app and all of its dependencies into a single standalone executable. Many thanks to its authors - see https://pyinstaller.org and https://github.com/pyinstaller/pyinstaller.":
            "Необязательный предсобранный двоичный файл Windows для zx-next-unite собирается PyInstaller от PyInstaller Development Team, который упаковывает приложение и все зависимости в один автономный исполняемый файл. Большое спасибо авторам - см. https://pyinstaller.org и https://github.com/pyinstaller/pyinstaller.",
        "PyInstaller is distributed under its GPL 2.0 license with a special exception that explicitly permits packaging applications of any license. It is a build-time tool only - used to produce the pre-compiled binary - and is not needed when running zx-next-unite from source with a manual python install.":
            "PyInstaller распространяется по своей лицензии GPL 2.0 со специальным исключением, прямо разрешающим упаковывать приложения под любой лицензией. Это инструмент только для сборки - им создаётся предсобранный двоичный файл - и он не нужен при запуске zx-next-unite из исходников с ручной установкой python.",
        "The pre-compiled Windows binary is additionally compressed with UPX (the Ultimate Packer for eXecutables) by Markus Oberhumer, Laszlo Molnar and John Reiser. Many thanks to its authors - see https://upx.github.io and https://github.com/upx/upx.":
            "Предсобранный двоичный файл Windows дополнительно сжат UPX (the Ultimate Packer for eXecutables) от Markus Oberhumer, Laszlo Molnar и John Reiser. Большое спасибо авторам - см. https://upx.github.io и https://github.com/upx/upx.",
        "UPX is distributed under its own liberal license (based on the GPL, with a special exception covering the compressed executables it produces). Like PyInstaller it is a build-time tool only and is not needed when running from source.":
            "UPX распространяется по собственной либеральной лицензии (на основе GPL, со специальным исключением для создаваемых им сжатых исполняемых файлов). Как и PyInstaller, это инструмент только для сборки, не нужный при запуске из исходников.",
        "Setup & How to:":
            "Установка и инструкции:",
        "Check out the main setup & demo video available at: https://youtu.be/-gUxV4fM1yo  (and the full python install is covered in the old py-hdfm-gooey since ZX-Next-Unite is an evolution of it : https://youtu.be/FJG-Z0DCIjQ )":
            "Посмотрите основное видео по установке и демонстрации: https://youtu.be/-gUxV4fM1yo  (полная установка python разобрана в старом py-hdfm-gooey, ведь ZX-Next-Unite — его эволюция : https://youtu.be/FJG-Z0DCIjQ )",
        "NextSync Head Over Heels demo: https://www.youtube.com/watch?v=D3_WqTPvjOE":
            "Демо NextSync с Head Over Heels: https://www.youtube.com/watch?v=D3_WqTPvjOE",
        "NextSync Night Knight demo: https://www.youtube.com/watch?v=eN1eMIqMCm4":
            "Демо NextSync с Night Knight: https://www.youtube.com/watch?v=eN1eMIqMCm4",
        "Is a required external component developed by Matt Westcott that allows browsing the image.":
            "Это обязательный внешний компонент от Matt Westcott, позволяющий просматривать образ.",
        "You will need to install it to get this application up and fully running.":
            "Его нужно установить, чтобы приложение заработало полностью.",
        "If hdfmonkey is not present you will see an error message in the main log window as it is missing.":
            "Если hdfmonkey отсутствует, в главном окне журнала появится сообщение об ошибке о его отсутствии.",
        "If that is the case you will see a 'Download and install HDF Monkey' button bottom right,":
            "В этом случае справа внизу появится кнопка 'Скачать и установить HDF Monkey';",
        "once clicked it will download a pre-compiled hdfmonkey build for your platform (Windows/Linux/macOS) and install it under the app's downloads folder.":
            "по нажатию она скачает готовую сборку hdfmonkey для вашей платформы (Windows/Linux/macOS) и установит её в папку downloads приложения.",
        "If the above automated install is successful, you should then be able to select an image and navigate it.":
            "Если автоматическая установка прошла успешно, вы сможете выбрать образ и перемещаться по нему.",
        "hdfmonkey can also be installed manually based on the instructions for your platform that can be found at: https://github.com/gasman/hdfmonkey":
            "hdfmonkey можно также установить вручную по инструкциям для вашей платформы: https://github.com/gasman/hdfmonkey",
        "zx-next-unite implements the <Server> side code and protocol of NextSync by Jari Komppa.":
            "zx-next-unite реализует код и протокол стороны <сервера> NextSync от Jari Komppa.",
        "It does not require any dot .sync modification and it uses the same very close python logic as nextsync.py.":
            "Он не требует изменений dot-команды .sync и использует ту же, очень близкую, python-логику, что и nextsync.py.",
        "Initial release on specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8":
            "Первый анонс на specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8",
        "As a result you will need to run the same dot .sync command on your Next as with the console version and the same network protocol.":
            "Поэтому на вашем Next нужно запускать ту же dot-команду .sync, что и с консольной версией, и с тем же сетевым протоколом.",
        "The latest release v1.2 of the .sync command can be found here https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 .":
            "Последний выпуск v1.2 команды .sync можно найти здесь https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 .",
        "You may follow the same instructions as provided in the readme.txt of that release.":
            "Можете следовать тем же инструкциям из readme.txt этого выпуска.",
        "On your Spectrum Next, clone or image copy the SYNC command that is located in the above release zip file into your Next dot folder.":
            "На вашем Spectrum Next склонируйте или скопируйте в образ команду SYNC из zip-архива этого выпуска в папку dot вашего Next.",
        "Navigate to the NextSync tab, select the root folder to sync on the left.":
            "Перейдите на вкладку NextSync и слева выберите корневую папку для синхронизации.",
        "Once you have selected the folder hit the 'Prepare Classic NextSync server' button, check the NextSync log window on the right.":
            "Выбрав папку, нажмите кнопку 'Подготовить классический сервер NextSync' и посмотрите окно журнала NextSync справа.",
        "The first time you run .sync on your Next you will be prompted to select the <server> IP address, this machine running NextSync.":
            "При первом запуске .sync на вашем Next вас попросят выбрать IP-адрес <сервера> — этой машины с NextSync.",
        "From the log window pick the IP address from this machine you want to use and type it on your Next.":
            "В окне журнала выберите нужный IP-адрес этой машины и введите его на вашем Next.",
        "Then start the sync server on this machine using the 'Start Classic NextSync server' button and then run the .sync command on your Next.":
            "Затем запустите сервер синхронизации на этой машине кнопкой 'Запустить классический сервер NextSync' и выполните команду .sync на вашем Next.",
        "At this point your Spectrum Next will connect to your machine using a network socket and the files will be sent to your Next.":
            "В этот момент ваш Spectrum Next подключится к машине через сетевой сокет, и файлы будут отправлены на ваш Next.",
        "As it is your Next that will connect to this machine check your firewall allows inbound calls to this machine on port: 2048 by default.":
            "Так как именно ваш Next подключается к этой машине, проверьте, что брандмауэр пропускает входящие подключения к этой машине на порт: по умолчанию 2048.",
        "The same syncignore.txt and syncpoint.dat file logic applies and allows you to control the sync (please check Jari's documentation).":
            "Действует та же логика файлов syncignore.txt и syncpoint.dat, позволяющая управлять синхронизацией (см. документацию Jari).",
        "NextSync source code can be found here: https://github.com/jarikomppa/specnext/tree/master/sync":
            "Исходный код NextSync находится здесь: https://github.com/jarikomppa/specnext/tree/master/sync",
        "If you run into any type of issue using the NextSync integration please run first Jari's command line version to see if it works as expected.":
            "Если с интеграцией NextSync возникают любые проблемы, сначала запустите консольную версию Jari и убедитесь, что она работает как ожидается.",
        "OpenAL sound engine (on Windows)":
            "Звуковой движок OpenAL (в Windows)",
        "The OpenAL library is required on Windows for CSpect to play sound, you may download it here: https://openal.org/":
            "Библиотека OpenAL нужна в Windows, чтобы CSpect воспроизводил звук; скачать можно здесь: https://openal.org/",
        "Mono (on Linux & MacOS Only)":
            "Mono (только Linux и MacOS)",
        "You will also need to manually install the mono-complete package for example using: sudo apt-get install mono-complete":
            "Также нужно вручную установить пакет mono-complete, например командой: sudo apt-get install mono-complete",
        "Third-Party Content Sources (GetIt / ZXDB / zxArt):":
            "Сторонние источники контента (GetIt / ZXDB / zxArt):",
        "zx-next-unite integrates three external databases to let you browse and download":
            "zx-next-unite объединяет три внешние базы данных, чтобы просматривать и скачивать",
        "Spectrum-related software and artwork directly from within the application.":
            "софт и графику, связанные со Spectrum, прямо из приложения.",
        "The application consumes their public APIs — it does not host, mirror, or":
            "Приложение обращается к их публичным API — оно само не хранит, не зеркалирует и",
        "redistribute any of the files itself.":
            "не распространяет никакие файлы.",
        "  GetIt is a community-maintained archive of ZX Spectrum Next software.":
            "  GetIt — поддерживаемый сообществом архив программ для ZX Spectrum Next.",
        "  The application queries the GetIt API to list and search files, then":
            "  Приложение запрашивает у API GetIt список и поиск файлов, затем",
        "  downloads them directly from the URLs returned by that API.":
            "  скачивает их напрямую по URL, возвращённым этим API.",
        "  ZXDB is an open-source database of ZX Spectrum and related software,":
            "  ZXDB — база данных с открытым кодом о программах ZX Spectrum и родственных,",
        "  maintained by the community at https://github.com/zxdb/ZXDB .":
            "  поддерживаемая сообществом на https://github.com/zxdb/ZXDB .",
        "  The application queries the ZXDB REST API for titles, releases, screenshots":
            "  Приложение запрашивает у REST API ZXDB названия, издания, снимки экрана",
        "  and inlays, then downloads files directly from the URLs returned by that API.":
            "  и обложки, затем скачивает файлы напрямую по URL, возвращённым этим API.",
        "  zxArt (https://zxart.ee) is a gallery and archive dedicated to ZX Spectrum":
            "  zxArt (https://zxart.ee) — галерея и архив, посвящённые визуальному искусству,",
        "  visual art, music, and productions.":
            "  музыке и продукциям ZX Spectrum.",
        "  The application sends requests to the zxArt API to search productions and":
            "  Приложение шлёт запросы к API zxArt для поиска продукций и",
        "  pictures, retrieve metadata and preview images, and download productions":
            "  картинок, получения метаданных и превью, а также скачивания продукций",
        "  directly from the URLs returned by that API.":
            "  напрямую по URL, возвращённым этим API.",
        "  Mame emulator brought to you by Holub for the ZX Spectrum Next can be installed following this documentation: https://wiki.specnext.dev/MAME:Installing":
            "  Эмулятор Mame, принесённый на ZX Spectrum Next Holub, устанавливается по этой документации: https://wiki.specnext.dev/MAME:Installing",
        "  Official Windows Binary Packages can be found here: https://www.mamedev.org/release.html":
            "  Официальные двоичные пакеты для Windows находятся здесь: https://www.mamedev.org/release.html",
        "  Put the file tbblue.zip that can be found here: https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip into MAME's roms folder.":
            "  Положите файл tbblue.zip, который можно взять здесь: https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip в папку roms MAME.",
        "  Important note: Don't extract the tbblue.zip file; MAME will look for the zip file when the 'tbblue' machine is selected.":
            "  Важное замечание: не распаковывайте файл tbblue.zip; MAME ищет именно zip, когда выбрана машина 'tbblue'.",
        "  Mike Dailly's CSpect is a downloadable emulator for Windows, macOS, and Linux":
            "  CSpect от Mike Dailly — скачиваемый эмулятор для Windows, macOS и Linux",
        "  Sites and links:":
            "  Сайты и ссылки:",
        "Legal disclaimer:":
            "Правовая оговорка:",
        "  The author of zx-next-unite does NOT distribute any files, ROMs, games,":
            "  Автор zx-next-unite НЕ распространяет никакие файлы, ROM'ы, игры,",
        "  demos, graphics, music, or any other content obtained through these APIs.":
            "  демо, графику, музыку и любой другой контент, получаемый через эти API.",
        "  All content is served exclusively by the respective third-party services.":
            "  Весь контент отдают исключительно соответствующие сторонние сервисы.",
        "  This application and author do not control third-party content.":
            "  Это приложение и его автор не контролируют сторонний контент.",
        "  It is the sole responsibility of the end user to ensure that any content":
            "  Исключительно конечный пользователь отвечает за то, чтобы любой контент,",
        "  they download or use through this application complies with the applicable":
            "  скачанный или используемый через это приложение, соответствовал применимым",
        "  copyright, licensing, and legal requirements in their jurisdiction.":
            "  требованиям авторского права, лицензий и законодательства его юрисдикции.",
        "  If in doubt, consult the terms of service of the relevant platform and":
            "  В случае сомнений изучите условия обслуживания соответствующей платформы и",
        "  seek appropriate legal advice before downloading or using any content.":
            "  при необходимости получите юридическую консультацию до скачивания или использования контента.",
        "  For inquiries you may reach out to me on my github page: https://github.com/jclauzel/ZX-Next-Unite":
            "  По вопросам можно написать мне на моей странице github: https://github.com/jclauzel/ZX-Next-Unite",
        "Enjoy!":
            "Приятного пользования!",
        # ---- OpenAL guided install (CSpect sound on Windows) ----
        "OpenAL 1.1 detected — CSpect sound is ready.":
            "OpenAL 1.1 обнаружен — звук CSpect готов.",
        "Install OpenAL?":
            "Установить OpenAL?",
        ("On Windows CSpect needs the <b>OpenAL 1.1</b> audio library "
         "for sound, and it was not detected on this machine — without "
         "it CSpect runs silent.<br><br>"
         "OpenAL is separate, third-party software — many thanks to its "
         "authors: {url}<br><br>"
         "Download the official installer (oalinst.exe) from openal.org "
         "and run it now?<br><br>"
         "Windows will ask for administrator approval when the installer "
         "starts — the app itself never runs elevated."):
            ("В Windows для звука CSpect нужна аудиобиблиотека "
             "<b>OpenAL 1.1</b>, а на этой машине она не обнаружена — без "
             "неё CSpect работает беззвучно.<br><br>"
             "OpenAL — отдельное стороннее программное обеспечение; большое "
             "спасибо его авторам: {url}<br><br>"
             "Скачать официальный установщик (oalinst.exe) с openal.org и "
             "запустить его сейчас?<br><br>"
             "Windows запросит подтверждение администратора при запуске "
             "установщика — само приложение никогда не работает с "
             "повышенными правами."),
        "Download and run the OpenAL installer":
            "Скачать и запустить установщик OpenAL",
        "Open openal.org":
            "Открыть openal.org",
        "The OpenAL download failed — see the log for details. You can install it manually from {url}":
            "Не удалось скачать OpenAL — подробности в журнале. Можно установить его вручную с {url}",
    },
    "cs": {
        # ---- 9.6.0: per-emulator button colour ----
        "Set the {emulator} color…":
            "Nastavit barvu {emulator}…",
        "Reset the {emulator} color":
            "Obnovit výchozí barvu {emulator}",
        # ---- 9.6.0: forgetting a remembered SD image path ----
        "Remove \"{path}\" from the list":
            "Odebrat \"{path}\" ze seznamu",
        "Clear the whole list":
            "Vymazat celý seznam",
        "Removed {path} from the image list — the image file itself was not deleted.":
            "Odebráno {path} ze seznamu obrazů — samotný soubor obrazu nebyl smazán.",
        "Cleared the image list — no image files were deleted.":
            "Seznam obrazů vymazán — žádné soubory obrazů nebyly smazány.",
        "Clear the image list?":
            "Vymazat seznam obrazů?",
        "Forget all {count} remembered image paths? The image files themselves are not deleted.":
            "Zapomenout všech {count} zapamatovaných cest k obrazům? Samotné soubory obrazů se nemažou.",
        "Remove the image path shown on the left from the list.\nThe image file itself is not deleted.":
            "Odebrat ze seznamu cestu k obrazu zobrazenou vlevo.\nSamotný soubor obrazu se nesmaže.",
        "Path to the SD card image (.img / .hdf).\nType a path directly, click the arrow to pick from recently loaded images,\nor use the 'Select NextZXOS disk Image' button to browse.\nRight-click the box for list options, or press Delete on a dropdown entry to forget it.":
            "Cesta k obrazu karty SD (.img / .hdf).\nZadejte cestu přímo, kliknutím na šipku vyberte z nedávno načtených obrazů,\nnebo procházejte tlačítkem 'Vybrat obraz disku NextZXOS'.\nPravým tlačítkem na pole zobrazíte volby seznamu, klávesa Delete na položce v nabídce ji zapomene.",
        # ---- GetIt starter pack ----
        'Assembling the starter pack…': 'Sestavování startovního balíčku…',
        'Downloading {title} ({idx}/{total})…': 'Stahování {title} ({idx}/{total})…',
        ('Everything on GetIt is freely distributable; the files are written to {dir} '
         'inside the loaded image.'):
            ('Vše na GetIt je volně šiřitelné; soubory se zapisují do {dir} uvnitř '
             'načteného obrazu.'),
        'Failed: {names}': 'Selhalo: {names}',
        'Fetching the GetIt catalogue…': 'Načítání katalogu GetIt…',
        ('Fill the loaded SD image with a hand-picked selection of modern homebrew '
         'from the GetIt catalogue — everything on GetIt is freely distributable.'):
            ('Naplní načtený obraz SD ručně vybraným moderním homebrew z katalogu '
             'GetIt — vše na GetIt je volně šiřitelné.'),
        'Fill your SD image with {count} hand-picked homebrew titles from the GetIt catalogue?': 'Naplnit obraz SD {count} ručně vybranými homebrew tituly z katalogu GetIt?',
        'Load a disk image first (SD Card tab), then try again.': 'Nejprve načtěte obraz disku (karta SD Card) a zkuste to znovu.',
        'No network connection.': 'Žádné síťové připojení.',
        'Not in the catalogue right now: {names}': 'Momentálně nejsou v katalogu: {names}',
        'Starter pack': 'Startovní balíček',
        'Starter pack cancelled — {done} title(s) were installed.': 'Startovní balíček zrušen — nainstalováno titulů: {done}.',
        'Starter pack complete: {done} of {total} titles installed to {dir}.': 'Startovní balíček hotov: nainstalováno {done} z {total} titulů do {dir}.',
        'Starter pack failed: {error}': 'Startovní balíček selhal: {error}',
        '🎁 Starter pack': '🎁 Startovní balíček',
        # ---- context menus & runtime dialogs ----
        '"{name}" already exists in this folder.': '"{name}" v této složce už existuje.',
        'Add drive {letter}: to the list?': 'Přidat jednotku {letter}: do seznamu?',
        'Add Next drive': 'Přidat jednotku Nextu',
        'Confirm deletion': 'Potvrdit smazání',
        'Copy': 'Kopírovat',
        'Copy all text': 'Kopírovat celý text',
        'Copy path to clipboard': 'Kopírovat cestu do schránky',
        'Copy text to clipboard': 'Kopírovat text do schránky',
        'Could not create {name}:': 'Nelze vytvořit {name}:',
        'Could not create:': 'Nelze vytvořit:',
        'Could not delete:': 'Nelze smazat:',
        'Could not extract {name}:': 'Nelze rozbalit {name}:',
        'Could not rename:': 'Nelze přejmenovat:',
        'Create directory failed': 'Vytvoření adresáře selhalo',
        'Create new directory': 'Vytvořit nový adresář',
        'Create new directory…': 'Vytvořit nový adresář…',
        'Cut': 'Vyjmout',
        'Decrease font size': 'Zmenšit velikost písma',
        'Delete failed': 'Smazání selhalo',
        'Delete from the local disk?': 'Smazat z místního disku?',
        'Delete on the Next? Folders are deleted with everything inside them.': 'Smazat na Nextu? Složky se mažou s celým obsahem.',
        'Delete the file "{name}"?': 'Smazat soubor "{name}"?',
        'Delete the folder "{name}" and all of its contents?': 'Smazat složku "{name}" a veškerý její obsah?',
        'Delete these {count} items? Folders are deleted with all of their contents.': 'Smazat těchto {count} položek? Složky se mažou s celým obsahem.',
        'Deleted files are sent to the Recycle Bin.': 'Smazané soubory putují do koše.',
        'Download (:<-)': 'Stáhnout (:<-)',
        'Download content': 'Stáhnout obsah',
        'Download Failed': 'Stahování selhalo',
        'Drive letter of the additional SD reader/partition (D..P):': 'Písmeno jednotky další čtečky SD/oddílu (D..P):',
        'Extraction Failed': 'Rozbalení selhalo',
        'Failed to download the NextZXOS image:': 'Nepodařilo se stáhnout obraz NextZXOS:',
        'Fetch issue info for this magazine': 'Načíst informace o číslech tohoto časopisu',
        'Fetch single magazine by name': 'Načíst časopis podle názvu',
        'file': 'soubor',
        'Files:  {files}\nFolders:  {folders}\nTotal size:  {size} bytes  ({pretty})': 'Soubory:  {files}\nSložky:  {folders}\nCelková velikost:  {size} bajtů  ({pretty})',
        'folder': 'složka',
        'Get size': 'Zjistit velikost',
        'Image not writable': 'Obraz není zapisovatelný',
        'Increase font size (now {px}px)': 'Zvětšit velikost písma (nyní {px}px)',
        'More like this': 'Další podobné',
        'New directory name in': 'Název nového adresáře v',
        'Name this Next': 'Pojmenovat tento Next',
        'Friendly name for {addr} (empty removes it):':
            'Přátelský název pro {addr} (prázdný jej odstraní):',
        'New folder in {path}:': 'Nová složka v {path}:',
        'New Folder…': 'Nová složka…',
        'New name for the {kind}:': 'Nový název ({kind}):',
        'Not enough space on the Next': 'Na Nextu není dost místa',
        ('Only add a drive that really exists on your Next (an extra SD card reader or '
         'partition). Selecting a drive that is not mounted CRASHES the Next.'):
            ('Přidávejte jen jednotku, která na vašem Nextu opravdu existuje (další '
             'čtečka SD nebo oddíl). Výběr nepřipojené jednotky Next SHODÍ.'),
        'Open': 'Otevřít',
        'Open in {source}': 'Otevřít v {source}',
        'Open: the system could not open {name}.':
            'Otevřít: systém nemohl otevřít {name}.',
        'Open on website (zxart.ee)': 'Otevřít na webu (zxart.ee)',
        'Open on website (zxinfo.dk)': 'Otevřít na webu (zxinfo.dk)',
        'Paste': 'Vložit',
        'Remote Unzip file': 'Rozbalit soubor vzdáleně',
        'Remote Zip': 'Zabalit vzdáleně',
        'Remove from Favorites': 'Odebrat z oblíbených',
        "Rename '{name}' to:": "Přejmenovat '{name}' na:",
        'Rename failed': 'Přejmenování selhalo',
        'Rename…': 'Přejmenovat…',
        'Retrieve all issues': 'Načíst všechna čísla',
        'Send to SD card (image)  →  {dest}': 'Odeslat na SD kartu (obraz)  →  {dest}',
        'Send using NextSync  →  {dest}': 'Odeslat přes NextSync  →  {dest}',
        'Set sync root': 'Nastavit kořen synchronizace',
        'Set this folder as the new sync root?': 'Nastavit tuto složku jako nový kořen synchronizace?',
        'Size on the Next': 'Velikost na Nextu',
        'The image was downloaded but could not be extracted:': 'Obraz byl stažen, ale nepodařilo se jej rozbalit:',
        "The name cannot contain '/' or '\\'.": "Název nesmí obsahovat '/' ani '\\'.",
        'This cannot be undone.': 'Tuto akci nelze vrátit zpět.',
        ('This copy needs {need} bytes ({need_h}), but drive {drive}: only has {free} '
         'bytes ({free_h}) free.\n\nIt exceeds the available remote space by {over} '
         'bytes ({over_h}).\n\nThe copy was not started.'):
            ('Kopie potřebuje {need} bajtů ({need_h}), ale jednotka {drive}: má '
             'volných jen {free} bajtů ({free_h}).\n\nPřekračuje dostupné vzdálené '
             'místo o {over} bajtů ({over_h}).\n\nKopírování nebylo zahájeno.'),
        'Unzip file': 'Rozbalit soubor',
        'Zip': 'Zabalit',
        '… and {n} more': '… a dalších {n}',
        # ---- labels ----
        "  Background": "  Pozadí",
        "  Directory name": "  Název adresáře",
        "  Directory type label": "  Popisek typu adresáře",
        "  File extension": "  Přípona souboru",
        "  File name": "  Název souboru",
        "  File size": "  Velikost souboru",
        "  Filter: ": "  Filtr: ",
        "  General UI text": "  Obecný text rozhraní",
        "  Retro logs console": "  Retro konzole protokolu",
        "  Up Directory item": "  Položka «o adresář výš»",
        "Background image opacity (%):": "Neprůhlednost pozadí (%):",
        "Background image:": "Obrázek na pozadí:",
        "CSpect default launch parameters:": "Parametry spouštění CSpect:",
        "Collection:": "Kolekce:",
        "Desktop Theme:": "Motiv prostředí:",
        "Disk Image Explorer: ": "Průzkumník obrazu disku: ",
        "Gallery animation:": "Animace galerie:",
        "Gallery image size:": "Velikost obrázků v galerii:",
        "Gallery items per row:": "Položek na řádek galerie:",
        "Gallery rows per page (min):": "Řádků na stránku galerie (min):",
        "Gallery search sort ordering preference:": "Řazení výsledků hledání v galerii:",
        "Gallery slideshow pause time:": "Prodleva prezentace:",
        "Language:": "Jazyk:",
        "Application language:": "Jazyk aplikace:",
        "Local file explorers & App Text Colors:": "Místní průzkumníky a barvy textu:",
        "Local path: ": "Místní cesta: ",
        "MAME ROM / system:": "ROM / systém MAME:",
        "MAME default launch parameters:": "Parametry spouštění MAME:",
        "Max connections:": "Max. připojení:",
        "NextSync — Automatically start Remote Explorer server on startup":
            "NextSync — Automaticky spouštět server Remote Exploreru při startu",
        "NextSync — when a sent file or directory exists locally:":
            "NextSync — když přijatý soubor či adresář už místně existuje:",
        "Page:": "Stránka:",
        "Port:": "Port:",
        "Reset theme": "Obnovit motiv",
        "Retro log font size:": "Velikost písma retro protokolu:",
        "Search:": "Hledat:",
        "Search: ": "Hledat: ",
        "View:": "Zobrazení:",
        "itch.io API key:": "Klíč API itch.io:",
        # ---- buttons ----
        "< Prev": "< Zpět",
        "Cancel": "Zrušit",
        "Cancel sync": "Zrušit synchronizaci",
        "Connect": "Připojit",
        "Create Directory": "Vytvořit adresář",
        "Create SyncIgnore File": "Vytvořit soubor SyncIgnore",
        "Delete": "Smazat",
        "Delete SyncIgnore File": "Smazat soubor SyncIgnore",
        "Delete SyncPoint File": "Smazat soubor SyncPoint",
        "Disconnect": "Odpojit",
        "RS232 ESP emulation is already running on port {port} for another emulator; this MAME joins it. The new port applies once every MAME has exited.":
            "Emulace RS232 ESP už běží na portu {port} pro jiný emulátor; tento MAME se k ní připojuje. Nový port se použije, až se ukončí všechny MAME.",
        "RS232 ESP emulation: {count} emulators are now sharing it (port {port}).":
            "Emulace RS232 ESP: sdílí ji nyní {count} emulátorů (port {port}).",
        "RS232 ESP emulation could not start (port {port} in use?). MAME starts without it.":
            "Emulaci RS232 ESP se nepodařilo spustit (port {port} obsazen?). MAME se spouští bez ní.",
        "RS232 ESP emulation inspired by jesperl - by Janko Stamenović":
            "Emulace RS232 ESP inspirovaná projektem jesperl - od Janko Stamenoviće",
        "The optional RS232 ESP Emulation for MAME (Settings) is a clean full reimplementation in Python of an idea from jesperl by Janko Stamenović - an ESP-AT emulator bridging MAME's emulated Wi-Fi module to the real network. Many thanks for the inspirational idea - see https://sourceforge.net/projects/jesperl/.":
            "Volitelná emulace RS232 ESP pro MAME (Nastavení) je čistá úplná reimplementace nápadu z projektu jesperl Janko Stamenoviće v Pythonu - emulátoru ESP-AT propojujícího emulovaný Wi-Fi modul MAME se skutečnou sítí. Velké díky za inspirativní nápad - viz https://sourceforge.net/projects/jesperl/.",
        "One RS232 ESP emulation serves every running MAME: launch a second MAME on another disk image and it joins the same emulation with its own separate session, so several emulated Nexts can be on the network at once. The emulation stops when the last MAME exits. When two of them ask for the same server port (a Next listening for incoming connections), the second one is moved to the next free port and the log says which port to connect to.":
            "Jedna emulace RS232 ESP obsluhuje všechny běžící MAME: spusťte druhý MAME s jiným obrazem disku a připojí se ke stejné emulaci s vlastní samostatnou relací, takže několik emulovaných Nextů může být v síti současně. Emulace se zastaví, když skončí poslední MAME. Když si dva z nich vyžádají stejný serverový port (Next naslouchající příchozím spojením), druhý se přesune na další volný port a protokol uvede, ke kterému portu se připojit.",
        "Transfers through the RS232 ESP emulation need the Next side on its SLOW pacing: use '.sync5 -s' for the dot, or set UART speed to Slow in ZX Next Remote's settings.":
            "Přenosy přes emulaci RS232 ESP vyžadují POMALÉ tempo na straně Next: použijte '.sync5 -s' pro dot, nebo nastavte rychlost UART na Slow v nastavení ZX Next Remote.",
        "Start {emulator}": "Spustit {emulator}",
        "Color:": "Barva:",
        "Pick a color for this Next. It tints the machine list and this machine's tab in the session strip.":
            "Vyberte barvu pro tento Next. Obarví seznam strojů a záložku tohoto stroje v pruhu relací.",
        "Clear the color": "Odebrat barvu",
        "Switch to this Next": "Přepnout na tento Next",
        "Name and color…": "Název a barva…",
        "That Next is no longer on the line.":
            "Tento Next už není na lince.",
        "Tell this Next to leave listen mode and exit? ZX Next Remote closes its application; a '.sync5' dot returns to BASIC. The server keeps listening, so it can connect again.":
            "Požádat tento Next, aby opustil režim naslouchání a skončil? ZX Next Remote zavře svou aplikaci; tečka '.sync5' se vrátí do BASICu. Server dál naslouchá, takže se lze připojit znovu.",
        "Asked the Next to leave listen mode and exit.":
            "Next byl požádán, aby opustil režim naslouchání a skončil.",
        "Remote .sync5 update": "Vzdálená aktualizace .sync5",
        "Update .sync5 on this Next ({old} → {new})…":
            "Aktualizovat .sync5 na tomto Nextu ({old} → {new})…",
        "Push new .sync5 to this Next…":
            "Odeslat nový .sync5 na tento Next…",
        ".sync5 version unknown — switch to this Next first":
            "Verze .sync5 není známa — nejdřív se přepněte na tento Next",
        ".sync5 v{old} predates self-update — copy the new dot to the Next by hand once":
            ".sync5 v{old} je starší než samoaktualizace — zkopírujte nový dot na Next jednou ručně",
        "Locating the .sync5 build to send…":
            "Hledání souboru .sync5 k odeslání…",
        "Still locating the .sync5 build to send — one moment.":
            "Soubor .sync5 k odeslání se stále hledá — okamžik.",
        "Could not obtain the .sync5 build to send: {reason}":
            "Nepodařilo se získat soubor .sync5 k odeslání: {reason}",
        ("Update .sync5 on {machine}: v{old} → v{new}.\n\nThe new dot is "
         "staged on the Next's SD card, read back and verified, then "
         "swapped in; the previous dot is kept as sync5.bak (renaming it "
         "back to sync5 is the one-step recovery). The session ends when "
         "the update completes — run {command} on the Next again "
         "afterwards.\n\nTarget directory on the Next:"):
            ("Aktualizace .sync5 na {machine}: v{old} → v{new}.\n\nNový "
             "dot příkaz se nahraje na SD kartu Nextu, načte zpět a "
             "ověří, a poté vymění; předchozí dot příkaz zůstane uložen "
             "jako sync5.bak (jeho přejmenování zpět na sync5 je obnova "
             "jedním krokem). Relace skončí po dokončení aktualizace — "
             "poté znovu spusťte {command} na Nextu.\n\nCílový adresář "
             "na Nextu:"),
        ("Push the new .sync5 (v{new}) to {machine}?\n\nThis machine's "
         "version is unknown (an older dot, or an old ZX Next Remote "
         "build — the two cannot be told apart), and the swap itself "
         "only works when the far side is a .sync dot v5.9 or newer: on "
         "anything older the staged sync5.new is left on the card and "
         "nothing is swapped. The previous dot is kept as sync5.bak "
         "(renaming it back to sync5 is the one-step recovery). The "
         "session ends when the update completes — run {command} on the "
         "Next again afterwards.\n\nTarget directory on the Next:"):
            ("Odeslat nový .sync5 (v{new}) na {machine}?\n\nVerze tohoto "
             "stroje není známa (starý dot příkaz, nebo stará verze ZX "
             "Next Remote — nelze je rozlišit), a samotná výměna funguje "
             "jen tehdy, když je na druhé straně dot příkaz .sync verze "
             "v5.9 nebo novější: u čehokoli staršího zůstane připravený "
             "sync5.new na kartě a nic se nevymění. Předchozí dot příkaz "
             "zůstane uložen jako sync5.bak (jeho přejmenování zpět na "
             "sync5 je obnova jedním krokem). Relace skončí po dokončení "
             "aktualizace — poté znovu spusťte {command} na "
             "Nextu.\n\nCílový adresář na Nextu:"),
        "Download File": "Stáhnout soubor",
        "Download NextZXOS Image": "Stáhnout obraz NextZXOS",
        "Download and install HDF Monkey": "Stáhnout a nainstalovat HDF Monkey",
        "Download and install HDF Monkey and OpenAL": "Stáhnout a nainstalovat HDF Monkey a OpenAL",
        "Generate": "Vygenerovat",
        "Get API key…": "Získat klíč API…",
        "Latest": "Novinky",
        "New Folder": "Nová složka",
        "Next >": "Další >",
        "OK": "OK",
        "Open config file": "Otevřít konfigurační soubor",
        "Prepare Classic NextSync server": "Připravit klasický server NextSync",
        "Random": "Náhodně",
        "Refresh": "Obnovit",
        "Rename": "Přejmenovat",
        "Search": "Hledat",
        "Select NextZXOS disk Image": "Vybrat obraz disku NextZXOS",
        "Set current folder as new sync root folder":
            "Nastavit aktuální složku jako nový kořen synchronizace",
        "Up": "Nahoru",
        "▶ Start Classic NextSync server": "▶ Spustit klasický server NextSync",
        "▶ Start Remote Explorer NextSync server":
            "▶ Spustit server NextSync vzdáleného průzkumníka",
        "⬇  Download": "⬇  Stáhnout",
        "⬇  Install MAME": "⬇  Nainstalovat MAME",
        "🎮 Retro": "🎮 Retro",
        "💾  Send to SD card": "💾  Odeslat na kartu SD",
        "🔁  Send via NextSync": "🔁  Odeslat přes NextSync",
        "🕹  Launch CSpect": "🕹  Spustit CSpect",
        "🕹  Launch Mame": "🕹  Spustit Mame",
        "🖼 Switch to 'Classic' view mode": "🖼 Přepnout na zobrazení 'Klasické'",
        # ---- emulator option combos (SD Card tab; selected by index) ----
        "Screen Size X1": "Velikost obrazu X1",
        "Screen Size X2": "Velikost obrazu X2",
        "Screen Size X3": "Velikost obrazu X3",
        "Screen Size X4": "Velikost obrazu X4",
        "Fullscreen": "Celá obrazovka",
        "Sound On": "Zvuk zapnut",
        "Sound Off": "Zvuk vypnut",
        "Sound WASAPI": "Zvuk WASAPI",
        "Sound XAudio2": "Zvuk XAudio2",
        "Sound PortAudio": "Zvuk PortAudio",
        "VSync On": "VSync zapnut",
        "VSync Off": "VSync vypnut",
        "Joystick On": "Joystick zapnut",
        "Joystick Off": "Joystick vypnut",
        "Mouse On": "Myš zapnuta",
        "Mouse Off": "Myš vypnuta",
        "Disable ESC Key Off": "Blokovat klávesu ESC: ne",
        "Disable ESC Key On": "Blokovat klávesu ESC: ano",
        "ZX Next Unite update check: could not parse the versions (latest tag {tag}); skipping.":
            "Kontrola ZX Next Unite: nepodařilo se rozpoznat verze (nejnovější značka {tag}) — přeskakuje se.",
        "ZX Next Unite {latest} is available, but the release has no package for this platform — opening the releases page instead.":
            "Je dostupný ZX Next Unite {latest}, ale vydání neobsahuje balíček pro tuto platformu — otevře se stránka vydání.",
        # ---- long guidance prompts (final) ----
        "A newer version of CSpect is available on itch.io.\n\nInstalled: {installed}\nLatest: {latest}\n\nDownload and install the newest version now?":
            "Na itch.io je dostupná novější verze CSpectu.\n\nNainstalovaná: {installed}\nNejnovější: {latest}\n\nStáhnout a nainstalovat nejnovější verzi nyní?",
        "CSpect update ▸ SUCCESS — {name} extracted to: {path}":
            "Aktualizace CSpectu ▸ ÚSPĚCH — {name} rozbaleno do: {path}",
        "CSpect update ▸ Starting download + install of {name} ({file}) from itch.io into {folder}.":
            "Aktualizace CSpectu ▸ zahájení stažení a instalace {name} ({file}) z itch.io do {folder}.",
        "ERROR: could not build {name}: {error}":
            "CHYBA: nepodařilo se vytvořit {name}: {error}",
        "MAME can't start: the ZX Spectrum Next boot ROM (TBBLUE) is missing. This step is manual — see {url} and follow \"Get TBBLUE (the Next 'boot ROM')\". Put the file tbblue.zip into MAME's roms folder (downloads\\mame\\roms) — DON'T extract it — and try again. You must provide a legally acquired, licensed ROM.":
            "MAME nemůže nastartovat: chybí zaváděcí ROM ZX Spectrum Next (TBBLUE). Tento krok je ruční — viz {url} a postup \"Get TBBLUE (the Next 'boot ROM')\". Vložte soubor tbblue.zip do složky roms MAME (downloads\\mame\\roms) — NEROZBALUJTE jej — a zkuste to znovu. Musíte použít legálně pořízenou licencovanou ROM.",
        "MAME install ▸ NEXT STEP (manual): add the TBBLUE boot ROM. See {url} → \"Get TBBLUE (the Next 'boot ROM')\". Put the file tbblue.zip into MAME's roms folder ({roms}) — DON'T extract it. You must provide a legally acquired, licensed ROM.":
            "Instalace MAME ▸ DALŠÍ KROK (ruční): přidejte zaváděcí ROM TBBLUE. Viz {url} → \"Get TBBLUE (the Next 'boot ROM')\". Vložte soubor tbblue.zip do složky roms MAME ({roms}) — NEROZBALUJTE jej. Musíte použít legálně pořízenou licencovanou ROM.",
        "NextSync .sync5 dot command updated: v{old} -> v{new} — please copy the new build to your Next (it cannot be deployed automatically).":
            "Dot příkaz NextSync .sync5 aktualizován: v{old} -> v{new} — zkopírujte novou verzi na svůj Next (nelze ji nasadit automaticky).",
        "TIP: Did you know that if you have purchased CSpect from itch.io you can do a full end-to-end CSpect install from there?\n\nCSpect ships with hdfmonkey bundled inside it, so that route needs no separate hdfmonkey install — the app finds and uses the bundled copy automatically.\n\nSimply log into your itch.io account in the itch.io tab, navigate to CSpect and click Install.\n\nDo you still want to install hdfmonkey only, or abort and then make an end-to-end install of CSpect using itch.io?":
            "TIP: věděli jste, že pokud máte CSpect zakoupený na itch.io, můžete odtud provést kompletní instalaci CSpectu?\n\nCSpect má hdfmonkey přibalený uvnitř, takže tato cesta nevyžaduje samostatnou instalaci hdfmonkey — aplikace přibalenou kopii automaticky najde a použije.\n\nPřihlaste se ke svému účtu itch.io na kartě itch.io, přejděte na CSpect a klikněte na Instalovat.\n\nChcete přesto nainstalovat pouze hdfmonkey, nebo akci přerušit a provést kompletní instalaci CSpectu přes itch.io?",
        "The automatic hdfmonkey download from specnext.com failed — the forum may be asking for a login or an anti-robot confirmation before the download can start (see the log for details).\n\nYou can install it manually instead:\n1. Click 'Open download page' below (or browse to\n    {url} ).\n2. Download the hdfmonkey .zip file.\n3. Drop the downloaded .zip into this EXACT folder — the app has already created it, and the 'Open downloads folder' button below opens it so nothing needs to be typed:\n    {folder}\n4. Click \"I've dropped the zip - try again\".":
            "Automatické stažení hdfmonkey ze specnext.com selhalo — fórum může před zahájením stahování vyžadovat přihlášení nebo potvrzení proti robotům (podrobnosti v logu).\n\nMůžete jej nainstalovat ručně:\n1. Klikněte na 'Otevřít stránku stahování' níže (nebo otevřete\n    {url} ).\n2. Stáhněte soubor .zip s hdfmonkey.\n3. Vložte stažený .zip PŘESNĚ do této složky — aplikace ji už vytvořila a tlačítko 'Otevřít složku stahování' níže ji otevře, takže nic nemusíte psát:\n    {folder}\n4. Klikněte na \"Zip jsem vložil - zkusit znovu\".",
        "ZX Next Unite update: downloaded {name} to {folder}":
            "Aktualizace ZX Next Unite: {name} staženo do {folder}",
        "ZX Next Unite update: downloaded {path} but could not unpack it: {error}":
            "Aktualizace ZX Next Unite: {path} staženo, ale nepodařilo se rozbalit: {error}",
        "ZX Next Unite {latest} is available (you are running {installed}).\n\nYou appear to be running from source (git clone), so the\nrecommended way to update is:\n\n    git pull\n\ninstead of downloading the Windows binary.":
            "Je dostupný ZX Next Unite {latest} (používáte {installed}).\n\nZdá se, že program spouštíte ze zdrojů (git clone), takže\ndoporučený způsob aktualizace je:\n\n    git pull\n\nmísto stahování binárky pro Windows.",
        "ZX Next Unite {latest} is available — download?\n\nInstalled: {installed}\nLatest: {latest}\nPackage: {asset} (~{size})\n\nThe new version is saved next to the current one — you choose\nwhen to switch (you'll be offered a restart after the download).":
            "Je dostupný ZX Next Unite {latest} — stáhnout?\n\nNainstalovaná: {installed}\nNejnovější: {latest}\nBalíček: {asset} (~{size})\n\nNová verze se uloží vedle stávající — sami zvolíte,\nkdy přepnout (po stažení bude nabídnut restart).",
        "ZX Next Unite {latest} is available — running from source, so update with 'git pull' instead of the Windows binary.":
            "Je dostupný ZX Next Unite {latest} — běží ze zdrojů, takže aktualizujte příkazem 'git pull' místo stahování binárky pro Windows.",
        # ---- emulator update prompts (bodies + buttons) ----
        "A newer version of MAME is available.\n\nInstalled: 0.{installed}\nLatest: {latest}  (0.{latest_num})\nPackage: {asset}\n\nDownload (~{size}) and update your MAME install now?\nThe existing files in the downloads MAME folder will be overwritten.":
            "Je dostupná novější verze MAME.\n\nNainstalovaná: 0.{installed}\nNejnovější: {latest}  (0.{latest_num})\nBalíček: {asset}\n\nStáhnout (~{size}) a aktualizovat instalaci MAME nyní?\nStávající soubory ve složce MAME budou přepsány.",
        "Close and start {name}":
            "Zavřít a spustit {name}",
        "Continue hdfmonkey standalone install":
            "Pokračovat v samostatné instalaci hdfmonkey",
        "I've dropped the zip - try again":
            "Zip jsem vložil - zkusit znovu",
        "MAME release: {tag}\nPackage: {asset} ({arch})\n\nDownload (~{size}) and install it into the downloads folder?\nNote: the fully extracted install is large (~500 MB).":
            "Verze MAME: {tag}\nBalíček: {asset} ({arch})\n\nStáhnout (~{size}) a nainstalovat do složky stahování?\nPoznámka: plně rozbalená instalace je velká (~500 MB).",
        "Open download page":
            "Otevřít stránku stahování",
        "Open downloads folder":
            "Otevřít složku stahování",
        "The new version was saved as:\n\n{path}\n\nClose ZX Next Unite now and start the new version ({name})?\nYour settings (hdfg.cfg) and downloads are picked up as-is —\nboth versions run from the same folder.":
            "Nová verze byla uložena jako:\n\n{path}\n\nZavřít ZX Next Unite a spustit novou verzi ({name})?\nVaše nastavení (hdfg.cfg) a stažené soubory se použijí beze změny —\nobě verze běží ze stejné složky.",
        "What's changed:":
            "Co je nového:",
        # ---- emulator / config console (final batch) ----
        "CSpect update check: {reason}.":
            "Kontrola CSpectu: {reason}.",
        "CSpect update ▸ FAILED — {error}":
            "Aktualizace CSpectu ▸ SELHALA — {error}",
        "CSpect update ▸ newer build available: installed {installed}, latest {latest}.":
            "Aktualizace CSpectu ▸ je dostupné novější sestavení: nainstalované {installed}, nejnovější {latest}.",
        "CSpect update ▸ user chose to update to {name}.":
            "Aktualizace CSpectu ▸ uživatel zvolil aktualizaci na {name}.",
        "Could not list the MAME releases: {error}":
            "Nepodařilo se načíst seznam verzí MAME: {error}",
        "ERROR: Failed to launch MAME: {error}":
            "CHYBA: nepodařilo se spustit MAME: {error}",
        "ERROR: Failed to launch CSpect: {error}":
            "CHYBA: Nepodařilo se spustit CSpect: {error}",
        "ERROR: could not extract {name}: {error}":
            "CHYBA: nepodařilo se rozbalit {name}: {error}",
        "ERROR: hdfmonkey failed - A file can't be opened this is commonly caused by strange characters such as quotes and signs":
            "CHYBA: hdfmonkey selhal - soubor nelze otevřít; obvykle to způsobují neobvyklé znaky jako uvozovky a symboly",
        "ERROR: hdfmonkey failed - A file can't be opened: {command} this is commonly caused by strange characters such as quotes and signs":
            "CHYBA: hdfmonkey selhal - soubor nelze otevřít: {command}; obvykle to způsobují neobvyklé znaky jako uvozovky a symboly",
        "Failed to save configuration file with IOError: {error}":
            "Nepodařilo se uložit konfigurační soubor (IOError): {error}",
        "Found hdfmonkey alongside CSpect: {path}":
            "hdfmonkey nalezen vedle CSpectu: {path}",
        "MAME exited with code {code}.":
            "MAME skončil s kódem {code}.",
        "MAME install ▸ SUCCESS — MAME detected at: {path}":
            "Instalace MAME ▸ ÚSPĚCH — MAME nalezen v: {path}",
        "Pygame mode unavailable — run: pip install pygame-ce":
            "Režim pygame není dostupný — spusťte: pip install pygame-ce",
        "Remote unzip: fetching {path} from the image …":
            "Vzdálené rozbalení: načítá se {path} z obrazu …",
        "Remote zip: fetching {count} item(s) from the image …":
            "Vzdálené zabalení: načítá se {count} položek z obrazu …",
        "Saved configuration file.":
            "Konfigurační soubor uložen.",
        "UI language set to '{lang}' to match the system language — change it on the Settings tab.":
            "Jazyk rozhraní nastaven na '{lang}' podle jazyka systému — změnit jej lze na kartě Nastavení.",
        "ZX Next Unite update available: {latest} (installed {installed}).":
            "K dispozici je aktualizace ZX Next Unite: {latest} (nainstalovaná {installed}).",
        "ZX Next Unite update ▸ downloading {asset}…":
            "Aktualizace ZX Next Unite ▸ stahuje se {asset}…",
        "ZX Next Unite update: could not start {name}: {error}":
            "Aktualizace ZX Next Unite: nepodařilo se spustit {name}: {error}",
        "ZX Next Unite update: download FAILED: {error}":
            "Aktualizace ZX Next Unite: stahování SELHALO: {error}",
        "ZX Next Unite update: downloaded — start it any time: {path}":
            "Aktualizace ZX Next Unite: stažena — spustit ji můžete kdykoli: {path}",
        "ZX Next Unite update: starting {name} and closing…":
            "Aktualizace ZX Next Unite: spouští se {name} a zavírá se…",
        "ZX Next Unite update: unpacked to {path}":
            "Aktualizace ZX Next Unite: rozbaleno do {path}",
        "Extracting {name} from the image, then starting CSpect…":
            "Rozbaluje se {name} z obrazu, poté se spustí CSpect…",
        "Start CSpect: {name} could not be read from the image, CSpect was not started.":
            "Spustit CSpect: {name} se nepodařilo načíst z obrazu — CSpect nebyl spuštěn.",
        "Extracting {name} from the image, then sending it via NextSync…":
            "Rozbaluje se {name} z obrazu, poté se odešle přes NextSync…",
        "Send via NextSync: {name} could not be read from the image, nothing was sent.":
            "Odeslání přes NextSync: {name} se nepodařilo načíst z obrazu — nic nebylo odesláno.",
        "Send via NextSync {name}":
            "Odeslat {name} přes NextSync",
        "Start NextSync Remote Explorer":
            "Spustit Remote Explorer NextSync",
        "Stop NextSync Remote Explorer":
            "Zastavit Remote Explorer NextSync",
        "Please select a sync root first on the NextSync Remote Explorer tab and retry.":
            "Nejprve zvolte kořenovou složku synchronizace na kartě Remote Explorer NextSync a zkuste to znovu.",
        # ---- CSpect auto-start actions (SD Card tab menus) ----
        "Send to SD Card and start CSpect with file {name}":
            "Odeslat na SD kartu a spustit CSpect se souborem {name}",
        "Send to SD Card and start CSpect: the transfer failed, CSpect was not started.":
            "Odeslat na SD kartu a spustit CSpect: přenos selhal — CSpect nebyl spuštěn.",
        "Sending {name} to the SD card image, then starting CSpect…":
            "Odesílání {name} do obrazu SD karty, poté spuštění CSpectu…",
        "Start CSpect with file {name}":
            "Spustit CSpect se souborem {name}",
        "Start MAME with file {name}":
            "Spustit MAME se souborem {name}",
        "Could not start {emulator}":
            "Nepodařilo se spustit {emulator}",
        "Could not prepare a folder for {name}: {error}":
            "Nepodařilo se připravit složku pro {name}: {error}",
        "Start {emulator}: {name} could not be downloaded from the Next, "
        "{emulator} was not started.":
            "Spustit {emulator}: {name} se nepodařilo stáhnout z Nextu — "
            "{emulator} nebyl spuštěn.",
        "Downloading {name} from the Next, then starting {emulator}…":
            "Stahuje se {name} z Nextu, poté se spustí {emulator}…",
        "Downloading {name}…":
            "Stahuje se {name}…",
        "Start MAME: could not prepare the staging folder {path} ({error}).":
            "Spustit MAME: nepodařilo se připravit dočasnou složku {path} ({error}).",
        "Send to SD Card and start MAME with file {name}":
            "Odeslat na SD kartu a spustit MAME se souborem {name}",
        "Extracting {name} from the image, then starting MAME…":
            "Rozbaluje se {name} z obrazu, poté se spustí MAME…",
        "Start MAME: {name} could not be read from the image, MAME was not started.":
            "Spustit MAME: {name} se nepodařilo načíst z obrazu — MAME nebyl spuštěn.",
        "Send to SD Card and start MAME: the transfer failed, MAME was not started.":
            "Odeslat na SD kartu a spustit MAME: přenos selhal — MAME nebyl spuštěn.",
        "Sending {name} to the SD card image, then starting MAME…":
            "Odesílání {name} do obrazu SD karty, poté spuštění MAME…",
        "MAME cannot load {name} directly; starting MAME without it.":
            "MAME nemůže {name} načíst přímo — MAME bude spuštěn bez tohoto souboru.",
        # ---- dialogs (message boxes) ----
        "CSpect update available":
            "K dispozici je aktualizace CSpectu",
        "Choose another release…":
            "Zvolit jinou verzi…",
        "Close":
            "Zavřít",
        "Download and install":
            "Stáhnout a nainstalovat",
        "File or directory already exists locally.":
            "Soubor nebo složka již místně existuje.",
        "File or directory exists":
            "Soubor nebo složka již existuje",
        "Ignore (always in this sync)":
            "Ignorovat (vždy při této synchronizaci)",
        "Ignore (one time)":
            "Ignorovat (jednorázově)",
        "Install from .zip…":
            "Instalovat ze .zip…",
        "Install hdfmonkey":
            "Nainstalovat hdfmonkey",
        "Later":
            "Později",
        "MAME update available":
            "K dispozici je aktualizace MAME",
        "Open itch.io page":
            "Otevřít stránku itch.io",
        "Open the releases page":
            "Otevřít stránku vydání",
        "Overwrite local file (always in this sync)":
            "Přepsat místní soubor (vždy při této synchronizaci)",
        "Overwrite local file (one time)":
            "Přepsat místní soubor (jednorázově)",
        "The automated download failed.":
            "Automatické stažení selhalo.",
        "This is going to completely delete the files in {path} and its sub folders, so they will be unrecoverable.\n\nAre you sure want to continue?":
            "Tímto se zcela smažou soubory v {path} a jeho podsložkách, bez možnosti obnovy.\n\nOpravdu chcete pokračovat?",
        "Tip: set a default for this in Settings → \"NextSync — when a sent file or directory exists locally\".":
            "Tip: výchozí chování nastavíte v Nastavení → \"NextSync — when a sent file or directory exists locally\".",
        "Uninstall":
            "Odinstalovat",
        "Update":
            "Aktualizovat",
        "Update downloaded":
            "Aktualizace stažena",
        "Yes":
            "Ano",
        "You can download it manually from the itch.io page in your browser, then install it from the downloaded .zip.":
            "Můžete jej stáhnout ručně ze stránky itch.io v prohlížeči a poté nainstalovat ze staženého .zip.",
        "ZX Next Unite update available":
            "K dispozici je aktualizace ZX Next Unite",
        "hdfmonkey download failed":
            "Stažení hdfmonkey selhalo",
        "itch.io download":
            "Stahování z itch.io",
        # ---- emulator install / update console ----
        "CSpect update check skipped: {reason}":
            "Kontrola CSpectu přeskočena: {reason}",
        "CSpect update ▸ user cancelled the update.":
            "Aktualizace CSpectu ▸ uživatel aktualizaci zrušil.",
        "ERROR: CSpect.exe is not present in the same local directory as zx-next-unite. Please install it from http://cspect.org":
            "CHYBA: CSpect.exe není ve stejném adresáři jako zx-next-unite. Nainstalujte jej z http://cspect.org",
        "ERROR: MAME executable not found on PATH. Cannot launch MAME.":
            "CHYBA: spustitelný soubor MAME nebyl nalezen v PATH. MAME nelze spustit.",
        "Listing the available MAME releases…":
            "Načítá se seznam dostupných verzí MAME…",
        "MAME install ▸ FAILED — the download and extraction finished, but no mame.exe could be found in downloads/mame.":
            "Instalace MAME ▸ SELHALA — stažení i rozbalení proběhlo, ale mame.exe nebyl v downloads/mame nalezen.",
        "MAME install ▸ FAILED — {error}. You can download it manually from https://www.mamedev.org/release.html":
            "Instalace MAME ▸ SELHALA — {error}. Ručně jej lze stáhnout z https://www.mamedev.org/release.html",
        "MAME install ▸ Starting: {tag} ({asset}, ~{size}).":
            "Instalace MAME ▸ spouští se: {tag} ({asset}, ~{size}).",
        "MAME install ▸ release picker cancelled.":
            "Instalace MAME ▸ výběr verze zrušen.",
        "MAME is ready to launch now — no restart needed. Use the '🕹  Launch Mame' button.":
            "MAME je připraven ke spuštění — bez restartu. Použijte tlačítko '🕹  Launch Mame'.",
        "MAME update check: could not determine the installed MAME version; skipping.":
            "Kontrola MAME: nepodařilo se zjistit nainstalovanou verzi — přeskakuje se.",
        "MAME update check: could not determine the latest release; skipping.":
            "Kontrola MAME: nepodařilo se zjistit nejnovější verzi — přeskakuje se.",
        "MAME update check: could not reach the release site; skipping.":
            "Kontrola MAME: nepodařilo se spojit se stránkou vydání — přeskakuje se.",
        "MAME update ▸ user chose to pick a release manually.":
            "Aktualizace MAME ▸ uživatel zvolil ruční výběr verze.",
        "MAME update ▸ user chose to update to {tag}.":
            "Aktualizace MAME ▸ uživatel zvolil aktualizaci na {tag}.",
        "On MacOS and Linux mono is required as it runs under it. Please make sure mono is installed.":
            "Na MacOS a Linuxu je vyžadován mono, protože pod ním program běží. Ujistěte se, že je mono nainstalován.",
        "Running as a Flatpak: mono must be installed on the HOST system — the launch is delegated there via flatpak-spawn.":
            "Běží jako Flatpak: mono musí být nainstalován v HOSTITELSKÉM systému — spuštění se tam deleguje přes flatpak-spawn.",
        "Select a valid ZX Spectrum Next disk image (.img/.hdf) before launching MAME.":
            "Před spuštěním MAME vyberte platný obraz disku ZX Spectrum Next (.img/.hdf).",
        "ZX Next Unite update check: could not reach GitHub (offline, or no release published yet); skipping.":
            "Kontrola ZX Next Unite: nepodařilo se spojit s GitHubem (offline nebo žádné vydání) — přeskakuje se.",
        "ZX Next Unite update check: running as a Flatpak — updates come from your software center, skipping.":
            "Kontrola ZX Next Unite: běží jako Flatpak — aktualizace přicházejí z centra softwaru, přeskakuje se.",
        "ZX Next Unite update ▸ skipped by user.":
            "Aktualizace ZX Next Unite ▸ přeskočena uživatelem.",
        "ZX Next Unite update: download cancelled.":
            "Aktualizace ZX Next Unite: stahování zrušeno.",
        # ---- SD Card tab: console + dialogs ----
        "Confirm Deletion":
            "Potvrdit smazání",
        "Create":
            "Vytvořit",
        "Create New Folder":
            "Vytvořit novou složku",
        "Created {name} in {folder} on the image ({count} file(s), {bytes} bytes).":
            "Vytvořeno {name} v {folder} na obrazu ({count} souborů, {bytes} bajtů).",
        "Delete files from the image to free space, or switch to a larger image.\nLarger SD card images can be downloaded from:":
            "Smažte soubory z obrazu pro uvolnění místa, nebo přejděte na větší obraz.\nVětší obrazy SD karet lze stáhnout z:",
        "Download":
            "Stáhnout",
        "Download failed: no valid destination folder.":
            "Stahování selhalo: chybí platná cílová složka.",
        "Downloading {name} from {url}":
            "Stahuje se {name} z {url}",
        "ERROR: hdfmonkey could not be found. Use the 'Download and install HDF Monkey' button (bottom right of the SD Card tab) to install it automatically, or do a full CSpect install from the itch.io tab, which also bundles hdfmonkey. It can also be installed manually from https://github.com/gasman/hdfmonkey — restart the app once installed.":
            "CHYBA: hdfmonkey nebyl nalezen. Použijte tlačítko 'Stáhnout a nainstalovat HDF Monkey' (vpravo dole na kartě SD Card) pro automatickou instalaci, nebo proveďte plnou instalaci CSpectu z karty itch.io, která hdfmonkey rovněž obsahuje. Lze jej nainstalovat i ručně z https://github.com/gasman/hdfmonkey — po instalaci aplikaci restartujte.",
        "Extracted disk image: {path}":
            "Obraz disku rozbalen: {path}",
        "Extracted {count} file(s) from {name} into {folder} on the image.":
            "Rozbaleno {count} souborů z {name} do {folder} na obrazu.",
        "Extracting image... %p%":
            "Rozbalování obrazu... %p%",
        "Failed downloading NextZXOS image: {error}":
            "Nepodařilo se stáhnout obraz NextZXOS: {error}",
        "Load Failed":
            "Načtení selhalo",
        "The image was extracted but could not be loaded:":
            "Obraz byl rozbalen, ale nepodařilo se jej načíst:",
        "Failed executing hdfmonkey, please make sure it is installed in the same local directory as zx-next-unite.":
            "Nepodařilo se spustit hdfmonkey — ujistěte se, že je nainstalován ve stejném adresáři jako zx-next-unite.",
        "Failed extracting NextZXOS image: {error}":
            "Nepodařilo se rozbalit obraz NextZXOS: {error}",
        "Failed loading image: {path}.":
            "Nepodařilo se načíst obraz: {path}.",
        "No SD-card disk image selected — pick or create a .img/.hdf at the top of this tab to unlock the emulator Launch buttons.":
            "Není vybrán žádný obraz SD karty — nahoře na této kartě zvolte nebo vytvořte .img/.hdf, aby se odemkla tlačítka pro spuštění emulátoru.",
        "Nothing to move: items are already in this folder.":
            "Není co přesouvat: položky už v této složce jsou.",
        "Only {free} MB free out of {total} MB ({used} % used, {pct} % free).":
            "Volných jen {free} MB z {total} MB ({used} % využito, {pct} % volných).",
        "Please load an image file first !":
            "Nejprve načtěte soubor obrazu!",
        "Please load an image first!":
            "Nejprve načtěte obraz!",
        "Please select an image file or folder first to delete!":
            "Nejprve vyberte soubor nebo složku obrazu ke smazání!",
        "Please select an image file or folder first to rename!":
            "Nejprve vyberte soubor nebo složku obrazu k přejmenování!",
        "Remote unzip cancelled — the image is unchanged.":
            "Vzdálené rozbalení zrušeno — obraz je beze změny.",
        "Remote unzip: download from the image failed or was cancelled — the image is unchanged.":
            "Vzdálené rozbalení: stahování z obrazu selhalo nebo bylo zrušeno — obraz je beze změny.",
        "Remote unzip: upload into the image failed or was cancelled.":
            "Vzdálené rozbalení: nahrání do obrazu selhalo nebo bylo zrušeno.",
        "Remote zip cancelled — no zip was created.":
            "Vzdálené zabalení zrušeno — žádný zip nebyl vytvořen.",
        "Remote zip: download from the image failed or was cancelled — no zip was created.":
            "Vzdálené zabalení: stahování z obrazu selhalo nebo bylo zrušeno — žádný zip nebyl vytvořen.",
        "Remote zip: upload into the image failed or was cancelled.":
            "Vzdálené zabalení: nahrání do obrazu selhalo nebo bylo zrušeno.",
        "SD Image Nearly Full":
            "Obraz SD je téměř plný",
        "The SD card image is nearly full.":
            "Obraz SD karty je téměř plný.",
        "The hdfmonkey provided by the CSpect itch.io package is not executable. Make it executable by running:":
            "hdfmonkey dodaný v balíčku CSpect z itch.io není spustitelný. Nastavte mu právo spouštění příkazem:",
        "The image is completely full ({total} MB capacity, 0 MB free).":
            "Obraz je zcela plný (kapacita {total} MB, 0 MB volných).",
        # ---- SD Card console: banner, detection, update checks ----
        "CSpect - by Mike Dailly http://cspect.org":
            "CSpect - autor: Mike Dailly http://cspect.org",
        "CSpect is up to date (installed {installed}, latest {latest}).":
            "CSpect je aktuální (nainstalovaná {installed}, nejnovější {latest}).",
        "Checking for a newer MAME release…":
            "Hledá se novější verze MAME…",
        "Checking for a newer ZX Next Unite release on GitHub…":
            "Na GitHubu se hledá novější verze ZX Next Unite…",
        "Checking itch.io for a newer CSpect release…":
            "Na itch.io se hledá novější verze CSpectu…",
        "HDF Monkey - by Matt Westcott":
            "HDF Monkey - autor: Matt Westcott",
        "Inspired by HDFM-GOOEY - by em00k":
            "Inspirováno HDFM-GOOEY - autor: em00k",
        "Loaded configuration file.":
            "Konfigurační soubor načten.",
        "MAME - ZX Spectrum Next support by Holub https://wiki.specnext.dev/MAME:Installing":
            "MAME - podpora ZX Spectrum Next: Holub https://wiki.specnext.dev/MAME:Installing",
        "MAME is up-to-date (installed 0.{installed}, latest 0.{latest}).":
            "MAME je aktuální (nainstalovaná 0.{installed}, nejnovější 0.{latest}).",
        "MAME is up-to-date with a patched version (installed 0.{installed}, latest 0.{latest}).":
            "MAME je aktuální v upravené verzi (nainstalovaná 0.{installed}, nejnovější 0.{latest}).",
        "MAME version: {version}":
            "Verze MAME: {version}",
        "NextSync - by Jari Komppa and Julien Clauzel":
            "NextSync - autoři: Jari Komppa a Julien Clauzel",
        "Using CSpect under downloads/cspect: {path}":
            "Používá se CSpect v downloads/cspect: {path}",
        "Using MAME under: {path}":
            "Používá se MAME v: {path}",
        "Using hdfmonkey bundled with CSpect: {path}":
            "Používá se hdfmonkey dodaný s CSpectem: {path}",
        "Welcome to ZX Next Unite {version}":
            "Vítejte v ZX Next Unite {version}",
        "ZX Next Unite is up to date (installed {installed}, latest {latest}).":
            "ZX Next Unite je aktuální (nainstalovaná {installed}, nejnovější {latest}).",
        "zx-next-unite - by Julien Clauzel 2024":
            "zx-next-unite - autor: Julien Clauzel 2024",
        "No image loaded": "Není načten žádný obraz",
        # ---- itch.io item viewer + web-link labels ----
        "About": "O hře",
        "Open on {site}": "Otevřít na {site}",
        "Open {url}": "Otevřít {url}",
        "✓  Re-install": "✓  Přeinstalovat",
        "⬇  Install": "⬇  Nainstalovat",
        "⬇  Installing…": "⬇  Instaluji…",
        "📂  Open download folder": "📂  Otevřít složku stahování",
        # ---- gallery item viewer (Classic + Retro) ----
        "🌐  Open on website": "🌐  Otevřít na webu",
        "🌐  Open on {site}": "🌐  Otevřít na {site}",
        "📂  Open install folder": "📂  Otevřít složku instalace",
        "🗑  Uninstall": "🗑  Odinstalovat",
        # ---- NextSync log lines (user-facing; protocol diagnostics stay English) ----
        "(-send saves received files under: {folder})":
            "(-send ukládá přijaté soubory do: {folder})",
        "Aliases:":
            "Aliasy:",
        "Cancel requested — stopping after current file":
            "Vyžádáno zrušení — zastavení po aktuálním souboru",
        "Cannot create {path}: {error}":
            "Nelze vytvořit {path}: {error}",
        "Closing connection":
            "Zavírání spojení",
        "Connected by {address} port {port}":
            "Připojeno z {address} port {port}",
        "Disconnected":
            "Odpojeno",
        "Existing-file policy: {policy} (change in Settings -> 'NextSync - when a sent file or directory exists locally').":
            "Pravidlo pro existující soubory: {policy} (změníte v Nastavení -> 'NextSync - when a sent file or directory exists locally').",
        "Failed to rename {path}: {error}":
            "Nepodařilo se přejmenovat {path}: {error}",
        "IP addresses:":
            "IP adresy:",
        "Import failed: no valid destination folder.":
            "Import selhal: chybí platná cílová složka.",
        "Navigate to a folder in the left local file explorer, press 'Set current folder as new sync root folder' to choose a sync root and then press the 'Start Classic NextSync server' button.":
            "Přejděte do složky v levém průzkumníku, stiskněte 'Set current folder as new sync root folder' pro volbu kořene synchronizace a poté stiskněte tlačítko 'Start Classic NextSync server'.",
        "NextSync HTTP bridge NOT started: {error}":
            "HTTP most NextSync NEBYL spuštěn: {error}",
        "NextSync HTTP bridge listening on port {port}":
            "HTTP most NextSync naslouchá na portu {port}",
        "NextSync HTTP bridge stopped.":
            "HTTP most NextSync zastaven.",
        "NextSync HTTP bridge: bearer-token protection is ON (requests must carry the {header} header; others get HTTP 401)":
            "HTTP most NextSync: ochrana tokenem je ZAPNUTA (požadavky musí obsahovat hlavičku {header}; ostatní dostanou HTTP 401)",
        "NextSync is already running — please wait for it to finish.":
            "NextSync už běží — počkejte na dokončení.",
        "NextSync listening to port {port}":
            "NextSync naslouchá na portu {port}",
        "NextSync server, protocol version: {version}":
            "Server NextSync, verze protokolu: {version}",
        "No network detected - connect to Wi-Fi/Ethernet to see the address your Next should sync to.":
            "Nebyla zjištěna síť — připojte se k Wi-Fi/Ethernetu, abyste viděli adresu, se kterou má Next synchronizovat.",
        "Note":
            "Poznámka",
        "Nothing (more) to sync":
            "Není (už) co synchronizovat",
        "Now run one of these commands on your Next:":
            "Nyní na svém Nextu spusťte jeden z těchto příkazů:",
        "Primary IP:":
            "Hlavní IP:",
        "Received {name} ({bytes} bytes)":
            "Přijato {name} ({bytes} bajtů)",
        "Receiving files from the Next...":
            "Přijímání souborů z Nextu...",
        "Receiving: {name} -> {path}":
            "Přijímání: {name} -> {path}",
        "Remote .sync5 update failed while reading {path}: {error} — nothing was sent.":
            "Vzdálená aktualizace .sync5 selhala při čtení {path}: {error} — nic nebylo odesláno.",
        "Remote .sync5 update refused: {path} does not carry the expected 'NextSync {version}' banner — wrong or stale file.":
            "Vzdálená aktualizace .sync5 odmítnuta: {path} neobsahuje očekávané označení 'NextSync {version}' — špatný nebo zastaralý soubor.",
        "Remote .sync5 update: staging {path} ({size} bytes)…":
            "Vzdálená aktualizace .sync5: nahrávání {path} ({size} bajtů)…",
        "Remote .sync5 update: staged copy verified ({size} bytes) — swapping it in…":
            "Vzdálená aktualizace .sync5: nahraná kopie ověřena ({size} bajtů) — probíhá výměna…",
        "Remote .sync5 update FAILED mid-swap: the Next may be missing {target}. If .sync5 no longer starts, rename {backup} back to sync5 in the NextZXOS Browser (the staged {staged} can be deleted).":
            "Vzdálená aktualizace .sync5 SELHALA uprostřed výměny: Next možná nemá {target}. Pokud se .sync5 už nespustí, přejmenujte {backup} zpět na sync5 v prohlížeči NextZXOS (nahraný {staged} lze smazat).",
        "Remote .sync5 update complete: {version} is on the card. The session will now close — run {command} on the Next to start the new dot.":
            "Vzdálená aktualizace .sync5 dokončena: {version} je na kartě. Relace se nyní ukončí — spusťte {command} na Nextu pro spuštění nového dot příkazu.",
        "Remote .sync5 update failed: {reason}. Nothing was swapped — the Next still runs its current dot.":
            "Vzdálená aktualizace .sync5 selhala: {reason}. Nic nebylo vyměněno — Next stále používá svůj současný dot příkaz.",
        "Remote explorer: connected to {address}":
            "Vzdálený průzkumník: připojeno k {address}",
        "Remote explorer: connection error from the Next ({error}) — session over.":
            "Vzdálený průzkumník: chyba spojení s Nextem ({error}) — relace ukončena.",
        "Remote explorer: the Next closed the connection.":
            "Vzdálený průzkumník: Next ukončil spojení.",
        "Remote explorer: no word from the Next for {seconds}s — assuming it is gone (powered off? Wi-Fi dropped?)":
            "Vzdálený průzkumník: od Nextu {seconds}s nic nepřišlo — považuje se za ztracený (vypnutý? spadla Wi-Fi?)",
        "Remote explorer: turned away a second Next at {address} — a session is already active (Busy).":
            "Vzdálený průzkumník: druhý Next z {address} byl odmítnut — relace už běží (Busy).",
        "Remote explorer: server keeps running in the background — stop it from the Remote Explorer view.":
            "Vzdálený průzkumník: server dál běží na pozadí — zastavte ho v pohledu Remote Explorer.",
        "Remote explorer: navigate to a folder in the left file explorer, press 'Set current folder as new sync root folder', click 'Start Remote Explorer NextSync server', then run {command} on your Next.":
            "Vzdálený průzkumník: přejděte do složky v levém průzkumníku, stiskněte 'Set current folder as new sync root folder', klikněte na 'Start Remote Explorer NextSync server' a pak na Nextu spusťte {command}.",
        "Remote explorer: port {port} is already in use — is another ZX-Next-Unite (or NextSync server) already running?":
            "Vzdálený průzkumník: port {port} je již obsazen — neběží už jiný ZX-Next-Unite (nebo server NextSync)?",
        "Remote explorer: the Next disconnected (BREAK / Bye) — restarting the listen server; run {command} on your Next to reconnect.":
            "Vzdálený průzkumník: Next se odpojil (BREAK / Bye) — restartuji naslouchací server; pro opětovné připojení spusťte na Nextu {command}.",
        "Remote explorer: the Next disconnected (BREAK / Bye). Press 'Start Remote Explorer NextSync server' to accept a new connection.":
            "Vzdálený průzkumník: Next se odpojil (BREAK / Bye). Stiskněte 'Start Remote Explorer NextSync server' pro přijetí nového spojení.",
        "Remote explorer: waiting for {command} on port {port}…":
            "Vzdálený průzkumník: čekání na {command} na portu {port}…",
        "Renamed: {old} -> {new}":
            "Přejmenováno: {old} -> {new}",
        "Running on host:":
            "Běží na počítači:",
        "Saving incoming files under: {folder}":
            "Příchozí soubory se ukládají do: {folder}",
        "Send via NextSync: nothing to send in {folder}.":
            "Odeslat přes NextSync: v {folder} není co odeslat.",
        "Sending {folder} via Remote Explorer (-listen) → {target} …":
            "Odesílání {folder} přes Vzdálený průzkumník (-listen) → {target} …",
        "Set a sync root folder first: navigate to the folder in the left local file explorer and press 'Set current folder as new sync root folder'.":
            "Nejprve zvolte kořenovou složku synchronizace: přejděte k ní v levém průzkumníku a stiskněte 'Set current folder as new sync root folder'.",
        "Skipped (already exists): {path}":
            "Přeskočeno (již existuje): {path}",
        "Skipped {path}: cannot import a folder into itself.":
            "Přeskočeno {path}: složku nelze importovat do sebe sama.",
        "Stop the running sync before starting the remote server.":
            "Před spuštěním vzdáleného serveru zastavte probíhající synchronizaci.",
        "Sync file list has {count} files.":
            "Seznam k synchronizaci obsahuje {count} souborů.",
        "Sync point file {name} not found, syncing all files regardless of timestamp.":
            "Soubor bodu synchronizace {name} nebyl nalezen — synchronizují se všechny soubory bez ohledu na datum.",
        "Sync point updated with {count} received file(s)":
            "Bod synchronizace aktualizován o {count} přijatých souborů",
        "Upload connection closed":
            "Spojení pro nahrávání uzavřeno",
        "Upload finished, {count} file(s) received":
            "Nahrávání dokončeno, přijato {count} souborů",
        "Using {folder} as sync root":
            "Kořen synchronizace: {folder}",
        "WARNING":
            "VAROVÁNÍ",
        "Warning":
            "Varování",
        "Warning! Ignore file {name} not found in directory. All files will be synced, possibly including this file.":
            "Pozor! Soubor výjimek {name} nebyl ve složce nalezen. Budou synchronizovány všechny soubory, možná včetně tohoto.",
        "{kb} kilobytes payload, {rate} kBps effective speed":
            "{kb} kilobajtů užitečných dat, efektivní rychlost {rate} kBps",
        "{kb} kilobytes transferred in {seconds} seconds, {rate} kBps":
            "Přeneseno {kb} kilobajtů za {seconds} s, {rate} kBps",
        "{severity}: Ready to sync {count} files, {kb} kilobytes.":
            "{severity}: připraveno k synchronizaci {count} souborů, {kb} kilobajtů.",
        # ---- checkboxes ----
        "Alien Floyd's — animated background on all tabs (Retro/pygame)":
            "Alien Floyd's — animované pozadí na všech kartách (Retro/pygame)",
        "Alien Floyd's — show the full-window 'Alien Floyd's' tab (Retro/pygame)":
            "Alien Floyd's — zobrazit celookennou kartu «Alien Floyd's» (Retro/pygame)",
        "Check for CSpect update on itch.io on startup":
            "Při startu hledat aktualizace CSpect na itch.io",
        "Check for ZX Next Unite updates at startup on Github":
            "Při startu hledat aktualizace ZX Next Unite na GitHubu",
        "Check for a newer MAME version at startup":
            "Při startu hledat novější verzi MAME",
        "Disable 'No emulators detected' message at startup":
            "Vypnout hlášku «Nenalezen žádný emulátor» při startu",
        "Do not prompt for confirmation on deletion.":
            "Nežádat o potvrzení při mazání.",
        "Enable NextSync HTTP bridge (web server for the Next's .http command)":
            "Zapnout HTTP most NextSync (webový server pro příkaz .http Nextu)",
        "Enable crash log file generation":
            "Zapnout zápis protokolu pádů",
        "Enable multi API endpoints search (GetIt, ZXDB & zxArt search together).":
            "Zapnout hledání ve více API najednou (GetIt, ZXDB a zxArt).",
        "Enable search autocompletion.": "Zapnout automatické doplňování hledání.",
        "NextSync — starfield log animation (Retro/pygame mode)":
            "NextSync — animace hvězd v protokolu (režim Retro/pygame)",
        "Perform pre-availability check on Downloads (ZXDB & zxArt).":
            "Ověřovat dostupnost souborů ke stažení (ZXDB a zxArt).",
        "Require bearer token": "Vyžadovat přístupový token",
        "SD Card - Warn when an image is nearly full.":
            "Karta SD — upozornit, když je obraz téměř plný.",
        "Send deleted files to the Recycle Bin (local file explorers).":
            "Přesouvat smazané soubory do Koše (místní průzkumníky).",
        "Show the itch.io tab (browse & install your itch.io collections)":
            "Zobrazit kartu itch.io (procházení a instalace vašich kolekcí)",
        "Slow transfer": "Pomalý přenos",
        "Unite! — Invaders background animation (Retro/pygame mode)":
            "Unite! — animace pozadí Invaders (režim Retro/pygame)",
        # ---- radio buttons / group boxes ----
        "Always sync (send everything)": "Synchronizovat vždy (posílat vše)",
        "Sync changed files (continuous)": "Synchronizovat změny (průběžně)",
        "Sync once": "Synchronizovat jednou",
        "Sync mode": "Režim synchronizace",
        # ---- placeholders ----
        "Filter by name, type or size...": "Filtrovat podle názvu, typu či velikosti…",
        "Filter by name...": "Filtrovat podle názvu…",
        "Local folder path...": "Cesta k místní složce…",
        "New directory name ...": "Název nového adresáře…",
        "Paste your personal API key (itch.io → Settings → API keys)":
            "Vložte svůj osobní klíč API (itch.io → Settings → API keys)",
        "Path inside the SD card image...": "Cesta uvnitř obrazu SD…",
        "SD card image path...": "Cesta k obrazu karty SD…",
        "Search ZXDB games... (leave empty for random selection)":
            "Hledat hry v ZXDB… (prázdné = náhodný výběr)",
        "Search across GetIt + ZXDB + zxArt...":
            "Hledat v GetIt + ZXDB + zxArt…",
        "Search files... (leave empty for latest 20)":
            "Hledat soubory… (prázdné = posledních 20)",
        "Search your itch.io library (collections + purchases)…":
            "Prohledat vaši knihovnu itch.io (kolekce a nákupy)…",
        "Search zxART productions... (leave empty to browse latest)":
            "Hledat produkce na zxART… (prázdné = novinky)",
        "Sync root folder...": "Kořenová složka synchronizace…",
        "bearer token (generated when you enable the checkbox)":
            "přístupový token (vygeneruje se po zaškrtnutí)",
        # ---- short tooltips ----
        "Browse mode": "Režim procházení",
        "Search mode": "Režim hledání",
        "Pick a letter": "Vyberte písmeno",
        "Double-click to enlarge": "Dvojklikem zvětšíte",
        "Double-click to open full view": "Dvojklikem otevřete plné zobrazení",
        "Generate a new random 64-character bearer token":
            "Vygenerovat nový náhodný token o 64 znacích",
        "Select a disk image to be loaded.": "Vyberte obraz disku k načtení.",
        "No SD card image is currently loaded.":
            "Není načten žádný obraz karty SD.",
        "Re-read the current local folder from disk.":
            "Znovu načíst aktuální místní složku z disku.",
        "Drag to resize the file explorers / log window split.":
            "Tažením změníte rozdělení průzkumníky / protokol.",
        "Drag to resize the results / MOTD split.":
            "Tažením změníte rozdělení výsledky / MOTD.",
        "Drag to set the background image opacity (0–100 %).":
            "Tažením nastavíte neprůhlednost pozadí (0–100 %).",
        "Type a value 0–100 to set the background image opacity.":
            "Zadejte hodnotu 0–100 pro neprůhlednost pozadí.",
        "Preview of the selected background image.":
            "Náhled vybraného obrázku na pozadí.",
        "Open https://itch.io/ in your browser":
            "Otevřít https://itch.io/ v prohlížeči",
        "Connect to itch.io using the API key above.":
            "Připojit se k itch.io pomocí výše uvedeného klíče API.",
        "Disconnect from itch.io and clear the listed items.":
            "Odpojit se od itch.io a vymazat zobrazené položky.",
        "Open https://itch.io/user/settings/api-keys in your browser":
            "Otevřít https://itch.io/user/settings/api-keys v prohlížeči",
        "Color used for directory name entries in the image explorer.":
            "Barva názvů adresářů v průzkumníku obrazu.",
        "Color used for file name entries in the image explorer.":
            "Barva názvů souborů v průzkumníku obrazu.",
        "Color used for the 'DIR' type label column of directory entries.":
            "Barva sloupce typu «DIR» u adresářů.",
        "Color used for the '[Up Directory..]' navigation row in the image explorer.":
            "Barva navigačního řádku «[Up Directory..]».",
        "Color used for the file extension column in the image explorer.":
            "Barva sloupce přípony souboru.",
        "Color used for the file size column in the image explorer.":
            "Barva sloupce velikosti souboru.",
        "Background color behind the file explorers and the whole app window.":
            "Barva pozadí za průzkumníky souborů a celým oknem aplikace.",
        "Discard the hand-picked colors above and restore the themed defaults.":
            "Zahodí ručně vybrané barvy výše a obnoví výchozí hodnoty motivu.",
        "MAME display aspect ratio (-aspect).":
            "Poměr stran obrazu MAME (-aspect).",
        "Perform a single sync and then stop the server.":
            "Provést jednu synchronizaci a server zastavit.",
        "Keep listening and send every file each time, ignoring the sync point.":
            "Dál naslouchat a pokaždé poslat všechny soubory bez ohledu na synchronizační bod.",
        "Keep listening and send only files that are new or changed since the\nlast sync (skips files recorded in the sync point). The default mode.":
            "Dál naslouchat a posílat jen soubory nové či změněné od poslední\nsynchronizace (přeskakuje soubory zapsané v synchronizačním bodě).\nVýchozí režim.",
        "Make the folder currently shown in the explorer above the new sync root.":
            "Nastavit složku zobrazenou v průzkumníku výše jako nový kořen synchronizace.",
        "Pick a random page from the full GetIt catalogue and show its entries.":
            "Vybrat náhodnou stránku katalogu GetIt a zobrazit její položky.",
        "Pick a random page of zxART productions and show its entries.":
            "Vybrat náhodnou stránku produkcí zxART a zobrazit ji.",
        "Show the most recent zxART productions/pictures (sorted by date).":
            "Zobrazit nejnovější produkce/obrázky zxART (podle data).",
        "Show the most recently added/updated ZXDB games.":
            "Zobrazit naposledy přidané či aktualizované hry v ZXDB.",
        "Fetch random entries from GetIt + ZXDB + zxArt and merge them here":
            "Načíst náhodné položky z GetIt + ZXDB + zxArt a sloučit je zde",
        "Fetch the latest releases from GetIt + ZXDB + zxArt and merge them here":
            "Načíst nejnovější vydání z GetIt + ZXDB + zxArt a sloučit je zde",
        "zxART catalog display language.\nPersisted across sessions in the config file.":
            "Jazyk katalogu zxART.\nUkládá se do konfiguračního souboru.",
        "Requires the optional 'pygame-ce' package.\nInstall with: pip install pygame-ce":
            "Vyžaduje volitelný balíček «pygame-ce».\nInstalace: pip install pygame-ce",
        "Load a ZX Spectrum Next disk image first — then CSpect can boot it from the mounted SD card.":
            "Nejdřív načtěte obraz disku ZX Spectrum Next — CSpect ho pak spustí z připojené karty SD.",
        "Select emulator image file: {path}":
            "Vybrat obraz disku pro emulátor: {path}",
        "No writable disk image available.":
            "Není k dispozici žádný zapisovatelný obraz disku.",
        ".img file {path} already in use.":
            "Soubor .img {path} se už používá.",
        "The disk image {path} can no longer be found — it may have been moved, renamed or deleted.":
            "Obraz disku {path} už nelze najít — mohl být přesunut, přejmenován nebo smazán.",
        "Select a ZX Spectrum Next disk image (.img/.hdf) first — then MAME can boot it as the Next's hard disk.":
            "Nejdřív vyberte obraz disku (.img/.hdf) ZX Spectrum Next — MAME ho pak spustí jako pevný disk Nextu.",
        "Select the parent folder inside the SD card image\n(at the top level this returns the target to the image root).":
            "Přejít do nadřazené složky uvnitř obrazu SD\n(na nejvyšší úrovni se vrací ke kořeni obrazu).",
        "Go up one folder in the local file explorer\n(same as double-clicking its '..' entry).":
            "O složku výš v místním průzkumníku\n(stejné jako dvojklik na položku «..»).",
        "Re-list the current image folder from the SD card image\n(runs 'hdfmonkey ls' again).":
            "Znovu vypsat aktuální složku obrazu SD\n(spustí «hdfmonkey ls» znovu).",
        "Download a ready-to-use NextZXOS SD card image from zxnext.uk,\nsave it to disk, and load it automatically.":
            "Stáhnout hotový obraz karty SD s NextZXOS ze zxnext.uk,\nuložit na disk a automaticky načíst.",
        "Switch between the classic table view and the picture (gallery)\nview. Persisted across sessions in the config file.":
            "Přepínání mezi klasickou tabulkou a zobrazením galerie.\nUkládá se do konfiguračního souboru.",
        "Switch between the classic table view and the picture (gallery) view.\nPersisted across sessions in the config file.":
            "Přepínání mezi klasickou tabulkou a zobrazením galerie.\nUkládá se do konfiguračního souboru.",
        "Language of the application's buttons, labels and checkboxes.\nApplies immediately; texts written while the app runs (logs, dialogs)\nfollow after a restart. Saved to the configuration file.":
            "Jazyk tlačítek, popisků a zaškrtávacích polí aplikace.\nProjeví se okamžitě; texty vznikající za běhu (protokol, dialogy)\nse změní po restartu. Ukládá se do konfigurace.",
        "🌐  Language set to match your system":
            "🌐  Jazyk nastaven podle vašeho systému",
        "The interface language was set to match your system language.\nYou can change it anytime in the Settings tab (\"Application language:\").":
            "Jazyk rozhraní byl nastaven podle jazyka vašeho systému.\nKdykoli ho můžete změnit na kartě Settings\n(«Jazyk aplikace:»).",
        # ---- Help tab (INIT_HELP; rebuilt per language by _repopulate_help) ----
        "Welcome to zx-next-unite {version} help":
            "Vítejte v nápovědě zx-next-unite {version}",
        "Introduction:":
            "Úvod:",
        "HdfmGooey was initially created by em00k and NextSync by Jari Komppa.":
            "HdfmGooey původně vytvořil em00k a NextSync Jari Komppa.",
        "A while back I rambled with the idea of an all in one bootstrapper transfer tool to":
            "Před časem jsem si pohrával s myšlenkou nástroje vše-v-jednom pro přenos a spouštění, aby",
        "avoid manipulating SD cards for the Spectrum Next and that was the initial idea of it.":
            "nebylo nutné přehazovat SD karty pro Spectrum Next — a to byl původní záměr.",
        "Last but not least some source code was lost from HDFM Gooey and the tool was stuck back in that time,":
            "V neposlední řadě se část zdrojového kódu HDFM Gooey ztratila a nástroj zůstal uvězněný v té době;",
        "with the agreement of em00k I started a rewrite in Python and later with Jari.":
            "se souhlasem em00k jsem začal s přepisem do Pythonu a později se přidal Jari.",
        "The point of using Python was that it would also provide MacOS and Linux portability.":
            "Smyslem použití Pythonu bylo, že zároveň přinese přenositelnost na MacOS a Linux.",
        "Later down the line I then extended the NextSync functionality from Sync3 to Sync4.":
            "Později jsem rozšířil funkčnost NextSync ze Sync3 na Sync4.",
        "The new .sync5 command for the Next speaks Sync4 and therefore allows sending files and directories using the -send command line option.":
            "Nový příkaz .sync5 pro Next mluví protokolem Sync4, a proto umožňuje posílat soubory a adresáře volbou příkazové řádky -send.",
        "There is as well a new nextsync5.py command line located at the root of the repository that supports the new Sync4 protocol.":
            "K dispozici je také nový nástroj příkazové řádky nextsync5.py v kořeni repozitáře, který podporuje nový protokol Sync4.",
        "Here we are now you have it!":
            "A jsme tady — máte to mít!",
        "Keyboard shortcuts":
            "Klávesové zkratky",
        "The three file explorers (SD Card local, SD Card disk image and NextSync local) share these shortcuts. Copy / Cut / Paste work across all three explorers and also exchange with the operating-system clipboard (e.g. copy in Windows Explorer, paste into the disk image, and vice-versa):":
            "Tři průzkumníky souborů (místní SD Card, obraz disku SD Card a místní NextSync) sdílejí tyto zkratky. Kopírovat / Vyjmout / Vložit fungují napříč všemi třemi průzkumníky a vyměňují si obsah i se schránkou operačního systému (např. zkopírujte v Průzkumníku Windows, vložte do obrazu disku a naopak):",
        "    Ctrl+C  -  Copy the selected file(s)/folder(s) to the shared clipboard.":
            "    Ctrl+C  -  Zkopírovat vybrané soubory/složky do sdílené schránky.",
        "    Ctrl+X  -  Cut the selection (moved to the destination on the next paste).":
            "    Ctrl+X  -  Vyjmout výběr (přesune se do cíle při příštím vložení).",
        "    Ctrl+V  -  Paste into the selected / currently shown folder.":
            "    Ctrl+V  -  Vložit do vybrané / právě zobrazené složky.",
        "    F2      -  Rename the selected file or folder.":
            "    F2      -  Přejmenovat vybraný soubor nebo složku.",
        "    Delete  -  Delete the selected file or folder (disk-image & NextSync explorers).":
            "    Delete  -  Smazat vybraný soubor nebo složku (průzkumníky obrazu disku a NextSync).",
        "In the picture (gallery) item viewer (double-click an item in the GetIt, ZXDB, zxArt or itch.io tabs):":
            "V prohlížeči položek s obrázky (galerii) (dvojklik na položku na kartách GetIt, ZXDB, zxArt nebo itch.io):",
        "    Esc           -  Close the viewer and return to the gallery.":
            "    Esc           -  Zavřít prohlížeč a vrátit se do galerie.",
        "    Left / Right  -  Show the previous / next screenshot.":
            "    Left / Right  -  Zobrazit předchozí / další snímek obrazovky.",
        "Third party license":
            "Licence třetích stran",
        "zx-next-unite is released under the MIT license. It is a Qt Application using PySide6 (Qt for Python) on top of Qt6, used under the GNU LGPL v3.":
            "zx-next-unite je vydán pod licencí MIT. Je to Qt aplikace používající PySide6 (Qt for Python) nad Qt6, užívaným pod GNU LGPL v3.",
        "Please refer to the LICENSE and THIRD-PARTY-NOTICES.md files on github: https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE and https://github.com/jclauzel/ZX-Next-Unite/blob/main/THIRD-PARTY-NOTICES.md.":
            "Viz soubory LICENSE a THIRD-PARTY-NOTICES.md na githubu: https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE a https://github.com/jclauzel/ZX-Next-Unite/blob/main/THIRD-PARTY-NOTICES.md.",
        "PySide6 is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The pre-built release executables do include PySide6/Qt; since the app's full source code is published, they can be rebuilt with a modified Qt.":
            "PySide6 není součástí ruční instalace pythonu a je třeba jej nainstalovat zvlášť (viz pokyny k instalaci). Předkompilovaná sestavení releasů PySide6/Qt obsahují; protože je zveřejněn celý zdrojový kód aplikace, lze je znovu sestavit s upraveným Qt.",
        "zx-next-unite also uses pygame-ce (the community edition of pygame) for its animated backgrounds and visualizations (e.g. the 'Alien Floyd's' effects). Many thanks to the pygame and pygame-ce communities - see https://pyga.me and https://www.pygame.org.":
            "zx-next-unite používá také pygame-ce (komunitní edici pygame) pro animovaná pozadí a vizualizace (např. efekty 'Alien Floyd's'). Velké díky komunitám pygame a pygame-ce - viz https://pyga.me a https://www.pygame.org.",
        "pygame-ce is distributed under the GNU LGPL v2.1 license and, like PySide6, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions).":
            "pygame-ce je šířen pod licencí GNU LGPL v2.1 a stejně jako PySide6 není součástí ruční instalace pythonu — instaluje se zvlášť (viz pokyny k instalaci).",
        "zx-next-unite optionally uses itch-dl by Dragoon Aethis to power the itch.io tab (browsing and installing your itch.io collections). Many thanks to its author - see https://github.com/DragoonAethis/itch-dl.":
            "zx-next-unite volitelně používá itch-dl od Dragoon Aethis pro kartu itch.io (procházení a instalace vašich sbírek itch.io). Velké díky autorovi - viz https://github.com/DragoonAethis/itch-dl.",
        "itch-dl is distributed under the MIT license (Copyright (c) 2022 Dragoon Aethis) and, like PySide6 and pygame-ce, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The itch.io tab is only shown when itch-dl is installed.":
            "itch-dl je šířen pod licencí MIT (Copyright (c) 2022 Dragoon Aethis) a stejně jako PySide6 a pygame-ce není součástí ruční instalace pythonu — instaluje se zvlášť (viz pokyny k instalaci). Karta itch.io se zobrazí, jen když je itch-dl nainstalován.",
        "zx-next-unite optionally uses Flask by the Pallets team to power the NextSync HTTP bridge - the web server behind the Next's .http dot command that lets one Next drive another Next's SD card. Many thanks to its authors - see https://flask.palletsprojects.com and https://github.com/pallets/flask.":
            "zx-next-unite volitelně používá Flask od týmu Pallets pro HTTP most NextSync - webový server za dot příkazem .http, který umožňuje jednomu Nextu ovládat SD kartu druhého Nextu. Velké díky autorům - viz https://flask.palletsprojects.com a https://github.com/pallets/flask.",
        "Flask is distributed under the BSD-3-Clause license and, like the other optional packages, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The HTTP bridge toggle in Settings is greyed out until Flask is installed.":
            "Flask je šířen pod licencí BSD-3-Clause a stejně jako ostatní volitelné balíčky není součástí ruční instalace pythonu — instaluje se zvlášť (viz pokyny k instalaci). Přepínač HTTP mostu v Settings je nedostupný, dokud není Flask nainstalován.",
        "zx-next-unite optionally uses Send2Trash by Andrew Senetar and contributors (originally by Virgil Dupras) to send files deleted in the local file explorers to the system Recycle Bin / Trash instead of removing them permanently. Many thanks to its authors - see https://github.com/arsenetar/send2trash.":
            "zx-next-unite volitelně používá Send2Trash od Andrewa Senetara a přispěvatelů (původně Virgil Dupras), aby soubory smazané v místních průzkumnících putovaly do systémového Koše místo trvalého odstranění. Velké díky autorům - viz https://github.com/arsenetar/send2trash.",
        "Send2Trash is distributed under the BSD license and, like the other optional packages, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The 'Send deleted files to the Recycle Bin' toggle in Settings is greyed out until Send2Trash is installed.":
            "Send2Trash je šířen pod licencí BSD a stejně jako ostatní volitelné balíčky není součástí ruční instalace pythonu — instaluje se zvlášť (viz pokyny k instalaci). Přepínač 'Přesouvat smazané soubory do Koše' v Settings je nedostupný, dokud není Send2Trash nainstalován.",
        "zx-next-unite's optional pre-compiled Windows binary is built with PyInstaller by the PyInstaller Development Team, which bundles the app and all of its dependencies into a single standalone executable. Many thanks to its authors - see https://pyinstaller.org and https://github.com/pyinstaller/pyinstaller.":
            "Volitelný předkompilovaný binární soubor pro Windows se sestavuje nástrojem PyInstaller od PyInstaller Development Team, který zabalí aplikaci a všechny závislosti do jediného samostatného spustitelného souboru. Velké díky autorům - viz https://pyinstaller.org a https://github.com/pyinstaller/pyinstaller.",
        "PyInstaller is distributed under its GPL 2.0 license with a special exception that explicitly permits packaging applications of any license. It is a build-time tool only - used to produce the pre-compiled binary - and is not needed when running zx-next-unite from source with a manual python install.":
            "PyInstaller je šířen pod licencí GPL 2.0 se zvláštní výjimkou, která výslovně dovoluje balit aplikace s jakoukoli licencí. Je to nástroj jen pro sestavení - vytváří předkompilovaný binární soubor - a při spouštění zx-next-unite ze zdrojáků s ruční instalací pythonu není potřeba.",
        "The pre-compiled Windows binary is additionally compressed with UPX (the Ultimate Packer for eXecutables) by Markus Oberhumer, Laszlo Molnar and John Reiser. Many thanks to its authors - see https://upx.github.io and https://github.com/upx/upx.":
            "Předkompilovaný binární soubor pro Windows je navíc komprimován nástrojem UPX (the Ultimate Packer for eXecutables) od Markuse Oberhumera, Laszla Molnara a Johna Reisera. Velké díky autorům - viz https://upx.github.io a https://github.com/upx/upx.",
        "UPX is distributed under its own liberal license (based on the GPL, with a special exception covering the compressed executables it produces). Like PyInstaller it is a build-time tool only and is not needed when running from source.":
            "UPX je šířen pod vlastní liberální licencí (založenou na GPL, se zvláštní výjimkou pro komprimované spustitelné soubory, které vytváří). Stejně jako PyInstaller je to nástroj jen pro sestavení a při spouštění ze zdrojáků není potřeba.",
        "Setup & How to:":
            "Instalace a návody:",
        "Check out the main setup & demo video available at: https://youtu.be/-gUxV4fM1yo  (and the full python install is covered in the old py-hdfm-gooey since ZX-Next-Unite is an evolution of it : https://youtu.be/FJG-Z0DCIjQ )":
            "Podívejte se na hlavní video s instalací a ukázkou: https://youtu.be/-gUxV4fM1yo  (úplná instalace pythonu je popsána ve starém py-hdfm-gooey, protože ZX-Next-Unite je jeho evolucí : https://youtu.be/FJG-Z0DCIjQ )",
        "NextSync Head Over Heels demo: https://www.youtube.com/watch?v=D3_WqTPvjOE":
            "Demo NextSync s Head Over Heels: https://www.youtube.com/watch?v=D3_WqTPvjOE",
        "NextSync Night Knight demo: https://www.youtube.com/watch?v=eN1eMIqMCm4":
            "Demo NextSync s Night Knight: https://www.youtube.com/watch?v=eN1eMIqMCm4",
        "Is a required external component developed by Matt Westcott that allows browsing the image.":
            "Je to povinná externí komponenta od Matta Westcotta, která umožňuje procházet obraz.",
        "You will need to install it to get this application up and fully running.":
            "Budete jej muset nainstalovat, aby tato aplikace plně fungovala.",
        "If hdfmonkey is not present you will see an error message in the main log window as it is missing.":
            "Pokud hdfmonkey chybí, uvidíte v hlavním okně protokolu chybovou zprávu, že není k dispozici.",
        "If that is the case you will see a 'Download and install HDF Monkey' button bottom right,":
            "V takovém případě uvidíte vpravo dole tlačítko 'Stáhnout a nainstalovat HDF Monkey';",
        "once clicked it will download a pre-compiled hdfmonkey build for your platform (Windows/Linux/macOS) and install it under the app's downloads folder.":
            "po kliknutí stáhne předkompilované sestavení hdfmonkey pro vaši platformu (Windows/Linux/macOS) a nainstaluje je do složky downloads aplikace.",
        "If the above automated install is successful, you should then be able to select an image and navigate it.":
            "Pokud výše uvedená automatická instalace uspěje, měli byste být schopni vybrat obraz a procházet jej.",
        "hdfmonkey can also be installed manually based on the instructions for your platform that can be found at: https://github.com/gasman/hdfmonkey":
            "hdfmonkey lze nainstalovat i ručně podle pokynů pro vaši platformu na: https://github.com/gasman/hdfmonkey",
        "zx-next-unite implements the <Server> side code and protocol of NextSync by Jari Komppa.":
            "zx-next-unite implementuje kód a protokol strany <serveru> NextSync od Jariho Komppy.",
        "It does not require any dot .sync modification and it uses the same very close python logic as nextsync.py.":
            "Nevyžaduje žádnou úpravu dot příkazu .sync a používá stejnou, velmi blízkou python logiku jako nextsync.py.",
        "Initial release on specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8":
            "První vydání na specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8",
        "As a result you will need to run the same dot .sync command on your Next as with the console version and the same network protocol.":
            "Proto budete muset na svém Nextu spustit stejný dot příkaz .sync jako u konzolové verze a se stejným síťovým protokolem.",
        "The latest release v1.2 of the .sync command can be found here https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 .":
            "Nejnovější vydání v1.2 příkazu .sync najdete zde https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 .",
        "You may follow the same instructions as provided in the readme.txt of that release.":
            "Můžete postupovat podle stejných pokynů z readme.txt daného vydání.",
        "On your Spectrum Next, clone or image copy the SYNC command that is located in the above release zip file into your Next dot folder.":
            "Na svém Spectrum Next naklonujte nebo do obrazu zkopírujte příkaz SYNC ze zipu daného vydání do složky dot vašeho Nextu.",
        "Navigate to the NextSync tab, select the root folder to sync on the left.":
            "Přejděte na kartu NextSync a vlevo vyberte kořenovou složku k synchronizaci.",
        "Once you have selected the folder hit the 'Prepare Classic NextSync server' button, check the NextSync log window on the right.":
            "Po výběru složky stiskněte tlačítko 'Připravit klasický server NextSync' a sledujte okno protokolu NextSync vpravo.",
        "The first time you run .sync on your Next you will be prompted to select the <server> IP address, this machine running NextSync.":
            "Při prvním spuštění .sync na vašem Nextu budete vyzváni k volbě IP adresy <serveru> — tohoto počítače s NextSync.",
        "From the log window pick the IP address from this machine you want to use and type it on your Next.":
            "V okně protokolu si vyberte IP adresu tohoto počítače, kterou chcete použít, a napište ji na svém Nextu.",
        "Then start the sync server on this machine using the 'Start Classic NextSync server' button and then run the .sync command on your Next.":
            "Poté spusťte synchronizační server na tomto počítači tlačítkem 'Spustit klasický server NextSync' a na svém Nextu spusťte příkaz .sync.",
        "At this point your Spectrum Next will connect to your machine using a network socket and the files will be sent to your Next.":
            "V tu chvíli se váš Spectrum Next připojí k tomuto počítači síťovým socketem a soubory se odešlou na váš Next.",
        "As it is your Next that will connect to this machine check your firewall allows inbound calls to this machine on port: 2048 by default.":
            "Protože se k tomuto počítači připojuje váš Next, zkontrolujte, že firewall povoluje příchozí spojení na tento počítač na portu: výchozí 2048.",
        "The same syncignore.txt and syncpoint.dat file logic applies and allows you to control the sync (please check Jari's documentation).":
            "Platí stejná logika souborů syncignore.txt a syncpoint.dat, kterou synchronizaci řídíte (viz dokumentace Jariho).",
        "NextSync source code can be found here: https://github.com/jarikomppa/specnext/tree/master/sync":
            "Zdrojový kód NextSync najdete zde: https://github.com/jarikomppa/specnext/tree/master/sync",
        "If you run into any type of issue using the NextSync integration please run first Jari's command line version to see if it works as expected.":
            "Narazíte-li s integrací NextSync na jakýkoli problém, spusťte nejdřív Jariho konzolovou verzi a ověřte, že funguje, jak má.",
        "OpenAL sound engine (on Windows)":
            "Zvukový engine OpenAL (ve Windows)",
        "The OpenAL library is required on Windows for CSpect to play sound, you may download it here: https://openal.org/":
            "Knihovna OpenAL je ve Windows nutná, aby CSpect přehrával zvuk; stáhnout ji můžete zde: https://openal.org/",
        "Mono (on Linux & MacOS Only)":
            "Mono (pouze Linux a MacOS)",
        "You will also need to manually install the mono-complete package for example using: sudo apt-get install mono-complete":
            "Budete také muset ručně nainstalovat balíček mono-complete, například přes: sudo apt-get install mono-complete",
        "Third-Party Content Sources (GetIt / ZXDB / zxArt):":
            "Zdroje obsahu třetích stran (GetIt / ZXDB / zxArt):",
        "zx-next-unite integrates three external databases to let you browse and download":
            "zx-next-unite propojuje tři externí databáze, abyste mohli procházet a stahovat",
        "Spectrum-related software and artwork directly from within the application.":
            "software a grafiku spojené se Spectrem přímo z aplikace.",
        "The application consumes their public APIs — it does not host, mirror, or":
            "Aplikace využívá jejich veřejná API — sama nehostuje, nezrcadlí ani",
        "redistribute any of the files itself.":
            "žádné soubory dále nešíří.",
        "  GetIt is a community-maintained archive of ZX Spectrum Next software.":
            "  GetIt je komunitou udržovaný archiv softwaru pro ZX Spectrum Next.",
        "  The application queries the GetIt API to list and search files, then":
            "  Aplikace se dotazuje API GetIt na výpis a hledání souborů a poté",
        "  downloads them directly from the URLs returned by that API.":
            "  je stahuje přímo z URL vrácených tímto API.",
        "  ZXDB is an open-source database of ZX Spectrum and related software,":
            "  ZXDB je open-source databáze softwaru ZX Spectrum a příbuzných strojů,",
        "  maintained by the community at https://github.com/zxdb/ZXDB .":
            "  udržovaná komunitou na https://github.com/zxdb/ZXDB .",
        "  The application queries the ZXDB REST API for titles, releases, screenshots":
            "  Aplikace se dotazuje REST API ZXDB na tituly, vydání, snímky obrazovky",
        "  and inlays, then downloads files directly from the URLs returned by that API.":
            "  a obaly a poté stahuje soubory přímo z URL vrácených tímto API.",
        "  zxArt (https://zxart.ee) is a gallery and archive dedicated to ZX Spectrum":
            "  zxArt (https://zxart.ee) je galerie a archiv věnované vizuálnímu umění,",
        "  visual art, music, and productions.":
            "  hudbě a produkcím ZX Spectrum.",
        "  The application sends requests to the zxArt API to search productions and":
            "  Aplikace posílá požadavky na API zxArt pro hledání produkcí a",
        "  pictures, retrieve metadata and preview images, and download productions":
            "  obrázků, získání metadat a náhledů a stahování produkcí",
        "  directly from the URLs returned by that API.":
            "  přímo z URL vrácených tímto API.",
        "  Mame emulator brought to you by Holub for the ZX Spectrum Next can be installed following this documentation: https://wiki.specnext.dev/MAME:Installing":
            "  Emulátor Mame, který na ZX Spectrum Next přinesl Holub, nainstalujete podle této dokumentace: https://wiki.specnext.dev/MAME:Installing",
        "  Official Windows Binary Packages can be found here: https://www.mamedev.org/release.html":
            "  Oficiální binární balíčky pro Windows najdete zde: https://www.mamedev.org/release.html",
        "  Put the file tbblue.zip that can be found here: https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip into MAME's roms folder.":
            "  Soubor tbblue.zip, který najdete zde: https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip vložte do složky roms MAME.",
        "  Important note: Don't extract the tbblue.zip file; MAME will look for the zip file when the 'tbblue' machine is selected.":
            "  Důležitá poznámka: soubor tbblue.zip nerozbalujte; MAME hledá zip, když je vybrán stroj 'tbblue'.",
        "  Mike Dailly's CSpect is a downloadable emulator for Windows, macOS, and Linux":
            "  CSpect Mika Daillyho je stažitelný emulátor pro Windows, macOS a Linux",
        "  Sites and links:":
            "  Weby a odkazy:",
        "Legal disclaimer:":
            "Právní upozornění:",
        "  The author of zx-next-unite does NOT distribute any files, ROMs, games,":
            "  Autor zx-next-unite NEŠÍŘÍ žádné soubory, ROMy, hry,",
        "  demos, graphics, music, or any other content obtained through these APIs.":
            "  dema, grafiku, hudbu ani jiný obsah získaný přes tato API.",
        "  All content is served exclusively by the respective third-party services.":
            "  Veškerý obsah poskytují výhradně příslušné služby třetích stran.",
        "  This application and author do not control third-party content.":
            "  Tato aplikace ani autor obsah třetích stran nekontrolují.",
        "  It is the sole responsibility of the end user to ensure that any content":
            "  Je výhradní odpovědností koncového uživatele zajistit, aby jakýkoli obsah,",
        "  they download or use through this application complies with the applicable":
            "  který přes tuto aplikaci stáhne nebo použije, vyhovoval platným",
        "  copyright, licensing, and legal requirements in their jurisdiction.":
            "  požadavkům autorského práva, licencí a předpisů jeho jurisdikce.",
        "  If in doubt, consult the terms of service of the relevant platform and":
            "  V případě pochybností si prostudujte podmínky služby dané platformy a",
        "  seek appropriate legal advice before downloading or using any content.":
            "  před stažením či použitím jakéhokoli obsahu vyhledejte vhodnou právní radu.",
        "  For inquiries you may reach out to me on my github page: https://github.com/jclauzel/ZX-Next-Unite":
            "  S dotazy se na mě můžete obrátit na mé stránce github: https://github.com/jclauzel/ZX-Next-Unite",
        "Enjoy!":
            "Užijte si to!",
        # ---- OpenAL guided install (CSpect sound on Windows) ----
        "OpenAL 1.1 detected — CSpect sound is ready.":
            "OpenAL 1.1 nalezen — zvuk CSpectu je připraven.",
        "Install OpenAL?":
            "Nainstalovat OpenAL?",
        ("On Windows CSpect needs the <b>OpenAL 1.1</b> audio library "
         "for sound, and it was not detected on this machine — without "
         "it CSpect runs silent.<br><br>"
         "OpenAL is separate, third-party software — many thanks to its "
         "authors: {url}<br><br>"
         "Download the official installer (oalinst.exe) from openal.org "
         "and run it now?<br><br>"
         "Windows will ask for administrator approval when the installer "
         "starts — the app itself never runs elevated."):
            ("Ve Windows potřebuje CSpect pro zvuk audio knihovnu "
             "<b>OpenAL 1.1</b>, a ta na tomto počítači nebyla nalezena — "
             "bez ní běží CSpect potichu.<br><br>"
             "OpenAL je samostatný software třetí strany — velké díky jeho "
             "autorům: {url}<br><br>"
             "Stáhnout oficiální instalátor (oalinst.exe) z openal.org a "
             "spustit jej nyní?<br><br>"
             "Windows si při startu instalátoru vyžádá schválení správce — "
             "samotná aplikace nikdy neběží se zvýšenými právy."),
        "Download and run the OpenAL installer":
            "Stáhnout a spustit instalátor OpenAL",
        "Open openal.org":
            "Otevřít openal.org",
        "The OpenAL download failed — see the log for details. You can install it manually from {url}":
            "Stažení OpenAL selhalo — podrobnosti najdete v protokolu. Můžete jej nainstalovat ručně z {url}",
    },
    "fr": {
        # ---- 9.6.0: per-emulator button colour ----
        "Set the {emulator} color…":
            "Définir la couleur de {emulator}…",
        "Reset the {emulator} color":
            "Réinitialiser la couleur de {emulator}",
        # ---- 9.6.0: forgetting a remembered SD image path ----
        "Remove \"{path}\" from the list":
            "Retirer \"{path}\" de la liste",
        "Clear the whole list":
            "Effacer toute la liste",
        "Removed {path} from the image list — the image file itself was not deleted.":
            "{path} retiré de la liste des images — le fichier image lui-même n'a pas été supprimé.",
        "Cleared the image list — no image files were deleted.":
            "Liste des images effacée — aucun fichier image n'a été supprimé.",
        "Clear the image list?":
            "Effacer la liste des images ?",
        "Forget all {count} remembered image paths? The image files themselves are not deleted.":
            "Oublier les {count} chemins d'images mémorisés ? Les fichiers image eux-mêmes ne sont pas supprimés.",
        "Remove the image path shown on the left from the list.\nThe image file itself is not deleted.":
            "Retirer de la liste le chemin d'image affiché à gauche.\nLe fichier image lui-même n'est pas supprimé.",
        "Path to the SD card image (.img / .hdf).\nType a path directly, click the arrow to pick from recently loaded images,\nor use the 'Select NextZXOS disk Image' button to browse.\nRight-click the box for list options, or press Delete on a dropdown entry to forget it.":
            "Chemin de l'image de carte SD (.img / .hdf).\nSaisissez un chemin, cliquez sur la flèche pour choisir une image récemment chargée,\nou utilisez le bouton 'Choisir une image disque NextZXOS' pour parcourir.\nClic droit sur le champ pour les options de liste, ou touche Suppr sur une entrée de la liste déroulante pour l'oublier.",
        # ---- GetIt starter pack ----
        'Assembling the starter pack…': 'Assemblage du pack de démarrage…',
        'Downloading {title} ({idx}/{total})…': 'Téléchargement de {title} ({idx}/{total})…',
        ('Everything on GetIt is freely distributable; the files are written to {dir} '
         'inside the loaded image.'):
            ('Tout ce qui est sur GetIt est librement distribuable ; les fichiers sont '
             "écrits dans {dir} à l'intérieur de l'image chargée."),
        'Failed: {names}': 'Échecs : {names}',
        'Fetching the GetIt catalogue…': 'Récupération du catalogue GetIt…',
        ('Fill the loaded SD image with a hand-picked selection of modern homebrew '
         'from the GetIt catalogue — everything on GetIt is freely distributable.'):
            ("Remplit l'image SD chargée d'une sélection faite main de homebrew "
             'moderne du catalogue GetIt — tout ce qui est sur GetIt est librement '
             'distribuable.'),
        'Fill your SD image with {count} hand-picked homebrew titles from the GetIt catalogue?': 'Remplir votre image SD avec {count} titres homebrew sélectionnés à la main du catalogue GetIt ?',
        'Load a disk image first (SD Card tab), then try again.': "Chargez d'abord une image disque (onglet SD Card), puis réessayez.",
        'No network connection.': 'Pas de connexion réseau.',
        'Not in the catalogue right now: {names}': 'Absent(s) du catalogue pour le moment : {names}',
        'Starter pack': 'Pack de démarrage',
        'Starter pack cancelled — {done} title(s) were installed.': 'Pack de démarrage annulé — {done} titre(s) installé(s).',
        'Starter pack complete: {done} of {total} titles installed to {dir}.': 'Pack de démarrage terminé : {done} titres sur {total} installés dans {dir}.',
        'Starter pack failed: {error}': 'Échec du pack de démarrage : {error}',
        '🎁 Starter pack': '🎁 Pack de démarrage',
        # ---- context menus & runtime dialogs ----
        '"{name}" already exists in this folder.': '"{name}" existe déjà dans ce dossier.',
        'Add drive {letter}: to the list?': 'Ajouter le lecteur {letter}: à la liste ?',
        'Add Next drive': 'Ajouter un lecteur du Next',
        'Confirm deletion': 'Confirmer la suppression',
        'Copy': 'Copier',
        'Copy all text': 'Copier tout le texte',
        'Copy path to clipboard': 'Copier le chemin dans le presse-papiers',
        'Copy text to clipboard': 'Copier le texte dans le presse-papiers',
        'Could not create {name}:': 'Impossible de créer {name} :',
        'Could not create:': 'Impossible de créer :',
        'Could not delete:': 'Impossible de supprimer :',
        'Could not extract {name}:': "Impossible d'extraire {name} :",
        'Could not rename:': 'Impossible de renommer :',
        'Create directory failed': 'Échec de la création du répertoire',
        'Create new directory': 'Créer un nouveau répertoire',
        'Create new directory…': 'Créer un nouveau répertoire…',
        'Cut': 'Couper',
        'Decrease font size': 'Réduire la taille de police',
        'Delete failed': 'Échec de la suppression',
        'Delete from the local disk?': 'Supprimer du disque local ?',
        'Delete on the Next? Folders are deleted with everything inside them.': 'Supprimer sur le Next ? Les dossiers sont supprimés avec tout leur contenu.',
        'Delete the file "{name}"?': 'Supprimer le fichier "{name}" ?',
        'Delete the folder "{name}" and all of its contents?': 'Supprimer le dossier "{name}" et tout son contenu ?',
        'Delete these {count} items? Folders are deleted with all of their contents.': 'Supprimer ces {count} éléments ? Les dossiers sont supprimés avec tout leur contenu.',
        'Deleted files are sent to the Recycle Bin.': 'Les fichiers supprimés sont envoyés à la corbeille.',
        'Download (:<-)': 'Télécharger (:<-)',
        'Download content': 'Télécharger le contenu',
        'Download Failed': 'Échec du téléchargement',
        'Drive letter of the additional SD reader/partition (D..P):': 'Lettre du lecteur SD/de la partition supplémentaire (D..P) :',
        'Extraction Failed': "Échec de l'extraction",
        'Failed to download the NextZXOS image:': "Impossible de télécharger l'image NextZXOS :",
        'Fetch issue info for this magazine': 'Récupérer les infos des numéros de ce magazine',
        'Fetch single magazine by name': 'Récupérer un magazine par son nom',
        'file': 'le fichier',
        'Files:  {files}\nFolders:  {folders}\nTotal size:  {size} bytes  ({pretty})': 'Fichiers :  {files}\nDossiers :  {folders}\nTaille totale :  {size} octets  ({pretty})',
        'folder': 'le dossier',
        'Get size': 'Obtenir la taille',
        'Image not writable': 'Image non accessible en écriture',
        'Increase font size (now {px}px)': 'Augmenter la taille de police (actuellement {px}px)',
        'More like this': 'Plus comme ceci',
        'New directory name in': 'Nom du nouveau répertoire dans',
        'Name this Next': 'Nommer ce Next',
        'Friendly name for {addr} (empty removes it):':
            'Nom convivial pour {addr} (vide le supprime) :',
        'New folder in {path}:': 'Nouveau dossier dans {path} :',
        'New Folder…': 'Nouveau dossier…',
        'New name for the {kind}:': 'Nouveau nom pour {kind} :',
        'Not enough space on the Next': "Pas assez d'espace sur le Next",
        ('Only add a drive that really exists on your Next (an extra SD card reader or '
         'partition). Selecting a drive that is not mounted CRASHES the Next.'):
            ("N'ajoutez qu'un lecteur qui existe vraiment sur votre Next (un lecteur "
             'SD ou une partition supplémentaire). Sélectionner un lecteur non monté '
             'FAIT PLANTER le Next.'),
        'Open': 'Ouvrir',
        'Open in {source}': 'Ouvrir dans {source}',
        'Open: the system could not open {name}.':
            'Ouvrir : le système n’a pas pu ouvrir {name}.',
        'Open on website (zxart.ee)': 'Ouvrir sur le site (zxart.ee)',
        'Open on website (zxinfo.dk)': 'Ouvrir sur le site (zxinfo.dk)',
        'Paste': 'Coller',
        'Remote Unzip file': 'Décompresser le fichier à distance',
        'Remote Zip': 'Compresser à distance',
        'Remove from Favorites': 'Retirer des favoris',
        "Rename '{name}' to:": "Renommer '{name}' en :",
        'Rename failed': 'Échec du renommage',
        'Rename…': 'Renommer…',
        'Retrieve all issues': 'Récupérer tous les numéros',
        'Send to SD card (image)  →  {dest}': 'Envoyer vers la carte SD (image)  →  {dest}',
        'Send using NextSync  →  {dest}': 'Envoyer via NextSync  →  {dest}',
        'Set sync root': 'Définir la racine de synchronisation',
        'Set this folder as the new sync root?': 'Définir ce dossier comme nouvelle racine de synchronisation ?',
        'Size on the Next': 'Taille sur le Next',
        'The image was downloaded but could not be extracted:': "L'image a été téléchargée mais n'a pas pu être extraite :",
        "The name cannot contain '/' or '\\'.": "Le nom ne peut pas contenir '/' ni '\\'.",
        'This cannot be undone.': 'Cette action est irréversible.',
        ('This copy needs {need} bytes ({need_h}), but drive {drive}: only has {free} '
         'bytes ({free_h}) free.\n\nIt exceeds the available remote space by {over} '
         'bytes ({over_h}).\n\nThe copy was not started.'):
            ('Cette copie nécessite {need} octets ({need_h}), mais le lecteur {drive}: '
             "n'a que {free} octets ({free_h}) libres.\n\nElle dépasse l'espace "
             "distant disponible de {over} octets ({over_h}).\n\nLa copie n'a pas été "
             'lancée.'),
        'Unzip file': 'Décompresser le fichier',
        'Zip': 'Compresser',
        '… and {n} more': '… et {n} de plus',
        # ---- labels ----
        "  Background": "  Arrière-plan",
        "  Directory name": "  Nom du répertoire",
        "  Directory type label": "  Étiquette de type de répertoire",
        "  File extension": "  Extension du fichier",
        "  File name": "  Nom du fichier",
        "  File size": "  Taille du fichier",
        "  Filter: ": "  Filtre : ",
        "  General UI text": "  Texte général de l'interface",
        "  Retro logs console": "  Console de journal rétro",
        "  Up Directory item": "  Élément « dossier parent »",
        "Background image opacity (%):": "Opacité de l'image de fond (%) :",
        "Background image:": "Image de fond :",
        "CSpect default launch parameters:": "Paramètres de lancement de CSpect :",
        "Collection:": "Collection :",
        "Desktop Theme:": "Thème du bureau :",
        "Disk Image Explorer: ": "Explorateur de l'image disque : ",
        "Gallery animation:": "Animation de la galerie :",
        "Gallery image size:": "Taille des images de la galerie :",
        "Gallery items per row:": "Éléments par ligne dans la galerie :",
        "Gallery rows per page (min):": "Lignes par page de galerie (min) :",
        "Gallery search sort ordering preference:": "Ordre des résultats de recherche de la galerie :",
        "Gallery slideshow pause time:": "Pause du diaporama :",
        "Language:": "Langue :",
        "Application language:": "Langue de l'application :",
        "Local file explorers & App Text Colors:": "Explorateurs locaux et couleurs du texte :",
        "Local path: ": "Chemin local : ",
        "MAME ROM / system:": "ROM / système MAME :",
        "MAME default launch parameters:": "Paramètres de lancement de MAME :",
        "Max connections:": "Connexions max :",
        "NextSync — Automatically start Remote Explorer server on startup":
            "NextSync — Démarrer automatiquement le serveur du Remote Explorer au lancement",
        "NextSync — when a sent file or directory exists locally:":
            "NextSync — si un fichier ou dossier reçu existe déjà en local :",
        "Page:": "Page :",
        "Port:": "Port :",
        "Reset theme": "Réinitialiser le thème",
        "Retro log font size:": "Taille de police du journal rétro :",
        "Search:": "Rechercher :",
        "Search: ": "Rechercher : ",
        "View:": "Vue :",
        "itch.io API key:": "Clé API itch.io :",
        # ---- buttons ----
        "< Prev": "< Préc.",
        "Cancel": "Annuler",
        "Cancel sync": "Annuler la synchronisation",
        "Connect": "Connecter",
        "Create Directory": "Créer un répertoire",
        "Create SyncIgnore File": "Créer le fichier SyncIgnore",
        "Delete": "Supprimer",
        "Delete SyncIgnore File": "Supprimer le fichier SyncIgnore",
        "Delete SyncPoint File": "Supprimer le fichier SyncPoint",
        "Disconnect": "Déconnecter",
        "RS232 ESP emulation is already running on port {port} for another emulator; this MAME joins it. The new port applies once every MAME has exited.":
            "L'émulation RS232 ESP tourne déjà sur le port {port} pour un autre émulateur ; ce MAME la rejoint. Le nouveau port s'appliquera quand tous les MAME seront fermés.",
        "RS232 ESP emulation: {count} emulators are now sharing it (port {port}).":
            "Émulation RS232 ESP : {count} émulateurs la partagent maintenant (port {port}).",
        "RS232 ESP emulation could not start (port {port} in use?). MAME starts without it.":
            "L'émulation RS232 ESP n'a pas pu démarrer (port {port} occupé ?). MAME démarre sans elle.",
        "RS232 ESP emulation inspired by jesperl - by Janko Stamenović":
            "Émulation RS232 ESP inspirée de jesperl - par Janko Stamenović",
        "The optional RS232 ESP Emulation for MAME (Settings) is a clean full reimplementation in Python of an idea from jesperl by Janko Stamenović - an ESP-AT emulator bridging MAME's emulated Wi-Fi module to the real network. Many thanks for the inspirational idea - see https://sourceforge.net/projects/jesperl/.":
            "L'émulation RS232 ESP optionnelle pour MAME (Réglages) est une réimplémentation complète et propre en Python d'une idée de jesperl, de Janko Stamenović : un émulateur ESP-AT reliant le module Wi-Fi émulé de MAME au réseau réel. Un grand merci pour l'idée inspirante - voir https://sourceforge.net/projects/jesperl/.",
        "One RS232 ESP emulation serves every running MAME: launch a second MAME on another disk image and it joins the same emulation with its own separate session, so several emulated Nexts can be on the network at once. The emulation stops when the last MAME exits. When two of them ask for the same server port (a Next listening for incoming connections), the second one is moved to the next free port and the log says which port to connect to.":
            "Une seule émulation RS232 ESP sert tous les MAME en cours : lancez un deuxième MAME avec une autre image disque et il rejoint la même émulation avec sa propre session séparée, de sorte que plusieurs Next émulés peuvent être sur le réseau en même temps. L'émulation s'arrête à la sortie du dernier MAME. Quand deux d'entre eux demandent le même port serveur (un Next à l'écoute de connexions entrantes), le second est déplacé sur le port libre suivant et le journal indique à quel port se connecter.",
        "Transfers through the RS232 ESP emulation need the Next side on its SLOW pacing: use '.sync5 -s' for the dot, or set UART speed to Slow in ZX Next Remote's settings.":
            "Les transferts via l'émulation RS232 ESP exigent le rythme LENT côté Next : utilisez '.sync5 -s' pour le dot, ou réglez la vitesse UART sur Slow dans les réglages de ZX Next Remote.",
        "Start {emulator}": "Démarrer {emulator}",
        "Color:": "Couleur :",
        "Pick a color for this Next. It tints the machine list and this machine's tab in the session strip.":
            "Choisissez une couleur pour ce Next. Elle colore la liste des machines et l'onglet de cette machine dans la bande de sessions.",
        "Clear the color": "Effacer la couleur",
        "Switch to this Next": "Basculer sur ce Next",
        "Name and color…": "Nom et couleur…",
        "That Next is no longer on the line.":
            "Ce Next n'est plus sur la ligne.",
        "Tell this Next to leave listen mode and exit? ZX Next Remote closes its application; a '.sync5' dot returns to BASIC. The server keeps listening, so it can connect again.":
            "Demander à ce Next de quitter le mode écoute et de se fermer ? ZX Next Remote ferme son application ; un point '.sync5' revient au BASIC. Le serveur continue d'écouter, il peut donc se reconnecter.",
        "Asked the Next to leave listen mode and exit.":
            "Demande envoyée au Next : quitter le mode écoute et se fermer.",
        "Remote .sync5 update": "Mise à jour à distance de .sync5",
        "Update .sync5 on this Next ({old} → {new})…":
            "Mettre à jour .sync5 sur ce Next ({old} → {new})…",
        "Push new .sync5 to this Next…":
            "Envoyer le nouveau .sync5 sur ce Next…",
        ".sync5 version unknown — switch to this Next first":
            "Version de .sync5 inconnue — basculez d'abord sur ce Next",
        ".sync5 v{old} predates self-update — copy the new dot to the Next by hand once":
            ".sync5 v{old} est antérieur à l'auto-mise à jour — copiez une fois le nouveau dot sur le Next à la main",
        "Locating the .sync5 build to send…":
            "Localisation du .sync5 à envoyer…",
        "Still locating the .sync5 build to send — one moment.":
            "Toujours en train de localiser le .sync5 à envoyer — un instant.",
        "Could not obtain the .sync5 build to send: {reason}":
            "Impossible d'obtenir le .sync5 à envoyer : {reason}",
        ("Update .sync5 on {machine}: v{old} → v{new}.\n\nThe new dot is "
         "staged on the Next's SD card, read back and verified, then "
         "swapped in; the previous dot is kept as sync5.bak (renaming it "
         "back to sync5 is the one-step recovery). The session ends when "
         "the update completes — run {command} on the Next again "
         "afterwards.\n\nTarget directory on the Next:"):
            ("Mise à jour de .sync5 sur {machine} : v{old} → "
             "v{new}.\n\nLa nouvelle commande dot est déposée sur la "
             "carte SD du Next, relue et vérifiée, puis mise en place ; "
             "la commande dot précédente est conservée sous le nom "
             "sync5.bak (la renommer en sync5 est la récupération en une "
             "étape). La session se termine quand la mise à jour est "
             "finie — relancez ensuite {command} sur le "
             "Next.\n\nRépertoire cible sur le Next :"),
        ("Push the new .sync5 (v{new}) to {machine}?\n\nThis machine's "
         "version is unknown (an older dot, or an old ZX Next Remote "
         "build — the two cannot be told apart), and the swap itself "
         "only works when the far side is a .sync dot v5.9 or newer: on "
         "anything older the staged sync5.new is left on the card and "
         "nothing is swapped. The previous dot is kept as sync5.bak "
         "(renaming it back to sync5 is the one-step recovery). The "
         "session ends when the update completes — run {command} on the "
         "Next again afterwards.\n\nTarget directory on the Next:"):
            ("Envoyer le nouveau .sync5 (v{new}) sur {machine} ?\n\nLa "
             "version de cette machine est inconnue (une ancienne "
             "commande dot, ou une ancienne version de ZX Next Remote — "
             "impossible de les distinguer), et l'échange lui-même ne "
             "fonctionne que si l'autre côté est une commande dot .sync "
             "v5.9 ou plus récente : avec quelque chose de plus ancien, "
             "le sync5.new déposé reste sur la carte et rien n'est "
             "échangé. La commande dot précédente est conservée sous le "
             "nom sync5.bak (la renommer en sync5 est la récupération en "
             "une étape). La session se termine quand la mise à jour est "
             "finie — relancez ensuite {command} sur le "
             "Next.\n\nRépertoire cible sur le Next :"),
        "Download File": "Télécharger le fichier",
        "Download NextZXOS Image": "Télécharger l'image NextZXOS",
        "Download and install HDF Monkey": "Télécharger et installer HDF Monkey",
        "Download and install HDF Monkey and OpenAL": "Télécharger et installer HDF Monkey et OpenAL",
        "Generate": "Générer",
        "Get API key…": "Obtenir une clé API…",
        "Latest": "Nouveautés",
        "New Folder": "Nouveau dossier",
        "Next >": "Suiv. >",
        "OK": "OK",
        "Open config file": "Ouvrir le fichier de configuration",
        "Prepare Classic NextSync server": "Préparer le serveur NextSync classique",
        "Random": "Aléatoire",
        "Refresh": "Actualiser",
        "Rename": "Renommer",
        "Search": "Rechercher",
        "Select NextZXOS disk Image": "Choisir une image disque NextZXOS",
        "Set current folder as new sync root folder":
            "Définir le dossier courant comme nouvelle racine de synchronisation",
        "Up": "Monter",
        "▶ Start Classic NextSync server": "▶ Démarrer le serveur NextSync classique",
        "▶ Start Remote Explorer NextSync server":
            "▶ Démarrer le serveur NextSync de l'explorateur distant",
        "⬇  Download": "⬇  Télécharger",
        "⬇  Install MAME": "⬇  Installer MAME",
        "🎮 Retro": "🎮 Rétro",
        "💾  Send to SD card": "💾  Envoyer vers la carte SD",
        "🔁  Send via NextSync": "🔁  Envoyer via NextSync",
        "🕹  Launch CSpect": "🕹  Lancer CSpect",
        "🕹  Launch Mame": "🕹  Lancer Mame",
        "🖼 Switch to 'Classic' view mode": "🖼 Passer à la vue 'Classique'",
        # ---- emulator option combos (SD Card tab; selected by index) ----
        "Screen Size X1": "Taille d'écran X1",
        "Screen Size X2": "Taille d'écran X2",
        "Screen Size X3": "Taille d'écran X3",
        "Screen Size X4": "Taille d'écran X4",
        "Fullscreen": "Plein écran",
        "Sound On": "Son activé",
        "Sound Off": "Son désactivé",
        "Sound WASAPI": "Son WASAPI",
        "Sound XAudio2": "Son XAudio2",
        "Sound PortAudio": "Son PortAudio",
        "VSync On": "VSync activée",
        "VSync Off": "VSync désactivée",
        "Joystick On": "Joystick activé",
        "Joystick Off": "Joystick désactivé",
        "Mouse On": "Souris activée",
        "Mouse Off": "Souris désactivée",
        "Disable ESC Key Off": "Désactiver la touche ÉCHAP : non",
        "Disable ESC Key On": "Désactiver la touche ÉCHAP : oui",
        "ZX Next Unite update check: could not parse the versions (latest tag {tag}); skipping.":
            "Vérification de ZX Next Unite : impossible d'analyser les versions (dernière étiquette {tag}) ; ignorée.",
        "ZX Next Unite {latest} is available, but the release has no package for this platform — opening the releases page instead.":
            "ZX Next Unite {latest} est disponible, mais la version ne contient aucun paquet pour cette plateforme — la page des versions va s'ouvrir.",
        # ---- long guidance prompts (final) ----
        "A newer version of CSpect is available on itch.io.\n\nInstalled: {installed}\nLatest: {latest}\n\nDownload and install the newest version now?":
            "Une version plus récente de CSpect est disponible sur itch.io.\n\nInstallée : {installed}\nDernière : {latest}\n\nTélécharger et installer la plus récente maintenant ?",
        "CSpect update ▸ SUCCESS — {name} extracted to: {path}":
            "Mise à jour de CSpect ▸ RÉUSSIE — {name} extrait dans : {path}",
        "CSpect update ▸ Starting download + install of {name} ({file}) from itch.io into {folder}.":
            "Mise à jour de CSpect ▸ démarrage du téléchargement et de l'installation de {name} ({file}) depuis itch.io vers {folder}.",
        "ERROR: could not build {name}: {error}":
            "ERREUR : impossible de créer {name} : {error}",
        "MAME can't start: the ZX Spectrum Next boot ROM (TBBLUE) is missing. This step is manual — see {url} and follow \"Get TBBLUE (the Next 'boot ROM')\". Put the file tbblue.zip into MAME's roms folder (downloads\\mame\\roms) — DON'T extract it — and try again. You must provide a legally acquired, licensed ROM.":
            "MAME ne peut pas démarrer : la ROM de démarrage du ZX Spectrum Next (TBBLUE) est absente. Cette étape est manuelle — voir {url} et suivre \"Get TBBLUE (the Next 'boot ROM')\". Placez le fichier tbblue.zip dans le dossier roms de MAME (downloads\\mame\\roms) — NE l'extrayez PAS — puis réessayez. Vous devez fournir une ROM acquise légalement et sous licence.",
        "MAME install ▸ NEXT STEP (manual): add the TBBLUE boot ROM. See {url} → \"Get TBBLUE (the Next 'boot ROM')\". Put the file tbblue.zip into MAME's roms folder ({roms}) — DON'T extract it. You must provide a legally acquired, licensed ROM.":
            "Installation de MAME ▸ ÉTAPE SUIVANTE (manuelle) : ajoutez la ROM de démarrage TBBLUE. Voir {url} → \"Get TBBLUE (the Next 'boot ROM')\". Placez le fichier tbblue.zip dans le dossier roms de MAME ({roms}) — NE l'extrayez PAS. Vous devez fournir une ROM acquise légalement et sous licence.",
        "NextSync .sync5 dot command updated: v{old} -> v{new} — please copy the new build to your Next (it cannot be deployed automatically).":
            "Commande dot .sync5 de NextSync mise à jour : v{old} -> v{new} — copiez la nouvelle version sur votre Next (le déploiement automatique est impossible).",
        "TIP: Did you know that if you have purchased CSpect from itch.io you can do a full end-to-end CSpect install from there?\n\nCSpect ships with hdfmonkey bundled inside it, so that route needs no separate hdfmonkey install — the app finds and uses the bundled copy automatically.\n\nSimply log into your itch.io account in the itch.io tab, navigate to CSpect and click Install.\n\nDo you still want to install hdfmonkey only, or abort and then make an end-to-end install of CSpect using itch.io?":
            "ASTUCE : saviez-vous que si vous avez acheté CSpect sur itch.io, vous pouvez y faire une installation complète de CSpect ?\n\nCSpect embarque hdfmonkey, cette voie ne nécessite donc aucune installation séparée de hdfmonkey — l'application trouve et utilise automatiquement la copie embarquée.\n\nConnectez-vous à votre compte itch.io dans l'onglet itch.io, allez sur CSpect et cliquez sur Installer.\n\nVoulez-vous quand même installer uniquement hdfmonkey, ou annuler et faire l'installation complète de CSpect via itch.io ?",
        "The automatic hdfmonkey download from specnext.com failed — the forum may be asking for a login or an anti-robot confirmation before the download can start (see the log for details).\n\nYou can install it manually instead:\n1. Click 'Open download page' below (or browse to\n    {url} ).\n2. Download the hdfmonkey .zip file.\n3. Drop the downloaded .zip into this EXACT folder — the app has already created it, and the 'Open downloads folder' button below opens it so nothing needs to be typed:\n    {folder}\n4. Click \"I've dropped the zip - try again\".":
            "Le téléchargement automatique de hdfmonkey depuis specnext.com a échoué — le forum demande peut-être une connexion ou une confirmation anti-robot avant de lancer le téléchargement (voir le journal pour les détails).\n\nVous pouvez l'installer manuellement :\n1. Cliquez sur 'Ouvrir la page de téléchargement' ci-dessous (ou ouvrez\n    {url} ).\n2. Téléchargez le fichier .zip de hdfmonkey.\n3. Déposez le .zip téléchargé EXACTEMENT dans ce dossier — l'application l'a déjà créé, et le bouton 'Ouvrir le dossier de téléchargement' ci-dessous l'ouvre pour éviter toute saisie :\n    {folder}\n4. Cliquez sur \"J'ai déposé le zip - réessayer\".",
        "ZX Next Unite update: downloaded {name} to {folder}":
            "Mise à jour de ZX Next Unite : {name} téléchargé dans {folder}",
        "ZX Next Unite update: downloaded {path} but could not unpack it: {error}":
            "Mise à jour de ZX Next Unite : {path} téléchargé mais impossible à décompresser : {error}",
        "ZX Next Unite {latest} is available (you are running {installed}).\n\nYou appear to be running from source (git clone), so the\nrecommended way to update is:\n\n    git pull\n\ninstead of downloading the Windows binary.":
            "ZX Next Unite {latest} est disponible (vous utilisez {installed}).\n\nVous semblez l'exécuter depuis les sources (git clone), la\nméthode de mise à jour recommandée est donc :\n\n    git pull\n\nplutôt que de télécharger le binaire Windows.",
        "ZX Next Unite {latest} is available — download?\n\nInstalled: {installed}\nLatest: {latest}\nPackage: {asset} (~{size})\n\nThe new version is saved next to the current one — you choose\nwhen to switch (you'll be offered a restart after the download).":
            "ZX Next Unite {latest} est disponible — télécharger ?\n\nInstallée : {installed}\nDernière : {latest}\nPaquet : {asset} (~{size})\n\nLa nouvelle version est enregistrée à côté de l'actuelle — vous choisissez\nquand basculer (un redémarrage vous sera proposé après le téléchargement).",
        "ZX Next Unite {latest} is available — running from source, so update with 'git pull' instead of the Windows binary.":
            "ZX Next Unite {latest} est disponible — exécution depuis les sources, mettez à jour avec 'git pull' plutôt que le binaire Windows.",
        # ---- emulator update prompts (bodies + buttons) ----
        "A newer version of MAME is available.\n\nInstalled: 0.{installed}\nLatest: {latest}  (0.{latest_num})\nPackage: {asset}\n\nDownload (~{size}) and update your MAME install now?\nThe existing files in the downloads MAME folder will be overwritten.":
            "Une version plus récente de MAME est disponible.\n\nInstallée : 0.{installed}\nDernière : {latest}  (0.{latest_num})\nPaquet : {asset}\n\nTélécharger (~{size}) et mettre à jour votre installation MAME maintenant ?\nLes fichiers existants du dossier MAME seront écrasés.",
        "Close and start {name}":
            "Fermer et lancer {name}",
        "Continue hdfmonkey standalone install":
            "Continuer l'installation autonome de hdfmonkey",
        "I've dropped the zip - try again":
            "J'ai déposé le zip - réessayer",
        "MAME release: {tag}\nPackage: {asset} ({arch})\n\nDownload (~{size}) and install it into the downloads folder?\nNote: the fully extracted install is large (~500 MB).":
            "Version de MAME : {tag}\nPaquet : {asset} ({arch})\n\nTélécharger (~{size}) et l'installer dans le dossier de téléchargement ?\nNote : l'installation complète est volumineuse (~500 Mo).",
        "Open download page":
            "Ouvrir la page de téléchargement",
        "Open downloads folder":
            "Ouvrir le dossier de téléchargement",
        "The new version was saved as:\n\n{path}\n\nClose ZX Next Unite now and start the new version ({name})?\nYour settings (hdfg.cfg) and downloads are picked up as-is —\nboth versions run from the same folder.":
            "La nouvelle version a été enregistrée sous :\n\n{path}\n\nFermer ZX Next Unite maintenant et lancer la nouvelle version ({name}) ?\nVos réglages (hdfg.cfg) et téléchargements sont repris tels quels —\nles deux versions s'exécutent depuis le même dossier.",
        "What's changed:":
            "Nouveautés :",
        # ---- emulator / config console (final batch) ----
        "CSpect update check: {reason}.":
            "Vérification de CSpect : {reason}.",
        "CSpect update ▸ FAILED — {error}":
            "Mise à jour de CSpect ▸ ÉCHEC — {error}",
        "CSpect update ▸ newer build available: installed {installed}, latest {latest}.":
            "Mise à jour de CSpect ▸ build plus récent disponible : installé {installed}, dernier {latest}.",
        "CSpect update ▸ user chose to update to {name}.":
            "Mise à jour de CSpect ▸ l'utilisateur a choisi de passer à {name}.",
        "Could not list the MAME releases: {error}":
            "Impossible de lister les versions de MAME : {error}",
        "ERROR: Failed to launch MAME: {error}":
            "ERREUR : impossible de lancer MAME : {error}",
        "ERROR: Failed to launch CSpect: {error}":
            "ERREUR : Impossible de lancer CSpect : {error}",
        "ERROR: could not extract {name}: {error}":
            "ERREUR : impossible d'extraire {name} : {error}",
        "ERROR: hdfmonkey failed - A file can't be opened this is commonly caused by strange characters such as quotes and signs":
            "ERREUR : échec de hdfmonkey - impossible d'ouvrir un fichier ; cela vient généralement de caractères inhabituels comme les guillemets et les signes",
        "ERROR: hdfmonkey failed - A file can't be opened: {command} this is commonly caused by strange characters such as quotes and signs":
            "ERREUR : échec de hdfmonkey - impossible d'ouvrir un fichier : {command} ; cela vient généralement de caractères inhabituels comme les guillemets et les signes",
        "Failed to save configuration file with IOError: {error}":
            "Échec de l'enregistrement du fichier de configuration (IOError) : {error}",
        "Found hdfmonkey alongside CSpect: {path}":
            "hdfmonkey trouvé à côté de CSpect : {path}",
        "MAME exited with code {code}.":
            "MAME s'est terminé avec le code {code}.",
        "MAME install ▸ SUCCESS — MAME detected at: {path}":
            "Installation de MAME ▸ RÉUSSIE — MAME détecté dans : {path}",
        "Pygame mode unavailable — run: pip install pygame-ce":
            "Mode pygame indisponible — lancez : pip install pygame-ce",
        "Remote unzip: fetching {path} from the image …":
            "Décompression distante : récupération de {path} depuis l'image …",
        "Remote zip: fetching {count} item(s) from the image …":
            "Compression distante : récupération de {count} élément(s) depuis l'image …",
        "Saved configuration file.":
            "Fichier de configuration enregistré.",
        "UI language set to '{lang}' to match the system language — change it on the Settings tab.":
            "Langue de l'interface réglée sur '{lang}' pour correspondre au système — modifiable dans l'onglet Réglages.",
        "ZX Next Unite update available: {latest} (installed {installed}).":
            "Mise à jour de ZX Next Unite disponible : {latest} (installée {installed}).",
        "ZX Next Unite update ▸ downloading {asset}…":
            "Mise à jour de ZX Next Unite ▸ téléchargement de {asset}…",
        "ZX Next Unite update: could not start {name}: {error}":
            "Mise à jour de ZX Next Unite : impossible de lancer {name} : {error}",
        "ZX Next Unite update: download FAILED: {error}":
            "Mise à jour de ZX Next Unite : le téléchargement a ÉCHOUÉ : {error}",
        "ZX Next Unite update: downloaded — start it any time: {path}":
            "Mise à jour de ZX Next Unite : téléchargée — lancez-la quand vous voulez : {path}",
        "ZX Next Unite update: starting {name} and closing…":
            "Mise à jour de ZX Next Unite : lancement de {name} et fermeture…",
        "ZX Next Unite update: unpacked to {path}":
            "Mise à jour de ZX Next Unite : décompressée dans {path}",
        "Extracting {name} from the image, then starting CSpect…":
            "Extraction de {name} depuis l'image, puis lancement de CSpect…",
        "Start CSpect: {name} could not be read from the image, CSpect was not started.":
            "Lancer CSpect : impossible de lire {name} depuis l'image, CSpect n'a pas été lancé.",
        "Extracting {name} from the image, then sending it via NextSync…":
            "Extraction de {name} depuis l'image, puis envoi via NextSync…",
        "Send via NextSync: {name} could not be read from the image, nothing was sent.":
            "Envoi via NextSync : impossible de lire {name} depuis l'image, rien n'a été envoyé.",
        "Send via NextSync {name}":
            "Envoyer {name} via NextSync",
        "Start NextSync Remote Explorer":
            "Démarrer le Remote Explorer NextSync",
        "Stop NextSync Remote Explorer":
            "Arrêter le Remote Explorer NextSync",
        "Please select a sync root first on the NextSync Remote Explorer tab and retry.":
            "Choisissez d'abord un dossier racine de synchronisation dans l'onglet Remote Explorer NextSync, puis réessayez.",
        # ---- CSpect auto-start actions (SD Card tab menus) ----
        "Send to SD Card and start CSpect with file {name}":
            "Envoyer vers la carte SD et lancer CSpect avec le fichier {name}",
        "Send to SD Card and start CSpect: the transfer failed, CSpect was not started.":
            "Envoyer vers la carte SD et lancer CSpect : le transfert a échoué, CSpect n'a pas été lancé.",
        "Sending {name} to the SD card image, then starting CSpect…":
            "Envoi de {name} vers l'image de la carte SD, puis lancement de CSpect…",
        "Start CSpect with file {name}":
            "Lancer CSpect avec le fichier {name}",
        "Start MAME with file {name}":
            "Lancer MAME avec le fichier {name}",
        "Could not start {emulator}":
            "Impossible de lancer {emulator}",
        "Could not prepare a folder for {name}: {error}":
            "Impossible de préparer un dossier pour {name} : {error}",
        "Start {emulator}: {name} could not be downloaded from the Next, "
        "{emulator} was not started.":
            "Lancer {emulator} : impossible de télécharger {name} depuis le "
            "Next, {emulator} n'a pas été lancé.",
        "Downloading {name} from the Next, then starting {emulator}…":
            "Téléchargement de {name} depuis le Next, puis lancement de "
            "{emulator}…",
        "Downloading {name}…":
            "Téléchargement de {name}…",
        "Start MAME: could not prepare the staging folder {path} ({error}).":
            "Lancer MAME : impossible de préparer le dossier temporaire {path} ({error}).",
        "Send to SD Card and start MAME with file {name}":
            "Envoyer vers la carte SD et lancer MAME avec le fichier {name}",
        "Extracting {name} from the image, then starting MAME…":
            "Extraction de {name} depuis l'image, puis lancement de MAME…",
        "Start MAME: {name} could not be read from the image, MAME was not started.":
            "Lancer MAME : impossible de lire {name} depuis l'image, MAME n'a pas été lancé.",
        "Send to SD Card and start MAME: the transfer failed, MAME was not started.":
            "Envoyer vers la carte SD et lancer MAME : le transfert a échoué, MAME n'a pas été lancé.",
        "Sending {name} to the SD card image, then starting MAME…":
            "Envoi de {name} vers l'image de la carte SD, puis lancement de MAME…",
        "MAME cannot load {name} directly; starting MAME without it.":
            "MAME ne peut pas charger {name} directement ; MAME sera lancé sans ce fichier.",
        # ---- dialogs (message boxes) ----
        "CSpect update available":
            "Mise à jour de CSpect disponible",
        "Choose another release…":
            "Choisir une autre version…",
        "Close":
            "Fermer",
        "Download and install":
            "Télécharger et installer",
        "File or directory already exists locally.":
            "Le fichier ou dossier existe déjà en local.",
        "File or directory exists":
            "Le fichier ou dossier existe",
        "Ignore (always in this sync)":
            "Ignorer (toujours dans cette synchronisation)",
        "Ignore (one time)":
            "Ignorer (une fois)",
        "Install from .zip…":
            "Installer depuis un .zip…",
        "Install hdfmonkey":
            "Installer hdfmonkey",
        "Later":
            "Plus tard",
        "MAME update available":
            "Mise à jour de MAME disponible",
        "Open itch.io page":
            "Ouvrir la page itch.io",
        "Open the releases page":
            "Ouvrir la page des versions",
        "Overwrite local file (always in this sync)":
            "Écraser le fichier local (toujours dans cette synchronisation)",
        "Overwrite local file (one time)":
            "Écraser le fichier local (une fois)",
        "The automated download failed.":
            "Le téléchargement automatique a échoué.",
        "This is going to completely delete the files in {path} and its sub folders, so they will be unrecoverable.\n\nAre you sure want to continue?":
            "Cela va supprimer définitivement les fichiers de {path} et de ses sous-dossiers, sans récupération possible.\n\nVoulez-vous vraiment continuer ?",
        "Tip: set a default for this in Settings → \"NextSync — when a sent file or directory exists locally\".":
            "Astuce : définissez une valeur par défaut dans Réglages → \"NextSync — when a sent file or directory exists locally\".",
        "Uninstall":
            "Désinstaller",
        "Update":
            "Mettre à jour",
        "Update downloaded":
            "Mise à jour téléchargée",
        "Yes":
            "Oui",
        "You can download it manually from the itch.io page in your browser, then install it from the downloaded .zip.":
            "Vous pouvez le télécharger manuellement depuis la page itch.io dans votre navigateur, puis l'installer depuis le .zip téléchargé.",
        "ZX Next Unite update available":
            "Mise à jour de ZX Next Unite disponible",
        "hdfmonkey download failed":
            "Échec du téléchargement de hdfmonkey",
        "itch.io download":
            "Téléchargement itch.io",
        # ---- emulator install / update console ----
        "CSpect update check skipped: {reason}":
            "Vérification de CSpect ignorée : {reason}",
        "CSpect update ▸ user cancelled the update.":
            "Mise à jour de CSpect ▸ l'utilisateur a annulé la mise à jour.",
        "ERROR: CSpect.exe is not present in the same local directory as zx-next-unite. Please install it from http://cspect.org":
            "ERREUR : CSpect.exe n'est pas dans le même dossier local que zx-next-unite. Installez-le depuis http://cspect.org",
        "ERROR: MAME executable not found on PATH. Cannot launch MAME.":
            "ERREUR : exécutable MAME introuvable dans le PATH. Impossible de lancer MAME.",
        "Listing the available MAME releases…":
            "Liste des versions de MAME disponibles…",
        "MAME install ▸ FAILED — the download and extraction finished, but no mame.exe could be found in downloads/mame.":
            "Installation de MAME ▸ ÉCHEC — le téléchargement et l'extraction sont terminés, mais aucun mame.exe n'a été trouvé dans downloads/mame.",
        "MAME install ▸ FAILED — {error}. You can download it manually from https://www.mamedev.org/release.html":
            "Installation de MAME ▸ ÉCHEC — {error}. Vous pouvez le télécharger manuellement depuis https://www.mamedev.org/release.html",
        "MAME install ▸ Starting: {tag} ({asset}, ~{size}).":
            "Installation de MAME ▸ démarrage : {tag} ({asset}, ~{size}).",
        "MAME install ▸ release picker cancelled.":
            "Installation de MAME ▸ choix de version annulé.",
        "MAME is ready to launch now — no restart needed. Use the '🕹  Launch Mame' button.":
            "MAME peut être lancé maintenant — aucun redémarrage nécessaire. Utilisez le bouton '🕹  Launch Mame'.",
        "MAME update check: could not determine the installed MAME version; skipping.":
            "Vérification de MAME : impossible de déterminer la version installée ; ignorée.",
        "MAME update check: could not determine the latest release; skipping.":
            "Vérification de MAME : impossible de déterminer la dernière version ; ignorée.",
        "MAME update check: could not reach the release site; skipping.":
            "Vérification de MAME : site des versions inaccessible ; ignorée.",
        "MAME update ▸ user chose to pick a release manually.":
            "Mise à jour de MAME ▸ l'utilisateur a choisi de sélectionner une version manuellement.",
        "MAME update ▸ user chose to update to {tag}.":
            "Mise à jour de MAME ▸ l'utilisateur a choisi de passer à {tag}.",
        "On MacOS and Linux mono is required as it runs under it. Please make sure mono is installed.":
            "Sur MacOS et Linux, mono est requis car l'exécution se fait via lui. Vérifiez que mono est installé.",
        "Running as a Flatpak: mono must be installed on the HOST system — the launch is delegated there via flatpak-spawn.":
            "Exécution en Flatpak : mono doit être installé sur le système HÔTE — le lancement y est délégué via flatpak-spawn.",
        "Select a valid ZX Spectrum Next disk image (.img/.hdf) before launching MAME.":
            "Sélectionnez une image disque ZX Spectrum Next valide (.img/.hdf) avant de lancer MAME.",
        "ZX Next Unite update check: could not reach GitHub (offline, or no release published yet); skipping.":
            "Vérification de ZX Next Unite : GitHub inaccessible (hors ligne, ou aucune version publiée) ; ignorée.",
        "ZX Next Unite update check: running as a Flatpak — updates come from your software center, skipping.":
            "Vérification de ZX Next Unite : exécution en Flatpak — les mises à jour viennent de votre logithèque, ignorée.",
        "ZX Next Unite update ▸ skipped by user.":
            "Mise à jour de ZX Next Unite ▸ ignorée par l'utilisateur.",
        "ZX Next Unite update: download cancelled.":
            "Mise à jour de ZX Next Unite : téléchargement annulé.",
        # ---- SD Card tab: console + dialogs ----
        "Confirm Deletion":
            "Confirmer la suppression",
        "Create":
            "Créer",
        "Create New Folder":
            "Créer un nouveau dossier",
        "Created {name} in {folder} on the image ({count} file(s), {bytes} bytes).":
            "{name} créé dans {folder} sur l'image ({count} fichier(s), {bytes} octets).",
        "Delete files from the image to free space, or switch to a larger image.\nLarger SD card images can be downloaded from:":
            "Supprimez des fichiers de l'image pour libérer de l'espace, ou passez à une image plus grande.\nDes images de carte SD plus grandes peuvent être téléchargées depuis :",
        "Download":
            "Télécharger",
        "Download failed: no valid destination folder.":
            "Échec du téléchargement : aucun dossier de destination valide.",
        "Downloading {name} from {url}":
            "Téléchargement de {name} depuis {url}",
        "ERROR: hdfmonkey could not be found. Use the 'Download and install HDF Monkey' button (bottom right of the SD Card tab) to install it automatically, or do a full CSpect install from the itch.io tab, which also bundles hdfmonkey. It can also be installed manually from https://github.com/gasman/hdfmonkey — restart the app once installed.":
            "ERREUR : hdfmonkey est introuvable. Utilisez le bouton 'Télécharger et installer HDF Monkey' (en bas à droite de l'onglet SD Card) pour l'installer automatiquement, ou faites une installation complète de CSpect depuis l'onglet itch.io, qui inclut aussi hdfmonkey. Il peut également être installé manuellement depuis https://github.com/gasman/hdfmonkey — redémarrez l'application une fois installé.",
        "Extracted disk image: {path}":
            "Image disque extraite : {path}",
        "Extracted {count} file(s) from {name} into {folder} on the image.":
            "{count} fichier(s) extrait(s) de {name} vers {folder} sur l'image.",
        "Extracting image... %p%":
            "Extraction de l'image... %p%",
        "Failed downloading NextZXOS image: {error}":
            "Échec du téléchargement de l'image NextZXOS : {error}",
        "Load Failed":
            "Échec du chargement",
        "The image was extracted but could not be loaded:":
            "L'image a été extraite mais n'a pas pu être chargée :",
        "Failed executing hdfmonkey, please make sure it is installed in the same local directory as zx-next-unite.":
            "Échec de l'exécution de hdfmonkey ; vérifiez qu'il est installé dans le même dossier local que zx-next-unite.",
        "Failed extracting NextZXOS image: {error}":
            "Échec de l'extraction de l'image NextZXOS : {error}",
        "Failed loading image: {path}.":
            "Échec du chargement de l'image : {path}.",
        "No SD-card disk image selected — pick or create a .img/.hdf at the top of this tab to unlock the emulator Launch buttons.":
            "Aucune image de carte SD sélectionnée — choisissez ou créez un .img/.hdf en haut de cet onglet pour débloquer les boutons de lancement de l'émulateur.",
        "Nothing to move: items are already in this folder.":
            "Rien à déplacer : les éléments sont déjà dans ce dossier.",
        "Only {free} MB free out of {total} MB ({used} % used, {pct} % free).":
            "Seulement {free} Mo libres sur {total} Mo ({used} % utilisé, {pct} % libre).",
        "Please load an image file first !":
            "Chargez d'abord un fichier image !",
        "Please load an image first!":
            "Chargez d'abord une image !",
        "Please select an image file or folder first to delete!":
            "Sélectionnez d'abord un fichier ou dossier de l'image à supprimer !",
        "Please select an image file or folder first to rename!":
            "Sélectionnez d'abord un fichier ou dossier de l'image à renommer !",
        "Remote unzip cancelled — the image is unchanged.":
            "Décompression distante annulée — l'image est inchangée.",
        "Remote unzip: download from the image failed or was cancelled — the image is unchanged.":
            "Décompression distante : le téléchargement depuis l'image a échoué ou a été annulé — l'image est inchangée.",
        "Remote unzip: upload into the image failed or was cancelled.":
            "Décompression distante : l'envoi vers l'image a échoué ou a été annulé.",
        "Remote zip cancelled — no zip was created.":
            "Compression distante annulée — aucun zip n'a été créé.",
        "Remote zip: download from the image failed or was cancelled — no zip was created.":
            "Compression distante : le téléchargement depuis l'image a échoué ou a été annulé — aucun zip n'a été créé.",
        "Remote zip: upload into the image failed or was cancelled.":
            "Compression distante : l'envoi vers l'image a échoué ou a été annulé.",
        "SD Image Nearly Full":
            "Image SD presque pleine",
        "The SD card image is nearly full.":
            "L'image de la carte SD est presque pleine.",
        "The hdfmonkey provided by the CSpect itch.io package is not executable. Make it executable by running:":
            "Le hdfmonkey fourni par le paquet CSpect d'itch.io n'est pas exécutable. Rendez-le exécutable en lançant :",
        "The image is completely full ({total} MB capacity, 0 MB free).":
            "L'image est totalement pleine (capacité {total} Mo, 0 Mo libre).",
        # ---- SD Card console: banner, detection, update checks ----
        "CSpect - by Mike Dailly http://cspect.org":
            "CSpect - par Mike Dailly http://cspect.org",
        "CSpect is up to date (installed {installed}, latest {latest}).":
            "CSpect est à jour (installée {installed}, dernière {latest}).",
        "Checking for a newer MAME release…":
            "Recherche d'une version plus récente de MAME…",
        "Checking for a newer ZX Next Unite release on GitHub…":
            "Recherche sur GitHub d'une version plus récente de ZX Next Unite…",
        "Checking itch.io for a newer CSpect release…":
            "Recherche sur itch.io d'une version plus récente de CSpect…",
        "HDF Monkey - by Matt Westcott":
            "HDF Monkey - par Matt Westcott",
        "Inspired by HDFM-GOOEY - by em00k":
            "Inspiré de HDFM-GOOEY - par em00k",
        "Loaded configuration file.":
            "Fichier de configuration chargé.",
        "MAME - ZX Spectrum Next support by Holub https://wiki.specnext.dev/MAME:Installing":
            "MAME - prise en charge du ZX Spectrum Next par Holub https://wiki.specnext.dev/MAME:Installing",
        "MAME is up-to-date (installed 0.{installed}, latest 0.{latest}).":
            "MAME est à jour (installée 0.{installed}, dernière 0.{latest}).",
        "MAME is up-to-date with a patched version (installed 0.{installed}, latest 0.{latest}).":
            "MAME est à jour avec une version modifiée (installée 0.{installed}, dernière 0.{latest}).",
        "MAME version: {version}":
            "Version de MAME : {version}",
        "NextSync - by Jari Komppa and Julien Clauzel":
            "NextSync - par Jari Komppa et Julien Clauzel",
        "Using CSpect under downloads/cspect: {path}":
            "CSpect utilisé depuis downloads/cspect : {path}",
        "Using MAME under: {path}":
            "MAME utilisé depuis : {path}",
        "Using hdfmonkey bundled with CSpect: {path}":
            "hdfmonkey fourni avec CSpect utilisé : {path}",
        "Welcome to ZX Next Unite {version}":
            "Bienvenue dans ZX Next Unite {version}",
        "ZX Next Unite is up to date (installed {installed}, latest {latest}).":
            "ZX Next Unite est à jour (installée {installed}, dernière {latest}).",
        "zx-next-unite - by Julien Clauzel 2024":
            "zx-next-unite - par Julien Clauzel 2024",
        "No image loaded": "Aucune image chargée",
        # ---- itch.io item viewer + web-link labels ----
        "About": "À propos",
        "Open on {site}": "Ouvrir sur {site}",
        "Open {url}": "Ouvrir {url}",
        "✓  Re-install": "✓  Réinstaller",
        "⬇  Install": "⬇  Installer",
        "⬇  Installing…": "⬇  Installation…",
        "📂  Open download folder": "📂  Ouvrir le dossier de téléchargement",
        # ---- gallery item viewer (Classic + Retro) ----
        "🌐  Open on website": "🌐  Ouvrir sur le site",
        "🌐  Open on {site}": "🌐  Ouvrir sur {site}",
        "📂  Open install folder": "📂  Ouvrir le dossier d'installation",
        "🗑  Uninstall": "🗑  Désinstaller",
        # ---- NextSync log lines (user-facing; protocol diagnostics stay English) ----
        "(-send saves received files under: {folder})":
            "(-send enregistre les fichiers reçus dans : {folder})",
        "Aliases:":
            "Alias :",
        "Cancel requested — stopping after current file":
            "Annulation demandée — arrêt après le fichier en cours",
        "Cannot create {path}: {error}":
            "Impossible de créer {path} : {error}",
        "Closing connection":
            "Fermeture de la connexion",
        "Connected by {address} port {port}":
            "Connecté depuis {address} port {port}",
        "Disconnected":
            "Déconnecté",
        "Existing-file policy: {policy} (change in Settings -> 'NextSync - when a sent file or directory exists locally').":
            "Règle pour les fichiers existants : {policy} (modifiable dans Réglages -> 'NextSync - when a sent file or directory exists locally').",
        "Failed to rename {path}: {error}":
            "Échec du renommage de {path} : {error}",
        "IP addresses:":
            "Adresses IP :",
        "Import failed: no valid destination folder.":
            "Échec de l'import : aucun dossier de destination valide.",
        "Navigate to a folder in the left local file explorer, press 'Set current folder as new sync root folder' to choose a sync root and then press the 'Start Classic NextSync server' button.":
            "Accédez à un dossier dans l'explorateur local de gauche, appuyez sur 'Set current folder as new sync root folder' pour choisir la racine de synchronisation, puis sur le bouton 'Start Classic NextSync server'.",
        "NextSync HTTP bridge NOT started: {error}":
            "Passerelle HTTP NextSync NON démarrée : {error}",
        "NextSync HTTP bridge listening on port {port}":
            "Passerelle HTTP NextSync à l'écoute sur le port {port}",
        "NextSync HTTP bridge stopped.":
            "Passerelle HTTP NextSync arrêtée.",
        "NextSync HTTP bridge: bearer-token protection is ON (requests must carry the {header} header; others get HTTP 401)":
            "Passerelle HTTP NextSync : la protection par jeton est ACTIVÉE (les requêtes doivent porter l'en-tête {header} ; les autres reçoivent HTTP 401)",
        "NextSync is already running — please wait for it to finish.":
            "NextSync est déjà en cours — attendez la fin.",
        "NextSync listening to port {port}":
            "NextSync écoute sur le port {port}",
        "NextSync server, protocol version: {version}":
            "Serveur NextSync, version du protocole : {version}",
        "No network detected - connect to Wi-Fi/Ethernet to see the address your Next should sync to.":
            "Aucun réseau détecté — connectez-vous au Wi-Fi/Ethernet pour voir l'adresse avec laquelle votre Next doit se synchroniser.",
        "Note":
            "Note",
        "Nothing (more) to sync":
            "Plus rien à synchroniser",
        "Now run one of these commands on your Next:":
            "Exécutez maintenant l'une de ces commandes sur votre Next :",
        "Primary IP:":
            "IP principale :",
        "Received {name} ({bytes} bytes)":
            "Reçu {name} ({bytes} octets)",
        "Receiving files from the Next...":
            "Réception des fichiers depuis le Next...",
        "Receiving: {name} -> {path}":
            "Réception : {name} -> {path}",
        "Remote .sync5 update failed while reading {path}: {error} — nothing was sent.":
            "La mise à jour distante de .sync5 a échoué à la lecture de {path} : {error} — rien n'a été envoyé.",
        "Remote .sync5 update refused: {path} does not carry the expected 'NextSync {version}' banner — wrong or stale file.":
            "Mise à jour distante de .sync5 refusée : {path} ne porte pas la bannière 'NextSync {version}' attendue — fichier erroné ou périmé.",
        "Remote .sync5 update: staging {path} ({size} bytes)…":
            "Mise à jour distante de .sync5 : envoi de {path} ({size} octets)…",
        "Remote .sync5 update: staged copy verified ({size} bytes) — swapping it in…":
            "Mise à jour distante de .sync5 : copie transférée vérifiée ({size} octets) — remplacement en cours…",
        "Remote .sync5 update FAILED mid-swap: the Next may be missing {target}. If .sync5 no longer starts, rename {backup} back to sync5 in the NextZXOS Browser (the staged {staged} can be deleted).":
            "Mise à jour distante de .sync5 ÉCHOUÉE en plein remplacement : le Next n'a peut-être plus de {target}. Si .sync5 ne démarre plus, renommez {backup} en sync5 dans le Browser de NextZXOS (le {staged} transféré peut être supprimé).",
        "Remote .sync5 update complete: {version} is on the card. The session will now close — run {command} on the Next to start the new dot.":
            "Mise à jour distante de .sync5 terminée : {version} est sur la carte. La session va maintenant se fermer — lancez {command} sur le Next pour démarrer la nouvelle commande dot.",
        "Remote .sync5 update failed: {reason}. Nothing was swapped — the Next still runs its current dot.":
            "Mise à jour distante de .sync5 échouée : {reason}. Rien n'a été remplacé — le Next exécute toujours sa commande dot actuelle.",
        "Remote explorer: connected to {address}":
            "Explorateur distant : connecté à {address}",
        "Remote explorer: connection error from the Next ({error}) — session over.":
            "Explorateur distant : erreur de connexion avec le Next ({error}) — session terminée.",
        "Remote explorer: the Next closed the connection.":
            "Explorateur distant : le Next a fermé la connexion.",
        "Remote explorer: no word from the Next for {seconds}s — assuming it is gone (powered off? Wi-Fi dropped?)":
            "Explorateur distant : plus de nouvelles du Next depuis {seconds}s — considéré comme perdu (éteint ? Wi-Fi coupé ?)",
        "Remote explorer: turned away a second Next at {address} — a session is already active (Busy).":
            "Explorateur distant : un second Next depuis {address} a été refusé — une session est déjà active (Busy).",
        "Remote explorer: server keeps running in the background — stop it from the Remote Explorer view.":
            "Explorateur distant : le serveur continue de tourner en arrière-plan — arrêtez-le depuis la vue Remote Explorer.",
        "Remote explorer: navigate to a folder in the left file explorer, press 'Set current folder as new sync root folder', click 'Start Remote Explorer NextSync server', then run {command} on your Next.":
            "Explorateur distant : accédez à un dossier dans l'explorateur de gauche, appuyez sur 'Set current folder as new sync root folder', cliquez sur 'Start Remote Explorer NextSync server', puis exécutez {command} sur votre Next.",
        "Remote explorer: port {port} is already in use — is another ZX-Next-Unite (or NextSync server) already running?":
            "Explorateur distant : le port {port} est déjà utilisé — un autre ZX-Next-Unite (ou serveur NextSync) tourne-t-il déjà ?",
        "Remote explorer: the Next disconnected (BREAK / Bye) — restarting the listen server; run {command} on your Next to reconnect.":
            "Explorateur distant : le Next s'est déconnecté (BREAK / Bye) — redémarrage du serveur d'écoute ; exécutez {command} sur votre Next pour vous reconnecter.",
        "Remote explorer: the Next disconnected (BREAK / Bye). Press 'Start Remote Explorer NextSync server' to accept a new connection.":
            "Explorateur distant : le Next s'est déconnecté (BREAK / Bye). Appuyez sur 'Start Remote Explorer NextSync server' pour accepter une nouvelle connexion.",
        "Remote explorer: waiting for {command} on port {port}…":
            "Explorateur distant : en attente de {command} sur le port {port}…",
        "Renamed: {old} -> {new}":
            "Renommé : {old} -> {new}",
        "Running on host:":
            "Exécuté sur l'hôte :",
        "Saving incoming files under: {folder}":
            "Enregistrement des fichiers entrants dans : {folder}",
        "Send via NextSync: nothing to send in {folder}.":
            "Envoi via NextSync : rien à envoyer dans {folder}.",
        "Sending {folder} via Remote Explorer (-listen) → {target} …":
            "Envoi de {folder} via l'Explorateur distant (-listen) → {target} …",
        "Set a sync root folder first: navigate to the folder in the left local file explorer and press 'Set current folder as new sync root folder'.":
            "Choisissez d'abord un dossier racine de synchronisation : accédez-y dans l'explorateur local de gauche et appuyez sur 'Set current folder as new sync root folder'.",
        "Skipped (already exists): {path}":
            "Ignoré (existe déjà) : {path}",
        "Skipped {path}: cannot import a folder into itself.":
            "Ignoré {path} : impossible d'importer un dossier dans lui-même.",
        "Stop the running sync before starting the remote server.":
            "Arrêtez la synchronisation en cours avant de démarrer le serveur distant.",
        "Sync file list has {count} files.":
            "La liste de synchronisation contient {count} fichiers.",
        "Sync point file {name} not found, syncing all files regardless of timestamp.":
            "Fichier de point de synchronisation {name} introuvable ; tous les fichiers seront synchronisés quelle que soit leur date.",
        "Sync point updated with {count} received file(s)":
            "Point de synchronisation mis à jour avec {count} fichier(s) reçu(s)",
        "Upload connection closed":
            "Connexion d'envoi fermée",
        "Upload finished, {count} file(s) received":
            "Envoi terminé, {count} fichier(s) reçu(s)",
        "Using {folder} as sync root":
            "Utilisation de {folder} comme racine de synchronisation",
        "WARNING":
            "AVERTISSEMENT",
        "Warning":
            "Avertissement",
        "Warning! Ignore file {name} not found in directory. All files will be synced, possibly including this file.":
            "Attention ! Le fichier d'exclusions {name} est introuvable dans le dossier. Tous les fichiers seront synchronisés, y compris peut-être celui-ci.",
        "{kb} kilobytes payload, {rate} kBps effective speed":
            "{kb} kilo-octets utiles, vitesse effective {rate} ko/s",
        "{kb} kilobytes transferred in {seconds} seconds, {rate} kBps":
            "{kb} kilo-octets transférés en {seconds} secondes, {rate} ko/s",
        "{severity}: Ready to sync {count} files, {kb} kilobytes.":
            "{severity} : prêt à synchroniser {count} fichiers, {kb} kilo-octets.",
        # ---- checkboxes ----
        "Alien Floyd's — animated background on all tabs (Retro/pygame)":
            "Alien Floyd's — fond animé sur tous les onglets (Rétro/pygame)",
        "Alien Floyd's — show the full-window 'Alien Floyd's' tab (Retro/pygame)":
            "Alien Floyd's — afficher l'onglet « Alien Floyd's » plein écran (Rétro/pygame)",
        "Check for CSpect update on itch.io on startup":
            "Vérifier les mises à jour de CSpect sur itch.io au démarrage",
        "Check for ZX Next Unite updates at startup on Github":
            "Vérifier les mises à jour de ZX Next Unite sur GitHub au démarrage",
        "Check for a newer MAME version at startup":
            "Vérifier une version plus récente de MAME au démarrage",
        "Disable 'No emulators detected' message at startup":
            "Désactiver le message « Aucun émulateur détecté » au démarrage",
        "Do not prompt for confirmation on deletion.":
            "Ne pas demander de confirmation à la suppression.",
        "Enable NextSync HTTP bridge (web server for the Next's .http command)":
            "Activer le pont HTTP NextSync (serveur web pour la commande .http du Next)",
        "Enable crash log file generation":
            "Activer la génération du journal de plantage",
        "Enable multi API endpoints search (GetIt, ZXDB & zxArt search together).":
            "Activer la recherche multi-API (GetIt, ZXDB et zxArt ensemble).",
        "Enable search autocompletion.": "Activer l'autocomplétion de la recherche.",
        "NextSync — starfield log animation (Retro/pygame mode)":
            "NextSync — animation d'étoiles dans le journal (mode Rétro/pygame)",
        "Perform pre-availability check on Downloads (ZXDB & zxArt).":
            "Vérifier la disponibilité des téléchargements (ZXDB et zxArt).",
        "Require bearer token": "Exiger un jeton d'accès",
        "SD Card - Warn when an image is nearly full.":
            "Carte SD — Avertir quand une image est presque pleine.",
        "Send deleted files to the Recycle Bin (local file explorers).":
            "Envoyer les fichiers supprimés à la Corbeille (explorateurs locaux).",
        "Show the itch.io tab (browse & install your itch.io collections)":
            "Afficher l'onglet itch.io (parcourir et installer vos collections)",
        "Slow transfer": "Transfert lent",
        "Unite! — Invaders background animation (Retro/pygame mode)":
            "Unite! — animation de fond Invaders (mode Rétro/pygame)",
        # ---- radio buttons / group boxes ----
        "Always sync (send everything)": "Toujours synchroniser (tout envoyer)",
        "Sync changed files (continuous)": "Synchroniser les changements (continu)",
        "Sync once": "Synchroniser une fois",
        "Sync mode": "Mode de synchronisation",
        # ---- placeholders ----
        "Filter by name, type or size...": "Filtrer par nom, type ou taille…",
        "Filter by name...": "Filtrer par nom…",
        "Local folder path...": "Chemin du dossier local…",
        "New directory name ...": "Nom du nouveau répertoire…",
        "Paste your personal API key (itch.io → Settings → API keys)":
            "Collez votre clé API personnelle (itch.io → Settings → API keys)",
        "Path inside the SD card image...": "Chemin dans l'image SD…",
        "SD card image path...": "Chemin de l'image de carte SD…",
        "Search ZXDB games... (leave empty for random selection)":
            "Rechercher des jeux ZXDB… (vide = sélection aléatoire)",
        "Search across GetIt + ZXDB + zxArt...":
            "Rechercher dans GetIt + ZXDB + zxArt…",
        "Search files... (leave empty for latest 20)":
            "Rechercher des fichiers… (vide = les 20 derniers)",
        "Search your itch.io library (collections + purchases)…":
            "Rechercher dans votre bibliothèque itch.io (collections et achats)…",
        "Search zxART productions... (leave empty to browse latest)":
            "Rechercher des productions zxART… (vide = nouveautés)",
        "Sync root folder...": "Dossier racine de synchronisation…",
        "bearer token (generated when you enable the checkbox)":
            "jeton d'accès (généré à l'activation de la case)",
        # ---- short tooltips ----
        "Browse mode": "Mode navigation",
        "Search mode": "Mode recherche",
        "Pick a letter": "Choisissez une lettre",
        "Double-click to enlarge": "Double-cliquez pour agrandir",
        "Double-click to open full view": "Double-cliquez pour la vue complète",
        "Generate a new random 64-character bearer token":
            "Générer un nouveau jeton aléatoire de 64 caractères",
        "Select a disk image to be loaded.": "Choisissez l'image disque à charger.",
        "No SD card image is currently loaded.":
            "Aucune image de carte SD n'est chargée.",
        "Re-read the current local folder from disk.":
            "Relire le dossier local courant depuis le disque.",
        "Drag to resize the file explorers / log window split.":
            "Faites glisser pour redimensionner la séparation explorateurs / journal.",
        "Drag to resize the results / MOTD split.":
            "Faites glisser pour redimensionner la séparation résultats / MOTD.",
        "Drag to set the background image opacity (0–100 %).":
            "Faites glisser pour régler l'opacité du fond (0–100 %).",
        "Type a value 0–100 to set the background image opacity.":
            "Saisissez une valeur 0–100 pour l'opacité du fond.",
        "Preview of the selected background image.":
            "Aperçu de l'image de fond sélectionnée.",
        "Open https://itch.io/ in your browser":
            "Ouvrir https://itch.io/ dans votre navigateur",
        "Connect to itch.io using the API key above.":
            "Se connecter à itch.io avec la clé API ci-dessus.",
        "Disconnect from itch.io and clear the listed items.":
            "Se déconnecter d'itch.io et effacer les éléments listés.",
        "Open https://itch.io/user/settings/api-keys in your browser":
            "Ouvrir https://itch.io/user/settings/api-keys dans votre navigateur",
        "Color used for directory name entries in the image explorer.":
            "Couleur des noms de répertoires dans l'explorateur de l'image.",
        "Color used for file name entries in the image explorer.":
            "Couleur des noms de fichiers dans l'explorateur de l'image.",
        "Color used for the 'DIR' type label column of directory entries.":
            "Couleur de la colonne de type « DIR » des répertoires.",
        "Color used for the '[Up Directory..]' navigation row in the image explorer.":
            "Couleur de la ligne de navigation « [Up Directory..] ».",
        "Color used for the file extension column in the image explorer.":
            "Couleur de la colonne d'extension de fichier.",
        "Color used for the file size column in the image explorer.":
            "Couleur de la colonne de taille de fichier.",
        "Background color behind the file explorers and the whole app window.":
            "Couleur de fond derrière les explorateurs de fichiers et toute la fenêtre de l'application.",
        "Discard the hand-picked colors above and restore the themed defaults.":
            "Abandonne les couleurs choisies ci-dessus et restaure les valeurs par défaut du thème.",
        "MAME display aspect ratio (-aspect).":
            "Format d'affichage de MAME (-aspect).",
        "Perform a single sync and then stop the server.":
            "Effectuer une seule synchronisation puis arrêter le serveur.",
        "Keep listening and send every file each time, ignoring the sync point.":
            "Rester à l'écoute et tout renvoyer à chaque fois, en ignorant le point de synchronisation.",
        "Keep listening and send only files that are new or changed since the\nlast sync (skips files recorded in the sync point). The default mode.":
            "Rester à l'écoute et n'envoyer que les fichiers nouveaux ou modifiés\ndepuis la dernière synchronisation (ignore ceux inscrits au point de\nsynchronisation). Mode par défaut.",
        "Make the folder currently shown in the explorer above the new sync root.":
            "Définir le dossier affiché dans l'explorateur ci-dessus comme nouvelle racine de synchronisation.",
        "Pick a random page from the full GetIt catalogue and show its entries.":
            "Choisir une page au hasard du catalogue GetIt et afficher ses entrées.",
        "Pick a random page of zxART productions and show its entries.":
            "Choisir une page au hasard des productions zxART et l'afficher.",
        "Show the most recent zxART productions/pictures (sorted by date).":
            "Afficher les productions/images zxART les plus récentes (par date).",
        "Show the most recently added/updated ZXDB games.":
            "Afficher les jeux ZXDB ajoutés ou mis à jour récemment.",
        "Fetch random entries from GetIt + ZXDB + zxArt and merge them here":
            "Récupérer des entrées aléatoires de GetIt + ZXDB + zxArt et les fusionner ici",
        "Fetch the latest releases from GetIt + ZXDB + zxArt and merge them here":
            "Récupérer les dernières nouveautés de GetIt + ZXDB + zxArt et les fusionner ici",
        "zxART catalog display language.\nPersisted across sessions in the config file.":
            "Langue du catalogue zxART.\nEnregistrée dans le fichier de configuration.",
        "Requires the optional 'pygame-ce' package.\nInstall with: pip install pygame-ce":
            "Nécessite le paquet optionnel « pygame-ce ».\nInstallation : pip install pygame-ce",
        "Load a ZX Spectrum Next disk image first — then CSpect can boot it from the mounted SD card.":
            "Chargez d'abord une image disque ZX Spectrum Next — CSpect pourra la démarrer depuis la carte SD montée.",
        "Select emulator image file: {path}":
            "Choisir l'image disque pour l'émulateur : {path}",
        "No writable disk image available.":
            "Aucune image disque accessible en écriture n'est disponible.",
        ".img file {path} already in use.":
            "Le fichier .img {path} est déjà utilisé.",
        "The disk image {path} can no longer be found — it may have been moved, renamed or deleted.":
            "L'image disque {path} est introuvable — elle a peut-être été déplacée, renommée ou supprimée.",
        "Select a ZX Spectrum Next disk image (.img/.hdf) first — then MAME can boot it as the Next's hard disk.":
            "Choisissez d'abord une image disque (.img/.hdf) ZX Spectrum Next — MAME pourra la démarrer comme disque dur du Next.",
        "Select the parent folder inside the SD card image\n(at the top level this returns the target to the image root).":
            "Remonter au dossier parent dans l'image SD\n(au plus haut niveau, revient à la racine de l'image).",
        "Go up one folder in the local file explorer\n(same as double-clicking its '..' entry).":
            "Remonter d'un dossier dans l'explorateur local\n(comme un double-clic sur l'entrée « .. »).",
        "Re-list the current image folder from the SD card image\n(runs 'hdfmonkey ls' again).":
            "Relister le dossier courant de l'image SD\n(relance « hdfmonkey ls »).",
        "Download a ready-to-use NextZXOS SD card image from zxnext.uk,\nsave it to disk, and load it automatically.":
            "Télécharger une image SD NextZXOS prête à l'emploi depuis zxnext.uk,\nl'enregistrer sur le disque et la charger automatiquement.",
        "Switch between the classic table view and the picture (gallery)\nview. Persisted across sessions in the config file.":
            "Basculer entre la vue tableau classique et la vue galerie.\nEnregistré dans le fichier de configuration.",
        "Switch between the classic table view and the picture (gallery) view.\nPersisted across sessions in the config file.":
            "Basculer entre la vue tableau classique et la vue galerie.\nEnregistré dans le fichier de configuration.",
        "Language of the application's buttons, labels and checkboxes.\nApplies immediately; texts written while the app runs (logs, dialogs)\nfollow after a restart. Saved to the configuration file.":
            "Langue des boutons, libellés et cases à cocher de l'application.\nS'applique immédiatement ; les textes générés en cours d'exécution\n(journal, boîtes de dialogue) changent après un redémarrage.\nEnregistrée dans la configuration.",
        "🌐  Language set to match your system":
            "🌐  Langue adaptée à votre système",
        "The interface language was set to match your system language.\nYou can change it anytime in the Settings tab (\"Application language:\").":
            "La langue de l'interface a été réglée sur celle de votre système.\nVous pouvez la changer à tout moment dans l'onglet Settings\n(« Langue de l'application : »).",
        # ---- Help tab (INIT_HELP; rebuilt per language by _repopulate_help) ----
        "Welcome to zx-next-unite {version} help":
            "Bienvenue dans l'aide de zx-next-unite {version}",
        "Introduction:":
            "Introduction :",
        "HdfmGooey was initially created by em00k and NextSync by Jari Komppa.":
            "HdfmGooey a été créé à l'origine par em00k et NextSync par Jari Komppa.",
        "A while back I rambled with the idea of an all in one bootstrapper transfer tool to":
            "Il y a quelque temps, je caressais l'idée d'un outil tout-en-un de transfert et d'amorçage pour",
        "avoid manipulating SD cards for the Spectrum Next and that was the initial idea of it.":
            "éviter de manipuler les cartes SD du Spectrum Next, et c'était l'idée de départ.",
        "Last but not least some source code was lost from HDFM Gooey and the tool was stuck back in that time,":
            "Enfin, et ce n'est pas le moindre, une partie du code source de HDFM Gooey a été perdue et l'outil est resté figé à cette époque ;",
        "with the agreement of em00k I started a rewrite in Python and later with Jari.":
            "avec l'accord d'em00k j'ai entamé une réécriture en Python, rejoint plus tard par Jari.",
        "The point of using Python was that it would also provide MacOS and Linux portability.":
            "L'intérêt de Python était d'apporter aussi la portabilité MacOS et Linux.",
        "Later down the line I then extended the NextSync functionality from Sync3 to Sync4.":
            "Plus tard, j'ai étendu la fonctionnalité NextSync de Sync3 à Sync4.",
        "The new .sync5 command for the Next speaks Sync4 and therefore allows sending files and directories using the -send command line option.":
            "La nouvelle commande .sync5 pour le Next parle Sync4 et permet donc d'envoyer fichiers et répertoires avec l'option de ligne de commande -send.",
        "There is as well a new nextsync5.py command line located at the root of the repository that supports the new Sync4 protocol.":
            "Il existe aussi un nouvel outil en ligne de commande nextsync5.py, à la racine du dépôt, qui prend en charge le nouveau protocole Sync4.",
        "Here we are now you have it!":
            "Et voilà, c'est à vous !",
        "Keyboard shortcuts":
            "Raccourcis clavier",
        "The three file explorers (SD Card local, SD Card disk image and NextSync local) share these shortcuts. Copy / Cut / Paste work across all three explorers and also exchange with the operating-system clipboard (e.g. copy in Windows Explorer, paste into the disk image, and vice-versa):":
            "Les trois explorateurs de fichiers (local SD Card, image disque SD Card et local NextSync) partagent ces raccourcis. Copier / Couper / Coller fonctionnent entre les trois explorateurs et échangent aussi avec le presse-papiers du système d'exploitation (p. ex. copiez dans l'Explorateur Windows, collez dans l'image disque, et inversement) :",
        "    Ctrl+C  -  Copy the selected file(s)/folder(s) to the shared clipboard.":
            "    Ctrl+C  -  Copier les fichiers/dossiers sélectionnés vers le presse-papiers partagé.",
        "    Ctrl+X  -  Cut the selection (moved to the destination on the next paste).":
            "    Ctrl+X  -  Couper la sélection (déplacée vers la destination au prochain collage).",
        "    Ctrl+V  -  Paste into the selected / currently shown folder.":
            "    Ctrl+V  -  Coller dans le dossier sélectionné / actuellement affiché.",
        "    F2      -  Rename the selected file or folder.":
            "    F2      -  Renommer le fichier ou dossier sélectionné.",
        "    Delete  -  Delete the selected file or folder (disk-image & NextSync explorers).":
            "    Delete  -  Supprimer le fichier ou dossier sélectionné (explorateurs image disque et NextSync).",
        "In the picture (gallery) item viewer (double-click an item in the GetIt, ZXDB, zxArt or itch.io tabs):":
            "Dans la visionneuse d'éléments en images (galerie) (double-clic sur un élément des onglets GetIt, ZXDB, zxArt ou itch.io) :",
        "    Esc           -  Close the viewer and return to the gallery.":
            "    Esc           -  Fermer la visionneuse et revenir à la galerie.",
        "    Left / Right  -  Show the previous / next screenshot.":
            "    Left / Right  -  Afficher la capture précédente / suivante.",
        "Third party license":
            "Licences tierces",
        "zx-next-unite is released under the MIT license. It is a Qt Application using PySide6 (Qt for Python) on top of Qt6, used under the GNU LGPL v3.":
            "zx-next-unite est publié sous licence MIT. C'est une application Qt utilisant PySide6 (Qt for Python) au-dessus de Qt6, utilisé sous GNU LGPL v3.",
        "Please refer to the LICENSE and THIRD-PARTY-NOTICES.md files on github: https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE and https://github.com/jclauzel/ZX-Next-Unite/blob/main/THIRD-PARTY-NOTICES.md.":
            "Consultez les fichiers LICENSE et THIRD-PARTY-NOTICES.md sur github : https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE et https://github.com/jclauzel/ZX-Next-Unite/blob/main/THIRD-PARTY-NOTICES.md.",
        "PySide6 is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The pre-built release executables do include PySide6/Qt; since the app's full source code is published, they can be rebuilt with a modified Qt.":
            "PySide6 n'est pas inclus lors d'une installation python manuelle et doit être installé séparément (voir les instructions d'installation). Les exécutables précompilés des releases incluent PySide6/Qt ; le code source complet de l'application étant publié, ils peuvent être reconstruits avec un Qt modifié.",
        "zx-next-unite also uses pygame-ce (the community edition of pygame) for its animated backgrounds and visualizations (e.g. the 'Alien Floyd's' effects). Many thanks to the pygame and pygame-ce communities - see https://pyga.me and https://www.pygame.org.":
            "zx-next-unite utilise aussi pygame-ce (l'édition communautaire de pygame) pour ses fonds animés et visualisations (p. ex. les effets 'Alien Floyd's'). Un grand merci aux communautés pygame et pygame-ce - voir https://pyga.me et https://www.pygame.org.",
        "pygame-ce is distributed under the GNU LGPL v2.1 license and, like PySide6, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions).":
            "pygame-ce est distribué sous licence GNU LGPL v2.1 et, comme PySide6, n'est pas inclus lors d'une installation python manuelle — il s'installe séparément (voir les instructions d'installation).",
        "zx-next-unite optionally uses itch-dl by Dragoon Aethis to power the itch.io tab (browsing and installing your itch.io collections). Many thanks to its author - see https://github.com/DragoonAethis/itch-dl.":
            "zx-next-unite utilise en option itch-dl de Dragoon Aethis pour l'onglet itch.io (parcourir et installer vos collections itch.io). Un grand merci à son auteur - voir https://github.com/DragoonAethis/itch-dl.",
        "itch-dl is distributed under the MIT license (Copyright (c) 2022 Dragoon Aethis) and, like PySide6 and pygame-ce, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The itch.io tab is only shown when itch-dl is installed.":
            "itch-dl est distribué sous licence MIT (Copyright (c) 2022 Dragoon Aethis) et, comme PySide6 et pygame-ce, n'est pas inclus lors d'une installation python manuelle — il s'installe séparément (voir les instructions d'installation). L'onglet itch.io n'apparaît que lorsque itch-dl est installé.",
        "zx-next-unite optionally uses Flask by the Pallets team to power the NextSync HTTP bridge - the web server behind the Next's .http dot command that lets one Next drive another Next's SD card. Many thanks to its authors - see https://flask.palletsprojects.com and https://github.com/pallets/flask.":
            "zx-next-unite utilise en option Flask, de l'équipe Pallets, pour le pont HTTP NextSync - le serveur web derrière la commande dot .http du Next qui permet à un Next de piloter la carte SD d'un autre Next. Un grand merci à ses auteurs - voir https://flask.palletsprojects.com et https://github.com/pallets/flask.",
        "Flask is distributed under the BSD-3-Clause license and, like the other optional packages, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The HTTP bridge toggle in Settings is greyed out until Flask is installed.":
            "Flask est distribué sous licence BSD-3-Clause et, comme les autres paquets optionnels, n'est pas inclus lors d'une installation python manuelle — il s'installe séparément (voir les instructions d'installation). L'interrupteur du pont HTTP dans Settings reste grisé tant que Flask n'est pas installé.",
        "zx-next-unite optionally uses Send2Trash by Andrew Senetar and contributors (originally by Virgil Dupras) to send files deleted in the local file explorers to the system Recycle Bin / Trash instead of removing them permanently. Many thanks to its authors - see https://github.com/arsenetar/send2trash.":
            "zx-next-unite utilise en option Send2Trash, d'Andrew Senetar et contributeurs (à l'origine Virgil Dupras), pour envoyer les fichiers supprimés dans les explorateurs locaux vers la Corbeille du système au lieu de les effacer définitivement. Un grand merci à ses auteurs - voir https://github.com/arsenetar/send2trash.",
        "Send2Trash is distributed under the BSD license and, like the other optional packages, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The 'Send deleted files to the Recycle Bin' toggle in Settings is greyed out until Send2Trash is installed.":
            "Send2Trash est distribué sous licence BSD et, comme les autres paquets optionnels, n'est pas inclus lors d'une installation python manuelle — il s'installe séparément (voir les instructions d'installation). L'interrupteur 'Envoyer les fichiers supprimés à la Corbeille' dans Settings reste grisé tant que Send2Trash n'est pas installé.",
        "zx-next-unite's optional pre-compiled Windows binary is built with PyInstaller by the PyInstaller Development Team, which bundles the app and all of its dependencies into a single standalone executable. Many thanks to its authors - see https://pyinstaller.org and https://github.com/pyinstaller/pyinstaller.":
            "Le binaire Windows précompilé optionnel de zx-next-unite est construit avec PyInstaller, du PyInstaller Development Team, qui regroupe l'application et toutes ses dépendances en un seul exécutable autonome. Un grand merci à ses auteurs - voir https://pyinstaller.org et https://github.com/pyinstaller/pyinstaller.",
        "PyInstaller is distributed under its GPL 2.0 license with a special exception that explicitly permits packaging applications of any license. It is a build-time tool only - used to produce the pre-compiled binary - and is not needed when running zx-next-unite from source with a manual python install.":
            "PyInstaller est distribué sous sa licence GPL 2.0 avec une exception spéciale autorisant explicitement l'empaquetage d'applications sous n'importe quelle licence. C'est un outil de build uniquement - servant à produire le binaire précompilé - inutile pour lancer zx-next-unite depuis les sources avec une installation python manuelle.",
        "The pre-compiled Windows binary is additionally compressed with UPX (the Ultimate Packer for eXecutables) by Markus Oberhumer, Laszlo Molnar and John Reiser. Many thanks to its authors - see https://upx.github.io and https://github.com/upx/upx.":
            "Le binaire Windows précompilé est en outre compressé avec UPX (the Ultimate Packer for eXecutables), de Markus Oberhumer, Laszlo Molnar et John Reiser. Un grand merci à ses auteurs - voir https://upx.github.io et https://github.com/upx/upx.",
        "UPX is distributed under its own liberal license (based on the GPL, with a special exception covering the compressed executables it produces). Like PyInstaller it is a build-time tool only and is not needed when running from source.":
            "UPX est distribué sous sa propre licence libérale (basée sur la GPL, avec une exception spéciale couvrant les exécutables compressés qu'il produit). Comme PyInstaller, c'est un outil de build uniquement, inutile pour une exécution depuis les sources.",
        "Setup & How to:":
            "Installation et guide :",
        "Check out the main setup & demo video available at: https://youtu.be/-gUxV4fM1yo  (and the full python install is covered in the old py-hdfm-gooey since ZX-Next-Unite is an evolution of it : https://youtu.be/FJG-Z0DCIjQ )":
            "Regardez la vidéo principale d'installation et de démonstration disponible ici : https://youtu.be/-gUxV4fM1yo  (l'installation python complète est couverte dans l'ancien py-hdfm-gooey, ZX-Next-Unite en étant une évolution : https://youtu.be/FJG-Z0DCIjQ )",
        "NextSync Head Over Heels demo: https://www.youtube.com/watch?v=D3_WqTPvjOE":
            "Démo NextSync avec Head Over Heels : https://www.youtube.com/watch?v=D3_WqTPvjOE",
        "NextSync Night Knight demo: https://www.youtube.com/watch?v=eN1eMIqMCm4":
            "Démo NextSync avec Night Knight : https://www.youtube.com/watch?v=eN1eMIqMCm4",
        "Is a required external component developed by Matt Westcott that allows browsing the image.":
            "C'est un composant externe obligatoire développé par Matt Westcott qui permet de parcourir l'image.",
        "You will need to install it to get this application up and fully running.":
            "Vous devrez l'installer pour que cette application fonctionne pleinement.",
        "If hdfmonkey is not present you will see an error message in the main log window as it is missing.":
            "Si hdfmonkey est absent, un message d'erreur signalant son absence apparaît dans la fenêtre de journal principale.",
        "If that is the case you will see a 'Download and install HDF Monkey' button bottom right,":
            "Dans ce cas, un bouton 'Télécharger et installer HDF Monkey' apparaît en bas à droite ;",
        "once clicked it will download a pre-compiled hdfmonkey build for your platform (Windows/Linux/macOS) and install it under the app's downloads folder.":
            "un clic télécharge une version précompilée de hdfmonkey pour votre plateforme (Windows/Linux/macOS) et l'installe dans le dossier downloads de l'application.",
        "If the above automated install is successful, you should then be able to select an image and navigate it.":
            "Si l'installation automatique ci-dessus réussit, vous devriez pouvoir sélectionner une image et y naviguer.",
        "hdfmonkey can also be installed manually based on the instructions for your platform that can be found at: https://github.com/gasman/hdfmonkey":
            "hdfmonkey peut aussi être installé manuellement en suivant les instructions pour votre plateforme : https://github.com/gasman/hdfmonkey",
        "zx-next-unite implements the <Server> side code and protocol of NextSync by Jari Komppa.":
            "zx-next-unite implémente le code et le protocole côté <serveur> de NextSync de Jari Komppa.",
        "It does not require any dot .sync modification and it uses the same very close python logic as nextsync.py.":
            "Il ne nécessite aucune modification du dot .sync et reprend la même logique python, très proche, que nextsync.py.",
        "Initial release on specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8":
            "Annonce initiale sur specnext : https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8",
        "As a result you will need to run the same dot .sync command on your Next as with the console version and the same network protocol.":
            "Vous devrez donc exécuter sur votre Next la même commande dot .sync qu'avec la version console, avec le même protocole réseau.",
        "The latest release v1.2 of the .sync command can be found here https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 .":
            "La dernière version v1.2 de la commande .sync se trouve ici https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 .",
        "You may follow the same instructions as provided in the readme.txt of that release.":
            "Vous pouvez suivre les mêmes instructions que celles du readme.txt de cette version.",
        "On your Spectrum Next, clone or image copy the SYNC command that is located in the above release zip file into your Next dot folder.":
            "Sur votre Spectrum Next, clonez ou copiez dans l'image la commande SYNC présente dans le zip de cette version vers le dossier dot de votre Next.",
        "Navigate to the NextSync tab, select the root folder to sync on the left.":
            "Allez dans l'onglet NextSync et sélectionnez à gauche le dossier racine à synchroniser.",
        "Once you have selected the folder hit the 'Prepare Classic NextSync server' button, check the NextSync log window on the right.":
            "Une fois le dossier sélectionné, cliquez sur le bouton 'Préparer le serveur NextSync classique' et surveillez la fenêtre de journal NextSync à droite.",
        "The first time you run .sync on your Next you will be prompted to select the <server> IP address, this machine running NextSync.":
            "Au premier lancement de .sync sur votre Next, il vous sera demandé de choisir l'adresse IP du <serveur>, cette machine qui exécute NextSync.",
        "From the log window pick the IP address from this machine you want to use and type it on your Next.":
            "Dans la fenêtre de journal, repérez l'adresse IP de cette machine à utiliser et saisissez-la sur votre Next.",
        "Then start the sync server on this machine using the 'Start Classic NextSync server' button and then run the .sync command on your Next.":
            "Démarrez ensuite le serveur de synchronisation sur cette machine avec le bouton 'Démarrer le serveur NextSync classique' puis lancez la commande .sync sur votre Next.",
        "At this point your Spectrum Next will connect to your machine using a network socket and the files will be sent to your Next.":
            "À ce moment, votre Spectrum Next se connecte à votre machine via un socket réseau et les fichiers sont envoyés vers votre Next.",
        "As it is your Next that will connect to this machine check your firewall allows inbound calls to this machine on port: 2048 by default.":
            "Comme c'est votre Next qui se connecte à cette machine, vérifiez que votre pare-feu autorise les connexions entrantes vers cette machine sur le port : 2048 par défaut.",
        "The same syncignore.txt and syncpoint.dat file logic applies and allows you to control the sync (please check Jari's documentation).":
            "La même logique des fichiers syncignore.txt et syncpoint.dat s'applique et vous permet de contrôler la synchronisation (consultez la documentation de Jari).",
        "NextSync source code can be found here: https://github.com/jarikomppa/specnext/tree/master/sync":
            "Le code source de NextSync se trouve ici : https://github.com/jarikomppa/specnext/tree/master/sync",
        "If you run into any type of issue using the NextSync integration please run first Jari's command line version to see if it works as expected.":
            "En cas de problème avec l'intégration NextSync, lancez d'abord la version en ligne de commande de Jari pour vérifier qu'elle fonctionne comme prévu.",
        "OpenAL sound engine (on Windows)":
            "Moteur audio OpenAL (sous Windows)",
        "The OpenAL library is required on Windows for CSpect to play sound, you may download it here: https://openal.org/":
            "La bibliothèque OpenAL est requise sous Windows pour que CSpect joue le son ; téléchargez-la ici : https://openal.org/",
        "Mono (on Linux & MacOS Only)":
            "Mono (Linux et MacOS uniquement)",
        "You will also need to manually install the mono-complete package for example using: sudo apt-get install mono-complete":
            "Vous devrez aussi installer manuellement le paquet mono-complete, par exemple avec : sudo apt-get install mono-complete",
        "Third-Party Content Sources (GetIt / ZXDB / zxArt):":
            "Sources de contenu tierces (GetIt / ZXDB / zxArt) :",
        "zx-next-unite integrates three external databases to let you browse and download":
            "zx-next-unite intègre trois bases de données externes pour parcourir et télécharger",
        "Spectrum-related software and artwork directly from within the application.":
            "logiciels et créations liés au Spectrum directement depuis l'application.",
        "The application consumes their public APIs — it does not host, mirror, or":
            "L'application consomme leurs API publiques — elle n'héberge, ne réplique ni",
        "redistribute any of the files itself.":
            "ne redistribue aucun fichier elle-même.",
        "  GetIt is a community-maintained archive of ZX Spectrum Next software.":
            "  GetIt est une archive de logiciels ZX Spectrum Next maintenue par la communauté.",
        "  The application queries the GetIt API to list and search files, then":
            "  L'application interroge l'API GetIt pour lister et chercher les fichiers, puis",
        "  downloads them directly from the URLs returned by that API.":
            "  les télécharge directement depuis les URL renvoyées par cette API.",
        "  ZXDB is an open-source database of ZX Spectrum and related software,":
            "  ZXDB est une base de données open source des logiciels ZX Spectrum et apparentés,",
        "  maintained by the community at https://github.com/zxdb/ZXDB .":
            "  maintenue par la communauté sur https://github.com/zxdb/ZXDB .",
        "  The application queries the ZXDB REST API for titles, releases, screenshots":
            "  L'application interroge l'API REST de ZXDB pour les titres, éditions, captures",
        "  and inlays, then downloads files directly from the URLs returned by that API.":
            "  et jaquettes, puis télécharge les fichiers directement depuis les URL renvoyées par cette API.",
        "  zxArt (https://zxart.ee) is a gallery and archive dedicated to ZX Spectrum":
            "  zxArt (https://zxart.ee) est une galerie et une archive dédiées à l'art visuel,",
        "  visual art, music, and productions.":
            "  la musique et les productions ZX Spectrum.",
        "  The application sends requests to the zxArt API to search productions and":
            "  L'application envoie des requêtes à l'API zxArt pour chercher productions et",
        "  pictures, retrieve metadata and preview images, and download productions":
            "  images, récupérer métadonnées et aperçus, et télécharger les productions",
        "  directly from the URLs returned by that API.":
            "  directement depuis les URL renvoyées par cette API.",
        "  Mame emulator brought to you by Holub for the ZX Spectrum Next can be installed following this documentation: https://wiki.specnext.dev/MAME:Installing":
            "  L'émulateur Mame porté sur ZX Spectrum Next par Holub s'installe en suivant cette documentation : https://wiki.specnext.dev/MAME:Installing",
        "  Official Windows Binary Packages can be found here: https://www.mamedev.org/release.html":
            "  Les paquets binaires Windows officiels se trouvent ici : https://www.mamedev.org/release.html",
        "  Put the file tbblue.zip that can be found here: https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip into MAME's roms folder.":
            "  Placez le fichier tbblue.zip, disponible ici : https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip dans le dossier roms de MAME.",
        "  Important note: Don't extract the tbblue.zip file; MAME will look for the zip file when the 'tbblue' machine is selected.":
            "  Note importante : ne décompressez pas le fichier tbblue.zip ; MAME cherche le zip quand la machine 'tbblue' est sélectionnée.",
        "  Mike Dailly's CSpect is a downloadable emulator for Windows, macOS, and Linux":
            "  CSpect, de Mike Dailly, est un émulateur téléchargeable pour Windows, macOS et Linux",
        "  Sites and links:":
            "  Sites et liens :",
        "Legal disclaimer:":
            "Avertissement légal :",
        "  The author of zx-next-unite does NOT distribute any files, ROMs, games,":
            "  L'auteur de zx-next-unite ne distribue AUCUN fichier, ROM, jeu,",
        "  demos, graphics, music, or any other content obtained through these APIs.":
            "  démo, graphisme, musique ni aucun autre contenu obtenu via ces API.",
        "  All content is served exclusively by the respective third-party services.":
            "  Tout le contenu est servi exclusivement par les services tiers respectifs.",
        "  This application and author do not control third-party content.":
            "  Cette application et son auteur ne contrôlent pas les contenus tiers.",
        "  It is the sole responsibility of the end user to ensure that any content":
            "  Il relève de la seule responsabilité de l'utilisateur final de s'assurer que tout contenu",
        "  they download or use through this application complies with the applicable":
            "  téléchargé ou utilisé via cette application respecte les exigences applicables",
        "  copyright, licensing, and legal requirements in their jurisdiction.":
            "  en matière de droit d'auteur, de licences et de législation de sa juridiction.",
        "  If in doubt, consult the terms of service of the relevant platform and":
            "  En cas de doute, consultez les conditions d'utilisation de la plateforme concernée et",
        "  seek appropriate legal advice before downloading or using any content.":
            "  demandez un avis juridique approprié avant de télécharger ou d'utiliser tout contenu.",
        "  For inquiries you may reach out to me on my github page: https://github.com/jclauzel/ZX-Next-Unite":
            "  Pour toute question, contactez-moi sur ma page github : https://github.com/jclauzel/ZX-Next-Unite",
        "Enjoy!":
            "Amusez-vous bien !",
        # ---- OpenAL guided install (CSpect sound on Windows) ----
        "OpenAL 1.1 detected — CSpect sound is ready.":
            "OpenAL 1.1 détecté — le son de CSpect est prêt.",
        "Install OpenAL?":
            "Installer OpenAL ?",
        ("On Windows CSpect needs the <b>OpenAL 1.1</b> audio library "
         "for sound, and it was not detected on this machine — without "
         "it CSpect runs silent.<br><br>"
         "OpenAL is separate, third-party software — many thanks to its "
         "authors: {url}<br><br>"
         "Download the official installer (oalinst.exe) from openal.org "
         "and run it now?<br><br>"
         "Windows will ask for administrator approval when the installer "
         "starts — the app itself never runs elevated."):
            ("Sous Windows, CSpect a besoin de la bibliothèque audio "
             "<b>OpenAL 1.1</b> pour le son, et elle n'a pas été détectée "
             "sur cette machine — sans elle CSpect reste muet.<br><br>"
             "OpenAL est un logiciel tiers distinct — un grand merci à ses "
             "auteurs : {url}<br><br>"
             "Télécharger l'installateur officiel (oalinst.exe) depuis "
             "openal.org et le lancer maintenant ?<br><br>"
             "Windows demandera une approbation administrateur au lancement "
             "de l'installateur — l'application elle-même ne s'exécute "
             "jamais avec des droits élevés."),
        "Download and run the OpenAL installer":
            "Télécharger et lancer l'installateur OpenAL",
        "Open openal.org":
            "Ouvrir openal.org",
        "The OpenAL download failed — see the log for details. You can install it manually from {url}":
            "Le téléchargement d'OpenAL a échoué — voir le journal pour les détails. Vous pouvez l'installer manuellement depuis {url}",
    },
}


# ---------------------------------------------------------------------------
# Toast titles/bodies (runtime strings, translated via ui_tr_now: the
# _show_toast chokepoint handles exact matches, and dynamic content is
# translated as a .format() TEMPLATE at its call site — keys carrying
# {placeholders} are those templates). Kept in a separate dict for
# readability and merged into CATALOGS below so ui_tr sees one namespace.
# tests/test_i18n.py has a tripwire asserting every toast string literal in
# the code exists in every language here.
# ---------------------------------------------------------------------------
_TOAST_CATALOGS = {
    "fr": {
        "⚠  No network connection": "⚠  Pas de connexion réseau",
        "Online features are paused until the connection returns — emulators and the SD Card tools still work.":
            "Les fonctions en ligne sont en pause jusqu'au retour de la connexion — les émulateurs et les outils SD Card fonctionnent toujours.",
        "✅  Network restored": "✅  Réseau rétabli",
        "Online features are back.": "Les fonctions en ligne sont de retour.",
        "✅  CSpect installed": "✅  CSpect installé",
        "✅  Emulator(s) detected": "✅  Émulateur(s) détecté(s)",
        "⚠  No emulators detected": "⚠  Aucun émulateur détecté",
        "✅  hdfmonkey installed": "✅  hdfmonkey installé",
        "✅  Send to SD card complete": "✅  Envoi vers la carte SD terminé",
        "NextSync server not started": "Serveur NextSync non démarré",
        "⚠  MAME needs the Next boot ROM": "⚠  MAME a besoin de la ROM de démarrage du Next",
        "✅  MAME installed": "✅  MAME installé",
        "⚠  MAME install failed": "⚠  Échec de l'installation de MAME",
        "✅  CSpect updated": "✅  CSpect mis à jour",
        "⚠  No disk image selected/found for your emulator": "⚠  Aucune image disque sélectionnée/trouvée pour votre émulateur",
        "Classic NextSync server not started": "Serveur NextSync classique non démarré",
        "NextSync HTTP bridge started": "Passerelle HTTP NextSync démarrée",
        "NextSync HTTP bridge not started": "Passerelle HTTP NextSync non démarrée",
        "Remote Explorer NextSync server not started": "Serveur NextSync du Remote Explorer non démarré",
        "Remote Explorer autostart not enabled": "Démarrage automatique du Remote Explorer non activé",
        "Define a sync root folder first, on the NextSync tab's Remote Explorer view.":
            "Définissez d'abord un dossier racine de synchronisation dans la vue Remote Explorer de l'onglet NextSync.",
        "Next connected": "Next connecté",
        "A Next is now connected to the NextSync Remote Explorer.":
            "Un Next est maintenant connecté au Remote Explorer NextSync.",
        "Next disconnected": "Next déconnecté",
        "The Next disconnected from the NextSync Remote Explorer.":
            "Le Next s'est déconnecté du Remote Explorer NextSync.",
        "Cancelling — will stop once the current file has finished transferring, to avoid file corruption…":
            "Annulation — s'arrêtera une fois le transfert du fichier en cours terminé, afin d'éviter toute corruption de fichier…",
        "NextSync server started": "Serveur NextSync démarré",
        "You have started a Remote Explorer nextsync server already": "Un serveur NextSync Remote Explorer est déjà démarré",
        "✅  Sent via Remote Explorer": "✅  Envoyé via le Remote Explorer",
        "Remote Explorer is busy": "Le Remote Explorer est occupé",
        "⚠  Remote copy interrupted": "⚠  Copie distante interrompue",
        "✅  Remote copy complete": "✅  Copie distante terminée",
        "A Next operation failed": "Une opération sur le Next a échoué",
        "{n} Next operations failed": "{n} opérations sur le Next ont échoué",
        "Folder unavailable": "Dossier indisponible",
        "Remote unzip failed": "Échec de la décompression distante",
        "Remote unzip": "Décompression distante",
        "Remote unzip refused": "Décompression distante refusée",
        "✅  Remote unzip complete": "✅  Décompression distante terminée",
        "Remote zip": "Compression distante",
        "Remote zip failed": "Échec de la compression distante",
        "Found: {emulators}.": "Détecté : {emulators}.",
        " and ": " et ",
        "Mame: via Flatpak ({app})": "Mame : via Flatpak ({app})",
        "⚠ On Windows, CSpect needs <b>OpenAL 1.1</b> for sound. If you have no audio, install it from <a href=\"https://www.openal.org/\">openal.org</a>.":
            "⚠ Sous Windows, CSpect a besoin d'<b>OpenAL 1.1</b> pour le son. Sans audio, installez-le depuis <a href=\"https://www.openal.org/\">openal.org</a>.",
        "Neither CSpect nor Mame were found. Add the emulator(s) to your operating system PATH environment variable so they can be launched from here. \r\n\r\nCSpect: https://mdf200.itch.io/cspect \r\nMame: https://wiki.specnext.dev/MAME:Installing":
            "Ni CSpect ni Mame n'ont été trouvés. Ajoutez le(s) émulateur(s) à la variable d'environnement PATH de votre système pour pouvoir les lancer d'ici. \r\n\r\nCSpect : https://mdf200.itch.io/cspect \r\nMame : https://wiki.specnext.dev/MAME:Installing",
        "hdfmonkey has been installed and is ready to use.": "hdfmonkey a été installé et est prêt à l'emploi.",
        "Location: {path}": "Emplacement : {path}",
        "The file was sent to the SD card image.": "Le fichier a été envoyé vers l'image de carte SD.",
        "Sent to SD card image:\n{name}": "Envoyé vers l'image de carte SD :\n{name}",
        "Sent {ok}/{n} file(s) to SD card image:\n{dir}": "{ok}/{n} fichier(s) envoyé(s) vers l'image de carte SD :\n{dir}",
        "Port {port} is already in use.\nIs another ZX-Next-Unite instance (or a standalone NextSync server) already running?":
            "Le port {port} est déjà utilisé.\nUne autre instance de ZX-Next-Unite (ou un serveur NextSync autonome) est-elle déjà en cours d'exécution ?",
        "MAME can't run without the TBBLUE boot ROM — a manual step.\r\nSee {url} → \"Get TBBLUE\".\r\nPut tbblue.zip into MAME's roms folder (downloads\\mame\\roms) — DON'T extract it.\r\nUse only a legally acquired, licensed ROM.":
            "MAME ne peut pas fonctionner sans la ROM de démarrage TBBLUE — une étape manuelle.\r\nVoir {url} → « Get TBBLUE ».\r\nPlacez tbblue.zip dans le dossier roms de MAME (downloads\\mame\\roms) — NE PAS l'extraire.\r\nUtilisez uniquement une ROM acquise légalement et sous licence.",
        "MAME is installed — no restart needed.\r\nManual step: add the TBBLUE boot ROM.\r\nSee {url} → \"Get TBBLUE\".\r\nPut tbblue.zip into MAME's roms folder\r\n({roms})\r\n— DON'T extract it.\r\nUse only a legally acquired, licensed ROM.":
            "MAME est installé — aucun redémarrage nécessaire.\r\nÉtape manuelle : ajoutez la ROM de démarrage TBBLUE.\r\nVoir {url} → « Get TBBLUE ».\r\nPlacez tbblue.zip dans le dossier roms de MAME\r\n({roms})\r\n— NE PAS l'extraire.\r\nUtilisez uniquement une ROM acquise légalement et sous licence.",
        "The archive was extracted but mame.exe could not be located in downloads/mame.":
            "L'archive a été extraite mais mame.exe est introuvable dans downloads/mame.",
        "CSpect {name} is installed — no restart needed.\r\n{extracted}":
            "CSpect {name} est installé — aucun redémarrage nécessaire.\r\n{extracted}",
        "To start an emulator please select first a disk image at the top of the screen on the SD Card Utility tab.":
            "Pour démarrer un émulateur, sélectionnez d'abord une image disque en haut de l'écran dans l'onglet SD Card Utility.",
        "You have already started a Remote Explorer nextsync server, please stop it first.":
            "Vous avez déjà démarré un serveur NextSync Remote Explorer, veuillez d'abord l'arrêter.",
        "You have already started a Classic nextsync server, please stop it first.":
            "Vous avez déjà démarré un serveur NextSync classique, veuillez d'abord l'arrêter.",
        "Serving on port {port}. A Next with the .http dot command (or curl) can now drive the Next connected in '.sync5 -L' (-l or -listen).":
            "Serveur actif sur le port {port}. Un Next avec la commande dot .http (ou curl) peut maintenant piloter le Next connecté en « .sync5 -L » (-l ou -listen).",
        "You have specified to start the flask integration server but port {port} is already in use, the web server has not been started.":
            "Vous avez demandé le démarrage du serveur d'intégration Flask mais le port {port} est déjà utilisé, le serveur web n'a pas été démarré.",
        "You can now start your Next {command} dot command.":
            "Vous pouvez maintenant lancer la commande dot {command} sur votre Next.",
        "Start '.sync5 -L' (-l or -listen) on your Next and retry again (canceling the upload / send process for now).":
            "Lancez « .sync5 -L » (-l ou -listen) sur votre Next puis réessayez (envoi annulé pour le moment).",
        "file {path}": "fichier {path}",
        "{n} files:": "{n} fichiers :",
        "…and {n} more": "…et {n} de plus",
        "Another transfer is still running — wait for it to finish, then try again.":
            "Un autre transfert est encore en cours — attendez qu'il se termine, puis réessayez.",
        "The connection to the Next ended before the copy finished; its state is unknown.":
            "La connexion au Next s'est terminée avant la fin de la copie ; son état est inconnu.",
        "Copied {n} item(s) on the Next.": "{n} élément(s) copié(s) sur le Next.",
        "{path} no longer exists on the Next.\nReturned to {root}.":
            "{path} n'existe plus sur le Next.\nRetour à {root}.",
        "Could not extract {name}: {error}": "Impossible d'extraire {name} : {error}",
        "{name} contains no extractable files.": "{name} ne contient aucun fichier extractible.",
        "Unzipping needs {need}, but drive {drive}: only has {free} free.":
            "La décompression nécessite {need}, mais le lecteur {drive}: n'a que {free} de libre.",
        "Extracted {files} file(s) from {name} into {cwd}.":
            "{files} fichier(s) extrait(s) de {name} vers {cwd}.",
        "{skipped} unsafe entries skipped": "{skipped} entrées non sûres ignorées",
        "Nothing was downloaded — no zip was created.": "Rien n'a été téléchargé — aucun zip n'a été créé.",
        "Could not build {zip_name}: {error}": "Impossible de créer {zip_name} : {error}",
        "Remote zip refused": "Compression distante refusée",
        "✅  Remote zip complete": "✅  Compression distante terminée",
        "Unzip failed": "Échec de la décompression",
        "✅  Unzip complete": "✅  Décompression terminée",
        "Zip failed": "Échec de la compression",
        "✅  Zip complete": "✅  Compression terminée",
        "{zip_name} is {size}, but drive {drive}: only has {free} free.":
            "{zip_name} fait {size}, mais le lecteur {drive}: n'a que {free} de libre.",
        "Created {zip_name} in {dest} ({files} file(s), {size}).":
            "{zip_name} créé dans {dest} ({files} fichier(s), {size}).",
        "Created {zip_name} in {dest} ({files} file(s)).":
            "{zip_name} créé dans {dest} ({files} fichier(s)).",
        "Could not create {zip_name}: {error}": "Impossible de créer {zip_name} : {error}",
    },
    "es": {
        "⚠  No network connection": "⚠  Sin conexión de red",
        "Online features are paused until the connection returns — emulators and the SD Card tools still work.":
            "Las funciones en línea quedan en pausa hasta que vuelva la conexión — los emuladores y las herramientas de SD Card siguen funcionando.",
        "✅  Network restored": "✅  Red restablecida",
        "Online features are back.": "Las funciones en línea están de vuelta.",
        "✅  CSpect installed": "✅  CSpect instalado",
        "✅  Emulator(s) detected": "✅  Emulador(es) detectado(s)",
        "⚠  No emulators detected": "⚠  No se detectaron emuladores",
        "✅  hdfmonkey installed": "✅  hdfmonkey instalado",
        "✅  Send to SD card complete": "✅  Envío a la tarjeta SD completado",
        "NextSync server not started": "Servidor NextSync no iniciado",
        "⚠  MAME needs the Next boot ROM": "⚠  MAME necesita la ROM de arranque del Next",
        "✅  MAME installed": "✅  MAME instalado",
        "⚠  MAME install failed": "⚠  Falló la instalación de MAME",
        "✅  CSpect updated": "✅  CSpect actualizado",
        "⚠  No disk image selected/found for your emulator": "⚠  No hay imagen de disco seleccionada/encontrada para su emulador",
        "Classic NextSync server not started": "Servidor NextSync clásico no iniciado",
        "NextSync HTTP bridge started": "Puente HTTP de NextSync iniciado",
        "NextSync HTTP bridge not started": "Puente HTTP de NextSync no iniciado",
        "Remote Explorer NextSync server not started": "Servidor NextSync del Remote Explorer no iniciado",
        "Remote Explorer autostart not enabled": "Autoarranque del Remote Explorer no activado",
        "Define a sync root folder first, on the NextSync tab's Remote Explorer view.":
            "Define primero una carpeta raíz de sincronización en la vista Remote Explorer de la pestaña NextSync.",
        "Next connected": "Next conectado",
        "A Next is now connected to the NextSync Remote Explorer.":
            "Un Next está ahora conectado al Remote Explorer de NextSync.",
        "Next disconnected": "Next desconectado",
        "The Next disconnected from the NextSync Remote Explorer.":
            "El Next se ha desconectado del Remote Explorer de NextSync.",
        "Cancelling — will stop once the current file has finished transferring, to avoid file corruption…":
            "Cancelando — se detendrá cuando el archivo actual termine de transferirse, para evitar la corrupción de archivos…",
        "NextSync server started": "Servidor NextSync iniciado",
        "You have started a Remote Explorer nextsync server already": "Ya ha iniciado un servidor NextSync del Remote Explorer",
        "✅  Sent via Remote Explorer": "✅  Enviado mediante el Remote Explorer",
        "Remote Explorer is busy": "El Remote Explorer está ocupado",
        "⚠  Remote copy interrupted": "⚠  Copia remota interrumpida",
        "✅  Remote copy complete": "✅  Copia remota completada",
        "A Next operation failed": "Falló una operación en el Next",
        "{n} Next operations failed": "Fallaron {n} operaciones en el Next",
        "Folder unavailable": "Carpeta no disponible",
        "Remote unzip failed": "Falló la descompresión remota",
        "Remote unzip": "Descompresión remota",
        "Remote unzip refused": "Descompresión remota rechazada",
        "✅  Remote unzip complete": "✅  Descompresión remota completada",
        "Remote zip": "Compresión remota",
        "Remote zip failed": "Falló la compresión remota",
        "Found: {emulators}.": "Encontrado: {emulators}.",
        " and ": " y ",
        "Mame: via Flatpak ({app})": "Mame: mediante Flatpak ({app})",
        "⚠ On Windows, CSpect needs <b>OpenAL 1.1</b> for sound. If you have no audio, install it from <a href=\"https://www.openal.org/\">openal.org</a>.":
            "⚠ En Windows, CSpect necesita <b>OpenAL 1.1</b> para el sonido. Si no tiene audio, instálelo desde <a href=\"https://www.openal.org/\">openal.org</a>.",
        "Neither CSpect nor Mame were found. Add the emulator(s) to your operating system PATH environment variable so they can be launched from here. \r\n\r\nCSpect: https://mdf200.itch.io/cspect \r\nMame: https://wiki.specnext.dev/MAME:Installing":
            "No se encontraron ni CSpect ni Mame. Añada el/los emulador(es) a la variable de entorno PATH de su sistema operativo para poder lanzarlos desde aquí. \r\n\r\nCSpect: https://mdf200.itch.io/cspect \r\nMame: https://wiki.specnext.dev/MAME:Installing",
        "hdfmonkey has been installed and is ready to use.": "hdfmonkey se ha instalado y está listo para usar.",
        "Location: {path}": "Ubicación: {path}",
        "The file was sent to the SD card image.": "El archivo se envió a la imagen de la tarjeta SD.",
        "Sent to SD card image:\n{name}": "Enviado a la imagen de la tarjeta SD:\n{name}",
        "Sent {ok}/{n} file(s) to SD card image:\n{dir}": "{ok}/{n} archivo(s) enviado(s) a la imagen de la tarjeta SD:\n{dir}",
        "Port {port} is already in use.\nIs another ZX-Next-Unite instance (or a standalone NextSync server) already running?":
            "El puerto {port} ya está en uso.\n¿Hay otra instancia de ZX-Next-Unite (o un servidor NextSync independiente) ya en ejecución?",
        "MAME can't run without the TBBLUE boot ROM — a manual step.\r\nSee {url} → \"Get TBBLUE\".\r\nPut tbblue.zip into MAME's roms folder (downloads\\mame\\roms) — DON'T extract it.\r\nUse only a legally acquired, licensed ROM.":
            "MAME no puede funcionar sin la ROM de arranque TBBLUE — un paso manual.\r\nVea {url} → «Get TBBLUE».\r\nColoque tbblue.zip en la carpeta roms de MAME (downloads\\mame\\roms) — NO lo extraiga.\r\nUse solo una ROM adquirida legalmente y con licencia.",
        "MAME is installed — no restart needed.\r\nManual step: add the TBBLUE boot ROM.\r\nSee {url} → \"Get TBBLUE\".\r\nPut tbblue.zip into MAME's roms folder\r\n({roms})\r\n— DON'T extract it.\r\nUse only a legally acquired, licensed ROM.":
            "MAME está instalado — no hace falta reiniciar.\r\nPaso manual: añada la ROM de arranque TBBLUE.\r\nVea {url} → «Get TBBLUE».\r\nColoque tbblue.zip en la carpeta roms de MAME\r\n({roms})\r\n— NO lo extraiga.\r\nUse solo una ROM adquirida legalmente y con licencia.",
        "The archive was extracted but mame.exe could not be located in downloads/mame.":
            "El archivo se extrajo pero no se encontró mame.exe en downloads/mame.",
        "CSpect {name} is installed — no restart needed.\r\n{extracted}":
            "CSpect {name} está instalado — no hace falta reiniciar.\r\n{extracted}",
        "To start an emulator please select first a disk image at the top of the screen on the SD Card Utility tab.":
            "Para iniciar un emulador, seleccione primero una imagen de disco en la parte superior de la pantalla, en la pestaña SD Card Utility.",
        "You have already started a Remote Explorer nextsync server, please stop it first.":
            "Ya ha iniciado un servidor NextSync del Remote Explorer; deténgalo primero.",
        "You have already started a Classic nextsync server, please stop it first.":
            "Ya ha iniciado un servidor NextSync clásico; deténgalo primero.",
        "Serving on port {port}. A Next with the .http dot command (or curl) can now drive the Next connected in '.sync5 -L' (-l or -listen).":
            "Sirviendo en el puerto {port}. Un Next con el comando dot .http (o curl) ya puede controlar el Next conectado en «.sync5 -L» (-l o -listen).",
        "You have specified to start the flask integration server but port {port} is already in use, the web server has not been started.":
            "Ha indicado iniciar el servidor de integración Flask pero el puerto {port} ya está en uso; el servidor web no se ha iniciado.",
        "You can now start your Next {command} dot command.":
            "Ya puede ejecutar el comando dot {command} en su Next.",
        "Start '.sync5 -L' (-l or -listen) on your Next and retry again (canceling the upload / send process for now).":
            "Ejecute «.sync5 -L» (-l o -listen) en su Next y reintente (el envío se cancela por ahora).",
        "file {path}": "archivo {path}",
        "{n} files:": "{n} archivos:",
        "…and {n} more": "…y {n} más",
        "Another transfer is still running — wait for it to finish, then try again.":
            "Otra transferencia sigue en curso — espere a que termine y vuelva a intentarlo.",
        "The connection to the Next ended before the copy finished; its state is unknown.":
            "La conexión con el Next terminó antes de completar la copia; su estado es desconocido.",
        "Copied {n} item(s) on the Next.": "{n} elemento(s) copiado(s) en el Next.",
        "{path} no longer exists on the Next.\nReturned to {root}.":
            "{path} ya no existe en el Next.\nSe volvió a {root}.",
        "Could not extract {name}: {error}": "No se pudo extraer {name}: {error}",
        "{name} contains no extractable files.": "{name} no contiene archivos extraíbles.",
        "Unzipping needs {need}, but drive {drive}: only has {free} free.":
            "Descomprimir necesita {need}, pero la unidad {drive}: solo tiene {free} libres.",
        "Extracted {files} file(s) from {name} into {cwd}.":
            "{files} archivo(s) extraído(s) de {name} en {cwd}.",
        "{skipped} unsafe entries skipped": "{skipped} entradas no seguras omitidas",
        "Nothing was downloaded — no zip was created.": "No se descargó nada — no se creó ningún zip.",
        "Could not build {zip_name}: {error}": "No se pudo crear {zip_name}: {error}",
        "Remote zip refused": "Compresión remota rechazada",
        "✅  Remote zip complete": "✅  Compresión remota completada",
        "Unzip failed": "Falló la descompresión",
        "✅  Unzip complete": "✅  Descompresión completada",
        "Zip failed": "Falló la compresión",
        "✅  Zip complete": "✅  Compresión completada",
        "{zip_name} is {size}, but drive {drive}: only has {free} free.":
            "{zip_name} ocupa {size}, pero la unidad {drive}: solo tiene {free} libres.",
        "Created {zip_name} in {dest} ({files} file(s), {size}).":
            "Se creó {zip_name} en {dest} ({files} archivo(s), {size}).",
        "Created {zip_name} in {dest} ({files} file(s)).":
            "Se creó {zip_name} en {dest} ({files} archivo(s)).",
        "Could not create {zip_name}: {error}": "No se pudo crear {zip_name}: {error}",
    },
    "pt": {
        "⚠  No network connection": "⚠  Sem ligação de rede",
        "Online features are paused until the connection returns — emulators and the SD Card tools still work.":
            "As funções online ficam em pausa até a ligação voltar — os emuladores e as ferramentas de SD Card continuam a funcionar.",
        "✅  Network restored": "✅  Rede restabelecida",
        "Online features are back.": "As funções online estão de volta.",
        "✅  CSpect installed": "✅  CSpect instalado",
        "✅  Emulator(s) detected": "✅  Emulador(es) detetado(s)",
        "⚠  No emulators detected": "⚠  Nenhum emulador detetado",
        "✅  hdfmonkey installed": "✅  hdfmonkey instalado",
        "✅  Send to SD card complete": "✅  Envio para o cartão SD concluído",
        "NextSync server not started": "Servidor NextSync não iniciado",
        "⚠  MAME needs the Next boot ROM": "⚠  O MAME precisa da ROM de arranque do Next",
        "✅  MAME installed": "✅  MAME instalado",
        "⚠  MAME install failed": "⚠  Falha na instalação do MAME",
        "✅  CSpect updated": "✅  CSpect atualizado",
        "⚠  No disk image selected/found for your emulator": "⚠  Nenhuma imagem de disco selecionada/encontrada para o seu emulador",
        "Classic NextSync server not started": "Servidor NextSync clássico não iniciado",
        "NextSync HTTP bridge started": "Ponte HTTP NextSync iniciada",
        "NextSync HTTP bridge not started": "Ponte HTTP NextSync não iniciada",
        "Remote Explorer NextSync server not started": "Servidor NextSync do Remote Explorer não iniciado",
        "Remote Explorer autostart not enabled": "Arranque automático do Remote Explorer não ativado",
        "Define a sync root folder first, on the NextSync tab's Remote Explorer view.":
            "Define primeiro uma pasta raiz de sincronização na vista Remote Explorer do separador NextSync.",
        "Next connected": "Next ligado",
        "A Next is now connected to the NextSync Remote Explorer.":
            "Um Next está agora ligado ao Remote Explorer do NextSync.",
        "Next disconnected": "Next desligado",
        "The Next disconnected from the NextSync Remote Explorer.":
            "O Next desligou-se do Remote Explorer do NextSync.",
        "Cancelling — will stop once the current file has finished transferring, to avoid file corruption…":
            "A cancelar — vai parar quando o ficheiro atual acabar de ser transferido, para evitar a corrupção de ficheiros…",
        "NextSync server started": "Servidor NextSync iniciado",
        "You have started a Remote Explorer nextsync server already": "Já iniciou um servidor NextSync do Remote Explorer",
        "✅  Sent via Remote Explorer": "✅  Enviado através do Remote Explorer",
        "Remote Explorer is busy": "O Remote Explorer está ocupado",
        "⚠  Remote copy interrupted": "⚠  Cópia remota interrompida",
        "✅  Remote copy complete": "✅  Cópia remota concluída",
        "A Next operation failed": "Uma operação no Next falhou",
        "{n} Next operations failed": "{n} operações no Next falharam",
        "Folder unavailable": "Pasta indisponível",
        "Remote unzip failed": "Falha na descompressão remota",
        "Remote unzip": "Descompressão remota",
        "Remote unzip refused": "Descompressão remota recusada",
        "✅  Remote unzip complete": "✅  Descompressão remota concluída",
        "Remote zip": "Compressão remota",
        "Remote zip failed": "Falha na compressão remota",
        "Found: {emulators}.": "Encontrado: {emulators}.",
        " and ": " e ",
        "Mame: via Flatpak ({app})": "Mame: via Flatpak ({app})",
        "⚠ On Windows, CSpect needs <b>OpenAL 1.1</b> for sound. If you have no audio, install it from <a href=\"https://www.openal.org/\">openal.org</a>.":
            "⚠ No Windows, o CSpect precisa do <b>OpenAL 1.1</b> para o som. Se não tiver áudio, instale-o a partir de <a href=\"https://www.openal.org/\">openal.org</a>.",
        "Neither CSpect nor Mame were found. Add the emulator(s) to your operating system PATH environment variable so they can be launched from here. \r\n\r\nCSpect: https://mdf200.itch.io/cspect \r\nMame: https://wiki.specnext.dev/MAME:Installing":
            "Não foram encontrados nem o CSpect nem o Mame. Adicione o(s) emulador(es) à variável de ambiente PATH do sistema operativo para poderem ser lançados a partir daqui. \r\n\r\nCSpect: https://mdf200.itch.io/cspect \r\nMame: https://wiki.specnext.dev/MAME:Installing",
        "hdfmonkey has been installed and is ready to use.": "O hdfmonkey foi instalado e está pronto a usar.",
        "Location: {path}": "Localização: {path}",
        "The file was sent to the SD card image.": "O ficheiro foi enviado para a imagem do cartão SD.",
        "Sent to SD card image:\n{name}": "Enviado para a imagem do cartão SD:\n{name}",
        "Sent {ok}/{n} file(s) to SD card image:\n{dir}": "{ok}/{n} ficheiro(s) enviado(s) para a imagem do cartão SD:\n{dir}",
        "Port {port} is already in use.\nIs another ZX-Next-Unite instance (or a standalone NextSync server) already running?":
            "A porta {port} já está a ser utilizada.\nHá outra instância do ZX-Next-Unite (ou um servidor NextSync autónomo) já em execução?",
        "MAME can't run without the TBBLUE boot ROM — a manual step.\r\nSee {url} → \"Get TBBLUE\".\r\nPut tbblue.zip into MAME's roms folder (downloads\\mame\\roms) — DON'T extract it.\r\nUse only a legally acquired, licensed ROM.":
            "O MAME não funciona sem a ROM de arranque TBBLUE — um passo manual.\r\nVeja {url} → «Get TBBLUE».\r\nColoque o tbblue.zip na pasta roms do MAME (downloads\\mame\\roms) — NÃO o extraia.\r\nUse apenas uma ROM adquirida legalmente e licenciada.",
        "MAME is installed — no restart needed.\r\nManual step: add the TBBLUE boot ROM.\r\nSee {url} → \"Get TBBLUE\".\r\nPut tbblue.zip into MAME's roms folder\r\n({roms})\r\n— DON'T extract it.\r\nUse only a legally acquired, licensed ROM.":
            "O MAME está instalado — não é preciso reiniciar.\r\nPasso manual: adicione a ROM de arranque TBBLUE.\r\nVeja {url} → «Get TBBLUE».\r\nColoque o tbblue.zip na pasta roms do MAME\r\n({roms})\r\n— NÃO o extraia.\r\nUse apenas uma ROM adquirida legalmente e licenciada.",
        "The archive was extracted but mame.exe could not be located in downloads/mame.":
            "O arquivo foi extraído mas o mame.exe não foi encontrado em downloads/mame.",
        "CSpect {name} is installed — no restart needed.\r\n{extracted}":
            "O CSpect {name} está instalado — não é preciso reiniciar.\r\n{extracted}",
        "To start an emulator please select first a disk image at the top of the screen on the SD Card Utility tab.":
            "Para iniciar um emulador, selecione primeiro uma imagem de disco no topo do ecrã, no separador SD Card Utility.",
        "You have already started a Remote Explorer nextsync server, please stop it first.":
            "Já iniciou um servidor NextSync do Remote Explorer; pare-o primeiro.",
        "You have already started a Classic nextsync server, please stop it first.":
            "Já iniciou um servidor NextSync clássico; pare-o primeiro.",
        "Serving on port {port}. A Next with the .http dot command (or curl) can now drive the Next connected in '.sync5 -L' (-l or -listen).":
            "A servir na porta {port}. Um Next com o comando dot .http (ou curl) já pode controlar o Next ligado em «.sync5 -L» (-l ou -listen).",
        "You have specified to start the flask integration server but port {port} is already in use, the web server has not been started.":
            "Indicou iniciar o servidor de integração Flask mas a porta {port} já está em uso; o servidor web não foi iniciado.",
        "You can now start your Next {command} dot command.":
            "Já pode executar o comando dot {command} no seu Next.",
        "Start '.sync5 -L' (-l or -listen) on your Next and retry again (canceling the upload / send process for now).":
            "Execute «.sync5 -L» (-l ou -listen) no seu Next e tente novamente (o envio é cancelado por agora).",
        "file {path}": "ficheiro {path}",
        "{n} files:": "{n} ficheiros:",
        "…and {n} more": "…e mais {n}",
        "Another transfer is still running — wait for it to finish, then try again.":
            "Outra transferência ainda está em curso — aguarde que termine e tente novamente.",
        "The connection to the Next ended before the copy finished; its state is unknown.":
            "A ligação ao Next terminou antes de a cópia acabar; o seu estado é desconhecido.",
        "Copied {n} item(s) on the Next.": "{n} item(ns) copiado(s) no Next.",
        "{path} no longer exists on the Next.\nReturned to {root}.":
            "{path} já não existe no Next.\nRegressou a {root}.",
        "Could not extract {name}: {error}": "Não foi possível extrair {name}: {error}",
        "{name} contains no extractable files.": "{name} não contém ficheiros extraíveis.",
        "Unzipping needs {need}, but drive {drive}: only has {free} free.":
            "Descomprimir precisa de {need}, mas a unidade {drive}: só tem {free} livres.",
        "Extracted {files} file(s) from {name} into {cwd}.":
            "{files} ficheiro(s) extraído(s) de {name} para {cwd}.",
        "{skipped} unsafe entries skipped": "{skipped} entradas não seguras ignoradas",
        "Nothing was downloaded — no zip was created.": "Nada foi transferido — nenhum zip foi criado.",
        "Could not build {zip_name}: {error}": "Não foi possível criar {zip_name}: {error}",
        "Remote zip refused": "Compressão remota recusada",
        "✅  Remote zip complete": "✅  Compressão remota concluída",
        "Unzip failed": "Falha na descompressão",
        "✅  Unzip complete": "✅  Descompressão concluída",
        "Zip failed": "Falha na compressão",
        "✅  Zip complete": "✅  Compressão concluída",
        "{zip_name} is {size}, but drive {drive}: only has {free} free.":
            "{zip_name} ocupa {size}, mas a unidade {drive}: só tem {free} livres.",
        "Created {zip_name} in {dest} ({files} file(s), {size}).":
            "{zip_name} criado em {dest} ({files} ficheiro(s), {size}).",
        "Created {zip_name} in {dest} ({files} file(s)).":
            "{zip_name} criado em {dest} ({files} ficheiro(s)).",
        "Could not create {zip_name}: {error}": "Não foi possível criar {zip_name}: {error}",
    },
    "pl": {
        "⚠  No network connection": "⚠  Brak połączenia sieciowego",
        "Online features are paused until the connection returns — emulators and the SD Card tools still work.":
            "Funkcje online są wstrzymane do powrotu połączenia — emulatory i narzędzia SD Card nadal działają.",
        "✅  Network restored": "✅  Sieć przywrócona",
        "Online features are back.": "Funkcje online wróciły.",
        "✅  CSpect installed": "✅  CSpect zainstalowany",
        "✅  Emulator(s) detected": "✅  Wykryto emulator(y)",
        "⚠  No emulators detected": "⚠  Nie wykryto emulatorów",
        "✅  hdfmonkey installed": "✅  hdfmonkey zainstalowany",
        "✅  Send to SD card complete": "✅  Wysyłanie na kartę SD zakończone",
        "NextSync server not started": "Serwer NextSync nie został uruchomiony",
        "⚠  MAME needs the Next boot ROM": "⚠  MAME wymaga ROM-u startowego Next",
        "✅  MAME installed": "✅  MAME zainstalowany",
        "⚠  MAME install failed": "⚠  Instalacja MAME nie powiodła się",
        "✅  CSpect updated": "✅  CSpect zaktualizowany",
        "⚠  No disk image selected/found for your emulator": "⚠  Nie wybrano/znaleziono obrazu dysku dla emulatora",
        "Classic NextSync server not started": "Klasyczny serwer NextSync nie został uruchomiony",
        "NextSync HTTP bridge started": "Mostek HTTP NextSync uruchomiony",
        "NextSync HTTP bridge not started": "Mostek HTTP NextSync nie został uruchomiony",
        "Remote Explorer NextSync server not started": "Serwer NextSync Remote Explorera nie został uruchomiony",
        "Remote Explorer autostart not enabled": "Autostart Remote Explorera nie został włączony",
        "Define a sync root folder first, on the NextSync tab's Remote Explorer view.":
            "Najpierw wskaż folder główny synchronizacji w widoku Remote Explorer na karcie NextSync.",
        "Next connected": "Next połączony",
        "A Next is now connected to the NextSync Remote Explorer.":
            "Next jest teraz połączony z Remote Explorerem NextSync.",
        "Next disconnected": "Next rozłączony",
        "The Next disconnected from the NextSync Remote Explorer.":
            "Next rozłączył się z Remote Explorerem NextSync.",
        "Cancelling — will stop once the current file has finished transferring, to avoid file corruption…":
            "Anulowanie — zatrzyma się, gdy bieżący plik zakończy przesyłanie, aby uniknąć uszkodzenia plików…",
        "NextSync server started": "Serwer NextSync uruchomiony",
        "You have started a Remote Explorer nextsync server already": "Serwer NextSync Remote Explorera jest już uruchomiony",
        "✅  Sent via Remote Explorer": "✅  Wysłano przez Remote Explorer",
        "Remote Explorer is busy": "Remote Explorer jest zajęty",
        "⚠  Remote copy interrupted": "⚠  Zdalne kopiowanie przerwane",
        "✅  Remote copy complete": "✅  Zdalne kopiowanie zakończone",
        "A Next operation failed": "Operacja na Next nie powiodła się",
        "{n} Next operations failed": "{n} operacji na Next nie powiodło się",
        "Folder unavailable": "Folder niedostępny",
        "Remote unzip failed": "Zdalne rozpakowywanie nie powiodło się",
        "Remote unzip": "Zdalne rozpakowywanie",
        "Remote unzip refused": "Zdalne rozpakowywanie odrzucone",
        "✅  Remote unzip complete": "✅  Zdalne rozpakowywanie zakończone",
        "Remote zip": "Zdalne pakowanie",
        "Remote zip failed": "Zdalne pakowanie nie powiodło się",
        "Found: {emulators}.": "Znaleziono: {emulators}.",
        " and ": " i ",
        "Mame: via Flatpak ({app})": "Mame: przez Flatpak ({app})",
        "⚠ On Windows, CSpect needs <b>OpenAL 1.1</b> for sound. If you have no audio, install it from <a href=\"https://www.openal.org/\">openal.org</a>.":
            "⚠ W systemie Windows CSpect wymaga <b>OpenAL 1.1</b> do dźwięku. Jeśli nie ma dźwięku, zainstaluj go ze strony <a href=\"https://www.openal.org/\">openal.org</a>.",
        "Neither CSpect nor Mame were found. Add the emulator(s) to your operating system PATH environment variable so they can be launched from here. \r\n\r\nCSpect: https://mdf200.itch.io/cspect \r\nMame: https://wiki.specnext.dev/MAME:Installing":
            "Nie znaleziono ani CSpect, ani Mame. Dodaj emulator(y) do zmiennej środowiskowej PATH systemu, aby można je było stąd uruchamiać. \r\n\r\nCSpect: https://mdf200.itch.io/cspect \r\nMame: https://wiki.specnext.dev/MAME:Installing",
        "hdfmonkey has been installed and is ready to use.": "hdfmonkey został zainstalowany i jest gotowy do użycia.",
        "Location: {path}": "Lokalizacja: {path}",
        "The file was sent to the SD card image.": "Plik został wysłany do obrazu karty SD.",
        "Sent to SD card image:\n{name}": "Wysłano do obrazu karty SD:\n{name}",
        "Sent {ok}/{n} file(s) to SD card image:\n{dir}": "Wysłano {ok}/{n} plik(ów) do obrazu karty SD:\n{dir}",
        "Port {port} is already in use.\nIs another ZX-Next-Unite instance (or a standalone NextSync server) already running?":
            "Port {port} jest już zajęty.\nCzy działa już inna instancja ZX-Next-Unite (lub samodzielny serwer NextSync)?",
        "MAME can't run without the TBBLUE boot ROM — a manual step.\r\nSee {url} → \"Get TBBLUE\".\r\nPut tbblue.zip into MAME's roms folder (downloads\\mame\\roms) — DON'T extract it.\r\nUse only a legally acquired, licensed ROM.":
            "MAME nie działa bez ROM-u startowego TBBLUE — krok ręczny.\r\nZobacz {url} → „Get TBBLUE\".\r\nUmieść tbblue.zip w folderze roms MAME (downloads\\mame\\roms) — NIE rozpakowuj go.\r\nUżywaj wyłącznie legalnie nabytego, licencjonowanego ROM-u.",
        "MAME is installed — no restart needed.\r\nManual step: add the TBBLUE boot ROM.\r\nSee {url} → \"Get TBBLUE\".\r\nPut tbblue.zip into MAME's roms folder\r\n({roms})\r\n— DON'T extract it.\r\nUse only a legally acquired, licensed ROM.":
            "MAME jest zainstalowany — restart nie jest potrzebny.\r\nKrok ręczny: dodaj ROM startowy TBBLUE.\r\nZobacz {url} → „Get TBBLUE\".\r\nUmieść tbblue.zip w folderze roms MAME\r\n({roms})\r\n— NIE rozpakowuj go.\r\nUżywaj wyłącznie legalnie nabytego, licencjonowanego ROM-u.",
        "The archive was extracted but mame.exe could not be located in downloads/mame.":
            "Archiwum zostało rozpakowane, ale nie znaleziono mame.exe w downloads/mame.",
        "CSpect {name} is installed — no restart needed.\r\n{extracted}":
            "CSpect {name} jest zainstalowany — restart nie jest potrzebny.\r\n{extracted}",
        "To start an emulator please select first a disk image at the top of the screen on the SD Card Utility tab.":
            "Aby uruchomić emulator, najpierw wybierz obraz dysku u góry ekranu na karcie SD Card Utility.",
        "You have already started a Remote Explorer nextsync server, please stop it first.":
            "Serwer NextSync Remote Explorera jest już uruchomiony — najpierw go zatrzymaj.",
        "You have already started a Classic nextsync server, please stop it first.":
            "Klasyczny serwer NextSync jest już uruchomiony — najpierw go zatrzymaj.",
        "Serving on port {port}. A Next with the .http dot command (or curl) can now drive the Next connected in '.sync5 -L' (-l or -listen).":
            "Serwer działa na porcie {port}. Next z poleceniem dot .http (lub curl) może teraz sterować Nextem połączonym w „.sync5 -L\" (-l lub -listen).",
        "You have specified to start the flask integration server but port {port} is already in use, the web server has not been started.":
            "Wybrano uruchomienie serwera integracji Flask, ale port {port} jest już zajęty — serwer WWW nie został uruchomiony.",
        "You can now start your Next {command} dot command.":
            "Możesz teraz uruchomić polecenie dot {command} na swoim Next.",
        "Start '.sync5 -L' (-l or -listen) on your Next and retry again (canceling the upload / send process for now).":
            "Uruchom „.sync5 -L\" (-l lub -listen) na swoim Next i spróbuj ponownie (wysyłanie na razie anulowano).",
        "file {path}": "plik {path}",
        "{n} files:": "{n} plików:",
        "…and {n} more": "…i {n} więcej",
        "Another transfer is still running — wait for it to finish, then try again.":
            "Inny transfer wciąż trwa — poczekaj, aż się zakończy, i spróbuj ponownie.",
        "The connection to the Next ended before the copy finished; its state is unknown.":
            "Połączenie z Next zakończyło się przed ukończeniem kopiowania; jego stan jest nieznany.",
        "Copied {n} item(s) on the Next.": "Skopiowano {n} element(ów) na Next.",
        "{path} no longer exists on the Next.\nReturned to {root}.":
            "{path} już nie istnieje na Next.\nPowrócono do {root}.",
        "Could not extract {name}: {error}": "Nie można rozpakować {name}: {error}",
        "{name} contains no extractable files.": "{name} nie zawiera plików do rozpakowania.",
        "Unzipping needs {need}, but drive {drive}: only has {free} free.":
            "Rozpakowanie wymaga {need}, ale dysk {drive}: ma tylko {free} wolnego.",
        "Extracted {files} file(s) from {name} into {cwd}.":
            "Rozpakowano {files} plik(ów) z {name} do {cwd}.",
        "{skipped} unsafe entries skipped": "pominięto {skipped} niebezpiecznych wpisów",
        "Nothing was downloaded — no zip was created.": "Nic nie pobrano — nie utworzono pliku zip.",
        "Could not build {zip_name}: {error}": "Nie można utworzyć {zip_name}: {error}",
        "Remote zip refused": "Zdalne pakowanie odrzucone",
        "✅  Remote zip complete": "✅  Zdalne pakowanie zakończone",
        "Unzip failed": "Rozpakowywanie nie powiodło się",
        "✅  Unzip complete": "✅  Rozpakowywanie zakończone",
        "Zip failed": "Pakowanie nie powiodło się",
        "✅  Zip complete": "✅  Pakowanie zakończone",
        "{zip_name} is {size}, but drive {drive}: only has {free} free.":
            "{zip_name} ma {size}, ale dysk {drive}: ma tylko {free} wolnego.",
        "Created {zip_name} in {dest} ({files} file(s), {size}).":
            "Utworzono {zip_name} w {dest} ({files} plik(ów), {size}).",
        "Created {zip_name} in {dest} ({files} file(s)).":
            "Utworzono {zip_name} w {dest} ({files} plik(ów)).",
        "Could not create {zip_name}: {error}": "Nie można utworzyć {zip_name}: {error}",
    },
    "ru": {
        "⚠  No network connection": "⚠  Нет сетевого подключения",
        "Online features are paused until the connection returns — emulators and the SD Card tools still work.":
            "Онлайн-функции приостановлены до восстановления подключения — эмуляторы и инструменты SD Card продолжают работать.",
        "✅  Network restored": "✅  Сеть восстановлена",
        "Online features are back.": "Онлайн-функции снова доступны.",
        "✅  CSpect installed": "✅  CSpect установлен",
        "✅  Emulator(s) detected": "✅  Обнаружен(ы) эмулятор(ы)",
        "⚠  No emulators detected": "⚠  Эмуляторы не обнаружены",
        "✅  hdfmonkey installed": "✅  hdfmonkey установлен",
        "✅  Send to SD card complete": "✅  Отправка на SD-карту завершена",
        "NextSync server not started": "Сервер NextSync не запущен",
        "⚠  MAME needs the Next boot ROM": "⚠  MAME требуется загрузочный ROM Next",
        "✅  MAME installed": "✅  MAME установлен",
        "⚠  MAME install failed": "⚠  Не удалось установить MAME",
        "✅  CSpect updated": "✅  CSpect обновлён",
        "⚠  No disk image selected/found for your emulator": "⚠  Образ диска для эмулятора не выбран/не найден",
        "Classic NextSync server not started": "Классический сервер NextSync не запущен",
        "NextSync HTTP bridge started": "HTTP-мост NextSync запущен",
        "NextSync HTTP bridge not started": "HTTP-мост NextSync не запущен",
        "Remote Explorer NextSync server not started": "Сервер NextSync Remote Explorer не запущен",
        "Remote Explorer autostart not enabled": "Автозапуск Remote Explorer не включён",
        "Define a sync root folder first, on the NextSync tab's Remote Explorer view.":
            "Сначала задайте корневую папку синхронизации в виде Remote Explorer на вкладке NextSync.",
        "Next connected": "Next подключён",
        "A Next is now connected to the NextSync Remote Explorer.":
            "Next подключён к Remote Explorer NextSync.",
        "Next disconnected": "Next отключён",
        "The Next disconnected from the NextSync Remote Explorer.":
            "Next отключился от Remote Explorer NextSync.",
        "Cancelling — will stop once the current file has finished transferring, to avoid file corruption…":
            "Отмена — остановится, когда завершится передача текущего файла, чтобы избежать повреждения файлов…",
        "NextSync server started": "Сервер NextSync запущен",
        "You have started a Remote Explorer nextsync server already": "Сервер NextSync Remote Explorer уже запущен",
        "✅  Sent via Remote Explorer": "✅  Отправлено через Remote Explorer",
        "Remote Explorer is busy": "Remote Explorer занят",
        "⚠  Remote copy interrupted": "⚠  Удалённое копирование прервано",
        "✅  Remote copy complete": "✅  Удалённое копирование завершено",
        "A Next operation failed": "Операция на Next не удалась",
        "{n} Next operations failed": "Не удалось {n} операций на Next",
        "Folder unavailable": "Папка недоступна",
        "Remote unzip failed": "Удалённая распаковка не удалась",
        "Remote unzip": "Удалённая распаковка",
        "Remote unzip refused": "Удалённая распаковка отклонена",
        "✅  Remote unzip complete": "✅  Удалённая распаковка завершена",
        "Remote zip": "Удалённое архивирование",
        "Remote zip failed": "Удалённое архивирование не удалось",
        "Found: {emulators}.": "Найдено: {emulators}.",
        " and ": " и ",
        "Mame: via Flatpak ({app})": "Mame: через Flatpak ({app})",
        "⚠ On Windows, CSpect needs <b>OpenAL 1.1</b> for sound. If you have no audio, install it from <a href=\"https://www.openal.org/\">openal.org</a>.":
            "⚠ В Windows для звука CSpect требуется <b>OpenAL 1.1</b>. Если звука нет, установите его с <a href=\"https://www.openal.org/\">openal.org</a>.",
        "Neither CSpect nor Mame were found. Add the emulator(s) to your operating system PATH environment variable so they can be launched from here. \r\n\r\nCSpect: https://mdf200.itch.io/cspect \r\nMame: https://wiki.specnext.dev/MAME:Installing":
            "Не найдены ни CSpect, ни Mame. Добавьте эмулятор(ы) в переменную окружения PATH, чтобы их можно было запускать отсюда. \r\n\r\nCSpect: https://mdf200.itch.io/cspect \r\nMame: https://wiki.specnext.dev/MAME:Installing",
        "hdfmonkey has been installed and is ready to use.": "hdfmonkey установлен и готов к использованию.",
        "Location: {path}": "Расположение: {path}",
        "The file was sent to the SD card image.": "Файл отправлен в образ SD-карты.",
        "Sent to SD card image:\n{name}": "Отправлено в образ SD-карты:\n{name}",
        "Sent {ok}/{n} file(s) to SD card image:\n{dir}": "Отправлено файлов: {ok}/{n} в образ SD-карты:\n{dir}",
        "Port {port} is already in use.\nIs another ZX-Next-Unite instance (or a standalone NextSync server) already running?":
            "Порт {port} уже занят.\nВозможно, уже запущена другая копия ZX-Next-Unite (или отдельный сервер NextSync)?",
        "MAME can't run without the TBBLUE boot ROM — a manual step.\r\nSee {url} → \"Get TBBLUE\".\r\nPut tbblue.zip into MAME's roms folder (downloads\\mame\\roms) — DON'T extract it.\r\nUse only a legally acquired, licensed ROM.":
            "MAME не работает без загрузочного ROM TBBLUE — это ручной шаг.\r\nСм. {url} → «Get TBBLUE».\r\nПоложите tbblue.zip в папку roms MAME (downloads\\mame\\roms) — НЕ распаковывайте его.\r\nИспользуйте только легально приобретённый лицензионный ROM.",
        "MAME is installed — no restart needed.\r\nManual step: add the TBBLUE boot ROM.\r\nSee {url} → \"Get TBBLUE\".\r\nPut tbblue.zip into MAME's roms folder\r\n({roms})\r\n— DON'T extract it.\r\nUse only a legally acquired, licensed ROM.":
            "MAME установлен — перезапуск не требуется.\r\nРучной шаг: добавьте загрузочный ROM TBBLUE.\r\nСм. {url} → «Get TBBLUE».\r\nПоложите tbblue.zip в папку roms MAME\r\n({roms})\r\n— НЕ распаковывайте его.\r\nИспользуйте только легально приобретённый лицензионный ROM.",
        "The archive was extracted but mame.exe could not be located in downloads/mame.":
            "Архив распакован, но mame.exe не найден в downloads/mame.",
        "CSpect {name} is installed — no restart needed.\r\n{extracted}":
            "CSpect {name} установлен — перезапуск не требуется.\r\n{extracted}",
        "To start an emulator please select first a disk image at the top of the screen on the SD Card Utility tab.":
            "Чтобы запустить эмулятор, сначала выберите образ диска вверху экрана на вкладке SD Card Utility.",
        "You have already started a Remote Explorer nextsync server, please stop it first.":
            "Сервер NextSync Remote Explorer уже запущен — сначала остановите его.",
        "You have already started a Classic nextsync server, please stop it first.":
            "Классический сервер NextSync уже запущен — сначала остановите его.",
        "Serving on port {port}. A Next with the .http dot command (or curl) can now drive the Next connected in '.sync5 -L' (-l or -listen).":
            "Работает на порту {port}. Next с dot-командой .http (или curl) теперь может управлять Next, подключённым в «.sync5 -L» (-l или -listen).",
        "You have specified to start the flask integration server but port {port} is already in use, the web server has not been started.":
            "Вы указали запустить сервер интеграции Flask, но порт {port} уже занят — веб-сервер не запущен.",
        "You can now start your Next {command} dot command.":
            "Теперь можно запустить dot-команду {command} на вашем Next.",
        "Start '.sync5 -L' (-l or -listen) on your Next and retry again (canceling the upload / send process for now).":
            "Запустите «.sync5 -L» (-l или -listen) на вашем Next и повторите попытку (отправка пока отменена).",
        "file {path}": "файл {path}",
        "{n} files:": "{n} файлов:",
        "…and {n} more": "…и ещё {n}",
        "Another transfer is still running — wait for it to finish, then try again.":
            "Другая передача ещё выполняется — дождитесь её завершения и повторите попытку.",
        "The connection to the Next ended before the copy finished; its state is unknown.":
            "Соединение с Next прервалось до завершения копирования; его состояние неизвестно.",
        "Copied {n} item(s) on the Next.": "Скопировано элементов на Next: {n}.",
        "{path} no longer exists on the Next.\nReturned to {root}.":
            "{path} больше не существует на Next.\nВыполнен возврат в {root}.",
        "Could not extract {name}: {error}": "Не удалось распаковать {name}: {error}",
        "{name} contains no extractable files.": "{name} не содержит файлов для распаковки.",
        "Unzipping needs {need}, but drive {drive}: only has {free} free.":
            "Для распаковки нужно {need}, но на диске {drive}: свободно только {free}.",
        "Extracted {files} file(s) from {name} into {cwd}.":
            "Извлечено файлов: {files} из {name} в {cwd}.",
        "{skipped} unsafe entries skipped": "пропущено небезопасных записей: {skipped}",
        "Nothing was downloaded — no zip was created.": "Ничего не загружено — zip не создан.",
        "Could not build {zip_name}: {error}": "Не удалось создать {zip_name}: {error}",
        "Remote zip refused": "Удалённое архивирование отклонено",
        "✅  Remote zip complete": "✅  Удалённое архивирование завершено",
        "Unzip failed": "Распаковка не удалась",
        "✅  Unzip complete": "✅  Распаковка завершена",
        "Zip failed": "Архивирование не удалось",
        "✅  Zip complete": "✅  Архивирование завершено",
        "{zip_name} is {size}, but drive {drive}: only has {free} free.":
            "{zip_name} занимает {size}, но на диске {drive}: свободно только {free}.",
        "Created {zip_name} in {dest} ({files} file(s), {size}).":
            "Создан {zip_name} в {dest} ({files} файлов, {size}).",
        "Created {zip_name} in {dest} ({files} file(s)).":
            "Создан {zip_name} в {dest} ({files} файлов).",
        "Could not create {zip_name}: {error}": "Не удалось создать {zip_name}: {error}",
    },
    "cs": {
        "⚠  No network connection": "⚠  Žádné síťové připojení",
        "Online features are paused until the connection returns — emulators and the SD Card tools still work.":
            "Online funkce jsou pozastaveny do návratu připojení — emulátory a nástroje SD Card fungují dál.",
        "✅  Network restored": "✅  Síť obnovena",
        "Online features are back.": "Online funkce jsou zpět.",
        "✅  CSpect installed": "✅  CSpect nainstalován",
        "✅  Emulator(s) detected": "✅  Emulátor(y) nalezen(y)",
        "⚠  No emulators detected": "⚠  Nebyly nalezeny žádné emulátory",
        "✅  hdfmonkey installed": "✅  hdfmonkey nainstalován",
        "✅  Send to SD card complete": "✅  Odeslání na SD kartu dokončeno",
        "NextSync server not started": "Server NextSync nebyl spuštěn",
        "⚠  MAME needs the Next boot ROM": "⚠  MAME potřebuje zaváděcí ROM Nextu",
        "✅  MAME installed": "✅  MAME nainstalován",
        "⚠  MAME install failed": "⚠  Instalace MAME selhala",
        "✅  CSpect updated": "✅  CSpect aktualizován",
        "⚠  No disk image selected/found for your emulator": "⚠  Nebyl vybrán/nalezen obraz disku pro emulátor",
        "Classic NextSync server not started": "Klasický server NextSync nebyl spuštěn",
        "NextSync HTTP bridge started": "HTTP most NextSync spuštěn",
        "NextSync HTTP bridge not started": "HTTP most NextSync nebyl spuštěn",
        "Remote Explorer NextSync server not started": "Server NextSync Remote Exploreru nebyl spuštěn",
        "Remote Explorer autostart not enabled": "Automatické spuštění Remote Exploreru nebylo zapnuto",
        "Define a sync root folder first, on the NextSync tab's Remote Explorer view.":
            "Nejprve nastavte kořenovou složku synchronizace v zobrazení Remote Explorer na kartě NextSync.",
        "Next connected": "Next připojen",
        "A Next is now connected to the NextSync Remote Explorer.":
            "Next je nyní připojen k Remote Exploreru NextSync.",
        "Next disconnected": "Next odpojen",
        "The Next disconnected from the NextSync Remote Explorer.":
            "Next se odpojil od Remote Exploreru NextSync.",
        "Cancelling — will stop once the current file has finished transferring, to avoid file corruption…":
            "Rušení — zastaví se, jakmile se dokončí přenos aktuálního souboru, aby nedošlo k poškození souborů…",
        "NextSync server started": "Server NextSync spuštěn",
        "You have started a Remote Explorer nextsync server already": "Server NextSync Remote Exploreru už běží",
        "✅  Sent via Remote Explorer": "✅  Odesláno přes Remote Explorer",
        "Remote Explorer is busy": "Remote Explorer je zaneprázdněn",
        "⚠  Remote copy interrupted": "⚠  Vzdálené kopírování přerušeno",
        "✅  Remote copy complete": "✅  Vzdálené kopírování dokončeno",
        "A Next operation failed": "Operace na Nextu selhala",
        "{n} Next operations failed": "{n} operací na Nextu selhalo",
        "Folder unavailable": "Složka není dostupná",
        "Remote unzip failed": "Vzdálené rozbalení selhalo",
        "Remote unzip": "Vzdálené rozbalení",
        "Remote unzip refused": "Vzdálené rozbalení odmítnuto",
        "✅  Remote unzip complete": "✅  Vzdálené rozbalení dokončeno",
        "Remote zip": "Vzdálené zabalení",
        "Remote zip failed": "Vzdálené zabalení selhalo",
        "Found: {emulators}.": "Nalezeno: {emulators}.",
        " and ": " a ",
        "Mame: via Flatpak ({app})": "Mame: přes Flatpak ({app})",
        "⚠ On Windows, CSpect needs <b>OpenAL 1.1</b> for sound. If you have no audio, install it from <a href=\"https://www.openal.org/\">openal.org</a>.":
            "⚠ Ve Windows potřebuje CSpect pro zvuk <b>OpenAL 1.1</b>. Pokud nemáte zvuk, nainstalujte jej z <a href=\"https://www.openal.org/\">openal.org</a>.",
        "Neither CSpect nor Mame were found. Add the emulator(s) to your operating system PATH environment variable so they can be launched from here. \r\n\r\nCSpect: https://mdf200.itch.io/cspect \r\nMame: https://wiki.specnext.dev/MAME:Installing":
            "Nebyl nalezen CSpect ani Mame. Přidejte emulátor(y) do proměnné prostředí PATH, aby šly spouštět odtud. \r\n\r\nCSpect: https://mdf200.itch.io/cspect \r\nMame: https://wiki.specnext.dev/MAME:Installing",
        "hdfmonkey has been installed and is ready to use.": "hdfmonkey byl nainstalován a je připraven k použití.",
        "Location: {path}": "Umístění: {path}",
        "The file was sent to the SD card image.": "Soubor byl odeslán do obrazu SD karty.",
        "Sent to SD card image:\n{name}": "Odesláno do obrazu SD karty:\n{name}",
        "Sent {ok}/{n} file(s) to SD card image:\n{dir}": "Odesláno {ok}/{n} souborů do obrazu SD karty:\n{dir}",
        "Port {port} is already in use.\nIs another ZX-Next-Unite instance (or a standalone NextSync server) already running?":
            "Port {port} je již obsazen.\nNeběží už jiná instance ZX-Next-Unite (nebo samostatný server NextSync)?",
        "MAME can't run without the TBBLUE boot ROM — a manual step.\r\nSee {url} → \"Get TBBLUE\".\r\nPut tbblue.zip into MAME's roms folder (downloads\\mame\\roms) — DON'T extract it.\r\nUse only a legally acquired, licensed ROM.":
            "MAME nefunguje bez zaváděcího ROM TBBLUE — ruční krok.\r\nViz {url} → „Get TBBLUE\".\r\nVložte tbblue.zip do složky roms MAME (downloads\\mame\\roms) — NEROZBALUJTE jej.\r\nPoužívejte pouze legálně získaný licencovaný ROM.",
        "MAME is installed — no restart needed.\r\nManual step: add the TBBLUE boot ROM.\r\nSee {url} → \"Get TBBLUE\".\r\nPut tbblue.zip into MAME's roms folder\r\n({roms})\r\n— DON'T extract it.\r\nUse only a legally acquired, licensed ROM.":
            "MAME je nainstalován — restart není potřeba.\r\nRuční krok: přidejte zaváděcí ROM TBBLUE.\r\nViz {url} → „Get TBBLUE\".\r\nVložte tbblue.zip do složky roms MAME\r\n({roms})\r\n— NEROZBALUJTE jej.\r\nPoužívejte pouze legálně získaný licencovaný ROM.",
        "The archive was extracted but mame.exe could not be located in downloads/mame.":
            "Archiv byl rozbalen, ale mame.exe se v downloads/mame nepodařilo najít.",
        "CSpect {name} is installed — no restart needed.\r\n{extracted}":
            "CSpect {name} je nainstalován — restart není potřeba.\r\n{extracted}",
        "To start an emulator please select first a disk image at the top of the screen on the SD Card Utility tab.":
            "Pro spuštění emulátoru nejprve vyberte obraz disku v horní části obrazovky na kartě SD Card Utility.",
        "You have already started a Remote Explorer nextsync server, please stop it first.":
            "Server NextSync Remote Exploreru už běží — nejprve jej zastavte.",
        "You have already started a Classic nextsync server, please stop it first.":
            "Klasický server NextSync už běží — nejprve jej zastavte.",
        "Serving on port {port}. A Next with the .http dot command (or curl) can now drive the Next connected in '.sync5 -L' (-l or -listen).":
            "Běží na portu {port}. Next s dot příkazem .http (nebo curl) teď může ovládat Next připojený v „.sync5 -L\" (-l nebo -listen).",
        "You have specified to start the flask integration server but port {port} is already in use, the web server has not been started.":
            "Zvolili jste spuštění integračního serveru Flask, ale port {port} je již obsazen — webový server nebyl spuštěn.",
        "You can now start your Next {command} dot command.":
            "Nyní můžete spustit dot příkaz {command} na svém Nextu.",
        "Start '.sync5 -L' (-l or -listen) on your Next and retry again (canceling the upload / send process for now).":
            "Spusťte „.sync5 -L\" (-l nebo -listen) na svém Nextu a zkuste to znovu (odesílání je zatím zrušeno).",
        "file {path}": "soubor {path}",
        "{n} files:": "{n} souborů:",
        "…and {n} more": "…a {n} dalších",
        "Another transfer is still running — wait for it to finish, then try again.":
            "Jiný přenos stále běží — počkejte na jeho dokončení a zkuste to znovu.",
        "The connection to the Next ended before the copy finished; its state is unknown.":
            "Spojení s Nextem skončilo před dokončením kopírování; jeho stav není znám.",
        "Copied {n} item(s) on the Next.": "Zkopírováno {n} položek na Nextu.",
        "{path} no longer exists on the Next.\nReturned to {root}.":
            "{path} už na Nextu neexistuje.\nNávrat do {root}.",
        "Could not extract {name}: {error}": "Nelze rozbalit {name}: {error}",
        "{name} contains no extractable files.": "{name} neobsahuje žádné soubory k rozbalení.",
        "Unzipping needs {need}, but drive {drive}: only has {free} free.":
            "Rozbalení vyžaduje {need}, ale disk {drive}: má volných jen {free}.",
        "Extracted {files} file(s) from {name} into {cwd}.":
            "Rozbaleno {files} souborů z {name} do {cwd}.",
        "{skipped} unsafe entries skipped": "přeskočeno {skipped} nebezpečných položek",
        "Nothing was downloaded — no zip was created.": "Nic nebylo staženo — žádný zip nevznikl.",
        "Could not build {zip_name}: {error}": "Nelze vytvořit {zip_name}: {error}",
        "Remote zip refused": "Vzdálené zabalení odmítnuto",
        "✅  Remote zip complete": "✅  Vzdálené zabalení dokončeno",
        "Unzip failed": "Rozbalení selhalo",
        "✅  Unzip complete": "✅  Rozbalení dokončeno",
        "Zip failed": "Zabalení selhalo",
        "✅  Zip complete": "✅  Zabalení dokončeno",
        "{zip_name} is {size}, but drive {drive}: only has {free} free.":
            "{zip_name} má {size}, ale disk {drive}: má volných jen {free}.",
        "Created {zip_name} in {dest} ({files} file(s), {size}).":
            "Vytvořen {zip_name} v {dest} ({files} souborů, {size}).",
        "Created {zip_name} in {dest} ({files} file(s)).":
            "Vytvořen {zip_name} v {dest} ({files} souborů).",
        "Could not create {zip_name}: {error}": "Nelze vytvořit {zip_name}: {error}",
    },
}

for _lang, _entries in _TOAST_CATALOGS.items():
    CATALOGS.setdefault(_lang, {}).update(_entries)
