#!/usr/bin/env python3
"""
Round-trip test for the repacker (driven by `make test`).

Proves the repack path is the inverse of the extract path:

    1. extract the original firmware               -> img.bin
    2. repack img.bin (unmodified)                 -> repacked.bin
    3. extract repacked.bin                         -> img_rt.bin
    4. assert img_rt.bin is byte-identical to img.bin
    5. assert fix_checksum --check repacked.bin is VALID

Exits non-zero on any failure.

Usage: python3 roundtrip_test.py [firmware.bin] [--chain N] [--workdir out]
"""
import sys, os, struct, zlib, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arj_compress import compress
from arj_decompress import decompress
import extract as extract_mod
from repack import repack
from fix_checksum import compute as compute_cksum


def _extract_img(firmware_bytes):
    """Decompress the ARJ member of a firmware image -> img.bin bytes."""
    d = firmware_bytes
    arj = d.find(extract_mod.ARJ_MAGIC)
    while arj != -1:
        try:
            if extract_mod.parse_arj_header(d, arj) is not None:
                break
        except Exception:
            pass
        arj = d.find(extract_mod.ARJ_MAGIC, arj + 1)
    if arj == -1:
        raise SystemExit("no ARJ archive found")
    main_hdr = extract_mod.parse_arj_header(d, arj)
    member = extract_mod.parse_arj_header(d, main_hdr["data_off"])
    comp = d[member["data_off"]:member["data_off"] + member["csize"]]
    return decompress(comp, member["osize"], member["crc"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("firmware", nargs="?", default="dir655_firmware_121.bin")
    ap.add_argument("--chain", type=int, default=128)
    ap.add_argument("--workdir", default="out")
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)
    print("[1] reading %s" % args.firmware)
    fw = open(args.firmware, "rb").read()

    print("[2] extracting original -> img.bin")
    img = _extract_img(fw)
    img_path = os.path.join(args.workdir, "img.bin")
    open(img_path, "wb").write(img)
    print("    img.bin: %d bytes" % len(img))

    print("[3] repacking unmodified img.bin (chain=%d) ..." % args.chain)
    repacked, info = repack(img, fw, chain=args.chain)
    rp_path = os.path.join(args.workdir, "repacked.bin")
    open(rp_path, "wb").write(repacked)
    print("    repacked.bin: %d bytes  (compressed stream %d, original 1453619)"
          % (info["size"], info["comp"]))

    print("[4] extracting repacked.bin -> img_rt.bin")
    img_rt = _extract_img(repacked)
    open(os.path.join(args.workdir, "img_rt.bin"), "wb").write(img_rt)

    ok = True
    if img_rt == img:
        print("    PASS: round-tripped image is byte-identical to img.bin")
    else:
        ok = False
        print("    FAIL: round-tripped image differs from img.bin "
              "(len %d vs %d)" % (len(img_rt), len(img)))

    print("[5] verifying header checksum")
    stored = struct.unpack_from("<I", repacked, 0x0C)[0]
    calc = compute_cksum(repacked)
    if stored == calc:
        print("    PASS: checksum VALID (0x%08x)" % stored)
    else:
        ok = False
        print("    FAIL: checksum stored 0x%08x != computed 0x%08x"
              % (stored, calc))

    fits = info["size"] <= info["flash"]
    print("    note: repacked image %s the %d-byte flash (size %d)"
          % ("fits" if fits else "EXCEEDS", info["flash"], info["size"]))

    print("RESULT:", "OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
