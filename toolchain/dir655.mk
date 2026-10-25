# Compiler settings for code that runs inside the DIR-655 Rev A firmware.
# Included by toolchain/Makefile; include it from your own Makefile too.
#
#   -march=ubicom32v3  IP5K instruction set (DIR-655 Rev A)
#   -mipos-abi         ipOS ABI: only a1, a2, a6 are callee-saved (matches
#                      the firmware's prologues, docs/DECOMPILER.md)
#   -ffixed-a0         never allocate a0; firmware code relies on a0 == 0
#   -ffreestanding     no hosted C library; there is no libc to link
TOOLCHAIN_DIR ?= $(dir $(lastword $(MAKEFILE_LIST)))
PREFIX        ?= $(TOOLCHAIN_DIR)install
CROSS         ?= $(PREFIX)/bin/ubicom32-elf-
DIR655_CC     ?= $(CROSS)gcc
DIR655_CXX    ?= $(CROSS)g++
DIR655_ARCH   := -march=ubicom32v3 -mipos-abi -ffixed-a0
DIR655_CFLAGS ?= $(DIR655_ARCH) -Os -ffreestanding -fno-common -Wall \
                 -I$(TOOLCHAIN_DIR)include
DIR655_CXXFLAGS ?= $(DIR655_CFLAGS) -fno-exceptions -fno-rtti
