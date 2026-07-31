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
        'New folder in {path}:': 'Nueva carpeta en {path}:',
        'New Folder…': 'Nueva carpeta…',
        'New name for the {kind}:': 'Nuevo nombre para {kind}:',
        'Not enough space on the Next': 'No hay espacio suficiente en el Next',
        ('Only add a drive that really exists on your Next (an extra SD card reader or '
         'partition). Selecting a drive that is not mounted CRASHES the Next.'):
            ('Añade solo una unidad que exista de verdad en tu Next (un lector SD o '
             'partición adicional). Seleccionar una unidad no montada BLOQUEA el Next.'),
        'Open in {source}': 'Abrir en {source}',
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
        "NextSync — when a sent file or directory exists locally:":
            "NextSync — si un archivo o directorio recibido ya existe localmente:",
        "Page:": "Página:",
        "Port:": "Puerto:",
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
        "Download File": "Descargar archivo",
        "Download NextZXOS Image": "Descargar imagen NextZXOS",
        "Download and install HDF Monkey": "Descargar e instalar HDF Monkey",
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
            "ERROR: no se encontró hdfmonkey. Usa el botón 'Download and install HDF Monkey' (abajo a la derecha de la pestaña SD Card) para instalarlo automáticamente, o haz una instalación completa de CSpect desde la pestaña itch.io, que también incluye hdfmonkey. También se puede instalar manualmente desde https://github.com/gasman/hdfmonkey — reinicia la aplicación una vez instalado.",
        "Extracted disk image: {path}":
            "Imagen de disco extraída: {path}",
        "Extracted {count} file(s) from {name} into {folder} on the image.":
            "Extraído(s) {count} archivo(s) de {name} en {folder} en la imagen.",
        "Failed downloading NextZXOS image: {error}":
            "Error al descargar la imagen NextZXOS: {error}",
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
        "Remote explorer: connected to {address}":
            "Explorador remoto: conectado a {address}",
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
    },
    "pt": {
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
        'New folder in {path}:': 'Nova pasta em {path}:',
        'New Folder…': 'Nova pasta…',
        'New name for the {kind}:': 'Novo nome para {kind}:',
        'Not enough space on the Next': 'Espaço insuficiente no Next',
        ('Only add a drive that really exists on your Next (an extra SD card reader or '
         'partition). Selecting a drive that is not mounted CRASHES the Next.'):
            ('Adiciona apenas uma unidade que exista mesmo no teu Next (um leitor SD '
             'ou partição adicional). Selecionar uma unidade não montada BLOQUEIA o '
             'Next.'),
        'Open in {source}': 'Abrir em {source}',
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
        "NextSync — when a sent file or directory exists locally:":
            "NextSync — se um ficheiro ou diretório recebido já existir localmente:",
        "Page:": "Página:",
        "Port:": "Porta:",
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
        "Download File": "Transferir ficheiro",
        "Download NextZXOS Image": "Transferir imagem NextZXOS",
        "Download and install HDF Monkey": "Transferir e instalar o HDF Monkey",
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
            "ERRO: o hdfmonkey não foi encontrado. Usa o botão 'Download and install HDF Monkey' (canto inferior direito do separador SD Card) para o instalar automaticamente, ou faz uma instalação completa do CSpect a partir do separador itch.io, que também inclui o hdfmonkey. Também pode ser instalado manualmente a partir de https://github.com/gasman/hdfmonkey — reinicia a aplicação depois de instalado.",
        "Extracted disk image: {path}":
            "Imagem de disco extraída: {path}",
        "Extracted {count} file(s) from {name} into {folder} on the image.":
            "Extraído(s) {count} ficheiro(s) de {name} para {folder} na imagem.",
        "Failed downloading NextZXOS image: {error}":
            "Falha ao transferir a imagem NextZXOS: {error}",
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
        "Remote explorer: connected to {address}":
            "Explorador remoto: ligado a {address}",
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
    },
    "pl": {
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
        'New folder in {path}:': 'Nowy folder w {path}:',
        'New Folder…': 'Nowy folder…',
        'New name for the {kind}:': 'Nowa nazwa ({kind}):',
        'Not enough space on the Next': 'Za mało miejsca na Nexcie',
        ('Only add a drive that really exists on your Next (an extra SD card reader or '
         'partition). Selecting a drive that is not mounted CRASHES the Next.'):
            ('Dodawaj tylko dysk, który naprawdę istnieje w Twoim Nexcie (dodatkowy '
             'czytnik SD lub partycja). Wybranie niezamontowanego dysku ZAWIESZA Nexta.'),
        'Open in {source}': 'Otwórz w {source}',
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
        "NextSync — when a sent file or directory exists locally:":
            "NextSync — gdy odebrany plik lub katalog już istnieje lokalnie:",
        "Page:": "Strona:",
        "Port:": "Port:",
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
        "Download File": "Pobierz plik",
        "Download NextZXOS Image": "Pobierz obraz NextZXOS",
        "Download and install HDF Monkey": "Pobierz i zainstaluj HDF Monkey",
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
            "BŁĄD: nie znaleziono hdfmonkey. Użyj przycisku 'Download and install HDF Monkey' (prawy dolny róg karty SD Card), aby zainstalować go automatycznie, albo wykonaj pełną instalację CSpect z karty itch.io, która również zawiera hdfmonkey. Można go też zainstalować ręcznie z https://github.com/gasman/hdfmonkey — po instalacji uruchom aplikację ponownie.",
        "Extracted disk image: {path}":
            "Wypakowano obraz dysku: {path}",
        "Extracted {count} file(s) from {name} into {folder} on the image.":
            "Wypakowano {count} plik(ów) z {name} do {folder} na obrazie.",
        "Failed downloading NextZXOS image: {error}":
            "Nie udało się pobrać obrazu NextZXOS: {error}",
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
        "Remote explorer: connected to {address}":
            "Eksplorator zdalny: połączono z {address}",
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
    },
    "ru": {
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
        'New folder in {path}:': 'Новая папка в {path}:',
        'New Folder…': 'Новая папка…',
        'New name for the {kind}:': 'Новое имя ({kind}):',
        'Not enough space on the Next': 'Недостаточно места на Next',
        ('Only add a drive that really exists on your Next (an extra SD card reader or '
         'partition). Selecting a drive that is not mounted CRASHES the Next.'):
            ('Добавляйте только диск, который действительно есть в вашем Next '
             '(дополнительный SD-ридер или раздел). Выбор несмонтированного диска '
             'ПРИВОДИТ К СБОЮ Next.'),
        'Open in {source}': 'Открыть в {source}',
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
        "NextSync — when a sent file or directory exists locally:":
            "NextSync — если принятый файл или каталог уже существует локально:",
        "Page:": "Страница:",
        "Port:": "Порт:",
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
        "Download File": "Скачать файл",
        "Download NextZXOS Image": "Скачать образ NextZXOS",
        "Download and install HDF Monkey": "Скачать и установить HDF Monkey",
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
            "ОШИБКА: hdfmonkey не найден. Нажмите кнопку 'Download and install HDF Monkey' (внизу справа на вкладке SD Card), чтобы установить его автоматически, или выполните полную установку CSpect со вкладки itch.io — она также включает hdfmonkey. Его можно установить и вручную с https://github.com/gasman/hdfmonkey — после установки перезапустите приложение.",
        "Extracted disk image: {path}":
            "Образ диска распакован: {path}",
        "Extracted {count} file(s) from {name} into {folder} on the image.":
            "Извлечено файлов: {count} из {name} в {folder} на образе.",
        "Failed downloading NextZXOS image: {error}":
            "Не удалось скачать образ NextZXOS: {error}",
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
        "Remote explorer: connected to {address}":
            "Удалённый проводник: подключено к {address}",
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
    },
    "cs": {
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
        'New folder in {path}:': 'Nová složka v {path}:',
        'New Folder…': 'Nová složka…',
        'New name for the {kind}:': 'Nový název ({kind}):',
        'Not enough space on the Next': 'Na Nextu není dost místa',
        ('Only add a drive that really exists on your Next (an extra SD card reader or '
         'partition). Selecting a drive that is not mounted CRASHES the Next.'):
            ('Přidávejte jen jednotku, která na vašem Nextu opravdu existuje (další '
             'čtečka SD nebo oddíl). Výběr nepřipojené jednotky Next SHODÍ.'),
        'Open in {source}': 'Otevřít v {source}',
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
        "NextSync — when a sent file or directory exists locally:":
            "NextSync — když přijatý soubor či adresář už místně existuje:",
        "Page:": "Stránka:",
        "Port:": "Port:",
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
        "Download File": "Stáhnout soubor",
        "Download NextZXOS Image": "Stáhnout obraz NextZXOS",
        "Download and install HDF Monkey": "Stáhnout a nainstalovat HDF Monkey",
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
            "CHYBA: hdfmonkey nebyl nalezen. Použijte tlačítko 'Download and install HDF Monkey' (vpravo dole na kartě SD Card) pro automatickou instalaci, nebo proveďte plnou instalaci CSpectu z karty itch.io, která hdfmonkey rovněž obsahuje. Lze jej nainstalovat i ručně z https://github.com/gasman/hdfmonkey — po instalaci aplikaci restartujte.",
        "Extracted disk image: {path}":
            "Obraz disku rozbalen: {path}",
        "Extracted {count} file(s) from {name} into {folder} on the image.":
            "Rozbaleno {count} souborů z {name} do {folder} na obrazu.",
        "Failed downloading NextZXOS image: {error}":
            "Nepodařilo se stáhnout obraz NextZXOS: {error}",
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
        "Remote explorer: connected to {address}":
            "Vzdálený průzkumník: připojeno k {address}",
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
    },
    "fr": {
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
        'New folder in {path}:': 'Nouveau dossier dans {path} :',
        'New Folder…': 'Nouveau dossier…',
        'New name for the {kind}:': 'Nouveau nom pour {kind} :',
        'Not enough space on the Next': "Pas assez d'espace sur le Next",
        ('Only add a drive that really exists on your Next (an extra SD card reader or '
         'partition). Selecting a drive that is not mounted CRASHES the Next.'):
            ("N'ajoutez qu'un lecteur qui existe vraiment sur votre Next (un lecteur "
             'SD ou une partition supplémentaire). Sélectionner un lecteur non monté '
             'FAIT PLANTER le Next.'),
        'Open in {source}': 'Ouvrir dans {source}',
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
        "NextSync — when a sent file or directory exists locally:":
            "NextSync — si un fichier ou dossier reçu existe déjà en local :",
        "Page:": "Page :",
        "Port:": "Port :",
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
        "Download File": "Télécharger le fichier",
        "Download NextZXOS Image": "Télécharger l'image NextZXOS",
        "Download and install HDF Monkey": "Télécharger et installer HDF Monkey",
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
            "ERREUR : hdfmonkey est introuvable. Utilisez le bouton 'Download and install HDF Monkey' (en bas à droite de l'onglet SD Card) pour l'installer automatiquement, ou faites une installation complète de CSpect depuis l'onglet itch.io, qui inclut aussi hdfmonkey. Il peut également être installé manuellement depuis https://github.com/gasman/hdfmonkey — redémarrez l'application une fois installé.",
        "Extracted disk image: {path}":
            "Image disque extraite : {path}",
        "Extracted {count} file(s) from {name} into {folder} on the image.":
            "{count} fichier(s) extrait(s) de {name} vers {folder} sur l'image.",
        "Failed downloading NextZXOS image: {error}":
            "Échec du téléchargement de l'image NextZXOS : {error}",
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
        "Remote explorer: connected to {address}":
            "Explorateur distant : connecté à {address}",
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
        "Serving on port {port}. A Next with the .http dot command (or curl) can now drive the Next connected in '.sync5 -listen'.":
            "Serveur actif sur le port {port}. Un Next avec la commande dot .http (ou curl) peut maintenant piloter le Next connecté en « .sync5 -listen ».",
        "You have specified to start the flask integration server but port {port} is already in use, the web server has not been started.":
            "Vous avez demandé le démarrage du serveur d'intégration Flask mais le port {port} est déjà utilisé, le serveur web n'a pas été démarré.",
        "Start '.sync5 -listen' on your Next to connect!":
            "Lancez « .sync5 -listen » sur votre Next pour vous connecter !",
        "Start '.sync5 -listen' on your Next and retry again (canceling the upload / send process for now).":
            "Lancez « .sync5 -listen » sur votre Next puis réessayez (envoi annulé pour le moment).",
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
        "Serving on port {port}. A Next with the .http dot command (or curl) can now drive the Next connected in '.sync5 -listen'.":
            "Sirviendo en el puerto {port}. Un Next con el comando dot .http (o curl) ya puede controlar el Next conectado en «.sync5 -listen».",
        "You have specified to start the flask integration server but port {port} is already in use, the web server has not been started.":
            "Ha indicado iniciar el servidor de integración Flask pero el puerto {port} ya está en uso; el servidor web no se ha iniciado.",
        "Start '.sync5 -listen' on your Next to connect!":
            "¡Ejecute «.sync5 -listen» en su Next para conectar!",
        "Start '.sync5 -listen' on your Next and retry again (canceling the upload / send process for now).":
            "Ejecute «.sync5 -listen» en su Next y reintente (el envío se cancela por ahora).",
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
        "Serving on port {port}. A Next with the .http dot command (or curl) can now drive the Next connected in '.sync5 -listen'.":
            "A servir na porta {port}. Um Next com o comando dot .http (ou curl) já pode controlar o Next ligado em «.sync5 -listen».",
        "You have specified to start the flask integration server but port {port} is already in use, the web server has not been started.":
            "Indicou iniciar o servidor de integração Flask mas a porta {port} já está em uso; o servidor web não foi iniciado.",
        "Start '.sync5 -listen' on your Next to connect!":
            "Execute «.sync5 -listen» no seu Next para ligar!",
        "Start '.sync5 -listen' on your Next and retry again (canceling the upload / send process for now).":
            "Execute «.sync5 -listen» no seu Next e tente novamente (o envio é cancelado por agora).",
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
        "Serving on port {port}. A Next with the .http dot command (or curl) can now drive the Next connected in '.sync5 -listen'.":
            "Serwer działa na porcie {port}. Next z poleceniem dot .http (lub curl) może teraz sterować Nextem połączonym w „.sync5 -listen\".",
        "You have specified to start the flask integration server but port {port} is already in use, the web server has not been started.":
            "Wybrano uruchomienie serwera integracji Flask, ale port {port} jest już zajęty — serwer WWW nie został uruchomiony.",
        "Start '.sync5 -listen' on your Next to connect!":
            "Uruchom „.sync5 -listen\" na swoim Next, aby się połączyć!",
        "Start '.sync5 -listen' on your Next and retry again (canceling the upload / send process for now).":
            "Uruchom „.sync5 -listen\" na swoim Next i spróbuj ponownie (wysyłanie na razie anulowano).",
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
        "Serving on port {port}. A Next with the .http dot command (or curl) can now drive the Next connected in '.sync5 -listen'.":
            "Работает на порту {port}. Next с dot-командой .http (или curl) теперь может управлять Next, подключённым в «.sync5 -listen».",
        "You have specified to start the flask integration server but port {port} is already in use, the web server has not been started.":
            "Вы указали запустить сервер интеграции Flask, но порт {port} уже занят — веб-сервер не запущен.",
        "Start '.sync5 -listen' on your Next to connect!":
            "Запустите «.sync5 -listen» на вашем Next для подключения!",
        "Start '.sync5 -listen' on your Next and retry again (canceling the upload / send process for now).":
            "Запустите «.sync5 -listen» на вашем Next и повторите попытку (отправка пока отменена).",
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
        "Serving on port {port}. A Next with the .http dot command (or curl) can now drive the Next connected in '.sync5 -listen'.":
            "Běží na portu {port}. Next s dot příkazem .http (nebo curl) teď může ovládat Next připojený v „.sync5 -listen\".",
        "You have specified to start the flask integration server but port {port} is already in use, the web server has not been started.":
            "Zvolili jste spuštění integračního serveru Flask, ale port {port} je již obsazen — webový server nebyl spuštěn.",
        "Start '.sync5 -listen' on your Next to connect!":
            "Spusťte „.sync5 -listen\" na svém Nextu a připojte se!",
        "Start '.sync5 -listen' on your Next and retry again (canceling the upload / send process for now).":
            "Spusťte „.sync5 -listen\" na svém Nextu a zkuste to znovu (odesílání je zatím zrušeno).",
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
