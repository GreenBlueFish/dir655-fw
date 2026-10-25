"""
Whole-program view: find every function in the code region.

Entries come from, in order of confidence:
  call     targets of `call` instructions;
  pointer  32-bit words anywhere in the image (and moveai+lea constants)
           that point at otherwise-unclaimed code -- function pointers in
           tables, callbacks, thread entry points;
  orphan   runs of valid instructions that nothing claims, starting right
           after the end of a claimed function (code reached only through
           pointers we could not resolve).
"""
from .machine import Analyzer, BASE


class Program(object):
    def __init__(self, img):
        self.img = img
        self.an = Analyzer(img)
        self.funcs = {}
        self.owner = {}       # insn addr -> set of function entries

    def _add(self, f, how):
        f.how = how
        self.funcs[f.entry] = f
        for b in f.blocks.values():
            for i in b.insns:
                self.owner.setdefault(i.addr, set()).add(f.entry)

    def discover(self, log=None):
        img = self.img
        calls = {}
        consts = set()
        last_ai = {}
        for a in range(img.code_lo, img.code_hi, 4):
            i = img.insn(a)
            if not i.valid:
                continue
            if i.mn == "call" and img.in_code(i.target):
                calls.setdefault(i.target, set()).add(a)
            if i.mn == "moveai":
                last_ai[i.dst.a] = i.imm
            elif i.mn.startswith("lea") and i.s1.kind == "mem":
                k = 0x80 + i.s1.a * 4
                if k in last_ai:
                    consts.add(last_ai[k] + i.s1.b)
        known = set(calls)
        for e in sorted(calls):
            f = self.an.build(e, known)
            f.callers = calls[e]
            self._add(f, "call")
        if log: log("call targets: %d functions" % len(self.funcs))
        # pointer candidates
        cands = set()
        d = img.data
        for o in range(0, len(d) - 3, 4):
            w = int.from_bytes(d[o:o + 4], "big")
            if img.in_code(w):
                cands.add(w)
        cands |= {c for c in consts if img.in_code(c)}
        npt = 0
        for c in sorted(cands):
            if c in self.funcs or c in self.owner:
                continue
            i = img.insn(c)
            if not i.valid:
                continue
            prev = img.insn(c - 4) if c - 4 >= img.code_lo else None
            # a function starts after an unconditional transfer
            if prev is not None and prev.valid and not _ends_flow(prev) \
                    and (c - 4) not in self.owner:
                continue
            f = self.an.build(c, known | {c})
            if f.asm_reasons:
                continue
            known.add(c)
            self._add(f, "pointer"); npt += 1
        if log: log("pointer entries: %d functions" % npt)
        # orphans
        norph = 0
        a = img.code_lo
        while a < img.code_hi:
            if a in self.owner or not img.insn(a).valid:
                a += 4; continue
            prev = img.insn(a - 4) if a > img.code_lo else None
            if prev is None or (a - 4) in self.owner and _ends_flow(prev):
                f = self.an.build(a, known | {a})
                if not f.asm_reasons and f.blocks:
                    known.add(a)
                    self._add(f, "orphan"); norph += 1
                    a += 4
                    continue
            a += 4
        if log: log("orphan entries: %d functions" % norph)
        return self.funcs


def _ends_flow(i):
    if i.mn == "ret": return True
    if i.mn == "calli" and (i.link != 5 or i.s1.a == 5): return True
    if i.mn == "jmp" and i.cc == 13 and i.target != i.addr + 4: return True
    return False
