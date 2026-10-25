#!/usr/bin/env python3
"""
One-time ISA-fact extractor for the Ubicom32 instruction set.

This reads the PUBLIC, GPL Ubicom32 binutils CGEN description (opcodes/
ubicom32-opc.c + ubicom32-desc.c) purely as a *reference for the factual
instruction encoding* (opcode values, field masks, mnemonics, operand
syntax). It emits those facts as a C data table (ubicom32_isa.inc) in our
own schema. No third-party *code* is copied or vendored: the disassembler
engine (ubidis.c) is written from scratch and only consumes this fact table.

The reference files are intentionally NOT committed to this repo. To
regenerate, fetch them yourself from the public binutils-for-ubicom32 port
(e.g. the OpenWrt 600-ubicom32_binutils patch) and point REFDIR at them.

Usage:  python3 gen_isa_table.py <refdir> <out.inc>
"""
import re, sys, json, os

refdir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ubiref"
outpath = sys.argv[2] if len(sys.argv) > 2 else "src/ubicom32_isa.inc"

opc = open(os.path.join(refdir, "ubicom32-opc.c")).read()
desc = open(os.path.join(refdir, "ubicom32-desc.c")).read()

# ---- operand bit-field table: name -> (hw, start, len) ----
opmap = {}
for m in re.finditer(r'\{ "([^"]+)", UBICOM32_OPERAND_\w+, (HW_\w+), (\d+), (\d+),', desc):
    opmap[m.group(1)] = (m.group(2), int(m.group(3)), int(m.group(4)))

# ---- ifmt -> mask ----
ifmt_mask = {}
for m in re.finditer(
    r'static const CGEN_IFMT (ifmt_\w+) ATTRIBUTE_UNUSED = \{\s*\n\s*(\d+),\s*(\d+),\s*(0x[0-9a-fA-F]+),',
    opc):
    ifmt_mask[m.group(1)] = int(m.group(4), 16)

# ---- insn table: human-comment + syntax token list + ifmt + base ----
pat = re.compile(
    r'/\* (.*?) \*/\s*\n\s*\{\s*\n\s*\{[^}]*\},\s*\n\s*\{ \{ (.*?) \} \},\s*\n'
    r'\s*& (ifmt_\w+), \{ (0x[0-9a-fA-F]+) \}\s*\n\s*\},', re.S)

# Map CGEN OP() macro name -> operand-table name.
def macro_to_opname(mac):
    specials = {"AN": "An", "AM": "Am", "DN": "Dn", "S2": "s2", "PC": "pc",
                "CC": "cc", "C": "C", "P": "P", "BIT5": "bit5",
                "INTERRUPT": "interrupt", "IMM24": "imm24",
                "IMM16_1": "imm16-1", "IMM16_2": "imm16-2",
                "OFFSET21": "offset21", "OFFSET24": "offset24",
                "OFFSET16": "offset16"}
    if mac in specials:
        return specials[mac]
    s = mac.lower().replace("_", "-")
    # S1_AN -> s1-an ; table uses s1-An. Fix the An/Am capital.
    s = s.replace("-an", "-An").replace("-am", "-Am")
    return s

# Renderer kinds understood by ubidis.c
# emit per-operand: (kind, p0, p1)  -- see ubidis.c
def operand_render(opname):
    # Flags/condition suffixes attached to the mnemonic.
    if opname == "cc":
        return ("K_CC", 26, 4)
    if opname == "C":
        return ("K_CFLAG", 21, 1)
    if opname == "P":
        return ("K_PFLAG", 22, 1)
    if opname in ("dsp-destA", "dsp-desta"):
        return ("K_ACC", 16, 1)
    if opname in ("dsp-s2-acc-reg-mul", "dsp-s2-acc-reg-addsub"):
        return ("K_ACC", 14, 4)
    if opname in ("dsp-s2-data-reg", "dsp-s2-data-reg-addsub"):
        return ("K_DREG", 14, 4)
    if opname in ("dsp-c", "dsp-t", "dsp-t-addsub", "dsp-s2-sel"):
        return ("K_SKIP", 0, 0)
    if opname not in opmap:
        return None
    hw, start, length = opmap[opname]
    if hw == "HW_H_AR":
        return ("K_AREG", start, length)
    if hw in ("HW_H_DR", "HW_H_S1_DR"):
        return ("K_DREG", start, length)
    if opname == "imm24":
        return ("K_IMM24", 0, 0)
    if opname in ("offset21",):
        return ("K_OFF21", 0, 0)
    if opname in ("offset24",):
        return ("K_OFF24", 0, 0)
    if opname in ("offset16",):
        return ("K_OFF16", 0, 0)
    if opname.endswith("direct-addr"):
        return ("K_DIRECT", start, length)   # value = field << 2
    m = re.search(r'imm7-(\d)$', opname)
    if m:
        scale = int(m.group(1))
        s = {1: 0, 2: 1, 4: 2}[scale]
        # s1-imm7: t@[9:8] b@[4:0]; d-imm7: t@[25:24] b@[20:16]
        if opname.startswith("s1"):
            return ("K_IMM7", (9 << 8) | 2, (4 << 8) | 5 | (s << 16))
        else:
            return ("K_IMM7", (25 << 8) | 2, (20 << 8) | 5 | (s << 16))
    m = re.search(r'i4-(\d)$', opname)
    if m:
        s = {1: 0, 2: 1, 4: 2}[int(m.group(1))]
        return ("K_I4", start, (length) | (s << 8))
    if hw == "HW_H_SINT":
        return ("K_SIMM", start, length)
    if hw == "HW_H_UINT":
        return ("K_UIMM", start, length)
    return ("K_UIMM", start, length)

def mnem_of(human):
    h = re.sub(r'\$\{[^}]*\}', '', human)
    h = h.strip().split()
    return h[0] if h else '?'

entries = []
for m in pat.finditer(opc):
    human, syn, ifmt, base = m.group(1), m.group(2), m.group(3), int(m.group(4), 16)
    mnem = mnem_of(human)
    if mnem.startswith("__") or mnem in ("Formats", "Instruction", "?"):
        continue
    mask = ifmt_mask.get(ifmt)
    if mask is None:
        continue
    # Build a template from the syntax token list. Tokens are comma-separated
    # in the source, but a token can itself be a quoted comma ("','"), so we
    # scan for whole tokens rather than splitting on commas.
    tmpl = []          # list of ("mnem",m) / ("lit", str) / ("op", (kind,p0,p1))
    tok_re = re.compile(r"MNEM|OP \((\w+)\)|'(\\.|[^'])'")
    for tm in tok_re.finditer(syn):
        whole = tm.group(0)
        if whole == "MNEM":
            tmpl.append(("mnem", mnem)); continue
        if tm.group(1) is not None:            # OP (NAME)
            opn = macro_to_opname(tm.group(1))
            r = operand_render(opn)
            tmpl.append(("op", r) if r else ("lit", "?" + opn + "?"))
            continue
        c = tm.group(2)                        # quoted char literal
        if c == "\\'":
            c = "'"
        elif c.startswith("\\"):
            c = c[1:]
        tmpl.append(("lit", c))
    entries.append((base, mask, mnem, tmpl))

# Collapse encodings that the reference lists twice under different names.
# Only one entry per (base, mask) can ever match, so the other is dead and
# which one won used to depend on CGEN source order. Make the choice explicit:
# ALIAS_DROP maps a mnemonic to the mnemonic it yields to when both share a
# (base, mask) and the same operand template. `iwrite` is the IP3K-specific
# name for what is encoded exactly as `move.4` to an address; we render the
# generic `move.4`. Any other collision is an error, so a new one gets decided
# here rather than silently by sort order.
ALIAS_DROP = {"iwrite": "move.4"}
by_key = {}
for e in entries:
    by_key.setdefault((e[0], e[1]), []).append(e)
deduped = []
for e in entries:
    group = by_key[(e[0], e[1])]
    if len(group) == 1:
        deduped.append(e)
        continue
    keep = ALIAS_DROP.get(e[2])
    if keep is not None and any(o[2] == keep and o[3] == e[3] for o in group):
        continue                       # shadowed alias; the canonical stays
    others = [o for o in group if o is not e]
    if all(ALIAS_DROP.get(o[2]) == e[2] and o[3] == e[3] for o in others):
        deduped.append(e)
        continue
    sys.exit("unresolved (base,mask) collision 0x%08x/0x%08x: %s"
             % (e[0], e[1], ", ".join(o[2] for o in group)))
entries = deduped

# Sort by popcount(mask) descending so most-specific matches win; ties keep
# reference order (stable sort). After the dedup above no two entries share a
# (base, mask). Distinct entries of equal specificity can still both match one
# word (e.g. a src-mode and a dest-mode form of move.4); the earlier one in
# this table wins, in ubidis.py and ubidis.c alike.
def popcount(x): return bin(x).count("1")
entries.sort(key=lambda e: -popcount(e[1]))

# ---- emit C ----
def c_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')

out = []
out.append("/* Auto-generated Ubicom32 ISA fact table. Schema & engine: ours.")
out.append("   Encoding facts derived from the public GPL ubicom32 binutils CGEN")
out.append("   description (read as reference, not vendored). Do not edit by hand. */")
out.append("/* Each insn: base, mask, mnemonic, and a template of render ops.   */")
out.append("")
out.append("static const struct insn_tmpl ubicom32_insns[] = {")
for base, mask, mnem, tmpl in entries:
    parts = []
    for kind, val in tmpl:
        if kind == "mnem":
            parts.append('{T_MNEM,0,0,0,""}')
        elif kind == "lit":
            parts.append('{T_LIT,0,0,0,"%s"}' % c_escape(val))
        else:
            k, p0, p1 = val
            parts.append('{T_OP,%s,%d,%d,""}' % (k, p0, p1))
    parts.append('{T_END,0,0,0,""}')
    out.append('  {0x%08xU,0x%08xU,"%s",{%s}},' % (base, mask, mnem, ",".join(parts)))
out.append("};")
out.append("#define UBICOM32_NINSNS (sizeof(ubicom32_insns)/sizeof(ubicom32_insns[0]))")
out.append("")

os.makedirs(os.path.dirname(outpath), exist_ok=True) if os.path.dirname(outpath) else None
open(outpath, "w").write("\n".join(out) + "\n")
print("wrote %s with %d insn encodings" % (outpath, len(entries)))

# ---- also emit a Python fact table (same facts, Python literal) ----
pyout = []
pyout.append("# Auto-generated Ubicom32 ISA fact table (shared with ubicom32_isa.inc).")
pyout.append("# Encoding facts derived from the public GPL ubicom32 binutils CGEN")
pyout.append("# description (read as reference, not vendored). Do not edit by hand.")
pyout.append("# Each entry: (base, mask, mnemonic, [tokens]).  Token forms:")
pyout.append("#   ('m',)            mnemonic")
pyout.append("#   ('l', text)       literal text")
pyout.append("#   ('o', kind, p0, p1)   operand (kind is a K_* string)")
pyout.append("INSNS = [")
for base, mask, mnem, tmpl in entries:
    toks = []
    for kind, val in tmpl:
        if kind == "mnem":
            toks.append("('m',)")
        elif kind == "lit":
            toks.append("('l',%r)" % val)
        else:
            k, p0, p1 = val
            toks.append("('o',%r,%d,%d)" % (k, p0, p1))
    pyout.append("  (0x%08x,0x%08x,%r,[%s])," % (base, mask, mnem, ",".join(toks)))
pyout.append("]")
pypath = os.path.join(os.path.dirname(outpath) or ".", "ubicom32_isa.py")
open(pypath, "w").write("\n".join(pyout) + "\n")
print("wrote %s (python table)" % pypath)
# report max template length
maxt = max(len(t) for _, _, _, t in entries)
print("max template ops:", maxt + 1)
