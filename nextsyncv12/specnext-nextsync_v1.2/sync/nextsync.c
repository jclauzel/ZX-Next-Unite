/* 
 * Part of Jari Komppa's zx spectrum next suite 
 * https://github.com/jarikomppa/specnext
 * released under the unlicense, see http://unlicense.org 
 * (practically public domain) 
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

extern unsigned char fopen(unsigned char *fn, unsigned char mode);
extern void fclose(unsigned char handle);
extern unsigned short fread(unsigned char handle, unsigned char* buf, unsigned short bytes);
extern void fwrite(unsigned char handle, unsigned char* buf, unsigned short bytes);
extern unsigned char opendir(unsigned char *path);
extern unsigned char readdir(unsigned char handle, unsigned char *buf);
extern void makepath(char *pathspec); // must be 0xff terminated!
extern void conprint(char *txt) __z88dk_fastcall;

extern void writenextreg(unsigned char reg, unsigned char val);
extern unsigned char readnextreg(unsigned char reg);
extern unsigned char allocpage();
extern void freepage(unsigned char page);

__sfr __banked __at 0x133b UART_TX;
__sfr __banked __at 0x143b UART_RX;
__sfr __banked __at 0x153b UART_CTL;

extern unsigned short receive(char *b);
extern char checksum(char *dp, unsigned short len);

extern void memcpy(char *dest, const char *source, unsigned short count);
extern unsigned short mulby10(unsigned short input) __z88dk_fastcall;

extern unsigned short framecounter;
extern char *cmdline;
extern char dbg;
extern unsigned short corever;

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

char memcmp(char *a, char *b, unsigned short l)
{
    unsigned short i = 0;
    while (i < l)
    {
        char v = a[i] - b[i];
        if (v != 0) return v;            
        i++;
    }
    return 0;
}

void memset(char *a, char b, unsigned short l)
{
    unsigned short i = 0;
    while (i < l)
    {
        a[i] = b;
        i++;
    }
}

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

extern unsigned char uitoa(unsigned short v, char *b);

void printnum(unsigned short v);

// Just flush as much as is in the queue right now.
void flush_uart()
{
    while (UART_TX & 1)
    {
        UART_RX;
    }
} 

// Do the maximum effort to empty the uart.
void flush_uart_hard()
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

unsigned char receive_slow()
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
    filehandle = fopen(fn, 2 + 0x0c);  // write + create new file, delete existing
    if (filehandle) return filehandle;
    // Okay, couldn't create the file, so let's try to make the path.
    // We need to call makepath for each directory in the tree to build
    // complex paths.
    slash = fn;    
    while (*slash) 
    {
        slash++;
        if (*slash == '/')
        {
            *slash = 0xff; // makepath wants strings to end with 0xff
            makepath(fn);    
            *slash = '/';
        }
    }
    return fopen(fn, 2 + 0x0c); // if it still doesn't work, well, it doesn't.
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

// Set g_syncmode if the n-char token at p is a -slow/-default/-fast switch and
// return 1, else return 0. Matched by first char + length (much cheaper than
// memcmp on z80): "-fast" (-f, len 5), "-default" (-d, len 8), "-slow" (-s, len
// 5, with 3rd char 'l' so it isn't confused with "-send").
unsigned char setspeed(char *p, unsigned char n)
{
    if (*p != '-') return 0;
    if (p[1] == 'f' && n == 5)                  { g_syncmode = MODE_FAST;    return 1; }
    if (p[1] == 'd' && n == 8)                  { g_syncmode = MODE_DEFAULT; return 1; }
    if (p[1] == 's' && n == 5 && p[2] == 'l')   { g_syncmode = MODE_SLOW;    return 1; }
    return 0;
}

// Pull -slow/-default/-fast switches out of the command line and copy the
// remaining tokens into dst. Sets g_syncmode. Works anywhere in the line.
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

void main()
{                                 //1234567890123456789012
    const char *cipstart_prefix  = "AT+CIPSTART=\"TCP\",\"";
    const char *cipstart_postfix = "\",2048\r\n";
    const char *conffile         = "c:/sys/config/nextsync.cfg";
    char fn[256];
    char inbuf[2048];
    char scratch[1280]; // outgoing block: 1024 file bytes + opcode + framing (~1030 max)
    char sendpath[256];
    char cleancmd[256]; // command line with speed switches removed (never touch the OS buffer)
    char sendmode = 0;
    unsigned char fnlen;
    unsigned char *dp;
    unsigned short len = 0;
    unsigned char nextreg6;
    unsigned char nextreg7;
    char fastuart = 0;
    char filehandle = 0; // init to silence "used before init" (the read is guarded, but be safe)
    char retrycount;

    // Strip speed switches into a private buffer (never write the OS cmdline),
    // then point cmdline at it so the normal parser sees the cleaned line.
    g_syncmode = MODE_FAST; // default when no -slow/-default/-fast is given
    parse_speed_switches(cleancmd);
    cmdline = cleancmd;
    g_fast_uart_mode = (g_syncmode == MODE_FAST) ? 14 : 12;

    print("NextSync 2.0 Clauzel/Komppa");

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

    if (!sendmode)
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
                "SYNC v2.0 Clauzel/Komppa\r"
                ".SYNC [server] : save cfg\r"
                ".SYNC : sync from PC\r"
                ".SYNC -send <file|dir>\r"
                ".SYNC -slow|-default|-fast\r"
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
        // Send mode: load the configured server name to connect to.
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
    atcmd("AT+UART_CUR=115200,8,1,0,0\r\n", "", 0, inbuf); // restore uart speed
    print("All done");
    writenextreg(0x07, nextreg7); // restore cpu speed
    writenextreg(0x06, nextreg6); // restore turbo key & 50/60 switch
terminate:
    return;
}