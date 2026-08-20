#!/usr/bin/env python3
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

    def try_lock(self):
        self.locked = True
        return True

    def unlock(self):
        self.locked = False

    def configure(self, **kw):
        pass

    def write(self, data):
        self.written += len(data)


def fake_digitalio():
    m = types.ModuleType("digitalio")
    m.Direction = types.SimpleNamespace(OUTPUT="out", INPUT="in")
    m.Pull = types.SimpleNamespace(UP="up", DOWN="down")
    m.DigitalInOut = Pin
    return m


def main():
    sys.path.insert(0, LIB)
    sys.modules["digitalio"] = fake_digitalio()

    # busy_ready: the level the pin must read for wait_busy() to return.
    # SSD1680 is busy-high; the UC8151-class panel is busy-low.
    cases = (("ssd1680e290", "SSD1680E290", 3, False),
             ("lcmen2r13efc1", "LCMEN2R13EFC1", 1, True))
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

    print("\n%s" % ("both drivers exercise clean" if not fails
                    else "%d DRIVER PROBLEM(S)" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
