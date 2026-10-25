# Goals

I have an image file for a D-Link DIR-655 Router, firmware version 1.21, hardware revision Rev A. I want to modify the firmware but there doesn't seem to be a disassembler available for the .bin file. Disassemble it. You may read other people's code online for reference but do NOT download it — write the disassembler from scratch. Ideally take the .bin and turn it into C or C++ (or whatever language works best). The original file was at C:\dev\AI_Projects\DIR-655_REVA_FIRMWARE_1.21 on my machine.

---

## HALTED — 2026-10-25

**Status: BLOCKED by Claude safety check. All work on this project (dir655-fw) is halted by the person as of 2026-10-25.**

**Reason:** The Claude safety classifier repeatedly stopped the firmware-to-C decompilation stage (3+ times, including while only reading disassembly). That stage cannot be completed on Claude.

**What is done and merged** (everything up to and including the static-analysis layer):
- Unpacker
- Repacker (`make repack`)
- Ubicom32 disassembler
- `make analyze`: 6,855 functions with call graph, CFG, switch tables, and documented CPU semantics/ABI

**What is NOT done:** the remaining expression-IR / lifter / dataflow / structuring / C-printer stages.

**Instruction:** Do not attempt further C-emission work on Claude. If this project is resumed later, it must be on a non-Claude model that the person adds.
