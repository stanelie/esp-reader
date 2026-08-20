# tools

Two are host-side, one runs on the device.

| tool | runs on | what it does |
|---|---|---|
| `battery_calibrate.py` | **device** | logs raw ADC counts at a known supply voltage |
| `battery_fit.py` | host | turns that log into `cal_slope` / `cal_offset` |
| `build_hyphen_patterns.py` | host | turns a TeX `.pat.txt` into the reader's pattern blob |
| `tests/test_hyphenator.py` | host | proves the hyphenator matches the implementation it was ported from |
| `tests/test_pagination.py` | host | paginates a real book and checks the page-boundary invariant |
| `tests/test_epub.py` | host | converts a real EPUB in a sandbox and paginates the result |
| `tests/test_pf.py` | host | decodes `.pf` glyph bitmaps: letters not stripes, stems not too heavy |
| `tests/test_structure.py` | host | checks code.py still defines everything it needs |
| `tests/test_drivers.py` | host | exercises both panel drivers against stub hardware |
| `build_pf.py` | host | builds a `.pf` font from any TTF |
| `ttf2bdf.py` | host | rasterises a TTF to a BDF subset (superseded by build_pf.py) |
| `subset_bdf.py` | host | cuts a BDF down to the glyphs a book actually needs |
| `bdf2pcf.py` | host | converts that subset to PCF, which loads far faster |

## Battery calibration

The reader converts raw counts to volts with `volts = raw * CAL_SLOPE +
CAL_OFFSET`, from the panel profile in `device/code.py`. A bare multiplier
cannot fit it — the ESP32-S3 converter does not pass through the origin, so one
constant is right at exactly one voltage and drifts everywhere else. The
constants are per *unit*, not per board: same 390K/100K divider on GPIO7, but
the offset belongs to the individual chip.

**This cannot be done over USB.** With a cable in, the divider reads the supply
node, which USB holds near 4 V whatever the cell is doing — the reading is not
slightly wrong, it is measuring the charger. The logger detects USB and refuses
to record rather than poisoning the fit.

```bash
# 1. put the logger on the device
cp tools/battery_calibrate.py /media/$USER/CIRCUITPY/code.py

# 2. unplug USB. For each voltage: set the PPK2, power up, press either
#    button, wait for the panel to redraw, power down. ~3.4 / 3.7 / 4.2 V
#    covers the range the reader maps to 0-100%.

# 3. plug in, fill in the `volts` column of /battery_cal.csv, then
python3 tools/battery_fit.py /media/$USER/CIRCUITPY/battery_cal.csv

# 4. paste the two constants into the panel profile, restore the reader
cp device/code.py /media/$USER/CIRCUITPY/code.py
```

Take at least three points. Two always fit a line exactly and tell you nothing;
the third is what makes the leave-one-out check in `battery_fit.py` meaningful —
it refits without each point in turn and reports how far off the prediction was,
which is how the E213's numbers were shown to be genuinely linear rather than
merely anchored at the ends.

The logger writes with `storage.remount()` from `code.py`, which only succeeds
while the host is not holding the drive — so it works on battery, needs nothing
in `boot.py`, and the flag lasts one run. There is no way to end up with a
permanently read-only CIRCUITPY.

## Hyphenation patterns

`hyphen_en.bin` is the Badger reader's blob, carried over verbatim because it
was validated against Ned Batchelder's reference over 234k words -
`tests/test_hyphenator.py` re-runs that comparison so the port cannot drift.

`build_hyphen_patterns.py` builds a blob from a TeX `.pat.txt` list. It already
handles what French needs - folding U+2019 to an ASCII apostrophe and the oe
ligature to its latin-9 byte, so that character positions and byte positions
stay the same number, which Liang's algorithm requires. French is not shipped:
patterns are language-specific, mixing sets breaks both, so a second language
also needs a way to choose between them.

```bash
curl -O https://raw.githubusercontent.com/hyphenation/tex-hyphen/master/hyph-utf8/tex/generic/hyph-utf8/patterns/txt/hyph-fr.pat.txt
python3 tools/build_hyphen_patterns.py hyph-fr.pat.txt device/hyphen_fr.txt
```

That yields 1145 patterns in 8.6 KB, with a longest key of 14 characters - the
hyphenator's `_LETTERS_MAX` would have to rise from 9 to match.

## Fonts

`ttf2bdf.py` rasterises, `bdf2pcf.py` converts, `tests/test_fonts.py` checks:

```bash
python3 tools/ttf2bdf.py OpenSans[wdth,wght].ttf 14 /tmp/open-sans-14-r.bdf OpenSans 350
python3 tools/bdf2pcf.py /tmp/open-sans-14-r.bdf device/fonts/open-sans-14-r.pcf
python3 tools/tests/test_fonts.py --show
```

**Always run the third one.** Four fonts once shipped that measured perfectly
and drew as horizontal stripes: Pillow keeps a mode-`"1"` mask as one byte per
pixel while `Image.frombytes("1", ...)` expects packed bits, so the buffer is
8x too long, nothing raises, and only the bitmaps are wrong. Ascent, ink
extents, line counts and characters-per-line were all correct. The only check
that could have caught it was looking at the pixels.

Aim for an ink height of 14-15 rows for 9 lines on the E290 - `ttf2bdf.py`
prints it.

### Stem weight on a 1-bit display

A variable font takes a fifth argument, the weight. It matters more than it
sounds: at 14px Open Sans Regular (400) renders capital stems 2px wide while
its horizontals round down to 1px, so `I B E Y` look bottom-heavy. At 350 the
stems land on 1px and the letter is balanced. Lowercase is identical either
way, which is why the fault shows only on capitals.

`get_variation_axes()` returns Weight first and Width second for Open Sans -
transposing them silently clamps to the axis limits and renders a weight you
did not ask for, which is easy to mistake for a hinting effect.

Literata 15 and 18 also have 2px capital stems. That is proportionate at 18px
and arguably heavy at 15px; lightening them is the same one-argument change.
