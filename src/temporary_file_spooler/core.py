"""A memory-buffered temporary file that spills to disk past a threshold.

The buffer is a bytearray while data fits in memory; once a write pushes
the total past the threshold, the buffer is flushed to a NamedTemporaryFile
and all subsequent operations go straight to disk. The spill is one-way:
shrinking the file below the threshold does not pull it back into memory.
That keeps the implementation honest about its state machine and avoids
surprises where a seek-and-truncate silently changes the backing store.
"""

from __future__ import annotations

import os
import tempfile
from typing import IO, Any, BinaryIO, Iterable, Optional


class SpooledTemporaryFile:
    """A binary temporary file that starts in memory and rolls to disk.

    The object behaves like a binary file (read, write, seek, tell,
    truncate, close, context-manager). While the cumulative byte count is
    at or below ``max_size`` the data lives in an in-memory ``bytearray``;
    once a write exceeds ``max_size`` the contents are moved to a
    ``tempfile.NamedTemporaryFile`` and the memory buffer is released.

    Parameters
    ----------
    max_size:
        Soft ceiling in bytes. A single write that crosses the ceiling
        triggers the roll. ``max_size=0`` rolls on the first non-empty
        write. ``None`` means "never roll" — the file stays in memory
        forever, which is useful when you want a pure in-memory buffer
        with a file-like interface.
    dir, prefix, suffix:
        Forwarded to :func:`tempfile.NamedTemporaryFile` when the roll
        happens. Ignored if no roll ever occurs.
    """

    def __init__(
        self,
        max_size: Optional[int] = None,
        *,
        dir: Optional[str] = None,
        prefix: Optional[str] = None,
        suffix: Optional[str] = None,
    ) -> None:
        if max_size is not None and max_size < 0:
            raise ValueError("max_size must be non-negative or None")
        self._max_size = max_size
        self._dir = dir
        self._prefix = prefix
        self._suffix = suffix

        self._buffer: Optional[bytearray] = bytearray()
        self._file: Optional[IO[bytes]] = None
        self._pos = 0
        self._rolled = False
        self._closed = False

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _check_open(self) -> None:
        if self._closed:
            raise ValueError("I/O operation on closed file.")

    def _roll_to_disk(self) -> None:
        """Move the in-memory buffer to a real temp file.

        Called only when ``self._buffer`` is still the active backing
        store. After this returns, ``self._buffer`` is ``None`` and
        ``self._file`` is an open ``NamedTemporaryFile`` positioned at
        the same offset the buffer was.
        """
        assert self._buffer is not None and self._file is None
        data = bytes(self._buffer)
        pos = self._pos
        self._file = tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=self._dir,
            prefix=self._prefix,
            suffix=self._suffix,
            delete=True,
        )
        self._file.write(data)
        self._file.seek(pos)
        self._buffer = None
        self._rolled = True

    def _maybe_roll(self, prospective_size: int) -> None:
        """Roll to disk if *prospective_size* exceeds the threshold.

        ``prospective_size`` is the size the backing store would have
        after the pending write. We compare against ``_max_size`` with a
        strict greater-than so that a buffer exactly at the threshold
        stays in memory — the threshold is "how much can I hold", not
        "how much triggers a roll one byte earlier".
        """
        if (
            self._buffer is not None
            and self._max_size is not None
            and prospective_size > self._max_size
        ):
            self._roll_to_disk()

    # ------------------------------------------------------------------
    # file-like API
    # ------------------------------------------------------------------

    @property
    def rolled(self) -> bool:
        """True once the data has been moved to a real temp file."""
        return self._rolled

    @property
    def closed(self) -> bool:
        return self._closed

    def write(self, data: AnyStr) -> int:
        """Write bytes (or a bytearray/memoryview) and return the count.

        Accepts ``bytes``-like objects. A ``str`` is rejected with
        ``TypeError`` — this is a binary file; accepting text and
        silently encoding it would hide bugs in callers.
        """
        self._check_open()
        if isinstance(data, str):
            raise TypeError("write() requires bytes-like object, got str")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(
                "write() requires bytes-like object, got %s" % type(data).__name__
            )
        chunk = bytes(data)
        n = len(chunk)
        if n == 0:
            return 0

        if self._file is not None:
            self._file.write(chunk)
            self._pos = self._file.tell()
            return n

        assert self._buffer is not None
        self._maybe_roll(len(self._buffer) + n)

        if self._file is not None:
            # roll happened; write through to the file
            self._file.write(chunk)
            self._pos = self._file.tell()
        else:
            if self._pos > len(self._buffer):
                self._buffer.extend(b"\x00" * (self._pos - len(self._buffer)))
            self._buffer[self._pos : self._pos + n] = chunk
            self._pos += n
        return n

    def read(self, size: int = -1) -> bytes:
        """Read up to *size* bytes, or all remaining if ``size < 0``."""
        self._check_open()
        if self._file is not None:
            data = self._file.read(-1 if size is None else size)
            self._pos = self._file.tell()
            return data

        assert self._buffer is not None
        remaining = len(self._buffer) - self._pos
        if size is None or size < 0 or size > remaining:
            size = remaining
        data = bytes(self._buffer[self._pos : self._pos + size])
        self._pos += size
        return data

    def readline(self, size: int = -1) -> bytes:
        """Read a single line, bounded by *size* if non-negative."""
        self._check_open()
        if self._file is not None:
            data = self._file.readline(-1 if size is None else size)
            self._pos = self._file.tell()
            return data

        assert self._buffer is not None
        start = self._pos
        nl = self._buffer.find(b"\n", start)
        if nl == -1:
            end = len(self._buffer)
        else:
            end = nl + 1
        if size is not None and size >= 0:
            end = min(end, start + size)
        if end <= start:
            return b""
        data = bytes(self._buffer[start:end])
        self._pos = end
        return data

    def readlines(self, hint: int = -1) -> list:
        """Read all lines into a list.

        *hint* is accepted for API compatibility with regular files;
        a non-negative hint bounds the total bytes returned.
        """
        self._check_open()
        lines: list = []
        total = 0
        while True:
            line = self.readline()
            if not line:
                break
            lines.append(line)
            total += len(line)
            if hint is not None and hint >= 0 and total >= hint:
                break
        return lines

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek within the file. *whence* follows :data:`os.SEEK_*`."""
        self._check_open()
        if self._file is not None:
            pos = self._file.seek(offset, whence)
            self._pos = pos
            return pos

        assert self._buffer is not None
        length = len(self._buffer)
        if whence == os.SEEK_SET:
            new = offset
        elif whence == os.SEEK_CUR:
            new = self._pos + offset
        elif whence == os.SEEK_END:
            new = length + offset
        else:
            raise ValueError("invalid whence: %r" % (whence,))
        if new < 0:
            raise ValueError("negative seek position %d" % new)
        # Seeking past the end of the in-memory buffer is allowed and
        # leaves a gap that a subsequent write will fill with NULs,
        # matching the behaviour of a real file.
        self._pos = new
        return new

    def tell(self) -> int:
        self._check_open()
        return self._pos

    def truncate(self, size: Optional[int] = None) -> int:
        """Truncate the file to *size* (default: current position)."""
        self._check_open()
        if size is None:
            size = self._pos
        if size < 0:
            raise ValueError("negative truncate size %d" % size)

        if self._file is not None:
            self._file.truncate(size)
            self._pos = self._file.tell()
            return size

        assert self._buffer is not None
        if size < len(self._buffer):
            del self._buffer[size:]
        elif size > len(self._buffer):
            self._buffer.extend(b"\x00" * (size - len(self._buffer)))
        return size

    def flush(self) -> None:
        self._check_open()
        if self._file is not None:
            self._file.flush()

    def writable(self) -> bool:
        return not self._closed

    def readable(self) -> bool:
        return not self._closed

    def seekable(self) -> bool:
        return not self._closed

    def fileno(self) -> int:
        """Return the OS file descriptor, rolling to disk first if needed."""
        self._check_open()
        if self._file is None:
            self._roll_to_disk()
        assert self._file is not None
        return self._file.fileno()

    def close(self) -> None:
        if self._closed:
            return
        if self._file is not None:
            self._file.close()
        self._buffer = None
        self._closed = True

    def __enter__(self) -> "SpooledTemporaryFile":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __iter__(self) -> "SpooledTemporaryFile":
        return self

    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration
        return line
