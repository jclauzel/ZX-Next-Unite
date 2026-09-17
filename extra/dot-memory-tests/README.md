# Dot memory-window test programs

BASIC programs for testing the `.sync5` dot's **banked `$6000-$7FFF` window**
(dot 5.9.5+, `DOTN_NUM_EXTRA = 1`, `map_data_bank()` in `uart.asm`). They exist
to put real, live BASIC program text *underneath* the window the dot borrows,
so a mapping bug shows up as a corrupted `LIST` rather than silence.

Built with `txt2bas -f 3dos` (npm; `bas2txt` decodes them back to verify).
`txt2bas` writes autostart `0x0000`, which makes a program **RUN on LOAD** —
both `.bas` files here have bytes 18:19 patched to `0x8000` ("no autostart")
with the byte-127 checksum recomputed, so they load quietly.

| file | program bytes | occupies | notes |
|---|---|---|---|
| `window.bas` | 6930 | `$5CCB-$77DD` | **the correct test.** Fills the borrowed window, stops 2083 bytes short of `$8000` |
| `page11.bas` | 11276 | `$5CCB-$8900` | **overshoots** — see below |

## Size these against `$8000`, not against "page 11"

A BASIC program starts at about **`$5CCB`**. The dotN **main bank** starts at
**`$8000`**. So a test program must satisfy `$5CCB + len < $8000`, i.e. stay
under roughly **9000 bytes**.

`page11.bas` was sized to "exceed ~8.5 KB so it reaches page 11" and runs to
`$8900` — past `$8000`. With it loaded, `.sync5` misbehaves on **5.9.4 too**
(`Bad server name`, a mangled command tail), so it cannot tell you anything
about the banked window. It produced a full false alarm on 2026-09-17 before
that was spotted.

It is kept deliberately, because that failure is **still unexplained** and is
worth investigating on its own — see below.

## The test that validated 5.9.5

```
LOAD "window.bas"
RUN                → Hello World
.sync5 -l -f       → connect
                   → a transfer that CREATES a file
LIST               → the program must come back intact
```

Creating a file is the point of step 4: `inbuf` doubles as the filename buffer
for `createfilewithpath`, so that step is esxDOS **resolving a path string out
of the borrowed window** — the one path with no precedent elsewhere.

## Still open

With `page11.bas` loaded, **stock 5.9.4** fails with `Bad server name` — the
`valid_server` guard catching a mangled command tail, the documented N-Go
failure family, with no banked page involved. `CRT_ENABLE_COMMANDLINE = 2`
hands `main()` a raw pointer into BASIC's program area and `tail_copy()`
(uart.asm) copies from it. Affects anyone with a large BASIC program in
memory. Unexplained.
