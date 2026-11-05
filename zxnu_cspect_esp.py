"""The CSpect side of the RS232 ESP emulation: the UARTRS232 plugin.

MAME reaches the ESP emulation (``espemu.py``) through its own serial
plumbing (``-rs232_esp null_modem -bitb socket...``). CSpect has no such
switch, but it loads plugins: ``UARTRS232.dll`` (source under
``extra/UARTRS232/``) takes the emulated Next's ESP UART ports and moves their
bytes over a TCP socket to the very same espemu worker, so one emulation
serves both emulators and a CSpect session is just another session of it.

The name is deliberate: UARTReplacement is Robin Verhagen-Guest's plugin for
a REAL ESP on a serial port, and this one must never be mistaken for it.

This module is the app's half, Qt-free so it can be tested on its own:

* the plugin binary itself, carried as ``zxnu_cspect_esp_blob`` (a generated
  module - see ``extra/UARTRS232/build_plugin.py``) so it ships in the wheel,
  the frozen executables and the Flatpak without a packaging rule of its own;
* :func:`deploy_plugin`, which puts it beside ``CSpect.exe`` (CSpect loads
  every plugin DLL in its own folder);
* the launch contract: :func:`cspect_esp_environment` builds the two
  environment variables the plugin reads. The launcher's word is final in
  both directions - an endpoint makes the plugin take the UART, ``off`` keeps
  a plugin left in the folder from taking it - so Settings decides per launch
  and the DLL never has to be removed again.

Left to itself (CSpect started by hand, no variable) the plugin is
"automatic": it takes the UART only when an ESP emulation answers on its
endpoint at startup, and steps aside for ``-com`` or an installed
UARTReplacement.

Told to be active, it OWNS the UART even with UARTReplacement installed
beside it: it registers its ports at a priority CSpect serves first
(measured - see the plugin's header). So the Settings toggle is a switch
between the emulated ESP and the real one, both DLLs left where they are;
:func:`uart_replacement_installed` only lets the launcher SAY which one this
launch got. The one thing that still refuses is ``-com=`` typed in the launch
parameters (:func:`cspect_esp_blocker`) - an explicit choice of the real
serial port, as a typed ``-rs232_esp`` card is on the MAME side.
"""
import base64
import hashlib
import os
import re

CSPECT_ESP_PLUGIN_FILE = "UARTRS232.dll"
CSPECT_ESP_PLUGIN_CFG = "UARTRS232.cfg"
CSPECT_ESP_ENV = "ZX_NEXT_UNITE_ESP"
CSPECT_ESP_ENV_VERBOSE = "ZX_NEXT_UNITE_ESP_VERBOSE"
CSPECT_ESP_ENV_OFF = "off"
CSPECT_UART_REPLACEMENT_FILE = "UARTReplacement.dll"

# deploy_plugin's verdicts.
DEPLOY_CURRENT = "current"        # the folder already holds these bytes
DEPLOY_INSTALLED = "installed"    # there was none; written
DEPLOY_UPDATED = "updated"        # another build was there; replaced
DEPLOY_FAILED = "failed"          # could not write (detail says why)

# Why a launch must leave the UART alone (cspect_esp_blocker).
BLOCKED_BY_COM = "com"

_VERSION_RE = re.compile(r'PluginVersion\s*=\s*"(\d+\.\d+\.\d+)"')
# "-com=COM5:115200" hands the UART to a real serial port. "-com2=" is the Pi
# UART, which the plugin never touches.
_COM_TOKEN_RE = re.compile(r'(?:^|\s)"?-com(?:=|\s|"|$)', re.IGNORECASE)


def plugin_banner(version):
    """The bytes a built DLL of *version* must contain: the plugin's Banner
    literal, which .NET stores as UTF-16."""
    return ("UARTRS232 " + str(version)).encode("utf-16-le")


def plugin_source_version(path):
    """``PluginVersion`` as written in the C# source, or ""."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            found = _VERSION_RE.search(handle.read())
    except OSError:
        return ""
    return found.group(1) if found else ""


def plugin_source_digest(paths):
    """A digest of the plugin's source files, the same on every checkout:
    line endings are normalised first, because git hands Windows CRLF and
    Linux LF for the same commit."""
    digest = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as handle:
            data = handle.read().replace(b"\r\n", b"\n")
        digest.update(os.path.basename(path).encode("utf-8") + b"\0")
        digest.update(data)
    return digest.hexdigest()


def plugin_version():
    """The version of the plugin build the app carries."""
    import zxnu_cspect_esp_blob as blob
    return blob.PLUGIN_VERSION


def plugin_bytes():
    """The plugin DLL the app carries, verified against its recorded size
    and digest - a damaged copy must never be written into a CSpect folder,
    where it would fail (or worse, half work) inside someone else's
    process."""
    import zxnu_cspect_esp_blob as blob
    data = base64.b64decode(blob.PLUGIN_BASE64)
    if (len(data) != blob.PLUGIN_SIZE
            or hashlib.sha256(data).hexdigest() != blob.PLUGIN_SHA256):
        raise ValueError("the bundled CSpect UARTRS232 plugin is damaged")
    return data


def deploy_plugin(folder, data=None):
    """Put the plugin beside CSpect.exe. Returns ``(verdict, detail)``.

    Idempotent: a folder that already holds these exact bytes is left alone
    (``DEPLOY_CURRENT``). Written through a temporary file and one rename,
    so a CSpect starting at that moment never loads half a DLL - and a
    CSpect already running is not disturbed either: CSpect loads its plugins
    from memory (measured: the assembly reports no file location), so the
    file is not held open. Never raises: a folder that cannot be written
    (a read-only install) is a ``DEPLOY_FAILED`` with the reason, and the
    caller decides what a launch without a fresh plugin means.
    """
    try:
        if data is None:
            data = plugin_bytes()
        target = os.path.join(folder, CSPECT_ESP_PLUGIN_FILE)
        existed = os.path.isfile(target)
        if existed:
            with open(target, "rb") as handle:
                if handle.read() == data:
                    return DEPLOY_CURRENT, target
        temporary = target + ".new"
        with open(temporary, "wb") as handle:
            handle.write(data)
        try:
            os.replace(temporary, target)
        except OSError:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
        return (DEPLOY_UPDATED if existed else DEPLOY_INSTALLED), target
    except (OSError, ValueError, TypeError) as ex:
        return DEPLOY_FAILED, str(ex)


def plugin_installed(folder):
    """True when a UARTRS232.dll (any build) sits in *folder*."""
    try:
        return os.path.isfile(os.path.join(folder, CSPECT_ESP_PLUGIN_FILE))
    except (OSError, TypeError):
        return False


def plugin_is_current(folder):
    """True when *folder* already holds exactly the build the app carries -
    there is then nothing to install, and nothing to ask about."""
    try:
        with open(os.path.join(folder, CSPECT_ESP_PLUGIN_FILE), "rb") as handle:
            return handle.read() == plugin_bytes()
    except (OSError, TypeError, ValueError):
        return False


def uart_replacement_installed(folder):
    """True when the UARTReplacement plugin sits in *folder*. Not a refusal -
    UARTRS232 answers the UART first when it is active - only what lets the
    launcher tell the user which ESP this launch uses."""
    try:
        return os.path.isfile(
            os.path.join(folder, CSPECT_UART_REPLACEMENT_FILE))
    except (OSError, TypeError):
        return False


def cspect_esp_endpoint(port, host="127.0.0.1"):
    """The value of ``ZX_NEXT_UNITE_ESP`` naming a running emulation."""
    return f"{host}:{int(port)}"


def cspect_esp_environment(endpoint, verbose=False, base=None):
    """The environment for a CSpect launch.

    *endpoint* is :func:`cspect_esp_endpoint`'s value to make the plugin take
    the UART, or a false value to tell it to stay out (``off``): a plugin
    left in the CSpect folder would otherwise decide for itself, and connect
    to an emulation that happens to be running for another emulator.
    A COPY of *base* (default: this process's environment) - never the
    mapping itself.
    """
    env = dict(os.environ if base is None else base)
    env[CSPECT_ESP_ENV] = str(endpoint) if endpoint else CSPECT_ESP_ENV_OFF
    if endpoint and verbose:
        env[CSPECT_ESP_ENV_VERBOSE] = "1"
    else:
        env.pop(CSPECT_ESP_ENV_VERBOSE, None)
    return env


def cspect_esp_flatpak_env_args(env):
    """``--env=`` switches carrying the plugin's variables through
    ``flatpak-spawn --host``, which starts the host process with the HOST's
    environment, not the sandbox's."""
    return " ".join(f"--env={name}={env[name]}"
                    for name in (CSPECT_ESP_ENV, CSPECT_ESP_ENV_VERBOSE)
                    if env.get(name))


def cspect_esp_blocker(params):
    """Why this CSpect launch must not hand the UART to the plugin, or "".

    ``-com=`` typed in the launch parameters is the user's explicit choice
    of a real ESP on a serial port for this CSpect; the emulation steps
    aside rather than take the ports from under it. (``-com2=``, the Pi
    UART, is no concern of the plugin's.)
    """
    if _COM_TOKEN_RE.search(str(params or "")):
        return BLOCKED_BY_COM
    return ""
