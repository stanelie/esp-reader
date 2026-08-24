#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Upgrade a PFN1 .pf in place to PFN2, measuring its ink extent.

For fonts built by an older tool - the Badger reader's build_font.py, whose
output this project also ships - where rebuilding from the TTF would not
reproduce the file. The ink extent is a property of the bitmaps, so it can be
measured from the .pf itself.

    python3 tools/pf_addink.py device/fonts/literata.pf
"""
import sys

for path in sys.argv[1:]:
    d = bytearray(open(path, "rb").read())
    if bytes(d[:4]) == b"PFN2":
        print("  %s is already PFN2" % path)
        continue
    if bytes(d[:4]) != b"PFN1":
        sys.exit("%s: not a .pf" % path)
    box_h, baseline, first, count, space = d[4], d[5], d[6], d[7], d[8]
    rec0, bmp0 = 9, 9 + count * 4
    top, bot = box_h, -1
    for i in range(count):
        ch = chr(first + i)
        if not (ch.isalnum() or ch in ",.;:!?'\"()-/"):
            continue
        o = rec0 + i * 4
        bw = d[o + 1]
        off = bmp0 + (d[o + 2] | (d[o + 3] << 8))
        rb = (bw + 7) // 8
        for r in range(box_h):
            if any(d[off + r * rb:off + r * rb + rb]):
                if r < top:
                    top = r
                if r > bot:
                    bot = r
    if bot < 0:
        top, bot = 0, box_h - 1
    ink_h = bot - top + 1
    out = (b"PFN2" + bytes([box_h, baseline, first, count, space, top, ink_h])
           + bytes(d[9:]))
    open(path, "wb").write(out)
    print("  %s: box_h %d -> ink rows %d..%d (%d tall)" % (path, box_h, top, bot, ink_h))
