"""Auto-deploy of the .sync5 dot command into a loaded disk image (9.7.8).

Emulator users run NextZXOS from an ``.img``/``.hdf`` the app mounts through
hdfmonkey, and the Next-side half of NextSync — the ``.sync5`` dot command —
has to sit in that image's ``/dot`` folder before the Remote Explorer can
talk to the emulated machine. Copying it there by hand was the one step of
the emulator setup nobody could do from inside the app. Now, every time an
image is loaded on the SD Card tab (and Settings → "Auto update and deploy
.sync5 command in an .img file" is on, its default), the app looks into
``/dot``:

* no ``sync5`` there, or one whose embedded ``NextSync <version>`` banner is
  older than the dot this build ships (or carries no banner at all — a
  pre-banner build), AND the build is obtainable (a source checkout beside
  the app, a cached download, or the GitHub release of THIS app version
  reachable) → the user is OFFERED a download + deploy, yes/no, with a
  reminder that the Settings switch turns the offer off;
* the current or a newer dot → nothing, silently;
* a ``sync5`` hdfmonkey cannot read back → a log line, no offer (the dot may
  well be current; "no banner" would be a lie);
* an image an emulator holds, or one the app cannot write (the 9.6.2 write
  gate's BUSY / DENIED verdicts) → nothing but a log line: hdfmonkey writing
  into a mounted image corrupts it, into a read-only one fails.

Who asks: Wizzy, when the wizard is enabled — and the offer WAITS for the
wizard to be free (started, no bubble up: the first-run intro, the
starter-pack offer, a Quick Start mid-flight), polling for up to
SYNC5_IMG_WIZARD_WAIT_S before it gives up on the wizard; else a 15-second
yellow toast with a "Deploy now" button beside Cancel. Accepting re-probes
the image (an emulator started outside the app since the load), resolves
the binary OFF the UI thread through the same resolver the Remote
Explorer's dot update uses (source checkout → cached download → the release
asset, banner-verified), then ``mkdir /dot``, ``put`` the file as
``/dot/sync5`` (an existing copy is removed first should hdfmonkey ever
refuse to overwrite), reads it back and checks the banner before reporting
success — green toast + log — or failure (red, naming hdfmonkey's message,
which the tool prints on stdout). Declining is remembered for that image
for the session; an open offer is never doubled by a reload; a deployed
image reads as current next time.

The module is Qt-free: every side effect (hdfmonkey, the resolver, the
thread helper, the timer, the re-probe, the wizard, toasts, the log) is
injected, so tests/test_sync5_img_deploy.py drives the whole flow
headlessly.
"""
import json
import logging
import os
import tempfile
import urllib.request

from zxnu_config import (
    IMAGE_WRITE_BUSY, IMAGE_WRITE_DENIED, SETTING_SYNC5_IMG_AUTODEPLOY,
    ZX_NEXT_UNITE_DOTN_VERSION, ZX_NEXT_UNITE_VERSION, ZXART_USER_AGENT,
    ZXNU_DATA_ROOT, ZXNU_GITHUB_RELEASE_TAG_API, is_filetype_a_directory,
    sync5_blob_has_banner, sync5_blob_version, sync5_version_key,
)
from zxnu_i18n import ui_tr_now

#: Where the dot lives on a NextZXOS card, and its file name there.
SYNC5_IMG_DIR = "/dot"
SYNC5_IMG_FILE = "sync5"
SYNC5_IMG_PATH = SYNC5_IMG_DIR + "/" + SYNC5_IMG_FILE

#: How long the toast fallback stays up (the wizard bubble waits for a click).
SYNC5_IMG_TOAST_MS = 15000
#: An enabled wizard that is busy (not started yet, or a bubble up) is polled
#: this often, for at most this long, before the toast asks instead.
SYNC5_IMG_WIZARD_POLL_MS = 1000
SYNC5_IMG_WIZARD_WAIT_S = 60


def sync5_img_verdict(current_version, found, blob):
    """What the image holds, against the dot this build ships.

    ``found`` says whether ``/dot/sync5`` exists, ``blob`` its bytes — or
    None when it exists but could not be read back. Returns one of
    ``"missing"``, ``"unreadable"``, ``"older"``, ``"unknown"`` (a sync5
    with no parseable banner — a pre-banner build: offered like an older
    one), ``"current"`` or ``"newer"``. A newer dot is left alone: a
    developer trying a build ahead of the app must not be downgraded."""
    if not found:
        return "missing"
    if blob is None:
        return "unreadable"
    ver = sync5_blob_version(blob)
    if not ver:
        return "unknown"
    have, want = sync5_version_key(ver), sync5_version_key(current_version)
    if have < want:
        return "older"
    if have > want:
        return "newer"
    return "current"


def parse_ls(stdout):
    """hdfmonkey ``ls`` output → ``[(name, is_dir)]``: ``<type>\\t<name>``
    lines, the type ``[DIR]`` for folders and the bare size for files (the
    bundled 0.5.7 prints ``24576\\tsync5``; the SD Card explorer's parser
    also tolerates a ``[n bytes]`` spelling, and so does this)."""
    entries = []
    for line in (stdout or b"").splitlines():
        decoded = line.decode(errors="replace") if isinstance(line, bytes) else line
        parts = decoded.split("\t", 1)
        if len(parts) < 2:
            continue
        entries.append((parts[1].strip(), is_filetype_a_directory(parts[0])))
    return entries


def hdfmonkey_message(res):
    """hdfmonkey's own words for a failed call — it prints its errors on
    STDOUT (``Error opening file for writing: Path not found``), stderr
    only as a fallback."""
    for attr in ("stdout", "stderr"):
        raw = getattr(res, attr, b"") or b""
        if isinstance(raw, bytes):
            raw = raw.decode(errors="replace")
        raw = str(raw).strip()
        if raw:
            return raw
    return ""


def sync5_source_reachable(timeout=6.0):
    """Can the dot this build ships be obtained right now, WITHOUT
    downloading it: the source checkout beside the app wears the banner, a
    prior download does, or the GitHub release of this app version answers
    and lists the ``sync5`` asset. Returns ``(ok, reason)``; the reason is
    English (self-update advisories are documented untranslated). Network
    is touched only as the last resort, so an offline PC with a checkout
    never waits."""
    version = ZX_NEXT_UNITE_DOTN_VERSION
    for cand in (os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "nextsync", "sync", "server", "dot", "syncdev"),
                 os.path.join(ZXNU_DATA_ROOT, "downloads", "sync5", "sync5")):
        try:
            with open(cand, "rb") as fh:
                if sync5_blob_has_banner(fh.read(), version):
                    return True, ""
        except OSError:
            pass
    tag = f"v{ZX_NEXT_UNITE_VERSION}"
    try:
        req = urllib.request.Request(
            ZXNU_GITHUB_RELEASE_TAG_API.format(tag=tag),
            headers={"User-Agent": ZXART_USER_AGENT,
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            release = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:                       # offline, 404, …
        return False, f"the GitHub release {tag} could not be reached: {exc}"
    if any(isinstance(a, dict) and a.get("name") == "sync5"
           for a in (release.get("assets") or [])):
        return True, ""
    return False, f"the GitHub release {tag} carries no 'sync5' asset"


def build_sync5_img_ops(host, *, configuration_dictionary, execute_hdf_monkey,
                        resolve_sync5, run_in_thread, show_toast, add_log,
                        wizard=None, image_reload_dir=None, defer=None,
                        reprobe=None, source_reachable=sync5_source_reachable,
                        current_version=ZX_NEXT_UNITE_DOTN_VERSION):
    """Define ``host._sync5_img_check(path)`` and ``host._sync5_img_deploy``.

    Hooks: ``execute_hdf_monkey(cmd, image, extra_argv=[...], silent=True,
    prompt_if_missing=False)`` → a CompletedProcess; ``resolve_sync5()`` →
    ``(path, version, reason)``; ``run_in_thread(fn, on_result, on_error)``
    runs *fn* off the UI thread and marshals its return value (or the
    exception) back; ``show_toast(title, message, variant=, duration_ms=,
    action=)``; ``add_log(line)``; ``wizard()`` → the WizardManager or None;
    ``image_reload_dir(path)`` refreshes a folder in the SD Card explorer;
    ``defer(ms, fn)`` runs *fn* later on the UI thread (the wizard wait);
    ``reprobe(path)`` → True when the image is free RIGHT NOW (the 9.6.2
    gate's re-probe, run at Yes: the load-time verdict may be minutes old)."""
    host._sync5_img_declined = set()      # image keys the user said No to
    host._sync5_img_inflight = set()      # image keys being checked/deployed
    host._sync5_img_asked = {}            # image key -> "offer still open?" callable

    def _key(path):
        keyer = getattr(host, "_image_state_key", None)
        try:
            return keyer(path) if keyer else os.path.normcase(os.path.abspath(path))
        except Exception:                                  # noqa: BLE001
            return path

    def _enabled():
        raw = str(configuration_dictionary.get(SETTING_SYNC5_IMG_AUTODEPLOY, "") or "")
        return raw.strip().lower() not in ("false", "0", "no")

    def _write_state(path):
        return (getattr(host, "_image_write_state", None) or {}).get(_key(path))

    def _busy(path):
        # The 9.6.2 write gate's verdict (probed by load_image right before
        # this runs) plus the emulators this app launched itself — the only
        # in-use signal on Linux/macOS, where the probe sees nobody.
        if _write_state(path) == IMAGE_WRITE_BUSY:
            return True
        for proc in (getattr(host, "_images_held_by_us", None) or {}).get(_key(path), ()):
            try:
                if proc.poll() is None:
                    return True
            except Exception:                              # noqa: BLE001
                pass
        return False

    def _hdf(cmd, image, argv):
        return execute_hdf_monkey(cmd, image, extra_argv=argv, silent=True,
                                  prompt_if_missing=False)

    def _read_image_sync5(image):
        """``(found, blob)`` for the image's /dot/sync5 — off the UI thread.
        ``(True, None)`` when the file is there but could not be read."""
        res = _hdf("ls", image, [SYNC5_IMG_DIR])
        if getattr(res, "returncode", 1) != 0:
            return False, None                # no /dot at all
        found = any(not is_dir and name.lower() == SYNC5_IMG_FILE
                    for name, is_dir in parse_ls(getattr(res, "stdout", b"")))
        if not found:
            return False, None
        fd, tmp = tempfile.mkstemp(prefix="zxnu_sync5_", suffix=".bin")
        os.close(fd)
        try:
            res = _hdf("get", image, [SYNC5_IMG_PATH, tmp.replace("\\", "/")])
            if getattr(res, "returncode", 1) != 0:
                return True, None
            with open(tmp, "rb") as fh:
                return True, fh.read()
        except OSError:
            return True, None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ── the check, on image load ─────────────────────────────────────────
    def _sync5_img_check(image):
        image = (image or "").strip().strip('"')
        if not image or not _enabled():
            return
        key = _key(image)
        if key in host._sync5_img_declined or key in host._sync5_img_inflight:
            return
        still_open = host._sync5_img_asked.get(key)
        if still_open is not None:
            try:
                if still_open():
                    return                    # the offer is up: never double it
            except Exception:                              # noqa: BLE001
                pass
            host._sync5_img_asked.pop(key, None)   # it vanished unanswered: ask again
        if _busy(image):
            add_log(ui_tr_now(
                ".sync5 check skipped: {image} is in use by an emulator — "
                "close it and load the image again to deploy the dot.").format(
                    image=image))
            return
        if _write_state(image) == IMAGE_WRITE_DENIED:
            add_log(ui_tr_now(
                ".sync5 check skipped: {image} cannot be written (read-only, "
                "or out of this app's reach) — nothing can be deployed into "
                "it.").format(image=image))
            return
        host._sync5_img_inflight.add(key)

        def _probe():
            found, blob = _read_image_sync5(image)
            verdict = sync5_img_verdict(current_version, found, blob)
            reachable, reason = (True, "")
            if verdict in ("missing", "older", "unknown"):
                reachable, reason = source_reachable()
            return verdict, sync5_blob_version(blob or b""), reachable, reason

        def _on_result(payload):
            host._sync5_img_inflight.discard(key)
            verdict, old_ver, reachable, reason = payload
            if verdict in ("current", "newer"):
                return
            if verdict == "unreadable":
                add_log(ui_tr_now(
                    ".sync5 check: {image} has a {path} that hdfmonkey could "
                    "not read back — leaving it alone.").format(
                        image=image, path=SYNC5_IMG_PATH))
                return
            if not reachable:
                add_log(ui_tr_now(
                    ".sync5 check: {image} needs the dot ({state}) but the "
                    "build cannot be obtained right now — {reason}").format(
                        image=image, state=verdict, reason=reason))
                return
            _offer(image, key, verdict, old_ver, 0)

        def _on_error(payload):
            host._sync5_img_inflight.discard(key)
            logging.error(f"sync5 image check failed for {image}: {payload}")

        run_in_thread(_probe, _on_result, _on_error)

    def _offer(image, key, verdict, old_ver, waited):
        if key in host._sync5_img_declined:
            return                            # answered (or given up) meanwhile

        def _yes():
            host._sync5_img_asked.pop(key, None)
            _sync5_img_deploy(image, verdict)

        def _no():
            host._sync5_img_asked.pop(key, None)
            host._sync5_img_declined.add(key)

        wiz = wizard() if wizard is not None else None
        if wiz is not None and _wizard_enabled(wiz):
            if _wizard_free(wiz):
                try:
                    wiz.offer_sync5_deploy(verdict, old_ver, current_version,
                                           image, _yes, _no)
                    bubble = getattr(wiz, "bubble", None)
                    host._sync5_img_asked[key] = (
                        (lambda b=bubble: bool(b.isVisible()))
                        if bubble is not None else (lambda: False))
                    return
                except Exception:                          # noqa: BLE001
                    logging.exception("sync5 image offer: the wizard could not ask")
            elif defer is not None and waited * SYNC5_IMG_WIZARD_POLL_MS < SYNC5_IMG_WIZARD_WAIT_S * 1000:
                # Enabled but busy (not started yet — the first-run intro is
                # 2.2 s after launch — or a bubble up): Wizzy drives the
                # prompt, so WAIT for it rather than toast beside it. The
                # image counts as asked meanwhile, so a reload cannot start
                # a second question.
                host._sync5_img_asked[key] = lambda: True
                defer(SYNC5_IMG_WIZARD_POLL_MS,
                      lambda: _offer(image, key, verdict, old_ver, waited + 1))
                return
        # The toast: 15 s, yellow, "Deploy now" beside Cancel (= No).
        if verdict == "missing":
            body = ui_tr_now(
                "This disk image has no .sync5 command in /dot. Download "
                "the latest (v{new}) from GitHub and put it there? The "
                "NextSync Remote Explorer needs it on the emulated Next. "
                "(Switch this offer off in Settings → 'Auto update and "
                "deploy .sync5 command in an .img file'.)").format(
                    new=current_version)
        elif verdict == "older":
            body = ui_tr_now(
                "The .sync5 in this disk image's /dot is v{old}; this ZX "
                "Next Unite ships v{new}. Download it and replace the old "
                "one? (Switch this offer off in Settings → 'Auto update "
                "and deploy .sync5 command in an .img file'.)").format(
                    old=old_ver, new=current_version)
        else:
            body = ui_tr_now(
                "The .sync5 in this disk image's /dot carries no version "
                "banner (an old build). Download v{new} and replace it? "
                "(Switch this offer off in Settings → 'Auto update and "
                "deploy .sync5 command in an .img file'.)").format(
                    new=current_version)
        show_toast(".sync5 for this disk image", body, variant="yellow",
                   duration_ms=SYNC5_IMG_TOAST_MS, action=("Deploy now", _yes))
        # A toast that times out or is cancelled is a No for this session:
        # remembered here, since the toast reports neither.
        host._sync5_img_asked.pop(key, None)
        host._sync5_img_declined.add(key)

    def _wizard_enabled(wiz):
        try:
            return bool(wiz.enabled())
        except Exception:                                  # noqa: BLE001
            return False

    def _wizard_free(wiz):
        # Started (its deferred startup() ran — the intro would otherwise
        # replace our bubble) and not talking: never speak over an open
        # bubble (a Quick Start mid-flight, the starter-pack offer).
        try:
            if not getattr(wiz, "_started", False):
                return False
            bubble = getattr(wiz, "bubble", None)
            return not (bubble is not None and bubble.isVisible())
        except Exception:                                  # noqa: BLE001
            return False

    # ── the deploy, on Yes ───────────────────────────────────────────────
    def _sync5_img_deploy(image, verdict="missing"):
        key = _key(image)
        if key in host._sync5_img_inflight:
            return
        host._sync5_img_declined.discard(key)
        # The verdict the load recorded may be minutes old by the time Yes
        # is clicked: re-probe NOW (an emulator started outside the app has
        # mounted the image since), and re-gate the Launch surfaces with it.
        free = True
        if reprobe is not None:
            try:
                free = bool(reprobe(image))
            except Exception:                              # noqa: BLE001
                logging.exception("sync5 deploy: re-probe failed")
        if not free or _busy(image):
            show_toast(".sync5 for this disk image", ui_tr_now(
                "{image} is in use by an emulator — close it and load the "
                "image again to deploy the dot.").format(image=image),
                variant="red", duration_ms=SYNC5_IMG_TOAST_MS)
            return
        host._sync5_img_inflight.add(key)
        add_log(ui_tr_now(
            ".sync5 deploy: fetching v{new} and writing it to {image}{path}…"
            ).format(new=current_version, image=image, path=SYNC5_IMG_PATH))

        def _work():
            path, ver, reason = resolve_sync5()
            if not path:
                return False, reason
            _hdf("mkdir", image, [SYNC5_IMG_DIR])          # exists → refused, fine
            src = path.replace("\\", "/")
            res = _hdf("put", image, [src, SYNC5_IMG_PATH])
            if getattr(res, "returncode", 1) != 0:
                # Should hdfmonkey ever refuse to overwrite: clear the old
                # copy (the new bytes are already in hand, so the window
                # is one put) and try once more.
                _hdf("rm", image, [SYNC5_IMG_PATH])
                res = _hdf("put", image, [src, SYNC5_IMG_PATH])
                if getattr(res, "returncode", 1) != 0:
                    err = hdfmonkey_message(res)
                    return False, ("hdfmonkey could not write " + SYNC5_IMG_PATH
                                   + (": " + err if err else ""))
            found, blob = _read_image_sync5(image)
            if not found or not blob or not sync5_blob_has_banner(blob, ver):
                return False, ("the copy read back from the image does not "
                               f"carry the 'NextSync {ver}' banner")
            return True, ver

        def _on_result(payload):
            host._sync5_img_inflight.discard(key)
            ok, info = payload
            if ok:
                msg = ui_tr_now(
                    ".sync5 v{new} is now in {image}{path} — the emulated "
                    "Next can run '.sync5 -listen' for the Remote "
                    "Explorer.").format(new=info, image=image, path=SYNC5_IMG_PATH)
                add_log(msg)
                show_toast(".sync5 deployed", msg, variant="green",
                           duration_ms=SYNC5_IMG_TOAST_MS)
                if image_reload_dir is not None:
                    try:
                        image_reload_dir(SYNC5_IMG_DIR)
                    except Exception:                      # noqa: BLE001
                        logging.exception("sync5 deploy: /dot reload failed")
            else:
                msg = ui_tr_now(
                    ".sync5 deploy into {image} failed: {reason}").format(
                        image=image, reason=info)
                add_log(msg)
                show_toast(".sync5 deploy failed", msg, variant="red",
                           duration_ms=SYNC5_IMG_TOAST_MS)

        def _on_error(payload):
            host._sync5_img_inflight.discard(key)
            logging.error(f"sync5 deploy failed for {image}: {payload}")
            show_toast(".sync5 deploy failed", ui_tr_now(
                ".sync5 deploy into {image} failed: {reason}").format(
                    image=image, reason=str(payload[1] if isinstance(payload, tuple)
                                            and len(payload) > 1 else payload)),
                variant="red", duration_ms=SYNC5_IMG_TOAST_MS)

        run_in_thread(_work, _on_result, _on_error)

    host._sync5_img_check = _sync5_img_check
    host._sync5_img_deploy = _sync5_img_deploy
    return _sync5_img_check
