; run.asm - the transfer display of the -anim eye-candy (v5.9.17): a little
; man who runs along a line while a file moves, the percentage and the rate
; under it, and the saucer that takes him when the transfer is interrupted.
;
; ZX Next Remote 1.13.0 shows a transfer this way; Julien asked for it in
; the dot too, "if memory of the .sync5 command permits", with an ending
; of its own when the transfer does not finish: a saucer flies over the
; man, sends down four rays of light - red, yellow, green, blue - and
; takes him away.
;
;   run_size(fn)   a put's command: the file's size, when the server sent
;                  it as eight hex digits after the path's NUL (ZX Next
;                  Remote 1.13.0+, ZX-Next-Unite 9.7.73+). Anything else
;                  there means "not told".
;   run_total      the same number, written by the C side for a file the
;                  dot serves (its own fstat). Zero = not known.
;   run_add(n)     n more bytes of the file have moved. The first call of
;                  a file draws the line and the text; every call counts,
;                  and - one step a video frame at most - moves the man
;                  and the green part of the line. Once a second the text
;                  is rewritten: "45%  12.3K/s", or "128K  12.3K/s" when
;                  the size is not known (then he runs laps).
;                  A wire that stalls stops his legs: nothing calls this.
;   run_end(ok)    the file is over. ok: the line is full, he stops with
;                  his arms up, and all of it stays until the next file.
;                  Not ok (BREAK, a link that died, a peer that gave up):
;                  the abduction, about a second and a half, played to the
;                  end before the dot goes on. A no-op when nothing moved.
;   run_init()     uploads the fixed patterns; called once, after
;                  anim_begin(), so only when the eye-candy is on (with
;                  -na every call here returns at its first test).
;   run_hide()     hides every sprite of the display; called at exit.
;
; EVERYTHING IS A SPRITE. The dot prints through the ROM and its screen
; scrolls, so nothing here writes the display file: the line is two
; pattern lines stretched by the sprite engine's X scale (8x and 4x make
; 192 pixels from two sprites), the green part is up to five more (128,
; 64, 32, 16 and 8 pixels, so it grows in steps of eight while the man
; moves by the pixel), and the text is seven sprites whose patterns are
; redrawn from a 16-glyph font once a second - about two milliseconds at
; 28 MHz, at a point where the link is idle.
;
; HAND ASSEMBLY BECAUSE OF THE BYTE BUDGET. The head page had 10 bytes
; free when this was written and the main bank's free space is the C
; stack; 5.9.17 bought the room by moving `scratch` (1280 bytes) into the
; banked window beside inbuf (uart.asm), and this file spends about 900
; of them where the same thing in C would have needed far more.
;
; Port-only, like anim.c: the sprite engine's pattern and attribute RAM
; are reached through ports 0x303B / 0x57 / 0x5B and nowhere near the
; CPU's memory map. The abduction's waits read FRAMES (the ROM's 50 Hz
; count, which keeps running while the dot works); each wait also gives
; up by itself after 65536 turns, so a machine whose interrupts were
; somehow off would play it too fast rather than hang.
;
; Sprites 6..25 and patterns 6..22: anim.c owns 0..5 of both. The saucer
; is anim.c's own pattern 3. State is DATA, not bss, for the reason anim.c
; gives: the top of bss is the run-stack's territory.

SECTION code_compiler

PUBLIC _run_init
PUBLIC _run_size
PUBLIC _run_add
PUBLIC _run_end
PUBLIC _run_hide
PUBLIC _run_cmd
PUBLIC _run_total

defc P_SLOT   = 0x303B          ; sprite / pattern slot select
defc P_ATTR   = 0x57            ; sprite attribute bytes, in order
defc P_PATT   = 0x5B            ; pattern pixels, in order
defc FRAMES   = 23672           ; the ROM's 50 Hz counter, low byte

defc TRANSP   = 0xE3
defc C_TEXT   = 0x1F            ; cyan
defc C_FILL   = 0x1C            ; green
defc C_TRACK  = 0x92            ; grey

defc SPR_MAN  = 6
defc SPR_UFO  = 7
defc SPR_RAY  = 8               ; 8..11
defc SPR_TRK  = 12              ; 12, 13
defc SPR_FILL = 14              ; 14..18
defc SPR_TXT  = 19              ; 19..25
defc SPR_END  = 26

defc PAT_UFO  = 3               ; anim.c's saucer
defc PAT_MAN  = 6               ; 6, 7 his strides, 8 arms up
defc PAT_RAY  = 9               ; 9..12
defc PAT_TRK  = 13              ; a 16-pixel line, grey
defc PAT_FILL = 14              ; the same, green
defc PAT_HALF = 15              ; 8 pixels of it
defc PAT_TXT  = 16              ; 16..22: the text, two glyphs each

defc X_LEFT   = 40              ; the line's left end (sprite coordinates)
defc BAR_W    = 192             ; its length
defc MAN_Y    = 204             ; his top: paper row 172, the screen's foot
defc BAR_Y    = MAN_Y + 2       ; pattern rows 14-15 land under his feet
defc TXT_Y    = MAN_Y + 22
defc UFO_Y    = MAN_Y - 26
defc RAY_Y    = MAN_Y - 17

defc STATE_LEN = 17             ; run_state .. run_state_end, below

defc G_PCT    = 10              ; glyph numbers after the ten digits
defc G_K      = 11
defc G_SLASH  = 12
defc G_S      = 13
defc G_DOT    = 14
defc G_SPACE  = 15

; ---- put: sprite B at x = D, y = E, attribute 3 = C (0 hides it) ----------
; Selecting the slot resets the attribute index. Keeps B, C, D, E, H, L.
put:
    push bc
    ld a, b
    ld bc, P_SLOT
    out (c), a
    pop bc
    ld a, d
    out (P_ATTR), a
    ld a, e
    out (P_ATTR), a
    xor a                       ; x msb 0, palette 0, no mirror
    out (P_ATTR), a
    ld a, c
    out (P_ATTR), a
    ret

; ---- bar: sprite B, a line pattern C at x = D, X scale bits L -------------
; (attribute 4 bits 4:3: 0x18 = 8x, 0x10 = 4x, 0x08 = 2x, 0 = 1x.)
; Keeps B, C, D, E, H, L.
bar:
    push bc
    ld a, b
    ld bc, P_SLOT
    out (c), a
    pop bc
    ld a, d
    out (P_ATTR), a
    ld a, BAR_Y
    out (P_ATTR), a
    xor a
    out (P_ATTR), a
    ld a, c
    or 0xC0                     ; visible, and a fifth byte follows
    out (P_ATTR), a
    ld a, l
    out (P_ATTR), a
    ret

; ---- void run_cmd(op) fastcall: a -listen command has arrived (5.9.18) -----
; The finished transfer's display stayed on screen for good: the man at the
; end of his line under every listing that followed. Any command clears it
; now - except the idle poll, and a crc ('K'), which clients send right
; after a put to check it: the result of that put is still what the screen
; is about. Falls into run_hide.
_run_cmd:
    ld a, (run_on)
    or a
    ret z                       ; the eye-candy is off: no sprite of ours
    ld a, l
    cp 'I'
    ret z
    cp 'K'
    ret z
; ---- void run_hide(void): every sprite of the display off ------------------
_run_hide:
    ld l, SPR_MAN
hide1:
    ld b, l
    ld c, 0
    call put
    inc l
    ld a, l
    cp SPR_END
    jr nz, hide1
    ret

; ---- wf: wait for the next video frame. Keeps everything but A. -----------
wf:
    push hl
    push de
    push bc
    ld hl, FRAMES
    ld e, (hl)
    ld bc, 0                    ; 65536 turns at most, then give up
wf1:
    ld a, (hl)
    cp e
    jr nz, wf2
    dec bc
    ld a, b
    or c
    jr nz, wf1
wf2:
    pop bc
    pop de
    pop hl
    ret

; ---- one pose: pattern slot A from the 16 row bytes at HL (HL moves on) ----
; Rows 0-4 white (his head), 5-9 red (his shirt), 10-15 cyan (his legs).
man_up:
    ld bc, P_SLOT
    out (c), a
    ld c, 16
man1:
    ld d, (hl)
    inc hl
    ld a, c                     ; c counts 16..1: row = 16 - c
    ld e, 0xFF
    cp 12
    jr nc, man2
    ld e, 0xE0
    cp 7
    jr nc, man2
    ld e, 0x1F
man2:
    ld b, 8
man3:
    sla d
    ld a, TRANSP
    jr nc, man4
    ld a, e
man4:
    out (P_PATT), a
    djnz man3
    ld b, 8                     ; the right half of the pattern is empty
    ld a, TRANSP
man5:
    out (P_PATT), a
    djnz man5
    dec c
    jr nz, man1
    ret

; ---- one ray: pattern slot A, a line two pixels wide in colour E ----------
ray_up:
    ld bc, P_SLOT
    out (c), a
    ld b, 0                     ; 256 pixels
ray1:
    ld a, b
    and 0x0E
    cp 8                        ; two columns of every row
    ld a, TRANSP
    jr nz, ray2
    ld a, e
ray2:
    out (P_PATT), a
    djnz ray1
    ret

; ---- one line: pattern slot A, colour E, rows 14-15 lit -------------------
; D = 0: all sixteen columns; D = 8: the left eight only. Keeps D, E.
line_up:
    ld bc, P_SLOT
    out (c), a
    ld b, 0                     ; pixel n is sent with B = (256 - n) & 255
line1:
    ld a, b
    dec a                       ; rows 14-15 are n >= 224: B - 1 in 0..31
    cp 32
    jr nc, line3
    ld c, a
    ld a, d
    or a
    jr z, line2
    ld a, c
    and 8                       ; columns 0-7 are where bit 3 of B - 1 is set
    jr z, line3
line2:
    ld a, e
    jr line4
line3:
    ld a, TRANSP
line4:
    out (P_PATT), a
    djnz line1
    ret

; ---- void run_init(void) --------------------------------------------------
_run_init:
    ld hl, man_rows
    ld a, PAT_MAN
    call man_up
    ld a, PAT_MAN + 1
    call man_up
    ld a, PAT_MAN + 2
    call man_up                 ; HL now at ray_cols
    ld d, PAT_RAY
init1:
    ld e, (hl)
    inc hl
    ld a, d
    call ray_up
    inc d
    ld a, d
    cp PAT_RAY + 4
    jr nz, init1
    ld d, 0
    ld e, C_TRACK
    ld a, PAT_TRK
    call line_up
    ld e, C_FILL
    ld a, PAT_FILL
    call line_up
    ld d, 8
    ld a, PAT_HALF
    call line_up
    ld a, 1
    ld (run_on), a
    ret

; ---- void run_size(char *fn) __z88dk_fastcall ------------------------------
; The eight hex digits after the path's NUL, most significant first, into
; run_total; anything that is not eight hex digits leaves it zero.
_run_size:
    xor a
size1:
    cp (hl)
    inc hl
    jr nz, size1                ; HL is past the NUL
    ld de, _run_total + 3
    ld b, 4
size2:
    call hexn
    jr c, size3
    rlca
    rlca
    rlca
    rlca
    ld c, a
    call hexn
    jr c, size3
    or c
    ld (de), a
    dec de
    djnz size2
    ret
size3:
    ld hl, 0
    ld (_run_total), hl
    ld (_run_total + 2), hl
    ret

; one hex digit at (HL) -> A, HL moves on; carry set when it is not one
hexn:
    ld a, (hl)
    inc hl
    sub '0'
    ret c
    cp 10
    ccf
    ret nc                      ; 0..9
    sub 7
    cp 10
    ret c                       ; between '9' and 'A'
    cp 16
    ccf
    ret

; ---- small helpers ---------------------------------------------------------
; Z when the size is not known. Keeps BC, DE.
tot0:
    ld hl, _run_total
    ld a, (hl)
    inc hl
    or (hl)
    inc hl
    or (hl)
    inc hl
    or (hl)
    ret

; (DE) += HL, three bytes. Keeps HL.
add24:
    ld a, (de)
    add a, l
    ld (de), a
    inc de
    ld a, (de)
    adc a, h
    ld (de), a
    inc de
    ld a, (de)
    adc a, 0
    ld (de), a
    ret

; run_acc -= run_total (carry = it did not fit) / run_acc += run_total
sub32:
    ld hl, run_acc
    ld de, _run_total
    ld b, 4
    or a
sub32a:
    ld a, (de)
    ld c, a
    ld a, (hl)
    sbc a, c
    ld (hl), a
    inc hl
    inc de
    djnz sub32a
    ret
add32:
    ld hl, run_acc
    ld de, _run_total
    ld b, 4
    or a
add32a:
    ld a, (de)
    adc a, (hl)
    ld (hl), a
    inc hl
    inc de
    djnz add32a
    ret

; ---- void run_add(unsigned short n) __z88dk_fastcall -----------------------
_run_add:
    ld a, (run_on)
    or a
    ret z                       ; the eye-candy is off
    cp 2
    jr z, add1
    push hl
    call start                  ; this file's first bytes
    pop hl
add1:
    ld de, run_done
    call add24
    ld de, run_sec
    call add24
    ; run_acc += n * 192; each run_total of it is one pixel of the line
    ld d, h
    ld e, l
    add hl, hl
    add hl, de                  ; 3n (n is 1024 at most)
    ld e, 0
    ld b, 6
add2:
    add hl, hl
    rl e
    djnz add2                   ; E:HL = n * 192
    ld b, h
    ld c, l
    ld hl, run_acc
    ld a, (hl)
    add a, c
    ld (hl), a
    inc hl
    ld a, (hl)
    adc a, b
    ld (hl), a
    inc hl
    ld a, (hl)
    adc a, e
    ld (hl), a
    inc hl
    jr nc, add3
    inc (hl)
add3:
    call tot0
    jr z, add6                  ; size not known: he runs laps
add4:
    ld a, (run_px)
    cp BAR_W
    jr nc, add6
    call sub32
    jr c, add5
    ld hl, run_px
    inc (hl)
    jr add4
add5:
    call add32
add6:
    ld hl, run_fr
    ld a, (FRAMES)
    cp (hl)
    ret z                       ; one step a frame
    ld (hl), a
    call tot0
    jr nz, add8
    ld hl, run_px               ; laps
    ld a, (hl)
    add a, 2
    cp BAR_W
    jr c, add7
    xor a
add7:
    ld (hl), a
add8:
    call show
    ld hl, run_t0               ; once a second: the rate, and the text
    ld a, (FRAMES)
    sub (hl)
    cp 50
    ret c
    ld a, (FRAMES)
    ld (hl), a
    ld hl, (run_sec + 1)        ; bytes this second / 256
    ld (run_rate), hl
    ld hl, 0
    ld (run_sec), hl
    xor a
    ld (run_sec + 2), a
    ; fall into text

; ---- the text: build its fourteen glyphs, then draw them ------------------
text:
    ld hl, run_txt
    ld b, 14
    ld a, G_SPACE
text1:
    ld (hl), a
    inc hl
    djnz text1
    ld de, run_txt
    call tot0
    jr z, text3
    ld a, (run_px)              ; percent = pixels * 100 / 192
    ld l, a
    ld h, 0
    ld b, h
    ld c, l
    add hl, hl
    add hl, bc                  ; 3x
    add hl, hl
    add hl, hl
    add hl, hl                  ; 24x
    add hl, bc                  ; 25x
    add hl, hl
    add hl, hl                  ; 100x
    ld bc, 0 - BAR_W
    xor a
text2:
    add hl, bc
    jr nc, text2a
    inc a
    jr text2
text2a:
    ld l, a
    ld h, 0
    call dec16
    ld a, G_PCT
    jr text4
text3:
    ld hl, (run_done + 1)       ; kilobytes moved = bytes >> 10
    srl h
    rr l
    srl h
    rr l
    call dec16
    ld a, G_K
text4:
    ld (de), a
    ld de, run_txt + 6          ; the rate's field
    ld hl, (run_rate)
    ld a, h
    or l
    jr z, text_up               ; no full second yet
    ld a, l
    and 3
    ld c, a                     ; quarters of a kilobyte
    srl h
    rr l
    srl h
    rr l                        ; HL = kilobytes a second
    ld a, h
    or a
    jr nz, text5
    ld a, l
    cp 100
    jr nc, text5
    push bc
    call dec16
    pop bc
    ld a, G_DOT
    ld (de), a
    inc de
    ld a, c                     ; 0, 1, 2, 3 quarters -> .0 .2 .5 .7
    add a, a
    cp 4
    jr c, text4a
    inc a
text4a:
    ld (de), a
    inc de
    jr text6
text5:
    call dec16
text6:
    ld hl, rate_sfx
    ld bc, 3
    ldir
    ; fall into text_up

; ---- draw run_txt into the seven text patterns ----------------------------
text_up:
    ld hl, run_txt
    ld d, PAT_TXT
tup1:
    ld a, d
    ld bc, P_SLOT
    out (c), a
    ld e, 0                     ; glyph row 0..7
tup2:
    call glyph_row              ; the left glyph's eight pixels
    inc hl
    call glyph_row              ; the right one's
    dec hl
    inc e
    ld a, e
    cp 8
    jr nz, tup2
    ld b, 128                   ; rows 8..15 are empty
    ld a, TRANSP
tup3:
    out (P_PATT), a
    djnz tup3
    inc hl
    inc hl
    inc d
    ld a, d
    cp PAT_TXT + 7
    jr nz, tup1
    ret

; eight pixels: row E of the glyph numbered (HL). Keeps DE, HL.
glyph_row:
    push hl
    ld a, (hl)
    add a, a
    add a, a
    add a, a
    add a, e
    ld l, a
    ld h, 0
    ld bc, font
    add hl, bc
    ld c, (hl)
    ld b, 8
grow1:
    sla c
    ld a, TRANSP
    jr nc, grow2
    ld a, C_TEXT
grow2:
    out (P_PATT), a
    djnz grow1
    pop hl
    ret

; HL in decimal, no leading zeros, as glyph numbers at (DE); DE moves on.
dec16:
    xor a
    ld (run_lz), a
    ld bc, 0 - 10000
    call dec1
    ld bc, 0 - 1000
    call dec1
    ld bc, 0 - 100
    call dec1
    ld bc, 0 - 10
    call dec1
    ld a, l
    ld (de), a
    inc de
    ret
dec1:
    xor a
dec2:
    add hl, bc
    jr nc, dec3
    inc a
    jr dec2
dec3:
    sbc hl, bc                  ; carry is clear: this puts the last one back
    or a
    jr nz, dec4
    ld a, (run_lz)
    or a
    ret z                       ; a leading zero
    xor a
dec4:
    ld (de), a
    inc de
    ld a, 1
    ld (run_lz), a
    ret

; ---- a file starts: the line, the text, nothing of the last file ----------
start:
    call _run_hide
    ld a, 2
    ld (run_on), a
    ld a, (FRAMES)
    ld (run_t0), a
    ld b, SPR_TRK
    ld c, PAT_TRK
    ld d, X_LEFT
    ld l, 0x18
    call bar                    ; 128 pixels
    inc b
    ld d, X_LEFT + 128
    ld l, 0x10
    call bar                    ; and 64
    call text
    ld b, SPR_TXT
    ld c, 0x80 + PAT_TXT
    ld d, X_LEFT
    ld e, TXT_Y
start1:
    call put
    inc b
    inc c
    ld a, d
    add a, 16
    ld d, a
    ld a, b
    cp SPR_TXT + 7
    jr nz, start1
    ret

; ---- the man on the line, and the green part of it -------------------------
show:
    ld a, (run_px)
    sub 8                       ; he stands on the line's tip
    jr nc, show1
    xor a
show1:
    add a, X_LEFT
    ld (run_x), a
    ld d, a
    ld a, (FRAMES)
    and 4                       ; the other stride every four frames
    rrca
    rrca
    add a, 0x80 + PAT_MAN
    ld c, a
    ld e, MAN_Y
    ld b, SPR_MAN
    call put
    call tot0
    ret z                       ; laps: no green
    ld a, (run_px)
    ld h, a
    ld d, X_LEFT
    ld b, SPR_FILL
    ld c, PAT_FILL
    ld l, 0x18
    ld e, 128
    call seg
    ld l, 0x10
    ld e, 64
    call seg
    ld l, 0x08
    ld e, 32
    call seg
    ld l, 0
    ld e, 16
    call seg
    ld c, PAT_HALF
    ld e, 8
    ; fall into seg

; sprite B: E pixels of line at D when H has that bit, else hidden. B moves on.
seg:
    ld a, h
    and e
    jr z, seg1
    call bar
    ld a, d
    add a, e
    ld d, a
    inc b
    ret
seg1:
    push bc
    ld c, 0
    call put
    pop bc
    inc b
    ret

; ---- void run_end(unsigned char ok) __z88dk_fastcall  (ok in L) -----------
_run_end:
    ld a, (run_on)
    cp 2
    ret nz                      ; no file was moving
    ld a, l
    or a
    jr z, abduct
    call tot0
    jr z, end1
    ld a, BAR_W                 ; landed: the whole line
    ld (run_px), a
end1:
    call show
    call text
    ld a, (run_x)               ; arms up, where he stands
    ld d, a
    ld e, MAN_Y
    ld bc, SPR_MAN * 256 + 0x80 + PAT_MAN + 2
    call put
    jp reset

abduct:
    ; The saucer flies in from the left until it is over him. H = its x.
    ld h, 0
abd1:
    ld b, SPR_UFO
    ld c, 0x80 + PAT_UFO
    ld d, h
    ld e, UFO_Y
    call put
    call wf
    ld a, h
    add a, 4
    ld h, a
    ld a, (run_x)
    sub 4                       ; the saucer is twice his width
    cp h
    jr nc, abd1
    ; Four rays, one after the other, side by side over him. L = which.
    ld l, 0
abd2:
    ld a, (run_x)
    sub 10                      ; a ray's line is in the middle of its pattern
    ld d, a
    ld a, l
    add a, a
    add a, a
    add a, d
    ld d, a
    ld a, l
    add a, SPR_RAY
    ld b, a
    add a, 0x80 + PAT_RAY - SPR_RAY
    ld c, a
    ld e, RAY_Y
    call put
    ld b, 6
abd3:
    call wf
    djnz abd3
    inc l
    ld a, l
    cp 4
    jr nz, abd2
    ; He rises into the saucer, arms up. L = his y.
    ld l, MAN_Y
abd4:
    ld a, (run_x)
    ld d, a
    ld e, l
    ld bc, SPR_MAN * 256 + 0x80 + PAT_MAN + 2
    call put
    call wf
    dec l
    ld a, l
    cp UFO_Y + 4
    jr nz, abd4
    ; Everything goes; the saucer leaves, straight up.
    push hl
    call _run_hide
    pop hl
    ld l, UFO_Y
abd5:
    ld b, SPR_UFO
    ld c, 0x80 + PAT_UFO
    ld d, h
    ld e, l
    call put
    call wf
    ld a, l
    sub 8
    ld l, a
    cp 12
    jr nc, abd5
    call _run_hide
    ; fall into reset

; ---- between files: every count back to zero -------------------------------
reset:
    ld hl, run_state
    ld b, STATE_LEN
    xor a
reset1:
    ld (hl), a
    inc hl
    djnz reset1
    inc a
    ld (run_on), a
    ret

; ---- the art ---------------------------------------------------------------
SECTION rodata_compiler

man_rows:
    ; stride A
    defb @00011000
    defb @00111100
    defb @00111100
    defb @00011000
    defb @00011000
    defb @00111100
    defb @01111110
    defb @10111101
    defb @00111100
    defb @00111100
    defb @00100100
    defb @01100110
    defb @01000010
    defb @11000011
    defb @00000000
    defb @00000000
    ; stride B
    defb @00011000
    defb @00111100
    defb @00111100
    defb @00011000
    defb @00011000
    defb @00111100
    defb @00111110
    defb @00111100
    defb @01111100
    defb @00111100
    defb @00011000
    defb @00011000
    defb @00011000
    defb @00111000
    defb @00011100
    defb @00000000
    ; arms up
    defb @00011000
    defb @10111101
    defb @10111101
    defb @10011001
    defb @01011010
    defb @00111100
    defb @00111100
    defb @00111100
    defb @00111100
    defb @00111100
    defb @00100100
    defb @00100100
    defb @00100100
    defb @00100100
    defb @01100110
    defb @00000000
ray_cols:
    defb 0xE0, 0xFC, 0x1C, 0x03 ; red, yellow, green, blue
rate_sfx:
    defb G_K, G_SLASH, G_S
font:                           ; 8x8, in glyph-number order
    defb 0x3C, 0x66, 0x6E, 0x76, 0x66, 0x66, 0x3C, 0x00   ; 0
    defb 0x18, 0x38, 0x18, 0x18, 0x18, 0x18, 0x7E, 0x00   ; 1
    defb 0x3C, 0x66, 0x06, 0x0C, 0x30, 0x60, 0x7E, 0x00   ; 2
    defb 0x3C, 0x66, 0x06, 0x1C, 0x06, 0x66, 0x3C, 0x00   ; 3
    defb 0x0C, 0x1C, 0x3C, 0x6C, 0x7E, 0x0C, 0x0C, 0x00   ; 4
    defb 0x7E, 0x60, 0x7C, 0x06, 0x06, 0x66, 0x3C, 0x00   ; 5
    defb 0x3C, 0x66, 0x60, 0x7C, 0x66, 0x66, 0x3C, 0x00   ; 6
    defb 0x7E, 0x06, 0x0C, 0x18, 0x30, 0x30, 0x30, 0x00   ; 7
    defb 0x3C, 0x66, 0x66, 0x3C, 0x66, 0x66, 0x3C, 0x00   ; 8
    defb 0x3C, 0x66, 0x66, 0x3E, 0x06, 0x66, 0x3C, 0x00   ; 9
    defb 0x62, 0x66, 0x0C, 0x18, 0x30, 0x66, 0x46, 0x00   ; %
    defb 0x66, 0x6C, 0x78, 0x70, 0x78, 0x6C, 0x66, 0x00   ; K
    defb 0x02, 0x06, 0x0C, 0x18, 0x30, 0x60, 0x40, 0x00   ; /
    defb 0x00, 0x00, 0x3E, 0x60, 0x3C, 0x06, 0x7C, 0x00   ; s
    defb 0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x00   ; .
    defb 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00   ; space

; ---- the state: DATA on purpose (see the header) ---------------------------
SECTION data_compiler

run_on:  defb 0                 ; 0 eye-candy off, 1 on, 2 a file is moving
run_fr:  defb 0                 ; FRAMES at the last step
run_x:   defb X_LEFT            ; where the man is
run_t0:  defb 0                 ; FRAMES when this second began
run_lz:  defb 0                 ; dec16: a digit has been written
run_state:                      ; zeroed between files
_run_total: defb 0, 0, 0, 0     ; the file's size, 0 = not known
run_acc:    defb 0, 0, 0, 0     ; bytes * 192 not yet turned into pixels
run_done:   defb 0, 0, 0        ; bytes moved
run_sec:    defb 0, 0, 0        ; bytes moved this second
run_rate:   defb 0, 0           ; last second's bytes / 256, 0 = none yet
run_px:     defb 0              ; pixels of the line done (0..192)
run_state_end:
run_txt: defs 14, 15            ; the text, as glyph numbers
