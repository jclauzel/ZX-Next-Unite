/*
 * syncsys.h - z88dk (dotN) compatibility shim for the NextSync dot command.
 *
 * The original .sync dot is built with SDCC + hand-written esxdos.s/uart.s/std.s
 * and is capped at 8 KB because a classic dot command loads at $2000 and must
 * fit $2000-$3FFF. This shim lets the *same* nextsync.c / gfx.c compile under
 * z88dk's toolchain so the command can be built as a "dotN" instead, which
 * appmake splits across banked pages and is no longer bound by the 8 KB wall.
 *
 * It re-expresses the interface the C code expects (fopen/fread/... nextreg,
 * conprint, receive/checksum, mulby10) in terms of z88dk's esxDOS library and
 * a tiny bit of ported asm. Semantics are preserved exactly:
 *   - fopen/opendir return 0 on failure (esxDOS 0xFF error mapped to 0);
 *   - readdir fills a buffer whose byte 0 is the FAT attribute and byte 1..
 *     the ASCIIZ name (this matches struct esx_dirent, which the callers parse);
 *   - fread returns the number of bytes read.
 *
 * Only nextsync.c and gfx.c include this. The implementations in syncsys.c
 * deliberately do NOT include this header (they call esx_f_* by their real
 * names) so the macros below never collide with the C library declarations.
 */
#ifndef SYNCSYS_H
#define SYNCSYS_H

#include <stddef.h>

/* --- esxDOS file / directory operations -------------------------------- */
/* 0 = failure, to match the original hand-written esxdos.s convention.     */
extern unsigned char  sync_open(const char *fn, unsigned char mode);
extern unsigned char  sync_opendir(const char *path);
extern void           sync_close(unsigned char handle);
extern unsigned short sync_read(unsigned char handle, void *buf, unsigned short bytes);
extern void           sync_write(unsigned char handle, void *buf, unsigned short bytes);
extern unsigned char  sync_readdir(unsigned char handle, void *buf);
extern unsigned char  sync_mkdir(const char *path);   /* create one directory */
extern unsigned char  sync_rmdir(const char *path);   /* remove a directory    */
extern unsigned char  sync_unlink(const char *path);  /* delete a file         */
extern unsigned char  sync_rename(const char *oldpath, const char *newpath); /* rename/move a file or dir */
extern unsigned char  sync_getdrive(void);             /* current drive letter 'A'..'P', 0 if unknown */

/* Free space on a drive for the -listen "psize"/"pfull" commands, counted in
 * 512-byte blocks. letter = 0 queries the current drive; 'C'..'P' (either
 * case) temporarily switches the default drive to measure another partition.
 * Returns 0xFFFFFFFF on any failure ('A'/'B' are rejected outright: they are
 * NextZXOS's +3DOS floppy drives, and merely touching them from a dotN remaps
 * $8000-$BFFF over our code+stack - see sync_getdrive's note).
 * Implemented in free.c, which is HEAD-PAGE resident (like anim.c). */
extern unsigned long  sync_getfree(unsigned char letter);

/* One directory entry with its size, for the -listen "ls" command. Enumerated
 * with sync_opendir()/sync_readdir_entry()/sync_close(). Long filenames: the
 * handle is opened with ESX_DIR_USE_LFN, and `name` points into a static
 * dirent buffer inside syncsys.c that the NEXT sync_readdir_entry() call
 * overwrites - consume it before reading the next entry. */
typedef struct {
   unsigned char is_dir;   /* 1 = directory, 0 = file                  */
   unsigned long size;     /* file size in bytes (0 for dirs)          */
   char         *name;     /* 0-terminated long name (max 255 chars)   */
} sync_dirent_t;

/* Read the next entry from an open directory handle into out.
 * Returns 1 on success, 0 at end of directory. */
extern unsigned char  sync_readdir_entry(unsigned char handle, sync_dirent_t *out);

/* These macros collide with the C library's fopen/fread/... and dirent's
 * readdir, so syncsys.c (which implements the wrappers and includes those
 * headers) defines SYNCSYS_NO_MACROS to pull in only the types/prototypes. */
#ifndef SYNCSYS_NO_MACROS
#define fopen(fn, mode)  sync_open((const char *)(fn), (unsigned char)(mode))
#define opendir(p)       sync_opendir((const char *)(p))
#define fclose(h)        sync_close((unsigned char)(h))
#define fread(h, b, n)   sync_read((unsigned char)(h), (void *)(b), (unsigned short)(n))
#define fwrite(h, b, n)  sync_write((unsigned char)(h), (void *)(b), (unsigned short)(n))
#define readdir(h, b)    sync_readdir((unsigned char)(h), (void *)(b))
#endif

/* --- rcpy walk state (shared between nextsync.c and rcpy.c) ------------ */
/* The iterative rcpy walk keeps its explicit level stack in the scratch
 * tail at +1024 (the paths sit at +512/+768). The struct is defined here so
 * the main-bank entry point (listen_rcpy, nextsync.c) can ARM it directly -
 * a separate head-page init function cost 57 bytes of the nearly-full head
 * page for three assignments. rcpy_step (rcpy.c) is its only other user. */
#define RCPY_MAX_DEPTH 12
typedef struct {                     /* 2 + 3*13*2 = 80 bytes               */
   unsigned char sp;                 /* current level                       */
   unsigned char ended;              /* walk finished / root failed         */
   unsigned short cur[RCPY_MAX_DEPTH + 1];   /* next entry # per level      */
   unsigned short sl[RCPY_MAX_DEPTH + 1];    /* src path length per level   */
   unsigned short dl[RCPY_MAX_DEPTH + 1];    /* dst path length per level   */
} rcpy_state_t;

/* One in-flight file copy, armed by rcpy_fbegin and drained one 2 KB chunk
 * per rcpy_fchunk call (a static inside rcpy.c: 7 bytes of main-bank bss,
 * bought so the head-page code can use absolute addressing). The copy is
 * chunk-stepped so the MAIN BANK can print the file's name BEFORE any byte
 * moves and twirl the -v spinner between chunks: no head-page frame is
 * ever on the stack when anything is drawn (rcpy.c/nextsync.c). */
typedef struct {
   unsigned char sfh;                /* source file handle                  */
   unsigned char dfh;                /* destination file handle             */
   unsigned char hb;                 /* chunks since the last keepalive     */
   unsigned long want;               /* stat size (0xFFFFFFFF = unknown)    */
} rcpy_fst_t;

/* --- Next hardware registers ------------------------------------------- */
extern unsigned char readnextreg(unsigned char reg);
extern void          writenextreg(unsigned char reg, unsigned char val);

/* --- console output (ROM print via z88dk's RST 16 stdout driver) ------- */
extern void conprint(char *txt);
/* Clear the screen to black paper / green ink and home the cursor. */
extern void con_cls(void);

/* --- timing-critical UART receive loop (uart.asm) ---------------------- */
extern unsigned short receive(char *b) __z88dk_fastcall;

/* --- rolling packet checksum (syncsys.c) ------------------------------- */
extern char checksum(char *dp, unsigned short len);

/* x*10 used by the decimal command-line parser; the z80n MUL the original
 * hand-asm used is not worth a call here - the compiler emits it inline. */
#define mulby10(x)  ((unsigned short)((unsigned short)(x) * 10u))

#endif /* SYNCSYS_H */
