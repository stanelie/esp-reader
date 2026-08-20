# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Streaming e-reader for the Heltec Vision Master E213 and E290.
#
# One file, both boards. Of ~1080 lines of code, eleven differ between them:
# six e-paper pins, the panel rotation, the driver class, and two battery
# calibration constants. Everything else - pagination, bookmarks, the picker,
# the jump-to screen, the gesture vocabulary, the sleep state machine - is
# identical, so it lives here once and the differences live in PANELS below.
#
# Which board this is gets detected at boot from board.board_id, never guessed:
# an unrecognised board halts rather than picking a default, because the two
# pin maps are permutations of the same six lines and the wrong one drives an
# output into the panel's BUSY output.

import time

t_boot_start = time.monotonic()

import os
import busio
import digitalio
import keypad
import analogio
import microcontroller
import board
import displayio
import alarm
import supervisor
import adafruit_framebuf
from fbrotate import rotate
from propfont import PropFont
from ssd1680e290 import SSD1680E290
from bookmarks import Bookmarks


# --- PINS COMMON TO BOTH BOARDS ---
# Raw microcontroller pins rather than board.*, so this runs on a stock
# ESP32-S3 build as well as on a board-specific one. (board.board_id, used for
# detection below, is the one exception: it is a plain string present on every
# build, unlike named pins such as board.LED0 which exist only where a board
# definition happens to declare them and raise at import everywhere else.)
#
# GPIO21 is the second button, NOT Vext - Vext is GPIO21 on the classic Heltec
# WiFi LoRa 32 boards, which is where that mix-up comes from. On both of these
# boards Vext is GPIO18. The E290 board definition drives it high (and marks it
# never_reset) to power the panel, so the reader must not try to claim it; the
# E213 panel does not need it at all.
PIN_KEY_NEXT = microcontroller.pin.GPIO21
PIN_KEY_BACK = microcontroller.pin.GPIO0     # BOOT button

# GPIO45 is also an ESP32-S3 strapping pin (VDD_SPI voltage select, sampled at
# reset). Driving it after boot is what the vendor's own board does, so this is
# safe, but never add a pull to it.
PIN_LED = microcontroller.pin.GPIO45

PIN_ADC_CTRL = microcontroller.pin.GPIO46    # high connects VBAT to the divider
PIN_BATTERY = microcontroller.pin.GPIO7


# Auto-reload is disabled in boot.py - see the reasoning there. Consequence:
# edits to this file need a reset to take effect.


def log_step(msg):
    print(f"[{time.monotonic() - t_boot_start:6.2f}s] {msg}")
    uart_log(msg)


# Diagnostics over the external FTDI adapter on GPIO44. The native USB console
# dies the moment the reader light-sleeps, so anything interesting about sleep
# or USB mode is invisible there. Costs nothing when no adapter is attached.
ENABLE_UART_LOG = True
LOG_UART_TX = microcontroller.pin.GPIO44
LOG_UART_RX = microcontroller.pin.GPIO43


def uart_log(msg):
    if not ENABLE_UART_LOG:
        return
    u = None
    try:
        u = busio.UART(LOG_UART_TX, LOG_UART_RX, baudrate=115200, timeout=0.05)
        u.write(("[%7.2f] %s\r\n" % (time.monotonic() - t_boot_start, msg)).encode())
        time.sleep(0.02)
    except Exception:
        pass
    finally:
        try:
            if u is not None:
                u.deinit()
        except Exception:
            pass


# --- WHICH BOARD IS THIS ---
#
# Everything that differs between the two readers. The panel pins are a
# permutation of the same six lines, which is exactly why this must not be
# guessed: the wrong table puts a driven output onto the line the panel is
# driving as BUSY.
#
#            SCK  MOSI  CS  DC  RST  BUSY   rotation  controller
#     E213    4    6     5   2    3    1       1      LCMEN2R13EFC1 (UC8151)
#     E290    2    1     3   4    5    6       3      SSD1680
#
# `rotation` is how the framebuffer is turned to get landscape out of a
# portrait panel; the two boards mount their panels opposite ways up.
#
# The battery constants are per *unit*, not per board: both use the same
# 390K/100K divider on GPIO7, but the offset belongs to the individual chip's
# converter. See the calibration note further down before trusting either set.
PANELS = {
    "e213": {
        "driver": "lcmen2r13efc1",
        "sck": microcontroller.pin.GPIO4,
        "mosi": microcontroller.pin.GPIO6,
        "cs": microcontroller.pin.GPIO5,
        "dc": microcontroller.pin.GPIO2,
        "rst": microcontroller.pin.GPIO3,
        "busy": microcontroller.pin.GPIO1,
        "rotation": 1,
        # Layout is expressed relative to whatever font is loaded, because the
        # font is selectable and its metrics move with it:
        #
        #   line pitch = the font's glyph box + leading
        #   first line = page_margin
        #
        # A .pf glyph box already includes the room a line needs above and
        # below its ink, which is why these are small numbers where the old
        # PCF-based derivation had to measure ink extents and cap heights.
        "leading": 1,
        "page_margin": 0,
        # These three are collinear to 0.3%, so interpolating between them and
        # fitting a line through them agree to well under a millivolt. Kept as
        # points anyway, so both boards use one mechanism.
        "cal_points": ((13370.3, 3.40), (14480.6, 3.70), (16336.1, 4.20)),
    },
    "e290": {
        "driver": "ssd1680e290",
        "sck": microcontroller.pin.GPIO2,
        "mosi": microcontroller.pin.GPIO1,
        "cs": microcontroller.pin.GPIO3,
        "dc": microcontroller.pin.GPIO4,
        "rst": microcontroller.pin.GPIO5,
        "busy": microcontroller.pin.GPIO6,
        "rotation": 3,
        # See the E213 entry for what these mean. One row tighter than the
        # E213 on both counts, which is what fits a 9th line of Literata 12 on
        # a panel only six rows taller, and what puts a capital on row 1 rather
        # than row 2. For Literata 12 they reproduce the hand-tuned 14 and -6.
        #
        # A slash on the first line of a page loses its top two rows at this
        # margin. That is the price of not leaving three blank rows above every
        # page, and / \ | are the only characters that pay it.
        # One row tighter than the E213, which is what fits nine lines of the
        # default font on a panel only six rows taller.
        "leading": 0,
        "page_margin": 0,
        # Measured on THIS unit, 2026-08-20. PPK2 into the battery input,
        # USB disconnected, three readings per voltage, each the median of 128
        # samples. The three medians at each voltage came back identical to the
        # count:
        #
        #     3.40 V -> 11577      3.70 V -> 14219      4.20 V -> 16066
        #
        # These are NOT collinear and no line fits them. The 3.40-3.70 slope is
        # 2.4x shallower than 3.70-4.20. A divider is a ratio, so a linear
        # converter reading 14219 at 3.70 V should read about 13066 at 3.40 V;
        # it read 11577, eleven percent low. ESP32-S3 non-linearity in this part
        # of its range is normally a few percent, so something else is going on.
        #
        # Two candidates, not distinguished:
        #   - the node really was below 3.40 V during that reading. The board
        #     draws more input current as its supply falls, and the divider
        #     measures the node, not the PPK2's setting - so lead and connector
        #     resistance would show up exactly here and nowhere else. If that is
        #     it, the artifact will not be there on a real cell, whose impedance
        #     is a fraction of an ohm, and this table will read low near empty.
        #   - the converter is genuinely bent down there.
        #
        # Interpolating between measured points instead of fitting a line makes
        # the question moot for now: it reproduces all three exactly and claims
        # no mechanism. Worth re-checking against a real cell once one is in.
        #
        # Unresolved: raw samples on this unit intermittently rail to 62297, up
        # to ~43% of a batch, cause unknown. get_battery_status() takes a median
        # and range-checks the result, which absorbs it - two of the nine
        # readings above were contaminated and their medians still matched their
        # clean siblings exactly.
        "cal_points": ((11577.0, 3.40), (14219.0, 3.70), (16066.0, 4.20)),
    },
}

# board.board_id -> (panel, does this firmware really light sleep).
#
# The second field is a property of the *firmware*, not the board, and it is
# knowable here only because the patched E290 build was deliberately given its
# own board name. That is what the rename bought: plain
# heltec_vision_master_e290 is upstream's stock build, which spins on WFI at
# ~43 mA instead of power-gating to ~1 mA, so the reader should drop through to
# deep sleep quickly rather than resting there.
BOARDS = {
    "heltec_vision_master_e213": ("e213", True),
    "heltec_vision_master_e290_lightsleep": ("e290", True),
    "heltec_vision_master_e290": ("e290", False),
}

# Set to a key of BOARDS to force the choice. Needed only on a generic
# ESP32-S3 build (yd_esp32_s3_n16r8 and friends), whose board_id names no
# panel at all.
BOARD_OVERRIDE = None


def _halt(msg):
    """Refuse to run, as loudly as the hardware still allows.

    Nothing can be put on the panel here - not knowing which panel it is is the
    whole problem - so the report goes to the two channels that work without
    one: the UART, and the LED, which is GPIO45 on both boards.
    """
    log_step(msg)
    try:
        sos = digitalio.DigitalInOut(PIN_LED)
        sos.direction = digitalio.Direction.OUTPUT
        for _ in range(20):
            sos.value = not sos.value
            time.sleep(0.15)
        sos.deinit()
    except Exception:
        pass
    raise RuntimeError(msg)


def _select_board():
    """(key, panel name, real light sleep) for the board we are running on."""
    key = BOARD_OVERRIDE
    source = "BOARD_OVERRIDE"
    if key is None:
        source = "board.board_id"
        try:
            key = board.board_id
        except AttributeError:
            key = None
    if key not in BOARDS:
        _halt("Unrecognised board %r from %s. Add it to BOARDS, or set "
              "BOARD_OVERRIDE to one of: %s"
              % (key, source, ", ".join(sorted(BOARDS))))
    panel, real_sleep = BOARDS[key]
    return key, panel, real_sleep


BOARD_KEY, PANEL_KEY, REAL_LIGHT_SLEEP = _select_board()
PANEL = PANELS[PANEL_KEY]
CANVAS_ROTATION = PANEL["rotation"]

if PANEL["driver"] == "ssd1680e290":
    from ssd1680e290 import SSD1680E290 as PanelDriver
else:
    from lcmen2r13efc1 import LCMEN2R13EFC1 as PanelDriver

log_step("Board %s: %s panel, real light sleep %s"
         % (BOARD_KEY, PANEL_KEY, REAL_LIGHT_SLEEP))

log_step("Imports loaded, starting streaming e-reader boot...")

# Release displayio's hold on the panel. Not optional on this board: unlike the
# E213 definition, the E290 one builds a displayio EPaperDisplay at boot, which
# owns the EPD SPI bus and all four control pins until this call hands them
# back. (It leaves Vext on GPIO18 asserted, which is what keeps the panel
# powered, so nothing here needs to drive it.)
displayio.release_displays()


def usb_attached():
    """True when a host has enumerated us over USB.

    Only meaningful while the USB peripheral is alive. After a light sleep it
    is powered down and this reads False even with the cable in - measured, and
    the reason USB mode has to be an explicit menu action rather than something
    detected. The battery divider cannot substitute: it reads the supply node,
    which a charger or a full cell holds at ~4 V regardless of the cable.

    Deep sleeping while plugged in drops into CircuitPython's *fake* deep
    sleep: the drive stays mounted but the VM halts and the console goes dead.
    """
    try:
        return bool(supervisor.runtime.usb_connected)
    except AttributeError:
        return False

# --- CONFIGURATION FLAGS ---
ENABLE_PERIODIC_FULL_REFRESH = False
DEFAULT_BOOK = "book.txt"

# Idle seconds before light sleep. Zero: sleep as soon as there is nothing to
# do. Waking is instant and the page cache, font and glyph cache all survive, so
# staying awake buys nothing and costs ~50 mA. At one page per 30 s even a
# single idle second averages ~1.7 mA - more than the ~1 mA sleep floor itself,
# so it would roughly double total consumption.
#
# Nothing needs a grace period here. The double-tap check runs synchronously
# inside turn_forward() (the ~0.5 s refresh already outlasts DOUBLE_TAP_MS), the
# neighbour prefetch finishes before the handler returns, and a press during a
# hold gesture never reaches this path because classify_hold() blocks until
# release.
LIGHT_SLEEP_TIMEOUT = 0.0

# Idle seconds before dropping from light sleep (~1 mA) to deep sleep (~16 uA).
# Worth it for a device that sits in a bag: 1 mA is ~24 mAh/day, which empties a
# 1000 mAh cell in about six weeks of doing nothing, while deep sleep is
# effectively free. The cost is a ~1.4 s reboot on the next wake.
#
# This has to be armed as a TimeAlarm inside the light sleep, not tested in the
# main loop. time.monotonic() does advance across a light sleep, but the loop
# never runs to check it: the reader is either asleep, or awake because a press
# just reset the idle timer.
#
# READ THIS BEFORE TRUSTING THE NUMBERS ABOVE. They hold on a firmware built
# with the real-light-sleep patch (CIRCUITPY_ESP_REAL_LIGHT_SLEEP, in
# firmware/patches/). Stock CircuitPython does not power-gate in light sleep -
# it spins in a WFI loop - so light sleep costs ~43 mA rather than ~1 mA,
# measured on the E213 with a PPK2. On a stock build the only sleep worth
# having is the deep one, so drop out to it quickly. REAL_LIGHT_SLEEP is not a
# knob here: it comes from the BOARDS table, because it is a property of the
# firmware and the firmware says which it is in its board name.
SLEEP_TIMEOUT = 300 if REAL_LIGHT_SLEEP else 20

# Seconds after boot during which sleeping is refused. Zero, because the
# usb_attached() guard below already does this job better: with a cable in at
# boot the reader never sleeps at all, so recovery from a bad edit is simply
# "plug in USB, press RESET". A fixed window on top of that just burned ~5 s of
# ~50 mA on every power-up for nothing.
#
# It is kept as a knob because it IS needed for bare test scripts that lack the
# usb_attached() guard: on this firmware a light sleep gates the USB peripheral
# permanently, so a script that sleeps seconds after boot cannot be reached at
# all, and only erase-and-reflash gets it back. Raise this if boot ever becomes
# fast enough to race USB enumeration (currently ~4.5 s of boot vs well under
# 1 s to enumerate, so there is ample margin).
BOOT_GRACE_SECONDS = 0

# Getting the drive (and the REPL) back needs no special mode: a light sleep
# gates the USB peripheral, but any wake from DEEP sleep is a full reboot and
# brings it straight back. So the workflow is simply: hold NEXT to sleep, plug
# in, press a button. Automatic detection is not possible on this board - the
# battery divider reads the supply node (~4 V with a cable in, whether that is
# a charger or a cell), usb_connected reads a powered-down peripheral, and
# there is no VBUS GPIO.

# --- LIBRARY & PICKER ---
BOOK_DIRS = ("/", "/books")   # where .txt books are looked for
# Files in the book folders that are not books. hyphen_en.txt was the pattern
# blob before it was renamed to .bin - it is listed so a drive restored from an
# older backup does not offer "hyphen_en" as something to read.
SKIP_FILES = ("boot_out.txt", "hyphen_en.txt")
MAX_BOOK_SLOTS = 20           # resume slots in NVM; oldest/departed books churn out
PICKER_TIMEOUT = 60           # leave the picker untouched this long and it backs out

# EPUB support: an .epub in / or /books is offered in the picker, converted to
# a .txt beside it on selection, and then read like any other book. The reader
# itself is unchanged - it still streams a plain text file by byte offset,
# which is what keeps resume, jump-to and hyphenation working.
#
# Conversion writes to the drive, so it needs the filesystem, so it only works
# on battery: with USB attached the host owns it. lib/epub_xtract.py says so
# on screen rather than producing a 0-byte book.
ENABLE_EPUB = True

GOTO_ROW = "\x00__goto__"      # sentinel picker rows, never filenames
FONTS_ROW = "\x00__fonts__"
GOTO_STEP = 5                 # percent per tap in the jump-to screen

SPI_BAUDRATE = 4000000  # a partial refresh clocks out 2 x 4736 bytes; ~0.02 s here, ~0.75 s at the busio default

# KEEP_DISPLAY_POWERED means something slightly different on this controller
# than it did on the E213. The SSD1680 raises and drops its own analog block as
# part of every update, so there are no rails to leave up; what this controls
# is whether the controller is put into its ~2 uA deep sleep between refreshes,
# which costs a re-init (a few ms, no refresh) on the next page turn. Left True
# to match the E213, and because the reader powers the panel down explicitly
# before it sleeps anyway.
# Justify wrapped lines by spreading the slack between words. Only lines that
# were actually wrapped are justified - the last line of a paragraph is short
# because the paragraph ended, and stretching it across the page is the classic
# way to make justification look broken.
JUSTIFY_TEXT = True

# Give up on a line whose gaps would have to grow past this multiple of a normal
# space. Rivers of white are worse than a ragged edge, and a line ending just
# before a very long word can need absurd stretching.
MAX_SPACE_STRETCH = 2.0

# Page turns between writes of the reading position to NVM.
#
# Not premature: on this port every microcontroller.nvm write reads the whole
# 8 KB blob, calls nvs_erase_all, and rewrites all 8 KB. The NVS partition is
# 20 KB - five 4 KB pages - so an 8 KB blob fills about two of them and roughly
# every second save forces garbage collection, i.e. real flash erases. Saving
# per page turn is ~110,000 of those a year at 300 turns/day; at 10 it is
# ~11,000. Flash is rated around 100,000 erase cycles.
#
# The exposure is losing at most this many pages if power is cut mid-session.
# Light sleep does not need a save - the VM survives it - and deep sleep, book
# switches and jumps all force one, so the window is bounded by the five idle
# minutes before deep sleep takes over.
SAVE_EVERY_N_TURNS = 10

KEEP_DISPLAY_POWERED = True
SHOW_SLEEP_SCREEN = True  # False = leave the page on the panel while asleep (fastest possible wake)
FAST_WAKE = True  # wake from deep sleep with a partial refresh instead of a full one
# A reading costs ~16 ms, and 95% of that is the settle - the conversions
# themselves are ~55 us each. So the way to spend less time and energy on the
# gauge is to take fewer readings, not cheaper ones.
#
# At 60 s this was at most 1440 readings/day: 23 s of extra awake time, 0.32
# mAh, against a light-sleep floor of 26 mAh/day. At 300 s it is 0.06 mAh, or
# 0.01% of a 1000 mAh cell - far below anything else the reader does, and a
# battery percentage does not change meaningfully in five minutes anyway.
BATTERY_CACHE_SECONDS = 300

# Samples per battery reading, and odd so the median is a single one of them.
# Cheap: the cost of a reading is the 15 ms settle, not the conversions.
BATTERY_SAMPLES = 15

# A reading outside this range is not a flat or a full cell, it is a broken
# measurement, and the gauge is better off repeating its last answer than
# reporting it. 4.35 V is above any Li-ion charge termination; 2.50 V is below
# any protection-circuit cutoff, so neither bound can be reached by a real cell
# on a working divider.
BATTERY_MIN_V = 2.50
BATTERY_MAX_V = 4.35

# --- HARDWARE DISPLAY SETUP ---
# Built through a function because light sleep tears every non-RTC peripheral
# down: the bus and pins must be released before sleeping and reconstructed
# after. Verified on hardware - six rebuilds across six light sleeps all drove
# the panel with unchanged timings (POWER_ON 135-138 ms, POWER_OFF 61-63 ms).
spi = cs = dc = rst = busy = epd = None


def build_display():
    global spi, cs, dc, rst, busy, epd
    spi = busio.SPI(clock=PANEL["sck"], MOSI=PANEL["mosi"])
    cs = digitalio.DigitalInOut(PANEL["cs"])
    dc = digitalio.DigitalInOut(PANEL["dc"])
    rst = digitalio.DigitalInOut(PANEL["rst"])
    busy = digitalio.DigitalInOut(PANEL["busy"])
    epd = PanelDriver(
        spi, cs, dc, rst, busy,
        baudrate=SPI_BAUDRATE,
        keep_powered=KEEP_DISPLAY_POWERED,
        rotation=CANVAS_ROTATION,
    )


def teardown_display():
    """Drop the panel rails and hand back every pin and the bus."""
    global spi, cs, dc, rst, busy, epd
    try:
        if epd is not None:
            epd.power_down()
            epd.release_bus()
    except Exception as e:
        print(f"display teardown: {e}")
    for pin in (cs, dc, rst, busy):
        try:
            if pin is not None:
                pin.deinit()
        except Exception:
            pass
    try:
        if spi is not None:
            spi.deinit()
    except Exception:
        pass
    spi = cs = dc = rst = busy = epd = None


build_display()

# --- PAGE GEOMETRY ---
# Derived from the panel rather than declared, because declaring it means two
# copies of one fact that have to be kept in agreement by hand - and they are
# not even in the same units: the driver reports the panel's native portrait
# size, while everything that draws works in the rotated landscape one.
#
# 250x122 on the E213, 296x128 on the E290. The driver works both out from its
# native size and the rotation it was given, and it is the one that has to
# agree with the panel, so it is the one that decides.
WIDTH, HEIGHT = epd.landscape_width, epd.landscape_height

PADDING_X = 2
MAX_LINE_WIDTH_PX = WIDTH - (PADDING_X * 2)

# PAGE_TOP and LINE_HEIGHT are not here any more - they depend on the font,
# which is selectable, so load_reader_font() derives them further down.
log_step("Panel %dx%d landscape (native %dx%d, rotation %d)"
         % (WIDTH, HEIGHT, epd.width, epd.height, CANVAS_ROTATION))

# --- FONT SETUP ---

# Fonts are whatever .pf files are in /fonts, so adding one is a file copy.
#
# .pf is the Badger reader's compact format: one small blob holding a fixed
# glyph box per character, which draw() blits into the framebuffer a byte at a
# time. The PCF fonts it replaced were 4-6x larger, covered 110 characters
# rather than the whole of Latin-1, needed adafruit_bitmap_font, and were drawn
# a pixel at a time through a per-glyph cache of Python tuples - that cache
# being the thing that would not have fitted on an RP2040.
#
# tools/build_pf.py makes one from any TTF.
FONT_DIR = "/fonts"
FONT_DEFAULT = "literata"           # picked when nothing is stored in NVM


def list_fonts():
    """[(path, name)] for the fonts on the drive, PCF preferred, sorted."""
    seen = {}
    try:
        entries = os.listdir(FONT_DIR)
    except OSError:
        return []
    for entry in entries:
        if entry.startswith("."):
            continue
        if not entry.lower().endswith(".pf"):
            continue
        seen[entry.rsplit(".", 1)[0]] = FONT_DIR + "/" + entry
    return sorted((path, stem) for stem, path in seen.items())


def font_label(stem):
    """A font's file stem as something worth showing: literata-large -> Literata Large."""
    out = []
    for part in stem.split("-"):
        if not part:
            continue
        out.append(part if part[0].isdigit() else part[0].upper() + part[1:])
    return " ".join(out)

# Characters our books use that a .pf font has no room for. It covers
# U+0020-U+00FF, and everything above that is mapped to the nearest thing
# inside it.
#
# Counted over the books on this drive: 10,563 curly double quotes, 5,114
# apostrophes, 553 em dashes, 242 ellipses - 1.66% of all text, so this is not
# a rare path and leaving them to render as "?" was never an option.
#
# Applied only when measuring and drawing, never to the text pagination works
# on. That matters: page offsets are byte positions in the file, and "..." is
# three characters where the ellipsis was one. Keeping the substitution inside
# the two font wrappers means the layout never sees a string whose length or
# byte offsets have moved.
FONT_SUBS = (
    ("\u201c", '"'), ("\u201d", '"'),      # curly double quotes
    ("\u2018", "'"), ("\u2019", "'"),      # curly single quotes
    ("\u2014", "-"), ("\u2013", "-"),      # em and en dash
    ("\u2026", "..."),                     # ellipsis
    ("\u2009", " "), ("\u202f", " "),      # thin and narrow no-break spaces
)


def to_font(text):
    """Fold a string into the range the font actually has glyphs for."""
    for src, dst in FONT_SUBS:
        if src in text:
            text = text.replace(src, dst)
    return text


# Set by load_reader_font(), which runs once the measuring helpers below exist.
reader_font = None
font_path = None
LINE_HEIGHT = 12          # the built-in 8x12 pitch, used only if no font loads
PAGE_TOP = 0
MAX_LINES_PER_PAGE = (HEIGHT - 2) // LINE_HEIGHT
PICKER_ROWS = MAX_LINES_PER_PAGE - 1
SPACE_WIDTH = 6

# --- BATTERY & USB SETUP ---
adc_ctrl = None
vbus_sense = None   # optional; the E290 has no dedicated VBUS GPIO either

try:
    adc_ctrl = digitalio.DigitalInOut(PIN_ADC_CTRL)
    adc_ctrl.direction = digitalio.Direction.OUTPUT
    adc_ctrl.value = False          # start disabled (saves power)
    log_step("Battery ADC_CTRL (GPIO46) initialized.")
except Exception as e:
    print(f"Battery ADC_CTRL setup failed: {e}")

# Battery ADC calibration.
#
# Each panel profile carries cal_points: raw ADC counts measured at known supply
# voltages, and raw_to_volts() interpolates between them. That is deliberately
# not a fitted line. It started as one - and a single multiplier before that,
# which computed 4.08 V at a true 4.20 V and so reported 88% for a full cell -
# but the E290's measurements are not collinear and no line fits all three.
#
# Two properties worth keeping in mind:
#
#   - The response does not pass through the origin. Extrapolating either
#     board's points to 0 V gives a positive raw count (~764 on the E213,
#     ~551 on the E290), which is the converter's offset. Any scheme with one
#     constant is exact at one voltage and wrong everywhere else.
#
#   - The measurement is only meaningful with USB UNPLUGGED. With a cable in,
#     that node is fed by USB and holds near 4 V whatever the cell is doing, so
#     a reading taken then is measuring the charger.
#
# To recalibrate a unit: tools/battery_calibrate.py, then tools/battery_fit.py,
# which prints a cal_points line ready to paste. The raw counts also go out
# over the UART on every read.
CAL_POINTS = PANEL["cal_points"]     # ((raw, volts), ...), ascending by raw


def raw_to_volts(raw):
    """Volts for a raw ADC count, interpolating between measured points.

    A straight line was the obvious thing and it does not survive the E290's
    measurements - see the profile above. Interpolation costs a couple of
    comparisons per reading, once every BATTERY_CACHE_SECONDS, and has the
    property a fitted line lacks here: it is exactly right at every voltage
    that was actually measured.

    Outside the measured range it extends the nearest segment, so a reading
    just past either end degrades smoothly rather than stepping.
    """
    pts = CAL_POINTS
    if raw <= pts[0][0]:
        (r0, v0), (r1, v1) = pts[0], pts[1]
    elif raw >= pts[-1][0]:
        (r0, v0), (r1, v1) = pts[-2], pts[-1]
    else:
        i = 0
        while raw > pts[i + 1][0]:
            i += 1
        (r0, v0), (r1, v1) = pts[i], pts[i + 1]
    return v0 + (raw - r0) * (v1 - v0) / (r1 - r0)

_batt_pct = -1
_batt_charging = False
_batt_read_time = None


def get_battery_status(force=False):
    """Battery percentage / USB state, cached for BATTERY_CACHE_SECONDS."""
    global adc_ctrl, vbus_sense, _batt_pct, _batt_charging, _batt_read_time

    if not force and _batt_read_time is not None:
        if time.monotonic() - _batt_read_time < BATTERY_CACHE_SECONDS:
            return _batt_pct, _batt_charging

    if not adc_ctrl:
        _batt_pct, _batt_charging, _batt_read_time = -1, False, time.monotonic()
        return _batt_pct, _batt_charging

    # usb_attached() is the only usable signal here. The divider reads the
    # supply node, which a charger or a cell holds at 4.2 V at most, so no
    # voltage threshold can separate "USB present" from "battery full" - the
    # `voltage > 4.35` test that used to live below could never fire.
    is_charging = usb_attached()
    if vbus_sense is not None:
        is_charging = is_charging or vbus_sense.value

    try:
        adc_ctrl.value = True
        time.sleep(0.015)

        adc = analogio.AnalogIn(PIN_BATTERY)
        vals = []
        for _ in range(BATTERY_SAMPLES):
            vals.append(adc.value)
        adc.deinit()

        adc_ctrl.value = False

        # Median, not mean, and this is not fussiness. Measured on the E290:
        # individual samples intermittently rail to 62297 of 65535 - about
        # 3.1 V at a pin that should be seeing 0.9 V through the divider - and
        # in three readings out of five they were 42-43% of the batch. The mean
        # of eight such samples lands near 34900, which converts to 9.2 V and
        # clamps the gauge to 100%: a full battery reported on a flat cell,
        # intermittently, with nothing on screen to suggest anything is wrong.
        #
        # A median is unmoved by anything short of half the batch, and those
        # bursts stayed under it. The guard below catches the case where they
        # do not.
        vals.sort()
        reading = float(vals[len(vals) // 2])
        voltage = raw_to_volts(reading)

        if not (BATTERY_MIN_V <= voltage <= BATTERY_MAX_V):
            railed = sum(1 for v in vals if v > reading * 1.5)
            uart_log("BATT implausible v=%.3f raw=%.1f railed=%d/%d; keeping %d%%"
                     % (voltage, reading, railed, len(vals), _batt_pct))
            _batt_charging, _batt_read_time = is_charging, time.monotonic()
            return _batt_pct, _batt_charging

        percent = (voltage - 3.20) / (4.20 - 3.20) * 100.0
        percent = int(max(0, min(100, round(percent))))

        # Reported over the external UART so the gauge can be checked against a
        # known source voltage without USB - which is the only way to check it,
        # since a cable changes what the divider is measuring.
        uart_log("BATT raw=%.1f v=%.3f pct=%d usb=%s"
                 % (reading, voltage, percent, is_charging))

        _batt_pct, _batt_charging, _batt_read_time = percent, is_charging, time.monotonic()
        return _batt_pct, _batt_charging

    except Exception as e:
        if adc_ctrl:
            adc_ctrl.value = False
        print(f"Battery read error: {e}")
        _batt_pct, _batt_charging, _batt_read_time = -1, is_charging, time.monotonic()
        return _batt_pct, _batt_charging

# --- FONT MEASUREMENT & DRAWING ---
# Thin wrappers over PropFont so the substitution above happens in exactly one
# place, and so the rest of the reader keeps calling the names it always has.


def get_string_width(text):
    if reader_font is None:
        return len(text) * 6
    return reader_font.text_width(to_font(text))


def draw_text(canvas, text, x, y, color=0):
    """Draw at (x, y) = the top-left of the glyph box."""
    if reader_font is None:
        canvas.text(text, x, y, color)
        return
    reader_font.draw(canvas, to_font(text), x, y, color)


def draw_text_justified(canvas, text, x, y, target_px, color=0):
    """Draw `text` spread to exactly target_px by widening its spaces.

    PropFont does the spreading: it hands the leftover pixels out one per space
    from the left, so the line ends exactly on the margin rather than a
    rounding error short of it.
    """
    if reader_font is None:
        canvas.text(text, x, y, color)
        return
    reader_font.draw_justified(canvas, to_font(text), x, y, color, target_px)


# The font choice lives in NVM, past the bookmark table. Bookmarks occupy
# HEADER_SIZE + MAX_BOOK_SLOTS * SLOT_SIZE = 210 bytes of the 8 KB available
# (4 KB on an RP2040), so there is no chance of collision, and a magic byte
# keeps an erased NVM - which reads back as 0xFF - from looking like a stored
# choice.
_FONT_NVM_OFFSET = 512
# Bumped when the font list changed from PCF to .pf. The stored value is an
# INDEX into a sorted list, and that list changed both membership and order, so
# an index saved under the old fonts now selects a different one - a reader who
# had picked Literata would silently come back reading Literata Large. Changing
# the magic makes every stored choice fall back to FONT_DEFAULT once.
_FONT_NVM_MAGIC = 0x5B


def load_font_choice(count):
    """Stored font index, or None if nothing valid is stored.

    None rather than 0, so "never chosen" can fall back to FONT_DEFAULT
    instead of silently meaning "chose the first one".
    """
    try:
        nvm = microcontroller.nvm
        if nvm is None or len(nvm) < _FONT_NVM_OFFSET + 2:
            return None
        if nvm[_FONT_NVM_OFFSET] != _FONT_NVM_MAGIC:
            return None
        idx = nvm[_FONT_NVM_OFFSET + 1]
        return idx if idx < count else None
    except Exception:
        return None


def save_font_choice(idx):
    """Remember the font. One NVM write, and only when it changed - see the
    note above SAVE_EVERY_N_TURNS for why that matters."""
    try:
        nvm = microcontroller.nvm
        if nvm is None or len(nvm) < _FONT_NVM_OFFSET + 2:
            return
        if (nvm[_FONT_NVM_OFFSET] == _FONT_NVM_MAGIC
                and nvm[_FONT_NVM_OFFSET + 1] == idx):
            return
        nvm[_FONT_NVM_OFFSET:_FONT_NVM_OFFSET + 2] = bytes([_FONT_NVM_MAGIC, idx])
    except Exception as e:
        log_step("Could not store the font choice: %s" % e)


def load_reader_font(path):
    """Load a font and derive the page layout from it. True on success.

    The panel profile says only how tight to be; the numbers come from the
    font, which is what makes the font selectable - a 13px serif and an 18px
    one cannot share a hand-tuned line height.

    On failure the previous font is left in place, so a bad file in /fonts
    cannot leave the reader with nothing to draw with.
    """
    global reader_font, font_path, LINE_HEIGHT, PAGE_TOP
    global MAX_LINES_PER_PAGE, PICKER_ROWS, SPACE_WIDTH

    t0 = time.monotonic()
    try:
        font = PropFont(path)
    except Exception as e:
        log_step("Font %s did not load (%s)" % (path, e))
        return False

    reader_font = font
    font_path = path
    # A .pf glyph box already contains the room a line needs above and below
    # the ink, so the line pitch is the box plus whatever the panel wants
    # between lines.
    LINE_HEIGHT = font.box_h + PANEL["leading"]
    PAGE_TOP = PANEL["page_margin"]
    MAX_LINES_PER_PAGE = (HEIGHT - 2) // LINE_HEIGHT
    PICKER_ROWS = MAX_LINES_PER_PAGE - 1      # one row goes to the header
    SPACE_WIDTH = font.text_width(" ")

    log_step("Font %s: box %d, baseline %d, line %d, %d lines/page (%.2fs)"
             % (path.rsplit("/", 1)[-1], font.box_h, font.baseline, LINE_HEIGHT,
                MAX_LINES_PER_PAGE, time.monotonic() - t0))
    return True


FONTS = list_fonts()

# A stored choice wins; otherwise FONT_DEFAULT; otherwise whatever is first.
_font_index = load_font_choice(len(FONTS))
if _font_index is None:
    _font_index = 0
    for _i in range(len(FONTS)):
        if FONTS[_i][1] == FONT_DEFAULT:
            _font_index = _i
            break
if not FONTS:
    log_step("No fonts in %s; falling back to the built-in 8x12." % FONT_DIR)
elif not load_reader_font(FONTS[_font_index][0]):
    for _i in range(len(FONTS)):          # any font beats none
        if load_reader_font(FONTS[_i][0]):
            _font_index = _i
            break


# --- BOOK LIBRARY ---
def list_epubs():
    """Every .epub that has no converted .txt yet, as paths, sorted.

    One that has already been converted is not offered again - its .txt is in
    the list instead, and converting a second time would only overwrite it and
    lose the reading position stored against that name.
    """
    if not ENABLE_EPUB:
        return []
    found = []
    for folder in BOOK_DIRS:
        try:
            entries = os.listdir(folder)
        except OSError:
            continue
        for entry in entries:
            if entry.startswith(".") or not entry.lower().endswith(".epub"):
                continue
            path = entry if folder == "/" else f"{folder.strip('/')}/{entry}"
            try:
                if not os.stat(path)[0] & 0x8000:
                    continue
            except OSError:
                continue
            if file_size_of(epub_txt_path(path)):
                continue          # already converted
            found.append(path)
    found.sort()
    return found


def epub_txt_path(epub_path):
    """Where a converted .epub lands: /books/<name>.txt, per epub_xtract."""
    name = epub_path.rsplit("/", 1)[-1]
    base = name[:-5] if name.lower().endswith(".epub") else name
    return "books/%s.txt" % base


def list_books():
    """Every .txt in the root and /books, as paths, sorted."""
    found = []
    for folder in BOOK_DIRS:
        try:
            entries = os.listdir(folder)
        except OSError:
            continue
        for entry in entries:
            if entry.startswith(".") or not entry.lower().endswith(".txt"):
                continue
            if entry in SKIP_FILES:
                continue
            path = entry if folder == "/" else f"{folder.strip('/')}/{entry}"
            try:
                if not os.stat(path)[0] & 0x8000:
                    continue
            except OSError:
                continue
            found.append(path)
    found.sort()
    return found


def book_title(path):
    """What the picker shows: no folder, no extension."""
    name = path.rsplit("/", 1)[-1]
    lower = name.lower()
    if lower.endswith(".txt"):
        return name[:-4]
    if lower.endswith(".epub"):
        return name[:-5]
    return name


def fit_text(text, max_px):
    """Trim text to max_px, ending in an ellipsis when it had to be cut."""
    if get_string_width(text) <= max_px:
        return text
    ell = "…"
    budget = max_px - get_string_width(ell)
    out = ""
    width = 0
    for ch in text:
        w = get_string_width(ch)
        if width + w > budget:
            break
        out += ch
        width += w
    return out + ell

# --- STATUS LED ---
# PIN_LED is up in the pin block. The E290 build does export board.LED0, but
# this file deliberately never touches board.*: the same code then runs on a
# stock ESP32-S3 build, where the name does not exist and referencing it raises
# at startup, leaving set_led() a silent no-op and taking the only cue away from
# the hold-to-picker and hold-to-sleep gestures.
led = None
try:
    led = digitalio.DigitalInOut(PIN_LED)
    led.direction = digitalio.Direction.OUTPUT
    led.value = False
    log_step("Status LED initialized (GPIO45).")
except Exception as e:
    print(f"LED pin setup failed: {e}")

def set_led(state: bool):
    if led is not None:
        led.value = state

# --- BUTTONS ---
keys = None


def build_keys():
    """keypad holds GPIO21/GPIO0, which the wake PinAlarms need, so it is
    released before sleeping and rebuilt afterwards."""
    global keys
    keys = keypad.Keys(
        (PIN_KEY_NEXT, PIN_KEY_BACK),
        value_when_pressed=False,
        pull=True,
    )


build_keys()

# Everything runs off one button. keypad scans in the background and queues
# events with timestamps, which is what makes a tap during a 0.5 s display
# refresh survive - the old polling loop simply missed presses while it was
# blocked in wait_busy().
KEY_NEXT = 0
KEY_BACK = 1  # BOOT button, kept as an optional shortcut for "previous page"

LONG_PRESS_MS = 700    # hold this long: open the picker, or select in it
SLEEP_HOLD_MS = 2500   # keep holding: sleep instead of opening the picker
DOUBLE_TAP_MS = 400    # a second tap this soon after the first means "back"

_TICKS_PERIOD = 1 << 29
_TICKS_HALF = _TICKS_PERIOD // 2
_stashed_press = None


def ticks_ms_diff(later, earlier):
    """Wrap-safe difference between two supervisor.ticks_ms() values, in ms.

    ticks_ms() wraps at 2**29. A plain subtraction goes hugely negative across
    that boundary, which would make a long hold look instant or a double tap
    look stale, roughly every six days of uptime.
    """
    return ((later - earlier + _TICKS_HALF) % _TICKS_PERIOD) - _TICKS_HALF


def next_press():
    """The next queued press event, or None. Releases are discarded."""
    global _stashed_press
    if _stashed_press is not None:
        event, _stashed_press = _stashed_press, None
        return event
    while True:
        event = keys.events.get()
        if event is None:
            return None
        if event.pressed:
            return event


def drain_events():
    """Forget anything queued, so a stale tap cannot act later."""
    global _stashed_press
    _stashed_press = None
    while keys.events.get() is not None:
        pass


def classify_hold(key_number, press_ticks):
    """Wait out a press. Returns (kind, release_ticks).

    kind is "tap", "picker" or "sleep": one button, escalating hold, with the
    LED as the cue. It lights once holding longer would open the picker, and
    goes out again once holding longer still would sleep instead.
    """
    # stage drives only the LED: 0 = nothing yet, 1 = picker armed (LED on),
    # 2 = sleep armed (LED off again). The returned kind is computed from the
    # measured press-to-release time, so it does not depend on poll timing.
    stage = 0
    while True:
        event = keys.events.get()
        if event is not None:
            if event.key_number == key_number and not event.pressed:
                if stage == 1:
                    set_led(False)
                held = ticks_ms_diff(event.timestamp, press_ticks)
                if held >= SLEEP_HOLD_MS:
                    return "sleep", event.timestamp
                if held >= LONG_PRESS_MS:
                    return "picker", event.timestamp
                return "tap", event.timestamp
            continue  # ignore the other key while a gesture is in progress
        held = ticks_ms_diff(supervisor.ticks_ms(), press_ticks)
        if stage == 0 and held >= LONG_PRESS_MS:
            stage = 1
            set_led(True)
        elif stage == 1 and held >= SLEEP_HOLD_MS:
            stage = 2
            set_led(False)
            # Decided: holding longer cannot change the outcome, so return now
            # rather than on release. The caller gets to put the sleep screen up
            # while the finger is still down, which is the whole point - a full
            # refresh takes ~3.8 s and waiting for release before starting it
            # made the gesture feel unresponsive.
            #
            # The cost is that the button may still be held when the caller
            # arms its wake alarms, so both sleep paths call
            # wait_buttons_released() first.
            return "sleep", supervisor.ticks_ms()
        time.sleep(0.005)


def was_double_tap(release_ticks):
    """Whether the tap that just acted was really the first half of a double.

    Call this *after* doing the tap's work. That display refresh takes ~0.5 s,
    which is longer than DOUBLE_TAP_MS, so the second press is already sitting
    in the queue and a forward page turn pays nothing at all for the gesture.
    A press that arrives too late to pair is stashed, and acts on its own on the
    next pass rather than being swallowed.
    """
    global _stashed_press
    event = next_press()
    if event is None:
        return False
    if event.key_number == KEY_NEXT:
        if ticks_ms_diff(event.timestamp, release_ticks) <= DOUBLE_TAP_MS:
            return True
    _stashed_press = event
    return False

# --- WHICH BOOK, AND WHERE IN IT ---
_turns_since_save = 0


def save_position(force=False):
    """Store the reading position, at most every SAVE_EVERY_N_TURNS turns.

    force=True for the moments where losing the position would actually cost
    something: deep sleep, switching books, jumping. Ordinary page turns ride
    the counter. Bookmarks.save() is itself a no-op when the offset has not
    changed, so a forced save straight after a throttled one is free.
    """
    global _turns_since_save
    _turns_since_save += 1
    if force or _turns_since_save >= SAVE_EVERY_N_TURNS:
        _turns_since_save = 0
        bookmarks.save(current_file, page_offsets[current_page_idx])


bookmarks = Bookmarks(MAX_BOOK_SLOTS)
if bookmarks.migrate_legacy(DEFAULT_BOOK):
    log_step(f"Carried the old single-book position into a slot for {DEFAULT_BOOK}.")

library = list_books()
gone = bookmarks.prune(library)
if gone:
    log_step(f"Cleared {gone} resume slot(s) for books no longer present.")

current_file = bookmarks.match(library)
if current_file is None:
    if DEFAULT_BOOK in library:
        current_file = DEFAULT_BOOK
    elif library:
        current_file = library[0]
    else:
        current_file = DEFAULT_BOOK


def file_size_of(path):
    try:
        return os.stat(path)[6]
    except Exception:
        return 0


FILE_SIZE = file_size_of(current_file)

if FILE_SIZE:
    initial_offset = bookmarks.open(current_file, library)
    if initial_offset >= FILE_SIZE:
        initial_offset = 0
else:
    initial_offset = 0

page_offsets = [initial_offset]

# --- HYPHENATION ---
# Liang's algorithm, English patterns, in lib/hyphenator.py. Optional: if the
# module or its pattern blob is missing the reader wraps on whole words exactly
# as before, so a stripped-down drive still works.
#
# Measured on the books here: unhyphenated, the ragged right edge averages 24 px
# of the 292 px line, 8.3% of the width, with one line in ten ending 50 px short.
ENABLE_HYPHENATION = True

hyphenate_ok = False
if ENABLE_HYPHENATION:
    try:
        import hyphenator
        hyphenator._load()   # fail here, on the file, not mid-page
        hyphenate_ok = True
        log_step("Hyphenation ready (%d bytes of patterns)." % len(hyphenator._BLOB))
    except Exception as e:
        log_step("Hyphenation unavailable (%s); wrapping on whole words." % e)


def _hyphenate_word(word, space_left):
    """(head, rest) for a word to be split across two lines, or (None, None).

    head carries the trailing hyphen. The hyphenator deals only in letters, so
    surrounding punctuation is peeled off and put back - a closing quote should
    not be what stops "responsibility," from breaking.

    Whatever the stripping does, head + rest reconstructs the word plus exactly
    one hyphen. That is the property the pagination depends on, and it holds
    even where the peeling is too eager, because the peeled pieces are simply
    carried through rather than re-derived.
    """
    lead = 0
    while lead < len(word) and not word[lead].isalpha():
        lead += 1
    tail = len(word)
    while tail > lead and not word[tail - 1].isalpha():
        tail -= 1
    core = word[lead:tail]
    if len(core) < 5:
        return None, None

    prefix = word[:lead]
    budget = space_left - (get_string_width(prefix) if prefix else 0)
    if budget <= 0:
        return None, None

    head, rest = hyphenator.hyphenate_split(core, budget, get_string_width)
    if head is None:
        return None, None
    return prefix + head, rest + word[tail:]


def read_page_stream(filename, start_offset):
    """(lines, wrapped, next_offset) for one page starting at a byte offset.

    `wrapped[i]` is True when line i ended because it ran out of room, and False
    when it ended because the paragraph did. Only the first kind may be
    justified: stretching a paragraph's last line across the page is what makes
    justification look broken.

    The distinction has to be made here, while wrapping, because it cannot be
    recovered afterwards. A blank line following would suggest it, and that
    works for a file with one line per paragraph - but a hard-wrapped file, of
    the sort Project Gutenberg ships, has a non-blank line after almost every
    line, and the short remainder of each source line would be stretched to
    full width.
    """
    lines = []
    wrapped = []
    next_offset = start_offset

    try:
        with open(filename, "r", encoding="utf-8") as f:
            f.seek(start_offset)

            while len(lines) < MAX_LINES_PER_PAGE:
                line_start_pos = f.tell()
                raw_line = f.readline()
                if not raw_line:
                    next_offset = line_start_pos
                    break

                stripped = raw_line.rstrip("\r\n")
                if not stripped:
                    lines.append("")
                    wrapped.append(False)
                    next_offset = f.tell()
                    continue

                words = stripped.split(" ")
                current_line = ""
                current_width = 0
                word_start_in_raw = 0
                page_full = False

                for word in words:
                    w_pos = raw_line.find(word, word_start_in_raw)
                    if w_pos == -1:
                        w_pos = word_start_in_raw

                    word_width = get_string_width(word)
                    if current_line:
                        test_line = current_line + " " + word
                        test_width = current_width + SPACE_WIDTH + word_width
                    else:
                        test_line = word
                        test_width = word_width

                    if test_width <= MAX_LINE_WIDTH_PX:
                        current_line = test_line
                        current_width = test_width
                    else:
                        # The word does not fit. Before pushing it whole to the
                        # next line, see whether a hyphenated head of it fits
                        # here - but only when the remainder is guaranteed a
                        # line on THIS page.
                        #
                        # That guard is the whole reason this is safe. A page's
                        # start is a byte offset into the file, and it is
                        # computed as the offset of the first word this page did
                        # not consume. Hyphenating the word that ends a page
                        # would make that offset land mid-word, and every path
                        # that re-derives a page from its offset - resume from
                        # NVM, back-navigation, jump-to - would have to agree
                        # about a split it cannot see. Refusing to hyphenate the
                        # last line keeps every page boundary on a whole word,
                        # which is the invariant the E213's pagination has
                        # always had, and costs one line's worth of raggedness
                        # per page.
                        head = None
                        rest = None
                        if hyphenate_ok and len(lines) + 1 < MAX_LINES_PER_PAGE:
                            if current_line:
                                space_left = (MAX_LINE_WIDTH_PX - current_width
                                              - SPACE_WIDTH)
                            else:
                                space_left = MAX_LINE_WIDTH_PX
                            if space_left > 0:
                                head, rest = _hyphenate_word(word, space_left)

                        if head is not None:
                            lines.append(current_line + " " + head
                                         if current_line else head)
                            wrapped.append(True)
                            current_line = rest
                            current_width = get_string_width(rest)
                        else:
                            lines.append(current_line)
                            wrapped.append(True)
                            current_line = word
                            current_width = word_width

                            if len(lines) == MAX_LINES_PER_PAGE:
                                next_offset = line_start_pos + len(raw_line[:w_pos].encode("utf-8"))
                                page_full = True
                                break

                    word_start_in_raw = w_pos + len(word)

                if page_full:
                    break

                if current_line:
                    lines.append(current_line)
                    wrapped.append(False)     # the paragraph ended here
                    current_line = ""
                    current_width = 0

                next_offset = f.tell()

    except Exception as e:
        return [f"Error reading {filename}", str(e)], [False, False], start_offset

    if not lines:
        return ["[End of File]"], [False], next_offset
    return lines, wrapped, next_offset

def get_page_lines(page_idx):
    if page_idx < 0:
        return None, None, 0

    while len(page_offsets) <= page_idx:
        prev_idx = len(page_offsets) - 1
        _, _, nxt_off = read_page_stream(current_file, page_offsets[prev_idx])
        if nxt_off == page_offsets[prev_idx]:
            return None, None, page_offsets[prev_idx]
        page_offsets.append(nxt_off)

    lines, wrapped, nxt_off = read_page_stream(current_file, page_offsets[page_idx])

    if page_idx + 1 == len(page_offsets):
        page_offsets.append(nxt_off)

    return lines, wrapped, nxt_off

# --- RENDERER ---
# One landscape scratch, drawn into and then transposed into each page buffer.
#
# The transpose has to happen HERE, while rendering, and not when the page is
# sent. Pages are rendered ahead of being wanted - the next and previous ones
# during the idle after a turn - so work done here is invisible, while work
# done on the way to the panel sits between the button and the screen changing.
# Putting it in the driver cost exactly that: the same total work, all of it in
# the one place the reader is being watched.
_frame_scratch = None
_frame_canvas = None
_frame_white = None


def begin_frame():
    """A blank landscape canvas. Draw, then call end_frame()."""
    global _frame_scratch, _frame_canvas, _frame_white
    if _frame_scratch is None:
        _frame_scratch = bytearray(epd.landscape_buffer_size)
        _frame_white = b"\xFF" * epd.landscape_buffer_size
        _frame_canvas = new_canvas(_frame_scratch)
    _frame_scratch[:] = _frame_white      # one copy, not a Python clear loop
    return _frame_canvas


def end_frame():
    """Transpose the scratch into a fresh buffer in the panel's orientation."""
    out = bytearray(epd.buffer_size)
    rotate(_frame_scratch, out,
           epd.landscape_width, epd.landscape_height, epd.landscape_stride,
           epd.width, epd.height, epd.bytes_per_row, epd.rotation)
    return out


def new_canvas(out_buf):
    # Landscape and unrotated. The driver transposes into the panel's own
    # orientation on the way out, which is what lets the font blit bytes: in
    # landscape a glyph's row of pixels is a run of bits inside one byte, where
    # in the panel's portrait layout it is one bit in each of eight bytes.
    canvas = adafruit_framebuf.FrameBuffer(
        out_buf,
        epd.landscape_width,
        epd.landscape_height,
        adafruit_framebuf.MHMSB,
        stride=epd.landscape_stride * 8
    )
    return canvas


def render_page_buffer(page_idx):
    lines, wrapped, current_offset = get_page_lines(page_idx)
    if lines is None:
        return None

    canvas = begin_frame()

    y = PAGE_TOP
    for i in range(len(lines)):
        if JUSTIFY_TEXT and wrapped[i]:
            draw_text_justified(canvas, lines[i], PADDING_X, y,
                                MAX_LINE_WIDTH_PX, color=0)
        else:
            draw_text(canvas, lines[i], PADDING_X, y, color=0)
        y += LINE_HEIGHT

    if FILE_SIZE > 0:
        progress = min(1.0, max(0.0, current_offset / FILE_SIZE))
        fill_w = int(WIDTH * progress)
        if fill_w > 0:
            canvas.hline(0, HEIGHT - 1, fill_w, 0)

    pct, charging = get_battery_status()
    if charging:
        status_text = "USB"
    elif pct >= 0:
        status_text = f"{pct}%"
    else:
        status_text = ""

    if status_text:
        status_w = len(status_text) * 6
        status_x = WIDTH - status_w - PADDING_X
        status_y = 2
        status_h = 10

        area_is_free = True
        for dy in range(status_h):
            for dx in range(status_w):
                if canvas.pixel(status_x + dx, status_y + dy) == 0:
                    area_is_free = False
                    break
            if not area_is_free:
                break

        if area_is_free:
            canvas.text(status_text, status_x, status_y, 0)

    return end_frame()


def render_sleep_screen():
    canvas = begin_frame()

    title = "Sleeping..."
    title_w = get_string_width(title)
    title_x = (WIDTH - title_w) // 2
    draw_text(canvas, title, title_x, 20, color=0)

    # Which book you were in, so a sleeping reader still identifies itself -
    # e-paper holds this screen for weeks at no power. Trimmed with an ellipsis
    # rather than overflowing the panel on a long filename.
    name = fit_text(book_title(current_file), WIDTH - 8)
    draw_text(canvas, name, (WIDTH - get_string_width(name)) // 2, 45, color=0)

    progress_pct = 0
    if FILE_SIZE > 0 and current_page_idx < len(page_offsets):
        offset = page_offsets[current_page_idx]
        progress_pct = int(min(100, max(0, (offset / FILE_SIZE) * 100)))

    prog_text = f"{progress_pct}%"
    draw_text(canvas, prog_text, 1, 100, color=0)

    if FILE_SIZE > 0:
        fill_w = int(WIDTH * (progress_pct / 100.0))
        if fill_w > 0:
            canvas.hline(0, HEIGHT - 1, fill_w, 0)

    return end_frame()

current_page_idx = 0
refresh_counter = 0

curr_buf = None
next_buf = None
prev_buf = None

def prefetch_neighbours(idx):
    global next_buf, prev_buf
    set_led(True)
    next_buf = render_page_buffer(idx + 1)
    prev_buf = render_page_buffer(idx - 1) if idx > 0 else None
    set_led(False)

def shift_cache_forward(new_idx):
    global curr_buf, next_buf, prev_buf
    set_led(True)
    prev_buf = curr_buf
    curr_buf = next_buf
    next_buf = render_page_buffer(new_idx + 1)
    set_led(False)

def shift_cache_backward(new_idx):
    global curr_buf, next_buf, prev_buf
    set_led(True)
    next_buf = curr_buf
    curr_buf = prev_buf
    prev_buf = render_page_buffer(new_idx - 1) if new_idx > 0 else None
    set_led(False)

def display_page(buf, is_full=False):
    global refresh_counter
    if buf is None:
        return

    set_led(True)
    if is_full or (ENABLE_PERIODIC_FULL_REFRESH and refresh_counter >= 10):
        epd.display_full(buf)
        refresh_counter = 0
    else:
        epd.display_partial(buf)
        refresh_counter += 1
    set_led(False)


def show_restored_page(buf):
    if not woke_from_deep_sleep:
        display_page(buf, is_full=True)
        return

    if not SHOW_SLEEP_SCREEN:
        epd.set_previous(buf)
        log_step("Woke from sleep; page still on panel, no refresh needed.")
        return

    if FAST_WAKE:
        epd.set_previous(render_sleep_screen())
        display_page(buf, is_full=False)
        return

    display_page(buf, is_full=True)

# --- BOOK PICKER ---
# PICKER_ROWS is set by load_reader_font(): it follows the font, and a fixed
# value here would freeze whatever the boot font happened to give.


def render_list(title, labels, sel, top):
    """A scrolling list with the selected row inverted. Books and fonts both."""
    canvas = begin_frame()

    # Every y here is the top of a glyph box, which is what PropFont.draw
    # takes. The old numbers (-3 for text, +2 for the highlight) were offsets
    # against the previous font system's origin and left the selection box
    # five pixels out of step with its own text.
    draw_text(canvas, "%s  %d/%d" % (title, sel + 1, len(labels)),
              PADDING_X, 0, color=0)
    canvas.hline(0, LINE_HEIGHT - 1, WIDTH, 0)

    for row in range(PICKER_ROWS):
        idx = top + row
        if idx >= len(labels):
            break
        row_y = LINE_HEIGHT * (row + 1)
        label = fit_text(labels[idx], MAX_LINE_WIDTH_PX - 4)
        if idx == sel:
            # The highlight is exactly one line pitch tall and starts where the
            # text does, so the glyph box sits inside it by construction.
            canvas.fill_rect(0, row_y, WIDTH, LINE_HEIGHT, 0)
            draw_text(canvas, label, PADDING_X, row_y, color=1)
        else:
            draw_text(canvas, label, PADDING_X, row_y, color=0)

    return end_frame()


def picker_label(name):
    if name == GOTO_ROW:
        return "Jump to…"
    if name == FONTS_ROW:
        return "Fonts…"
    if name.lower().endswith(".epub"):
        # Keep the extension here, unlike everywhere else. This row is not a
        # book yet - selecting it starts a conversion that takes a minute and
        # needs the board off USB - and ".epub" says that on its own.
        return name.rsplit("/", 1)[-1]
    return book_title(name)


def turn_forward():
    """Advance one page. False at the end of the book."""
    global current_page_idx
    if next_buf is None:
        return False
    current_page_idx += 1
    t0 = time.monotonic()
    display_page(next_buf)
    shift_cache_forward(current_page_idx)
    save_position()
    print(f"[Page {current_page_idx + 1}] Turn took {time.monotonic() - t0:.2f}s")
    return True


def turn_back(pages=1):
    """Go back `pages`, clamped at the start. False if already there.

    Two pages back is what a double tap needs: the first tap has already moved
    forward one, so undoing that and stepping back lands on the page before the
    one the reader was on. Only one page back is cached, so the two-page case
    re-renders.
    """
    global current_page_idx, curr_buf, next_buf, prev_buf
    target = max(0, current_page_idx - pages)
    if target == current_page_idx:
        return False

    t0 = time.monotonic()
    if target == current_page_idx - 1 and prev_buf is not None:
        current_page_idx = target
        display_page(prev_buf)
        shift_cache_backward(current_page_idx)
    else:
        current_page_idx = target
        set_led(True)
        curr_buf = render_page_buffer(target)
        set_led(False)
        next_buf = None
        prev_buf = None
        display_page(curr_buf)
        prefetch_neighbours(target)
    save_position()
    print(f"[Page {current_page_idx + 1}] Back took {time.monotonic() - t0:.2f}s")
    return True


def choose_from_list(title, labels, sel=0, idle_msg="Idle; nothing chosen."):
    """Scroll a list and return the chosen index, or None.

    The gesture vocabulary and its subtleties live here once: tap moves down,
    double-tap moves up, hold selects, longer hold backs out. The double-tap
    correction in particular is easy to get subtly wrong - the refresh outlasts
    the double-tap window, so the second press is already queued by the time we
    look - and having the font menu re-implement it would have meant two places
    to get it right.
    """
    if not labels:
        return None
    top = 0
    idle_since = time.monotonic()
    pending_release = None
    drain_events()

    while True:
        if sel < top:
            top = sel
        elif sel >= top + PICKER_ROWS:
            top = sel - PICKER_ROWS + 1
        top = max(0, min(top, max(0, len(labels) - PICKER_ROWS)))

        display_page(render_list(title, labels, sel, top))

        if pending_release is not None:
            if was_double_tap(pending_release):
                sel = (sel - 2) % len(labels)
                pending_release = None
                continue
            pending_release = None

        while True:
            event = next_press()
            if event is None:
                if time.monotonic() - idle_since >= PICKER_TIMEOUT:
                    log_step(idle_msg)
                    return None
                time.sleep(0.02)
                continue

            if event.key_number == KEY_BACK:
                sel = (sel - 1) % len(labels)
                break

            kind, release = classify_hold(KEY_NEXT, event.timestamp)
            if kind == "picker":        # hold selects
                return sel
            if kind == "sleep":         # hold longer backs out
                return None
            sel = (sel + 1) % len(labels)  # tap moves down, corrected above
            pending_release = release
            break

        idle_since = time.monotonic()


def run_fonts():
    """Pick a reading font. Applies it and returns True if it changed."""
    global _font_index
    if len(FONTS) < 2:
        display_page(render_message("Fonts", [
            "Only one font is installed.", "",
            "Copy more .pcf files into /fonts.",
            "tools/ttf2bdf.py and tools/bdf2pcf.py",
            "make them from any TTF."]))
        time.sleep(4)
        return False

    labels = []
    for i in range(len(FONTS)):
        labels.append(("* " if i == _font_index else "  ") + font_label(FONTS[i][1]))
    idx = choose_from_list("Fonts", labels, _font_index,
                           "Font menu idle; keeping the current font.")
    if idx is None or idx == _font_index:
        return False

    previous = FONTS[_font_index][0]
    if not load_reader_font(FONTS[idx][0]):
        load_reader_font(previous)      # put back what was working
        return False
    _font_index = idx
    save_font_choice(idx)
    return True


def run_picker():
    """Show the library. Returns the chosen path, or None if nothing was picked.

    Next moves down (short press) or opens/selects (long press).
    Back moves up (short press).
    """
    names = list_books()
    # Unconverted EPUBs go at the end, prefixed in the list, so the books you
    # can actually read stay at the top.
    names = names + list_epubs()
    if not names:
        log_step("No books found.")
        return None

    # Jump-to rides in as the first row, but only with a book actually open -
    # there is nothing to seek within otherwise. It also starts selected, since
    # seeking within the book you are reading is the commoner reason to open
    # this menu; the book list is one tap down.
    # Jump-to and Fonts ride in as the first rows. Jump-to only with a book
    # actually open - there is nothing to seek within otherwise - and it starts
    # selected, since seeking within the book you are reading is the commoner
    # reason to open this menu; the book list is a tap or two down.
    if FILE_SIZE > 0:
        names = [GOTO_ROW, FONTS_ROW] + names
        sel = 0
    else:
        names = [FONTS_ROW] + names
        sel = names.index(current_file) if current_file in names else 0

    labels = [picker_label(nm) for nm in names]
    idx = choose_from_list("Books", labels, sel,
                           "Picker idle; keeping the current book.")
    return None if idx is None else names[idx]


def _snap_to_line(pos):
    """First byte after the next newline at or after pos.

    Done in binary: seeking to an arbitrary byte can land mid-UTF-8, which text
    mode will not decode. Page offsets are byte offsets anyway.
    """
    if pos <= 0:
        return 0
    try:
        with open(current_file, "rb") as f:
            f.seek(pos)
            chunk = f.read(4096)
            i = chunk.find(b"\n")
            return pos + i + 1 if i >= 0 else pos
    except Exception:
        return 0


def _pages_around(target, back_bytes=4000):
    """(previous page offset, landing page offset) for a byte position.

    Paginating from the start of the book to find a page boundary would mean
    parsing everything before it. Instead start a few KB back, snap to a line,
    and walk forward the handful of pages that covers - so a jump costs a couple
    of milliseconds instead of scaling with book length.

    The boundaries differ from what pagination from page one would produce,
    because wrapping depends on where you started. That is invisible to the
    reader: what matters is that the two pages returned are contiguous.
    """
    start = _snap_to_line(max(0, target - back_bytes))
    prev = start
    off = start
    while True:
        _, _, nxt = read_page_stream(current_file, off)
        if nxt <= off:          # end of file
            break
        if nxt > target:        # off is the page containing target
            break
        prev = off
        off = nxt
    return prev, off


def current_percent():
    """Where we are, rounded to the nearest GOTO_STEP for display.

    Lossy on purpose - the screen shows whole steps - so it is only ever the
    *label* for the current position, never a way back to it. Anything that
    needs the real position must use page_offsets[current_page_idx].
    """
    if FILE_SIZE <= 0 or current_page_idx >= len(page_offsets):
        return 0
    pct = page_offsets[current_page_idx] / FILE_SIZE * 100.0
    return int(max(0, min(100, round(pct / GOTO_STEP) * GOTO_STEP)))


def render_goto_screen(pct):
    # Deliberately the same y positions as the E213, which is 6px shorter. The
    # slack lands at the bottom, and keeping the two files diffable is worth
    # more than six pixels of centring.
    canvas = begin_frame()

    title = "Jump to"
    draw_text(canvas, title, (WIDTH - get_string_width(title)) // 2, 0, color=0)

    # No double-size any more: PropFont draws one size, and scaling a 1-bit
    # bitmap by doubling pixels looks worse than the plain glyphs do.
    big = "%d%%" % pct
    draw_text(canvas, big, (WIDTH - get_string_width(big)) // 2, 22, color=0)

    bar_x, bar_y, bar_w, bar_h = 10, 66, WIDTH - 20, 12
    canvas.rect(bar_x, bar_y, bar_w, bar_h, 0)
    fill = int((bar_w - 4) * pct / 100.0)
    if fill > 0:
        canvas.fill_rect(bar_x + 2, bar_y + 2, fill, bar_h - 4, 0)

    # Minus on the left, plus on the right, matching the bar underneath it.
    hint1 = "double-tap -%d%%    tap +%d%%" % (GOTO_STEP, GOTO_STEP)
    draw_text(canvas, hint1, (WIDTH - get_string_width(hint1)) // 2, 86, color=0)
    hint2 = "hold to open here"
    draw_text(canvas, hint2, (WIDTH - get_string_width(hint2)) // 2, 101, color=0)

    return end_frame()


def run_goto():
    """Percentage picker. Returns the chosen percent, or None to stay put.

    Same gesture vocabulary as the book picker - tap moves, hold commits, longer
    hold backs out - so there is nothing new to learn.

    None means "do not move", and it covers backing out, timing out, *and*
    committing the value we arrived on. That last one matters: the number on
    screen is rounded to GOTO_STEP, so a reader sitting at 37% sees 35%, and
    jumping to the 35% the screen offers would quietly shove them back two
    percent of the book - a couple of pages, silently, for a gesture that
    looked like "stay here". Coming in and confirming without moving now costs
    nothing at all, and the exact position survives because nothing is asked to
    reconstruct it.
    """
    start_pct = current_percent()
    pct = start_pct
    idle_since = time.monotonic()
    pending_release = None
    drain_events()

    while True:
        display_page(render_goto_screen(pct))

        # The refresh outlasts the double-tap window, so if that tap was really
        # the first half of a double, the second press is already queued.
        if pending_release is not None:
            if was_double_tap(pending_release):
                pct = (pct - 2 * GOTO_STEP) % (100 + GOTO_STEP)
            pending_release = None

        while True:
            event = next_press()
            if event is None:
                if time.monotonic() - idle_since >= PICKER_TIMEOUT:
                    log_step("Jump-to idle; staying where we were.")
                    return None
                time.sleep(0.02)
                continue

            if event.key_number == KEY_BACK:
                pct = (pct - GOTO_STEP) % (100 + GOTO_STEP)
                break

            kind, release = classify_hold(KEY_NEXT, event.timestamp)
            if kind == "picker":        # hold opens the book here
                if pct == start_pct:
                    log_step("Jump-to: %d%% unchanged, keeping the exact "
                             "position (offset %d)."
                             % (pct, page_offsets[current_page_idx]))
                    return None
                return pct
            if kind == "sleep":         # hold longer cancels
                return None
            # Wraps at the top: on a one-button device, getting from 100% back
            # to 5% should not need twenty double-taps.
            pct = (pct + GOTO_STEP) % (100 + GOTO_STEP)
            pending_release = release
            break

        idle_since = time.monotonic()


def jump_to_percent(pct):
    """Open the current book at pct%, with both neighbours cached."""
    global page_offsets, current_page_idx, curr_buf, next_buf, prev_buf

    target = int(FILE_SIZE * pct / 100.0)
    prev_off, land_off = _pages_around(target)

    # Seed the offsets list with the previous page as well as the landing page,
    # so "back" works immediately after a jump. Pagination only runs forwards,
    # so without this there would be nothing behind the landing page.
    if prev_off < land_off:
        page_offsets = [prev_off, land_off]
        current_page_idx = 1
    else:
        page_offsets = [land_off]
        current_page_idx = 0

    next_buf = None
    prev_buf = None

    set_led(True)
    curr_buf = render_page_buffer(current_page_idx)
    set_led(False)
    display_page(curr_buf)
    prefetch_neighbours(current_page_idx)
    save_position(force=True)
    log_step("Jumped to %d%% (offset %d)" % (pct, page_offsets[current_page_idx]))


def render_message(title, lines):
    """A centred title with a few lines under it. Used by the converter."""
    canvas = begin_frame()
    draw_text(canvas, title, (WIDTH - get_string_width(title)) // 2, PAGE_TOP,
              color=0)
    canvas.hline(0, PAGE_TOP + LINE_HEIGHT + 4, WIDTH, 0)
    y = PAGE_TOP + LINE_HEIGHT * 2
    for line in lines:
        draw_text(canvas, fit_text(line, MAX_LINE_WIDTH_PX), PADDING_X, y,
                  color=0)
        y += LINE_HEIGHT
    return end_frame()


def convert_epub(path):
    """Convert an .epub to a .txt beside it. Returns the .txt path, or None.

    The conversion writes to the drive, which the board can only do when the
    USB host is not holding it - so this works on battery and refuses, with a
    reason on screen, when plugged in. epub_xtract makes that call itself; all
    this does is show what it decided.
    """
    title = book_title(path)
    # Partial, not full. A full refresh flashes for ~2.5 s before a conversion
    # that is itself slow, and this screen is transient. The cost is that some
    # of the page underneath may ghost through it - on a message this brief,
    # that is a fair trade.
    display_page(render_message("Converting", [title, "", "Opening the EPUB…"]))

    try:
        import epub_xtract
    except Exception as e:
        display_page(render_message("Cannot convert",
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
            display_page(render_message("Converting",
                                        [title, "", "%d of %d" % (done, total)]))
        elif stage == "readonly":
            display_page(render_message("Cannot convert",
                                        ["The USB host owns the drive.", "",
                                         "Unplug and convert on battery,",
                                         "then plug back in."]))
        elif stage in ("failed", "empty"):
            display_page(render_message("Conversion failed",
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

    set_led(True)
    t0 = time.monotonic()
    out = None
    err = None
    try:
        out = epub_xtract.convert_book(source, progress=progress, keep_display=True)
    except Exception as e:
        err = e
        log_step("EPUB conversion raised: %s" % e)
    set_led(False)

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
        display_page(render_message("Conversion failed", [title, ""] + detail))
        time.sleep(6)
        return None

    if out:
        log_step("Converted %s in %.1fs -> %s" % (path, time.monotonic() - t0, out))
        # epub_xtract returns an absolute path; the reader's book list is
        # relative to the root for files in it, so match its convention.
        return out[1:] if out.startswith("/") else out

    time.sleep(3)
    return None


def reflow_current_page():
    """Re-paginate from where we are, after the layout metrics changed.

    The byte offset survives a font change - it is a position in the file, not
    in the layout - but every page boundary derived from it does not, so the
    offsets list is rebuilt starting here. That is also why a font change does
    not lose your place: the reader has never stored a page number.
    """
    global page_offsets, current_page_idx, curr_buf, next_buf, prev_buf
    offset = page_offsets[current_page_idx]
    page_offsets = [offset]
    current_page_idx = 0
    next_buf = None
    prev_buf = None
    set_led(True)
    curr_buf = render_page_buffer(0)
    set_led(False)
    # Full: every line moved, so a partial would smear the old layout under
    # the new one.
    display_page(curr_buf, is_full=True)
    prefetch_neighbours(0)


def switch_to_book(path):
    """Store where we are, then open path at its own remembered position."""
    global current_file, FILE_SIZE, page_offsets, current_page_idx
    global curr_buf, next_buf, prev_buf

    save_position(force=True)

    current_file = path
    FILE_SIZE = file_size_of(path)
    present = list_books()
    offset = bookmarks.open(path, present)
    if FILE_SIZE and offset >= FILE_SIZE:
        offset = 0

    page_offsets = [offset]
    current_page_idx = 0
    next_buf = None
    prev_buf = None

    set_led(True)
    curr_buf = render_page_buffer(0)
    set_led(False)
    display_page(curr_buf)
    prefetch_neighbours(0)


def wait_buttons_released(timeout=5.0):
    """Block until neither button is held, so wake alarms can be armed safely.

    alarm.pin.PinAlarm(value=False) is level-triggered: arming it while the pin
    is still pulled low fires it instantly and the board wakes right back up.
    Returns False if the timeout expires, in which case the caller sleeps
    anyway - waking immediately is better than hanging forever on a stuck
    button.
    """
    pins = []
    try:
        for p in (PIN_KEY_NEXT, PIN_KEY_BACK):
            io = digitalio.DigitalInOut(p)
            io.direction = digitalio.Direction.INPUT
            io.pull = digitalio.Pull.UP
            pins.append(io)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(io.value for io in pins):
                time.sleep(0.03)                 # settle, then confirm
                if all(io.value for io in pins):
                    return True
            time.sleep(0.01)
        log_step("Buttons still held after %.1fs; arming alarms anyway." % timeout)
        return False
    except Exception as e:
        print(f"wait_buttons_released: {e}")
        return False
    finally:
        for io in pins:
            try:
                io.deinit()
            except Exception:
                pass


def enter_light_sleep():
    """Pause between page turns at ~1 mA, waking instantly.

    Unlike deep sleep the VM survives, so there is no reboot and no re-render:
    the page cache, the font and the glyph cache are all still in RAM when the
    button is pressed. That is the whole reason this is worth the teardown.

    Returns (key, held_ms) for the press that woke us, so the caller can act on
    it as an ordinary gesture - otherwise the press is swallowed and every page
    costs two of them.
    """
    global keys

    # No save here. Light sleep keeps the VM, so the position is still in RAM
    # on the other side, and this path runs after nearly every page turn -
    # saving here would defeat SAVE_EVERY_N_TURNS entirely. The deadline alarm
    # inside the sleep hands over to deep sleep, which does force a save.

    # Deliberately no sleep screen. The page is already on the panel and
    # e-paper holds it without power, so the fastest wake is to leave it be.
    teardown_display()
    keys.deinit()
    wait_buttons_released()

    next_alarm = alarm.pin.PinAlarm(pin=PIN_KEY_NEXT, value=False, pull=True)
    back_alarm = alarm.pin.PinAlarm(pin=PIN_KEY_BACK, value=False, pull=True)
    # The deadline that eventually drops us into real deep sleep. One wake per
    # SLEEP_TIMEOUT costs nothing next to the 1 mA it is there to escape.
    deadline_alarm = alarm.time.TimeAlarm(
        monotonic_time=time.monotonic() + SLEEP_TIMEOUT
    )
    t_sleep_start = time.monotonic()
    woke = alarm.light_sleep_until_alarms(next_alarm, back_alarm, deadline_alarm)
    slept = time.monotonic() - t_sleep_start

    # Identity alone cannot classify the wake. A fast press-and-release can wake
    # the board without the cause being attributable to a specific pin - the
    # button is already up by the time it is resolved - and
    # light_sleep_until_alarms() then returns None. Treating None as "the
    # deadline fired" is what made rapid page turns occasionally drop into deep
    # sleep, since the caller reads that as five minutes of inactivity.
    #
    # So fall back on elapsed time, which is unambiguous: an unattributed wake
    # that happened long before SLEEP_TIMEOUT was a button, not a timeout.
    if woke is back_alarm:
        key = KEY_BACK
    elif woke is next_alarm:
        key = KEY_NEXT
    elif slept < SLEEP_TIMEOUT - 5.0:
        key = KEY_NEXT          # unattributed but early: almost certainly NEXT
    else:
        key = None              # genuinely nothing pressed for SLEEP_TIMEOUT

    # The finger may still be on the button. Measure the hold BEFORE rebuilding
    # anything, so waking with a long press opens the picker exactly as it would
    # while awake - otherwise every wake is forced to be a page turn and the
    # picker needs a second, separate press.
    held_ms = 0
    release_ticks = supervisor.ticks_ms()
    if key == KEY_NEXT:
        btn = digitalio.DigitalInOut(PIN_KEY_NEXT)
        btn.direction = digitalio.Direction.INPUT
        btn.pull = digitalio.Pull.UP
        start = supervisor.ticks_ms()
        lit = False
        while not btn.value:
            held_ms = ticks_ms_diff(supervisor.ticks_ms(), start)
            if held_ms >= SLEEP_HOLD_MS:
                break
            if not lit and held_ms >= LONG_PRESS_MS:
                lit = True
                set_led(True)          # same cue as classify_hold()
            time.sleep(0.005)
        release_ticks = supervisor.ticks_ms()
        if lit:
            set_led(False)
        btn.deinit()

    # keypad first, display second. The second tap of a double tap can land
    # while the display is still being reconstructed, and anything pressed
    # before keypad.Keys exists is simply lost - so start scanning as early as
    # possible and drain the wake press before the slow part.
    build_keys()
    drain_events()

    build_display()
    # The rebuilt driver assumes a blank panel. Tell it what is actually up
    # there, or the next partial refresh smears against the wrong reference.
    if curr_buf is not None:
        epd.set_previous(curr_buf)

    return key, held_ms, release_ticks


def enter_deep_sleep():
    global led, adc_ctrl, vbus_sense

    # The point of no return: waking from here is a reboot that reads the
    # position back from NVM. Nothing else on this path saves it.
    save_position(force=True)

    if SHOW_SLEEP_SCREEN:
        log_step("Preparing sleep screen...")
        sleep_buf = render_sleep_screen()
        set_led(True)
        epd.display_full(sleep_buf)
        set_led(False)

    epd.power_down()
    # Hand the bus back too: exiting while holding the SPI lock stops
    # CircuitPython from offering the REPL afterwards - "Code done running."
    # prints but its "press any key" wait never arrives. Confirmed on hardware.
    epd.release_bus()

    log_step("Entering deep sleep (Wake with Button '21' or BOOT button)...")

    if led is not None:
        led.value = True

    if adc_ctrl is not None:
        adc_ctrl.value = False
        adc_ctrl.deinit()
        adc_ctrl = None

    if vbus_sense is not None:
        vbus_sense.deinit()
        vbus_sense = None

    keys.deinit()   # frees GPIO21/GPIO0 for the wake alarms below

    # The sleep screen went up while the button was still down (classify_hold
    # returns as soon as the hold threshold is crossed), so the finger may still
    # be on it. Arming a level-triggered PinAlarm now would wake us instantly.
    wait_buttons_released()

    wake_btn_next = alarm.pin.PinAlarm(pin=PIN_KEY_NEXT, value=False, pull=True)
    wake_btn_prev = alarm.pin.PinAlarm(pin=PIN_KEY_BACK, value=False, pull=True)

    alarm.exit_and_deep_sleep_until_alarms(wake_btn_next, wake_btn_prev)

def open_picker():
    """Run the picker and act on the choice. Shared by the awake path and the
    light-sleep wake path, so a long press behaves identically either way."""
    chosen = run_picker()
    if chosen == FONTS_ROW:
        if run_fonts():
            log_step("Font is now %s; reflowing from offset %d"
                     % (font_path, page_offsets[current_page_idx]))
            reflow_current_page()
        else:
            display_page(curr_buf, is_full=True)
    elif chosen == GOTO_ROW:
        target = run_goto()
        if target is not None:
            jump_to_percent(target)
        else:
            display_page(curr_buf)
    elif chosen is not None and chosen.lower().endswith(".epub"):
        made = convert_epub(chosen)
        if made:
            switch_to_book(made)
            log_step("Now reading %s at offset %d" % (made, page_offsets[0]))
        else:
            display_page(curr_buf, is_full=True)
    elif chosen is not None and chosen != current_file:
        switch_to_book(chosen)
        log_step(f"Now reading {chosen} at offset {page_offsets[0]}")
    else:
        display_page(curr_buf)
    drain_events()


# Boot setup
woke_from_deep_sleep = alarm.wake_alarm is not None


log_step(f"Rendering restored page (offset {initial_offset} from NVM)...")
t0 = time.monotonic()
set_led(True)
curr_buf = render_page_buffer(current_page_idx)
set_led(False)
log_step(f"Page rendered in {time.monotonic() - t0:.2f}s")

t0 = time.monotonic()
show_restored_page(curr_buf)
log_step(f"Display ready in {time.monotonic() - t0:.2f}s")
log_step(f"=== READABLE (Total: {time.monotonic() - t_boot_start:.2f}s) ===")

t0 = time.monotonic()
prefetch_neighbours(current_page_idx)
log_step(f"Neighbour pages cached in {time.monotonic() - t0:.2f}s")
log_step(f"=== BOOT COMPLETE (Total: {time.monotonic() - t_boot_start:.2f}s) ===")
log_step("boot: reset=%s usb_connected=%s" % (microcontroller.cpu.reset_reason, usb_attached()))

last_activity_time = time.monotonic()

# --- MAIN LOOP ---

try:
    while True:

        now = time.monotonic()

        event = next_press()
        if event is not None:
            if event.key_number == KEY_BACK:
                turn_back()
            else:
                kind, release = classify_hold(KEY_NEXT, event.timestamp)
                if kind == "sleep":
                    log_step("Held past the picker: sleeping now.")
                    enter_deep_sleep()
                elif kind == "picker":
                    open_picker()
                else:
                    # Act on the tap at once. The refresh it triggers is longer
                    # than the double-tap window, so a second press is already
                    # queued by the time we look - forward turns pay nothing.
                    moved = turn_forward()
                    if was_double_tap(release):
                        turn_back(2 if moved else 1)
            last_activity_time = time.monotonic()

        if now - last_activity_time >= LIGHT_SLEEP_TIMEOUT:
            # Two reasons to refuse. Plugged in, sleeping would kill the drive
            # mid-copy. And within the boot grace window, sleeping would make
            # the board unreachable for editing until an erase-and-reflash.
            if usb_attached():
                last_activity_time = time.monotonic()
            elif time.monotonic() - t_boot_start < BOOT_GRACE_SECONDS:
                pass                      # let the grace window run out
            else:
                woke_key, held_ms, release = enter_light_sleep()
                # The PinAlarm swallowed the press, so replay it here as the
                # gesture it actually was - tap turns the page, a hold opens the
                # picker, a longer hold sleeps for real.
                if woke_key == KEY_NEXT:
                    if held_ms >= SLEEP_HOLD_MS:
                        enter_deep_sleep()
                    elif held_ms >= LONG_PRESS_MS:
                        open_picker()
                    else:
                        # Double tap has to be handled here too, not just on the
                        # awake path. With LIGHT_SLEEP_TIMEOUT = 0 the reader is
                        # asleep between virtually every page turn, so this is
                        # the path a double tap actually takes - omitting it is
                        # why going back one page stopped working.
                        moved = turn_forward()
                        if was_double_tap(release):
                            turn_back(2 if moved else 1)
                elif woke_key == KEY_BACK:
                    turn_back()
                else:
                    # No press for SLEEP_TIMEOUT: stop paying 1 mA and drop to
                    # ~16 uA. Costs a reboot to wake, which is the right trade
                    # once the book has clearly been put down.
                    log_step("Idle past SLEEP_TIMEOUT; entering deep sleep.")
                    enter_deep_sleep()
                last_activity_time = time.monotonic()

        time.sleep(0.02)

finally:
    # Thonny's Ctrl-C lands here. Hand the panel and the SPI bus back rather
    # than exiting with the bus still locked and the panel's rails up: the
    # driver takes the lock once in __init__ and holds it for speed, which the
    # earlier version of this app never did.
    try:
        epd.power_down()
    except Exception as e:
        print(f"power_down on exit failed: {e}")
    epd.release_bus()
    set_led(False)
    print("Reader exited; display powered down and SPI bus released.")

