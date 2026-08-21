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
           rotation, y0=0, y1=None):
    # Transpose `src` (landscape) into `dst` (native). Both are bytearrays.
    #
    #     land_stride / nat_stride are bytes per row.
    #
    # y0/y1 transpose only a band of landscape rows, leaving the rest of `dst`
    # alone. A menu highlight moving one row does not need the other eight
    # transposed - measured at ~250ms for a whole frame on the RP2040, which is
    # most of what makes a keypress feel slow.
    if y1 is None:
        y1 = land_h
    band = (y0 != 0) or (y1 != land_h)

    if not band:
        for i in range(len(dst)):
            dst[i] = 0xFF                 # white; ink is cleared in below
    elif rotation == 3:
        # One landscape row is one bit position inside a fixed byte column, so
        # a band of rows is a few whole columns across every native row.
        c0 = y0 >> 3
        c1 = ((y1 - 1) >> 3) + 1
        for r in range(nat_h):
            base = r * nat_stride
            for c in range(c0, c1):
                dst[base + c] = 0xFF
    else:
        for i in range(len(dst)):
            dst[i] = 0xFF

    if rotation == 3:
        # A whole landscape row lands in one native column, so the destination
        # bit position is fixed for the row and only the byte index moves.
        for y in range(y0, y1):
            mask = ~(0x80 >> (y & 7)) & 0xFF
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
                            dst[(nat_h - 1 - x) * nat_stride + col] &= mask
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
