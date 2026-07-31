"""zxnu_emulator_ops.py — emulator + self-update operation layer.

Strangler extraction from MainWindow.__init__ (same builder-function seam as
the pane modules, but pure ops — no widgets are created here):
build_emulator_ops(host, ...) defines and wires

* the CSpect / MAME settings-combo setter closures,
* open_cspect_configuration_file, launch_cspect, launch_mame,
* the MAME install + update-check chain (GitHub latest-release asset
  selection, background download/extract job, progress dialog, rescan),
* the zx-next-unite self-update chain (release fetch, asset pick, download
  next to the RUNNING executable — the one deliberate argv-anchored path —
  and the restart offer),
* the .sync dotN version advisory,
* the CSpect update chain (itch.io API probe + install into downloads/), and
* _wire_viewer_emulators (the gallery item-viewer Launch buttons).

The block is definition-only (no construction-time side effects), so the
builder is safe to call at the block's historical position. Everything it
assigned to ``self`` is written to ``host``; the closures the rest of
__init__ wires to widgets by bare name are additionally exposed as host
attributes and re-bound to bare locals at the call site. Helpers defined
later in __init__ (_start_load_image_hint_animation, add_main_log_window,
execute_shell_command) arrive as forwarding lambdas; the module-global
``right_disk_image_explorer_content`` is read via the ``_right_disk_content``
getter hook. See CLAUDE.md and the memory ``strangler-extraction-pattern``.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox

from zxnu_config import *
from zxnu_i18n import ui_tr_now
from zxnu_workers import *
import zxnu_itchio


# ── MAME self-extractor watchdog ─────────────────────────────────────────
# MAME's official Windows package is a *GUI* 7-Zip self-extractor (the same
# SFX stub in every release — 0288 and 0289 are byte-identical there). Given
# "-o<dir> -y" it unpacks silently and exits 0 all by itself, which is why
# driving it as a plain subprocess worked for years. It has one failure mode
# though: when a file cannot be written — nearly always because MAME itself
# is running, so mame.exe is locked — it reports the error in its progress
# dialog and waits for a click on Close. The app launches it hidden
# (CREATE_NO_WINDOW/SW_HIDE), so that click can never happen: the extraction
# blocks forever, the install neither succeeds nor fails, and the orphaned
# extractor keeps its own .exe locked, which is what made the *next* attempt
# die with "[Errno 13] Permission denied" while re-downloading it.
#
# Hence: refuse to start when the target binary is locked, and never wait on
# the extractor without a deadline. The stall window is what actually fires
# (a real extraction writes continuously — ~5 s for the whole 574 MB here);
# the absolute cap is only a backstop for a pathologically slow disk.
MAME_SFX_STALL_SECONDS = 120
MAME_SFX_TIMEOUT_SECONDS = 3600


def _mame_dest_fingerprint(root):
    """(file count, total bytes) below *root* — the cheap progress signal that
    tells an extractor still writing files apart from one parked on a dialog.
    Entries that vanish mid-walk simply don't count towards the total."""
    count = 0
    total = 0
    for walk_root, _dirs, files in os.walk(root):
        for name in files:
            count += 1
            try:
                total += os.path.getsize(os.path.join(walk_root, name))
            except OSError:
                pass
    return count, total


def _mame_file_is_locked(path):
    """True when *path* exists but cannot be opened for writing. On Windows a
    running executable is exactly that, so this spots "MAME is still open"
    before an extraction walks into it (a read-only file answers True too,
    which is equally a reason not to start)."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r+b"):
            return False
    except OSError:
        return True


def _mame_extract_sfx(sfx_path, dest_root):
    """Run the MAME self-extractor over *dest_root* and return once it has
    unpacked everything.

    Waits on it in slices, sampling :func:`_mame_dest_fingerprint` between
    them: an extractor that is working writes files continuously, so a window
    with no new bytes at all means it is parked on its hidden dialog and will
    never come back. Raises RuntimeError on a stall, on the absolute timeout,
    and on a non-zero exit — the caller turns that into the failure line and
    the warning dialog."""
    proc = subprocess.Popen(
        [sfx_path, f"-o{dest_root}", "-y"],
        cwd=dest_root,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **subprocess_no_window_kwargs())
    started = time.monotonic()
    last_progress = started
    seen = _mame_dest_fingerprint(dest_root)
    while True:
        try:
            returncode = proc.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            pass
        now = time.monotonic()
        current = _mame_dest_fingerprint(dest_root)
        if current != seen:
            seen = current
            last_progress = now
        if (now - last_progress >= MAME_SFX_STALL_SECONDS
                or now - started >= MAME_SFX_TIMEOUT_SECONDS):
            _mame_sfx_kill(proc)
            raise RuntimeError(
                "the MAME self-extractor stopped unpacking files and was "
                "cancelled. That happens when it cannot write one of the "
                "files — most often because MAME is still running — and then "
                "waits on a confirmation dialog that stays hidden. Close MAME "
                "and start the install again. Some files may already have "
                "been replaced, so re-running the install is the way to get a "
                "consistent version.")
    if returncode != 0:
        raise RuntimeError(
            f"the MAME self-extractor exited with status {returncode} — the "
            "install is incomplete. Close MAME if it is running and try "
            "again.")


def _mame_sfx_kill(proc):
    """Stop a self-extractor that has stopped making progress, and make sure
    it is really gone: while it lives it holds a lock on its own .exe, and
    that lock is what breaks the *following* download attempt."""
    try:
        proc.kill()
    except OSError:
        logging.exception(
            "MAME install: could not kill the stalled self-extractor")
    try:
        proc.wait(timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        logging.error(
            "MAME install: the stalled self-extractor is still alive after "
            "kill(); the next download will fall back to a fresh file name.")


def build_emulator_ops(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    set_all_buttons_disabled,
    set_all_buttons_enabled,
    _update_mame_controls,
    _right_disk_content,
    getit_run_in_thread,
    _start_load_image_hint_animation,
    add_main_log_window,
    execute_shell_command,
):
    """Define the emulator/self-update closures (no widgets built here)."""
    def set_cspect_screen_size():
        configuration_dictionary[SETTING_SCREENSIZE] = host.cspect_screensize.currentIndex()
        save_configuration_file()

    def set_cspect_sound_on_off():
        configuration_dictionary[SETTING_SOUND] = host.cspect_sound.currentIndex()
        save_configuration_file()

    def set_cspect_vsync_on_off():
        configuration_dictionary[SETTING_VSYNC] = host.cspect_vsync.currentIndex()
        save_configuration_file()

    def set_cspect_joystick_on_off():
        configuration_dictionary[SETTING_JOYSTICK] = host.cspect_joystick.currentIndex()
        save_configuration_file()

    def set_cspect_mouse_on_off():
        configuration_dictionary[SETTING_MOUSE] = host.cspect_mouse.currentIndex()
        save_configuration_file()

    def set_cspect_display_frequency():
        configuration_dictionary[SETTING_HERTZ] = host.cspect_frequency.currentIndex()
        save_configuration_file()

    def set_cspect_esc():
        configuration_dictionary[SETTING_ESC] = host.cspect_esc.currentIndex()
        save_configuration_file()

    # MAME per-launch option combos (aspect / sound / mouse / joystick).
    # Persisted as combo indices, mirroring the CSpect option setters above.
    def set_mame_aspect():
        configuration_dictionary[SETTING_MAME_ASPECT] = host.mame_aspect.currentIndex()
        save_configuration_file()

    def set_mame_sound():
        configuration_dictionary[SETTING_MAME_SOUND] = host.mame_sound.currentIndex()
        save_configuration_file()

    def set_mame_mouse():
        configuration_dictionary[SETTING_MAME_MOUSE] = host.mame_mouse.currentIndex()
        save_configuration_file()

    def set_mame_joystick():
        configuration_dictionary[SETTING_MAME_JOYSTICK] = host.mame_joystick.currentIndex()
        save_configuration_file()

    def set_mame_esc():
        configuration_dictionary[SETTING_MAME_ESC] = host.mame_esc.currentIndex()
        save_configuration_file()

    def open_cspect_configuration_file():
        if platform.system() == "Windows":
            execute_shell_command("notepad", ZX_NEXT_UNITE_CONFIG_FILE_NAME)
        else:
            execute_shell_command("vim", "./" + ZX_NEXT_UNITE_CONFIG_FILE_NAME)
        return

    def launch_cspect(autostart_file=None):
        """Launch CSpect against the loaded SD image.

        *autostart_file* is a HOST path to a file CSpect should load and start
        right after boot; it is appended as CSpect's final argument:

            CSpect.exe … -zxnext -mmc=<image> C:\\tmp\\foo.nex

        It must be a host path, NOT a path inside the mounted image: CSpect
        resolves this argument against its own working directory, so an
        in-image path sends it looking for <cspect dir>/<that path> and it
        fails with "Could not find a part of the path". A caller that wants to
        boot something living on the image has to extract it to the host first
        (see _cspect_start_from_image in zxnu_sdcard_ops).

        Note the parameter is positional on purpose: this function is also a
        clicked() slot, and Qt passes the button's `checked` bool to slots that
        accept an argument. That bool is filtered out by the isinstance() check
        below rather than by a keyword-only signature, which Qt handles less
        predictably across PySide versions."""
        if _right_disk_content():  # check that we have an image content first
            set_all_buttons_disabled()

            # Editable "CSpect default launch parameters" from the Settings
            # tab (persisted in SETTING_CUSTOM), falling back to the built-in
            # default. The SD Card group options are appended on top below,
            # mirroring how launch_mame handles its default command line.
            cspect_default_params = configuration_dictionary.get(
                SETTING_CUSTOM, CSPECT_DEFAULT_LAUNCH_PARAMETERS)
            if not cspect_default_params:
                cspect_default_params = CSPECT_DEFAULT_LAUNCH_PARAMETERS
            cspect_arguments = " " + cspect_default_params + " "
            # Selections are read by INDEX: these combos show translated labels,
            # so currentText() no longer identifies the entry (see
            # emulator_option_argument).
            for _cspect_combo, _cspect_opts in (
                    (host.cspect_screensize, CSPECT_SCREEN_SIZES),
                    (host.cspect_sound, CSPECT_SOUND),
                    (host.cspect_vsync, CSPECT_SCREEN_SYNC),
                    (host.cspect_joystick, CSPECT_JOYSTICK),
                    (host.cspect_mouse, CSPECT_MOUSE),
                    (host.cspect_frequency, CSPECT_FREQUENCY),
                    # ESC-key disable ("-esc"); "Disable ESC Key Off" (default)
                    # passes nothing so ESC still exits.
                    (host.cspect_esc, CSPECT_ESC)):
                cspect_arguments += emulator_option_argument(
                    _cspect_opts, _cspect_combo.currentIndex()) + " "

            # When the CSpect copy in use is a bundled itch.io install under
            # downloads/cspect, it must be launched from its own folder so its
            # Next ROMs resolve. The working directory then differs from the
            # app directory, so the image path must be absolute.
            cspect_exe = getattr(host, "_cspect_executable_path", None)
            use_bundled = (getattr(host, "_cspect_from_downloads", False)
                           and cspect_exe and os.path.isfile(cspect_exe))
            # The path is normalised (unquoted) when loaded, but strip any
            # stray surrounding quotes defensively before os.path work:
            # os.path.abspath() on a string starting with '"' treats it as a
            # relative path and prepends the cwd, yielding a bogus value like
            # <cwd>\"C:\temp\img". Re-quote only the final path below (the
            # -mmc= argument goes through the shell, so spaces need quoting).
            img_path = (host.right_disk_image_path or "").strip().strip('"')
            if use_bundled:
                # CSpect runs from its own folder so its Next ROMs resolve;
                # the working dir then differs from the app dir, so the image
                # path must be absolute.
                cspect_cwd = os.path.dirname(cspect_exe)
                if img_path:
                    img_path = os.path.abspath(img_path)
            else:
                cspect_cwd = None
            mmc_path = f'"{img_path}"' if img_path else img_path

            cspect_arguments += " -mmc=" + mmc_path + " "

            # Auto-start file: a HOST path, appended as CSpect's trailing
            # argument. It is made absolute because CSpect may run from its
            # own folder (the bundled itch.io copy does), and always quoted —
            # this goes through the shell and Next files live under paths with
            # spaces often enough.
            if isinstance(autostart_file, str) and autostart_file.strip():
                _auto = os.path.abspath(autostart_file.strip().strip('"'))
                cspect_arguments += f'"{_auto}" '

            # The command that will actually be invoked: the bundled itch.io
            # copy by absolute path, otherwise the CSpect.exe resolved from the
            # app directory / PATH (prefixed with mono on macOS/Linux).
            if platform.system() == "Windows":
                cspect_executable = f'"{cspect_exe}"' if use_bundled else "CSpect.exe"
            else:
                cspect_executable = f'mono "{cspect_exe}"' if use_bundled else "mono CSpect.exe"
                # Inside the Flatpak sandbox there is no mono — delegate the
                # launch to the host through the Flatpak portal, exactly like
                # the MAME launch (mame_flatpak_command): every involved path
                # (CSpect under ~/.var/app, the cwd, the -mmc image) is a real
                # host path, so the host-side mono resolves them unchanged.
                if os.environ.get("FLATPAK_ID"):
                    cspect_executable = "flatpak-spawn --host " + cspect_executable

            logging.info(f"CSpect executable: {cspect_executable}")
            add_main_log_window(f"CSpect executable: {cspect_executable}")
            logging.info(f"Cspect start with arguments: {cspect_arguments}")
            add_main_log_window(f"Cspect start with arguments: {cspect_arguments}")

            try:
                execute_shell_command(cspect_executable, cspect_arguments, cwd=cspect_cwd)
            except subprocess.CalledProcessError as ex:
                if ex.returncode == 1:
                    logging.error("CSpect.exe is not present in the same local directory as zx-next-unite.Please install it from http://cspect.org")
                    add_main_log_window(ui_tr_now(
                        "ERROR: CSpect.exe is not present in the same local "
                        "directory as zx-next-unite. Please install it from "
                        "http://cspect.org"))
                else:
                    logging.error(f"ERROR: Unknown shell execute error: {ex.returncode} - :{ex}")
                    add_main_log_window(f"ERROR: Unknown shell execute error: {ex.returncode} - :{ex}")

                if platform.system() != "Windows":
                    logging.error("On MacOS and Linux mono is required as it runs under it. Please make sure mono is installed.")
                    add_main_log_window(ui_tr_now(
                        "On MacOS and Linux mono is required as it runs under "
                        "it. Please make sure mono is installed."))
                    if os.environ.get("FLATPAK_ID"):
                        add_main_log_window(ui_tr_now(
                            "Running as a Flatpak: mono must be installed on "
                            "the HOST system — the launch is delegated there "
                            "via flatpak-spawn."))

            set_all_buttons_enabled()


    def launch_mame():
        # Launching MAME boots the Next with the selected HDF as its hard
        # disk and never calls hdfmonkey, so gate on a real image file being
        # selected rather than on right_disk_image_explorer_content (the
        # hdfmonkey-produced listing, which is empty when hdfmonkey is
        # missing) — otherwise MAME could never be launched without hdfmonkey.
        _sel_image = host.imageinput.currentText().strip().strip('"')
        if not (_sel_image and os.path.isfile(_sel_image)):
            add_main_log_window(ui_tr_now(
                "Select a valid ZX Spectrum Next disk image (.img/.hdf) "
                "before launching MAME."))
            return

        # Flatpak mode (Linux) launches `flatpak run org.mamedev.MAME …`
        # instead of a local binary, so a detected executable isn't required
        # there — Flatpak provides MAME itself.
        _flatpak = host._mame_flatpak_enabled()
        mame_path = getattr(host, "_mame_executable_path", None)
        if not _flatpak and not mame_path:
            logging.error("MAME executable not found on PATH. Cannot launch MAME.")
            add_main_log_window(ui_tr_now(
                "ERROR: MAME executable not found on PATH. Cannot launch MAME."))
            return

        # Pull the (possibly user-customised) command line from the cfg file,
        # falling back to the built-in default. The literal placeholder
        # {MAME_EXECUTABLE_NAME} is resolved to the detected executable.
        mame_parameters = configuration_dictionary.get(
            SETTING_MAME_COMMAND_LINE_PARAMETERS, MAME_DEFAULT_COMMAND_LINE
        )
        if not mame_parameters:
            mame_parameters = MAME_DEFAULT_COMMAND_LINE
        mame_parameters = mame_parameters.replace("{MAME_EXECUTABLE_NAME}", "").strip()

        # The ROM/system (e.g. "tbblue") is picked by the user in the Settings
        # tab and stored separately; it is inserted right after the executable.
        mame_rom = configuration_dictionary.get(
            SETTING_MAME_ROM_CHOICE, MAME_ROM_CHOICE[0]
        ).strip()
        if not mame_rom:
            mame_rom = MAME_ROM_CHOICE[0]

        # Build: mame + <rom> + MAME_COMMAND_LINE_PARAMETERS + "-hard1" + image
        # The image path is wrapped in double quotes in the combo box; strip
        # them so it is a valid command-line argument. MAME runs from its own
        # install directory (see below), so resolve the image to an absolute
        # path to keep relative paths working. The "-hard1 <image>" pair is
        # appended last so the image is always the final argument.
        mame_image = host.imageinput.currentText().strip().strip('"')
        if mame_image:
            mame_image = os.path.abspath(mame_image)
        # Drop any aspect/mouse/joystick options from the (editable) params —
        # these are now controlled by the MAME group combos and appended
        # below, so they must not be duplicated or conflict. The launcher
        # prefix is either the detected binary or the Flatpak run command.
        _param_tokens = strip_mame_combo_options(shlex.split(mame_parameters))
        _launch_prefix = (list(mame_flatpak_command()) if _flatpak
                          else [mame_path])
        mame_argv = _launch_prefix + [mame_rom] + _param_tokens

        # Append the per-launch combo options (aspect / mouse / joystick) so
        # the UI selections are authoritative regardless of the params string.
        for _mame_combo, _mame_opts in (
            (getattr(host, "mame_aspect", None), MAME_ASPECT),
            (getattr(host, "mame_sound", None), MAME_SOUND),
            (getattr(host, "mame_mouse", None), MAME_MOUSE),
            (getattr(host, "mame_joystick", None), MAME_JOYSTICK),
            (getattr(host, "mame_esc", None), MAME_ESC),
        ):
            if _mame_combo is None:
                continue
            # By index, not label — the combo shows a translated one.
            _mame_arg = emulator_option_argument(
                _mame_opts, _mame_combo.currentIndex())
            if _mame_arg:
                mame_argv += shlex.split(_mame_arg)

        # Flatpak MAME is sandboxed and has no roms/ next to a binary, so the
        # user's rom directory is passed explicitly as -rompath (before the
        # -hard1 image, which must stay last).
        if _flatpak:
            _rompath = (configuration_dictionary.get(
                SETTING_MAME_FLATPAK_ROMPATH, "") or "").strip()
            if not _rompath:
                _rompath = default_mame_flatpak_rompath()
            mame_argv += ["-rompath", _rompath]

        if mame_image:
            mame_argv += [MAME_HARD_DISK_PARAMETER, mame_image]

        # Executable that will actually be invoked: the detected MAME binary,
        # or the Flatpak run command when Flatpak mode is enabled.
        _mame_executable = " ".join(mame_flatpak_command()) if _flatpak else mame_path
        logging.info(f"MAME executable: {_mame_executable}")
        add_main_log_window(f"MAME executable: {_mame_executable}")
        logging.info(f"MAME start with arguments: {mame_argv}")
        add_main_log_window(f"MAME start with arguments: {' '.join(mame_argv)}")

        # Launch MAME with its stdout/stderr captured so we can surface any
        # startup error (bad ROM path, missing media, invalid option, etc.)
        # in the log window. The process itself runs in its own session/group
        # so it is detached from the app, and a daemon reader thread drains
        # the pipe without blocking the UI.
        #
        # MAME loads its support files (bgfx shaders, hash/, roms/) relative
        # to its own install directory, so run it from there; otherwise it
        # exits immediately when those files cannot be found. A Flatpak launch
        # is self-contained (its support files live inside the sandbox), so no
        # working directory is imposed there.
        mame_cwd = None if _flatpak else (os.path.dirname(mame_path) or None)
        # Reset per-launch: set True if MAME reports the missing-boot-ROM
        # fatal error, so _on_mame_finished can advise the manual TBBLUE step.
        host._mame_missing_files = False
        try:
            if platform.system() == "Windows":
                # CREATE_NEW_PROCESS_GROUP (0x200) detaches MAME from the app.
                # gui_app=True keeps CREATE_NO_WINDOW (so MAME's own console
                # doesn't flash while we capture its stdout) but drops the
                # SW_HIDE STARTUPINFO — otherwise MAME's emulator window would
                # inherit that hidden show-state and open invisible.
                mame_proc = subprocess.Popen(
                    mame_argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    text=True,
                    bufsize=1,
                    cwd=mame_cwd,
                    **subprocess_no_window_kwargs(extra_flags=0x00000200, gui_app=True),
                )
            else:
                mame_proc = subprocess.Popen(
                    mame_argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    text=True,
                    bufsize=1,
                    cwd=mame_cwd,
                    start_new_session=True,
                )
        except Exception as ex:
            logging.error(f"ERROR: Failed to launch MAME: {ex}")
            add_main_log_window(f"ERROR: Failed to launch MAME: {ex}")
            return

        # Marshal captured output back to the UI thread via queued signals
        # (Qt widgets must only be touched from the main thread).
        mame_signals = MameProcessSignals()

        def _on_mame_output(line):
            add_main_log_window(f"MAME: {line}")
            # Detect the "boot ROM missing" fatal error so the finished
            # handler can point the user at the manual TBBLUE step. Runs on
            # the UI thread (queued) before _on_mame_finished (see the reader
            # below: outputs are emitted, then 'finished' in a finally).
            if "required files are missing" in line.lower():
                host._mame_missing_files = True

        mame_signals.output.connect(_on_mame_output, Qt.QueuedConnection)

        def _on_mame_finished(return_code):
            add_main_log_window(f"MAME exited with code {return_code}.")
            logging.info(f"MAME exited with code {return_code}.")
            # MAME aborts when the ZX Spectrum Next boot ROM (TBBLUE, e.g.
            # boot-30204.bin) is absent — a manual step the auto-install
            # deliberately leaves to the user. Point them at the guide.
            if getattr(host, "_mame_missing_files", False):
                add_main_log_window(
                    "MAME can't start: the ZX Spectrum Next boot ROM (TBBLUE) "
                    "is missing. This step is manual — see "
                    f"{MAME_INSTALL_WIKI_URL} and follow \"Get TBBLUE (the "
                    "Next 'boot ROM')\". Put the file tbblue.zip into MAME's "
                    "roms folder (downloads\\mame\\roms) — DON'T extract it — "
                    "and try again. You must provide a legally acquired, "
                    "licensed ROM.")
                logging.warning(
                    "MAME launch failed: TBBLUE boot ROM missing. See "
                    f"{MAME_INSTALL_WIKI_URL}")
                try:
                    host._show_toast(
                        "⚠  MAME needs the Next boot ROM",
                        ui_tr_now(
                            "MAME can't run without the TBBLUE boot ROM — "
                            "a manual step.\r\nSee {url} → \"Get TBBLUE\"."
                            "\r\nPut tbblue.zip into MAME's roms folder "
                            "(downloads\\mame\\roms) — DON'T extract it."
                            "\r\nUse only a legally acquired, licensed "
                            "ROM.").format(url=MAME_INSTALL_WIKI_URL),
                        variant="yellow", duration_ms=12000)
                except Exception:
                    pass

        mame_signals.finished.connect(_on_mame_finished, Qt.QueuedConnection)
        # Keep a reference so the signals object is not garbage-collected
        # while the reader thread is still running.
        host._mame_signals = mame_signals

        def _read_mame_output(proc, signals):
            try:
                if proc.stdout is not None:
                    for raw_line in proc.stdout:
                        line = raw_line.rstrip("\r\n")
                        if line:
                            signals.output.emit(line)
                proc.wait()
            except Exception as exc:
                signals.output.emit(f"ERROR reading MAME output: {exc}")
            finally:
                signals.finished.emit(proc.returncode if proc.returncode is not None else -1)

        threading.Thread(
            target=_read_mame_output,
            args=(mame_proc, mame_signals),
            daemon=True,
        ).start()

    # ── Automatic MAME install (64-bit Windows) ──────────────────────────
    # When MAME isn't on PATH, the SD-card tab shows an "Install MAME" button
    # instead of a disabled "Launch Mame". It detects the latest official
    # release on GitHub, downloads the self-extracting Windows binary for this
    # CPU (x64/arm64) and unpacks it into downloads/mame — no external 7-Zip
    # or extra Python dependency needed (the SFX extracts silently with
    # "-o<dir> -y"). The download/extract runs on the thread pool so the UI
    # stays responsive; only the small "latest release" JSON is fetched inline.
    def _fetch_latest_mame_asset(arch):
        """Query the MAME 'latest release' API and return
        (tag, asset_name, download_url, size_bytes) for this architecture's
        Windows self-extractor. Raises on network error or when no matching
        asset exists (GitHub's API requires a User-Agent header)."""
        req = urllib.request.Request(
            MAME_GITHUB_LATEST_RELEASE_API,
            headers={"User-Agent": ZXART_USER_AGENT,
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            release = json.loads(resp.read().decode("utf-8", "replace"))
        picked = select_mame_release_asset(release, arch)
        if not picked:
            raise RuntimeError(
                f"the latest MAME release has no Windows {arch} build.")
        # Also carry the release "what's changed" notes so the prompts can
        # show them (like the ZX Next Unite self-update prompt).
        return (*picked, str(release.get("body") or "").strip())

    def _fetch_mame_releases(arch):
        """Query the MAME releases list and return the recent releases that
        ship a Windows build for this architecture, newest first (dicts — see
        select_mame_release_assets). Raises on a network error or when none of
        them carries a matching asset. Fetched inline like the latest-release
        lookup above: it is one small JSON request behind a button press."""
        req = urllib.request.Request(
            f"{MAME_GITHUB_RELEASES_API}?per_page={MAME_RELEASE_FETCH_COUNT}",
            headers={"User-Agent": ZXART_USER_AGENT,
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            releases = json.loads(resp.read().decode("utf-8", "replace"))
        found = select_mame_release_assets(releases, arch)
        if not found:
            raise RuntimeError(
                f"none of the recent MAME releases has a Windows {arch} build.")
        return found

    def _pick_mame_release(releases, title):
        """Let the user choose which MAME release to install, newest
        preselected — the same chooser the itch.io CSpect install shows when
        several versions are downloadable. Returns the chosen release dict, or
        None when cancelled (a lone choice needs no dialog)."""
        if len(releases) == 1:
            return releases[0]
        labels = []
        for entry in releases:
            size = entry.get("size")
            labels.append(
                f"{entry['tag']}  —  {entry['asset_name']}"
                + (f"  ({size / 1048576:.0f} MB)" if size else ""))
        label, ok = QInputDialog.getItem(
            host, title,
            f"{len(releases)} recent MAME releases are available for this "
            "machine.\nChoose the one to download and install\n"
            "(the newest is selected by default):",
            labels, 0, False)
        if not ok or not label:
            return None
        return releases[labels.index(label)]

    def _confirm_and_start_mame_install(release, arch, title):
        """Show the chosen release's size/notes for a final confirmation, then
        hand over to the download+extract worker."""
        size = release.get("size")
        size_txt = f"{size / 1048576:.0f} MB" if size else "about 90 MB"
        box = QMessageBox(host)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(
            f"MAME release: {release['tag']}\n"
            f"Package: {release['asset_name']} ({arch})\n\n"
            f"Download (~{size_txt}) and install it into the downloads "
            f"folder?\nNote: the fully extracted install is large (~500 MB).")
        _attach_release_notes(box, release.get("notes"))
        go = box.addButton(ui_tr_now("Download and install"), QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(go)
        box.exec()
        if box.clickedButton() is not go:
            return
        _start_mame_install(release["tag"], release["asset_name"],
                            release["url"], size, size_txt,
                            sha256=release.get("sha256"))

    def _choose_and_install_mame(title):
        """List the recent MAME releases, let the user pick one and install it.

        Shared by the first-time install button and the update prompt's
        "Choose another release…" escape hatch — the latter is also the way to
        put an older build back and exercise the startup update check."""
        if getattr(host, "_mame_installing", False):
            return
        arch = mame_windows_asset_arch()
        if arch is None:
            QMessageBox.information(
                host, title,
                "Automatic MAME installation is only available on 64-bit "
                "Windows (x64 / arm64).\n\nDownload the official binaries for "
                "your system from:\nhttps://www.mamedev.org/release.html")
            return
        add_main_log_window(ui_tr_now("Listing the available MAME releases…"))
        try:
            releases = _fetch_mame_releases(arch)
        except Exception as e:
            add_main_log_window(f"Could not list the MAME releases: {e}")
            logging.error(f"MAME release listing failed: {e}")
            QMessageBox.warning(
                host, title,
                f"Could not list the available MAME releases.\n\n{e}\n\n"
                "Check your internet connection, or download manually from "
                "https://www.mamedev.org/release.html")
            return
        chosen = _pick_mame_release(releases, title)
        if chosen is None:
            add_main_log_window(ui_tr_now(
                "MAME install ▸ release picker cancelled."))
            return
        _confirm_and_start_mame_install(chosen, arch, title)

    def _mame_install_job(url, asset_name, dest_root, mame_sig,
                          expected_sha256=None):
        """Worker-thread job: download the MAME self-extractor and unpack it.

        Never touches Qt widgets — phase lines and the button's percentage
        are marshalled to the UI via *mame_sig* (queued). Streams the download
        to downloads/mame/<asset>, runs the SFX with '-o<dir> -y' to extract
        in place under the watchdog described at the top of this module,
        deletes the installer and returns the path to the installed mame
        executable. Windows-only (the sole platform with an official
        precompiled MAME binary). The caller keeps *mame_sig* alive for the
        duration."""
        def _phase(msg):
            try:
                mame_sig.status.emit(msg)
            except RuntimeError:
                pass

        def _prog(pct):
            try:
                mame_sig.progress.emit(pct)
            except RuntimeError:
                pass

        os.makedirs(dest_root, exist_ok=True)
        sfx_path = os.path.join(dest_root, asset_name)

        # ── Phase 0: can what is already there actually be replaced? ──
        # The extractor cannot ask (its dialog is hidden), so a locked
        # mame.exe would cost an ~87 MB download and then stall. Both entry
        # points — first install and the startup update offer — come through
        # here, so one check covers them.
        installed_exe = os.path.join(
            dest_root,
            MAME_EXECUTABLE_NAME
            + (".exe" if platform.system() == "Windows" else ""))
        if _mame_file_is_locked(installed_exe):
            raise RuntimeError(
                f"{os.path.basename(installed_exe)} cannot be replaced — it is "
                "running, locked by another program, or read-only. Close MAME "
                "(including any emulator window started from this app) and run "
                "the install again.")

        # A leftover installer from an earlier attempt has to go before the
        # download can reuse its name; an app build without the watchdog below
        # could orphan a still-running extractor, and Windows refuses to open
        # a running image for writing. Falling back to a side-by-side name
        # keeps even a stubborn leftover from blocking the install.
        if os.path.exists(sfx_path):
            try:
                os.remove(sfx_path)
            except OSError:
                logging.exception(
                    f"MAME install: could not remove the stale installer "
                    f"{sfx_path}")
                sfx_path = os.path.join(
                    dest_root, f"{os.getpid()}-{asset_name}")
                _phase("MAME install ▸ The previous installer is still locked; "
                       f"downloading as {os.path.basename(sfx_path)} instead.")

        # ── Phase 1: download ──
        _phase(f"MAME install ▸ Downloading {asset_name} …")
        req = urllib.request.Request(
            url, headers={"User-Agent": ZXART_USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp, \
                open(sfx_path, "wb") as out:
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            read = 0
            while True:
                chunk = resp.read(262144)
                if not chunk:
                    break
                out.write(chunk)
                read += len(chunk)
                if total:
                    _prog(min(98, int(read * 100 / total)))
        _prog(99)
        _phase(f"MAME install ▸ Download finished ({read // 1048576} MB).")

        # ── Phase 1b: verify ── the GitHub API publishes a per-asset
        # SHA-256 digest; check the download against it BEFORE running the
        # self-extractor (it is an executable). No digest published -> log
        # and continue, exactly as before.
        if expected_sha256:
            _phase("MAME install ▸ Verifying the download (SHA-256)…")
            actual = sha256_of_file(sfx_path)
            if actual.lower() != expected_sha256.lower():
                try:
                    os.remove(sfx_path)
                except OSError:
                    pass
                raise RuntimeError(
                    "SHA-256 mismatch on the downloaded MAME installer "
                    f"(expected {expected_sha256[:12]}…, got {actual[:12]}…) "
                    "— refusing to run it. Please retry the download.")
            _phase("MAME install ▸ SHA-256 verified.")
        else:
            _phase("MAME install ▸ No SHA-256 digest published for this "
                   "asset; skipping verification.")

        # ── Phase 2: extract ──
        # MAME's Windows .exe is a 7-Zip self-extractor: "-o<dir> -y" unpacks
        # it silently (verified: no GUI, mame.exe lands at the top of <dir>).
        # It runs under the watchdog documented at the top of this module —
        # a hidden extractor that hits an unwritable file waits on a dialog
        # nobody can see, so progress is sampled instead of blindly waited on.
        _phase("MAME install ▸ Extracting the archive (this can take a moment) …")
        _mame_extract_sfx(sfx_path, dest_root)
        _phase("MAME install ▸ Extraction finished; cleaning up the installer.")
        try:
            os.remove(sfx_path)
        except OSError:
            pass
        return find_mame_in_downloads(
            ZXNU_DATA_ROOT)

    def _on_mame_install_progress(pct):
        try:
            if pct >= 99:
                host.button_install_mame.setText("⬇  Installing MAME… unpacking")
            else:
                host.button_install_mame.setText(f"⬇  Installing MAME… {pct}%")
        except RuntimeError:
            pass

    def _mame_finish_install(detected):
        """Shared post-install success path for both the Windows and Linux
        installers: adopt the freshly installed build with no restart, reveal
        the SD Card tab's MAME launch controls, persist the installed release
        tag (so the startup update check can compare without re-running the
        binary), and log + toast the completion — including the one manual
        step, dropping the TBBLUE boot ROM into MAME's roms folder. The caller
        has already hidden its own install button and confirmed *detected* is
        a real path."""
        host._mame_executable_path = detected
        # An emulator is now usable: if no image is loaded yet, start the
        # yellow hint pulse on the image-picking buttons.
        _start_load_image_hint_animation()
        _installed_tag = getattr(host, "_mame_pending_install_tag", "")
        if _installed_tag:
            configuration_dictionary[SETTING_MAME_INSTALLED_TAG] = _installed_tag
            try:
                save_configuration_file()
            except Exception:
                pass
        try:
            # The MAME group may have been hidden entirely at startup (MAME
            # absent and no installer offered in that tab) — reveal it and its
            # launch controls now that a build is present.
            host.mame_group.setVisible(True)
            host.button_start_mame.setVisible(True)
            # Reveal the MAME option combos, hidden while only an installer
            # button was offered.
            for _mame_combo in (getattr(host, "mame_aspect", None),
                                getattr(host, "mame_sound", None),
                                getattr(host, "mame_mouse", None),
                                getattr(host, "mame_joystick", None),
                                getattr(host, "mame_esc", None)):
                if _mame_combo is not None:
                    _mame_combo.setVisible(True)
            # Now that MAME is present, set the just-revealed group's
            # enabled state (and the launch hint) from the current image.
            _update_mame_controls()
        except RuntimeError:
            pass
        # One manual step remains: MAME needs the ZX Spectrum Next boot ROM
        # (TBBLUE, e.g. boot-30204.bin), which we deliberately don't fetch. It
        # goes in a 'roms' folder next to the mame binary; compute that from
        # the detected path so the hint is right wherever the build unpacked.
        roms_dir = os.path.join(os.path.dirname(detected), "roms")
        add_main_log_window(f"MAME install ▸ SUCCESS — MAME detected at: {detected}")
        add_main_log_window(ui_tr_now(
            "MAME is ready to launch now — no restart needed. Use the "
            "'🕹  Launch Mame' button."))
        add_main_log_window(
            "MAME install ▸ NEXT STEP (manual): add the TBBLUE boot ROM. See "
            f"{MAME_INSTALL_WIKI_URL} → \"Get TBBLUE (the Next 'boot ROM')\". "
            f"Put the file tbblue.zip into MAME's roms folder ({roms_dir}) — "
            "DON'T extract it. You must provide a legally acquired, licensed "
            "ROM.")
        logging.info(f"Successfully installed MAME: {detected}")
        try:
            host._show_toast(
                "✅  MAME installed",
                ui_tr_now(
                    "MAME is installed — no restart needed.\r\n"
                    "Manual step: add the TBBLUE boot ROM.\r\n"
                    "See {url} → \"Get TBBLUE\".\r\n"
                    "Put tbblue.zip into MAME's roms folder\r\n({roms})\r\n"
                    "— DON'T extract it.\r\n"
                    "Use only a legally acquired, licensed ROM.").format(
                        url=MAME_INSTALL_WIKI_URL, roms=roms_dir),
                variant="green", duration_ms=12000)
        except Exception:
            pass

    def _on_mame_install_result(mame_path):
        host._mame_installing = False
        # Re-run the normal MAME detection so the app adopts the freshly
        # installed build straight away — the user shouldn't have to restart.
        app_dir = ZXNU_DATA_ROOT
        try:
            detected = resolve_mame_executable(app_dir)
        except Exception:
            detected = None
        detected = detected or mame_path
        if not detected:
            try:
                host.button_install_mame.setEnabled(True)
                host.button_install_mame.setText(ui_tr_now("⬇  Install MAME"))
            except RuntimeError:
                pass
            add_main_log_window(ui_tr_now(
                "MAME install ▸ FAILED — the download and extraction finished, "
                "but no mame.exe could be found in downloads/mame."))
            logging.error("MAME install: mame.exe not found after extraction.")
            try:
                host._show_toast(
                    "⚠  MAME install failed",
                    "The archive was extracted but mame.exe could not be "
                    "located in downloads/mame.",
                    variant="yellow", duration_ms=8000)
            except Exception:
                pass
            return
        try:
            host.button_install_mame.setVisible(False)
        except RuntimeError:
            pass
        _mame_finish_install(detected)

    def _on_mame_install_error(err):
        host._mame_installing = False
        try:
            host.button_install_mame.setEnabled(True)
            host.button_install_mame.setText(ui_tr_now("⬇  Install MAME"))
        except RuntimeError:
            pass
        detail = err[1] if isinstance(err, (tuple, list)) and len(err) > 1 else err
        add_main_log_window(ui_tr_now(
            "MAME install ▸ FAILED — {error}. You can download it manually "
            "from https://www.mamedev.org/release.html").format(error=detail))
        logging.error(f"Failed to download/install MAME: {err}")
        try:
            QMessageBox.warning(
                host, "Install MAME",
                f"MAME installation failed.\n\n{detail}\n\n"
                "You can download it manually from "
                "https://www.mamedev.org/release.html")
        except Exception:
            pass

    def _start_mame_install(tag, asset_name, url, size, size_txt,
                            sha256=None):
        """Kick off the MAME download+extract worker for a chosen release
        asset. Shared by the first-time install (install_mame) and the
        startup auto-update flow — both overwrite any existing downloads/mame
        files (the SFX runs with '-y'). *tag* is remembered so a successful
        install persists the installed release for later update checks."""
        app_dir = ZXNU_DATA_ROOT
        dest_root = os.path.join(app_dir, DOWNLOADS_MAME_DIRNAME)
        host._mame_pending_install_tag = tag
        host._mame_installing = True
        try:
            host.button_install_mame.setEnabled(False)
            host.button_install_mame.setText("⬇  Installing MAME… 0%")
        except RuntimeError:
            pass
        add_main_log_window(ui_tr_now(
            "MAME install ▸ Starting: {tag} ({asset}, ~{size}).").format(
                tag=tag, asset=asset_name, size=size_txt))
        # Channel for the worker's phase log lines + button percentage. It is
        # stored on self so it outlives this call: otherwise it would be
        # garbage-collected the moment we return, cancelling the queued emits
        # before they reach the UI thread. Connected with Qt.QueuedConnection
        # so the slots run on the UI thread.
        mame_sig = MameInstallSignals()
        mame_sig.status.connect(
            lambda line: add_main_log_window(line), Qt.QueuedConnection)
        mame_sig.progress.connect(_on_mame_install_progress, Qt.QueuedConnection)
        host._mame_install_signals = mame_sig
        # Run the download+extract off the UI thread. getit_run_in_thread
        # keeps its own result/error signals alive (parented to the app) until
        # the main-thread slot has run, so the completion callbacks fire
        # reliably — the missing piece that left the button stuck before.
        getit_run_in_thread(
            lambda: _mame_install_job(url, asset_name, dest_root, mame_sig,
                                      expected_sha256=sha256),
            _on_mame_install_result,
            _on_mame_install_error)

    def install_mame():
        """Download and install a MAME release of the user's choosing (button
        handler): the recent releases are listed with the newest preselected,
        so this is both the first-time install and the way to pick a specific
        build."""
        _choose_and_install_mame("Install MAME")

    def _probe_mame_version_text(mame_path):
        """Ask an installed MAME binary for its version and return the raw
        output text (e.g. "0.288 (mame0288)"), or "". Runs on a worker
        thread (blocking subprocess). Used only when no installed release
        tag was persisted (MAME was installed manually / before the
        update-check feature): a copy installed via the app records its
        tag, so this probe is skipped.

        'mame -version' prints the version and exits; on an older build that
        doesn't know the option, MAME still prints its usage banner ("MAME
        v0.NNN …") which carries the version too, so parsing the combined
        output is robust either way. The raw text is returned (not just the
        parsed number) so the caller can also spot a custom-patched build
        from the parenthesised git tag."""
        try:
            proc = subprocess.run(
                [mame_path, "-version"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=os.path.dirname(mame_path) or None,
                text=True, timeout=20,
                **subprocess_no_window_kwargs())
            return proc.stdout or ""
        except Exception as exc:
            logging.info(f"MAME version probe failed: {exc}")
            return ""

    def _attach_release_notes(box, notes):
        """Show a release's "what's changed" notes on a QMessageBox: a short
        excerpt as the informative text, the full text behind the Show
        Details button. No-op when *notes* is empty. Shared by the ZX Next
        Unite, MAME and CSpect update/install prompts."""
        notes = (notes or "").strip()
        if not notes:
            return
        excerpt = "\n".join(notes.splitlines()[:10]).strip()[:800]
        box.setInformativeText(
            "What's changed:\n" + excerpt
            + ("\n…" if len(excerpt) < len(notes) else ""))
        box.setDetailedText(notes)

    def _prompt_mame_update(info, installed_num):
        """UI-thread dialog offering to update MAME to a newer release found
        by the startup check. 'Update' reuses the standard install flow for
        the platform (Windows SFX or Linux zip), overwriting the existing
        files; 'Cancel' does nothing."""
        tag = info["tag"]
        asset_name = info["asset_name"]
        url = info["url"]
        size = info["size"]
        latest_num = info["latest_num"]
        size_txt = f"{size / 1048576:.0f} MB" if size else "about 90 MB"
        box = QMessageBox(host)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(ui_tr_now("MAME update available"))
        box.setText(
            "A newer version of MAME is available.\n\n"
            f"Installed: 0.{installed_num}\n"
            f"Latest: {tag}  (0.{latest_num})\n"
            f"Package: {asset_name}\n\n"
            f"Download (~{size_txt}) and update your MAME install now?\n"
            "The existing files in the downloads MAME folder will be "
            "overwritten.")
        _attach_release_notes(box, info.get("notes"))
        upd = box.addButton(ui_tr_now("Update"), QMessageBox.AcceptRole)
        # Escape hatch to any other recent release — including an older one,
        # which is how the update path itself can be exercised again.
        other = box.addButton(
            ui_tr_now("Choose another release…"), QMessageBox.ActionRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(upd)
        box.exec()
        clicked = box.clickedButton()
        if clicked is upd:
            add_main_log_window(ui_tr_now(
                "MAME update ▸ user chose to update to {tag}.").format(tag=tag))
            _start_mame_install(tag, asset_name, url, size, size_txt,
                                sha256=info.get("sha256"))
        elif clicked is other:
            add_main_log_window(ui_tr_now(
                "MAME update ▸ user chose to pick a release manually."))
            _choose_and_install_mame("Choose a MAME release")

    def _check_mame_update_async():
        """At startup, if MAME is installed and the check is enabled, look up
        the latest MAME release on GitHub (off the UI thread) and, when it is
        newer than the installed build, offer to update. Any failure is
        logged quietly and never disrupts startup."""
        pref = configuration_dictionary.get(
            SETTING_MAME_UPDATE_CHECK, "").strip().lower()
        if pref in ("false", "0", "no"):
            return  # user disabled the check
        if getattr(host, "_mame_executable_path", None) is None:
            return  # MAME not installed — nothing to update
        if getattr(host, "_mame_installing", False):
            return  # an install/update is already running
        arch = mame_windows_asset_arch()
        # In-app install/update exists only on 64-bit Windows (the only
        # platform with an official precompiled MAME binary). Linux/macOS are
        # detection-only, so bail there.
        if not mame_auto_install_supported():
            return  # auto-install/update unsupported on this OS/CPU
        mame_path = host._mame_executable_path
        installed_tag = configuration_dictionary.get(
            SETTING_MAME_INSTALLED_TAG, "")

        def _job():
            # Prefer the persisted tag of an app-managed install; otherwise
            # ask the binary itself. Do this first so a probe failure still
            # lets us fetch the release (and vice-versa). An app-managed
            # install is an official build, so only the probe path can
            # detect a custom-patched binary.
            installed_num = parse_mame_version_number(installed_tag)
            patched = False
            if installed_num is None:
                raw = _probe_mame_version_text(mame_path)
                installed_num = parse_mame_version_number(raw)
                patched = mame_version_is_patched(raw)
            tag, asset_name, url, size, sha256, notes = _fetch_latest_mame_asset(arch)
            return {
                "installed_num": installed_num,
                "patched": patched,
                "latest_num": parse_mame_version_number(tag),
                "tag": tag, "asset_name": asset_name,
                "url": url, "size": size, "sha256": sha256,
                "notes": notes,
            }

        def _on_result(info):
            # Every branch logs something: the "Checking for a newer MAME
            # release…" line above must never be the last word, or the
            # check looks stuck.
            try:
                installed_num = info.get("installed_num")
                latest_num = info.get("latest_num")
                if latest_num is None:
                    add_main_log_window(ui_tr_now(
                        "MAME update check: could not determine the "
                        "latest release; skipping."))
                    return
                if installed_num is None:
                    add_main_log_window(ui_tr_now(
                        "MAME update check: could not determine the installed "
                        "MAME version; skipping."))
                    return
                if latest_num <= installed_num:
                    if info.get("patched"):
                        add_main_log_window(ui_tr_now(
                            "MAME is up-to-date with a patched version "
                            "(installed 0.{installed}, latest 0.{latest})."
                        ).format(installed=installed_num, latest=latest_num))
                    else:
                        add_main_log_window(ui_tr_now(
                            "MAME is up-to-date (installed 0.{installed}, "
                            "latest 0.{latest})."
                        ).format(installed=installed_num, latest=latest_num))
                    return
                _prompt_mame_update(info, installed_num)
            except Exception as exc:
                logging.info(f"MAME update check result handling failed: {exc}")

        def _on_error(err):
            detail = err[1] if isinstance(err, (tuple, list)) and len(err) > 1 else err
            logging.info(f"MAME update check skipped: {detail}")
            add_main_log_window(ui_tr_now(
                "MAME update check: could not reach the release site; "
                "skipping."))

        add_main_log_window(ui_tr_now("Checking for a newer MAME release…"))
        getit_run_in_thread(_job, _on_result, _on_error)

    # Expose so the startup sequence can kick the check once the config
    # (enable flag + installed tag) has been loaded.
    host._check_mame_update_async = _check_mame_update_async

    # ── ZX Next Unite self-update check (GitHub releases) ───────────────
    # Mirrors the MAME startup check above, but for this app's own
    # releases: binary (frozen) users are offered a download + restart
    # with the package for their platform (Windows .exe, linux tar.gz,
    # macOS zip), source checkouts are advised to 'git pull' instead.
    # Every branch logs to the SD Card log window so the check never
    # looks stuck.

    def _parse_zxnu_version(text):
        """'v9.0.8' / '9.0.8' -> (9, 0, 8); None when unparseable."""
        try:
            parts = str(text).strip().lstrip("vV").split(".")
            out = tuple(int(p) for p in parts if p != "")
            return out or None
        except (ValueError, AttributeError):
            return None

    def _fetch_latest_zxnu_release():
        """Decoded JSON of this app's 'latest release' GitHub API reply
        (GitHub requires a User-Agent header). Raises on network errors;
        404 simply means no release has been published yet."""
        req = urllib.request.Request(
            ZXNU_GITHUB_LATEST_RELEASE_API,
            headers={"User-Agent": ZXART_USER_AGENT,
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def _zxnu_pick_release_asset(release):
        """(name, url, size, sha256) of the update package for THIS
        platform attached to *release*: the .exe on Windows, the
        linux tar.gz / macOS zip elsewhere (see
        select_zxnu_release_asset in zxnu_config). None when the
        release carries no package for this platform."""
        return select_zxnu_release_asset(release)

    def _zxnu_offer_restart(new_path):
        """Post-download: offer to close the app and start the new binary
        (or .app bundle on macOS), naming it clearly so the user knows
        exactly what to run."""
        name = os.path.basename(new_path)
        is_app_bundle = new_path.lower().endswith(".app")
        gatekeeper_note = (
            "\n\nmacOS may block the first launch (unidentified "
            "developer) — if so, right-click the app in Finder and "
            "choose Open." if is_app_bundle else "")
        box = QMessageBox(host)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(ui_tr_now("Update downloaded"))
        box.setText(
            "The new version was saved as:\n\n"
            f"{new_path}\n\n"
            f"Close ZX Next Unite now and start the new version ({name})?\n"
            "Your settings (hdfg.cfg) and downloads are picked up as-is —\n"
            "both versions run from the same folder." + gatekeeper_note)
        yes = box.addButton(f"Close and start {name}", QMessageBox.AcceptRole)
        box.addButton(ui_tr_now("Later"), QMessageBox.RejectRole)
        box.setDefaultButton(yes)
        box.exec()
        if box.clickedButton() is not yes:
            add_main_log_window(
                f"ZX Next Unite update: downloaded — start it any time: {new_path}")
            return
        add_main_log_window(f"ZX Next Unite update: starting {name} and closing…")
        try:
            launch = ["open", new_path] if is_app_bundle else [new_path]
            subprocess.Popen(launch, cwd=os.path.dirname(new_path) or None)
        except OSError as exc:
            add_main_log_window(f"ZX Next Unite update: could not start {name}: {exc}")
            QMessageBox.critical(host, "Could not start the new version",
                                 f"{new_path}\n\n{exc}")
            return
        host.close()

    def _zxnu_download_update(tag, asset_name, url, size,
                              expected_sha256=None):
        """Download the release package next to the current binary on a
        worker thread (progress dialog, cancellable), unpack it when it
        is an archive (linux tar.gz / macOS zip — the runnable inside is
        version-stamped by the release workflow), then offer the restart.
        Never overwrites the RUNNING binary: a name collision gets the
        release tag appended to the filename."""
        app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        dest = os.path.join(app_dir, asset_name)
        current = os.path.abspath(
            sys.executable if getattr(sys, "frozen", False) else sys.argv[0])
        if os.path.normcase(dest) == os.path.normcase(current):
            stem, ext = os.path.splitext(asset_name)
            dest = os.path.join(app_dir, f"{stem}-{tag}{ext}")
        holder = {"ok": False, "error": "", "runnable": ""}

        def _task(signals, cancel_event, _url=url, _dest=dest, _size=size,
                  _h=holder):
            tmp = _dest + ".part"
            try:
                req = urllib.request.Request(
                    _url, headers={"User-Agent": ZXART_USER_AGENT})
                with urllib.request.urlopen(req, timeout=30) as resp, \
                        open(tmp, "wb") as out:
                    total = _size or int(resp.headers.get("Content-Length") or 0)
                    got = 0
                    while True:
                        if cancel_event.is_set():
                            _h["error"] = "cancelled"
                            break
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        out.write(chunk)
                        got += len(chunk)
                        if total:
                            signals.progress.emit(int(got * 100 / total))
                        signals.status.emit(
                            f"Downloading {os.path.basename(_dest)}…\n"
                            f"{got // 1048576} MB"
                            + (f" / {total // 1048576} MB" if total else ""))
                if not _h["error"]:
                    # Verify against the GitHub asset digest BEFORE the
                    # file takes its final (runnable) name.
                    if (expected_sha256
                            and sha256_of_file(tmp).lower()
                            != expected_sha256.lower()):
                        _h["error"] = (
                            "SHA-256 mismatch — the download does not match "
                            "the hash GitHub published for this release "
                            "asset. Please retry.")
                    else:
                        os.replace(tmp, _dest)
                        if _dest.lower().endswith(".exe"):
                            _h["runnable"] = _dest
                        else:
                            # Archive package (linux tar.gz / macOS zip):
                            # unpack next to the app; the runnable inside
                            # is version-stamped, so it never collides
                            # with the running binary.
                            signals.status.emit(
                                f"Unpacking {os.path.basename(_dest)}…")
                            _h["runnable"] = extract_zxnu_update_archive(
                                _dest, os.path.dirname(_dest))
                        _h["ok"] = True
            except Exception as exc:   # network/disk problems must never crash
                _h["error"] = str(exc)
            if not _h["ok"]:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

        dlg = HdfProgressDialog("Downloading ZX Next Unite update…", host)
        worker = HdfTaskWorker(_task)
        dlg.cancel_requested.connect(worker.cancel)
        worker.signals.progress.connect(dlg.set_progress)
        worker.signals.status.connect(dlg.set_status)
        worker.signals.cancelled.connect(dlg.mark_cancelled)

        def _on_zxnu_download_done():
            dlg.close()
            if holder["ok"]:
                add_main_log_window(
                    f"ZX Next Unite update: downloaded {os.path.basename(dest)} "
                    f"to {os.path.dirname(dest)}"
                    + (" (SHA-256 verified)." if expected_sha256 else
                       " (no SHA-256 digest published; not verified)."))
                runnable = holder["runnable"] or dest
                if runnable != dest:
                    add_main_log_window(
                        f"ZX Next Unite update: unpacked to {runnable}")
                _zxnu_offer_restart(runnable)
            elif holder["error"] == "cancelled":
                add_main_log_window(ui_tr_now(
                    "ZX Next Unite update: download cancelled."))
            elif os.path.isfile(dest):
                # The package arrived but could not be unpacked — keep it
                # so the user can extract it by hand.
                add_main_log_window(
                    f"ZX Next Unite update: downloaded {dest} but could not "
                    f"unpack it: {holder['error']}")
                QMessageBox.critical(
                    host, "Update could not be unpacked",
                    f"The update was downloaded to:\n{dest}\n\n"
                    f"but unpacking failed:\n{holder['error']}\n\n"
                    "You can extract it manually next to the current app.")
            else:
                add_main_log_window(
                    f"ZX Next Unite update: download FAILED: {holder['error']}")
                QMessageBox.critical(host, "Update download failed",
                                     f"Could not download the update:\n{holder['error']}")

        worker.signals.finished.connect(_on_zxnu_download_done)
        host.threadpool.start(worker)
        dlg.exec()

    def _prompt_zxnu_update(tag, release):
        """UI-thread prompt for an available app update. Binary (frozen)
        users get download + restart with their platform's package; a
        source checkout (git clone) is advised to 'git pull' instead.
        The release's "what's changed" notes ride along: a short excerpt
        inline, the full text behind the box's Show Details button."""
        frozen = bool(getattr(sys, "frozen", False))
        notes = str(release.get("body") or "").strip()

        def _attach_notes(box):
            _attach_release_notes(box, notes)
        if not frozen:
            add_main_log_window(
                f"ZX Next Unite {tag} is available — running from source, so "
                "update with 'git pull' instead of the Windows binary.")
            box = QMessageBox(host)
            box.setIcon(QMessageBox.Information)
            box.setWindowTitle(ui_tr_now("ZX Next Unite update available"))
            box.setText(
                f"ZX Next Unite {tag} is available "
                f"(you are running {ZX_NEXT_UNITE_VERSION}).\n\n"
                "You appear to be running from source (git clone), so the\n"
                "recommended way to update is:\n\n"
                "    git pull\n\n"
                "instead of downloading the Windows binary.")
            _attach_notes(box)
            openrel = box.addButton(
                ui_tr_now("Open the releases page"), QMessageBox.AcceptRole)
            box.addButton(ui_tr_now("Close"), QMessageBox.RejectRole)
            box.setDefaultButton(openrel)
            box.exec()
            if box.clickedButton() is openrel:
                webbrowser.open(ZXNU_GITHUB_RELEASES_PAGE)
            return
        asset = _zxnu_pick_release_asset(release)
        if not asset:
            add_main_log_window(
                f"ZX Next Unite {tag} is available, but the release has no "
                "package for this platform — opening the releases page "
                "instead.")
            if QMessageBox.question(
                    host, "ZX Next Unite update available",
                    f"{tag} is available but carries no package for this "
                    "platform.\n"
                    "Open the releases page in your browser?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes) == QMessageBox.Yes:
                webbrowser.open(ZXNU_GITHUB_RELEASES_PAGE)
            return
        asset_name, url, size, sha256 = asset
        size_txt = f"{size / 1048576:.0f} MB" if size else "?"
        box = QMessageBox(host)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(ui_tr_now("ZX Next Unite update available"))
        box.setText(
            f"ZX Next Unite {tag} is available — download?\n\n"
            f"Installed: {ZX_NEXT_UNITE_VERSION}\n"
            f"Latest: {tag}\n"
            f"Package: {asset_name} (~{size_txt})\n\n"
            "The new version is saved next to the current one — you choose\n"
            "when to switch (you'll be offered a restart after the download).")
        _attach_notes(box)
        dl = box.addButton("Download", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(dl)
        box.exec()
        if box.clickedButton() is dl:
            add_main_log_window(f"ZX Next Unite update ▸ downloading {asset_name}…")
            _zxnu_download_update(tag, asset_name, url, size,
                                  expected_sha256=sha256)
        else:
            add_main_log_window(ui_tr_now(
                "ZX Next Unite update ▸ skipped by user."))

    def _check_zxnu_update_async():
        """At startup, when the Settings toggle is on, look up this app's
        latest GitHub release (off the UI thread) and offer to update when
        it is newer. Mirrors the MAME check: every branch logs to the SD
        Card log window and failures never disrupt startup."""
        if os.environ.get("FLATPAK_ID"):
            # Sandboxed install: /app is read-only and updates arrive via
            # the Flatpak remote, so the in-app self-updater must stay out
            # of the way.
            add_main_log_window(ui_tr_now(
                "ZX Next Unite update check: running as a Flatpak — "
                "updates come from your software center, skipping."))
            return
        pref = configuration_dictionary.get(
            SETTING_ZXNU_UPDATE_CHECK, "").strip().lower()
        if pref in ("false", "0", "no"):
            return  # user disabled the check

        def _job():
            return _fetch_latest_zxnu_release()

        def _on_result(release):
            try:
                tag = str(release.get("tag_name", "")).strip()
                remote = _parse_zxnu_version(tag)
                local = _parse_zxnu_version(ZX_NEXT_UNITE_VERSION)
                if not remote or not local:
                    add_main_log_window(
                        "ZX Next Unite update check: could not parse the "
                        f"versions (latest tag {tag!r}); skipping.")
                    return
                if remote <= local:
                    add_main_log_window(ui_tr_now(
                        "ZX Next Unite is up to date (installed {installed}, "
                        "latest {latest})."
                    ).format(installed=ZX_NEXT_UNITE_VERSION, latest=tag))
                    return
                add_main_log_window(
                    f"ZX Next Unite update available: {tag} "
                    f"(installed {ZX_NEXT_UNITE_VERSION}).")
                _prompt_zxnu_update(tag, release)
            except Exception as exc:
                logging.info(f"ZXNU update check result handling failed: {exc}")

        def _on_error(err):
            detail = err[1] if isinstance(err, (tuple, list)) and len(err) > 1 else err
            logging.info(f"ZXNU update check skipped: {detail}")
            add_main_log_window(ui_tr_now(
                "ZX Next Unite update check: could not reach GitHub "
                "(offline, or no release published yet); skipping."))

        add_main_log_window(ui_tr_now(
            "Checking for a newer ZX Next Unite release on GitHub…"))
        getit_run_in_thread(_job, _on_result, _on_error)

    host._check_zxnu_update_async = _check_zxnu_update_async

    def _check_dotn_version_advisory():
        """After an app update, warn ONCE when the bundled NextSync .sync5
        dotN version changed: the dot lives on the Next's SD card, so the
        app cannot deploy it automatically. First run (no saved value in
        the cfg) records the current version silently — existing installs
        aren't nagged retroactively."""
        saved = configuration_dictionary.get(
            SETTING_DOTN_LAST_VERSION, "").strip()
        if saved == ZX_NEXT_UNITE_DOTN_VERSION:
            return
        configuration_dictionary[SETTING_DOTN_LAST_VERSION] = \
            ZX_NEXT_UNITE_DOTN_VERSION
        save_configuration_file()
        if not saved:
            return  # first run with this feature: remember, don't nag
        add_main_log_window(
            f"NextSync .sync5 dot command updated: v{saved} -> "
            f"v{ZX_NEXT_UNITE_DOTN_VERSION} — please copy the new build "
            "to your Next (it cannot be deployed automatically).")
        QMessageBox.information(
            host, ".sync5 needs updating on your Next",
            "This ZX Next Unite version ships an updated NextSync dot "
            f"command: .sync5 v{saved} → v{ZX_NEXT_UNITE_DOTN_VERSION}.\n\n"
            "The dot runs on the Spectrum Next itself, so it cannot be "
            "updated automatically. Please copy the new build to your "
            "Next (e.g. into C:/dot as 'sync5'):\n\n"
            "  • the 'sync5' file attached to the GitHub release, or\n"
            "  • nextsync/sync/server/dot/syncdev from the repository "
            "(also push-able via the Remote Explorer while the OLD dot "
            "is connected).\n\n"
            "Until then the previous dot keeps working with this app.")

    host._check_dotn_version_advisory = _check_dotn_version_advisory

    # ── CSpect update check (itch.io) ──────────────────────────────────
    # Mirrors the MAME startup update check above, but sources the build
    # from the user's *owned* itch.io CSpect item instead of GitHub. The
    # closures below resolve add_main_log_window / getit_run_in_thread /
    # configuration_dictionary at call time (late binding), exactly as the
    # MAME check does — they are defined later in this same scope but the
    # functions here only run after startup completes.

    def _cspect_update_dest_dir():
        """Absolute downloads/itchio root where itch.io CSpect installs land
        (and where an update is downloaded + extracted)."""
        return os.path.join(
            ZXNU_DATA_ROOT,
            DOWNLOADS_CSPECT_DIRNAME)

    def _start_cspect_update_install(info):
        """Download + extract the newer CSpect build found by the startup
        check, logging every phase (download %, extraction, final extracted
        path) to the SD Card log window. On success re-runs emulator
        detection so the new build is adopted without a restart; on failure a
        detailed reason is logged and shown in a dialog."""
        if getattr(host, "_cspect_update_installing", False):
            return
        host._cspect_update_installing = True
        game = info.get("game") or {"url": CSPECT_ITCH_URL, "title": "CSpect"}
        api_key = (configuration_dictionary.get(SETTING_ITCHIO_API_KEY, "")
                   or "").strip()
        latest_name = info.get("version_name") or "the latest build"
        filename = info.get("filename") or ""
        dest_dir = _cspect_update_dest_dir()

        add_main_log_window(
            f"CSpect update ▸ Starting download + install of {latest_name} "
            f"({filename or 'archive'}) from itch.io into {dest_dir}.")

        # Marshal worker-thread log lines to the UI thread. Reuses
        # MameInstallSignals (its 'status' str signal); kept on self so the
        # queued emits survive until delivered (same rationale as the MAME
        # install worker). add_main_log_window touches the QListWidget so it
        # MUST run on the UI thread — hence the queued connection.
        sig = MameInstallSignals()
        sig.status.connect(lambda line: add_main_log_window(line),
                           Qt.QueuedConnection)
        host._cspect_update_signals = sig

        def _log_cb(line):
            try:
                sig.status.emit(str(line))
            except RuntimeError:
                pass

        def _progress_cb(read, total):
            try:
                if total:
                    pct = min(100, int(read * 100 / total))
                    sig.status.emit(
                        f"CSpect update ▸ downloading… {pct}% "
                        f"({read / 1048576:.1f}/{total / 1048576:.1f} MB)")
                else:
                    sig.status.emit(
                        "CSpect update ▸ downloading… "
                        f"{read / 1048576:.1f} MB")
            except RuntimeError:
                pass

        # Install exactly the build the update check identified (itch.io
        # lists several CSpect versions; info carries the newest upload + its
        # download key), so there's no second lookup and no chance of a
        # mismatch with the version named in the prompt.
        _chosen_upload = (info.get("uploads") or [None])[0]
        _chosen_key_id = info.get("key_id")

        def _job():
            return zxnu_itchio.install_cspect_update(
                game, api_key, dest_dir,
                log_cb=_log_cb, progress_cb=_progress_cb,
                upload=_chosen_upload, key_id=_chosen_key_id)

        def _ok(extracted):
            host._cspect_update_installing = False
            add_main_log_window(
                f"CSpect update ▸ SUCCESS — {latest_name} extracted to: "
                f"{extracted}")
            # Adopt the freshly-installed build: drop the current CSpect path
            # so the rescan re-selects the newest one (find_emulators_in_
            # downloads now prefers the highest version folder). Also re-picks
            # up the hdfmonkey bundled with the new CSpect.
            host._cspect_executable_path = None
            host._cspect_from_downloads = False
            _rescan = getattr(host, "_rescan_emulators_after_install", None)
            if _rescan is not None:
                try:
                    _rescan()
                except Exception as exc:
                    logging.info(f"CSpect update rescan failed: {exc}")
            try:
                host._show_toast(
                    "✅  CSpect updated",
                    ui_tr_now(
                        "CSpect {name} is installed — no restart "
                        "needed.\r\n{extracted}").format(
                            name=latest_name, extracted=extracted),
                    variant="green", duration_ms=9000)
            except Exception:
                pass

        def _err(err):
            host._cspect_update_installing = False
            detail = (err[1] if isinstance(err, (tuple, list)) and len(err) > 1
                      else err)
            add_main_log_window(f"CSpect update ▸ FAILED — {detail}")
            logging.error(f"CSpect update failed: {detail}")
            try:
                QMessageBox.warning(
                    host, "CSpect update failed",
                    "The CSpect update could not be completed.\n\n"
                    f"{detail}\n\n"
                    "You can retry later, or install CSpect manually from the "
                    "itch.io tab (https://mdf200.itch.io/cspect).")
            except Exception:
                pass

        getit_run_in_thread(_job, _ok, _err)

    def _prompt_cspect_update(info):
        """UI-thread dialog offering to update CSpect to the newer itch.io
        build found by the startup check. 'Yes' downloads + installs it;
        'Cancel' leaves the current build untouched."""
        installed_name = info.get("installed_name") or "the current build"
        latest_name = info.get("version_name") or "a newer build"
        box = QMessageBox(host)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(ui_tr_now("CSpect update available"))
        box.setText(
            "A newer version of CSpect is available on itch.io.\n\n"
            f"Installed: {installed_name}\n"
            f"Latest: {latest_name}\n\n"
            "Download and install the newest version now?")
        _attach_release_notes(box, info.get("notes"))
        yes = box.addButton(ui_tr_now("Yes"), QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(yes)
        box.exec()
        if box.clickedButton() is yes:
            add_main_log_window(
                f"CSpect update ▸ user chose to update to {latest_name}.")
            _start_cspect_update_install(info)
        else:
            add_main_log_window(ui_tr_now(
                "CSpect update ▸ user cancelled the update."))

    def _check_cspect_update_async():
        """At startup — once an itch.io API key is configured and the check is
        enabled — ask itch.io for the newest CSpect build and, when it is
        newer than the installed one, offer to download + install it.

        Self-gates and never disrupts startup: any failure is logged to the
        SD Card log window / Python log and swallowed. Runs once per session
        (self._cspect_update_checked), so the two triggers — the startup timer
        and the itch.io post-login callback — don't both fire it. The slow
        parts (the installed-build disk scan and the itch.io API lookup) run
        off the UI thread; the prompt runs back on the UI thread."""
        if getattr(host, "_cspect_update_checked", False):
            return
        if getattr(host, "_cspect_update_installing", False):
            return
        pref = configuration_dictionary.get(
            SETTING_CSPECT_UPDATE_CHECK, "").strip().lower()
        if pref in ("false", "0", "no"):
            return  # user disabled the check in Settings
        api_key = (configuration_dictionary.get(SETTING_ITCHIO_API_KEY, "")
                   or "").strip()
        if not api_key:
            return  # no itch.io account configured — nothing to check
        # Passed the cheap gates. Mark done so the other trigger no-ops, then
        # do the heavier work (disk scan + authenticated itch.io lookup) on a
        # worker thread. The authenticated lookup doubles as the "login
        # succeeded" confirmation — nothing is offered unless it returns.
        host._cspect_update_checked = True
        app_dir = ZXNU_DATA_ROOT

        def _job():
            installed_name, installed_exe = find_installed_cspect_version(app_dir)
            if not installed_name:
                return {"skip": "no itch.io CSpect install was found to update"}
            info = zxnu_itchio.latest_cspect_upload(api_key)
            if not info:
                return {"skip": "itch.io lists no CSpect download for this "
                                "account (not owned, no download key, or "
                                "only BETA builds — betas are never "
                                "offered as updates)"}
            info = dict(info)
            info["installed_name"] = installed_name
            info["installed_exe"] = installed_exe
            info["newer"] = cspect_version_newer(
                info.get("version_name") or "", installed_name)
            return info

        def _on_result(info):
            try:
                skip = info.get("skip")
                if skip:
                    add_main_log_window(f"CSpect update check: {skip}.")
                    return
                installed_name = info.get("installed_name")
                latest_name = info.get("version_name")
                if not info.get("newer"):
                    add_main_log_window(ui_tr_now(
                        "CSpect is up to date (installed {installed}, "
                        "latest {latest})."
                    ).format(installed=installed_name, latest=latest_name))
                    return
                add_main_log_window(
                    f"CSpect update ▸ newer build available: installed "
                    f"{installed_name}, latest {latest_name}.")
                # itch.io's download API carries no per-build changelog, so
                # point at the CSpect itch.io page where the release notes
                # live (the shared helper shows this as the "what's changed"
                # section, mirroring MAME / ZX Next Unite).
                _game_url = ((info.get("game") or {}).get("url")
                             or zxnu_itchio.CSPECT_ITCH_URL)
                info["notes"] = (
                    f"New CSpect build: {latest_name}\n\n"
                    "itch.io does not publish inline release notes for CSpect; "
                    "the full changelog is on the CSpect itch.io page:\n"
                    f"{_game_url}")
                _prompt_cspect_update(info)
            except Exception as exc:
                logging.info(f"CSpect update check result handling failed: {exc}")

        def _on_error(err):
            detail = (err[1] if isinstance(err, (tuple, list)) and len(err) > 1
                      else err)
            add_main_log_window(ui_tr_now(
                "CSpect update check skipped: {reason}").format(reason=detail))
            logging.info(f"CSpect update check skipped: {detail}")

        add_main_log_window(ui_tr_now(
            "Checking itch.io for a newer CSpect release…"))
        getit_run_in_thread(_job, _on_result, _on_error)

    # Expose so the startup sequence (and the itch.io post-login callback)
    # can kick the check once the config (enable flag + API key) has loaded.
    host._check_cspect_update_async = _check_cspect_update_async


    # Expose the emulator launch helpers so other UI surfaces (e.g. the
    # GalleryItemViewer action bars on GetIt / ZXDB / ZxArt) can trigger
    # the same launch logic as the main window buttons.
    host._launch_cspect_fn = launch_cspect
    host._launch_mame_fn   = launch_mame

    def _wire_viewer_emulators(viewer, allow=True):
        """Add "Launch CSpect" / "Launch Mame" buttons to a
        GalleryItemViewer action bar (under "Send to SD card").

        A button is only wired/shown when the matching emulator is
        launchable *and* ``allow`` is True.  ``allow`` lets the ZXDB/ZxArt
        panes honour their ENABLE_DOWNLOAD_BUTTONS settings; GetIt passes the
        default (always allowed). MAME counts as launchable when a binary was
        detected or the Flatpak launch option is on, and its label/tooltip
        reflect the Flatpak mode — evaluated per call, so a viewer opened
        after toggling Flatpak picks up the current state.

        A shown button is *enabled* only when the emulator can actually
        start right now, mirroring the SD Card tab gating: CSpect needs the
        mounted image (its -mmc= comes from the hdfmonkey listing), MAME
        needs a valid image *file* selected (it boots the image directly).
        With no image ready the buttons stay visible but greyed out, with a
        'load an image first' tooltip — in both the Qt (Classic) and pygame
        (Retro) viewers, which share this set_emulator_actions API."""
        cspect_ok = bool(allow) and getattr(host, "_cspect_executable_path", None) is not None
        mame_ok   = bool(allow) and host._mame_usable()
        _flatpak  = host._mame_flatpak_enabled()
        _img_mounted = bool(_right_disk_content())
        try:
            _img = (host.imageinput.currentText() or "").strip().strip('"')
            _img_file = bool(_img) and os.path.isfile(_img)
        except (RuntimeError, AttributeError):
            _img_file = False
        viewer.set_emulator_actions(
            cspect_cb=(host._launch_cspect_fn if cspect_ok else None),
            mame_cb=(host._launch_mame_fn if mame_ok else None),
            cspect_enabled=cspect_ok and _img_mounted,
            mame_enabled=mame_ok and _img_file,
            cspect_tooltip=("🕹  Launch CSpect with the loaded SD card image"
                            if _img_mounted else
                            "Load a ZX Spectrum Next disk image first (SD Card "
                            "tab) — then CSpect can boot it from the mounted "
                            "SD card."),
            mame_tooltip=((("🕹  Launch MAME (via Flatpak) with the loaded image"
                            if _flatpak
                            else "🕹  Launch MAME with the loaded image"))
                          if _img_file else
                          "Select a ZX Spectrum Next disk image (.img/.hdf) "
                          "first (SD Card tab) — then MAME can boot it as "
                          "the Next's hard disk."),
            mame_label=host._mame_launch_label(),
        )
    host._wire_viewer_emulators = _wire_viewer_emulators

    # Expose the closures the rest of __init__ wires to widgets by bare name
    # (re-bound to bare locals at the call site). launch_cspect/launch_mame
    # are already exposed above as host._launch_cspect_fn/_launch_mame_fn.
    host.set_cspect_screen_size = set_cspect_screen_size
    host.set_cspect_sound_on_off = set_cspect_sound_on_off
    host.set_cspect_vsync_on_off = set_cspect_vsync_on_off
    host.set_cspect_joystick_on_off = set_cspect_joystick_on_off
    host.set_cspect_mouse_on_off = set_cspect_mouse_on_off
    host.set_cspect_display_frequency = set_cspect_display_frequency
    host.set_cspect_esc = set_cspect_esc
    host.set_mame_aspect = set_mame_aspect
    host.set_mame_sound = set_mame_sound
    host.set_mame_mouse = set_mame_mouse
    host.set_mame_joystick = set_mame_joystick
    host.set_mame_esc = set_mame_esc
    host.open_cspect_configuration_file = open_cspect_configuration_file
    host.install_mame = install_mame


def build_hdfmonkey_install_ops(
    host,
    *,
    _update_mame_controls,
    _update_cspect_controls,
    _start_hdfmonkey_button_animation,
    _stop_hdfmonkey_button_animation,
    load_image,
    add_main_log_window,
):
    """Define the hdfmonkey download/install/discovery chain (no widgets).

    The jjjs zip-inside-a-zip download + per-platform extract, the manual-
    download fallback prompt, the install-button flow, binary discovery
    (_hdfmonkey_binary_found — note os.path.dirname(__file__) still resolves
    to the app directory: every module ships side by side in source,
    PyInstaller and pip layouts alike) and the missing-hdfmonkey prompt
    signal wiring.
    """
    def _hdfmonkey_downloads_root():
        """Top-level ``downloads`` folder next to the app (created if needed).
        This is where the auto-download saves the jjjs zip and where the
        manual-fallback flow asks the user to drop a hand-downloaded copy."""
        app_dir = ZXNU_DATA_ROOT
        root = os.path.join(app_dir, DOWNLOADS_ROOT_DIRNAME)
        os.makedirs(root, exist_ok=True)
        return root

    def _download_hdfmonkey_zip():
        """STEP 1 — download the jjjs hdfmonkey archive from
        HDF_MONKEY_JJJS_URL into the downloads folder.

        Logs each stage (URL, HTTP status, content type/length, bytes read,
        save location) so a failure points at the real cause. Returns the
        saved zip path on success, or None on failure — importantly it detects
        the common specnext.com case where an HTML login / anti-robot page is
        returned with a 200 status instead of the actual attachment, rather
        than letting that non-zip flow through to a confusing extract error.
        """
        downloads_root = _hdfmonkey_downloads_root()
        dest_zip = os.path.join(downloads_root, HDF_MONKEY_JJJS_ZIP_FILENAME)
        add_main_log_window(f"hdfmonkey: [1/2] downloading archive from {HDF_MONKEY_JJJS_URL} ...")
        logging.info(f"hdfmonkey download: requesting {HDF_MONKEY_JJJS_URL}")
        try:
            req = urllib.request.Request(
                HDF_MONKEY_JJJS_URL, headers={"User-Agent": ZXART_USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                content_type = resp.headers.get("Content-Type", "") or ""
                content_length = resp.headers.get("Content-Length", "") or ""
                final_url = resp.geturl()
                add_main_log_window(
                    f"hdfmonkey: server responded HTTP {status}; "
                    f"Content-Type='{content_type}', "
                    f"Content-Length='{content_length or 'unknown'}'.")
                logging.info(
                    f"hdfmonkey download: HTTP {status} type={content_type!r} "
                    f"len={content_length!r} final_url={final_url!r}")
                data = resp.read()
        except urllib.error.HTTPError as e:
            add_main_log_window(
                f"hdfmonkey: download failed — server returned HTTP {e.code} "
                f"{e.reason}. specnext.com may require a forum login or an "
                f"anti-robot confirmation before the file can be downloaded.")
            logging.error(f"hdfmonkey download HTTPError: {e.code} {e.reason}")
            return None
        except urllib.error.URLError as e:
            add_main_log_window(
                f"hdfmonkey: download failed — could not reach "
                f"{HDF_MONKEY_JJJS_URL} ({e.reason}). Check your internet "
                f"connection, or a firewall/proxy blocking the request.")
            logging.error(f"hdfmonkey download URLError: {e.reason}")
            return None
        except Exception as e:
            add_main_log_window(f"hdfmonkey: download failed — {e}")
            logging.error(f"hdfmonkey download error: {e}")
            return None

        add_main_log_window(f"hdfmonkey: downloaded {len(data):,} bytes.")
        logging.info(f"hdfmonkey download: read {len(data)} bytes")

        # Guard against the forum returning an HTML login / anti-robot / error
        # page (often with a 200 status) instead of the real attachment. A
        # genuine zip starts with the 'PK' local-file-header magic.
        if data[:2] != b"PK":
            head = data[:200].decode("utf-8", "replace").strip()
            head = " ".join(head.split())
            lower = data[:1024].lower()
            looks_html = b"<html" in lower or b"<!doctype" in lower
            if looks_html:
                add_main_log_window(
                    "hdfmonkey: the server returned a web page, not the zip "
                    "file — this usually means specnext.com is asking for a "
                    "login or an anti-robot confirmation before the download "
                    "starts.")
            else:
                add_main_log_window(
                    "hdfmonkey: the downloaded data is not a zip file "
                    f"(it begins with: {head!r}).")
            logging.error(
                f"hdfmonkey download: not a zip (html={looks_html}); "
                f"head={head!r}")
            return None

        try:
            with open(dest_zip, "wb") as f:
                f.write(data)
        except OSError as e:
            add_main_log_window(
                f"hdfmonkey: could not save the downloaded zip to "
                f"{dest_zip} — {e}")
            logging.error(f"hdfmonkey download save error: {e}")
            return None

        # Verify the pinned SHA-256 BEFORE anything touches the archive:
        # a corrupted or tampered download is refused, not extracted.
        actual = sha256_of_file(dest_zip)
        if actual.lower() != HDF_MONKEY_JJJS_SHA256:
            add_main_log_window(
                "hdfmonkey: SHA-256 mismatch on the downloaded archive "
                f"(expected {HDF_MONKEY_JJJS_SHA256[:12]}…, got {actual[:12]}…) "
                "— refusing to extract it. Retry the download, or install "
                "hdfmonkey manually (see the wiki).")
            logging.error(
                f"hdfmonkey download hash mismatch: expected "
                f"{HDF_MONKEY_JJJS_SHA256}, got {actual}")
            try:
                os.remove(dest_zip)
            except OSError:
                pass
            return None
        add_main_log_window(
            f"hdfmonkey: saved archive to {dest_zip} (SHA-256 verified).")
        logging.info(f"hdfmonkey download: saved {dest_zip}")
        return dest_zip

    def _install_hdfmonkey_from_zip(zip_path, keep_zip=False):
        """STEP 2 — extract this platform's hdfmonkey binary out of a jjjs
        archive (auto-downloaded or manually dropped) into
        downloads/hdfmonkey/<platform>/.

        The extracted binary is recorded so execute_hdf_monkey prefers it and
        re-discovered on the next launch by find_hdfmonkey_in_downloads (so
        this works on Windows, Linux and macOS). Returns the installed binary
        path on success, or None on failure. ``keep_zip`` leaves a
        user-provided archive in place; an auto-downloaded one is removed once
        it has been unpacked.
        """
        app_dir = ZXNU_DATA_ROOT
        dest_root = os.path.join(app_dir, DOWNLOADS_HDFMONKEY_DIRNAME)
        add_main_log_window(
            f"hdfmonkey: [2/2] extracting the build for this platform from "
            f"{zip_path} ...")
        logging.info(f"hdfmonkey install: extracting from {zip_path}")
        try:
            hdfmonkey_path = extract_hdfmonkey_from_jjjs_zip(zip_path, dest_root)
        except Exception as e:
            add_main_log_window(
                f"hdfmonkey: could not extract the binary from {zip_path} — "
                f"{e}. The archive may be incomplete or corrupted; try "
                f"downloading it again.")
            logging.error(f"hdfmonkey install extract error: {e}")
            return None
        finally:
            if not keep_zip:
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
        add_main_log_window(f"hdfmonkey: extracted binary to {hdfmonkey_path}.")
        logging.info(f"hdfmonkey install: extracted {hdfmonkey_path}")
        return hdfmonkey_path

    def _finish_hdfmonkey_install(hdfmonkey_path):
        """Common UI updates once hdfmonkey has been installed (whether via
        the automatic download or the manual-drop fallback)."""
        host._hdfmonkey_executable_path = hdfmonkey_path
        host.button_new_folder.setVisible(True)
        host.button_rename.setVisible(True)
        host.button_delete_files.setVisible(True)
        host.download_and_install_hdfmonkey_button.setVisible(False)
        # hdfmonkey is now installed — stop the yellow attention pulse and
        # restore the button's normal look straight away.
        _stop_hdfmonkey_button_animation()
        logging.info(f"Successfully installed hdfmonkey: {hdfmonkey_path}")
        add_main_log_window(f"Successfully installed hdfmonkey: {hdfmonkey_path}")

        # Confirm the install with a green toast (like the emulator detection
        # one) showing where the binary landed on disk.
        host._show_hdfmonkey_installed_toast(hdfmonkey_path)

        # Reload the currently-selected image straight away so the file
        # explorer repopulates without the user having to reopen it via
        # "Select NextZXOS disk Image". The extract succeeded and
        # _hdfmonkey_executable_path now points at a verified binary, so
        # there's no need to re-probe first; load_image() restores the
        # controls once the (async) listing completes, and safely no-ops when
        # no image is selected.
        load_image()

    def _try_install_hdfmonkey_from_manual_zip():
        """Look for a jjjs hdfmonkey zip the user dropped into the downloads
        folder and, if a valid one is found, install from it. Returns True on
        a successful install."""
        app_dir = ZXNU_DATA_ROOT
        manual_zip = find_hdfmonkey_jjjs_zip_in_downloads(app_dir)
        if not manual_zip:
            return False
        add_main_log_window(
            f"hdfmonkey: found a manually-downloaded archive at "
            f"{manual_zip} — installing from it.")
        logging.info(f"hdfmonkey: installing from manual zip {manual_zip}")
        # Leave a hand-placed archive in place so the user can retry offline.
        hdfmonkey_path = _install_hdfmonkey_from_zip(manual_zip, keep_zip=True)
        if hdfmonkey_path:
            _finish_hdfmonkey_install(hdfmonkey_path)
            return True
        return False

    def _prompt_manual_hdfmonkey_download():
        """After an automatic download failure, invite the user to fetch the
        zip in a browser and drop it into the downloads folder, then retry
        detecting it from there. Returns True if a manually-dropped zip was
        subsequently found and installed."""
        downloads_root = _hdfmonkey_downloads_root()
        box = QMessageBox(host)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(ui_tr_now("hdfmonkey download failed"))
        box.setText(
            "The automatic hdfmonkey download from specnext.com failed — the "
            "forum may be asking for a login or an anti-robot confirmation "
            "before the download can start (see the log for details).\n\n"
            "You can install it manually instead:\n"
            f"1. Click 'Open download page' below (or browse to\n"
            f"    {HDF_MONKEY_JJJS_URL} ).\n"
            "2. Download the hdfmonkey .zip file.\n"
            f"3. Drop the downloaded .zip into this EXACT folder — the app "
            f"has already created it, and the 'Open downloads folder' button "
            f"below opens it so nothing needs to be typed:\n"
            f"    {downloads_root}\n"
            "4. Click \"I've dropped the zip - try again\".")
        open_page_btn = box.addButton("Open download page", QMessageBox.ActionRole)
        open_folder_btn = box.addButton("Open downloads folder", QMessageBox.ActionRole)
        retry_btn = box.addButton("I've dropped the zip - try again", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(retry_btn)
        while True:
            box.exec()
            clicked = box.clickedButton()
            if clicked is open_page_btn:
                try:
                    webbrowser.open(HDF_MONKEY_JJJS_URL)
                    add_main_log_window(
                        f"hdfmonkey: opened {HDF_MONKEY_JJJS_URL} in your "
                        f"browser. Save the .zip into {downloads_root}, then "
                        f"click 'try again'.")
                except Exception as e:
                    add_main_log_window(
                        f"hdfmonkey: could not open the browser automatically "
                        f"({e}). Please browse to {HDF_MONKEY_JJJS_URL} "
                        f"manually.")
                continue  # reshow the dialog so the user can retry afterwards
            if clicked is open_folder_btn:
                try:
                    if sys.platform == "win32":
                        os.startfile(downloads_root)
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", downloads_root])
                    else:
                        subprocess.Popen(["xdg-open", downloads_root])
                except Exception as e:
                    add_main_log_window(
                        f"hdfmonkey: could not open {downloads_root} ({e}).")
                continue
            if clicked is retry_btn:
                if _try_install_hdfmonkey_from_manual_zip():
                    return True
                add_main_log_window(
                    f"hdfmonkey: no valid hdfmonkey .zip found in "
                    f"{downloads_root} yet. Download it from "
                    f"{HDF_MONKEY_JJJS_URL} and drop the .zip there, then try "
                    f"again.")
                continue  # let the user place the file and retry
            return False  # Cancel / dialog closed

    def download_and_install_hdflonkey():
        """Install hdfmonkey in two explicit, individually-logged steps:
        download the jjjs archive, then extract this platform's binary. If a
        valid archive is already sitting in the downloads folder (e.g. the
        user dropped one after a previous failure) it is used directly; if the
        automatic download fails, the manual browser-download fallback is
        offered."""
        # Use an already-present (manually-dropped) archive first — this makes
        # a retry after the manual route succeed without hitting the network
        # again, and keeps the whole flow working when specnext.com blocks us.
        if _try_install_hdfmonkey_from_manual_zip():
            return True

        zip_path = _download_hdfmonkey_zip()
        if not zip_path:
            # Download blocked/failed — offer the manual browser route (drop
            # the zip into downloads, then detect it from there).
            return _prompt_manual_hdfmonkey_download()

        hdfmonkey_path = _install_hdfmonkey_from_zip(zip_path, keep_zip=False)
        if not hdfmonkey_path:
            # A file downloaded but could not be unpacked — the cleanest
            # recovery is a fresh (manual) download.
            add_main_log_window(
                "hdfmonkey: you can install it manually from "
                "https://github.com/gasman/hdfmonkey , or (recommended) do a "
                "full CSpect install from the itch.io tab, which also bundles "
                "hdfmonkey.")
            return _prompt_manual_hdfmonkey_download()

        _finish_hdfmonkey_install(hdfmonkey_path)
        return True

    def _on_hdfmonkey_button_clicked():
        """Button handler for "Download and install HDF Monkey". Shows an
        intermediary tip about the itch.io end-to-end CSpect install (which
        also bundles hdfmonkey) before running the standalone hdfmonkey
        download, so the user can choose the fuller route instead."""
        box = QMessageBox(host)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(ui_tr_now("Install hdfmonkey"))
        box.setText(
            "TIP: Did you know that if you have purchased CSpect from "
            "itch.io you can do a full end-to-end CSpect install from "
            "there?\n\n"
            "Simply log into your itch.io account in the itch.io tab, "
            "navigate to CSpect and click Install.\n\n"
            "Do you still want to install hdfmonkey only, or abort and then "
            "make an end-to-end install of CSpect using itch.io?")
        continue_btn = box.addButton(
            "Continue hdfmonkey standalone install", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(continue_btn)
        box.exec()
        if box.clickedButton() is continue_btn:
            return download_and_install_hdflonkey()
        return False

    def show_hdf_monkey_download_and_install_buttons():
        host.download_and_install_hdfmonkey_button.setVisible(True)
        host.button_new_folder.setVisible(False)
        host.button_rename.setVisible(False)
        host.button_delete_files.setVisible(False)
        # Draw the eye to the install button with a yellow 'breathing' pulse
        # while hdfmonkey is missing; it stops itself once the button is
        # hidden (i.e. hdfmonkey has been installed/detected).
        _start_hdfmonkey_button_animation()
        # hdfmonkey is confirmed missing here, so the file explorer stays
        # disabled — but MAME doesn't need hdfmonkey, so make sure its launch
        # button and option combos reflect (MAME present + a valid image)
        # rather than staying stuck disabled. The CSpect option combos don't
        # need hdfmonkey either (only Launch does, via the mounted image),
        # so refresh them too when a CSpect build is present.
        _update_mame_controls()
        _update_cspect_controls()

    def _hdfmonkey_binary_found():
        """True if the hdfmonkey executable can be located (PATH, current
        directory, the application directory, or a bundled copy discovered
        under downloads/cspect). Used to tell a genuine hdfmonkey error apart
        from "it isn't installed", without running it."""
        override = getattr(host, "_hdfmonkey_executable_path", None)
        if override and os.path.isfile(override):
            return True
        # A copy left by the standalone auto-download (downloads/hdfmonkey/
        # <platform>/) counts too, even before startup re-adopts it as the
        # active override (e.g. right after a fresh launch).
        try:
            if find_hdfmonkey_in_downloads(ZXNU_DATA_ROOT):
                return True
        except Exception:
            pass
        if shutil.which(HDFMONKEY_EXECUTABLE):
            return True
        names = [HDFMONKEY_EXECUTABLE]
        if platform.system() == "Windows":
            names.append(HDFMONKEY_EXECUTABLE + ".exe")
        search_dirs = [os.getcwd(), ZXNU_DATA_ROOT]
        try:
            search_dirs.append(os.path.dirname(os.path.abspath(sys.argv[0])))
        except Exception:
            pass
        try:
            search_dirs.append(os.path.dirname(os.path.abspath(__file__)))
        except Exception:
            pass
        for d in search_dirs:
            for n in names:
                if d and os.path.isfile(os.path.join(d, n)):
                    return True
        return False

    def prompt_install_hdfmonkey():
        """Offer to install hdfmonkey when it appears to be missing, on every
        platform. The jjjs auto-download ships fixed builds for Windows, Linux
        and macOS, so this runs the same "Install hdfmonkey" tip box + download
        flow as the SD-card tab button (which also points the user at the
        fuller end-to-end CSpect install via itch.io). Runs on the UI thread
        (invoked via the missing-signal so it is safe from workers)."""
        if _on_hdfmonkey_button_clicked():
            # Installed OK — allow a fresh prompt if it ever breaks again.
            host._hdfmonkey_prompt_shown = False

    # Marshals the "hdfmonkey is missing" prompt onto the UI thread: the
    # signal may be emitted from a worker thread (uploads/deletes), so the
    # dialog must not be created inline there.
    host._hdfmonkey_prompt_shown = False
    host._hdfmonkey_missing_signals = HdfMonkeyMissingSignals()
    host._hdfmonkey_missing_signals.missing.connect(prompt_install_hdfmonkey)

    # Expose the closures the rest of __init__ wires by bare name (re-bound
    # to bare locals at the call site).
    host._hdfmonkey_binary_found = _hdfmonkey_binary_found
    host.show_hdf_monkey_download_and_install_buttons = (
        show_hdf_monkey_download_and_install_buttons)
    host._on_hdfmonkey_button_clicked = _on_hdfmonkey_button_clicked
