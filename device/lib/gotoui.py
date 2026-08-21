# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# The jump-to-percent screen.
#
# Outside code.py for the same reason as convertui: on the RP2040 a kilobyte of
# code compiled at boot costs roughly five kilobytes of CONTIGUOUS heap, and the
# reader needs 4736-byte blocks for page buffers. This screen is opened from a
# menu a few times a session and has no business holding one all day.
#
# It reads the reader's state through its globals rather than importing it back,
# which would run the reader's module body a second time. Nothing here assigns
# to a reader global - run_goto only returns the chosen percent, and the caller
# does the jumping - so read access is all it needs.

import time

R = None


def run(reader_globals):
    global R
    R = reader_globals
    return run_goto()


def _g(name):
    return R[name]


def render_goto_screen(pct, out=None):
    # Deliberately the same y positions as the E213, which is 6px shorter. The
    # slack lands at the bottom, and keeping the two files diffable is worth
    # more than six pixels of centring.
    canvas = _g("begin_frame")()

    title = "Jump to"
    _g("draw_text")(canvas, title, (_g("WIDTH") - _g("get_string_width")(title)) // 2, 0, color=0)

    # No double-size any more: PropFont draws one size, and scaling a 1-bit
    # bitmap by doubling pixels looks worse than the plain glyphs do.
    big = "%d%%" % pct
    _g("draw_text")(canvas, big, (_g("WIDTH") - _g("get_string_width")(big)) // 2, 22, color=0)

    bar_x, bar_y, bar_w, bar_h = 10, 66, _g("WIDTH") - 20, 12
    canvas.rect(bar_x, bar_y, bar_w, bar_h, 0)
    fill = int((bar_w - 4) * pct / 100.0)
    if fill > 0:
        canvas.fill_rect(bar_x + 2, bar_y + 2, fill, bar_h - 4, 0)

    # Minus on the left, plus on the right, matching the bar underneath it.
    hint1 = "double-tap -%d%%    tap +%d%%" % (_g("GOTO_STEP"), _g("GOTO_STEP"))
    _g("draw_text")(canvas, hint1, (_g("WIDTH") - _g("get_string_width")(hint1)) // 2, 86, color=0)
    hint2 = "hold to open here"
    _g("draw_text")(canvas, hint2, (_g("WIDTH") - _g("get_string_width")(hint2)) // 2, 101, color=0)

    return _g("end_frame")(out)


def run_goto():
    # Percentage picker. Returns the chosen percent, or None to stay put.
    #
    #     Same gesture vocabulary as the book picker - tap moves, hold commits, longer
    #     hold backs out - so there is nothing new to learn.
    #
    #     None means "do not move", and it covers backing out, timing out, *and*
    #     committing the value we arrived on. That last one matters: the number on
    #     screen is rounded to _g("GOTO_STEP"), so a reader sitting at 37% sees 35%, and
    #     jumping to the 35% the screen offers would quietly shove them back two
    #     percent of the book - a couple of pages, silently, for a gesture that
    #     looked like "stay here". Coming in and confirming without moving now costs
    #     nothing at all, and the exact position survives because nothing is asked to
    #     reconstruct it.
    #
    _g("release_neighbours")()
    # One buffer for the whole menu session. Every redraw used to take another
    # from the pool and drop it, so scrolling a list drained the pool: the
    # buffers became garbage rather than going back, and the reader then died
    # in prefetch_neighbours() on the way out, with nothing left to draw into.
    _ui = _g("_take_buf")()
    try:
        start_pct = _g("current_percent")()
        pct = start_pct
        idle_since = time.monotonic()
        pending_release = None
        _g("drain_events")()

        while True:
            _g("display_page")(render_goto_screen(pct, _ui))

            # The refresh outlasts the double-tap window, so if that tap was really
            # the first half of a double, the second press is already queued.
            if pending_release is not None:
                if _g("was_double_tap")(pending_release):
                    pct = (pct - 2 * _g("GOTO_STEP")) % (100 + _g("GOTO_STEP"))
                pending_release = None

            while True:
                event = _g("next_press")()
                if event is None:
                    if time.monotonic() - idle_since >= _g("PICKER_TIMEOUT"):
                        _g("log_step")("Jump-to idle; staying where we were.")
                        return None
                    time.sleep(0.02)
                    continue

                if event.key_number == _g("KEY_BACK"):
                    pct = (pct - _g("GOTO_STEP")) % (100 + _g("GOTO_STEP"))
                    break

                kind, release = _g("classify_hold")(_g("KEY_NEXT"), event.timestamp)
                if kind == "picker":        # hold opens the book here
                    if pct == start_pct:
                        _g("log_step")("Jump-to: %d%% unchanged, keeping the exact "
                                 "position (offset %d)."
                                 % (pct, _g("page_offsets")[_g("current_page_idx")]))
                        return None
                    return pct
                if kind == "sleep":         # hold longer cancels
                    return None
                # Wraps at the top: on a one-button device, getting from 100% back
                # to 5% should not need twenty double-taps.
                pct = (pct + _g("GOTO_STEP")) % (100 + _g("GOTO_STEP"))
                pending_release = release
                break

            idle_since = time.monotonic()


    finally:
        _g("_give_buf")(_ui)
