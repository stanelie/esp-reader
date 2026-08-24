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

# How many enclosed white regions each letter must have. This is the check for
# a stroke with a hole in it: when the bowl of an `o` breaks by one pixel the
# counter drains into the surrounding white and the count goes 1 -> 0. Nothing
# else here catches that - the glyph still has ink in the right rows, the right
# stem width and the right advance, and it still reads as "not gibberish".
#
# Only letters whose topology every Latin face agrees on. `g` is out because
# DejaVu's is single-storey and Literata's is double, `Q` because the tail may
# or may not cross the bowl, `a` because at 13px the arch legitimately closes
# against the stem, and `4`/`6`/`9` because open and closed forms both exist.
CLOSED = {"o": 1, "b": 1, "d": 1, "p": 1, "q": 1, "e": 1,
          "O": 1, "D": 1, "B": 2, "P": 1, "R": 1, "0": 1, "8": 2}

# Fonts exempt from the stem-width check. It asks whether an outline was
# rasterised too heavily, and that question does not apply to a bitmap font,
# whose pixels were placed by hand and are the design rather than an
# approximation of one. The IBM VGA face draws every stem 2px at 15 rows on
# purpose. The enclosed-region check below still applies - a hand-drawn font
# can still be converted wrongly, and that is what would show it.
HAND_DRAWN = {"vga-8x16.pf"}


def load(path):
    d = open(path, "rb").read()
    magic = bytes(d[:4])
    assert magic in (b"PFN1", b"PFN2"), "%s: bad magic" % path
    box_h, baseline, first, count, space = d[4], d[5], d[6], d[7], d[8]
    # PFN2 stores the ink extent - the page pitch keys off it rather than the
    # glyph box, so a font that renders a pixel taller does not lose a line.
    ink_top, ink_h = (d[9], d[10]) if magic == b"PFN2" else (0, box_h)
    recs = {}
    base = 11 if magic == b"PFN2" else 9
    for i in range(count):
        adv, bw, off = struct.unpack_from("<BBH", d, base + i * 4)
        recs[first + i] = (adv, bw, off)
    data = base + count * 4
    return d, box_h, baseline, first, recs, data, space, ink_top, ink_h


def bitmap(font, ch):
    d, box_h, baseline, first, recs, data, space = font[:7]
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


def counters(rows):
    """Enclosed white regions: flood the outside, count what white is left.

    A one-pixel margin is added all round first so a counter that touches the
    glyph box edge is not treated as enclosed. Background is flooded 4-connected
    against 8-connected ink, which is the pairing that makes a diagonal run of
    pixels count as a wall - otherwise every diagonal stroke leaks.
    """
    w = len(rows[0]) + 2
    g = [[False] * w]
    g += [[False] + [c == "#" for c in r] + [False] for r in rows]
    g += [[False] * w]
    h = len(g)
    seen = [[False] * w for _ in range(h)]
    stack = [(0, 0)]
    seen[0][0] = True
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not g[ny][nx] and not seen[ny][nx]:
                seen[ny][nx] = True
                stack.append((nx, ny))
    n = 0
    for y in range(h):
        for x in range(w):
            if not g[y][x] and not seen[y][x]:
                n += 1
                stack = [(x, y)]
                seen[y][x] = True
                while stack:
                    cx, cy = stack.pop()
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if (0 <= nx < w and 0 <= ny < h
                                and not g[ny][nx] and not seen[ny][nx]):
                            seen[ny][nx] = True
                            stack.append((nx, ny))
    return n


def broken_bowls(font):
    """Letters whose enclosed-region count is not what the letter has."""
    bad = []
    for ch, want in CLOSED.items():
        rows = bitmap(font, ch)
        if rows is None:
            continue
        got = counters(rows)
        if got != want:
            bad.append("%s=%d(want %d)" % (ch, got, want))
    return bad


def stroke_runs(font, chars="abcdefghijklmnopqrstuvwxyz"
                                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"):
    """How wide this font's strokes are, as a distribution.

    EVERY horizontal run of ink, not just the leftmost one in each row. The
    earlier version stopped at the first run, so it never saw the vertical in
    `d`, `u`, `q` or `4` - all of which carry theirs on the right - and passed
    a font whose right-hand stems were twice the width of its left-hand ones.

    Reported, not judged. Two contradictory reports came from trying to judge
    it: a font was called too thick at 48% 1px runs, and too thin at 70%. The
    reference face this one is measured against sits at 56%, which is inside
    the range both complaints straddle. What a face should weigh is a matter
    of taste and of the panel's contrast; what it must not do is break, and
    that is what the enclosed-region check tests.
    """
    from collections import Counter
    hist = Counter()
    for ch in chars:
        rows = bitmap(font, ch)
        if not rows:
            continue
        for r in rows:
            if not r.count("#") or r.count("#") > len(r) * 0.7:
                continue
            run = 0
            for c in r:
                if c == "#":
                    run += 1
                elif run:
                    hist[run] += 1
                    run = 0
            if run:
                hist[run] += 1
    total = sum(hist.values()) or 1
    return {w: 100.0 * n / total for w, n in hist.items()}


def main():
    show = "--show" in sys.argv
    files = sorted(f for f in os.listdir(FONTS) if f.endswith(".pf"))
    fails = 0
    for name in files:
        font = load(os.path.join(FONTS, name))
        d, box_h, baseline, first, recs, data, space, ink_top, ink_h = font
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
        spread = stroke_runs(font)
        stem_ok = True                # reported below, not judged - see above
        bad = broken_bowls(font)
        ok = ok and stem_ok and not bad
        fails_stem = not stem_ok
        print("%-20s box %2d ink %2d baseline %2d space %d, %d glyphs, %3d rows, "
              "%3.0f%% uniform, strokes %.0f%% 1px / %.0f%% 2px  %s"
              % (name, box_h, ink_h, baseline, space, len(recs), total, frac * 100,
                 spread.get(1, 0.0), spread.get(2, 0.0),
                 "ok" if ok else (("OPEN BOWLS: " + " ".join(bad)) if bad
                                  else "GIBBERISH")))
        fails += not ok
        if show or not ok:
            for ch in ("AE" if not bad else "".join(
                    sorted({b.split("=")[0] for b in bad}))[:4]):
                for r in bitmap(font, ch) or []:
                    print("      " + r)
                print()
    print("\n%s" % ("every .pf renders letters" if not fails
                    else "%d FONT(S) BROKEN" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
