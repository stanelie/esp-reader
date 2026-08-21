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
python3 "$(dirname "$0")/build_pf.py" "$LIT"    "$OUT/literata.pf"        13 mono 350 15   # 14
python3 "$(dirname "$0")/build_pf.py" "$LIT"    "$OUT/literata-large.pf"  15 mono 400 16   # 16
python3 "$(dirname "$0")/build_pf.py" "$LIT"    "$OUT/literata-larger.pf" 18 mono 400 19   # 19
python3 "$(dirname "$0")/build_pf.py" "$DEJAVU" "$OUT/dejavu.pf"          11 mono 400 15   # 13

# Open Sans is the one that needs its box clipped: mono at size 11 gives a
# 12-row box and at size 12 a 14-row box, and the layout wants 13. Size 12 with
# the box capped at 13 drops one row from the top, which is above every
# ascender and capital in the face - checked, not assumed.
python3 "$(dirname "$0")/build_pf.py" "$OPENSANS" "$OUT/open-sans.pf"    12 mono 400 13   # 13
