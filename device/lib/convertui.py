# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# On-device EPUB -> text conversion, and the screens that report it.
#
# This lives outside code.py because it is compiled only when a .epub is
# actually opened. On the RP2040 a kilobyte of code compiled at boot costs
# about five kilobytes of CONTIGUOUS heap - not of free memory, of the largest
# single block - and the reader needs 4736-byte blocks for page buffers. Code
# that runs once a month should not be holding one of them hostage.
#
# It takes the reader's globals rather than importing it back: a two-way import
# between code.py and this would run the reader's module body a second time.

import time

R = None


def convert(path, reader_globals):
    global R
    R = reader_globals
    return convert_epub(path)


def _g(name):
    return R[name]


def convert_epub(path):
    # Convert an .epub to a .txt beside it. Returns the .txt path, or None.
    #
    #     The conversion writes to the drive, which the board can only do when the
    #     USB host is not holding it - so this works on battery and refuses, with a
    #     reason on screen, when plugged in. epub_xtract makes that call itself; all
    #     this does is show what it decided.
    #
    _ui = _g("_take_buf")()
    try:
        title = _g("book_title")(path)
        # Partial, not full. A full refresh flashes for ~2.5 s before a conversion
        # that is itself slow, and this screen is transient. The cost is that some
        # of the page underneath may ghost through it - on a message this brief,
        # that is a fair trade.
        _g("display_page")(_g("render_message_into")(_ui, "Converting", [title, "", "Opening the EPUB…"]))

        try:
            import epub_xtract
        except Exception as e:
            _g("display_page")(_g("render_message_into")(_ui, "Cannot convert",
                                        ["The EPUB converter is not installed:",
                                         "%s" % e, "",
                                         "lib/epub_xtract.py, uzipfile.py",
                                         "and inflate.py are needed."]))
            time.sleep(4)
            return None

        # One update per chapter, no rate limit. That used to cost the conversion
        # ~0.5 s per draw, because the driver waited for each refresh to finish
        # before returning; it now starts the refresh and collects the wait only
        # when something next touches the panel. So the panel redraws while the
        # next chapter is being decompressed, and the drawing is very nearly free.
        def progress(stage, done, total, name=""):
            if stage == "chapter":
                _g("display_page")(_g("render_message_into")(_ui, "Converting",
                                            [title, "", "%d of %d" % (done, total)]))
            elif stage == "readonly":
                _g("display_page")(_g("render_message_into")(_ui, "Cannot convert",
                                            ["The USB host owns the drive.", "",
                                             "Unplug and convert on battery,",
                                             "then plug back in."]))
            elif stage in ("failed", "empty"):
                _g("display_page")(_g("render_message_into")(_ui, "Conversion failed",
                                            [title, "",
                                             "Nothing could be extracted.",
                                             "See the .convert.log beside it."]))

        # Absolute, always. The reader names books the way its picker lists them -
        # "alice.epub" in the root, "books/alice.epub" under /books - while
        # epub_xtract.source_path() reads a name without a leading slash as being
        # inside /books. So "alice.epub" was looked for at /books/alice.epub and
        # "books/alice.epub" at /books/books/alice.epub. Both conventions are
        # reasonable; they just are not the same one.
        source = path if path.startswith("/") else "/" + path

        _g("set_led")(True)
        t0 = time.monotonic()
        out = None
        err = None
        try:
            out = epub_xtract.convert_book(source, progress=progress, keep_display=True)
        except Exception as e:
            err = e
            _g("log_step")("EPUB conversion raised: %s" % e)
        _g("set_led")(False)

        # Hand the drive back. epub_xtract takes it with storage.remount() and does
        # not return it, so without this the host sees a read-only CIRCUITPY until
        # the next reset - the same trap the battery logger had.
        try:
            import storage
            storage.remount("/", readonly=True)
        except Exception:
            pass

        if out is None:
            # Put the converter's own last lines on the panel. The .convert.log has
            # the full story, but reading it means plugging in, and the thing that
            # just failed may be the reason the log is not there.
            detail = []
            try:
                detail = list(epub_xtract.STATUS_HISTORY)[-4:]
            except Exception:
                pass
            if err is not None:
                detail.append("raised: %s" % err)
            _g("display_page")(_g("render_message_into")(_ui, "Conversion failed", [title, ""] + detail))
            time.sleep(6)
            return None

        if out:
            _g("log_step")("Converted %s in %.1fs -> %s" % (path, time.monotonic() - t0, out))
            # epub_xtract returns an absolute path; the reader's book list is
            # relative to the root for files in it, so match its convention.
            return out[1:] if out.startswith("/") else out

        time.sleep(3)
        return None


    finally:
        _g("_give_buf")(_ui)
