# SPDX-FileCopyrightText: Port of todd-herbert/heltec-eink-modules LCMEN2R13EFC1 Driver
# SPDX-License-Identifier: MIT

import time
import digitalio

_PANEL_SETTING = 0x00
_POWER_OFF = 0x02
_POWER_ON = 0x04
_DISPLAY_REFRESH = 0x12
_DATA_START_TRANSMISSION_1 = 0x10
_DATA_START_TRANSMISSION_2 = 0x13
_VCOM_AND_DATA_INTERVAL = 0x50

LUT_VCOM_PARTIAL = bytes([
    0x01, 0x06, 0x03, 0x02, 0x01, 0x01, 0x01,
    0x01, 0x06, 0x02, 0x01, 0x01, 0x01, 0x01,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
])

LUT_WW_PARTIAL = bytes([
    0x01, 0x06, 0x03, 0x02, 0x81, 0x01, 0x01,
    0x01, 0x06, 0x02, 0x01, 0x01, 0x01, 0x01,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
])

LUT_BW_PARTIAL = bytes([
    0x01, 0x86, 0x83, 0x82, 0x81, 0x01, 0x01,
    0x01, 0x86, 0x82, 0x01, 0x01, 0x01, 0x01,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
])

LUT_WB_PARTIAL = bytes([
    0x01, 0x46, 0x43, 0x02, 0x01, 0x01, 0x01,
    0x01, 0x46, 0x42, 0x01, 0x01, 0x01, 0x01,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
])

LUT_BB_PARTIAL = bytes([
    0x01, 0x06, 0x03, 0x42, 0x41, 0x01, 0x01,
    0x01, 0x06, 0x02, 0x01, 0x01, 0x01, 0x01,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
])


class LCMEN2R13EFC1:
    def __init__(self, spi, cs_pin, dc_pin, reset_pin, busy_pin, baudrate=4000000,
                 keep_powered=True, rotation=1):
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

        self.width = 122
        self.height = 250
        self.bytes_per_row = 16          # 128 bits: 122 rounded up, 6 unused
        self.buffer_size = self.bytes_per_row * self.height

        # See the note in ssd1680e290.py: the reader draws landscape and this
        # transposes on the way out, so the font can blit bytes rather than set
        # pixels through a rotated FrameBuffer.
        self.rotation = rotation
        if rotation % 2:
            self.landscape_width = self.height
            self.landscape_height = self.width
        else:
            self.landscape_width = self.width
            self.landscape_height = self.height
        self.landscape_stride = (self.landscape_width + 7) // 8
        self.landscape_buffer_size = self.landscape_stride * self.landscape_height

        # power_off (0.062 s) plus the next power_on (0.136 s) is ~28% of a
        # partial refresh, so keep the rails up between updates by default and
        # let the caller drop them with power_down() before sleeping.
        self.keep_powered = keep_powered
        self.powered = False

        # busio.SPI defaults to 100 kHz, and every refresh shifts out two full
        # framebuffers (2 x 4000 bytes), so the default costs ~0.65 s of pure
        # clocking per update. Configure the bus once and keep the lock: nothing
        # else on this board shares this SPI.
        self.baudrate = baudrate
        while not self.spi.try_lock():
            pass
        self.spi.configure(baudrate=baudrate, polarity=0, phase=0)

        # True between starting a refresh and collecting it - see _wait_ready.
        self._busy_pending = False
        self.previous_buffer = bytearray(b"\xFF" * self.buffer_size)
        self.reset()

    def set_previous(self, native_buf):
        """Tell the driver what is on the panel. Buffers here are native."""
        self.previous_buffer[:] = native_buf

    def _wait_ready(self):
        """Collect a deferred refresh, if one is still running.

        DISPLAY_REFRESH does not need the bus once started - the panel drives
        itself from its own RAM - so the wait for it is deferred to whatever
        touches the panel next. That lets the caller pre-render the following
        page, or carry on converting a book, while this one is still drawing.
        """
        if self._busy_pending:
            self.wait_busy()
            self._busy_pending = False

    def power_down(self):
        """Drop the panel's DC/DC rails. Call before sleeping."""
        self._wait_ready()
        if not self.powered:
            return
        self.send_command(_POWER_OFF)
        self.wait_busy()
        self.powered = False

    def release_bus(self):
        """Hand the SPI bus back. Call before the program exits or sleeps.

        The lock is taken once in __init__ and held, which is what keeps each
        refresh cheap. The cost is that exiting while still holding it stops
        CircuitPython from entering its post-program "press any key to enter the
        REPL" wait, leaving a serial host unable to reach the board at all.
        """
        # As in ssd1680e290.release_bus: the panel would finish without the
        # bus, but a refresh cut short by the rails dropping leaves a
        # half-drawn page.
        self._wait_ready()
        try:
            self.spi.unlock()
        except Exception:
            pass

    def reset(self):
        self._busy_pending = False   # a hardware reset ends any refresh
        self.rst.value = False
        time.sleep(0.01)
        self.rst.value = True
        time.sleep(0.01)
        self.wait_busy()

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

    def wait_busy(self, timeout=10.0):
        start = time.monotonic()
        time.sleep(0.002)
        while not self.busy.value:
            if time.monotonic() - start > timeout:
                break
            time.sleep(0.005)

    def display_full(self, new_buffer):
        """The flashing refresh: every pixel driven, no ghosting left behind."""
        self._wait_ready()
        self.send_command(_PANEL_SETTING)
        self.send_data(0xDF)

        self.send_command(_VCOM_AND_DATA_INTERVAL)
        self.send_data(0xB7)

        self.send_command(_DATA_START_TRANSMISSION_1)
        self.send_data(new_buffer)

        self.send_command(_DATA_START_TRANSMISSION_2)
        self.send_data(new_buffer)

        # Harmless when the rails are already up: BUSY never drops, so this
        # costs ~2 ms instead of the ~136 ms a cold power-on takes.
        self.send_command(_POWER_ON)
        self.wait_busy()
        self.powered = True

        self.send_command(_DISPLAY_REFRESH)
        self._busy_pending = True

        if not self.keep_powered:
            self.power_down()        # collects the wait itself

        self.previous_buffer[:] = new_buffer

    def display_partial(self, new_buffer):
        """The fast refresh, using the partial LUTs loaded below."""
        self._wait_ready()
        self.send_command(_PANEL_SETTING)
        self.send_data(0xFF)

        self.send_command(_VCOM_AND_DATA_INTERVAL)
        self.send_data(0xD7)

        self.send_command(0x20)
        self.send_data(LUT_VCOM_PARTIAL)

        self.send_command(0x21)
        self.send_data(LUT_WW_PARTIAL)

        self.send_command(0x22)
        self.send_data(LUT_BW_PARTIAL)

        self.send_command(0x23)
        self.send_data(LUT_WB_PARTIAL)

        self.send_command(0x24)
        self.send_data(LUT_BB_PARTIAL)

        self.send_command(_DATA_START_TRANSMISSION_1)
        self.send_data(self.previous_buffer)

        self.send_command(_DATA_START_TRANSMISSION_2)
        self.send_data(new_buffer)

        # Harmless when the rails are already up: BUSY never drops, so this
        # costs ~2 ms instead of the ~136 ms a cold power-on takes.
        self.send_command(_POWER_ON)
        self.wait_busy()
        self.powered = True

        self.send_command(_DISPLAY_REFRESH)
        self._busy_pending = True

        if not self.keep_powered:
            self.power_down()        # collects the wait itself

        self.previous_buffer[:] = new_buffer
