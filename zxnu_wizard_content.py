"""zxnu_wizard_content.py — everything the onboarding Wizard says, in all
supported UI languages, plus the tab-tour script.

Pure data (no Qt): keeping the Wizard's dialogue here — outside both the
monolith and zxnu_wizard.py — makes translations easy to maintain: every
entry is a ``key -> {lang: text}`` dict covering the same six extra
languages as zxnu_i18n (es/pt/pl/ru/cs/fr; English is the source and the
fallback).  tests/test_wizard.py tripwires that every key carries every
language, so an added line can't silently ship untranslated.

The per-tab deep-dive content is deliberately NOT duplicated here: each
tour step names its GitHub wiki user-manual page (one page per tab — the
wiki is the single source of truth), which zxnu_wizard.py fetches at
runtime for the "From the manual" teaser and opens in the browser on
"Read the manual".
"""
from __future__ import annotations

# Raw-markdown fetch and human page URLs for the wiki user manual.
WIKI_RAW_BASE = "https://raw.githubusercontent.com/wiki/jclauzel/ZX-Next-Unite/{page}.md"
WIKI_PAGE_BASE = "https://github.com/jclauzel/ZX-Next-Unite/wiki/{page}"

WIZARD_LANGS = ("en", "es", "pt", "pl", "ru", "cs", "fr")

TEXTS = {
    # ── In-depth guides: offer + extra buttons ───────────────────────────
    "guide.offer": {
        "en": "You're on one of my favourite tabs — I know it inside out! "
              "Shall I run the discovery tour of all the tabs, or tell you "
              "more about THIS one?",
        "es": "¡Estás en una de mis pestañas favoritas — la conozco al "
              "dedillo! ¿Hago el recorrido de descubrimiento por todas las "
              "pestañas, o te cuento más sobre ESTA?",
        "pt": "Estás num dos meus separadores preferidos — conheço-o de "
              "trás para a frente! Faço a visita de descoberta por todos "
              "os separadores, ou conto-te mais sobre ESTE?",
        "pl": "Jesteś na jednej z moich ulubionych kart — znam ją na "
              "wylot! Mam poprowadzić wycieczkę po wszystkich kartach, czy "
              "opowiedzieć ci więcej o TEJ?",
        "ru": "Вы на одной из моих любимых вкладок — я знаю её вдоль и "
              "поперёк! Провести экскурсию по всем вкладкам или "
              "рассказать больше именно об ЭТОЙ?",
        "cs": "Jsi na jedné z mých oblíbených záložek — znám ji jako své "
              "boty! Mám spustit objevnou prohlídku všech záložek, nebo ti "
              "povědět víc o TÉHLE?",
        "fr": "Vous êtes sur l'un de mes onglets préférés — je le connais "
              "par cœur ! Je lance la visite découverte de tous les "
              "onglets, ou je vous en dis plus sur CELUI-CI ?",
    },
    "btn.indepth": {
        "en": "🔍 Tell me more",
        "es": "🔍 Cuéntame más",
        "pt": "🔍 Conta-me mais",
        "pl": "🔍 Opowiedz więcej",
        "ru": "🔍 Расскажи больше",
        "cs": "🔍 Řekni mi víc",
        "fr": "🔍 Dis-m'en plus",
    },
    "btn.yes": {
        "en": "Yes, please!",
        "es": "¡Sí, por favor!",
        "pt": "Sim, por favor!",
        "pl": "Tak, poproszę!",
        "ru": "Да, пожалуйста!",
        "cs": "Ano, prosím!",
        "fr": "Oui, volontiers !",
    },
    "btn.no": {
        "en": "Not now",
        "es": "Ahora no",
        "pt": "Agora não",
        "pl": "Nie teraz",
        "ru": "Не сейчас",
        "cs": "Teď ne",
        "fr": "Pas maintenant",
    },
    "btn.takeme": {
        "en": "🪄 Take me there",
        "es": "🪄 Llévame allí",
        "pt": "🪄 Leva-me lá",
        "pl": "🪄 Zabierz mnie tam",
        "ru": "🪄 Отведи меня туда",
        "cs": "🪄 Vezmi mě tam",
        "fr": "🪄 Emmène-moi",
    },
    "help.offer": {
        "en": "A new tab! Would you like a quick word about what it "
              "does?",
        "es": "¡Una pestaña nueva! ¿Quieres que te cuente en dos "
              "palabras qué hace?",
        "pt": "Um separador novo! Queres que te diga em duas palavras o "
              "que faz?",
        "pl": "Nowa karta! Chcesz usłyszeć w dwóch słowach, co robi?",
        "ru": "Новая вкладка! Рассказать в двух словах, что она делает?",
        "cs": "Nová záložka! Chceš v rychlosti slyšet, co dělá?",
        "fr": "Un nouvel onglet ! Voulez-vous un petit mot sur ce qu'il "
              "fait ?",
    },
    "wizard.font": {
        "en": "Are my letters comfortable to read? I can make them "
              "bigger or smaller — your choice is saved for next time.",
        "es": "¿Se leen bien mis letras? Puedo hacerlas más grandes o "
              "más pequeñas — tu elección se guarda para la próxima "
              "vez.",
        "pt": "As minhas letras leem-se bem? Posso torná-las maiores ou "
              "mais pequenas — a tua escolha fica guardada para a "
              "próxima vez.",
        "pl": "Czy moje literki dobrze się czytają? Mogę je powiększyć "
              "lub pomniejszyć — twój wybór zostanie zapamiętany.",
        "ru": "Удобно ли читать мои буквы? Могу сделать их крупнее или "
              "мельче — ваш выбор сохранится на следующий раз.",
        "cs": "Čtou se má písmenka pohodlně? Umím je zvětšit i zmenšit "
              "— tvá volba se uloží na příště.",
        "fr": "Mes lettres sont-elles agréables à lire ? Je peux les "
              "agrandir ou les rapetisser — votre choix est gardé pour "
              "la prochaine fois.",
    },
    "btn.font": {
        "en": "🔠 Font size",
        "es": "🔠 Tamaño de letra",
        "pt": "🔠 Tamanho da letra",
        "pl": "🔠 Rozmiar czcionki",
        "ru": "🔠 Размер шрифта",
        "cs": "🔠 Velikost písma",
        "fr": "🔠 Taille de police",
    },
    "btn.setup": {
        "en": "⚙ Set up NextSync",
        "es": "⚙ Configurar NextSync",
        "pt": "⚙ Configurar o NextSync",
        "pl": "⚙ Skonfiguruj NextSync",
        "ru": "⚙ Настроить NextSync",
        "cs": "⚙ Nastavit NextSync",
        "fr": "⚙ Configurer NextSync",
    },
    # Feature names stay untranslated (they match the in-app selector).
    "btn.remotexp": {
        "en": "🗂 Remote Explorer", "es": "🗂 Remote Explorer",
        "pt": "🗂 Remote Explorer", "pl": "🗂 Remote Explorer",
        "ru": "🗂 Remote Explorer", "cs": "🗂 Remote Explorer",
        "fr": "🗂 Remote Explorer",
    },
    "btn.classic": {
        "en": "🔁 Classic Sync", "es": "🔁 Classic Sync",
        "pt": "🔁 Classic Sync", "pl": "🔁 Classic Sync",
        "ru": "🔁 Classic Sync", "cs": "🔁 Classic Sync",
        "fr": "🔁 Classic Sync",
    },
    # ── SD Card in-depth guide ───────────────────────────────────────────
    "sd.images": {
        "en": "The Next boots from an SD card — and on the PC you work "
              "with an IMAGE of one: a single .img or .hdf file holding a "
              "whole NextZXOS card, byte for byte. Conjure a fresh one "
              "with 'Download NextZXOS image' below, or use a dump of "
              "your real card.",
        "es": "El Next arranca desde una tarjeta SD — y en el PC trabajas "
              "con una IMAGEN de ella: un único archivo .img o .hdf que "
              "contiene una tarjeta NextZXOS completa, byte a byte. "
              "Invoca una nueva con «Download NextZXOS image» abajo, o "
              "usa un volcado de tu tarjeta real.",
        "pt": "O Next arranca de um cartão SD — e no PC trabalhas com uma "
              "IMAGEM dele: um único ficheiro .img ou .hdf com um cartão "
              "NextZXOS completo, byte a byte. Invoca uma nova com "
              "«Download NextZXOS image» em baixo, ou usa uma cópia do "
              "teu cartão real.",
        "pl": "Next uruchamia się z karty SD — a na PC pracujesz z jej "
              "OBRAZEM: jednym plikiem .img lub .hdf zawierającym całą "
              "kartę NextZXOS, bajt po bajcie. Wyczaruj świeży przyciskiem "
              "„Download NextZXOS image” poniżej albo użyj zrzutu swojej "
              "prawdziwej karty.",
        "ru": "Next загружается с SD-карты — а на ПК вы работаете с её "
              "ОБРАЗОМ: одним файлом .img или .hdf, хранящим целую карту "
              "NextZXOS, байт в байт. Наколдуйте свежий кнопкой "
              "«Download NextZXOS image» ниже или используйте дамп вашей "
              "настоящей карты.",
        "cs": "Next bootuje z SD karty — a na PC pracuješ s jejím OBRAZEM: "
              "jediným souborem .img nebo .hdf, který nese celou kartu "
              "NextZXOS, bajt po bajtu. Vyčaruj si čerstvý tlačítkem "
              "„Download NextZXOS image“ níže, nebo použij otisk své "
              "skutečné karty.",
        "fr": "Le Next démarre depuis une carte SD — et sur PC vous "
              "travaillez avec une IMAGE de celle-ci : un seul fichier "
              ".img ou .hdf contenant une carte NextZXOS complète, octet "
              "par octet. Invoquez-en une neuve avec « Download NextZXOS "
              "image » ci-dessous, ou utilisez une copie de votre vraie "
              "carte.",
    },
    "sd.hdfmonkey": {
        "en": "hdfmonkey is the tiny command-line genie that reads and "
              "writes those images — every browse, copy and delete in the "
              "right-hand explorer is hdfmonkey working behind my wand. "
              "The app can fetch it for you — more on that in a moment.",
        "es": "hdfmonkey es el pequeño genio de línea de comandos que lee "
              "y escribe esas imágenes — cada exploración, copia y "
              "borrado en el explorador derecho es hdfmonkey trabajando "
              "tras mi varita. La aplicación puede instalarlo por ti — "
              "enseguida te cuento cómo.",
        "pt": "O hdfmonkey é o pequeno génio de linha de comandos que lê "
              "e escreve essas imagens — cada navegação, cópia e remoção "
              "no explorador da direita é o hdfmonkey a trabalhar por "
              "trás da minha varinha. A aplicação pode instalá-lo por ti "
              "— já a seguir explico como.",
        "pl": "hdfmonkey to mały dżin wiersza poleceń, który czyta i "
              "zapisuje te obrazy — każde przeglądanie, kopiowanie i "
              "usuwanie w prawym eksploratorze to hdfmonkey pracujący za "
              "moją różdżką. Aplikacja może go zainstalować za ciebie — "
              "za chwilę powiem jak.",
        "ru": "hdfmonkey — это маленький джинн командной строки, который "
              "читает и пишет эти образы: каждый просмотр, копирование и "
              "удаление в правом проводнике — это hdfmonkey, работающий "
              "за моей палочкой. Приложение может установить его за вас — "
              "об этом чуть позже.",
        "cs": "hdfmonkey je malý džin příkazové řádky, který ty obrazy "
              "čte a zapisuje — každé procházení, kopírování a mazání v "
              "pravém průzkumníku je hdfmonkey pracující za mou hůlkou. "
              "Aplikace ti ho umí stáhnout — za okamžik povím jak.",
        "fr": "hdfmonkey est le petit génie en ligne de commande qui lit "
              "et écrit ces images — chaque navigation, copie et "
              "suppression dans l'explorateur de droite, c'est hdfmonkey "
              "qui œuvre derrière ma baguette. L'application peut "
              "l'installer pour vous — j'y viens dans un instant.",
    },
    "sd.cspect": {
        "en": "CSpect is the classic Next emulator: one click and your "
              "image boots. The best route is installing it from the "
              "itch.io tab — you support Mike's work, and the download "
              "ships hdfmonkey too: two spells for the price of one. Want "
              "the step-by-step for CSpect on itch.io?",
        "es": "CSpect es el emulador clásico del Next: un clic y tu "
              "imagen arranca. La mejor vía es instalarlo desde la "
              "pestaña itch.io — apoyas el trabajo de Mike y la descarga "
              "incluye también hdfmonkey: dos hechizos al precio de uno. "
              "¿Quieres el paso a paso de CSpect en itch.io?",
        "pt": "O CSpect é o emulador clássico do Next: um clique e a tua "
              "imagem arranca. O melhor caminho é instalá-lo a partir do "
              "separador itch.io — apoias o trabalho do Mike e a "
              "descarga traz também o hdfmonkey: dois feitiços pelo preço "
              "de um. Queres o passo a passo do CSpect no itch.io?",
        "pl": "CSpect to klasyczny emulator Nexta: jedno kliknięcie i "
              "twój obraz startuje. Najlepsza droga to instalacja z karty "
              "itch.io — wspierasz pracę Mike'a, a pobranie zawiera też "
              "hdfmonkey: dwa zaklęcia w cenie jednego. Chcesz "
              "instrukcję krok po kroku dla CSpecta na itch.io?",
        "ru": "CSpect — классический эмулятор Next: один клик, и ваш "
              "образ загружается. Лучший путь — установить его со "
              "вкладки itch.io: вы поддерживаете работу Майка, а в "
              "загрузке есть и hdfmonkey — два заклинания по цене "
              "одного. Хотите пошаговую инструкцию для CSpect на "
              "itch.io?",
        "cs": "CSpect je klasický emulátor Nextu: jedno kliknutí a tvůj "
              "obraz nabootuje. Nejlepší cesta je instalace ze záložky "
              "itch.io — podpoříš Mikovu práci a stažení nese i "
              "hdfmonkey: dvě kouzla za cenu jednoho. Chceš návod krok "
              "za krokem pro CSpect na itch.io?",
        "fr": "CSpect est l'émulateur Next classique : un clic et votre "
              "image démarre. La meilleure voie est de l'installer depuis "
              "l'onglet itch.io — vous soutenez le travail de Mike, et le "
              "téléchargement embarque aussi hdfmonkey : deux sorts pour "
              "le prix d'un. Voulez-vous le pas-à-pas de CSpect sur "
              "itch.io ?",
    },
    "sd.cspect_steps": {
        "en": "Here is the full incantation: 1) Purchase CSpect on "
              "itch.io (the website) — it joins your itch.io library. "
              "2) Open the itch.io tab here and connect with your "
              "account's API key — the tab's 'Get key' button opens the "
              "exact page. 3) Your library appears: click CSpect, then "
              "Install. 4) That's it — it lands in the app's downloads "
              "folder, hdfmonkey included, and the 'Launch CSpect' "
              "button on the SD Card tab lights up automatically.",
        "es": "Aquí va el conjuro completo: 1) Compra CSpect en itch.io "
              "(la web) — se añadirá a tu biblioteca de itch.io. 2) Abre "
              "aquí la pestaña itch.io y conéctate con la clave API de tu "
              "cuenta — el botón «Get key» abre la página exacta. 3) "
              "Aparece tu biblioteca: haz clic en CSpect y luego en "
              "Install. 4) Listo — cae en la carpeta de descargas de la "
              "aplicación, con hdfmonkey incluido, y el botón «Launch "
              "CSpect» de la pestaña SD Card se enciende automáticamente.",
        "pt": "Eis o encantamento completo: 1) Compra o CSpect no "
              "itch.io (o site) — junta-se à tua biblioteca itch.io. 2) "
              "Abre aqui o separador itch.io e liga-te com a chave API da "
              "tua conta — o botão «Get key» abre a página certa. 3) A "
              "tua biblioteca aparece: clica em CSpect e depois em "
              "Install. 4) Pronto — cai na pasta de descargas da "
              "aplicação, com o hdfmonkey incluído, e o botão «Launch "
              "CSpect» do separador SD Card acende-se automaticamente.",
        "pl": "Oto pełne zaklęcie: 1) Kup CSpecta na itch.io (na "
              "stronie) — trafi do twojej biblioteki itch.io. 2) Otwórz "
              "tutaj kartę itch.io i połącz się kluczem API swojego "
              "konta — przycisk „Get key” otwiera właściwą stronę. 3) "
              "Pojawi się twoja biblioteka: kliknij CSpect, potem "
              "Install. 4) Gotowe — ląduje w folderze pobrań aplikacji "
              "razem z hdfmonkey, a przycisk „Launch CSpect” na karcie "
              "SD Card zapala się automatycznie.",
        "ru": "Вот полное заклинание: 1) Купите CSpect на itch.io (на "
              "сайте) — он появится в вашей библиотеке itch.io. 2) "
              "Откройте здесь вкладку itch.io и подключитесь API-ключом "
              "вашего аккаунта — кнопка «Get key» открывает нужную "
              "страницу. 3) Появится ваша библиотека: нажмите CSpect, "
              "затем Install. 4) Готово — он попадает в папку загрузок "
              "приложения вместе с hdfmonkey, и кнопка «Launch CSpect» "
              "на вкладке SD Card загорается автоматически.",
        "cs": "Tady je celé zaříkadlo: 1) Kup CSpect na itch.io (na "
              "webu) — přibude do tvé knihovny itch.io. 2) Otevři tady "
              "záložku itch.io a připoj se API klíčem svého účtu — "
              "tlačítko „Get key“ otevře přesně tu správnou stránku. 3) "
              "Objeví se tvá knihovna: klikni na CSpect a pak na "
              "Install. 4) Hotovo — přistane ve složce stahování "
              "aplikace i s hdfmonkey a tlačítko „Launch CSpect“ na "
              "záložce SD Card se rozsvítí samo.",
        "fr": "Voici l'incantation complète : 1) Achetez CSpect sur "
              "itch.io (le site) — il rejoint votre bibliothèque "
              "itch.io. 2) Ouvrez ici l'onglet itch.io et connectez-vous "
              "avec la clé API de votre compte — le bouton « Get key » "
              "ouvre la page exacte. 3) Votre bibliothèque apparaît : "
              "cliquez sur CSpect, puis sur Install. 4) Et voilà — il "
              "atterrit dans le dossier de téléchargements de "
              "l'application, hdfmonkey inclus, et le bouton « Launch "
              "CSpect » de l'onglet SD Card s'allume automatiquement.",
    },
    "sd.mame": {
        "en": "MAME also emulates the Next (the 'tbblue' driver). MAME "
              "does NOT bundle hdfmonkey, so take the classic standalone "
              "route: the 'Download and install HDF Monkey' button on "
              "this tab fetches hdfmonkey, and the 'Install MAME' button "
              "fetches the latest MAME build for you.",
        "es": "MAME también emula el Next (el driver «tbblue»). MAME NO "
              "incluye hdfmonkey, así que usa la vía clásica "
              "independiente: el botón «Download and install HDF Monkey» "
              "de esta pestaña instala hdfmonkey, y el botón «Install "
              "MAME» descarga por ti la última versión de MAME.",
        "pt": "O MAME também emula o Next (o driver «tbblue»). O MAME "
              "NÃO traz o hdfmonkey, por isso segue a via clássica "
              "independente: o botão «Download and install HDF Monkey» "
              "deste separador instala o hdfmonkey, e o botão «Install "
              "MAME» descarrega por ti a versão mais recente do MAME.",
        "pl": "MAME również emuluje Nexta (sterownik „tbblue”). MAME NIE "
              "zawiera hdfmonkey, więc skorzystaj z klasycznej "
              "samodzielnej ścieżki: przycisk „Download and install HDF "
              "Monkey” na tej karcie instaluje hdfmonkey, a przycisk "
              "„Install MAME” pobiera najnowszą wersję MAME.",
        "ru": "MAME тоже эмулирует Next (драйвер «tbblue»). MAME НЕ "
              "включает hdfmonkey, поэтому идите классическим отдельным "
              "путём: кнопка «Download and install HDF Monkey» на этой "
              "вкладке установит hdfmonkey, а кнопка «Install MAME» "
              "скачает для вас свежую сборку MAME.",
        "cs": "MAME také emuluje Next (ovladač „tbblue“). MAME hdfmonkey "
              "NEobsahuje, takže zvol klasickou samostatnou cestu: "
              "tlačítko „Download and install HDF Monkey“ na této "
              "záložce nainstaluje hdfmonkey a tlačítko „Install MAME“ "
              "stáhne nejnovější sestavení MAME.",
        "fr": "MAME émule aussi le Next (le pilote « tbblue »). MAME "
              "n'embarque PAS hdfmonkey : prenez la voie classique "
              "autonome — le bouton « Download and install HDF Monkey » "
              "de cet onglet installe hdfmonkey, et le bouton « Install "
              "MAME » télécharge pour vous la dernière version de MAME.",
    },
    "sd.mame.linux": {
        "en": "A note for Linux: there is no official MAME binary — if "
              "compiling from source isn't your idea of fun, enable "
              "'Launch Mame with Flatpak' in the Settings tab and the "
              "app will use the Flatpak MAME instead.",
        "es": "Nota para Linux: no existe binario oficial de MAME — si "
              "compilar desde el código fuente no es tu idea de "
              "diversión, activa «Launch Mame with Flatpak» en la "
              "pestaña Ajustes y la aplicación usará el MAME de Flatpak.",
        "pt": "Nota para Linux: não há binário oficial do MAME — se "
              "compilar a partir do código-fonte não é a tua ideia de "
              "diversão, ativa «Launch Mame with Flatpak» no separador "
              "Definições e a aplicação usará o MAME do Flatpak.",
        "pl": "Uwaga dla Linuksa: nie ma oficjalnej binarki MAME — "
              "jeśli kompilacja ze źródeł to nie twoja bajka, włącz "
              "„Launch Mame with Flatpak” w karcie Ustawienia, a "
              "aplikacja użyje MAME z Flatpaka.",
        "ru": "Замечание для Linux: официальной сборки MAME нет — если "
              "компиляция из исходников не ваше развлечение, включите "
              "«Launch Mame with Flatpak» во вкладке Настройки, и "
              "приложение будет использовать MAME из Flatpak.",
        "cs": "Poznámka pro Linux: oficiální binárka MAME neexistuje — "
              "pokud kompilace ze zdrojáků není tvá představa zábavy, "
              "zapni „Launch Mame with Flatpak“ v záložce Nastavení a "
              "aplikace použije MAME z Flatpaku.",
        "fr": "Note pour Linux : il n'existe pas de binaire MAME "
              "officiel — si compiler depuis les sources n'est pas votre "
              "idée du plaisir, activez « Launch Mame with Flatpak » "
              "dans l'onglet Réglages et l'application utilisera le MAME "
              "de Flatpak.",
    },
    "sd.manipulate_ask": {
        "en": "Shall I show you how to actually work with an image — "
              "download one, mount it and fill it with treasures?",
        "es": "¿Te enseño a trabajar de verdad con una imagen — "
              "descargarla, montarla y llenarla de tesoros?",
        "pt": "Queres que te mostre como trabalhar mesmo com uma imagem "
              "— descarregá-la, montá-la e enchê-la de tesouros?",
        "pl": "Pokazać ci, jak naprawdę pracować z obrazem — pobrać go, "
              "zamontować i wypełnić skarbami?",
        "ru": "Показать, как по-настоящему работать с образом — скачать, "
              "смонтировать и наполнить сокровищами?",
        "cs": "Mám ti ukázat, jak s obrazem doopravdy pracovat — "
              "stáhnout ho, připojit a naplnit poklady?",
        "fr": "Je vous montre comment vraiment travailler avec une image "
              "— la télécharger, la monter et la remplir de trésors ?",
    },
    "sd.nextzxos": {
        "en": "NextZXOS is the Next's operating system — the menus, "
              "BASIC and dot commands your machine boots into. 'Download "
              "NextZXOS image' fetches an official ready-to-boot image; "
              "'Select Image' then mounts it (or any image of yours) "
              "into the right-hand explorer.",
        "es": "NextZXOS es el sistema operativo del Next — los menús, el "
              "BASIC y los comandos dot con los que arranca tu máquina. "
              "«Download NextZXOS image» descarga una imagen oficial "
              "lista para arrancar; «Select Image» la monta (o cualquier "
              "imagen tuya) en el explorador derecho.",
        "pt": "O NextZXOS é o sistema operativo do Next — os menus, o "
              "BASIC e os comandos dot com que a tua máquina arranca. "
              "«Download NextZXOS image» descarrega uma imagem oficial "
              "pronta a arrancar; «Select Image» monta-a (ou qualquer "
              "imagem tua) no explorador da direita.",
        "pl": "NextZXOS to system operacyjny Nexta — menu, BASIC i "
              "polecenia dot, do których startuje twoja maszyna. "
              "„Download NextZXOS image” pobiera oficjalny obraz gotowy "
              "do startu; „Select Image” montuje go (lub dowolny twój "
              "obraz) w prawym eksploratorze.",
        "ru": "NextZXOS — операционная система Next: меню, BASIC и "
              "dot-команды, в которые загружается ваша машина. «Download "
              "NextZXOS image» скачивает официальный готовый к загрузке "
              "образ; «Select Image» монтирует его (или любой ваш образ) "
              "в правый проводник.",
        "cs": "NextZXOS je operační systém Nextu — menu, BASIC a dot "
              "příkazy, do kterých tvůj stroj bootuje. „Download "
              "NextZXOS image“ stáhne oficiální obraz připravený k "
              "bootu; „Select Image“ ho pak připojí (nebo kterýkoli tvůj "
              "obraz) do pravého průzkumníka.",
        "fr": "NextZXOS est le système d'exploitation du Next — les "
              "menus, le BASIC et les commandes dot sur lesquels votre "
              "machine démarre. « Download NextZXOS image » télécharge "
              "une image officielle prête à démarrer ; « Select Image » "
              "la monte ensuite (ou n'importe quelle image à vous) dans "
              "l'explorateur de droite.",
    },
    "sd.explorers": {
        "en": "Two panes, one bridge: the LEFT explorer is your PC, the "
              "RIGHT one looks inside the image. Drag & drop works both "
              "ways, and so does the clipboard: Ctrl+C/X/V copy, cut and "
              "paste between panes AND with your OS file explorer; Del "
              "deletes, F2 renames; the '->:' and ':<-' buttons transfer "
              "the selection.",
        "es": "Dos paneles, un puente: el explorador IZQUIERDO es tu PC, "
              "el DERECHO mira dentro de la imagen. Arrastrar y soltar "
              "funciona en ambos sentidos, y el portapapeles también: "
              "Ctrl+C/X/V copian, cortan y pegan entre paneles Y con el "
              "explorador de tu sistema; Supr borra, F2 renombra; los "
              "botones «->:» y «:<-» transfieren la selección.",
        "pt": "Dois painéis, uma ponte: o explorador ESQUERDO é o teu "
              "PC, o DIREITO olha para dentro da imagem. Arrastar e "
              "largar funciona nos dois sentidos, e a área de "
              "transferência também: Ctrl+C/X/V copiam, cortam e colam "
              "entre painéis E com o explorador do teu sistema; Del "
              "apaga, F2 renomeia; os botões «->:» e «:<-» transferem a "
              "seleção.",
        "pl": "Dwa panele, jeden most: LEWY eksplorator to twój PC, "
              "PRAWY zagląda do wnętrza obrazu. Przeciąganie działa w "
              "obie strony, schowek też: Ctrl+C/X/V kopiują, wycinają i "
              "wklejają między panelami ORAZ z eksploratorem systemu; "
              "Del usuwa, F2 zmienia nazwę; przyciski „->:” i „:<-” "
              "przenoszą zaznaczenie.",
        "ru": "Две панели, один мост: ЛЕВЫЙ проводник — ваш ПК, ПРАВЫЙ "
              "смотрит внутрь образа. Перетаскивание работает в обе "
              "стороны, буфер обмена тоже: Ctrl+C/X/V копируют, "
              "вырезают и вставляют между панелями И с проводником "
              "вашей ОС; Del удаляет, F2 переименовывает; кнопки «->:» "
              "и «:<-» переносят выделенное.",
        "cs": "Dva panely, jeden most: LEVÝ průzkumník je tvé PC, PRAVÝ "
              "nahlíží dovnitř obrazu. Drag & drop funguje oběma směry a "
              "schránka také: Ctrl+C/X/V kopírují, vyjímají a vkládají "
              "mezi panely I s průzkumníkem tvého systému; Del maže, F2 "
              "přejmenovává; tlačítka „->:“ a „:<-“ přenášejí výběr.",
        "fr": "Deux panneaux, un pont : l'explorateur de GAUCHE est "
              "votre PC, celui de DROITE regarde dans l'image. Le "
              "glisser-déposer marche dans les deux sens, le "
              "presse-papiers aussi : Ctrl+C/X/V copient, coupent et "
              "collent entre panneaux ET avec l'explorateur de votre "
              "système ; Suppr efface, F2 renomme ; les boutons « ->: » "
              "et « :<- » transfèrent la sélection.",
    },
    "sd.emulators": {
        "en": "Below the explorers wait the launch buttons: they appear "
              "as you install CSpect or MAME, and boot the LOADED image "
              "automatically — no flags to remember. And here's the real "
              "magic: later, the Unite!, GetIt, ZXDB and zxArt tabs can "
              "send content STRAIGHT into this image and start the "
              "emulator instantly.",
        "es": "Bajo los exploradores esperan los botones de arranque: "
              "aparecen a medida que instalas CSpect o MAME, y arrancan "
              "la imagen CARGADA automáticamente — sin parámetros que "
              "recordar. Y aquí está la verdadera magia: más adelante, "
              "las pestañas Unite!, GetIt, ZXDB y zxArt pueden enviar "
              "contenido DIRECTO a esta imagen y arrancar el emulador al "
              "instante.",
        "pt": "Debaixo dos exploradores esperam os botões de arranque: "
              "aparecem à medida que instalas o CSpect ou o MAME, e "
              "arrancam a imagem CARREGADA automaticamente — sem "
              "parâmetros para decorar. E eis a verdadeira magia: mais "
              "tarde, os separadores Unite!, GetIt, ZXDB e zxArt podem "
              "enviar conteúdo DIRETO para esta imagem e arrancar o "
              "emulador num instante.",
        "pl": "Pod eksploratorami czekają przyciski startu: pojawiają "
              "się w miarę instalowania CSpecta lub MAME i uruchamiają "
              "ZAŁADOWANY obraz automatycznie — bez parametrów do "
              "pamiętania. A oto prawdziwa magia: później karty Unite!, "
              "GetIt, ZXDB i zxArt mogą wysyłać treści PROSTO do tego "
              "obrazu i natychmiast uruchamiać emulator.",
        "ru": "Под проводниками ждут кнопки запуска: они появляются по "
              "мере установки CSpect или MAME и автоматически загружают "
              "СМОНТИРОВАННЫЙ образ — никаких флагов запоминать не "
              "нужно. И вот настоящая магия: позже вкладки Unite!, "
              "GetIt, ZXDB и zxArt смогут отправлять контент ПРЯМО в "
              "этот образ и мгновенно запускать эмулятор.",
        "cs": "Pod průzkumníky čekají spouštěcí tlačítka: objevují se, "
              "jak instaluješ CSpect nebo MAME, a bootují NAČTENÝ obraz "
              "automaticky — žádné parametry k pamatování. A tady je ta "
              "pravá magie: později umí záložky Unite!, GetIt, ZXDB a "
              "zxArt poslat obsah PŘÍMO do tohoto obrazu a emulátor "
              "okamžitě spustit.",
        "fr": "Sous les explorateurs attendent les boutons de "
              "lancement : ils apparaissent au fur et à mesure que vous "
              "installez CSpect ou MAME, et démarrent automatiquement "
              "l'image CHARGÉE — aucun paramètre à retenir. Et voici la "
              "vraie magie : plus tard, les onglets Unite!, GetIt, ZXDB "
              "et zxArt pourront envoyer du contenu DIRECTEMENT dans "
              "cette image et lancer l'émulateur instantanément.",
    },
    # ── NextSync in-depth guide ──────────────────────────────────────────
    "ns.what": {
        "en": "NextSync beams files from this PC to a REAL Spectrum Next "
              "over Wi-Fi — no card shuffling. The Next runs the little "
              ".sync5 dot command; this tab is the server it talks to.",
        "es": "NextSync envía archivos de este PC a un Spectrum Next "
              "REAL por Wi-Fi — sin trajín de tarjetas. El Next ejecuta "
              "el pequeño comando dot .sync5; esta pestaña es el "
              "servidor con el que habla.",
        "pt": "O NextSync envia ficheiros deste PC para um Spectrum Next "
              "A SÉRIO por Wi-Fi — sem andar a trocar cartões. O Next "
              "corre o pequeno comando dot .sync5; este separador é o "
              "servidor com quem ele fala.",
        "pl": "NextSync przesyła pliki z tego PC do PRAWDZIWEGO Spectrum "
              "Nexta przez Wi-Fi — bez żonglowania kartami. Next "
              "uruchamia małe polecenie dot .sync5; ta karta to serwer, "
              "z którym ono rozmawia.",
        "ru": "NextSync передаёт файлы с этого ПК на НАСТОЯЩИЙ Spectrum "
              "Next по Wi-Fi — без перетыкания карт. Next запускает "
              "маленькую dot-команду .sync5; эта вкладка — сервер, с "
              "которым она говорит.",
        "cs": "NextSync posílá soubory z tohoto PC do OPRAVDOVÉHO "
              "Spectra Next přes Wi-Fi — žádné přehazování karet. Next "
              "spouští malý dot příkaz .sync5; tato záložka je server, "
              "se kterým mluví.",
        "fr": "NextSync téléporte des fichiers de ce PC vers un VRAI "
              "Spectrum Next en Wi-Fi — fini le va-et-vient de cartes. "
              "Le Next exécute la petite commande dot .sync5 ; cet "
              "onglet est le serveur auquel elle parle.",
    },
    "ns.compat": {
        "en": "ZX Next Unite (and its command-line twin nextsync5.py) is "
              "fully compatible with the LEGACY .sync command — but that "
              "road is one-way only: PC to Next, no picking and "
              "choosing. That's what the 'Classic Sync' experience is "
              "for. The shiny new way is the Remote Explorer: complete "
              "file management between PC and Next. What shall we dive "
              "into?",
        "es": "ZX Next Unite (y su gemelo de línea de comandos "
              "nextsync5.py) es totalmente compatible con el comando "
              ".sync CLÁSICO — pero ese camino es de sentido único: del "
              "PC al Next, sin elegir qué. Para eso está la experiencia "
              "«Classic Sync». La vía nueva y reluciente es el Remote "
              "Explorer: gestión completa de archivos entre PC y Next. "
              "¿En qué nos sumergimos?",
        "pt": "O ZX Next Unite (e o seu gémeo de linha de comandos "
              "nextsync5.py) é totalmente compatível com o comando "
              ".sync LEGADO — mas essa estrada é de sentido único: do "
              "PC para o Next, sem escolher o quê. É para isso que "
              "existe a experiência «Classic Sync». O caminho novo e "
              "reluzente é o Remote Explorer: gestão completa de "
              "ficheiros entre PC e Next. Em que mergulhamos?",
        "pl": "ZX Next Unite (i jego bliźniak wiersza poleceń "
              "nextsync5.py) jest w pełni zgodny z DAWNYM poleceniem "
              ".sync — ale ta droga jest jednokierunkowa: z PC do "
              "Nexta, bez wybierania. Po to jest tryb „Classic Sync”. "
              "Nowa, błyszcząca droga to Remote Explorer: pełne "
              "zarządzanie plikami między PC a Nextem. W co się "
              "zagłębiamy?",
        "ru": "ZX Next Unite (и его близнец для командной строки "
              "nextsync5.py) полностью совместим со СТАРОЙ командой "
              ".sync — но эта дорога односторонняя: с ПК на Next, без "
              "выбора файлов. Для этого и есть режим «Classic Sync». "
              "Новый блестящий путь — Remote Explorer: полное "
              "управление файлами между ПК и Next. Во что нырнём?",
        "cs": "ZX Next Unite (a jeho dvojče pro příkazovou řádku "
              "nextsync5.py) je plně kompatibilní se STARÝM příkazem "
              ".sync — ale ta cesta je jednosměrná: z PC do Nextu, bez "
              "vybírání. Od toho je zážitek „Classic Sync“. Nová lesklá "
              "cesta je Remote Explorer: kompletní správa souborů mezi "
              "PC a Nextem. Do čeho se ponoříme?",
        "fr": "ZX Next Unite (et son jumeau en ligne de commande "
              "nextsync5.py) est entièrement compatible avec l'ANCIENNE "
              "commande .sync — mais cette route est à sens unique : du "
              "PC vers le Next, sans tri possible. C'est à cela que "
              "sert l'expérience « Classic Sync ». La nouvelle voie "
              "étincelante, c'est le Remote Explorer : gestion complète "
              "des fichiers entre PC et Next. Où plongeons-nous ?",
    },
    "ns.setup1": {
        "en": "Setting up: from the app's GitHub release package, drop "
              "the 'sync5' command into the DOT folder at the root of "
              "the Next's SD card — the SD Card Utility tab is perfect "
              "for that. Then, on the Next, run '.sync5' followed by "
              "the IPv4 address of the machine running ZX Next Unite "
              "(or nextsync5.py) and press Enter — the Next saves that "
              "address and knows whom to talk to from now on.",
        "es": "Configuración: del paquete de la release de GitHub, "
              "coloca el comando «sync5» en la carpeta DOT de la raíz "
              "de la tarjeta SD del Next — la pestaña SD Card Utility "
              "es perfecta para eso. Luego, en el Next, ejecuta «.sync5» "
              "seguido de la dirección IPv4 de la máquina que ejecuta "
              "ZX Next Unite (o nextsync5.py) y pulsa Enter — el Next "
              "guarda esa dirección y ya sabe con quién hablar.",
        "pt": "Configuração: do pacote da release no GitHub, coloca o "
              "comando «sync5» na pasta DOT na raiz do cartão SD do "
              "Next — o separador SD Card Utility é perfeito para isso. "
              "Depois, no Next, corre «.sync5» seguido do endereço IPv4 "
              "da máquina que executa o ZX Next Unite (ou nextsync5.py) "
              "e carrega em Enter — o Next guarda esse endereço e passa "
              "a saber com quem falar.",
        "pl": "Konfiguracja: z pakietu wydania na GitHubie umieść "
              "polecenie „sync5” w folderze DOT w katalogu głównym "
              "karty SD Nexta — karta SD Card Utility nadaje się do "
              "tego idealnie. Potem na Nexcie uruchom „.sync5” z "
              "adresem IPv4 maszyny, na której działa ZX Next Unite "
              "(lub nextsync5.py) i wciśnij Enter — Next zapisze ten "
              "adres i będzie wiedział, z kim rozmawiać.",
        "ru": "Настройка: из релизного пакета на GitHub положите "
              "команду «sync5» в папку DOT в корне SD-карты Next — "
              "вкладка SD Card Utility отлично для этого подходит. "
              "Затем на Next выполните «.sync5», затем IPv4-адрес "
              "машины, где работает ZX Next Unite (или nextsync5.py), и "
              "нажмите Enter — Next сохранит адрес и будет знать, с "
              "кем говорить.",
        "cs": "Nastavení: z release balíčku na GitHubu vlož příkaz "
              "„sync5“ do složky DOT v kořeni SD karty Nextu — záložka "
              "SD Card Utility se na to hodí dokonale. Pak na Nextu "
              "spusť „.sync5“ následované IPv4 adresou stroje, na němž "
              "běží ZX Next Unite (nebo nextsync5.py), a stiskni Enter "
              "— Next si adresu uloží a ví, s kým mluvit.",
        "fr": "Installation : depuis le paquet de release GitHub, "
              "déposez la commande « sync5 » dans le dossier DOT à la "
              "racine de la carte SD du Next — l'onglet SD Card Utility "
              "est parfait pour ça. Puis, sur le Next, lancez « .sync5 » "
              "suivi de l'adresse IPv4 de la machine qui exécute ZX "
              "Next Unite (ou nextsync5.py) et appuyez sur Entrée — le "
              "Next enregistre cette adresse et sait désormais à qui "
              "parler.",
    },
    "ns.setup2": {
        "en": "Next, in ZX Next Unite pick your root synchronization "
              "folder — the base for every sync and file transfer. Then "
              "start the server (here, or in nextsync5.py): it now "
              "waits and LISTENS for the Next to connect. Finally, on "
              "the Next, launch the mode of your choice: '.sync5 -l' "
              "(-listen) for the Remote Explorer, or plain '.sync5' for "
              "a Classic sync.",
        "es": "Después, en ZX Next Unite elige tu carpeta raíz de "
              "sincronización — la base de cada sincronización y "
              "transferencia. Luego arranca el servidor (aquí, o en "
              "nextsync5.py): ahora espera y ESCUCHA a que el Next se "
              "conecte. Por último, en el Next, lanza el modo que "
              "quieras: «.sync5 -l» (-listen) para el Remote Explorer, "
              "o «.sync5» a secas para una sincronización clásica.",
        "pt": "A seguir, no ZX Next Unite escolhe a tua pasta raiz de "
              "sincronização — a base de cada sincronização e "
              "transferência. Depois arranca o servidor (aqui, ou no "
              "nextsync5.py): ele fica à espera, à ESCUTA de que o Next "
              "se ligue. Por fim, no Next, lança o modo que quiseres: "
              "«.sync5 -l» (-listen) para o Remote Explorer, ou "
              "«.sync5» simples para uma sincronização clássica.",
        "pl": "Następnie w ZX Next Unite wybierz główny folder "
              "synchronizacji — bazę każdej synchronizacji i przesyłki. "
              "Potem uruchom serwer (tutaj albo w nextsync5.py): teraz "
              "czeka i NASŁUCHUJE połączenia Nexta. Na końcu na Nexcie "
              "odpal wybrany tryb: „.sync5 -l” (-listen) dla Remote "
              "Explorera albo samo „.sync5” dla klasycznej "
              "synchronizacji.",
        "ru": "Далее в ZX Next Unite выберите корневую папку "
              "синхронизации — основу каждой синхронизации и передачи. "
              "Затем запустите сервер (здесь или в nextsync5.py): "
              "теперь он ждёт и СЛУШАЕТ подключение Next. Наконец, на "
              "Next запустите нужный режим: «.sync5 -l» (-listen) для "
              "Remote Explorer или просто «.sync5» для классической "
              "синхронизации.",
        "cs": "Dál si v ZX Next Unite vyber kořenovou složku "
              "synchronizace — základ každé synchronizace a přenosu. "
              "Pak spusť server (tady, nebo v nextsync5.py): teď čeká a "
              "NASLOUCHÁ, až se Next připojí. Nakonec na Nextu spusť "
              "zvolený režim: „.sync5 -l“ (-listen) pro Remote "
              "Explorer, nebo prosté „.sync5“ pro klasickou "
              "synchronizaci.",
        "fr": "Ensuite, dans ZX Next Unite, choisissez votre dossier "
              "racine de synchronisation — la base de chaque synchro et "
              "transfert. Puis démarrez le serveur (ici, ou dans "
              "nextsync5.py) : il attend désormais et ÉCOUTE la "
              "connexion du Next. Enfin, sur le Next, lancez le mode de "
              "votre choix : « .sync5 -l » (-listen) pour le Remote "
              "Explorer, ou « .sync5 » tout court pour une synchro "
              "classique.",
    },
    "ns.options": {
        "en": "Here is the .sync5 spellbook:\n"
              ".sync5 <ip> — save the server address\n"
              ".sync5 — classic sync from the PC\n"
              ".sync5 -send <file|dir> — send from the Next to the PC\n"
              ".sync5 -listen (-l) — the Remote Explorer file server "
              "(the BREAK key stops it safely)\n"
              ".sync5 -slow | -default | -fast — transfer speed\n"
              ".sync5 -na | -nv | -nr — disable the animation, the "
              "verbose trace or the retro look\n"
              ".sync5 -help (-h) — the built-in help",
        "es": "Aquí está el grimorio de .sync5:\n"
              ".sync5 <ip> — guardar la dirección del servidor\n"
              ".sync5 — sincronización clásica desde el PC\n"
              ".sync5 -send <archivo|carpeta> — enviar del Next al PC\n"
              ".sync5 -listen (-l) — el servidor de archivos del Remote "
              "Explorer (la tecla BREAK lo detiene con seguridad)\n"
              ".sync5 -slow | -default | -fast — velocidad de "
              "transferencia\n"
              ".sync5 -na | -nv | -nr — desactivar la animación, la "
              "traza detallada o el aspecto retro\n"
              ".sync5 -help (-h) — la ayuda integrada",
        "pt": "Eis o grimório do .sync5:\n"
              ".sync5 <ip> — guardar o endereço do servidor\n"
              ".sync5 — sincronização clássica a partir do PC\n"
              ".sync5 -send <ficheiro|pasta> — enviar do Next para o PC\n"
              ".sync5 -listen (-l) — o servidor de ficheiros do Remote "
              "Explorer (a tecla BREAK pára-o em segurança)\n"
              ".sync5 -slow | -default | -fast — velocidade de "
              "transferência\n"
              ".sync5 -na | -nv | -nr — desligar a animação, o registo "
              "detalhado ou o visual retro\n"
              ".sync5 -help (-h) — a ajuda integrada",
        "pl": "Oto księga zaklęć .sync5:\n"
              ".sync5 <ip> — zapisz adres serwera\n"
              ".sync5 — klasyczna synchronizacja z PC\n"
              ".sync5 -send <plik|katalog> — wyślij z Nexta na PC\n"
              ".sync5 -listen (-l) — serwer plików Remote Explorera "
              "(klawisz BREAK bezpiecznie go zatrzymuje)\n"
              ".sync5 -slow | -default | -fast — prędkość przesyłu\n"
              ".sync5 -na | -nv | -nr — wyłącz animację, szczegółowy "
              "ślad lub wygląd retro\n"
              ".sync5 -help (-h) — wbudowana pomoc",
        "ru": "Вот книга заклинаний .sync5:\n"
              ".sync5 <ip> — сохранить адрес сервера\n"
              ".sync5 — классическая синхронизация с ПК\n"
              ".sync5 -send <файл|папка> — отправить с Next на ПК\n"
              ".sync5 -listen (-l) — файловый сервер Remote Explorer "
              "(клавиша BREAK безопасно останавливает его)\n"
              ".sync5 -slow | -default | -fast — скорость передачи\n"
              ".sync5 -na | -nv | -nr — отключить анимацию, подробную "
              "трассировку или ретро-вид\n"
              ".sync5 -help (-h) — встроенная справка",
        "cs": "Tady je kniha kouzel .sync5:\n"
              ".sync5 <ip> — uložit adresu serveru\n"
              ".sync5 — klasická synchronizace z PC\n"
              ".sync5 -send <soubor|složka> — poslat z Nextu do PC\n"
              ".sync5 -listen (-l) — souborový server Remote Exploreru "
              "(klávesa BREAK ho bezpečně zastaví)\n"
              ".sync5 -slow | -default | -fast — rychlost přenosu\n"
              ".sync5 -na | -nv | -nr — vypnout animaci, podrobný "
              "výpis nebo retro vzhled\n"
              ".sync5 -help (-h) — vestavěná nápověda",
        "fr": "Voici le grimoire de .sync5 :\n"
              ".sync5 <ip> — enregistrer l'adresse du serveur\n"
              ".sync5 — synchro classique depuis le PC\n"
              ".sync5 -send <fichier|dossier> — envoyer du Next vers le "
              "PC\n"
              ".sync5 -listen (-l) — le serveur de fichiers du Remote "
              "Explorer (la touche BREAK l'arrête en sécurité)\n"
              ".sync5 -slow | -default | -fast — vitesse de transfert\n"
              ".sync5 -na | -nv | -nr — désactiver l'animation, la "
              "trace détaillée ou le look rétro\n"
              ".sync5 -help (-h) — l'aide intégrée",
    },
    "ns.classic": {
        "en": "The new sync5 command still speaks the legacy Sync3 "
              "protocol: in 'Classic NextSync' mode, a plain '.sync5' "
              "on the Next triggers a traditional sync — the server "
              "pushes everything new under the sync root, PC to Next. "
              "Let me show you the knobs that tame it.",
        "es": "El nuevo comando sync5 sigue hablando el protocolo "
              "clásico Sync3: en modo «Classic NextSync», un «.sync5» a "
              "secas en el Next lanza una sincronización tradicional — "
              "el servidor envía todo lo nuevo bajo la raíz de "
              "sincronización, del PC al Next. Deja que te enseñe los "
              "mandos que la doman.",
        "pt": "O novo comando sync5 continua a falar o protocolo "
              "clássico Sync3: em modo «Classic NextSync», um «.sync5» "
              "simples no Next dispara uma sincronização tradicional — "
              "o servidor envia tudo o que é novo sob a raiz de "
              "sincronização, do PC para o Next. Deixa-me mostrar-te os "
              "botões que a domam.",
        "pl": "Nowe polecenie sync5 wciąż mówi starym protokołem "
              "Sync3: w trybie „Classic NextSync” samo „.sync5” na "
              "Nexcie wyzwala tradycyjną synchronizację — serwer "
              "wypycha wszystko, co nowe w katalogu głównym, z PC do "
              "Nexta. Pokażę ci pokrętła, które ją oswajają.",
        "ru": "Новая команда sync5 по-прежнему говорит на старом "
              "протоколе Sync3: в режиме «Classic NextSync» простое "
              "«.sync5» на Next запускает традиционную синхронизацию — "
              "сервер отправляет всё новое из корня синхронизации, с "
              "ПК на Next. Покажу ручки, которые её укрощают.",
        "cs": "Nový příkaz sync5 stále mluví starým protokolem Sync3: "
              "v režimu „Classic NextSync“ prosté „.sync5“ na Nextu "
              "spustí tradiční synchronizaci — server pošle vše nové "
              "pod kořenem synchronizace, z PC do Nextu. Ukážu ti "
              "páčky, které ji krotí.",
        "fr": "La nouvelle commande sync5 parle toujours l'ancien "
              "protocole Sync3 : en mode « Classic NextSync », un "
              "« .sync5 » tout court sur le Next déclenche une synchro "
              "traditionnelle — le serveur pousse tout ce qui est "
              "nouveau sous la racine de synchronisation, du PC vers le "
              "Next. Laissez-moi vous montrer les réglages qui "
              "l'apprivoisent.",
    },
    "ns.root": {
        "en": "Choose your sync root — the folder whose contents get "
              "pushed to the Next. A .syncignore file can exclude "
              "patterns, and the syncpoint remembers what was already "
              "sent, so only NEW files fly on the next sync.",
        "es": "Elige tu raíz de sincronización — la carpeta cuyo "
              "contenido se envía al Next. Un archivo .syncignore puede "
              "excluir patrones, y el syncpoint recuerda lo ya enviado, "
              "así que solo los archivos NUEVOS vuelan en la próxima "
              "sincronización.",
        "pt": "Escolhe a tua raiz de sincronização — a pasta cujo "
              "conteúdo é enviado para o Next. Um ficheiro .syncignore "
              "pode excluir padrões, e o syncpoint lembra-se do que já "
              "foi enviado, por isso só os ficheiros NOVOS voam na "
              "próxima sincronização.",
        "pl": "Wybierz katalog główny synchronizacji — folder, którego "
              "zawartość trafia do Nexta. Plik .syncignore może "
              "wykluczać wzorce, a syncpoint pamięta, co już wysłano — "
              "przy następnej synchronizacji lecą tylko NOWE pliki.",
        "ru": "Выберите корень синхронизации — папку, содержимое "
              "которой отправляется на Next. Файл .syncignore может "
              "исключать шаблоны, а syncpoint помнит уже отправленное — "
              "при следующей синхронизации летят только НОВЫЕ файлы.",
        "cs": "Vyber si kořen synchronizace — složku, jejíž obsah se "
              "posílá do Nextu. Soubor .syncignore umí vyloučit vzory a "
              "syncpoint si pamatuje, co už bylo odesláno — příště letí "
              "jen NOVÉ soubory.",
        "fr": "Choisissez votre racine de synchronisation — le dossier "
              "dont le contenu est poussé vers le Next. Un fichier "
              ".syncignore peut exclure des motifs, et le syncpoint se "
              "souvient de ce qui a déjà été envoyé : seuls les NOUVEAUX "
              "fichiers volent à la synchro suivante.",
    },
    "ns.server": {
        "en": "Press 'Start server', then run .sync5 on the Next — files "
              "flow immediately. 'Sync once' stops after one session; "
              "'Always sync' keeps serving. And when the Next SENDS you "
              "files (.sync5 -send), the conflict policy in Settings "
              "decides what happens if a file already exists here.",
        "es": "Pulsa «Start server» y ejecuta .sync5 en el Next — los "
              "archivos fluyen al momento. «Sync once» se detiene tras "
              "una sesión; «Always sync» sigue sirviendo. Y cuando el "
              "Next te ENVÍA archivos (.sync5 -send), la política de "
              "conflictos de Ajustes decide qué pasa si un archivo ya "
              "existe aquí.",
        "pt": "Carrega em «Start server» e corre .sync5 no Next — os "
              "ficheiros fluem de imediato. «Sync once» pára após uma "
              "sessão; «Always sync» continua a servir. E quando o Next "
              "te ENVIA ficheiros (.sync5 -send), a política de "
              "conflitos nas Definições decide o que acontece se um "
              "ficheiro já existir aqui.",
        "pl": "Wciśnij „Start server”, potem uruchom .sync5 na Nexcie — "
              "pliki płyną od razu. „Sync once” kończy po jednej sesji; "
              "„Always sync” serwuje dalej. A gdy Next WYSYŁA ci pliki "
              "(.sync5 -send), polityka konfliktów w Ustawieniach "
              "decyduje, co się dzieje, gdy plik już tu istnieje.",
        "ru": "Нажмите «Start server», затем запустите .sync5 на Next — "
              "файлы польются сразу. «Sync once» останавливается после "
              "одной сессии; «Always sync» продолжает работать. А когда "
              "Next ПРИСЫЛАЕТ вам файлы (.sync5 -send), политика "
              "конфликтов в Настройках решает, что делать, если файл "
              "здесь уже есть.",
        "cs": "Stiskni „Start server“ a na Nextu spusť .sync5 — soubory "
              "tečou okamžitě. „Sync once“ skončí po jedné relaci; "
              "„Always sync“ servíruje dál. A když ti Next soubory "
              "POSÍLÁ (.sync5 -send), politika konfliktů v Nastavení "
              "rozhodne, co se stane, když soubor už tady existuje.",
        "fr": "Appuyez sur « Start server », puis lancez .sync5 sur le "
              "Next — les fichiers coulent aussitôt. « Sync once » "
              "s'arrête après une session ; « Always sync » continue de "
              "servir. Et quand le Next vous ENVOIE des fichiers "
              "(.sync5 -send), la politique de conflit des Réglages "
              "décide du sort d'un fichier déjà présent ici.",
    },
    "ns.remote": {
        "en": "The Remote Explorer is the full file-management mode: "
              "run '.sync5 -l' (-listen) on the Next, and this tab "
              "becomes a two-pane manager for the Next itself — "
              "transfer files and whole directories both ways, rename, "
              "and even zip and unzip REMOTELY on the Next. And "
              "straight from the Next, '.sync5 -send <file|dir>' "
              "pushes a file or directory back to the PC.",
        "es": "El Remote Explorer es el modo de gestión completa de "
              "archivos: ejecuta «.sync5 -l» (-listen) en el Next y "
              "esta pestaña se convierte en un gestor de dos paneles "
              "para el propio Next — transfiere archivos y carpetas "
              "enteras en ambos sentidos, renombra, e incluso comprime "
              "y descomprime zip EN REMOTO en el Next. Y directamente "
              "desde el Next, «.sync5 -send <archivo|carpeta>» envía "
              "un archivo o carpeta de vuelta al PC.",
        "pt": "O Remote Explorer é o modo de gestão completa de "
              "ficheiros: corre «.sync5 -l» (-listen) no Next e este "
              "separador torna-se um gestor de dois painéis para o "
              "próprio Next — transfere ficheiros e pastas inteiras "
              "nos dois sentidos, renomeia, e até faz zip e unzip "
              "REMOTAMENTE no Next. E diretamente do Next, «.sync5 "
              "-send <ficheiro|pasta>» envia um ficheiro ou pasta de "
              "volta para o PC.",
        "pl": "Remote Explorer to tryb pełnego zarządzania plikami: "
              "uruchom „.sync5 -l” (-listen) na Nexcie, a ta karta "
              "stanie się dwupanelowym menedżerem samego Nexta — "
              "przesyłaj pliki i całe katalogi w obie strony, zmieniaj "
              "nazwy, a nawet pakuj i rozpakowuj zip ZDALNIE na "
              "Nexcie. A prosto z Nexta „.sync5 -send <plik|katalog>” "
              "odsyła plik lub katalog na PC.",
        "ru": "Remote Explorer — режим полного управления файлами: "
              "запустите «.sync5 -l» (-listen) на Next, и эта вкладка "
              "станет двухпанельным менеджером самого Next — "
              "передавайте файлы и целые каталоги в обе стороны, "
              "переименовывайте и даже упаковывайте и распаковывайте "
              "zip УДАЛЁННО на Next. А прямо с Next «.sync5 -send "
              "<файл|папка>» отправляет файл или каталог обратно на "
              "ПК.",
        "cs": "Remote Explorer je režim plné správy souborů: spusť na "
              "Nextu „.sync5 -l“ (-listen) a tahle záložka se stane "
              "dvoupanelovým správcem samotného Nextu — přenášej "
              "soubory i celé složky oběma směry, přejmenovávej a "
              "dokonce zipuj a rozbaluj VZDÁLENĚ na Nextu. A přímo z "
              "Nextu „.sync5 -send <soubor|složka>“ pošle soubor nebo "
              "složku zpátky do PC.",
        "fr": "Le Remote Explorer est le mode de gestion complète des "
              "fichiers : lancez « .sync5 -l » (-listen) sur le Next et "
              "cet onglet devient un gestionnaire à deux panneaux pour "
              "le Next lui-même — transférez fichiers et dossiers "
              "entiers dans les deux sens, renommez, et compressez ou "
              "décompressez même des zip À DISTANCE sur le Next. Et "
              "directement depuis le Next, « .sync5 -send "
              "<fichier|dossier> » renvoie un fichier ou un dossier "
              "vers le PC.",
    },
    # ── Buttons / menu ────────────────────────────────────────────────────
    "btn.tour": {
        "en": "🧙 Take the tour",
        "es": "🧙 Hacer el recorrido",
        "pt": "🧙 Fazer a visita",
        "pl": "🧙 Rozpocznij wycieczkę",
        "ru": "🧙 Начать экскурсию",
        "cs": "🧙 Vydat se na prohlídku",
        "fr": "🧙 Faire la visite",
    },
    "btn.later": {
        "en": "Maybe later",
        "es": "Quizás luego",
        "pt": "Talvez depois",
        "pl": "Może później",
        "ru": "Может позже",
        "cs": "Možná později",
        "fr": "Plus tard",
    },
    "btn.off": {
        "en": "Turn me off",
        "es": "Apágame",
        "pt": "Desliga-me",
        "pl": "Wyłącz mnie",
        "ru": "Выключи меня",
        "cs": "Vypni mě",
        "fr": "Éteins-moi",
    },
    "btn.next": {
        "en": "Next ▶",
        "es": "Siguiente ▶",
        "pt": "Seguinte ▶",
        "pl": "Dalej ▶",
        "ru": "Дальше ▶",
        "cs": "Další ▶",
        "fr": "Suivant ▶",
    },
    "btn.stop": {
        "en": "Stop the tour",
        "es": "Detener el recorrido",
        "pt": "Parar a visita",
        "pl": "Zakończ wycieczkę",
        "ru": "Закончить экскурсию",
        "cs": "Ukončit prohlídku",
        "fr": "Arrêter la visite",
    },
    "btn.more": {
        "en": "📖 Read the manual",
        "es": "📖 Leer el manual",
        "pt": "📖 Ler o manual",
        "pl": "📖 Otwórz podręcznik",
        "ru": "📖 Открыть руководство",
        "cs": "📖 Otevřít příručku",
        "fr": "📖 Lire le manuel",
    },
    "btn.joke": {
        "en": "😄 Tell me a joke",
        "es": "😄 Cuéntame un chiste",
        "pt": "😄 Conta uma piada",
        "pl": "😄 Opowiedz dowcip",
        "ru": "😄 Расскажи шутку",
        "cs": "😄 Řekni vtip",
        "fr": "😄 Raconte une blague",
    },
    "btn.story": {
        "en": "📜 Tell me a story",
        "es": "📜 Cuéntame una historia",
        "pt": "📜 Conta uma história",
        "pl": "📜 Opowiedz historię",
        "ru": "📜 Расскажи историю",
        "cs": "📜 Vyprávěj příběh",
        "fr": "📜 Raconte une histoire",
    },
    "btn.another": {
        "en": "Another one!",
        "es": "¡Otro más!",
        "pt": "Mais um!",
        "pl": "Jeszcze raz!",
        "ru": "Ещё!",
        "cs": "Ještě jeden!",
        "fr": "Encore un !",
    },
    "btn.close": {
        "en": "Close",
        "es": "Cerrar",
        "pt": "Fechar",
        "pl": "Zamknij",
        "ru": "Закрыть",
        "cs": "Zavřít",
        "fr": "Fermer",
    },
    "menu.title": {
        "en": "Need a hand? Pick a spell:",
        "es": "¿Necesitas ayuda? Elige un hechizo:",
        "pt": "Precisas de ajuda? Escolhe um feitiço:",
        "pl": "Potrzebujesz pomocy? Wybierz zaklęcie:",
        "ru": "Нужна помощь? Выбери заклинание:",
        "cs": "Potřebuješ pomoc? Vyber si kouzlo:",
        "fr": "Besoin d'aide ? Choisis un sortilège :",
    },
    # ── First-run introduction ────────────────────────────────────────────
    "intro.hello": {
        "en": "Greetings! I'm Wizzy, the ZX-Next-Unite wizard — at your "
              "service! I live down here in the corner and I know every "
              "tab of this app by heart.",
        "es": "¡Saludos! Soy Wizzy, el mago de ZX-Next-Unite, ¡a tu "
              "servicio! Vivo aquí abajo en la esquina y me sé de memoria "
              "todas las pestañas de esta aplicación.",
        "pt": "Saudações! Sou o Wizzy, o feiticeiro do ZX-Next-Unite — ao "
              "teu serviço! Moro aqui em baixo no canto e conheço de cor "
              "todos os separadores desta aplicação.",
        "pl": "Witaj! Jestem Wizzy, czarodziej ZX-Next-Unite — do usług! "
              "Mieszkam tu w rogu i znam każdą kartę tej aplikacji na "
              "pamięć.",
        "ru": "Приветствую! Я Виззи, волшебник ZX-Next-Unite — к вашим "
              "услугам! Я живу здесь в уголке и знаю каждую вкладку этого "
              "приложения наизусть.",
        "cs": "Zdravím! Jsem Wizzy, kouzelník ZX-Next-Unite — k tvým "
              "službám! Bydlím tady dole v rohu a znám každou záložku "
              "této aplikace nazpaměť.",
        "fr": "Salutations ! Je suis Wizzy, le magicien de ZX-Next-Unite — "
              "à votre service ! J'habite ici dans le coin et je connais "
              "chaque onglet de cette application par cœur.",
    },
    "intro.offer": {
        "en": "Shall I take you on a little tour of the tabs? It only "
              "takes a minute — SD cards, Wi-Fi syncing, whole galleries "
              "of Spectrum software... Or turn me off and I'll vanish "
              "in a puff of smoke (you can summon me back in Settings).",
        "es": "¿Te llevo a un pequeño recorrido por las pestañas? Solo "
              "toma un minuto: tarjetas SD, sincronización Wi-Fi, "
              "galerías enteras de software de Spectrum... O apágame y "
              "desapareceré en una nube de humo (puedes invocarme de "
              "nuevo en Ajustes).",
        "pt": "Queres que te leve numa pequena visita pelos separadores? "
              "Demora só um minuto: cartões SD, sincronização por Wi-Fi, "
              "galerias inteiras de software do Spectrum... Ou desliga-me "
              "e desapareço numa nuvem de fumo (podes invocar-me de novo "
              "nas Definições).",
        "pl": "Zabrać cię na małą wycieczkę po kartach? To tylko minuta: "
              "karty SD, synchronizacja Wi-Fi, całe galerie programów na "
              "Spectrum... Albo wyłącz mnie, a zniknę w kłębie dymu "
              "(przywołasz mnie z powrotem w Ustawieniach).",
        "ru": "Провести вам небольшую экскурсию по вкладкам? Это займёт "
              "всего минуту: SD-карты, синхронизация по Wi-Fi, целые "
              "галереи софта для Spectrum... Или выключите меня — и я "
              "исчезну в клубе дыма (вернуть меня можно в Настройках).",
        "cs": "Mám tě vzít na malou prohlídku záložek? Zabere to jen "
              "minutu: SD karty, synchronizace přes Wi-Fi, celé galerie "
              "spectristického softwaru... Nebo mě vypni a zmizím v "
              "obláčku dýmu (přivolat mě můžeš zpět v Nastavení).",
        "fr": "Je vous emmène faire un petit tour des onglets ? Une "
              "minute suffit : cartes SD, synchronisation Wi-Fi, des "
              "galeries entières de logiciels Spectrum... Ou éteignez-moi "
              "et je disparaîtrai dans un nuage de fumée (vous pourrez me "
              "rappeler dans les Réglages).",
    },
    "wizard.off": {
        "en": "As you wish! *poof* — you can summon me back any time from "
              "the Settings tab. Farewell, adventurer!",
        "es": "¡Como desees! *puf* — puedes invocarme de nuevo cuando "
              "quieras desde la pestaña Ajustes. ¡Adiós, aventurero!",
        "pt": "Como queiras! *puf* — podes invocar-me de novo quando "
              "quiseres no separador Definições. Adeus, aventureiro!",
        "pl": "Jak sobie życzysz! *puf* — przywołasz mnie z powrotem w "
              "każdej chwili w karcie Ustawienia. Żegnaj, poszukiwaczu "
              "przygód!",
        "ru": "Как пожелаете! *пуф* — вернуть меня можно в любой момент "
              "во вкладке Настройки. Прощайте, искатель приключений!",
        "cs": "Jak si přeješ! *puf* — přivolat mě můžeš kdykoli zpět v "
              "záložce Nastavení. Sbohem, dobrodruhu!",
        "fr": "Comme vous voudrez ! *pouf* — vous pourrez me rappeler à "
              "tout moment depuis l'onglet Réglages. Adieu, aventurier !",
    },
    # Tour finale: KUDOS! The {names} placeholder is filled from
    # KUDOS_NAMES below (proper names — never translated), so adding a
    # name is a one-line edit that reaches every language.
    "tour.kudos": {
        "en": "One last magic word before you go: KUDOS to {names} — and "
              "the entire community — for their support and incredible "
              "work to make the Next an awesome platform!",
        "es": "Una última palabra mágica antes de irte: ¡KUDOS a {names} "
              "— y a toda la comunidad — por su apoyo y su increíble "
              "trabajo para hacer del Next una plataforma increíble!",
        "pt": "Uma última palavra mágica antes de ires: KUDOS a {names} — "
              "e a toda a comunidade — pelo apoio e pelo trabalho "
              "incrível que fazem do Next uma plataforma fantástica!",
        "pl": "Ostatnie magiczne słowo na drogę: KUDOS dla {names} — i "
              "całej społeczności — za wsparcie i niesamowitą pracę, "
              "dzięki której Next jest tak wspaniałą platformą!",
        "ru": "Последнее волшебное слово на прощание: KUDOS {names} — и "
              "всему сообществу — за поддержку и невероятную работу, "
              "благодаря которой Next стал такой замечательной "
              "платформой!",
        "cs": "Ještě jedno kouzelné slovo na cestu: KUDOS pro {names} — a "
              "celou komunitu — za podporu a neuvěřitelnou práci, díky "
              "níž je Next tak skvělá platforma!",
        "fr": "Un dernier mot magique avant de partir : KUDOS à {names} — "
              "et à toute la communauté — pour leur soutien et leur "
              "travail incroyable qui font du Next une plateforme "
              "formidable !",
    },
    "tour.done": {
        "en": "And that's the tour! Click me any time for a joke, a story "
              "or another walk-around. Happy Speccy-ing!",
        "es": "¡Y ese es el recorrido! Haz clic en mí cuando quieras para "
              "un chiste, una historia u otra vuelta. ¡Feliz Speccy!",
        "pt": "E é esta a visita! Clica em mim quando quiseres para uma "
              "piada, uma história ou outra volta. Bom Speccy!",
        "pl": "I to cała wycieczka! Kliknij mnie kiedy chcesz — dowcip, "
              "historia albo kolejna runda. Miłego Speccy!",
        "ru": "Вот и вся экскурсия! Нажимайте на меня в любое время — "
              "шутка, история или ещё один обход. Счастливого Speccy!",
        "cs": "A to je celá prohlídka! Klikni na mě kdykoli — vtip, "
              "příběh nebo další kolečko. Šťastné Speccy!",
        "fr": "Et voilà la visite ! Cliquez sur moi quand vous voulez — "
              "une blague, une histoire ou un autre tour. Bon Speccy !",
    },
    # Gentle rights reminder appended to the online-catalogue tour steps
    # (GetIt / ZXDB / zxArt / Unite!) — see DISCLAIMER_STEPS below.
    "tour.disclaimer": {
        "en": "A friendly reminder: this application distributes no "
              "software or ROMs itself — it only presents content from "
              "third-party services, and it is up to you to check that "
              "you have the right to download what you grab.",
        "es": "Un recordatorio amistoso: esta aplicación no distribuye "
              "software ni ROMs por sí misma — solo presenta contenido de "
              "servicios de terceros, y te corresponde a ti comprobar que "
              "tienes derecho a descargar lo que te lleves.",
        "pt": "Um lembrete amigável: esta aplicação não distribui "
              "software nem ROMs — apenas apresenta conteúdo de serviços "
              "de terceiros, e cabe-te a ti verificar se tens o direito "
              "de descarregar o que levares.",
        "pl": "Przyjazne przypomnienie: ta aplikacja sama nie "
              "rozpowszechnia żadnego oprogramowania ani ROM-ów — jedynie "
              "prezentuje treści z serwisów zewnętrznych, a sprawdzenie, "
              "czy masz prawo je pobrać, należy do ciebie.",
        "ru": "Дружеское напоминание: это приложение само не "
              "распространяет программы или ROM-файлы — оно лишь "
              "показывает контент сторонних сервисов, и проверять, есть "
              "ли у вас право его скачивать, должны вы сами.",
        "cs": "Přátelské připomenutí: tato aplikace sama žádný software "
              "ani ROMy nešíří — pouze zobrazuje obsah služeb třetích "
              "stran a je na tobě ověřit, že máš právo si stáhnout, co "
              "si bereš.",
        "fr": "Petit rappel amical : cette application ne distribue "
              "elle-même aucun logiciel ni ROM — elle ne fait que "
              "présenter du contenu de services tiers, et il vous "
              "appartient de vérifier que vous avez le droit de "
              "télécharger ce que vous prenez.",
    },
    "manual.teaser": {
        "en": "From the manual:",
        "es": "Del manual:",
        "pt": "Do manual:",
        "pl": "Z podręcznika:",
        "ru": "Из руководства:",
        "cs": "Z příručky:",
        "fr": "Extrait du manuel :",
    },
    # ── The tab tour ──────────────────────────────────────────────────────
    # Step 0 — the tour opens on the Settings tab so the user can pick
    # their language before anything else (the wizard re-speaks instantly).
    "tour.language": {
        "en": "First things first: I speak seven languages! Right here in "
              "Settings, find 'Application language:' and pick yours — I "
              "will switch the very moment you do. All set? Then let's "
              "go exploring!",
        "es": "Lo primero es lo primero: ¡hablo siete idiomas! Aquí mismo "
              "en Ajustes, busca «Application language:» y elige el tuyo "
              "— cambiaré en el mismo instante. ¿Todo listo? ¡Pues vamos "
              "a explorar!",
        "pt": "Primeiro o mais importante: falo sete línguas! Aqui mesmo "
              "nas Definições, procura «Application language:» e escolhe "
              "a tua — mudo no preciso instante. Tudo pronto? Então vamos "
              "explorar!",
        "pl": "Najpierw najważniejsze: mówię w siedmiu językach! Tutaj, w "
              "Ustawieniach, znajdź „Application language:” i wybierz "
              "swój — przełączę się w tej samej chwili. Gotowe? To "
              "ruszamy na zwiedzanie!",
        "ru": "Первым делом: я говорю на семи языках! Прямо здесь, в "
              "Настройках, найдите «Application language:» и выберите "
              "свой — я переключусь в то же мгновение. Готовы? Тогда "
              "отправляемся исследовать!",
        "cs": "Nejdřív to hlavní: mluvím sedmi jazyky! Přímo tady v "
              "Nastavení najdi „Application language:“ a vyber si svůj — "
              "přepnu se v tu samou chvíli. Připraveno? Tak vyrážíme na "
              "průzkum!",
        "fr": "Commençons par l'essentiel : je parle sept langues ! Ici "
              "même, dans les Réglages, trouvez « Application language: » "
              "et choisissez la vôtre — je changerai à l'instant même. "
              "Tout est prêt ? Alors partons explorer !",
    },
    "tour.sdcard": {
        "en": "The SD Card Utility! Mount a Next .hdf/.img image, browse "
              "it side by side with your PC files, drag things across — "
              "then boot it straight into CSpect or MAME.",
        "es": "¡La utilidad de tarjeta SD! Monta una imagen .hdf/.img del "
              "Next, explórala junto a tus archivos del PC, arrastra "
              "cosas de un lado a otro y arráncala directamente en "
              "CSpect o MAME.",
        "pt": "O utilitário de cartão SD! Monta uma imagem .hdf/.img do "
              "Next, explora-a lado a lado com os ficheiros do teu PC, "
              "arrasta coisas de um lado para o outro — e arranca-a "
              "diretamente no CSpect ou no MAME.",
        "pl": "Narzędzie karty SD! Zamontuj obraz .hdf/.img Nexta, "
              "przeglądaj go obok plików z PC, przeciągaj pliki między "
              "panelami — a potem uruchom go prosto w CSpect albo MAME.",
        "ru": "Утилита SD-карты! Смонтируйте образ .hdf/.img для Next, "
              "просматривайте его рядом с файлами ПК, перетаскивайте "
              "файлы туда-сюда — и запускайте образ прямо в CSpect или "
              "MAME.",
        "cs": "Nástroj SD karty! Připoj obraz .hdf/.img Nextu, procházej "
              "ho vedle souborů z PC, přetahuj soubory sem a tam — a pak "
              "ho spusť rovnou v CSpectu nebo MAME.",
        "fr": "L'utilitaire de carte SD ! Montez une image .hdf/.img du "
              "Next, parcourez-la côte à côte avec vos fichiers PC, "
              "glissez-déposez dans les deux sens — puis démarrez-la "
              "directement dans CSpect ou MAME.",
    },
    "tour.nextsync": {
        "en": "NextSync beams files to a REAL Spectrum Next over Wi-Fi: "
              "run .sync5 on the Next, press Start server here, and "
              "whoosh! The Remote Explorer view can even browse the "
              "Next's SD card from your chair.",
        "es": "NextSync envía archivos a un Spectrum Next REAL por Wi-Fi: "
              "ejecuta .sync5 en el Next, pulsa Iniciar servidor aquí y "
              "¡zas! La vista Remote Explorer incluso te deja explorar "
              "la tarjeta SD del Next sin levantarte de la silla.",
        "pt": "O NextSync envia ficheiros para um Spectrum Next A SÉRIO "
              "por Wi-Fi: corre o .sync5 no Next, carrega em Iniciar "
              "servidor aqui e zás! A vista Remote Explorer até te deixa "
              "explorar o cartão SD do Next sem sair da cadeira.",
        "pl": "NextSync przesyła pliki do PRAWDZIWEGO Spectrum Nexta "
              "przez Wi-Fi: uruchom .sync5 na Nexcie, wciśnij tutaj "
              "Start serwera i szuuu! Widok Remote Explorer pozwala "
              "nawet przeglądać kartę SD Nexta z fotela.",
        "ru": "NextSync передаёт файлы на НАСТОЯЩИЙ Spectrum Next по "
              "Wi-Fi: запустите .sync5 на Next, нажмите здесь «Запустить "
              "сервер» — и вжух! А вид Remote Explorer позволяет даже "
              "просматривать SD-карту Next, не вставая с кресла.",
        "cs": "NextSync posílá soubory do OPRAVDOVÉHO Spectra Next přes "
              "Wi-Fi: spusť na Nextu .sync5, tady stiskni Spustit server "
              "a šup! Pohled Remote Explorer ti dokonce umožní procházet "
              "SD kartu Nextu z křesla.",
        "fr": "NextSync téléporte des fichiers vers un VRAI Spectrum Next "
              "en Wi-Fi : lancez .sync5 sur le Next, appuyez ici sur "
              "Démarrer le serveur, et hop ! La vue Remote Explorer "
              "permet même de parcourir la carte SD du Next depuis votre "
              "fauteuil.",
    },
    "tour.getit": {
        "en": "GetIt browses zxnext.uk — the freshest Next software. One "
              "click to download, another to send it onto your SD image. "
              "New releases appear here like magic!",
        "es": "GetIt navega por zxnext.uk: el software más fresco para el "
              "Next. Un clic para descargar y otro para enviarlo a tu "
              "imagen SD. ¡Las novedades aparecen aquí como por arte de "
              "magia!",
        "pt": "O GetIt navega pelo zxnext.uk — o software mais fresco "
              "para o Next. Um clique para descarregar, outro para o "
              "enviar para a tua imagem SD. As novidades aparecem aqui "
              "como por magia!",
        "pl": "GetIt przegląda zxnext.uk — najświeższe oprogramowanie na "
              "Nexta. Jedno kliknięcie, by pobrać, drugie, by wysłać na "
              "obraz SD. Nowości pojawiają się tu jak za dotknięciem "
              "różdżki!",
        "ru": "GetIt просматривает zxnext.uk — самый свежий софт для "
              "Next. Один клик — скачать, второй — отправить на образ "
              "SD. Новинки появляются здесь как по волшебству!",
        "cs": "GetIt prochází zxnext.uk — nejčerstvější software pro "
              "Next. Jedním kliknutím stáhneš, druhým pošleš na obraz "
              "SD. Novinky se tu objevují jako mávnutím proutku!",
        "fr": "GetIt parcourt zxnext.uk — les logiciels Next les plus "
              "frais. Un clic pour télécharger, un autre pour l'envoyer "
              "sur votre image SD. Les nouveautés apparaissent ici comme "
              "par magie !",
    },
    "tour.zxart": {
        "en": "zxArt.ee is a treasure chest of Spectrum pixel art and "
              "AY music. Browse the gallery, preview SCREEN$ in all "
              "their 8-colour glory, and grab what you fancy.",
        "es": "zxArt.ee es un cofre del tesoro de pixel art y música AY "
              "del Spectrum. Explora la galería, previsualiza SCREEN$ en "
              "todo su esplendor de 8 colores y llévate lo que te guste.",
        "pt": "O zxArt.ee é um baú de tesouros de pixel art e música AY "
              "do Spectrum. Explora a galeria, pré-visualiza SCREEN$ em "
              "toda a sua glória de 8 cores e leva o que te apetecer.",
        "pl": "zxArt.ee to skarbiec pixel artu i muzyki AY ze Spectrum. "
              "Przeglądaj galerię, oglądaj SCREEN$ w pełnej chwale 8 "
              "kolorów i bierz, co ci się podoba.",
        "ru": "zxArt.ee — сундук с сокровищами: пиксель-арт и AY-музыка "
              "Spectrum. Листайте галерею, смотрите SCREEN$ во всей "
              "красе восьми цветов и забирайте, что приглянётся.",
        "cs": "zxArt.ee je truhla pokladů — spectristický pixel art a AY "
              "hudba. Procházej galerii, prohlížej SCREEN$ v plné slávě "
              "osmi barev a ber si, co se ti líbí.",
        "fr": "zxArt.ee est un coffre aux trésors de pixel art et de "
              "musique AY du Spectrum. Parcourez la galerie, admirez les "
              "SCREEN$ dans toute leur gloire 8 couleurs et prenez ce "
              "qui vous plaît.",
    },
    "tour.zxdb": {
        "en": "ZXDB (via ZXInfo.dk) is the great library of the Spectrum "
              "world — nearly every game ever released, with screenshots, "
              "details and downloads. Ask it anything!",
        "es": "ZXDB (vía ZXInfo.dk) es la gran biblioteca del mundo "
              "Spectrum: casi todos los juegos jamás publicados, con "
              "capturas, detalles y descargas. ¡Pregúntale lo que sea!",
        "pt": "O ZXDB (via ZXInfo.dk) é a grande biblioteca do mundo "
              "Spectrum: quase todos os jogos alguma vez publicados, com "
              "capturas, detalhes e descargas. Pergunta-lhe o que "
              "quiseres!",
        "pl": "ZXDB (przez ZXInfo.dk) to wielka biblioteka świata "
              "Spectrum — niemal każda wydana gra, ze zrzutami ekranu, "
              "szczegółami i plikami. Pytaj o co chcesz!",
        "ru": "ZXDB (через ZXInfo.dk) — великая библиотека мира "
              "Spectrum: почти каждая когда-либо выпущенная игра, со "
              "скриншотами, деталями и загрузками. Спрашивайте что "
              "угодно!",
        "cs": "ZXDB (přes ZXInfo.dk) je velká knihovna spectristického "
              "světa — téměř každá kdy vydaná hra, se screenshoty, "
              "detaily a soubory ke stažení. Zeptej se na cokoli!",
        "fr": "ZXDB (via ZXInfo.dk) est la grande bibliothèque du monde "
              "Spectrum — presque tous les jeux jamais publiés, avec "
              "captures, détails et téléchargements. Demandez-lui "
              "n'importe quoi !",
    },
    "tour.favorites": {
        "en": "Anything you mark with a ♥ lands here — your favorites "
              "from every source, gathered in one cosy place.",
        "es": "Todo lo que marques con un ♥ acaba aquí: tus favoritos de "
              "todas las fuentes, reunidos en un solo lugar acogedor.",
        "pt": "Tudo o que marcares com um ♥ vem parar aqui — os teus "
              "favoritos de todas as fontes, reunidos num só lugar "
              "acolhedor.",
        "pl": "Wszystko, co oznaczysz ♥, trafia tutaj — twoje ulubione ze "
              "wszystkich źródeł, zebrane w jednym przytulnym miejscu.",
        "ru": "Всё, что вы отметите ♥, попадает сюда — ваше избранное из "
              "всех источников, собранное в одном уютном месте.",
        "cs": "Vše, co označíš ♥, skončí tady — tvé oblíbené ze všech "
              "zdrojů, pohromadě na jednom útulném místě.",
        "fr": "Tout ce que vous marquez d'un ♥ atterrit ici — vos favoris "
              "de toutes les sources, réunis dans un seul endroit "
              "douillet.",
    },
    "tour.unite": {
        "en": "Unite! is my favourite spell: it searches GetIt, ZXDB and "
              "zxArt all at once and merges the results — the whole "
              "Spectrum universe in a single query.",
        "es": "¡Unite! es mi hechizo favorito: busca en GetIt, ZXDB y "
              "zxArt a la vez y combina los resultados. Todo el universo "
              "Spectrum en una sola consulta.",
        "pt": "O Unite! é o meu feitiço preferido: pesquisa no GetIt, "
              "ZXDB e zxArt ao mesmo tempo e junta os resultados — todo "
              "o universo Spectrum numa única pesquisa.",
        "pl": "Unite! to moje ulubione zaklęcie: przeszukuje GetIt, ZXDB "
              "i zxArt naraz i scala wyniki — cały wszechświat Spectrum "
              "w jednym zapytaniu.",
        "ru": "Unite! — моё любимое заклинание: ищет в GetIt, ZXDB и "
              "zxArt одновременно и объединяет результаты. Вся вселенная "
              "Spectrum в одном запросе.",
        "cs": "Unite! je mé oblíbené kouzlo: hledá v GetIt, ZXDB a zxArt "
              "najednou a výsledky spojí — celý spectristický vesmír v "
              "jediném dotazu.",
        "fr": "Unite! est mon sortilège préféré : il interroge GetIt, "
              "ZXDB et zxArt en même temps et fusionne les résultats — "
              "tout l'univers Spectrum en une seule requête.",
    },
    "tour.itchio": {
        "en": "The itch.io tab connects your itch.io account: browse your "
              "collections and purchases, and install Next goodies — "
              "including CSpect itself, hat-tip included.",
        "es": "La pestaña itch.io conecta tu cuenta de itch.io: explora "
              "tus colecciones y compras e instala maravillas para el "
              "Next, incluido el propio CSpect.",
        "pt": "O separador itch.io liga a tua conta itch.io: explora as "
              "tuas coleções e compras e instala maravilhas para o Next "
              "— incluindo o próprio CSpect.",
        "pl": "Karta itch.io łączy się z twoim kontem itch.io: przeglądaj "
              "kolekcje i zakupy oraz instaluj smakołyki na Nexta — w "
              "tym samego CSpecta.",
        "ru": "Вкладка itch.io подключает ваш аккаунт itch.io: "
              "просматривайте коллекции и покупки и устанавливайте "
              "вкусности для Next — включая сам CSpect.",
        "cs": "Záložka itch.io propojí tvůj účet na itch.io: procházej "
              "své sbírky a nákupy a instaluj dobroty pro Next — včetně "
              "samotného CSpectu.",
        "fr": "L'onglet itch.io connecte votre compte itch.io : parcourez "
              "vos collections et achats et installez des merveilles "
              "pour le Next — y compris CSpect lui-même.",
    },
    "tour.settings": {
        "en": "Settings! Themes, colors, gallery sizes, update checks, "
              "the application language — and my own on/off switch "
              "(please be gentle with that one).",
        "es": "¡Ajustes! Temas, colores, tamaños de galería, "
              "comprobaciones de actualización, el idioma de la "
              "aplicación... y mi propio interruptor (sé amable con "
              "ese, por favor).",
        "pt": "Definições! Temas, cores, tamanhos de galeria, "
              "verificações de atualização, o idioma da aplicação — e o "
              "meu próprio interruptor (sê gentil com esse, por favor).",
        "pl": "Ustawienia! Motywy, kolory, rozmiary galerii, sprawdzanie "
              "aktualizacji, język aplikacji — i mój własny wyłącznik "
              "(obchodź się z nim delikatnie, proszę).",
        "ru": "Настройки! Темы, цвета, размеры галереи, проверка "
              "обновлений, язык приложения — и мой собственный "
              "выключатель (пожалуйста, обращайтесь с ним бережно).",
        "cs": "Nastavení! Motivy, barvy, velikosti galerie, kontrola "
              "aktualizací, jazyk aplikace — a můj vlastní vypínač "
              "(s tím prosím zacházej něžně).",
        "fr": "Les Réglages ! Thèmes, couleurs, tailles de galerie, "
              "vérification des mises à jour, la langue de l'application "
              "— et mon propre interrupteur (soyez doux avec celui-là, "
              "s'il vous plaît).",
    },
    "tour.help": {
        "en": "The Help tab keeps the built-in guide and the legal "
              "notes — and the full user manual lives on the GitHub "
              "wiki. That's where I read my lines!",
        "es": "La pestaña de Ayuda guarda la guía integrada y las notas "
              "legales, y el manual de usuario completo vive en la wiki "
              "de GitHub. ¡De ahí saco mis guiones!",
        "pt": "O separador de Ajuda guarda o guia integrado e as notas "
              "legais — e o manual de utilizador completo vive na wiki "
              "do GitHub. É de lá que tiro as minhas falas!",
        "pl": "Karta Pomoc zawiera wbudowany przewodnik i noty prawne — "
              "a pełny podręcznik użytkownika mieszka na wiki GitHuba. "
              "Stamtąd biorę swoje kwestie!",
        "ru": "Вкладка «Справка» хранит встроенное руководство и "
              "юридические заметки, а полное руководство пользователя "
              "живёт на вики GitHub. Оттуда я и беру свои реплики!",
        "cs": "Záložka Nápověda obsahuje vestavěného průvodce a právní "
              "poznámky — a úplná uživatelská příručka bydlí na GitHub "
              "wiki. Odtud čtu své repliky!",
        "fr": "L'onglet Aide contient le guide intégré et les mentions "
              "légales — et le manuel utilisateur complet vit sur le "
              "wiki GitHub. C'est là que je lis mes répliques !",
    },
}

# ── Jokes and stories (lists per language, same length everywhere) ────────
JOKES = {
    "en": [
        "Why did the Spectrum Next refuse to argue? It didn't want to "
        "raise an interrupt.",
        "128K of RAM? Luxury! I learned magic on 48K and a rubber "
        "keyboard.",
        "I asked the Next for a spell check. It replied: R Tape loading "
        "error, 0:1.",
        "Why does the Next never get lost? It always knows its border "
        "colour.",
        "My wand runs at 28MHz now. The turbo button? Pure sorcery.",
    ],
    "es": [
        "¿Por qué el Spectrum Next no quiso discutir? No quería lanzar "
        "una interrupción.",
        "¿128K de RAM? ¡Un lujo! Yo aprendí magia con 48K y un teclado "
        "de goma.",
        "Le pedí al Next que revisara un hechizo. Respondió: R Tape "
        "loading error, 0:1.",
        "¿Por qué el Next nunca se pierde? Siempre conoce su color de "
        "borde.",
        "Mi varita ya va a 28MHz. ¿El botón turbo? Pura hechicería.",
    ],
    "pt": [
        "Porque é que o Spectrum Next recusou discutir? Não queria "
        "lançar uma interrupção.",
        "128K de RAM? Um luxo! Eu aprendi magia com 48K e um teclado de "
        "borracha.",
        "Pedi ao Next para rever um feitiço. Respondeu: R Tape loading "
        "error, 0:1.",
        "Porque é que o Next nunca se perde? Sabe sempre a cor da sua "
        "border.",
        "A minha varinha já corre a 28MHz. O botão turbo? Pura "
        "feitiçaria.",
    ],
    "pl": [
        "Dlaczego Spectrum Next nie chciał się kłócić? Nie chciał "
        "zgłaszać przerwania.",
        "128K RAM-u? Luksus! Ja uczyłem się magii na 48K i gumowej "
        "klawiaturze.",
        "Poprosiłem Nexta o sprawdzenie zaklęcia. Odpowiedział: R Tape "
        "loading error, 0:1.",
        "Dlaczego Next nigdy się nie gubi? Zawsze zna kolor swojego "
        "borderu.",
        "Moja różdżka chodzi teraz na 28MHz. Przycisk turbo? Czysta "
        "magia.",
    ],
    "ru": [
        "Почему Spectrum Next отказался спорить? Не хотел вызывать "
        "прерывание.",
        "128К памяти? Роскошь! Я учился магии на 48К и резиновой "
        "клавиатуре.",
        "Я попросил Next проверить заклинание. Он ответил: R Tape "
        "loading error, 0:1.",
        "Почему Next никогда не теряется? Он всегда знает цвет своего "
        "бордюра.",
        "Моя палочка теперь работает на 28МГц. Кнопка турбо? Чистое "
        "волшебство.",
    ],
    "cs": [
        "Proč se Spectrum Next odmítl hádat? Nechtěl vyvolat přerušení.",
        "128K RAM? Přepych! Já se učil kouzlit na 48K a gumové "
        "klávesnici.",
        "Požádal jsem Next o kontrolu kouzla. Odpověděl: R Tape loading "
        "error, 0:1.",
        "Proč se Next nikdy neztratí? Vždycky zná barvu svého borderu.",
        "Moje hůlka teď běží na 28MHz. Tlačítko turbo? Čiré "
        "čarodějnictví.",
    ],
    "fr": [
        "Pourquoi le Spectrum Next a-t-il refusé de se disputer ? Il ne "
        "voulait pas lever une interruption.",
        "128K de RAM ? Le luxe ! Moi, j'ai appris la magie sur 48K et un "
        "clavier en caoutchouc.",
        "J'ai demandé au Next de vérifier un sort. Il a répondu : R Tape "
        "loading error, 0:1.",
        "Pourquoi le Next ne se perd-il jamais ? Il connaît toujours la "
        "couleur de son border.",
        "Ma baguette tourne à 28MHz maintenant. Le bouton turbo ? Pure "
        "sorcellerie.",
    ],
}

STORIES = {
    "en": [
        "Once upon a time, in 1982, Sir Clive gave the world a little "
        "black box with a rainbow on its corner — and bedrooms "
        "everywhere turned into software houses overnight.",
        "The Next was born on Kickstarter in 2017, when thousands of "
        "Speccy fans wished the rainbow back to life. Their wish came "
        "true — accelerated to 28MHz, with sprites and Copper to spare!",
        "Legend says every LOAD \"\" once took five minutes of screeching "
        "song. The Next remembers all the old spells — and casts new "
        "ones in FPGA hardware, faster than any tape ever dreamed.",
    ],
    "es": [
        "Érase una vez, en 1982, Sir Clive dio al mundo una cajita negra "
        "con un arcoíris en la esquina, y los dormitorios de medio mundo "
        "se convirtieron en estudios de software de la noche a la "
        "mañana.",
        "El Next nació en Kickstarter en 2017, cuando miles de fans del "
        "Speccy desearon revivir el arcoíris. Su deseo se cumplió: "
        "¡acelerado a 28MHz, con sprites y Copper de sobra!",
        "Cuenta la leyenda que cada LOAD \"\" costaba cinco minutos de "
        "canción chirriante. El Next recuerda todos los hechizos "
        "antiguos y lanza otros nuevos en hardware FPGA, más rápido de "
        "lo que ninguna cinta soñó jamás.",
    ],
    "pt": [
        "Era uma vez, em 1982, Sir Clive deu ao mundo uma caixinha preta "
        "com um arco-íris no canto — e os quartos de meio mundo "
        "tornaram-se casas de software de um dia para o outro.",
        "O Next nasceu no Kickstarter em 2017, quando milhares de fãs do "
        "Speccy desejaram trazer o arco-íris de volta. O desejo "
        "realizou-se: acelerado a 28MHz, com sprites e Copper de sobra!",
        "Reza a lenda que cada LOAD \"\" custava cinco minutos de "
        "cantoria estridente. O Next lembra-se de todos os feitiços "
        "antigos — e lança novos em hardware FPGA, mais depressa do que "
        "qualquer cassete alguma vez sonhou.",
    ],
    "pl": [
        "Dawno, dawno temu, w 1982 roku, Sir Clive dał światu małe "
        "czarne pudełko z tęczą w rogu — i sypialnie na całym świecie z "
        "dnia na dzień zmieniły się w studia software'owe.",
        "Next narodził się na Kickstarterze w 2017 roku, gdy tysiące "
        "fanów Speccy zapragnęło wskrzesić tęczę. Życzenie się "
        "spełniło: przyspieszony do 28MHz, ze sprite'ami i Copperem w "
        "zapasie!",
        "Legenda głosi, że każde LOAD \"\" kosztowało pięć minut "
        "piszczącej pieśni. Next pamięta wszystkie stare zaklęcia — a "
        "nowe rzuca w układzie FPGA, szybciej niż jakakolwiek taśma "
        "śmiała marzyć.",
    ],
    "ru": [
        "Давным-давно, в 1982 году, сэр Клайв подарил миру чёрную "
        "коробочку с радугой на уголке — и спальни по всему миру за "
        "одну ночь превратились в студии разработки.",
        "Next родился на Kickstarter в 2017-м, когда тысячи фанатов "
        "Speccy загадали вернуть радугу к жизни. Желание сбылось: "
        "разогнан до 28МГц, со спрайтами и Copper в придачу!",
        "Легенда гласит, что каждый LOAD \"\" стоил пяти минут "
        "скрипучей песни. Next помнит все старые заклинания — и творит "
        "новые в железе FPGA, быстрее, чем мечтала любая кассета.",
    ],
    "cs": [
        "Kdysi dávno, v roce 1982, dal Sir Clive světu malou černou "
        "krabičku s duhou v rohu — a ložnice po celém světě se přes noc "
        "proměnily v softwarové dílny.",
        "Next se narodil na Kickstarteru v roce 2017, když si tisíce "
        "fanoušků Speccy přály duhu zpátky k životu. Přání se splnilo: "
        "zrychlený na 28MHz, se sprity a Copperem k tomu!",
        "Legenda praví, že každý LOAD \"\" stál pět minut skřípavé "
        "písně. Next si pamatuje všechna stará kouzla — a nová sesílá v "
        "FPGA hardwaru, rychleji, než o jakém kdy kazeta snila.",
    ],
    "fr": [
        "Il était une fois, en 1982, Sir Clive offrit au monde une "
        "petite boîte noire avec un arc-en-ciel dans le coin — et les "
        "chambres du monde entier devinrent des studios de logiciels du "
        "jour au lendemain.",
        "Le Next est né sur Kickstarter en 2017, quand des milliers de "
        "fans du Speccy souhaitèrent ressusciter l'arc-en-ciel. Leur "
        "vœu fut exaucé : accéléré à 28MHz, avec sprites et Copper en "
        "prime !",
        "La légende dit que chaque LOAD \"\" coûtait cinq minutes de "
        "chant strident. Le Next se souvient de tous les anciens sorts "
        "— et en lance de nouveaux en FPGA, plus vite qu'aucune "
        "cassette n'a jamais osé en rêver.",
    ],
}

# ── The tour script ──────────────────────────────────────────────────────
# (tab-title constant NAME in zxnu_config, dialogue key above, wiki page).
# The wizard resolves the constant at runtime, finds the tab whose title
# starts with it, and SKIPS steps whose tab is hidden (zxArt/ZXDB feature
# flags, itch.io without itch-dl). The Settings/Help steps match the
# literal titles the monolith uses for those two addTab calls.
TOUR_STEPS = (
    # The tour OPENS on Settings so the user can pick their language first
    # (the wizard re-speaks the step live when they do).
    ("Settings 🔩",                        "tour.language",  "Settings-tab"),
    ("ZX_NEXT_UNITE_TAB_TITLE_GOOEY",     "tour.sdcard",    "SD-Card-Utility-tab"),
    ("ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC",  "tour.nextsync",  "NextSync-tab"),
    ("ZX_NEXT_UNITE_TAB_TITLE_GETIT",     "tour.getit",     "GetIt-tab"),
    ("ZX_NEXT_UNITE_TAB_TITLE_ZXART",     "tour.zxart",     "zxArt-tab"),
    ("ZX_NEXT_UNITE_TAB_TITLE_ZXDB",      "tour.zxdb",      "ZXDB-tab"),
    ("ZX_NEXT_UNITE_TAB_TITLE_FAVORITES", "tour.favorites", "Favorites-tab"),
    ("ZX_NEXT_UNITE_TAB_TITLE_ALLINONE",  "tour.unite",     "Unite-tab"),
    ("ZX_NEXT_UNITE_TAB_TITLE_ITCHIO",    "tour.itchio",    "itch-io-tab"),
    ("Settings 🔩",                        "tour.settings",  "Settings-tab"),
    ("?",                                  "tour.help",      "Help-tab"),
)

# Tour steps that browse third-party online catalogues: the wizard softly
# appends the "tour.disclaimer" rights reminder to these.
DISCLAIMER_STEPS = {"tour.getit", "tour.zxdb", "tour.zxart", "tour.unite"}

# The heroes the wizard thanks at the end of the tour (fills the {names}
# placeholder of "tour.kudos"; proper names, shared by every language).
KUDOS_NAMES = ("em00k, Jari, Leonis, Remy, Phoebus, Adrian, Mike, Thomas, "
               "Robin, Flash, the Chris(s) 🙂, Holub, the Richard(s) 🙂, "
               "the Gary(s) 🙂, Henrique, Victor, Nicolas & Anthony, "
               "Simon, Jamie, Tim")

# The manual's landing page (the wizard's "Read the manual" outside a tour).
USER_MANUAL_PAGE = "User-Manual"

# The project repository (every guide bubble links to it).
GITHUB_URL = "https://github.com/jclauzel/ZX-Next-Unite"

# ── In-depth guides ──────────────────────────────────────────────────────
# Branching, step-by-step help per tab, driven by zxnu_wizard.py's guide
# engine. Node ids double as TEXTS keys (so the translation tripwire covers
# every screen). Node schema: "buttons" = ordered (button-key, target)
# pairs where target is another node id or "close"; optional "linux_extra"
# = a TEXTS key appended only on Linux; optional "goto" = a zxnu_config
# tab-title constant offered as a "Take me there" button; optional
# "gesture" for the sprite. The wizard offers a guide the first time its
# tab is visited in a session ("guide.offer").
GUIDES = {
    "sdcard": {
        "tab": "ZX_NEXT_UNITE_TAB_TITLE_GOOEY",
        "page": "SD-Card-Utility-tab",
        "start": "sd.images",
        "nodes": {
            "sd.images":       {"buttons": [("btn.next", "sd.hdfmonkey")]},
            "sd.hdfmonkey":    {"buttons": [("btn.next", "sd.cspect")]},
            "sd.cspect":       {"buttons": [("btn.yes", "sd.cspect_steps"),
                                            ("btn.no", "sd.mame")]},
            "sd.cspect_steps": {"goto": "ZX_NEXT_UNITE_TAB_TITLE_ITCHIO",
                                "buttons": [("btn.next", "sd.manipulate_ask")]},
            "sd.mame":         {"linux_extra": "sd.mame.linux",
                                "buttons": [("btn.next", "sd.manipulate_ask")]},
            "sd.manipulate_ask": {"buttons": [("btn.yes", "sd.nextzxos"),
                                              ("btn.no", "close")]},
            "sd.nextzxos":     {"buttons": [("btn.next", "sd.explorers")]},
            "sd.explorers":    {"buttons": [("btn.next", "sd.emulators")]},
            "sd.emulators":    {"buttons": [("btn.close", "close")],
                                "gesture": "cast"},
        },
    },
    "nextsync": {
        "tab": "ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC",
        "page": "NextSync-tab",
        "start": "ns.what",
        "nodes": {
            # Intro, then a three-way branch: setup / Remote Explorer /
            # Classic Sync.
            "ns.what":   {"buttons": [("btn.next", "ns.compat")]},
            "ns.compat": {"buttons": [("btn.setup", "ns.setup1"),
                                      ("btn.remotexp", "ns.remote"),
                                      ("btn.classic", "ns.classic")]},
            # Branch 1: full setup, ending on the .sync5 spellbook.
            "ns.setup1": {"buttons": [("btn.next", "ns.setup2")]},
            "ns.setup2": {"buttons": [("btn.next", "ns.options")]},
            "ns.options": {"buttons": [("btn.close", "close")],
                           "gesture": "cast"},
            # Branch 2: the Remote Explorer, focused.
            "ns.remote": {"buttons": [("btn.close", "close")],
                          "gesture": "cast"},
            # Branch 3: Classic Sync -> sync root -> server.
            "ns.classic": {"buttons": [("btn.next", "ns.root")]},
            "ns.root":   {"buttons": [("btn.next", "ns.server")]},
            "ns.server": {"buttons": [("btn.close", "close")],
                          "gesture": "cast"},
        },
    },
}


def wizard_tr(key, lang):
    """Translate a wizard dialogue key; English is the fallback."""
    entry = TEXTS.get(key)
    if not entry:
        return key
    return entry.get(lang) or entry.get("en") or key


def wizard_lines(table, lang):
    """The jokes/stories list for *lang*, falling back to English."""
    return table.get(lang) or table["en"]
