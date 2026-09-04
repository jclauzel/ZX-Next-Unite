; crc32.asm - CRC-32 (IEEE 802.3, reflected 0xEDB88320, init/final FFFFFFFF:
; zlib.crc32's value) for the 'K' listener op (v5.9.2).
;
; Two homes, on purpose. The bit-serial core sits in the HEAD PAGE
; (code_dot): 62 bytes there, where the page had 88 left, instead of in a
; main bank whose every byte is stack headroom. No lookup table - the page
; has no room for one - so it runs at roughly 30 KB/s at 28 MHz, enough for
; a verification verb (ZX Next Remote's twin is table-driven and faster).
; The hex formatter is MAIN BANK (code_compiler) because the page could not
; take both. The accumulator is main-bank bss (_crc_acc in nextsync.c): the
; head page's data/bss top is the crt's exit-stack territory (5.7.3).
;
; crc_run() in nextsync.c resets the accumulator to FFFFFFFF before the
; first update; crc_hex complements it on the way out.

SECTION code_dot

PUBLIC _crc_update
EXTERN _crc_acc

; void crc_update(unsigned char *p, unsigned short n) __z88dk_callee
;   The callee pops its arguments: p is on top (sdcc pushes right to left).
;   Accumulator byte order in memory is little-endian; the 32-bit value is
;   held as D:E:H:L (D highest) for the four-register shift.
_crc_update:
    pop  bc              ; return address
    pop  hl              ; p
    pop  de              ; n
    push bc
crcu_byte:
    ld   a, d
    or   e
    ret  z
    dec  de
    ld   a, (hl)
    inc  hl
    push hl
    push de
    ld   hl, (_crc_acc)     ; L = byte 0, H = byte 1
    ld   de, (_crc_acc + 2) ; E = byte 2, D = byte 3
    xor  l
    ld   l, a
    ld   b, 8
crcu_bit:
    srl  d
    rr   e
    rr   h
    rr   l
    jr   nc, crcu_noxor
    ld   a, l
    xor  0x20
    ld   l, a
    ld   a, h
    xor  0x83
    ld   h, a
    ld   a, e
    xor  0xb8
    ld   e, a
    ld   a, d
    xor  0xed
    ld   d, a
crcu_noxor:
    djnz crcu_bit
    ld   (_crc_acc), hl
    ld   (_crc_acc + 2), de
    pop  de
    pop  hl
    jr   crcu_byte

SECTION code_compiler

PUBLIC _crc_hex

; void crc_hex(char *dst) __z88dk_fastcall
;   HL = dst. Writes the 8 upper-case hex digits of ~acc, most significant
;   first (byte 3 down to byte 0), no terminator. The add/daa/adc/daa pair is
;   the classic nibble-to-ASCII-hex conversion.
_crc_hex:
    ld   de, _crc_acc + 3
    ld   b, 4
crch_byte:
    ld   a, (de)
    cpl
    push af
    rrca
    rrca
    rrca
    rrca
    call crch_nib
    pop  af
    call crch_nib
    dec  de
    djnz crch_byte
    ret
crch_nib:
    and  0x0f
    add  a, 0x90
    daa
    adc  a, 0x40
    daa
    ld   (hl), a
    inc  hl
    ret
