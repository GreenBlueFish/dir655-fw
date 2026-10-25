# DIR-655 Rev A firmware reverse-engineering toolkit.
#
# Targets:
#   make ubidis     - build the native C Ubicom32 disassembler (needs a C compiler)
#   make extract    - decompress the firmware -> out/img.bin (needs python3)
#   make disasm     - full labeled disassembly of the code region -> out/img.asm
#   make analyze  - function discovery + call graph -> out/functions.tsv
#   make checksum   - verify the firmware header checksum
#   make test       - extract + CRC-32, ISA table checks, C-vs-Python disasm diff
#   make clean

CC      ?= cc
CFLAGS  ?= -O2 -std=c99 -Wall
PY      ?= python3
FW      ?= dir655_firmware_121.bin
IMG     ?= out/img.bin

.PHONY: all ubidis extract disasm analyze checksum test clean

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

checksum:
	$(PY) tools/fix_checksum.py --check $(FW)

test: checksum
	$(PY) tests/run_tests.py

clean:
	rm -rf bin out/img.bin out/img.asm out/functions.tsv
