# dir655-fw

Reverse-engineering the D-Link **DIR-655 Rev A** firmware **v1.21**
(`dir655_firmware_121.bin`) — a from-scratch toolkit to unpack, disassemble
and re-sign the image.

## What this is

The DIR-655 Rev A runs on a **Ubicom** processor (IP3000/IP5000 family) using
the **Ubicom32** 32-bit big-endian instruction set, under Ubicom's **ipOS**
RTOS. There was no public disassembler for this `.bin`. This repo provides one,
plus everything needed to get from the raw flash image to readable assembly:

1. **Container + checksum** are fully decoded. The flash image is a small
   Ubicom32 loader + an **ARJ-compressed archive** holding the real 6.5 MB
   application. The header checksum algorithm is cracked (so you can re-flash
   modified images). See [`docs/FORMAT.md`](docs/FORMAT.md).
2. **A from-scratch ARJ method-1 decompressor** turns the blob into the real
   `img.bin`, **verified against its embedded size and CRC-32** — provably
   correct output.
3. **A from-scratch Ubicom32 disassembler** (Python + C) that decodes the
   ~1.28 MB code region at **99.82% known instructions**, with
   **100.000%** of all branch/call targets landing on valid 4-byte boundaries.
   See [`docs/ISA.md`](docs/ISA.md).

Nothing third-party is vendored. The ISA opcode *facts* were derived from the
public GPL Ubicom32 binutils description (read as a reference); all code here —
decompressor, disassembler engine, container parser — is original.

## Quick start

```sh
# 1. Verify the stock header checksum (sanity check)
python3 tools/fix_checksum.py --check dir655_firmware_121.bin

# 2. Unpack the real image (writes out/img.bin, verifies CRC-32)
python3 tools/extract.py dir655_firmware_121.bin out/img.bin
#    ... or:  make extract

# 3. Disassemble the code region to a labeled listing
python3 tools/ubidis.py --listing -s 0 -n 0x148000 out/img.bin > out/img.asm
#    ... or:  make disasm

# 4. Run the checks: extract + CRC-32, ISA tables, and (if a C compiler is
#    present) the C disassembler diffed against the Python one
make test

# Native (faster) disassembler, if you have a C compiler:
make ubidis          # builds bin/ubidis
bin/ubidis -s 0 -n 0x148000 out/img.bin
```

## Modifying and repacking the firmware

The toolkit goes both ways. To change the firmware, edit the decompressed
`out/img.bin` (patch code via the disassembly, or edit the web-UI resources in
its upper region), then **repack** it into a complete, re-checksummed image:

```sh
# recompress out/img.bin -> out/repacked.bin (header, loader, ARJ archive,
# recomputed CRCs and image checksum all rebuilt)
python3 tools/repack.py out/img.bin -o out/repacked.bin
#    ... or:  make repack

# prove the pipeline is lossless: repack the unmodified img.bin and a copy with
# a few bytes changed, re-extract each with extract.py, and require
# byte-identical results + valid checksums
make test
```

`tools/repack.py` recompresses with a **from-scratch ARJ method-1 encoder**
(`tools/arj_compress.py`, the exact inverse of the decoder), regenerates the
ARJ member header (compressed size, original size, data CRC-32, header
CRC-32), re-emits the end-of-archive marker, preserves the 4-byte flash
trailer, pads to the flash size, and fixes the image-header checksum. The
compressed stream it produces is actually a little **smaller** than the stock
one (≈1.38 MB vs 1.45 MB), so a repacked image comfortably fits the 1.47 MB
flash budget. A full repack takes ~15 s in pure Python.

> **Unverified:** the round-trip (repack → extract) is provably lossless and
> the checksums are correct, but **no repacked image has been flashed to a real
> DIR-655**. Whether the stock loader accepts a differently-sized compressed
> member, and whether the 4-byte flash trailer carries anything the loader
> checks, are inferred from the format — not confirmed on hardware. Flash at
> your own risk, ideally with a serial console / recovery (TFTP) path ready.

## Repository layout

```
dir655_firmware_121.bin   the stock firmware image (input)
tests/
  run_tests.py             `make test`: extract, ISA tables, C-vs-Python disasm
  test_arj_compress.py     `make test`: encoder single-symbol/empty-tree edge cases
tools/
  extract.py              parse container + decompress  -> out/img.bin
  arj_decompress.py        from-scratch ARJ method-1 decoder (CRC-verified)
  arj_compress.py          from-scratch ARJ method-1 encoder (inverse of above)
  repack.py                rebuild a flashable image from img.bin  -> out/repacked.bin
  roundtrip_test.py        `make test`: repack unmodified + modified img.bin, re-extract
  fix_checksum.py          verify / recompute the header CRC-32
  ubidis.py                from-scratch Ubicom32 disassembler (validated)
  gen_isa_table.py         one-time ISA-fact extractor (provenance; see below)
src/
  ubidis.c                 native C port of the disassembler
  ubicom32_isa.inc         ISA fact table (C)      } same facts,
  ubicom32_isa.py          ISA fact table (Python) } two renderings
docs/
  FORMAT.md                container format, memory map, checksum
  ISA.md                   Ubicom32 encoding notes + validation evidence
  functions.txt            4,890 discovered function entry points
  sample_startup.asm       labeled disassembly of the C-runtime startup
Makefile                   build / extract / disasm / checksum targets
```

## What "known instructions" buys you, and what it doesn't

The disassembler produces faithful, labeled Ubicom32 **assembly**, with
functions (from call targets) and local branch labels resolved. That is what
you need to read and patch the firmware, and to re-sign it with
`fix_checksum.py`.

Full **decompilation to C/C++** is deliberately *not* claimed. Recovering
idiomatic C from a stripped binary for an obscure ISA — data-flow and type
recovery, control-flow structuring — is a decompiler-grade effort well beyond a
correct disassembler, and doing it "from scratch" here would produce output
that only *looks* like C without being trustworthy. The honest, verifiable
deliverable is correct assembly plus a complete, documented ISA model. If you
want a C-level view, the cleanest route is to drive a decompiler (e.g. Ghidra)
with a Ubicom32 processor spec built from [`docs/ISA.md`](docs/ISA.md); the
encoding there is complete enough to do so.

## Regenerating the ISA table

`src/ubicom32_isa.{inc,py}` are generated by `tools/gen_isa_table.py` from the
public Ubicom32 binutils CGEN description. Those reference files are
intentionally **not** committed (we don't vendor third-party source). To
regenerate, fetch them yourself from the public binutils-for-ubicom32 port and:

```sh
python3 tools/gen_isa_table.py <refdir> src/ubicom32_isa.inc
```

The committed tables contain only factual instruction-encoding data
(opcodes, masks, mnemonics, operand syntax) re-expressed in our own schema.
