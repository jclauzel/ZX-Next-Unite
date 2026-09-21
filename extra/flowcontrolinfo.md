# UART hardware flow control: what was measured, on real hardware

*Since 5.9.12 flow control is on by default where the board has the pins
(`-nfc` refuses it) and `gofast` asks the module as found before it resets;
the measurements below stand, and their `:NNNN` anchors are as of 5.9.10.*

Notes behind `.sync5`'s `-fc` (5.9.9) and the fallback fix (5.9.10). Written up
because two of the things in here are **measurements**, not reasoning, and one
of them contradicts the obvious answer. Source references are
`nextsync/sync/z88dk/nextsync.c` unless stated.

## The two halves

Arming flow control means arming *both* ends, and they are separate mechanisms:

* **The FPGA side** — bit 5 of port `0x163B` (UART Frame). `FLOW_ON()` /
  `FLOW_OFF()` at `:106-107`. Every write is read-modify-write: a blind
  `out 0x20` would also reconfigure the line, which presents as corruption
  rather than as a flow-control bug.
* **The ESP side** — the *fifth* field of `AT+UART_CUR=<baud>,8,1,0,<flow>`.
  `0` none, `3` RTS+CTS. Patched in at `:693`, the only place in the program
  that may arm anything.

They are asymmetric on purpose, and the asymmetry is the whole subject:

| direction | signal | what it buys | what it needs |
|---|---|---|---|
| module's CTS input ← FPGA `esp_cts_n_o` | the Next stops the module | the **download** leg — stops the 512-byte Rx FIFO overrunning during the SD write after each block | the FPGA to drive it |
| Next's `esp_rtr_n_i` ← module's RTS output | the module stops the Next | the upload leg | the daughterboard to route it |

The download leg is the one the dot actually needs, and it is the one that
depends on the FPGA.

## The gate, and why it is a closed set

The switch only records a *request* (`g_flow_ask`: 1 unless `-nfc` since 5.9.12,
0 unless `-fc` before); there is no board test at parse time. The decision is
made once, before anything touches the wire:

```c
unsigned char bid = readnextreg(0x0F) & 0x0F;          /* :1857 */
g_flow = (unsigned char)(g_flow_ask
                         && (bid == 2u || bid == 3u)   /* :1874  the guard */
                         && g_syncmode != MODE_SLOW);
```

Board issue is `bid + 2`, so the accepted set is **issue 4 and issue 5 only**.
It is a closed set and never `>= 2`, because `0xFF & 0x0F` is 15 — what an
emulator with no `0x0F` model hands back — and **an unknown id must mean no**.
`-slow` is folded into the same expression so a slow run can neither arm nor
report that it did.

Everything downstream reads `g_flow`, never `g_flow_ask`: the `,3` patch
(`:693`) and `FLOW_ON()` (`:747`, the file's only call site); the hard reset
(`:630`) is gated on `reset_retry` since 5.9.12 and runs only after a failed
probe. On a board outside the set, a run is byte-for-byte the 5.9.7
bring-up.

## What "Flow on" proves — and what it does not

**The dot never asks the module whether flow control is on.** The armed command
is sent fire-and-forget:

```c
atcmd(cmd, "", 0, inbuf);        /* :694 */
```

`strinstr` short-circuits on an empty expect (`gfx.c:26`, `if (!*b || !blen)
return 1;`), so `atcmd` returns success after one `receive()` without examining
anything — and the module's actual reply is then **destroyed** by the
`flush_uart_hard()` at `:710`. There is no `AT+UART_CUR?` query anywhere in the
program.

What *is* checked is the next step (`:714`): a bare `\r\n` — an invalid AT
command, so a live module answers `ERROR` — sent at the new rate. That is a real
reply check, but it confirms the **baud**, not the flow field. The leap from one
to the other is the atomicity assumption at `:716-718`: ESP-AT parses
`UART_CUR`'s five fields together and applies them together or not at all.

So `Flow on` means *"both halves were commanded and the link still works"*, not
*"the module confirmed flow control is on"*. The comment at `:1999` —
*"Everything above proves the module ACCEPTED ',3'"* — overstates it; what was
observed is the baud. `:2001` is honest about the rest: nothing proves the
module's RTS pin is physically routed, *"the one link no software can verify"*.

## The N-GO experiment

Run on an N-GO (`Brd 2 t7 p14`) with scratchpad builds that **removed the guard
at `:1874`** and instrumented `gofast()`. That machine is the one the source
comment at `:598` names by name: `27000/14 = 1928.6 kHz`, so the module is told
`1929000` (`khz_values[1][7]`).

**First build** — guard removed, nothing else. Result: `No fast esp`, and
useless, because three different failures print it: the module refused `,3`; the
module never came back from the arming hard reset (the 8×`AT` probe at `:668`
discards its result, so eight failures fall through exactly like a success); or
this board cannot hold 1929000 at all.

**Second build** — markers at every decision point, and critically it **waits
for the `OK`** to `AT+UART_CUR` instead of flushing it:

```
Brd 2 t7 p14
fc:on  fc:up  fc:s3  fc:ack  fc:nofast  fc:s0  fc:noack  fc:nofast
No fast esp
```

Read it:

* `fc:ack` — **the module accepted the armed command.** It OK'd
  `AT+UART_CUR=1929000,8,1,0,3` at 115200.
* `fc:nofast` — and then went silent at 1929000.
* `fc:noack` — the `,0` retry **at 115200 got no reply**. This is the line that
  closes it: an `OK` for `UART_CUR` is emitted at the *old* baud *before* the
  switch, so a module still at 115200 would have answered. It didn't. Therefore
  it had already left 115200 — **it applied the command, both fields.**

**Conclusion: the ESP module accepts RTS/CTS and then goes permanently mute**,
because nothing on an issue-2 board drives its CTS input. Not a refusal — an
acceptance followed by silence. The guard's own comment at `:1863` — *"telling a
module to honour a CTS line nobody drives is how you get a transmitter that
never sends"* — is now measured rather than assumed.

Note `fc:arm` never printed, so `FLOW_ON()` never ran and bit 5 was never set.
Had the run got that far, the Next's own transmitter would have parked too, and
that needs a reset rather than printing `All done`.

## The defect this exposed, and the 5.9.10 fix

The `,0` fallback used to do this:

```c
setupuart(0);              /* back to 115200 to be heard */
goto retry;
```

on the premise at `:716` that *"the rate did not take, so NOTHING in that
command took"*. **That premise is false in the accept-then-mute case**, and the
root cause is that `atcmd` returns the same `1` for a refusal and for silence —
the two are indistinguishable from that branch. The module was at the *fast*
rate, never heard the 115200 retry, the second probe failed too, and the run
died with `No fast esp` instead of degrading to the unprotected fast link the
fallback exists to provide.

Two structural consequences were found with it:

* The hard-reset block at `:630` — the one thing that returns the module to a
  known state — is gated `if (g_flow)`, and `g_flow` is zeroed one statement
  before the old `goto retry`. **The fallback skipped it precisely when it was
  needed.**
* `g_flow_esp` is set only at `:730`, and the verdict print sits below the
  `goto bailout` at `:1995`. So **`Flow off esp` could only ever print when the
  fallback SUCCEEDED** — structurally unreachable on every path that sets the
  flag and then fails.

5.9.10 forces the module's state instead of inferring it: a `hardreset:` label
above `:630`, the gate widened to `if (g_flow || reset_retry)`, and the fallback
does `reset_retry = 1; goto hardreset;`. Two passes maximum; the second failure
still falls through to `No fast esp`. Cost: one extra module reset on a run that
was already failing, and with it a Wi-Fi re-association — visible as a
`Retrying connection` line or two before the session comes up.

## The idea that was measured and failed

There is an obvious cheaper fix, and **it does not work.** A muted module should
still *receive* — only its transmitter is CTS-gated — so re-sending `,0` at the
rate we are already at ought to disarm it on the spot, with no reset and no
re-association.

A third build tried that first and fell back to the reset. On the N-GO:

```
fc:s3 fc:ack fc:nofast           the armed attempt, accepted then mute
fc:try-fast fc:s0 fc:noack fc:nofast    same-rate resend: rescued NOTHING
fc:try-reset fc:up fc:s0 fc:ack fc:rescued   the reset brought it back
Flow off esp
Listening for commands
```

The session came up: `-fc` on incapable hardware degraded to a working
unprotected link instead of killing the run. But stage 1 acked nothing and the
module stayed mute. **Only the hard reset recovers it. Do not reintroduce the
same-rate resend.**

Why stage 1 fails is *not* settled, and does not need to be: either the module's
AT processor stalls once it cannot emit a reply (blocked TX ⇒ the command is
never applied), or it was no longer at that rate by then. Both are cured by the
reset. Separating them would take one probe at 115200 before resetting.

## What this does not establish

* **The VHDL claims.** That issue 2 lacks `esp_cts_n_o` / `esp_rtr_n_i`, and
  that `uart_tx.vhd` holds `S_RTR` only while `i_cts_n='1'` and
  `i_frame(5)='1'`, are taken from the comments — no `zxnext.vhd` is tracked in
  this repo.
* **That capable boards are exposed.** The mute needs the FPGA's `esp_cts_n_o`
  to be absent or dead; a daughterboard cannot fail to drive its own input, so
  the `:1999` concession (about the *other* direction) does not reach this
  failure. And `:614-617` records a capable Brd 4 where `-fc` worked — i.e. a
  module at `,3` that *did* transmit with bit 5 still clear.
* **But there is a residual risk on allowed hardware, by a different route:**
  the gate reads the **board** and never the **core**. `:1873-1875` tests only
  the `0x0F` nibble; `corever` is read later at `:1891`. Meanwhile `:57-59`
  records a core generation that does not decode `0x163B` at all (*"`-- todo:
  add cts/rts` sits beside them"*). An issue-4/5 board on such a core has the
  pins in its top-level entity with nothing driving them, `FLOW_ON()` writes to
  a floating bus, and a module told `,3` would mute exactly as traced.

## Reproducing it

The test builds are not in the repo and should not be — they remove a safety
guard. To rebuild one:

1. Copy `nextsync/sync/z88dk/` somewhere scratch, plus a sibling
   `server/dot/` for the build script's deploy step.
2. Replace the conjunct `&& (bid == 2u || bid == 3u)` at `:1874` with a
   comment. Keep `g_flow_ask` and the `MODE_SLOW` term.
3. Add markers where you need them — `print()` is safe in `gofast()` (it
   already prints `No fast esp`), but **never** in head-page code, which pages
   the ROM over itself.
4. To learn whether the module accepted the command, change `:694` from
   `atcmd(cmd, "", 0, inbuf)` to expect `"OK"`. Echo is still on at that point,
   so the reply carries the command back before its OK; `strinstr` finds it
   either way. This is also what ZX Next Remote does.
5. `.\build_dotn.ps1`, then rename the output so it cannot collide with the
   real dot — an experiment build called `sync5fc` installs to `c:/dot/sync5fc`
   and runs as `.sync5fc`, reading the same `c:/sys/config/nextsync.cfg`.

Expect a parked transmitter if you get as far as `FLOW_ON()` on a board without
the pins: the Next appears frozen and needs a reset. Nothing else is at risk —
the module is restored by the exit path's reset pulse (`bailout:` since 5.9.11:
`FLOW_OFF()` first, a bounded drain so the `CIPCLOSE` just sent leaves, then
the nextreg 0x02 hold/release and `setupuart(0)`), which reaches it at any
baud and needs no working transmitter - the same pulse the
`No esp - reset, try again` bail now jumps to.
