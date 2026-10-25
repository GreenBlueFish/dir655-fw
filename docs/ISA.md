# Ubicom32 instruction set — encoding notes

The DIR-655 Rev A runs on a Ubicom processor (IP3000/IP5000 family) using the
**Ubicom32** architecture. This document records the encoding facts the
disassembler relies on. They were confirmed empirically against the firmware
and cross-checked with the public (GPL) Ubicom32 binutils CGEN description,
which was read purely as a reference — no third-party code is vendored here.

## Fundamentals (confirmed from the binary)

- Fixed **32-bit** instructions, **big-endian** in the image.
- Major opcode in bits **[31:27]** (5 bits). A statistical scan of the code
  region shows this byte position has by far the lowest entropy, and `moveai`
  (op1 `0x1C`) dominates — consistent with pointer-heavy startup code.
- 16 data registers `d0`–`d15`; 8 address registers `a0`–`a7` (`a7` = `sp`).
- 16 condition codes: `f cc cs eq ge gt hi le ls lt mi ne pl t vc vs`.

Validation that these are correct, not merely plausible:

- The ~1.28 MB code region decodes with **100% known opcodes**.
- **100.000%** of 72,608 branch/call targets are 4-byte aligned (random would
  be ~25%), and 91% land inside the code region. This proves the instruction
  width, endianness, and PC-relative offset math simultaneously.
- The decoded startup is a recognisable C runtime (`.data` copy / `.bss` zero
  loops) with correctly-closing branches.

## Instruction shape

Most ALU/move/load-store instructions use two 11-bit "effective address"
operand fields plus a secondary opcode:

```
 31    27 26                  16 15   11 10                     0
+--------+----------------------+-------+-----------------------+
|  op1   |   destination  (d)   |  op2  |     source1  (s1)     |
+--------+----------------------+-------+-----------------------+
```

Each 11-bit EA field encodes one of several addressing modes (register direct,
`#imm`, `(aN)`, `offset(aN)`, `(aN,dR)` indexed, and post/pre-increment
`(aN)N++` / `N(aN)++`). A third source `s2` (a 4-bit data-register field at
bits [14:11]) appears in three-operand ALU forms. Each (mnemonic × addressing
mode) pairing is a distinct encoding, which is why the ISA table has ~2,700
rows for ~130 mnemonics.

The disassembler decodes by matching `(word & mask) == base` against the ISA
table (indexed by op1), then rendering the matched entry's operand template.

## Control flow (offset math)

`pc` below is the virtual address of the instruction itself.

- **`jmp<cc>[.s]<.t|.f> offset21`** — conditional branch. op1 `0x1A`.
  `target = ((sext21(bits[20:0]) << 2) + pc) & ~3`.
  `cc` = bits[26:23]; `C` flag (bit 21): `0`→`.s` (signed), `1`→(default);
  `P` flag (bit 22): predict `.t`/`.f`. `jmpt` is an unconditional jump.
- **`call aN, offset24`** — call, link register in `aN`. op1 `0x1B`.
  `target = ((sext3(bits[26:24])<<21 | bits[20:0]) << 2) + pc`.
- **`calli aN, offset16(aM)`** — indirect call through `aM`. op1 `0x1E`.
- **`ret (aN)`** and friends live in the op1 `0x00` (misc) group.

## Immediates

- `moveai aN, #imm24` (op1 `0x1C`): 24-bit value = `(bits[26:24]<<21) | bits[20:0]`.
- `movei <d>, #imm16` (op1 `0x19`): signed 16-bit at bits[15:0].
- `#imm8`: signed 8-bit. `direct` addresses: 8-bit field `<< 2` (word-scaled).
- `offset(aN)`: 7-bit offset, scaled by the access width (`.1`/`.2`/`.4`).
- post/pre-increment amount `N`: signed 4-bit, scaled by access width.

## Width suffixes

`.1` = byte, `.2` = halfword (16-bit), `.4` = word (32-bit). E.g. `move.4`,
`add.2`, `sub.1`, `lea.4`.

## Scope / honesty

The table covers the full base ISA plus the DSP/MAC and pixel (`px*`)
extensions. DSP-specific operand *suffixes* (e.g. some `mac`/`msub`
accumulator selectors) are rendered conservatively; the base integer,
load/store, move and control-flow instructions — i.e. essentially all of what
a C compiler emits, and what you need to read and patch the firmware — are
rendered fully. A handful of addressing-mode aliases share an identical
`(base,mask)`; the disassembler picks one faithful rendering (e.g. it may show
`0(aN)` where `(aN)` would also be valid).
