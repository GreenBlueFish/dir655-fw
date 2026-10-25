#!/usr/bin/env python3
"""
Round-trip test for the repacker (driven by `make test`).

Proves the repack path is the inverse of the extract path, driving the shipped
command-line tools (extract.py, repack.py, fix_checksum.py) as a user would:

    1. extract.py the original firmware              -> img.bin
    2. repack.py img.bin (unmodified)                -> repacked.bin
    3. extract.py repacked.bin                       -> img_rt.bin
    4. assert img_rt.bin is byte-identical to img.bin
    5. assert fix_checksum.py --check repacked.bin is VALID
    6. modify a few bytes of img.bin                 -> img_mod.bin
       repack.py + extract.py it, and assert the modification survives
       byte for byte and the checksum is VALID

Exits non-zero on any failure.

Usage: python3 roundtrip_test.py [firmware.bin] [--chain N] [--workdir out]
"""
import sys, os, subprocess, argparse

TOOLS = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# Bytes changed for the modified-image case: (offset, new bytes). Spread over
# the code region, the data region and the end of the image; the last edit
# also writes a run that the original does not contain.
EDITS = [
    (0x000100, b"\xde\xad\xbe\xef"),
    (0x0a0000, b"\x00"),
    (0x200000, b"modified by roundtrip_test"),
    (0x400000, b"\xff" * 4096),
]


def run(tool, *args):
    """Run a shipped tool; return (ok, combined output)."""
    r = subprocess.run([PY, os.path.join(TOOLS, tool)] + list(args),
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def tool(tool_name, *args):
    ok, out = run(tool_name, *args)
    if not ok:
        print("    FAIL: %s %s\n%s" % (tool_name, " ".join(args), out))
        print("RESULT: FAILED")
        sys.exit(1)
    return out


def repack_and_check(name, img_path, fw, chain, workdir):
    """Repack img_path, re-extract it, compare, check the checksum. -> ok."""
    rp = os.path.join(workdir, name + "_repacked.bin")
    rt = os.path.join(workdir, name + "_rt.bin")
    print("    repack.py %s (chain=%d) ..." % (os.path.basename(img_path), chain))
    out = tool("repack.py", img_path, "-o", rp, "--template", fw,
               "--chain", str(chain))
    for line in out.splitlines():
        if line.startswith(("compressed stream", "wrote", "WARNING")):
            print("      " + line)
    print("    extract.py %s" % os.path.basename(rp))
    tool("extract.py", rp, rt)

    ok = True
    want = open(img_path, "rb").read()
    got = open(rt, "rb").read()
    if got == want:
        print("    PASS: re-extracted image is byte-identical to %s"
              % os.path.basename(img_path))
    else:
        ok = False
        print("    FAIL: re-extracted image differs (len %d vs %d)"
              % (len(got), len(want)))
    cok, cout = run("fix_checksum.py", "--check", rp)
    if cok:
        print("    PASS: checksum VALID")
    else:
        ok = False
        print("    FAIL: checksum: " + cout)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("firmware", nargs="?", default="dir655_firmware_121.bin")
    ap.add_argument("--chain", type=int, default=256)
    ap.add_argument("--workdir", default="out")
    args = ap.parse_args()
    os.makedirs(args.workdir, exist_ok=True)

    print("[1] extract.py %s -> img.bin" % args.firmware)
    img_path = os.path.join(args.workdir, "img.bin")
    tool("extract.py", args.firmware, img_path)
    img = open(img_path, "rb").read()
    print("    img.bin: %d bytes" % len(img))

    print("[2] round trip of the unmodified img.bin")
    ok = repack_and_check("img", img_path, args.firmware, args.chain,
                          args.workdir)

    print("[3] round trip of a modified img.bin (%d edits)" % len(EDITS))
    mod = bytearray(img)
    for off, new in EDITS:
        mod[off:off + len(new)] = new
    if bytes(mod) == img:
        sys.exit("EDITS do not change the image; pick other bytes")
    mod_path = os.path.join(args.workdir, "img_mod.bin")
    open(mod_path, "wb").write(mod)
    ok = repack_and_check("img_mod", mod_path, args.firmware, args.chain,
                          args.workdir) and ok

    print("RESULT:", "OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
