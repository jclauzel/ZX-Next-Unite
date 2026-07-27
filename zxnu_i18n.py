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

from weakref import WeakKeyDictionary

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


def ui_tr(text, lang):
    """Translate one exact UI string into *lang* (English/unknown pass through)."""
    if not text or lang in ("", None, DEFAULT_UI_LANGUAGE):
        return text
    return CATALOGS.get(lang, {}).get(text, text)


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
    },
}
