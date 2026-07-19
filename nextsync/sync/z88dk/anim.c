// anim.c - optional "-anim" / "-a" eye-candy for the .sync5 dotN.
//
// Hardware sprites (Pink Floyd's gulls + Clive's saucers) drift across the
// screen while syncing. This is DELIBERATELY register/port-only: the Next
// sprite engine has its own pattern + attribute RAM reached solely through the
// I/O ports (0x303B / 0x005B / 0x0057), completely independent of the CPU
// memory map - so nothing here can disturb the dot's own memory or corrupt the
// timing-critical UART transfer. It only ever runs when -anim/-a is passed, and
// nextsync.c only ticks it at *safe points* - between commands, between the
// acked packets of a transfer, between the 2 KB chunks of an rcpy - never
// mid-packet (the link is idle at every one of those moments, so a ~100 us
// sprite update cannot lose UART bytes). anim_tick self-limits to one step
// per video frame via the ROM's 50 Hz FRAMES counter, so the busiest loop
// pays a ~microsecond early-out and the flock still moves at wall-clock
// speed. (A real IM2 interrupt was considered and rejected: the vector table
// + ISR would have to live in memory that stays mapped at every instant an
// interrupt can fire, and both dot homes fail that - the head page is paged
// away by every esxDOS call and by the print driver, and the main bank has
// only a few hundred bytes of stack headroom to donate.)
//
// Layer 2 colour-scroll was deliberately left out of this first cut: writing
// Layer 2 pixels needs careful MMU bank juggling (the dot keeps live data in
// low RAM) and wants hardware testing, whereas sprites carry none of that risk.
//
// The art stays simple (8bpp 16x16, default sprite palette) and the colours
// easy to tweak once seen on real hardware.
//
// BYTE BUDGET: this file's code and const data are placed in the primary 8 KB
// dot page (~5 KB of which sits unused after the crt+clib) instead of the
// main bank, whose free space doubles as the C stack (only ~0.5 KB left
// between __BSS_END and REGISTER_SP). zsdcc has no per-file section control
// (#pragma codeseg and --codeseg are silently ignored), so build_dotn.ps1
// compiles this file to asm, retargets its sections at code_dot/rodata_dot —
// the dotn memory model's head-page sections reserved for user dot content,
// placed after the crt+clib chains — and links the patched anim_head.asm
// (see zproject.lst). Only the few bytes of sprite state below stay in
// main-bank bss. Everything here is callable from the main bank - both pages
// are always mapped while the dot runs.

#include <arch/zxn.h>

#define N_SPR   6           // 3 gulls (patterns 0-2, 4-5) + 3 saucers (pattern 3)
#define TRANSP  0xE3        // default sprite transparency index (nextreg 0x4B)
#define PINK    0xF3        // 3-3-2 default palette: R7 G4 B3  (Pink Floyd's bird)
#define GREY    0xB6        // R5 G5 B2  (saucer body)
#define CYAN    0x1F        // R0 G7 B3  (saucer dome)

// 16x16 1bpp masks, authored so the shape is visible right here in the source
// (bit15 = leftmost pixel). Expanded to 8bpp at upload time.
//
// The gull has THREE wing poses - up, level, down - uploaded to pattern slots
// 0/1/2. anim_tick() cycles each bird through up->level->down->level so the
// wings flap; a per-bird phase keeps them out of lockstep. The saucer sits in
// slot 3. Slots 4 (wings banked) and 5 (edge-on sliver) are the turn tween:
// birds fly either way and occasionally flip heading with a smooth 4-pose
// turn - banked, edge-on, banked again on the new heading (the hardware X
// mirror flips slot 4), then wings level (see turn_pat in anim_tick).
static const unsigned short gull_up[16] = {    // wings raised (tips high)
    0b0000000000000000,
    0b0000000000000000,
    0b0110000000000110,
    0b0011000000001100,
    0b0001100000011000,
    0b0000110000110000,
    0b0000011001100000,
    0b0000001111000000,
    0b0000000110000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000
};
static const unsigned short gull_level[16] = { // wings spread flat
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b1100000000000011,
    0b0111111111111110,
    0b0000011111100000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000
};
static const unsigned short gull_down[16] = {  // wings drooped (tips low)
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000110000000,
    0b0000011001100000,
    0b0000110000110000,
    0b0001100000011000,
    0b0011000000001100,
    0b0110000000000110,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000
};
static const unsigned short gull_bank[16] = {  // mid-turn: wings banked, tail
    0b0000000000000000,                        // trailing left of the heading
    0b0000000000000000,                        // (X-mirrored for the other way)
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0011000000000000,
    0b0001100000000000,
    0b0000110011100000,
    0b0000011111000000,
    0b0000001110000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000
};
static const unsigned short gull_edge[16] = {  // mid-turn: edge-on sliver
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000110000000,
    0b0000000110000000,
    0b0000001111000000,
    0b0000000110000000,
    0b0000000110000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000
};
static const unsigned short ufo_mask[16] = {
    0b0000000000000000,
    0b0000000000000000,
    0b0000001111000000,
    0b0000011111100000,
    0b0001111111111000,
    0b0111111111111110,
    0b1111111111111111,
    0b0111111111111110,
    0b0001100110011000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000,
    0b0000000000000000
};

static unsigned short spx[N_SPR];   // 0..319 (9-bit sprite X)
static unsigned char  spy[N_SPR];   // 0..255
static unsigned char  spd[N_SPR];   // horizontal speed
static unsigned char  sdir[N_SPR];  // 0 = moving right, 1 = left
static unsigned char  sturn[3];     // birds only: >0 = turn tween countdown
static unsigned char  g_frame;
static unsigned char  last_frame;   // FRAMES value of the last real tick
static unsigned char  saved_r15;
static unsigned char  g_anim_on = 0;

// FRAMES (sysvar 23672): incremented at 50 Hz by the ROM's IM1 interrupt
// handler, which keeps running the whole time the dot works - a plain RAM
// read at $5C78 (bank 5, always mapped), safe from anywhere.
#define FRAMES_LO  (*((unsigned char *)23672))

// Expand a 16x16 mask into the current sprite pattern slot (256 bytes). Rows
// 0-3 use colour2 (dome / wingtip), the rest colour1 (body).
static void upload_pattern(unsigned char slot, const unsigned short *mask,
                           unsigned char color1, unsigned char color2)
{
    unsigned char y, x;
    IO_SPRITE_SLOT = slot;
    for (y = 0; y < 16; y++)
    {
        unsigned short row = mask[y];
        unsigned char c = (y < 4) ? color2 : color1;
        for (x = 0; x < 16; x++)
        {
            IO_SPRITE_PATTERN = (row & 0x8000) ? c : TRANSP;
            row = (unsigned short)(row << 1);
        }
    }
}

// Write the 4 attribute bytes for sprite i: X, Y, (X msb / palette / mirror),
// (visible + pattern). Selecting the slot first resets the attribute index.
// *mirror* (always 0 or 1, from sdir) sets the attribute-2 X-mirror bit,
// used for the leftward heading.
static void put_sprite(unsigned char i, unsigned short x, unsigned char y,
                       unsigned char pattern, unsigned char mirror)
{
    IO_SPRITE_SLOT = i;
    IO_SPRITE_ATTRIBUTE = (unsigned char)(x & 0xFF);      // attr0: X lsb
    IO_SPRITE_ATTRIBUTE = y;                              // attr1: Y
    IO_SPRITE_ATTRIBUTE = (unsigned char)(((x >> 8) & 1)  // attr2: X msb, pal 0
                          | (unsigned char)(mirror << 3));//        + X mirror
    IO_SPRITE_ATTRIBUTE = (unsigned char)(0x80 | pattern);// attr3: visible+pattern
}

// Pattern-slot uploads and per-sprite start states, table-driven: the six
// explicit upload_pattern calls + the init arithmetic cost ~90 more bytes of
// the (packed-full) head page than these rows. Values are identical to the
// old computed ones.
static const unsigned short *const pat_mask[6] = {
    gull_up, gull_level, gull_down,   // gull wing poses -> slots 0/1/2
    ufo_mask,                         // saucer -> slot 3 (cyan dome, grey body)
    gull_bank, gull_edge              // turn tween -> slots 4/5
};
static const unsigned char spr_init[N_SPR][4] = {
    // x/8   y  speed dir      (x stored /8 so it fits a byte; old formulas:
    {   0,  40,   1,   0 },   // x = i*48, y = 40+i*22, spd = 1+(i&3);
    {   6,  62,   2,   1 },   // dir: bird 1 and saucer 4 start leftward so
    {  12,  84,   3,   0 },   // the flock/fleet moves both ways from the
    {  18, 106,   4,   0 },   // start; everyone flips later too (anim_tick)
    {  24, 128,   1,   1 },   // - birds with the turn tween, saucers by
    {  30, 150,   2,   0 }    // darting back.)
};

void anim_begin(void)
{
    unsigned char i;

    saved_r15 = ZXN_READ_REG(REG_SPRITE_LAYER_SYSTEM);
    ZXN_WRITE_REG(REG_SPRITE_LAYER_SYSTEM,
                  (unsigned char)(saved_r15 | RSLS_SPRITES_VISIBLE
                                            | RSLS_SPRITES_OVER_BORDER));

    for (i = 0; i < 6; i++)
        upload_pattern(i, pat_mask[i],
                       (unsigned char)(i == 3 ? GREY : PINK),
                       (unsigned char)(i == 3 ? CYAN : PINK));

    g_frame = 0;
    sturn[0] = sturn[1] = sturn[2] = 0;
    for (i = 0; i < N_SPR; i++)
    {
        spx[i] = (unsigned short)((unsigned short)spr_init[i][0] << 3);
        spy[i] = spr_init[i][1];
        spd[i] = spr_init[i][2];
        sdir[i] = spr_init[i][3];
        put_sprite(i, spx[i], spy[i], (unsigned char)(i < 3 ? 0 : 3), sdir[i]);
    }
    g_anim_on = 1;
}

void anim_tick(void)
{
    // Wing flap cycle: up -> level -> down -> level -> (repeat). Ticks are
    // frame-locked (below), so the >>1 makes the wings flap at 25 Hz; the
    // per-bird phase offset stops them flapping in lockstep. Tune the >>1
    // and/or the phase if it looks too fast or slow on your Next.
    static const unsigned char flap_frame[4] = { 0, 1, 2, 1 };
    // Turn tween, indexed by the sturn countdown after decrement (7..0): two
    // ticks per pose - banked (old heading), edge-on, banked (new heading,
    // hardware-mirrored), wings level - then normal flight resumes. The
    // heading itself flips at the edge-on midpoint (countdown == 3).
    static const unsigned char turn_pat[8] = { 1, 1, 4, 4, 5, 5, 4, 4 };
    unsigned char i, phf, ph85, ph5;
    unsigned char fr = FRAMES_LO;
    // Rate limit: at most one animation step per video frame, however often
    // the safe points come round. Call sites can therefore sit in per-chunk /
    // per-packet loops for free - a repeat call inside the same frame is a
    // ~microsecond early-out, and the movement speed is wall-clock stable
    // whether ticks arrive at 60/s or 6000/s.
    if (!g_anim_on || fr == last_frame) return;
    last_frame = fr;

    g_frame++;
    // Per-sprite phase offsets as running sums ((g_frame >> 1) + i for the
    // flap, g_frame + i*85 and + i*5 for turns/bob): zsdcc's multiplies and
    // repeated index math cost more head-page bytes than three adds.
    phf = (unsigned char)(g_frame >> 1);
    ph85 = g_frame;
    ph5 = g_frame;
    for (i = 0; i < N_SPR; i++, phf++, ph85 += 85, ph5 += 5)
    {
        unsigned short x = spx[i];
        unsigned char ph, y, pat, mir;
        signed char bob;

        mir = 0;
        if (i < 3 && sturn[i])
        {
            // Mid-turn: the bird brakes (X holds) while the 4-pose flip plays.
            sturn[i]--;
            if (sturn[i] == 3)
                sdir[i] ^= 1;
            pat = turn_pat[sturn[i]];
            mir = sdir[i];
        }
        else
        {
            // Move, wrapping at the screen edges: x lives in [0,319], so a
            // leftward underflow shows up as x >= 320 too (16-bit wrap) and
            // +320 lands exactly on the old (x + 320 - spd) value.
            if (sdir[i])
            {
                x = (unsigned short)(x - spd[i]);
                if (x >= 320) x = (unsigned short)(x + 320);
            }
            else
            {
                x = (unsigned short)(x + spd[i]);
                if (x >= 320) x = (unsigned short)(x - 320);
            }
            spx[i] = x;
            if (i < 3)
            {
                // birds flap through slots 0/1/2, mirrored when flying left
                pat = flap_frame[phf & 3];
                mir = sdir[i];
            }
            else
                pat = 3;    // saucers keep their single pose
            // Occasionally change heading - staggered per sprite (the +i*85
            // offset) so nobody turns in lockstep. Birds bank through the
            // 4-pose turn tween; saucers just dart back the way they came
            // (they're symmetric - an instant reverse reads as UFO-like).
            if ((ph85 & 127) == 0)
            {
                if (i < 3)
                    sturn[i] = 8;
                else
                    sdir[i] ^= 1;
            }
        }
        // gentle vertical bob, triangle wave in [-8,+7] (no trig, no tables)
        ph = (unsigned char)(ph5 & 31);
        bob = (ph < 16) ? (signed char)(ph - 8) : (signed char)(23 - ph);
        y = (unsigned char)(spy[i] + bob);
        put_sprite(i, x, y, pat, mir);
    }
}

void anim_end(void)
{
    unsigned char i, j;
    if (!g_anim_on) return;

    // Hide our sprites (visible bit cleared) so nothing lingers, then restore
    // the original sprite/layers register.
    for (i = 0; i < N_SPR; i++)
    {
        IO_SPRITE_SLOT = i;
        for (j = 0; j < 4; j++)
            IO_SPRITE_ATTRIBUTE = 0;   // attr3 bit7 = 0 -> not visible
    }
    ZXN_WRITE_REG(REG_SPRITE_LAYER_SYSTEM, saved_r15);
    g_anim_on = 0;
}

// --- console spinner: the -v trace's busy cursor (v5.3) ---------------------
//
// While a long copy/transfer runs chunk by chunk, its trace line stays open
// ("f-> name") and the main bank calls spin(1) between chunks to twirl a
// | / - \ glyph in the character cell AT the ROM print position. This is the
// one kind of "output" the head page CAN host, because it never goes near the
// print driver (which pages the ROM over this very page): it writes the 8
// pixel bytes straight into the ULA display file at DF_CC (sysvar 23684, the
// print position's screen address, maintained by the driver), which lives at
// $4000-$57FF and is always mapped. The glyphs are private 8x8 art: the ROM
// font under CHARS (23606) is NOT readable from a dot - it points into
// $3C00-$3FFF, which is this dot page itself while we run. Attributes are
// left alone (the cell keeps the line's paper/ink). spin(0) blanks the
// cell; both ways are -v gated like the trace they decorate, re-read DF_CC
// every call (so they follow the cursor wherever the last print left it),
// and the twirl is frame-locked so a fast SD card doesn't blur it.

extern char g_verbose;   // the -v flag (nextsync.c); spinner is part of -v

// The pose art (6 rows per pose; the blank top/bottom rows are written by
// spin() itself) lives in nextsync.c so it lands in MAIN-BANK rodata: the
// head page is packed to the byte, and reading main-bank data from head-page
// code is always safe - both stay mapped while the dot runs.
extern const unsigned char spin_glyphs[30];

static unsigned char spin_phase;   // BYTE OFFSET of the pose on screen (0/6/12/18)
static unsigned char spin_frame;   // FRAMES value of the last pose change

// spin(1): advance one pose (frame-locked); spin(0): blank the cell.
// One function, not three - every head-page byte is precious here.
void spin(unsigned char go)
{
    unsigned char *cell = *(unsigned char **)23684;   // DF_CC
    const unsigned char *g;
    unsigned char i;

    if (!g_verbose) return;
    if (go)
    {
        unsigned char fr = FRAMES_LO;
        if (fr == spin_frame) return;              // one pose per frame
        spin_frame = fr;
        spin_phase += 6;                           // next pose (offsets 0/6/12/18)
        if (spin_phase >= 24) spin_phase = 0;
        g = spin_glyphs + spin_phase;
    }
    else
        g = spin_glyphs + 24;                      // the blank pose
    // Only draw when DF_CC really is the top row of a display-file cell
    // (bits 8-10 clear, below the attribute file at $5800). At the exact
    // moment a print ends on the screen's last cell the ROM can leave DF_CC
    // just past the display file, and +256 row strides from there would hit
    // the attribute file and then sysvars - skip the pose instead.
    if (((unsigned short)cell & 0x0700) || (unsigned short)cell >= 0x5800)
        return;
    *cell = 0;                                     // blank top row
    for (i = 0; i < 6; i++)
    {
        cell += 0x100;                             // rows sit 256 bytes apart
        *cell = g[i];
    }
    cell += 0x100;
    *cell = 0;                                     // blank bottom row
}
