#!/bin/bash
# Symlink both board definitions into a CircuitPython checkout, apply the
# light-sleep patch if it is missing, and report drift from upstream.
#
#   ./install.sh [/path/to/circuitpython]     (default: ~/circuitpython)
#
# The definitions live here rather than in the checkout on purpose. The E290's
# is a fork of a board that exists upstream, so editing it in place would mean
# a tracked file that `git pull` silently reverts -- taking real light sleep
# with it, without changing anything you would think to look at.
set -u

CP="${1:-$HOME/circuitpython}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOARDS_DIR="$CP/ports/espressif/boards"
PATCH="$HERE/patches/0001-opt-in-real-light-sleep.patch"

# board dir : upstream board it was forked from ("" = wholly ours)
BOARDS="heltec_vision_master_e213:
heltec_vision_master_e290_lightsleep:heltec_vision_master_e290"

if [ ! -d "$BOARDS_DIR" ]; then
    echo "error: $BOARDS_DIR not found -- is $CP a CircuitPython checkout?" >&2
    exit 1
fi

for entry in $BOARDS; do
    board="${entry%%:*}"
    upstream="${entry#*:}"
    target="$BOARDS_DIR/$board"

    if [ -L "$target" ]; then
        current="$(readlink "$target")"
        if [ "$current" != "$HERE/boards/$board" ]; then
            ln -sfn "$HERE/boards/$board" "$target"
            echo "re-pointed: $board (was $current)"
        else
            echo "already linked: $board"
        fi
    elif [ -e "$target" ]; then
        echo "error: $target exists and is not a symlink; refusing to touch it" >&2
        continue
    else
        ln -s "$HERE/boards/$board" "$target"
        echo "linked: $board"
    fi

    # Drift: for a forked board, everything but mpconfigboard.h should still be
    # byte-identical to the board it came from. A stale copy of board.c is a
    # miserable thing to debug, so surface it here instead.
    if [ -n "$upstream" ] && [ -d "$BOARDS_DIR/$upstream" ]; then
        for f in board.c pins.c mpconfigboard.mk sdkconfig; do
            if ! diff -q "$BOARDS_DIR/$upstream/$f" "$HERE/boards/$board/$f" >/dev/null 2>&1; then
                echo "  drift: $f differs from $upstream -- re-copy it:"
                echo "      cp $BOARDS_DIR/$upstream/$f $HERE/boards/$board/$f"
            fi
        done
    fi
done

echo
if grep -q "CIRCUITPY_ESP_REAL_LIGHT_SLEEP" "$CP/ports/espressif/common-hal/alarm/__init__.c" 2>/dev/null; then
    echo "light-sleep patch: applied"
else
    echo "light-sleep patch: applying..."
    if (cd "$CP" && patch -p1 < "$PATCH"); then
        echo "light-sleep patch: applied"
    else
        echo "light-sleep patch: FAILED -- without it both boards build firmware"
        echo "  that claims real light sleep and still spins on WFI at ~43 mA." >&2
    fi
fi

echo
echo "forked from upstream $(cat "$HERE/.upstream-base" 2>/dev/null || echo '?')"
echo "build with:"
echo "  cd $CP/ports/espressif && . ./esp-idf/export.sh"
echo "  make BOARD=heltec_vision_master_e213 -j\$(nproc)"
echo "  make BOARD=heltec_vision_master_e290_lightsleep -j\$(nproc)"
