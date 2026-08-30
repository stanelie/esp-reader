#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a ready-to-copy, precompiled Badger 2040 release.

    python3 tools/build_badger_release.py [--mpy-cross PATH] [--version X.Y]

Produces build/badger-release/<version>/ (the exact contents to copy onto the
CIRCUITPY drive's root) and a zip of the same next to it.

Why precompiled: device/code.py is compiled from source on every boot -
CircuitPython has no choice, since the file it runs at startup can never be
.mpy. Splitting the reader out into an importable module and shipping that
precompiled skips the compile step on every deep-sleep wake, which on the
Badger is a real reboot. Measured on this project's own hardware: compiling
just the main file cut wake-to-ready by about a third; precompiling the five
lib modules it imports at boot (hyphenator, propfont, fbrotate, bookmarks,
uc8151badger) cut it by close to half again.

What stays as source, and why:
  code.py, boot.py       CircuitPython only ever runs these as source
  gotoui.py, menufast.py lazy-loaded (menu / jump-to), never touch boot time
  convboot.py and the    lazy-loaded (EPUB conversion), same reason
    EPUB conversion chain
The E213/E290 drivers (lcmen2r13efc1.py, ssd1680e290.py) are left out
entirely - the Badger never imports them, and shipping them would just be
dead weight on this board's drive.
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEVICE = os.path.join(ROOT, "device")
LIB = os.path.join(DEVICE, "lib")

# Compiled to .mpy: everything the Badger actually imports at boot.
PRECOMPILE = ["hyphenator", "propfont", "fbrotate", "bookmarks", "uc8151badger"]

# Left as source: lazily imported, never touched during boot/wake.
KEEP_SOURCE = ["convboot", "convertui", "epub_xtract", "gotoui", "inflate",
               "menufast", "uzipfile"]

SHIM = '''# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# The shim CircuitPython insists on.
#
# The reader itself is lib/ereader.mpy, precompiled with mpy-cross, so this
# boot never pays to parse and compile the reader's ~2700 lines of source.
# CircuitPython will only accept source for the file it runs at startup -
# which is why this file is kept to nothing else.
import ereader        # noqa: F401  - importing it runs the reader
'''


def find_mpy_cross():
    candidates = [
        os.path.join(os.path.expanduser("~"), "circuitpython", "mpy-cross",
                     "build", "mpy-cross"),
        shutil.which("mpy-cross") or "",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def compile_mpy(mpy_cross, src, dst):
    r = subprocess.run([mpy_cross, src, "-o", dst], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("mpy-cross failed on %s:\n%s" % (src, r.stderr))


def build(out_dir, mpy_cross):
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, "lib"))
    os.makedirs(os.path.join(out_dir, "fonts"))

    shutil.copy2(os.path.join(DEVICE, "boot.py"), out_dir)
    with open(os.path.join(out_dir, "code.py"), "w") as f:
        f.write(SHIM)

    compile_mpy(mpy_cross, os.path.join(DEVICE, "code.py"),
                os.path.join(out_dir, "lib", "ereader.mpy"))
    for name in PRECOMPILE:
        compile_mpy(mpy_cross, os.path.join(LIB, name + ".py"),
                    os.path.join(out_dir, "lib", name + ".mpy"))
    for name in KEEP_SOURCE:
        shutil.copy2(os.path.join(LIB, name + ".py"), os.path.join(out_dir, "lib"))
    shutil.copy2(os.path.join(LIB, "adafruit_framebuf.mpy"), os.path.join(out_dir, "lib"))

    for name in ("hyphen_en.bin", "font5x8.bin", "demobook.txt"):
        shutil.copy2(os.path.join(DEVICE, name), out_dir)
    for entry in os.listdir(os.path.join(DEVICE, "fonts")):
        if entry.endswith(".pf"):
            shutil.copy2(os.path.join(DEVICE, "fonts", entry),
                         os.path.join(out_dir, "fonts", entry))
    shutil.copy2(os.path.join(ROOT, "LICENSE"), out_dir)

    with open(os.path.join(out_dir, "README.txt"), "w") as f:
        f.write(
            "Badger 2040 e-reader - precompiled release\n"
            "===========================================\n\n"
            "Install: copy everything in this folder to the root of the\n"
            "CIRCUITPY drive (replacing any existing code.py/boot.py/lib),\n"
            "add your own .txt or .epub books, and reset the board.\n\n"
            "This is a build artifact, not source - lib/*.mpy are compiled\n"
            "from https://github.com/stanelie/esp-reader (device/lib/*.py),\n"
            "precompiled so the Badger doesn't recompile ~2700 lines of\n"
            "source on every boot and deep-sleep wake. To modify the reader,\n"
            "edit the source there and rebuild with\n"
            "tools/build_badger_release.py.\n\n"
            "Licensed GPL-3.0-or-later; see LICENSE. Third-party file\n"
            "provenance is listed in device/README.md in the source repo.\n"
        )


def make_zip(out_dir, zip_path):
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, _dirs, files in os.walk(out_dir):
            for fn in files:
                full = os.path.join(base, fn)
                zf.write(full, os.path.relpath(full, out_dir))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mpy-cross", default=None)
    ap.add_argument("--version", default="dev")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "badger-release"))
    args = ap.parse_args()

    mpy_cross = args.mpy_cross or find_mpy_cross()
    if not mpy_cross:
        raise SystemExit(
            "mpy-cross not found. Build it (see circuitpython/mpy-cross) and "
            "pass --mpy-cross /path/to/mpy-cross, or put it on PATH.")

    r = subprocess.run([mpy_cross, "--version"], capture_output=True, text=True)
    print("Using: %s" % r.stdout.strip())

    stage = os.path.join(args.out, "badger-%s" % args.version)
    build(stage, mpy_cross)

    zip_path = os.path.join(args.out, "badger-%s.zip" % args.version)
    make_zip(stage, zip_path)

    size = sum(os.path.getsize(os.path.join(b, f))
               for b, _d, fs in os.walk(stage) for f in fs)
    print("Built %s (%d files, %.1f KB unpacked)" %
          (stage, sum(len(fs) for _b, _d, fs in os.walk(stage)), size / 1024))
    print("Zip: %s (%.1f KB)" % (zip_path, os.path.getsize(zip_path) / 1024))


if __name__ == "__main__":
    main()
