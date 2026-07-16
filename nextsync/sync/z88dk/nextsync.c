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

#define TIMEOUT 20000
#define TIMEOUT_FLUSHUART 10000

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

// Command line pointer (was a crt0 global). main() points it first at the raw
// NextZXOS command tail, then at the cleaned private buffer. The old crt0 also
// exported framecounter/dbg/scr_x/scr_y/osiy - all unused here, so dropped.
char *cmdline;
unsigned short corever;
char g_verbose = 0;   // -v: echo -listen commands/actions on the Next screen
char g_anim = 0;      // -anim/-a: hardware-sprite eye-candy while syncing (opt-in)
char g_dark = 0;      // -dark/-d: retro green-on-black look + custom font (opt-in)

// Optional sprite animation (anim.c). The functions self-guard (tick/end do
// nothing until begin has run), so they're safe to call unconditionally.
extern void anim_begin(void);
extern void anim_tick(void);
extern void anim_end(void);

// See calc_prescalar.c for the prescalar calculation code
static const unsigned short prescalar_values[] = {
  243,   248,   255,   260,   269,   277,   286,   234, // (0) 115200
  486,   496,   511,   520,   538,   555,   572,   468, // (1) 57600
  729,   744,   767,   781,   807,   833,   859,   703, // (2) 38400
  896,   914,   942,   960,   992,  1024,  1056,   864, // (3) 31250
 1458,  1488,  1534,  1562,  1614,  1666,  1718,  1406, // (4) 19200
 2916,  2976,  3069,  3125,  3229,  3333,  3437,  2812, // (5) 9600
 5833,  5952,  6138,  6250,  6458,  6666,  6875,  5625, // (6) 4800
11666, 11904, 12276, 12500, 12916, 13333, 13750, 11250, // (7) 2400
  121,   124,   127,   130,   134,   138,   143,   117, // (8) 230400
   60,    62,    63,    65,    67,    69,    71,    58, // (9) 460800
   48,    49,    51,    52,    53,    55,    57,    46, // (10) 576000
   30,    31,    31,    32,    33,    34,    35,    29, // (11) 921600
   24,    24,    25,    26,    26,    27,    28,    23, // (12) 1152000
   18,    19,    19,    20,    20,    21,    22,    18, // (13) 1500000
   14,    14,    14,    15,    15,    16,    16,    13  // (14) 2000000    
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

// Print "<label><number>" on one line (e.g. "wrote 512"), verbose only.
void vlabelnum(char *label, unsigned short v)
{
    char b[8];
    if (!g_verbose) return;
    *((unsigned char *)23692) = 255;
    conprint(label);
    uitoa(v, b);
    conprint(b);
    conprint("\r");
}

extern unsigned char uitoa(unsigned short v, char *b);

void printnum(unsigned short v);

// Just flush as much as is in the queue right now.
void flush_uart(void)
{
    while (UART_TX & 1)
    {
        UART_RX;
    }
} 

// Do the maximum effort to empty the uart.
void flush_uart_hard(void)
{
    unsigned short timeout = TIMEOUT_FLUSHUART;
    while (timeout)
    {
        if (UART_TX & 1)
        {
            UART_RX;
            timeout = TIMEOUT_FLUSHUART;
        }
        timeout--;
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

void send(const char *b, unsigned char bytes)
{
    unsigned short timeout = TIMEOUT;
    unsigned char t;
    while (timeout && bytes)
    {
        // busy wait until byte is transmitted
        do
        {
            timeout--;
            t = UART_TX;
        }
        while ((t & 2) && timeout); // bit 1 = 1 if the Tx buffer is full
        
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
    *dataptr = output + 2; // skip size bytes
    *len = received - 2; // reduce size bytes    
}

char gofast(char *inbuf)
{
    if (g_syncmode == MODE_FAST)
        atcmd("AT+UART_CUR=2000000,8,1,0,0\r\n", "", 0, inbuf);
    else
        atcmd("AT+UART_CUR=1152000,8,1,0,0\r\n", "", 0, inbuf);

    setupuart(g_fast_uart_mode);
    flush_uart_hard();
    if (atcmd("\r\n", "ERROR", 5, inbuf))
    {
        print("No fast esp");
        return 1;
    }
    return 0;
}

unsigned char createfilewithpath(char * fn)
{
    unsigned char filehandle;
    char * slash;
    if (g_verbose) { vprint("open:"); vprint(fn); }
    filehandle = fopen(fn, 2 + 0x0c);  // write + create new file, delete existing
    if (filehandle) { vprint("open ok"); return filehandle; }
    vprint("open failed, mkdir path");
    // Okay, couldn't create the file, so let's try to make the path.
    // We need to call makepath for each directory in the tree to build
    // complex paths.
    slash = fn;
    while (*slash)
    {
        slash++;
        if (*slash == '/')
        {
            *slash = 0;      // esx_f_mkdir wants a 0-terminated path prefix
            if (g_verbose) { vprint("mkdir:"); vprint(fn); }
            sync_mkdir(fn);  // make this directory level (ignore "exists")
            *slash = '/';
        }
    }
    filehandle = fopen(fn, 2 + 0x0c); // if it still doesn't work, well, it doesn't.
    vprint(filehandle ? "open ok (2)" : "open still failed");
    return filehandle;
}

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
        return 0;
    }

    do
    {
        cipxfer("Get", 3, inbuf, &len, &dp);
retry:
        if (dp[len - 1] != packetno)
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

    fclose(filehandle);
    return 0;
failure:
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

static unsigned char g_packetno;

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
//                                        look with the custom font (OFF by
//                                        default; opt-in because it repaints the
//                                        screen and is not wanted in every mode)
//
// Also consumes the standalone option flags "-v" (verbose), "-a" and "-anim"
// (sprite eye-candy), setting their globals. Consuming them here means they are
// dropped from the cleaned command line, so e.g. ".sync4 -anim" still runs a
// normal PC->Next sync (with anim) instead of being mistaken for a bad argument.
unsigned char setspeed(char *p, unsigned char n)
{
    if (*p != '-') return 0;
    if (p[1] == 'f' && (n == 5 || n == 2))              { g_syncmode = MODE_FAST;    return 1; }
    if (p[1] == 'd' && n == 8)                          { g_syncmode = MODE_DEFAULT; return 1; }
    if (p[1] == 'd' && (n == 2 || (n == 5 && p[2] == 'a'))) { g_dark = 1;            return 1; }
    if (p[1] == 's' && (n == 2 || (n == 5 && p[2] == 'l'))) { g_syncmode = MODE_SLOW; return 1; }
    if (p[1] == 'v' && n == 2)                          { g_verbose = 1;             return 1; }
    if (p[1] == 'a' && n == 2)                          { g_anim = 1;                return 1; }
    if (p[1] == 'a' && n == 5 && p[2] == 'n')           { g_anim = 1;                return 1; }
    return 0;
}

// Pull the -slow/-default/-fast/-v/-anim/-a switches out of the command line and
// copy the remaining tokens into dst. Sets g_syncmode/g_verbose/g_anim. Works
// anywhere in the line.
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
//        'Q'          quit  -> leave listen mode
//
// ls/get/mkdir/rmdir/rm/ren answer by PUSHING blocks to the server (each acked
// with a framed "Ok", exactly like -send):
//   ls  : 'D' blocks of packed entries, then 'E'.
//         entry = [1B flags][4B size, little-endian][1B namelen][name],
//         flags bit0 = directory.
//   get : send_file / send_dir ('N'/'D'/'E' per file), then a final 'B'.
//   mkdir/rmdir/rm/ren : one status block, 'O' (ok) or 'F' (fail). 'ren'
//         carries two NUL-separated paths in one frame (old then new).
//   put : reuses transfer() - the Next pulls data with "Get", server serves it.
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
// so the directory cursor is safe.
void listen_ls(char *path, unsigned char *inbuf, unsigned char *scratch)
{
    unsigned char dh, i, nl;
    sync_dirent_t ent;
    unsigned short used = 0;   // entry bytes packed after the opcode (at scratch[3])

    g_packetno = 0;

    dh = opendir((unsigned char *)path);
    if (dh == 0) { listen_status(0, inbuf, scratch); return; }  // 'F' - can't open

    while (sync_readdir_entry(dh, &ent))
    {
        nl = 0;
        while (ent.name[nl]) nl++;
        if (used + 6 + nl > 1000)          // flush the current block first
        {
            scratch[2] = 'D';
            send_block_rt(scratch, (unsigned short)(1 + used), inbuf);
            used = 0;
        }
        scratch[3 + used++] = ent.is_dir ? 1 : 0;
        scratch[3 + used++] = (unsigned char)(ent.size);
        scratch[3 + used++] = (unsigned char)(ent.size >> 8);
        scratch[3 + used++] = (unsigned char)(ent.size >> 16);
        scratch[3 + used++] = (unsigned char)(ent.size >> 24);
        scratch[3 + used++] = nl;
        for (i = 0; i < nl; i++) scratch[3 + used++] = ent.name[i];
    }
    fclose(dh);

    if (used)                              // flush remaining entries
    {
        scratch[2] = 'D';
        send_block_rt(scratch, (unsigned short)(1 + used), inbuf);
    }
    scratch[2] = 'E';
    send_block_rt(scratch, 1, inbuf);
}

// Big I/O buffers live in bss (main bank, mmu4/mmu5) rather than on the stack:
// under the dotN model that keeps the stack small and forces those pages to be
// allocated and mapped. They are only used from main() and its callees, one
// invocation at a time, so static is safe.
static char fn[256];
static char inbuf[2048];
static char scratch[1280]; // outgoing block: 1024 file bytes + opcode + framing (~1030 max)
static char sendpath[256];
static char cleancmd[256]; // command line with speed switches removed (never touch the OS buffer)

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
    unsigned char saved_scr_ct;
    unsigned char saved_attr_p, saved_attr_t;

    // Save SCR_CT (23692) before print() starts forcing it to 255, so it can be
    // restored at terminate. Leaving it at 255 would suppress the ROM "scroll?"
    // prompt for the rest of the BASIC session after the dot command exits.
    saved_scr_ct = *((unsigned char *)23692);

    // Point cmdline at the raw NextZXOS command tail handed to us by the crt.
    (void)arglen;
    cmdline = rawcmd;

    // Strip speed/option switches into a private buffer (never write the OS
    // cmdline), then point cmdline at it so the normal parser sees the cleaned
    // line. This sets g_dark (from -dark/-d) before we decide on the look below.
    g_syncmode = MODE_FAST; // default when no -slow/-default/-fast is given
    parse_speed_switches(cleancmd);
    cmdline = cleancmd;
    g_fast_uart_mode = (g_syncmode == MODE_FAST) ? 14 : 12;

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

    print("NextSync 4.7 Clauzel/Komppa");

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

    // Detect "-listen" (or its short alias "-l"): run as a remote file server
    // driven by the PC. Like -send, it connects to the saved server (from the
    // config file). "-l" is accepted because "-listen" is a mouthful to type.
    if (!sendmode &&
        ((len >= 7 && fn[0] == '-' && fn[1] == 'l' && fn[2] == 'i' && fn[3] == 's' &&
          fn[4] == 't' && fn[5] == 'e' && fn[6] == 'n' && (fn[7] == 0 || fn[7] == ' ')) ||
         (len >= 2 && fn[0] == '-' && fn[1] == 'l' && (fn[2] == 0 || fn[2] == ' '))))
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
            conprint(
               //12345678901234567890123456789012
                "SYNC v4.7 Clauzel/Komppa\r"
                ".SYNC [server] : save cfg\r"
                ".SYNC : sync files from PC\r"
                ".SYNC -send <file|dir> : to PC\r"
                ".SYNC -listen|-l : file server\r"
                "  PC drives: ls get put\r"
                "  mkdir rmdir rm ren\r"
                "  BREAK key stops it (safe)\r"
                ".SYNC -slow|-default|-fast\r"
                ".SYNC -dark|-d : retro look\r"
                ".SYNC -v : verbose trace\r"
                ".SYNC -anim|-a : sprite fun\r"
                "See nextsync.txt\r\r");
            goto terminate;
        }

        if (isserver)
        {
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
            conprint("No server set - .sync <ip>\r");
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

    nextreg6 = readnextreg(0x06);
    writenextreg(0x06, nextreg6 & 0x7d); // disable turbo key & 50/60 switch (leave other bits alone)
    nextreg7 = readnextreg(0x07);
    writenextreg(0x07, 3); // 28MHz

    // read Next core version - e.g. 3.01.10 will be 0x310a
    corever = readnextreg(0x01) * 256 + readnextreg(0x0e);

    // select esp uart, set 17-bit prescalar top bits to zero
    UART_CTL = 16; 
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

    if (g_syncmode != MODE_SLOW && !fastuart && gofast(inbuf))
        goto bailout;

    atcmd("ATE0\r\n", "OK", 2, inbuf); // command echo off; if on, we might match server name as OK/ERROR/BUSY =)

retryconnect:

    // Try disconnecting a few times just in case.
    retrycount = 10;
    while (retrycount && atcmd("AT+CIPCLOSE\r\n", "ERROR", 5, inbuf)) { retrycount--; }

    memcpy(scratch, cipstart_prefix, 19);
    memcpy(scratch+19, fn, len);
    memcpy(scratch+19+len, cipstart_postfix, 9); // take care to copy the terminating zero

    if (atcmd(scratch, "OK", 2, inbuf))
    {
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
                flush_uart_hard();          // bad/empty frame - re-poll
                continue;
            }

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

                if (op == 'Q') break;               // quit listen mode
                else if (op == 'I') { for (len = 0; len < 30000; len++); } // idle: throttle before re-polling
                else if (op == 'L') { listen_ls(fn, inbuf, scratch); vprint("ls done"); }
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
                    scratch[2] = 'B';
                    send_block_rt(scratch, 1, inbuf);
                    vprint("get done");
                }
                else if (op == 'P') { if (transfer(fn, inbuf)) vprint("put failed"); else vprint("put done"); } // put
                else if (op == 'M') { unsigned char ok = sync_mkdir(fn)  != 0xFF; vprint(ok ? "mkdir ok" : "mkdir fail"); listen_status(ok, inbuf, scratch); }
                else if (op == 'R') { unsigned char ok = sync_rmdir(fn)  != 0xFF; vprint(ok ? "rmdir ok" : "rmdir fail"); listen_status(ok, inbuf, scratch); }
                else if (op == 'X') { unsigned char ok = sync_unlink(fn) != 0xFF; vprint(ok ? "rm ok" : "rm fail"); listen_status(ok, inbuf, scratch); }
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
                    else { vprint("ren malformed"); listen_status(0, inbuf, scratch); }
                }
            }
        }
        print("Listen ended");
        goto closeconn;
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
                if (transfer(fn, inbuf))
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
    print("Closing..");
    cipxfer("Bye", 3, inbuf, &len, &dp);
    atcmd("AT+CIPCLOSE\r\n", "", 0, inbuf);
bailout:
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