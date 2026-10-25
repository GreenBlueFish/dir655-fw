# DIR-655 Rev A firmware reverse-engineering toolkit.
#
# Targets:
#   make ubidis     - build the native C Ubicom32 disassembler (needs a C compiler)
#   make extract    - decompress the firmware -> out/img.bin (needs python3)
#   make disasm     - full labeled disassembly of the code region -> out/img.asm
#   make analyze    - function discovery + call graph -> out/functions.tsv
#   make repack     - recompress out/img.bin -> out/repacked.bin (flashable image)
#   make roundtrip  - repack unmodified and modified img.bin, re-extract, require
#                     byte-identical
#   make checksum   - verify the firmware header checksum
#   make test       - extract + CRC-32, ISA table checks, C-vs-Python disasm diff,
#                     ubidec decoder/discovery checks, compressor edge-case
#                     unit tests, the repack round trip, and the
#                     toolchain/ checks (see toolchain/README.md); one line
#                     per suite, full logs in out/test-*.log, V=1 for all
#   make clean

CC      ?= cc
CFLAGS  ?= -O2 -std=c99 -Wall
PY      ?= python3
FW      ?= dir655_firmware_121.bin
IMG     ?= out/img.bin

.PHONY: all ubidis extract disasm analyze repack roundtrip checksum test clean

# LZSS hash-chain depth for repack and the round trip; 256 is repack.py's
# default too, so `make test` checks the settings a user actually ships.
CHAIN   ?= 256

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

# Round-trip test: repack the unmodified img.bin and a copy with a few bytes
# changed, re-extract each, require byte-identical results, and require the
# header checksum to verify.
roundtrip:
	$(PY) tools/roundtrip_test.py $(FW) --chain $(CHAIN) --workdir out

checksum:
	$(PY) tools/fix_checksum.py --check $(FW)

# Each suite's full output goes to out/test-<name>.log and only its last line
# is printed, so the whole run fits a short log; a failing suite prints its
# FAIL lines and the tail of its log. `make test V=1` prints everything.
V ?=
ifeq ($(V),1)
suite = $(2)
else
suite = @mkdir -p out; if $(2) > out/test-$(1).log 2>&1; then \
	  echo "PASS $(1): $$(tail -n 1 out/test-$(1).log)"; \
	else \
	  echo "FAIL $(1) (full log: out/test-$(1).log)"; grep -E 'FAIL' out/test-$(1).log; \
	  echo "--- last 30 lines:"; tail -n 30 out/test-$(1).log; exit 1; \
	fi
endif

test:
	$(call suite,checksum,$(PY) tools/fix_checksum.py --check $(FW))
	$(call suite,disasm,$(PY) tests/run_tests.py)
	$(call suite,compress,$(PY) tests/test_arj_compress.py)
	$(call suite,roundtrip,$(PY) tools/roundtrip_test.py $(FW) --chain $(CHAIN) --workdir out)
	$(call suite,toolchain,$(PY) toolchain/tests/run_tests.py)
	@echo "all test suites passed"

clean:
	rm -rf bin out/test-*.log out/img.bin out/img.asm out/functions.tsv out/repacked.bin out/img_rt.bin \
	       out/img_repacked.bin out/img_mod.bin out/img_mod_repacked.bin out/img_mod_rt.bin
