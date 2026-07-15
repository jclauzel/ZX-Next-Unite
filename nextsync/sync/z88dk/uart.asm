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
;   HL = b (destination buffer)
;   -> HL = number of bytes received
_receive:
    ld   d, h            ; DE = destination buffer
    ld   e, l
    ld   hl, 0           ; HL = running count
    ld   bc, 0x133b      ; UART Tx/status port

nextbyte:
    in   a, (c)          ; read status @ 0x133b
    and  0x01
    jr   z, done         ; bit0 clear -> nothing waiting, finished
    inc  b               ; B: 0x13 -> 0x14  (port 0x143b = UART Rx)
    in   a, (c)          ; read the incoming byte
    ld   (de), a         ; store it
    and  0x07
    out  (0xfe), a       ; flash the border with the low bits
    inc  de              ; advance the buffer
    inc  hl              ; count++
    dec  b               ; B: 0x14 -> 0x13  (back to status)
    jp   nextbyte

done:
    xor  a
    out  (0xfe), a       ; border back to black
    ret                  ; HL = count
