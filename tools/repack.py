#!/usr/bin/env python3
"""
Rebuild a flashable DIR-655 Rev A firmware 1.21 image from a (possibly
modified) img.bin — the inverse of tools/extract.py.

The container is (see docs/FORMAT.md):

    [0x0000 .. 0x4731)   128-B image header + Ubicom32 loader + ARJ main header
    [0x4731 .. 0x47A3)   ARJ member local-file header for "img.bin"
    [0x47A3 .. )         ARJ method-1 compressed stream (the real image)
    ...                  ARJ end-of-archive marker (60 EA 00 00)
    ...                  0xFF padding to the flash size
    [FLASH-4 .. FLASH)   4-byte flash trailer

This tool keeps the header, loader and ARJ main header verbatim from a
template firmware (the original .bin), recompresses img.bin with the
from-scratch ARJ method-1 encoder, regenerates the member header (patching
the compressed size, original size, data CRC-32 and the header CRC-32),
re-emits the end-of-archive marker, preserves the flash trailer, pads to the
flash size, and finally fixes the image-header checksum so the loader will
accept the result.

Usage:
    python3 repack.py img.bin [-o out.bin] [--template dir655_firmware_121.bin]
                             [--chain N]

What is verified here (and in `make test`): recompressing the *unmodified*
img.bin and running extract.py on the result yields a byte-identical img.bin,
and fix_checksum reports VALID. What is NOT verified: nobody has flashed a
repacked image to a real DIR-655 — the loader's acceptance of a
differently-sized compressed stream and of the regenerated member header is
inferred from the format, not confirmed on hardware.
"""
import sys, os, struct, zlib, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arj_compress import compress

# Container offsets (fixed for this firmware; see docs/FORMAT.md).
MEMBER_HDR_OFF = 0x4731     # start of the ARJ member local-file header
DATA_OFF       = 0x47A3     # start of the compressed member stream
EOA            = b"\x60\xEA\x00\x00"
CKSUM_OFF      = 0x0C
TRAILER_LEN    = 4          # flash trailer occupies the last 4 bytes


def _patch_member_header(tmpl, csize, osize, crc):
    """Return the regenerated member header bytes [MEMBER_HDR_OFF, DATA_OFF).

    Copies the template member header and patches the compressed size,
    original size and data CRC-32 in the basic header, then recomputes the
    basic-header CRC-32 that follows it.
    """
    magic = tmpl[MEMBER_HDR_OFF:MEMBER_HDR_OFF + 2]
    if magic != b"\x60\xEA":
        raise ValueError("template member header missing ARJ magic")
    basic_size = struct.unpack_from("<H", tmpl, MEMBER_HDR_OFF + 2)[0]
    basic_start = MEMBER_HDR_OFF + 4
    basic = bytearray(tmpl[basic_start:basic_start + basic_size])
    # fields are relative to the start of the basic header (the first_hdr_size byte)
    struct.pack_into("<I", basic, 12, csize)
    struct.pack_into("<I", basic, 16, osize)
    struct.pack_into("<I", basic, 20, crc)
    hdr_crc = zlib.crc32(bytes(basic)) & 0xFFFFFFFF
    out = bytearray()
    out += magic
    out += struct.pack("<H", basic_size)
    out += basic
    out += struct.pack("<I", hdr_crc)
    out += b"\x00\x00"                      # extended-header count = 0
    if len(out) != DATA_OFF - MEMBER_HDR_OFF:
        raise ValueError("regenerated member header size %d != expected %d"
                         % (len(out), DATA_OFF - MEMBER_HDR_OFF))
    return bytes(out)


def repack(img, template, chain=256):
    """Build a complete firmware image from img.bin and a template firmware."""
    flash_size = len(template)
    img = bytes(img)

    comp, crc = compress(img, max_chain=chain)
    member_hdr = _patch_member_header(template, len(comp), len(img), crc)

    body = bytearray()
    body += template[:MEMBER_HDR_OFF]        # header + loader + ARJ main header
    body += member_hdr
    body += comp
    body += EOA

    trailer = template[flash_size - TRAILER_LEN:]
    pad_target = flash_size - TRAILER_LEN
    if len(body) > pad_target:
        over = len(body) + TRAILER_LEN - flash_size
        sys.stderr.write(
            "WARNING: image is %d bytes over the %d-byte flash size; the "
            "compressed stream did not fit. The result is self-consistent "
            "(extract/checksum still work) but may be too large to flash. "
            "Try a higher --chain, or shrink the modification.\n"
            % (over, flash_size))
        body += trailer                      # append trailer; no padding room
    else:
        body += b"\xFF" * (pad_target - len(body))
        body += trailer

    out = bytearray(body)
    # fix the image-header checksum: CRC-32 over the whole file with 0x0C..0x0F zeroed
    out[CKSUM_OFF:CKSUM_OFF + 4] = b"\x00\x00\x00\x00"
    cksum = zlib.crc32(bytes(out)) & 0xFFFFFFFF
    struct.pack_into("<I", out, CKSUM_OFF, cksum)
    return bytes(out), dict(comp=len(comp), crc=crc, cksum=cksum,
                            size=len(out), flash=flash_size)


def main():
    ap = argparse.ArgumentParser(description="Rebuild a flashable DIR-655 1.21 image from img.bin")
    ap.add_argument("img", help="the (possibly modified) decompressed image, e.g. out/img.bin")
    ap.add_argument("-o", "--out", default="out/repacked.bin", help="output firmware path")
    ap.add_argument("--template", default="dir655_firmware_121.bin",
                    help="original firmware, source of header/loader/main-header/trailer")
    ap.add_argument("--chain", type=int, default=256,
                    help="LZSS hash-chain search depth (higher = smaller, slower)")
    args = ap.parse_args()

    img = open(args.img, "rb").read()
    template = open(args.template, "rb").read()
    print("img.bin: %d bytes   template: %s (%d bytes)"
          % (len(img), args.template, len(template)))
    print("compressing (this takes a few minutes for a full image) ...")
    out, info = repack(img, template, chain=args.chain)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    open(args.out, "wb").write(out)
    print("compressed stream: %d bytes (original was 1453619)" % info["comp"])
    print("image CRC-32:      0x%08x" % info["crc"])
    print("header checksum:   0x%08x" % info["cksum"])
    print("wrote %s (%d bytes, flash size %d)" % (args.out, info["size"], info["flash"]))


if __name__ == "__main__":
    main()
