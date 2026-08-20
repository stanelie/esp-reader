#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Subset a BDF font down to the glyphs a text file actually uses.

The Adafruit BDF loader has no glyph index: every load_glyphs() call for an
uncached codepoint re-scans the .bdf line-by-line from byte 0.  A 1163-glyph
font therefore costs seconds of CircuitPython file I/O at boot.  Keeping only
the glyphs the book needs makes that single scan ~10x shorter.

Pass every book you keep on the device: a glyph missing from the subset renders
as a blank gap, so the font has to cover all of them at once.

Usage:
    python3 subset_bdf.py <source.bdf> <out.bdf> <book.txt> [more books...]
"""
import sys

# Always keep printable ASCII (UI strings, page numbers, error messages) plus
# the typographic punctuation that shows up in almost any ebook.
# Always kept, whatever the books happen to contain, so a newly added book in
# any Western European language renders correctly without regenerating the font.
# Characters outside this set still work if present in the font: the reader
# loads them lazily on first use.
EXTRA = set(range(0x20, 0x7F))            # printable ASCII
EXTRA |= set(range(0x00A0, 0x0100))       # Latin-1: French/Spanish/German/Portuguese
                                          # accents, guillemets, nbsp, degree, copyright
EXTRA |= {0x0152, 0x0153, 0x0178}         # OE, oe, Y-diaeresis - French needs these
EXTRA |= {0x2009, 0x200A, 0x202F}         # thin, hair, narrow no-break space
EXTRA |= {0x2013, 0x2014}                 # en dash, em dash
EXTRA |= {0x2018, 0x2019, 0x201A}         # single quotes
EXTRA |= {0x201C, 0x201D, 0x201E}         # double quotes
EXTRA |= {0x2039, 0x203A}                 # single guillemets
EXTRA |= {0x2022, 0x2026, 0x2032, 0x2033} # bullet, ellipsis, prime, double prime
EXTRA |= {0x20AC}                         # euro


def wanted_codepoints(book_paths):
    """Returns (everything to keep, just what the books use)."""
    used = set()
    for path in book_paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                used.update(ord(c) for c in line)
    used -= {ord("\n"), ord("\r"), 0xFFFD}
    keep = set(EXTRA) | used
    return keep, used


def subset(src_path, keep, out_path):
    with open(src_path, encoding="latin-1") as f:
        lines = f.read().split("\n")

    header, blocks, block, encoding = [], [], None, None
    for line in lines:
        if line.startswith("STARTCHAR"):
            block, encoding = [line], None
            continue
        if block is None:
            if not line.strip():
                continue  # e.g. the trailing empty element after the final newline
            if line.startswith("CHARS "):
                header.append("CHARS %d")  # placeholder, filled in below
            elif line.strip() != "ENDFONT":
                header.append(line)
            continue
        block.append(line)
        if line.startswith("ENCODING "):
            encoding = int(line.split()[1])
        elif line.startswith("ENDCHAR"):
            if encoding in keep:
                blocks.append((encoding, block))
            block, encoding = None, None

    kept = {cp for cp, _ in blocks}
    out = []
    for line in header:
        out.append(line % len(blocks) if line == "CHARS %d" else line)
    for _, blk in blocks:
        out.extend(blk)
    out.append("ENDFONT")
    out.append("")

    with open(out_path, "w", encoding="latin-1") as f:
        f.write("\n".join(out))

    return kept, len(blocks)


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    src, out, books = sys.argv[1], sys.argv[2], sys.argv[3:]
    keep, used = wanted_codepoints(books)
    kept, count = subset(src, keep, out)

    print(f"wrote {out}: {count} glyphs covering {len(books)} book(s)")
    print("GLYPH_SET extras for code.py (what these books actually use): " + "".join(
        chr(cp) for cp in sorted(used & kept) if cp < 0x20 or cp > 0x7E))
    missing = sorted(keep - kept)
    if missing:
        preview = " ".join(f"U+{cp:04X}" for cp in missing[:12])
        print(f"note: {len(missing)} requested codepoints absent from source font: {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
