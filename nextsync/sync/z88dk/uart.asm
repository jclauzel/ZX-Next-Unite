; uart.asm - z88dk (z80asm) port of the timing-critical UART receive loop.
;
; Ported from the SDCC hand-asm uart.s. Only receive() is kept in assembly;
; it must drain the UART faster than bytes arrive (up to 2 Mbaud), so it stays
; hand-written. checksum() is now a plain C function in syncsys.c.
;
; Calling convention: __z88dk_fastcall, so the single argument (char *b, the
; destination buffer) arrives in HL. The 16-bit result (byte count) is returned
; in HL, as fastcall expects.
;
; Lives in code_compiler alongside the compiled C so it sits in the always-
; mapped main bank while the command runs.

SECTION code_compiler

PUBLIC _receive
PUBLIC _zx_keyrow

EXTERN _inbuf        ; nextsync.c's 2 KB receive buffer - receive()'s hard wall

; unsigned char zx_keyrow(unsigned char highbyte) __z88dk_fastcall
;   L = highbyte : the high 8 bits of the ULA keyboard port (low byte is 0xFE),
;                  which selects one keyboard half-row.
;   -> HL = the raw row read from IN A,(port); a pressed key reads as 0 in its
;           bit (bits 0-4 are the five keys of that half-row).
; Used to poll for BREAK (CAPS SHIFT + SPACE) during a -listen session.
_zx_keyrow:
    ld   b, l            ; B = row-select high byte
    ld   c, 0xfe         ; C = 0xFE (ULA keyboard port low byte)
    in   a, (c)          ; IN A,(BC) -> keyboard half-row bits
    ld   l, a
    ld   h, 0
    ret                  ; HL = row bits (L significant)

; unsigned short receive(char *b) __z88dk_fastcall
;   HL = b (destination: always inside inbuf - every caller drains into
;           inbuf[2048] at some offset; nothing else may be passed here)
;   -> HL = number of bytes received
;
;   v5.7: the drain is HARD-BOUNDED at inbuf+2048. The protocol paces the
;   peer (one acked ~2 KB frame in flight), which is why the unbounded
;   drain never bit in practice - but a hostile/buggy peer, an ESP reset
;   burst or an emulated UART that never reads empty could push the write
;   past inbuf into scratch and on toward the ~165-byte stack gap (the
;   sibling ZXNextRemote .nex had exactly that failure on real hardware).
;   Excess bytes stay in the FIFO for the next call; every call site
;   already loops. Budget check costs ~15 T/byte extra: ~108 T/byte total,
;   still inside the 140 T budget of 2 Mbaud at 28 MHz.
_receive:
    ex   de, hl          ; DE = destination buffer
    ld   hl, _inbuf + 2048
    or   a
    sbc  hl, de          ; HL = room left in inbuf from b
    jr   c, rx_none      ; b past the end (broken caller): store nothing
    push hl              ; keep the starting budget for the count math
    ld   bc, 0x133b      ; UART Tx/status port

nextbyte:
    ld   a, h
    or   l
    jr   z, done         ; budget spent -> leave the rest in the FIFO
    in   a, (c)          ; read status @ 0x133b
    and  0x01
    jr   z, done         ; bit0 clear -> nothing waiting, finished
    inc  b               ; B: 0x13 -> 0x14  (port 0x143b = UART Rx)
    in   a, (c)          ; read the incoming byte
    ld   (de), a         ; store it
    and  0x07
    out  (0xfe), a       ; flash the border with the low bits
    inc  de              ; advance the buffer
    dec  hl              ; budget--
    dec  b               ; B: 0x14 -> 0x13  (back to status)
    jp   nextbyte

done:
    xor  a
    out  (0xfe), a       ; border back to black
    pop  de              ; DE = starting budget
    ex   de, hl          ; HL = starting budget, DE = remaining
    or   a
    sbc  hl, de          ; HL = bytes actually stored
    ret

rx_none:
    ld   hl, 0
    ret

; --- v5.6 clone hardening (N-Go): hand asm because BOTH byte budgets are
; --- full — the head page tail brushes the $3F00 line and every main-bank
; --- byte is stack headroom, so ~150 bytes of compiled C become ~75 here.

PUBLIC _tail_copy
PUBLIC _valid_server

; void tail_copy(char *dst, char *src)   [sdcc default convention]
;   Bounded private copy of the OS command tail: hard 158-byte cap + forced
;   NUL; 0x00/0x0D end the copy early. Clones do not guarantee a terminator
;   after the tail, so nothing may parse the OS buffer directly.
;   254 -> 158 (5.7.3): a real tail is a typed BASIC line (well under 100
;   chars), and the cap bounds cleancmd downstream, whose 256 -> 160
;   shrink bought back the main-bank stack headroom the Busy branch had
;   eaten - the -anim state sat 98 bytes under REGISTER_SP and every deep
;   call scrambled the flock (the 2026-08-13 hardware round).
_tail_copy:
    pop  de              ; return address
    pop  hl              ; dst
    pop  bc              ; src
    push bc              ; caller pops its own args
    push hl
    push de
    ld   a, b
    or   c
    jr   z, tc_term      ; NULL src -> just terminate dst
    ld   e, 158          ; cap (bounds cleancmd[160], see header note)
tc_loop:
    ld   a, (bc)
    or   a
    jr   z, tc_term      ; 0x00 ends
    cp   0x0d
    jr   z, tc_term      ; 0x0D ends
    ld   (hl), a
    inc  hl
    inc  bc
    dec  e
    jr   nz, tc_loop
tc_term:
    ld   (hl), 0
    ret

; unsigned char valid_server(char *fn) __z88dk_fastcall
;   1 if fn is a plausible server name: host chars only (alnum . -), the
;   WHOLE token valid, at least 2 chars — a mangled tail must never
;   overwrite the saved config (the N-Go wrote a lone 'n' before this).
_valid_server:
    ld   d, h            ; DE = start of fn
    ld   e, l
vs_loop:
    ld   a, (hl)
    or   a
    jr   z, vs_end       ; NUL: token scanned
    cp   '.'
    jr   z, vs_ok
    cp   '-'
    jr   z, vs_ok
    cp   '0'
    jr   c, vs_bad
    cp   '9'+1
    jr   c, vs_ok
    cp   'A'
    jr   c, vs_bad
    cp   'Z'+1
    jr   c, vs_ok
    cp   'a'
    jr   c, vs_bad
    cp   'z'+1
    jr   c, vs_ok
vs_bad:
    ld   hl, 0
    ret
vs_ok:
    inc  hl
    jr   vs_loop
vs_end:
    or   a               ; length = HL - DE, must be >= 2
    sbc  hl, de
    ld   a, h
    or   a
    jr   nz, vs_yes
    ld   a, l
    cp   2
    jr   c, vs_bad
vs_yes:
    ld   hl, 1
    ret
