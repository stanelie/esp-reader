# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Host-side tool: convert a TTF/OTF into the reader's compact 1-bit
proportional bitmap font (.pf). Needs Pillow. Runs on a desktop, not the Badger.

    python tools/build_pf.py <font.ttf> <out.pf> [size=13] [threshold|mono] [weight=400]

Pass `mono` for the threshold to rasterise through FreeType's monochrome
hinted renderer instead of thresholding a greyscale render. Prefer it. A
threshold has to be wrong somewhere: low enough to keep the bowl of an `o`
connected is high enough to smear every stem to 2px, and the gap between those
two is empty at 13px. The hinted rasteriser has dropout control - it is the
thing that guarantees a thin stroke still renders as a connected run - so the
curves close and the stems stay 1px at the same time. Numeric thresholds are
kept because the older fonts were built with them.

For a variable font it selects the given weight (400 = Regular) and, if the
font has an optical-size axis, sets it to the pixel size; other axes keep their
default. A slightly heavier weight (e.g. 500) rasterises thin sans stems more
cleanly to 1-bit at small sizes.

All bundled fonts are open-licensed (SIL OFL). Sources:
  Literata     https://github.com/google/fonts/tree/main/ofl/literata
  Lexend Deca  https://github.com/google/fonts/tree/main/ofl/lexenddeca

.pf format:
  magic 4 = b"PFN1"; box_h; baseline; first_char(0x20); count; space_advance
  then `count` records of 4 bytes: advance, box_width, offset(uint16 LE)
  then per glyph: box_h rows x ceil(box_width/8) bytes, MSB first.
"""
import sys
import unicodedata
from PIL import Image, ImageFont, ImageDraw

TTF = sys.argv[1]
OUT = sys.argv[2]
SIZE = int(sys.argv[3]) if len(sys.argv) > 3 else 13
_th = sys.argv[4] if len(sys.argv) > 4 else "mono"
MONO = _th == "mono"
THRESH = 0 if MONO else int(_th)
WEIGHT = int(sys.argv[5]) if len(sys.argv) > 5 else 400
# Hard ceiling on the glyph box. code.py draws lines 14px apart, so a box taller
# than this starts pushing accents into the line above. If the font's natural
# extent exceeds it, rows are dropped from the TOP (accent tips on a few rare
# letters such as the Scandinavian a-ring) and never from the bottom, because
# descenders - including the cedilla French needs for 'c' - must stay intact.
MAXBOX = int(sys.argv[6]) if len(sys.argv) > 6 else 15
# Optical size, when the font has that axis. Defaults to the pixel size, which
# is the conventional choice, but it is worth setting explicitly: Literata's
# lower optical sizes are drawn wider and sturdier on purpose - that is what
# the axis is for - so a smaller opsz at the same pixel size buys legibility
# without buying height, and height is what costs a line on the page.
OPSZ = int(sys.argv[7]) if len(sys.argv) > 7 else None

# Space through the end of Latin-1, so accented text (French, Spanish, German,
# ...) renders instead of falling back to '?'. U+007F-U+009F are control codes
# with no glyphs; they stay in the range so lookup remains a single subtraction,
# but are packed as empty.
FIRST, LAST = 0x20, 0xFF
CONTROL = range(0x7F, 0xA0)
CHARS = [chr(c) for c in range(FIRST, LAST + 1)]

font = ImageFont.truetype(TTF, SIZE)
try:
    axes = font.get_variation_axes()
    vals = []
    for ax in axes:
        nm = ax["name"]
        nm = nm.decode("latin-1") if isinstance(nm, bytes) else str(nm)
        nm = nm.lower()
        if "weight" in nm:
            vals.append(WEIGHT)
        elif "optical" in nm:
            vals.append(OPSZ if OPSZ is not None else SIZE)
        else:
            vals.append(ax.get("default", 0))
    font.set_variation_by_axes(vals)
except Exception as e:
    print("note: not a variable font / axis set skipped:", e)

BASE = 48
CANVAS_H = 96


def fold(ch):
    """The character whose glyph is actually stored for `ch`.

    Accents on capitals sit above cap height and would force a 17px glyph box
    instead of 15px, which at the 14px line pitch makes them collide with
    descenders on the line above. They are stored as the plain letter instead
    (E for E-acute, and so on) - French routinely drops accents on capitals,
    and it keeps the line spacing and 9-lines-per-page layout unchanged.
    Lowercase accents are unaffected: they fit inside the existing box.
    """
    if ch.isupper():
        stripped = "".join(c for c in unicodedata.normalize("NFD", ch)
                           if not unicodedata.combining(c))
        if len(stripped) == 1:
            return stripped
    return ch


def render(ch, w):
    """The glyph on a canvas, plus a predicate saying whether a pixel is ink.

    Monochrome comes back as a mode-"1" image whose pixels are still one byte
    each once loaded - Pillow keeps the packing to itself - so the test is
    against zero, not against a bit.
    """
    img = Image.new("1" if MONO else "L", (w, CANVAS_H), 0)
    if ord(ch) not in CONTROL:
        ImageDraw.Draw(img).text((0, BASE), fold(ch), font=font,
                                 fill=1 if MONO else 255, anchor="ls")
    px = img.load()
    if MONO:
        return img, (lambda x, y: px[x, y] != 0)
    return img, (lambda x, y: px[x, y] >= THRESH)


def ink_bbox(img, is_ink):
    """Bounding box of what will actually be stored.

    Not img.getbbox(): on a greyscale render that counts antialiasing the
    threshold is about to discard, which sizes the glyph box off pixels no
    glyph ends up having.
    """
    w, h = img.size
    ys = [y for y in range(h) for x in range(w) if is_ink(x, y)]
    if not ys:
        return None
    xs = [x for x in range(w) for y in range(h) if is_ink(x, y)]
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)


advances = {ch: (0 if ord(ch) in CONTROL else max(0, round(font.getlength(fold(ch)))))
            for ch in CHARS}
folded = [ch for ch in CHARS if fold(ch) != ch]

# The glyph box is sized from the characters that carry text - ASCII plus the
# accented lowercase letters. A handful of rare standalone marks (degree, acute,
# diaeresis) sit higher than any letter; sizing the box around them would add a
# row to EVERY line and push the accents into the line above. They are clipped
# by a pixel instead, which nothing legible depends on.
CORE = [ch for ch in CHARS
        if ord(ch) not in CONTROL and (ord(ch) < 0x7F or ch.isalpha())]
missing = [ch for ch in CHARS
           if ord(ch) not in CONTROL and ch != " " and advances[ch] == 0]
if missing:
    print(f"warning: {len(missing)} glyph(s) missing from the font: {''.join(missing)}")
union_top, union_bot = CANVAS_H, 0
for ch in CORE:
    bb = ink_bbox(*render(ch, max(advances[ch] + 4, 8)))
    if bb:
        union_top = min(union_top, bb[1])
        union_bot = max(union_bot, bb[3])
clipped_rows = 0
if union_bot - union_top > MAXBOX:
    clipped_rows = (union_bot - union_top) - MAXBOX
    union_top += clipped_rows          # drop from the top, keep descenders

box_h = union_bot - union_top
baseline = BASE - union_top

records, bitmap = bytearray(), bytearray()
for ch in CHARS:
    adv = advances[ch]
    g, is_ink = render(ch, max(adv + 4, 8))
    ink = ink_bbox(g, is_ink)
    box_w = max(adv, ink[2] if ink else 0, 1)
    rb = (box_w + 7) // 8
    off = len(bitmap)
    for ry in range(box_h):
        sy = union_top + ry
        row = bytearray(rb)
        for rx in range(box_w):
            if is_ink(rx, sy):
                row[rx >> 3] |= 0x80 >> (rx & 7)
        bitmap += row
    records += bytes([min(adv, 255), min(box_w, 255), off & 0xFF, (off >> 8) & 0xFF])

header = bytes(b"PFN1") + bytes([box_h, baseline, FIRST, len(CHARS), min(advances[" "], 255)])
open(OUT, "wb").write(header + bytes(records) + bytes(bitmap))
print(f"{TTF} size={SIZE} thresh={'mono' if MONO else THRESH} box_h={box_h} baseline={baseline} "
      f"space={advances[' ']} glyphs={len(CHARS)} "
      f"accent-folded capitals={len(folded)} top-rows-clipped={clipped_rows} "
      f"-> {OUT} ({len(header)+len(records)+len(bitmap)} bytes)")
