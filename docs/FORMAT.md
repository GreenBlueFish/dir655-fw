# DIR-655 Rev A firmware 1.21 — container format & memory map

File analysed: `dir655_firmware_121.bin`, 1,507,328 bytes (0x170000).

The flash image is **not** code you can disassemble directly. It is a small
uncompressed Ubicom32 loader followed by an **ARJ-compressed archive** holding
the real 6.5 MB application image. The first useful step is therefore to
decompress it (see `tools/extract.py`), after which the result can be
disassembled.

## Top-level layout

| Offset | Size | Contents |
|--------|------|----------|
| `0x000000` | 128 B | D-Link/Ubicom image header (see below) |
| `0x000080` | ~0x467C | Ubicom32 bootstrap/loader — uncompressed machine code (≈98% decodes as valid instructions; the rest is embedded constants) |
| `0x0046FC` | 43 B | ARJ **main** header (archive name `img.arj`, method 0 = stored) |
| `0x004731` | 104 B | ARJ **local file** header for the member |
| `0x0047A3` | 1,453,619 B | ARJ **method-1** compressed stream |
| `0x1675D6` | 4 B | ARJ end-of-archive marker (`60 EA 00 00`) |
| `0x1675DA` | 35,366 B | `0xFF` padding to flash size |

The compressed member's path (embedded in the archive) is:

```
cygdrive/c/Ubicom/sdk/projects/dir-655/build/bin/img.bin
```

This confirms the build host (Cygwin) and the Ubicom SDK/toolchain, and names
the member `img.bin`.

### ARJ member (the real image)

| Field | Value |
|-------|-------|
| Compression method | 1 (LZ77 + Huffman, LHA/LZHUF lineage) |
| Compressed size | 1,453,619 bytes |
| Original size | 6,561,024 bytes |
| CRC-32 | `0x40CF4632` |

`tools/arj_decompress.py` decompresses this from scratch and **verifies both
the original size and the CRC-32** — so the decompression is provably correct.

## The 128-byte image header

Little-endian fields. Only the clearly-identified ones are listed; several
bytes are not yet interpreted.

| Offset | Bytes (this image) | Meaning |
|--------|--------------------|---------|
| `0x00` | `FA 32` | magic / signature |
| `0x02` | `00 80` → `0x0080` | header length = 128 (code starts at `0x80`) |
| `0x04` | `01 04 00 06` | version / revision markers (ties to firmware "1.21") |
| `0x0C` | `BC 02 D0 2C` → `0x2CD002BC` | **image checksum** — see below |
| `0x10` | `60 05 00 00` → `0x0560` | (unverified) a length/offset |
| `0x14` | `60 40 00 00` → `0x4060` | (unverified) |
| `0x40`–`0x4F` | — | a secondary descriptor block (sizes/offsets; not fully decoded) |

### Image checksum (important for re-flashing)

The 32-bit value at `0x0C` (little-endian) is a **standard CRC-32**
(poly `0xEDB88320`, init/final `0xFFFFFFFF`) computed over the **entire file
with those 4 checksum bytes set to zero**.

Verified: zeroing `0x0C..0x0F` and running CRC-32 over all 1,507,328 bytes
yields `0x2CD002BC`, exactly the stored value.

If you modify the image you must recompute this field or the loader will
reject it. Use:

```
python3 tools/fix_checksum.py --check firmware.bin   # verify
python3 tools/fix_checksum.py --fix   firmware.bin   # recompute in place
```

## Rebuilding a flashable image (repacking)

`tools/repack.py` is the inverse of `tools/extract.py`: it takes a (possibly
modified) `img.bin` and writes a complete flash image. The steps mirror the
layout above:

1. **Header + loader + ARJ main header** (`0x0000..0x4731`) are copied verbatim
   from a template firmware (the stock `.bin`). The main header's
   compressed-size field is `0`, so it is independent of the member payload.
2. **The member stream is recompressed** with `tools/arj_compress.py`, a
   from-scratch ARJ method-1 encoder that emits exactly the bitstream
   `arj_decompress.py` reads (per-block canonical Huffman trees for the
   literal/length and position alphabets, built with length-limited
   package-merge; a 64 KiB LZSS window with lazy matching).
3. **The member local-file header is regenerated**: the compressed size
   (`+12`), original size (`+16`) and payload CRC-32 (`+20`) are patched in the
   basic header, and the 4-byte basic-header CRC-32 that follows it is
   recomputed. (The filename, timestamps and flags are kept from the template.)
4. **The end-of-archive marker** `60 EA 00 00` is re-emitted after the stream.
5. **The 4-byte flash trailer** that occupies the last 4 bytes of the stock
   image (`60 29 00 80` at `0x16FFFC`) is preserved as the final 4 bytes.
6. The image is **padded with `0xFF` to the flash size** (`0x170000`), and
   finally the **image-header checksum** (above) is recomputed.

### ARJ header CRCs

ARJ stores, immediately after each basic header, a **CRC-32** (not CRC-16) of
that basic header's bytes (standard poly `0xEDB88320`). Both the main header
(`0x0FA52CD2`) and the member header (`0xCB493045`) in the stock image verify
as `zlib.crc32(basic_bytes)`. `repack.py` recomputes the member header's CRC
after patching the size/CRC fields.

### Correctness and limits

`make test` recompresses the **unmodified** `img.bin` and a copy with a few
bytes changed, re-extracts each through `extract.py`, and requires
byte-identical results plus a valid header checksum — proving the repack path
is a true inverse of extract. `tests/test_arj_compress.py` separately drives
the encoder's constant-tree fallbacks (a block whose code, temp or position
alphabet has a single symbol), which the stock image never reaches but a
low-entropy modification could. The encoder's output is ~1.38 MB,
slightly smaller than the stock 1.45 MB stream, so a repacked image fits the
flash. **Not verified on hardware:** no repacked image has been flashed to a
real DIR-655, so the loader's tolerance of a resized compressed member and the
exact role of the flash trailer remain inferred from the format.

## Decompressed `img.bin` (6,561,024 bytes)

Entropy and content analysis split it into two broad regions:

| Offset (in img.bin) | Contents |
|---------------------|----------|
| `0x000000` .. ~`0x148000` | Ubicom32 code + initialised data (ipOS kernel, drivers, application). **100% of this region decodes as valid Ubicom32 instructions.** |
| ~`0x148000` .. end | Web-UI resource area: HTML/JavaScript/XML/CSS templates, config strings (the router's management pages) and other data. |

Identifying strings confirm the platform: `ipOS SDK: %s`, `ipVersion(Date)`,
`Firmware Internal Version: ver%d.%02dB%02d`, plus 802.11 driver messages and
`DIR-655` configuration keys. The OS is Ubicom's **ipOS** real-time OS.

### Runtime addresses

Branch and call instructions are PC-relative, so the disassembly is correct
regardless of load address. Absolute pointers built by `moveai` reveal the
runtime memory map: code/rodata are referenced at low addresses, and RAM data
structures cluster around `0x007FFxxx`–`0x0080xxxx`. Ubicom32's 32-bit
absolute relocations zero the top byte, giving a 24-bit (16 MB) effective
address space.

### Free gap after the image (`0x40641d00`..`0x40650000`)

`img.bin` runs at `0x40000000` (see docs/DECOMPILER.md for how that base was
established), so the stock 6,561,024-byte (`0x641d00`) image ends at
**`0x40641d00`**. The C runtime's `.bss` starts at **`0x40650000`**: the
startup (docs/sample_startup.asm, `0x4000005c`) runs `moveai a1,#0x80ca00`, and
`moveai` sets the register to `imm24 << 7`, so `0x80ca00 << 7 = 0x40650000`.
The loop that follows stores zero words from there. 25 `moveai` in the code
region load that same address.

Nothing in the image refers to the 58,112 bytes between the two addresses:

- No `moveai` in the code region (`0x0`..`0x148000`) has a base in the gap.
  The highest base below it is `0x40174b00`, nearly 5 MB lower, so no
  `moveai`+`lea`/load offset reaches it either.
- Of all the aligned 32-bit words in the image, only two fall in
  `0x40641d00`..`0x4064ffff`: `0x5188f4` and `0x5189c0`. Both are the text
  `"@dli"` from `support@dlink.com` inside an X.509 certificate, not pointers.

The toolchain (toolchain/README.md) links new code into this gap. `make test`
re-derives these facts from `out/img.bin` (toolchain/tests/run_tests.py,
`test_gap_unreferenced`). Two things are still untested on hardware: whether the
boot loader accepts an `img.bin` that is longer than the stock one, and whether
something at runtime (the loader, ipOS's heap setup) writes to the gap without
a pointer that is visible in the image.
