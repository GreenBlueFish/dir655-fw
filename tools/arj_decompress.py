#!/usr/bin/env python3
"""
From-scratch ARJ method-1 (LZ77 + Huffman) decompressor.

The D-Link DIR-655 Rev A firmware stores its real image as an ARJ archive
(archive member "img.bin") embedded in the flash image. ARJ compression
methods 1-3 share one decoder: a sliding-window LZ77 with two canonical
Huffman trees (a "code/length" tree and a "position" tree) rebuilt per
block, in the LHA/LZHUF lineage. This is an original implementation written
from the published algorithm; no third-party code is vendored.

Correctness is self-checking: the ARJ local-file header carries the member's
original size and CRC-32, and decompress() verifies both.
"""
import zlib

UCHAR_MAX = 255
THRESHOLD = 3
MAXMATCH  = 256
NC   = UCHAR_MAX + MAXMATCH + 2 - THRESHOLD   # 510 code/length symbols
CBIT = 9
CODE_BIT = 16
NT   = CODE_BIT + 3                           # 19 temp-tree symbols
TBIT = 5
NP   = 17                                     # position-tree symbols
PBIT = 5


class _Bits:
    """LHA-style big-endian bit reader over a 16-bit window."""
    def __init__(self, buf):
        self.buf = buf
        self.pos = 0
        self.bitbuf = 0
        self.subbit = 0
        self.bitcount = 0
        self.fill(16)

    def _next(self):
        if self.pos < len(self.buf):
            b = self.buf[self.pos]
            self.pos += 1
            return b
        return 0

    def fill(self, n):
        while n > self.bitcount:
            n -= self.bitcount
            self.bitbuf = ((self.bitbuf << self.bitcount) |
                           (self.subbit >> (8 - self.bitcount))) & 0xFFFF
            self.subbit = self._next()
            self.bitcount = 8
        self.bitcount -= n
        self.bitbuf = ((self.bitbuf << n) |
                       (self.subbit >> (8 - n))) & 0xFFFF
        self.subbit = (self.subbit << n) & 0xFF

    def getbits(self, n):
        x = self.bitbuf >> (16 - n)
        self.fill(n)
        return x

    def peek(self, n):
        return self.bitbuf >> (16 - n)


class _Decoder:
    def __init__(self, comp):
        self.b = _Bits(comp)
        self.left = [0] * (2 * NC)
        self.right = [0] * (2 * NC)
        self.c_len = [0] * NC
        self.pt_len = [0] * max(NT, NP)
        self.c_table = [0] * 4096
        self.pt_table = [0] * 256
        self.blocksize = 0

    def _make_table(self, nchar, bitlen, tablebits, table):
        count = [0] * 17
        weight = [0] * 17
        start = [0] * 18
        for i in range(nchar):
            count[bitlen[i]] += 1
        for i in range(1, 17):
            start[i + 1] = start[i] + (count[i] << (16 - i))
        if start[17] != (1 << 16):
            raise ValueError("bad Huffman table (start[17]=%d)" % start[17])
        jutbits = 16 - tablebits
        for i in range(1, tablebits + 1):
            start[i] >>= jutbits
            weight[i] = 1 << (tablebits - i)
        for i in range(tablebits + 1, 17):
            weight[i] = 1 << (16 - i)
        i = start[tablebits + 1] >> jutbits
        if i != (1 << tablebits):
            k = 1 << tablebits
            while i != k:
                table[i] = 0
                i += 1
        avail = nchar
        mask = 1 << (15 - tablebits)
        for ch in range(nchar):
            ln = bitlen[ch]
            if ln == 0:
                continue
            nextcode = start[ln] + weight[ln]
            if ln <= tablebits:
                for i in range(start[ln], nextcode):
                    table[i] = ch
            else:
                k = start[ln]
                kind = 0            # 0=table, 1=left, 2=right
                pidx = k >> jutbits
                i = ln - tablebits
                while i != 0:
                    if kind == 0:
                        cur = table[pidx]
                    elif kind == 1:
                        cur = self.left[pidx]
                    else:
                        cur = self.right[pidx]
                    if cur == 0:
                        self.right[avail] = 0
                        self.left[avail] = 0
                        if kind == 0:
                            table[pidx] = avail
                        elif kind == 1:
                            self.left[pidx] = avail
                        else:
                            self.right[pidx] = avail
                        cur = avail
                        avail += 1
                    if k & mask:
                        kind, pidx = 2, cur
                    else:
                        kind, pidx = 1, cur
                    k = (k << 1) & 0xFFFFFFFF
                    i -= 1
                if kind == 0:
                    table[pidx] = ch
                elif kind == 1:
                    self.left[pidx] = ch
                else:
                    self.right[pidx] = ch
            start[ln] = nextcode

    def _read_pt_len(self, nn, nbit, i_special):
        b = self.b
        n = b.getbits(nbit)
        if n == 0:
            c = b.getbits(nbit)
            for i in range(nn):
                self.pt_len[i] = 0
            for i in range(256):
                self.pt_table[i] = c
            return
        i = 0
        while i < n:
            c = b.peek(3)
            if c == 7:
                mask = 1 << (16 - 4)
                while mask & b.bitbuf:
                    mask >>= 1
                    c += 1
            b.fill(3 if c < 7 else c - 3)
            self.pt_len[i] = c
            i += 1
            if i == i_special:
                c = b.getbits(2)
                while c > 0:
                    self.pt_len[i] = 0
                    i += 1
                    c -= 1
        while i < nn:
            self.pt_len[i] = 0
            i += 1
        self._make_table(nn, self.pt_len, 8, self.pt_table)

    def _read_c_len(self):
        b = self.b
        n = b.getbits(CBIT)
        if n == 0:
            c = b.getbits(CBIT)
            for i in range(NC):
                self.c_len[i] = 0
            for i in range(4096):
                self.c_table[i] = c
            return
        i = 0
        while i < n:
            c = self.pt_table[b.peek(8)]
            if c >= NT:
                mask = 1 << (16 - 9)
                while c >= NT:
                    c = self.right[c] if (b.bitbuf & mask) else self.left[c]
                    mask >>= 1
            b.fill(self.pt_len[c])
            if c <= 2:
                if c == 0:
                    c = 1
                elif c == 1:
                    c = b.getbits(4) + 3
                else:
                    c = b.getbits(CBIT) + 20
                while c > 0:
                    self.c_len[i] = 0
                    i += 1
                    c -= 1
            else:
                self.c_len[i] = c - 2
                i += 1
        while i < NC:
            self.c_len[i] = 0
            i += 1
        self._make_table(NC, self.c_len, 12, self.c_table)

    def _decode_c(self):
        b = self.b
        if self.blocksize == 0:
            self.blocksize = b.getbits(16)
            self._read_pt_len(NT, TBIT, 3)
            self._read_c_len()
            self._read_pt_len(NP, PBIT, -1)
        self.blocksize -= 1
        j = self.c_table[b.peek(12)]
        if j >= NC:
            mask = 1 << (16 - 13)
            while j >= NC:
                j = self.right[j] if (b.bitbuf & mask) else self.left[j]
                mask >>= 1
        b.fill(self.c_len[j])
        return j

    def _decode_p(self):
        b = self.b
        j = self.pt_table[b.peek(8)]
        if j >= NP:
            mask = 1 << (16 - 9)
            while j >= NP:
                j = self.right[j] if (b.bitbuf & mask) else self.left[j]
                mask >>= 1
        b.fill(self.pt_len[j])
        if j != 0:
            j = (1 << (j - 1)) + b.getbits(j - 1)
        return j

    def run(self, origsize):
        out = bytearray()
        self.blocksize = 0
        while len(out) < origsize:
            c = self._decode_c()
            if c <= UCHAR_MAX:
                out.append(c)
            else:
                length = c - (UCHAR_MAX + 1) + THRESHOLD
                dist = self._decode_p()
                s = len(out) - dist - 1
                for k in range(length):
                    out.append(out[s + k])
        return bytes(out[:origsize])


def decompress(comp, orig_size, expected_crc=None):
    """Decompress an ARJ method-1 stream. Verifies size and (optionally) CRC-32."""
    out = _Decoder(comp).run(orig_size)
    if len(out) != orig_size:
        raise ValueError("size mismatch: got %d, expected %d" % (len(out), orig_size))
    if expected_crc is not None:
        crc = zlib.crc32(out) & 0xFFFFFFFF
        if crc != expected_crc:
            raise ValueError("CRC mismatch: got 0x%08x, expected 0x%08x"
                             % (crc, expected_crc))
    return out
