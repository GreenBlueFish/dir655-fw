#!/usr/bin/env python3
"""
End-to-end checks for the toolkit (run with `make test`).

1. extract.py unpacks the firmware and the result has the ARJ member's size and
   CRC-32 (re-checked here independently of the decoder's own check).
2. The C and Python ISA tables hold the same entries in the same order, sorted
   most-specific first, with no two entries sharing a (base, mask).
3. The Python disassembler decodes the code region at no less than the known
   coverage.
4. If a C compiler is available ($CC, cc, gcc, clang, or `zig cc`), bin/ubidis
   is built and its output on the code region must match ubidis.py byte for
   byte. With no compiler this step is skipped, unless REQUIRE_CC=1.
5. tools/ubidec: the structured decoder agrees with the ISA table on every
   code word the table accepts, decodes known operands correctly (moveai
   shift, negative pdec offset, cmpi immediate field), and whole-program
   discovery holds its function / coverage / switch-table floors with no
   flow errors.

Exits non-zero on the first failure.
"""
import os, re, shlex, shutil, subprocess, sys, tempfile, zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FW = os.path.join(ROOT, "dir655_firmware_121.bin")
PY = sys.executable

IMG_SIZE = 6561024
IMG_CRC = 0x40CF4632
CODE = ["-s", "0", "-n", "0x148000", "-b", "0"]
CODE_WORDS = 0x148000 // 4
MIN_KNOWN = 335277          # ubidis.py coverage when this test was written

failed = False


def check(ok, what):
    global failed
    print("%s  %s" % ("ok  " if ok else "FAIL", what))
    if not ok:
        failed = True


def test_extract(tmp):
    out = os.path.join(tmp, "img.bin")
    r = subprocess.run([PY, os.path.join(ROOT, "tools", "extract.py"), FW, out],
                       capture_output=True, text=True)
    check(r.returncode == 0, "extract.py exits 0" +
          ("" if r.returncode == 0 else ": " + r.stderr.strip()[-300:]))
    if r.returncode != 0:
        return None
    data = open(out, "rb").read()
    check(len(data) == IMG_SIZE, "img.bin is %d bytes (got %d)" % (IMG_SIZE, len(data)))
    crc = zlib.crc32(data) & 0xFFFFFFFF
    check(crc == IMG_CRC, "img.bin CRC-32 is 0x%08x (got 0x%08x)" % (IMG_CRC, crc))
    return out


def test_tables():
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from ubicom32_isa import INSNS
    py = [(b, m, n) for b, m, n, _ in INSNS]
    inc = open(os.path.join(ROOT, "src", "ubicom32_isa.inc")).read()
    c = [(int(b, 16), int(m, 16), n) for b, m, n in
         re.findall(r'^\s*\{0x([0-9a-f]+)U,0x([0-9a-f]+)U,"([^"]*)"', inc, re.M)]
    check(len(py) > 0 and py == c,
          "C and Python ISA tables agree (%d vs %d entries)" % (len(c), len(py)))
    keys = [(b, m) for b, m, _ in py]
    dups = len(keys) - len(set(keys))
    check(dups == 0, "no duplicate (base,mask) entries (%d found)" % dups)
    pop = [bin(m).count("1") for _, m, _ in py]
    check(pop == sorted(pop, reverse=True), "table sorted most-specific first")
    check(all((m >> 27) & 0x1F == 0x1F for _, m, _ in py),
          "every entry fixes the major opcode (C bucket index is exhaustive)")


def disasm_py(img):
    r = subprocess.run([PY, os.path.join(ROOT, "tools", "ubidis.py")] + CODE + [img],
                       capture_output=True)
    check(r.returncode == 0, "ubidis.py exits 0")
    m = re.search(rb"# (\d+)/(\d+) words known", r.stderr)
    known = int(m.group(1)) if m else -1
    total = int(m.group(2)) if m else -1
    check(total == CODE_WORDS and known >= MIN_KNOWN,
          "ubidis.py decodes >= %d/%d code words (got %d/%d)"
          % (MIN_KNOWN, CODE_WORDS, known, total))
    return r.stdout


def find_cc():
    cands = []
    if os.environ.get("CC"):
        cands.append(shlex.split(os.environ["CC"]))
    cands += [["cc"], ["gcc"], ["clang"], ["zig", "cc"]]
    for cmd in cands:
        if shutil.which(cmd[0]):
            return cmd
    return None


def test_c(tmp, img, py_out):
    cc = find_cc()
    if cc is None:
        msg = "no C compiler found; ubidis.c not built"
        if os.environ.get("REQUIRE_CC") == "1":
            check(False, msg)
        else:
            print("skip  " + msg + " (set REQUIRE_CC=1 to make this a failure)")
        return
    exe = os.path.join(tmp, "ubidis")
    r = subprocess.run(cc + ["-O2", "-std=c99", "-Wall", "-Werror",
                             "-I" + os.path.join(ROOT, "src"), "-o", exe,
                             os.path.join(ROOT, "src", "ubidis.c")],
                       capture_output=True, text=True)
    check(r.returncode == 0, "ubidis.c builds with %s -Wall -Werror%s"
          % (" ".join(cc), "" if r.returncode == 0 else ":\n" + r.stderr))
    if r.returncode != 0:
        return
    r = subprocess.run([exe] + CODE + [img], capture_output=True)
    check(r.returncode == 0, "bin/ubidis exits 0")
    if r.stdout == py_out:
        check(True, "ubidis.c output matches ubidis.py on the code region "
              "(%d lines)" % py_out.count(b"\n"))
        return
    a, b = py_out.split(b"\n"), r.stdout.split(b"\n")
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    check(False, "ubidis.c output differs from ubidis.py: %d lines (py %d, c %d); first:\n"
          "  py: %s\n  c:  %s" % (len(diff), len(a), len(b),
                                  *(diff[0] if diff else (b"", b""))))


# Floors from when tools/ubidec was written (docs/DECOMPILER.md).
MIN_FUNCS = 6855
MIN_COVERED = 334726
MIN_SWITCH = 131


def test_ubidec(img):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import ubidis
    from ubidec.decode import decode
    from ubidec.machine import BASE
    import analyze
    data = open(img, "rb").read()
    bad = 0
    for o in range(0, 0x148000, 4):
        w = int.from_bytes(data[o:o + 4], "big")
        e = ubidis.match(w)
        if e is None:
            continue
        a = {"iwrite": "move.4"}.get(e[2], e[2])
        if a.startswith("jmp"):
            a = "jmp"
        if decode(w, BASE + o).mn != a:
            bad += 1
    check(bad == 0, "ubidec decoder agrees with the ISA table (%d mismatches)"
          % bad)

    def at(off):
        return decode(int.from_bytes(data[off:off + 4], "big"), BASE + off)
    i = at(0x8)
    check(i.mn == "moveai" and i.imm == 0x7ffa00 << 7,
          "moveai loads imm24 << 7 (0x%x)" % (i.imm or 0))
    i = at(0x8b4)
    check(i.mn == "pdec" and i.s1.kind == "mem" and i.s1.a == 7
          and i.s1.b == -44, "pdec offset is negative: sp - 44 (%r)" % i.s1)
    i = at(0x1d8)
    check(i.mn == "cmpi" and i.imm == 19, "cmpi immediate in bits 26:11")
    i = at(0x4)
    check(i.mn == "call" and i.target == BASE + 0x12c and i.link == 5,
          "call target and link register")

    img_, prog = analyze.analyze(img)
    s = analyze.summary(img_, prog)
    check(s["functions"] >= MIN_FUNCS, "functions discovered >= %d (got %d)"
          % (MIN_FUNCS, s["functions"]))
    check(s["covered"] >= MIN_COVERED, "code words claimed by a function "
          ">= %d (got %d of %d)" % (MIN_COVERED, s["covered"], s["valid"]))
    check(s["blocks"].get("switch", 0) >= MIN_SWITCH,
          "jump tables resolved >= %d (got %d)"
          % (MIN_SWITCH, s["blocks"].get("switch", 0)))
    check(s["flow_errors"] == 0, "no function flows into data or invalid "
          "code (%d)" % s["flow_errors"])


def main():
    with tempfile.TemporaryDirectory() as tmp:
        img = test_extract(tmp)
        test_tables()
        if img:
            py_out = disasm_py(img)
            test_c(tmp, img, py_out)
            test_ubidec(img)
    print("FAILED" if failed else "all checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
