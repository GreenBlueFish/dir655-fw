#!/usr/bin/env python3
"""
Parse the D-Link DIR-655 Rev A firmware container and extract the real image.

Container layout (see docs/FORMAT.md):
    0x00000 .. 0x0007F   128-byte D-Link/Ubicom image header
    0x00080 .. ~0x046FC  Ubicom32 bootstrap/loader (uncompressed machine code)
    ~0x046FC             embedded ARJ archive (main header + member "img.bin")
    member "img.bin"     ARJ method-1 compressed -> the real 6.5 MB image
    tail                 0xFF padding to the flash size

This tool locates the ARJ archive, parses the member header, decompresses the
member with the from-scratch decoder, verifies size+CRC, and writes img.bin.

Usage: python3 extract.py dir655_firmware_121.bin [out/img.bin]
"""
import sys, os, struct

sys.path.insert(0, os.path.dirname(__file__))
from arj_decompress import decompress

ARJ_MAGIC = b"\x60\xEA"


def u16(d, o):
    return struct.unpack_from("<H", d, o)[0]


def u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def parse_arj_header(d, pos):
    """Parse one ARJ header at pos. Returns dict or None for end-of-archive."""
    if d[pos:pos+2] != ARJ_MAGIC:
        raise ValueError("no ARJ magic at 0x%x" % pos)
    basic = u16(d, pos + 2)
    if basic == 0:
        return None                    # end-of-archive marker
    h = pos + 4
    first_hdr = d[h]
    method = d[h + 5]
    ftype = d[h + 6]
    csize = u32(d, h + 12)
    osize = u32(d, h + 16)
    crc = u32(d, h + 20)
    fn_start = h + first_hdr
    fn_end = d.index(b"\0", fn_start)
    fname = d[fn_start:fn_end].decode("latin1")
    cm_end = d.index(b"\0", fn_end + 1)
    after_basic = pos + 4 + basic      # basic-header CRC follows
    p = after_basic + 4
    while True:                        # skip any extended headers
        ext = u16(d, p)
        if ext == 0:
            p += 2
            break
        p += 2 + ext + 4
    return dict(method=method, ftype=ftype, csize=csize, osize=osize,
                crc=crc, fname=fname, data_off=p, hdr_off=pos)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: extract.py <firmware.bin> [out/img.bin]")
    inp = sys.argv[1]
    outp = sys.argv[2] if len(sys.argv) > 2 else "out/img.bin"
    d = open(inp, "rb").read()
    print("firmware: %s (%d bytes)" % (inp, len(d)))

    # Locate the first ARJ header (the archive main header).
    arj = d.find(ARJ_MAGIC)
    while arj != -1:
        try:
            if parse_arj_header(d, arj) is not None:
                break
        except Exception:
            pass
        arj = d.find(ARJ_MAGIC, arj + 1)
    if arj == -1:
        sys.exit("no ARJ archive found")

    main_hdr = parse_arj_header(d, arj)
    print("ARJ archive @0x%x  (name=%r)" % (arj, main_hdr["fname"]))

    # The member header follows the main header's compressed-data area (empty).
    member = parse_arj_header(d, main_hdr["data_off"])
    print("member: %r  method=%d  comp=%d  orig=%d  crc=0x%08x  data@0x%x"
          % (member["fname"], member["method"], member["csize"],
             member["osize"], member["crc"], member["data_off"]))

    if member["method"] not in (1, 2, 3):
        sys.exit("unsupported ARJ method %d (this decoder handles 1-3)"
                 % member["method"])

    comp = d[member["data_off"]:member["data_off"] + member["csize"]]
    print("decompressing %d -> %d bytes ..." % (len(comp), member["osize"]))
    out = decompress(comp, member["osize"], member["crc"])
    print("OK: size and CRC-32 verified (0x%08x)" % member["crc"])

    os.makedirs(os.path.dirname(outp) or ".", exist_ok=True)
    open(outp, "wb").write(out)
    print("wrote %s" % outp)


if __name__ == "__main__":
    main()
