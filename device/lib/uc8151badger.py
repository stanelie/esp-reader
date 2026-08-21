# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Driver for the Pimoroni Badger 2040's UC8151 e-paper panel (296x128).
#
# Same contract as the two Heltec drivers: native buffers in, set_previous() to
# say what is on the glass, display_full()/display_partial(), power_down(), and a
# deferred busy wait so a refresh overlaps whatever the reader does next.
#
# The UC8151 is the same controller family as the E213's LCMEN2R13EFC1 - PSR
# 0x00, DTM1 0x10, DTM2 0x13, LUTs 0x20-0x24, DRF 0x12 are identical - so this
# is that driver's structure with this panel's size and waveforms. The one real
# difference is where the waveforms come from. The E213 has usable tables in OTP
# and only uploads its own for partial updates; here both refreshes upload
# computed tables, because the LUTs are generated from a speed setting rather
# than being fixed constants.
#
# Waveform generation is ported from Salvatore Sanfilippo's MicroPython UC8151
# driver (github.com/antirez/uc8151_micropython, MIT), by way of the
# CircuitPython port that was on this Badger.
#
# The partial refresh works by emptying the WW and BB tables - the waveforms for
# a pixel that is staying white or staying black. A pixel whose value did not
# change is then never driven, which is what makes the update quiet and quick,
# and it is why previous_buffer has to be accurate: the panel decides what to
# move by comparing DTM1 against DTM2.
import time
import digitalio

_PSR = 0x00
_PWR = 0x01
_POF = 0x02
_PFS = 0x03
_PON = 0x04
_BTST = 0x06
_DSLP = 0x07
_DTM1 = 0x10
_DSP = 0x11
_DRF = 0x12
_DTM2 = 0x13
_LUT_VCOM = 0x20
_LUT_WW = 0x21
_LUT_BW = 0x22
_LUT_WB = 0x23
_LUT_BB = 0x24
_PLL = 0x30
_TSE = 0x41
_CDI = 0x50
_TCON = 0x60
_PTOU = 0x92

# PSR bits. The resolution field is bits 7:6 and it is not optional - setting
# it to zero selects 96x230, and a panel told it is 96x230 while being fed
# 128x296 of data draws a garbled block over most of the glass. That is what
# this driver did on its first run.
_RES_128x296 = 0b10000000
_LUT_OTP = 0b00000000
_LUT_REG = 0b00100000
_FORMAT_BW = 0b00010000
_SCAN_UP = 0b00001000
_SHIFT_RIGHT = 0b00000100
_BOOSTER_ON = 0b00000010
_RESET_SOFT = 0b00000000
_RESET_NONE = 0b00000001

# Everything except the LUT-source bit, which changes per refresh.
_PSR_BASE = (_RES_128x296 | _FORMAT_BW | _SCAN_UP | _SHIFT_RIGHT
             | _BOOSTER_ON | _RESET_NONE)

_FRAMES_4 = 0b00110000        # power-off discharge length
_HZ_100 = 0b00111010

# None means "upload nothing and use the waveform in the panel's OTP". That is
# the factory table, and it is long: it drives the pigment far harder than any
# of the computed tables and is what gives a full page its deepest black. A
# computed speed-2 refresh looks correct on its own but is visibly greyer side
# by side with an OTP one, which is what a full refresh is for.
_SPEED_FULL = None
_SPEED_PARTIAL = 4       # quick, and with WW/BB emptied, only changed pixels


def _lut_row(lut, row, pat, dur, rep):
    # One 6-byte waveform row: a phase pattern, four durations, a repeat.
    off = row * 6
    lut[off] = pat
    lut[off + 1] = dur[0]
    lut[off + 2] = dur[1]
    lut[off + 3] = dur[2]
    lut[off + 4] = dur[3]
    lut[off + 5] = rep


def _build_luts(speed, no_flickering):
    # (VCOM, WW, BW, WB, BB) for a speed, as the MicroPython driver computes.
    vcom = bytearray(44)
    ww = bytearray(42)
    bw = bytearray(42)
    wb = bytearray(42)
    bb = bytearray(42)
    period = max(int(64 / (2 ** (speed - 1))), 1)
    hperiod = max(int(32 / (2 ** (speed - 1))), 1)

    if speed <= 3 and not no_flickering:
        # Charge-neutral three-phase: invert, ping-pong, go to target. The
        # ping-pong is what makes it flash, and what stops charge building up
        # in the film over many refreshes.
        _lut_row(vcom, 0, 0, [period, 0, 0, 0], 2)
        _lut_row(bw, 0, 0b01_000000, [period, 0, 0, 0], 2)
        _lut_row(wb, 0, 0b10_000000, [period, 0, 0, 0], 2)
        _lut_row(vcom, 1, 0, [hperiod, hperiod, 0, 0], 2)
        _lut_row(bw, 1, 0b10_01_0000, [hperiod, hperiod, 0, 0], 1)
        _lut_row(wb, 1, 0b01_10_0000, [hperiod, hperiod, 0, 0], 1)
        _lut_row(vcom, 2, 0, [period, 0, 0, 0], 2)
        _lut_row(bw, 2, 0b10_000000, [period, 0, 0, 0], 2)
        _lut_row(wb, 2, 0b01_000000, [period, 0, 0, 0], 2)
        ww[:] = bw[:]
        bb[:] = wb[:]
    else:
        p = period
        _lut_row(vcom, 0, 0, [p, p, p, p], 1)
        _lut_row(bw, 0, 0b10_00_00_00, [p * 4, 0, 0, 0], 1)
        _lut_row(wb, 0, 0b01_00_00_00, [p * 4, 0, 0, 0], 1)
        _lut_row(ww, 0, 0b01_10_00_00, [p * 2, p * 2, 0, 0], 1)
        _lut_row(bb, 0, 0b10_01_00_00, [p * 2, p * 2, 0, 0], 1)

    if no_flickering:
        # An unchanged pixel gets no waveform at all, so it is not driven.
        for i in range(42):
            ww[i] = 0
            bb[i] = 0
    return vcom, ww, bw, wb, bb


class UC8151Badger:
    def __init__(self, spi, cs_pin, dc_pin, reset_pin, busy_pin, baudrate=4000000,
                 keep_powered=True, rotation=3, previous=None):
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

        self.width = 128
        self.height = 296
        self.bytes_per_row = 16
        self.buffer_size = self.bytes_per_row * self.height

        self.rotation = rotation
        if rotation % 2:
            self.landscape_width = self.height
            self.landscape_height = self.width
        else:
            self.landscape_width = self.width
            self.landscape_height = self.height
        self.landscape_stride = (self.landscape_width + 7) // 8
        self.landscape_buffer_size = self.landscape_stride * self.landscape_height

        self.keep_powered = keep_powered
        self.powered = False
        self.baudrate = baudrate
        while not self.spi.try_lock():
            pass
        self.spi.configure(baudrate=baudrate, polarity=0, phase=0)

        self._busy_pending = False
        self._lut_speed = None          # which tables are currently uploaded
        # Taken from the caller when offered. The reader claims every screen-sized
        # buffer before anything else runs, because this heap does not compact and
        # a 4736-byte block asked for late is the one that fails - see the claim
        # block at the top of code.py.
        if previous is not None:
            self.previous_buffer = previous
            # Chunked, not a per-byte loop: 4736 iterations of Python is ~5ms
            # of boot for no reason. Not b"\xFF" * buffer_size either - that
            # allocates a second screen-sized object, which is the thing this
            # whole arrangement exists to avoid.
            _ff = b"\xFF" * 64
            for _i in range(0, self.buffer_size - 63, 64):
                self.previous_buffer[_i:_i + 64] = _ff
            for _i in range((self.buffer_size // 64) * 64, self.buffer_size):
                self.previous_buffer[_i] = 0xFF
        else:
            self.previous_buffer = bytearray(b"\xFF" * self.buffer_size)
        self.reset()
        self._init_panel()

    # --- the contract the reader depends on -------------------------------

    def set_previous(self, native_buf):
        self.previous_buffer[:] = native_buf

    def _wait_ready(self):
        # Collect a deferred refresh. See the E213 driver for why it defers.
        if self._busy_pending:
            self.wait_busy()
            self._busy_pending = False

    def power_down(self):
        self._wait_ready()
        if not self.powered:
            return
        self.send_command(_POF)
        self.wait_busy()
        self.powered = False

    def release_bus(self):
        self._wait_ready()
        try:
            self.spi.unlock()
        except Exception:
            pass

    def display_full(self, new_buffer):
        self._refresh(new_buffer, _SPEED_FULL, False)

    def display_partial(self, new_buffer):
        self._refresh(new_buffer, _SPEED_PARTIAL, True)

    # --- panel plumbing ----------------------------------------------------

    def reset(self):
        self._busy_pending = False
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
        # BUSY is active LOW on this panel, as on the E213.
        start = time.monotonic()
        time.sleep(0.002)
        while not self.busy.value:
            if time.monotonic() - start > timeout:
                break
            time.sleep(0.005)

    def _init_panel(self):
        # The order here is the vendor driver's, which is known to work on
        #         this panel: soft reset, rails, booster, power on, then configuration.
        self.send_command(_PSR)
        self.send_data(_RESET_SOFT)
        self.send_command(_PWR)
        self.send_data(bytes([0b000011,    # VDS internal, VDG internal
                              0b000000,    # VCOM_VD, VGHL 16V
                              0b100110,    # +10V VDH
                              0b100110,    # -10V VDL
                              0b000011]))  # VDHR, red only, unused here
        self.send_command(_BTST)
        self.send_data(bytes([0x17, 0x17, 0x17]))   # 10ms, strength 3, 6.58us
        self.send_command(_PON)
        self.wait_busy()
        self.powered = True
        self.send_command(_PSR)
        self.send_data(_PSR_BASE | _LUT_REG)
        self.send_command(_PFS)
        self.send_data(_FRAMES_4)
        self.send_command(_TSE)
        self.send_data(0x00)          # internal temperature sensor
        self.send_command(_TCON)
        self.send_data(0x22)
        # Register 0x50: [7:6] border, [5:4] data polarity (DDX), [3:0] interval.
        #
        # DDX is 11, not the vendor driver's 00. That driver keeps its own
        # framebuffer where 0 is white; this reader's convention is the
        # opposite - a blank page is 0xFF - and feeding those bits to a panel
        # expecting the other polarity gives a correct but photographically
        # negative page. The E213 driver, same controller family and the same
        # 0xFF-is-blank convention, also runs DDX 11.
        self.send_command(_CDI)
        self.send_data(0b11_11_1100)
        self.send_command(_PLL)
        self.send_data(_HZ_100)
        self.send_command(_POF)
        self.wait_busy()
        self.powered = False

    def _upload_luts(self, speed, no_flickering):
        key = (speed, no_flickering)
        if self._lut_speed == key:
            return
        vcom, ww, bw, wb, bb = _build_luts(speed, no_flickering)
        for cmd, table in ((_LUT_VCOM, vcom), (_LUT_WW, ww), (_LUT_BW, bw),
                           (_LUT_WB, wb), (_LUT_BB, bb)):
            self.send_command(cmd)
            self.send_data(table)
        self._lut_speed = key

    def _refresh(self, new_buffer, speed, no_flickering):
        self._wait_ready()
        if speed is None:
            # OTP: the panel's own table. Nothing to upload, and the register
            # tables are left behind, so note that they must be sent again
            # before the next partial.
            self.send_command(_PSR)
            self.send_data(_PSR_BASE | _LUT_OTP)
            self._lut_speed = None
        else:
            self.send_command(_PSR)
            self.send_data(_PSR_BASE | _LUT_REG)
            self._upload_luts(speed, no_flickering)

        self.send_command(_PON)
        self.wait_busy()
        self.powered = True
        self.send_command(_PTOU)

        # DTM1 is what is on the glass, DTM2 what should be. The panel drives
        # only the pixels where they differ, when WW and BB are empty.
        self.send_command(_DTM1)
        self.send_data(self.previous_buffer)
        self.send_command(_DTM2)
        self.send_data(new_buffer)
        self.send_command(_DSP)

        self.send_command(_DRF)
        self._busy_pending = True

        if not self.keep_powered:
            self.power_down()

        self.previous_buffer[:] = new_buffer
