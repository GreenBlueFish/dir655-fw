/*
 * dir655.h - helpers for C code that is linked into the DIR-655 Rev A 1.21
 * application image by toolchain/tools/dir655patch.py.
 *
 * Compile with the flags in toolchain/dir655.mk (DIR655_CFLAGS):
 *   -march=ubicom32v3   IP5K instruction set (the DIR-655's CPU)
 *   -mipos-abi          ipOS calling convention: d10-d13 caller-saved, so
 *                       only a1, a2, a6 are callee-saved, as in the firmware
 *   -ffixed-a0          never write a0; the firmware relies on a0 == 0
 */
#ifndef DIR655_H
#define DIR655_H

#define DIR655_STR_(x) #x
#define DIR655_STR(x) DIR655_STR_(x)

/* Load address of img.bin and the free gap new code is linked into. */
#define DIR655_IMG_BASE   0x40000000u
#define DIR655_GAP_START  0x40641d00u
#define DIR655_GAP_END    0x40650000u

/*
 * DIR655_HOOK(0x40012340, my_function);   (at file scope)
 *
 * Overwrite the first two instructions (8 bytes) of the firmware function
 * that starts at the given address with a jump to my_function. Every call to
 * the firmware function then runs my_function instead, with the caller's
 * arguments (d0, d1, ...) and return address (a5); its return value (d0)
 * goes back to the caller. my_function must have the same prototype. The
 * rest of the old function is left in place but is no longer reached from
 * its entry. The jump clobbers a3, which is caller-saved and not an argument
 * register.
 *
 * The address must be written as a hex literal (it becomes the section
 * name .at.0x40012340) and must be the entry of a function, e.g. one listed
 * by `make analyze` (out/functions.tsv).
 */
#define DIR655_HOOK(addr, fn)                                           \
    __asm__("\t.section .at." #addr ",\"ax\",@progbits\n"               \
            "\tmoveai a3, #%hi(" #fn ")\n"                              \
            "\tcalli a3, %lo(" #fn ")(a3)\n"                            \
            "\t.previous\n")

/*
 * DIR655_AT(0x40012340) puts the following function or object at that
 * address, on top of the firmware bytes there, e.g. to overwrite a constant
 * or a function body in place:
 *
 *   DIR655_AT(0x40150000) const char banner[8] = "hello!";
 *
 * Nothing checks that what you overwrite is unused; dir655patch.py prints
 * the old bytes so you can compare.
 */
#define DIR655_AT(addr) __attribute__((section(".at." #addr), used))

/*
 * Call a firmware function by address without a linker symbol:
 *
 *   #define fw_strlen DIR655_FW(int (*)(const char *), 0x40012340)
 *   n = fw_strlen("abc");
 *
 * Alternatively give it a name in a symbols file passed to the link
 * (SYMS=... in toolchain/Makefile), e.g. a line  fw_strlen = 0x40012340;
 * and declare it extern int fw_strlen(const char *);
 */
#define DIR655_FW(type, addr) ((type)(addr))

#endif
