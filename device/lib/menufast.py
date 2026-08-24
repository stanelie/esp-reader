# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Moving a list highlight without redrawing the list.
#
# Measured on the Badger, per keypress in the picker: ~320ms redrawing every
# row's text, ~250ms transposing all 4736 bytes into panel orientation, ~31ms
# for the refresh itself and 1-3ms waiting on the panel. The redraw was the
# whole problem - a windowed refresh would have addressed the 31ms.
#
# Out here rather than in code.py because it is only needed once a menu is
# open, by which time the boot-time allocations are long done. Compiled into
# code.py it cost a page buffer, at roughly five bytes of contiguous heap per
# byte of source on this board.

from fbrotate import rotate


def flip(R, buf, old_row, new_row, title, sel, total):
    # Flip two highlight bands and refresh the header. True if it worked.
    #
    #     The highlight is a plain inversion - a black bar with white text where
    #     the other rows are black text on white - so XORing a row's band turns
    #     one into the other exactly. Only the header needs drawing again,
    #     because it carries the position counter, and only its band is
    #     transposed.
    #
    #     This leans on the landscape scratch still holding the list from the
    #     last full render. Anything calling begin_frame() in between
    #     invalidates that, so the caller falls back whenever it is unsure.
    canvas = R["_frame_canvas"]
    if canvas is None or R["reader_font"] is None:
        return False
    epd = R["epd"]
    # Both halves of this - the banded rotate and the XOR - are written for the
    # rotation-3 mapping only. On any other panel the caller redraws the list,
    # which is correct, just slower.
    if epd.rotation != 3:
        return False
    line_h = R["LINE_HEIGHT"]
    canvas.fill_rect(0, 0, R["WIDTH"], line_h - 1, 1)
    R["draw_text"](canvas, "%s  %d/%d" % (title, sel + 1, total),
                   R["PADDING_X"], 0, color=0)
    # Same polarity as the rest of the frame, or the header band comes out
    # reversed against it.
    rotate(R["_frame_scratch"], buf, epd.landscape_width, epd.landscape_height,
           epd.landscape_stride, epd.width, epd.height, epd.bytes_per_row,
           epd.rotation, 0, line_h, R.get("INVERT_OUTPUT", False))
    for row in (old_row, new_row):
        xor_band(epd, buf, line_h * (row + 1), line_h)
    return True


def xor_band(epd, buf, y, h):
    # Invert a landscape band inside a native frame.
    #
    #     A landscape row is one bit position within a fixed byte column, so a
    #     band is a mask per column applied down every native row. Masks rather
    #     than whole bytes because a row pitch of 14 or 15 never lands on a byte
    #     boundary, and rounding outward would invert slivers of its neighbours.
    if epd.rotation != 3:
        return
    y1 = min(y + h, epd.landscape_height)
    masks = {}
    for yy in range(max(0, y), y1):
        c = yy >> 3
        masks[c] = masks.get(c, 0) | (0x80 >> (yy & 7))
    stride = epd.bytes_per_row
    base = (epd.height - epd.landscape_width) * stride
    for _dx in range(epd.landscape_width):
        for c in masks:
            buf[base + c] ^= masks[c]
        base += stride
