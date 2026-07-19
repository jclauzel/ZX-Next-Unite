/*
 * rfsize.c - the -listen "rfsize" command ('S'): measure the total size of a
 * file, or of a whole directory tree (every file in every sub-directory), on
 * any partition - the natural companion of rcpy ("will this copy fit?").
 * Part of NextSync 5.2.
 *
 * Reply: 'D' progress blocks (one per directory entered, carrying its path,
 * plus empty keepalives every 256 enumerated entries so a huge folder never
 * leaves the PC staring at a silent socket), then one terminal block:
 *   'O' + [4B files LE][4B dirs LE][4B size_lo LE][2B size_hi LE]
 * (total bytes = size_hi * 2^32 + size_lo: a folder can easily exceed the
 * 32 bits a z80 unsigned long holds, so a carry counter rides along), or
 * 'F' on any failure (missing path, unreadable dir, tree too deep, link
 * lost) - the standard failure pattern.
 *
 * HEAD PAGE RESIDENT like free.c/rcpy.c (see build_dotn.ps1): no printing,
 * esxDOS + main-bank calls only, no NEW static data - the one growing path
 * buffer reuses the scratch tail (scratch+512, untouched by block framing),
 * and directory entries go through syncsys.c's sync_readdir_entry, whose
 * static LFN dirent already exists in the main bank. The single shared
 * dirent is safe across the recursion because each entry is fully consumed
 * (size summed / name copied into the path) before any further call.
 *
 * Enumeration safety, per the send_dir/listen_ls school of hard knocks:
 * counting needs NO file I/O at all (sizes come from the dirents), so each
 * directory is swept in ONE O(n) pass with the handle held open - only
 * readdir + network I/O happen then, the exact pattern listen_ls proved on
 * hardware. Descending into sub-directories uses the re-open/skip/close
 * dance (no handle is ever open across a level change) - and the walk is
 * ITERATIVE like rcpy_step's (explicit level stack in the scratch tail at
 * +1024, shared with rcpy's walk state - never concurrent), so it runs on
 * ONE stack frame at any tree depth: the main-bank C stack is too tight for
 * per-level recursion frames.
 */

#include <arch/zxn/esxdos.h>
#include <string.h>
#include "syncsys.h"     /* sync_opendir / sync_close / sync_readdir_entry */

/* main-bank helpers/state from nextsync.c */
extern char send_block_rt(unsigned char *scratch, unsigned short payloadlen, unsigned char *inbuf);
extern unsigned char g_packetno;
/* the 'D' progress/keepalive pusher, shared with rcpy.c (identical needs) */
extern unsigned char rcpy_hb(char *name, unsigned char *inbuf, unsigned char *scratch);

/* Deepest nesting followed (= the level-stack size; the stack itself lives
 * in the scratch tail, not on the C stack). Beyond it: clean 'F'. */
#define RFSIZE_MAX_DEPTH 12

/* One shared dirent (safe: each entry is fully consumed before the next
 * call), in main-bank bss - head-page files must hold no data, and bss is
 * never section-retargeted, so this lands where it belongs automatically. */
static sync_dirent_t rfsize_ent;

typedef struct {
    unsigned long files;
    unsigned long dirs;
    unsigned long size_lo;
    unsigned short size_hi;   /* counts 2^32 wraps of size_lo */
    unsigned char bad;        /* any failure / dead link -> terminal 'F' */
} rfsize_tot_t;

/* "." / ".." test, shared with rcpy_step (rcpy.c) - both walks skip the dot
 * entries, and the head page is too full to carry the test twice. */
unsigned char walk_isdots(char *n)
{
    return n[0] == '.' && (n[1] == 0 || (n[1] == '.' && n[2] == 0));
}

/* Iterative deep scan of the directory at path (length plen0). Per level:
 * phase 1 (cur == 0xFFFF) is the O(n) counting sweep with the handle open;
 * phase 2 steps entry-by-entry with the re-open/skip/close dance looking
 * for sub-directories to enter. Keepalives ride rcpy_hb throughout. */
static void rfsize_walk(char *path, unsigned short plen0, rfsize_tot_t *t,
                        unsigned char *inbuf, unsigned char *scratch)
{
    unsigned short *cur  = (unsigned short *)(scratch + 1024);
    unsigned short *plen = (unsigned short *)(scratch + 1024 + 2 * (RFSIZE_MAX_DEPTH + 1));
    unsigned char sp = 0;
    unsigned char dh, got;
    unsigned short ne, j, nl, e;

    cur[0] = 0xFFFF;                       /* phase 1 pending for the root */
    plen[0] = plen0;

    for (;;)
    {
        path[plen[sp]] = 0;                /* re-truncate to this level */

        if (cur[sp] == 0xFFFF)             /* --- phase 1: counting sweep -- */
        {
            if (rcpy_hb(path, inbuf, scratch))   /* per-directory progress */
            {
                t->bad = 1;
                return;
            }
            dh = sync_opendir(path);
            if (dh == 0)
            {
                t->bad = 1;
                return;
            }
            ne = 0;
            while (sync_readdir_entry(dh, &rfsize_ent))
            {
                ne++;
                if ((ne & 0xFF) == 0 && rcpy_hb(0, inbuf, scratch))
                {
                    t->bad = 1;
                    break;
                }
                if (rfsize_ent.is_dir)
                {
                    if (!walk_isdots(rfsize_ent.name))
                        t->dirs++;
                }
                else
                {
                    t->files++;
                    t->size_lo += rfsize_ent.size;
                    if (t->size_lo < rfsize_ent.size)
                        t->size_hi++;      /* 32-bit wrap -> carry */
                }
            }
            sync_close(dh);
            if (t->bad)
                return;
            cur[sp] = 0;
            continue;
        }

        /* --- phase 2: land on entry #cur, descend if it's a sub-dir ----- */
        if (cur[sp] >= 4000)               /* runaway dir: treat as its end */
            got = 0;
        else
        {
            dh = sync_opendir(path);
            if (dh == 0)
            {
                t->bad = 1;
                return;
            }
            got = 0;
            for (j = 0; j <= cur[sp]; j++)
            {
                got = sync_readdir_entry(dh, &rfsize_ent);
                if (!got)
                    break;
                if ((j & 0xFF) == 0xFF && rcpy_hb(0, inbuf, scratch))
                {
                    t->bad = 1;
                    break;
                }
            }
            sync_close(dh);
            if (t->bad)
                return;
        }
        if (!got)                          /* past this level's last entry  */
        {
            if (sp == 0)
                return;                    /* whole tree done               */
            sp--;
            continue;
        }
        cur[sp]++;
        if (!rfsize_ent.is_dir || walk_isdots(rfsize_ent.name))
            continue;

        nl = 0;
        while (rfsize_ent.name[nl]) nl++;
        e = plen[sp];
        if (path[e - 1] != '/')            /* drive roots already end in '/' */
            path[e++] = '/';
        if (e + nl >= 254 || sp >= RFSIZE_MAX_DEPTH)
        {
            t->bad = 1;                    /* too long / too deep: clean 'F' */
            return;
        }
        for (j = 0; j < nl; j++)
            path[e + j] = rfsize_ent.name[j];
        path[e + nl] = 0;

        sp++;                              /* enter the sub-directory       */
        cur[sp] = 0xFFFF;                  /* its phase 1 runs next         */
        plen[sp] = (unsigned short)(e + nl);
    }
}

/* 32-bit LE store - main-bank helper (nextsync.c); the head page is full. */
extern void put32le(unsigned char *p, unsigned long v);

/* rfsize entry point: arg (in fn[]) is the file or directory to measure.
 * Sends the terminal status itself; like rcpy the terminal block must
 * CONTINUE the 'D' sequence, so listen_status (which resets g_packetno) is
 * never used here. */
void listen_rfsize(char *arg, unsigned char *inbuf, unsigned char *scratch)
{
    char *path = (char *)scratch + 512;
    rfsize_tot_t t;
    struct esx_stat es;
    unsigned short pl = 0;
    unsigned char fh;

    g_packetno = 0;
    t.files = 0;
    t.dirs = 0;
    t.size_lo = 0;
    t.size_hi = 0;
    t.bad = 0;

    while (arg[pl] && pl < 255) { path[pl] = arg[pl]; pl++; }
    path[pl] = 0;
    /* Trailing slashes off (keep a drive root's, "c:/" - stripping it would
     * leave "c:", which esxDOS reads as c:'s CURRENT dir, not its root). */
    while (pl > 1 && path[pl - 1] == '/' && path[pl - 2] != ':') path[--pl] = 0;

    if (pl == 0)
        t.bad = 1;
    else
    {
        fh = esx_f_open(path, 1);          /* a plain file? its stat size */
        if (fh != 0xFF)
        {
            if (esx_f_fstat(fh, &es) == 0)
            {
                t.files = 1;
                t.size_lo = es.size;
            }
            else
                t.bad = 1;
            esx_f_close(fh);
        }
        else
            rfsize_walk(path, pl, &t, inbuf, scratch);
    }

    if (t.bad)
    {
        scratch[2] = 'F';
        send_block_rt(scratch, 1, inbuf);
    }
    else
    {
        scratch[2] = 'O';
        put32le(scratch + 3, t.files);
        put32le(scratch + 7, t.dirs);
        put32le(scratch + 11, t.size_lo);
        scratch[15] = (unsigned char)t.size_hi;
        scratch[16] = (unsigned char)(t.size_hi >> 8);
        send_block_rt(scratch, 15, inbuf);
    }
}
