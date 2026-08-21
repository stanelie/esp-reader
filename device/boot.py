# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Runs before code.py, which is the only time the USB drive can be turned off.
#
# A conversion has to write its .txt, its log and the cover to the filesystem,
# and CircuitPython will not let the device take write access while the host
# can see the drive - storage.remount() raises "Cannot remount path when
# visible via USB". So a queued conversion is read here, from the same NVM
# record code.py uses, and the drive is hidden for that boot only. It comes
# back on the reset that follows the conversion.
#
# Nothing else is disabled: the serial console stays up, which is the only way
# to watch a conversion happen.
import supervisor

supervisor.runtime.autoreload = False

try:
    import microcontroller
    import storage

    _nvm = getattr(microcontroller, "nvm", None)
    if _nvm is not None and _nvm[256] == 0xEC and 0 < _nvm[257] <= 96:
        # Both, and in this order. Hiding the drive stops the host writing to
        # it; the remount is what actually gives the filesystem to the device.
        # Hiding alone is not enough - epub_xtract asks storage.getmount()
        # whether it owns the filesystem, finds it does not, sees that USB is
        # still connected (the serial console keeps usb_connected True even
        # with the drive hidden) and refuses to write rather than risk the
        # host's cached directory overwriting what it produces.
        storage.disable_usb_drive()
        storage.remount("/", readonly=False)
        print("boot.py: conversion queued; drive hidden and filesystem "
              "handed to the device")
except Exception as _e:
    print("boot.py: could not check for a queued conversion: %s" % _e)
