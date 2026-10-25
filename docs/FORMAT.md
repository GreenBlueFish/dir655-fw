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
