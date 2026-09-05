# Firmware

Both boards run CircuitPython with one patch applied: an opt-in real light
sleep. Everything needed to reproduce that is here.

```
install.sh          symlink both boards into a checkout, apply the patch, report drift
boards/             the two board definitions
patches/            the CircuitPython change
```

Ready-to-flash images live on the [Releases page](https://github.com/stanelie/esp-reader/releases)
now, not in this tree - a merged image is ~1.9 MB and doesn't diff, so it has
no business in git history. Each release carries `SHA256SUMS` covering every
asset attached to it.

## Flashing a prebuilt image

Download `heltec_vision_master_e213.bin` or `heltec_vision_master_e290_lightsleep.bin`
from the release, verify it against `SHA256SUMS`, then:

Hold **BOOT**, tap **RESET**, release BOOT. The board re-enumerates as
`303a:1001`. Then, at address **`0x0`**:

```bash
python3 ~/circuitpython/ports/espressif/esp-idf/components/esptool_py/esptool/esptool.py --chip esp32s3 write_flash 0x0 heltec_vision_master_e290_lightsleep.bin
```

Or <https://esptool.spacehuhn.com/> from Chrome or Edge, no installation.

**`0x0`, not `0x10000`.** These are *merged* images: bootloader at `0x0`,
partition table at `0x8000`, app at `0x10000`, all in one file. `0x10000` is
correct only for the app-only `circuitpython-firmware.bin` left in a build
directory. Verify first with `sha256sum -c SHA256SUMS`.

**Back up the CIRCUITPY drive first.** Flashing has preserved the filesystem
sometimes and reformatted it others, and it holds your books and reading
positions.

## Building

```bash
./install.sh                       # or ./install.sh /path/to/circuitpython
cd ~/circuitpython/ports/espressif
. ./esp-idf/export.sh
make BOARD=heltec_vision_master_e213 -j$(nproc)
make BOARD=heltec_vision_master_e290_lightsleep -j$(nproc)
```

Build with CircuitPython's **bundled** ESP-IDF at `ports/espressif/esp-idf`, not
a system-wide one — CircuitPython pins a specific IDF revision and a mismatch
fails in confusing ways. First time:

```bash
cd ~/circuitpython
make fetch-port-submodules
make -C mpy-cross
cd ports/espressif/esp-idf && ./install.sh esp32s3 && . ./export.sh
```

## Why the boards live here and not in the checkout

`heltec_vision_master_e290_lightsleep` is a fork of a board that **exists
upstream**, differing in one functional line. Editing upstream's copy in place
would mean a git-tracked file that `git pull` or `git checkout` silently
reverts — taking real light sleep with it, without changing anything you would
think to look at. So it is a separate board, symlinked in, with its own name.

That rename pays for itself twice. `boot_out.txt` reports which firmware is on a
device you did not flash five minutes ago, and `code.py` uses the same string to
decide whether light sleep is worth resting in.

`heltec_vision_master_e213` has no upstream equivalent at all.

Everything except `mpconfigboard.h` is a verbatim copy of the upstream E290
board, taken at the commit in `.upstream-base`. `install.sh` diffs them and
prints the `cp` line for anything that has moved, so drift is something you find
on purpose rather than by debugging a stale `board.c`.

## The RP2040 patch (Badger 2040)

`patches/0002-rp2-real-light-sleep.patch` is the same idea for the other
chip: stock CircuitPython's `alarm.light_sleep_until_alarms()` on RP2040
barely sleeps. Measured on a Badger 2040 with a PPK2, one page every 10 s:

| state | stock | patched |
|---|---|---|
| awake, 125 MHz | 25 mA | 25 mA |
| light sleep | 16 mA | **2 mA** |
| reader average, 1 page / 10 s | 18 mA | **5 mA** |

1 mA of that 2 mA is the board itself — regulator, panel, divider — measured
by putting the chip in XOSC dormant and reading what was left. So the chip's
own share went from ~15 mA to ~1 mA, and there is close to nothing else to
win without powering the board down.

Two things were wrong, and fixing only one of them buys 6 mA of the 14:

- **The clock masks gate distribution, not sources.** The port's
  `RP_LIGHTSLEEP_EN0_MASK` gates a handful of peripherals and leaves clk_sys
  running to the rest of the chip, so the floor was "idle at 125 MHz".
  Upstream says so in its own comment: `this only saves about 2mA right now`.
  The patch adds a mask keeping only what the wake path needs, then runs
  clk_sys from the crystal and stops both PLLs.
- **The 1024 Hz supervisor tick keeps firing.** `port_enable_tick()` arms a
  hardware alarm every 977 µs and `_tick_callback` re-arms it, so the core is
  pulled out of WFI a thousand times a second no matter what the masks say.
  The patch stops it for the duration and restores it on every exit path.

**Order matters, and getting it wrong is silent.** `clk_rtc` is fed from
PLL_USB, a TimeAlarm wakes through the RTC alarm, and `clk_rtc` has no
glitchless mux — so lowering the clocks *after* `_setup_sleep_alarms()` has
called `rtc_set_alarm()` leaves the match hardware in a state that never
fires. That failure looks like success: the board sleeps perfectly at 1 mA
and simply never wakes. The clocks are lowered before the alarm is armed for
this reason. The `clk_rtc` divisor is chosen to hold the RTC's rate exactly
(XOSC 12 MHz ÷ 256 = 46875 Hz, the same as PLL_USB 48 MHz ÷ 1024), because
the alarm is an absolute time and a rate change would move it.

PLL_USB is only stopped when USB is not enumerated — it clocks the peripheral
`RUN_BACKGROUND_TASKS` touches on every pass of the sleep loop. The test is
`tud_ready()`, which is exactly what `supervisor.runtime.usb_connected`
reports, so the firmware and any Python-side guard agree by construction.

Opt-in per board, like the ESP one:
`#define CIRCUITPY_RP2_REAL_LIGHT_SLEEP (1)` in `mpconfigboard.h` — the
header, not the `.mk`, for the reason given below.

```bash
cd ~/circuitpython && source venv/bin/activate
export PATH=/opt/arm-gnu-toolchain-14.2.rel1-x86_64-arm-none-eabi/bin:$PATH
cd ports/raspberrypi && make BOARD=pimoroni_badger2040_stan -j$(nproc)
```

Needs **GCC 14+** (CircuitPython refuses to build on 13, which miscompiles
it) and the build venv for `cascadetoml`. Verify the define survived the
same way as the ESP build:

```bash
arm-none-eabi-nm build-pimoroni_badger2040_stan/common-hal/alarm/__init__.o \
  | grep -E "pll_deinit|port_disable_tick"
```

Both should appear as undefined references. Neither does without the define.

## The patch

`patches/0001-opt-in-real-light-sleep.patch` touches one file,
`ports/espressif/common-hal/alarm/__init__.c`, and is board-independent. It adds
a `CIRCUITPY_ESP_REAL_LIGHT_SLEEP` build flag; both board definitions set it.

Upstream declines to power-gate, in so many words:

```c
// We cannot esp_light_sleep_start() here because it shuts down all non-RTC peripherals.
```

so `light_sleep_until_alarms()` spins on WFI at 43 mA. The patch calls
`esp_light_sleep_start()` instead, reaching 1.1 mA.

**A one-line swap is not enough.** It power-gates but over-sleeps by a
non-deterministic multiple of the requested period. `_get_wakeup_cause(false)`
consults only software flags, and the TimeAlarm flag is set from `ESP_TIMER_TASK`
— a separate FreeRTOS task that usually has not run when
`esp_light_sleep_start()` returns. The flag reads clear, the loop re-sleeps, and
the timer is still armed. Upstream never hit this because upstream never sleeps;
the timer is literally named `pretend_sleep_timer`. The patch reads
`esp_sleep_get_wakeup_cause()` instead, which is valid immediately.

The define **must** be in `mpconfigboard.h`. The espressif Makefile has no
`CFLAGS_BOARD`, so putting it in `mpconfigboard.mk` is dropped silently.

## Verifying a build really has it

A dropped define produces a firmware that builds, boots, names itself and still
spins on WFI. Two checks:

```bash
nm build-<board>/common-hal/alarm/__init__.o | grep light_sleep
```

should show undefined references to `esp_light_sleep_start` and
`esp_sleep_get_wakeup_cause`. Neither appears without the define. And on the
E290, `boot_out.txt` should read `Heltec Vision Master E290 (real light sleep)`.

## What real light sleep costs

Every non-RTC peripheral is torn down, so `busio`, `pwmio`, `countio` and
`keypad` objects must be deinit'd before and rebuilt after — never held across a
sleep. And the USB peripheral is gated and never re-initialised: console and
CIRCUITPY drive die on the first sleep, and plugging in afterwards does **not**
bring them back. Only a reset-class event does, which includes any wake from
deep sleep.

`microcontroller.reset()` does not recover USB. The physical RESET button does,
and so does `alarm.exit_and_deep_sleep_until_alarms()` with a short TimeAlarm,
which resets through the RTC controller.

Any script on this firmware needs a guard that refuses to sleep while USB is
attached — without one, a bad edit is unreachable short of erase-and-reflash.
`code.py` has one; bare test scripts need `BOOT_GRACE_SECONDS` instead.
