"""Per-book reading positions kept in the ESP32's NVM.

NVM is small and there is no room for an unbounded list, so positions live in a
fixed ring of slots that churns: books no longer present on the device are
cleared first, and after that the least recently opened slot is evicted.

Books are identified by a 32-bit hash of their path rather than by name, which
keeps a slot to 10 bytes and costs nothing to match - the reader hashes the
files it can actually see and looks for the stored hash among them.

Layout, big-endian:
    0..1    magic
    2       format version
    3       number of slots this layout uses
    4..7    hash of the book being read
    8..9    LRU clock
    10..    slots of 10 bytes: hash (4), offset (4), last-opened tick (2)

A slot whose hash is 0 is free.
"""
import struct

import microcontroller

MAGIC = b"\xEB\x0C"
LEGACY_MAGIC = b"\xEB\x0B"  # the old single-book format: magic + 4-byte offset
VERSION = 1

HEADER_SIZE = 10
SLOT_SIZE = 10
CLOCK_MAX = 0xFFFF


def _is_free(h):
    """Erased NVM reads back as 0xFF (or 0x00), so both mean 'no book here'."""
    return h == 0 or h == 0xFFFFFFFF


def name_hash(name):
    """FNV-1a over the path. Never returns 0, which marks a free slot."""
    h = 0x811C9DC5
    for b in name.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h or 1


class Bookmarks:
    def __init__(self, max_slots=20):
        self.nvm = getattr(microcontroller, "nvm", None)
        self.slots = 0
        if self.nvm is not None and len(self.nvm) >= HEADER_SIZE + SLOT_SIZE:
            capacity = (len(self.nvm) - HEADER_SIZE) // SLOT_SIZE
            self.slots = min(max_slots, capacity)

        self.table = [[0, 0, 0] for _ in range(self.slots)]  # hash, offset, tick
        self.current = 0
        self.clock = 0
        self._read()

    # --- storage ---------------------------------------------------------
    def _read(self):
        if not self.slots:
            return
        try:
            head = bytes(self.nvm[0:HEADER_SIZE])
        except Exception as e:
            print(f"NVM unreadable, positions will not persist: {e}")
            self.slots = 0
            return

        if head[0:2] != MAGIC or head[2] != VERSION:
            return  # blank or legacy; caller may call migrate_legacy()

        self.current = struct.unpack(">I", head[4:8])[0]
        self.clock = struct.unpack(">H", head[8:10])[0]

        stored = min(head[3], self.slots)
        raw = bytes(self.nvm[HEADER_SIZE:HEADER_SIZE + stored * SLOT_SIZE])
        for i in range(stored):
            base = i * SLOT_SIZE
            h, off = struct.unpack(">II", raw[base:base + 8])
            tick = struct.unpack(">H", raw[base + 8:base + 10])[0]
            # Normalise never-written slots in RAM rather than spending writes.
            self.table[i] = [0, 0, 0] if _is_free(h) else [h, off, tick]

    def _write_header(self):
        if not self.slots:
            return
        head = MAGIC + bytes([VERSION, self.slots])
        head += struct.pack(">IH", self.current, self.clock)
        try:
            self.nvm[0:HEADER_SIZE] = head
        except Exception as e:
            print(f"Could not write NVM header: {e}")

    def _write_slot(self, i):
        if not self.slots:
            return
        h, off, tick = self.table[i]
        base = HEADER_SIZE + i * SLOT_SIZE
        try:
            self.nvm[base:base + SLOT_SIZE] = struct.pack(">IIH", h, off, tick)
        except Exception as e:
            print(f"Could not write NVM slot {i}: {e}")

    # --- slot bookkeeping ------------------------------------------------
    def _find(self, h):
        for i in range(self.slots):
            if self.table[i][0] == h:
                return i
        return -1

    def _renumber(self):
        """Compact tick values so the 16-bit clock can keep counting."""
        live = sorted((self.table[i][2], i)
                      for i in range(self.slots) if not _is_free(self.table[i][0]))
        for rank, (_, i) in enumerate(live):
            self.table[i][2] = rank + 1
            self._write_slot(i)
        self.clock = len(live)

    def _allocate(self, h, present_hashes):
        i = self._find(h)
        if i >= 0:
            return i
        for i in range(self.slots):
            if _is_free(self.table[i][0]):
                return i
        # Churn: books that have left the device go before anything still here.
        gone = [i for i in range(self.slots)
                if self.table[i][0] not in present_hashes]
        pool = gone if gone else list(range(self.slots))
        return min(pool, key=lambda i: self.table[i][2])

    # --- public API ------------------------------------------------------
    def get(self, name):
        """Saved offset for name, or 0 if it has no slot."""
        i = self._find(name_hash(name))
        return self.table[i][1] if i >= 0 else 0

    def open(self, name, present=()):
        """Mark name as the book being read; returns its saved offset."""
        if not self.slots:
            return 0

        h = name_hash(name)
        if self.current == h:
            i = self._find(h)
            if i >= 0:
                # Already the book on screen: resuming it costs no flash write.
                return self.table[i][1]

        i = self._allocate(h, tuple(name_hash(n) for n in present))
        slot = self.table[i]
        if slot[0] != h:  # recycled or never-seen book, so start at the top
            slot[0], slot[1] = h, 0

        if self.clock >= CLOCK_MAX:
            self._renumber()
        self.clock += 1
        slot[2] = self.clock

        self._write_slot(i)
        self.current = h
        self._write_header()
        return slot[1]

    def save(self, name, offset):
        """Store a reading position, writing only when it actually changed."""
        if not self.slots:
            return
        i = self._find(name_hash(name))
        if i < 0:
            self.open(name)
            i = self._find(name_hash(name))
            if i < 0:
                return
        if self.table[i][1] != offset:
            self.table[i][1] = offset
            self._write_slot(i)

    def match(self, names):
        """Which of names is the book that was open last, if any."""
        if not self.current:
            return None
        for name in names:
            if name_hash(name) == self.current:
                return name
        return None

    def prune(self, present):
        """Clear slots for books no longer on the device. Returns how many."""
        if not self.slots:
            return 0
        keep = tuple(name_hash(n) for n in present)
        dropped = 0
        for i in range(self.slots):
            if not _is_free(self.table[i][0]) and self.table[i][0] not in keep:
                self.table[i] = [0, 0, 0]
                self._write_slot(i)
                dropped += 1
        return dropped

    def migrate_legacy(self, name):
        """Carry a position written by the old single-book format into a slot."""
        if not self.slots:
            return False
        try:
            head = bytes(self.nvm[0:6])
        except Exception:
            return False
        if head[0:2] != LEGACY_MAGIC:
            return False

        offset = struct.unpack(">I", head[2:6])[0]
        self.table = [[0, 0, 0] for _ in range(self.slots)]
        self.current = 0
        self.clock = 0
        self.open(name)
        self.save(name, offset)
        return True

    def used(self):
        return sum(1 for i in range(self.slots) if not _is_free(self.table[i][0]))
