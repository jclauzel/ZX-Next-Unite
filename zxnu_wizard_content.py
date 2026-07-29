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

# The manual's landing page (the wizard's "Read the manual" outside a tour).
USER_MANUAL_PAGE = "User-Manual"


def wizard_tr(key, lang):
    """Translate a wizard dialogue key; English is the fallback."""
    entry = TEXTS.get(key)
    if not entry:
        return key
    return entry.get(lang) or entry.get("en") or key


def wizard_lines(table, lang):
    """The jokes/stories list for *lang*, falling back to English."""
    return table.get(lang) or table["en"]
