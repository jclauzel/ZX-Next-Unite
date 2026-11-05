"""The CSpect UARTRS232 plugin and the app's half of it (9.7.58).

CSpect has no serial switch to point at the RS232 ESP emulation the way
MAME's ``-bitb`` is, but it loads plugins: ``UARTRS232.dll`` (C# source in
``extra/UARTRS232/``) takes the emulated Next's ESP UART ports and connects
them to the same ``espemu`` worker MAME uses. The app carries the built DLL
as a Python module (``zxnu_cspect_esp_blob``), puts it beside ``CSpect.exe``
at launch and tells it where the worker listens through the environment.

CI cannot build or run the plugin (no CSpect, no .NET Framework compiler on
the Linux runner), so what is checked here is everything around it:

* the carried binary is whole, is the one checked in under extra/, wears the
  version its source declares - and was BUILT FROM THAT SOURCE: the blob
  records a digest of the .cs it was compiled from, so a source edit without
  ``python extra/UARTRS232/build_plugin.py`` fails here instead of shipping
  a stale plugin (the syncdev banner gate's twin);
* ``deploy_plugin`` (install / unchanged / replace / the failures, never a
  raise, no temporary file left behind);
* the launch contract (``cspect_esp_environment``: an endpoint, or an
  explicit "off" - never silence; the Flatpak ``--env`` form);
* ``cspect_esp_blocker`` (``-com=`` is the user's real serial port and wins;
  ``-com2=``, the Pi UART, is nobody's business);
* source tripwires on the plugin - the rules that cost a failed run each
  (see the plugin's header) - and on ``launch_cspect``.

The plugin itself was proven end to end by hand on CSpect 3.3.1 and 4.5.1
(2026-11-05): megabyte round trips byte-identical through the .sync5 dot at
its fast speed and through ZX Next Remote at "fast", the Next's own CRC-32
matching. The real ``launch_cspect`` with the toggle on is driven in
offscreen phase 12 (a fake CSpect, the real worker).

Run: python tests/test_cspect_esp_plugin.py  (no pytest, matching the suite).
"""
import base64
import hashlib
import os
import re
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

import zxnu_cspect_esp as zce  # noqa: E402
import zxnu_cspect_esp_blob as blob  # noqa: E402

PLUGIN_DIR = os.path.join(REPO, "extra", "UARTRS232")
SOURCE = os.path.join(PLUGIN_DIR, "UARTRS232.cs")
BUILT = os.path.join(PLUGIN_DIR, "UARTRS232.dll")

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def test_names():
    print("--- the name: UARTRS232, never to be mistaken for UARTReplacement")
    check("the DLL is UARTRS232.dll", zce.CSPECT_ESP_PLUGIN_FILE == "UARTRS232.dll")
    check("its config file is UARTRS232.cfg", zce.CSPECT_ESP_PLUGIN_CFG == "UARTRS232.cfg")
    check("the plugin it must not be confused with is named as such",
          zce.CSPECT_UART_REPLACEMENT_FILE == "UARTReplacement.dll")
    check("the source lives under extra/UARTRS232", os.path.isfile(SOURCE))
    check("the built DLL is checked in beside it", os.path.isfile(BUILT))
    check("its build script is there too",
          os.path.isfile(os.path.join(PLUGIN_DIR, "build_plugin.py")))


def test_blob():
    print("--- the carried binary")
    data = zce.plugin_bytes()
    check("plugin_bytes() decodes to the recorded size",
          len(data) == blob.PLUGIN_SIZE, f"{len(data)} != {blob.PLUGIN_SIZE}")
    check("...and the recorded digest",
          hashlib.sha256(data).hexdigest() == blob.PLUGIN_SHA256)
    check("it is a PE file (a .NET assembly starts 'MZ')", data[:2] == b"MZ")
    with open(BUILT, "rb") as handle:
        on_disk = handle.read()
    check("the DLL checked in under extra/ is the very one the app carries",
          on_disk == data,
          "run: python extra/UARTRS232/build_plugin.py (it writes both)")
    version = zce.plugin_source_version(SOURCE)
    check("the source declares a three-part PluginVersion",
          bool(re.fullmatch(r"\d+\.\d+\.\d+", version)), repr(version))
    check("the blob carries the version its source declares",
          blob.PLUGIN_VERSION == version == zce.plugin_version(),
          f"blob {blob.PLUGIN_VERSION}, source {version}")
    check("the built DLL wears that version's banner",
          zce.plugin_banner(version) in data)
    check("...and not just any banner (a wrong version is not found)",
          zce.plugin_banner("0.0.0") not in data)
    check("THE STALE-BUILD TRIPWIRE: the blob was built from the source as "
          "it stands",
          zce.plugin_source_digest([SOURCE]) == blob.SOURCE_SHA256,
          "UARTRS232.cs changed since the DLL was built - bump PluginVersion "
          "and run: python extra/UARTRS232/build_plugin.py")
    # The digest must not depend on the checkout's line endings: git hands
    # Windows CRLF and the Linux CI runner LF for the same commit.
    scratch = tempfile.mkdtemp(prefix="zxnu-uartrs232-")
    try:
        text = read(SOURCE).replace("\r\n", "\n")
        lf = os.path.join(scratch, "lf", "UARTRS232.cs")
        crlf = os.path.join(scratch, "crlf", "UARTRS232.cs")
        for path, body in ((lf, text), (crlf, text.replace("\n", "\r\n"))):
            os.makedirs(os.path.dirname(path))
            with open(path, "wb") as handle:
                handle.write(body.encode("utf-8"))
        check("the source digest is the same for an LF and a CRLF checkout",
              zce.plugin_source_digest([lf]) == zce.plugin_source_digest([crlf])
              == blob.SOURCE_SHA256)
        with open(lf, "ab") as handle:
            handle.write(b"// one more line\n")
        check("...and moves when the source does",
              zce.plugin_source_digest([lf]) != blob.SOURCE_SHA256)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    check("the blob is plain base64 (nothing else hides in the module)",
          base64.b64encode(data).decode("ascii")
          == "".join(blob.PLUGIN_BASE64.split()))

    # A damaged copy must never be written into someone's CSpect folder.
    saved = blob.PLUGIN_SHA256
    try:
        blob.PLUGIN_SHA256 = "0" * 64
        try:
            zce.plugin_bytes()
            refused = False
        except ValueError:
            refused = True
        check("a blob that does not match its digest is refused", refused)
        verdict, _detail = zce.deploy_plugin(tempfile.gettempdir())
        check("...and deploy_plugin reports it instead of raising or writing",
              verdict == zce.DEPLOY_FAILED
              and not os.path.exists(os.path.join(
                  tempfile.gettempdir(), zce.CSPECT_ESP_PLUGIN_FILE)))
    finally:
        blob.PLUGIN_SHA256 = saved


def test_deploy():
    print("--- deploy_plugin")
    scratch = tempfile.mkdtemp(prefix="zxnu-uartrs232-")
    try:
        folder = os.path.join(scratch, "CSpect")
        os.makedirs(folder)
        target = os.path.join(folder, zce.CSPECT_ESP_PLUGIN_FILE)
        data = zce.plugin_bytes()
        check("nothing there yet", not zce.plugin_installed(folder))
        verdict, detail = zce.deploy_plugin(folder)
        with open(target, "rb") as handle:
            landed = handle.read()
        check("a first deploy installs it", verdict == zce.DEPLOY_INSTALLED
              and detail == target, f"{verdict} {detail}")
        check("...byte for byte", landed == data)
        check("plugin_installed sees it", zce.plugin_installed(folder))
        check("...and plugin_is_current says it is this very build",
              zce.plugin_is_current(folder))
        check("a second deploy changes nothing",
              zce.deploy_plugin(folder)[0] == zce.DEPLOY_CURRENT)
        with open(target, "wb") as handle:
            handle.write(b"MZ an older build")
        check("another build in the folder is installed but not current",
              zce.plugin_installed(folder) and not zce.plugin_is_current(folder))
        check("plugin_is_current of a missing or None folder is simply False",
              zce.plugin_is_current(os.path.join(scratch, "nope")) is False
              and zce.plugin_is_current(None) is False)
        verdict, _ = zce.deploy_plugin(folder)
        with open(target, "rb") as handle:
            landed = handle.read()
        check("another build in the folder is replaced",
              verdict == zce.DEPLOY_UPDATED and landed == data, verdict)
        check("no temporary file is left behind",
              sorted(os.listdir(folder)) == [zce.CSPECT_ESP_PLUGIN_FILE],
              str(os.listdir(folder)))
        gone = os.path.join(scratch, "no-such-folder")
        verdict, detail = zce.deploy_plugin(gone)
        check("a folder that is not there is a failure with a reason, not a "
              "raise", verdict == zce.DEPLOY_FAILED and bool(detail), verdict)
        check("...and creates nothing", not os.path.exists(gone))
        verdict, detail = zce.deploy_plugin(target)        # a FILE, not a folder
        check("a path that is a file fails the same way",
              verdict == zce.DEPLOY_FAILED and bool(detail), verdict)
        check("a None folder fails, never raises",
              zce.deploy_plugin(None)[0] == zce.DEPLOY_FAILED)
        check("plugin_installed(None) is simply False",
              zce.plugin_installed(None) is False)

        check("UARTReplacement is not seen where it is not",
              not zce.uart_replacement_installed(folder))
        with open(os.path.join(folder, zce.CSPECT_UART_REPLACEMENT_FILE), "wb") as handle:
            handle.write(b"MZ")
        check("...and is seen beside UARTRS232 when installed",
              zce.uart_replacement_installed(folder))
        check("uart_replacement_installed(None) is simply False",
              zce.uart_replacement_installed(None) is False)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_environment():
    print("--- the launch contract")
    endpoint = zce.cspect_esp_endpoint(2220)
    check("the endpoint names loopback and the worker's port",
          endpoint == "127.0.0.1:2220", endpoint)
    base = {"PATH": "x", zce.CSPECT_ESP_ENV_VERBOSE: "1"}
    on = zce.cspect_esp_environment(endpoint, verbose=True, base=base)
    check("an endpoint is handed over as it is",
          on[zce.CSPECT_ESP_ENV] == "127.0.0.1:2220")
    check("verbose rides along when asked", on[zce.CSPECT_ESP_ENV_VERBOSE] == "1")
    check("the rest of the environment survives", on["PATH"] == "x")
    quiet = zce.cspect_esp_environment(endpoint, verbose=False, base=base)
    check("verbose inherited from the app's own environment is dropped when "
          "not asked", zce.CSPECT_ESP_ENV_VERBOSE not in quiet)
    off = zce.cspect_esp_environment("", verbose=True, base=base)
    check("no endpoint is an EXPLICIT off, never silence (a plugin left in "
          "the folder would otherwise decide for itself)",
          off[zce.CSPECT_ESP_ENV] == zce.CSPECT_ESP_ENV_OFF == "off")
    check("...with no verbose flag", zce.CSPECT_ESP_ENV_VERBOSE not in off)
    check("the base mapping is never modified",
          base == {"PATH": "x", zce.CSPECT_ESP_ENV_VERBOSE: "1"})
    live = zce.cspect_esp_environment(endpoint)
    check("with no base it starts from this process's environment",
          all(live.get(k) == v for k, v in os.environ.items()
              if k not in (zce.CSPECT_ESP_ENV, zce.CSPECT_ESP_ENV_VERBOSE)))
    check("...as a copy", zce.CSPECT_ESP_ENV not in os.environ
          or os.environ[zce.CSPECT_ESP_ENV] != "127.0.0.1:2220")
    check("flatpak-spawn gets the endpoint as --env",
          zce.cspect_esp_flatpak_env_args(on)
          == "--env=ZX_NEXT_UNITE_ESP=127.0.0.1:2220 "
             "--env=ZX_NEXT_UNITE_ESP_VERBOSE=1")
    check("...and the off word too",
          zce.cspect_esp_flatpak_env_args(off) == "--env=ZX_NEXT_UNITE_ESP=off")
    check("the names are the ones the plugin reads",
          f'"{zce.CSPECT_ESP_ENV}"' in read(SOURCE)
          and f'"{zce.CSPECT_ESP_ENV_VERBOSE}"' in read(SOURCE))


def test_blocker():
    print("--- cspect_esp_blocker")
    for params in ('-basickeys -zxnext -com="COM5:115200"',
                   "-com=COM5:115200 -zxnext", "-zxnext -COM=COM1",
                   '"-com=COM5"', "-zxnext -com"):
        check(f"-com wins: {params}",
              zce.cspect_esp_blocker(params) == zce.BLOCKED_BY_COM)
    for params in ("-basickeys -zxnext", "", None, "-com2=COM3 -zxnext",
                   "-zxnext -compact", "-welcome -zxnext", "-r=-com"):
        check(f"no real serial port: {params!r}",
              zce.cspect_esp_blocker(params) == "")


def test_plugin_source():
    print("--- the plugin's source: the rules that cost a failed run each")
    src = read(SOURCE)
    code = "\n".join(line.split("//")[0] for line in src.splitlines())
    check("the class implements CSpect's iPlugin", ": iPlugin" in src)
    check("it carries the two members CSpect 4.x added to the interface",
          "public string GetName()" in src and "public string GetDescription()" in src)
    check("the banner is 'UARTRS232 <version>'",
          'Banner = "UARTRS232 " + PluginVersion' in src)
    # C# 5 only: the compiler every Windows ships understands nothing newer,
    # and CI cannot compile this file to say so.
    for label, pattern in (("interpolated strings", r'\$"'),
                           ("null-conditional operators", r"\?\.|\?\["),
                           ("nameof()", r"\bnameof\s*\("),
                           ("expression-bodied members", r"\)\s*=>"),
                           ("'out var' declarations", r"\bout\s+var\b")):
        check(f"C# 5 only: no {label}", not re.search(pattern, code))
    # Priority: CSpect serves the LOWER number first (measured on 3.3.1 with
    # UARTReplacement beside it: at 0 that plugin kept the UART and drove
    # the user's real ESP while this one sat idle).
    found = re.search(r"DefaultPriority\s*=\s*(-?\d+)\s*;", src)
    check("the ports are registered below UARTReplacement's priority (0)",
          bool(found) and int(found.group(1)) < 0, found.group(0) if found else "")
    registrations = re.findall(r"new sIO\(([^;]*)\);", code)
    check("six registrations: TX write, status read, RX read, select, "
          "frame, the reset nextreg", len(registrations) == 6, str(registrations))
    check("...every one at that priority",
          all(r.rstrip(")").rstrip().endswith("priority") for r in registrations),
          str(registrations))
    check("the prescaler port (0x143B write) is left to CSpect",
          "PORT_UART_RX, eAccess.Port_Write" not in code)
    for name, value in (("PORT_UART_TX", "0x133B"), ("PORT_UART_RX", "0x143B"),
                        ("PORT_UART_SELECT", "0x153B"), ("PORT_UART_FRAME", "0x163B"),
                        ("REG_RESET", "0x02")):
        check(f"{name} is {value}",
              bool(re.search(rf"{name}\s*=\s*{value}\s*;", src)))
    # The pause after a +IPD payload: without it the .sync5 dot's drain loop
    # read straight into the next block and EVERY upload failed.
    check("the +IPD tracker notes where each payload ends",
          "gaps.Enqueue(rxWritten + i + 1)" in code)
    check("the status port answers 'nothing waiting' while that pause lasts",
          bool(re.search(r"if \(gapPolls > 0\)\s*gapPolls = gapPolls - 1;\s*"
                         r"else if \(rxCount > 0\)\s*status \|= STATUS_RX_AVAILABLE;",
                         code)))
    found = re.search(r"GapStatusReads\s*=\s*(\d+)\s*;", src)
    check("the pause is at least one status read and far under the dot's "
          "20-read wait", bool(found) and 1 <= int(found.group(1)) <= 8)
    check("a data read past the pause cancels it (software that never asks "
          "must not be starved)", "gapPolls = pause;" in code)
    check("the tracker survives a FIFO reset (the rest of a payload is "
          "still on its way)",
          "ipdState = 0" not in code.split("private void ClearRx()")[1]
          .split("private void ClearTx()")[0])
    # CSpect loads plugins from memory: Assembly.Location is "" there, and
    # Path.GetDirectoryName("") throws (first run: the cfg was never read).
    check("the plugin's folder falls back to the application base directory",
          "AppDomain.CurrentDomain.BaseDirectory" in code
          and "private static string PluginFolder()" in code)
    check("...and nothing else asks the assembly where it is",
          code.count("GetExecutingAssembly().Location") == 1)
    # The emulation thread must never wait on the network.
    check("the socket is non-blocking", "s.Blocking = false;" in code)
    check("connecting happens on the link thread only",
          code.count("s.Connect(host, port)") == 1
          and "s.Connect(host, port)" in code.split("private void LinkLoop()")[1]
          .split("private void Pump(")[0])
    check("the automatic probe is bounded",
          "WaitOne(timeoutMs)" in code and bool(re.search(r"AutoProbeMs\s*=\s*\d+", src)))
    check("an Init that throws registers nothing (CSpect must still start)",
          "ports.Clear();" in code)
    # Automatic mode is polite; told to be active it owns the UART.
    auto = code.split("// Automatic")[0] if "// Automatic" in code else ""
    auto_part = src.split("// Automatic: step aside")[1].split("private static")[0]
    check("left to itself it steps aside for -com", '"-com="' in auto_part)
    check("...and for an installed UARTReplacement",
          '"UARTReplacement.dll"' in auto_part)
    check("...and takes the UART only when an emulation answers",
          "Probe(host, port, AutoProbeMs)" in auto_part)
    del auto
    check("a machine reset empties the FIFOs but keeps the link (the ESP "
          "survives one on hardware)",
          "DropLink" not in code.split("public void Reset()")[1]
          .split("public void Quit()")[0])
    check("nextreg 2 bit 7 is the ESP's reset line",
          "(_value & 0x80) != 0" in code.split("REG_RESET)")[1][:200])


def test_launch_wiring():
    print("--- launch_cspect")
    ops = read(os.path.join(REPO, "zxnu_emulator_ops.py"))
    start = ops.index("    def launch_cspect(")
    lc = ops[start:ops.index("    def launch_mame(", start)]
    check("the setting is read at each launch",
          "cspect_rs232_esp_enabled(configuration_dictionary)" in lc)
    check("the plugin is deployed beside CSpect.exe", "deploy_plugin(_plugin_dir)" in lc)
    check("the worker is the SHARED one, started or reused",
          '_start_esp_emulation("CSpect")' in lc)
    check("the plugin is handed the port the worker ACTUALLY listens on",
          "cspect_esp_endpoint(_esp_server.port)" in lc)
    env_at = lc.index("_esp_env = cspect_esp_environment(")
    gate_at = lc.index("if cspect_rs232_esp_enabled(configuration_dictionary):")
    env_line = lc[lc.rfind("\n", 0, env_at) + 1:env_at]
    gate_line = lc[lc.rfind("\n", 0, gate_at) + 1:gate_at]
    check("the environment is built on EVERY launch - same indent as the "
          "toggle test, after it - so 'off' is always said",
          env_at > gate_at and env_line == gate_line and env_line.strip() == "",
          repr((gate_line, env_line)))
    check("...and given to Popen", '_popen_kwargs["env"] = _esp_env' in lc
          and "subprocess.Popen(_cmd, **_popen_kwargs)" in lc)
    check("Flatpak carries it as --env", "cspect_esp_flatpak_env_args(_esp_env)" in lc)
    check("-com typed in the parameters keeps the emulation out",
          "cspect_esp_blocker(cspect_default_params) == BLOCKED_BY_COM" in lc)
    check("the worker reference is taken once Popen succeeded",
          lc.index("subprocess.Popen(_cmd") < lc.index("host._esp_emu_users += 1"))
    check("...and dropped when that CSpect exits",
          "_release_esp_emulation(_esp_server)" in lc)
    check("a launch that failed takes the worker down only when nobody "
          "else uses it",
          "if _esp_server is not None and host._esp_emu_users == 0:" in lc)
    check("an installed UARTReplacement is announced, not refused",
          "uart_replacement_installed(_plugin_dir)" in lc)
    start = ops.index("    def _start_esp_emulation(")
    worker = ops[start:ops.index("host._start_esp_emulation = _start_esp_emulation", start)]
    check("the worker takes the emulator's name only to word two lines",
          'def _start_esp_emulation(emulator="MAME")' in worker
          and '_emulator_launch_failed(emulator,' in worker)

    from zxnu_config import (CONFIG_FILE_SETTINGS, SETTING_CSPECT_RS232_ESP,
                             cspect_rs232_esp_enabled)
    check("the setting is registered to be saved (an unregistered key reads "
          "but never persists)",
          SETTING_CSPECT_RS232_ESP == "cspect_rs232_esp"
          and SETTING_CSPECT_RS232_ESP in CONFIG_FILE_SETTINGS)
    check("off by default",
          not cspect_rs232_esp_enabled({}) and not cspect_rs232_esp_enabled(None)
          and not cspect_rs232_esp_enabled({SETTING_CSPECT_RS232_ESP: ""}))
    check("on when saved true",
          cspect_rs232_esp_enabled({SETTING_CSPECT_RS232_ESP: "true"})
          and not cspect_rs232_esp_enabled({SETTING_CSPECT_RS232_ESP: "false"}))

    toml = read(os.path.join(REPO, "pyproject.toml"))
    check("both modules ship in the wheel (py-modules is explicit)",
          '"zxnu_cspect_esp",' in toml and '"zxnu_cspect_esp_blob",' in toml)


def test_install_offer():
    """After an itch.io CSpect install or update the app offers the plugin
    for the build just adopted, then the Settings toggle. The dialogs are
    driven in offscreen phase 12; here, the wiring that gets it asked."""
    print("--- the offer after an itch.io CSpect install / update")
    ops = read(os.path.join(REPO, "zxnu_emulator_ops.py"))
    pane = read(os.path.join(REPO, "zxnu_itchio_pane.py"))
    main_src = read(os.path.join(REPO, "zxnu_main.py"))
    arm = "host._cspect_esp_plugin_offer_pending = True"
    check("the itch.io tab arms the offer on both CSpect install paths "
          "(downloaded build, manually picked zip)", pane.count(arm) == 2,
          str(pane.count(arm)))
    for site in pane.split(arm)[:-1]:
        check("...each beside the OpenAL notice, i.e. only for CSpect",
              "host._cspect_openal_notice_pending = True" in site[-600:])
    start = ops.index("    def _start_cspect_update_install(")
    update = ops[start:ops.index("    def _prompt_cspect_update(", start)]
    check("the update chain arms it too, before it rescans for the new build",
          arm in update and update.index(arm) < update.index("_rescan()"))
    check("the flag starts False (a plain startup detection offers nothing)",
          "self._cspect_esp_plugin_offer_pending = False" in main_src)
    start = main_src.index("    def _show_emulator_detection_toast(")
    toast = main_src[start:main_src.index("\n    def ", start + 10)]
    check("the detection toast consumes it, one-shot, once CSpect is found",
          'getattr(self, "_cspect_esp_plugin_offer_pending", False)' in toast
          and '"CSpect" in found' in toast
          and "self._cspect_esp_plugin_offer_pending = False" in toast)
    check("...and schedules the offer after the OpenAL one (400 ms)",
          "QTimer.singleShot(900, self._offer_cspect_esp_plugin)" in toast
          and "QTimer.singleShot(400, self._offer_openal_install)" in toast)
    start = ops.index("    def _offer_cspect_esp_plugin(")
    offer = ops[start:ops.index("host._offer_cspect_esp_plugin = _offer_cspect_esp_plugin", start)]
    check("the offer never opens on top of another dialog",
          "QApplication.activeModalWidget() is not None" in offer
          and "QTimer.singleShot(" in offer.split("activeModalWidget")[1][:400])
    check("...and that wait is bounded", "_tries < " in offer)
    check("it targets the CSpect in use, i.e. the build just adopted",
          'getattr(host, "_cspect_executable_path", None)' in offer)
    check("a folder that already holds this build is not asked about",
          "plugin_is_current(folder)" in offer)
    ask_install = offer.index('ui_tr_now("Install the UARTRS232 plugin?")')
    ask_enable = offer.index('ui_tr_now("Enable the RS232 ESP emulation for CSpect?")')
    declined = offer.index("if box.clickedButton() is not install_btn:")
    check("the plugin is asked about first, the Settings box second",
          ask_install < ask_enable)
    check("'not now' to the plugin returns before the box is offered",
          ask_install < declined < ask_enable
          and "return False" in offer[declined:declined + 700])
    check("the Settings box is offered only while it is off",
          "if not enabled:" in offer[:ask_enable]
          and "cspect_rs232_esp_enabled(configuration_dictionary)" in offer)
    check("ticking goes through the checkbox, whose handler saves it",
          "checkbox.setChecked(True)" in offer)
    check("the folder is escaped into the rich-text question",
          "html.escape(folder)" in offer)


def main():
    test_names()
    test_blob()
    test_deploy()
    test_environment()
    test_blocker()
    test_plugin_source()
    test_launch_wiring()
    test_install_offer()
    print()
    if FAIL:
        print(f"FAILED: {len(FAIL)}")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
