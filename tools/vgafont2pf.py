#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert a MicroPython VGA bitmap font module (vga2_8x16.py and friends)
into the reader's .pf format.

    python3 tools/vgafont2pf.py vga2_8x16.py device/fonts/vga.pf [maxbox]

These modules are the IBM VGA ROM fonts: WIDTH, HEIGHT, FIRST, LAST and a flat
`FONT` blob of HEIGHT bytes per glyph, one byte per row, MSB leftmost. Nothing
is rasterised here - the pixels are already decided, and the point is to keep
them exactly as they are.

The one real problem is the encoding. Glyph N is codepoint N in **CP437**, not
Latin-1, and the two agree only below 0x80. Above that they are unrelated: an
accented letter sits at a completely different index in each. So the upper half
is remapped through Python's cp437 codec rather than copied.

CP437 has no accented capitals. They are folded to the bare letter, the same
thing build_pf.py does and for the same reason - `E` for `E-acute` - which
between them covers English and French completely. What is left missing is a
handful of letters no Latin-1 codepage of this vintage carried at all: the
Portuguese, Icelandic and Danish forms. They pack as blanks.
"""
import importlib.util
import sys
import unicodedata

SRC, OUT = sys.argv[1], sys.argv[2]
MAXBOX = int(sys.argv[3]) if len(sys.argv) > 3 else 99

spec = importlib.util.spec_from_file_location("vgafont", SRC)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
W, H, BLOB = mod.WIDTH, mod.HEIGHT, bytes(mod.FONT)
if W > 8:
    sys.exit("this converter assumes one byte per row (WIDTH <= 8), got %d" % W)

FIRST, LAST = 0x20, 0xFF
CONTROL = range(0x7F, 0xA0)
CHARS = [chr(c) for c in range(FIRST, LAST + 1)]


def fold(ch):
    """Accented capitals are stored as the bare letter - CP437 has no others."""
    if ch.isupper():
        bare = "".join(c for c in unicodedata.normalize("NFD", ch)
                       if not unicodedata.combining(c))
        if len(bare) == 1:
            return bare
    return ch


def rows_for(ch):
    """The 8xH bitmap for `ch`, or None if this font has no such glyph."""
    if ord(ch) in CONTROL:
        return None
    try:
        enc = fold(ch).encode("cp437")
    except (UnicodeEncodeError, LookupError):
        return None
    if len(enc) != 1:
        return None
    i = enc[0] - mod.FIRST
    if not 0 <= i < (len(BLOB) // H):
        return None
    return BLOB[i * H:(i + 1) * H]


# Size the box on the characters that carry prose, exactly as build_pf.py does:
# a couple of rare marks sit higher than any letter, and sizing to them adds a
# row to every line on the page for glyphs that never appear in a book.
CORE = [ch for ch in CHARS
        if ord(ch) not in CONTROL and (ord(ch) < 0x7F or ch.isalpha())]
top, bot = H, -1
for ch in CORE:
    r = rows_for(ch)
    if not r:
        continue
    ys = [y for y in range(H) if r[y]]
    if ys:
        top = min(top, min(ys))
        bot = max(bot, max(ys))
clipped = 0
if bot - top + 1 > MAXBOX:
    clipped = (bot - top + 1) - MAXBOX
    top += clipped                     # from the top; descenders must survive

box_h = bot - top + 1
# The baseline is one row below the last row a flat-bottomed capital reaches.
# Flat-bottomed specifically: `Q` has a descending tail and `J` often drops
# below the line, and taking the whole alphabet let the tail of the Q define
# the baseline, which left one row for descenders instead of three.
cap_bot = max((max(y for y in range(H) if rows_for(c)[y])
               for c in "HIEMNTXZLFBDPRU" if rows_for(c)), default=bot)
baseline = cap_bot - top + 1

missing = []
records, bitmap = bytearray(), bytearray()
for ch in CHARS:
    r = rows_for(ch)
    if r is None and ch != " " and ord(ch) not in CONTROL:
        missing.append(ch)
    blank = r is None or not any(r)
    adv = 0 if (ord(ch) in CONTROL) else W
    off = len(bitmap)
    for ry in range(box_h):
        sy = top + ry
        bitmap.append(0 if blank else r[sy])
    records += bytes([adv, W, off & 0xFF, (off >> 8) & 0xFF])

header = bytes(b"PFN1") + bytes([box_h, baseline, FIRST, len(CHARS), W])
open(OUT, "wb").write(header + bytes(records) + bytes(bitmap))
if missing:
    print("no glyph in CP437 for %d character(s): %s" % (len(missing), "".join(missing)))
print("%s %dx%d -> %s  box_h=%d baseline=%d advance=%d glyphs=%d top-rows-clipped=%d (%d bytes)"
      % (SRC, W, H, OUT, box_h, baseline, W, len(CHARS), clipped,
         len(header) + len(records) + len(bitmap)))
