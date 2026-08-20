#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode .pf fonts and check they render letters, not gibberish.

Same discipline as test_fonts.py, and for the same reason: metrics can be
perfect while every bitmap is wrong, and the only check that catches it is
looking at the pixels.

.pf format (from tools/build_pf.py):
  b"PFN1", box_h, baseline, first_char, count, space_advance   (9 bytes)
  count records of 4 bytes: advance, box_width, offset (uint16 LE)
  then per glyph: box_h rows of ceil(box_width/8) bytes, MSB first
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
FONTS = os.path.join(ROOT, "device", "fonts")
PROBES = "ABegoSnh"


def load(path):
    d = open(path, "rb").read()
    assert d[:4] == b"PFN1", "%s: bad magic" % path
    box_h, baseline, first, count, space = d[4], d[5], d[6], d[7], d[8]
    recs = {}
    base = 9
    for i in range(count):
        adv, bw, off = struct.unpack_from("<BBH", d, base + i * 4)
        recs[first + i] = (adv, bw, off)
    data = base + count * 4
    return d, box_h, baseline, first, recs, data, space


def bitmap(font, ch):
    d, box_h, baseline, first, recs, data, space = font
    r = recs.get(ord(ch))
    if not r:
        return None
    adv, bw, off = r
    if bw == 0:
        return None
    rb = (bw + 7) // 8
    start = data + off
    return ["".join("#" if (d[start + y * rb + (x >> 3)] >> (7 - (x & 7))) & 1
                    else "." for x in range(bw)) for y in range(box_h)]


def stem_widths(font, chars="nhmuildbpr"):
    """Dominant width of the leftmost vertical stroke, over lowercase.

    Lowercase, because that is what prose is - a check that only looked at
    capital H once passed a font whose lowercase stems were all 2px wide.
    """
    from collections import Counter
    runs = []
    for ch in chars:
        rows = bitmap(font, ch)
        if not rows:
            continue
        for r in rows:
            if not r.count("#") or r.count("#") > len(r) * 0.7:
                continue          # blank, or a crossbar row
            run = 0
            for c in r:
                if c == "#":
                    run += 1
                elif run:
                    break
            if run:
                runs.append(run)
    if not runs:
        return 0, {}
    c = Counter(runs)
    n = sum(c.values())
    return c.most_common(1)[0][0], {w: c[w] / n for w in c}


def main():
    show = "--show" in sys.argv
    files = sorted(f for f in os.listdir(FONTS) if f.endswith(".pf"))
    fails = 0
    for name in files:
        font = load(os.path.join(FONTS, name))
        d, box_h, baseline, first, recs, data, space = font
        uniform = total = ink = 0
        for ch in PROBES:
            rows = bitmap(font, ch)
            if rows is None:
                continue
            for r in rows:
                if r.count("#") == 0:
                    continue          # a glyph box is taller than its letter
                total += 1
                if r.count("#") == len(r):
                    uniform += 1
                ink += r.count("#")
        frac = uniform / total if total else 1.0
        ok = total and frac < 0.45 and ink > 0
        # A stem should stay proportional to the size: 1px on a 13px box,
        # 2px by the time the box is 19. Threshold 108 on the greyscale render
        # gave the 13px Literata 2px lowercase stems and it read as heavy.
        stem, spread = stem_widths(font)
        # /12 rather than /13: a 19px box should be allowed a 2px stem, and
        # round(19/13) is 1. Getting this wrong flagged a font that was
        # perfectly proportionate.
        allowed = max(1, round(box_h / 12.0))
        # Dominance alone is not enough: the build that read as heavy still
        # had 1px as its most common stem, just only 46% of the time against
        # 73% for the one that looked right. What matters is how OFTEN the
        # stem is the width it should be.
        # 0.55 is calibrated, not arbitrary. Measured: the build that read as
        # heavy on the device sat at 46%, the ones that read correctly at
        # 73-96%, and the 18px serif at 58% - a large face legitimately has
        # more variation in stroke width, so the bar has to clear it.
        at_allowed = spread.get(allowed, 0.0)
        stem_ok = stem <= allowed and at_allowed >= 0.55
        ok = ok and stem_ok
        fails_stem = not stem_ok
        print("%-20s box_h %2d baseline %2d space %d, %d glyphs, %3d rows, "
              "%3.0f%% uniform, stem %dpx (<=%d), %3.0f%% at that width  %s"
              % (name, box_h, baseline, space, len(recs), total, frac * 100,
                 stem, allowed, at_allowed * 100,
                 "ok" if ok else ("TOO HEAVY" if fails_stem else "GIBBERISH")))
        fails += not ok
        if show or not ok:
            for ch in "AE":
                for r in bitmap(font, ch) or []:
                    print("      " + r)
                print()
    print("\n%s" % ("every .pf renders letters" if not fails
                    else "%d FONT(S) BROKEN" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
