/* 
 * Part of Jari Komppa's zx spectrum next suite 
 * https://github.com/jarikomppa/specnext
 * released under the unlicense, see http://unlicense.org 
 * (practically public domain) 
 * Updated by Julien Clauzel
 * Recompiled for NextSync 4.0, with upload support and a new framing protocol.
 * Used SDCC 4.5.0 on Windows https://sourceforge.net/projects/sdcc/files/sdcc-win64/4.5.0/
 * Use build.ps1 to build the .dot file (syncdev.dot), then copy it to the Next and run it from BASIC with
 */

// The dot's OWN version - single source of truth: the banner below and
// the 'Y' ident reply both splice this in at compile time, so the
// version a controller reads over the wire can never drift from the
// one printed on screen. On a bump ALSO update ZX_NEXT_UNITE_DOTN_VERSION
// in zxnu_config.py (the app's refresh-your-.sync5 advisory).
#define SYNC_VERSION "5.9.10"

#define TIMEOUT 20000
#define TIMEOUT_FLUSHUART 10000
// Hard ceiling on ONE flush_uart_hard() call, never reset by arriving bytes
// (5.9.3). Six times the quiet window, so a legitimate burst is still drained
// to silence, but a peer that streams without pause can no longer keep the
// dot in there for ever - which is what wedged a put until the Next was power
// cycled. Bounded by unsigned short: keep it under 65535.
#define TIMEOUT_FLUSHUART_CAP 60000

// UART speed is chosen at runtime from the .sync command line, so one binary
// covers every case (no more separate SYNCSLOW/SYNCFAST builds):
//   -slow    : stay at 115200  (most compatible, slowest)
//   -default : 1152000         (conservative fast)
//   -fast    : 2000000         (fastest)
// With no switch the previous compiled-in behaviour (fast) is kept.
#define MODE_DEFAULT 0
#define MODE_FAST    1
#define MODE_SLOW    2

// Left uninitialised on purpose: this dot's custom crt0 has no initialised-data
// segment (an initialiser here balloons the binary past the 8KB limit), so both
// are assigned at runtime in main() before first use.
static unsigned char g_syncmode;
static unsigned char g_fast_uart_mode;
// Flow control for THIS run: 1 = this board has the pins (nextreg 0x0F
// nibble 2 or 3) AND this run will reach a fast rate, so the module is
// told ",3" and 0x163B bit 5 is set. ONE VARIABLE - ZX Next Remote
// tracked the FPGA bit and the module setting separately and four paths
// disagreed (its 1.3.8). Decided ONCE in the board block, set from the
// ONE statement sequence that tells the module (gofast), and cleared
// unconditionally by three sites that never read it. IT HAS EXACTLY ONE
// JOB: the moment it acquires a second, re-read the two paths that never
// tell the module - MODE_SLOW and the already-fast branch. That is
// ZXNR's 1.3.10 bug exactly. Assigned before any read, so it does not
// depend on bss being zeroed.
static unsigned char g_flow;
// The user asked for flow control (-fc). NOTHING touches port 0x163B
// unless this is set, and that is the whole point of it: the register is
// documented as living in the shared core, but the one zxnext.vhd this
// project can see decodes three UART ports and not that one ("-- todo:
// add cts/rts" sits beside them), so on a core generation without it a
// read returns the floating bus and the read-modify-write puts garbage
// back. A default run must therefore be byte-for-byte the 5.9.7 dot.
static unsigned char g_flow_ask;
// The armed AT+UART_CUR did not take and gofast retried at ",0". Kept
// only so the report can tell "this board cannot" from "the module
// refused" - the 5.9.8 field report could not, and that cost a session.
static unsigned char g_flow_esp;
// The module took ",3" but the link then failed its first real test (the
// ATE0 that follows), so we disarmed OUR side and left the module armed -
// the benign half of the mismatch. A separate flag from g_flow_esp
// because "the module refused" and "the module agreed and then the link
// died" are different faults and the report is read to tell them apart.
static unsigned char g_flow_ate;

// xxxsmbbb
// where b = border color, m is mic, s is speaker
__sfr __at 0xfe gPort254;

// esxDOS/nextreg/console/receive/checksum/mulby10 all come from the z88dk shim
// (syncsys.h); fopen/fread/... are macros onto esx_f_* there. memcpy is libc.
#include <string.h>
#include "syncsys.h"

__sfr __banked __at 0x133b UART_TX;
__sfr __banked __at 0x143b UART_RX;
__sfr __banked __at 0x153b UART_CTL;
// UART Frame (R/W, hard reset 0x18): bits 4:3 frame size, bit 0 stop
// bits, BIT 5 = HARDWARE FLOW CONTROL. Every write is READ-MODIFY-WRITE:
// a blind "out 0x20" would also reconfigure the line, which presents as
// corruption rather than as a flow-control bug. Safe to READ, which
// 0x133B is NOT - its status bits clear on read, so probing there eats
// the evidence a lost-byte hunt depends on.
//
// SAFE ON EVERY BOARD: the register is in the shared core, so it reads
// and writes identically on an issue 2; only the PINS are missing there.
// That is why the CLEARS below are unguarded and only the ENABLE is
// gated - the one operation that must always be possible must never
// grow a board test in front of it.
//
// WHICH UART: 0x153B bit 6 selects ESP(0)/Pi(1) and we inherit it.
// Whether 0x163B is multiplexed by that select is unconfirmed, so every
// access below sits AFTER main()'s "UART_CTL = 16" - correct under
// either answer, and the only placement that is. Do not hoist the entry
// clear above that line.
__sfr __banked __at 0x163b UART_FRAME;

#define FLOW_ON()  (UART_FRAME = (unsigned char)(UART_FRAME | 0x20))
#define FLOW_OFF() (UART_FRAME = (unsigned char)(UART_FRAME & 0xDF))

// Command line pointer (was a crt0 global). main() points it first at the raw
// NextZXOS command tail, then at the cleaned private buffer. The old crt0 also
// exported framecounter/dbg/scr_x/scr_y/osiy - all unused here, so dropped.
char *cmdline;
unsigned short corever;
// The three cosmetic/trace options default ON (v5.0); each has an -n? switch
// to turn it off. The old opt-in flags (-v / -anim|-a / -dark|-d) are still
// accepted as harmless no-ops so existing habits and scripts keep working.
char g_verbose = 1;   // echo -listen commands/actions on screen; -nv disables
char g_anim = 1;      // hardware-sprite eye-candy while syncing; -na disables
char g_dark = 1;      // retro green-on-black look + custom font; -nr disables

// Optional sprite animation (anim.c). The functions self-guard (tick/end do
// nothing until begin has run), so they're safe to call unconditionally.
// The Sir Clive walk animation (anim.c, section-retargeted into the head
// page by build_dotn.ps1). ANIM_ENABLED 0 compiles it out entirely (the call
// sites become preprocessor no-ops and anim_head.asm must be dropped from
// zproject.lst + build_dotn.ps1) - used during the v5.1 drives bring-up to
// rule the head page out; the animation was innocent.
#define ANIM_ENABLED 1
#if ANIM_ENABLED
extern void anim_begin(void);
extern void anim_tick(void);
extern void anim_end(void);
// The -v busy cursor (also anim.c, head page): a | / - \ glyph drawn by
// direct pixel writes into the screen cell at the ROM print position -
// never through the print driver, so it is safe anywhere. spin(1) advances
// one pose (self-limited to one per frame, no-op without -v); spin(0)
// blanks the cell again.
extern void spin(unsigned char go);
// CRC-32 (v5.9.2): the bit-serial core lives in the head page (crc32.asm),
// the hex formatter in the main bank; the accumulator is main-bank bss so
// the head page's stomped-at-load data top never holds state (5.7.3).
extern void crc_update(unsigned char *p, unsigned short n) __z88dk_callee;
extern void crc_hex(char *dst) __z88dk_fastcall;
unsigned char crc_acc[4];
// The spinner's pose art, read by spin() (anim.c, head page). It lives HERE
// so it lands in main-bank rodata - the head page is packed to the byte, and
// head-page code reads main-bank data freely (both stay mapped). 6 rows per
// pose; the blank top/bottom rows are written by spin() itself.
const unsigned char spin_glyphs[30] = {
    0x10,0x10,0x10,0x10,0x10,0x10,   // |
    0x02,0x04,0x08,0x10,0x20,0x40,   // /
    0x00,0x00,0x7E,0x00,0x00,0x00,   // -
    0x40,0x20,0x10,0x08,0x04,0x02,   // \ (backslash)
    0x00,0x00,0x00,0x00,0x00,0x00    // blank (spin(0) erases with it)
};
#else
// Preprocessor no-ops (not stub functions): the call sites vanish entirely,
// so no code is generated and nothing links against the removed anim.c.
#define anim_begin()
#define anim_tick()
#define anim_end()
#define spin(go)
#endif

// Liveness shim for head-page loops: rcpy_hb (rcpy.c) calls this on every
// progress/keepalive block, which keeps the flock moving through directory
// skip-loops and rfsize sweeps. One main-bank hop so the head page never
// references the anim symbols directly and the ANIM_ENABLED knob above still
// compiles the whole animation out (this body then becomes empty).
void live_tick(void)
{
    anim_tick();
}

// See calc_prescalar.c for the prescalar calculation code
// Baud prescalars, one row per rate, 8 columns indexed by the machine's
// clock (nextreg 0x11 & 7).
//
// 5.9.3: TRIMMED FROM 15 ROWS TO THE 3 THAT ARE REACHABLE, reclaiming 192
// bytes of main-bank rodata - which is stack headroom, the dot's tightest
// budget (build_dotn.ps1 checks it every build; 5.7.1's hardware-proven
// floor is 156 bytes and 5.7.2 corrupted the -anim state at 46). The 12
// rows removed were never selectable: setupuart() is called exactly twice,
// as setupuart(0) and setupuart(g_fast_uart_mode), and g_fast_uart_mode is
// assigned one of two constants. Nothing else indexes this table.
//
// The mode number is therefore a PRIVATE index, not a wire or config value -
// it is a static, set once from g_syncmode and never persisted, sent or
// parsed - so renumbering it breaks no compatibility with anything, on the
// card or on the PC. Keep it that way: if a mode number ever has to leave
// this program, map it to a stable name first.
//
// ADDING A RATE means adding its row here AND its index in the
// g_fast_uart_mode assignment; the old 15-row table is in the 5.9.2 source
// if a rate needs to come back.
// CORRECTED IN 5.9.4, AND THIS WAS A REGRESSION, not an original sin.
// calc_prescalar.c divided with plain integer division, so the regenerated
// table TRUNCATED where the original hand-written one (still quoted at the
// foot of calc_prescalar.c) rounded. Five of the eight 1152000 cells and
// four of the 2000000 cells were one LOW, which puts the wire ~3% ABOVE
// the nominal rate the ESP is handed - at the edge of what a UART
// tolerates, and invisible because timings 0, 3 and 7 happened to agree.
// ZXNextRemote recalculated the same numbers independently at its 1.2.9
// and arrived here too.
//
// STILL NOT EXACT, and it cannot be: the prescalar is an integer divisor
// of the video clock, so 27000/2000 = 13.5 rounds to 14 and leaves timing
// 7 at -fast 3.6% off whichever way it goes. The cure for THAT is to tell
// the ESP the rate we are really running (clock / prescalar) instead of
// the nominal one, which is what gofast() still does not do.
static const unsigned short prescalar_values[] = {
  243,   248,   256,   260,   269,   278,   286,   234, // (0) 115200
   24,    25,    26,    26,    27,    28,    29,    23, // (1) 1152000
   14,    14,    15,    15,    16,    16,    17,    14  // (2) 2000000
};

// Uart setup based on code by D. ‘Xalior’ Rimron-Soutter
void setupuart(char mode)
{
   unsigned short prescalar = prescalar_values[mode * 8 + (readnextreg(0x11) & 0x07)];
   
   UART_CTL = (UART_CTL & 0x40) | 0x10 | (unsigned char)(prescalar >> 14);
   UART_RX = 0x80 | (unsigned char)(prescalar >> 7);
   UART_RX = (unsigned char)(prescalar) & 0x7f;
}

unsigned char parse_cmdline(char *f)
{
    unsigned char i;   
    
    if (!cmdline)
    {
        f[0] = 0;
        return 0;
    }

    i = 0;
    while (i < 127 && cmdline[i] != 0 && cmdline[i] != 0xd && cmdline[i] != ':')
    {
        f[i] = cmdline[i];
        i++;        
    }

    f[i] = 0;
    return i;
}

// memcmp comes from libc (<string.h>); the app only uses it for equality tests.
// The original also carried a private memset, but nothing calls it, so it's gone.

// Print a line via the ROM (conprint), followed by a newline. Strings use '\r'
// for embedded line breaks (ROM print treats CR as newline).
//
// Force SCR_CT (sysvar at 23692) to 255 every line: the ROM otherwise stops at
// the bottom of the screen with a "scroll?" prompt, which hangs the command
// line during a multi-file sync. 255 makes it auto-scroll without prompting.
void print(char * t)
{
    *((unsigned char *)23692) = 255;
    conprint(t);
    conprint("\r");
}

extern unsigned char uitoa(unsigned short v, char *b);  // defined in gfx.c

// -v helpers: only emit when g_verbose is set (used to trace -listen on-screen).
void vprint(char *t)
{
    if (g_verbose) print(t);
}

// Print "<prefix><name>" verbose-only and leave the line OPEN (no newline):
// the per-file trace prints this BEFORE a copy starts, and the spinner then
// twirls in the cell right after the name until the file completes.
void vbegin2(char *a, char *b)
{
    if (!g_verbose) return;
    *((unsigned char *)23692) = 255;
    conprint(a);
    conprint(b);
}

// Print "<prefix><name>" on one line, verbose only - the rcpy per-item
// trace ("d-> y", "/!\ error -> z").
void vprint2(char *a, char *b)
{
    vbegin2(a, b);
    if (g_verbose) conprint("\r");
}

// (vlabelnum and printnum were dead code - removed in v5.2 to buy main-bank
// stack headroom back.)

extern unsigned char uitoa(unsigned short v, char *b);

// Just flush as much as is in the queue right now.
void flush_uart(void)
{
    while (UART_TX & 1)
    {
        UART_RX;
    }
} 

// Drain the uart for a FIXED window, then leave.
//
// 5.9.3: the countdown used to be RESET by every byte that arrived
// ("timeout = TIMEOUT_FLUSHUART" inside the if), so while ANYTHING kept
// coming this never returned - the only unbounded wait left in the transfer
// path (cipxfer/atcmd/bufinput/send all run on TIMEOUT; receive() is walled
// at inbuf+2048 in uart.asm). transfer()'s mismatch arm calls it once per
// failed round, so a Next whose packet stream had desynced entered it and
// stopped polling, stopped animating and ignored BREAK until it was power
// cycled - the field report behind 9.7.12: six "open ok" lines and then
// nothing, the hang landing between the 6th mismatch and failcount++.
//
// The countdown still RESETS on every byte, because "drain until the line is
// quiet" is the semantic every caller needs - the poll loop flushes a bad
// frame and immediately re-polls, so returning with bytes still in the fifo
// corrupts the NEXT reply, and eight of those in a row is "Connection lost -
// stopping". A first cut at this bound dropped the reset for a plain fixed
// window and did exactly that on hardware: an idle listen session came back
// to the NextZXOS menu on its own.
//
// What makes it terminate is the SECOND counter, which nothing resets. The
// drain therefore ends either when the line falls quiet (normal) or when the
// cap runs out (a peer that will not stop talking - the case we must escape),
// and never later than that. All NINE call sites get the bound from this one
// edit.
void flush_uart_hard(void)
{
    unsigned short timeout = TIMEOUT_FLUSHUART;
    unsigned short cap = TIMEOUT_FLUSHUART_CAP;
    while (timeout && cap)
    {
        if (UART_TX & 1)
        {
            UART_RX;
            timeout = TIMEOUT_FLUSHUART;
        }
        timeout--;
        cap--;
    }
}

unsigned char receive_slow(void)
{
    unsigned short timeout = (g_syncmode == MODE_SLOW) ? 200 : 20;
    while (timeout && !(UART_TX & 1))
    { 
        // wait for data.
        timeout--; 
    }
    if (!timeout) return 0;
    return UART_RX;
}

// TWO COUNTERS SINCE 5.9.8, and hardware flow control is why. `timeout`
// was a single per-CALL budget: 20000 iterations of a 59 T-state loop at
// 28 MHz, i.e. ~42 ms shared by all of a chunk's bytes, against the ~450
// iterations a healthy 255-byte chunk at 2 Mbaud spends. Nothing could
// hold the Tx-full bit for that long, so the expiry arm was unreachable.
//
// Arming 0x163B bit 5 makes it reachable ON PURPOSE: the ESP may now park
// our transmitter (uart_tx.vhd S_RTR, no timeout) for as long as it likes,
// which is the entire point of the feature. A single budget therefore
// conflated "the peer legitimately paused us" with "the link is dead" -
// and on expiry the old code wrote the byte into a FIFO it had just
// measured FULL, then abandoned the chunk's remainder. The block checksum
// and the 12-retry loop above catch the short block, so nothing corrupt is
// ever accepted, but the retries are pure cost.
//
// THE SHAPE IS flush_uart_hard's, proven in this file: a window that
// REFILLS ON PROGRESS and a cap that never does. That is the rule this
// project already paid for once (ZX Next Remote 0.7.13: any two-phase
// deadline must reset on progress), applied here to "a byte went out"
// rather than to a clock. A dead link still terminates - on the cap, in
// ~126 ms instead of ~42 - while a legitimate pause costs time, not bytes.
//
// NO RETURN VALUE, deliberately: every caller's correctness already rests
// on the framed reply and its checksum, so a short write is detected end
// to end today, and threading a status through send_long/send_block to
// re-detect it would touch every transfer path for no new information.
void send(const char *b, unsigned char bytes)
{
    unsigned short timeout;
    unsigned char t;
    while (bytes)
    {
        // ONE WINDOW PER BYTE. `timeout` used to be a single budget for the
        // whole call, and nothing could hold the Tx-full bit long enough to
        // spend it, so its expiry arm was unreachable. Arming 0x163B bit 5
        // makes it reachable ON PURPOSE - the ESP may now park our
        // transmitter (uart_tx.vhd S_RTR, no timeout) for as long as it
        // likes, which is the whole point of the feature - and one shared
        // budget conflates "the peer legitimately paused us" with "the link
        // is dead". Refilling it here is the rule this project already paid
        // for once (ZX Next Remote 0.7.13: any two-phase deadline must reset
        // on progress), applied to a byte going out rather than to a clock.
        timeout = TIMEOUT;
        // busy wait until byte is transmitted
        do
        {
            timeout--;
            t = (unsigned char)(UART_TX & 2); // bit 1 = Tx buffer full
        }
        while (t && timeout);

        // The window expired with no slot: STOP, and do not store. The old
        // code wrote the byte into a buffer it had just measured full -
        // lost either way, and it is what made the expiry arm look like a
        // successful send. Breaking here is also what BOUNDS the whole
        // loop: it can only go round again when a byte actually went out,
        // and `bytes` is at most 255, so the call is capped at 255 windows
        // by construction. That is why there is no second never-reset
        // counter here, unlike flush_uart_hard above - THAT window resets
        // on a byte ARRIVING, which a chatty peer can sustain for ever, so
        // it genuinely needs a ceiling. This one cannot run away.
        //
        // A dead link therefore still gives up in one window (~42 ms at 28
        // MHz), exactly as before this change; what is new is that a
        // legitimate pause now costs time instead of bytes.
        //
        // NO COMPOUND CONDITIONALS, deliberately: the first cut carried a
        // cap and read `while (timeout && cap && bytes)`, which compiled
        // with a new "warning 110: conditional flow changed by optimizer" -
        // the same one syncsys.c's `(d >= 'A' && d <= 'P') ? d : 0` draws.
        // The generated code was correct (verified by disassembly), but in
        // the hottest function in the program, in the one loop that now has
        // a reachable failure arm, a clean build is worth more than a
        // counter that cannot fire.
        if (t)
            break;

        UART_TX = *b;

        gPort254 = *b & 7;
        b++;
        bytes--;
    }
    gPort254 = 0;

    // On later core versions, UART Tx buffer size is 64 not 1, so bytes are accepted faster but still
    // sent at the same rate. To preserve previous timings, wait for buffer to empty before continuing.
    // On core versions where flag bit 4 does not exist yet, skip this Tx buffer flush.
    if (corever >= 0x310a) // 3.01.10
    {
        timeout = TIMEOUT;
        do
        {
            timeout--;
            t = UART_TX;       
        }
        while (!(t & 16) && timeout); // bit 4 = 1 if the Tx buffer is empty
    }
}

extern unsigned char strinstr(char *a, char *b, unsigned short len, char blen);

// Anatomy of a cipxfer:
// [s]"AT+CIPSENDEX=5\r\n"
// [at]"AT+CIPSENDEX=5\r\r\n\r\nOK\r\n> "
// [s]"Sync3"
// [bi]"\r\nRecv 5 bytes\r\n\r\nSEND OK\r\n\r\n+IPD,14:\0\x0eNextSync33\x0a\0"
unsigned short bufinput(char *buf)
{
    unsigned short timeout = TIMEOUT;
    unsigned short datalen = 0;
    unsigned short ofs = 0;
    unsigned char r;
    while (timeout && receive_slow() != '+') { timeout--; }
    // TODO: size/speed opt
    if (receive_slow() != 'I') return 0; // should be I
    if (receive_slow() != 'P') return 0; // should be P
    if (receive_slow() != 'D') return 0; // should be D
    if (receive_slow() != ',') return 0; // should be ,
    datalen = receive_slow() - '0'; // first digit
    r = receive_slow();
    while (r != ':')
    {
        datalen = mulby10(datalen);
        datalen += r - '0';
        r = receive_slow();
        if (r != ':' && (r < '0' || r > '9')) return 0;
    }

    if (datalen > 2048 || datalen == 0) return 0;
    do
    {
        ofs += receive(buf + ofs);
        timeout--;
    }
    while (timeout && ofs < datalen);

    return ofs;    
}

unsigned char atcmd(char *cmd, char *expect, char expectlen, char *buf)
{
    unsigned short len = 0;
    unsigned short timeout = TIMEOUT;
    unsigned short retrycount = 100;
    unsigned char l = 0;
        
    while (cmd[l]) l++;        
retryatcmd:
    flush_uart();
    send(cmd, l);      

    while (timeout && len < 2048)
    {        
        len += receive(buf + len);
        timeout--;
        if (strinstr(buf, expect, len, expectlen))
        {
            return 0;
        }
        if (strinstr(buf, "busy", len, 4))
        {
            if (!retrycount)
                return 1;
            len = 0;
            retrycount--;
            goto retryatcmd;
        }
    }    
    return 1;
}

// max cmdlen = 9
void cipxfer(char *cmd, unsigned char cmdlen, unsigned char *output, unsigned short *len, unsigned char **dataptr)
{    
    const char *cipsendcmd_c="AT+CIPSENDEX=0\r\n";
    char *cipsendcmd = (char *)cipsendcmd_c;
    unsigned short received, expected;
    unsigned short timeout = 5; // relatively small timeout needed because bufinput has timeout
    cipsendcmd[13] = '0' + cmdlen;
    *len = 0;
    // 5.9.3: assign *dataptr HERE, not only on the success path. Both early
    // returns below used to leave it untouched, so transfer()'s caller-side
    // local stayed uninitialised and "dp[len - 1]" read through a garbage
    // pointer - a wild read that can itself MANUFACTURE the packetno mismatch
    // that starts a retry storm. Hoisted, not added: the success path's own
    // assignment goes away, so this costs nothing.
    *dataptr = output + 2;
    if (atcmd(cipsendcmd, ">", 1, output)) // cipsend prompt
    {
        return;
    }
    flush_uart();
    expected = 2; // always expect at least 2 bytes. Actually, we should expect at least 5.. size+checksums
    received = 0;
    send(cmd, cmdlen);
    do 
    {
        unsigned short r = bufinput(output + received);
        received += r;
        if (expected == 2 && received > 2)
        {
            expected = ((output[0]<<8) | output[1]);
            if (expected < 5 || expected > 2048)
            {
                return;
            }
        }
        timeout--;
    }
    while (timeout && received < expected);
    // *dataptr was set at the top of the function (5.9.3) so the early
    // returns above cannot leave the caller's pointer uninitialised.
    *len = received - 2; // reduce size bytes    
}

// WHAT THE WIRE REALLY RUNS AT, in kHz, one row per fast mode and eight
// columns of video timing - i.e. clock / prescalar, rounded, for exactly
// the prescalars the table above programs. Row 0 is 1152000, row 1 is
// 2000000 (g_fast_uart_mode - 1; gofast is never reached at -slow, which
// leaves the module at its 115200 default).
//
// 16-BIT VALUES, NOT STRINGS, and that is the memory decision: sixteen
// shorts are 32 bytes where sixteen 4-character cells would be 64, and
// uitoa renders them for nothing since it is already linked and divides by
// repeated subtraction. Computing clock/prescalar here instead would drag
// in SDCC's division runtime, measured at 490 bytes when ZXNextRemote hit
// this same wall at its 1.2.9.
static const unsigned short khz_values[] = {
  1167, 1143, 1133, 1154, 1148, 1143, 1138, 1174, // (1) 1152000
  2000, 2041, 1964, 2000, 1938, 2000, 1941, 1929  // (2) 2000000
};

char gofast(char *inbuf)
{
    // TELL THE MODULE THE RATE WE ARE ACTUALLY PRODUCING (5.9.4). The
    // prescalar is an integer divisor of the video clock, so the wire
    // almost never runs at the nominal rate, and this used to hand the ESP
    // the nominal one regardless. Two machines, same command:
    //
    //   Brd 4 t0 p14   28000/14 = 2000.0 kHz  told 2000000   0.00%
    //   Brd 2 t7 p14   27000/14 = 1928.6 kHz  told 2000000  -3.57%
    //
    // -3.57% is outside what a UART tolerates, and rounding cannot fix it:
    // 27000/2000 is 13.5 and no integer divisor exists. Saying the real
    // number does. Built here rather than stored as sixteen full commands
    // for the obvious reason.
    //
    // The buffer is a FRAME LOCAL: gofast runs once, at connect depth,
    // nowhere near the deep sync and -listen loops the stack floor is
    // sized for - and a static would spend the same bytes permanently, on
    // the very budget build_dotn.ps1 guards.
    char cmd[32];
    unsigned char n;
    unsigned short w;
    // 5.9.10: set once when the armed attempt failed and the ,0 retry is
    // about to go round through the hard reset. ONE SHOT - it is what
    // stops the third failure looping, and it is a frame local because
    // gofast runs once at connect depth (the file's standing rule for
    // this function: a static would spend the bytes permanently).
    unsigned char reset_retry = 0;

    // HARD-RESET THE MODULE BEFORE ARMING (5.9.9), and this is the field
    // report's own finding: on a KS2, -fc worked on the FIRST run after a
    // complete power-off and failed on every run after it. That is not a
    // board problem (the gate said "Brd 4", i.e. capable, every time) and not
    // our own bit (the entry clear already handles that). It is the MODULE:
    // ESP-AT only takes ",3" cleanly from its power-on state, and once it has
    // been through a ",3" -> ",0" cycle the next armed AT+UART_CUR is not
    // applied - the following probe then burns its timeout (the ~1 s pause
    // after the Brd line), we fall back to ",0", and the report says off.
    //
    // ZX Next Remote never sees this because it routes EVERY failed probe
    // through esp_hard_reset(); the dot only resets on its "No esp" bail. So
    // reproduce the one state that demonstrably works. Only when we are
    // actually going to arm: an incapable board, or a run without -fc, must
    // stay byte-for-byte the old bring-up - which is what KS1 and MAME, both
    // connecting happily today, are the evidence for.
    //
    // 5.9.10 GAVE THIS BLOCK A SECOND CALLER, and that is the whole fix:
    // the ,0 fallback below used to `goto retry`, which lands AFTER this
    // block and therefore skipped the one thing that puts the module
    // somewhere known - at the exact moment its state had become
    // unknowable. It now jumps HERE instead. reset_retry rather than
    // g_flow because g_flow is cleared one statement before that jump.
hardreset:
    if (g_flow || reset_retry)
    {
        FLOW_OFF();                  /* ours first - the ZXNR ordering */
        writenextreg(0x02, 128);     /* hold the ESP in reset */
        for (w = 0; w < 20000u; w++) ;
        writenextreg(0x02, 0);       /* release */
        setupuart(0);                /* it comes back at 115200, flow off */
        // THEN WAIT FOR IT TO COME BACK, AND DO NOT GUESS HOW LONG.
        // 5.9.9's first cut released reset and waited one 60000-iteration
        // drain before sending the armed command. MEASURED by disassembling
        // the shipped binary, and the first pass at these numbers got two of
        // three wrong - they are re-counted here: the drain loop is 60
        // T-states idle (73 when a byte arrives), so one 60000-iteration
        // pass is ~129 ms at 28 MHz and the six below are ~771 ms. The hold
        // above is 26 T, i.e. 18.6 ms - not the '10 frames' its own comment
        // once claimed. (main()'s 10000-iteration reset delay says '5+
        // frames' and is ~22.9 ms, not the ~9 ms first reported here: its
        // counter is spilled to a stack slot, 64 T per iteration, where this
        // one stays in BC. Still not 5 frames, and it has never mattered
        // because that path bails immediately afterwards.)
        //
        // An ESP-AT module needs several hundred ms to boot, so we were
        // firing AT+UART_CUR=...,3 into a module that was still coming up:
        // the command was lost, the probe burned its timeout, and the ,0
        // fallback then succeeded because by THAT point it had booted.
        // Exactly the field report - 'Flow off esp' on every run after the
        // first, and a retried connection with it.
        //
        // So drain for ~771 ms (six passes, an unsigned short caps one at
        // 65535) and then PROBE until it answers, which is self-timing and
        // costs nothing on a module that is already up. Echo is still on
        // here - ATE0 comes later - so the reply carries the command back
        // before its OK; strinstr finds the OK either way.
        for (n = 0; n < 6; n++)
            for (w = 0; w < 60000u; w++)
                if (UART_TX & 1)
                    UART_RX;
        flush_uart_hard();
        for (n = 0; n < 8; n++)
            if (!atcmd("AT\r\n", "OK", 2, inbuf))
                break;
    }

retry:
    memcpy(cmd, "AT+UART_CUR=", 12);
    n = uitoa(khz_values[(g_fast_uart_mode - 1) * 8
                         + (readnextreg(0x11) & 0x07)], cmd + 12);
    memcpy(cmd + 12 + n, "000,8,1,0,0\r\n", 14);   /* the NUL travels too */
    // The fifth field is the MODULE's flow control: 0 none, 3 RTS+CTS.
    // 3 in BOTH directions - CTS lets the FPGA stop the module before the
    // Next's 512-byte Rx fifo overruns (the download leg, the one this dot
    // actually needs), RTS lets the module stop US. ",2" would buy the
    // download half alone and is NOT safer: with the ESP not driving RTS,
    // our bit 5 would point the transmitter at an undriven esp_rtr_n_i, a
    // pin with no pullup in the issue-4 constraints.
    //
    // INDEX IS COMPUTED, not the literal 26: uitoa returned n, the tail
    // starts at 12+n, and the flow digit is its 11th character. Every
    // khz_values cell is four digits today, so 26 would be right BY LUCK.
    //
    // THIS COMMAND IS WHERE THE MODULE LEARNS, so it is the only place in
    // the program that may arm anything.
    if (g_flow)
        cmd[12 + n + 10] = '3';
    atcmd(cmd, "", 0, inbuf);

    // DRAIN AT THE OLD BAUD BEFORE FOLLOWING WITH THE PRESCALAR (5.9.9).
    // This is ZX Next Remote's shape, and its comment names this very
    // function: "the dot's gofast(): command the module up, drain its OK (it
    // arrives at the OLD baud), then follow with the prescalar". The dot has
    // always switched immediately instead, and at ",0" that was fine for
    // years - the module only had a divisor to change.
    //
    // ",3" also makes it reconfigure its handshake pins, and the KS2 field
    // report for 5.9.8 was exactly the shape you would expect if we moved
    // first: a ~1 s pause after the Brd line (the following probe burning
    // its timeout), then the ",0" fallback, and occasionally a link left
    // desynced. atcmd() returns as soon as it has sent, because the command
    // is issued with expect "" - so without this the module gets no quiet
    // time at the rate it is still listening on.
    flush_uart_hard();

    setupuart(g_fast_uart_mode);
    flush_uart_hard();
    if (atcmd("\r\n", "ERROR", 5, inbuf))
    {
        // WHAT THIS BRANCH KNOWS IS ONLY THAT NOBODY ANSWERED. atcmd
        // returns the same 1 for a refusal and for silence (it has one
        // exit for a timeout), so "no reply at the new rate" does NOT
        // mean "the command was rejected".
        //
        // Until 5.9.10 it was read that way - "the rate did not take, so
        // NOTHING in that command took" - and the retry went back to
        // 115200 to be heard. HARDWARE SAYS OTHERWISE (an N-GO, board
        // issue 2, with the board guard lifted for the experiment): the
        // module ACKED the armed AT+UART_CUR at 115200 and then went
        // silent, because ,3 made it honour a CTS line nothing on that
        // board drives. It had applied all five fields. It was sitting at
        // the FAST rate, so the 115200 retry was never heard, the second
        // probe failed too, and the run died with "No fast esp" - instead
        // of the unprotected fast link this retry exists to fall back to.
        // The atomicity claim is not the error; the inference from silence
        // is. A module that refuses and a module that obeyed and was then
        // muted look identical from here, and the second is the one -fc
        // actually produces on a board without the pins.
        //
        // So the module's state is FORCED, not guessed: go round through
        // the hard reset above, which returns it to 115200 with flow off
        // whichever of the two it was in. MEASURED, and the cheaper idea
        // was measured too and does not work: re-sending ,0 at the rate
        // we are already at, on the theory that a muted module still
        // RECEIVES (only its transmitter is CTS-gated), rescued nothing on
        // that N-GO - it acked nothing and stayed mute. Only the reset
        // brought it back. Do not reintroduce it.
        //
        // The fallback still exists for the case it always did - an ESP-AT
        // build that will not do flow control must not lose fast mode
        // ENTIRELY on a board that has always had it - and our bit is
        // still clear (the only line that sets it is below), so both ends
        // go round unarmed.
        //
        // COST: one extra module reset on a run that was already failing,
        // and with it a Wi-Fi re-association - visible afterwards as a
        // "Retrying connection" or two before the session comes up.
        if (g_flow)
        {
            g_flow = 0;
            g_flow_esp = 1;            /* so the report can say WHY */
            reset_retry = 1;
            goto hardreset;
        }
        print("No fast esp");
        return 1;
    }

    // OURS LAST, AND ONLY HERE - the rule this change must not break. The
    // module was told above and the rate has just been CONFIRMED, and that
    // confirmation IS the flow confirmation, because baud and flow ride in
    // one command and apply together. Arming any earlier points our
    // receiver's readiness at a line the ESP is not yet driving, and the
    // dot has no UI, no Settings escape and no watchdog: a parked
    // transmitter is a frozen Next needing a reset.
    if (g_flow)
        FLOW_ON();
    return 0;
}

unsigned char createfilewithpath(char * fn)
{
    unsigned char filehandle;
    char * slash;
    if (g_verbose) { vprint("open:"); vprint(fn); }
    filehandle = fopen(fn, 2 + 0x0c);  // write + create new file, delete existing
    if (filehandle) { vprint("open ok"); return filehandle; }
    // Couldn't create the file - the usual cause is a missing directory
    // level (a stock NextZXOS card has c:/sys but no c:/sys/config, so the
    // very first ".sync5 <ip>" lands here). This is the recovery, not a
    // failure - the old "open failed, mkdir path" trace read like one and
    // got the first-run config save reported as broken (5.9.1): make the
    // path one level at a time, then retry the open below. Keep the string
    // SHORT - main-bank rodata is stack headroom (build_dotn.ps1's floor).
    vprint("creating path");
    slash = fn;
    while (*slash)
    {
        slash++;
        if (*slash == '/')
        {
            *slash = 0;      // esx_f_mkdir wants a 0-terminated path prefix
            // Skip the bare drive prefix ("c:"): mkdir on a drive root can
            // never create anything and only added a scary no-op to the
            // trace.
            if (slash[-1] != ':')
            {
                if (g_verbose) { vprint("mkdir:"); vprint(fn); }
                sync_mkdir(fn);  // make this directory level (ignore "exists")
            }
            *slash = '/';
        }
    }
    filehandle = fopen(fn, 2 + 0x0c); // if it still doesn't work, well, it doesn't.
    vprint(filehandle ? "open ok (2)" : "open still failed");
    return filehandle;
}

// Defined further down (with the -listen machinery it was written for);
// transfer() has polled it since 5.9.3, so it needs the prototype here.
unsigned char break_pressed(void);

char transfer(char *fn, unsigned char *inbuf)
{
    unsigned char *dp;
    unsigned short len;
    unsigned char filehandle;
    unsigned char packetno = 0;
    unsigned char failcount = 0;

restart:
    filehandle = createfilewithpath(fn);
    if (filehandle == 0)
    {
        print("Unable to open file");
        return 2;   // distinct: could not create the destination (not a link loss)
    }

    do
    {
        cipxfer("Get", 3, inbuf, &len, &dp);
retry:
        // 5.9.3: the link is idle here (the next command has not been sent),
        // which makes this the one point EVERY round passes through - good
        // packet, bad checksum or packetno mismatch alike. Three things
        // therefore live here rather than in the good-packet arm below:
        //
        //  * anim_tick()/spin(1). They used to run only on a GOOD packet, so
        //    a retry storm froze the display and the machine looked dead
        //    while it was in fact still working (the 9.7.12 report). A frozen
        //    spinner now means STOPPED, not "retrying".
        //  * break_pressed(). transfer() had no BREAK check at all: the poll
        //    loop's own comment notes it is sampled strictly BETWEEN
        //    commands, and one put is one command, so BREAK was dead for a
        //    whole file. A user watching a storm can now stop it.
        //  * a len guard. len == 0 (both cipxfer early returns) made this
        //    read dp[-1]; *dataptr is always assigned now, but comparing a
        //    length byte against packetno is still meaningless - treat a
        //    short frame as the mismatch it is.
        anim_tick();
        spin(1);
        if (break_pressed()) goto failure;
        if (len < 3 || dp[len - 1] != packetno)
        {
            if (len == 5+3 && checksum(dp, len - 3) == 0 && memcmp(dp, "Error", 5) == 0)
            {
                goto doretry;
            }
            flush_uart_hard();
            cipxfer("Restart", 7, inbuf, &len, &dp);
            fclose(filehandle);
            len = 0;
            packetno = 0;
            failcount++;
            if (failcount > 5) goto failure;                        
            goto restart;
        }
        
        if (len && checksum(dp, len - 3) == 0)
        {
            len -= 3;
            fwrite(filehandle, dp, len);
            packetno++;
            failcount = 0;
            // (the sprite step + spinner pose moved up to retry:, 5.9.3, so
            // a retry storm animates too - both still self-limit to one step
            // per frame)
        }
        else
        {
doretry:
            failcount++;
            if (failcount > 5) goto failure;
            flush_uart_hard();
            cipxfer("Retry", 5, inbuf, &len, &dp);
            goto retry;
        }
    }
    while (len != 0);

    spin(0);
    fclose(filehandle);
    return 0;
failure:
    spin(0);
    fclose(filehandle);
    return 1;
}

// ----------------------------------------------------------------------------
// Sync4 upload (Next -> PC). The Next pushes files to the ZX Next Unite app.
// Each block mirrors the server's download packet framing so the asm checksum()
// verifier is reused:
//   [2 bytes big-endian total][payload][checksum0][checksum1][packetno]
// total = payloadlen + 5. packetno is one counter for the whole send session.
// The payload's first byte is an opcode: 'N' new file, 'D' data, 'E' end file,
// 'B' bye. The server replies with a framed "Ok" (accept) or anything else
// (resend). Kept deliberately compact - a .dot command must fit in 8KB.
// ----------------------------------------------------------------------------

// Not static: also reset by the head-page listen_free (free.c).
unsigned char g_packetno;

// send() takes an unsigned char count (max 255); send an arbitrary length.
void send_long(const char *b, unsigned short len)
{
    unsigned char n;
    while (len)
    {
        n = len > 255 ? 255 : (unsigned char)len;
        send(b, n);
        b += n;
        len -= n;
    }
}

// Frame the payload already at scratch+2 (payloadlen bytes), send it via
// AT+CIPSEND (not CIPSENDEX, which stops at a NUL), and read the framed reply.
// Returns 1 if the server replied "Ok", else 0 (caller retries).
char send_block(unsigned char *scratch, unsigned short payloadlen, unsigned char *inbuf)
{
    char cmd[18];
    unsigned char p, c0 = 0, c1 = 0;
    unsigned short i, total = payloadlen + 5, received = 0, expected = 2, timeout = 5;

    for (i = 0; i < payloadlen; i++) { c0 ^= scratch[2 + i]; c1 += c0; }
    scratch[0] = (unsigned char)(total >> 8);
    scratch[1] = (unsigned char)total;
    scratch[2 + payloadlen] = c0;
    scratch[3 + payloadlen] = c1;
    scratch[4 + payloadlen] = g_packetno;

    memcpy(cmd, "AT+CIPSEND=", 11);
    p = (unsigned char)(11 + uitoa(total, cmd + 11));
    cmd[p++] = '\r'; cmd[p++] = '\n'; cmd[p] = 0;
    if (atcmd(cmd, ">", 1, (char*)inbuf)) return 0;

    flush_uart();
    send_long((const char *)scratch, total);

    do {
        received += bufinput((char*)inbuf + received);
        if (expected == 2 && received > 2)
        {
            expected = (inbuf[0] << 8) | inbuf[1];
            if (expected < 5 || expected > 2048) return 0;
        }
        timeout--;
    } while (timeout && received < expected);

    if (received < 5 || checksum((char*)inbuf + 2, received - 5) != 0) return 0;
    return inbuf[2] == 'O'; // "Ok"
}

// Send a block with retries; advances the session packetno on success.
// Returns 0 on success, 1 on give-up.
char send_block_rt(unsigned char *scratch, unsigned short payloadlen, unsigned char *inbuf)
{
    unsigned char tries = 12;
    while (tries--)
    {
        if (send_block(scratch, payloadlen, inbuf)) { g_packetno++; return 0; }
        flush_uart_hard();
    }
    return 1;
}

// Send one file. relname is the (relative) path the server recreates under its
// selected folder. Returns 1 on fatal transmission failure, 0 otherwise (an
// unopenable file is skipped).
char send_file(char *fullpath, char *relname, unsigned char *inbuf, unsigned char *scratch)
{
    unsigned char fh, namelen = 0;
    unsigned short n;

    fh = fopen((unsigned char *)fullpath, 1);
    if (fh == 0) { print("Skip:"); print(relname); return 0; }
    print(relname);
    anim_tick();   // eye-candy step per file (no-op unless -anim); before I/O

    while (relname[namelen] && namelen < 200) namelen++;
    scratch[2] = 'N';
    scratch[3] = 0; scratch[4] = 0; scratch[5] = 0; scratch[6] = 0; // length unknown
    scratch[7] = namelen;
    memcpy((char*)scratch + 8, relname, namelen);
    if (send_block_rt(scratch, (unsigned short)(6 + namelen), inbuf)) { fclose(fh); return 1; }

    do {
        n = fread(fh, scratch + 3, 1024);
        if (n)
        {
            scratch[2] = 'D';
            if (send_block_rt(scratch, (unsigned short)(n + 1), inbuf)) { fclose(fh); return 1; }
            anim_tick();   // between acked blocks: link idle, safe (1 step/frame)
        }
    } while (n);

    scratch[2] = 'E';
    send_block_rt(scratch, 1, inbuf);
    fclose(fh);
    return 0;
}

// Send a directory tree. Minimal-stack design that avoids two things that each
// crashed/looped the dot on real hardware:
//   * file I/O while a readdir handle is open (corrupts the esxDOS dir cursor);
//   * a big collection buffer on the stack (the 8KB stack bank already holds
//     inbuf+scratch, and FATFS opendir/readdir needs a lot of headroom on top -
//     a 1KB buffer tipped it over and corrupted the return address -> re-run).
// So: for each entry we re-open the directory, skip to that entry, CLOSE it,
// then send the file (or recurse). Only one esxDOS handle is ever open, never
// during file I/O, and no per-level buffer is used. O(n^2) readdir but tiny.
char send_dir(char *fullpath, unsigned short plen, unsigned char *inbuf, unsigned char *scratch, unsigned char *entrybuf)
{
    unsigned char *name;
    unsigned short i, nl, newlen, cur = 0, j;
    unsigned char dh, got;

    for (;;)
    {
        if (cur >= 4000) return 0;                  // safety cap
        dh = opendir((unsigned char *)fullpath);
        if (dh == 0) return 0;
        got = 0;
        for (j = 0; j <= cur; j++) got = readdir(dh, entrybuf); // land on entry #cur
        fclose(dh);                                             // closed before any file I/O
        if (!got) return 0;                                     // past the last entry -> done
        cur++;

        name = entrybuf + 1;
        if (name[0] == '.' && (name[1] == 0 || (name[1] == '.' && name[2] == 0))) continue;
        nl = 0; while (name[nl]) nl++;
        if (plen + 1 + nl >= 254) continue;

        newlen = plen;
        fullpath[newlen++] = '/';
        for (i = 0; i < nl; i++) fullpath[newlen++] = name[i];
        fullpath[newlen] = 0;

        if (entrybuf[0] & 0x10)
        {
            if (send_dir(fullpath, newlen, inbuf, scratch, entrybuf)) return 1;
        }
        else
        {
            if (send_file(fullpath, fullpath, inbuf, scratch)) return 1;
        }
        fullpath[plen] = 0;
    }
}

// Set g_syncmode (or a flag) if the n-char token at p is one of our switches and
// return 1, else return 0. Matched by first char + length (much cheaper than
// memcmp on z80):
//   "-fast"    / "-f"      (len 5 / 2)   speed: fastest
//   "-default"            (len 8)        speed: middle
//   "-slow"    / "-s"      (len 5 w/ 3rd char 'l' so it isn't "-send" / len 2)
//   "-dark"    / "-d"      (len 5 w/ 3rd char 'a' / len 2)  retro green-on-black
//                                        look with the custom font
//   "-na" "-nv" "-nr"      (len 3)       v5.0: anim, verbose trace and the
//                                        retro look are ON by default; these
//                                        switch each one off (no anim / no
//                                        verbose / no retro)
//
// Also consumes "-fc" (UART hardware flow control, issue 4/5 boards only -
// see g_flow_ask) and the standalone option flags "-v" (verbose), "-a" and
// "-anim"
// (sprite eye-candy) - now-default no-ops kept for old habits and scripts.
// Consuming them here means they are dropped from the cleaned command line, so
// e.g. ".sync5 -na" still runs a normal PC->Next sync (without anim) instead
// of being mistaken for a bad argument. "-h"/"--h"/"-help" are deliberately
// NOT matched here: they stay in the cleaned line, whose leading '-' routes
// main() to the help screen.
unsigned char setspeed(char *p, unsigned char n)
{
    if (*p != '-') return 0;
    if (p[1] == 'f' && (n == 5 || n == 2))              { g_syncmode = MODE_FAST;    return 1; }
    // -fc: ask for UART hardware flow control. OPT-IN since 5.9.9 - see
    // g_flow_ask. n == 3 is free here because -f is matched at 2 or 5.
    if (p[1] == 'f' && n == 3 && p[2] == 'c')           { g_flow_ask = 1;           return 1; }
    if (p[1] == 'd' && n == 8)                          { g_syncmode = MODE_DEFAULT; return 1; }
    if (p[1] == 'd' && (n == 2 || (n == 5 && p[2] == 'a'))) { g_dark = 1;            return 1; }
    if (p[1] == 's' && (n == 2 || (n == 5 && p[2] == 'l'))) { g_syncmode = MODE_SLOW; return 1; }
    if (p[1] == 'v' && n == 2)                          { g_verbose = 1;             return 1; }
    if (p[1] == 'a' && n == 2)                          { g_anim = 1;                return 1; }
    if (p[1] == 'a' && n == 5 && p[2] == 'n')           { g_anim = 1;                return 1; }
    if (p[1] == 'n' && n == 3)
    {
        if (p[2] == 'a') { g_anim = 0;    return 1; }
        if (p[2] == 'v') { g_verbose = 0; return 1; }
        if (p[2] == 'r') { g_dark = 0;    return 1; }
    }
    return 0;
}

// Pull the -slow/-default/-fast/-v/-anim/-a/-dark/-d/-na/-nv/-nr switches out
// of the command line and copy the remaining tokens into dst. Sets
// g_syncmode/g_verbose/g_anim/g_dark. Works anywhere in the line.
//
// CRITICAL: this only READS cmdline and writes to dst (a private buffer). It
// must NEVER write into cmdline itself - that buffer belongs to the NextZXOS
// command processor, and poking it makes the OS re-dispatch the command after
// the dot returns, so the dot runs again and again forever. main() then points
// cmdline at dst so the rest of the parser sees the cleaned line.
void parse_speed_switches(char *dst)
{
    unsigned char si = 0, di = 0, ts, n;
    dst[0] = 0;
    if (!cmdline) return;
    for (;;)
    {
        while (cmdline[si] == ' ') si++;
        if (!cmdline[si] || cmdline[si] == 0xd) break;
        ts = si;
        while (cmdline[si] && cmdline[si] != ' ' && cmdline[si] != 0xd) si++;
        n = si - ts;
        if (setspeed(cmdline + ts, n)) continue;   // recognised switch -> drop it
        if (di) dst[di++] = ' ';                   // keep this token
        while (ts < si) dst[di++] = cmdline[ts++];
    }
    dst[di] = 0;
}

// ---------------------------------------------------------------------------
// -listen mode: act as a small remote file server, driven by the PC over the
// Sync protocol. COMPATIBILITY: this is only ever reached after a NEW handshake
// keyword ("Listen"); every Sync3/Sync4/-send path and frame is untouched, so
// old dots and old servers are completely unaffected.
//
// The Next keeps driving, as everywhere else: it polls the server for the next
// command and runs it. All frames use the existing block framing
// [2B total][payload][cs0][cs1][packetno]:
//
//   Next  -> server : "Poll"   (cipxfer)
//   server-> Next   : one command frame, payload = opcode + optional path:
//        'I'          idle, nothing queued  -> the Next just re-polls
//        'L' <path>   ls    : the Next pushes a directory listing back
//        'G' <path>   get   : the Next pushes the file/dir back (send_file/dir)
//        'P' <path>   put   : the Next pulls the file from the server (transfer)
//        'M' <path>   mkdir
//        'R' <path>   rmdir
//        'X' <path>   rm (unlink)
//        'V' <old>\0<new>  ren : rename/move a file or directory
//        'U'          release : close the dot's OWN file handle (v5.9+)
//        'W'          getdrives : the Next pushes the mounted drive letters
//        'Z' [drive]  free  : free space on a partition (psize/pfull, v5.2+)
//        'C' <src>\0<dst>  rcpy : copy a file/dir LOCALLY on the Next (v5.2+)
//        'S' <path>   rfsize : total size of a file / directory tree (v5.2+)
//        'K' <path>   crc : CRC-32 of a file, 'O' + 8 hex digits (v5.9.2+)
//        'Q'          quit  -> leave listen mode
//
// Every <path> may carry an optional drive prefix ("m:/games"); esxDOS
// resolves it directly, and a path without one lands on the dot's current
// drive exactly as before -- so pre-'W' servers keep working unchanged.
//
// ls/get/mkdir/rmdir/rm/ren answer by PUSHING blocks to the server (each acked
// with a framed "Ok", exactly like -send):
//   ls  : 'D' blocks of packed entries, then 'E'.
//         entry = [1B flags][4B size, little-endian][1B namelen][name],
//         flags bit0 = directory.
//   get : send_file / send_dir ('N'/'D'/'E' per file), then a final 'B'.
//   mkdir/rmdir/rm/ren/release : one status block, 'O' (ok) or 'F' (fail).
//         'ren' carries two NUL-separated paths in one frame (old then new).
//   release ('U', v5.9+): the dot closes the OS's own read handle on its
//         /dot/sync5 file (held open by the dotN loader for the whole run),
//         so the server can then swap a staged sync5.new in with ren ops -
//         the running code is all in RAM and never re-reads the file. After
//         'U' the server must send ONLY path-based ops (V/X/Q): anything
//         that OPENS a file or directory could be handed the freed handle
//         number, which NextZXOS's exit tidy-up still closes.
//   getdrives : one status block, 'O' + <current drive letter> + <drive
//         letters> (e.g. "O" "C" "CM"). The list is {C, M, current}: C and M
//         are guaranteed by NextZXOS, the current drive is mounted by
//         definition. Never probed - file calls on unmounted drives crash.
//   rcpy : the whole copy runs ON the Next - no data crosses the wire, and
//         because every esxDOS call takes drive-prefixed paths it works
//         across partitions (c:/x -> m:/y) with no drive switching. The
//         Next pushes 'D' progress blocks (one per file with the dest path,
//         plus an empty keepalive every 64KB inside big files) and ends
//         with 'O' (all copied) or 'F' (failed; already-copied files stay,
//         like an interrupted cp). Directory copies re-enumerate with the
//         send_dir safe walk (no file I/O while a readdir handle is open).
//         The destination-inside-source trap is guarded PC-side.
//   rfsize : rcpy's companion ("will the copy fit?"): measures a file or a
//         whole directory tree. 'D' progress blocks (one per directory with
//         its path + empty keepalives every 256 entries), then one terminal
//         'O' + [4B files][4B dirs][4B size_lo][2B size_hi] (all LE; total
//         bytes = size_hi*2^32 + size_lo - a tree can exceed 32 bits), or
//         'F'. Sizes come from the dirents, so each dir is swept O(n) with
//         the handle open (the listen_ls-proven pattern); only sub-dir
//         recursion uses the re-open/skip/close dance.
//   free : one status block, 'O' + 4 bytes little-endian = free 512-byte
//         blocks on the partition (F_GETFREE), or 'F' on failure. The
//         optional argument is a drive letter ('C', 'M', ...); without one
//         the dot's current drive is measured. This is the only storage
//         metric NextZXOS exposes through the dotN-safe divMMC API - total
//         partition size would need +3DOS/IDEDOS via M_P3DOS, which is fatal
//         here (see listen_drives below) - so the PC's psize/pfull commands
//         both present this same free-space figure.
//   put : reuses transfer() - the Next pulls data with "Get", server serves it.
//         On success the server has counted every byte, so nothing more is sent;
//         on failure (couldn't create the file, or the transfer gave up) the Next
//         pushes an 'F' status block so the server can report the error.
// ---------------------------------------------------------------------------

// BREAK key detection so a -listen session can be stopped from the Next itself
// (the Next's equivalent of the PC pressing Ctrl-C). zx_keyrow(high) does
// IN A,(high*256 + 0xFE) - a pressed key reads as 0 in its bit. BREAK is
// CAPS SHIFT + SPACE held together: CAPS SHIFT is bit0 of the 0xFEFE half-row,
// SPACE is bit0 of the 0x7FFE half-row. Both down => BREAK.
extern unsigned char zx_keyrow(unsigned char highbyte) __z88dk_fastcall;

unsigned char break_pressed(void)
{
    return ((zx_keyrow(0xFE) & 1) == 0) && ((zx_keyrow(0x7F) & 1) == 0);
}

// Map a -listen command opcode to its command name, so the -v trace prints a
// consistent verb ("ls", "get", "put", "mkdir", ...) for every command instead
// of the raw single-letter opcode. Returns "?" for anything unexpected.
// The -v result words, shared (v5.9.2). "ls done" / "rfsize done" / "rcpy
// malformed" each carried their own literal, and main-bank rodata is stack
// headroom: the crc verb below was paid for by folding them into three.
// The trace loses nothing - the "> verb path" echo line names the command.
static char s_ok[] = "ok";
static char s_fail[] = "fail";
static char s_bad[] = "bad";

// CRC-32 (IEEE 802.3, reflected - what zlib.crc32 and the ZX Next Remote
// listener compute) of one file, 2 KB at a time through inbuf so any size
// fits (v5.9.2). Writes the 8 upper-case hex digits at out, NUL-terminated;
// 0 = the file did not open, or a read failed mid-way (esx_f_read answers
// 0xFFFF then, never a short count as success) - never the digest of a
// prefix dressed up as the whole file. sync_read answers 0 at the end;
// an errno check on top cost 25 bytes the main bank does not have.
// Reached from the 'K' listener op only: a standalone "-crc <file>" verb
// was built and measured - its parse, branch and help line left 23 bytes
// of stack headroom against the 156-byte floor - so the PC asks instead
// (Unite's Remote Explorer, the HTTP bridge's /crc, the console's crc verb)
// and the digits are printed here for the Next's screen as they go out.
extern char inbuf[2048];   // the 2 KB receive buffer. v5.9.5: it is NOT bss
                           // - uart.asm defines the symbol as the absolute
                           // address $6000, the base of the banked mmu3
                           // window (see the note where the other big
                           // buffers are declared, below)
unsigned char crc_run(char *path, char *out)
{
    unsigned char fh = fopen(path, 1);
    unsigned short n;
    if (!fh) return 0;
    memset(crc_acc, 0xff, 4);
    for (;;)
    {
        n = fread(fh, inbuf, 2048);
        if (n == 0 || n > 2048) break;
        crc_update((unsigned char *)inbuf, n);
        spin(1);
    }
    fclose(fh);
    spin(0);
    if (n > 2048)
        return 0;
    crc_hex(out);
    out[8] = 0;
    return 1;
}

char *listen_cmd_name(unsigned char op)
{
    switch (op)
    {
        case 'L': return "ls";
        case 'G': return "get";
        case 'P': return "put";
        case 'M': return "mkdir";
        case 'R': return "rmdir";
        case 'X': return "rm";
        case 'V': return "ren";
        case 'U': return "release";
        case 'W': return "drives";
        case 'Z': return "free";
        case 'C': return "rcpy";
        case 'S': return "rfsize";
        case 'Y': return "ident";
        case 'K': return "crc";
        case 'Q': return "quit";
        default:  return "?";
    }
}

// Push a one-byte status result ('O' ok / 'F' fail) for mkdir/rmdir/rm/ren.
void listen_status(char ok, unsigned char *inbuf, unsigned char *scratch)
{
    g_packetno = 0;
    scratch[2] = ok ? 'O' : 'F';
    send_block_rt(scratch, 1, inbuf);
}

// ls: enumerate 'path' and push the listing to the server as 'D' blocks of
// packed [flags][size][namelen][name] entries, ended by an 'E' block. Only
// readdir + network I/O happen while the handle is open (no esxDOS file I/O),
// so the directory cursor is safe. Print-free, so it lives in the HEAD PAGE
// (free.c) - moved there in v5.2 to reclaim main-bank stack headroom.
extern void listen_ls(char *path, unsigned char *inbuf, unsigned char *scratch);

// getdrives: report the drives the PC may target, as one status block:
// 'O' + <current drive letter> + <drive letters>.
//
// NO PROBING - the list is exactly {C, M, current}. Three real-hardware
// crashes taught us every path-touching probe is fatal in a dotN:
//  * esx_dos_get_drive/M_P3DOS: +3DOS remaps $8000-$BFFF (our code+stack);
//  * any path on A:/B:, the +3DOS floppy drives: same remap via the floppy
//    driver;
//  * opendir on an UNMOUNTED letter (e.g. "D:/" with no D: partition):
//    NextZXOS's drive resolution for a missing drive dies the same way -
//    only MOUNTED drives answer file calls safely, so "probe to see what is
//    mounted" is a contradiction.
// What IS safe: M_GETDRV (sync_getdrive, divMMC hook - proven on hardware),
// and NextZXOS guarantees C: (boot SD) and M: (RAM disk) always exist. The
// current drive is mounted by definition, so launching the dot from another
// partition (e.g. D:) still exposes it to the PC.
void listen_drives(unsigned char *inbuf, unsigned char *scratch)
{
    unsigned char n = 0, cur;

    g_packetno = 0;
    cur = sync_getdrive();
    if (!cur) cur = 'C';
    scratch[2] = 'O';
    scratch[3] = cur;
    scratch[4 + n++] = 'C';
    if (cur != 'C' && cur != 'M')
        scratch[4 + n++] = cur;
    scratch[4 + n++] = 'M';
    send_block_rt(scratch, (unsigned short)(2 + n), inbuf);
}

// ident ('Y', v5.8.0): 'O' + "sync" + NUL + SYNC_VERSION, one status
// block - the twin of ZX Next Remote's 1.0.2 fs_version, for the same
// update automation: the type names WHAT answered (the dot is "sync",
// the .nex flavors answer "httpbridge"/"n2n"), the number names its
// build. One opcode for both facts: a pre-5.8 dot ignores an unknown
// opcode in silence and the server's block parse then trips over the
// next raw Poll, so every probe against an old build costs a false
// "connection closed" log - once is enough.
void listen_ident(unsigned char *inbuf, unsigned char *scratch)
{
    unsigned char n = 0;
    const char *s = "sync";
    const char *v = SYNC_VERSION;

    g_packetno = 0;
    scratch[2] = 'O';
    while (*s) scratch[3 + n++] = (unsigned char)*s++;
    scratch[3 + n++] = 0;
    while (*v) scratch[3 + n++] = (unsigned char)*v++;
    send_block_rt(scratch, (unsigned short)(1 + n), inbuf);
}

// psize/pfull ('Z'): free space on a partition, as one status block:
// 'O' + 4 bytes little-endian free 512-byte block count, or 'F' when the
// drive can't be measured. arg = optional drive letter (empty string = the
// dot's current drive). Implemented in free.c, which - like anim.c - is
// section-retargeted into the head page by build_dotn.ps1 so its 32-bit
// arithmetic doesn't eat main-bank stack headroom (it follows the head-page
// rules: no printing, esxDOS calls only).
extern void listen_free(char *arg, unsigned char *inbuf, unsigned char *scratch);

// ---------------------------------------------------------------------------
// rcpy ('C'): copy a file or whole directory locally on the Next; pushes 'D'
// progress blocks then a final 'O'/'F'. All the machinery is head-page
// resident and print-free (rcpy.c): the chunk-stepped one-file copy
// (rcpy_fbegin arms it, rcpy_fchunk moves one 2 KB chunk per call) plus an
// ITERATIVE walk (rcpy_step - explicit level stack in the scratch tail, ONE
// stack frame at any depth). Only the code below stays in the main bank: it
// drives the walk one item per step, announces each file BEFORE its bytes
// move ("f-> file" - a large file used to leave the screen silent until
// done), pumps the chunks with the spinner twirling at the line's end, and
// prints "d-> dir" / "/!\ error -> item" between steps - head-page code
// must never print, not even via a main-bank helper reached from a
// head-page frame (hardware-proven), and the stepper design means no
// head-page frame is on the stack when the trace or the spinner happens.
// Paths at scratch+512/+768, walk state at scratch+1024; the only bss is
// the 11-byte in-flight copy state (a static inside rcpy.c).
// ---------------------------------------------------------------------------
extern unsigned char rcpy_hb(char *name, unsigned char *inbuf, unsigned char *scratch);
extern unsigned char rcpy_fbegin(char *src, char *dst, unsigned char *inbuf, unsigned char *scratch);
extern unsigned char rcpy_fchunk(unsigned char *inbuf, unsigned char *scratch);
extern unsigned char rcpy_step(unsigned char *inbuf, unsigned char *scratch);

// rcpy's failed-item count for the terminal 'O'/'F' status. A static, not a
// listen_rcpy local threaded through rcpy_run: pointer plumbing costs more
// code than these 2 bytes of bss, and bytes here are stack headroom.
static unsigned short rcpy_fails;

// Run one armed file copy (rcpy_fbegin returned 0) to completion: announce
// "f-> dst" with the line left open, pump rcpy_fchunk stepping the animation
// and the -v spinner between 2 KB chunks - all from the main bank, with no
// head-page frame on the stack while anything is drawn - then close the
// line, tracing (and counting) a per-file failure. The dst/src paths are
// where listen_rcpy/rcpy_step keep them: scratch +768/+512.
// Returns rcpy_fchunk's terminal code: 1 copied, 2 failed, 3 link dead.
unsigned char rcpy_run(unsigned char *inbuf, unsigned char *scratch)
{
    unsigned char r;
    vbegin2("f-> ", (char *)scratch + 768);
    for (;;)
    {
        anim_tick();
        r = rcpy_fchunk(inbuf, scratch);
        if (r) break;
        spin(1);
    }
    spin(0);
    if (g_verbose) conprint("\r");
    if (r == 2) { vprint2("/!\\ error -> ", (char *)scratch + 512); rcpy_fails++; }
    return r;
}

// 32-bit little-endian store, used by the head-page listen_rfsize to build
// its totals reply (lives here so those few bytes don't crowd the nearly
// full head page).
void put32le(unsigned char *p, unsigned long v)
{
    p[0] = (unsigned char)v;
    p[1] = (unsigned char)(v >> 8);
    p[2] = (unsigned char)(v >> 16);
    p[3] = (unsigned char)(v >> 24);
}

// rcpy entry point: copies the two NUL-separated paths out of fn[] into the
// scratch-tail buffers (fn is too small to grow two paths), decides file vs
// directory, runs the copy and sends the terminal status: 'O' = everything
// copied, 'F' = anything failed or the link died (already-copied files
// stay, like an interrupted cp). The terminal block must CONTINUE the 'D'
// sequence, so it is sent directly (listen_status would reset g_packetno).
void listen_rcpy(char *src0, char *dst0, unsigned char *inbuf, unsigned char *scratch)
{
    char *src = (char *)scratch + 512;
    char *dst = (char *)scratch + 768;
    unsigned short sl = 0, dl = 0;
    unsigned char fh, r = 0;

    g_packetno = 0;
    rcpy_fails = 0;

    while (src0[sl] && sl < 255) { src[sl] = src0[sl]; sl++; }
    src[sl] = 0;
    while (dst0[dl] && dl < 255) { dst[dl] = dst0[dl]; dl++; }
    dst[dl] = 0;
    // Trailing slashes off (keep a drive root's, "c:/" - "c:" would mean
    // c:'s CURRENT dir, not its root).
    while (sl > 1 && src[sl - 1] == '/' && src[sl - 2] != ':') src[--sl] = 0;
    while (dl > 1 && dst[dl - 1] == '/' && dst[dl - 2] != ':') dst[--dl] = 0;

    if (sl == 0 || dl == 0)
        rcpy_fails = 1;
    else
    {
        fh = fopen((unsigned char *)src, 1);   // readable file?
        if (fh)
        {
            fclose(fh);
            // Single file: rcpy_run announces it BEFORE the bytes move and
            // twirls the spinner after the name while they do.
            r = rcpy_fbegin(src, dst, inbuf, scratch);
            if (r == 0)
                r = rcpy_run(inbuf, scratch);
            else if (r == 1)
            {
                vprint2("/!\\ error -> ", src);   // unreadable/uncreatable
                rcpy_fails = 1;
                r = 2;
            }
            else
                r = 3;                            // the link died in fbegin
        }
        else
        {
            // Not a file: only a directory copy if it really opens as one
            // (probing FIRST avoids leaving a junk empty destination dir
            // behind for a typo'd source).
            fh = opendir((unsigned char *)src);
            if (fh == 0) { vprint2("/!\\ error -> ", src); rcpy_fails = 1; }
            else
            {
                fclose(fh);
                sync_mkdir(dst);               // create the root (exists=merge)
                vprint2("d-> ", dst);
                // Drive the head-page iterative walk one item at a time,
                // tracing each completed item HERE - between steps, so no
                // head-page frame is ever on the stack while printing. The
                // walk state (rcpy_state_t, syncsys.h) is armed directly.
                {
                    rcpy_state_t *st = (rcpy_state_t *)(scratch + 1024);
                    st->sp = 0;
                    st->ended = 0;
                    st->cur[0] = 0;
                    st->sl[0] = sl;
                    st->dl[0] = dl;
                }
                for (;;)
                {
                    r = rcpy_step(inbuf, scratch);
                    if (r == 0) break;                                   // done
                    else if (r == 5)
                    {
                        // A file copy just armed: announce + pump + trace.
                        r = rcpy_run(inbuf, scratch);
                        if (r == 3) { r = 4; break; }                    // link dead
                    }
                    else if (r == 2) vprint2("d-> ", dst);               // dir created
                    else if (r == 3) { vprint2("/!\\ error -> ", src); rcpy_fails++; }
                    else break;                                          // 4: link dead
                }
            }
        }
    }

    // r: single file 1 ok / 2 failed (rcpy_fails set) / 3 link dead;
    // directory walk 0 done / 4 link dead; other failures already counted.
    scratch[2] = (rcpy_fails || r == 3 || r == 4) ? 'F' : 'O';
    send_block_rt(scratch, 1, inbuf);
}

// rfsize ('S'): total size of a file or directory tree, as 'D' progress
// blocks then 'O' + [files][dirs][size_lo][size_hi] or 'F'. Head-page
// resident (rfsize.c); reuses syncsys.c's static LFN dirent and the scratch
// tail for its path, so it too adds (almost) zero main-bank bss.
extern void listen_rfsize(char *arg, unsigned char *inbuf, unsigned char *scratch);

// Big I/O buffers live in bss (main bank, mmu4/mmu5) rather than on the stack:
// under the dotN model that keeps the stack small and forces those pages to be
// allocated and mapped. They are only used from main() and its callees, one
// invocation at a time, so static is safe.
static char fn[256];

// v5.9.5 - inbuf is the one buffer that is NOT in main-bank bss any more.
// uart.asm defines _inbuf as the absolute address $6000: the base of the
// mmu3 window, which main() points at a private 8 KB page the dotN loader
// got from NextZXOS (DOTN_NUM_EXTRA = 1) and the crt hands back at
// terminate. That buys the main bank 2048 bytes of stack headroom for ~11
// bytes of code.
//
// WHAT MUST STAY TRUE, because a pointer into a paged window is only as
// good as the mapping underneath it:
//   * map_data_bank() is called once, at the top of main(), immediately
//     after tail_copy() and before ANY use of inbuf - and the page is then
//     held for the whole run. There is no unmap: nothing else in this dot
//     touches $6000-$7FFF, so one owner holds one window and no pointer
//     can go dark.
//   * receive() (uart.asm) drains straight into it, and its hard wall is
//     the link-time constant _inbuf+2048, which now resolves to $6800 -
//     dead space inside our own page, where it used to be scratch's first
//     byte.
//   * esxDOS both reads and writes it (readdir entries, esx_f_read chunks,
//     and filenames handed to createfilewithpath). esxDOS traps take
//     $0000-$3FFF, not mmu3; the dotN loader itself F_READs 8192 bytes
//     into a freshly allocated page at $6000 on machines whose BASIC stack
//     is high.
//   * the ROM print driver saves and restores mmu6/mmu7 only, so printing
//     a string out of inbuf is safe here - it would NOT be at $C000-$FFFF.
//   * being outside bss it is NOT zeroed by the crt, so main() clears it
//     explicitly right after the map, to keep the old semantics exactly.
extern void map_data_bank(void);   // uart.asm

static char scratch[1280]; // outgoing block: 1024 file bytes + opcode + framing (~1030 max)
static char sendpath[256]; // -send's path, and send_dir's walk extends it IN
                           // PLACE up to 254 chars (its guard) - so 256, not the
                           // 160 the command line alone would need (v5.9.2 tried)
static char cleancmd[160]; // command line with speed switches removed (never
                           // touch the OS buffer). 256 -> 160 in 5.7.3: the
                           // source is tail_copy's 158-cap private copy (a
                           // typed BASIC line, never near 160), and the 96
                           // bytes bought back the main-bank stack headroom
                           // the Busy branch had eaten (see anim.c's state
                           // comment for the hardware evidence).

// v5.6 clone hardening, hand-asm in uart.asm: BOTH byte budgets are full
// (the head page tail brushes the $3F00 line, and every main-bank byte is
// stack headroom), so the two helpers are ~75 bytes of asm instead of
// ~150 of compiled C. See uart.asm for what they guarantee.
extern void tail_copy(char *dst, char *src);
extern unsigned char valid_server(char *fn) __z88dk_fastcall;

// arglen = z88dk's measured command-line length (unused); rawcmd = pointer to
// the unprocessed NextZXOS command tail (CRT_ENABLE_COMMANDLINE=2), which keeps
// ':' intact for paths like "c:/foo".
int main(int arglen, char *rawcmd)
{                                 //1234567890123456789012
    const char *cipstart_prefix  = "AT+CIPSTART=\"TCP\",\"";
    const char *cipstart_postfix = "\",2048\r\n";
    const char *conffile         = "c:/sys/config/nextsync.cfg";
    char sendmode = 0;
    char listenmode = 0;   // -listen: run as a remote file server for the PC
    unsigned char fnlen;
    unsigned char *dp;
    unsigned short len = 0;
    unsigned char nextreg6;
    unsigned char nextreg7;
    char fastuart = 0;
    char filehandle = 0; // init to silence "used before init" (the read is guarded, but be safe)
    char retrycount;
    char conn_tries;   // startup connect retries used (see retryconnect)
    unsigned char saved_scr_ct;
    // init to silence "used before init" (reads are guarded by the same
    // if (g_dark) as the writes, but SDCC's flow analysis can't see that)
    unsigned char saved_attr_p = 0, saved_attr_t = 0;

    // Save SCR_CT (23692) before print() starts forcing it to 255, so it can be
    // restored at terminate. Leaving it at 255 would suppress the ROM "scroll?"
    // prompt for the rest of the BASIC session after the dot command exits.
    saved_scr_ct = *((unsigned char *)23692);

    // Take a bounded PRIVATE copy of the command tail before any parsing.
    // Genuine Next hardware leaves a 0x0D after the arguments, but clones
    // do not all guarantee a terminator: on an N-Go the bytes after the
    // tail can be garbage, the tokenizer (8-bit indices) then runs off
    // through memory, and ".sync5 <ip>" showed the HELP instead of saving
    // the config. tail_copy (uart.asm) caps at 158 bytes and
    // forces a NUL — a well-behaved machine still sees exactly the old
    // bytes (0x00/0x0D end the copy early). arglen stays unused: the crt
    // measures it by scanning for the same terminator, so it is no more
    // trustworthy than the buffer itself. sendpath doubles as the scratch:
    // it is not filled until AFTER the parse below (and from cleancmd, not
    // the raw line), so no new buffer is spent on this.
    (void)arglen;
    tail_copy(sendpath, rawcmd);
    cmdline = sendpath;

    // The command tail is ours now, so the page underneath it may go.
    // Point mmu3 at our own 8 KB page: from here to terminate, $6000-$7FFF
    // is inbuf and nothing else. This must NOT move any earlier - rawcmd
    // points into BASIC's program, which spans the very page we hide - and
    // must not move later either, because everything below can reach
    // inbuf. Zero it because it is no longer bss and the crt therefore
    // does not.
    map_data_bank();
    memset(inbuf, 0, 2048);

    // Strip speed/option switches into a private buffer (never write the OS
    // cmdline), then point cmdline at it so the normal parser sees the cleaned
    // line. This sets g_dark (from -dark/-d) before we decide on the look below.
    g_syncmode = MODE_FAST; // default when no -slow/-default/-fast is given
    parse_speed_switches(cleancmd);
    cmdline = cleancmd;
    // Row indices into the trimmed prescalar_values (5.9.3): 2 = 2000000,
    // 1 = 1152000. They were 14 and 12 against the old 15-row table - change
    // both together or the dot sets a wild baud rate and goes deaf.
    g_fast_uart_mode = (g_syncmode == MODE_FAST) ? 2 : 1;

    // Optional retro look (-dark/-d), restored at terminate: green ink (4) on
    // black paper (0). We poke the ZX attribute sysvars (ATTR_P permanent 23693,
    // ATTR_T temporary 23695; value = paper*8 + ink) so every ROM-printed line
    // comes out green-on-black, then con_cls() paints the whole screen black.
    // OFF by default so it never disturbs the plain sync / -listen paths (the
    // screen repaint was found to interfere with -listen). A bundled custom font
    // was tried too but dropped: its 1 KB pushed the big I/O buffers past the top
    // of the main bank ($BFFF) into the stack / NextZXOS, which corrupted
    // -listen. We use the ROM font instead.
    if (g_dark)
    {
        saved_attr_p = *((unsigned char *)23693);
        saved_attr_t = *((unsigned char *)23695);
        *((unsigned char *)23693) = 0x04;                 // paper 0 (black), ink 4 (green)
        *((unsigned char *)23695) = 0x04;
        con_cls();                                        // paint the whole screen black + home
    }

    // On a version bump ALSO update ZX_NEXT_UNITE_DOTN_VERSION in
    // zxnu_config.py (and the help text below): the app compares it against
    // the cfg's dotn_last_version to advise the user to refresh the .sync5
    // copy on their Next after updating the app.
    print("NextSync " SYNC_VERSION " Clauzel/Komppa");

    len = parse_cmdline(fn);

    // The dot loader can pass a leading space (e.g. " -send foo" or " 1.2.3.4").
    // Strip leading spaces so "-send" is recognised and a stray space is never
    // mistaken for a server name (which would overwrite the saved IP in config).
    {
        unsigned short lead = 0;
        while (fn[lead] == ' ') lead++;
        if (lead)
        {
            unsigned short j = 0;
            while (fn[lead]) fn[j++] = fn[lead++];
            fn[j] = 0;
            len = j;
        }
    }

    // Detect "-send <path>" upload mode (Next -> PC). The path is read from the
    // raw command line so ':' (e.g. c:/foo) survives - parse_cmdline() stops at
    // ':'. Skip the same leading spaces there to stay aligned with fn.
    sendpath[0] = 0;
    if (len >= 5 && fn[0] == '-' && fn[1] == 's' && fn[2] == 'e' &&
        fn[3] == 'n' && fn[4] == 'd' && (fn[5] == 0 || fn[5] == ' '))
    {
        unsigned short ci = 0, di = 0;
        while (cmdline[ci] == ' ') ci++; // leading spaces in the raw line
        ci += 5;                         // skip "-send"
        while (cmdline[ci] == ' ') ci++; // spaces before the path
        while (cmdline[ci] && cmdline[ci] != 0xd && di < 255)
            sendpath[di++] = cmdline[ci++];
        sendpath[di] = 0;
        if (di)
            sendmode = 1;
    }

    // Detect "-listen" (or its short alias "-l" / "-L"): run as a remote file
    // server driven by the PC. Like -send, it connects to the saved server
    // (from the config file). "-l" is accepted because "-listen" is a mouthful
    // to type; upper-case "-L" too because a lone lower-case l is easily
    // misread as a 1 or an I on the Next's screen.
    if (!sendmode &&
        ((len >= 7 && fn[0] == '-' && fn[1] == 'l' && fn[2] == 'i' && fn[3] == 's' &&
          fn[4] == 't' && fn[5] == 'e' && fn[6] == 'n' && (fn[7] == 0 || fn[7] == ' ')) ||
         (len >= 2 && fn[0] == '-' && (fn[1] == 'l' || fn[1] == 'L') &&
          (fn[2] == 0 || fn[2] == ' '))))
    {
        listenmode = 1;
    }

    // -v (verbose) and -anim/-a (sprite eye-candy) were already recognised and
    // stripped by parse_speed_switches() above, so g_verbose/g_anim are set.

    if (!sendmode && !listenmode)
    {
        // Only treat the argument as a server name to save when it actually
        // looks like one (starts alphanumeric). Anything else - a flag, a stray
        // space, garbage - must NOT overwrite the saved server in the config.
        char isserver = (fn[0] >= '0' && fn[0] <= '9') ||
                        (fn[0] >= 'a' && fn[0] <= 'z') ||
                        (fn[0] >= 'A' && fn[0] <= 'Z');

        if (!len || !isserver)
            filehandle = fopen((char*)conffile, 1); // read + open existing

        if ((len && fn[0] == '-') || ((!len || !isserver) && filehandle == 0))
        {
            // Probably asking for help (or no usable config to sync from).
            // No version/author line here: the banner printed at startup
            // (a few lines up) already shows both, and this screen is only
            // ever reached after it.
            conprint(
               //12345678901234567890123456789012
                ".SYNC5 [server] : save cfg\r"
                ".SYNC5 : sync files from PC\r"
                ".SYNC5 -send <file|dir> : to PC\r"
                ".SYNC5 -listen -l -L\r"
                "  : file server. PC drives:\r"
                "  ls get put mkdir rmdir rm\r"
                "  ren free rcpy rfsize\r"
                "  BREAK key stops it (safe)\r"
                ".SYNC5 -slow -default -fast\r"
                ".SYNC5 -fc : UART flow control\r"
                "  (issue 4/5 boards only)\r"
                "Anim, verbose trace and retro\r"
                "look are ON; to disable:\r"
                ".SYNC5 -na -nv -nr\r"
                ".SYNC5 -help -h : this help\r"
                "See nextsync.txt\r\r");
            goto terminate;
        }

        if (isserver)
        {
            // Clone hardening, same N-Go failure family as the bounded copy
            // above: a mangled tail must NEVER overwrite the saved config.
            // valid_server (hand asm in uart.asm, SECTION code_compiler
            // - the MAIN bank, not the head page and not free.c; the
            // comment said both and neither is true, which would have
            // sent the next person costing a change to the wrong pool)
            // accepts host chars only
            // (alnum . -), whole token, minimum 2 chars — junk like the
            // lone 'n' a mangled tail produced is refused, not written.
            if (!valid_server(fn))
            {
                conprint("Bad server name\r");
                goto terminate;
            }

            conprint("Setting server to:");
            conprint(fn);
            conprint("\r-> ");
            conprint((char*)conffile);
            conprint("\r");
            memcpy((char*)inbuf, (char*)conffile, 27);     // Constants are located below $4000, so copy
            filehandle = createfilewithpath((char*)inbuf); // filename into temp buffer to keep IDE_PATH happy.
            if (filehandle == 0)
            {
                conprint("Failed to open file\r");
                goto terminate;
            }

            fwrite(filehandle, fn, len);
            fclose(filehandle);
            conprint("Ok\r");
            goto terminate;
        }

        len = fread(filehandle, fn, 255);
        fclose(filehandle);
        fn[len] = 0;
    }
    else
    {
        // Send / listen mode: load the configured server to connect to.
        filehandle = fopen((char*)conffile, 1);
        if (filehandle == 0)
        {
            conprint("No server set - .sync5 <ip>\r");
            goto terminate;
        }
        len = fread(filehandle, fn, 255);
        fclose(filehandle);
        fn[len] = 0;
    }

    // Show where and how fast we're about to sync: the server IP (from the
    // config file, now in fn) and the selected UART speed.
    conprint("Server: ");
    conprint(fn);
    conprint("\rSpeed: ");
    conprint((g_syncmode == MODE_SLOW)    ? "slow (115200)"     :
             (g_syncmode == MODE_DEFAULT) ? "default (1152000)" :
                                            "fast (2000000)");
    conprint("\r");

    // WHAT THIS MACHINE ACTUALLY IS (5.9.4), as "Brd 2 t7 p14", before
    // anything touches the wire. Two of the three decide what this UART
    // can do and neither was ever visible from any screen:
    //
    // SINCE 5.9.8 THIS IS NOT JUST A REPORT: the dot ASKS for hardware
    // flow control on board id 2 or 3 and prints the verdict on the
    // "Flow" line below, so this block decides as well as describes.
    //
    //   Brd <n>  nextreg 0x0F bits 3:0, + 2 = the BOARD ISSUE. Not
    //            cosmetic: issue 2 (KS1, and the N-GO) has no esp_cts_n_o
    //            or esp_rtr_n_i in its top-level entity at all, so
    //            hardware flow control there is not switched off - it does
    //            not exist, and the ESP can never be told to pause. Issue
    //            4 and 5 have the pins. The boot banner cannot tell you
    //            this: a licensed clone shows its own logo and the stock
    //            core version and stops there.
    //   t<n>     nextreg 0x11 & 7, the video timing - it picks the clock
    //            the prescalar divides, i.e. the row of the table above.
    //   p<n>     the prescalar this run programs. The wire runs clock/p,
    //            so these two numbers give the real rate.
    //
    // BUILT IN inbuf, NOT IN A LOCAL ARRAY, and that is the memory point.
    // SDCC allocates a function's WHOLE frame at entry and main() sits
    // under every deep call chain for the entire run, so a 16-byte local
    // here would cost 16 bytes of the C stack arena at EVERY depth - and
    // build_dotn.ps1 measures 0xC000 - __BSS_END_head, which counts bss
    // and code but NOT frame locals, so it would report a comfortable
    // number while the real margin sat under 5.7.1's proven floor. Giving
    // it its own function was measured too: 15 bytes of code to save 16 of
    // frame, a net gain of one byte, and it tripped the guard. inbuf costs
    // nothing - it is dead here (the conffile copy above consumes it
    // immediately, and the AT traffic that uses it next comes after this),
    // exactly the argument sendpath already relies on to double as the
    // command-line scratch.
    {
        char *nb = (char *)inbuf;
        unsigned char bid = readnextreg(0x0F) & 0x0F;
        unsigned char vt  = readnextreg(0x11) & 0x07;

        // Decided HERE, off the read this line already makes, and nowhere
        // else. A CLOSED RANGE {2,3}, never ">= 2": 0xFF & 0x0F is 15,
        // which is what an emulator with no 0x0F model hands back, and
        // telling a module to honour a CTS line nobody drives is how you
        // get a transmitter that never sends - AN UNKNOWN ID MUST MEAN NO.
        // The RAW nibble, not the printed digit: the line prints '2' + bid,
        // so capable is the line reading "Brd 4" or "Brd 5". Kept identical
        // to ZX Next Remote's flow_capable(), or the two programs arm on
        // different machines and the field reports stop being comparable.
        // MODE_SLOW is folded in so -slow can never arm and can never
        // report on: gofast is not reached at -slow today, but this flag
        // also drives the report, and a slow run claiming flow control is a
        // lie that would be believed.
        g_flow = (unsigned char)(g_flow_ask
                                 && (bid == 2u || bid == 3u)
                                 && g_syncmode != MODE_SLOW);

        memcpy(nb, "Brd - t- p", 11);            /* the NUL travels too */
        nb[4] = (char)((bid < 4) ? ('2' + bid) : '?');
        nb[7] = (char)('0' + vt);
        uitoa(prescalar_values[((g_syncmode == MODE_SLOW)
                                ? 0 : g_fast_uart_mode) * 8 + vt], nb + 10);
        print(nb);
    }

    nextreg6 = readnextreg(0x06);
    writenextreg(0x06, nextreg6 & 0x7d); // disable turbo key & 50/60 switch (leave other bits alone)
    nextreg7 = readnextreg(0x07);
    writenextreg(0x07, 3); // 28MHz

    // read Next core version - e.g. 3.01.10 will be 0x310a
    corever = readnextreg(0x01) * 256 + readnextreg(0x0e);

    // select esp uart, set 17-bit prescalar top bits to zero
    UART_CTL = 16; 
    // INHERITED FLOW CONTROL, CLEARED BEFORE WE TRANSMIT A BYTE. 0x163B
    // outlives a program: the crt restores the MMU slots and this code
    // restores nextreg 0x06/0x07, but nothing restores the UART frame. A
    // dot killed by NMI or reset, or an older ZX Next Remote, leaves bit 5
    // SET, and this run would start against a module at its 115200 default
    // with flow OFF - parking our transmitter on esp_rtr_n_i before
    // anything useful prints. That run reports "No esp - reset, try again"
    // and bails: the symptom is a dot claiming there is no ESP attached.
    //
    // uart_tx.vhd holds S_RTR only while i_cts_n='1' AND i_frame(5)='1',
    // evaluated combinationally, so CLEARING RELEASES A PARKED TRANSMITTER
    // ON THE NEXT EDGE - and the clear is one OUT that needs no working
    // transmitter. Both the reset and the escape hatch. UNCONDITIONAL: not
    // gated on the board (shared silicon, legal everywhere) and not on the
    // speed mode (a -slow run after a killed fast run is exactly the case
    // it exists for). AFTER the select above, never before.
    //
    // AND IT IS NOT GATED ON -fc, which 5.9.9 briefly made it. Review
    // found the gate was ASYMMETRIC and that the asymmetry manufactured
    // the very failure this line exists to clear: a run without the flag
    // skipped this clear but still DISARMED THE MODULE, because bailout's
    // AT+UART_CUR=115200,8,1,0,0 and the "No esp" hard reset are both
    // unconditional. Inheriting (our bit set, module armed) - which works
    // - a plain run turned it into (our bit set, module not driving RTS),
    // which is the combination that parks the transmitter.
    //
    // The worry that motivated the gate was a core that does not decode
    // 0x163B, where the read-modify-write would put back garbage. Hardware
    // answered it: 5.9.8 ran this clear unconditionally on a KS1 and under
    // MAME and both connected normally. CLEARING IS ALWAYS SAFE AND ALWAYS
    // RIGHT; enabling is the half that needs asking for.
    //
    // SCOPE: "unconditional" means no board test, no speed test and no
    // flag test. The argument-parsing exits above jump straight to
    // terminate: and never reach this line, which is harmless - they touch
    // no UART at all.
    FLOW_OFF();
    // set the baud rate (default)
    setupuart(0);

    if (atcmd("\r\n", "ERROR", 5, inbuf))
    {
        if (g_syncmode != MODE_SLOW)
        {
            // Maybe we're already going fast?
            fastuart = 1;
            setupuart(g_fast_uart_mode);
            flush_uart_hard();
            // THE MODULE'S FLOW STATE IS UNKNOWN HERE, and it must not be
            // inferred from "we are at the fast rate": gofast is the ONLY
            // place it is ever told, and this branch used to skip it.
            //
            // The first cut simply cleared g_flow here and let the skip
            // stand, arguing that re-stating the rate would buy flow
            // control on a RECOVERY path at the price of a new failure
            // mode "on a path that today always proceeds". The premise was
            // wrong, and the field found it in one session: this is not a
            // recovery path, it is the NORMAL path for every run after the
            // first, because the dot leaves the module at the fast rate on
            // the way out. So -fc armed once after a power-off and never
            // again, reporting a bare "Flow off" - not "Flow off esp",
            // because nothing had refused anything; nothing had been asked.
            //
            // ZX Next Remote hit this at 1.3.10 and wrote down the lesson:
            // "when a variable acquires a new job, re-read its old edge
            // cases". g_flow is left ALONE here and the guard below lets
            // gofast run anyway when we mean to arm - gofast's own hard
            // reset then puts the module back to a known 115200 first, so
            // the sequence is identical to the one that already works.
        }
        // In slow mode there is no fast rate to fall back to, so bail straight
        // away; otherwise bail only if the esp is unresponsive at the fast rate.
        if (g_syncmode == MODE_SLOW || atcmd("\r\n", "ERROR", 5, inbuf))
        {
            print("No esp - reset, try again");
            // reset esp
            writenextreg(0x02, 128);
            // wait for 5+ frames
            for (len = 0; len < 10000; len++);
            writenextreg(0x02, 0);
            goto bailout;
        }
        // if we get this far, esp was already at the fast rate
        // (which can happen if you reset the next while
        // transfer is going on)
    }

    // `!fastuart || g_flow`: the second half is 5.9.9's fix. An unarmed
    // run is byte-for-byte what it always was - fastuart still skips
    // gofast - while a run that means to arm goes through it even when the
    // module is already fast, because that is the only way the flow field
    // is ever sent. g_flow is non-zero only with -fc on a capable board at
    // a non-slow rate, so nothing else can reach the new branch.
    //
    // The cost is honest and bounded: on this path gofast can now bail
    // where it previously was not called, taking the run with it. It only
    // does so when BOTH the armed attempt and its ,0 fallback fail, which
    // means the module is unreachable at either rate - a run that had
    // nothing to offer anyway.
    if (g_syncmode != MODE_SLOW && (!fastuart || g_flow) && gofast(inbuf))
        goto bailout;

    // ATE0 IS ALSO THE ARMED LINK'S FIRST REAL TEST (echo off; if on, we
    // might match the server name as OK/ERROR/BUSY =). Everything above
    // proves the module ACCEPTED ",3". Nothing above proves its RTS pin is
    // physically routed to esp_rtr_n_i on this ESP board - the one link no
    // software can verify. If it is not, our transmitter is parked right
    // now and this is the first command that notices. WHAT THIS DOES NOT
    // COVER: a pin that floats benign here and misbehaves later. It will
    // also disarm on an unrelated transient ATE0 failure, costing that run
    // its flow control and nothing else. Clearing ours while the module
    // stays at ,3 is the benign half of the mismatch, so this is safe
    // unconditionally; && short-circuits, so an unarmed run is
    // byte-for-byte what it was.
    if (atcmd("ATE0\r\n", "OK", 2, inbuf) && g_flow)
    {
        g_flow = 0;
        // NOT g_flow_esp: that word means the module REFUSED the armed
        // command and we retried at ,0. Here it ACCEPTED - gofast
        // confirmed the rate before arming - and its ,0 fallback is
        // upstream of this point and never re-entered. What failed is the
        // link, after the module was already armed, so the two ends are
        // left disagreeing in the BENIGN direction (module at ,3, our bit
        // clear) and the report has to say so rather than blame the ESP.
        g_flow_ate = 1;
        FLOW_OFF();
        flush_uart_hard();
        atcmd("ATE0\r\n", "OK", 2, inbuf);  /* retry unarmed; ignored */
    }

    // WHAT THIS RUN ACTUALLY DID, printed at the first point that KNOWS -
    // a line of its own, not a field on the Brd line, which is printed
    // before anything touches the wire and could therefore only ever carry
    // a PREDICTION (and would guess WRONG on the already-fast path, which
    // has no failure line to correct it). ZXNR's first cut printed its f18
    // on the one bring-up that actually armed and looked like it had done
    // nothing; a field meant to be read off two machines and compared has
    // to be true the first time. "Flow on" is ZXNR's f38, "Flow off" its
    // f18. A "Brd 2" machine always prints off and cannot print anything
    // else: those owners want -default rather than -fast.
    // WHY, not just whether (5.9.9). The 5.9.8 field report was "Flow off on
    // a KS2, and it hangs" - and "Flow off" could not distinguish a board
    // that cannot from a module that refused the armed command, which is
    // what had actually happened. Three outcomes, three strings:
    //   "Flow off"       - not asked for (no -fc), or this board has no pins
    //   "Flow on"        - asked, capable, and the module took ",3"
    //   "Flow off esp"   - asked and capable, but the module refused and we
    //                      fell back to ",0"; the transfer is unprotected but
    //                      the link is the same one 5.9.7 would have built.
    //   "Flow off ate"   - the module TOOK ",3" and then the link failed its
    //                      first command, so we disarmed our half. Not the
    //                      module's fault, and not the same link as esp:
    //                      that one is symmetric at ,0, this one is the
    //                      module still at ,3 with our bit clear.
    print(g_flow ? "Flow on"
                 : (g_flow_esp ? "Flow off esp"
                               : (g_flow_ate ? "Flow off ate" : "Flow off")));

    // Runs once (the handshake "goto retryconnect"s land BELOW this line), so
    // the retry budget is never refilled by a mid-session reconnect.
    conn_tries = 0;

retryconnect:

    // Try disconnecting a few times just in case.
    retrycount = 10;
    while (retrycount && atcmd("AT+CIPCLOSE\r\n", "ERROR", 5, inbuf)) { retrycount--; }

    memcpy(scratch, cipstart_prefix, 19);
    memcpy(scratch+19, fn, len);
    memcpy(scratch+19+len, cipstart_postfix, 9); // take care to copy the terminating zero

    if (atcmd(scratch, "OK", 2, inbuf))
    {
        // The server may simply not be RUNNING yet: retry the connect up to
        // 3 times, ~2 s apart, giving the user a moment to start the classic
        // sync server / Remote Explorer on the PC before the old hard
        // failure. Every mode connects here, so all of them benefit. BREAK
        // exits immediately (checked before and all through each pause),
        // with the same message the -listen loop uses.
        if (conn_tries < 3)
        {
            if (break_pressed()) goto connbreak;   // held during the attempt
            conn_tries++;
            scratch[0] = '0' + conn_tries;   // the CIPSTART cmd in scratch is
            scratch[1] = 0;                  // dead here - rebuilt on retry
            *((unsigned char *)23692) = 255;
            conprint("Retrying connection (");
            conprint(scratch);
            conprint("/3)...\r");
            {
                // ~2 s: 100 ticks of the ROM's 50 Hz FRAMES counter (same
                // sysvar the animation paces itself with; ~1.7 s on a 60 Hz
                // display, close enough).
                unsigned char frames = 100;
                unsigned char last = *((unsigned char *)23672);
                while (frames)
                {
                    unsigned char now = *((unsigned char *)23672);
                    if (now != last) { last = now; frames--; }
                    if (break_pressed()) goto connbreak;
                }
            }
            goto retryconnect;
connbreak:
            print("Break - stopping");
            goto bailout;
        }
        print("Unable to connect");
        goto bailout;
    }
        
    retrycount = 0;

    // Connected. Start the optional sprite eye-candy (no-op unless -anim/-a).
    if (g_anim) anim_begin();

    if (listenmode)
    {
        // Remote file server. New handshake keyword: only a NEW server answers
        // "Listening"; an old server replies "Error" and we bail out (Sync3/
        // Sync4 handshakes are never affected).
        cipxfer("Listen", 6, inbuf, &len, &dp);
        if (len >= 9 && checksum(dp, len - 3) == 0 && memcmp(dp, "Busy", 4) == 0)
        {
            // 5.7.2: a busy-aware server (ZX-Next-Unite's Remote Explorer,
            // ZXNextRemote's Listener) is already serving ANOTHER Next and
            // said so by name. Before this reply existed the newcomer sat in
            // the server's TCP backlog until its own timeout ran out - then
            // blamed the server's age ("Server too old") for what was merely
            // a taken seat.
            print("Server busy: another Next");
            goto closeconn;
        }
        if (len < 12 || checksum(dp, len - 3) != 0 || memcmp(dp, "Listening", 9) != 0)
        {
            print("Server too old (-listen)");
            goto closeconn;
        }
        print("Listening for commands");

        // Poll the server for the next command and run it, until "Q" (quit).
        for (;;)
        {
            // BREAK (CAPS SHIFT + SPACE) requests a graceful exit - the Next-side
            // equivalent of Ctrl-C. Checked ONLY here, at the top of the poll
            // loop, i.e. strictly BETWEEN commands: every ls/get/put/... runs to
            // completion inside the loop body before we get back here, so a file
            // or directory transfer is never interrupted half-way. Leaving the
            // loop drops into the same clean close path as the server's 'Q'.
            if (break_pressed())
            {
                print("Break - stopping");
                break;
            }
            anim_tick();   // eye-candy step (no-op unless -anim); safe: between commands
            cipxfer("Poll", 4, inbuf, &len, &dp);
            if (len < 4 || checksum(dp, len - 3) != 0)
            {
                // v5.4: a dead link used to leave this loop polling forever.
                // When the server goes away WITHOUT its goodbye 'Q' reaching
                // us (app killed or crashed, PC put to sleep, wifi drop, or
                // the app's clean-shutdown 'Q' losing its 10s race against a
                // long transfer), the esp answers every AT+CIPSENDEX on the
                // closed connection with ERROR, cipxfer returns an empty
                // frame, and we re-polled a corpse for eternity - looking
                // exactly like "listening but ignoring all commands" (only
                // BREAK helped). Give up after enough bad polls IN A ROW;
                // any good frame resets the count below, so uart noise and
                // brief wifi hiccups still just re-poll like before.
                // retrycount is free in listen mode (see "retrycount = 0"
                // after connect); each dead poll burns the full cipxfer/
                // atcmd timeout, so 8 in a row is many seconds of silence.
                retrycount++;
                if (retrycount >= 8)
                {
                    print("Connection lost - stopping");
                    goto listen_noreply;    // dead link - nothing answers a Bye
                }
                flush_uart_hard();          // bad/empty frame - re-poll
                continue;
            }
            retrycount = 0;                 // good frame - the link is alive

            {
                unsigned char op = dp[0];
                unsigned short al = len - 3 - 1;   // path length (payload minus opcode)
                if (al > 254) al = 254;
                memcpy(fn, dp + 1, al);
                fn[al] = 0;

                // -v: echo the command NAME + path we received, e.g. "> put /ho/bj.txt".
                // Using the full verb keeps the trace consistent with the result
                // lines below ("put done", "mkdir ok", ...) rather than a raw opcode.
                if (g_verbose && op != 'I')
                {
                    *((unsigned char *)23692) = 255;
                    conprint("> "); conprint(listen_cmd_name(op));
                    conprint(" "); conprint(fn); conprint("\r");
                }

                if (op == 'Q') goto listen_noreply; // quit listen mode (server-sent)
                // idle: throttle before re-polling, animating meanwhile (the
                // tick self-limits to one step per frame, so the sprites
                // glide instead of stuttering once per poll).
                else if (op == 'I') { for (len = 0; len < 30000; len++) anim_tick(); }
                else if (op == 'L') { listen_ls(fn, inbuf, scratch); vprint(s_ok); }
                else if (op == 'G')
                {
                    // get: push the file, or the whole directory tree, then 'B'.
                    unsigned char fh = fopen((unsigned char *)fn, 1);
                    g_packetno = 0;
                    if (fh)
                    {
                        fclose(fh);
                        send_file(fn, fn, inbuf, scratch);
                    }
                    else
                    {
                        unsigned short plen = 0;
                        while (fn[plen]) plen++;
                        if (plen && fn[plen - 1] == '/') { plen--; fn[plen] = 0; }
                        send_dir(fn, plen, inbuf, scratch, inbuf);
                    }
                    // The terminal 'B' is what tells the server the item is
                    // complete. Its ack MATTERS: when it never arrives (the
                    // server stopped reading - a wedged bridge, a dropped
                    // link), send_block_rt burns all 12 retries and gives
                    // up, and printing "get done" there actively misled
                    // debugging - the Next looked like it had succeeded
                    // while the PC side had nothing. Report what happened.
                    scratch[2] = 'B';
                    vprint(send_block_rt(scratch, 1, inbuf) ? "get failed"
                                                            : "get done");
                }
                else if (op == 'P')
                {
                    // put: the Next pulls the file from the server. On failure -
                    // couldn't create the file (2) or the transfer gave up (1) -
                    // push an 'F' status so the server knows and can report it; on
                    // success the server already counted every byte, so send none.
                    if (transfer(fn, inbuf)) { vprint(s_fail); listen_status(0, inbuf, scratch); }
                    else vprint(s_ok);
                }
                else if (op == 'W') { listen_drives(inbuf, scratch); vprint(s_ok); }
                else if (op == 'Z') { listen_free(fn, inbuf, scratch); vprint(s_ok); }
                else if (op == 'Y') { listen_ident(inbuf, scratch); vprint(s_ok); }
                // crc ('K', v5.9.2): 'O' + 8 hex digits of the file's CRC-32,
                // printed here too (verbose or not: it is the answer).
                else if (op == 'K') { g_packetno = 0;
                                      if (crc_run(fn, (char *)scratch + 3)) { print((char *)scratch + 3); scratch[2] = 'O'; send_block_rt(scratch, 9, inbuf); }
                                      else listen_status(0, inbuf, scratch); }
                else if (op == 'C')
                {
                    // rcpy: the payload is src '\0' dst, exactly like ren -
                    // the embedded NUL terminates src, fn[al]=0 terminates dst.
                    unsigned short sp = 0;
                    while (sp < al && fn[sp]) sp++;
                    if (sp < al) { listen_rcpy(fn, fn + sp + 1, inbuf, scratch); vprint(s_ok); }
                    else { vprint(s_bad); listen_status(0, inbuf, scratch); }
                }
                else if (op == 'S') { listen_rfsize(fn, inbuf, scratch); vprint(s_ok); }
                else if (op == 'M') { unsigned char ok = sync_mkdir(fn)  != 0xFF; vprint(ok ? s_ok : s_fail); listen_status(ok, inbuf, scratch); }
                else if (op == 'R') { unsigned char ok = sync_rmdir(fn)  != 0xFF; vprint(ok ? s_ok : s_fail); listen_status(ok, inbuf, scratch); }
                else if (op == 'X') { unsigned char ok = sync_unlink(fn) != 0xFF; vprint(ok ? s_ok : s_fail); listen_status(ok, inbuf, scratch); }
                // release: close the OS's own read handle on this dot's file
                // so the server can swap /dot/sync5 while we run (hardware-
                // measured: ren on it fails while it is open). CONTRACT:
                // after 'U' the server sends only V/X/Q - see the protocol
                // comment above. No result vprint (unlike its M/R/X/V
                // siblings): its two strings alone busted the 150-byte
                // main-bank stack floor; -v still traces "> release" at
                // dispatch and the PC reports the status block's verdict.
                else if (op == 'U') { listen_status(sync_release_self() != 0xFF, inbuf, scratch); }
                else if (op == 'V')
                {
                    // ren: the payload is old '\0' new. fn already holds both -
                    // the embedded NUL terminates 'old', fn[al]=0 terminates
                    // 'new'. Find the separator within the al payload bytes.
                    unsigned short sp = 0;
                    while (sp < al && fn[sp]) sp++;
                    if (sp < al)   // separator found -> new path starts after it
                    {
                        unsigned char ok = sync_rename(fn, fn + sp + 1) != 0xFF;
                        vprint(ok ? "ren ok" : "ren fail");
                        listen_status(ok, inbuf, scratch);
                    }
                    else { vprint(s_bad); listen_status(0, inbuf, scratch); }
                }
            }
        }
        // BREAK falls out here: the server is still in its command loop and
        // answers our "Bye" with "Later", so the polite goodbye stays.
        print("Listen ended");
        goto closeconn;

listen_noreply:
        // 5.7.5: server-sent quit ('Q' - incl. the /forceexit-marked one) or a
        // dead link. Either way NOTHING will answer a "Bye": after its 'Q' the
        // server only drains the socket (nextsync5 _goodbye_linger), so the
        // classic goodbye below just stared at silence for its full cipxfer
        // timeout - the long "Closing.." hang on quit (~20 s on -s). Skip the
        // handshake and close the socket directly.
        print("Listen ended");
        print("Closing..");
        goto closenobye;
    }

    if (sendmode)
    {
        // Negotiate the bidirectional protocol. Only an updated app answers
        // "NextSync4"; older apps/servers reply Error and we bail out.
retryhandshake4:
        cipxfer("Sync4", 5, inbuf, &len, &dp);
        if (len < 9 || memcmp(dp, "NextSync4", 9) != 0)
        {
            retrycount++;
            if (retrycount < 5)
            {
                if (len == 0) goto retryconnect;
                flush_uart_hard();
                goto retryhandshake4;
            }
            print("App too old");
            goto closeconn;
        }

        // Switch the server into receive-from-Next mode.
        cipxfer("Send", 4, inbuf, &len, &dp);

        g_packetno = 0;

        {
            // Decide file vs directory with fopen (proven on the receive path)
            // so a single-file send never calls opendir/readdir.
            unsigned char fh = fopen((unsigned char*)sendpath, 1); // read existing
            if (fh)
            {
                fclose(fh);
                send_file(sendpath, sendpath, inbuf, scratch);
            }
            else
            {
                // Not openable as a file - treat it as a directory. Two passes:
                // (1) collect every file path with enumeration only (no file I/O,
                // so the readdir cursor can't be corrupted), then (2) send them
                // with no directory handle open.
                unsigned short plen = 0;
                while (sendpath[plen]) plen++;
                // drop a trailing slash so paths don't get doubled
                if (plen && sendpath[plen - 1] == '/') { plen--; sendpath[plen] = 0; }
                send_dir(sendpath, plen, inbuf, scratch, inbuf);
            }
        }

        // Tell the server we're done sending.
        scratch[2] = 'B';
        send_block_rt(scratch, 1, inbuf);

        print("Upload done");
        // Ours first, as at closeconn/closenobye: this CIPCLOSE has to get
        // out through the very transmitter a set bit 5 could be holding,
        // and this exit reaches bailout's clear only AFTER sending it.
        FLOW_OFF();
        atcmd("AT+CIPCLOSE\r\n", "", 0, inbuf);
        goto bailout;
    }

retryhandshake:
    // Check server version/request protocol
    cipxfer("Sync3", 5, inbuf, &len, &dp);

    if (len < 9 || memcmp(dp, "NextSync3", 9) != 0)
    {        
        retrycount++;
        if (retrycount < 5)
        {
            if (len == 0) goto retryconnect;
            flush_uart_hard();
            goto retryhandshake;
        }
        print("Ver mismatch");
        goto closeconn;
    }

    do
    {

        cipxfer("Next", 4, inbuf, &len, &dp);
retrynext:
        if (checksum(dp, len-3) == 0)
        {
            // dp[0..3] = file length (big endian) - not displayed (16-bit UI)
            fnlen = dp[4];
            memcpy(fn, dp+5, fnlen);
            fn[fnlen] = 0;
            if (*fn)
            {
                print(fn);
                anim_tick();   // eye-candy step per file (no-op unless -anim)
                // 1 = link loss (fatal here); 2 = couldn't open this file, so skip
                // it and keep syncing the rest (mirrors the old return-0 behaviour).
                if (transfer(fn, inbuf) == 1)
                {
                    print("Lost connection.");
                    goto closeconn;
                }
            }
        }
        else
        {
            flush_uart_hard();
            cipxfer("Retry", 5, inbuf, &len, &dp);
            goto retrynext;
        }
    }
    while (*fn != 0);
    
closeconn:
    // OURS FIRST, AND HERE rather than only at bailout: the "Bye" below
    // and the CIPCLOSE after it both have to get out through the very
    // transmitter a stale bit 5 would be holding. Parked, they burn their
    // timeouts and deliver nothing, and the server never learns we left -
    // which since Unite 9.7.18 means it holds our seat for
    // PEER_SILENCE_LIMIT (620 s since 9.7.24; it was 400 when that
    // mechanism landed). One OUT, needs no working transmitter,
    // releases a parked one on the next clock edge. UNGATED, like every
    // other clear: our bit is cheap to write and a stale one is not.
    FLOW_OFF();
    print("Closing..");
    cipxfer("Bye", 3, inbuf, &len, &dp);
closenobye:
    // AGAIN, because "goto closenobye" (the -listen quit) jumps PAST the
    // clear above. Without this the CIPCLOSE below is the one wire
    // command the closeconn comment names as protected and is not -
    // idempotent, one IN and one OUT, and it makes the invariant true on
    // every route rather than on the one it was written beside.
    FLOW_OFF();
    atcmd("AT+CIPCLOSE\r\n", "", 0, inbuf);
bailout:
    // Again for the five direct "goto bailout"s, which skip closeconn, and
    // before the restore below for the same reason. THIS IS THE WHOLE EXIT
    // SURFACE: closeconn and closenobye fall through here, every goto
    // bailout lands here, and the only exits that skip it are the
    // "goto terminate"s in the argument parsing - every one ABOVE
    // "UART_CTL = 16", i.e. before the entry clear and long before
    // anything can set the bit. IF A NEW EARLY EXIT IS ADDED BELOW THE
    // UART BRING-UP IT MUST COME HERE, NOT TO terminate. The restore's own
    // fifth field is already 0, so the MODULE is disarmed for free on
    // every wire exit; only our side needed this. UNGATED for the reason
    // the entry clear is: the restore below runs on every run, so gating
    // only our half is what leaves the two ends disagreeing.
    FLOW_OFF();
    anim_end();   // hide sprites + restore the sprite/layers reg (no-op unless -anim)
    atcmd("AT+UART_CUR=115200,8,1,0,0\r\n", "", 0, inbuf); // restore uart speed
    print("All done");
    writenextreg(0x07, nextreg7); // restore cpu speed
    writenextreg(0x06, nextreg6); // restore turbo key & 50/60 switch
terminate:
    *((unsigned char  *)23692) = saved_scr_ct;  // restore ROM scroll counter
    if (g_dark)                                  // undo the -dark look
    {
        *((unsigned char *)23693) = saved_attr_p;   // restore colours (back to black ink)
        *((unsigned char *)23695) = saved_attr_t;
    }
    return 0; // clean exit to BASIC (crt returns with carry clear)
}