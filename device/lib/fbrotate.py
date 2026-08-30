# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Rotate a landscape 1-bit framebuffer into the panel's native orientation.
#
# Why this exists: text is drawn into a landscape buffer so a glyph's row of
# pixels is a run of bits in one byte, which lets the font blit bytes instead of
# setting pixels. The panel wants those same pixels in its own portrait layout,
# where that run becomes one bit in each of eight different bytes. Something has
# to transpose, and doing it once per refresh beats doing it per pixel drawn.
#
# Both buffers are MHMSB: bit 7 of a byte is the leftmost pixel, 1 is white and 0
# is ink, matching the reader's b"\xFF"-filled page buffers.
#
# The mapping reproduces adafruit_framebuf's own rotation exactly - the reader
# used to draw through a rotated FrameBuffer, and the panel must not notice the
# change:
#
#     rotation 1: (x, y) -> (nat_w - 1 - y, x)
#     rotation 3: (x, y) -> (y, nat_h - 1 - x)
#
# The loop only touches source bytes that are not 0xFF. A page of text is mostly
# white, so most bytes are skipped without ever looking at their bits - which is
# what keeps this cheaper than the per-pixel drawing it replaces.


def rotate(src, dst, land_w, land_h, land_stride, nat_w, nat_h, nat_stride,
           rotation, y0=0, y1=None, invert=False):
    # Transpose `src` (landscape) into `dst` (native). Both are bytearrays.
    #
    #     land_stride / nat_stride are bytes per row.
    #
    # y0/y1 transpose only a band of landscape rows, leaving the rest of `dst`
    # alone. A menu highlight moving one row does not need the other eight
    # transposed - measured at ~250ms for a whole frame on the RP2040, which is
    # most of what makes a keypress feel slow.
    # `invert` writes the panel's own polarity instead of the framebuffer's.
    # The Badger's UC8151 wants blank = 0x00 and ink = 0xFF, the opposite of
    # every FrameBuffer in this reader. Done here rather than as a pass over
    # the finished frame for two reasons: this loop already touches only the
    # ink, so it is almost free, where a separate pass is 4736 Python
    # iterations on every render; and doing it here covers the BANDED rotate
    # too, which a pass over `out` in end_frame() does not - the menu's header
    # band would come out in the opposite polarity to the rest of the screen.
    blank = 0x00 if invert else 0xFF
    if y1 is None:
        y1 = land_h
    band = (y0 != 0) or (y1 != land_h)

    if not band:
        for i in range(len(dst)):
            dst[i] = blank
    elif rotation == 3:
        # One landscape row is one bit position inside a fixed byte column, so
        # a band of rows is a few whole columns across every native row.
        c0 = y0 >> 3
        c1 = ((y1 - 1) >> 3) + 1
        for r in range(nat_h):
            base = r * nat_stride
            for c in range(c0, c1):
                dst[base + c] = blank
    else:
        # A band is only mapped for rotation 3. Clearing the whole destination
        # and then filling one band would wipe the screen and put a single
        # stripe back, so refuse instead - callers fall back to a full frame.
        if band:
            raise ValueError("banded rotate needs rotation 3")
        for i in range(len(dst)):
            dst[i] = blank

    if rotation == 3:
        # A whole landscape row lands in one native column, so the destination
        # bit position is fixed for the row and only the byte index moves.
        #
        # Where both widths are byte-aligned (true for every panel that uses
        # this rotation today), a faster path below tests a nibble at a time
        # instead of walking all 8 bits of every non-blank byte: a page of
        # text averages a couple of lit pixels per inked byte, so most nibbles
        # are empty and this skips four destination writes at once for them.
        # Ported from the reference reader's _rotate_framebuffer, whose own
        # comment calls this "the slowest pure-Python loop in the project" and
        # measured the nibble test as a third off the rotation's own time.
        # x steps by 1 as b runs 0..7, and i(x) = (nat_h-1-x)*nat_stride+col
        # falls by exactly nat_stride per step - which is why the four
        # candidate destinations below are one fixed stride apart, computed
        # once per byte rather than recomputed per bit.
        if land_w % 8 == 0 and nat_h % 8 == 0:
            ds1 = nat_stride
            ds2 = ds1 + ds1
            ds3 = ds2 + ds1
            ds4 = ds2 + ds2
            for y in range(y0, y1):
                bit = 0x80 >> (y & 7)
                mask = ~bit & 0xFF
                col = y >> 3
                row_base = y * land_stride
                base0 = (nat_h - 1) * nat_stride + col
                if invert:
                    for bx in range(land_stride):
                        byte = src[row_base + bx]
                        if byte == 0xFF:
                            continue
                        ink = (~byte) & 0xFF
                        base = base0 - (bx << 3) * nat_stride
                        if ink & 0xF0:
                            if ink & 0x80:
                                dst[base] |= bit
                            if ink & 0x40:
                                dst[base - ds1] |= bit
                            if ink & 0x20:
                                dst[base - ds2] |= bit
                            if ink & 0x10:
                                dst[base - ds3] |= bit
                        if ink & 0x0F:
                            base -= ds4
                            if ink & 0x08:
                                dst[base] |= bit
                            if ink & 0x04:
                                dst[base - ds1] |= bit
                            if ink & 0x02:
                                dst[base - ds2] |= bit
                            if ink & 0x01:
                                dst[base - ds3] |= bit
                else:
                    for bx in range(land_stride):
                        byte = src[row_base + bx]
                        if byte == 0xFF:
                            continue
                        ink = (~byte) & 0xFF
                        base = base0 - (bx << 3) * nat_stride
                        if ink & 0xF0:
                            if ink & 0x80:
                                dst[base] &= mask
                            if ink & 0x40:
                                dst[base - ds1] &= mask
                            if ink & 0x20:
                                dst[base - ds2] &= mask
                            if ink & 0x10:
                                dst[base - ds3] &= mask
                        if ink & 0x0F:
                            base -= ds4
                            if ink & 0x08:
                                dst[base] &= mask
                            if ink & 0x04:
                                dst[base - ds1] &= mask
                            if ink & 0x02:
                                dst[base - ds2] &= mask
                            if ink & 0x01:
                                dst[base - ds3] &= mask
            return
        for y in range(y0, y1):
            bit = 0x80 >> (y & 7)
            mask = ~bit & 0xFF
            col = y >> 3
            row_base = y * land_stride
            for bx in range(land_stride):
                byte = src[row_base + bx]
                if byte == 0xFF:
                    continue
                x0 = bx << 3
                for b in range(8):
                    if not (byte >> (7 - b)) & 1:
                        x = x0 + b
                        if x < land_w:
                            i = (nat_h - 1 - x) * nat_stride + col
                            if invert:
                                dst[i] |= bit
                            else:
                                dst[i] &= mask
    elif rotation == 1:
        for y in range(land_h):
            nx = nat_w - 1 - y
            mask = ~(0x80 >> (nx & 7)) & 0xFF
            col = nx >> 3
            row_base = y * land_stride
            for bx in range(land_stride):
                byte = src[row_base + bx]
                if byte == 0xFF:
                    continue
                x0 = bx << 3
                for b in range(8):
                    if not (byte >> (7 - b)) & 1:
                        x = x0 + b
                        if x < land_w:
                            dst[x * nat_stride + col] &= mask
    else:
        raise ValueError("rotation %r not supported" % rotation)
