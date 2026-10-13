"""Which ESP card the emulated Next gets in MAME (9.7.48).

MAME's rs232_esp slot takes ONE card. The ZX Spectrum Next MAME fork gained
an ``esp8266_at`` card (holub/mame PR #9, October 2026) that does the AT
dialect and the networking inside MAME and whose serial rate FOLLOWS the
``AT+UART_CUR`` the dot sends - every ``.sync5`` speed connects through it.
The ``null_modem`` card espemu sits behind is pinned to MAME's Machine
Configuration baud (115200 at most), so the dot's fast rates end in
"No fast esp" there. Most users do not have that build yet, so the launcher
ASKS the installed MAME (``-listslots``) and takes the built-in card only
when it is offered; everything else keeps the proxy path byte for byte.

Covered headlessly:

* ``parse_mame_listslots`` on MAME's real column layout (captured from the
  build that carries the card), on a stock layout without it, and on every
  non-answer (empty, an error message, None);
* ``mame_typed_esp_card`` / ``choose_mame_esp_card`` - the automatic pick,
  the hand-typed override, and the two refusals (unknown probe never picks
  the built-in card; a typed card this build lacks falls back);
* ``mame_esp_launch_tokens`` - the stripping and the appended pair(s);
* ``mame_slot_options`` live against a MAME found under downloads/ (skipped
  cleanly without one, as on CI), plus the bogus-binary and unknown-machine
  non-answers;
* source tripwires on launch_mame: the probe, the chooser and the token
  builder are what the launch goes through, and the proxy path still hands
  MAME the port the server ACTUALLY listens on.

Run: python tests/test_mame_esp_card.py  (no pytest, matching the suite).
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from zxnu_config import (  # noqa: E402
    MAME_ESP_CARD_BUILTIN, MAME_ESP_CARD_PROXY, MAME_ESP_SLOT,
    MAME_ROM_CHOICE, choose_mame_esp_card, mame_esp_launch_tokens,
    mame_typed_esp_card, parse_mame_listslots)

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


# MAME 0.289 (holub fork, PR #9 merged): the real output, verbatim.
LISTSLOTS_WITH_CARD = """SYSTEM           SLOT NAME        SLOT OPTIONS     SLOT DEVICE NAME
---------------- ---------------- ---------------- ----------------------------
specnext_ks2     ram              2m               RAM

                 rs232_esp        esp8266_at       ESP8266 WiFi module (AT firmware)
                                  null_modem       RS-232 Null Modem
                                  pty              Pseudo Terminal

                 rs232_rpi        esp8266_at       ESP8266 WiFi module (AT firmware)
                                  null_modem       RS-232 Null Modem
                                  pty              Pseudo Terminal

                 mdin             midiin           MIDI In port

                 mdthru           midiout          MIDI Out port

                 mdout            midiout          MIDI Out port

                 zxbus:1          nemoide          Nemo IDE Controller
                                  neogs            NeoGS / General Sound
"""

# A build predating the card: the same layout, two options in the slot.
LISTSLOTS_STOCK = """SYSTEM           SLOT NAME        SLOT OPTIONS     SLOT DEVICE NAME
---------------- ---------------- ---------------- ----------------------------
tbblue           ram              2m               RAM

                 rs232_esp        null_modem       RS-232 Null Modem
                                  pty              Pseudo Terminal

                 mdout            midiout          MIDI Out port
"""

# Wider columns, CRLF, a trailing blank: the parser must read the header,
# not count spaces.
LISTSLOTS_WIDE = (
    "SYSTEM                SLOT NAME             SLOT OPTIONS          SLOT DEVICE NAME\r\n"
    "--------------------- --------------------- --------------------- ----------------\r\n"
    "specnext_ks3          rs232_esp             esp8266_at            ESP8266 WiFi module (AT firmware)\r\n"
    "                                            null_modem            RS-232 Null Modem\r\n"
    "\r\n"
    "                      rs232_rpi             null_modem            RS-232 Null Modem\r\n"
    "\r\n")


def test_parser():
    print("\n== parse_mame_listslots ==")
    slots = parse_mame_listslots(LISTSLOTS_WITH_CARD)
    check("the slot with the card lists all three options, in MAME's order",
          slots.get(MAME_ESP_SLOT) == ["esp8266_at", "null_modem", "pty"],
          repr(slots.get(MAME_ESP_SLOT)))
    check("every slot of the machine is read",
          set(slots) == {"ram", "rs232_esp", "rs232_rpi", "mdin", "mdthru",
                         "mdout", "zxbus:1"}, sorted(slots))
    check("the dashed underline is not a slot",
          not any(set(k) <= {"-"} for k in slots), sorted(slots))
    check("descriptions with spaces never leak into the options",
          slots.get("zxbus:1") == ["nemoide", "neogs"], repr(slots.get("zxbus:1")))
    stock = parse_mame_listslots(LISTSLOTS_STOCK)
    check("a stock build's slot has no built-in card",
          stock.get(MAME_ESP_SLOT) == ["null_modem", "pty"], repr(stock))
    wide = parse_mame_listslots(LISTSLOTS_WIDE)
    check("wider columns and CRLF are read from the header, not assumed",
          wide.get(MAME_ESP_SLOT) == ["esp8266_at", "null_modem"]
          and wide.get("rs232_rpi") == ["null_modem"], repr(wide))
    for label, text in (("an empty answer", ""), ("None", None),
                        ("an error message", "Unknown system 'specnext_ks9'\n"),
                        ("a header-less dump", "rs232_esp esp8266_at\n")):
        check(f"{label} parses to {{}} (unknown, never 'no slots')",
              parse_mame_listslots(text) == {}, repr(parse_mame_listslots(text)))


def test_typed():
    print("\n== mame_typed_esp_card ==")
    check("no -rs232_esp typed -> ''",
          mame_typed_esp_card(["mame", "tbblue", "-window"]) == "")
    check("a typed card is read",
          mame_typed_esp_card(["mame", "-rs232_esp", "null_modem", "-x"]) == "null_modem")
    check("the LAST one wins, as MAME reads repeated options",
          mame_typed_esp_card(["-rs232_esp", "null_modem", "-rs232_esp", "esp8266_at"]) == "esp8266_at")
    check("a trailing -rs232_esp with no value counts as none",
          mame_typed_esp_card(["mame", "-rs232_esp"]) == "")
    check("a dangling -rs232_esp followed by another OPTION names no card",
          mame_typed_esp_card(["mame", "-rs232_esp", "-aspect", "4:3"]) == "")
    check("None / empty token lists are fine",
          mame_typed_esp_card(None) == "" and mame_typed_esp_card([]) == "")


def test_chooser():
    print("\n== choose_mame_esp_card ==")
    offered = ["esp8266_at", "null_modem", "pty"]
    stock = ["null_modem", "pty"]
    check("the built-in card is taken when offered",
          choose_mame_esp_card(offered) == (MAME_ESP_CARD_BUILTIN, "builtin"))
    check("the proxy when it is not",
          choose_mame_esp_card(stock) == (MAME_ESP_CARD_PROXY, "proxy"))
    check("an UNKNOWN probe never picks the built-in card",
          choose_mame_esp_card(None) == (MAME_ESP_CARD_PROXY, "unknown"))
    check("an empty option list is the proxy too (not a crash)",
          choose_mame_esp_card([]) == (MAME_ESP_CARD_PROXY, "proxy"))
    check("case of the offered names does not matter",
          choose_mame_esp_card(["ESP8266_AT"]) == (MAME_ESP_CARD_BUILTIN, "builtin"))
    check("a typed null_modem wins over an offered built-in card",
          choose_mame_esp_card(offered, "null_modem") == (MAME_ESP_CARD_PROXY, "typed"))
    check("a typed esp8266_at is honoured when offered",
          choose_mame_esp_card(offered, "esp8266_at") == (MAME_ESP_CARD_BUILTIN, "typed"))
    check("a typed card is honoured when the probe could not answer",
          choose_mame_esp_card(None, "esp8266_at") == (MAME_ESP_CARD_BUILTIN, "typed"))
    check("a typed esp8266_at this build provably lacks falls back to the pick",
          choose_mame_esp_card(stock, "esp8266_at") == (MAME_ESP_CARD_PROXY, "typed-missing"))
    check("a typed card that is neither of the two falls back to the pick, "
          "with its OWN reason (pty IS offered - 'missing' would be a lie)",
          choose_mame_esp_card(offered, "pty") == (MAME_ESP_CARD_BUILTIN, "typed-unsupported"))
    check("...and so does a typo, whatever the probe said",
          choose_mame_esp_card(None, "esp8266at") == (MAME_ESP_CARD_PROXY, "typed-unsupported")
          and choose_mame_esp_card(stock, "pty") == (MAME_ESP_CARD_PROXY, "typed-unsupported"))
    check("'typed-missing' is reserved for one of the two cards an ANSWERED probe omits",
          choose_mame_esp_card(stock, "esp8266_at")[1] == "typed-missing"
          and choose_mame_esp_card(None, "esp8266_at")[1] == "typed")
    check("typed names are matched case-blind",
          choose_mame_esp_card(offered, " Null_Modem ") == (MAME_ESP_CARD_PROXY, "typed"))


def test_tokens():
    print("\n== mame_esp_launch_tokens ==")
    base = ["mame", "tbblue", "-window", "-hard1", "x.img"]
    typed = ["mame", "tbblue", "-rs232_esp", "null_modem", "-bitb",
             "socket.127.0.0.1:1234", "-window", "-hard1", "x.img"]
    check("the built-in card is appended alone",
          mame_esp_launch_tokens(base, MAME_ESP_CARD_BUILTIN)
          == base + ["-rs232_esp", "esp8266_at"])
    check("the proxy card carries the bitbanger socket with the LIVE port",
          mame_esp_launch_tokens(base, MAME_ESP_CARD_PROXY, 2222)
          == base + ["-rs232_esp", "null_modem", "-bitb", "socket.127.0.0.1:2222"])
    check("hand-typed -rs232_esp/-bitb pairs are stripped before the card is added",
          mame_esp_launch_tokens(typed, MAME_ESP_CARD_BUILTIN)
          == base + ["-rs232_esp", "esp8266_at"])
    check("...on the proxy path too (never an option twice)",
          mame_esp_launch_tokens(typed, MAME_ESP_CARD_PROXY, 2223)
          == base + ["-rs232_esp", "null_modem", "-bitb", "socket.127.0.0.1:2223"])
    check("the input list is left unchanged",
          typed[2:6] == ["-rs232_esp", "null_modem", "-bitb", "socket.127.0.0.1:1234"])
    try:
        mame_esp_launch_tokens(base, MAME_ESP_CARD_PROXY)
        check("the proxy card without a port is refused", False)
    except ValueError:
        check("the proxy card without a port is refused", True)
    check("a dangling -rs232_esp/-bitb never swallows the next option",
          mame_esp_launch_tokens(["m", "-rs232_esp", "-aspect", "4:3", "-bitb"],
                                 MAME_ESP_CARD_BUILTIN)
          == ["m", "-aspect", "4:3", "-rs232_esp", "esp8266_at"])
    check("a non-loopback host is honoured for the socket",
          mame_esp_launch_tokens(base, MAME_ESP_CARD_PROXY, 7, host="10.0.0.5")[-1]
          == "socket.10.0.0.5:7")


def test_live_probe():
    print("\n== mame_slot_options against a real MAME (skips without one) ==")
    import zxnu_emulator_ops as ops
    exe = None
    for cand in (os.path.join(REPO, "downloads", "mame", "mame.exe"),
                 os.path.join(REPO, "downloads", "mame", "mame")):
        if os.path.isfile(cand):
            exe = cand
            break
    check("a bogus binary answers None (not a crash)",
          ops.mame_slot_options([os.path.join(REPO, "no-such-mame.exe")], "tbblue") is None)
    check("an empty launcher answers None",
          ops.mame_slot_options([], "tbblue") is None)
    if exe is None:
        print("SKIP  no MAME under downloads/mame - live probe not run")
        return
    slots = None
    for machine in MAME_ROM_CHOICE:
        slots = ops.mame_slot_options([exe], machine, cwd=os.path.dirname(exe))
        if slots:
            break
    check("the installed MAME answers the probe for one of the known machines",
          bool(slots), repr(slots)[:120])
    if slots:
        check("...and names the rs232_esp slot",
              MAME_ESP_SLOT in slots, sorted(slots))
        check("...with the null_modem card every build has",
              MAME_ESP_CARD_PROXY in [o.lower() for o in slots.get(MAME_ESP_SLOT, [])],
              repr(slots.get(MAME_ESP_SLOT)))
        print(f"      installed MAME offers: {slots.get(MAME_ESP_SLOT)} -> "
              f"{choose_mame_esp_card(slots.get(MAME_ESP_SLOT))}")
    check("a machine MAME does not know answers None",
          ops.mame_slot_options([exe], "no_such_machine_zxnu", cwd=os.path.dirname(exe)) is None)


def test_launch_wiring():
    """Source-level tripwires: launch_mame needs a whole MainWindow, so the
    seams are checked in the text, the way tests/test_esp_emu.py does."""
    print("\n== launch_mame goes through the chooser ==")
    src = open(os.path.join(REPO, "zxnu_emulator_ops.py"), encoding="utf-8").read()
    start = src.find("def launch_mame(")
    # The body ends at the next def at the same (builder-level) indent.
    end = src.find("\n    def ", start + 1) if start >= 0 else -1
    body = src[start:end] if (start >= 0 and end > start) else ""
    check("launch_mame exists and its body was bounded", bool(body)
          and "_start_esp_emulation()" in body and len(body) < 40000, len(body))
    check("the launch asks the installed MAME for its slot cards",
          "_mame_slot_options(_launch_prefix, mame_rom)" in body)
    # The probe's own body, not the module: a comment above the cache also
    # carries the quoted "-listslots", and the def's DEFAULT carries the
    # timeout constant - neither proves the subprocess call does.
    pstart = src.find("def mame_slot_options(")
    probe = src[pstart:src.find("\ndef ", pstart + 1)] if pstart >= 0 else ""
    check("the probe exists at module level", bool(probe))
    check("the probe runs -listslots through subprocess",
          '"-listslots"' in probe and "subprocess.run(" in probe)
    check("the probe's subprocess call is bounded",
          "timeout=timeout" in probe
          and "timeout=MAME_ESP_SLOT_PROBE_TIMEOUT_S" in probe)
    check("the probe refuses an empty launcher before running anything",
          "if not prefix or not prefix[0]:" in probe)
    check("the choice goes through choose_mame_esp_card",
          "choose_mame_esp_card(" in body)
    check("a hand-typed card is read from the parameters",
          "mame_typed_esp_card(mame_argv)" in body)
    check("the built-in card is appended through the token builder",
          "mame_esp_launch_tokens(mame_argv, MAME_ESP_CARD_BUILTIN)" in body)
    check("the proxy path hands MAME the port the server ACTUALLY listens on",
          "mame_esp_launch_tokens(\n                        mame_argv, MAME_ESP_CARD_PROXY, _esp_server.port)" in body
          or "MAME_ESP_CARD_PROXY, _esp_server.port)" in body)
    check("the proxy is only started on the proxy path",
          body.count("_start_esp_emulation()") == 1
          and body.find("_start_esp_emulation()") > body.find("choose_mame_esp_card("))
    check("no hard-coded null_modem pair survives in the launch",
          '"-rs232_esp", "null_modem"' not in body)
    check("the verbose checkbox maps to MAME's own -verbose on the built-in path",
          '"-verbose"' in body)
    check("only successful probes are cached",
          "if slots is not None:\n            cache[key] = slots" in src
          or "if slots is not None:\r\n            cache[key] = slots" in src)


def main():
    test_parser()
    test_typed()
    test_chooser()
    test_tokens()
    test_live_probe()
    test_launch_wiring()
    print()
    if FAIL:
        print(f"FAILED: {len(FAIL)}")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
