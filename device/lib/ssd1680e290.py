# SPDX-License-Identifier: MIT
"""Driver for the Heltec Vision Master E290 e-paper panel (296x128, SSD1680).

Written to be a drop-in replacement for the E213's LCMEN2R13EFC1 driver: same
constructor signature, same width/height/bytes_per_row/buffer_size attributes,
same previous_buffer, display_full(), display_partial(), power_down() and
release_bus(). Everything above the driver in the reader is unchanged.

The two panels share nothing at the command level. The E213 is UC8151-class:
0x12 is *refresh*, image data goes out through 0x10/0x13, the partial waveform
is uploaded as five LUTs into 0x20-0x24, and the DC/DC rails are raised and
dropped by hand with 0x04/0x02. The E290 is SSD1680-class: 0x12 is *soft reset*,
image data goes to 0x24 (and the previous frame to 0x26), the update is kicked
off with 0x22/0x20, and there is no separate power-on step at all - the mode
byte written to 0x22 tells the controller to bring the analog block up and take
it down again as part of the update.

Command values and panel geometry follow the CircuitPython board definition for
this board (ports/espressif/boards/heltec_vision_master_e290/board.c), which is
the one source attested on this exact hardware.
"""

import time
import digitalio

_SW_RESET = 0x12
_DRIVER_OUTPUT = 0x01
_GATE_VOLTAGE = 0x03
_SOURCE_VOLTAGE = 0x04
_DEEP_SLEEP = 0x10
_DATA_ENTRY = 0x11
_TEMP_SENSOR = 0x18
_MASTER_ACTIVATE = 0x20
_UPDATE_CONTROL_2 = 0x22
_WRITE_RAM_BW = 0x24
_WRITE_RAM_OLD = 0x26
_VCOM = 0x2C
_BORDER = 0x3C
_RAM_X_WINDOW = 0x44
_RAM_Y_WINDOW = 0x45
_RAM_X_COUNTER = 0x4E
_RAM_Y_COUNTER = 0x4F

# Mode bytes for 0x22.
#
#   0xF7  bits: clock, analog, load temperature, load LUT from OTP, display,
#         then shut the analog block and clock down again. The full, flashing
#         refresh, and the *only* thing this panel's OTP waveform can do.
#   0xCC  clock, analog, display, disable analog - deliberately without the
#         load-temperature and load-LUT-from-OTP bits, because by then the
#         waveform we want is sitting in the register, not in OTP.
#
# Asking for a partial update by mode byte alone does not work on this panel:
# there is no differential waveform in its OTP to select, so it quietly gives
# you the flashing full refresh instead. The partial LUT below has to be
# uploaded through 0x32 first. Values follow GxEPD2_290_BS, the reference
# driver for this panel family.
_MODE_FULL = 0xF7
_MODE_PARTIAL = 0xCC

# Border waveform, set once at init. Left alone across a partial, as GxEPD2
# does. (Waveshare's driver for a similar panel switches it to 0x80 for
# partials to stop the frame edge flashing; if a bright border strip shows up
# on page turns, that is the knob.)
_BORDER = 0x05

# Partial-update waveform, uploaded into the controller with 0x32. 153 bytes:
# five 12-byte LUTs, twelve 7-byte timing groups, then nine bytes of frame rate
# and XON. The five LUTs are indexed by the (old pixel, new pixel) pair - old
# from RAM 0x26, new from RAM 0x24 - which is why display_partial() has to put
# the frame currently on the panel into 0x26 before writing the new one.
#
# Verbatim from GxEPD2_290_BS. Do not hand-edit: each byte packs four 2-bit
# phase fields (00 ground, 01 VSH1, 10 VSL, 11 VSH2) and a wrong one drives the
# panel with a waveform its pigment was not characterised for.
#
# PROVENANCE, and read this before publishing: GxEPD2 is GPL-3.0-or-later. The
# rest of this file is original, but this table was transcribed from it. Whether
# a panel waveform table carries copyright at all is arguable - it is closer to
# a hardware fact than to code, and Waveshare publish equivalent tables in their
# own sample drivers - but the honest thing is to record where it came from and
# let whoever licenses this repository decide, rather than to quietly assert MIT
# over the whole file.
_LUT_PARTIAL = bytes([
    0x00, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x80, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x40, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x0A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x22, 0x22, 0x22, 0x22, 0x22, 0x22, 0x00, 0x00, 0x00,
])
_WRITE_LUT = 0x32

# The panel is 128 source lines wide, but they hang off S8..S135 rather than
# S0..S127, so the visible area starts 8 pixels - one byte - into the
# controller's RAM. This is the `colstart = 8` in board.c. (Waveshare's driver
# for the same size of panel gets there the other way, leaving the window at 0
# and remapping the sources with 0x21 0x00 0x80. Do one or the other, not both.)
_X_BYTE_START = 1


class SSD1680E290:
    def __init__(self, spi, cs_pin, dc_pin, reset_pin, busy_pin, baudrate=4000000,
                 keep_powered=True, rotation=3):
        self.spi = spi
        self.cs = cs_pin
        self.dc = dc_pin
        self.rst = reset_pin
        self.busy = busy_pin

        self.cs.direction = digitalio.Direction.OUTPUT
        self.dc.direction = digitalio.Direction.OUTPUT
        self.rst.direction = digitalio.Direction.OUTPUT
        self.busy.direction = digitalio.Direction.INPUT

        self.cs.value = True
        self.dc.value = False

        # Native panel orientation: 128 wide, 296 tall.
        self.width = 128
        self.height = 296
        self.bytes_per_row = 16          # 128 / 8, and no padding for once
        self.buffer_size = self.bytes_per_row * self.height   # 4736

        # The reader draws landscape and this rotates on the way out. It used
        # to draw through a FrameBuffer with rotation set, which put a
        # coordinate transform and a method call behind every lit pixel; a
        # glyph's row of pixels is a run of bits in one byte in landscape, so
        # drawing that way lets the font blit bytes instead. Measured on a page
        # of text, the transpose costs about 0.6x what the per-pixel drawing it
        # replaces did, and the drawing gets cheaper on top of that.
        self.rotation = rotation
        if rotation % 2:
            self.landscape_width = self.height
            self.landscape_height = self.width
        else:
            self.landscape_width = self.width
            self.landscape_height = self.height
        self.landscape_stride = (self.landscape_width + 7) // 8
        self.landscape_buffer_size = self.landscape_stride * self.landscape_height

        # Kept for API compatibility with the E213 driver. It means something
        # slightly different here: there are no rails to leave up between
        # refreshes, so what it controls is whether the controller is put into
        # its 2 uA deep sleep after each update - which costs a re-init (a few
        # ms, no refresh) on the next one.
        self.keep_powered = keep_powered

        # busio.SPI defaults to 100 kHz and every partial refresh shifts out two
        # 4736-byte frames, so the default would cost ~0.75 s of pure clocking
        # per page turn. Configure once and hold the lock: nothing else on this
        # board shares this bus.
        self.baudrate = baudrate
        while not self.spi.try_lock():
            pass
        self.spi.configure(baudrate=baudrate, polarity=0, phase=0)

        # True between starting a refresh and collecting it - see _update().
        self._busy_pending = False
        self.previous_buffer = bytearray(b"\xFF" * self.buffer_size)
        self.asleep = True              # nothing has been initialised yet
        self.partial_lut_loaded = False
        self._init_panel()

    # --- plumbing --------------------------------------------------------
    def send_command(self, command):
        self.dc.value = False
        self.cs.value = False
        self.spi.write(bytes([command]))
        self.cs.value = True

    def send_data(self, data):
        self.dc.value = True
        self.cs.value = False
        if isinstance(data, int):
            self.spi.write(bytes([data]))
        else:
            self.spi.write(data)
        self.cs.value = True

    def reset(self):
        self._busy_pending = False   # a hardware reset ends any refresh
        self.rst.value = False
        time.sleep(0.01)
        self.rst.value = True
        time.sleep(0.01)
        self.wait_busy()

    def wait_busy(self, timeout=20.0):
        """Block while BUSY is asserted.

        Note the polarity is the opposite of the E213's panel: the SSD1680
        drives BUSY *high* while it is working. Getting this backwards does not
        fail loudly - every wait returns instantly and the refreshes simply come
        out torn, because data is clocked in while the controller is mid-update.
        """
        start = time.monotonic()
        time.sleep(0.002)
        while self.busy.value:
            if time.monotonic() - start > timeout:
                break
            time.sleep(0.005)

    # --- panel state -----------------------------------------------------
    def _init_panel(self):
        self.reset()
        self.send_command(_SW_RESET)
        self.wait_busy()

        # Gate count: 296 lines, little-endian, third byte selects scan order.
        self.send_command(_DRIVER_OUTPUT)
        self.send_data(bytes([(self.height - 1) & 0xFF,
                              ((self.height - 1) >> 8) & 0xFF,
                              0x00]))

        # X increment, Y increment, address counter walks X first - which is
        # what makes the framebuffer a plain row-major blit.
        self.send_command(_DATA_ENTRY)
        self.send_data(0x03)

        self.send_command(_BORDER)
        self.send_data(_BORDER)

        self.send_command(_VCOM)
        self.send_data(0x36)

        self.send_command(_GATE_VOLTAGE)
        self.send_data(0x17)

        self.send_command(_SOURCE_VOLTAGE)
        self.send_data(bytes([0x41, 0x00, 0x32]))

        # Use the on-chip temperature sensor to pick the waveform.
        self.send_command(_TEMP_SENSOR)
        self.send_data(0x80)

        self._set_window()
        self.wait_busy()
        self.asleep = False
        # Whatever was in the LUT register is gone with the reset.
        self.partial_lut_loaded = False

    def _set_window(self):
        self.send_command(_RAM_X_WINDOW)
        self.send_data(bytes([_X_BYTE_START,
                              _X_BYTE_START + self.bytes_per_row - 1]))
        self.send_command(_RAM_Y_WINDOW)
        self.send_data(bytes([0x00, 0x00,
                              (self.height - 1) & 0xFF,
                              ((self.height - 1) >> 8) & 0xFF]))

    def _set_cursor(self):
        self.send_command(_RAM_X_COUNTER)
        self.send_data(_X_BYTE_START)
        self.send_command(_RAM_Y_COUNTER)
        self.send_data(bytes([0x00, 0x00]))

    def _wake(self):
        if self.asleep:
            self._init_panel()

    def _update(self, mode):
        """Start a refresh and return. Does NOT wait for it to finish.

        The panel drives itself from its own RAM once activated - the data is
        already clocked out, and nothing on the SPI bus is needed until the
        next operation. Blocking here made every refresh cost its full duration
        even when the caller had work to do: page turns waited ~0.4 s before
        pre-rendering the next page, and an EPUB conversion stopped dead for
        half a second on every progress update it drew.

        The wait is deferred instead. _wait_ready() collects it at the top of
        anything that next touches the panel, so the panel and the CPU overlap
        and nobody has to remember to poll.
        """
        self.send_command(_UPDATE_CONTROL_2)
        self.send_data(mode)
        self.send_command(_MASTER_ACTIVATE)
        self._busy_pending = True

    def set_previous(self, native_buf):
        """Tell the driver what is on the panel. Buffers here are native.

        Needed after a light sleep, where the driver object is rebuilt from
        scratch but the panel still holds the page.
        """
        self.previous_buffer[:] = native_buf

    def _wait_ready(self):
        """Collect a deferred refresh, if one is still running."""
        if self._busy_pending:
            self.wait_busy()
            self._busy_pending = False

    def power_down(self):
        """Put the controller in its ~2 uA deep sleep. Call before sleeping.

        Coming back needs a hardware reset and a fresh init, which _wake() does
        on the next refresh, so this is safe to call at any point.
        """
        self._wait_ready()
        if self.asleep:
            return
        self.send_command(_DEEP_SLEEP)
        self.send_data(0x01)
        time.sleep(0.002)
        self.asleep = True

    def release_bus(self):
        """Hand the SPI bus back. Call before the program exits or sleeps.

        The lock is taken once in __init__ and held, which is what keeps each
        refresh cheap. The cost is that exiting while still holding it stops
        CircuitPython from entering its post-program "press any key to enter the
        REPL" wait, leaving a serial host unable to reach the board at all.
        """
        # The panel would finish on its own without the bus, but the caller is
        # usually about to sleep or exit, and a refresh cut short by the rails
        # going down leaves a half-drawn page on screen.
        self._wait_ready()
        try:
            self.spi.unlock()
        except Exception:
            pass

    # --- refreshes -------------------------------------------------------
    def display_full(self, new_buffer):
        """The flashing refresh: every pixel driven, no ghosting left behind."""
        self._wait_ready()
        self._wake()

        self._set_window()
        self._set_cursor()
        self.send_command(_WRITE_RAM_BW)
        self.send_data(new_buffer)

        # 0xF7 carries the load-LUT-from-OTP bit, so this overwrites whatever
        # was uploaded to the LUT register: the next partial has to send it
        # again. Missing this is subtle - the first partial after any full
        # refresh silently comes out as another full one.
        self._update(_MODE_FULL)
        self.partial_lut_loaded = False

        if not self.keep_powered:
            self.power_down()

        self.previous_buffer[:] = new_buffer

    def display_partial(self, new_buffer):
        """The fast refresh: only pixels that changed are driven.

        The controller works out which those are by comparing the new image in
        RAM 0x24 against the old one in RAM 0x26, so the old frame is written
        out explicitly first. Trusting the controller to still be holding it
        would break exactly where it matters - after a light sleep the driver
        object is rebuilt from scratch, and the reader hands the previous frame
        back through previous_buffer.
        """
        self._wait_ready()
        self._wake()

        if not self.partial_lut_loaded:
            self.send_command(_WRITE_LUT)
            self.send_data(_LUT_PARTIAL)
            self.partial_lut_loaded = True

        self._set_window()
        self._set_cursor()
        self.send_command(_WRITE_RAM_OLD)
        self.send_data(self.previous_buffer)

        self._set_cursor()
        self.send_command(_WRITE_RAM_BW)
        self.send_data(new_buffer)

        self._update(_MODE_PARTIAL)

        if not self.keep_powered:
            self.power_down()

        self.previous_buffer[:] = new_buffer
