# ------------------------------------------------------------
# uzipfile.py  -  pure-Python ZIP reader (CircuitPython port)
# ------------------------------------------------------------
# Ported from the MicroPython version in the repository root. The only real
# difference is decompression: MicroPython has `deflate.DeflateIO`, a streaming
# inflater, which CircuitPython does not - its `zlib` module offers only
# `zlib.decompress(data, wbits)`. So a DEFLATE member has to be decompressed
# whole into memory instead of being streamed.
#
# That matters for memory, and shapes how this is used:
#   * stored members (method 0) are still streamed, so extracting a cover image
#     - which EPUBs almost always store uncompressed, being JPEG already -
#     costs only the copy buffer
#   * deflated members are held in RAM while they are read, so the converter is
#     meant to be run standalone, without the reader's buffers loaded
import struct
import zlib
from io import BytesIO

# Imported here rather than inside the fallback. Importing a module allocates
# (this one costs ~14KB for its code objects and tables), and the fallback is
# reached precisely when memory is tight - so a lazy import fails exactly when
# it is needed. That is what stopped a large cover being extracted.
from inflate import RawInflater


BUILD = "uzipfile-2 small-eocd-scan"


class FileSliceReader:
    """Streaming reader over a slice of the archive (for stored members)."""

    def __init__(self, fp, start, size):
        self.fp = fp
        self.pos = start
        self.end = start + size
        self.fp.seek(start)

    def read(self, size=-1):
        if size < 0:
            size = self.end - self.pos
        remaining = self.end - self.pos
        if size > remaining:
            size = remaining
        if size <= 0:
            return b""
        data = self.fp.read(size)
        self.pos += len(data)
        return data

    def close(self):
        pass  # fp is shared, don't close it


class UZipFile:
    """Read-only ZIP archive supporting stored (0) and DEFLATE (8) members."""

    def __init__(self, filename, window=0):
        self.fp = open(filename, "rb")

        # Take the streaming window BEFORE anything else is allocated, while
        # the heap is still in one piece. It is long-lived, so putting it down
        # first keeps it out of the way of everything that follows; carving it
        # out later means taking 32KB from whatever the largest remaining block
        # happens to be, which is exactly the space big members need.
        self._window = bytearray(window) if window else None

        self.filelist = self._read_central_directory()
        # Reused buffer for reading compressed members. Allocating a fresh one
        # per chapter leaves a differently-sized hole each time, and the
        # collector does not move objects to close them up - which is what
        # eventually leaves no block big enough for zlib.decompress's output.
        self._zbuf = None

    def reserve_read_buffer(self, size):
        """Place the compressed-read buffer early, at a known size.

        Otherwise it is created the first time something is read - in the
        middle of the heap - and kept for reuse, permanently splitting the free
        space in two. Sized once here, it sits alongside the window and leaves
        the rest of the heap in one piece.
        """
        if size and (self._zbuf is None or len(self._zbuf) < size):
            try:
                self._zbuf = bytearray(size)
            except MemoryError:
                pass
        return self._zbuf

    def ensure_window(self, size=32768):
        """Preallocate the streaming inflater's window.

        Worth calling straight after opening the archive, while the heap is
        still whole. By the time a big chapter actually fails, the largest free
        block can already be under 32KB - so allocating the window on demand
        would fail exactly when it is needed.
        """
        if self._window is None:
            self._window = bytearray(size)
        return self._window

    def _inflate_stream(self, data_start, csize):
        """Streaming inflater over a member, for output too big to hold whole."""
        return RawInflater(FileSliceReader(self.fp, data_start, csize),
                           window=self.ensure_window())

    def _read_compressed(self, data_start, size):
        """Read `size` compressed bytes, reusing one buffer where possible."""
        self.fp.seek(data_start)
        if self._zbuf is None or len(self._zbuf) < size:
            try:
                self._zbuf = bytearray(size)
            except MemoryError:
                self._zbuf = None
                return self.fp.read(size)       # fall back to a fresh read
        try:
            view = memoryview(self._zbuf)[:size]
            got = self.fp.readinto(view)
            if got == size:
                return view
        except Exception:
            pass
        self.fp.seek(data_start)
        return self.fp.read(size)

    # -----------------------------------------------------------------
    def _read_central_directory(self):
        self.fp.seek(0, 2)
        file_size = self.fp.tell()

        # ---- find End Of Central Directory (EOCD) -----------------
        # The EOCD is the last 22 bytes, plus any archive comment. A comment
        # can in principle be 64KB, but EPUBs have none - so scan backwards in
        # steps rather than pulling the 64KB worst case in. That read was the
        # single largest allocation the converter made, bigger than any chapter,
        # and it happened before anything else had a chance to settle.
        eocd_start = -1
        span = 0
        for want in (1024, 4096, 16384, 65557):
            if want <= span:
                continue
            span = want if want < file_size else file_size
            self.fp.seek(file_size - span)
            tail = self.fp.read(span)
            pos = tail.rfind(b"\x50\x4b\x05\x06")
            if pos >= 0:
                eocd_start = file_size - span + pos
                break
            tail = None                      # release before growing the scan
            if span >= file_size:
                break

        if eocd_start < 0:
            raise OSError("Not a valid ZIP file (EOCD missing)")
        self.fp.seek(eocd_start + 16)
        cd_offset = struct.unpack("<I", self.fp.read(4))[0]

        # ---- read Central Directory entries -----------------------
        self.fp.seek(cd_offset)
        files = []

        while True:
            header = self.fp.read(46)
            if len(header) < 46 or header[:4] != b"\x50\x4b\x01\x02":
                break

            comp_method, = struct.unpack("<H", header[10:12])
            comp_size, = struct.unpack("<I", header[20:24])
            uncomp_size, = struct.unpack("<I", header[24:28])
            name_len, = struct.unpack("<H", header[28:30])
            extra_len, = struct.unpack("<H", header[30:32])
            comment_len, = struct.unpack("<H", header[32:34])
            lfh_offset, = struct.unpack("<I", header[42:46])

            name = self.fp.read(name_len).decode("utf-8")
            self.fp.seek(extra_len + comment_len, 1)   # skip

            files.append({
                "filename": name,
                "compression_method": comp_method,
                "compressed_size": comp_size,
                "uncompressed_size": uncomp_size,
                "lfl_offset": lfh_offset,
            })

        return files

    # -----------------------------------------------------------------
    def namelist(self):
        return [f["filename"] for f in self.filelist]

    def entry_for(self, member):
        for f in self.filelist:
            if f["filename"] == member:
                return f
        return None

    # -----------------------------------------------------------------
    def _get_entry(self, member):
        entry = self.entry_for(member)
        if entry is None:
            raise KeyError(member)

        # ---- go to Local File Header -------------------------------
        self.fp.seek(entry["lfl_offset"])
        lfh = self.fp.read(30)
        name_len, = struct.unpack("<H", lfh[26:28])
        extra_len, = struct.unpack("<H", lfh[28:30])

        data_start = entry["lfl_offset"] + 30 + name_len + extra_len
        return entry, data_start

    # -----------------------------------------------------------------
    def read(self, member):
        """Whole member as bytes. Only for small files - the OPF, container.xml."""
        entry, data_start = self._get_entry(member)

        if entry["compression_method"] == 0:
            self.fp.seek(data_start)
            return self.fp.read(entry["compressed_size"])

        if entry["compression_method"] == 8:
            compressed = self._read_compressed(data_start, entry["compressed_size"])
            # negative wbits selects raw DEFLATE (no zlib header), which is
            # what ZIP stores
            try:
                return zlib.decompress(compressed, -15)
            except TypeError:
                # some builds want a real bytes object, not a memoryview
                return zlib.decompress(bytes(compressed), -15)

        raise NotImplementedError(
            "Compression method %d not supported" % entry["compression_method"])

    # -----------------------------------------------------------------
    def get_reader(self, member):
        """A reader with .read(size)/.close() for the member.

        Stored members stream straight off the archive. Deflated ones are
        decompressed in full first, because CircuitPython has no streaming
        inflater - so the caller still reads in chunks, but the memory has
        already been spent.
        """
        entry, data_start = self._get_entry(member)

        if entry["compression_method"] == 0:
            return FileSliceReader(self.fp, data_start, entry["compressed_size"])

        if entry["compression_method"] == 8:
            # zlib is far quicker, but it returns the whole member as one
            # object; when no block that big is free, inflate it in a stream
            # instead, which only needs the window.
            try:
                compressed = self._read_compressed(data_start, entry["compressed_size"])
                try:
                    return BytesIO(zlib.decompress(compressed, -15))
                except TypeError:
                    return BytesIO(zlib.decompress(bytes(compressed), -15))
            except MemoryError:
                return self._inflate_stream(data_start, entry["compressed_size"])

        raise NotImplementedError(
            "Compression method %d not supported" % entry["compression_method"])

    # -----------------------------------------------------------------
    def extract_to(self, member, dest_path, chunk=1024):
        """Write a member out to `dest_path`.

        Stored members are copied a chunk at a time and never held whole in
        memory, which is the case that matters: cover images are already
        compressed, so EPUBs normally store them rather than deflate them.
        """
        entry, data_start = self._get_entry(member)

        if entry["compression_method"] == 0:
            self.fp.seek(data_start)
            remaining = entry["compressed_size"]
            with open(dest_path, "wb") as out:
                while remaining > 0:
                    buf = self.fp.read(chunk if chunk < remaining else remaining)
                    if not buf:
                        break
                    out.write(buf)
                    remaining -= len(buf)
        else:
            # Going straight to a file, so there is nothing to gain from
            # decompressing it whole - and doing so would need the compressed
            # AND uncompressed sizes at once. A cover is barely compressible,
            # so that is roughly twice its size in two big blocks. Stream it.
            if entry["uncompressed_size"] > 16384 and self._window is not None:
                reader = self._inflate_stream(data_start, entry["compressed_size"])
            else:
                reader = self.get_reader(member)  # streams if it has to
            try:
                with open(dest_path, "wb") as out:
                    while True:
                        buf = reader.read(chunk)
                        if not buf:
                            break
                        out.write(buf)
            finally:
                try:
                    reader.close()
                except Exception:
                    pass
        return True

    # -----------------------------------------------------------------
    def close(self):
        self.fp.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
