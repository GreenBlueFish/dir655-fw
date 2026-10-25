#!/usr/bin/env python3
"""
Unit tests for tools/arj_compress.py's degenerate-tree paths (run by `make test`).

The real image never produces a block whose code/length, temp or position
alphabet has only one symbol in use, so _send_block's constant-tree fallbacks
(c_used <= 1, t_used <= 1, p_used <= 1) are not reached by the round trip.
A low-entropy modified img.bin could reach them, so each is driven here, both
with hand-built token blocks (checked to really take the branch) and through
compress() on small pathological inputs; every stream must decompress back.

Exits non-zero on any failure.
"""
import os, random, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "tools"))
import arj_compress as ac
from arj_decompress import decompress

failed = False


def check(ok, what):
    global failed
    print("%s  %s" % ("ok  " if ok else "FAIL", what))
    if not ok:
        failed = True


def lit(b):
    return (b, 0)


def match(length, dist):
    """A match token: copy `length` bytes from dist+1 bytes back."""
    return (ac.UCHAR_MAX + 1 + length - ac.THRESHOLD, dist)


def expand(tokens):
    """What the decoder should produce for a token list."""
    out = bytearray()
    for c, dist in tokens:
        if c <= ac.UCHAR_MAX:
            out.append(c)
        else:
            s = len(out) - dist - 1
            for k in range(c - (ac.UCHAR_MAX + 1) + ac.THRESHOLD):
                out.append(out[s + k])
    return bytes(out)


def used(tokens):
    """(c_used, t_used, p_used) as _send_block computes them."""
    c_freq, p_freq = [0] * ac.NC, [0] * ac.NP
    for c, dist in tokens:
        c_freq[c] += 1
        if c > ac.UCHAR_MAX:
            p_freq[dist.bit_length()] += 1
    c_len, c_used = ac._code_lengths(c_freq, ac.NC)
    t_used = None
    if c_used > 1:
        t_freq, _ = ac._count_t_freq(c_len)
        t_used = ac._code_lengths(t_freq, ac.NT)[1]
    return c_used, t_used, ac._code_lengths(p_freq, ac.NP)[1]


def decodes(blocks):
    """Emit each token block with _send_block; True if it decodes back."""
    bw = ac._BitWriter()
    for blk in blocks:
        ac._send_block(bw, blk)
    want = expand([t for blk in blocks for t in blk])
    try:
        return decompress(bw.finish(), len(want)) == want
    except Exception as e:
        print("      decode raised %r" % e)
        return False


# (name, block, predicate on (c_used, t_used, p_used))
BLOCKS = [
    ("one literal: c_used=1, p_used=0",
     [lit(0x41)], lambda c, t, p: c == 1 and p == 0),
    ("one literal repeated: c_used=1",
     [lit(0x00)] * 300, lambda c, t, p: c == 1 and p == 0),
    # a block may not start the stream with a match (nothing to copy yet);
    # test_blocks() puts a literal block in front of it
    ("block of one repeated match: c_used=1, p_used=1",
     [match(256, 0)] * 30, lambda c, t, p: c == 1 and p == 1),
    ("two literals of equal length: t_used=1",
     [lit(0x00), lit(0x01)] * 50, lambda c, t, p: c == 2 and t == 1 and p == 0),
    ("literal + matches at one distance: p_used=1",
     [lit(0x5a)] + [match(256, 0)] * 40 + [match(10, 0)],
     lambda c, t, p: c > 1 and p == 1),
    ("matches at distance 0 and 1 only: p_used=2 (normal p tree)",
     [lit(1), lit(2)] + [match(4, 0), match(4, 1)] * 20,
     lambda c, t, p: p == 2),
]


def test_blocks():
    for name, blk, pred in BLOCKS:
        cu, tu, pu = used(blk)
        check(pred(cu, tu, pu), "%s (c=%d t=%s p=%d)" % (name, cu, tu, pu))
        prefix = [] if blk[0][0] <= ac.UCHAR_MAX else [[lit(0x33)]]
        check(decodes(prefix + [blk]), "  ... decodes back")
    # every degenerate kind back to back in one stream
    check(decodes([[lit(9)]] + [b for _, b, _ in BLOCKS]),
          "all degenerate blocks in one stream decode back")


def roundtrip(data, chain=64):
    comp, crc = ac.compress(data, max_chain=chain)
    try:
        return decompress(comp, len(data), crc) == data
    except Exception as e:
        print("      decode raised %r" % e)
        return False


def test_compress():
    cases = [
        ("empty", b""),
        ("one byte", b"\x00"),
        ("two equal bytes", b"\xff\xff"),
        ("100 KiB of zeros", b"\x00" * 102400),
        ("two-symbol alternation", b"\x00\x01" * 3000),
        ("one byte then a long run", b"A" + b"B" * 70000),
        ("0xFF flash padding, 1 MiB", b"\xff" * (1 << 20)),
    ]
    rng = random.Random(655)
    cases.append(("random over a 2-byte alphabet",
                  bytes(rng.choice(b"\x00\x01") for _ in range(20000))))
    # mostly literals: more than one 0xFFFF-token block
    cases.append(("random bytes spanning 3 blocks",
                  bytes(rng.randrange(256) for _ in range(140000))))
    for name, data in cases:
        check(roundtrip(data), "compress/decompress: %s (%d bytes)"
              % (name, len(data)))
    for n in range(300):
        size = rng.randrange(1, 600)
        alpha = bytes(rng.sample(range(256), rng.randrange(1, 4)))
        data = bytes(rng.choice(alpha) for _ in range(size))
        if not roundtrip(data, chain=rng.choice((1, 8, 256))):
            check(False, "fuzz case %d: %r" % (n, data[:40]))
            return
    check(True, "300 random low-entropy inputs round-trip")


def main():
    test_blocks()
    test_compress()
    print("FAILED" if failed else "all checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
