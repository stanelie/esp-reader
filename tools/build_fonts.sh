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
# 11 is measured, and the deciding measurement is stem consistency, not ink.
#
# Literata's optical-size axis draws small sizes wider and sturdier, which
# costs no height - and height is what costs a line on a 128px panel. But the
# hinted rasteriser rounds each stem independently, so as the face gets sturdier
# individual glyphs tip from a 1px stem to 2px while their neighbours do not,
# and a single thick `b` or `J` in a page of prose is obvious.
#
# Swept opsz 8-12 x weight 350-395, over a 130-character sample. Exactly one
# combination has NO 2px stems while still fitting 11 words to the line:
#
#   opsz 11 w350   ink 3259   width 838   2px stems: none      <- this
#   opsz  8 w350   ink 3376   width 882   2px stems: J
#   opsz  9 w350   ink 3355   width 849   2px stems: b 1 J
#   opsz 12 w365   ink 3321   width 837   2px stems: p
#
# For reference the hand-built font this is meant to match measures 3412 ink at
# 849px and has a 2px `9`. So this is ~4% lighter than that one and more
# consistent than it. Contrast on the panel is set by the driver's waveform -
# full refreshes use the OTP table - not by squeezing ink out of the face.
python3 "$(dirname "$0")/build_pf.py" "$LIT"    "$OUT/literata.pf"        13 mono 350 15 11  # 14
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
