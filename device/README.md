# What goes on the device

Copy the **contents of this folder** to the root of the CIRCUITPY drive, add a
`.txt` book, and reset. Nothing else to install. The same contents work on both
boards — the reader works out which one it is on at boot.

```
CIRCUITPY/
  code.py                     the reader, both boards
  boot.py                     disables auto-reload; see the note in it
  lib/
    lcmen2r13efc1.py          E213 panel driver (UC8151-class)
    ssd1680e290.py            E290 panel driver (SSD1680)
    bookmarks.py              per-book reading positions in NVM
    hyphenator.py             Liang hyphenation (English)
    epub_xtract.py            EPUB -> text conversion
    uzipfile.py               minimal zip reader
    inflate.py                pure-Python DEFLATE, fallback only
    adafruit_framebuf.mpy
    propfont.py               the .pf font renderer
    fbrotate.py               landscape -> panel transpose
  hyphen_en.bin               ~4900 Knuth-Liang patterns, 31 KB. Without it
                              the reader wraps on whole words and says so in
                              the boot log.
  font5x8.bin                 adafruit_framebuf's built-in 8x12 font. Small,
                              easy to miss, and the battery percentage and the
                              calibration tool both draw with it - without it
                              they raise rather than degrade.
  fonts/
    literata.pf               serif, 9 lines - the default
    open-sans.pf              sans, 9 lines
    dejavu.pf                 sans, 9 lines
    literata-large.pf         serif, 7 lines
    literata-larger.pf        serif, 6 lines - large print
  YourBook.txt                any plain UTF-8 text file
```

Both drivers ship. The unused one is never imported, so it costs drive space
(~10 KB of ~11.7 MB free) and nothing else — and it means one folder is
correct for either device.

## Which board am I on

`code.py` reads `board.board_id` at boot and looks it up in `BOARDS`:

| board_id | panel | real light sleep |
|---|---|---|
| `heltec_vision_master_e213` | E213 | yes |
| `heltec_vision_master_e290_lightsleep` | E290 | yes |
| `heltec_vision_master_e290` | E290 | no (upstream stock build) |

An unrecognised board **halts** — it logs to the UART, blinks the LED and
raises. It does not fall back to a default, because the two pin maps are
permutations of the same six lines: the wrong one puts a driven output onto the
line the panel is driving as BUSY. On a generic ESP32-S3 build, whose board_id
names no panel, set `BOARD_OVERRIDE` at the top of `code.py`.

Note the third row. Real light sleep is a property of the *firmware*, not the
board, and it is knowable here only because the patched E290 build was given its
own board name. On upstream's stock build the reader drops through to deep sleep
after 20 s instead of resting at 43 mA.

## Controls

One button does everything, by how long you hold it.

| gesture | reading | in a menu |
|---|---|---|
| tap | next page | move down |
| double-tap | previous page | move up |
| hold (~0.7 s, LED on) | open the library | select |
| hold longer (~2.5 s, LED off) | sleep | back out |

The BOOT button is an optional shortcut for "previous page" / "move up".

The library has a **Jump to…** row at the top, which opens a percentage picker
using the same gestures. Jumping does not paginate the book from the start — it
snaps to a line a few KB back from the target and walks forward, so it costs a
couple of milliseconds regardless of book size.

## EPUB

Drop an `.epub` in the root or `/books` and it appears in the picker as
`Name.epub` - keeping its extension, unlike every other row, because that row
is not a book yet: selecting it starts a conversion that takes a minute and
needs the board off USB. Afterwards it is an ordinary book listed as `Name`,
and the `.epub` is no longer offered.

The reader itself is unchanged - it still streams a plain text file by byte
offset, which is exactly what keeps resume, jump-to, back-navigation and
hyphenation working. Converting up front rather than reading the zip directly
is what buys that.

**Conversion only works on battery.** It writes to the drive, and with USB
attached the host owns the filesystem: `storage.remount()` can be made to
succeed anyway, but then both sides write from different ideas of what is on
the disk and the host's cached directory wins - the new book comes back as a
0-byte file. So it refuses, and says so on the panel. Unplug, convert, plug
back in.

The source `.epub` is kept (`DELETE_SOURCE_AFTER_CONVERT = False` in
`epub_xtract.py`). The Badger deleted it because its flash held a book or two;
this board has ~11 MB free.

Alice in Wonderland converts in well under a second of actual work - 14 of 14
chapters, 172 KB of text, 613 pages - and a `.convert.log` is written beside
the book so a conversion that dies mid-way leaves its reasons behind.

## Fonts

Whatever is in `/fonts` is offered under **Fonts…** in the picker, so adding one
is a file copy. `.pf` is a compact bitmap format - one blob with a fixed glyph
box per character, blitted into the framebuffer a byte at a time. It replaced
PCF, which was 4-6x larger, covered 110 characters rather than the whole of
Latin-1, needed `adafruit_bitmap_font`, and drew a pixel at a time through a
per-glyph cache of Python tuples.

Characters above U+00FF are folded to the nearest thing inside it before
measuring or drawing - curly quotes to `"` and `'`, em and en dashes to `-`,
the ellipsis to `...`. That is 1.66% of the books here, so it is not a rare
path. The fold happens only in the font wrappers, never in the text pagination
works on, because page offsets are byte positions and `...` is three characters
where `…` was one.

| font | glyph box | E290 | E213 |
|---|---|---|---|
| Literata (default) | 14 | 9 lines | 8 |
| Open Sans | 13 | 9 lines | 8 |
| Dejavu | 13 | 9 lines | 8 |
| Literata Large | 16 | 7 lines | 7 |
| Literata Larger | 19 | 6 lines | 6 |

The layout is derived from the font rather than declared:

    line pitch = the font's glyph box + the panel's `leading`
    first line = the panel's `page_margin`

A `.pf` glyph box already carries the room a line needs above and below its
ink, so both numbers are small - 0 or 1 here.

Changing font does not lose your place. The reader stores a byte offset, never
a page number, so it repaginates from where you are. The choice lives in NVM
and survives a flat battery.

`tools/build_pf.py` makes a `.pf` from any TTF - size, threshold and weight are
arguments. Aim for a glyph box of 13-14 rows to get 9 lines on the E290, and
run `tools/tests/test_pf.py` afterwards.

Build them with `tools/build_fonts.sh`, which records the exact arguments -
the first set was built by hand without recording them, and recovering the
arguments later meant brute-forcing them against the shipped bytes.

Rasterise through FreeType's monochrome hinted renderer (`mono`), not by
thresholding a greyscale render. Any fixed threshold is wrong somewhere: at
13px, low enough to keep the bowl of an `o` connected is also low enough to
smear every stem to 2px, and there is no value in between that does both. The
default font shipped at threshold 150 with 15 letters whose strokes had holes
in them - `o`, `b`, `e`, `p`, `O` and more - because a broken bowl looks fine
by every metric the tests had: right ink, right stem width, right advance. The
hinted rasteriser has dropout control, which is precisely the guarantee that a
thin stroke still comes out as a connected run.

`tests/test_pf.py` checks two things that catch opposite failures: stem width
over lowercase (a font whose verticals are all 2px reads as heavy), and the
number of enclosed white regions per letter, which goes 1 to 0 the moment a
bowl breaks.

## Hyphenation

Long words are split across lines using Liang's algorithm - the same one TeX
uses - with the standard English patterns. Measured over 400 pages of
Neuromancer it tightens the ragged right edge from 24.3 px to 17.6 px on a
292 px line, 27%, and saves about one page in a hundred.

It costs ~3 calls per page, because it is only consulted for the word that
overflows a line, never for words that fit. The 31 KB pattern blob is read once
at boot.

A word that already contains a hyphen, em dash or en dash breaks after that
character instead, adding nothing. Measured, this is rare - 4 times in 254
pages of the demo book, 0 in 400 pages of a novel - because the break only
applies to the word that overflowed, and by then the space left on the line is
small.

Wrapped lines are then justified (`JUSTIFY_TEXT`), with gaps capped at
`MAX_SPACE_STRETCH` times a normal space; a line needing more than that is left
ragged rather than turned into rivers of white. Only lines that were wrapped
are justified - `read_page_stream` records which those are, because a
hard-wrapped source file makes it impossible to tell afterwards.

**A page never ends on a hyphenated word.** A page's start is a byte offset into
the file, and resume, back-navigation and jump-to all re-derive a page from that
offset. If the last line of a page were hyphenated, that offset would land
mid-word and those paths would have to agree about a split they cannot see. The
wrapper declines to hyphenate the final line of a page, which keeps every
boundary on a whole word - the invariant this reader's pagination has always
had. `tools/tests/test_pagination.py` checks it over a real book.

Set `ENABLE_HYPHENATION = False` in `code.py` to turn it off, or delete
`hyphen_en.bin` - either way the reader falls back to whole-word wrapping.

## Third-party files

| file | origin | licence |
|---|---|---|
| `lib/ssd1680e290.py` | original, **except** its partial-update LUT, transcribed from [GxEPD2](https://github.com/ZinggJM/GxEPD2) | GPL-3.0-or-later - the reason this repository is GPL |
| `lib/lcmen2r13efc1.py` | ported from [todd-herbert/heltec-eink-modules](https://github.com/todd-herbert/heltec-eink-modules) | MIT |
| `lib/hyphenator.py` | ported from the [Badger 2040 reader](https://github.com/stanelie/badger2040-ebook-reader); algorithm after Ned Batchelder's hyphenate.py | public domain |
| `hyphen_en.bin` | Knuth-Liang patterns + ushyphmax (Gerard D.C. Kuiken) | public domain |
| `lib/epub_xtract.py`, `lib/uzipfile.py`, `lib/inflate.py` | from the Badger 2040 reader, same author | GPL-3.0-or-later here |
| `lib/adafruit_framebuf.mpy`, `font5x8.bin` | [Adafruit CircuitPython Bundle](https://github.com/adafruit/Adafruit_CircuitPython_Bundle) | MIT |
| `lib/propfont.py` | from the [Badger 2040 reader](https://github.com/stanelie/badger2040-ebook-reader), same author | GPL-3.0-or-later here |
| `fonts/literata*.pf` | [Literata](https://fonts.google.com/specimen/Literata) | SIL Open Font License 1.1 |
| `fonts/open-sans.pf` | [Open Sans](https://github.com/googlefonts/opensans) | SIL Open Font License 1.1 |
| `fonts/dejavu.pf` | [DejaVu Sans](https://dejavu-fonts.github.io/) | DejaVu Fonts License (Bitstream Vera derivative) |

The Adafruit `.mpy` files are compiled for CircuitPython 9.x/10.x. If a future
CircuitPython release changes the `.mpy` format, replace them from the bundle
matching your version rather than debugging import errors.

Everything not listed above is original and GPL-3.0-or-later; see `LICENSE`
in the repository root.
