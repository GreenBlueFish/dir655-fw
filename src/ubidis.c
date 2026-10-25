/*
 * ubidis - a from-scratch disassembler for the Ubicom32 instruction set
 * (the 32-bit, big-endian ISA used by the Ubicom IP3023/IP5000 family and
 * the D-Link DIR-655 Rev A firmware, which runs Ubicom's ipOS).
 *
 * Fixed 32-bit instructions, big-endian. Major opcode in bits [31:27].
 * Decoding is table-driven: for each word we find the ISA entry whose
 * (word & mask) == base, then render its operand template.
 *
 * The ISA *facts* (opcode values, field masks, mnemonics, operand syntax)
 * live in ubicom32_isa.inc and were derived from the public GPL Ubicom32
 * binutils CGEN description, read purely as a reference. All code here --
 * field extraction, operand formatting, matching, control-flow target
 * computation, and output -- is original and written for this project.
 * No third-party source code is vendored.
 *
 * Author: Hive worker, 2026.  License: same spirit as the project (private).
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

/* ---- operand renderer kinds (referenced by the generated table) ---- */
enum {
    K_DREG, K_AREG, K_UIMM, K_SIMM, K_DIRECT, K_I4, K_IMM7,
    K_IMM24, K_OFF21, K_OFF24, K_OFF16, K_CC, K_CFLAG, K_PFLAG,
    K_ACC, K_SKIP
};

/* ---- template token kinds ---- */
enum { T_END, T_MNEM, T_LIT, T_OP };

struct tok {
    int kind;       /* T_* */
    int opkind;     /* K_* when kind==T_OP */
    int p0, p1;     /* operand params (see renderers) */
    const char *lit;
};

struct insn_tmpl {
    uint32_t base;
    uint32_t mask;
    const char *mnem;
    struct tok toks[24];
};

#include "ubicom32_isa.inc"

static const char *cc_names[16] = {
    "f","cc","cs","eq","ge","gt","hi","le","ls","lt","mi","ne","pl","t","vc","vs"
};

/* Extract an lsb0 bitfield: bits [start : start-len+1]. */
static uint32_t xf(uint32_t w, int start, int len)
{
    if (len <= 0) return 0;
    return (w >> (start - len + 1)) & ((len >= 32) ? 0xFFFFFFFFu : ((1u << len) - 1));
}

/* Sign-extend an n-bit value. */
static int32_t sext(uint32_t v, int n)
{
    uint32_t m = 1u << (n - 1);
    return (int32_t)((v ^ m) - m);
}

/* Render one operand into buf. 'pc' is the virtual address of this insn.
 * Returns number of chars written. If this operand feeds a branch target,
 * *target is set and *is_target=1. */
static int render_op(char *buf, const struct tok *t, uint32_t w,
                     uint32_t pc, uint32_t *target, int *is_target)
{
    switch (t->opkind) {
    case K_DREG:  return sprintf(buf, "d%u", xf(w, t->p0, t->p1));
    case K_AREG: {
        uint32_t a = xf(w, t->p0, t->p1);
        return a == 7 ? sprintf(buf, "%s", "sp") : sprintf(buf, "a%u", a);
    }
    case K_ACC:   return sprintf(buf, "acc%u", xf(w, t->p0, t->p1));
    case K_UIMM:  return sprintf(buf, "%u", xf(w, t->p0, t->p1));
    case K_SIMM:  return sprintf(buf, "%d", sext(xf(w, t->p0, t->p1), t->p1));
    case K_DIRECT:return sprintf(buf, "0x%x", xf(w, t->p0, t->p1) << 2);
    case K_I4: {
        int len = t->p1 & 0xff, s = (t->p1 >> 8) & 0xff;
        return sprintf(buf, "%d", sext(xf(w, t->p0, len), len) << s);
    }
    case K_IMM7: {
        int tstart = t->p0 >> 8, tlen = t->p0 & 0xff;
        int bstart = (t->p1 >> 8) & 0xff, blen = t->p1 & 0xff, s = (t->p1 >> 16) & 0xff;
        uint32_t tt = xf(w, tstart, tlen), bb = xf(w, bstart, blen);
        return sprintf(buf, "%u", (tt << (5 + s)) | (bb << s));
    }
    case K_IMM24: {
        uint32_t v = (xf(w, 26, 3) << 21) | xf(w, 20, 21);
        return sprintf(buf, "0x%x", v);
    }
    case K_OFF21: {
        int32_t o = sext(xf(w, 20, 21), 21);
        uint32_t tgt = (((uint32_t)(o << 2)) + pc) & 0xfffffffcu;
        *target = tgt; *is_target = 1;
        return sprintf(buf, "0x%x", tgt);
    }
    case K_OFF24: {
        int32_t hi = sext(xf(w, 26, 3), 3);
        uint32_t lo = xf(w, 20, 21);
        int32_t v = (hi << 21) | lo;
        uint32_t tgt = ((uint32_t)(v << 2)) + pc;
        *target = tgt; *is_target = 1;
        return sprintf(buf, "0x%x", tgt);
    }
    case K_OFF16: {
        int32_t o = (sext(xf(w, 26, 3), 3) << 13) | (xf(w, 20, 5) << 8)
                  | (xf(w, 10, 3) << 5) | xf(w, 4, 5);
        return sprintf(buf, "%d", o << 2);
    }
    case K_CC:    return sprintf(buf, "%s", cc_names[xf(w, 26, 4) & 15]);
    case K_CFLAG: return xf(w, 21, 1) ? 0 : sprintf(buf, ".s");
    case K_PFLAG: return sprintf(buf, "%s", xf(w, 22, 1) ? ".t" : ".f");
    case K_SKIP:  return 0;
    }
    return 0;
}

/* Index entries by the 5-bit major opcode [31:27] for speed. Every entry in
 * this ISA fixes op1 in its mask, so the bucket is exhaustive. Each bucket is
 * filled in table order, which is pre-sorted most-specific (widest mask) first.
 */
static const struct insn_tmpl *buckets[32][1024];
static int bucket_n[32];

static void build_index(void)
{
    for (size_t i = 0; i < UBICOM32_NINSNS; i++) {
        const struct insn_tmpl *e = &ubicom32_insns[i];
        unsigned op1 = (e->base >> 27) & 0x1f;
        if (bucket_n[op1] < (int)(sizeof buckets[0] / sizeof buckets[0][0]))
            buckets[op1][bucket_n[op1]++] = e;
    }
}

/* Find the matching ISA entry (buckets pre-sorted most-specific first). */
static const struct insn_tmpl *match(uint32_t w)
{
    unsigned op1 = (w >> 27) & 0x1f;
    for (int i = 0; i < bucket_n[op1]; i++)
        if ((w & buckets[op1][i]->mask) == buckets[op1][i]->base)
            return buckets[op1][i];
    return NULL;
}

/* Disassemble one word into text. Returns 1 if a known insn, 0 if unknown.
 * Sets *target and *is_target for branch/call; *is_call set for 'call'. */
static int disasm_one(uint32_t w, uint32_t pc, char *out,
                      uint32_t *target, int *is_target, int *is_call)
{
    *is_target = 0; *is_call = 0;
    const struct insn_tmpl *in = match(w);
    if (!in) { sprintf(out, ".word\t0x%08x", w); return 0; }
    char *p = out;
    for (const struct tok *t = in->toks; t->kind != T_END; t++) {
        if (t->kind == T_MNEM)      p += sprintf(p, "%s", in->mnem);
        else if (t->kind == T_LIT)  p += sprintf(p, "%s", t->lit);
        else                        p += render_op(p, t, w, pc, target, is_target);
    }
    if (!strcmp(in->mnem, "call")) *is_call = 1;
    return 1;
}

int main(int argc, char **argv)
{
    const char *path = NULL;
    uint32_t start = 0, base_va = 0;
    long length = -1;
    int do_targets = 1;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-s"))      start = strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "-n")) length = strtol(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "-b")) base_va = strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--no-targets")) do_targets = 0;
        else path = argv[i];
    }
    if (!path) {
        fprintf(stderr,
          "usage: %s [-s file_off] [-n nbytes] [-b base_vaddr] [--no-targets] img.bin\n"
          "  Disassembles a region as Ubicom32 (big-endian, 4-byte insns).\n"
          "  base_vaddr is the virtual address mapped to -s (default 0).\n", argv[0]);
        return 2;
    }

    build_index();

    FILE *f = fopen(path, "rb");
    if (!f) { perror("open"); return 1; }
    fseek(f, 0, SEEK_END);
    long fsz = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc(fsz);
    if (fread(buf, 1, fsz, f) != (size_t)fsz) { perror("read"); return 1; }
    fclose(f);

    if (start >= (uint32_t)fsz) { fprintf(stderr, "start past EOF\n"); return 1; }
    long end = (length < 0) ? fsz : (long)start + length;
    if (end > fsz) end = fsz;

    unsigned known = 0, total = 0;
    for (long off = start; off + 4 <= end; off += 4) {
        uint32_t w = ((uint32_t)buf[off] << 24) | ((uint32_t)buf[off+1] << 16)
                   | ((uint32_t)buf[off+2] << 8) | buf[off+3];
        uint32_t pc = base_va + (off - start);
        char text[256]; uint32_t tgt = 0; int ist = 0, isc = 0;
        int ok = disasm_one(w, pc, text, &tgt, &ist, &isc);
        known += ok; total++;
        printf("%08x:  %08x\t%s", pc, w, text);
        if (do_targets && ist) {
            long foff = (long)tgt - (long)base_va + start;
            if (foff >= 0 && foff < fsz)
                printf("\t; %s0x%x", isc ? "call->" : "->", tgt);
        }
        printf("\n");
    }
    fprintf(stderr, "# %u/%u words decoded as known insns (%.1f%%)\n",
            known, total, total ? 100.0 * known / total : 0.0);
    free(buf);
    return 0;
}
