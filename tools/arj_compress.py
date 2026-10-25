#!/usr/bin/env python3
"""
From-scratch ARJ method-1 (LZSS + Huffman) *compressor* — the inverse of
tools/arj_decompress.py.

ARJ methods 1-3 share the LHA/LZHUF-lineage scheme: a sliding-window LZSS
parse whose literal/length and position symbols are coded with two canonical
Huffman trees that are rebuilt per block. This module reproduces exactly the
bitstream that tools/arj_decompress.py consumes, so a round trip
(compress -> decompress) is the identity. It is an original implementation
written from the published algorithm; no third-party code is vendored.

The bit layout per block (as read by the decoder) is:

    blocksize            16 bits   -- number of tokens (codes) in the block
    temp tree            write_pt_len(NT=19, nbit=5, i_special=3)
    code/length tree     write_c_len() (CBIT=9 count, RLE'd through temp tree)
    position tree        write_pt_len(NP=17, nbit=5, i_special=-1)
    tokens               each: c-code (+ position code + extra bits for a match)

See docs/FORMAT.md and tools/arj_decompress.py for the matching reader.
"""
import zlib
from array import array

# --- format constants (must match arj_decompress.py) ---
UCHAR_MAX = 255
THRESHOLD = 3
MAXMATCH  = 256
NC   = UCHAR_MAX + MAXMATCH + 2 - THRESHOLD   # 510 code/length symbols
CBIT = 9
NT   = 16 + 3                                 # 19 temp-tree symbols
TBIT = 5
NP   = 17                                     # position-tree symbols
PBIT = 5
MAXLEN = 16                                   # Huffman code-length limit

WINDOW = 1 << 16          # 64 KiB sliding window (dist in [0, 65535])
NICE_LEN = 128            # stop searching once a match this long is found
BLOCKSIZE = 0xFFFF        # max tokens per block (blocksize field is 16-bit)
MIN_MATCH = THRESHOLD     # 3
MAX_MATCH = MAXMATCH      # 256


class _BitWriter:
    """MSB-first bit writer — the inverse of arj_decompress._Bits."""
    __slots__ = ("out", "acc", "nbits")

    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.nbits = 0

    def put(self, n, x):
        if n == 0:
            return
        acc = (self.acc << n) | (x & ((1 << n) - 1))
        nbits = self.nbits + n
        out = self.out
        while nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
        self.acc = acc & ((1 << nbits) - 1)   # keep only pending bits (not history)
        self.nbits = nbits

    def finish(self):
        if self.nbits:
            self.out.append((self.acc << (8 - self.nbits)) & 0xFF)
            self.nbits = 0
        return bytes(self.out)


def _code_lengths(freq, nsyms, maxlen=MAXLEN):
    """Length-limited Huffman code lengths via package-merge.

    Returns (lengths, nused). lengths[s] is the code length for symbol s
    (0 if unused). nused is the number of symbols with freq>0. For nused<=1
    the lengths are all 0 and the caller uses the decoder's constant path.
    """
    lengths = [0] * nsyms
    present = [(f, i) for i, f in enumerate(freq) if f > 0]
    if len(present) <= 1:
        return lengths, len(present)
    present.sort()
    leaves = [(f, (s,)) for f, s in present]
    coins = leaves
    for _ in range(maxlen - 1):
        packaged = []
        i = 0
        m = len(coins) - 1
        while i < m:
            packaged.append((coins[i][0] + coins[i + 1][0],
                             coins[i][1] + coins[i + 1][1]))
            i += 2
        # merge two sorted lists (leaves, packaged) by weight
        merged = []
        a = b = 0
        la, lb = len(leaves), len(packaged)
        while a < la and b < lb:
            if leaves[a][0] <= packaged[b][0]:
                merged.append(leaves[a]); a += 1
            else:
                merged.append(packaged[b]); b += 1
        if a < la:
            merged.extend(leaves[a:])
        if b < lb:
            merged.extend(packaged[b:])
        coins = merged
    use = coins[:2 * len(present) - 2]
    cnt = {}
    for _w, syms in use:
        for s in syms:
            cnt[s] = cnt.get(s, 0) + 1
    for s, l in cnt.items():
        lengths[s] = l
    return lengths, len(present)


def _canonical_codes(lengths, maxlen=MAXLEN):
    """DEFLATE-style canonical codes from code lengths (decoder-compatible)."""
    count = [0] * (maxlen + 1)
    for l in lengths:
        if l:
            count[l] += 1
    code = 0
    next_code = [0] * (maxlen + 1)
    for l in range(1, maxlen + 1):
        code = (code + count[l - 1]) << 1
        next_code[l] = code
    codes = [0] * len(lengths)
    for s, l in enumerate(lengths):
        if l:
            codes[s] = next_code[l]
            next_code[l] += 1
    return codes


def _write_pt_len(bw, length, n, nbit, i_special):
    """Write a position/temp code-length table. Mirrors _read_pt_len."""
    while n > 0 and length[n - 1] == 0:
        n -= 1
    bw.put(nbit, n)
    i = 0
    while i < n:
        k = length[i]
        i += 1
        if k <= 6:
            bw.put(3, k)
        else:
            bw.put(k - 3, (1 << (k - 3)) - 2)   # k-3 bits, all ones but last
        if i == i_special:
            while i < 6 and length[i] == 0:
                i += 1
            bw.put(2, (i - 3) & 3)


def _count_t_freq(c_len):
    """Temp-tree frequencies for the RLE'd c_len stream. Mirrors write_c_len."""
    t_freq = [0] * NT
    n = NC
    while n > 0 and c_len[n - 1] == 0:
        n -= 1
    i = 0
    while i < n:
        k = c_len[i]
        i += 1
        if k == 0:
            count = 1
            while i < n and c_len[i] == 0:
                count += 1
                i += 1
            if count <= 2:
                t_freq[0] += count
            elif count <= 18:
                t_freq[1] += 1
            elif count == 19:
                t_freq[0] += 1
                t_freq[1] += 1
            else:
                t_freq[2] += 1
        else:
            t_freq[k + 2] += 1
    return t_freq, n


def _write_c_len(bw, c_len, n, pt_len, pt_code):
    """Write the code/length tree's lengths, RLE'd through the temp tree."""
    bw.put(CBIT, n)
    i = 0
    while i < n:
        k = c_len[i]
        i += 1
        if k == 0:
            count = 1
            while i < n and c_len[i] == 0:
                count += 1
                i += 1
            if count <= 2:
                for _ in range(count):
                    bw.put(pt_len[0], pt_code[0])
            elif count <= 18:
                bw.put(pt_len[1], pt_code[1])
                bw.put(4, count - 3)
            elif count == 19:
                bw.put(pt_len[0], pt_code[0])
                bw.put(pt_len[1], pt_code[1])
                bw.put(4, 15)
            else:
                bw.put(pt_len[2], pt_code[2])
                bw.put(CBIT, count - 20)
        else:
            bw.put(pt_len[k + 2], pt_code[k + 2])


def _send_block(bw, tokens):
    """Huffman-code and emit one block of tokens.

    tokens is a list of (c, dist): c in 0..NC-1; dist>=0 only when c>UCHAR_MAX
    (a match), else dist is ignored.
    """
    c_freq = [0] * NC
    p_freq = [0] * NP
    for c, dist in tokens:
        c_freq[c] += 1
        if c > UCHAR_MAX:
            pc = dist.bit_length()        # position (length) symbol
            p_freq[pc] += 1

    bw.put(16, len(tokens))

    # --- code/length tree ---
    c_len, c_used = _code_lengths(c_freq, NC)
    if c_used <= 1:
        root = next((i for i, f in enumerate(c_freq) if f > 0), 0)
        bw.put(TBIT, 0); bw.put(TBIT, 0)      # empty temp tree
        bw.put(CBIT, 0); bw.put(CBIT, root)   # constant c tree
        c_code = [0] * NC
    else:
        t_freq, c_n = _count_t_freq(c_len)
        t_len, t_used = _code_lengths(t_freq, NT)
        if t_used <= 1:
            troot = next((i for i, f in enumerate(t_freq) if f > 0), 0)
            bw.put(TBIT, 0); bw.put(TBIT, troot)
            t_code = [0] * NT
        else:
            t_code = _canonical_codes(t_len)
            _write_pt_len(bw, t_len, NT, TBIT, 3)
        _write_c_len(bw, c_len, c_n, t_len, t_code)
        c_code = _canonical_codes(c_len)

    # --- position tree ---
    p_len, p_used = _code_lengths(p_freq, NP)
    if p_used <= 1:
        proot = next((i for i, f in enumerate(p_freq) if f > 0), 0)
        bw.put(PBIT, 0); bw.put(PBIT, proot)
        p_code = [0] * NP
    else:
        p_code = _canonical_codes(p_len)
        _write_pt_len(bw, p_len, NP, PBIT, -1)

    # --- tokens ---
    for c, dist in tokens:
        bw.put(c_len[c], c_code[c])
        if c > UCHAR_MAX:
            pc = dist.bit_length()
            bw.put(p_len[pc], p_code[pc])
            if pc > 1:
                bw.put(pc - 1, dist - (1 << (pc - 1)))


def _parse(data, max_chain):
    """LZSS parse with a 64 KiB window, hash-chain match finder, lazy matching.

    Yields a flat list of (c, dist) tokens.
    """
    n = len(data)
    mv = memoryview(data)
    HASH_BITS = 17
    HASH_SIZE = 1 << HASH_BITS
    head = array('l', [-1]) * HASH_SIZE
    prev = array('l', [-1]) * n if n else array('l')

    def h3(i):
        return ((data[i] << 10) ^ (data[i + 1] << 5) ^ data[i + 2]) & (HASH_SIZE - 1)

    def insert(i):
        hv = h3(i)
        prev[i] = head[hv]
        head[hv] = i

    def lcp(i, j, maxlen):
        """Common-prefix length of data[i:] and data[j:].

        Chunked C-speed compares (memcmp stops at the first differing byte),
        then a byte-wise tail. Fast for the common short-match case and still
        cheap for long matches.
        """
        k = 0
        while k + 8 <= maxlen and mv[i + k:i + k + 8] == mv[j + k:j + k + 8]:
            k += 8
        while k < maxlen and data[i + k] == data[j + k]:
            k += 1
        return k

    def find(i, best_prev_len):
        """Longest match for position i. Returns (length, dist) or (0, 0)."""
        if i + MIN_MATCH > n:
            return 0, 0
        limit = i - WINDOW
        maxlen = min(MAX_MATCH, n - i)
        if maxlen < MIN_MATCH:
            return 0, 0
        best_len = 0
        best_pos = -1
        # only bother to beat what lazy matching already has
        want = max(MIN_MATCH, best_prev_len + 1)
        if want > maxlen:
            return 0, 0
        j = head[h3(i)]
        chain = max_chain
        d = data
        while j >= 0 and j >= limit and chain:
            chain -= 1
            # quick reject: the byte just past the current best must match
            if best_len and d[j + best_len] != d[i + best_len]:
                j = prev[j]
                continue
            k = lcp(i, j, maxlen)
            if k > best_len:
                best_len = k
                best_pos = j
                if k >= maxlen or k >= NICE_LEN:
                    break
            j = prev[j]
        if best_len >= MIN_MATCH and (best_len >= want or best_prev_len == 0):
            return best_len, i - best_pos - 1
        return 0, 0

    tokens = []
    i = 0
    while i < n:
        mlen, mdist = find(i, 0)
        if mlen >= MIN_MATCH:
            # lazy: is a match one byte ahead strictly better?
            if mlen < MAX_MATCH and i + 1 < n:
                insert(i)
                nlen, ndist = find(i + 1, mlen)
                if nlen > mlen:
                    tokens.append((data[i], 0))   # emit literal, take next match
                    i += 1
                    mlen, mdist = nlen, ndist
                    # positions i already inserted; insert the match span below
                    tokens.append((UCHAR_MAX + 1 + (mlen - THRESHOLD), mdist))
                    end = i + mlen
                    i += 1
                    while i < end:
                        if i + MIN_MATCH <= n:
                            insert(i)
                        i += 1
                    continue
                # no better lazy match: take current match (i already inserted)
                tokens.append((UCHAR_MAX + 1 + (mlen - THRESHOLD), mdist))
                end = i + mlen
                i += 1
                while i < end:
                    if i + MIN_MATCH <= n:
                        insert(i)
                    i += 1
                continue
            # match at end of input, or already max length
            tokens.append((UCHAR_MAX + 1 + (mlen - THRESHOLD), mdist))
            end = i + mlen
            while i < end:
                if i + MIN_MATCH <= n:
                    insert(i)
                i += 1
            continue
        tokens.append((data[i], 0))
        if i + MIN_MATCH <= n:
            insert(i)
        i += 1
    return tokens


def compress(data, max_chain=256):
    """Compress bytes into an ARJ method-1 stream decodable by arj_decompress."""
    data = bytes(data)
    tokens = _parse(data, max_chain)
    bw = _BitWriter()
    for off in range(0, len(tokens), BLOCKSIZE):
        _send_block(bw, tokens[off:off + BLOCKSIZE])
    if not tokens:
        # empty input: a single empty block
        _send_block(bw, [])
    return bw.finish(), (zlib.crc32(data) & 0xFFFFFFFF)


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from arj_decompress import decompress
    src = sys.argv[1] if len(sys.argv) > 1 else "out/img.bin"
    raw = open(src, "rb").read()
    comp, crc = compress(raw)
    print("in=%d  out=%d  ratio=%.3f  crc=0x%08x"
          % (len(raw), len(comp), len(comp) / max(1, len(raw)), crc))
    back = decompress(comp, len(raw), crc)
    print("round-trip:", "OK" if back == raw else "MISMATCH")
