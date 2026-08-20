USB_VID = 0x303A
# 0x82CA is unused across every board in this CircuitPython checkout, but it is
# NOT registered with Adafruit. Request a real PID before upstreaming this board.
USB_PID = 0x82CA
USB_PRODUCT = "Vision Master E213"
USB_MANUFACTURER = "Heltec"

IDF_TARGET = esp32s3

# N16R8: 16MB SiP flash, 8MB octal PSRAM.
#
# 16MB is from Heltec's own product page ("16MB SiP Flash"). Meshtastic builds
# this board with an 8MB partition scheme, which is merely them underusing the
# chip -- it is not evidence of an 8MB part.
#
# The 8MB of PSRAM is confirmed directly on hardware: under the stock n16r8
# build gc.mem_free() reported 8,133,152 bytes and a fresh bytearray landed at
# 0x3c1b15c0, inside the S3's external-RAM window (0x3C000000-0x3DFFFFFF).
# Heltec's spec table lists only the SoC's internal 512KB SRAM and omits it.
CIRCUITPY_ESP_FLASH_SIZE = 16MB
CIRCUITPY_ESP_FLASH_MODE = qio
CIRCUITPY_ESP_FLASH_FREQ = 80m

CIRCUITPY_ESP_PSRAM_SIZE = 8MB
CIRCUITPY_ESP_PSRAM_MODE = opi
CIRCUITPY_ESP_PSRAM_FREQ = 80m

# --- Optional: build without PSRAM -------------------------------------------
# Comment out the three PSRAM lines above to put the CircuitPython heap in
# internal SRAM instead. Measured on this board, for the e-reader workload:
#
#   page render   0.41 s -> 0.17 s   (internal SRAM is ~2.4x faster than OPI PSRAM)
#   idle current  50 mA  -> 49 mA    (PSRAM is worth only ~1 mA)
#
# The e-reader fits comfortably in the ~200-300KB internal heap; it ran unchanged
# on the espressif_esp32s3_devkitc_1_n8 build. Unless an application actually
# needs megabytes of RAM, no PSRAM is the better configuration here.
