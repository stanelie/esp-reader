# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Minimal proportional 1-bit bitmap font renderer for the Badger reader.
#
# Loads a `.pf` font (see build_literata.py for the format) as one small bytes
# blob and blits glyphs into an adafruit_framebuf.FrameBuffer. Also provides pixel
# width metrics so the layout engine can wrap/justify/hyphenate in pixels instead
# of character counts.


class PropFont:
    def __init__(self, path, min_space_ratio=0.30, buf=None, file_backed=False):
        # `buf`, when given, is a buffer big enough for any installed font,
        # reused for every load. Only one font is ever live, so reading into it
        # costs nothing and - the point - allocates nothing: switching fonts on
        # a heap the reader has been paginating into used to need a fresh ~4KB
        # in one piece, which is exactly what a fragmented heap cannot give.
        #
        # `file_backed` keeps only the header and the per-glyph records in RAM
        # (a few hundred bytes) and reads each glyph's rows from the file as it
        # is drawn. That is for the interface font, which draws a couple of
        # hundred glyphs on a screen that already pays a ~1s panel refresh -
        # not for the reading font, where holding the whole file is what lets
        # draw() shift bytes instead of touching pixels.
        self._f = None
        self._gbuf = None
        f = open(path, "rb")
        try:
            if file_backed:
                head = f.read(9)
                if len(head) < 9 or bytes(head[:4]) not in (b"PFN1", b"PFN2"):
                    raise ValueError("bad font file")
                if bytes(head[:4]) == b"PFN2":
                    head = head + f.read(2)      # ink_top, ink_h
                d = head + f.read(head[7] * 4)   # header + glyph records
                self._f = f
                f = None                          # kept open for glyph reads
            elif buf is None:
                d = f.read()
            else:
                n = f.readinto(buf)
                if not n:
                    raise ValueError("empty font file")
                d = buf
        finally:
            if f is not None:
                f.close()
        # bytes() around the slice: `buf` may be a bytearray, and comparing a
        # bytearray slice to a bytes literal is not reliable across ports.
        # PFN2 adds ink_top and ink_h after the space advance. PFN1 files -
        # anything built before the page pitch keyed off ink rather than the
        # glyph box - still load; they just report the box, which is what the
        # reader used to use anyway.
        magic = bytes(d[:4])
        if magic not in (b"PFN1", b"PFN2"):
            raise ValueError("bad font file")
        self.d = d
        self.box_h = d[4]
        self.baseline = d[5]
        self.first = d[6]
        self.count = d[7]
        # The baked space advance can be tiny at small sizes, which makes packed
        # and justified lines run together. Enforce a visible minimum scaled to
        # the font height so it holds across sizes.
        self.space_w = max(d[8], round(self.box_h * min_space_ratio))
        if magic == b"PFN2":
            self.ink_top = d[9]
            self.ink_h = d[10]
            self.rec0 = 11
        else:
            self.ink_top = 0
            self.ink_h = self.box_h
            self.rec0 = 9
        self.bmp0 = self.rec0 + self.count * 4
        self._qmark = ord("?")
        self._space_idx = ord(" ") - self.first
        if self._f is not None:
            # One glyph's rows at a time, sized from the widest glyph the font
            # actually declares rather than a guess.
            widest = 0
            for i in range(self.count):
                bw = d[self.rec0 + i * 4 + 1]
                if bw > widest:
                    widest = bw
            self._gbuf = bytearray(self.box_h * ((widest + 7) // 8) or 1)

    def _glyph(self, off, n):
        # Return (data, base) for a glyph's `n` bitmap bytes at file offset
        #         `off`. In memory that is the blob itself; file-backed it is one read
        #         into the scratch buffer, so a glyph costs a seek rather than a seek per
        #         byte.
        if self._f is None:
            return self.d, off
        self._f.seek(off)
        try:
            self._f.readinto(memoryview(self._gbuf)[:n])
        except Exception:
            self._gbuf[:n] = self._f.read(n)     # ports without readinto
        return self._gbuf, 0

    def deinit(self):
        # Close the font file, for a file-backed font.
        if self._f is not None:
            try:
                self._f.close()
            except Exception:
                pass
            self._f = None

    def _rec(self, ch):
        idx = ord(ch) - self.first
        if idx < 0 or idx >= self.count:
            idx = self._qmark - self.first
        r = self.rec0 + idx * 4
        d = self.d
        adv = self.space_w if idx == self._space_idx else d[r]
        return adv, d[r + 1], self.bmp0 + (d[r + 2] | (d[r + 3] << 8))

    def char_width(self, ch):
        return self._rec(ch)[0]

    def text_width(self, s):
        w = 0
        for ch in s:
            w += self._rec(ch)[0]
        return w

    def draw(self, fb, s, x, y, color=1, extra_each=0, extra_first=0):
        # Blit `s` at (x, y) top-left. `extra_each` px is added to every space
        #         advance and `extra_first` more spaces get one extra px (for justified
        #         line filling). Returns the final pen x.
        #
        #         Writes bytes straight into the framebuffer's buffer (MHMSB) instead of
        #         calling fb.pixel() per lit pixel - the latter is far too slow on the
        #         RP2040. Falls back to fb.pixel() if the buffer isn't exposed.
        buf = getattr(fb, "buf", None)
        if buf is None:
            return self._draw_slow(fb, s, x, y, color, extra_each, extra_first)
        box_h = self.box_h
        W = fb.width
        H = fb.height
        stride = getattr(fb, "stride", W)  # bits per row; == width for MHMSB
        row_bytes = stride >> 3
        first_n = extra_first
        # Loop-invariant, so tested once rather than per glyph. Measured on a
        # desktop this changes nothing - the call was not the expense - but a
        # branch is no worse than a call and this is the reader's hot path.
        in_ram = self._f is None
        blob = self.d

        for ch in s:
            adv, bw, off = self._rec(ch)
            rb = (bw + 7) // 8
            if in_ram:
                d = blob
            else:
                d, off = self._glyph(off, rb * box_h)
            dst0 = x >> 3
            shift = x & 7
            nbytes = (shift + bw + 7) >> 3

            # Fast path: shift each glyph row into place as a small integer and
            # OR it into the 1-3 bytes it lands on, instead of testing and
            # setting every pixel individually. Glyph drawing dominates the cost
            # of rendering a page, and this turns ~8 operations per PIXEL into
            # ~4 per destination BYTE.
            if (color and rb <= 2 and x >= 0
                    and dst0 + nbytes <= row_bytes):
                # Source pixel 0 is the MSB of the first row byte, i.e. bit
                # (rb*8-1). It has to land at bit (nbytes*8-1-shift) of the
                # destination window; sh is the difference. Padding bits below
                # the glyph width are zero, so OR-ing the whole window is safe.
                sh = (nbytes << 3) - shift - (rb << 3)
                for ry in range(box_h):
                    yy = y + ry
                    if yy < 0 or yy >= H:
                        continue
                    base = off + ry * rb
                    v = d[base] if rb == 1 else ((d[base] << 8) | d[base + 1])
                    if not v:
                        continue                     # blank row - very common
                    v = (v << sh) if sh >= 0 else (v >> -sh)
                    bi = yy * row_bytes + dst0
                    if nbytes == 1:
                        buf[bi] |= v & 0xFF
                    elif nbytes == 2:
                        buf[bi] |= (v >> 8) & 0xFF
                        buf[bi + 1] |= v & 0xFF
                    else:
                        buf[bi] |= (v >> 16) & 0xFF
                        buf[bi + 1] |= (v >> 8) & 0xFF
                        buf[bi + 2] |= v & 0xFF
            else:
                # Clipped at an edge, erasing, or an unusually wide glyph.
                for ry in range(box_h):
                    yy = y + ry
                    if yy < 0 or yy >= H:
                        continue
                    rowbyte = yy * row_bytes
                    base = off + ry * rb
                    for cx in range(bw):
                        if d[base + (cx >> 3)] & (0x80 >> (cx & 7)):
                            xx = x + cx
                            if 0 <= xx < W:
                                bi = rowbyte + (xx >> 3)
                                mask = 0x80 >> (xx & 7)
                                if color:
                                    buf[bi] |= mask
                                else:
                                    buf[bi] &= ~mask & 0xFF

            x += adv
            if ch == " ":
                x += extra_each
                if first_n > 0:
                    x += 1
                    first_n -= 1
        return x

    def _draw_slow(self, fb, s, x, y, color, extra_each, extra_first):
        box_h = self.box_h
        first_n = extra_first
        in_ram = self._f is None
        blob = self.d
        for ch in s:
            adv, bw, off = self._rec(ch)
            rb = (bw + 7) // 8
            if in_ram:
                d = blob
            else:
                d, off = self._glyph(off, rb * box_h)
            for ry in range(box_h):
                base = off + ry * rb
                yy = y + ry
                for cx in range(bw):
                    if d[base + (cx >> 3)] & (0x80 >> (cx & 7)):
                        fb.pixel(x + cx, yy, color)
            x += adv
            if ch == " ":
                x += extra_each
                if first_n > 0:
                    x += 1
                    first_n -= 1
        return x

    def draw_justified(self, fb, s, x, y, color, target_width):
        # Draw `s` stretched to `target_width` px by widening its spaces.
        spaces = s.count(" ")
        extra = target_width - self.text_width(s)
        if spaces == 0 or extra <= 0:
            return self.draw(fb, s, x, y, color)
        base = extra // spaces
        rem = extra - base * spaces
        return self.draw(fb, s, x, y, color, extra_each=base, extra_first=rem)
