/*
 * rcpy.c - print-free single-file primitives of the -listen "rcpy" command
 * ('C'). Part of NextSync 5.2.
 *
 * The DIRECTORY WALK and the command entry point live in nextsync.c (main
 * bank): v5.2's field testing asked for a per-item on-screen trace ("f-> ",
 * "d-> ", "/!\ error -> "), and head-page code must NEVER print - not even
 * through a main-bank helper (a vprint reached from a head-page frame
 * crashed real hardware in the drives.c days). So everything that traces
 * runs in the main bank, and only these print-free leaves stay here.
 *
 * HEAD PAGE RESIDENT: build_dotn.ps1 retargets this file's code/rodata into
 * the primary 8 KB dot page (like anim.c/free.c), so it costs no main-bank
 * stack headroom. Head-page rules honoured: no printing, esxDOS + main-bank
 * calls only, no file-scope static data.
 *
 * rcpy_file deliberately uses inbuf both as its 2 KB copy buffer and as the
 * ack-reception buffer of its progress blocks: safe, because a heartbeat
 * only ever fires AFTER the chunk in inbuf has been written out.
 */

#include <arch/zxn/esxdos.h>
#include <string.h>
#include "syncsys.h"   /* rcpy_state_t (shared with the main-bank caller) */

/* main-bank helpers/state from nextsync.c */
extern char send_block_rt(unsigned char *scratch, unsigned short payloadlen, unsigned char *inbuf);
extern unsigned char g_packetno;

#define RCPY_CHUNK 2048   /* copy buffer = the whole of inbuf */

/* Push one 'D' progress block: with a name (per file, so the PC can show
 * what is being copied) or empty (keepalive - also used by the main-bank
 * walk to bridge its long directory skip-loops, which otherwise leave the
 * link silent long enough for the PC to give up: seen on real hardware).
 * Returns non-zero when the link is gone (send_block_rt gave up). */
unsigned char rcpy_hb(char *name, unsigned char *inbuf, unsigned char *scratch)
{
    unsigned short nl = 0;
    if (name)
        while (name[nl] && nl < 255) nl++;
    scratch[2] = 'D';
    if (nl)
        memcpy(scratch + 3, name, nl);
    return send_block_rt(scratch, (unsigned short)(1 + nl), inbuf);
}

/* Open (create/truncate) the destination file; on failure make each missing
 * directory level of its path and retry - a print-free createfilewithpath.
 * Returns the handle, 0xFF on failure. */
static unsigned char rcpy_creat(char *dst)
{
    unsigned char fh, i;

    fh = esx_f_open(dst, 2 + 0x0c);        /* write + create, delete existing */
    if (fh != 0xFF)
        return fh;
    for (i = 1; dst[i]; i++)               /* from 1: never mkdir("") for a   */
    {                                      /* leading '/'                     */
        if (dst[i] == '/')
        {
            dst[i] = 0;
            esx_f_mkdir(dst);              /* "exists" errors are fine        */
            dst[i] = '/';
        }
    }
    fh = esx_f_open(dst, 2 + 0x0c);
    return fh;
}

/* Copy one file src -> dst.
 * Returns 0 = copied, 1 = this file failed (unreadable src, uncreatable
 * dst, short write = destination full, or a read that stopped short of the
 * file's stat size - a silent half-copy must never pass as success),
 * 2 = the LINK died (abort the whole command; per-file retries are futile). */
unsigned char rcpy_file(char *src, char *dst, unsigned char *inbuf, unsigned char *scratch)
{
    unsigned char sfh, dfh, hb = 0, err = 0;
    unsigned short n;
    unsigned long want = 0xFFFFFFFFUL, copied = 0;
    struct esx_stat es;

    if (rcpy_hb(dst, inbuf, scratch))      /* per-file progress + keepalive   */
        return 2;
    sfh = esx_f_open(src, 1);              /* read existing                   */
    if (sfh == 0xFF)
        return 1;
    if (esx_f_fstat(sfh, &es) == 0)        /* expected length, to catch a     */
        want = es.size;                    /* read error posing as EOF        */
    dfh = rcpy_creat(dst);
    if (dfh == 0xFF)
    {
        esx_f_close(sfh);
        return 1;
    }
    for (;;)
    {
        n = esx_f_read(sfh, inbuf, RCPY_CHUNK);
        if (n == 0)
            break;
        if (esx_f_write(dfh, inbuf, n) != n)   /* short write: dest full      */
        {
            err = 1;
            break;
        }
        copied += n;
        /* Keepalive every 32 chunks (64 KB) so a multi-MB file can never
         * leave the PC staring at a silent socket. */
        if ((++hb & 31) == 0 && rcpy_hb(0, inbuf, scratch))
        {
            err = 2;
            break;
        }
    }
    esx_f_close(sfh);
    esx_f_close(dfh);
    if (err == 0 && want != 0xFFFFFFFFUL && copied != want)
        err = 1;                           /* truncated by a mid-file error   */
    return err;
}

/* ---------------------------------------------------------------------------
 * The directory walk - ITERATIVE, one reportable item per call.
 *
 * Why not recursive like send_dir: the main-bank C stack is nearly full and
 * every recursion level cost ~45 bytes there (the v5.2 walk overflowed the
 * bank outright). An explicit level stack lives in the scratch tail instead
 * (+1024; the paths sit at +512/+768), so the walk runs on ONE stack frame
 * at any tree depth - and because rcpy_step RETURNS between items, the
 * main-bank caller can print each completed item with no head-page frame
 * anywhere on the stack (head-page code must never print, and hardware
 * proved that even a main-bank vprint reached FROM a head-page frame dies).
 *
 * Same safe-walk core as ever: re-open, skip to entry #cur, CLOSE, then act
 * - never file I/O while a readdir handle is open - now with keepalives
 * inside the skip loop (every 256 entries): on a LARGE directory the
 * quadratic skip otherwise leaves the link silent long enough for the PC to
 * give up mid-copy, after which every later block burns its retries unacked
 * - the "endless border blinking" hang seen on real hardware. The keepalive
 * is guarded with j != cur because its ack lands in inbuf, which also holds
 * the dirent just landed on.
 * ------------------------------------------------------------------------- */

/* rcpy_state_t (syncsys.h) lives in the 256-byte tail at scratch+1024; the
 * main-bank caller ARMS it directly before the first rcpy_step call. */

/* Advance the walk by ONE reportable item. Returns:
 *   0 = walk complete             1 = file copied      (dst = its path)
 *   2 = directory created         3 = item failed      (src = its path:
 *       (dst = its path)              strange name, unreadable, dest full,
 *                                     tree too deep, ...)
 *   4 = link dead - abort
 * The child path that 1/2/3 leave in src/dst stays valid until the NEXT
 * call (which re-truncates), so the caller prints between calls. */
unsigned char rcpy_step(unsigned char *inbuf, unsigned char *scratch)
{
    rcpy_state_t *st = (rcpy_state_t *)(scratch + 1024);
    char *src = (char *)scratch + 512;
    char *dst = (char *)scratch + 768;
    unsigned char *name;
    unsigned short j, nl, se, de;
    unsigned char dh, got, r;

    if (st->ended)
        return 0;

    for (;;)
    {
        /* Re-truncate to the current level (undoes the child path left in
         * place for the caller's print after the previous step). */
        src[st->sl[st->sp]] = 0;
        dst[st->dl[st->sp]] = 0;

        if (st->cur[st->sp] >= 4000)       /* runaway dir: treat as its end   */
            got = 0;
        else
        {
            dh = esx_f_opendir_ex(src, ESX_DIR_USE_LFN);
            if (dh == 0xFF)
            {
                /* This level's dir became unreadable: report it and pop.    */
                if (st->sp == 0) { st->ended = 1; return 3; }
                st->sp--;
                return 3;
            }
            got = 0;
            for (j = 0; j <= st->cur[st->sp]; j++)
            {
                got = esx_f_readdir(dh, inbuf);
                if (j != st->cur[st->sp] && (j & 0xFF) == 0xFF &&
                    rcpy_hb(0, inbuf, scratch))
                {
                    esx_f_close(dh);
                    return 4;
                }
            }
            esx_f_close(dh);               /* closed before any file I/O      */
        }
        if (!got)                          /* past the last entry             */
        {
            if (st->sp == 0) { st->ended = 1; return 0; }
            st->sp--;                      /* resume scanning the parent      */
            continue;
        }
        st->cur[st->sp]++;

        name = inbuf + 1;
        if (name[0] == '.' && (name[1] == 0 || (name[1] == '.' && name[2] == 0)))
            continue;
        nl = 0;
        while (name[nl]) nl++;

        /* Append "/<name>" to both paths; a base already ending in '/' (a
         * drive root like "c:/") doesn't get a second slash. */
        se = st->sl[st->sp]; if (src[se - 1] != '/') src[se++] = '/';
        de = st->dl[st->sp]; if (dst[de - 1] != '/') dst[de++] = '/';
        if (se + nl >= 254 || de + nl >= 254)
            return 3;                      /* name won't fit: skip the item   */
        memcpy(src + se, name, nl);
        src[se + nl] = 0;
        memcpy(dst + de, name, nl);
        dst[de + nl] = 0;

        if (inbuf[0] & 0x10)               /* directory                       */
        {
            esx_f_mkdir(dst);              /* exists -> merge, fine           */
            if (st->sp >= RCPY_MAX_DEPTH)
                return 3;                  /* too deep: report, don't descend */
            st->sp++;
            st->cur[st->sp] = 0;
            st->sl[st->sp] = (unsigned short)(se + nl);
            st->dl[st->sp] = (unsigned short)(de + nl);
            return 2;                      /* next call scans inside it       */
        }
        r = rcpy_file(src, dst, inbuf, scratch);
        if (r == 2)
            return 4;
        return r ? 3 : 1;
    }
}
