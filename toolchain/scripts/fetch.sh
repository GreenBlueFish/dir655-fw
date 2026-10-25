#!/bin/sh
# Download every file in sources.txt into $1 (default: toolchain/dl) and
# verify its SHA-256. A file already present is re-verified, not re-fetched.
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
dl=${1:-$here/dl}
mkdir -p "$dl"
grep -v '^#' "$here/sources.txt" | while read -r file sum urls; do
    [ -n "$file" ] || continue
    if [ -f "$dl/$file" ] && echo "$sum  $dl/$file" | sha256sum -c --status; then
        echo "ok      $file"
        continue
    fi
    rm -f "$dl/$file.part"
    for url in $urls; do
        echo "fetch   $file  <- $url"
        if curl -fsSL --retry 2 -o "$dl/$file.part" "$url" &&
           echo "$sum  $dl/$file.part" | sha256sum -c --status; then
            mv "$dl/$file.part" "$dl/$file"
            break
        fi
        echo "        (failed or wrong sha256)"
        rm -f "$dl/$file.part"
    done
    if [ ! -f "$dl/$file" ]; then
        echo "error: could not fetch $file with sha256 $sum" >&2
        exit 1
    fi
done
