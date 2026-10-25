"""
Structured Ubicom32 instruction decoder (from scratch).

Unlike tools/ubidis.py, which renders text from the ISA fact table, this
decoder returns *operands as data* so that the lifter can give every
instruction a meaning. The opcode numbering agrees with src/ubicom32_isa.py
(tests/run_tests.py checks the two against each other over the whole code
region); the operand semantics below were pinned down against the firmware
itself -- see docs/DECOMPILER.md, "Semantics and how each was established".

Encoding summary (bit numbers are lsb-0 of the 32-bit big-endian word):

  31..27 op1      26..16 dest EA (11 bits)   15..11 op2   10..0 source-1 EA

An 11-bit effective-address field `f` is:
  f[10:8] = 000  #imm8 (signed)
          = 001  direct: register-file address f[7:0] << 2  (d0 = 0x00 ...)
          = 010  (An)N++ / N(An)++  An=f[7:5], pre=f[4], N=sext4(f[3:0])*size
          = 011  (An,Dr)            An=f[7:5], Dr=f[3:0], scaled by size
          = 1xx  off(An)            off = (f[9:8]<<5 | f[4:0]) * size
"""

# ---- register file (direct addresses) --------------------------------------
REGNAMES = {}
for _i in range(16):
    REGNAMES[_i * 4] = "d%d" % _i
for _i in range(8):
    REGNAMES[0x80 + _i * 4] = "a%d" % _i if _i != 7 else "sp"
REGNAMES.update({
    0xa0: "acc0_hi", 0xa4: "acc0_lo", 0xa8: "mac_rc16", 0xac: "source3",
    0xb0: "inst_cnt", 0xb4: "csr", 0xb8: "rosr", 0xbc: "iread_data",
    0xc0: "int_mask0", 0xc4: "int_mask1",
    0xd0: "pc",
    0x100: "chip_id", 0x104: "int_stat0", 0x108: "int_stat1",
    0x114: "int_set0", 0x118: "int_set1", 0x124: "int_clr0",
    0x128: "int_clr1", 0x134: "global_ctrl",
})

CC = ["f", "cc", "cs", "eq", "ge", "gt", "hi", "le", "ls", "lt", "mi", "ne",
      "pl", "t", "vc", "vs"]

# op1 == 0 secondary opcodes (bits 15..11)
OP0 = {1: "suspend", 4: "ret", 5: "ierase", 6: "iread", 7: "bkpt",
       10: "not.4", 11: "not.2", 12: "move.4", 13: "move.2", 14: "movea",
       15: "move.1", 16: "iwrite", 18: "setcsr", 21: "ext.2", 23: "ext.1",
       24: "swapb.2", 25: "swapb.4", 26: "pxcnv", 27: "pxcnv.t",
       28: "lea.4", 29: "lea.2", 30: "pdec", 31: "lea.1"}
# op1 == 2 secondary opcodes (bits 25..21)
OP2 = {0: "pxhi", 1: "muls", 2: "pxhi.s", 3: "mulu", 5: "mulf", 6: "btst",
       8: "crcgen", 9: "mac", 10: "lsl.1", 11: "lsr.1", 12: "asr.1",
       16: "lsl.4", 17: "lsl.2", 18: "lsr.4", 19: "lsr.2", 20: "asr.4",
       21: "asr.2", 22: "bfextu", 24: "bfrvrs", 26: "shftd", 28: "merge",
       30: "shmrg.2", 31: "shmrg.1"}
# op1 == 6 (DSP) secondary opcodes (bits 25..21)
OP6 = {0: "muls", 1: "macs", 2: "mulu", 3: "macu", 4: "mulf", 5: "macf",
       7: "macus", 8: "muls.4", 9: "msuf", 10: "mulu.4", 16: "madd.4",
       17: "madd.2", 18: "msub.4", 19: "msub.2"}
# three-operand ALU ops: op1 -> (mnemonic stem, size or None = bit15 picks .1/.2)
ALU3 = {8: ("and", None), 9: ("and", 4), 10: ("or", None), 11: ("or", 4),
        12: ("xor", None), 13: ("xor", 4), 14: ("add", None), 15: ("add", 4),
        16: ("addc", 4), 17: ("sub", None), 18: ("sub", 4), 19: ("subc", 4),
        20: ("pxblend", None), 21: ("pxvi", None), 22: ("pxadds", None)}


def sext(v, n):
    m = 1 << (n - 1)
    return ((v & ((1 << n) - 1)) ^ m) - m


class EA(object):
    """An operand. kind is one of:
       imm  (v)               immediate value
       reg  (r)               register-file direct address
       mem  (an, disp)        memory at An + disp
       idx  (an, dn, scale)   memory at An + Dn*scale
       post (an, inc)         memory at An, then An += inc
       pre  (an, inc)         An += inc, then memory at An
    """
    __slots__ = ("kind", "a", "b", "c")

    def __init__(self, kind, a=0, b=0, c=0):
        self.kind = kind; self.a = a; self.b = b; self.c = c

    def is_mem(self):
        return self.kind in ("mem", "idx", "post", "pre")

    def __repr__(self):
        k = self.kind
        if k == "imm": return "#%d" % self.a
        if k == "reg": return REGNAMES.get(self.a, "r%03x" % self.a)
        an = "sp" if self.a == 7 else "a%d" % self.a
        if k == "mem": return "%d(%s)" % (self.b, an)
        if k == "idx": return "(%s,d%d)" % (an, self.b)
        if k == "post": return "(%s)%d++" % (an, self.b)
        if k == "pre": return "%d(%s)++" % (self.b, an)
        return "?"

    def __eq__(self, o):
        return isinstance(o, EA) and (self.kind, self.a, self.b, self.c) == \
            (o.kind, o.a, o.b, o.c)

    def __hash__(self):
        return hash((self.kind, self.a, self.b, self.c))


def dreg(n):
    return EA("reg", n * 4)


def areg(n):
    return EA("reg", 0x80 + n * 4)


def decode_ea(f, size):
    mode = (f >> 8) & 7
    if mode == 0:
        return EA("imm", sext(f & 0xff, 8))
    if mode == 1:
        return EA("reg", (f & 0xff) << 2)
    an = (f >> 5) & 7
    if mode == 2:
        inc = sext(f & 0xf, 4) * size
        return EA("pre" if f & 0x10 else "post", an, inc)
    if mode == 3:
        return EA("idx", an, f & 0xf, size)
    off = (((f >> 8) & 3) << 5) | (f & 0x1f)
    return EA("mem", an, off * size)


class Insn(object):
    __slots__ = ("addr", "word", "mn", "size", "dst", "s1", "s2", "imm",
                 "cc", "wide", "target", "link", "valid")

    def __init__(self, addr, word):
        self.addr = addr; self.word = word; self.mn = None; self.size = None
        self.dst = None; self.s1 = None; self.s2 = None; self.imm = None
        self.cc = None; self.wide = True; self.target = None; self.link = None
        self.valid = True

    def __repr__(self):
        ops = [repr(x) for x in (self.dst, self.s1, self.s2) if x is not None]
        if self.imm is not None: ops.append("#%d" % self.imm)
        if self.target is not None: ops.append("0x%x" % self.target)
        mn = self.mn
        if mn == "jmp": mn = "jmp" + CC[self.cc] + ("" if self.wide else ".s")
        return "%08x  %s %s" % (self.addr, mn, ",".join(ops))


def decode(word, addr):
    """Decode one 32-bit word at virtual address addr."""
    i = Insn(addr, word)
    op = word >> 27
    dfld = (word >> 16) & 0x7ff
    sfld = word & 0x7ff
    if op == 0:
        sub = (word >> 11) & 0x1f
        mn = OP0.get(sub)
        if mn is None:
            i.valid = False; i.mn = ".word"; return i
        i.mn = mn
        if mn in ("suspend",):
            return i
        if mn in ("ret", "bkpt", "iread", "setcsr"):
            i.size = 4; i.s1 = decode_ea(sfld, 4); return i
        if mn == "pdec":
            i.size = 4
            i.dst = decode_ea(dfld, 4)
            off = (((sfld >> 8) & 3) << 5) | (sfld & 0x1f)
            i.s1 = EA("mem", (sfld >> 5) & 7, (off - 128) * 4)
            if (sfld >> 10) & 1 == 0:
                i.valid = False
            return i
        size = {"move.4": 4, "move.2": 2, "move.1": 1, "movea": 4,
                "not.4": 4, "not.2": 2, "ext.2": 2, "ext.1": 1,
                "swapb.2": 2, "swapb.4": 4, "lea.4": 4, "lea.2": 2,
                "lea.1": 1, "iwrite": 4, "ierase": 4, "pxcnv": 4,
                "pxcnv.t": 4}[mn]
        i.size = size
        # ext/lea/movea always write a 32-bit destination
        dsize = 4 if mn.startswith(("ext", "lea", "movea")) else size
        i.dst = decode_ea(dfld, dsize)
        i.s1 = decode_ea(sfld, size)
        return i
    if op in (2, 6):
        sub = (word >> 21) & 0x1f
        mn = (OP2 if op == 2 else OP6).get(sub)
        if mn is None:
            i.valid = False; i.mn = ".word"; return i
        i.mn = mn
        if mn.endswith(".1"): ssize = 1
        elif mn.endswith(".2") or mn in ("muls", "mulu", "mulf", "mac",
                                          "macs", "macu", "macf", "macus",
                                          "msuf", "crcgen", "btst", "pxhi",
                                          "pxhi.s"):
            ssize = 2
        else:
            ssize = 4
        if mn == "btst": ssize = 4
        if mn == "crcgen": ssize = 1
        i.size = ssize
        i.s1 = decode_ea(sfld, ssize)
        if (word >> 26) & 1:
            i.s2 = dreg((word >> 11) & 0xf)
        else:
            i.s2 = EA("imm", (word >> 11) & 0x1f)
        if op == 2 and mn not in ("btst", "muls", "mulu", "mulf", "mac",
                                  "crcgen"):
            i.dst = dreg((word >> 16) & 0x1f)
            if (word >> 16) & 0x10:
                i.valid = False
        elif op == 6 or mn in ("muls", "mulu", "mulf", "mac"):
            i.dst = EA("acc", (word >> 16) & 1)
        return i
    if op in (4, 5):
        i.mn = "bset" if op == 4 else "bclr"
        i.size = 4
        i.dst = decode_ea(dfld, 4); i.s1 = decode_ea(sfld, 4)
        i.imm = (word >> 11) & 0x1f
        return i
    if op in ALU3:
        stem, size = ALU3[op]
        if size is None:
            size = 1 if (word >> 15) & 1 else 2
        i.mn = stem if stem in ("addc", "subc") else "%s.%d" % (stem, size)
        i.size = size
        i.dst = decode_ea(dfld, size); i.s1 = decode_ea(sfld, size)
        i.s2 = dreg((word >> 11) & 0xf)
        if stem.startswith("px"):
            i.valid = False
        return i
    if op == 0x18:
        i.mn = "cmpi"; i.size = 2
        i.s1 = decode_ea(sfld, 2)
        i.imm = sext((word >> 11) & 0xffff, 16)
        return i
    if op == 0x19:
        i.mn = "movei"; i.size = 2
        i.dst = decode_ea(dfld, 2)
        i.imm = sext(word & 0xffff, 16)
        return i
    if op == 0x1a:
        i.mn = "jmp"
        i.cc = (word >> 23) & 0xf
        i.wide = bool((word >> 21) & 1)
        off = sext(word & 0x1fffff, 21)
        i.target = (addr + (off << 2)) & 0xffffffff
        return i
    if op == 0x1b:
        i.mn = "call"
        i.link = (word >> 21) & 7
        v = (sext((word >> 24) & 7, 3) << 21) | (word & 0x1fffff)
        i.target = (addr + (v << 2)) & 0xffffffff
        return i
    if op == 0x1c:
        i.mn = "moveai"
        i.dst = areg((word >> 21) & 7)
        v = (((word >> 24) & 7) << 21) | (word & 0x1fffff)
        i.imm = (v << 7) & 0xffffffff
        return i
    if op == 0x1e:
        i.mn = "calli"
        i.link = (word >> 21) & 7
        o = (sext((word >> 24) & 7, 3) << 13) | (((word >> 16) & 0x1f) << 8) \
            | (((word >> 8) & 7) << 5) | (word & 0x1f)
        i.s1 = EA("mem", (word >> 5) & 7, o << 2)
        return i
    i.valid = False; i.mn = ".word"
    return i


def decode_image(data, base, start=0, end=None):
    """Decode every aligned word of data[start:end]; returns {addr: Insn}."""
    if end is None: end = len(data)
    out = {}
    for off in range(start, end - 3, 4):
        w = int.from_bytes(data[off:off + 4], "big")
        a = base + off
        out[a] = decode(w, a)
    return out
