# Ubicom32 C/C++ toolchain for the DIR-655 Rev A (see README.md).
#
#   make toolchain   fetch the pinned GPL sources and build ubicom32-elf-gcc,
#                    -g++, -as, -ld, -objdump ... into install/ (a few minutes)
#   make patch SRC="a.c b.c" [SYMS=syms.ld] [PB=dir]
#                    compile, link into the image's free gap, and write
#                    $(OUT) = $(IMG) with the patch applied; objects, the
#                    ELF, map and listing go to $(PB) (build/patch)
#   make test        the toolchain checks (the compiler parts are skipped
#                    until `make toolchain` has run)
#   make clean       remove the patch build (keeps the toolchain)
#   make distclean   remove the toolchain, its build trees and downloads
#
# Then, from the repo root:  make repack IMG=out/img_patched.bin

TOOLCHAIN_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
include $(TOOLCHAIN_DIR)dir655.mk

PY    ?= python3
REPO  := $(abspath $(TOOLCHAIN_DIR)..)
IMG   ?= $(REPO)/out/img.bin
OUT   ?= $(REPO)/out/img_patched.bin
SRC   ?= $(TOOLCHAIN_DIR)examples/hook/hook.c
SYMS  ?=
PB    ?= $(TOOLCHAIN_DIR)build/patch

.PHONY: all toolchain patch test clean distclean

all: toolchain

toolchain:
	PREFIX=$(PREFIX) $(TOOLCHAIN_DIR)scripts/build.sh

OBJS := $(addprefix $(PB)/,$(addsuffix .o,$(basename $(notdir $(SRC)))))
# Objects are named after the source's basename, so a/foo.c and b/foo.cpp
# would both become $(PB)/foo.o and one would silently replace the other.
SRC_STEMS := $(basename $(notdir $(SRC)))
ifneq ($(words $(SRC_STEMS)),$(words $(sort $(SRC_STEMS))))
$(error SRC has two files with the same name ($(strip $(foreach s,$(sort $(SRC_STEMS)),$(if $(filter-out 1,$(words $(filter $(s),$(SRC_STEMS)))),$(s))))); rename one)
endif

patch: $(PB)/patch.elf
	$(PY) $(TOOLCHAIN_DIR)tools/dir655patch.py apply $(IMG) $< -o $(OUT)
	@echo "next: make -C $(REPO) repack IMG=$(OUT)"

$(PB)/patch.elf: $(OBJS) $(SYMS)
	$(PY) $(TOOLCHAIN_DIR)tools/dir655patch.py ldscript $(OBJS) -o $(PB)/patch.ld
	$(DIR655_CC) $(DIR655_ARCH) -nostdlib -nostartfiles -T $(PB)/patch.ld \
	    $(OBJS) $(SYMS) -lgcc -Wl,-Map,$(PB)/patch.map -o $@
	$(CROSS)objdump -d $@ > $(PB)/patch.lst
	@echo "linked $@ (listing: $(PB)/patch.lst, map: $(PB)/patch.map)"

# One pattern rule per source directory, so SRC may name files anywhere.
define compile_rules
$(PB)/%.o: $(1)%.c $(TOOLCHAIN_DIR)include/dir655.h | $(PB)
	$$(DIR655_CC) $$(DIR655_CFLAGS) -c $$< -o $$@
$(PB)/%.o: $(1)%.cpp $(TOOLCHAIN_DIR)include/dir655.h | $(PB)
	$$(DIR655_CXX) $$(DIR655_CXXFLAGS) -c $$< -o $$@
$(PB)/%.o: $(1)%.S | $(PB)
	$$(DIR655_CC) $$(DIR655_ARCH) -c $$< -o $$@
endef
$(foreach d,$(sort $(dir $(SRC))),$(eval $(call compile_rules,$(d))))

$(PB):
	mkdir -p $@

test:
	$(PY) $(TOOLCHAIN_DIR)tests/run_tests.py

clean:
	rm -rf $(PB)

distclean: clean
	rm -rf $(TOOLCHAIN_DIR)build $(TOOLCHAIN_DIR)install $(TOOLCHAIN_DIR)dl
