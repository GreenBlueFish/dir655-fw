#!/bin/sh
# Build the ubicom32-elf cross toolchain (binutils 2.19.1 + GCC 4.4.5 with
# Ubicom's GPL Ubicom32 port) from the pinned sources in sources.txt.
#
# Environment (all optional):
#   PREFIX   install directory            (default: toolchain/install)
#   WORK     source + build trees, ~1.5 GB (default: toolchain/build)
#   DL       downloaded sources           (default: toolchain/dl)
#   JOBS     parallel make jobs           (default: number of CPUs)
#   LANGS    GCC front ends: c or c,c++   (default: c,c++)
#
# Host needs: a C compiler (gcc or clang), make, m4, xz, bzip2, patch, curl.
# Tested with GCC 13 on x86-64 Linux (Ubuntu 24.04-era); the old sources
# are compiled with -std=gnu89 -fcommon so a modern compiler accepts them.
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
PREFIX=${PREFIX:-$here/install}
WORK=${WORK:-$here/build}
DL=${DL:-$here/dl}
JOBS=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}
LANGS=${LANGS:-c,c++}
TARGET=ubicom32-elf

# MPFR 2.4 still spells const as GMP 4's __gmp_const, which GMP 6 dropped.
HOST_CFLAGS="-O2 -g0 -std=gnu89 -fcommon -w -D__gmp_const=const"
HOST_CXXFLAGS="-O2 -g0 -w"
# Build every target library with a0 kept out of the register allocator:
# the DIR-655 firmware relies on a0 == 0 (see README "ABI").
TARGET_CFLAGS="-O2 -ffixed-a0"

log() { echo "== $*"; }
run_logged() {  # run_logged <logfile> <command...>
    _log=$1; shift
    if ! "$@" > "$_log" 2>&1; then
        echo "error: '$*' failed; last lines of $_log:" >&2
        tail -n 30 "$_log" >&2
        exit 1
    fi
}

for tool in make m4 patch xz bzip2 curl; do
    command -v $tool >/dev/null || { echo "error: host tool '$tool' not found" >&2; exit 1; }
done
CC=${CC:-$(command -v gcc || command -v cc || command -v clang || true)}
[ -n "$CC" ] || { echo "error: no host C compiler (gcc/cc/clang) found" >&2; exit 1; }
export CC

"$here/scripts/fetch.sh" "$DL"

src=$WORK/src
bld=$WORK/obj
mkdir -p "$src" "$bld" "$PREFIX"
export PATH="$PREFIX/bin:$PATH"

# Teach an old config.sub the ubicom32 CPU name (the Ubicom patches add it to
# configure but not to binutils' config.sub).
fix_config_sub() {
    if ! grep -q '| ubicom32 ' "$1"; then
        sed -i -e 's/^\t| v850 | v850e \\$/\t| ubicom32 | v850 | v850e \\/' \
               -e 's/^\t| v850-\* | v850e-\* | vax-\* \\$/\t| ubicom32-* | v850-* | v850e-* | vax-* \\/' "$1"
    fi
    sh "$1" $TARGET > /dev/null || { echo "error: $1 still rejects $TARGET" >&2; exit 1; }
}

# ---------------------------------------------------------------- binutils
if [ ! -x "$PREFIX/bin/$TARGET-as" ]; then
    log "binutils 2.19.1 + Ubicom32 patch"
    rm -rf "$src/binutils-2.19.1" "$bld/binutils"
    tar -C "$src" -xjf "$DL/binutils-2.19.1.tar.bz2"
    (cd "$src/binutils-2.19.1" && patch -p1 -s < "$DL/binutils-2.19.1-ubicom32.patch")
    fix_config_sub "$src/binutils-2.19.1/config.sub"
    mkdir -p "$bld/binutils"
    cd "$bld/binutils"
    run_logged configure.log env CFLAGS="$HOST_CFLAGS" \
        "$src/binutils-2.19.1/configure" --target=$TARGET --prefix="$PREFIX" \
        --disable-werror --disable-nls
    run_logged make.log make -j"$JOBS" MAKEINFO=true
    run_logged install.log make install MAKEINFO=true
else
    log "binutils already installed in $PREFIX"
fi

# --------------------------------------------------------------------- gcc
if [ ! -x "$PREFIX/bin/$TARGET-gcc" ]; then
    log "gcc 4.4.5 + Ubicom32 patch (languages: $LANGS)"
    rm -rf "$src/gcc-4.4.5" "$src/gmp-6.1.2" "$src/mpfr-2.4.2" "$bld/gcc"
    tar -C "$src" -xjf "$DL/gcc-core-4.4.5.tar.bz2"
    case ",$LANGS," in *,c++,*) tar -C "$src" -xjf "$DL/gcc-g++-4.4.5.tar.bz2" ;; esac
    tar -C "$src" -xJf "$DL/gmp-6.1.2.tar.xz"
    tar -C "$src" -xjf "$DL/mpfr-2.4.2.tar.bz2"
    g=$src/gcc-4.4.5
    (cd "$g" && patch -p1 -s < "$DL/gcc-4.4.5-ubicom32.patch")
    fix_config_sub "$g/config.sub"
    ln -s ../gmp-6.1.2 "$g/gmp"
    ln -s ../mpfr-2.4.2 "$g/mpfr"
    # Only build the libgcc variants this project uses: the default
    # (ubicom32v4) and IP5K (ubicom32v3), each with and without the ipOS ABI.
    # Upstream also multiplies these by -mfdpic and -fleading-underscore.
    t=$g/gcc/config/ubicom32/t-ubicom32
    sed -i -e '/^MULTILIB_OPTIONS += mfdpic$/d' \
           -e '/^MULTILIB_OPTIONS += fno-leading-underscore\/fleading-underscore$/d' "$t"
    grep -q 'ffixed-a0' "$t" || printf '\nTARGET_LIBGCC2_CFLAGS += -ffixed-a0\n' >> "$t"
    mkdir -p "$bld/gcc"
    cd "$bld/gcc"
    run_logged configure.log env CFLAGS="$HOST_CFLAGS" CXXFLAGS="$HOST_CXXFLAGS" \
        CFLAGS_FOR_TARGET="$TARGET_CFLAGS" \
        "$g/configure" --target=$TARGET --prefix="$PREFIX" \
        --enable-languages="$LANGS" --disable-nls --disable-werror \
        --without-headers --with-newlib --disable-shared --disable-threads \
        --disable-libssp --disable-libmudflap --disable-libgomp \
        --disable-decimal-float
    run_logged make-gcc.log make -j"$JOBS" MAKEINFO=true all-gcc
    run_logged make-libgcc.log make -j"$JOBS" MAKEINFO=true all-target-libgcc
    run_logged install.log make MAKEINFO=true install-gcc install-target-libgcc
else
    log "gcc already installed in $PREFIX"
fi

# Licence texts of what was built, kept beside the binaries.
mkdir -p "$PREFIX/share/licenses"
for f in COPYING COPYING3 COPYING.LIB COPYING3.LIB COPYING.RUNTIME; do
    for pkg in gcc-4.4.5 binutils-2.19.1; do
        if [ -f "$src/$pkg/$f" ]; then
            cp "$src/$pkg/$f" "$PREFIX/share/licenses/${pkg%%-*}-$f"
        fi
    done
done

log "done: $("$PREFIX/bin/$TARGET-gcc" --version | head -n 1) in $PREFIX/bin"
