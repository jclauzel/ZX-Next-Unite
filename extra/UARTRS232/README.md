# UARTRS232 — a CSpect plugin for ZX-Next-Unite's RS232 ESP emulation

`UARTRS232.dll` gives the ZX Spectrum Next emulated by
[CSpect](https://mdf200.itch.io/cspect) a working ESP (Wi-Fi module) **without
any hardware**: it takes the Next's ESP UART ports and connects them to
ZX-Next-Unite's RS232 ESP emulation, the same one the app uses for MAME. Network
software on the emulated Next — `.sync5`, ZX Next Remote, the NextZXOS WiFi
tools — then reaches the real network through your PC.

```
Next software (in CSpect) --UART ports--> UARTRS232 --TCP 127.0.0.1--> RS232 ESP emulation
                                                                             |
                                                       real TCP to the LAN <-+
```

It is **not** UARTReplacement. That plugin, by Robin Verhagen-Guest
([CSpectPlugins](https://github.com/Threetwosevensixseven/CSpectPlugins)),
connects CSpect to a *real* ESP module on a serial port; this one needs no
module at all. The name was chosen so the two are never mistaken for each other,
and they can live in the same CSpect folder (see below).

## Using it from ZX-Next-Unite (the normal way)

1. **Settings → tick "RS232 ESP Emulation (CSpect)".**
2. **Launch CSpect** from the app (the Launch button, an emulator strip, or
   "start with file").

At each launch the app copies `UARTRS232.dll` beside `CSpect.exe` (CSpect loads
every plugin DLL in its own folder), starts the RS232 ESP emulation — or joins
the one already running for a MAME or another CSpect — and tells the plugin
where it listens. The SD Card log says what the launch got.

There is nothing to configure on the Next side and **no speed restriction**:
the link between the plugin and the emulation has no baud rate, so `.sync5 -f`,
`-default` and `-s`, and ZX Next Remote's Fast / Medium / Slow all work. (Through
MAME's `null_modem` card the Next side must use its slow pacing; not here.)

Untick the box and CSpect is launched with the plugin told to stay out. The DLL
can stay in the folder: the box is the switch.

The port, the verbose trace and "IP address shown to the emulated Next" in
Settings belong to the emulation itself, so they apply to CSpect and MAME alike.

### After an itch.io CSpect install or update

Each CSpect version lives in its own folder, so a plugin installed in the old
one does not follow you to the new one. Right after the itch.io tab installs a
CSpect - or the startup update fetches a newer build - the app asks, about the
CSpect it has just adopted:

1. **Install the UARTRS232 plugin?** Yes copies the DLL into that folder.
   *Not now* installs nothing and ends there.
2. **Enable the RS232 ESP emulation for CSpect?** - only when the Settings box is
   not ticked yet. Yes ticks it; *Not now* leaves the plugin installed but unused.

Nothing is asked when the folder already holds this build of the plugin and
the box is ticked. The offer waits for any other dialog (the OpenAL one on
Windows) to close first.

### With UARTReplacement in the same folder

If you also use UARTReplacement with a USB ESP module, leave it installed:

| "RS232 ESP Emulation (CSpect)" | The Next's ESP is… |
|---|---|
| ticked | the **emulated** one — UARTRS232 answers the UART first, the real module rests |
| unticked | the **real** one — UARTRS232 registers nothing, UARTReplacement works as always |

A `-com=...` typed in the app's "CSpect default launch parameters" is an
explicit choice of the real serial port and wins: that launch does not use the
emulation, and the log says so.

## Using it without the app launching CSpect

Copy `UARTRS232.dll` into your CSpect folder. Started by hand, the plugin is
**automatic**: it takes the UART only when an RS232 ESP emulation answers at
startup (default `127.0.0.1:2222`), and steps aside if CSpect was given `-com`
or if UARTReplacement is installed beside it. Its first console line says which
it did, and why.

The emulation has to be running first. Either it is already up in the app (a
CSpect or a `null_modem` MAME launched from the app keeps it running until the
last of them exits; the MCP tool `esp_emulation_start` starts one), or run it
on its own from a source checkout:

```
python espemu.py --port 2222
```

An optional `UARTRS232.cfg` beside the DLL changes the defaults:

```
# UARTRS232.cfg
enabled  = auto        # auto (default) | on | off
host     = 127.0.0.1
port     = 2222
verbose  = off
priority = -100        # see "How it coexists" below
```

`enabled = on` makes the plugin take the UART unconditionally and keep trying to
reach the emulation (the Next sees no ESP until one answers).

The app's launcher overrides the file through the environment:
`ZX_NEXT_UNITE_ESP=<host>:<port>` (active) or `ZX_NEXT_UNITE_ESP=off`, plus
`ZX_NEXT_UNITE_ESP_VERBOSE=1`.

## What it does to the machine

Only the **ESP** UART is touched; with the Pi UART selected every access falls
through to CSpect.

| Port / register | Access | What the plugin does |
|---|---|---|
| `0x133B` | write | a byte for the ESP — handled |
| `0x133B` | read | UART status: bit 0 = a byte is waiting, bit 1 = transmit buffer full, bit 4 = transmit buffer empty — handled |
| `0x143B` | read | the next received byte (0 when none) — handled |
| `0x153B` | write | bit 6 selects ESP / Pi — observed, passed on |
| `0x163B` | write | bit 7 empties the FIFOs — observed, passed on |
| nextreg `0x02` | write | bit 7 holds the ESP in reset; releasing it starts a **fresh** emulation session, which is what a hardware reset of the module amounts to — observed, passed on |

A machine reset empties the FIFOs but keeps the link, as on hardware (the module
survives one).

### The one thing it knows about the AT dialect

The plugin moves bytes; the ESP8266 "AT" commands are the emulation's business.
The exception is `+IPD,<len>:` — it follows the stream just far enough to know
where each payload ends, and makes the status port read "nothing waiting" for a
few reads there.

A real UART delivers bytes at its baud rate, and Next software leans on that:
the `.sync5` dot drains "whatever is in the FIFO" after a `+IPD` header and
trusts the wire to have nothing more to offer when the block is complete. With
no baud rate a second block is already waiting, and the drain ran straight
through it — in the first cut every upload failed its checksum. The pause is
what the wire would have done, exactly where the software needs it and nowhere
else.

### How it coexists

CSpect asks the plugin with the **lower** priority number first. Measured on
CSpect 3.3.1 with UARTReplacement installed: at priority 0 or +100 UARTReplacement
kept the UART (and drove the real module); at −100 this plugin got every byte.
So the ports are registered at −100, and only when the plugin is active.

CSpect loads plugins from memory, so the DLL file is not locked while CSpect
runs and can be replaced under a running emulator.

## What was tested

By hand, on Windows 11, CSpect **3.3.1** and **4.5.1**, 2026-11-05 — megabyte
round trips byte-identical, the Next's own CRC-32 matching the PC's:

- `.sync5` 5.7.5 and 5.9.14 in `-listen -f` (2,000,000 baud asked of the module)
- ZX Next Remote 1.8.0, UART speed "fast", unattended Listener
- the emulation killed and restarted under a live session (the plugin reconnects,
  ZX Next Remote re-dials)
- automatic mode, `enabled = off` and `-com`
- UARTReplacement v1.8 in the same folder, on CSpect 3.3.1: transfers identical
  through the emulation while UARTReplacement was loaded beside it (CSpect 4.5.1
  did not load UARTReplacement v1.8 at all in these runs, so there was nothing
  to coexist with there)

Not tested: CSpect under **mono** (Linux, macOS). Nothing in the plugin is
Windows-only, but it has not been run there. The reset-line path (nextreg 2
bit 7) is implemented and unexercised: neither consumer pulsed it in these runs.
The "unticked = the real ESP through UARTReplacement" half of the table above
follows from the plugin registering nothing when told off; it was not re-run
with a module plugged in.

## Building

```
python extra/UARTRS232/build_plugin.py
```

It needs no SDK: the C# 5 `csc.exe` that ships with the .NET Framework on every
Windows (or mono's `mcs`), plus CSpect's own `Plugin.dll` to compile against —
the newest one under `downloads/itchio` by default, `--plugin-dll` otherwise.
`Plugin.dll` is CSpect's and is not kept in this repository.

The script writes two things, always together: `UARTRS232.dll` here, and
`zxnu_cspect_esp_blob.py` at the repository root — the same bytes as a Python
module, which is how the app carries the plugin into the wheel, the frozen
executables and the Flatpak. The in-box compiler is not deterministic, so
rebuild only when `UARTRS232.cs` changed, and bump its `PluginVersion` when it
did: `tests/test_cspect_esp_plugin.py` fails when the source and the shipped
binary drift apart.

The source sticks to C# 5 (no `$""`, `?.`, `nameof`, `=>` members, `out var`).

## Licence

MIT, like the rest of ZX-Next-Unite. CSpect is © Mike Dailly; UARTReplacement is
© Robin Verhagen-Guest (Apache 2.0) — neither is included here.
