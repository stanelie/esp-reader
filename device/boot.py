# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later

# import board
# import digitalio
# import storage
# import usb_cdc
# import usb_hid
import supervisor
# 
# # Setup GPIO 0 (BOOT button)
# boot_pin = digitalio.DigitalInOut(board.GPIO21)
# boot_pin.direction = digitalio.Direction.INPUT
# boot_pin.pull = digitalio.Pull.UP
# 
# # Check if button is pressed (LOW / False)
# is_pressed = not boot_pin.value
# 
# if not is_pressed:
#     # Normal Deployment Mode: Disable USB to enable true low-power Light Sleep
#     storage.disable_usb_drive()
#     usb_cdc.disable()
#     usb_hid.disable()
# else:
#     # Maintenance / Debug Mode: Keep USB enabled when BOOT is held
#     pass

supervisor.runtime.autoreload = False
