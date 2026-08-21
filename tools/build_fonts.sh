#!/bin/sh
# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Rebuild every bundled .pf from its source TTF.
#
# This file exists because the first set of fonts was built by hand and the
# commands were not written down. Recovering them meant re-deriving the
# arguments by brute force against the shipped bytes. Don't do that again: if
# you build a font, add it here.
#
#     SRC=/path/to/ttfs tools/build_fonts.sh
#
# The sizes are not free choices - each one is picked so the glyph box comes
# out at the height the layout expects, because box_h sets the line pitch and
# therefore how many lines fit a page (9 on the E290, 8 on the E213, for the
# default). Changing a size changes the page. Run tools/tests/test_pf.py after.
set -e
SRC="${SRC:-$HOME/Documents/bf07-work/fonts}"
DEJAVU="${DEJAVU:-/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf}"
LIT="$SRC/Literata-VariableFont_opsz,wght.ttf"
OPENSANS="$SRC/OpenSans-Regular.ttf"
OUT="$(dirname "$0")/../device/fonts"

#                                      size mode weight maxbox   box_h
# opsz 9, not the pixel size. Literata's optical-size axis draws small sizes
# wider and sturdier on purpose, and that costs no HEIGHT - which matters,
# because height is what costs a line on a 128px panel.
#
# 9 is measured, not chosen for looks. Against the hand-built font this is
# meant to match, over a 130-character sample: opsz 13 gives 819px of line and
# 3253 ink pixels (too light), opsz 7 gives 888px and 3417 (dark enough but
# 4.6% wider, which costs a word per line), opsz 9 gives 849px and 3355 - the
# same line width to the pixel, within 2% of the ink.
#
# Weight stays 350. Raising it to 400 does add ink, but by thickening stems to
# 2px: the 1px-stem share falls from 60% to 43%, and the reference font sits at
# 65%. Its extra darkness comes from width, not from heavier strokes.
python3 "$(dirname "$0")/build_pf.py" "$LIT"    "$OUT/literata.pf"        13 mono 350 15 9  # 14
python3 "$(dirname "$0")/build_pf.py" "$LIT"    "$OUT/literata-large.pf"  15 mono 400 16   # 16
python3 "$(dirname "$0")/build_pf.py" "$LIT"    "$OUT/literata-larger.pf" 18 mono 400 19   # 19
python3 "$(dirname "$0")/build_pf.py" "$DEJAVU" "$OUT/dejavu.pf"          11 mono 400 15   # 13

# Open Sans is the one that needs its box clipped: mono at size 11 gives a
# 12-row box and at size 12 a 14-row box, and the layout wants 13. Size 12 with
# the box capped at 13 drops one row from the top, which is above every
# ascender and capital in the face - checked, not assumed.
python3 "$(dirname "$0")/build_pf.py" "$OPENSANS" "$OUT/open-sans.pf"    12 mono 400 13   # 13

# The IBM VGA 8x16 ROM face, carried over from the Badger. Not rasterised from
# an outline - the module in tools/fontsrc/ is already a bitmap, and the
# converter only re-encodes CP437 to Latin-1 and trims the box.
python3 "$(dirname "$0")/vgafont2pf.py" "$(dirname "$0")/fontsrc/vga2_8x16.py" \
        "$OUT/vga-8x16.pf"                                              # 15
