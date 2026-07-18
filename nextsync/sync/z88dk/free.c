/*
 * free.c - head-page -listen helpers: the "free" command ('Z', free space on
 * a partition, behind the PC's psize/pfull commands) plus the print-free
 * protocol repliers listen_ls ('L') and listen_drives ('W'), moved here from
 * nextsync.c in v5.2 to reclaim main-bank stack headroom. Part of NextSync
 * 5.2.
 *
 * HEAD PAGE RESIDENT: build_dotn.ps1 compiles this file to asm and retargets
 * its code/rodata sections into the primary 8 KB dot page (code_dot /
 * rodata_dot), exactly like anim.c, so none of it costs main-bank bytes -
 * the main bank's free space doubles as the C stack and every byte there is
 * stack headroom (see the __BSS_END_tail note in build_dotn.ps1).
 *
 * Head-page rules honoured here (learned the hard way during v5.1):
 *   - NEVER print (print/conprint/fputc): the ROM-print driver pages the ROM
 *     over this very code for the call and the Next dies instantly. All
 *     tracing (vprint "free done") is done by the main-bank caller.
 *   - esxDOS divMMC calls (esx_f_xxx / esx_m_xxx) ARE safe from here.
 *   - No file-scope statics (bss/data must stay in the main bank).
 */

#include <arch/zxn/esxdos.h>
#include "syncsys.h"   /* sync_* wrappers + the fopen/opendir/... macros */

/* Main-bank helpers from nextsync.c - calling INTO the main bank from head-
 * page code is fine (both regions are mapped together while the dot runs). */
extern char send_block_rt(unsigned char *scratch, unsigned short payloadlen, unsigned char *inbuf);
extern void listen_status(char ok, unsigned char *inbuf, unsigned char *scratch);
extern unsigned char g_packetno;

/* --- moved from nextsync.c (v5.2): print-free -listen repliers ---------- */

/* ls: enumerate 'path' and push the listing to the server as 'D' blocks of
 * packed [flags][size][namelen][name] entries, ended by an 'E' block. Only
 * readdir + network I/O happen while the handle is open (no esxDOS file
 * I/O), so the directory cursor is safe. */
void listen_ls(char *path, unsigned char *inbuf, unsigned char *scratch)
{
    unsigned char dh, i, nl;
    sync_dirent_t ent;
    unsigned short used = 0;   /* entry bytes packed after the opcode */

    g_packetno = 0;

    dh = opendir((unsigned char *)path);
    if (dh == 0) { listen_status(0, inbuf, scratch); return; }  /* 'F' */

    while (sync_readdir_entry(dh, &ent))
    {
        nl = 0;
        while (ent.name[nl]) nl++;
        if (used + 6 + nl > 1000)          /* flush the current block first */
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

    if (used)                              /* flush remaining entries */
    {
        scratch[2] = 'D';
        send_block_rt(scratch, (unsigned short)(1 + used), inbuf);
    }
    scratch[2] = 'E';
    send_block_rt(scratch, 1, inbuf);
}

/* (listen_drives stayed in nextsync.c - it is tiny and the head page is the
 * scarcer resource now.) */

/* Free 512-byte blocks on a drive. letter = 0 (or the current drive's own
 * letter) queries the default drive directly; any other letter temporarily
 * switches the default drive around the query and always switches back.
 * Returns 0xFFFFFFFF on failure.
 *
 * F_GETFREE ($b1, esx_f_getfree - a divMMC hook, so dotN-safe) only accepts
 * '*' (default) or '$' (system) as its drive specifier, NOT a letter; passing
 * a letter is undocumented and could silently answer for the wrong drive. So
 * other partitions are reached the documented way: M_GETSETDRV ($89, the same
 * proven-safe hook sync_getdrive uses) to re-point the default drive, then
 * F_GETFREE('*'), then M_GETSETDRV back.
 *
 * Only 'C'..'P' are ever passed on: 'A'/'B' are the +3DOS FLOPPY drives and
 * even touching them from a dotN is fatal (the $8000-$BFFF remap - see
 * sync_getdrive in syncsys.c). Note an UNMOUNTED letter in that range is
 * still the caller's responsibility: like every other -listen command, the
 * PC only offers drives reported by getdrives or declared by the user. */
unsigned long sync_getfree(unsigned char letter)
{
   unsigned long blocks;
   unsigned char cur, target;

   if (letter >= 'a' && letter <= 'p')
      letter -= 'a' - 'A';

   if (letter == 0)
      return esx_f_getfree();

   if (letter < 'C' || letter > 'P')
      return 0xFFFFFFFFUL;

   cur = esx_m_getdrv();               /* encoded 8*(letter-'A') + partition  */
   if (cur == 0xFF)
      return 0xFFFFFFFFUL;             /* can't read the drive to restore it  */
   if ((cur >> 3) == (unsigned char)(letter - 'A'))
      return esx_f_getfree();          /* already the default drive           */

   /* esx_m_setdrv returns the new default drive; 0xFF = error (encoded drive
    * bytes top out at 0x7F, so 0xFF is unambiguous). */
   target = (unsigned char)((letter - 'A') << 3);
   if (esx_m_setdrv(target) == 0xFF)
      return 0xFFFFFFFFUL;             /* drive not there - clean refusal     */

   blocks = esx_f_getfree();
   esx_m_setdrv(cur);                  /* ALWAYS restore the original drive   */
   return blocks;
}

/* psize/pfull: free space on a partition, as one status block:
 * 'O' + 4 bytes little-endian free 512-byte block count, or 'F' when the
 * drive can't be measured. arg = optional drive letter (empty string = the
 * dot's current drive). */
void listen_free(char *arg, unsigned char *inbuf, unsigned char *scratch)
{
    unsigned long blocks = sync_getfree((unsigned char)arg[0]);

    if (blocks == 0xFFFFFFFFUL)
    {
        listen_status(0, inbuf, scratch);   /* 'F' - bad drive / query failed */
        return;
    }
    g_packetno = 0;
    scratch[2] = 'O';
    scratch[3] = (unsigned char)blocks;
    scratch[4] = (unsigned char)(blocks >> 8);
    scratch[5] = (unsigned char)(blocks >> 16);
    scratch[6] = (unsigned char)(blocks >> 24);
    send_block_rt(scratch, 5, inbuf);
}
