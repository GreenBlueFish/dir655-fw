# toolchain/ — a C (and C++) compiler for the DIR-655 Rev A CPU

This project is separate from the reverse-engineering tools in the rest of the
repo. It builds a GNU cross toolchain for the router's **Ubicom32 IP5K** CPU
(`ubicom32-elf-gcc`, `-g++`, `-as`, `-ld`, `-objdump`, ...). It also contains a small
linker (`tools/dir655patch.py`) that puts what you compile into the
decompressed firmware image `out/img.bin`, so that the top-level `make repack` can
turn it into a flashable `.bin`.

> **Nothing built with this has been run on a real router.** All the tests run
> on the host and check bytes, not behaviour. Read [Before you flash](#before-you-flash).

## What it is built from

The toolchain is Ubicom's GPL port of GCC and binutils to Ubicom32, as published
in OpenWrt's (archived) `ubicom32` target. It is applied to the GNU release
tarballs. Nothing from them is committed here. `scripts/fetch.sh` downloads
every file listed in [`sources.txt`](sources.txt) and refuses any file whose
SHA-256 doesn't match the pinned one.

| Component | Version | Source |
|-----------|---------|--------|
| binutils | 2.19.1 + `600-ubicom32_binutils_20090818.patch` | ftp.gnu.org; patch from git.openwrt.org svn-archive @ `64e5e77b` (GitHub mirror as fallback) |
| GCC (C, C++) | 4.4.5 + `600-ubicom_support.patch` | ftp.gnu.org; patch from the same OpenWrt commit |
| GMP, MPFR | 6.1.2, 2.4.2 | ftp.gnu.org (built in-tree, host-side only) |

The build makes three small changes of its own, all in `scripts/build.sh`:

1. It adds `ubicom32` to binutils' `config.sub`. The Ubicom patch adds the
   target to `configure` but not there.
2. It trims GCC's libgcc multilibs to {default, `-march=ubicom32v3`,
   `-march=ubicom32v4`} × {default, `-mipos-abi`}. Upstream also multiplies
   these by `-mfdpic` and `-fleading-underscore`.
3. It compiles libgcc with `-ffixed-a0` (see [ABI](#abi-what-compiled-code-must-respect)).

**Licences.** GCC and binutils are GPL (libgcc carries the GCC Runtime Library
Exception), and the build copies their `COPYING*` files to
`install/share/licenses/`. If you give someone a toolchain built from this, or
firmware containing libgcc code, the GPL obligations of those components apply.
The scripts, header, tools and tests in this directory are this repo's own.

## Building it

Host requirements: x86-64 Linux (WSL works), a C compiler, `make`, `m4`,
`bzip2`, `xz`, `patch` and `curl`, plus about 1.5 GB of free disk. On Ubuntu/Debian:

```sh
sudo apt install build-essential m4 bzip2 xz-utils patch curl
make -C toolchain toolchain          # fetch + build + install into toolchain/install
```

The whole build took about 2.5 minutes on 16 cores. The old sources are compiled with
`-std=gnu89 -fcommon` so that a modern host compiler accepts them; it was built
and tested with GCC 13. Variables: `PREFIX=` (install directory), `WORK=`
(build trees), `DL=` (downloads), `JOBS=`, `LANGS=c` (skip C++). If the repo is
checked out on a Windows drive, `WORK=$HOME/ubicom32-build` builds much faster
than building under `/mnt/c`.

## Compiling

Use the flags in [`dir655.mk`](dir655.mk). `include` it from your own Makefile,
or pass them by hand:

```sh
PATH=$PWD/toolchain/install/bin:$PATH
ubicom32-elf-gcc -march=ubicom32v3 -mipos-abi -ffixed-a0 -Os -ffreestanding -c foo.c
ubicom32-elf-objdump -d foo.o
```

| Flag | Why |
|------|-----|
| `-march=ubicom32v3` | the IP5K instruction set. GCC's default is v4 (IP7K), which has instructions the IP5K lacks |
| `-mipos-abi` | the ipOS calling convention the firmware uses (below) |
| `-ffixed-a0` | the firmware relies on `a0` always being 0 |
| `-ffreestanding` | there is no C library to link against; see [Limits](#limits) |

C++ works for freestanding code: `ubicom32-elf-g++ $(DIR655_CXXFLAGS)` adds
`-fno-exceptions -fno-rtti`. There is no libstdc++, and global constructors
are never run (`dir655patch.py` refuses `.ctors`). Classes with pure virtual
functions need `extern "C" void __cxa_pure_virtual() { for (;;) ; }`. See
`tests/cases/classes.cpp`.

## Putting compiled code into the firmware

```sh
make extract                                         # out/img.bin (repo root)
make -C toolchain patch SRC="$PWD/my.c $PWD/other.c" [SYMS=$PWD/fw.ld]
                                                     # -> out/img_patched.bin
make repack IMG=out/img_patched.bin                  # -> out/repacked.bin
```

`make -C` runs in `toolchain/`, so give `SRC` and `SYMS` as absolute paths
(or relative to `toolchain/`). `make patch` compiles `SRC`. `dir655patch.py ldscript` then generates a
linker script. `ubicom32-elf-gcc -T` links the objects with libgcc into
`toolchain/build/patch/patch.elf` (a listing and a map file are saved next to
it), and `dir655patch.py apply` writes the result into a copy of the image.

**Where the code goes.** `img.bin` runs at `0x40000000`, and the stock image
ends at `0x40641d00`. The firmware's startup code zeroes `.bss` starting at
`0x40650000`. No `moveai` pointer and no data word in the image refers to
anything between those two addresses, so `.text`, `.rodata`, `.data` and `.bss`
of your code are linked there. That gives **58,112 bytes**, and the link fails if
the code doesn't fit. `img.bin` grows by what you use, and `repack.py` records
the new size. It hasn't been tested whether the router's loader accepts a
larger image, or whether nothing at runtime uses that gap.

**Hooking a firmware function.** In your C file, at file scope:

```c
#include "dir655.h"

int my_version(int a, int b) { ... }       /* same prototype as the original */
DIR655_HOOK(0x40000fc4, my_version);        /* entry address of the original */
```

This overwrites the first two instructions of the original function with
`moveai a3,#hi(my_version); calli a3,lo(my_version)(a3)`, so every call to it
runs your function instead. Your function gets the caller's arguments and
returns to the caller. Get function entry addresses from `make analyze`
(`out/functions.tsv`) and `make disasm`. There is no "call the original"
helper yet: the two overwritten instructions would have to be relocated.

**Overwriting bytes in place.** `DIR655_AT(0x4xxxxxxx)` puts a function or
object at that address, on top of the firmware. `dir655patch.py` prints the
bytes it replaces. Nothing checks that the place you overwrite is unused.

**Calling the firmware.** Either use `DIR655_FW(type, address)` (a cast to a
function pointer), or name the function in a symbols file passed as `SYMS=`
(lines like `fw_strlen = 0x40012340;`) and declare it `extern`. GCC calls it
with `moveai a5,#hi(f); calli a5,lo(f)(a5)`, which reaches any address.

Example: [`examples/hook/hook.c`](examples/hook/hook.c). The tests build it.
It only shows the mechanics, and **must not be flashed**: it replaces a
firmware function whose purpose nobody has worked out.

## ABI: what compiled code must respect

The firmware was built by Ubicom's own GCC-based SDK. These are the parts of
its convention that compiled code relies on. The firmware column comes from
[`docs/DECOMPILER.md`](../docs/DECOMPILER.md). The GCC column was read from
the port's `ubicom32.h` and `ubicom32.c`, and checked on compiled code by
`make test`.

| | Firmware 1.21 | This GCC with `dir655.mk` flags |
|---|---|---|
| return address | `a5` (`call a5,...`) | `a5` (`calli a5,...`) |
| arguments | `d0, d1, ...` | `d0`–`d9`, then the stack |
| result | `d0` | `d0` (64-bit: `d0:d1`) |
| callee-saved | `a1, a2, a6` (all that prologues ever save) | `a1, a2, a6` with `-mipos-abi`; without it, `d10`–`d13` are callee-saved too |
| `a0` | always 0; compiled code never writes it | never allocated, with `-ffixed-a0` (libgcc too) |
| stack | `sp` = `a7`, grows down | same |

The following have **not** been checked against the firmware: arguments
passed on the stack (more than 10 words), structs passed or returned by value,
and variadic functions. Avoid them at the boundary between your code and the
firmware's.

## Tests

```sh
make -C toolchain test       # also run by the top-level `make test`
```

These checks always run:

- every source in `sources.txt` is pinned by a SHA-256 and HTTPS URLs;
- the linker-script generator and the image patcher, on synthetic ELF files:
  placement, `.bss` zero-fill, image growth, and refusing overlaps,
  out-of-range sections, constructors and double application.

Once the toolchain is installed (in `toolchain/install`, or wherever `PREFIX=`
points; set `REQUIRE_TOOLCHAIN=1` to make a missing toolchain fail the run),
these run as well:

- every file in `tests/cases/` (C and C++) compiles with `-Werror`, and links
  into the gap together with the libgcc routines it uses;
- **`tools/ubidis.py` disassembles every linked instruction, and each one matches
  what `objdump` says the compiler emitted** (mnemonic, registers, immediates,
  branch targets; about 390 instructions). The only normalisations are
  ubidis's direct-address register notation (`0x8` = `d2`), its ARM-style
  condition names (`jmpcs` = binutils `jmphs`), and the accumulator operand
  of multiplies, which ubidis doesn't always print;
- no instruction writes `a0`, and prologues save only `a5`, `a1`, `a2` and `a6`;
- `make patch` on `out/img.bin` (if it has been extracted): the hook site
  disassembles to a jump to the compiled function, nothing else in the stock
  image changes, and `repack.py` followed by `extract.py` gives back the
  patched image.

## Limits

- No C library. `memcpy`, `printf` and the rest are whatever you write, or
  the firmware's own (call them by address once you have identified them).
  GCC may emit calls to `memcpy`/`memset` for struct copies. Provide them,
  or point them at the firmware's.
- No global constructors, no C++ exceptions or RTTI, no libstdc++.
- Code runs on the stack and in the thread of whichever firmware code calls
  it. The patch has no entry point of its own.
- 58 KB of space in the gap. For more, you have to overwrite firmware you
  know to be unused, with `DIR655_AT`.

## Before you flash

Nothing produced here, and no repacked image (`make repack`), has been flashed
to a real DIR-655. The loader's handling of a larger `img.bin`, the free gap,
and every hooked function are inferred from the image, not tested on
hardware. A bad image can leave the router unbootable. Before you try one, make
sure you have a way back:

- **Serial console.** The board's UART header gives you the bootloader's
  console. You need a 3.3 V USB-serial adapter; never connect RS-232 levels.
- **Bootloader recovery.** Many D-Link bootloaders have a recovery mode
  (an emergency web page, or TFTP, entered by holding reset at power-on) that
  accepts a stock image. Whether and how the DIR-655 Rev A's loader does this
  hasn't been checked here. Confirm it with the stock `dir655_firmware_121.bin`
  before you need it.

Keep the stock `dir655_firmware_121.bin` to flash back.
