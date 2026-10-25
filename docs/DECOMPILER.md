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
| CFG | 6,904 return blocks, 31,130 conditional branches, 12,199 jumps, 2 tail calls |
| Jump tables | **131** switch tables resolved. 128 take their size from the guarding bounds check; for 3 no guard was found, so entries are read while they point at code near the function (heuristic) |
| Stack frames | Entry-relative stack-pointer offset at every instruction, and where the return address and saved registers are stored |

The decoder's mnemonics match the ISA table on every code word the table
accepts (`make test`). It also accepts 61 words that the table rejects
because of reserved bits, and marks them invalid.

**A disassembler bug this exposed.** `ubidis.py`/`ubidis.c` render every
`pdec` operand wrongly. The ISA table's operand template takes the
offset bits from the destination field, so every prologue shows
`pdec sp,156(sp)`. The real operand is `sp - 4*(128 - off7)`, e.g.
`0x400008b4: pdec sp,-44(sp)`. `ubidec/decode.py` decodes it correctly. The
text disassembler is left unchanged here, so its byte-for-byte C/Python
comparison stays valid; fixing its renderer is a separate change.

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

Not yet verified: what `.2` ALU operations leave in the upper half of a
register destination, `lsl.2`/`lsr.2`, and the DSP
multiply-accumulate family (`mac*`, `madd*`, `msub*`). These are rare in compiled code.

## Only assembly

Some code does not follow the compiler's conventions, and no decompiler can
turn it into meaningful C. Treat it as assembly.

- **Unresolved indirect jumps: 432.** They fall into three groups:
  - **119 in hand-written hardware-thread (HRT) and driver code**, roughly
    `0x4000a500`–`0x4000e800`. That code dispatches through
    register-indexed tables (`ret (a3,d15)`, `ret (a6,d9)`), parks threads
    with `suspend`, uses `a0` as an I/O base and drives on-chip I/O and
    `int_set0`/`int_clr0` directly. It is assembly by nature.
  - **180 `calli a0,0(a0)` computed jumps** inside the hand-unrolled
    `memcpy`/`memset` routines. Also assembly by nature: the jump target is a
    computed offset into a run of unrolled moves.
  - **133 elsewhere**, spread over `0x40000000`–`0x40140000`. Most of them are
    `ret (sp)N++` / `ret dN` returns where the abstract interpreter lost track
    of the return address, at alternate entry points and shared epilogues
    (e.g. `0x400531ec`, `0x40051000`). These are probably **ordinary
    returns that a better analysis will resolve**, not assembly-only code.
    The current report does not separate them out.
- Instructions with no C meaning, wherever they appear: `suspend`, `bkpt`,
  `setcsr`, `iread`/`iwrite`/`ierase`, the pixel (`px*`) operations, and direct
  writes to special registers (`int_set0`, `csr`, `mt_*`). In C output these
  will appear as inline assembly or named intrinsics.

## What remains for C output

Not done yet; to be briefed separately:

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
