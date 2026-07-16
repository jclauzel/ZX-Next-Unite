/*
 * syncsys.c - z88dk implementations behind syncsys.h.
 *
 * Compiled by z88dk (zsdcc, -clib=sdcc_iy). Calls the esxDOS newlib wrappers
 * directly, so it must NOT include syncsys.h (whose macros rename fopen/fread/
 * ... onto these very functions).
 */

#include <stdio.h>
#include <string.h>
#include <arch/zxn.h>
#include <arch/zxn/esxdos.h>

/* Pull in the shared types (sync_dirent_t) and prototypes, but NOT the
 * fopen/fread/... macros - we implement those wrappers here by their real
 * names and call the C library / esx_f_* directly. */
#define SYNCSYS_NO_MACROS
#include "syncsys.h"

/* --- esxDOS file / directory operations -------------------------------- */

/* esx_f_open returns 0xFF (and sets errno) on error; the NextSync C code was
 * written against a hand-asm fopen that returned 0 on error, so remap it. */
unsigned char sync_open(const char *fn, unsigned char mode)
{
   unsigned char h = esx_f_open(fn, mode);
   return (h == 0xFF) ? 0 : h;
}

unsigned char sync_opendir(const char *path)
{
   unsigned char h = esx_f_opendir(path);
   return (h == 0xFF) ? 0 : h;
}

void sync_close(unsigned char handle)
{
   esx_f_close(handle);
}

unsigned short sync_read(unsigned char handle, void *buf, unsigned short bytes)
{
   return esx_f_read(handle, buf, bytes);
}

void sync_write(unsigned char handle, void *buf, unsigned short bytes)
{
   esx_f_write(handle, buf, bytes);
}

/* esx_f_readdir writes a struct esx_dirent: byte 0 = FAT attribute, byte 1..
 * = ASCIIZ name. That is exactly the layout the callers parse (entry[0] & 0x10
 * for "is directory", name = entry + 1). Returns entries read (0 = end). */
unsigned char sync_readdir(unsigned char handle, void *buf)
{
   return esx_f_readdir(handle, buf);
}

/* Original createfilewithpath() walked the path making each directory in turn;
 * esx_f_mkdir makes a single directory (errors on an existing one are ignored
 * by the caller, which then retries the file create). */
unsigned char sync_mkdir(const char *path)
{
   return esx_f_mkdir(path);
}

/* rmdir / rm for the -listen commands. esxDOS returns 0xFF (and sets errno) on
 * error; callers treat "!= 0xFF" as success. */
unsigned char sync_rmdir(const char *path)
{
   return esx_f_rmdir(path);
}

unsigned char sync_unlink(const char *path)
{
   return esx_f_unlink(path);
}

/* rename / move a file or directory (within the same drive). esxDOS returns
 * 0xFF (and sets errno) on error; callers treat "!= 0xFF" as success, exactly
 * like sync_rmdir / sync_unlink above. */
unsigned char sync_rename(const char *oldpath, const char *newpath)
{
   return esx_f_rename(oldpath, newpath);
}

/* Read one directory entry, exposing its FAT attribute (directory flag) and
 * size. esx_f_readdir fills a struct esx_dirent (attr, then ASCIIZ name, then
 * date/time and size); esx_slice_dirent() locates the size that follows the
 * variable-length name. Returns 1 on success, 0 at end of directory. */
unsigned char sync_readdir_entry(unsigned char handle, sync_dirent_t *out)
{
   struct esx_dirent ent;
   unsigned char i;

   if (esx_f_readdir(handle, &ent) == 0)
      return 0;                          /* end of directory */

   out->is_dir = (ent.attr & 0x10) ? 1 : 0;

   for (i = 0; ent.name[i] && i < sizeof(out->name) - 1; i++)
      out->name[i] = ent.name[i];
   out->name[i] = 0;

   if (out->is_dir)
      out->size = 0;
   else
      out->size = ((struct esx_dirent_slice *)esx_slice_dirent(&ent))->size;

   return 1;
}

/* --- Next hardware registers ------------------------------------------- */

unsigned char readnextreg(unsigned char reg)
{
   return ZXN_READ_REG(reg);
}

void writenextreg(unsigned char reg, unsigned char val)
{
   ZXN_WRITE_REG(reg, val);
}

/* --- console output ---------------------------------------------------- */

/* stdout is wired through z88dk's ROM-print driver (RST 16), which also pages
 * the ROM/system into mmu6 for the call - so we must go through it rather than
 * hitting RST 16 directly. That driver, however, *ignores* '\r' (13) and only
 * emits a newline for '\n' (10). The NextSync code uses '\r' as its newline
 * everywhere, so translate it here. (Protocol/UART bytes never pass through
 * conprint, so this only ever affects on-screen text.) */
void conprint(char *txt)
{
   char c;
   while ((c = *txt++) != 0)
      fputc((c == '\r') ? '\n' : c, stdout);
}

/* Clear the whole ULA screen to the classic NextSync look - black paper, green
 * ink - and home the print cursor, matching the original .sync startup (which
 * did the same two memsets + a home). Without this only the cells the ROM prints
 * to turn black; the rest of the screen keeps its previous (usually white)
 * paper. The display file is at $4000 (6144 pixel bytes) / $5800 (768 attribute
 * bytes) under NextZXOS, and stays mapped there while the dot runs. */
void con_cls(void)
{
   /* CRITICAL ORDERING: z88dk's terminal stdout driver runs a one-time init the
    * first time it emits output, and that init clears the whole attribute file
    * to its default (white) paper. It does NOT touch ATTR_T/ATTR_P or CHARS,
    * which is why the printed cells come out green-on-black (ATTR_T) with the
    * custom font while the rest of the screen stays white. So we must let that
    * init happen FIRST (send one newline to trigger it) and paint the screen
    * black AFTER, otherwise the init wipes our fill. */
   fputc('\n', stdout);                  /* force the driver's init (clears attrs to white) */
   memset((void *)0x4000, 0x00, 6144);   /* pixels -> all black          */
   memset((void *)0x5800, 0x04, 768);    /* attrs  -> paper 0, ink 4      */
   /* Home the ROM print position to the top-left of the upper screen so the log
    * starts at the top of the cleared page (the original .sync did SETXY(0,0)).
    * We set the sysvars directly rather than sending the AT control code, which
    * the terminal driver could intercept: S_POSN counts the column/line DOWN
    * from 33/24, and DF_CC is the display-file address of that cell. */
   *((unsigned char  *)23688) = 33;      /* S_POSN column (33 = left edge) */
   *((unsigned char  *)23689) = 24;      /* S_POSN line   (24 = top)       */
   *((unsigned short *)23684) = 0x4000;  /* DF_CC = address of top-left cell */
}

/* --- rolling packet checksum ------------------------------------------- */

/* Faithful C port of the hand-asm checksum: e = xor of all bytes, d = running
 * sum of the intermediate xor values; compare against the 16-bit checksum
 * stored little-endian right after the data. Returns 0 when they match. */
char checksum(char *dp, unsigned short len)
{
   unsigned char e = 0, d = 0;
   unsigned short i, computed, stored;

   for (i = 0; i < len; i++)
   {
      e ^= (unsigned char)dp[i];
      d += e;
   }

   computed = ((unsigned short)d << 8) | e;
   stored = (unsigned char)dp[len] | ((unsigned short)(unsigned char)dp[len + 1] << 8);

   return (char)(computed != stored);
}
