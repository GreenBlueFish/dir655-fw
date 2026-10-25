"""
Machine-level analysis: the image, function discovery, control-flow graphs,
and a small abstract interpreter that tracks the stack pointer, the return
address, constants and jump tables through each function.

Nothing here produces C; it gives a later lifter (not written yet; see
docs/DECOMPILER.md, "What remains for C output") the facts it needs:
where functions start and end, which `ret`/`calli` is a return, which is a
switch, what offset from the entry stack pointer every sp-relative access has.
"""
import os
import sys

from .decode import decode, CC

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."
                                , "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ubidis  # noqa: E402  (ISA fact table, used for strict validity)

BASE = 0x40000000          # img.bin is loaded at the start of DDR
CODE_END = 0x148000        # end of the code region inside img.bin

# Registers the ABI preserves across a call (established empirically: the
# only registers compiled code ever pushes in a prologue are a1, a2, a6 and
# the return address a5; a0 is held at zero; sp is restored).
CALLEE_SAVED = {0x84, 0x88, 0x98, 0x9c, 0x80}
SP = 0x9c
A5 = 0x94


class Image(object):
    def __init__(self, data, base=BASE, code_end=CODE_END):
        self.data = data
        self.base = base
        self.code_lo = base
        self.code_hi = base + code_end
        self._cache = {}

    def in_code(self, a):
        return self.code_lo <= a < self.code_hi and not (a & 3)

    def in_image(self, a):
        return self.base <= a < self.base + len(self.data)

    def word(self, a):
        o = a - self.base
        return int.from_bytes(self.data[o:o + 4], "big")

    def byte(self, a):
        return self.data[a - self.base]

    def insn(self, a):
        i = self._cache.get(a)
        if i is None:
            w = self.word(a)
            i = decode(w, a)
            if i.valid and ubidis.match(w) is None:
                i.valid = False
            self._cache[a] = i
        return i

    def cstring(self, a, maxlen=512):
        """Return the NUL-terminated printable string at a, or None."""
        if not self.in_image(a):
            return None
        o = a - self.base
        end = self.data.find(b"\0", o, o + maxlen)
        if end < 0 or end == o:
            return None
        s = self.data[o:end]
        ok = sum(1 for c in s if 32 <= c < 127 or c in (9, 10, 13))
        if ok != len(s):
            return None
        if len(s) < 2 and not s.isalnum():
            return None
        return s.decode("latin-1")


# ---- abstract values --------------------------------------------------------
# None = unknown; ("c", n) constant; ("sp", off) entry-sp + off;
# ("ra",) the caller's return address; ("tbl", base, idxreg, scale) a value
# loaded from a table indexed by a data register.

def _c(v):
    return ("c", v & 0xffffffff)


def _add(v, k):
    if v is None: return None
    if v[0] == "c": return _c(v[1] + k)
    if v[0] == "sp": return ("sp", v[1] + k)
    return None


class Block(object):
    __slots__ = ("start", "end", "succs", "kind", "insns", "target",
                 "table", "preds")

    def __init__(self, start):
        self.start = start
        self.end = start          # address after the last instruction
        self.succs = []           # successor block addresses
        self.kind = "fall"        # fall|jmp|cjmp|ret|tail|switch|ijmp|stop|
                                  # noreturn|fragret (program.py)
        self.insns = []
        self.target = None        # tail-call target / indirect target
        self.table = None         # switch: (index EA, [targets])
        self.preds = []


class Function(object):
    def __init__(self, entry):
        self.entry = entry
        self.blocks = {}
        self.order = []
        self.asm_only = False
        self.asm_reasons = []
        self.states = {}          # insn addr -> abstract state before it
        self.callers = set()
        self.how = "call"         # how the entry was found
        self.ptr_refs = 0


def _is_nop_jump(i):
    # jmpt.t/.f .+4 and jmpf are pipeline padding with no effect on control
    return i.mn == "jmp" and (i.cc == 0 or
                              (i.cc == 13 and i.target == i.addr + 4))


class Analyzer(object):
    def __init__(self, img):
        self.img = img
        self.entries = {}
        self.funcs = {}

    # -- function body discovery ---------------------------------------------
    def leaders(self, entry, known_entries, extra=()):
        """Explore from entry; returns (block starts, problems)."""
        img = self.img
        seen = set(); work = [entry] + list(extra)
        starts = {entry} | set(extra); problems = []
        while work:
            a = work.pop()
            while True:
                if a in seen: break
                if not img.in_code(a):
                    problems.append("flows outside code at 0x%x" % a); break
                seen.add(a)
                i = img.insn(a)
                if not i.valid:
                    problems.append("invalid instruction at 0x%x" % a); break
                if i.mn == "jmp" and not _is_nop_jump(i):
                    t = i.target
                    if t in known_entries and t != entry:
                        if i.cc == 13: break         # tail call
                        a += 4; starts.add(a); continue
                    if not img.in_code(t):
                        problems.append("jump outside code at 0x%x" % a)
                        break
                    if t not in starts:
                        starts.add(t); work.append(t)
                    if i.cc == 13: break
                    a += 4; starts.add(a); continue
                if i.mn == "ret" or i.mn == "calli" and \
                        (i.link == i.s1.a or (i.link != 5)):
                    break
                if i.mn == "call" and (a + 4) in known_entries:
                    break                            # call never returns
                a += 4
            # loop
        return starts, seen, problems

    def build(self, entry, known_entries):
        extra = set()
        for _ in range(8):
            f = self._build(entry, known_entries, extra)
            new = getattr(f, "pending", set()) - extra
            if not new:
                return f
            extra |= new
        return f

    def _build(self, entry, known_entries, extra):
        img = self.img
        f = Function(entry)
        starts, seen, problems = self.leaders(entry, known_entries, extra)
        f.asm_reasons.extend(problems)
        # split into blocks
        for s in sorted(starts):
            if s not in seen: continue
            b = Block(s)
            a = s
            while True:
                i = img.insn(a)
                if not i.valid or a not in seen:
                    b.kind = "stop"; break
                b.insns.append(i)
                a += 4
                if i.mn == "jmp" and not _is_nop_jump(i):
                    if i.cc == 13:
                        if i.target in known_entries and i.target != entry:
                            b.kind = "tail"; b.target = i.target
                        else:
                            b.kind = "jmp"; b.succs = [i.target]
                    else:
                        b.kind = "cjmp"
                        b.succs = [i.target, a]
                    break
                if i.mn == "ret" or i.mn == "calli" and \
                        (i.link == i.s1.a or i.link != 5):
                    b.kind = "ijmp"
                    break
                if i.mn == "call" and a in known_entries:
                    b.kind = "noreturn"; break
                if a in starts:
                    b.succs = [a]; break
            b.end = a
            f.blocks[s] = b
        for b in f.blocks.values():
            b.succs = [s for s in b.succs if s in f.blocks]
            for s in b.succs:
                f.blocks[s].preds.append(b.start)
        f.order = sorted(f.blocks)
        self.interpret(f)
        for b in f.blocks.values():
            b.succs = [s for s in b.succs if s in f.blocks]
            b.preds = []
        for b in f.blocks.values():
            for s in b.succs:
                f.blocks[s].preds.append(b.start)
        return f

    # -- abstract interpretation ---------------------------------------------
    def interpret(self, f):
        img = self.img
        init = {"r": {SP: ("sp", 0), A5: ("ra",), 0x80: _c(0)}, "m": {}}
        instate = {f.entry: init}
        work = [f.entry]
        iters = 0
        while work and iters < 20000:
            iters += 1
            s = work.pop()
            b = f.blocks[s]
            st = {"r": dict(instate[s]["r"]), "m": dict(instate[s]["m"])}
            for i in b.insns:
                f.states[i.addr] = st
                st = self.step(i, st)
            # resolve the block-ending indirect jump
            if b.kind == "ijmp":
                self.classify_ijmp(f, b)
                for t in b.succs:
                    if t not in f.blocks:
                        pass
            for t in b.succs:
                if t not in f.blocks: continue
                old = instate.get(t)
                if old is None:
                    instate[t] = st; work.append(t)
                else:
                    j = _join(old, st)
                    if j != old:
                        instate[t] = j; work.append(t)
        f.instate = instate

    def step(self, i, st):
        r = dict(st["r"]); m = dict(st["m"])
        mn = i.mn

        def addr_of(ea):
            if ea.kind == "mem":
                return _add(r.get(0x80 + ea.a * 4), ea.b)
            if ea.kind == "post":
                return r.get(0x80 + ea.a * 4)
            if ea.kind == "pre":
                return _add(r.get(0x80 + ea.a * 4), ea.b)
            if ea.kind == "idx":
                base = r.get(0x80 + ea.a * 4)
                idx = r.get(ea.b * 4)
                if base and idx and base[0] == "c" and idx[0] == "c":
                    return _c(base[1] + idx[1] * ea.c)
                return None
            return None

        def side(ea):
            if ea is not None and ea.kind in ("post", "pre"):
                k = 0x80 + ea.a * 4
                r[k] = _add(r.get(k), ea.b)

        def read(ea, size):
            if ea.kind == "imm": return _c(ea.a)
            if ea.kind == "reg":
                return r.get(ea.a)
            a = addr_of(ea)
            if a is not None and a[0] == "sp" and size == 4:
                return m.get(a[1])
            if ea.kind == "idx" and size == 4:
                base = r.get(0x80 + ea.a * 4)
                if base and base[0] == "c":
                    return ("tbl", base[1], ea.b, ea.c)
            return None

        def write(ea, val, size):
            if ea is None: return
            if ea.kind == "reg":
                r[ea.a] = val if size == 4 else None
                return
            if ea.kind == "acc": return
            a = addr_of(ea)
            if a is not None and a[0] == "sp":
                if size == 4:
                    m[a[1]] = val
                else:
                    m.pop(a[1] & ~3, None)

        if mn in ("call",) or (mn == "calli" and i.link == 5
                                and i.s1.a != 5):
            for k in list(r):
                if k not in CALLEE_SAVED:
                    del r[k]
            r[0x80] = _c(0)
            return {"r": r, "m": m}
        if mn == "moveai":
            r[i.dst.a] = _c(i.imm); return {"r": r, "m": m}
        if mn == "movei":
            if i.dst.kind == "reg":
                r[i.dst.a] = _c(i.imm)
            else:
                write(i.dst, None, 2); side(i.dst)
            return {"r": r, "m": m}
        if mn in ("jmp", "cmpi", "btst", "ret", "suspend", "bkpt",
                  "setcsr", "iread"):
            if i.s1 is not None:
                side(i.s1)
            return {"r": r, "m": m}
        if mn.startswith("lea") or mn == "pdec":
            v = addr_of(i.s1) if mn != "pdec" else \
                _add(r.get(0x80 + i.s1.a * 4), i.s1.b)
            if i.s1.kind == "idx":
                base = r.get(0x80 + i.s1.a * 4)
                idx = r.get(i.s1.b * 4)
                if base == _c(0):
                    v = idx if i.s1.c == 1 else (
                        _c(idx[1] * i.s1.c) if idx and idx[0] == "c"
                        else None)
            side(i.s1)
            write(i.dst, v, 4); side(i.dst)
            return {"r": r, "m": m}
        if mn in ("move.4", "movea"):
            v = read(i.s1, 4); side(i.s1)
            write(i.dst, v, 4); side(i.dst)
            return {"r": r, "m": m}
        if mn in ("add.4", "sub.4") and i.dst is not None:
            a = read(i.s1, 4); side(i.s1)
            b = r.get(i.s2.a)
            v = None
            if a is not None and b is not None:
                if a[0] == "c" and b[0] == "c":
                    v = _c(a[1] + b[1] if mn == "add.4" else a[1] - b[1])
                elif mn == "add.4" and a[0] == "c" and b[0] == "sp":
                    v = ("sp", b[1] + a[1])
                elif a[0] == "sp" and b[0] == "c":
                    v = ("sp", a[1] + b[1] if mn == "add.4" else a[1] - b[1])
            write(i.dst, v, 4); side(i.dst)
            return {"r": r, "m": m}
        if mn == "shmrg.2":
            a = read(i.s1, 4); b = read(i.s2, 4) if i.s2.kind == "reg" \
                else _c(i.s2.a)
            side(i.s1)
            v = None
            if a and b and a[0] == "c" and b[0] == "c":
                v = _c((b[1] << 16) | (a[1] & 0xffff))
            write(i.dst, v, 4)
            return {"r": r, "m": m}
        # generic: sources may side-effect, destination becomes unknown
        if i.s1 is not None: side(i.s1)
        if i.dst is not None:
            write(i.dst, None, i.size or 4); side(i.dst)
        return {"r": r, "m": m}

    def classify_ijmp(self, f, b):
        i = b.insns[-1]
        st = f.states[i.addr]
        r = st["r"]
        if i.mn == "ret":
            ea = i.s1
            if ea.kind == "reg":
                v = r.get(ea.a)
            elif ea.kind in ("post", "mem") and ea.a == 7:
                sp = r.get(SP)
                v = None
                if sp and sp[0] == "sp":
                    off = sp[1] + (ea.b if ea.kind == "mem" else 0)
                    v = st["m"].get(off)
            else:
                v = None
            if v == ("ra",):
                b.kind = "ret"; return
            b.kind = "ijmp"
            return
        # calli
        base = r.get(0x80 + i.s1.a * 4)
        if base == ("ra",) and i.s1.b == 0:
            b.kind = "ret"; return
        if base and base[0] == "tbl":
            tb, idxreg, scale = base[1], base[2], base[3]
            n = self.table_bound(f, b, idxreg)
            targets = []
            heuristic = n is None
            limit = n if n is not None else 512
            for k in range(limit):
                a = tb + k * scale
                if not self.img.in_image(a): break
                t = self.img.word(a)
                if not self.img.in_code(t):
                    break
                if heuristic and k > 0 and not self._near(f, t):
                    break
                targets.append(t)
            if targets:
                b.kind = "switch"
                b.table = (idxreg, targets, tb, heuristic)
                new = [t for t in targets if t not in f.blocks]
                b.succs = list(dict.fromkeys(targets))
                if new:
                    f.pending = getattr(f, "pending", set()) | set(new)
                return
        b.kind = "ijmp"

    def _near(self, f, t):
        return abs(t - f.entry) < 0x20000

    def table_bound(self, f, b, idxreg):
        """Find how many entries a jump table has from the guard compare.

        Walks back over the block and its single predecessors looking for
        the conditional branch that guards the table and evaluates its
        condition for idx = 0, 1, 2, ... ."""
        cur = b
        path_cond = None
        seen = 0
        while seen < 4:
            seen += 1
            if len(cur.preds) != 1:
                return None
            p = f.blocks[cur.preds[0]]
            if p.kind != "cjmp":
                cur = p; continue
            j = p.insns[-1]
            taken = (j.target == cur.start)
            # find the flag setter
            for k in range(len(p.insns) - 2, -1, -1):
                s = p.insns[k]
                if s.mn == "cmpi" and s.s1 == _dreg_ea(idxreg):
                    return _count(lambda x: _cond(j.cc, x, s.imm, j.wide)
                                  == taken)
                if s.mn == "sub.4" and s.dst.kind == "imm":
                    if s.s2.a == idxreg * 4 and s.s1.kind == "imm":
                        kk = s.s1.a
                        return _count(lambda x: _cond(j.cc, kk, x, j.wide)
                                      == taken)
                    if s.s1 == _dreg_ea(idxreg) and s.s2.kind == "reg":
                        return None
                if s.mn not in ("move.4", "movei", "moveai", "lea.1",
                                "lea.4", "lea.2", "move.2", "move.1"):
                    break
            return None
        return None


def _dreg_ea(n):
    from .decode import EA
    return EA("reg", n * 4)


def _count(pred):
    n = 0
    while n < 4096 and pred(n):
        n += 1
    return n if 0 < n < 4096 else None


def _cond(cc, a, b, wide):
    """Evaluate a Ubicom32 condition after computing a - b."""
    bits = 32 if wide else 16
    m = (1 << bits) - 1
    a &= m; b &= m
    r = (a - b) & m
    n = r >> (bits - 1)
    z = r == 0
    c = a >= b
    sa = a >> (bits - 1); sb = b >> (bits - 1)
    v = (sa != sb) and (n != sa)
    name = CC[cc]
    return {"f": False, "t": True, "eq": z, "ne": not z, "cs": c,
            "cc": not c, "mi": n == 1, "pl": n == 0, "vs": v, "vc": not v,
            "hi": c and not z, "ls": (not c) or z, "ge": n == v,
            "lt": n != v, "gt": (not z) and n == v,
            "le": z or n != v}[name]


def _join(a, b):
    r = {}
    for k, v in a["r"].items():
        if b["r"].get(k) == v:
            r[k] = v
    m = {}
    for k, v in a["m"].items():
        if b["m"].get(k) == v:
            m[k] = v
    # keep the sp lattice finite: an sp that disagrees becomes unknown
    return {"r": r, "m": m}
