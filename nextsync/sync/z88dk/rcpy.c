/*
 * rcpy.c - print-free single-file primitives of the -listen "rcpy" command
 * ('C'). Part of NextSync 5.2.
 *
 * The COMMAND ENTRY POINT and every piece of on-screen tracing live in
 * nextsync.c (main bank): head-page code must NEVER print - not even
 * through a main-bank helper (a vprint reached from a head-page frame
 * crashed real hardware in the drives.c days; the print driver pages the
 * ROM over this very page). So everything here RETURNS to the main bank
 * whenever there is something to show: the directory walk one item per
 * rcpy_step call, and (v5.3) the single-file copy one 2 KB chunk per
 * rcpy_fchunk call - which is what lets the main bank print a file's name
 * BEFORE its bytes move and twirl the -v spinner while they do (a large
 * file used to leave the screen frozen until the copy finished).
 *
 * HEAD PAGE RESIDENT: build_dotn.ps1 retargets this file's code/rodata into
 * the primary 8 KB dot page (like anim.c/free.c), so it costs no main-bank
 * stack headroom. Head-page rules honoured: no printing, esxDOS + main-bank
 * calls only, and the one static (the in-flight copy state, v5.3) lands in
 * main-bank bss automatically - bss is never retargeted.
 *
 * rcpy_fchunk deliberately uses inbuf both as its 2 KB copy buffer and as
 * the ack-reception buffer of its progress blocks: safe, because a heartbeat
 * only ever fires AFTER the chunk in inbuf has been written out.
 */

#include <arch/zxn/esxdos.h>
#include <string.h>
#include "syncsys.h"   /* rcpy_state_t (shared with the main-bank caller) */

/* main-bank helpers/state from nextsync.c */
extern char send_block_rt(unsigned char *scratch, unsigned short payloadlen, unsigned char *inbuf);
extern unsigned char g_packetno;
extern void live_tick(void);   /* main-bank anim shim: one eye-candy step */
extern unsigned char walk_isdots(char *n);   /* "."/".." test (rfsize.c)  */

#define RCPY_CHUNK 2048   /* copy buffer = the whole of inbuf */

/* Push one 'D' progress block: with a name (per file, so the PC can show
 * what is being copied) or empty (keepalive - also used to bridge the
 * long directory skip-loops, which otherwise leave the link silent long
 * enough for the PC to give up: seen on real hardware).
 * Also the anim's liveness hook: every progress/keepalive moment steps the
 * sprites (via the main-bank shim, so the ANIM_ENABLED knob still works;
 * the tick self-limits to one step per frame and is port-I/O only), which
 * keeps the flock moving through rfsize sweeps and directory skip-loops.
 * Returns non-zero when the link is gone (send_block_rt gave up). */
unsigned char rcpy_hb(char *name, unsigned char *inbuf, unsigned char *scratch)
{
    unsigned short nl = 0;
    live_tick();
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

/* --- one-file copy, chunk-stepped (v5.3) -----------------------------------
 * The copy no longer runs to completion inside one head-page call:
 * rcpy_fbegin ARMS it (per-file progress block, open source, stat, create
 * destination with its path), then the MAIN BANK pumps rcpy_fchunk - one
 * 2 KB chunk per call - printing the name first and spinning between
 * chunks. The in-flight state is the one static this file holds: bss is
 * never section-retargeted (the rfsize_ent precedent), so it lands in
 * main-bank bss where absolute addressing keeps this head-page code small
 * - 11 bytes of bss bought ~90 bytes of head page.                        */
static rcpy_fst_t fst;

/* Arm a copy src -> dst. Returns 0 = armed (now pump rcpy_fchunk),
 * 1 = this file failed (unreadable src / uncreatable dst), 2 = LINK died. */
unsigned char rcpy_fbegin(char *src, char *dst, unsigned char *inbuf, unsigned char *scratch)
{
    struct esx_stat es;

    if (rcpy_hb(dst, inbuf, scratch))      /* per-file progress + keepalive   */
        return 2;
    fst.sfh = esx_f_open(src, 1);          /* read existing                   */
    if (fst.sfh == 0xFF)
        return 1;
    fst.want = 0xFFFFFFFFUL;               /* expected length, to catch a     */
    if (esx_f_fstat(fst.sfh, &es) == 0)    /* read error posing as EOF        */
        fst.want = es.size;
    fst.dfh = rcpy_creat(dst);
    if (fst.dfh == 0xFF)
    {
        esx_f_close(fst.sfh);
        return 1;
    }
    fst.hb = 0;
    return 0;
}

/* Copy the next chunk of the armed file. Returns 0 = chunk copied, call
 * again; otherwise TERMINAL (both handles closed): 1 = file copied,
 * 2 = this file failed (short write = destination full, or a read that
 * stopped short of the file's stat size - a silent half-copy must never
 * pass as success), 3 = the LINK died (abort the whole command). */
unsigned char rcpy_fchunk(unsigned char *inbuf, unsigned char *scratch)
{
    unsigned short n;
    unsigned char r;

    n = esx_f_read(fst.sfh, inbuf, RCPY_CHUNK);
    if (n == 0)
        r = 1;                             /* EOF                             */
    else if (esx_f_write(fst.dfh, inbuf, n) != n)
        r = 2;                             /* short write: dest full          */
    else
    {
        /* Keepalive every 32 chunks (64 KB) so a multi-MB file can never
         * leave the PC staring at a silent socket. */
        if ((++fst.hb & 31) == 0 && rcpy_hb(0, inbuf, scratch))
            r = 3;
        else
            return 0;
    }
    esx_f_close(fst.sfh);
    if (r == 1 && fst.want != 0xFFFFFFFFUL)
    {
        /* The destination's own size must equal the source's stat size: a
         * read error posing as EOF (or a write that lied) shows up here -
         * and re-statting is smaller than carrying a 32-bit byte counter. */
        struct esx_stat es;
        if (esx_f_fstat(fst.dfh, &es) != 0 || es.size != fst.want)
            r = 2;                         /* truncated by a mid-file error   */
    }
    esx_f_close(fst.dfh);
    return r;
}

/* ---------------------------------------------------------------------------
 * The directory walk - ITERATIVE, one reportable item per call.
 *
 * Why not recursive like send_dir: the main-bank C stack is nearly full and
 * every recursion level cost ~45 bytes there (the v5.2 walk overflowed the
 * bank outright). An explicit level stack lives in the scratch tail instead
 * (+1024; the paths sit at +512/+768), so the walk runs on ONE stack frame
 * at any tree depth - and because rcpy_step RETURNS between items (and,
 * since v5.3, before and between the CHUNKS of each file, via rcpy_fbegin/
 * rcpy_fchunk), the main-bank caller can print and spin with no head-page
 * frame anywhere on the stack (head-page code must never print, and hardware
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
 *   0 = walk complete             2 = directory created (dst = its path)
 *   3 = item failed (src = its path: strange name, unreadable dir,
 *       uncreatable file, tree too deep, ...)
 *   4 = link dead - abort
 *   5 = a file copy was ARMED (dst = its path): the caller announces it,
 *       then pumps rcpy_fchunk to completion BEFORE the next rcpy_step
 *       call (rcpy_fchunk shares inbuf/scratch with the walk).
 * The child path that 2/3/5 leave in src/dst stays valid until the NEXT
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
        if (walk_isdots((char *)name))
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
        r = rcpy_fbegin(src, dst, inbuf, scratch);
        if (r == 2)
            return 4;
        return r ? 3 : 5;                  /* 5: caller pumps rcpy_fchunk     */
    }
}
