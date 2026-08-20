# Battery ADC calibration logger. Copy over code.py on the device, run it on
# battery power, restore the reader afterwards:
#
#     cp tools/battery_calibrate.py /media/.../CIRCUITPY/code.py
#     ... take readings ...
#     cp device/code.py            /media/.../CIRCUITPY/code.py
#
# Procedure, one voltage per power-up:
#   1. unplug USB, set the PPK2 to a known voltage, power the board
#   FIRST, check repeatability: press the button three times at ONE voltage.
#   If the three means agree to a few counts the method is sound; if they wander
#   by hundreds, fix that before taking a calibration curve - you cannot tell
#   converter non-linearity from an unrepeatable measurement.
#
#   2. press either button - the panel shows the point number and the raw mean.
#      Wait for the panel to update before cutting power: the file is closed
#      (and so flushed) before that refresh starts, so the new screen IS the
#      confirmation that the point reached flash.
#   3. repeat for the next voltage; points append across power-ups
#   4. plug in, open /battery_cal.csv, fill in the `volts` column
#   5. run tools/battery_fit.py on it to get CAL_SLOPE and CAL_OFFSET
#
# Why it will not record over USB: with a cable in, the divider is reading the
# supply node, which USB holds near 4 V whatever the cell is doing. A reading
# taken then is not wrong by a little, it is measuring something else entirely,
# so logging it would poison the fit. The panel says so rather than pretending.

import time

t0 = time.monotonic()

import os
import board
import busio
import digitalio
import storage
import supervisor
import analogio
import microcontroller
import displayio
import adafruit_framebuf

# "point": one reading per press, for taking a calibration curve.
# "sweep": one press measures the ADC node's settling curve, which is the
#          diagnostic to run first if readings at a fixed voltage drift.
# Measured 2026-08-20: the sweep came back flat from 15 ms to 2500 ms - 20
# counts of scatter over a 166x change in settle time - so the node is settled
# well before 15 ms and there is nothing to gain by waiting. Back to point mode.
MODE = "point"

LOG = "/battery_cal.csv" if MODE == "point" else "/battery_settle.csv"

# Settle times to try, ms. Each step starts from a discharged node - ADC_CTRL is
# taken low and left there for REST_S first - because otherwise every step after
# the first inherits the previous step's charge and the curve looks flat no
# matter what the truth is.
SETTLE_SWEEP_MS = (15, 50, 150, 400, 1000, 2500)
REST_S = 2.5

# Readings per press in point mode. Three, so every voltage carries its own
# repeatability check and a bad one is obvious at the time rather than after
# the fit. The sweep measured ~20 counts of spread between consecutive
# readings; if a triple comes back spread by hundreds, something is wrong with
# that reading and it should be retaken, not fitted.
REPEATS = 3
SAMPLES = 128           # the reader averages 8; more here for a better mean
SETTLE_S = 0.015        # identical to the reader's, so the fit transfers
SAMPLE_GAP_S = 0.0005   # spreads the burst over ~64 ms instead of ~2 ms, so
                        # supply ripple averages out instead of being sampled
                        # at one arbitrary phase of it

# --- which board (same whitelist rule as the reader: never guess) -----------
PANELS = {
    "e213": {"driver": "lcmen2r13efc1", "sck": 4, "mosi": 6, "cs": 5,
             "dc": 2, "rst": 3, "busy": 1, "rotation": 1},
    "e290": {"driver": "ssd1680e290", "sck": 2, "mosi": 1, "cs": 3,
             "dc": 4, "rst": 5, "busy": 6, "rotation": 3},
}
BOARDS = {
    "heltec_vision_master_e213": "e213",
    "heltec_vision_master_e290_lightsleep": "e290",
    "heltec_vision_master_e290": "e290",
}
BOARD_OVERRIDE = None

PIN_KEY_NEXT = microcontroller.pin.GPIO21
PIN_KEY_BACK = microcontroller.pin.GPIO0
PIN_LED = microcontroller.pin.GPIO45
PIN_ADC_CTRL = microcontroller.pin.GPIO46
PIN_BATTERY = microcontroller.pin.GPIO7


def gpio(n):
    return getattr(microcontroller.pin, "GPIO%d" % n)


key = BOARD_OVERRIDE or getattr(board, "board_id", None)
if key not in BOARDS:
    raise RuntimeError("Unrecognised board %r; set BOARD_OVERRIDE to one of %s"
                       % (key, ", ".join(sorted(BOARDS))))
PANEL = PANELS[BOARDS[key]]

if PANEL["driver"] == "ssd1680e290":
    from ssd1680e290 import SSD1680E290 as PanelDriver
else:
    from lcmen2r13efc1 import LCMEN2R13EFC1 as PanelDriver

displayio.release_displays()

led = digitalio.DigitalInOut(PIN_LED)
led.direction = digitalio.Direction.OUTPUT
led.value = False

spi = busio.SPI(clock=gpio(PANEL["sck"]), MOSI=gpio(PANEL["mosi"]))
epd = PanelDriver(spi,
                  digitalio.DigitalInOut(gpio(PANEL["cs"])),
                  digitalio.DigitalInOut(gpio(PANEL["dc"])),
                  digitalio.DigitalInOut(gpio(PANEL["rst"])),
                  digitalio.DigitalInOut(gpio(PANEL["busy"])),
                  baudrate=4000000, keep_powered=True)

ROT = PANEL["rotation"]
WIDTH, HEIGHT = (epd.height, epd.width) if ROT % 2 else (epd.width, epd.height)

adc_ctrl = digitalio.DigitalInOut(PIN_ADC_CTRL)
adc_ctrl.direction = digitalio.Direction.OUTPUT
adc_ctrl.value = False

buttons = []
for p in (PIN_KEY_NEXT, PIN_KEY_BACK):
    b = digitalio.DigitalInOut(p)
    b.direction = digitalio.Direction.INPUT
    b.pull = digitalio.Pull.UP
    buttons.append(b)


def usb_attached():
    try:
        return bool(supervisor.runtime.usb_connected)
    except AttributeError:
        return False


# --- can we write? ---------------------------------------------------------
# storage.remount() only fails while the host holds the block device, so on
# battery this succeeds and the flag lasts just for this run - the next boot
# hands the drive back to USB read-write on its own. Nothing in boot.py, and no
# way to end up with a permanently read-only drive.
# The drive is taken for the few milliseconds of each write and handed straight
# back - never held across the session.
#
# Holding it was what stranded the board twice. Once remounted, the host sees a
# read-only CIRCUITPY for as long as the program runs, and with no serial access
# on this board there is then no way in: the fix costs an erase and reflash. My
# first attempt at a fix - release it when USB appears - also had a hole, since
# it marked the drive released even when the remount raised, and so never
# retried. Not holding it in the first place removes the whole class of problem:
# the exposure window is now ~10 ms per recorded point rather than minutes, and
# nothing needs to notice a cable to end it.
#
# Defensive: if a previous run somehow left the flag set, put it back.
try:
    storage.remount("/", readonly=True)
except Exception:
    pass


def take_filesystem():
    """Borrow write access. Returns True if we got it."""
    try:
        storage.remount("/", readonly=False)
        return True
    except Exception:
        return False       # host holds it - which is also our cue not to record


def release_filesystem():
    for _ in range(3):     # this one really must not be skipped
        try:
            storage.remount("/", readonly=True)
            return True
        except Exception:
            time.sleep(0.05)
    return False


def draw(lines, invert_first=False):
    buf = bytearray(b"\xFF" * epd.buffer_size)
    c = adafruit_framebuf.FrameBuffer(buf, epd.width, epd.height,
                                      adafruit_framebuf.MHMSB,
                                      stride=epd.bytes_per_row * 8)
    c.rotation = ROT
    y = 3
    for i, line in enumerate(lines):
        if i == 0 and invert_first:
            c.fill_rect(0, y - 2, WIDTH, 12, 0)
            c.text(line, 2, y, 1)
        else:
            c.text(line, 2, y, 0)
        y += 12
    return buf


def show(lines, invert_first=False, full=False):
    buf = draw(lines, invert_first)
    if full:
        epd.display_full(buf)
    else:
        epd.display_partial(buf)


def read_adc(settle_s=None, samples=None):
    """Mean/median/min/max of SAMPLES raw counts.

    Nothing else on the board may be drawing current while this runs. The LED
    is off and the panel controller is in its deep sleep, because the divider
    reads the *supply node*: any load here pulls that node down through the
    source's output impedance and the reading follows it. That is not noise
    that averages away, it is an offset, and it is worst at low supply voltage
    where a switching regulator draws the most input current for the same
    output - exactly where a calibration wants to be accurate.
    """
    try:
        epd.power_down()        # the next refresh re-inits it; costs ~30 ms
    except Exception:
        pass
    led.value = False
    time.sleep(0.05)            # let the supply recover from whatever just ran

    settle_s = SETTLE_S if settle_s is None else settle_s
    samples = SAMPLES if samples is None else samples

    adc_ctrl.value = True
    time.sleep(settle_s)
    adc = analogio.AnalogIn(PIN_BATTERY)
    vals = []
    for _ in range(samples):
        vals.append(adc.value)
        time.sleep(SAMPLE_GAP_S)
    adc.deinit()
    adc_ctrl.value = False

    vals.sort()
    mid = len(vals) // 2
    median = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0
    # Count the full-scale outliers rather than letting them quietly wreck the
    # mean. Three readings in the first run had 42-43% of their samples pinned
    # at 62297 while the median stayed sane; without this the mean just looks
    # like a strange number.
    railed = sum(1 for v in vals if v > median * 1.5)
    return sum(vals) / float(len(vals)), median, vals[0], vals[-1], railed


def existing_points():
    n = 0
    try:
        with open(LOG, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("idx"):
                    n += 1
    except OSError:
        pass
    return n


def cpu_temp():
    try:
        return "%.1f" % microcontroller.cpu.temperature
    except Exception:
        return ""


def record(idx, settle_ms, samples, mean, median, lo, hi, railed):
    if not take_filesystem():
        raise OSError("drive is held by the host")
    try:
        _write_row(idx, settle_ms, samples, mean, median, lo, hi, railed)
    finally:
        release_filesystem()


def _write_row(idx, settle_ms, samples, mean, median, lo, hi, railed):
    new = not _log_exists()
    with open(LOG, "a") as f:
        if new:
            f.write("# raw ADC counts at a known supply voltage.\n"
                    "# point mode: fill in `volts`, then run tools/battery_fit.py\n"
                    "# sweep mode: read the settle_ms column directly - the\n"
                    "#   question is where `median` stops changing.\n")
            f.write("idx,volts,settle_ms,n,mean,median,min,max,spread,railed,temp_c\n")
        f.write("%d,,%d,%d,%.1f,%.1f,%d,%d,%d,%d,%s\n"
                % (idx, settle_ms, samples, mean, median, lo, hi,
                   hi - lo, railed, cpu_temp()))


def _log_exists():
    try:
        os.stat(LOG)
        return True
    except OSError:
        return False


def blink(n, on=0.06, off=0.12):
    for _ in range(n):
        led.value = True
        time.sleep(on)
        led.value = False
        time.sleep(off)


count = existing_points()

if usb_attached():
    show(["USB IS CONNECTED - NOT RECORDING",
          "",
          "With a cable in, the divider reads the",
          "supply node, which USB holds near 4V",
          "whatever the cell is doing. Readings",
          "here measure the charger, not the",
          "battery, so they are not logged.",
          "",
          "Unplug USB, power from the PPK2,",
          "and press a button."], invert_first=True, full=True)
elif MODE == "sweep":
    show(["SETTLE SWEEP",
          "",
          "logging to %s" % LOG,
          "%d row(s) already recorded" % count,
          "",
          "one press measures %d settle times" % len(SETTLE_SWEEP_MS),
          "(%s ms), ~%ds per press." % (
              "/".join(str(m) for m in SETTLE_SWEEP_MS),
              int(sum(SETTLE_SWEEP_MS) / 1000 + len(SETTLE_SWEEP_MS) * REST_S)),
          "",
          "ready."], invert_first=True, full=True)
else:
    show(["BATTERY CALIBRATION",
          "",
          "logging to %s" % LOG,
          "%d point(s) already recorded" % count,
          "",
          "set the PPK2 voltage, then press",
          "either button to record a point.",
          "",
          "ready."], invert_first=True, full=True)

blink(2)
print("calibrate: board=%s mode=%s usb=%s rows=%d"
      % (key, MODE, usb_attached(), count))

while True:
    if not all(b.value for b in buttons):          # either button, active low
        blink(1)                                   # acknowledge, then go quiet

        if MODE == "sweep":
            steps = SETTLE_SWEEP_MS
        else:
            # round, not int: 0.015 * 1000 is 14.999999999999998 in binary
            # floating point, so int() logged every row as a 14 ms settle.
            steps = (round(SETTLE_S * 1000),) * REPEATS
        results = []
        for ms in steps:
            if len(steps) > 1:
                adc_ctrl.value = False             # discharge, same start every time
                time.sleep(REST_S)
            mean, median, lo, hi, railed = read_adc(settle_s=ms / 1000.0)
            saved = False
            err = ""
            if usb_attached():
                err = "not recorded: USB attached"
            else:
                try:
                    record(count + 1, ms, SAMPLES, mean, median, lo, hi, railed)
                    count += 1
                    saved = True
                except Exception as e:
                    err = "%s: %s" % (type(e).__name__, e)
            results.append((ms, median, railed, saved, err))
            print("settle=%dms mean=%.1f median=%.1f min=%d max=%d railed=%d saved=%s %s"
                  % (ms, mean, median, lo, hi, railed, saved, err))

        meds = [r[1] for r in results]
        if MODE == "sweep":
            lines = ["SETTLE SWEEP", ""]
            for ms, median, railed, saved, err in results:
                lines.append("%5d ms  %8.1f%s%s"
                             % (ms, median,
                                "  %d railed" % railed if railed else "",
                                "" if saved else "  UNSAVED"))
            lines += ["", "drift %+.0f counts over the sweep"
                          % (meds[-1] - meds[0])]
        else:
            lines = ["ROWS %d-%d" % (count - len(results) + 1, count)
                     if results[-1][3] else "READINGS (NOT SAVED)", ""]
            for i, (ms, median, railed, saved, err) in enumerate(results):
                lines.append("  #%d   %9.1f%s%s"
                             % (i + 1, median,
                                "   %d railed" % railed if railed else "",
                                "" if saved else "   UNSAVED"))
            spread = max(meds) - min(meds)
            lines += ["",
                      "spread %.0f counts%s" % (spread,
                          "  - RETAKE THIS ONE" if spread > 100 else "  - good"),
                      "", "next voltage, then press again."]
        show(lines, invert_first=True)

        blink(3 if results[-1][3] else 6)
        while not all(b.value for b in buttons):   # wait for release
            time.sleep(0.01)
        time.sleep(0.2)                            # debounce
    time.sleep(0.02)
