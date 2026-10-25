# Towards C: what is recovered, what remains, what stays assembly

`tools/ubidec/` is the start of a from-scratch decompiler for the Ubicom32
application image (`out/img.bin`). This first stage recovers the program's
**structure**: where the functions are, how they call each other, each
function's control-flow graph, and its switch tables. It emits **no C yet**.

```sh
make analyze        # -> out/functions.tsv (one row per function, with callers/callees)
make test           # includes the ubidec checks (decoder, semantics, discovery floors)
```

| Module | What it does |
|--------|--------------|
| `decode.py`  | Decoder that returns operands as data (addressing mode, register, offset, immediate, target) rather than text |
| `machine.py` | Builds the per-function CFG; an abstract interpreter tracks the stack pointer, the return address, constants and jump-table loads through it |
| `program.py` | Whole-program function discovery |
| `../analyze.py` | CLI: writes the function table / call graph |

## Recovered

| What | Result |
|------|--------|
| Functions | **6,855**: 4,890 `call` targets, 1,828 found through function pointers (words in the image or `moveai`+`lea` constants that point at unclaimed code), 137 orphans (unreferenced code right after a function's end) |
| Coverage | 334,726 of 335,277 valid code words (99.84%) belong to some function; no function flows into data or invalid encodings |
| Call graph | Direct callers and callees per function (`out/functions.tsv`); 15 calls recognised as never returning (the next word is another function's entry) |
| CFG | 6,904 return blocks, 31,130 conditional branches, 12,199 jumps, 2 tail calls; 110 more returns made through an enclosing function's frame (`fragret`, below) |
| Jump tables | **131** switch tables resolved. 128 take their size from the guarding bounds check; for 3 no guard was found, so entries are read while they point at code near the function (heuristic) |
| Stack frames | Entry-relative stack-pointer offset at every instruction, and where the return address and saved registers are stored |

The decoder's mnemonics match the ISA table on every code word the table
accepts (`make test`). It also accepts 61 words that the table rejects
because of reserved bits, and marks them invalid.

**A disassembler bug this exposed, now fixed.** `ubidis.py`/`ubidis.c` used
to render every `pdec` source operand from the destination's offset bits, so
every prologue showed `pdec sp,156(sp)`. The real operand is
`An - 4*(128 - off7)` with off7 in the source field, e.g.
`0x400008b4: pdec 0x9c,-44(sp)`. Both disassemblers now render it with a
dedicated `K_PDEC` operand kind (`tools/gen_isa_table.py` emits it for pdec's
source), their outputs still match byte for byte, and `make test` checks every
`pdec` in the code region against `ubidec/decode.py`.

## Semantics and how each was established

These are facts about the CPU and about this binary's compiler conventions.
Each one was checked against the firmware itself. The Ubicom Linux kernel
port (`arch/ubicom32`, in particular `kernel/unaligned_trap.c`, which decodes
instructions) was read online purely as a reference; nothing from it is
vendored.

| Fact | Evidence |
|------|----------|
| `img.bin` runs at **0x40000000** (DDR) | `moveai`+`lea` pointers computed with this base land on the start of C strings ("Blocked packet from %v ...", "Content-Length:"); `main` is reached through such a pointer |
| `moveai An,#imm24` sets An = **imm24 << 7** | startup `0x7ffa00<<7 = 0x3ffd0000` (on-chip RAM); the timer block `0x20002<<7 = 0x01000100` receives the watchdog key 0xa1b2c3d4 |
| EA modes: `(An,Dr)` is **scaled by the access size**; `(An)N++`/`N(An)++` are post/pre increment by N×size | kernel decoder; consistent with array code |
| **`a0` is a zero register** by ABI | compiled code never writes it; hand-written `memcpy` uses a0 as a scratch and then does `movei a0,#0`; `lea.1 X,(a0,dN)` is the compiler's register move |
| `pdec dst,off(An)` = An − 4·(128 − off7) | the frame it allocates matches the epilogue's `lea sp,N(sp)` exactly |
| `cmpi`: source EA in bits 10:0, signed imm16 in bits **26:11**; `movei` to memory is a **16-bit store** | encodings; the watchdog key is written as two `movei` halves |
| `sub d,s1,s2` = **s1 − s2**; carry is **ARM-style** (`cs` = unsigned ≥, `cc` = unsigned <) | counted loops across the image, e.g. `0x40008e90`–`0x40008ec0`: `sub #0,d2(=0),d1; jmpcs` skips the loop when n = 0, and `d2++; sub #0,d2,d1; jmpcc` repeats while i < n. Signed `jmplt` with the counter as s1 (15 loops) fixes the operand order |
| `.s` jumps test the 16-bit flags | they follow `.2` operations; kernel `memcpy` uses them that way |
| moves do not set flags | the kernel's `memcpy` branches on `cmpi` flags across `move`s |
| `bset`/`bclr` set Z from the **old** bit | the spinlock loops `bset (a4),(a4),#0; jmpeq acquired` |
| `shmrg.2 Dn,s1,s2` = (s2 << 16) \| lo16(s1); `bfextu Dn,s,#n` = the low n bits | IP checksum fold compares with `0xffff` built as `movei -1; shmrg.2 d,d,#0` |
| `mulu`/`muls` (16×16) write accumulator 0, read back through `acc0_lo` (0xa4) | `mulu` is always followed by a read of 0xa4 |
| Calls: `call a5,target` (always a5). **Arguments in d0, d1, ...; result in d0; callee-saved a1, a2, a6** | prologues only ever push a5, a1, a2, a6 (3,096 / 2,408 / 2,022 / 878 times); arguments are copied out of d0.. on entry |
| Returns: restore the saved a5 into some register, then `ret reg` or `ret (sp)N++`; leaf functions use `calli a5,0(a5)` | the abstract interpreter follows the return address; 6,904 returns resolved |
| `switch`: `sub #0,#K,dI; jmpcc default; aX = (aT,dI); calli aX,0(aX)` | 131 tables decoded; their entries land on block starts of the same function |

### 16-bit operations and the DSP family: what the lifter may rely on

A static survey of the code region (each `.2` operation with a data-register
destination, followed to the first instruction that reads, overwrites or
branches past that register) gives the following. It shows what compiled code
**depends on**. It does not show what the hardware leaves in the upper half.

| Operation | Next use of the destination | Lifter rule |
|-----------|-----------------------------|-------------|
| `movei Dn,#imm16` | 7,174 times by 32-bit ops (`add.4`, `move.4`, `sub.4` as a signed addend), 1,592 by `shmrg.2` building the high half | **sign-extended to 32 bits** |
| `move.2`/`move.1` load into Dn | 1,171 times by 32-bit ops and 472 times as the source of a 32-bit shift; only 15 are followed by `ext.2` | **zero-extends** (`ext.2`/`ext.1` are the sign extensions, 68 and 926 uses) |
| ALU `.2` ops (`add`, `sub`, `and`, `or`, `xor`, `not`, `lsl`), about 1,800 | almost all by 16-bit consumers (`.2` ops, 16-bit stores) or a `.s` branch on the 16-bit flags. 36 are read as 32 bits. In 16 of them `and.2 Dn,#1,Dm` feeds `lsl.4` by 10 to 15 (bit-field packing at `0x40031450`, `0x40048380`–`0x4004b444`), so bits 16 and up of Dn reach the result. At `0x4000a790`, in the hand-written HRT code, an `and.2` result is used as an `(An,Dn)` index. The rest only look at low bits (`and.4 #15`, `btst #5`, a shift count) or store the word to a stack slot | the destination's **upper half is a function of the 16-bit result, not left-over bits**: the compiler relies on it. Zero- versus sign-extension is **not settled**, since every such use is an `&1` result where both agree. Lift as `(u16)` only when the value is provably below 0x8000; otherwise flag it |
| `lsl.2` | 137 uses, all with 16-bit consumers but one | as the ALU `.2` row |
| `lsr.2`, `asr.2` | **do not occur** in the code region | none needed |
| `madd*`, `msub*` | **do not occur** | none needed |
| `macu`, `macus` | 5 uses, in 2 functions (`0x400956dc`–`0x40095704`, `0x400985fc`) | emit as named intrinsics on acc1 |
| `macs`, `muls.4` | only at `0x401472cc` and `0x40146f48`, past the last function (`0x40146e14`) and in no function; `0x401472c0` holds the ASCII `OFDM`, so this is data | none |

Only the sign of the `.2` ALU extension is left open. No compiled code
depends on it here, and an emulator or a hardware run can settle it
(stage 5 below).

## Only assembly

Some code does not follow the compiler's conventions, and no decompiler can
turn it into meaningful C. Treat it as assembly.

- **Indirect jumps not resolved as a return or switch: 432 before, 322 now
  unresolved.** `make analyze` reports them by group:
  - **119 `hrt`: hand-written hardware-thread (HRT) and driver code**, in
    `0x4000a500`–`0x4000e800`. That code dispatches through
    register-indexed tables (`ret (a3,d15)`, `ret (a6,d9)`), parks threads
    with `suspend`, uses `a0` as an I/O base and drives on-chip I/O and
    `int_set0`/`int_clr0` directly. It is assembly by nature.
  - **180 `computed`: `calli a0,0(a0)` jumps** inside the hand-unrolled
    `memcpy`/`memset` routines. Also assembly by nature: the jump target is a
    computed offset into a run of unrolled moves.
  - **110 resolved as `fragret`** (they were among the 133 "elsewhere"). Each
    is a `ret (sp)N++` / `ret dN` in a "function" that is really a piece of
    another function G, entered part-way through G's frame: switch-case
    bodies and memcpy tails found through pointer tables, and call targets in
    the middle of functions in `0x40051000`–`0x4008d000` (called from the
    early code at `0x40000000`–`0x4000f000`). The same instruction is a
    resolved return in G, so `Program.resolve_shared_returns` marks the block
    `fragret`, records `f.parent = G` and, when both stack pointers are known,
    `f.parent_sp` (G's entry-relative sp at the fragment's entry).
    `out/functions.tsv` lists the parent. Why the early code calls into the
    middle of these functions is an open question for the C printer. It will
    probably emit each fragment as a label inside G.
  - **8 `ram_table`**: `calli` through a table in on-chip RAM
    (`0x3ffd1ad4`, `0x3ffd1b60`, `0x3ffd1b98`). The table is filled at run
    time, so these are indirect calls through function pointers, not switches.
  - **15 `lost_ra`**: what is left, listed by `Program.ijmp_groups()`.
    These are orphans and pointer entries at `0x400032a4`–`0x400035cc` and
    `0x40013db8`/`0x40013e50`, which pop a return address nothing in view
    stored, plus a few `calli 0(a5)` leaves whose a5 was overwritten.
- Instructions with no C meaning, wherever they appear: `suspend`, `bkpt`,
  `setcsr`, `iread`/`iwrite`/`ierase`, the pixel (`px*`) operations, and direct
  writes to special registers (`int_set0`, `csr`, `mt_*`). In C output these
  will appear as inline assembly or named intrinsics.

## What remains for C output

Not done yet. The stages are briefed as follow-ups. An earlier attempt at
stage 1 (`tools/ubidec/ir.py`, a module of plain-tuple expressions and
statements) was stopped by a safety classifier when it was written. Its
content was ordinary decompiler code and the classifier gave no reason. The
briefs therefore state the purpose (the owner modifying their own router's
firmware) and ask for the module to be written in small pieces.

1. **Expression IR / lifter**: give each instruction a meaning as expressions
   and statements. This includes flag-setting instructions folded into
   comparisons, stack slots turned into locals, and prologue/epilogue removal.
2. **Dataflow**: liveness, expression propagation and dead-code removal;
   argument counts and whether a function returns a value, inferred across
   the call graph.
3. **Structuring**: rebuild `if`/`else`, loops and `switch` from the CFG, with
   `goto` where nothing better fits.
4. **C printer**: strings as literals, functions and globals named by
   address, and the assembly-only regions above emitted as inline assembly.
5. **Validation**: run each function's machine code in an emulator and its C
   in an interpreter on the same random inputs, then compare calls, stores and
   return values.
