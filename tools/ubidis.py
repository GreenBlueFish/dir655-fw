#!/usr/bin/env python3
"""
ubidis - from-scratch Ubicom32 disassembler (Python reference implementation).

Ubicom32 is the 32-bit, big-endian ISA used by the Ubicom IP3023/IP5000
family and by the D-Link DIR-655 Rev A firmware (which runs Ubicom's ipOS).
Instructions are a fixed 32 bits, big-endian, major opcode in bits [31:27].

Decoding is table-driven: for each word we find the ISA entry whose
(word & mask) == base, then render its operand template. The ISA *facts*
(opcodes, masks, mnemonics, operand syntax) come from ubicom32_isa.py, which
was derived from the public GPL Ubicom32 binutils description read purely as a
reference. All logic here is original; no third-party code is vendored.

This mirrors src/ubidis.c exactly and is the implementation used for
validation. Usage:
    python3 ubidis.py [-s off] [-n nbytes] [-b base_vaddr] img.bin
"""
import sys, os, argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ubicom32_isa import INSNS  # noqa: E402

CC = ["f","cc","cs","eq","ge","gt","hi","le","ls","lt","mi","ne","pl","t","vc","vs"]

# ISA table pre-sorted most-specific (highest mask popcount) first.
_TABLE = sorted(INSNS, key=lambda e: -bin(e[1]).count("1"))

# Index by the 5-bit major opcode [31:27] for speed. Entries whose mask does
# not fully fix op1 go in a fallback list checked for every word.
_BUCKETS = [[] for _ in range(32)]
_FALLBACK = []
for _e in _TABLE:
    if (_e[1] >> 27) & 0x1f == 0x1f:
        _BUCKETS[(_e[0] >> 27) & 0x1f].append(_e)
    else:
        _FALLBACK.append(_e)


def xf(w, start, length):
    """Extract lsb0 bitfield: bits [start : start-length+1]."""
    if length <= 0:
        return 0
    return (w >> (start - length + 1)) & ((1 << length) - 1)


def sext(v, n):
    m = 1 << (n - 1)
    return (v ^ m) - m


def render_op(tok, w, pc):
    """Return (text, target_or_None)."""
    _, kind, p0, p1 = tok
    if kind == "K_DREG":  return "d%u" % xf(w, p0, p1), None
    if kind == "K_AREG":
        a = xf(w, p0, p1); return ("sp" if a == 7 else "a%u" % a), None
    if kind == "K_ACC":   return "acc%u" % xf(w, p0, p1), None
    if kind == "K_UIMM":  return "%u" % xf(w, p0, p1), None
    if kind == "K_SIMM":  return "%d" % sext(xf(w, p0, p1), p1), None
    if kind == "K_DIRECT":return "0x%x" % (xf(w, p0, p1) << 2), None
    if kind == "K_I4":
        length = p1 & 0xff; s = (p1 >> 8) & 0xff
        return "%d" % (sext(xf(w, p0, length), length) << s), None
    if kind == "K_IMM7":
        tstart = p0 >> 8; tlen = p0 & 0xff
        bstart = (p1 >> 8) & 0xff; blen = p1 & 0xff; s = (p1 >> 16) & 0xff
        tt = xf(w, tstart, tlen); bb = xf(w, bstart, blen)
        return "%u" % ((tt << (5 + s)) | (bb << s)), None
    if kind == "K_IMM24":
        v = (xf(w, 26, 3) << 21) | xf(w, 20, 21)
        return "0x%x" % v, None
    if kind == "K_OFF21":
        o = sext(xf(w, 20, 21), 21)
        tgt = (((o << 2) & 0xffffffff) + pc) & 0xfffffffc
        return "0x%x" % tgt, tgt
    if kind == "K_OFF24":
        hi = sext(xf(w, 26, 3), 3); lo = xf(w, 20, 21)
        v = (hi << 21) | lo
        tgt = (((v << 2) & 0xffffffff) + pc) & 0xffffffff
        return "0x%x" % tgt, tgt
    if kind == "K_OFF16":
        o = (sext(xf(w, 26, 3), 3) << 13) | (xf(w, 20, 5) << 8) \
            | (xf(w, 10, 3) << 5) | xf(w, 4, 5)
        return "%d" % (o << 2), None
    if kind == "K_CC":    return CC[xf(w, 26, 4) & 15], None
    if kind == "K_CFLAG": return ("" if xf(w, 21, 1) else ".s"), None
    if kind == "K_PFLAG": return (".t" if xf(w, 22, 1) else ".f"), None
    if kind == "K_SKIP":  return "", None
    return "?", None


def match(w):
    # Buckets and fallback are each pre-sorted most-specific first.
    for e in _BUCKETS[(w >> 27) & 0x1f]:
        if (w & e[1]) == e[0]:
            return e
    for e in _FALLBACK:
        if (w & e[1]) == e[0]:
            return e
    return None


def disasm_one(w, pc):
    """Return (text, target_or_None, is_call, known)."""
    e = match(w)
    if e is None:
        return ".word\t0x%08x" % w, None, False, False
    base, mask, mnem, toks = e
    out = []; target = None
    for t in toks:
        if t[0] == 'm':   out.append(mnem)
        elif t[0] == 'l': out.append(t[1])
        else:
            txt, tgt = render_op(t, w, pc)
            out.append(txt)
            if tgt is not None:
                target = tgt
    return "".join(out), target, (mnem == "call"), True


def build_labels(data, start, end, base):
    """Two-pass: collect call targets (functions) and branch targets (locals)."""
    funcs, locs = set(), set()
    off = start
    while off + 4 <= end:
        w = int.from_bytes(data[off:off+4], "big")
        pc = base + (off - start)
        _, tgt, is_call, _ = disasm_one(w, pc)
        if tgt is not None:
            foff = tgt - base + start
            if start <= foff < end:
                (funcs if is_call else locs).add(tgt)
        off += 4
    locs -= funcs
    return funcs, locs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("-s", "--start", type=lambda x: int(x, 0), default=0)
    ap.add_argument("-n", "--nbytes", type=lambda x: int(x, 0), default=-1)
    ap.add_argument("-b", "--base", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--stats", action="store_true", help="only print coverage stats")
    ap.add_argument("--listing", action="store_true",
                    help="annotated listing with func_/loc_ labels")
    args = ap.parse_args()

    data = open(args.image, "rb").read()
    start = args.start
    end = len(data) if args.nbytes < 0 else min(len(data), start + args.nbytes)

    funcs = locs = set()
    if args.listing:
        funcs, locs = build_labels(data, start, end, args.base)

    known = total = 0
    out = []
    off = start
    while off + 4 <= end:
        w = int.from_bytes(data[off:off+4], "big")
        pc = args.base + (off - start)
        text, tgt, is_call, ok = disasm_one(w, pc)
        known += ok; total += 1
        if not args.stats:
            if args.listing:
                if pc in funcs:
                    out.append("\nfunc_%08x:" % pc)
                elif pc in locs:
                    out.append("loc_%08x:" % pc)
            line = "%08x:  %08x\t%s" % (pc, w, text)
            if tgt is not None:
                foff = tgt - args.base + start
                if 0 <= foff < len(data):
                    if args.listing and tgt in funcs:
                        line += "\t; func_%08x" % tgt
                    elif args.listing and tgt in locs:
                        line += "\t; loc_%08x" % tgt
                    else:
                        line += "\t; %s0x%x" % ("call->" if is_call else "->", tgt)
            out.append(line)
        off += 4
    if out:
        sys.stdout.write("\n".join(out) + "\n")
    sys.stderr.write("# %u/%u words known (%.2f%%)" % (known, total,
                     100.0 * known / total if total else 0))
    if args.listing:
        sys.stderr.write("  %u funcs, %u local labels" % (len(funcs), len(locs)))
    sys.stderr.write("\n")


if __name__ == "__main__":
    main()
