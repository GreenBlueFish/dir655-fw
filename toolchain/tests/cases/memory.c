/* Pointers, arrays, structs, globals and loops. */
struct pkt {
    unsigned char  type;
    unsigned short len;
    unsigned int   addr;
    struct pkt    *next;
};

int counter;
static const char hex[] = "0123456789abcdef";
unsigned char buf[64];

void *my_memset(void *d, int c, unsigned n)
{
    unsigned char *p = d;
    while (n--)
        *p++ = (unsigned char)c;
    return d;
}

unsigned my_strlen(const char *s)
{
    const char *p = s;
    while (*p)
        p++;
    return (unsigned)(p - s);
}

unsigned total_len(const struct pkt *p)
{
    unsigned n = 0;
    for (; p; p = p->next)
        if (p->type != 0)
            n += p->len;
    return n;
}

void to_hex(unsigned v, char *out)
{
    int i;
    for (i = 7; i >= 0; i--, v >>= 4)
        out[i] = hex[v & 15];
    out[8] = 0;
    counter++;
}

int sum16(const short *a, int n)
{
    int s = 0, i;
    for (i = 0; i < n; i++)
        s += a[i];
    return s;
}
