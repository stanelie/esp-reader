// This file is part of the CircuitPython project: https://circuitpython.org
//
// SPDX-FileCopyrightText: Copyright (c) 2020 Scott Shawcroft for Adafruit Industries
//
// SPDX-License-Identifier: MIT

#include "shared-bindings/board/__init__.h"

CIRCUITPY_BOARD_BUS_SINGLETON(epd_spi, spi, 0)
CIRCUITPY_BOARD_BUS_SINGLETON(lora_spi, spi, 1)

static const mp_rom_map_elem_t board_module_globals_table[] = {
    CIRCUITPYTHON_BOARD_DICT_STANDARD_ITEMS

    // User buttons. GPIO21 is the second button, NOT Vext -- Vext is GPIO21 on
    // the classic Heltec WiFi LoRa 32 boards, which is a common mix-up.
    { MP_ROM_QSTR(MP_QSTR_BUTTON0), MP_ROM_PTR(&pin_GPIO0) },
    { MP_ROM_QSTR(MP_QSTR_BUTTON1), MP_ROM_PTR(&pin_GPIO21) },

    // LED. Not populated on the earliest E213 board revision, so code should
    // tolerate driving a pin that lights nothing.
    // GPIO45 is also an S3 strapping pin (VDD_SPI voltage select, sampled at
    // reset): safe to drive after boot, never add a pull to it.
    { MP_ROM_QSTR(MP_QSTR_LED0), MP_ROM_PTR(&pin_GPIO45) },
    { MP_ROM_QSTR(MP_QSTR_LED), MP_ROM_PTR(&pin_GPIO45) },

    // ADC. With ADC_CTRL high, VBAT reaches ADC_IN through a 390K/100K divider.
    { MP_ROM_QSTR(MP_QSTR_ADC_CTRL), MP_ROM_PTR(&pin_GPIO46) },
    { MP_ROM_QSTR(MP_QSTR_ADC_IN), MP_ROM_PTR(&pin_GPIO7) },
    { MP_ROM_QSTR(MP_QSTR_BATTERY), MP_ROM_PTR(&pin_GPIO7) },

    // VEXT powers the e-paper panel and the 3.3V rail on the I2C QuickLink
    // connector. Active HIGH. On the E213 (unlike the E290) it feeds the
    // QuickLink too, so allow it time to settle before touching I2C.
    { MP_ROM_QSTR(MP_QSTR_VEXT_CTRL), MP_ROM_PTR(&pin_GPIO18) },

    // SX1262 LoRa
    { MP_ROM_QSTR(MP_QSTR_LORA_CS), MP_ROM_PTR(&pin_GPIO8) },
    { MP_ROM_QSTR(MP_QSTR_LORA_SCK), MP_ROM_PTR(&pin_GPIO9) },
    { MP_ROM_QSTR(MP_QSTR_LORA_MOSI), MP_ROM_PTR(&pin_GPIO10) },
    { MP_ROM_QSTR(MP_QSTR_LORA_MISO), MP_ROM_PTR(&pin_GPIO11) },
    { MP_ROM_QSTR(MP_QSTR_LORA_RESET), MP_ROM_PTR(&pin_GPIO12) },
    { MP_ROM_QSTR(MP_QSTR_LORA_BUSY), MP_ROM_PTR(&pin_GPIO13) },
    { MP_ROM_QSTR(MP_QSTR_LORA_DIO1), MP_ROM_PTR(&pin_GPIO14) },

    // e-Paper panel (LCMEN2R13EFC1, 250x122, UC8151-class controller).
    // Every one of these differs from the E290.
    { MP_ROM_QSTR(MP_QSTR_EPD_BUSY), MP_ROM_PTR(&pin_GPIO1) },
    { MP_ROM_QSTR(MP_QSTR_EPD_DC), MP_ROM_PTR(&pin_GPIO2) },
    { MP_ROM_QSTR(MP_QSTR_EPD_RESET), MP_ROM_PTR(&pin_GPIO3) },
    { MP_ROM_QSTR(MP_QSTR_EPD_SCK), MP_ROM_PTR(&pin_GPIO4) },
    { MP_ROM_QSTR(MP_QSTR_EPD_CS), MP_ROM_PTR(&pin_GPIO5) },
    { MP_ROM_QSTR(MP_QSTR_EPD_MOSI), MP_ROM_PTR(&pin_GPIO6) },

    // I2C on the external QuickLink connector (P2), powered from VEXT.
    { MP_ROM_QSTR(MP_QSTR_SDA), MP_ROM_PTR(&pin_GPIO39) },
    { MP_ROM_QSTR(MP_QSTR_SCL), MP_ROM_PTR(&pin_GPIO38) },

    // UART
    { MP_ROM_QSTR(MP_QSTR_TX), MP_ROM_PTR(&pin_GPIO43) },
    { MP_ROM_QSTR(MP_QSTR_RX), MP_ROM_PTR(&pin_GPIO44) },

    // Broken-out pins on the GPIO header
    { MP_ROM_QSTR(MP_QSTR_IO0), MP_ROM_PTR(&pin_GPIO0) },
    { MP_ROM_QSTR(MP_QSTR_IO17), MP_ROM_PTR(&pin_GPIO17) },
    { MP_ROM_QSTR(MP_QSTR_IO38), MP_ROM_PTR(&pin_GPIO38) },
    { MP_ROM_QSTR(MP_QSTR_IO39), MP_ROM_PTR(&pin_GPIO39) },
    { MP_ROM_QSTR(MP_QSTR_IO40), MP_ROM_PTR(&pin_GPIO40) },
    { MP_ROM_QSTR(MP_QSTR_IO41), MP_ROM_PTR(&pin_GPIO41) },
    { MP_ROM_QSTR(MP_QSTR_IO42), MP_ROM_PTR(&pin_GPIO42) },
    { MP_ROM_QSTR(MP_QSTR_IO45), MP_ROM_PTR(&pin_GPIO45) },
    { MP_ROM_QSTR(MP_QSTR_IO47), MP_ROM_PTR(&pin_GPIO47) },
    { MP_ROM_QSTR(MP_QSTR_IO48), MP_ROM_PTR(&pin_GPIO48) },

    // Bus objects. No DISPLAY object is exported: see board.c for why the
    // panel is left to application code rather than displayio.
    { MP_ROM_QSTR(MP_QSTR_I2C), MP_ROM_PTR(&board_i2c_obj) },
    { MP_ROM_QSTR(MP_QSTR_UART), MP_ROM_PTR(&board_uart_obj) },
    { MP_ROM_QSTR(MP_QSTR_SPI), MP_ROM_PTR(&board_epd_spi_obj) },
    { MP_ROM_QSTR(MP_QSTR_EPD_SPI), MP_ROM_PTR(&board_epd_spi_obj) },
    { MP_ROM_QSTR(MP_QSTR_LORA_SPI), MP_ROM_PTR(&board_lora_spi_obj) },
};

MP_DEFINE_CONST_DICT(board_module_globals, board_module_globals_table);
