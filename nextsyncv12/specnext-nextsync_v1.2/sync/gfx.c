// gfx.c
// Text output helpers. The original on-screen font / positioned-counter UI was
// removed so the bidirectional (Sync4 send) build fits within the 8KB .dot
// limit; all output now goes through the ROM print routine via print()/conprint().

extern void print(char * t);

// 16-bit unsigned -> decimal string. Kept 16-bit (no 32-bit math, no division)
// to save code space; the dot only ever prints values that fit in 16 bits.
unsigned char uitoa(unsigned short v, char *b)
{
    static const unsigned short tt[] = { 10000, 1000, 100, 10, 1 };
    unsigned char p = 0, i, digit;
    char started = 0;
    for (i = 0; i < 5; i++)
    {
        digit = 0;
        while (v >= tt[i]) { v -= tt[i]; digit++; }
        if (digit || started || i == 4) { b[p++] = '0' + digit; started = 1; }
    }
    b[p] = 0;
    return p;
}

void printnum(unsigned short v)
{
    char temp[8];
    uitoa(v, temp);
    print(temp);
}

unsigned char strinstr(char *a, char *b, unsigned short len, char blen)
{
    if (!*b || !blen) return 1;
    while (len)
    {
        if (*a == *b)
        {
            unsigned char i = 0;
            while (i < blen && a[i] == b[i]) i++;
            if (i >= blen)
                return 1;
        }
        a++;
        len--;
    }
    return 0;
}
