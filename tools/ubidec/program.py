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
from .machine import Analyzer, BASE, SP

# Hand-written hardware-thread (HRT) and driver code: register-indexed
# dispatch, `suspend`, a0 as an I/O base (docs/DECOMPILER.md, "Only assembly").
HRT_LO, HRT_HI = 0x4000a500, 0x4000e800


class Program(object):
    def __init__(self, img):
        self.img = img
        self.an = Analyzer(img)
        self.funcs = {}
        self.owner = {}       # insn addr -> set of function entries
        # pointer/orphan candidates dropped because they flow into data or
        # invalid code; never added, so flow errors in funcs can't see them
        self.rejected = []

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
                self.rejected.append(f)
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
                if f.asm_reasons:
                    self.rejected.append(f)
            a += 4
        if log: log("orphan entries: %d functions" % norph)
        n = self.resolve_shared_returns()
        if log: log("returns through an enclosing frame: %d" % n)
        return self.funcs

    def resolve_shared_returns(self):
        """Resolve indirect jumps whose return address was lost because the
        function was entered part-way through another function's frame.

        Switch-case bodies found through pointer tables and alternate entry
        points start with no saved return address in view, so their
        `ret (sp)N++` / `ret dN` stays unresolved. When the same instruction
        is a resolved return in an enclosing function G, it is G's return:
        the block becomes kind "fragret" and the function records
        f.parent = G and, when both stack pointers are known there,
        f.parent_sp = G's entry-relative sp at this function's entry."""
        rets = {}
        for g in self.funcs.values():
            for b in g.blocks.values():
                if b.kind == "ret":
                    rets.setdefault(b.insns[-1].addr, []).append(g)
        n = 0
        for f in self.funcs.values():
            for b in f.blocks.values():
                if b.kind != "ijmp":
                    continue
                a = b.insns[-1].addr
                gs = [g for g in rets.get(a, ()) if g is not f]
                if not gs:
                    continue
                g = min(gs, key=lambda g: g.entry)
                b.kind = "fragret"; b.target = g.entry
                n += 1
                if getattr(f, "parent", None) is not None:
                    continue
                f.parent = g.entry
                fs = f.states[a]["r"].get(SP)
                gs_ = g.states[a]["r"].get(SP)
                f.parent_sp = (gs_[1] - fs[1]) if fs and gs_ and \
                    fs[0] == gs_[0] == "sp" else None
        return n

    def ijmp_groups(self):
        """Classify every unresolved indirect jump (block kind "ijmp")."""
        groups = {"hrt": [], "computed": [], "ram_table": [], "lost_ra": []}
        for f in self.funcs.values():
            for b in f.blocks.values():
                if b.kind != "ijmp":
                    continue
                i = b.insns[-1]
                if HRT_LO <= i.addr < HRT_HI:
                    g = "hrt"
                elif i.mn == "calli" and i.s1.a == 0:
                    g = "computed"       # memcpy/memset unrolled-run jumps
                else:
                    r = f.states[i.addr]["r"]
                    v = r.get(0x80 + i.s1.a * 4) if i.mn == "calli" else None
                    # a table in on-chip RAM (0x3ffcxxxx), filled at run time
                    g = "ram_table" if v and v[0] == "tbl" else "lost_ra"
                groups[g].append((f.entry, i.addr))
        return groups


def _ends_flow(i):
    if i.mn == "ret": return True
    if i.mn == "calli" and (i.link != 5 or i.s1.a == 5): return True
    if i.mn == "jmp" and i.cc == 13 and i.target != i.addr + 4: return True
    return False
