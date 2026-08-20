#!/usr/bin/env python3
"""Convert a BDF bitmap font to PCF, for adafruit_bitmap_font on CircuitPython.

WHY THIS EXISTS
---------------
adafruit_bitmap_font's BDF reader has no glyph index. Every load_glyphs() call
for a codepoint it has not cached re-scans the .bdf line by line from byte 0,
parsing hex text in Python. On a Heltec Vision Master E213 (ESP32-S3 at 240 MHz)
loading 110 glyphs from a 12.5 KB subset BDF measured 0.42 s at boot.

PCF is the same bitmap data with lookup tables in front of it:

  * an encoding table maps a codepoint straight to a glyph index
  * a metrics table and a bitmap-offset table are indexed by that number
  * glyph bitmaps are read by bitmaptools.readinto(), which is native C

So loading becomes a handful of seeks per glyph instead of a text scan, and the
bitmap decode leaves Python entirely.

There is no bdftopcf on macOS and no such package on PyPI, hence this script.

THE READER'S CONTRACT
---------------------
Written against adafruit_bitmap_font 2.4.2 (lib/adafruit_bitmap_font/pcf.py).
These are not stylistic choices - the reader rejects or misparses anything else:

  1. tables[PCF_BITMAPS].format must be EXACTLY 0xE, or __init__ raises
     NotImplementedError("Unsupported format"). 0xE means:
       bits 0-1 = 2 -> glyph rows padded to 1<<2 = 4 bytes
       bit  2   = 1 -> table bodies are big-endian (required: _seek_table()
                       raises RuntimeError("Only big endian supported") without it)
       bit  3   = 1 -> most significant bit is the leftmost pixel
  2. Each table begins with a format word stored LITTLE-endian (read with "<I"),
     while everything after it in that table is BIG-endian. The file header and
     the table directory are little-endian too.
  3. An accelerator table must be present (PCF_ACCELERATORS or
     PCF_BDF_ACCELERATORS), else RuntimeError("Accelerator table missing").
     font_ascent from it becomes the font's .ascent, which the reader app uses
     to place glyphs vertically - it must match the BDF's FONT_ASCENT or all
     text shifts.
  4. Glyph geometry is derived, not stored:
       width  = right_side_bearing - left_side_bearing
       height = character_ascent + character_descent
       Glyph(dx = left_side_bearing, dy = -character_descent,
             shift_x = character_width)
     Mapping from BDF "BBX w h xoff yoff" and "DWIDTH dwx":
       left_side_bearing  = xoff        right_side_bearing = xoff + w
       character_ascent   = h + yoff    character_descent  = -yoff
       character_width    = dwx
     This reproduces exactly what bdf.py builds, so rendering is unchanged.
  5. Row padding is defined by pcf.py's own no-bitmaptools fallback:
       words_per_row = (width + 31) // 32   ->  4 * words_per_row bytes per row
       pixel k of a row = bit (128 >> (k % 8)) of byte (k // 8)
     i.e. BDF's own byte layout, each row zero-padded up to a multiple of 4.
  6. PCF_PROPERTIES is not written. Nothing in __init__ reads it, and pcf.py's
     _read_properties() would raise anyway (it subscripts a namedtuple by
     string). Omitting it keeps the file smaller.

The encoding table is a dense (max_byte1-min_byte1+1) x (max_byte2-min_byte2+1)
grid of uint16, so a font mixing Latin-1 with U+20xx punctuation spends ~15 KB
there. That is the price of O(1) lookup; the file gets bigger than the BDF while
loading far faster, which is the right trade on a device with megabytes of flash.

USAGE
-----
    python3 tools/bdf2pcf.py <in.bdf> <out.pcf>

Writes out.pcf, then re-parses it with an independent spec-based reader and
compares every glyph's metrics and every bitmap pixel against the source BDF.
It refuses to leave a file behind that fails that check. Pass --no-verify to
skip it (not recommended).

PIPELINE
--------
    python3 tools/subset_bdf.py fonts/literata-12.bdf fonts/sub.bdf book.txt ...
    python3 tools/bdf2pcf.py   fonts/sub.bdf          fonts/literata-12-r.pcf
then point FONT_PATH in code.py at the .pcf. Subset first: this converter does
not drop glyphs, and a full 1163-glyph font would make the encoding table and
the bitmap data far larger than they need to be.
"""
import struct
import sys

# Table type bits, from the PCF format spec.
PCF_PROPERTIES = 1 << 0
PCF_ACCELERATORS = 1 << 1
PCF_METRICS = 1 << 2
PCF_BITMAPS = 1 << 3
PCF_BDF_ENCODINGS = 1 << 5

PCF_BYTE_MASK = 1 << 2  # table body is big-endian
PCF_BIT_MASK = 1 << 3  # leftmost pixel is the high bit

FORMAT_BE = PCF_BYTE_MASK  # 0x04: plain big-endian table
FORMAT_BITMAPS = 2 | PCF_BYTE_MASK | PCF_BIT_MASK  # 0x0E, the only value accepted
GLYPH_PAD = 1 << (FORMAT_BITMAPS & 3)  # 4 bytes per row

NO_GLYPH = 0xFFFF


class Font:
    def __init__(self):
        self.ascent = None
        self.descent = None
        self.glyphs = {}  # codepoint -> Glyph


class Glyph:
    __slots__ = ("w", "h", "xoff", "yoff", "dwidth", "rows")

    def row_bytes(self, pad=1):
        """Bytes per bitmap row once padded to a multiple of `pad`."""
        src = (self.w + 7) // 8
        return ((src + pad - 1) // pad) * pad

    def bitmap(self, pad=GLYPH_PAD):
        stride = self.row_bytes(pad)
        out = bytearray()
        for row in self.rows:
            out += row + bytes(stride - len(row))
        return bytes(out)


def parse_bdf(path):
    """Read the glyphs and vertical metrics we need out of a BDF."""
    font = Font()
    glyph = None
    codepoint = None
    reading_bitmap = False

    with open(path, encoding="latin-1") as f:
        for line in f:
            line = line.strip()

            if reading_bitmap:
                if line == "ENDCHAR":
                    reading_bitmap = False
                    if codepoint is not None:
                        font.glyphs[codepoint] = glyph
                    glyph, codepoint = None, None
                elif line:
                    glyph.rows.append(bytes.fromhex(line))
                continue

            if line.startswith("FONT_ASCENT"):
                font.ascent = int(line.split()[1])
            elif line.startswith("FONT_DESCENT"):
                font.descent = int(line.split()[1])
            elif line.startswith("STARTCHAR"):
                glyph = Glyph()
                glyph.rows = []
                glyph.dwidth = 0
                codepoint = None
            elif line.startswith("ENCODING") and glyph is not None:
                codepoint = int(line.split()[1])
            elif line.startswith("DWIDTH") and glyph is not None:
                glyph.dwidth = int(line.split()[1])
            elif line.startswith("BBX") and glyph is not None:
                glyph.w, glyph.h, glyph.xoff, glyph.yoff = (
                    int(v) for v in line.split()[1:5]
                )
            elif line == "BITMAP" and glyph is not None:
                reading_bitmap = True

    if font.ascent is None or font.descent is None:
        raise ValueError(f"{path}: FONT_ASCENT/FONT_DESCENT missing")
    if not font.glyphs:
        raise ValueError(f"{path}: no glyphs found")
    return font


def glyph_metrics(g):
    """PCF metrics for one glyph: see contract note 4."""
    return (
        g.xoff,           # left_side_bearing
        g.xoff + g.w,     # right_side_bearing
        g.dwidth,         # character_width
        g.h + g.yoff,     # character_ascent
        -g.yoff,          # character_descent
        0,                # character_attributes
    )


def _metrics_bytes(m):
    return struct.pack(">5hH", *m)


def build_metrics_table(font, order):
    body = struct.pack("<I", FORMAT_BE) + struct.pack(">I", len(order))
    for cp in order:
        body += _metrics_bytes(glyph_metrics(font.glyphs[cp]))
    return body


def build_bitmaps_table(font, order):
    offsets = []
    data = bytearray()
    for cp in order:
        offsets.append(len(data))
        data += font.glyphs[cp].bitmap(GLYPH_PAD)

    # bitmap_sizes[i] is the total data size were rows padded to 1<<i bytes;
    # the reader picks index (format & 3), which is 2 for us.
    sizes = []
    for shift in range(4):
        pad = 1 << shift
        sizes.append(sum(font.glyphs[cp].row_bytes(pad) * font.glyphs[cp].h
                         for cp in order))

    body = struct.pack("<I", FORMAT_BITMAPS) + struct.pack(">I", len(order))
    for off in offsets:
        body += struct.pack(">I", off)
    body += struct.pack(">4I", *sizes)
    body += data
    return body


def build_encodings_table(font, order):
    index_of = {cp: i for i, cp in enumerate(order)}
    min_b1 = min(cp >> 8 for cp in order)
    max_b1 = max(cp >> 8 for cp in order)
    min_b2 = min(cp & 0xFF for cp in order)
    max_b2 = max(cp & 0xFF for cp in order)
    cols = max_b2 - min_b2 + 1
    rows = max_b1 - min_b1 + 1

    default_char = 0x20 if 0x20 in index_of else order[0]
    grid = [NO_GLYPH] * (rows * cols)
    for cp, gi in index_of.items():
        r = (cp >> 8) - min_b1
        c = (cp & 0xFF) - min_b2
        grid[r * cols + c] = gi

    body = struct.pack("<I", FORMAT_BE)
    body += struct.pack(">5h", min_b2, max_b2, min_b1, max_b1, default_char)
    for value in grid:
        body += struct.pack(">H", value)
    return body


def build_accelerators_table(font, order):
    mets = [glyph_metrics(font.glyphs[cp]) for cp in order]
    minb = tuple(min(m[i] for m in mets) for i in range(6))
    maxb = tuple(max(m[i] for m in mets) for i in range(6))

    body = struct.pack("<I", FORMAT_BE)
    body += struct.pack(
        ">BBBBBBBBIII",
        0,  # no_overlap        - conservative
        0,  # constant_metrics  - proportional font
        0,  # terminal_font
        0,  # constant_width
        0,  # ink_inside
        0,  # ink_metrics
        0,  # draw_direction    - left to right
        0,  # padding
        font.ascent,
        font.descent,
        0,  # max_overlap
    )
    body += _metrics_bytes(minb)
    body += _metrics_bytes(maxb)
    return body


def build_pcf(font):
    order = sorted(font.glyphs)

    tables = [
        (PCF_ACCELERATORS, build_accelerators_table(font, order)),
        (PCF_METRICS, build_metrics_table(font, order)),
        (PCF_BITMAPS, build_bitmaps_table(font, order)),
        (PCF_BDF_ENCODINGS, build_encodings_table(font, order)),
    ]

    header_size = 8 + 16 * len(tables)  # magic + count, then the directory
    offset = header_size
    placed = []
    for type_, body in tables:
        pad = (-offset) % 4  # every table starts on a 4-byte boundary
        offset += pad
        placed.append((type_, body, offset, pad))
        offset += len(body)

    out = bytearray(struct.pack("<4sI", b"\x01fcp", len(tables)))
    for (type_, body, table_offset, _) in placed:
        fmt = struct.unpack("<I", body[0:4])[0]
        out += struct.pack("<IIII", type_, fmt, len(body), table_offset)
    for (_, body, _, pad) in placed:
        out += bytes(pad) + body

    return bytes(out), order, {t: len(b) for t, b, _, _ in placed}


# --- verification ----------------------------------------------------------
def verify_pcf(blob, font):
    """Re-read the produced file per the spec and compare against the BDF.

    Deliberately independent of the writer above: it walks the directory,
    resolves each codepoint through the encoding grid, reads that glyph's
    metrics and bitmap, and checks both against the source glyph.
    """
    magic, count = struct.unpack_from("<4sI", blob, 0)
    assert magic == b"\x01fcp", "bad magic"

    directory = {}
    for i in range(count):
        type_, fmt, size, off = struct.unpack_from("<IIII", blob, 8 + 16 * i)
        directory[type_] = (fmt, size, off)

    for type_ in (PCF_ACCELERATORS, PCF_METRICS, PCF_BITMAPS, PCF_BDF_ENCODINGS):
        assert type_ in directory, f"missing table {type_}"
        fmt, _, off = directory[type_]
        assert struct.unpack_from("<I", blob, off)[0] == fmt, "format word mismatch"
        assert fmt & PCF_BYTE_MASK, "table not marked big-endian"

    assert directory[PCF_BITMAPS][0] == 0xE, "bitmap format must be 0xE"

    # accelerators: the ascent the renderer will use
    acc_off = directory[PCF_ACCELERATORS][2]
    asc, desc = struct.unpack_from(">II", blob, acc_off + 4 + 8)
    assert asc == font.ascent, f"font_ascent {asc} != BDF {font.ascent}"
    assert desc == font.descent, f"font_descent {desc} != BDF {font.descent}"

    # encoding grid
    enc_off = directory[PCF_BDF_ENCODINGS][2]
    min_b2, max_b2, min_b1, max_b1, _ = struct.unpack_from(">5h", blob, enc_off + 4)
    cols = max_b2 - min_b2 + 1
    indices_at = enc_off + 14

    met_off = directory[PCF_METRICS][2]
    met_count = struct.unpack_from(">I", blob, met_off + 4)[0]
    first_metric = met_off + 8

    bmp_off = directory[PCF_BITMAPS][2]
    glyph_count = struct.unpack_from(">I", blob, bmp_off + 4)[0]
    first_bitmap = bmp_off + 4 * (6 + glyph_count)
    bitmap_sizes = struct.unpack_from(">4I", blob, bmp_off + 8 + 4 * glyph_count)

    assert met_count == glyph_count == len(font.glyphs), "glyph count mismatch"
    assert first_bitmap + bitmap_sizes[2] <= len(blob), "bitmap data truncated"

    checked = 0
    for cp, g in font.glyphs.items():
        b1, b2 = cp >> 8, cp & 0xFF
        assert min_b1 <= b1 <= max_b1 and min_b2 <= b2 <= max_b2, f"U+{cp:04X} outside grid"
        gi = struct.unpack_from(">H", blob, indices_at + 2 * ((b1 - min_b1) * cols + (b2 - min_b2)))[0]
        assert gi != NO_GLYPH, f"U+{cp:04X} has no glyph index"

        lsb, rsb, cw, casc, cdesc, _ = struct.unpack_from(">5hH", blob, first_metric + 12 * gi)
        # exactly what pcf.py will hand to the renderer
        width, height = rsb - lsb, casc + cdesc
        assert (width, height, lsb, -cdesc, cw) == (g.w, g.h, g.xoff, g.yoff, g.dwidth), (
            f"U+{cp:04X} geometry {(width, height, lsb, -cdesc, cw)} "
            f"!= BDF {(g.w, g.h, g.xoff, g.yoff, g.dwidth)}"
        )

        # bitmap, decoded the way pcf.py's fallback path does
        data_at = first_bitmap + struct.unpack_from(">I", blob, bmp_off + 8 + 4 * gi)[0]
        stride = 4 * ((width + 31) // 32)
        for y in range(height):
            row = blob[data_at + y * stride:data_at + y * stride + stride]
            src = g.rows[y]
            for x in range(width):
                got = (row[x // 8] >> (7 - (x % 8))) & 1
                want = (src[x // 8] >> (7 - (x % 8))) & 1
                assert got == want, f"U+{cp:04X} pixel ({x},{y}) differs"
            assert not any(row[(width + 7) // 8:]), f"U+{cp:04X} row {y} padding not zero"
        checked += 1

    return checked


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        return 1
    src, dst = args

    font = parse_bdf(src)
    blob, order, sizes = build_pcf(font)

    if "--no-verify" not in sys.argv:
        checked = verify_pcf(blob, font)
        print(f"verified {checked} glyphs: metrics and every bitmap pixel match {src}")

    with open(dst, "wb") as f:
        f.write(blob)

    names = {
        PCF_ACCELERATORS: "accelerators",
        PCF_METRICS: "metrics",
        PCF_BITMAPS: "bitmaps",
        PCF_BDF_ENCODINGS: "encodings",
    }
    print(f"wrote {dst}: {len(order)} glyphs, {len(blob)} bytes")
    for type_, size in sorted(sizes.items()):
        print(f"    {names[type_]:<13} {size:6} bytes")
    print(f"    ascent {font.ascent}, descent {font.descent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
