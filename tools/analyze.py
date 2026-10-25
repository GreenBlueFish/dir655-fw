#!/usr/bin/env python3
"""
analyze - whole-program analysis of img.bin with tools/ubidec.

Writes a tab-separated function table: one row per discovered function with
its entry address, how it was found (call / pointer / orphan), size, block
counts by kind, direct callers and direct callees (the call graph).
`fragret` counts returns made through an enclosing function's frame and
`parent` names that function (a switch case or alternate entry found on its
own; see Program.resolve_shared_returns).

Usage:  python3 tools/analyze.py out/img.bin out/functions.tsv
"""
import os
import sys
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ubidec.machine import Image          # noqa: E402
from ubidec.program import Program        # noqa: E402


def analyze(path, log=None):
    img = Image(open(path, "rb").read())
    prog = Program(img)
    prog.discover(log=log)
    return img, prog


def summary(img, prog):
    kinds = collections.Counter()
    for f in prog.funcs.values():
        for b in f.blocks.values():
            kinds[b.kind] += 1
    valid = sum(1 for a in range(img.code_lo, img.code_hi, 4)
                if img.insn(a).valid)
    how = collections.Counter(f.how for f in prog.funcs.values())
    return {"functions": len(prog.funcs), "how": dict(how),
            "blocks": dict(kinds), "covered": len(prog.owner),
            "valid": valid,
            # only functions that were kept: a pointer or orphan candidate
            # that flows into data or invalid code is rejected, not counted
            "flow_errors": sum(len(f.asm_reasons)
                               for f in prog.funcs.values()),
            "rejected": len(prog.rejected),
            "ijmp": {k: len(v) for k, v in prog.ijmp_groups().items()}}


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    img, prog = analyze(sys.argv[1], log=lambda m: sys.stderr.write(m + "\n"))
    with open(sys.argv[2], "w") as out:
        out.write("entry\thow\tinsns\tblocks\tret\tswitch\tijmp\ttail\t"
                  "callers\tcallees\n")
        for e in sorted(prog.funcs):
            f = prog.funcs[e]
            k = collections.Counter(b.kind for b in f.blocks.values())
            insns = [i for b in f.blocks.values() for i in b.insns]
            callees = sorted({i.target for i in insns if i.mn == "call"})
            parent = getattr(f, "parent", None)
            out.write("0x%08x\t%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%s\t%s\t%s\n"
                      % (e, f.how, len(insns), len(f.blocks), k["ret"],
                         k["switch"], k["ijmp"], k["tail"], k["fragret"],
                         "0x%08x" % parent if parent else "",
                ",".join("0x%08x" % c for c in sorted(f.callers)),
                ",".join("0x%08x" % c for c in callees)))
    s = summary(img, prog)
    sys.stderr.write("%d functions %s; %d/%d valid code words covered; "
                     "blocks %s\n" % (s["functions"], s["how"], s["covered"],
                                      s["valid"], s["blocks"]))
    sys.stderr.write("unresolved indirect jumps by group %s; %d pointer/orphan "
                     "candidates rejected for flowing into data\n"
                     % (s["ijmp"], s["rejected"]))


if __name__ == "__main__":
    main()
