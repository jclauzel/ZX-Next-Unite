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

from zxnu_config import ITCH_API_BASE, ITCH_USER_AGENT, ITCH_PAGE_SIZE, ITCH_MAX_PAGES


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

    def _log(line):
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
            return True, f"Installed to {dest_dir}"
    except SystemExit as exc:
        # itch-dl internals call exit()/sys.exit() on fatal config problems.
        return False, f"itch-dl aborted: {exc}"
    except Exception as exc:
        return False, f"itch-dl failed: {exc}"
    finally:
        logging.raiseExceptions = prev_raise
        root.removeHandler(handler)


def install_game(game, api_key, dest_dir, log_cb=None, timeout=3600):
    """Download/install a single game via itch-dl into *dest_dir*.

    Returns ``(ok, message)``. *log_cb*, when given, receives streamed output
    lines so the UI can show progress. Runs synchronously — call from a worker
    thread."""
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

    # In a PyInstaller-frozen build, sys.executable is the GUI executable rather
    # than a Python interpreter, so a "[sys.executable, -m, itch_dl, …]"
    # subprocess just relaunches the app instead of running itch-dl (the folder
    # is created but nothing is downloaded). Run itch-dl in-process there — it is
    # bundled into the exe and importable. The subprocess path below is kept for
    # the source/venv build, where it stays nicely isolated and already works.
    if getattr(sys, "frozen", False):
        _log("Running bundled itch-dl in-process (frozen build)…")
        return _install_in_process(game_url, api_key, dest_dir, log_cb=_log)

    # Try `python -m itch_dl` first, then the `itch-dl` console script.
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
        if proc.returncode == 0:
            return True, f"Installed to {dest_dir}"
        last_err = f"itch-dl exited with code {proc.returncode}"
        # A non-zero exit is a real failure (not a missing executable): stop.
        return False, last_err
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
