#!/usr/bin/env python3
"""
Checks for the toolchain project (run with `make -C toolchain test`; the
repo's top-level `make test` runs them too).

Always:
 1. sources.txt pins every download by a SHA-256 and https URLs; the shell
    scripts parse.
 2. dir655patch.py on synthetic ELF files: the generated linker script places
    `.at.0xADDR` sections at ADDR and everything else in the gap; `apply`
    writes sections at the right offsets, zero-fills .bss, extends the image,
    and refuses overlaps, sections outside the allowed regions, constructor
    sections and an image whose gap is already used.

Once `make toolchain` has installed ubicom32-elf-gcc (or PREFIX points at
one; set REQUIRE_TOOLCHAIN=1 to fail instead of skipping):
 3. Every test case in tests/cases (C and C++) compiles with the DIR-655
    flags and links into the gap with libgcc.
 4. tools/ubidis.py disassembles all the linked code (the test cases and the
    libgcc routines they pull in) with every word known, and each
    instruction matches what objdump says the compiler emitted (same
    mnemonic, same registers, immediates and branch targets).
 5. No compiled instruction writes a0, and functions save only callee-saved
    registers the firmware also treats as callee-saved (a1, a2, a6) plus the
    return address a5.
 6. If out/img.bin exists: `make patch` builds examples/hook into
    out/img_patched.bin; the hook site disassembles to a jump to the
    compiled function, the gap holds the linked bytes, the rest of the image
    is unchanged, and repack.py + extract.py give the patched image back.
"""
import os, re, struct, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TC = os.path.dirname(HERE)
ROOT = os.path.dirname(TC)
sys.path.insert(0, os.path.join(TC, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dir655patch as dp  # noqa: E402

PY = sys.executable
FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   " + msg)
    else:
        print("  FAIL " + msg)
        FAILS.append(msg)


def expect_error(fn, needle, msg):
    try:
        fn()
    except dp.PatchError as e:
        check(needle in str(e), f"{msg} ({e})")
        return
    check(False, msg + " (no error raised)")


# ------------------------------------------------------------ synthetic ELF

def make_elf(sections, etype=2):
    """sections: [(name, type, flags, addr, data_or_size)] -> ELF32 BE bytes."""
    shstr = b"\0"
    names = []
    for s in sections + [(".shstrtab",)]:
        names.append(len(shstr))
        shstr += s[0].encode() + b"\0"
    body = b""
    hdrs = [bytes(40)]
    off = 52
    for (nm, typ, flags, addr, data), ni in zip(sections, names):
        size = data if isinstance(data, int) else len(data)
        if not isinstance(data, int):
            body += data
        hdrs.append(struct.pack(">10I", ni, typ, flags, addr, off, size, 0, 0, 4, 0))
        if not isinstance(data, int):
            off += len(data)
    hdrs.append(struct.pack(">10I", names[-1], 3, 0, 0, off, len(shstr), 0, 0, 1, 0))
    body += shstr
    off += len(shstr)
    shoff = off
    ehdr = b"\x7fELF\x01\x02\x01" + bytes(9)
    ehdr += struct.pack(">HHIIIIIHHHHHH", etype, 0xde3d, 1, 0, 0, shoff, 0,
                        52, 0, 0, 40, len(hdrs), len(hdrs) - 1)
    return ehdr + body + b"".join(hdrs)


def test_sources():
    print("sources and scripts")
    n = 0
    for line in open(os.path.join(TC, "sources.txt")):
        if line.startswith("#") or not line.strip():
            continue
        f = line.split()
        n += 1
        check(len(f) >= 3 and re.fullmatch(r"[0-9a-f]{64}", f[1]) and
              all(u.startswith("https://") for u in f[2:]), f"pinned: {f[0]}")
    check(n == 7, f"sources.txt lists 7 files ({n})")
    for s in ("fetch.sh", "build.sh"):
        r = subprocess.run(["sh", "-n", os.path.join(TC, "scripts", s)])
        check(r.returncode == 0, f"scripts/{s} parses")
    # Objects are named by basename: two sources with one name must not merge.
    dup = subprocess.run(["make", "-n", "-C", TC, "patch", "SRC=a/foo.c b/foo.cpp c/bar.c"],
                         capture_output=True, text=True)
    check(dup.returncode != 0 and "same name (foo)" in dup.stderr,
          "make patch refuses two SRC files with the same name"
          + (f" ({dup.stderr.strip()[-200:]})" if dup.stderr else " (no error)"))
    ok = subprocess.run(["make", "-n", "-C", TC, "clean", "SRC=a/foo.c b/bar.c"],
                        capture_output=True, text=True)
    check(ok.returncode == 0, "...and accepts distinct names" + (f" ({ok.stderr.strip()[-200:]})" if ok.returncode else ""))


def test_patcher():
    print("dir655patch.py")
    PB, NB, AX, WA = 1, 8, 6, 3
    with tempfile.TemporaryDirectory() as td:
        obj = os.path.join(td, "a.o")
        open(obj, "wb").write(make_elf([
            (".text", PB, AX, 0, b"\0" * 8),
            (".at.0x40000fc4", PB, AX, 0, b"\0" * 8),
            (".at.0x40000100", PB, AX, 0, b"\0" * 4),
        ], etype=1))
        ld = dp.ldscript([obj])
        check(".at.0x40000100 0x40000100 : { KEEP(*(.at.0x40000100)) }" in ld and
              ".at.0x40000fc4 0x40000fc4" in ld and
              ld.index(".at.0x40000100") < ld.index(".at.0x40000fc4"),
              "ldscript links each .at. section at its address, in order")
        check(f". = 0x{dp.GAP_START:08x};" in ld and f"<= 0x{dp.GAP_END:08x}" in ld,
              "ldscript starts the gap at the stock image end and asserts its limit")

    img = bytes(range(256)) * (dp.STOCK_SIZE // 256) + bytes(dp.STOCK_SIZE % 256)
    img = img[:dp.STOCK_SIZE]
    hook = b"\xe4\x60\xc8\x3a\xf0\x60\x00\x60"
    code = bytes(range(1, 21))
    elf = make_elf([
        (".at.0x40000fc4", PB, AX, 0x40000fc4, hook),
        (".text", PB, AX, dp.GAP_START, code),
        (".bss", NB, WA, dp.GAP_START + 20, 10),
        (".comment", PB, 0, 0, b"GCC"),
    ])
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "p.elf")
        open(p, "wb").write(elf)
        _, secs = dp.read_sections(p)
    out = dp.apply(img, secs, log=lambda *_: None)
    check(out[0xfc4:0xfcc] == hook, "hook bytes written at 0xfc4")
    check(out[dp.STOCK_SIZE:dp.STOCK_SIZE + 20] == code, "code written at the gap start")
    check(len(out) == dp.STOCK_SIZE + 32 and not any(out[dp.STOCK_SIZE + 20:]),
          "image extended over .bss with zeros, to a multiple of 4")
    check(out[:0xfc4] == img[:0xfc4] and out[0xfcc:dp.STOCK_SIZE] == img[0xfcc:],
          "nothing else in the stock image changed")
    expect_error(lambda: dp.apply(out, secs), "already holds data",
                 "refuses an image whose gap is used")
    check(dp.apply(out, secs, force=True, log=lambda *_: None) == out, "--force re-applies identically")

    def sec(nm, addr, size, typ=PB, flags=AX):
        return (nm, typ, flags, addr, None if typ == NB else bytes(size), size)

    expect_error(lambda: dp.plan(len(img), [sec(".text", dp.GAP_END - 4, 8)]),
                 "outside the free gap", "refuses code that runs past the .bss start")
    expect_error(lambda: dp.plan(len(img), [sec(".text", 0x40001000, 8)]),
                 "outside the free gap", "refuses an ordinary section linked over firmware")
    expect_error(lambda: dp.plan(len(img), [sec(".at.0x40001000", 0x40001000, 8),
                                            sec(".at.0x40001004", 0x40001004, 8)]),
                 "overlap", "refuses overlapping sections")
    expect_error(lambda: dp.plan(len(img), [sec(".at.0x40641cfc", 0x40641cfc, 8)]),
                 "outside the stock image", "refuses an .at. section past the stock image")
    expect_error(lambda: dp.plan(len(img), [sec(".at.0x40001000", 0x40001008, 8)]),
                 "linked at", "refuses an .at. section linked somewhere else")
    expect_error(lambda: dp.plan(len(img), [sec(".ctors", dp.GAP_START, 4)]),
                 "never run", "refuses global constructors")
    expect_error(lambda: dp.apply(img[:100], []), "expected the 1.21 img.bin",
                 "refuses an image that is not the 1.21 img.bin")


# ------------------------------------------------------------ with toolchain

def find_prefix():
    for p in (os.environ.get("PREFIX"), os.path.join(TC, "install")):
        if p and os.path.exists(os.path.join(p, "bin", "ubicom32-elf-gcc")):
            return p
    return None


def mk_vars(prefix):
    """DIR655_ARCH / DIR655_CFLAGS / DIR655_CXXFLAGS, read from dir655.mk via make."""
    mk = (f"include {os.path.join(TC, 'dir655.mk')}\n"
          "show:\n\t@echo '$(DIR655_ARCH)'\n\t@echo '$(DIR655_CFLAGS)'\n\t@echo '$(DIR655_CXXFLAGS)'\n")
    r = subprocess.run(["make", "-s", "-f", "-", "show", f"PREFIX={prefix}",
                        f"TOOLCHAIN_DIR={TC}/"], input=mk, capture_output=True, text=True, check=True)
    arch, cflags, cxxflags = r.stdout.strip().split("\n")
    return arch.split(), cflags.split(), cxxflags.split()


DIRECT_REGS = {0xa0: "mac_hi", 0xa4: "mac_lo", 0xdc: "acc1_lo"}
# Condition names binutils prints where ubidis prints the ARM-style ones.
CC_ALIAS = {"hs": "cs", "lo": "cc"}


def norm_ops(text):
    """Operand text -> list of comparable tokens."""
    # objdump writes relocated immediates as %hi(X) / %lo(X): X>>7 / X&0x7f.
    text = re.sub(r"%hi\((0x[0-9a-f]+)\)", lambda m: hex(int(m.group(1), 16) >> 7), text)
    text = re.sub(r"%lo\((0x[0-9a-f]+)\)", lambda m: hex(int(m.group(1), 16) & 0x7f), text)
    # objdump writes branch targets as bare hex followed by <symbol+off>.
    text = re.sub(r"\b([0-9a-f]+) <[^>]*>", r"0x\1", text)
    toks = re.findall(r"-?0x[0-9a-f]+|-?\d+|[a-z_][a-z_0-9]*|[^\s\w]", text)
    out = []
    for t in toks:
        if re.fullmatch(r"-?(0x[0-9a-f]+|\d+)", t):
            out.append(int(t, 0))
        else:
            out.append(t)
    return out


def direct_as_reg(tokens):
    """ubidis prints register operands in direct-address form (0x8 = d2)."""
    out = []
    for i, t in enumerate(tokens):
        prev = tokens[i - 1] if i else None
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if isinstance(t, int) and prev != "#" and nxt != "(" and prev != "(" and prev != "+":
            if 0 <= t <= 0x3c and t % 4 == 0:
                t = f"d{t // 4}"
            elif 0x80 <= t <= 0x9c and t % 4 == 0:
                t = "sp" if t == 0x9c else f"a{(t - 0x80) // 4}"
            elif t in DIRECT_REGS:
                t = DIRECT_REGS[t]
        out.append(t)
    return out


def objdump_insns(objdump, elf):
    out = subprocess.run([objdump, "-d", elf], capture_output=True, text=True, check=True).stdout
    insns = []
    for line in out.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} ){4})\s*(\S+)\s*(.*)$", line)
        if m:
            insns.append((int(m.group(1), 16), int(m.group(2).replace(" ", ""), 16),
                          m.group(3), m.group(4)))
    return insns


def compare_disasm(objdump, elf, label):
    import ubidis
    insns = objdump_insns(objdump, elf)
    bad, unknown = [], 0
    for addr, word, mn, ops in insns:
        text, _tgt, _call, known = ubidis.disasm_one(word, addr)
        if not known:
            unknown += 1
            bad.append(f"0x{addr:08x} {word:08x}: objdump '{mn} {ops}', ubidis unknown")
            continue
        umn, _, uops = text.partition(" ")
        a, b = norm_ops(ops), norm_ops(uops)
        # ubidis does not always print the accumulator a multiply writes
        # (docs/ISA.md), so compare multiplies without it.
        if mn.startswith(("mul", "mac", "msub")):
            a, b = [x[2:] if x[:2] in (["acc0", ","], ["acc1", ","]) else x for x in (a, b)]
        a, b = direct_as_reg(a), direct_as_reg(b)
        m = re.fullmatch(r"jmp([a-z]+)(.*)", mn)
        if m:
            mn = "jmp" + CC_ALIAS.get(m.group(1), m.group(1)) + m.group(2)
        if umn != mn or a != b:
            bad.append(f"0x{addr:08x} {word:08x}: objdump '{mn} {ops}' vs ubidis '{text}'")
    check(insns and not bad,
          f"{label}: ubidis.py agrees with objdump on all {len(insns)} instructions"
          + (f"; {len(bad)} differ:\n      " + "\n      ".join(bad[:20]) if bad else ""))
    return insns


WRITES_A0 = re.compile(r"^(a0\s*,|.*\(a0\)-?\d+\+\+|.*-?\d+\(a0\)\+\+)")
SAVE = re.compile(r"^-\d+\(sp\)\+\+\s*,\s*(\w+)$")


def check_abi(insns, label):
    a0 = [f"0x{a:08x} {mn} {ops}" for a, _w, mn, ops in insns
          if WRITES_A0.match(ops) and not mn.startswith(("cmp", "jmp", "btst"))]
    check(not a0, f"{label}: no instruction writes a0" + (f" ({a0[:3]})" if a0 else ""))
    saved = set()
    for _a, _w, mn, ops in insns:
        m = SAVE.match(ops.replace(" ", "")) if mn == "move.4" else None
        if m:
            saved.add(m.group(1))
    check(saved <= {"a1", "a2", "a6", "a5"},
          f"{label}: prologues save only a5 and callee-saved a1/a2/a6 ({sorted(saved)})")


def test_toolchain(prefix):
    print(f"toolchain in {prefix}")
    cross = os.path.join(prefix, "bin", "ubicom32-elf-")
    arch, cflags, cxxflags = mk_vars(prefix)
    cases = sorted(f for f in os.listdir(os.path.join(HERE, "cases")) if f.endswith((".c", ".cpp")))
    with tempfile.TemporaryDirectory() as td:
        objs = []
        for c in cases:
            src = os.path.join(HERE, "cases", c)
            o = os.path.join(td, c + ".o")
            cc, fl = (cross + "g++", cxxflags) if c.endswith(".cpp") else (cross + "gcc", cflags)
            r = subprocess.run([cc] + fl + ["-Werror", "-c", src, "-o", o], capture_output=True, text=True)
            check(r.returncode == 0, f"compiles {c}" + (f": {r.stderr.strip()[:300]}" if r.returncode else ""))
            if r.returncode == 0:
                objs.append(o)
        if len(objs) != len(cases):
            return
        syms = os.path.join(td, "syms.ld")
        open(syms, "w").write("fw_func = 0x40000fc4;\n")
        ld = os.path.join(td, "patch.ld")
        open(ld, "w").write(dp.ldscript(objs))
        elf = os.path.join(td, "all.elf")
        r = subprocess.run([cross + "gcc"] + arch + ["-nostdlib", "-nostartfiles", "-T", ld] + objs
                           + [syms, "-lgcc", "-o", elf], capture_output=True, text=True)
        check(r.returncode == 0, "links all cases + libgcc into the gap"
              + (f": {r.stderr.strip()[:400]}" if r.returncode else ""))
        if r.returncode:
            return
        nm = subprocess.run([cross + "nm", elf], capture_output=True, text=True).stdout
        check(" T __udivsi3" in nm and " T __muldi3" in nm, "libgcc division and 64-bit multiply linked")
        insns = compare_disasm(cross + "objdump", elf, "test cases + libgcc")
        check(len(insns) > 200, f"a meaningful amount of code compared ({len(insns)} instructions)")
        check_abi(insns, "test cases + libgcc")


def test_gap_unreferenced():
    """The free-gap facts in docs/FORMAT.md, re-derived from out/img.bin."""
    img_path = os.path.join(ROOT, "out", "img.bin")
    if not os.path.exists(img_path):
        print("  skip gap scan: out/img.bin missing (run `make extract`)")
        return
    print("free gap in out/img.bin")
    import ubidis
    img = open(img_path, "rb").read()
    check(dp.GAP_START == dp.BASE + len(img), f"stock image ends at 0x{dp.BASE + len(img):08x}")
    # moveai An,#imm24 sets An = imm24 << 7 (major opcode 0b11100). A pointer
    # into the gap is a moveai base inside it, or up to 8 KB below it plus a
    # lea/load offset.
    bases = {}
    for off in range(0, dp.CODE_END - dp.BASE, 4):
        w = struct.unpack_from(">I", img, off)[0]
        if w >> 27 != 0x1c:
            continue
        m = re.fullmatch(r"moveai a\d,#(0x[0-9a-f]+)", ubidis.disasm_one(w, dp.BASE + off)[0])
        if m:
            bases.setdefault(int(m.group(1), 16) << 7, []).append(dp.BASE + off)
    near = sorted(a for a in bases if dp.GAP_START - 0x2000 <= a < dp.GAP_END)
    check(not near, "no moveai base in or just below the gap"
          + (f" ({', '.join(f'0x{a:08x}' for a in near[:5])})" if near else ""))
    check(0x40000000 + 0x5c in bases.get(dp.GAP_END, []),
          f"the startup's moveai a1,#0x80ca00 at 0x4000005c starts .bss at 0x{dp.GAP_END:08x}"
          f" ({len(bases.get(dp.GAP_END, []))} moveai load that address)")
    # 32-bit data words: the only ones in range are text in a certificate.
    words = [off for off in range(0, len(img) - 3, 4)
             if dp.GAP_START <= struct.unpack_from(">I", img, off)[0] < dp.GAP_END]
    text = [o for o in words if img[o:o + 4].isascii() and img[o + 1:o + 4].isalpha()]
    check(words == text, f"no data word points into the gap ({len(words)} in range, all ASCII text: "
          + ", ".join(f"0x{o:x} {img[o:o + 4]!r}" for o in words[:3]) + ")")


def test_patch_image(prefix):
    img = os.path.join(ROOT, "out", "img.bin")
    if not os.path.exists(img):
        print("  skip image patch test: out/img.bin missing (run `make extract`)")
        return
    print("make patch on out/img.bin")
    import ubidis
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "img_patched.bin")
        pb = os.path.join(td, "patch")  # not build/patch: keep the user's last `make patch`
        r = subprocess.run(["make", "-s", "-C", TC, "patch", f"PREFIX={prefix}", f"OUT={out}",
                            f"IMG={img}", f"PB={pb}"], capture_output=True, text=True)
        check(r.returncode == 0, "make patch builds examples/hook"
              + (f": {(r.stdout + r.stderr).strip()[-400:]}" if r.returncode else ""))
        if r.returncode:
            return
        a, b = open(img, "rb").read(), open(out, "rb").read()
        nm = subprocess.run([prefix + "/bin/ubicom32-elf-nm", os.path.join(pb, "patch.elf")],
                            capture_output=True, text=True).stdout
        fn = int(re.search(r"([0-9a-f]+) T my_replacement", nm).group(1), 16)
        w0, w1 = struct.unpack(">II", b[0xfc4:0xfcc])
        t0, t1 = ubidis.disasm_one(w0, 0x40000fc4)[0], ubidis.disasm_one(w1, 0x40000fc8)[0]
        # moveai a3,#hi ; calli a3,lo(a3): target = (hi << 7) + lo
        m0 = re.fullmatch(r"moveai a3,#(0x[0-9a-f]+)", t0)
        m1 = re.fullmatch(r"calli a3,(-?\d+)\(a3\)", t1)
        check(bool(m0 and m1) and (int(m0.group(1), 16) << 7) + int(m1.group(1)) == fn,
              f"hook at 0x40000fc4 is '{t0}; {t1}' -> my_replacement 0x{fn:08x}")
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        check(diff and min(diff) >= 0xfc4 and max(diff) < 0xfcc,
              "only the 8 hook bytes of the stock image changed")
        check(len(b) > len(a) and fn - dp.BASE < len(b), f"image grew by {len(b) - len(a)} bytes of gap code")
        # Repack and re-extract: the patched image survives the flash format.
        rp, rx = os.path.join(td, "rp.bin"), os.path.join(td, "rx.bin")
        r = subprocess.run([PY, os.path.join(ROOT, "tools", "repack.py"), out, "-o", rp,
                            "--template", os.path.join(ROOT, "dir655_firmware_121.bin")],
                           capture_output=True, text=True)
        check(r.returncode == 0, "repack.py packs the patched image" + (f": {r.stderr[-300:]}" if r.returncode else ""))
        if r.returncode == 0:
            r = subprocess.run([PY, os.path.join(ROOT, "tools", "extract.py"), rp, rx],
                               capture_output=True, text=True)
            check(r.returncode == 0 and open(rx, "rb").read() == b,
                  "extract.py on the repacked image gives the patched img.bin back")


def main():
    test_sources()
    test_patcher()
    test_gap_unreferenced()
    prefix = find_prefix()
    if prefix:
        test_toolchain(prefix)
        test_patch_image(prefix)
    elif os.environ.get("REQUIRE_TOOLCHAIN") == "1":
        check(False, "toolchain installed (REQUIRE_TOOLCHAIN=1)")
    else:
        print("  skip compiler tests: no toolchain (run `make -C toolchain toolchain`, or set PREFIX)")
    if FAILS:
        print(f"{len(FAILS)} toolchain check(s) failed")
        return 1
    print("toolchain checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
