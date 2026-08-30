# esp-reader

A streaming e-book reader for the Heltec Vision Master **E213** and **E290**,
in CircuitPython, with the firmware work needed to make it sip power.

One `code.py` runs on both boards. Of ~1150 lines, eleven differ between them —
six e-paper pins, the panel rotation, the driver class, and two battery
calibration constants — so the differences live in a table at the top of the
file and the board is detected at boot from `board.board_id`.

```
device/            copy the CONTENTS of this to the CIRCUITPY drive
  code.py          the reader
  boot.py
  lib/             both panel drivers, bookmarks, Adafruit deps
  fonts/           subset Literata, PCF and BDF
firmware/
  install.sh       symlink both boards into a CircuitPython checkout
  boards/          the two board definitions
  patches/         the real-light-sleep patch
tools/             font subsetting and BDF -> PCF conversion
```

## Quick start

Flash the image for your board from the [Releases page](https://github.com/stanelie/esp-reader/releases)
at **`0x0`**, copy the contents of `device/` to CIRCUITPY, drop in a `.txt`
book, reset. See [`firmware/README.md`](firmware/README.md) and
[`device/README.md`](device/README.md).

## What it does

Reads plain `.txt`, and converts `.epub` on the device to the same. Streams
pages straight out of the file rather than loading it, so book size costs
nothing. Reading positions live in NVM, per book, and survive a flat
battery. One button drives everything by escalating hold — tap turns the page,
double-tap goes back, hold opens the library, hold longer opens jump-to-percent,
hold longer still sleeps. Neighbouring pages are pre-rendered during the idle
after a turn, so a page turn is one partial refresh and nothing else.

## Power

Measured on the E213 with a PPK2, USB disconnected, 4 V into the battery input:

| state | current |
|---|---|
| running, 240 MHz | 68.1 mA |
| light sleep, stock CircuitPython | 43.2 mA |
| light sleep, patched | **1.1 mA** |
| deep sleep | 16.4 µA |

Stock CircuitPython does not power-gate in light sleep — it spins in a WFI loop,
and upstream says so in a comment. The patch in `firmware/patches/` makes
`alarm.light_sleep_until_alarms()` call `esp_light_sleep_start()` instead, which
is a 39x reduction and the reason the reader sleeps between every page turn
rather than staying awake.

The cost is that every non-RTC peripheral is torn down across a sleep, so the
display bus and the keypad are rebuilt on each wake — and **USB does not come
back until a reset-class event**. The reader refuses to sleep at all while a
cable is attached, which is what keeps a bad edit recoverable.

## Things that cost real time to find

- The E213 and E290 panels share **no** commands. `0x12` is *refresh* on one and
  *soft reset* on the other. Do not port display code between them.
- The E290 has no differential waveform in OTP: asking for a partial update by
  mode byte alone silently performs a full flashing refresh. The 153-byte LUT
  must be uploaded first — and a full refresh wipes it again.
- `0x22 = 0xF7` reloads the LUT from OTP. Re-upload before the next partial or
  the first page turn after every full refresh flashes.
- The E290 panel's BUSY is asserted **high**; the E213's is asserted low.
  Getting it backwards fails silently as torn refreshes, not as an error.
- Vext is GPIO18 on both. It is GPIO21 on the *classic* Heltec WiFi LoRa 32
  boards, so that number is all over the forums — and on these boards GPIO21 is
  a button and a wake pin.
- `CIRCUITPY_ESP_REAL_LIGHT_SLEEP` must be defined in `mpconfigboard.h`, not
  `mpconfigboard.mk`. The espressif Makefile has no `CFLAGS_BOARD`, so a define
  there is dropped without a word and you get a firmware that looks right and
  still spins on WFI.

## Licence

**GPL-3.0-or-later.** Full text in [`LICENSE`](LICENSE); every source file
carries an SPDX header.

The deciding factor is `device/lib/ssd1680e290.py`. The file is original except
for its 153-byte partial-update LUT, which was transcribed from
[GxEPD2](https://github.com/ZinggJM/GxEPD2) (GPL-3.0-or-later) — and that table
is not incidental, it is what makes partial refresh work on this panel at all.
Whether a waveform table carries copyright is arguable, but GPL settles it
either way rather than resting the whole repository on the argument. If you want
that driver under a permissive licence, re-derive the waveform from the SSD1680
datasheet; nothing else in the file is encumbered.

Everything the GPL is layered over stays under its own terms, and all of it is
GPL-compatible:

- `device/lib/lcmen2r13efc1.py` — MIT, a port of
  [todd-herbert/heltec-eink-modules](https://github.com/todd-herbert/heltec-eink-modules).
  It contains no GPL material and keeps its MIT header.
- Adafruit libraries — MIT. `firmware/patches/` modifies CircuitPython — MIT.
- Literata and Open Sans — SIL Open Font License 1.1. DejaVu Sans — Bitstream
  Vera derivative. The `.pf` files are rasterised subsets, and both licences
  permit that.
- The hyphenation patterns are Knuth-Liang plus ushyphmax — public domain.

Per-file table in [`device/README.md`](device/README.md).

**Do not commit books.** `.gitignore` excludes `device/*.txt` for that reason.
