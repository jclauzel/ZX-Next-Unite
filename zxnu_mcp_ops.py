"""zxnu_mcp_ops.py — the app side of the MCP server (9.7.57).

``build_mcp_ops(host, …)`` gives MainWindow the MCP server's lifecycle
(``host._mcp_server_start`` / ``_mcp_server_stop`` /
``_mcp_server_restart_if_running``, driven by Settings → "Enable MCP server"
and by the startup restore) and the HOST ADAPTER the tools in zxnu_mcp call:
``_AppMcpHost``, one method per entry of the protocol listed in zxnu_mcp.py.

Every adapter method runs on an HTTP thread of the MCP server. What it may
touch from there is decided per method, and the rule is the one the rest of
the app follows:

* anything that reads or writes a Qt widget, calls an operation closure
  (``load_image``, ``launch_mame``, the listener start/stop, the Settings
  handlers) or creates a QObject goes through ``_UiInvoker.call`` — a
  queued signal carrying the job to the UI thread, the HTTP thread blocked
  on an Event until it returns (or a bounded timeout, so an agent waiting
  on a tool while a modal dialog holds the UI gets an answer rather than a
  hang);
* hdfmonkey runs INLINE on the HTTP thread with ``silent=True`` — exactly
  how the transfer workers call ``execute_hdf_monkey`` off the UI thread —
  under one lock, so two tool calls never write the same image at once;
* the Nexts seated on the ``-listen`` worker are driven through the SAME
  callables the HTTP bridge is built on (``host._re_bridge_callables``, the
  pane's enqueue / make_cmd / state / sessions / enqueue_to): a command
  carrying a ``BridgeReply`` is silent to the Remote Explorer pane and
  answers only through its reply, which is what makes an agent's traffic
  invisible to a user browsing the same Next.

Nothing here changes what the GUI does: the launchers, the image loader and
the servers are the existing closures, called with the arguments their
buttons pass, so an agent can do exactly what a user can and nothing more.
"""

import logging
import os
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot

from zxnu_config import *                                   # noqa: F401,F403
from zxnu_http_bridge import BridgeReply, flask_available
from zxnu_i18n import ui_tr_now
from zxnu_mcp import (LAUNCH_TIMEOUT, MCP_INSTRUCTIONS, MCP_PATH, McpHttpServer,
                      McpServer, McpToolError, build_mcp_tools)
from zxnu_sdcard_ops import (download_nextzxos_archive, extract_nextzxos_image,
                             nextzxos_image_url)
from zxnu_workers import emulator_launch_entries

UI_CALL_TIMEOUT = 30.0        # a plain widget read / a closure that returns at once
STOP_WAIT_S = 3.0             # how long emulator_stop waits for the tree to end


class _UiInvoker(QObject):
    """Run a callable on the UI thread from any thread.

    Created ON the UI thread (the builder runs in MainWindow.__init__), so
    its queued signal delivers the job there. ``call`` blocks the caller
    until the job returns and re-raises what it raised; ``post`` just
    schedules it. On the UI thread itself ``call`` runs the job inline — a
    queued round trip from the thread that drains the queue would wait on
    itself for ever."""

    request = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.request.connect(self._run, Qt.QueuedConnection)

    @Slot(object)
    def _run(self, job):
        try:
            job()
        except Exception:                                  # noqa: BLE001
            logging.exception("MCP: UI job failed")

    def call(self, fn, timeout=UI_CALL_TIMEOUT):
        if threading.current_thread() is threading.main_thread():
            return fn()
        done = threading.Event()
        box = {}

        def job():
            try:
                box["r"] = fn()
            except BaseException as ex:                    # noqa: BLE001
                box["e"] = ex
            finally:
                done.set()
        self.request.emit(job)
        if not done.wait(timeout):
            raise McpToolError(
                "the app's window did not answer within %d s (a dialog may be "
                "open on it - close it and retry)" % int(timeout))
        if "e" in box:
            raise box["e"]
        return box.get("r")

    def post(self, fn):
        self.request.emit(fn)


def _emulator_label(proc):
    """"MAME" / "CSpect" from a recorded Popen's own command line."""
    args = getattr(proc, "args", "")
    text = " ".join(args) if isinstance(args, (list, tuple)) else str(args)
    low = text.lower()
    if "cspect" in low:
        return "CSpect"
    if "mame" in low:
        return "MAME"
    return "emulator"


def build_mcp_ops(host, *, configuration_dictionary, save_configuration_file,
                  add_main_log_window, add_nextsync_log_window,
                  _right_disk_content):
    """Define the MCP server's lifecycle and host adapter on ``host``."""
    ui = _UiInvoker(host)
    host._mcp_server = None
    host._mcp_ui_invoker = ui          # keep the QObject alive

    def _log(line):
        # From an HTTP thread: the NextSync console is a QListWidget (UI
        # thread only), the file log is thread-safe.
        logging.info("%s", line)
        ui.post(lambda: add_nextsync_log_window(str(line)))

    # ------------------------------------------------------------------
    class _AppMcpHost:
        """The zxnu_mcp host protocol, over the running MainWindow."""

        def __init__(self):
            self._cmd_lock = threading.Lock()
            self._img_lock = threading.Lock()

        # ---- app ---------------------------------------------------
        def version(self):
            return ZX_NEXT_UNITE_VERSION

        def status(self):
            def on_ui():
                srv = host._mcp_server
                return {
                    "version": ZX_NEXT_UNITE_VERSION,
                    "data_root": ZXNU_DATA_ROOT,
                    "platform": sys.platform,
                    "image": self._image_status_ui(),
                    "tools": self._tools_ui(),
                    "emulators": self._emulators_ui(),
                    "processes": self._processes_ui(),
                    "esp_emulation": self._esp_status_ui(),
                    "nextsync": self._nextsync_status_ui(),
                    "mcp": {"url": srv.url if srv is not None else "",
                            "calls": srv.server.calls if srv is not None else 0,
                            "client": srv.server.last_client if srv is not None else ""},
                }
            return ui.call(on_ui)

        def log_tail(self, console, lines):
            n = max(1, int(lines))
            if console == "file":
                path = ""
                for h in logging.getLogger().handlers:
                    base = getattr(h, "baseFilename", "")
                    if base:
                        path = base
                        break
                if not path or not os.path.isfile(path):
                    return []
                with open(path, "rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    fh.seek(max(0, size - 256 * 1024))
                    text = fh.read().decode("utf-8", "replace")
                return text.splitlines()[-n:]

            def on_ui():
                widget = host.nextsync_log if console == "nextsync" else host.listWidgetLog
                count = widget.count()
                # Newest at index 0 (inserted at the top): answer oldest first.
                rows = [widget.item(i).text() for i in range(min(n, count) - 1, -1, -1)]
                return rows
            return ui.call(on_ui)

        # ---- disk image -------------------------------------------
        def _image_status_ui(self):
            path = host.right_disk_image_path or ""
            loaded = bool(_right_disk_content())
            d = {"path": path, "loaded": loaded,
                 "exists": bool(path) and os.path.isfile(path)}
            if d["exists"]:
                try:
                    d["bytes"] = os.path.getsize(path)
                except OSError:
                    pass
                usage = read_image_fat_usage(path)
                if usage is not None:
                    total = usage.total_clusters * usage.cluster_bytes
                    free = usage.free_clusters * usage.cluster_bytes
                    d["usage"] = {"fat": "FAT%d" % usage.fat_bits,
                                  "total_bytes": total, "free_bytes": free,
                                  "used_percent": round(100.0 * (total - free) / total, 1)
                                  if total else None}
                blocker = getattr(host, "_image_write_blocker", None)
                d["write_blocked"] = blocker(path) if blocker is not None else ""
                key = host._image_state_key(path)
                d["write_state"] = (getattr(host, "_image_write_state", {}) or {}).get(key, "")
            return d

        def image_status(self):
            return ui.call(self._image_status_ui)

        def image_path(self):
            return host.right_disk_image_path or ""

        def image_load(self, path, timeout):
            done = threading.Event()
            box = {}

            def on_ui():
                if path:
                    clean = normalize_sd_image_path(path)
                    host.imageinput.setCurrentText(clean)
                    configuration_dictionary[SETTING_HDDFILE] = clean
                else:
                    host.imageinput.blockSignals(True)
                    host.imageinput.setCurrentText("")
                    host.imageinput.blockSignals(False)
                    configuration_dictionary[SETTING_HDDFILE] = ""
                    save_configuration_file()

                def _cb(success):
                    box["ok"] = bool(success)
                    done.set()
                host.load_image(_cb)
            ui.call(on_ui)
            if not done.wait(timeout):
                return False, "timed out waiting for hdfmonkey to list the image"
            if not path:
                return True, "image unloaded"
            if box.get("ok"):
                return True, "loaded"
            return False, ("hdfmonkey could not list the image (see the SD Card "
                           "console: app_log_tail console=sdcard)")

        def image_recent(self):
            def on_ui():
                combo = host.imageinput
                state = getattr(host, "_image_write_state", {}) or {}
                out = []
                for i in range(combo.count()):
                    p = combo.itemText(i)
                    if not p:
                        continue
                    out.append({"path": p, "exists": os.path.isfile(p),
                                "write_state": state.get(host._image_state_key(p), ""),
                                "loaded": p == (host.right_disk_image_path or "")
                                and bool(_right_disk_content())})
                return out
            return ui.call(on_ui)

        def image_download(self, size, dest_dir, load):
            url = nextzxos_image_url(size)
            if not url:
                raise McpToolError(f"unknown image size {size!r}")
            dest = dest_dir or os.path.join(ZXNU_DATA_ROOT, "downloads", "nextzxos")
            os.makedirs(dest, exist_ok=True)
            save_path = os.path.join(dest, os.path.basename(url.rsplit("/", 1)[-1]))
            ui.post(lambda: add_main_log_window(ui_tr_now(
                "Downloading {name} from {url}").format(name=size, url=url)))
            try:
                download_nextzxos_archive(url, save_path)
            except Exception as ex:                        # noqa: BLE001
                logging.error("MCP: NextZXOS download failed: %s", ex)
                raise McpToolError(f"download failed: {ex}") from ex
            blocker = getattr(host, "_image_write_blocker", None)
            try:
                image = extract_nextzxos_image(save_path, refuse=blocker)
            except Exception as ex:                        # noqa: BLE001
                logging.error("MCP: NextZXOS extraction failed: %s", ex)
                raise McpToolError(f"downloaded to {save_path} but could not extract: {ex}") from ex
            out = {"ok": True, "zip": save_path, "image": image, "loaded": False}
            ui.post(lambda: add_main_log_window(ui_tr_now(
                "Extracted disk image: {path}").format(path=image)))
            if load and image.lower().endswith((".img", ".hdf")):
                ok, msg = self.image_load(image, 180.0)
                out["loaded"] = ok
                out["message"] = msg
            return out

        def hdfmonkey(self, verb, argv):
            image = host.right_disk_image_path or ""
            if not image or not _right_disk_content():
                raise McpToolError("no disk image is loaded: image_load (or "
                                   "image_download_nextzxos) first")
            with self._img_lock:
                res = host.execute_hdf_monkey(verb, image, extra_argv=list(argv),
                                              silent=True, prompt_if_missing=False)
            if res.returncode == -1 and res.stdout is None and res.stderr is None:
                raise McpToolError("hdfmonkey is not installed (SD Card tab -> "
                                   "'Download and install HDF Monkey')")
            return res.returncode, res.stdout, res.stderr

        def image_refresh(self, path):
            def on_ui():
                try:
                    host.image_reload_dir(path or "/")
                except Exception:                          # noqa: BLE001
                    logging.exception("MCP: image tree refresh failed")
                gauge = getattr(host, "_update_image_usage_gauge", None)
                if gauge is not None:
                    gauge(None)
            ui.post(on_ui)

        # ---- emulators --------------------------------------------
        def _tools_ui(self):
            hdf = getattr(host, "_hdfmonkey_executable_path", None)
            found = getattr(host, "_hdfmonkey_binary_found", None)
            return {"hdfmonkey": hdf or ("hdfmonkey" if (found and found()) else ""),
                    "cspect": getattr(host, "_cspect_executable_path", None) or "",
                    "cspect_mcp": {"enabled_in_settings": cspect_mcp_enabled(configuration_dictionary),
                                   "port": cspect_mcp_port(configuration_dictionary)},
                    "mame": getattr(host, "_mame_executable_path", None) or "",
                    "mame_flatpak": bool(host._mame_flatpak_enabled())
                    if hasattr(host, "_mame_flatpak_enabled") else False}

        def _emulators_ui(self):
            rows = {}
            for e in emulator_launch_entries(host):
                name = "MAME" if e.name.lower().startswith("mame") else e.name
                rows[name] = {"name": name, "installed": True,
                              "blocked": e.blocked or ""}
            for name in ("MAME", "CSpect"):
                rows.setdefault(name, {"name": name, "installed": False,
                                       "blocked": "not installed"})
            return [rows["MAME"], rows["CSpect"]]

        def emulators(self):
            return ui.call(self._emulators_ui)

        def launch(self, emulator, file, card_path, cspect_mcp=None,
                   cspect_mcp_port=None):
            def on_ui():
                host._last_launch_failure = None
                if emulator == "MAME":
                    fn = getattr(host, "_launch_mame_fn", None)
                    if fn is None:
                        return False, "MAME is not available", None, {}
                    proc = fn(file, card_path=card_path) if file else fn()
                elif emulator == "CSpect":
                    fn = getattr(host, "_launch_cspect_fn", None)
                    if fn is None:
                        return False, "CSpect is not available", None, {}
                    kw = {"mcp": cspect_mcp, "mcp_port": cspect_mcp_port}
                    proc = fn(file, **kw) if file else fn(**kw)
                else:
                    return False, f"unknown emulator {emulator!r}", None, {}
                if proc is None:
                    why = getattr(host, "_last_launch_failure", None)
                    return False, (why[1] if why else f"{emulator} did not start"), None, {}
                extra = {}
                port = int(getattr(proc, "zxnu_mcp_port", 0) or 0)
                if port:
                    extra["cspect_mcp_port"] = port
                    extra["cspect_mcp_note"] = ("CSpect was started with -mcp=%d: its own "
                                                "MCP server (CSpect 4.2.0+, see its "
                                                "ReadMe.txt) listens there" % port)
                return True, f"{emulator} started (pid {proc.pid})", proc.pid, extra
            return ui.call(on_ui, LAUNCH_TIMEOUT)

        def _processes_ui(self):
            out = []
            held = getattr(host, "_images_held_by_us", {}) or {}
            for key, procs in list(held.items()):
                for p in list(procs):
                    if p.poll() is None:
                        row = {"pid": p.pid, "emulator": _emulator_label(p),
                               "image": key}
                        port = int(getattr(p, "zxnu_mcp_port", 0) or 0)
                        if port:
                            row["cspect_mcp_port"] = port
                        out.append(row)
            return out

        def processes(self):
            return ui.call(self._processes_ui)

        def stop_process(self, pid):
            def on_ui():
                held = getattr(host, "_images_held_by_us", {}) or {}
                for procs in held.values():
                    for p in procs:
                        if p.pid == pid and p.poll() is None:
                            return p
                return None
            proc = ui.call(on_ui)
            if proc is None:
                return False, f"no running emulator started from the app has pid {pid}"
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   capture_output=True, timeout=15)
                else:
                    subprocess.run(["pkill", "-TERM", "-P", str(pid)],
                                   capture_output=True, timeout=15)
                    proc.terminate()
                proc.wait(timeout=STOP_WAIT_S)
            except subprocess.TimeoutExpired:
                return True, f"pid {pid} was told to stop but is still exiting"
            except Exception as ex:                        # noqa: BLE001
                return False, f"could not stop pid {pid}: {ex}"
            return True, f"pid {pid} stopped"

        # ---- RS232 ESP emulation --------------------------------------
        def _esp_status_ui(self):
            server = getattr(host, "_esp_emu", None)
            running = bool(server is not None and server.running)
            try:
                cfg_port = int(configuration_dictionary.get(SETTING_MAME_RS232_ESP_PORT) or 2222)
            except (TypeError, ValueError):
                cfg_port = 2222
            d = {"running": running,
                 "port": server.port if running else cfg_port,
                 "sessions": server.session_count if running else 0,
                 "station_ip": (server.station_ip or server.resolve_station_ip())
                 if running else esp_station_ip_setting(
                     configuration_dictionary.get(SETTING_MAME_RS232_ESP_IP, "")),
                 "verbose": server.verbose if running else str(configuration_dictionary.get(
                     SETTING_MAME_RS232_ESP_VERBOSE, "")).strip().lower() in ("true", "1", "yes", "on"),
                 "mame_users": int(getattr(host, "_esp_emu_users", 0) or 0),
                 "enabled_in_settings": str(configuration_dictionary.get(
                     SETTING_MAME_RS232_ESP, "")).strip().lower() in ("true", "1", "yes", "on")}
            return d

        def esp_status(self):
            return ui.call(self._esp_status_ui)

        def esp_start(self, port, verbose):
            def on_ui():
                starter = getattr(host, "_start_esp_emulation", None)
                if starter is None:
                    return False, "the RS232 ESP emulation is not available in this build", None
                cb = getattr(host, "settings_rs232_esp_checkbox", None)
                if cb is not None and not cb.isChecked():
                    cb.setChecked(True)          # persists; MAME launches wire to it
                if port is not None:
                    spin = getattr(host, "settings_rs232_esp_port_spinbox", None)
                    if spin is not None and spin.value() != int(port):
                        spin.setValue(int(port))   # persists through its handler
                    else:
                        configuration_dictionary[SETTING_MAME_RS232_ESP_PORT] = str(int(port))
                if verbose is not None:
                    vb = getattr(host, "settings_rs232_esp_verbose_checkbox", None)
                    if vb is not None and vb.isChecked() != bool(verbose):
                        vb.setChecked(bool(verbose))
                host._last_launch_failure = None
                server = starter()
                if server is None:
                    why = getattr(host, "_last_launch_failure", None)
                    return False, (why[1] if why else "the RS232 ESP emulation did not start"), None
                note = ""
                if port is not None and server.port != int(port):
                    note = (" (a running MAME holds the worker on port %d; port %d "
                            "applies once every MAME has exited)" % (server.port, int(port)))
                return True, "RS232 ESP emulation running on port %d%s" % (server.port, note), server.port
            return ui.call(on_ui)

        def esp_stop(self, force):
            def on_ui():
                server = getattr(host, "_esp_emu", None)
                if server is None or not server.running:
                    return True, "the RS232 ESP emulation is not running"
                users = int(getattr(host, "_esp_emu_users", 0) or 0)
                if users > 0 and not force:
                    return False, ("%d MAME(s) started from the app still use it; "
                                   "stop them (emulator_stop) or pass force=true" % users)
                host._stop_esp_emulation()
                return True, "RS232 ESP emulation stopped"
            return ui.call(on_ui)

        # ---- NextSync -------------------------------------------------
        def _nextsync_status_ui(self):
            parts = getattr(host, "_re_bridge_callables", None)
            st = parts["state"]() if parts else {}
            roster = self.next_sessions()
            active, rows, maxp = roster if roster else (None, [], 0)
            t = getattr(host, "_nextsync_thread", None)
            bridge = getattr(host, "_re_bridge", None)
            return {
                "listener": {"running": bool(getattr(host, "_re_running", False)),
                             "port": PORT,
                             "sync_root": getattr(host, "_re_sync_root", "") or "",
                             "connected": bool(st.get("connected")),
                             "current_drive": st.get("current") or "",
                             "drives": list(st.get("drives") or []),
                             "active": active, "max_sessions": maxp,
                             "sessions": [{"sid": s, "addr": a, "name": n,
                                           "active": s == active} for s, a, n in rows]},
                "classic": {"running": bool(t is not None and t.is_alive()),
                            "sync_root": configuration_dictionary.get(
                                SETTING_NEXTSYNC_EXPLORERPATH, "") or ""},
                "http_bridge": {"running": bool(bridge is not None and bridge.running),
                                "port": bridge.port if bridge is not None and bridge.running
                                else int(configuration_dictionary.get(SETTING_NEXTSYNC_HTTP_PORT) or 80),
                                "flask_installed": flask_available()},
            }

        def nextsync_status(self):
            return ui.call(self._nextsync_status_ui)

        def set_sync_root(self, path):
            def on_ui():
                norm = (path or "").replace("\\", "/").rstrip("/")
                if not norm or not os.path.isdir(norm):
                    return False, f"not a folder: {path}"
                widget = getattr(host, "_re_widget", None)
                if widget is not None:
                    widget._commit_sync_root(norm)     # notifies the host, persists
                else:
                    configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] = norm
                    host._re_sync_root = norm
                    save_configuration_file()
                return True, f"sync root set to {norm}"
            return ui.call(on_ui)

        def listener_start(self, sync_root):
            def on_ui():
                if getattr(host, "_re_running", False):
                    return True, "the listener is already running"
                if not getattr(host, "_re_sync_root", ""):
                    root = configuration_dictionary.get(SETTING_NEXTSYNC_EXPLORERPATH, "") or ""
                    if root and os.path.isdir(root):
                        host._re_sync_root = root.replace("\\", "/").rstrip("/")
                if not getattr(host, "_re_sync_root", ""):
                    return False, ("no sync root is set: pass sync_root=<local folder> "
                                   "(or nextsync_set_sync_root)")
                t = getattr(host, "_nextsync_thread", None)
                if t is not None and t.is_alive():
                    return False, "the classic NextSync server is running: nextsync_classic_stop first"
                tabs = getattr(host, "nextsync_mode_tabs", None)
                if tabs is not None and tabs.currentIndex() != TRANSFER_SUBTAB_REMOTE:
                    # This run only, like -start-remote-explorer-listener: the
                    # experience choice and the saved sub-tab stay as they were.
                    host._re_open_restoring = True
                    host._transfer_subtab_restoring = True
                    try:
                        tabs.setCurrentIndex(TRANSFER_SUBTAB_REMOTE)
                    finally:
                        host._re_open_restoring = False
                        host._transfer_subtab_restoring = False
                host._nextsync_re_toggle_server()
                if getattr(host, "_re_running", False):
                    return True, "listening on port %d; run `.sync5 -L` (or ZX Next Remote's Listener) on the Next" % PORT
                return False, "the listener did not start (see app_log_tail console=nextsync)"
            return ui.call(on_ui)

        def listener_stop(self):
            def on_ui():
                if not getattr(host, "_re_running", False):
                    return True, "the listener was not running"
                host._nextsync_stop_listen_server_fn()
                return True, "listener stopped"
            return ui.call(on_ui, 30.0)

        def classic_start(self, mode):
            def on_ui():
                if getattr(host, "_re_running", False):
                    return False, "the Remote Explorer listener is running: nextsync_listener_stop first"
                t = getattr(host, "_nextsync_thread", None)
                if t is not None and t.is_alive():
                    return False, "the classic NextSync server is already running"
                root = configuration_dictionary.get(SETTING_NEXTSYNC_EXPLORERPATH, "") or ""
                if not root or not os.path.isdir(root):
                    return False, "no sync root is set: nextsync_set_sync_root first"
                if mode == "always":
                    host.nextsync_alwayssync_checkbox.setChecked(True)
                else:
                    host.nextsync_synconce_checkbox.setChecked(True)
                # The start opens a MODAL progress dialog and blocks until the
                # sync ends: posted, so this tool answers at once.
                ui.post(host._nextsync_start_server_fn)
                return True, ("classic NextSync server starting on port %d (%s); run "
                              "`.sync5` on the Next" % (PORT, mode))
            return ui.call(on_ui)

        def classic_stop(self):
            def on_ui():
                t = getattr(host, "_nextsync_thread", None)
                flag = getattr(host, "_nextsync_cancel_flag", None)
                if t is None or not t.is_alive() or flag is None:
                    return True, "the classic NextSync server was not running"
                flag.set()
                return True, "stop requested; the current file finishes first"
            return ui.call(on_ui)

        def bridge_start(self, port):
            def on_ui():
                if not flask_available():
                    return False, "the HTTP bridge needs the optional 'flask' package"
                b = getattr(host, "_re_bridge", None)
                if b is not None and b.running:
                    return True, "the HTTP bridge is already running on port %d" % b.port
                if port is not None:
                    spin = getattr(host, "settings_http_port_spinbox", None)
                    if spin is not None:
                        spin.blockSignals(True)
                        spin.setValue(int(port))
                        spin.blockSignals(False)
                    configuration_dictionary[SETTING_NEXTSYNC_HTTP_PORT] = str(int(port))
                    save_configuration_file()
                host._nextsync_http_bridge_start()
                b = getattr(host, "_re_bridge", None)
                if b is not None and b.running:
                    return True, "HTTP bridge listening on port %d" % b.port
                return False, "the HTTP bridge did not start (see app_log_tail console=nextsync)"
            return ui.call(on_ui)

        def bridge_stop(self):
            def on_ui():
                b = getattr(host, "_re_bridge", None)
                if b is None or not b.running:
                    return True, "the HTTP bridge was not running"
                host._nextsync_http_bridge_stop()
                return True, "HTTP bridge stopped"
            return ui.call(on_ui)

        # ---- the seated Nexts ------------------------------------------
        def next_sessions(self):
            parts = getattr(host, "_re_bridge_callables", None)
            if not parts:
                return None
            try:
                active, rows, maxp = parts["sessions"]()
            except Exception:                              # noqa: BLE001
                logging.exception("MCP: roster read failed")
                return None
            return (active, [(int(s), str(a), str(n or "")) for s, a, n in rows], int(maxp))

        def next_cmd(self, op, a1, a2, session, timeout):
            parts = getattr(host, "_re_bridge_callables", None)
            if not parts or not getattr(host, "_re_running", False):
                raise McpToolError("the NextSync listener is not running: "
                                   "nextsync_listener_start first")
            st = parts["state"]()
            if not st.get("connected"):
                raise McpToolError("no Next is seated on the listener: on the Next run "
                                   "`.sync5 -L` (or ZX Next Remote's Listener) pointed at "
                                   "this PC, then retry")
            reply = BridgeReply()
            cmd = parts["make_cmd"](op, a1, a2, reply)
            if cmd is None:
                raise McpToolError(f"'{op}' is not supported by this server")
            with self._cmd_lock:
                if session is not None:
                    if not parts["enqueue_to"](int(session), cmd):
                        raise McpToolError(f"session {session} is gone - next_sessions "
                                           "for the live list")
                elif not parts["enqueue"](cmd):
                    raise McpToolError("the listener stopped")
                res = reply.wait(timeout)
            if res is None:
                raise McpToolError("timed out after %d s waiting for the Next to answer %s"
                                   % (int(timeout), op))
            return res

        def next_select(self, sid):
            parts = getattr(host, "_re_bridge_callables", None)
            if not parts or not getattr(host, "_re_running", False):
                return False, "the NextSync listener is not running"
            roster = self.next_sessions()
            sids = [s for s, _a, _n in (roster[1] if roster else [])]
            if sid not in sids:
                return False, f"no seated Next has sid {sid} (seated: {sids})"
            if not parts["enqueue"](("select_next", sid)):
                return False, "the listener stopped"
            return True, f"session {sid} is now the active one"

    adapter = _AppMcpHost()
    host._mcp_host_adapter = adapter

    # ------------------------------------------------------------------
    def _mcp_port():
        return mcp_server_port(configuration_dictionary)

    def _mcp_token():
        if not mcp_token_enabled(configuration_dictionary):
            return None
        return (configuration_dictionary.get(SETTING_MCP_TOKEN) or "").strip() or None

    def _mcp_connect_hint():
        """The one-line client setup for the CURRENT settings (shown in
        Settings, logged at start): Claude Code's `claude mcp add`."""
        srv = host._mcp_server
        url = srv.url if srv is not None and srv.running else \
            "http://127.0.0.1:%d%s" % (_mcp_port(), MCP_PATH)
        cmd = "claude mcp add --transport http zx-next-unite " + url
        tok = _mcp_token()
        if tok:
            cmd += ' --header "Authorization: Bearer %s"' % tok
        return cmd
    host._mcp_connect_hint = _mcp_connect_hint

    def _refresh_hint():
        fn = getattr(host, "_mcp_refresh_hint", None)
        if fn is not None:
            try:
                fn()
            except Exception:                              # noqa: BLE001
                logging.exception("MCP: hint refresh failed")

    def _mcp_server_start():
        if host._mcp_server is not None and host._mcp_server.running:
            return
        port = _mcp_port()
        token = _mcp_token()
        mcp = McpServer(build_mcp_tools(adapter), ZX_NEXT_UNITE_VERSION,
                        instructions=MCP_INSTRUCTIONS, log=_log)
        srv = McpHttpServer(mcp, port=port, auth_token=token, log=_log)
        try:
            srv.start()
        except OSError as ex:
            host._mcp_server = None
            logging.error("MCP server failed to start: %s", ex)
            add_nextsync_log_window(ui_tr_now(
                "MCP server NOT started: {error}").format(error=ex))
            if srv.port_in_use:
                host._show_toast(
                    "MCP server not started",
                    ui_tr_now("Port {port} is already in use; the MCP server has "
                              "not been started.").format(port=port),
                    variant="red", duration_ms=12000)
            else:
                host._show_toast("MCP server not started", str(ex),
                                 variant="yellow", duration_ms=12000)
            _refresh_hint()
            return
        host._mcp_server = srv
        add_nextsync_log_window(ui_tr_now(
            "MCP server listening on {url}").format(url=srv.url))
        if token:
            add_nextsync_log_window(ui_tr_now(
                "MCP server: bearer-token protection is ON (requests must carry "
                "Authorization: Bearer <token>; others get HTTP 401)"))
        add_nextsync_log_window("MCP: " + _mcp_connect_hint())
        host._show_toast(
            "MCP server started",
            ui_tr_now("Serving on {url}. An AI agent (Claude Code, Claude Desktop...) "
                      "can now drive ZX Next Unite: connect it with the command "
                      "shown in Settings.").format(url=srv.url),
            variant="green", duration_ms=6000)
        _refresh_hint()

    def _mcp_server_stop():
        srv, host._mcp_server = host._mcp_server, None
        if srv is not None:
            srv.stop()
            add_nextsync_log_window(ui_tr_now("MCP server stopped."))
        _refresh_hint()

    def _mcp_server_restart_if_running():
        if host._mcp_server is not None and host._mcp_server.running:
            _mcp_server_stop()
            _mcp_server_start()
        else:
            _refresh_hint()

    def _mcp_status():
        srv = host._mcp_server
        return {"running": bool(srv is not None and srv.running),
                "url": srv.url if srv is not None else "",
                "port": srv.port if srv is not None else _mcp_port(),
                "token_required": bool(_mcp_token())}

    host._mcp_server_start = _mcp_server_start
    host._mcp_server_stop = _mcp_server_stop
    host._mcp_server_restart_if_running = _mcp_server_restart_if_running
    host._mcp_status = _mcp_status
    # The Settings hint line shows the connect command from the first frame,
    # server on or off (a first run has no cfg, so the restore's own refresh
    # never happens there).
    _refresh_hint()
