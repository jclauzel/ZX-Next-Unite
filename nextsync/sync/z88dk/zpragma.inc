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

// Stack sits near the top of mmu5 and grows down toward the statics. There is
// ~16 KB between $8000 and here; content is well under that.
#pragma output REGISTER_SP = 0xbf00

// Hand main() the unprocessed command line (len in 1st arg, pointer in 2nd) so
// the app can do its own parsing and keep ':' in paths like "c:/foo".
#pragma output CRT_ENABLE_COMMANDLINE = 2

// Take the command line from HL (the argument tail, WITHOUT the ".sync" program
// name), matching the original SDCC crt0. The dotN default is 0x80, whose bit 7
// takes it from BC *with* the program name - that makes a bare ".sync" look like
// it was given "sync" as a server argument, so it saves config instead of
// syncing.
#pragma output CRT_ENABLE_COMMANDLINE_EX = 0

// Cover the main-bank logical->physical page table.
#pragma output DOTN_LAST_PAGE = 11

// No manually-paged extra data pages and no divmmc pages.
#pragma output DOTN_NUM_EXTRA = 0

// We only run under 128 K NextZXOS; do not try to load a 48 K alternate.
#pragma output DOTN_ENABLE_ALTERNATE = 0

// Room for one atexit handler (the clib file-close on exit).
#pragma output CLIB_EXIT_STACK_SIZE = 1

// Keep printf small (we only ever go through fputs, but this bounds any pull-in).
#pragma printf = "%s %u"
