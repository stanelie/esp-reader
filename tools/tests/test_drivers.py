#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise both panel drivers against stub hardware.

Written because a NameError sat in lcmen2r13efc1.display_partial through three
rounds of debugging: the reader booted, drew its first page, and died on the
first partial refresh. Every check up to then had been a parse or a grep, and
neither runs a line of driver code.

This calls the methods the reader calls - the full refresh, the partial one,
set_previous, power_down, release_bus - so an undefined name or a bad argument
fails here instead of on the bench.
"""
import os
import sys
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", ".."))
LIB = os.path.join(ROOT, "device", "lib")


class Pin:
    """A DigitalInOut stand-in. `ready` is what the busy pin should read."""
    def __init__(self, value=False):
        self.direction = None
        self.value = value

    def deinit(self):
        pass


class SPI:
    def __init__(self):
        self.written = 0
        self.locked = False
        self.log = []          # the bytes themselves, for the windowed check

    def try_lock(self):
        self.locked = True
        return True

    def unlock(self):
        self.locked = False

    def configure(self, **kw):
        pass

    def write(self, data):
        self.written += len(data)
        self.log.append(bytes(data))


def fake_digitalio():
    m = types.ModuleType("digitalio")
    m.Direction = types.SimpleNamespace(OUTPUT="out", INPUT="in")
    m.Pull = types.SimpleNamespace(UP="up", DOWN="down")
    m.DigitalInOut = Pin
    return m


def check_region():
    # The windowed refresh gathers the right pixels.
    #
    # Worth checking against arithmetic rather than against the panel: a wrong
    # stride here draws a plausible-looking band of the wrong part of the
    # screen, which is easy to mistake for a rendering bug somewhere else.
    sys.modules["digitalio"] = fake_digitalio()
    sys.path.insert(0, LIB)
    for mod in ("uc8151badger",):
        if mod in sys.modules:
            del sys.modules[mod]
    import uc8151badger
    spi = SPI()
    d = uc8151badger.UC8151Badger(spi, Pin(), Pin(), Pin(), Pin(True), rotation=3)
    frame = bytearray((i * 7 + 3) & 0xFF for i in range(d.buffer_size))
    spi.log = []
    if not d.display_region(frame, 0, 16, d.landscape_width, 16):
        print("  display_region refused a valid rectangle")
        return 1
    payload = None
    for i, chunk in enumerate(spi.log):
        if chunk == bytes([0x13]) and i + 1 < len(spi.log):
            payload = spi.log[i + 1]
            break
    y0, y1 = 16, 32
    px = d.height - d.landscape_width
    cols = (y1 - y0) >> 3
    bank = y0 >> 3
    expect = bytearray()
    for dx in range(d.landscape_width):
        start = (px + dx) * d.bytes_per_row + bank
        expect += frame[start:start + cols]
    ok = payload is not None and bytes(expect) == bytes(payload)
    cmds = [c[0] for c in spi.log if len(c) == 1]
    left = 0x92 in cmds                      # PTOU: partial mode exited
    print("  windowed refresh  %d of %d bytes (%.0f%%), gather %s, mode exited %s"
          % (len(expect), d.buffer_size, 100.0 * len(expect) / d.buffer_size,
             "correct" if ok else "WRONG", "yes" if left else "NO"))
    return 0 if (ok and left) else 1


def main():
    sys.path.insert(0, LIB)
    sys.modules["digitalio"] = fake_digitalio()

    # busy_ready: the level the pin must read for wait_busy() to return.
    # SSD1680 is busy-high; the UC8151-class panel is busy-low.
    # (module, class, rotation, what BUSY reads when the panel is ready).
    # The SSD1680 signals busy HIGH; the two UC8151-family panels signal it LOW.
    cases = (("ssd1680e290", "SSD1680E290", 3, False),
             ("lcmen2r13efc1", "LCMEN2R13EFC1", 1, True),
             ("uc8151badger", "UC8151Badger", 3, True))
    fails = 0
    for modname, clsname, rotation, busy_ready in cases:
        mod = __import__(modname)
        cls = getattr(mod, clsname)
        spi = SPI()
        try:
            epd = cls(spi, Pin(), Pin(), Pin(), Pin(busy_ready),
                      baudrate=4000000, keep_powered=True, rotation=rotation)
        except Exception as e:
            print("  %-14s CONSTRUCT FAILED %s: %s" % (clsname, type(e).__name__, e))
            fails += 1
            continue

        page = bytearray(b"\xFF" * epd.buffer_size)
        page[100] = 0x00                      # a little ink, so it is not trivial
        ok = []
        for label, call in (
                ("display_full", lambda: epd.display_full(page)),
                ("display_partial", lambda: epd.display_partial(page)),
                ("display_partial again", lambda: epd.display_partial(page)),
                ("set_previous", lambda: epd.set_previous(page)),
                ("power_down", lambda: epd.power_down()),
                ("release_bus", lambda: epd.release_bus())):
            try:
                call()
                ok.append(label)
            except Exception as e:
                print("  %-14s %s raised %s: %s"
                      % (clsname, label, type(e).__name__, e))
                fails += 1
        geom = ("%dx%d native, %dx%d landscape, stride %d, buffers %d/%d"
                % (epd.width, epd.height, epd.landscape_width,
                   epd.landscape_height, epd.landscape_stride,
                   epd.buffer_size, epd.landscape_buffer_size))
        print("  %-14s %s" % (clsname, geom))
        print("       %d/6 calls clean, %d bytes clocked out"
              % (len(ok), spi.written))
        if len(ok) == 6 and spi.written < epd.buffer_size:
            print("       WARNING: less than one frame was sent")
            fails += 1

    fails += check_region()

    print("\n%s" % ("%d drivers exercise clean" % len(cases) if not fails
                    else "%d DRIVER PROBLEM(S)" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
