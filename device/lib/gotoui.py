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


def _resolve_tap(release):
    # Was the tap that just finished the first half of a double-tap, or does
    # it stand alone? Returns ("single", None), ("double", None), or
    # ("held", kind) where kind is classify_hold()'s own "picker"/"sleep".
    #
    #     The book picker's was_double_tap() decides this from press timing
    #     alone: if a new KEY_NEXT press starts within DOUBLE_TAP_MS of the
    #     release, it commits to "double tap" without checking what that new
    #     press turns out to be. That is safe there because only one gesture
    #     happens per pass through the main loop. Here it is not: dialling in
    #     a percentage means tapping repeatedly, then immediately holding to
    #     confirm - and a hold's press-down lands inside that same window as
    #     often as a real double-tap's second press does. Measured on this
    #     board: rapid dial-taps land 530-670 ms apart, a real double-tap
    #     220-260 ms - DOUBLE_TAP_MS=350 separates those cleanly, so the
    #     window itself was never the problem. Treating any fast press as a
    #     confirmed double-tap was: it swallowed the hold's press-down whole,
    #     including its later release, discarding the confirm gesture and
    #     silently applying a double-tap's -GOTO_STEP - a reader who dialled
    #     to 25% and held right away would land 10% off with no warning,
    #     because the hold never got to run classify_hold() at all.
    #
    #     So this waits out the same window, but does not decide from timing
    #     alone: whatever press arrives gets classified for real, the same
    #     way any other press does, and only a press that itself resolves as
    #     a quick "tap" counts as the double-tap's second half. A press that
    #     resolves as a hold is returned as-is, so the caller can act on it
    #     as the confirm/cancel gesture it actually was rather than lose it.
    #
    deadline = time.monotonic() + _g("DOUBLE_TAP_MS") / 1000.0
    second = None
    while time.monotonic() < deadline:
        second = _g("next_press")()
        if second is not None:
            break
        time.sleep(0.005)

    if second is None:
        return "single", None

    if (second.key_number != _g("KEY_NEXT")
            or _g("ticks_ms_diff")(second.timestamp, release) > _g("DOUBLE_TAP_MS")):
        # Not part of this gesture (KEY_BACK, or arrived just past the
        # window) - not ours to consume. Replay it on the next pass.
        R["_stashed_press"] = second
        return "single", None

    kind, _release = _g("classify_hold")(_g("KEY_NEXT"), second.timestamp)
    if kind == "tap":
        return "double", None
    return "held", kind


def run_goto():
    # Percentage picker. Returns the chosen percent, or None to stay put.
    #
    #     Same gesture vocabulary as the book picker - tap moves, hold commits, longer
    #     hold backs out - so there is nothing new to learn. See _resolve_tap() for
    #     why the double-tap check is not the shared was_double_tap().
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
        _g("drain_events")()

        def _confirm(kind):
            # Shared tail for a hold, whichever tap led to it.
            if kind == "picker":
                if pct == start_pct:
                    _g("log_step")("Jump-to: %d%% unchanged, keeping the exact "
                             "position (offset %d)."
                             % (pct, _g("page_offsets")[_g("current_page_idx")]))
                    return None
                return pct
            return None   # "sleep": hold longer cancels

        while True:
            _g("display_page")(render_goto_screen(pct, _ui))

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
                if kind in ("picker", "sleep"):
                    return _confirm(kind)
                # kind == "tap": do not commit to +GOTO_STEP yet - see
                # _resolve_tap() for why a fast follow-up needs classifying,
                # not just timing, before deciding what this tap was.
                outcome, held_kind = _resolve_tap(release)
                if outcome == "double":
                    pct = (pct - _g("GOTO_STEP")) % (100 + _g("GOTO_STEP"))
                    break
                # "single" or "held": the tap that just finished stands on
                # its own either way.
                pct = (pct + _g("GOTO_STEP")) % (100 + _g("GOTO_STEP"))
                if outcome == "held":
                    return _confirm(held_kind)
                break

            idle_since = time.monotonic()


    finally:
        _g("_give_buf")(_ui)
