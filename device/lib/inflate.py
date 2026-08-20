# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# ------------------------------------------------------------
# inflate.py  -  streaming raw-DEFLATE decompressor, pure Python
# ------------------------------------------------------------
# CircuitPython's zlib only offers zlib.decompress(), which returns the whole
# result as ONE object. Its collector does not move objects, so on a device
# that has been running a while the largest free block is much smaller than the
# total free memory - opening an EPUB and parsing its manifest is enough to cut
# the biggest block from ~120KB to ~32KB - and a chapter bigger than that block
# cannot be decompressed at all, however much memory is free overall.
#
# MicroPython solves this with `deflate.DeflateIO`, but that is a C module:
# having it in CircuitPython would mean building custom firmware. This is the
# same thing in Python. It holds only the 32KB sliding window that DEFLATE
# requires, so output size stops mattering.
#
# It is much slower than the built-in, so it is meant as a fallback for the
# occasional chapter that will not fit, not as the normal path.

BUILD = "inflate-2 stored-blocks-stream"

_MAXBITS = 15

# RFC 1951 length/distance tables
_LEN_BASE = (3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43,
             51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258)
_LEN_EXTRA = (0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3,
              4, 4, 4, 4, 5, 5, 5, 5, 0)
_DIST_BASE = (1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257,
              385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289,
              16385, 24577)
_DIST_EXTRA = (0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8,
               9, 9, 10, 10, 11, 11, 12, 12, 13, 13)
_CLEN_ORDER = (16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15)


def _build(lengths):
    """Canonical Huffman table as (count-per-length, symbols-in-order)."""
    count = [0] * (_MAXBITS + 1)
    for l in lengths:
        count[l] += 1
    count[0] = 0
    offs = [0] * (_MAXBITS + 2)
    for i in range(1, _MAXBITS + 1):
        offs[i + 1] = offs[i] + count[i]
    symbol = [0] * len(lengths)
    for sym in range(len(lengths)):
        l = lengths[sym]
        if l:
            symbol[offs[l]] = sym
            offs[l] += 1
    return count, symbol


class RawInflater:
    """Inflates a raw DEFLATE stream, exposing .read(n) like a file.

    `src` is anything with .read(n). Output is produced on demand, so memory
    stays at the 32KB window plus whatever the caller has asked for.
    """

    def __init__(self, src, window=None, chunk=512):
        self.src = src
        self.chunk = chunk
        self.win = window if window is not None else bytearray(32768)
        self.wsize = len(self.win)
        self.wpos = 0
        self.out = bytearray()
        self.bitbuf = 0
        self.nbits = 0
        self.inbuf = b""
        self.inpos = 0
        self.eof = False          # no more compressed input
        self.done = False         # final block consumed
        self._last = 0            # current block is the final one
        # Mid-block resume: (ltable, dtable) for a Huffman block, or
        # (None, bytes-still-to-copy) for a stored one.
        self.state = None

    # ---- input -------------------------------------------------------
    def _byte(self):
        if self.inpos >= len(self.inbuf):
            self.inbuf = self.src.read(self.chunk)
            self.inpos = 0
            if not self.inbuf:
                self.eof = True
                return 0
        b = self.inbuf[self.inpos]
        self.inpos += 1
        return b

    def _bits(self, n):
        if n == 0:
            return 0
        while self.nbits < n:
            self.bitbuf |= self._byte() << self.nbits
            self.nbits += 8
        v = self.bitbuf & ((1 << n) - 1)
        self.bitbuf >>= n
        self.nbits -= n
        return v

    def _decode(self, table):
        """Walk the canonical code one bit at a time."""
        count, symbol = table
        code = first = index = 0
        for length in range(1, _MAXBITS + 1):
            code |= self._bits(1)
            cnt = count[length]
            if code - first < cnt:
                return symbol[index + (code - first)]
            index += cnt
            first = (first + cnt) << 1
            code <<= 1
        raise ValueError("bad Huffman code")

    # ---- output ------------------------------------------------------
    def _emit(self, byte):
        self.out.append(byte)
        self.win[self.wpos] = byte
        self.wpos += 1
        if self.wpos == self.wsize:
            self.wpos = 0

    # ---- blocks ------------------------------------------------------
    def _stored(self, want, remaining=None):
        """Copy a stored (literal) block. True if finished, False if paused.

        Must stop at `want` like a compressed block does. A stored block holds
        up to 64KB, and running it to completion buffers all of it - precisely
        the large allocation this class exists to avoid. It goes unnoticed on
        prose, which always compresses and so never arrives stored; it bites on
        an image, which does not compress and so arrives stored in full.
        """
        if remaining is None:
            self.bitbuf = 0
            self.nbits = 0        # skip to a byte boundary
            remaining = self._byte() | (self._byte() << 8)
            self._byte()
            self._byte()          # one's complement, not checked
        while remaining:
            self._emit(self._byte())
            remaining -= 1
            if remaining and len(self.out) >= want:
                self.state = (None, remaining)   # None marks a stored block
                return False
        return True

    def _dynamic_tables(self):
        hlit = self._bits(5) + 257
        hdist = self._bits(5) + 1
        hclen = self._bits(4) + 4

        clen = [0] * 19
        for i in range(hclen):
            clen[_CLEN_ORDER[i]] = self._bits(3)
        ctable = _build(clen)

        lengths = []
        while len(lengths) < hlit + hdist:
            sym = self._decode(ctable)
            if sym < 16:
                lengths.append(sym)
            elif sym == 16:
                prev = lengths[-1]
                for _ in range(3 + self._bits(2)):
                    lengths.append(prev)
            elif sym == 17:
                for _ in range(3 + self._bits(3)):
                    lengths.append(0)
            else:
                for _ in range(11 + self._bits(7)):
                    lengths.append(0)
        return _build(lengths[:hlit]), _build(lengths[hlit:hlit + hdist])

    def _fixed_tables(self):
        lit = [8] * 144 + [9] * 112 + [7] * 24 + [8] * 8
        dist = [5] * 30
        return _build(lit), _build(dist)

    def _compressed(self, ltable, dtable, want):
        """Decode symbols until `want` bytes are pending or the block ends."""
        while True:
            sym = self._decode(ltable)
            if sym < 256:
                self._emit(sym)
            elif sym == 256:
                return True                      # end of block
            else:
                sym -= 257
                if sym >= len(_LEN_BASE):
                    raise ValueError("bad length symbol")
                length = _LEN_BASE[sym] + self._bits(_LEN_EXTRA[sym])
                dsym = self._decode(dtable)
                dist = _DIST_BASE[dsym] + self._bits(_DIST_EXTRA[dsym])
                src = self.wpos - dist
                if src < 0:
                    src += self.wsize
                for _ in range(length):          # byte-wise: copies may overlap
                    b = self.win[src]
                    src += 1
                    if src == self.wsize:
                        src = 0
                    self._emit(b)
            if len(self.out) >= want:
                self.state = (ltable, dtable)    # resume mid-block next call
                return False
            if self.eof and self.nbits == 0 and self.inpos >= len(self.inbuf):
                raise ValueError("truncated DEFLATE stream")

    # ---- public ------------------------------------------------------
    def read(self, size=512):
        while len(self.out) < size and not self.done:
            if self.state is not None:
                ltable, dtable = self.state
                self.state = None
                if ltable is None:
                    finished = self._stored(size, remaining=dtable)
                else:
                    finished = self._compressed(ltable, dtable, size)
                if finished and self._last:
                    self.done = True
                continue

            self._last = self._bits(1)
            btype = self._bits(2)
            if btype == 0:
                if self._stored(size) and self._last:
                    self.done = True
            elif btype == 1:
                lt, dt = self._fixed_tables()
                if self._compressed(lt, dt, size) and self._last:
                    self.done = True
            elif btype == 2:
                lt, dt = self._dynamic_tables()
                if self._compressed(lt, dt, size) and self._last:
                    self.done = True
            else:
                raise ValueError("bad DEFLATE block type")

            if self.eof and not self.out:
                break

        if len(self.out) <= size:
            data = bytes(self.out)
            self.out = bytearray()
        else:
            data = bytes(self.out[:size])
            self.out = self.out[size:]
        return data

    def close(self):
        try:
            self.src.close()
        except Exception:
            pass
