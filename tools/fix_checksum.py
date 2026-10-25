#!/usr/bin/env python3
"""
Verify or repair the D-Link DIR-655 firmware header checksum.

The 128-byte image header stores, at offset 0x0C (little-endian, 32-bit), a
CRC-32 computed over the *entire file* with those 4 checksum bytes set to zero
(standard CRC-32: poly 0xEDB88320, init/final 0xFFFFFFFF). After modifying a
firmware image you must recompute this field or the bootloader will reject it.

Usage:
    python3 fix_checksum.py --check  firmware.bin
    python3 fix_checksum.py --fix    firmware.bin [out.bin]   (in place if no out)
"""
import sys, zlib, struct

CKSUM_OFF = 0x0C


def compute(data):
    b = bytearray(data)
    b[CKSUM_OFF:CKSUM_OFF + 4] = b"\x00\x00\x00\x00"
    return zlib.crc32(bytes(b)) & 0xFFFFFFFF


def main():
    args = sys.argv[1:]
    if len(args) < 2 or args[0] not in ("--check", "--fix"):
        sys.exit(__doc__)
    mode, path = args[0], args[1]
    data = bytearray(open(path, "rb").read())
    stored = struct.unpack_from("<I", data, CKSUM_OFF)[0]
    calc = compute(data)
    print("stored:     0x%08x" % stored)
    print("computed:   0x%08x" % calc)
    if mode == "--check":
        print("VALID" if stored == calc else "INVALID (needs --fix)")
        sys.exit(0 if stored == calc else 1)
    # --fix
    struct.pack_into("<I", data, CKSUM_OFF, calc)
    out = args[2] if len(args) > 2 else path
    open(out, "wb").write(data)
    print("wrote %s with checksum 0x%08x" % (out, calc))


if __name__ == "__main__":
    main()
