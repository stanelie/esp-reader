// This file is part of the CircuitPython project: https://circuitpython.org
//
// SPDX-FileCopyrightText: Copyright (c) 2019 Scott Shawcroft for Adafruit Industries
//
// SPDX-License-Identifier: MIT

#pragma once

// Micropython setup

#define MICROPY_HW_BOARD_NAME       "Heltec Vision Master E213"
#define MICROPY_HW_MCU_NAME         "ESP32S3"

#define CIRCUITPY_BOOT_BUTTON (&pin_GPIO0)

// Opt into real light sleep. Requires patches/0001-opt-in-real-light-sleep.patch.
// Without the patch this define does nothing; with it,
// alarm.light_sleep_until_alarms() calls esp_light_sleep_start() instead of
// spinning on WFI (measured 43 mA) -- at the cost of tearing down every non-RTC
// peripheral, so SPI/I2C/PWM/keypad objects must be rebuilt after each wake.
// This MUST live here and not in mpconfigboard.mk: the espressif Makefile has no
// CFLAGS_BOARD, so a define there is silently dropped.
//
// Left OFF by default so an unrelated rebuild cannot hand you a firmware whose
// peripherals silently die across every sleep. build/firmware-lightsleep.bin was
// produced with this line uncommented.
#define CIRCUITPY_ESP_REAL_LIGHT_SLEEP (1)

// Two SPI buses, and the pin assignment is where the E213 diverges from the
// E290. The panel bus differs on every single pin; only the LoRa bus matches.
//   E213 EPD: SCK 4, MOSI 6   (CS 5, DC 2, RST 3, BUSY 1)
//   E290 EPD: SCK 2, MOSI 1   (CS 3, DC 4, RST 5, BUSY 6)
#define CIRCUITPY_BOARD_SPI         (2)
#define CIRCUITPY_BOARD_SPI_PIN     { \
        {.clock = &pin_GPIO4, .mosi = &pin_GPIO6, .miso = NULL}, \
        {.clock = &pin_GPIO9, .mosi = &pin_GPIO10, .miso = &pin_GPIO11}, \
}

#define CIRCUITPY_BOARD_I2C         (1)
#define CIRCUITPY_BOARD_I2C_PIN     { \
        {.scl = &pin_GPIO38, .sda = &pin_GPIO39}, \
}

#define DEFAULT_UART_BUS_TX         (&pin_GPIO43)
#define DEFAULT_UART_BUS_RX         (&pin_GPIO44)
