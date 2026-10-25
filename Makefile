# DIR-655 Rev A firmware reverse-engineering toolkit.
#
# Targets:
#   make ubidis     - build the native C Ubicom32 disassembler (needs a C compiler)
#   make extract    - decompress the firmware -> out/img.bin (needs python3)
#   make disasm     - full labeled disassembly of the code region -> out/img.asm
#   make analyze    - function discovery + call graph -> out/functions.tsv
#   make repack     - recompress out/img.bin -> out/repacked.bin (flashable image)
#   make roundtrip  - repack unmodified img.bin, re-extract, require byte-identical
#   make checksum   - verify the firmware header checksum
#   make test       - extract + CRC-32, ISA table checks, C-vs-Python disasm diff,
#                     ubidec decoder/discovery checks, and the repack round trip
#   make clean

CC      ?= cc
CFLAGS  ?= -O2 -std=c99 -Wall
PY      ?= python3
FW      ?= dir655_firmware_121.bin
IMG     ?= out/img.bin

.PHONY: all ubidis extract disasm analyze repack roundtrip checksum test clean

CHAIN   ?= 128

all: ubidis extract

ubidis: bin/ubidis

bin/ubidis: src/ubidis.c src/ubicom32_isa.inc
	@mkdir -p bin
	$(CC) $(CFLAGS) -Isrc -o $@ src/ubidis.c

$(IMG) extract:
	@mkdir -p out
	$(PY) tools/extract.py $(FW) $(IMG)

# Full labeled disassembly of the code region (0x0 .. 0x148000).
disasm: $(IMG)
	$(PY) tools/ubidis.py --listing -s 0 -n 0x148000 -b 0 $(IMG) > out/img.asm
	@echo "wrote out/img.asm"

analyze: $(IMG)
	$(PY) tools/analyze.py $(IMG) out/functions.tsv
	@echo "wrote out/functions.tsv"

# Recompress a (possibly modified) out/img.bin into a flashable image.
repack: $(IMG)
	$(PY) tools/repack.py $(IMG) -o out/repacked.bin --template $(FW) --chain $(CHAIN)

# Round-trip test: repack the unmodified img.bin, re-extract, require it to be
# byte-identical, and require the header checksum to verify.
roundtrip:
	$(PY) tools/roundtrip_test.py $(FW) --chain $(CHAIN) --workdir out

checksum:
	$(PY) tools/fix_checksum.py --check $(FW)

test: checksum
	$(PY) tests/run_tests.py
	$(PY) tools/roundtrip_test.py $(FW) --chain $(CHAIN) --workdir out

clean:
	rm -rf bin out/img.bin out/img.asm out/functions.tsv out/repacked.bin out/img_rt.bin
