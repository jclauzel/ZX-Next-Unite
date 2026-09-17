; LOCAL OVERRIDE of z88dk's crt_cmdline_esxdos.inc (dot 5.9.7).
;
; WHY THIS FILE EXISTS. NextZXOS hands a dot command a RAW POINTER into live
; BASIC memory - the API doc says the address "usually points within a BASIC
; statement", and for a TYPED command that statement is the edit line at
; (E_LINE) = PROG + program length + variables length + 1. So the tail's
; address CLIMBS with the size of the user's BASIC program.
;
; A dotN then maps its own freshly allocated pages over $8000-$BFFF. Once the
; tail has climbed into that window the dot has paged BASIC's memory out from
; under the pointer it was given, and reads ITS OWN CODE AND BSS instead.
; Nothing is corrupted - the user's program is safe in bank 2 and LISTs
; intact - but the command line the dot parses is compiled Z80.
;
; Hardware-proven 2026-09-17, and it is a 16 KB BAND rather than a cliff:
;
;     program+vars  <  ~9005   tail below $8000   works
;     program+vars ~9005-25397 tail in  $8000-$BFFF  BROKEN
;     program+vars  >  ~25397  tail above $C000   works again
;
; (6930 works, 11276 fails, 26404 works again - all three measured on a real
; Next and an N-GO. Only a paging explanation predicts the third.)
;
; The same bug at the TOP of the map was fixed at v5.6 by
; DOTN_MAIN_ABSOLUTE_MASK = 0xc0, which forces mmu6/mmu7 back to bank 0.
; That covered $C000-$FFFF. $8000-$BFFF has been dark ever since.
;
; THE FIX IS TIMING. The crt dereferences the pointer ONCE, at
; zxn_crt_798.asm.m4:546, with SP still at DOTN_REGISTER_SP = $4000 - the
; dot's DivMMC head page, which is never repaged - and about twenty
; instructions BEFORE the only mmu4/mmu5 writes in the whole crt (:572,
; :575). Copying the tail there takes it while BASIC's own bank is still
; mapped. By the time main() runs it is already too late: _main and
; _tail_copy physically occupy the addresses the tail would have been at.
;
; WHY NOT JUST SET CRT_ENABLE_COMMANDLINE = 4 AND USE THE LIBRARY COPY. Two
; reasons, both real:
;
;   * Its length scan (crt_cmdline_esxdos_len.inc) stops at the first ':'
;     outside double quotes, so ".sync5 -send c:/foo" would arrive as
;     "-send c". nextsync.c reads drive-letter paths out of the raw line, so
;     that is a live regression. NextZXOS does document ':' as a terminator,
;     so the library is not wrong - it is just stricter than this dot needs.
;   * It caps at 128 bytes where tail_copy's contract is 158, and it pulls in
;     l_minu_bc_hl and l_neg_hl for arithmetic that is three instructions
;     inline. The head page has single-digit bytes of slack.
;
; This override therefore keeps mode 4's TIMING and replaces its scan:
; terminate on $00 or $0D only, cap at 160, no library helpers.
;
; CONTRACT KEPT EXACTLY: [len][ptr] pushed in the same order the library
; uses, NUL-terminated, and the no-command-line case pushes HL twice just as
; the library does - so main(int, char *) is unchanged and tail_copy stays as
; defence in depth, simply re-copying from the head page instead of from
; BASIC's.

   IF __crt_enable_commandline < 4

      ; This override implements mode 4 only. Anything else means zpragma.inc
      ; and this file have drifted apart, and silently falling back to the
      ; library copy would restore the bug this file exists to fix.
      defc CRT_CMDLINE_OVERRIDE_REQUIRES_MODE_4 = 1/0

   ENDIF

   IF __crt_enable_commandline >= 4

      ; hl = command line, or 0 when there are no arguments at all

      ld a,h
      or l

      jr nz, cl_have_line

   cl_none:

      push hl                  ; command line = 0
      push hl                  ; length      = 0

      jr cl_end

   cl_have_line:

      ld e,l
      ld d,h                   ; de = the source, kept across the scan

      ; ---- length: $00 / $0D terminate, hard cap 160 --------------------
      ; Deliberately NOT ':'-aware (see the header). bc stays under 256, so
      ; only c is tested against the cap and b is known zero throughout.

      ld bc,0

   cl_scan:

      ld a,(hl)
      or a
      jr z, cl_scanned         ; $00 ends
      cp 0x0d
      jr z, cl_scanned         ; $0D ends

      inc hl
      inc bc

      ld a,c
      cp 160
      jr c, cl_scan            ; under the cap, keep going

   cl_scanned:

      inc bc                   ; room for the terminator we write below

      ; ---- carve bc bytes off the stack (still the head page's $4000) ----

      ld hl,0
      or a                     ; clear carry before the sbc
      sbc hl,bc                ; hl = -bc
      add hl,sp
      ld sp,hl

      push hl                  ; command line (the copy)
      push bc                  ; length

      ex de,hl                 ; hl = source, de = destination
      ldir

      dec de
      xor a
      ld (de),a                ; terminator becomes a real NUL

   cl_end:

   ENDIF
