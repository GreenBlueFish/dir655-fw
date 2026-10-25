/* Integer arithmetic, shifts, comparisons, byte and halfword access. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

int add3(int a, int b, int c) { return a + b - c; }
u32 mul(u32 a, u32 b) { return a * b; }
int sdiv(int a, int b) { return a / b; }
u32 umod(u32 a, u32 b) { return a % b; }
long long add64(long long a, long long b) { return a + b; }
long long mul64(long long a, long long b) { return a * b; }
u32 rotl(u32 x, int n) { return (x << n) | (x >> (32 - n)); }
int sext8(u8 v) { return (signed char)v; }
u16 swap16(u16 v) { return (u16)((v << 8) | (v >> 8)); }
u32 bswap32(u32 v)
{
    return (v >> 24) | ((v >> 8) & 0xff00) | ((v << 8) & 0xff0000) | (v << 24);
}
int max(int a, int b) { return a > b ? a : b; }
u32 umin(u32 a, u32 b) { return a < b ? a : b; }
int big_constant(void) { return 0x12345678; }
