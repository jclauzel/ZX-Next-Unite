"""Online catalogue API layer for ZX-Next-Unite: GetIt (zxnext.uk),
ZXDB (api.zxinfo.dk/v3) and zxART (zxart.ee).

Extracted verbatim from zx-next-unite.py (strangler refactor, sitting #2):
the shared HTTP retry helpers, the per-service fetchers, response parsers,
website-URL builders and the thread-safe zxArt name-resolution caches.
Pure Python — no Qt — so everything here is unit-testable without a
QApplication (see tests/test_api_parsers.py). zx-next-unite star-imports
this module, keeping every historical name unchanged.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from zxnu_config import (
    GETIT_BASE_URL,
    GETIT_PAGE_SIZE,
    GETIT_USER_AGENT,
    ZXART_BASE_URL,
    ZXART_USER_AGENT,
    ZXDB_BASE_URL,
    ZXDB_PAGE_SIZE,
    ZXDB_USER_AGENT,
    _zxart_lang,
)

# ---------------------------------------------------------------------------
# Shared HTTP retry helper
# ---------------------------------------------------------------------------

_HTTP_RETRYABLE = (429, 502, 503, 504)


def _is_retryable_connection_error(exc: OSError) -> bool:
    """Return True for transient connection-level errors that are worth retrying.

    Covers:
    - ``ConnectionResetError`` / WinError 10054 ("An existing connection was
      forcibly closed by the remote host") — raised directly or wrapped inside
      ``urllib.error.URLError`` whose ``__str__`` reads "urlopen error [WinError
      10054] …".
    - ``ConnectionRefusedError``, ``BrokenPipeError``, ``TimeoutError``.
    - Any ``urllib.error.URLError`` whose *reason* is one of the above.
    """
    import urllib.error as _ue
    # URLError wraps the underlying socket/OS error in .reason
    inner = exc.reason if isinstance(exc, _ue.URLError) else exc
    return isinstance(inner, (
        ConnectionResetError,
        ConnectionRefusedError,
        BrokenPipeError,
        TimeoutError,
        OSError,  # catches generic errno-based errors (ECONNRESET etc.)
    ))


def _http_fetch_bytes_with_retry(
    url: str,
    *,
    headers: dict = None,
    method: str = "GET",
    timeout: int = 20,
    _retries: int = 3,
    _backoff: float = 1.5,
) -> bytes:
    """Fetch *url* as bytes with retry/back-off on transient errors.

    Retries on HTTP 429/502/503/504 and on connection-level OS errors,
    including ``ConnectionResetError`` / WinError 10054 ("An existing
    connection was forcibly closed by the remote host") whether raised
    directly or wrapped inside ``urllib.error.URLError``.
    Closes the HTTPError response before sleeping on retryable HTTP codes.
    """
    import urllib.error as _ue
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    delay = _backoff
    last_exc = None
    for attempt in range(1, _retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except _ue.HTTPError as exc:
            if exc.code in _HTTP_RETRYABLE:
                last_exc = exc
                exc.close()
                # logging.warning(
                #     "_http_fetch_bytes_with_retry: HTTP %d on attempt %d/%d for %s",
                #     exc.code, attempt, _retries, url,
                # )
                if attempt < _retries:
                    time.sleep(delay)
                    delay *= 2
            else:
                raise
        except OSError as exc:
            # Catches urllib.error.URLError (subclass of OSError) and direct
            # socket errors such as ConnectionResetError (WinError 10054).
            last_exc = exc
            # logging.warning(
            #     "_http_fetch_bytes_with_retry: connection error on attempt %d/%d for %s: %s",
            #     attempt, _retries, url, exc,
            # )
            if attempt < _retries:
                time.sleep(delay)
                delay *= 2
    raise last_exc


def _http_fetch_with_cd_retry(
    url: str,
    *,
    headers: dict = None,
    timeout: int = 60,
    _retries: int = 3,
    _backoff: float = 1.5,
):
    """Fetch *url*, returning ``(content_disposition_header, bytes)`` with retry.

    Retries on HTTP 429/502/503/504 and on connection-level OS errors,
    including ``ConnectionResetError`` / WinError 10054.
    """
    import urllib.error as _ue
    req = urllib.request.Request(url, headers=headers or {})
    delay = _backoff
    last_exc = None
    for attempt in range(1, _retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                cd   = resp.headers.get("Content-Disposition", "")
                data = resp.read()
            return cd, data
        except _ue.HTTPError as exc:
            if exc.code in _HTTP_RETRYABLE:
                last_exc = exc
                exc.close()
                logging.warning(
                    "_http_fetch_with_cd_retry: HTTP %d on attempt %d/%d for %s",
                    exc.code, attempt, _retries, url,
                )
                if attempt < _retries:
                    time.sleep(delay)
                    delay *= 2
            else:
                raise
        except OSError as exc:
            # Catches urllib.error.URLError and direct socket errors
            # including ConnectionResetError (WinError 10054).
            last_exc = exc
            logging.warning(
                "_http_fetch_with_cd_retry: connection error on attempt %d/%d for %s: %s",
                attempt, _retries, url, exc,
            )
            if attempt < _retries:
                time.sleep(delay)
                delay *= 2
    raise last_exc


def _http_head_ok_with_retry(
    url: str,
    *,
    headers: dict = None,
    timeout: int = 10,
    _retries: int = 3,
    _backoff: float = 1.5,
) -> bool:
    """Send a HEAD request; return True when the server responds with status < 400.

    Retries on *_HTTP_RETRYABLE* HTTP codes and on connection-level OS errors,
    including ``ConnectionResetError`` / WinError 10054 ("An existing
    connection was forcibly closed by the remote host").
    Returns False after exhausting retries so callers treat the URL as
    unavailable rather than propagating an exception.
    """
    import urllib.error as _ue
    req = urllib.request.Request(url, headers=headers or {}, method="HEAD")
    delay = _backoff
    for attempt in range(1, _retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status < 400
        except _ue.HTTPError as exc:
            if exc.code in _HTTP_RETRYABLE:
                exc.close()
                if attempt < _retries:
                    time.sleep(delay)
                    delay *= 2
            else:
                return exc.code < 400
        except OSError:
            # Catches urllib.error.URLError and direct socket errors
            # including ConnectionResetError (WinError 10054).
            if attempt < _retries:
                time.sleep(delay)
                delay *= 2
    return False


# ---------------------------------------------------------------------------
# GetIt API helpers
# ---------------------------------------------------------------------------

def getit_fetch(path: str, timeout: int = 10) -> str:
    """Fetch a plain-text response from the GetIt server and return the body string."""
    url = GETIT_BASE_URL + path
    data = _http_fetch_bytes_with_retry(
        url, headers={"User-Agent": GETIT_USER_AGENT}, timeout=timeout
    )
    return data.decode("utf-8", errors="replace")


def getit_parse_file_list(text: str):
    """Parse a ^R^…^END^ file listing into a list of dicts.

    The entire response is a single caret-delimited line:
      ^R^[total]^[id]^[title]^[author]^[size]^[category]^…^END^

    Returns (entries, total, page, total_pages) where entries is a list of
    {'id', 'title', 'author', 'size', 'category'} dicts.
    """
    text = text.strip()
    entries = []
    total = 0
    page = 1
    total_pages = 1

    # Split on ^ and drop empty leading/trailing tokens
    parts = [p for p in text.split("^") if p != ""]
    # parts[0] == 'R', parts[1] == total (int), then groups of 5: id,title,author,size,category
    if not parts or parts[0] != "R":
        return entries, total, page, total_pages

    try:
        total = int(parts[1])
    except (IndexError, ValueError):
        pass

    i = 2
    while i + 4 < len(parts):
        chunk = parts[i:i + 5]
        if chunk[0] == "END":
            break
        entries.append({
            "id":       chunk[0].strip(),
            "title":    chunk[1].strip(),
            "author":   chunk[2].strip(),
            "size":     chunk[3].strip(),
            "category": chunk[4].strip(),
        })
        i += 5

    total_pages = max(1, (total + GETIT_PAGE_SIZE - 1) // GETIT_PAGE_SIZE) if total else 1

    return entries, total, page, total_pages


def getit_parse_detail(text: str) -> dict:
    """Parse an entry-detail response into a dict of TAG->value pairs."""
    text = text.strip()
    detail = {}
    TAGS = ["IDID", "TITL", "LINK", "FSIZ", "AUTH", "HITS", "MD5", "VER", "DESC", "DATE", "URL"]
    for tag in TAGS:
        marker = f"^{tag}^"
        idx = text.find(marker)
        if idx == -1:
            continue
        value_start = idx + len(marker)
        # Find the next known tag or end of string
        end = len(text)
        for other_tag in TAGS:
            if other_tag == tag:
                continue
            other_idx = text.find(f"^{other_tag}^", value_start)
            if other_idx != -1 and other_idx < end:
                end = other_idx
        raw_value = text[value_start:end].strip(" \r\n^")
        # Strip embedded newlines from description per spec
        raw_value = raw_value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()
        detail[tag] = raw_value
    return detail


# ---------------------------------------------------------------------------
# ZXDB (zxinfo.dk) helpers
# ---------------------------------------------------------------------------

def zxdb_entry_website_url(eid) -> str:
    """Return the public zxinfo.dk landing-page URL for a ZXDB entry id.

    ZXInfo ids are commonly zero-padded to 7 digits but the website happily
    accepts the unpadded form too. We pass the id through unchanged when it
    is non-numeric (e.g. magazine ids that may include letters)."""
    s = str(eid or "").strip()
    if not s:
        return ""
    try:
        n = int(s)
        s = f"{n:07d}"
    except (TypeError, ValueError):
        pass
    return f"https://zxinfo.dk/details/{urllib.parse.quote(s)}"


def zxart_entry_website_url(entry) -> str:
    """Return the public zxart.ee landing-page URL for a zxArt gallery entry.

    The url is provided by the zxArt API on each prod / picture record, so
    we read it from *entry* (which carries the API record under ``_source``).
    Falls back to a generic search URL if the API record did not include
    a direct url."""
    if not isinstance(entry, dict):
        return ""
    src = entry.get("_source") if isinstance(entry.get("_source"), dict) else {}
    for key in ("url", "Url", "URL", "pageUrl", "page_url"):
        u = src.get(key) if isinstance(src, dict) else None
        if isinstance(u, str) and u.strip():
            return u.strip()
    title = entry.get("title") or ""
    if title:
        return "https://zxart.ee/eng/search/?searchString=" + urllib.parse.quote(title)
    return ""


def zxdb_fetch_json(path: str, timeout: int = 15, _retries: int = 3, _backoff: float = 1.5):
    """GET JSON from the ZXInfo API. *path* must include any query string.
    Identifies the client per API policy via a custom User-Agent.
    Retries up to *_retries* times on transient server errors (5xx / network)
    with exponential backoff starting at *_backoff* seconds."""
    import time as _time
    url = ZXDB_BASE_URL + path
    last_exc = None
    for attempt in range(_retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": ZXDB_USER_AGENT,
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            last_exc = exc
            try:
                exc.close()
            except Exception:
                pass
            if exc.code in (502, 503, 504, 429) and attempt < _retries - 1:
                _time.sleep(_backoff * (2 ** attempt))
                continue
            raise
        except (urllib.error.URLError, OSError) as exc:
            # urllib.error.URLError wraps socket errors such as
            # ConnectionResetError (WinError 10054) — all are retried here.
            last_exc = exc
            if attempt < _retries - 1:
                _time.sleep(_backoff * (2 ** attempt))
                continue
            raise
    raise last_exc


def zxdb_fetch_bytes(url: str, timeout: int = 30) -> bytes:
    """Fetch raw bytes (e.g. a screenshot or game file) using ZXDB UA."""
    return _http_fetch_bytes_with_retry(
        url, headers={"User-Agent": ZXDB_USER_AGENT}, timeout=timeout
    )


def zxdb_pick(d: dict, *keys, default=""):
    """Return the first non-empty value from *d* among *keys*."""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def zxdb_parse_search(payload) -> tuple:
    """Normalize a /search JSON response into (entries, total, page, total_pages, page_size).

    Each entry is a dict: id, title, year, author(s), machine, genre, score.
    Handles a couple of envelope shapes seen on the ZXInfo API (Elastic-style
    `hits.hits[]._source` and a flatter `{ items: [...] }`).
    """
    entries = []
    total = 0
    page = 0
    total_pages = 1
    page_size = ZXDB_PAGE_SIZE

    if not isinstance(payload, dict):
        return entries, total, page, total_pages, page_size

    # Pagination metadata (may appear under different keys)
    # ZXInfo v3 uses ES envelope: hits.total.value (or hits.total as int)
    _hits_meta = payload.get("hits")
    if isinstance(_hits_meta, dict):
        _hits_total = _hits_meta.get("total")
        if isinstance(_hits_total, dict):
            total = int(_hits_total.get("value", 0) or 0)
        elif isinstance(_hits_total, (int, float)):
            total = int(_hits_total)
        else:
            total = int(zxdb_pick(payload, "hits_count", "total", "totalHits", default=0) or 0)
    else:
        total = int(zxdb_pick(payload, "hits_count", "total", "totalHits", default=0) or 0)
    page  = int(zxdb_pick(payload, "current_page", "currentPage", "page", default=0) or 0)
    total_pages = int(zxdb_pick(payload, "total_pages", "totalPages", "pages", default=0) or 0)
    page_size = int(zxdb_pick(payload, "size", "pageSize", default=ZXDB_PAGE_SIZE) or ZXDB_PAGE_SIZE)

    # Pull the array of hits
    hits = []
    if isinstance(payload.get("hits"), dict) and isinstance(payload["hits"].get("hits"), list):
        hits = payload["hits"]["hits"]
    elif isinstance(payload.get("hits"), list):
        hits = payload["hits"]
    elif isinstance(payload.get("items"), list):
        hits = payload["items"]
    elif isinstance(payload.get("results"), list):
        hits = payload["results"]

    for h in hits:
        if isinstance(h, dict) and "_source" in h and isinstance(h["_source"], dict):
            src = h["_source"]
            eid = h.get("_id") or src.get("id") or src.get("entry_id") or ""
            score = h.get("_score", "")
        else:
            src = h if isinstance(h, dict) else {}
            eid = src.get("id") or src.get("entry_id") or ""
            score = src.get("_score", src.get("score", ""))

        # Authors / publishers may be a list of dicts
        author = ""
        for key in ("authors", "publishers"):
            v = src.get(key)
            if isinstance(v, list) and v:
                names = []
                for a in v:
                    if isinstance(a, dict):
                        nm = a.get("name") or a.get("groupName") or ""
                    else:
                        nm = str(a)
                    if nm:
                        names.append(nm)
                if names:
                    author = ", ".join(names)
                    break
            elif isinstance(v, str) and v:
                author = v
                break

        machine = zxdb_pick(src, "machineType", "machine_type", "machine")
        genre   = zxdb_pick(src, "genreType", "genre", "genretype")
        year    = src.get("originalYearOfRelease") or src.get("yearOfRelease") or src.get("year") or ""
        title   = zxdb_pick(src, "title", "fullTitle", "name")

        entries.append({
            "id":      str(eid),
            "title":   str(title),
            "year":    str(year),
            "author":  str(author),
            "machine": str(machine),
            "genre":   str(genre),
            "score":   "" if score == "" else f"{score:.1f}" if isinstance(score, (int, float)) else str(score),
        })

    if not total_pages and page_size > 0 and total:
        total_pages = max(1, (total + page_size - 1) // page_size)

    return entries, total, page or 0, max(1, total_pages or 1), page_size


def zxdb_parse_game_detail(payload) -> dict:
    """Extract a flat detail dict from a /games/{id} response.

    Returns: title, year, authors, publishers, machine, genre, language,
    description, remarks, screenshot_url, downloads (list of {format, url, type}).
    """
    if not isinstance(payload, dict):
        return {}

    src = payload
    if "_source" in payload and isinstance(payload["_source"], dict):
        src = payload["_source"]

    def _join_names(v):
        if isinstance(v, list):
            out = []
            for a in v:
                if isinstance(a, dict):
                    nm = a.get("name") or a.get("groupName") or ""
                    if nm:
                        out.append(nm)
                elif isinstance(a, str) and a:
                    out.append(a)
            return ", ".join(out)
        if isinstance(v, str):
            return v
        return ""

    detail = {
        "id":          str(payload.get("_id") or src.get("id") or ""),
        "title":       str(zxdb_pick(src, "title", "fullTitle", "name")),
        "year":        str(src.get("originalYearOfRelease") or src.get("yearOfRelease") or src.get("year") or ""),
        "authors":     _join_names(src.get("authors")),
        "publishers":  _join_names(src.get("publishers")),
        "machine":     str(zxdb_pick(src, "machineType", "machine_type", "machine")),
        "genre":       str(zxdb_pick(src, "genreType", "genre", "genretype")),
        "language":    str(zxdb_pick(src, "language")),
        "description": "",
        "remarks":     str(zxdb_pick(src, "remarks", "originalPublication")),
        "screenshot_url": "",
        "screenshots":   [],   # list of {url, type}
        "text_files":    [],   # list of {url, type} — readable .txt/.nfo pages
        "downloads":   [],
    }

    # ZXInfo nests publishers inside releases[].publishers in /games/{id}.
    # Fall back to release-level publishers when top-level is empty.
    if not detail["publishers"]:
        rel_pub_names: list = []
        seen_pub: set = set()
        rels0 = src.get("releases") or []
        if isinstance(rels0, list):
            for rel in rels0:
                if not isinstance(rel, dict):
                    continue
                rp = rel.get("publishers")
                names_str = _join_names(rp)
                if not names_str:
                    continue
                for nm in [n.strip() for n in names_str.split(",")]:
                    if nm and nm not in seen_pub:
                        seen_pub.add(nm)
                        rel_pub_names.append(nm)
        if rel_pub_names:
            detail["publishers"] = ", ".join(rel_pub_names)

    # Description: usually under additionals or comments – best-effort.
    desc_candidates = [
        src.get("description"),
        src.get("comments"),
        src.get("manual"),
    ]
    for d in desc_candidates:
        if isinstance(d, str) and d.strip():
            detail["description"] = d.strip()
            break
        if isinstance(d, list) and d:
            joined = " ".join(str(x) for x in d if x)
            if joined.strip():
                detail["description"] = joined.strip()
                break

    # Screenshots / additionals: collect ALL image-like entries for slideshow.
    image_exts = (".png", ".gif", ".jpg", ".jpeg", ".bmp", ".scr")
    # Web-renderable raster formats; preferred over raw .scr screen dumps when
    # the same screen is offered in more than one format.
    web_image_exts = (".png", ".gif", ".jpg", ".jpeg", ".bmp")
    # Plain-text assets (manuals/instructions/.nfo) that the Pygame item viewer
    # can render as a log console.  Collected separately from screenshots so the
    # detail-pane slideshow and thumbnails stay images-only.
    text_exts = (".txt", ".nfo", ".diz", ".asc", ".md")
    seen_urls = set()
    seen_text_urls = set()
    # Maps a screen's base filename (without extension) to its index in
    # detail["screenshots"], so the same picture offered both as a PNG and as
    # a raw .scr screen dump (e.g. ZXInfo "additionalDownloads") is counted
    # only once instead of inflating the slideshow page count.
    seen_stems = {}

    def _abs_url(u):
        if not u:
            return ""
        if u.startswith("/"):
            # ZXInfo hosts its own *rendered* screen images (/zxscreens/…) under
            # https://zxinfo.dk/media, but the raw archive paths it references
            # for screen dumps (.scr) and downloads — /pub/… and /zxdb/… — are
            # NOT on zxinfo.dk/media (they 404 there); they live on the
            # spectrumcomputing.co.uk mirror. Route each to the host that serves
            # it, otherwise e.g. a .scr loading screen never renders.
            if u.lower().startswith("/zxscreens/"):
                return "https://zxinfo.dk/media" + u
            return "https://spectrumcomputing.co.uk" + u
        return u

    # Gather candidate "asset" lists from top-level AND from each release.
    asset_lists = []
    for key in ("screens", "additionals", "additionalDownloads"):
        v = src.get(key)
        if isinstance(v, list):
            asset_lists.append(v)
    rels = src.get("releases") or []
    if isinstance(rels, list):
        for rel in rels:
            if not isinstance(rel, dict):
                continue
            for key in ("screens", "additionals", "additionalDownloads"):
                v = rel.get(key)
                if isinstance(v, list):
                    asset_lists.append(v)

    for arr in asset_lists:
        for a in arr:
            if not isinstance(a, dict):
                continue
            url = _abs_url(a.get("url") or a.get("path"))
            if not url:
                continue
            t = (a.get("type") or "").lower()
            fmt = (a.get("format") or "").lower()
            ulow = url.lower()
            is_image = (
                any(ulow.endswith(ext) for ext in image_exts)
                or "picture" in fmt
                or any(s in t for s in (
                    "running", "loading", "screenshot", "screen",
                    "inlay", "cover", "advert", "map", "picture",
                    "media scan", "tape", "cassette", "disk", "box",
                ))
            )
            if not is_image:
                # Surface readable text files (e.g. "Instructions | Document
                # (TXT)") as viewer pages — the Pygame item viewer renders them
                # as a log console; the Qt viewer filters them back out.
                if ulow.endswith(text_exts) and url not in seen_text_urls:
                    seen_text_urls.add(url)
                    detail["text_files"].append({
                        "url":  url,
                        "type": str(a.get("type") or ""),
                    })
                continue
            if url in seen_urls:
                continue
            # Deduplicate the same screen offered in multiple formats (e.g. a
            # PNG screenshot, an animated GIF and its raw .scr screen dump). Key
            # on the base filename without extension and keep the best format.
            #
            # Preference (lower = better): static web raster (.png/.jpg/.bmp) is
            # ideal; then the native .scr screen dump (the viewer decodes it
            # crisply); then .gif LAST — a ZX loading screen offered as a GIF is
            # usually animated (the FLASH effect), so its static .scr twin is
            # preferred over a flickering GIF.
            def _fmt_priority(e):
                e = (e or "").lower()
                if e in (".png", ".jpg", ".jpeg", ".bmp"):
                    return 0
                if e == ".scr":
                    return 1
                if e == ".gif":
                    return 2
                return 3
            base = os.path.basename(ulow)
            stem, ext = os.path.splitext(base)
            # Dedup key = (screen kind, normalised stem). Including the kind
            # stops two *different* screens that happen to share a base name
            # (e.g. a "Running" GIF and a "Loading" SCR both named
            # "BattleShips_2") from being merged. Normalising the stem — dropping
            # a trailing -load/-run/… qualifier — lets the *same* screen offered
            # under slightly different names ("BattleShips_2-load.gif" vs
            # "BattleShips_2.scr") collapse into a single entry, so its best
            # format wins instead of both appearing.
            def _screen_kind(tt):
                tt = (tt or "").lower()
                if "load" in tt:  return "loading"
                if "run" in tt:   return "running"
                if "inlay" in tt or "cover" in tt or "box" in tt: return "inlay"
                if "map" in tt:   return "map"
                return "screen"
            def _norm_stem(s):
                s = s.lower()
                for suff in ("-loading", "-running", "-load", "-run", "-screen",
                             "_loading", "_running", "_load", "_run"):
                    if s.endswith(suff):
                        return s[:-len(suff)]
                return s
            key = (_screen_kind(t), _norm_stem(stem))
            prev_idx = seen_stems.get(key)
            if prev_idx is not None:
                prev_url = detail["screenshots"][prev_idx]["url"]
                prev_ext = os.path.splitext(prev_url.lower())[1]
                if _fmt_priority(ext) < _fmt_priority(prev_ext):
                    # This format is better than the one we already kept.
                    seen_urls.discard(prev_url)
                    seen_urls.add(url)
                    detail["screenshots"][prev_idx] = {
                        "url":  url,
                        "type": str(a.get("type") or ""),
                    }
                continue
            seen_urls.add(url)
            seen_stems[key] = len(detail["screenshots"])
            detail["screenshots"].append({
                "url":  url,
                "type": str(a.get("type") or ""),
            })

    # Prefer the "loading" splash screen as the very first frame (it is the
    # iconic title artwork), then the in-game "running" screen, then any other
    # screen. This first frame is also used as the gallery thumbnail below.
    def _shot_priority(s):
        t = (s.get("type") or "").lower()
        if "loading" in t:   return 0
        if "running" in t:   return 1
        if "screen"  in t:   return 2
        return 3
    detail["screenshots"].sort(key=_shot_priority)
    if detail["screenshots"]:
        detail["screenshot_url"] = detail["screenshots"][0]["url"]

    # Releases / downloads – collect every file we can find:
    #   * releases[].files                 (game tape/disk images)
    #   * releases[].additionals           (per-release manuals, poke files, scans)
    #   * top-level src["additionals"]     (general manuals, poke files, scans)
    seen_dl_urls = set()

    def _add_download(entry: dict, release_year: str = ""):
        if not isinstance(entry, dict):
            return
        url = _abs_url(entry.get("path") or entry.get("url") or entry.get("downloadPath"))
        if not url or url in seen_dl_urls:
            return
        seen_dl_urls.add(url)
        try:
            host = urllib.parse.urlparse(url).netloc or ""
        except Exception:
            host = ""
        fname = os.path.basename(urllib.parse.urlparse(url).path) or ""
        detail["downloads"].append({
            "url":      url,
            "format":   str(entry.get("format") or ""),
            "type":     str(entry.get("type") or entry.get("format") or ""),
            "size":     str(entry.get("size") or ""),
            "filename": fname,
            "source":   host or "zxinfo",
            "year":     str(release_year or entry.get("yearOfRelease") or ""),
        })

    releases = src.get("releases") or []
    if isinstance(releases, list):
        for rel in releases:
            if not isinstance(rel, dict):
                continue
            release_year = rel.get("yearOfRelease") or rel.get("year") or ""
            for key in ("files", "dl", "additionals", "additionalDownloads"):
                items = rel.get(key)
                if isinstance(items, list):
                    for f in items:
                        _add_download(f, release_year)

    for key in ("additionals", "additionalDownloads"):
        top = src.get(key)
        if isinstance(top, list):
            for f in top:
                _add_download(f)

    return detail


# ---------------------------------------------------------------------------
# zxART (zxart.ee) helpers
# ---------------------------------------------------------------------------

_RE_ID_ONLY_URL = re.compile(r"/id:\d+/?$", re.IGNORECASE)

def _filter_download_urls(downloads: list) -> list:
    """Remove entries whose URL looks like a bare id-only path with no file
    extension (e.g. ``…/id:12345/``).  These are API browse URLs, not
    downloadable files, and will not work as direct downloads."""
    if not downloads:
        return downloads
    filtered = []
    for d in downloads:
        url = (d.get("url") or "").strip()
        if not url:
            continue
        path = urllib.parse.urlparse(url).path
        if _RE_ID_ONLY_URL.search(path):
            continue
        filtered.append(d)
    return filtered


def zxart_safe_url(url: str) -> str:
    """Percent-encode any non-ASCII characters in *url* so the request can be
    sent over HTTP. Some zxArt asset URLs (e.g. Clive prod images) include
    Cyrillic characters in their filenames which would otherwise cause
    ``UnicodeEncodeError`` inside ``http.client``."""
    try:
        if not url:
            return url
        # Already pure ASCII -> nothing to do.
        url.encode("ascii")
        return url
    except UnicodeEncodeError:
        pass
    try:
        parts = urllib.parse.urlsplit(url)
        # Preserve reserved characters that are legal in their respective
        # components; only percent-encode the bytes that are not valid ASCII.
        path     = urllib.parse.quote(parts.path,     safe="/:@!$&'()*+,;=-._~%")
        query    = urllib.parse.quote(parts.query,    safe="=&%:/@!$'()*+,;-._~")
        fragment = urllib.parse.quote(parts.fragment, safe="=&%:/@!$'()*+,;-._~")
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, fragment))
    except Exception:
        return url


def zxart_fetch_json(path: str, timeout: int = 15, _retries: int = 3, _backoff: float = 1.5):
    """GET JSON from the zxART API. *path* is appended to ZXART_BASE_URL.
    Sends the mandatory User-Agent header on every request.
    Retries up to *_retries* times on transient HTTP errors with exponential back-off."""
    import urllib.error
    url = zxart_safe_url(ZXART_BASE_URL + path)
    delay = _backoff
    last_exc = None
    for attempt in range(1, _retries + 1):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": ZXART_USER_AGENT,
                "Accept": "application/json",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if exc.code in _HTTP_RETRYABLE:
                last_exc = exc
                exc.close()
                logging.warning(
                    "zxart_fetch_json: HTTP %d on attempt %d/%d for %s",
                    exc.code, attempt, _retries, path,
                )
                if attempt < _retries:
                    time.sleep(delay)
                    delay *= 2
            else:
                raise
        except OSError as exc:
            # urllib.error.URLError (subclass of OSError) wraps socket errors
            # such as ConnectionResetError (WinError 10054) — all retried here.
            last_exc = exc
            if attempt < _retries:
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise last_exc


def zxart_fetch_bytes(url: str, timeout: int = 30) -> bytes:
    """Fetch raw bytes from any URL, identifying as zxART user agent."""
    return _http_fetch_bytes_with_retry(
        zxart_safe_url(url), headers={"User-Agent": ZXART_USER_AGENT}, timeout=timeout
    )


# Process-level caches for zxArt author / group name lookups.
# The API answers one entity per call, so we memoize to avoid re-querying.
# These dicts are read from the UI thread (table population) and written from
# background worker threads (prefetch / progressive resolve), so every access
# is guarded by _ZXART_NAME_CACHE_LOCK to make check-then-set atomic and avoid
# data races. All caches are kept entirely in memory for the process lifetime.
_ZXART_NAME_CACHE_LOCK = threading.RLock()
_ZXART_AUTHOR_NAME_CACHE: dict = {}
_ZXART_GROUP_NAME_CACHE:  dict = {}
_ZXART_PUBLISHER_NAME_CACHE: dict = {}


def _zxart_resolve_author_name(author_id) -> str:
    """Resolve a numeric zxArt authorId to its display title via the API.

    Uses the documented /export:author/filter:authorId=<id>/ endpoint.
    Returns the title string, or "" if the lookup fails / yields nothing.
    Results are cached for the lifetime of the process.
    """
    if author_id in (None, "", 0, "0"):
        return ""
    try:
        key = int(author_id)
    except (TypeError, ValueError):
        return str(author_id)
    with _ZXART_NAME_CACHE_LOCK:
        if key in _ZXART_AUTHOR_NAME_CACHE:
            return _ZXART_AUTHOR_NAME_CACHE[key]
    name = ""
    try:
        resp = zxart_fetch_json(f"/export:author/filter:authorId={key}/")
        rows = (resp.get("responseData") or {}).get("author") or []
        if rows:
            name = str(rows[0].get("title") or "")
    except Exception:
        name = ""
    with _ZXART_NAME_CACHE_LOCK:
        _ZXART_AUTHOR_NAME_CACHE[key] = name
    return name


def _zxart_resolve_group_name(group_id) -> str:
    """Resolve a numeric zxArt groupId to its display title via the API.

    Uses the documented /export:group/filter:groupId=<id>/ endpoint.
    Returns the title string, or "" if the lookup fails / yields nothing.
    Results are cached for the lifetime of the process.
    """
    if group_id in (None, "", 0, "0"):
        return ""
    try:
        key = int(group_id)
    except (TypeError, ValueError):
        return str(group_id)
    with _ZXART_NAME_CACHE_LOCK:
        if key in _ZXART_GROUP_NAME_CACHE:
            return _ZXART_GROUP_NAME_CACHE[key]
    name = ""
    try:
        resp = zxart_fetch_json(f"/export:group/filter:groupId={key}/")
        rows = (resp.get("responseData") or {}).get("group") or []
        if rows:
            name = str(rows[0].get("title") or "")
    except Exception:
        name = ""
    with _ZXART_NAME_CACHE_LOCK:
        _ZXART_GROUP_NAME_CACHE[key] = name
    return name


def _zxart_resolve_author_names(author_ids) -> str:
    """Resolve a list of authorIds to a comma-separated display string.

    Unknown IDs fall back to the raw numeric value so we never silently
    drop information.
    """
    out = []
    for aid in author_ids or []:
        name = _zxart_resolve_author_name(aid)
        out.append(name if name else str(aid))
    return ", ".join(s for s in out if s)


def _zxart_resolve_group_names(group_ids) -> str:
    """Resolve a list of groupIds to a comma-separated display string."""
    out = []
    for gid in group_ids or []:
        name = _zxart_resolve_group_name(gid)
        out.append(name if name else str(gid))
    return ", ".join(s for s in out if s)


def _zxart_resolve_publisher_name(publisher_id) -> str:
    """Resolve a numeric zxArt publisherId to a display name.

    The public zxArt API has no working ``export:publisher`` entity, but in
    practice publisher ids are reused from the ``group`` namespace
    (e.g. publisherId ``366520`` is the same as groupId ``366520`` →
    ``ZX Online``). We therefore look the id up via ``export:group`` first.
    Falls back to the raw numeric id so the caller never gets an empty value.
    """
    if publisher_id in (None, "", 0, "0"):
        return ""
    name = _zxart_resolve_group_name(publisher_id)
    if name:
        return name
    return str(publisher_id)


def _zxart_resolve_publisher_names(publisher_ids) -> str:
    """Resolve a list of zxArt publisher ids to a comma-separated string.

    Uses :func:`_zxart_resolve_publisher_name` which treats publisher ids as
    group ids — that matches the actual zxArt data model where the same
    numeric id is reused across the two namespaces.
    """
    out = []
    for pid in publisher_ids or []:
        name = _zxart_resolve_publisher_name(pid)
        out.append(name if name else str(pid))
    return ", ".join(s for s in out if s)


def _zxart_scrape_publishers_from_prod_url(prod_url: str) -> str:
    """Fetch the English zxArt landing page for *prod_url* and return the
    publisher name(s) parsed from its ``<meta name="description">`` tag,
    which is rendered as ``... published by <Publisher> in <Year>``.

    Returns an empty string if the URL is missing or the pattern is not
    found. Result is cached per-URL for the process lifetime.
    """
    if not prod_url:
        return ""
    url = str(prod_url)
    # Force the English landing page so the meta description is in English
    # regardless of which localized URL the API returned.
    if "/rus/soft/" in url:
        url = url.replace("/rus/soft/", "/eng/software/")
    elif "/rus/" in url:
        url = url.replace("/rus/", "/eng/")
    cache_key = ("prod_url", url)
    with _ZXART_NAME_CACHE_LOCK:
        if cache_key in _ZXART_PUBLISHER_NAME_CACHE:
            return _ZXART_PUBLISHER_NAME_CACHE[cache_key]
    name = ""
    try:
        html = _http_fetch_bytes_with_retry(
            url,
            headers={
                "User-Agent":      "Mozilla/5.0 ZX-Next-Unite",
                "Accept-Language": "en",
            },
            timeout=15,
        ).decode("utf-8", errors="replace")
        m = re.search(r"published by ([^\"<]+?) in \d{4}", html, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
    except Exception:
        name = ""
    with _ZXART_NAME_CACHE_LOCK:
        _ZXART_PUBLISHER_NAME_CACHE[cache_key] = name
    return name


def _zxart_resolve_publishers_via_zxdb(title: str, year: str = "") -> str:
    """Cross-reference a zxArt production *title* against the ZXDB (ZXInfo)
    API to recover a human-readable publisher name when zxArt only exposes
    an opaque numeric publisher id.

    Strategy: search ZXDB by title (mode=tit) and return the publishers of
    the best hit. If *year* is provided, prefer hits whose
    ``originalYearOfRelease`` / ``yearOfRelease`` matches. Falls back to
    release-level publishers when the top-level record has none. Results
    are cached per (title, year) for the process lifetime.
    """
    if not title:
        return ""
    t = str(title).strip()
    if not t:
        return ""
    y = str(year or "").strip()
    cache_key = ("zxdb_title", t.lower(), y)
    with _ZXART_NAME_CACHE_LOCK:
        if cache_key in _ZXART_PUBLISHER_NAME_CACHE:
            return _ZXART_PUBLISHER_NAME_CACHE[cache_key]
    name = ""
    try:
        q = urllib.parse.quote(t)
        payload = zxdb_fetch_json(f"/search?query={q}&mode=tit&size=10")
        hits = ((payload or {}).get("hits") or {}).get("hits") or []
        # Prefer year-matched hits when a year is provided.
        def _pubs_from_hit(hit):
            d = zxdb_parse_game_detail(hit)
            return str(d.get("publishers") or "")
        def _year_of(hit):
            s = hit.get("_source") or {}
            return str(s.get("originalYearOfRelease")
                       or s.get("yearOfRelease")
                       or s.get("year") or "")
        if y:
            for h in hits:
                if _year_of(h) == y:
                    p = _pubs_from_hit(h)
                    if p:
                        name = p
                        break
        if not name:
            for h in hits:
                p = _pubs_from_hit(h)
                if p:
                    name = p
                    break
    except Exception:
        name = ""
    with _ZXART_NAME_CACHE_LOCK:
        _ZXART_PUBLISHER_NAME_CACHE[cache_key] = name
    return name


def _zxart_prefetch_names_for_entries(entries):
    """Pre-warm the group / publisher name caches for *entries*.

    Intended to be called from a background thread immediately after fetching
    a page of results.  That way :func:`_zxart_table_author_col` only hits
    the in-memory cache when it runs on the UI thread, keeping the UI smooth.

    This warms the *entire* resolution chain used by the author/group table
    column — group ids, publisher ids, and the HTML scrape fallback — so the
    UI thread never performs a network request while populating the table.

    Picture entries are skipped because their author field is already a plain
    string and they don't use group / publisher IDs.
    """
    for e in entries:
        src  = e.get("_source") or {}
        kind = (e.get("_kind") or "").lower()
        if kind == "zxart_picture":
            continue
        # 1. Groups (drives "Produced by").
        groups = [str(g) for g in (src.get("groups") or []) if g]
        if not groups:
            groups = [n for n in [_zxart_resolve_group_name(gid)
                                  for gid in (src.get("groupsIds") or [])] if n]
        # 2. Publishers (drives "Published by"); resolve ids then, if still
        #    empty, warm the HTML scrape fallback so the UI thread won't block.
        pub_ids = src.get("publishersIds") or []
        published_by = _zxart_resolve_publisher_names(pub_ids)
        if not published_by:
            _zxart_scrape_publishers_from_prod_url(str(src.get("url") or ""))


# ---------------------------------------------------------------------------
# Cache-only (non-blocking) zxArt name resolution.
#
# The functions above may perform a network request on a cold cache.  The UI
# thread must never do that, so the helpers below ONLY consult the in-memory
# caches and return None on a miss.  Callers running on the GUI thread use
# these to render immediately and schedule a background warm-up if needed.
# ---------------------------------------------------------------------------

def _zxart_cached_group_name(group_id):
    """Return the cached group title, or None if not yet resolved."""
    if group_id in (None, "", 0, "0"):
        return ""
    try:
        key = int(group_id)
    except (TypeError, ValueError):
        return str(group_id)
    with _ZXART_NAME_CACHE_LOCK:
        return _ZXART_GROUP_NAME_CACHE.get(key)


def _zxart_cached_publisher_name(publisher_id):
    """Return the cached publisher title (group namespace), or None on miss."""
    if publisher_id in (None, "", 0, "0"):
        return ""
    try:
        key = int(publisher_id)
    except (TypeError, ValueError):
        return str(publisher_id)
    with _ZXART_NAME_CACHE_LOCK:
        return _ZXART_GROUP_NAME_CACHE.get(key)


def _zxart_cached_scraped_publisher(prod_url: str):
    """Return the cached scraped publisher for *prod_url*, or None on miss."""
    if not prod_url:
        return ""
    url = str(prod_url)
    if "/rus/soft/" in url:
        url = url.replace("/rus/soft/", "/eng/software/")
    elif "/rus/" in url:
        url = url.replace("/rus/", "/eng/")
    with _ZXART_NAME_CACHE_LOCK:
        return _ZXART_PUBLISHER_NAME_CACHE.get(("prod_url", url))


def _zxart_author_col_cached(e):
    """Cache-only version of the table's author/group column.

    Returns a tuple ``(text, complete)``:
      * ``text``     — the best string we can build from in-memory caches.
      * ``complete`` — True if every required name was already cached (so no
        background warm-up is needed); False if a network lookup is still
        pending (the caller should warm the cache off the UI thread and then
        refresh the cell).

    This function performs NO network I/O and is safe to call on the GUI
    thread while populating a results table.
    """
    src  = e.get("_source") or {}
    kind = (e.get("_kind") or "").lower()
    if kind == "zxart_picture":
        return (e.get("author", ""), True)

    complete = True

    # 1. Groups -> "Produced by".
    groups = [str(g) for g in (src.get("groups") or []) if g]
    if not groups:
        for gid in (src.get("groupsIds") or []):
            name = _zxart_cached_group_name(gid)
            if name is None:
                complete = False
            elif name:
                groups.append(name)
    produced_by = ", ".join(groups)

    # 2. Authors (direct strings) when there are no groups.
    if not produced_by:
        authors = [str(a) for a in (src.get("authors") or []) if a]
        if authors:
            return (", ".join(authors), True)

    # 3. Publishers -> "Published by".
    published_by_parts = []
    for pid in (src.get("publishersIds") or []):
        name = _zxart_cached_publisher_name(pid)
        if name is None:
            complete = False
        elif name:
            published_by_parts.append(name)
    published_by = ", ".join(published_by_parts)
    if not published_by:
        scraped = _zxart_cached_scraped_publisher(str(src.get("url") or ""))
        if scraped is None:
            complete = False
        elif scraped:
            published_by = scraped

    parts = []
    if produced_by:  parts.append(f"Produced by: {produced_by}")
    if published_by: parts.append(f"Published by: {published_by}")
    text = " · ".join(parts) if parts else e.get("author", "")
    return (text, complete)


def zxart_parse_prod_list(response: dict) -> tuple:
    """Parse a zxART API response for zxProd entities.

    Returns (entries, total) where each entry has keys:
    id, title, year, author, machine, genre, _kind, _source.
    """
    entries = []
    if not isinstance(response, dict):
        return entries, 0

    total = 0
    try:
        total = int(response.get("totalAmount") or 0)
    except (TypeError, ValueError):
        pass

    prods = (response.get("responseData") or {}).get("zxProd", [])
    if not isinstance(prods, list):
        prods = []

    for prod in prods:
        if not isinstance(prod, dict):
            continue
        pid   = str(prod.get("id") or "")
        title = str(prod.get("title") or "")
        year  = str(prod.get("year") or "")
        # groupsIds -> we resolve names separately when detail is loaded;
        # use description as author placeholder
        authors_info = prod.get("authorsInfo") or []
        group_ids    = prod.get("groupsIds") or []
        author_hint  = ""
        if group_ids:
            author_hint = f"{len(group_ids)} group(s)"
        elif authors_info:
            author_hint = f"{len(authors_info)} author(s)"

        compo = str(prod.get("compo") or "")
        party_place = prod.get("partyPlace")
        genre = compo or ""
        if party_place:
            genre = f"{genre} (#{party_place})" if genre else f"#{party_place}"

        entries.append({
            "id":      pid,
            "title":   title,
            "year":    year,
            "author":  author_hint,
            "machine": ", ".join(str(h) for h in (prod.get("hardwareRequired") or [])),
            "genre":   genre,
            "_kind":   "zxart_prod",
            "_source": prod,
        })

    return entries, total


# Letter-to-approximate-offset table for the zxART Games category (zxProdCategory=92177,
# ~23 000 entries ordered by title,asc).  Values are conservative lower-bound offsets so
# that a ±500-item window centred on the estimate reliably contains all titles starting
# with the requested letter.
# Category ID in zxART that covers all software productions (games + demos)
# Selected zxART API language ("eng" | "pol" | "spa").  Mutated by the
# language combo in the zxArt pane and persisted to the cfg file.  All
# zxART HTTP request builders use _zxart_lang() to honour this value.



def _zxart_title_at(offset: int) -> str:
    """Return the lowercase title at ``offset`` within the title-asc ordering.

    Uses a ``limit:1`` probe which is reliable at any depth in the catalog
    (unlike larger windows which can hit HTTP 500 on some offsets).
    Returns an empty string on error.
    """
    try:
        resp = zxart_fetch_json(
            f"/export:zxProd/language:{_zxart_lang()}/start:{offset}/limit:1"
            f"/order:title,asc",
            timeout=15,
        )
        prods = (resp.get("responseData") or {}).get("zxProd", [])
        if isinstance(prods, list) and prods:
            return str(prods[0].get("title") or "").lower()
    except Exception as exc:
        logging.warning("_zxart_title_at(%d) failed: %s", offset, exc)
    return ""


def zxart_prefix_search(query: str, progress_cb=None,
                        window: int = 200, max_results: int = 200) -> tuple:
    """Find zxART productions whose title *starts with* ``query``.

    Strategy:
      1. Probe the total catalog size via the sentinel.
      2. Binary-search the title-asc ordering using single-item probes
         to locate the first offset whose title >= query.
      3. Fetch a small window starting at that offset and keep entries
         whose lowercase title starts with the query.

    Issues roughly log2(N) + 1 small requests (~16 total for 23k entries).
    Each request is a few hundred bytes to a few KB.  Returns
    ``(entries, total_matched)``.
    """
    if not query:
        return [], 0

    q_lower = query.lower()

    def _notify(msg: str):
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    _notify(f"Searching zxART for titles starting with '{query}'…")

    # 1. catalog size — cheap single-item probe
    try:
        resp0 = zxart_fetch_json(
            f"/export:zxProd/language:{_zxart_lang()}/start:0/limit:1/order:date,desc",
            timeout=10,
        )
        total = int(resp0.get("totalAmount") or 0)
    except Exception:
        total = 0
    if total <= 0:
        total = 1000

    # 2. binary search for the first offset whose title >= query
    lo, hi = 0, total
    while lo < hi:
        mid = (lo + hi) // 2
        title = _zxart_title_at(mid)
        if not title:
            # treat unknown as "past the query" so we shrink to the lower half
            hi = mid
            continue
        if title < q_lower:
            lo = mid + 1
        else:
            hi = mid

    start = max(0, lo - 5)  # small back-step in case of rounding

    # 3. fetch a window and filter client-side
    path = (
        f"/export:zxProd/language:{_zxart_lang()}/start:{start}/limit:{window}"
        f"/order:title,asc"
    )
    try:
        resp = zxart_fetch_json(path, timeout=30)
        entries, _ = zxart_parse_prod_list(resp)
    except Exception as exc:
        # If the window straddles a known bad offset, shrink it.
        logging.warning("zxart_prefix_search window fetch failed: %s — retrying smaller", exc)
        entries = []
        for sub_off in range(start, start + window, 25):
            try:
                resp = zxart_fetch_json(
                    f"/export:zxProd/language:{_zxart_lang()}/start:{sub_off}/limit:25"
                    f"/order:title,asc",
                    timeout=20,
                )
                sub, _ = zxart_parse_prod_list(resp)
                entries.extend(sub)
            except Exception as sub_exc:
                logging.warning("zxart_prefix_search sub-window %d failed: %s", sub_off, sub_exc)

    # The window may begin slightly before the prefix and extend slightly past
    # it.  Trim to the contiguous run of entries that start with the query.
    matched = []
    seen_match = False
    for e in entries:
        title = (e.get("title") or "").lower()
        if title.startswith(q_lower):
            matched.append(e)
            seen_match = True
        elif seen_match:
            # past the prefix range — done
            break

    for e in matched:
        e["_kind"] = "zxart_prod"
    _notify("")
    return matched[:max_results], len(matched)


def zxart_client_search(query: str, progress_cb=None) -> tuple:
    """Search zxART productions by title using fast prefix search.

    Returns (matched_entries, total_matched).
    """
    if not query:
        return [], 0
    return zxart_prefix_search(query, progress_cb=progress_cb)


def zxart_parse_picture_list(response: dict) -> tuple:
    """Parse a zxART API response for zxPicture entities."""
    entries = []
    if not isinstance(response, dict):
        return entries, 0

    total = 0
    try:
        total = int(response.get("totalAmount") or 0)
    except (TypeError, ValueError):
        pass

    pics = (response.get("responseData") or {}).get("zxPicture", [])
    if not isinstance(pics, list):
        pics = []

    for pic in pics:
        if not isinstance(pic, dict):
            continue
        pid   = str(pic.get("id") or "")
        title = str(pic.get("title") or "")
        year  = str(pic.get("year") or "")
        rating = str(pic.get("rating") or "")
        tags   = pic.get("tags") or []
        genre  = ", ".join(str(t) for t in tags[:3]) if tags else ""

        entries.append({
            "id":      pid,
            "title":   title,
            "year":    year,
            "author":  "",
            "machine": str(pic.get("type") or ""),
            "genre":   genre,
            "_kind":   "zxart_picture",
            "_source": pic,
        })

    return entries, total
