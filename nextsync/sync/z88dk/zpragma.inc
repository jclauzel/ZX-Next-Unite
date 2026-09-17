// ---------------------------------------------------------------------------
// zpragma.inc - crt configuration for the NextSync dotN command.
//
// Memory model (kept entirely inside mmu4+mmu5 so it never disturbs NextZXOS,
// which stays paged in at mmu6/mmu7 for the esxDOS file calls):
//
//   $2000-$3FFF  primary 8 KB dot page  : crt + clib + drivers
//   $8000-$BFFF  "main bank" (mmu4/5)   : our C code, rodata, data,
//                                         the big static buffers, and the stack
//   $C000-$FFFF  (mmu6/7)               : left untouched = NextZXOS
//
// The big I/O buffers (inbuf/scratch/fn/...) are file-scope statics rather than
// stack locals, so (a) they land in bss inside the main bank and force mmu4+mmu5
// to be allocated, and (b) the stack stays small.
// ---------------------------------------------------------------------------

// Main bank starts at mmu4; code + statics grow up from here.
#pragma output CRT_ORG_MAIN = 0x8000

// Stack starts at the very top of mmu5 and grows down toward the statics
// (z80 push pre-decrements, so the first push lands at $BFFE - still inside
// our allocated page; $C000 itself is mmu6 = NextZXOS and is never written).
// v5.2 used $BF00, wasting the top 256 bytes: nothing in the dotn crt or the
// clib touches $BF00-$BFFF (verified against the crt source and the map),
// and after v5.3's spinner/chunked-rcpy additions the stack headroom
// (__BSS_END_tail vs here - THE budget to check after every build) needed
// that quarter-KB back.
#pragma output REGISTER_SP = 0xc000

// Allocate ONLY mmu4+mmu5 for the main bank — make the loader match the
// memory model documented above. Without this, appmake's dotn loader claims
// every page from CRT_ORG_MAIN to the top (allocation mask 0xf0), so
// NextZXOS maps FRESH banks over $C000-$FFFF before main() runs. On
// machines whose BASIC editor keeps the typed line in that region (N-Go
// clones), the command-tail buffer the OS handed us was swapped away
// mid-launch: the v5.6 diag build showed ptr=$E1D0 pointing at another
// bank's tokenized BASIC, which is why ".sync5 <ip>" parsed garbage.
//
// CORRECTED 5.9.7 - the fix is right, this explanation of it was wrong, and
// the fix's own mechanics disprove it. 0xc0 does not "leave the OS banks in
// place": the crt writes all six MMU slots unconditionally, and for an
// absolute page appmake fills the table with an identity map, so mmu6/mmu7
// are FORCED to physical pages 0/1 = bank 0 - which is exactly what 128K
// BASIC already has at $C000. Before the fix those entries were 0xFE
// (allocate-only), i.e. FRESH banks carrying stale data mapped over
// $C000-$FFFF. Had the N-Go really kept the line in some other bank up
// there, forcing bank 0 in would have swapped it away just as thoroughly
// and v5.6 would have fixed nothing.
//
// There is no N-Go special case. $E1D0 is simply (E_LINE) for a program of
// roughly 34 KB - an ordinary address for an ordinary large program - and
// the bug is that a dotN pages BASIC out from under the pointer NextZXOS
// gave it. v5.6 fixed the UPPER window. $8000-$BFFF stayed dark until 5.9.7;
// see CRT_ENABLE_COMMANDLINE below.
#pragma output DOTN_MAIN_ABSOLUTE_MASK = 0xc0

// Hand main() the unprocessed command line (len in 1st arg, pointer in 2nd) so
// the app can do its own parsing and keep ':' in paths like "c:/foo".
//
// 2 -> 4 AT 5.9.7, AND IT IS THE TIMING THAT MATTERS, not the format. Mode 2
// hands main() a RAW POINTER into live BASIC memory - the NextZXOS API doc
// says the address "usually points within a BASIC statement", and for a typed
// command that statement is the edit line at (E_LINE) = PROG + program length
// + variables length + 1. The tail's address therefore CLIMBS with the size
// of the user's BASIC program, and once it reaches $8000 this dot has mapped
// its own pages over that window and reads ITS OWN CODE as the command line.
//
// A 16 KB BAND, measured on hardware: program+vars under ~9005 works, ~9005
// to ~25397 is BROKEN, above ~25397 works again because the tail climbs out
// past $C000. (6930 ok / 11276 broken / 26404 ok again, on a real Next and an
// N-GO.) Nothing is corrupted - BASIC's program is safe in its own bank and
// LISTs intact; only our VIEW of it goes dark, which is why this looked like
// a parsing bug for so long.
//
// Mode 4 copies the tail at zxn_crt_798.asm.m4:546, with SP still at
// DOTN_REGISTER_SP ($4000 - the DivMMC head page, never repaged) and about
// twenty instructions BEFORE the crt's only mmu4/mmu5 writes. That is the
// entire fix: the copy is taken while BASIC's own bank is still mapped. No
// user code can be early enough - by the time main() runs, _main and
// _tail_copy physically occupy the addresses the tail was at.
//
// The library's mode 4 is NOT what runs: crt_cmdline_esxdos.inc in THIS
// directory overrides it, because the library's length scan stops at the
// first ':' (so "-send c:/foo" would arrive as "-send c") and caps at 128
// where tail_copy's contract is 158. The override keeps mode 4's timing,
// terminates on $00/$0D only, caps at 160, and by dropping l_minu_bc_hl and
// the ':'-aware scan costs the head page NOTHING - measured one byte smaller
// than mode 2.
//
// NOT 1: on this target 1 means "push an EMPTY argc/argv", not "copy the
// line" - it would break every invocation on every machine at every size.
#pragma output CRT_ENABLE_COMMANDLINE = 4

// Take the command line from HL (the argument tail, WITHOUT the ".sync" program
// name), matching the original SDCC crt0. The dotN default is 0x80, whose bit 7
// takes it from BC *with* the program name - that makes a bare ".sync" look like
// it was given "sync" as a server argument, so it saves config instead of
// syncing.
#pragma output CRT_ENABLE_COMMANDLINE_EX = 0

// Cover the main-bank logical->physical page table.
#pragma output DOTN_LAST_PAGE = 11

// ONE manually-paged extra 8 KB data page, and no divmmc pages.
// The dotN loader asks NextZXOS for it (IDE_BANK through the M_P3DOS
// hook) BEFORE main() runs - i.e. while the stack is still the crt's and
// not ours - and frees it again on every exit path that reaches
// terminate. Do NOT be tempted to allocate a page at run time instead:
// M_P3DOS remaps $8000-$BFFF, which is this dot's code AND its stack
// (see syncsys.c's getdrives note - the dot died on hardware doing
// exactly that). The page is allocated but NOT loaded
// (__z_extra_alloc_table defaults to 0xfe), so nothing is appended to the
// dot file, and the loader maps it nowhere - main() maps it over mmu3 /
// $6000-$7FFF itself, where inbuf now lives. See map_data_bank in
// uart.asm.
#pragma output DOTN_NUM_EXTRA = 1

// We only run under 128 K NextZXOS; do not try to load a 48 K alternate.
#pragma output DOTN_ENABLE_ALTERNATE = 0

// Room for one atexit handler (the clib file-close on exit).
#pragma output CLIB_EXIT_STACK_SIZE = 1

// Keep printf small (we only ever go through fputs, but this bounds any pull-in).
#pragma printf = "%s %u"
