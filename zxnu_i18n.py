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
    English literals (option values);
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
