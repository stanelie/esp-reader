// This file is part of the CircuitPython project: https://circuitpython.org
//
// SPDX-FileCopyrightText: Copyright (c) 2019 Scott Shawcroft for Adafruit Industries
//
// SPDX-License-Identifier: MIT

#pragma once

// Micropython setup

// Deliberately not the same string as the upstream board. boot_out.txt is the
// only thing that tells you, on a device you did not flash five minutes ago,
// whether light sleep on it is real or a WFI loop -- and that is exactly the
// question you end up asking when a power measurement looks wrong.
#define MICROPY_HW_BOARD_NAME       "Heltec Vision Master E290 (real light sleep)"
#define MICROPY_HW_MCU_NAME         "ESP32S3"

#define CIRCUITPY_BOOT_BUTTON (&pin_GPIO0)

// The whole reason this board definition exists.
//
// Requires patches/0001-opt-in-real-light-sleep.patch from
// ~/Documents/circuitpython-e213/, which is board-independent and already
// applied to the checkout. Without the patch this define does nothing; with
// it, alarm.light_sleep_until_alarms() calls esp_light_sleep_start() instead
// of spinning on WFI -- 1.1 mA against 43 mA, measured on the E213 with a
// PPK2 -- at the cost of tearing down every non-RTC peripheral, so SPI, I2C,
// PWM and keypad objects must be rebuilt after each wake, and USB does not
// come back until a reset-class event.
//
// This MUST live here and not in mpconfigboard.mk: the espressif Makefile has
// no CFLAGS_BOARD, so a define there is silently dropped and you get a
// firmware that looks right and still spins on WFI.
#define CIRCUITPY_ESP_REAL_LIGHT_SLEEP (1)

#define CIRCUITPY_BOARD_SPI         (2)
#define CIRCUITPY_BOARD_SPI_PIN     { \
        {.clock = &pin_GPIO2, .mosi = &pin_GPIO1, .miso = NULL}, \
        {.clock = &pin_GPIO9, .mosi = &pin_GPIO10, .miso = &pin_GPIO11}, \
}

#define CIRCUITPY_BOARD_I2C         (1)
#define CIRCUITPY_BOARD_I2C_PIN     { \
        {.scl = &pin_GPIO38, .sda = &pin_GPIO39}, \
}

#define DEFAULT_UART_BUS_TX         (&pin_GPIO43)
#define DEFAULT_UART_BUS_RX         (&pin_GPIO44)
