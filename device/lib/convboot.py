# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Queueing an EPUB conversion, and running one on a boot of its own.
#
# Both halves live out here because neither runs during ordinary reading, and
# on the RP2040 a kilobyte of code compiled at boot costs roughly five
# kilobytes of contiguous heap - which is exactly the thing a conversion is
# short of. code.py keeps only the NVM read that decides which boot this is.
#
# Why a separate boot at all: the extractor needs 32KB in one piece for
# DEFLATE's window plus room to compile itself, and it cannot get that beside
# a loaded reader. Releasing the reader's memory first does not work either -
# this heap never compacts, so what comes back is scattered holes. Measured:
# that version got past the import and died asking for 32768 bytes. The only
# thing that works is never allocating in the first place.

import time
import microcontroller
import supervisor

PEND_AT = 256          # clear of the 210 bytes bookmarks uses, and 512 (font)
PEND_MAGIC = 0xEC
PEND_MAX = 96


def _nvm():
    return getattr(microcontroller, "nvm", None)


def restart(log=None):
    # Restart the reader, by whichever route survives on this power source.
    #
    #     On USB: a hard reset, because boot.py has to run - it is the only place
    #     the drive can be hidden and the filesystem handed to the device, and
    #     without that the extractor refuses to write.
    #
    #     On battery: a soft reload. The Badger latches its own power through
    #     ENABLE_DIO, and a hard reset drops that pin to its reset state, which
    #     cuts the 3V3 rail - the board switches off instead of rebooting. It is
    #     the same reason the reset button does nothing unplugged. A soft reload
    #     restarts code.py without touching the chip, so the latch holds.
    #
    #     Skipping boot.py costs nothing there: with no host attached nothing
    #     else owns the filesystem, so the device can take it itself.
    try:
        on_usb = supervisor.runtime.usb_connected
    except Exception:
        on_usb = True
    if log is not None:
        log("Restarting via %s." % ("hard reset" if on_usb else "soft reload"))
    time.sleep(2)
    if on_usb:
        microcontroller.reset()
    else:
        supervisor.reload()


def read_pending():
    try:
        nvm = _nvm()
        if nvm is None:
            return ""
        if nvm[PEND_AT] != PEND_MAGIC or not 0 < nvm[PEND_AT + 1] <= PEND_MAX:
            return ""
        return bytes(nvm[PEND_AT + 2:PEND_AT + 2 + nvm[PEND_AT + 1]]).decode()
    except Exception:
        return ""


def clear():
    try:
        _nvm()[PEND_AT:PEND_AT + 1] = bytes([0])
    except Exception:
        pass


def queue(path, R):
    # Record the path and restart. Never returns.
    log = R["log_step"]
    try:
        b = path.encode()[:PEND_MAX]
        nvm = _nvm()
        nvm[PEND_AT:PEND_AT + 2] = bytes([PEND_MAGIC, len(b)])
        nvm[PEND_AT + 2:PEND_AT + 2 + len(b)] = b
    except Exception as e:
        log("Could not queue %s: %s" % (path, e))
        return
    log("Queued %s; restarting to convert it." % path)
    try:
        R["display_page"](R["render_message_into"](
            R["_take_buf"](), "Converting",
            [R["book_title"](path), "", "Restarting to make room..."]))
    except Exception:
        pass
    restart(log)


def run(path, R):
    # Convert on a clean boot, then restart into the result. Never returns.
    log = R["log_step"]
    log("Converting %s on a clean boot..." % path)
    # Nothing has been carved out of this heap but one page buffer and the
    # panel's own frame, so the 32KB block is here to be had. Taking it before
    # the extractor is imported matters: compiling that is by itself enough to
    # leave nothing that large behind.
    import gc
    gc.collect()
    free = gc.mem_free()
    try:
        R["_zip_window"] = bytearray(32768)
        log("Inflate window taken; %d bytes free before it." % free)
    except MemoryError:
        R["_zip_window"] = None
        lo, hi = 0, free
        while lo < hi:
            mid = (lo + hi + 1) // 2
            try:
                b = bytearray(mid)
                del b
                lo = mid
            except MemoryError:
                hi = mid - 1
        gc.collect()
        log("No 32KB block even on a clean boot: %d free, largest %d."
            % (free, lo))
    # epub_xtract directly, not through convertui. That wrapper exists for the
    # picker - it owns a UI buffer, handles the drive hand-back and paints
    # failure screens - and on a conversion boot it was returning None without
    # ever reaching convert_book and without raising, so nothing said why.
    # Everything it does for us here is three lines.
    made = None
    try:
        import epub_xtract

        # The first screen of a conversion boot must be a FULL refresh.
        #
        # A driver built moments ago starts with previous_buffer all white,
        # because it has no way to know what is on the glass - but the panel is
        # still holding the page that was there before the reset. A partial
        # refresh diffs against white, so it draws the new ink and leaves
        # everything else showing underneath. The reader's ordinary boot deals
        # with this in show_restored_page(); this path had nothing.
        #
        # One full refresh fixes the reference for every partial after it, so
        # only the first costs the extra ~2s.
        drawn = []

        def progress(stage, done, total, name=""):
            if stage == "chapter":
                try:
                    R["display_page"](R["render_message_into"](
                        R["_take_buf"](), "Converting",
                        [R["book_title"](path), "", "%d of %d" % (done, total)]),
                        not drawn)
                    drawn.append(1)
                except Exception:
                    pass

        # Absolute, always. The picker names a book the way it lists it -
        # "alice.epub" in the root, "books/alice.epub" underneath - while
        # epub_xtract.source_path() reads any name without a leading slash as
        # being inside /books. So a root-level EPUB was looked for at
        # /books/alice.epub, found missing, and the boot went quietly back to
        # the book that was already open. convertui normalises this; this path
        # bypasses convertui and did not.
        source = path if path.startswith("/") else "/" + path
        log("Converting from %s" % source)
        made = epub_xtract.convert_book(source, progress=progress,
                                        keep_display=True,
                                        window=R.get("_zip_window"))
    except Exception as e:
        log("Conversion raised: %s" % e)
    if made:
        made = made[1:] if made.startswith("/") else made
    clear()
    if made:
        try:
            R["Bookmarks"](R["MAX_BOOK_SLOTS"]).open(made, R["list_books"]())
        except Exception as e:
            log("Could not pre-select %s: %s" % (made, e))
        log("Converted; restarting into %s" % made)
    else:
        log("Conversion produced nothing; restarting.")
    restart(log)
