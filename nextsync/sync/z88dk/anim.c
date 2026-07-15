// anim.c - optional "-anim" / "-a" eye-candy for the .sync4 dotN.
//
// Hardware sprites (Pink Floyd's gulls + Clive's saucers) drift across the
// screen while syncing. This is DELIBERATELY register/port-only: the Next
// sprite engine has its own pattern + attribute RAM reached solely through the
// I/O ports (0x303B / 0x005B / 0x0057), completely independent of the CPU
// memory map - so nothing here can disturb the dot's own memory or corrupt the
// timing-critical UART transfer. It only ever runs when -anim/-a is passed, and
// nextsync.c only ticks it at *safe points* (between files / commands), never
// mid-packet.
//
// Layer 2 colour-scroll was deliberately left out of this first cut: writing
// Layer 2 pixels needs careful MMU bank juggling (the dot keeps live data in
// low RAM) and wants hardware testing, whereas sprites carry none of that risk.
//
// v1 keeps the art simple (8bpp 16x16, default sprite palette) and the colours
// easy to tweak once seen on real hardware.

#include <arch/zxn.h>

#define N_SPR   6           // 3 gulls (pattern 0) + 3 saucers (pattern 1)
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
// slot 3.
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
static unsigned char  g_frame;
static unsigned char  saved_r15;
static unsigned char  g_anim_on = 0;

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
static void put_sprite(unsigned char i, unsigned short x, unsigned char y,
                       unsigned char pattern)
{
    IO_SPRITE_SLOT = i;
    IO_SPRITE_ATTRIBUTE = (unsigned char)(x & 0xFF);      // attr0: X lsb
    IO_SPRITE_ATTRIBUTE = y;                              // attr1: Y
    IO_SPRITE_ATTRIBUTE = (unsigned char)((x >> 8) & 1);  // attr2: X msb, pal 0
    IO_SPRITE_ATTRIBUTE = (unsigned char)(0x80 | pattern);// attr3: visible+pattern
}

void anim_begin(void)
{
    unsigned char i;

    saved_r15 = ZXN_READ_REG(REG_SPRITE_LAYER_SYSTEM);
    ZXN_WRITE_REG(REG_SPRITE_LAYER_SYSTEM,
                  (unsigned char)(saved_r15 | RSLS_SPRITES_VISIBLE
                                            | RSLS_SPRITES_OVER_BORDER));

    upload_pattern(0, gull_up,    PINK, PINK);   // gull wing poses -> slots 0/1/2
    upload_pattern(1, gull_level, PINK, PINK);
    upload_pattern(2, gull_down,  PINK, PINK);
    upload_pattern(3, ufo_mask,   GREY, CYAN);   // saucer: cyan dome, grey body

    g_frame = 0;
    for (i = 0; i < N_SPR; i++)
    {
        spx[i] = (unsigned short)(i * 48u);
        spy[i] = (unsigned char)(40 + i * 22);
        spd[i] = (unsigned char)(1 + (i & 3));
        put_sprite(i, spx[i], spy[i], (unsigned char)(i < 3 ? 0 : 3));
    }
    g_anim_on = 1;
}

void anim_tick(void)
{
    // Wing flap cycle: up -> level -> down -> level -> (repeat). Advancing every
    // 2 ticks (g_frame >> 1) keeps it from blurring during rapid idle polling;
    // the per-bird phase offset stops them flapping in lockstep. Tune the >>1
    // and/or the phase if it looks too fast or slow on your Next.
    static const unsigned char flap_frame[4] = { 0, 1, 2, 1 };
    unsigned char i;
    if (!g_anim_on) return;

    g_frame++;
    for (i = 0; i < N_SPR; i++)
    {
        unsigned short x = (unsigned short)(spx[i] + spd[i]);
        unsigned char ph, y, pat;
        signed char bob;
        if (x >= 320) x = (unsigned short)(x - 320);
        spx[i] = x;
        // gentle vertical bob, triangle wave in [-8,+7] (no trig, no tables)
        ph = (unsigned char)((g_frame + i * 5) & 31);
        bob = (ph < 16) ? (signed char)(ph - 8) : (signed char)(23 - ph);
        y = (unsigned char)(spy[i] + bob);
        // birds flap through slots 0/1/2; saucers stay on slot 3
        pat = (i < 3) ? flap_frame[((g_frame >> 1) + i) & 3] : 3;
        put_sprite(i, x, y, pat);
    }
}

void anim_end(void)
{
    unsigned char i;
    if (!g_anim_on) return;

    // Hide our sprites (visible bit cleared) so nothing lingers, then restore
    // the original sprite/layers register.
    for (i = 0; i < N_SPR; i++)
    {
        IO_SPRITE_SLOT = i;
        IO_SPRITE_ATTRIBUTE = 0;
        IO_SPRITE_ATTRIBUTE = 0;
        IO_SPRITE_ATTRIBUTE = 0;
        IO_SPRITE_ATTRIBUTE = 0;   // attr3 bit7 = 0 -> not visible
    }
    ZXN_WRITE_REG(REG_SPRITE_LAYER_SYSTEM, saved_r15);
    g_anim_on = 0;
}
