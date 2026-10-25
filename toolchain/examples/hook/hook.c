/*
 * Example patch: replace one firmware function with compiled C.
 *
 * 0x40000fc4 is the entry of a small function in the 1.21 image (11
 * instructions, 10 callers in out/functions.tsv). This example does not
 * claim to know what that function is for: it only shows the mechanics, and
 * is what the toolchain tests build and check. Do not flash it.
 */
#include "dir655.h"

static int table[4] = { 1, 2, 4, 8 };
static int calls;               /* .bss: zero-filled in the gap */

int my_replacement(int a, int b)
{
    calls++;
    return table[a & 3] * b + calls;
}

DIR655_HOOK(0x40000fc4, my_replacement);
