"""itch.io integration helpers for zx-next-unite.

This module is intentionally self-contained and dependency-light:

* Detection — :func:`itchdl_available` reports whether the optional ``itch-dl``
  package is importable. The whole itch.io tab is gated on this so the feature
  stays optional.
* Browsing — collections, collection games, owned games and search are read
  straight from the public itch.io API (``api.itch.io``) with the user's
  personal API key (https://itch.io/user/settings/api-keys), via ``urllib``.
  This needs no third-party code and keeps the browsing UI responsive.
* Installing — the actual download of a collection item is delegated to
  ``itch-dl`` (run as a subprocess), matching its supported interface.

Every network call raises on failure; callers run them on a background thread
(see ``getit_run_in_thread`` in the main app) and surface errors in the UI.
"""

import contextlib
import io
import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from zxnu_config import (ITCH_API_BASE, ITCH_USER_AGENT, ITCH_PAGE_SIZE,
                         ITCH_MAX_PAGES, CSPECT_ITCH_URL, cspect_version_key)


# ── optional-dependency detection ──────────────────────────────────────────

def itchdl_available():
    """Return ``(ok, reason)``. *ok* is True when the optional ``itch-dl``
    package can be imported; *reason* is a short human-readable string used to
    hint the user how to enable the feature when it is missing."""
    try:
        import itch_dl  # noqa: F401
        return True, "itch-dl is installed"
    except Exception as exc:  # ImportError or a broken install
        return False, (
            "The optional 'itch-dl' package is not installed.\n"
            "Install it with:  pip install itch-dl\n"
            f"({exc})"
        )


# ── low-level API access ───────────────────────────────────────────────────

def _api_get(path, api_key, params=None, timeout=20):
    """GET ``{ITCH_API_BASE}{path}`` authenticated with *api_key* and return the
    decoded JSON object. Raises urllib/JSON errors on failure."""
    url = ITCH_API_BASE + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": ITCH_USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", errors="replace") or "{}")


def _normalise_game(game):
    """Map an itch.io API ``game`` object to the flat dict shape the gallery
    widgets expect (``id``, ``title``, ``url``, ``cover_url``, ``author`` …)."""
    if not isinstance(game, dict):
        return None
    user = game.get("user") or {}
    return {
        "id": str(game.get("id") or ""),
        "title": game.get("title") or "",
        "url": game.get("url") or "",
        "cover_url": game.get("cover_url") or game.get("still_cover_url") or "",
        "author": (user.get("display_name") or user.get("username") or ""),
        "short_text": game.get("short_text") or "",
        "classification": game.get("classification") or "",
        "min_price": game.get("min_price"),
        "_fav_source": "itchio",
        "source": "itchio",
    }


# ── public API used by the UI ──────────────────────────────────────────────

def validate_key(api_key, timeout=20):
    """Return ``(ok, message)``. On success *message* is the itch.io display
    name; on failure it is a short error suitable for the status label."""
    api_key = (api_key or "").strip()
    if not api_key:
        return False, "No API key entered."
    try:
        data = _api_get("/profile", api_key, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, "Invalid API key (itch.io rejected it)."
        return False, f"itch.io error: HTTP {exc.code}"
    except Exception as exc:
        return False, f"Connection failed: {exc}"
    user = (data or {}).get("user") or {}
    name = user.get("display_name") or user.get("username") or ""
    if not name:
        return False, "Invalid API key."
    return True, name


def list_collections(api_key, timeout=20):
    """Return a list of ``{'id', 'title', 'count'}`` for the user's
    collections (https://itch.io/my-collections)."""
    data = _api_get("/profile/collections", api_key, timeout=timeout)
    out = []
    for c in (data or {}).get("collections", []) or []:
        if not isinstance(c, dict):
            continue
        out.append({
            "id": str(c.get("id") or ""),
            "title": c.get("title") or f"Collection {c.get('id')}",
            "count": int(c.get("games_count") or 0),
        })
    return out


def collection_games(api_key, collection_id, max_pages=ITCH_MAX_PAGES, timeout=20):
    """Return the normalised game dicts in a collection, following pagination."""
    games = []
    page = 1
    while page <= max_pages:
        data = _api_get(
            f"/collections/{collection_id}/collection-games",
            api_key, params={"page": page}, timeout=timeout,
        )
        rows = (data or {}).get("collection_games") or []
        if not rows:
            break
        for row in rows:
            g = _normalise_game((row or {}).get("game"))
            if g and g["id"]:
                games.append(g)
        per_page = int((data or {}).get("per_page") or len(rows) or 1)
        if len(rows) < per_page:
            break
        page += 1
    return games


def owned_game_ids(api_key, max_pages=ITCH_MAX_PAGES, timeout=20):
    """Return a set of game-id strings the user owns (their itch.io library).
    Used to flag collection items the user already owns."""
    owned = set()
    page = 1
    while page <= max_pages:
        try:
            data = _api_get("/profile/owned-keys", api_key,
                            params={"page": page}, timeout=timeout)
        except Exception:
            break
        rows = (data or {}).get("owned_keys") or []
        if not rows:
            break
        for row in rows:
            g = (row or {}).get("game") or {}
            gid = g.get("id")
            if gid is not None:
                owned.add(str(gid))
        per_page = int((data or {}).get("per_page") or len(rows) or 1)
        if len(rows) < per_page:
            break
        page += 1
    return owned


def owned_games(api_key, max_pages=ITCH_MAX_PAGES, timeout=20):
    """Return the normalised game dicts the user owns (their itch.io library /
    purchases), following pagination. Like :func:`owned_game_ids` but returns
    the full game records so they can be browsed/searched."""
    games = []
    seen = set()
    page = 1
    while page <= max_pages:
        data = _api_get("/profile/owned-keys", api_key,
                        params={"page": page}, timeout=timeout)
        rows = (data or {}).get("owned_keys") or []
        if not rows:
            break
        for row in rows:
            g = _normalise_game((row or {}).get("game"))
            if g and g["id"] and g["id"] not in seen:
                seen.add(g["id"])
                games.append(g)
        per_page = int((data or {}).get("per_page") or len(rows) or 1)
        if len(rows) < per_page:
            break
        page += 1
    return games


def library_games(api_key, collections, max_pages=ITCH_MAX_PAGES, timeout=20):
    """Return the user's combined library — purchased/owned games plus every
    game across *collections* — de-duplicated by game id. Used to search the
    user's own itch.io content (collections + purchases)."""
    seen = set()
    out = []
    for g in owned_games(api_key, max_pages=max_pages, timeout=timeout):
        if g["id"] not in seen:
            seen.add(g["id"])
            out.append(g)
    for c in (collections or []):
        cid = c.get("id") if isinstance(c, dict) else c
        if not cid:
            continue
        try:
            for g in collection_games(api_key, cid, max_pages=max_pages,
                                      timeout=timeout):
                if g["id"] not in seen:
                    seen.add(g["id"])
                    out.append(g)
        except Exception:
            continue
    return out


def search_library(api_key, collections, query, timeout=20):
    """Return the user's library games (collections + purchases) whose title or
    author matches *query* (case-insensitive substring)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    lib = library_games(api_key, collections, timeout=timeout)
    return [g for g in lib
            if q in (g.get("title") or "").lower()
            or q in (g.get("author") or "").lower()]


def search_games(api_key, query, timeout=20):
    """Return normalised game dicts matching *query* via the global itch.io
    search API (the whole catalogue, not just the user's library)."""
    query = (query or "").strip()
    if not query:
        return []
    data = _api_get("/search/games", api_key,
                    params={"query": query}, timeout=timeout)
    out = []
    for g in (data or {}).get("games", []) or []:
        ng = _normalise_game(g)
        if ng and ng["id"]:
            out.append(ng)
    return out[:ITCH_PAGE_SIZE]


# ── install / installed-status ─────────────────────────────────────────────

def _game_slug(game):
    """Best-effort slug for a game from its url (``author.itch.io/the-game``)."""
    url = (game or {}).get("url") or ""
    m = re.search(r"itch\.io/([^/?#]+)", url)
    if m:
        return m.group(1)
    title = (game or {}).get("title") or ""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _itchdl_command(game_url, api_key, dest_dir):
    """Build the itch-dl invocation. Prefer ``python -m itch_dl`` (works in the
    current venv); the caller falls back to the ``itch-dl`` script on failure."""
    base = [sys.executable, "-m", "itch_dl"]
    return base + [game_url, "--api-key", api_key, "--download-to", dest_dir]


class _StreamToLog(io.TextIOBase):
    """A minimal writable text stream that forwards completed lines to a callback.

    A ``--windowed`` PyInstaller build has ``sys.stdout`` / ``sys.stderr`` set to
    ``None``, so itch-dl's tqdm progress bar (stderr) and ``print()`` summary
    (stdout) raise ``'NoneType' object has no attribute 'write'`` and abort the
    download. Substituting this stream keeps those writes working. tqdm redraws a
    line in place with ``\\r``; we only emit on ``\\n`` (keeping the text after the
    last ``\\r``) so progress redraws don't flood the log."""

    def __init__(self, emit):
        super().__init__()
        self._emit = emit
        self._buf = ""

    def writable(self):
        return True

    def write(self, s):
        if not s:
            return 0
        if not isinstance(s, str):
            try:
                s = s.decode("utf-8", "replace")
            except Exception:
                s = str(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.split("\r")[-1].rstrip()
            if line and self._emit:
                try:
                    self._emit(line)
                except Exception:
                    pass
        # Don't let an unterminated tqdm bar grow the buffer without bound.
        if len(self._buf) > 8192:
            self._buf = self._buf[-1024:]
        return len(s)

    def flush(self):
        return None


# Shown when itch-dl "succeeds" (exit 0 / no return value) but nothing actually
# downloaded. Since ~2025 itch.io fronts its game pages with a Cloudflare bot
# challenge that returns HTTP 403 to non-browser clients, so itch-dl can no
# longer scrape the page (or its data.json) to find the uploads — the itch.io
# *API* still works (the key validates and download keys are fetched), only the
# page/site fetch is blocked. This affects every itch-dl version (0.7.2 is the
# latest) regardless of user-agent, so the practical workaround is a manual
# browser download.
ITCHIO_BLOCKED_MSG = (
    "itch.io blocked the automated download: its game pages are now behind a "
    "Cloudflare bot challenge that returns HTTP 403 to itch-dl, so the page "
    "can't be read and nothing was downloaded. Open the item's itch.io page in "
    "your browser and download the file manually.")


def _itchdl_failure_from_output(text):
    """Return a user-facing message when itch-dl's captured output reports that a
    game download failed, otherwise "".

    itch-dl exits 0 and ``drive_downloads`` returns ``None`` even when a game
    fails — it merely prints ``Download failed for <url>:`` followed by the
    reason — so the exit code / return value cannot be trusted to detect
    failure. The Cloudflare 403 block gets a tailored, actionable message; any
    other failure surfaces itch-dl's own reason line."""
    if not text:
        return ""
    low = text.lower()
    reported = ("download failed for" in low
                or "could not download the game site" in low
                or "game data fetching failed" in low)
    if not reported:
        return ""
    if "403" in low or "forbidden" in low or "cloudflare" in low:
        return ITCHIO_BLOCKED_MSG
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("- ") and s[2:].strip():
            return s[2:].strip()
    return "itch-dl reported the download failed; nothing was downloaded."


def _download_produced_files(game, dest_dir):
    """True when a real file (not just an empty game folder) was downloaded for
    *game* under *dest_dir*.

    itch-dl creates the per-game folder even when the download fails, so an
    existing-but-empty folder must count as 'nothing downloaded'. Used as a
    catch-all backstop so a silently failed install is never reported as
    success regardless of how itch-dl signalled it."""
    path = installed_path(game, dest_dir)
    if not path or not os.path.isdir(path):
        return False
    for _root, _dirs, files in os.walk(path):
        if files:
            return True
    return False


def _install_in_process(game_url, api_key, dest_dir, log_cb=None):
    """Run itch-dl entirely in-process (no subprocess).

    This is required for the PyInstaller-frozen build: there ``sys.executable``
    is the GUI executable, not a Python interpreter, so
    ``[sys.executable, "-m", "itch_dl", …]`` does not run itch-dl — it relaunches
    the whole application (the "the app restarts and nothing downloads" symptom).
    itch-dl is bundled into the frozen exe and importable, and it downloads using
    threads (``tqdm.contrib.concurrent.thread_map``) rather than multiprocessing,
    so it is safe to drive its pipeline directly here.

    Mirrors ``itch_dl.cli.run()`` but without reading ``sys.argv`` / calling
    ``sys.exit``, and forwards itch-dl's logging (emitted on the root logger) to
    *log_cb*. Returns ``(ok, message)``; runs synchronously — call from a worker
    thread."""

    # Capture forwarded lines too: drive_downloads() prints "Download failed
    # for <url>:" and returns None even on failure, so scanning the output is
    # how the failure is actually detected below.
    _captured = []

    def _log(line):
        _captured.append(line)
        if log_cb:
            try:
                log_cb(line)
            except Exception:
                pass

    try:
        from itch_dl.config import Settings
        from itch_dl.handlers import get_jobs_for_url_or_path, preprocess_job_urls
        from itch_dl.downloader import drive_downloads
        from itch_dl.keys import get_download_keys
        from itch_dl.api import ItchApiClient
    except Exception as exc:
        return False, f"Could not load the bundled itch-dl: {exc}"

    # Forward itch-dl's progress (it logs on the root logger, level INFO) to the
    # UI log for the duration of the download, then detach again.
    class _CbHandler(logging.Handler):
        def emit(self, record):
            try:
                _log(self.format(record))
            except Exception:
                pass

    handler = _CbHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)

    # In a --windowed frozen exe, sys.stdout/sys.stderr are None. itch-dl's
    # tqdm/print writes would crash on that; redirect them to a log-forwarding
    # stream. Also disable logging.raiseExceptions so that any pre-existing root
    # StreamHandler bound to the None stream fails silently (logging swallows the
    # error) instead of dumping tracebacks once we provide a real stderr.
    writer = _StreamToLog(_log)
    prev_raise = logging.raiseExceptions
    logging.raiseExceptions = False
    try:
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            settings = Settings(api_key=api_key, download_to=dest_dir, parallel=1)

            # Validate the key up front (same check itch-dl's CLI makes).
            client = ItchApiClient(settings.api_key, settings.user_agent)
            profile_req = client.get("/profile")
            if not profile_req.ok:
                return False, f"itch.io rejected the API key: {profile_req.text}"

            jobs = get_jobs_for_url_or_path(game_url, settings)
            jobs = preprocess_job_urls(jobs, settings)
            if not jobs:
                return False, "itch-dl found nothing to download for this item."

            settings.download_to = os.path.normpath(settings.download_to or os.getcwd())
            os.makedirs(settings.download_to, exist_ok=True)

            keys = get_download_keys(client)
            drive_downloads(jobs, settings, keys)
            # drive_downloads never raises on a per-game failure; detect it from
            # the output it printed (e.g. the Cloudflare 403 page block).
            fail = _itchdl_failure_from_output("\n".join(_captured))
            if fail:
                return False, fail
            return True, f"Installed to {dest_dir}"
    except SystemExit as exc:
        # itch-dl internals call exit()/sys.exit() on fatal config problems.
        return False, f"itch-dl aborted: {exc}"
    except Exception as exc:
        return False, f"itch-dl failed: {exc}"
    finally:
        logging.raiseExceptions = prev_raise
        root.removeHandler(handler)


def _canonical_itch_url(url):
    """Normalise an itch.io URL for equality checks (drop scheme, trailing slash,
    case)."""
    if not url:
        return ""
    return re.sub(r"^https?://", "", url.strip().lower()).rstrip("/")


def _author_slug_from_url(url):
    """Return ``(author, game_slug)`` parsed from an itch.io URL such as
    ``https://mdf200.itch.io/cspect`` → ``("mdf200", "cspect")``. Mirrors the
    ``<author>/<game>`` layout itch-dl uses under the download dir, so the API
    download lands where ``installed_path`` / the emulator scan already look.
    Falls back to ``("itchio", <slug>)`` when the subdomain can't be parsed."""
    m = re.search(r"https?://([^./]+)\.itch\.io/([^/?#]+)", url or "")
    if m:
        return m.group(1), m.group(2)
    return "itchio", (_game_slug({"url": url}) or "item")


def _owned_key_for_game(game, api_key):
    """Locate *game* among the user's owned itch.io download keys and return
    ``(game_id, download_key_id)``, or ``(None, None)`` when it isn't owned.

    itch.io now fronts its game *pages* with a Cloudflare bot challenge (HTTP
    403), which is what breaks itch-dl, but the ``api.itch.io`` endpoints — incl.
    ``/profile/owned-keys`` — are unaffected. So for anything the user owns
    (bought, or claimed for free like CSpect) this yields the download key
    needed to fetch the uploads directly, no page scrape required."""
    want_url = _canonical_itch_url((game or {}).get("url"))
    want_id = str((game or {}).get("id") or "")
    for page in range(1, ITCH_MAX_PAGES + 1):
        data = _api_get("/profile/owned-keys", api_key, params={"page": page})
        keys = (data or {}).get("owned_keys") or []
        if not keys:
            break
        for k in keys:
            g = k.get("game") or {}
            if (want_id and str(g.get("id")) == want_id) or \
               (want_url and _canonical_itch_url(g.get("url")) == want_url):
                return g.get("id"), k.get("id")
        # owned-keys is paginated; stop once a short page is returned.
        if len(keys) < int(data.get("per_page") or len(keys) or 1):
            break
    return None, None


def _stream_response_to_file(resp, dest_path, log_cb=None, progress_cb=None):
    """Stream an open HTTP response body to *dest_path* (via a .part temp file
    that is renamed on completion). Returns the byte count.

    When *progress_cb* is given it is called as ``progress_cb(read, total)`` as
    the download proceeds (``total`` is the ``Content-Length`` in bytes, or 0 if
    the server didn't advertise one). Callbacks are throttled to roughly one per
    512 KB so the UI log isn't flooded, plus a final 100% call on completion."""
    tmp = dest_path + ".part"
    total = 0
    try:
        content_length = int(resp.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        content_length = 0
    last_emit = 0
    with open(tmp, "wb") as fh:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            fh.write(chunk)
            total += len(chunk)
            if progress_cb and (total - last_emit >= 524288):
                last_emit = total
                try:
                    progress_cb(total, content_length)
                except Exception:
                    pass
    os.replace(tmp, dest_path)
    if progress_cb:
        try:
            progress_cb(total, content_length or total)
        except Exception:
            pass
    if log_cb:
        try:
            log_cb(f"Downloaded {total} bytes → {os.path.basename(dest_path)}")
        except Exception:
            pass
    return total


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirect handler that refuses to auto-follow, so we can read the 302
    ``Location`` ourselves and fetch the signed CDN URL without re-sending the
    itch.io ``Authorization`` header (the CDN rejects it with HTTP 400)."""

    def redirect_request(self, *_args, **_kw):
        return None


def _download_api_upload(upload_id, download_key_id, api_key, dest_path,
                         log_cb=None, progress_cb=None):
    """Download one owned upload to *dest_path* via the itch.io API.

    ``/uploads/{id}/download`` 302-redirects to a signed CDN URL that rejects the
    ``Authorization`` header, so the redirect is resolved first (authenticated,
    not auto-followed) and the CDN URL then fetched with a clean request.
    *progress_cb* (see :func:`_stream_response_to_file`) is forwarded so callers
    can report download progress."""
    api_url = (ITCH_API_BASE + f"/uploads/{upload_id}/download"
               f"?download_key_id={download_key_id}")
    req = urllib.request.Request(api_url, headers={
        "Authorization": f"Bearer {api_key}",
        "User-Agent": ITCH_USER_AGENT,
    })
    opener = urllib.request.build_opener(_NoRedirect)
    cdn_url = None
    try:
        with opener.open(req, timeout=60) as resp:
            # No redirect: the API streamed the file itself.
            _stream_response_to_file(resp, dest_path, log_cb, progress_cb)
            return
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            cdn_url = exc.headers.get("Location")
        else:
            raise
    if not cdn_url:
        raise RuntimeError("itch.io did not return a download URL.")
    req2 = urllib.request.Request(cdn_url, headers={"User-Agent": ITCH_USER_AGENT})
    with urllib.request.urlopen(req2, timeout=300) as resp2:
        _stream_response_to_file(resp2, dest_path, log_cb, progress_cb)


def list_owned_uploads(game, api_key):
    """List the downloadable files (uploads) for an *owned* itch.io item.

    itch.io can offer several uploads for one game — CSpect in particular ships
    every build side by side (``CSpect3_1_4_0.zip``, ``CSpect3_1_3_0.zip``,
    ``CSpect3_0_15_2.zip`` …) — so a caller that wants to let the user pick a
    specific version needs the full list, not just the newest.

    Returns ``(game_id, key_id, uploads)`` where *uploads* is a list of dicts
    sorted **newest/highest-version first** so index 0 is the sensible default::

        {"id", "filename", "size", "version_name", "version_key"}

    or ``None`` when the item isn't among the user's owned keys / has no download
    key / lists no uploads (the caller then falls back to itch-dl). Raises on a
    network/API error. Runs synchronously — call from a worker thread."""
    game_id, key_id = _owned_key_for_game(game, api_key)
    if not game_id or not key_id:
        return None  # not owned / no download key — let the caller fall back
    data = _api_get(f"/games/{game_id}/uploads", api_key,
                    params={"download_key_id": key_id})
    raw = [u for u in ((data or {}).get("uploads") or []) if u.get("id")]
    if not raw:
        return None
    # Newest build first (CSpect3_1_4_0 > CSpect3_1_3_0 > CSpect3_0_15_2).
    raw.sort(key=lambda u: _version_sort_key(u.get("filename") or ""),
             reverse=True)
    uploads = []
    for u in raw:
        filename = u.get("filename") or f"upload-{u['id']}"
        uploads.append({
            "id": u["id"],
            "filename": filename,
            "size": u.get("size"),
            "version_name": os.path.splitext(os.path.basename(filename))[0],
            "version_key": cspect_version_key(filename),
        })
    return game_id, key_id, uploads


def download_owned_upload(game, api_key, dest_dir, upload, key_id,
                          log_cb=None, progress_cb=None):
    """Download one specific *upload* dict (from :func:`list_owned_uploads`) of an
    owned item straight from the itch.io API.

    The file is saved under ``<dest>/<author>/<game>/files/`` so the existing
    post-install extract step unpacks it into a build-numbered folder and the
    emulator scan can discover it. Returns ``(True, message)``. *progress_cb*
    (``progress_cb(read, total)``), when given, is forwarded to the byte stream
    so callers can show download progress. Runs synchronously — call from a
    worker thread."""
    def _log(line):
        if log_cb:
            try:
                log_cb(line)
            except Exception:
                pass

    filename = upload.get("filename") or f"upload-{upload['id']}"
    author, slug = _author_slug_from_url((game or {}).get("url"))
    files_dir = os.path.join(dest_dir, author, slug, "files")
    os.makedirs(files_dir, exist_ok=True)
    zip_path = os.path.join(files_dir, filename)

    _log(f"Fetching {filename} …")
    _download_api_upload(upload["id"], key_id, api_key, zip_path,
                         log_cb=log_cb, progress_cb=progress_cb)
    return True, f"Installed to {os.path.join(dest_dir, author, slug)}"


def install_via_api(game, api_key, dest_dir, log_cb=None, progress_cb=None,
                    upload=None, key_id=None):
    """Download an *owned* itch.io item straight from the itch.io API, bypassing
    the Cloudflare-blocked game-page scrape that itch-dl relies on.

    Returns ``(ok, message)`` when the item is owned (so the API path applies),
    or ``None`` when it isn't among the user's owned keys — signalling the caller
    to fall back to itch-dl. By default the newest upload (by version-sorted
    filename) is downloaded; pass *upload* (a dict from :func:`list_owned_uploads`
    — and, ideally, its *key_id*) to download a specific version the user chose
    instead. *progress_cb* (``progress_cb(read, total)``), when given, is
    forwarded to the byte stream so callers can show download progress."""
    def _log(line):
        if log_cb:
            try:
                log_cb(line)
            except Exception:
                pass

    # When the caller already picked an upload we still need a download key; only
    # re-fetch the listing when we have to choose the newest ourselves.
    if upload is None or not key_id:
        listed = list_owned_uploads(game, api_key)
        if listed is None:
            # No listing available. If a specific upload was requested but the
            # item isn't owned, surface that; otherwise let the caller fall back.
            if upload is not None:
                return False, "itch.io returned no download key for this item."
            return None
        _game_id, key_id, uploads = listed
        if upload is None:
            upload = uploads[0]  # newest/highest version

    _log("Downloading via the itch.io API (bypassing the Cloudflare page block)…")
    return download_owned_upload(game, api_key, dest_dir, upload, key_id,
                                 log_cb=log_cb, progress_cb=progress_cb)


# ── CSpect startup update check (itch.io) ──────────────────────────────────

def latest_cspect_upload(api_key, game=None):
    """Look up the newest CSpect build available to this itch.io account.

    Builds a minimal game dict from :data:`CSPECT_ITCH_URL` (unless *game* is
    supplied) and reuses :func:`list_owned_uploads` — CSpect exposes several
    builds at once, so this walks the full version list and reports the newest
    (index 0). Returns a dict describing the newest upload plus the whole list::

        {"game", "game_id", "key_id", "uploads",
         "filename", "version_name", "version_key"}

    or ``None`` when the account doesn't own CSpect / has no download key / the
    API lists no uploads (the caller then skips the update check quietly). Raises
    on a network/API error so the caller can log the reason. Runs synchronously —
    call from a worker thread."""
    game = game or {"url": CSPECT_ITCH_URL, "title": "CSpect"}
    listed = list_owned_uploads(game, api_key)
    if listed is None:
        return None  # not owned / no download key / no uploads
    game_id, key_id, uploads = listed
    newest = uploads[0]
    return {
        "game": game,
        "game_id": game_id,
        "key_id": key_id,
        "uploads": uploads,
        "filename": newest["filename"],
        "version_name": newest["version_name"],
        "version_key": newest["version_key"],
    }


def install_cspect_update(game, api_key, dest_dir, log_cb=None, progress_cb=None,
                          upload=None, key_id=None):
    """Download a specific owned CSpect build from itch.io and extract it.

    Reuses :func:`install_via_api`, then extracts the downloaded archive into its
    build-numbered folder (``files/CSpect3_1_4_0/``) so the emulator scan can
    discover it. CSpect exposes several builds at once; by default the newest is
    fetched, but the caller can pass the *upload* dict (and its *key_id*) that the
    update check already identified so exactly that build is downloaded — no
    second version lookup, and no risk of a mismatch with what the user was told.
    Returns the full path to the extracted build folder. Raises ``RuntimeError``
    with a human-readable reason on any failure so the caller can surface it in
    the log. Runs synchronously — call from a worker thread."""
    def _log(line):
        if log_cb:
            try:
                log_cb(line)
            except Exception:
                pass

    result = install_via_api(game, api_key, dest_dir,
                             log_cb=log_cb, progress_cb=progress_cb,
                             upload=upload, key_id=key_id)
    if result is None:
        raise RuntimeError(
            "this itch.io account does not own CSpect (no download key was "
            "found), so it can't be downloaded via the API.")
    ok, msg = result
    if not ok:
        raise RuntimeError(msg)

    install_dir = installed_path(game, dest_dir)
    if not install_dir:
        author, slug = _author_slug_from_url((game or {}).get("url"))
        install_dir = os.path.join(dest_dir, author, slug)
    # include_extracted=True: the archive we just downloaded is the newest one,
    # and re-extracting an already-present folder is a harmless overwrite.
    zips = find_extractable_zips(install_dir, include_extracted=True)
    if not zips:
        raise RuntimeError(
            f"the CSpect archive was downloaded but could not be located under "
            f"{install_dir} to extract.")
    newest = zips[0]
    _log(f"Extracting {os.path.basename(newest)} …")
    extracted = extract_zip(newest)
    return extracted


def manual_install_zip(game, zip_path, dest_dir):
    """Install a manually-downloaded itch.io ``.zip`` (the browser fallback used
    when the automated download is Cloudflare-blocked).

    Copies *zip_path* into the item's ``<dest>/<author>/<game>/files/`` folder —
    the same layout an API / itch-dl install uses, so ``installed_path`` and the
    emulator scan find it — then extracts it into a build-numbered subfolder
    (via :func:`extract_zip`). Returns the extracted directory path."""
    author, slug = _author_slug_from_url((game or {}).get("url"))
    files_dir = os.path.join(dest_dir, author, slug, "files")
    os.makedirs(files_dir, exist_ok=True)
    target = os.path.join(files_dir, os.path.basename(zip_path))
    if os.path.abspath(zip_path) != os.path.abspath(target):
        import shutil
        shutil.copy2(zip_path, target)
    return extract_zip(target)


def install_game(game, api_key, dest_dir, log_cb=None, timeout=3600):
    """Download/install a single game into *dest_dir*.

    Prefers the itch.io API (works for owned items, and bypasses the Cloudflare
    bot challenge that now blocks itch-dl's page scrape); falls back to itch-dl
    for items the user doesn't own. Returns ``(ok, message)``. *log_cb*, when
    given, receives streamed output lines so the UI can show progress. Runs
    synchronously — call from a worker thread."""
    api_key = (api_key or "").strip()
    game_url = (game or {}).get("url") or ""
    if not api_key:
        return False, "No API key configured."
    if not game_url:
        return False, "This item has no itch.io URL to install."
    os.makedirs(dest_dir, exist_ok=True)

    def _log(line):
        if log_cb:
            try:
                log_cb(line)
            except Exception:
                pass

    # 1) Preferred: the itch.io API. itch-dl scrapes the game page, which itch.io
    #    now Cloudflare-blocks (HTTP 403), but the API is unaffected — so this is
    #    what actually restores installs for owned items (e.g. CSpect).
    try:
        api_result = install_via_api(game, api_key, dest_dir, log_cb=_log)
    except Exception as exc:
        _log(f"itch.io API download failed: {exc}")
        api_result = (False, f"itch.io API download failed: {exc}")
    if api_result is not None:
        ok, msg = api_result
        if ok and not _download_produced_files(game, dest_dir):
            return False, ITCHIO_BLOCKED_MSG
        return ok, msg

    # 2) Not owned: fall back to itch-dl. In a PyInstaller-frozen build,
    #    sys.executable is the GUI exe (a "-m itch_dl" subprocess would relaunch
    #    the app), so itch-dl is driven in-process there; the source/venv build
    #    uses the isolated subprocess path.
    if getattr(sys, "frozen", False):
        _log("Running bundled itch-dl in-process (frozen build)…")
        ok, msg = _install_in_process(game_url, api_key, dest_dir, log_cb=_log)
    else:
        ok, msg = _run_itchdl_subprocess(game_url, api_key, dest_dir, _log, timeout)

    # Backstop: itch-dl can report success (exit 0 / no return) while itch.io
    # blocked the actual download, leaving an empty game folder. If nothing
    # landed on disk, override the phantom success with a clear message.
    if ok and not _download_produced_files(game, dest_dir):
        return False, ITCHIO_BLOCKED_MSG
    return ok, msg


def _run_itchdl_subprocess(game_url, api_key, dest_dir, _log, timeout):
    """Run itch-dl as a subprocess (source/venv build) and return ``(ok, msg)``.

    Tries ``python -m itch_dl`` first, then the ``itch-dl`` console script.
    Because itch-dl exits 0 even when a game download fails, the captured output
    is scanned for a failure report before success is declared."""
    attempts = [
        _itchdl_command(game_url, api_key, dest_dir),
        ["itch-dl", game_url, "--api-key", api_key, "--download-to", dest_dir],
    ]
    last_err = ""
    for cmd in attempts:
        _log(f"$ {' '.join(c if c != api_key else '***' for c in cmd)}")
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
        except FileNotFoundError as exc:
            last_err = str(exc)
            continue
        except subprocess.TimeoutExpired:
            return False, "itch-dl timed out."
        out = (proc.stdout or "") + (proc.stderr or "")
        for ln in out.splitlines():
            _log(ln)
        # itch-dl exits 0 even when a game fails to download (it just prints
        # "Download failed for …"), so check the output before trusting success.
        fail = _itchdl_failure_from_output(out)
        if fail:
            return False, fail
        if proc.returncode == 0:
            return True, f"Installed to {dest_dir}"
        # A non-zero exit is a real failure (not a missing executable): stop.
        return False, f"itch-dl exited with code {proc.returncode}"
    return False, f"Could not launch itch-dl: {last_err}"


def installed_path(game, dest_dir):
    """Best-effort path to where *game* appears to be downloaded under
    *dest_dir*. itch-dl lays files out per game/author, so we look for a
    directory whose name matches the game slug or title (case-insensitive).
    Returns the matched directory path, or None if not found."""
    if not dest_dir or not os.path.isdir(dest_dir):
        return None
    slug = _game_slug(game).lower()
    title = re.sub(r"[^a-z0-9]+", "-", ((game or {}).get("title") or "").lower()).strip("-")
    wanted = {s for s in (slug, title) if s}
    if not wanted:
        return None
    try:
        for root, dirs, _files in os.walk(dest_dir):
            # Limit recursion depth to keep this cheap.
            depth = root[len(dest_dir):].count(os.sep)
            if depth >= 3:
                dirs[:] = []
                continue
            for d in dirs:
                norm = re.sub(r"[^a-z0-9]+", "-", d.lower()).strip("-")
                if norm in wanted or any(w and w in norm for w in wanted):
                    return os.path.join(root, d)
    except Exception:
        return None
    return None


def installed_status(game, dest_dir):
    """True when *game* appears to be already downloaded under *dest_dir*."""
    return installed_path(game, dest_dir) is not None


def installed_dir_names(dest_dir):
    """Return the set of normalised directory names found under *dest_dir*
    (same depth-limited walk and normalisation as :func:`installed_path`).

    Computed once and matched against many games with :func:`entry_installed`,
    this lets a gallery flag every installed cell without re-walking the
    downloads tree per item."""
    names = set()
    if not dest_dir or not os.path.isdir(dest_dir):
        return names
    try:
        for root, dirs, _files in os.walk(dest_dir):
            depth = root[len(dest_dir):].count(os.sep)
            if depth >= 3:
                dirs[:] = []
                continue
            for d in dirs:
                names.add(re.sub(r"[^a-z0-9]+", "-", d.lower()).strip("-"))
    except Exception:
        return names
    return names


def entry_installed(game, dir_names):
    """True when *game* matches one of the pre-scanned *dir_names* (from
    :func:`installed_dir_names`). Mirrors :func:`installed_path`'s slug/title
    matching, but against a cached name set instead of a fresh disk walk."""
    if not dir_names:
        return False
    slug = _game_slug(game).lower()
    title = re.sub(r"[^a-z0-9]+", "-", ((game or {}).get("title") or "").lower()).strip("-")
    wanted = {s for s in (slug, title) if s}
    if not wanted:
        return False
    for norm in dir_names:
        if norm in wanted or any(w and w in norm for w in wanted):
            return True
    return False


# ── post-install zip extraction ────────────────────────────────────────────
#
# itch-dl lays a downloaded item's payload out under a ``files`` directory. Some
# ZX tools (CSpect in particular) ship their build as one or more .zip archives
# in there which still need extracting before use. After an install we look for
# such archives and extract each into a subfolder named after the archive (so
# ``files/CSpect3_1_4_0.zip`` → ``files/CSpect3_1_4_0/…``).

def _zip_target_dir(zip_path):
    """The folder a *zip_path* should be extracted into: a sibling directory
    named after the archive without its ``.zip`` suffix."""
    stem = os.path.splitext(os.path.basename(zip_path))[0]
    return os.path.join(os.path.dirname(zip_path), stem)


def _version_sort_key(zip_path):
    """Natural-order key for a zip path so versioned names sort numerically
    (``CSpect3_1_4_0`` > ``CSpect3_1_3_0`` > ``CSpect3_0_15_2``) rather than
    lexically. Digit runs compare as ints, other runs as lower-case text."""
    name = os.path.basename(zip_path)
    return [int(tok) if tok.isdigit() else tok.lower()
            for tok in re.split(r"(\d+)", name)]


def find_extractable_zips(install_dir, include_extracted=False):
    """Return a list of ``.zip`` paths found in any ``files`` subfolder under
    *install_dir*, sorted newest/highest version first (so the latest build is
    the default selection when several are offered).

    By default archives that have already been extracted (their target subfolder
    exists) are skipped, so callers can re-run this without re-prompting. Pass
    *include_extracted* to list every archive regardless."""
    zips = []
    if not install_dir or not os.path.isdir(install_dir):
        return zips
    for root, _dirs, files in os.walk(install_dir):
        if os.path.basename(root).lower() != "files":
            continue
        for f in files:
            if not f.lower().endswith(".zip"):
                continue
            zp = os.path.join(root, f)
            if include_extracted or not os.path.isdir(_zip_target_dir(zp)):
                zips.append(zp)
    try:
        return sorted(zips, key=_version_sort_key, reverse=True)
    except TypeError:
        # Mixed int/str tokens at the same position (unusual, heterogeneous
        # names) can't be compared; fall back to a plain descending sort.
        return sorted(zips, reverse=True)


def extract_zip(zip_path):
    """Extract *zip_path* into a subfolder named after the archive and return
    that folder's path. Raises on a bad/unreadable archive — callers run this on
    a worker thread and surface errors in the UI."""
    target = _zip_target_dir(zip_path)
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(target)
    return target
