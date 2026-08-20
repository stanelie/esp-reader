// This file is part of the CircuitPython project: https://circuitpython.org
//
// SPDX-FileCopyrightText: Copyright (c) 2020 Scott Shawcroft for Adafruit Industries
//
// SPDX-License-Identifier: MIT

#include "supervisor/board.h"
#include "mpconfigboard.h"
#include "shared-bindings/microcontroller/Pin.h"
#include "shared-bindings/digitalio/DigitalInOut.h"

// Deliberately no displayio epaper display is constructed here.
//
// The E290 board definition builds one, but its start sequence is for an
// SSD1680-class controller (0x12 soft reset, 0x11 RAM entry mode, 0x24/0x26 RAM
// writes, 0x20 refresh). The E213 panel is an LCMEN2R13EFC1, a UC8151-class
// part with an incompatible command set -- on it 0x12 *is* the refresh command,
// data goes out via 0x10/0x13, and the partial-update LUTs are loaded into
// 0x20-0x24. Copying the E290 sequence would produce a display that never
// updates.
//
// Rather than ship an unverified init sequence, the panel is left to
// application code, which already drives it correctly over board.EPD_SPI. That
// also keeps boot fast: constructing a display here costs a full refresh at
// startup, and an app that calls displayio.release_displays() pays for it twice.
//
// To add displayio support later, port the LUTs and the power-on sequence from
// the working driver and construct the display exactly as the E290 does.

void board_init(void) {
    // Deliberately does NOT drive VEXT (GPIO18).
    //
    // The E290 definition sets it high here to power its panel, and this board
    // definition copied that. On the E213 it is wrong on two counts. First, the
    // panel works without it: every stock ESP32-S3 build leaves GPIO18
    // untouched and the e-paper drives fine. Second, VEXT here also feeds the
    // 3.3V QuickLink rail (and, per the E290's own pin comments, a LoRa antenna
    // boost), so asserting it adds load nothing on a plain e-reader uses.
    //
    // never_reset() made it worse: the pin was permanently claimed, so an
    // application could not release it even to measure the cost
    // ("ValueError: VEXT_CTRL in use").
    //
    // VEXT_CTRL is exported in pins.c instead. Code that needs the QuickLink
    // connector should drive it high itself and allow ~1s of settling before
    // touching I2C.
}

// Use the MP_WEAK supervisor/shared/board.c versions of routines not defined here.
