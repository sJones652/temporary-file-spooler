# temporary-file-spooler

A binary file-like object that buffers writes in memory and transparently spills to a
temp file once the data exceeds a threshold.

## Usage

```python
from temporary_file_spooler import SpooledTemporaryFile

with SpooledTemporaryFile(max_size=1024) as f:
    f.write(b"small payload")
    f.seek(0)
    data = f.read()
    print(f.rolled)  # False — stayed in memory

with SpooledTemporaryFile(max_size=4) as f:
    f.write(b"hello world")
    print(f.rolled)  # True — spilled to a NamedTemporaryFile
    f.seek(0)
    print(f.read())  # b'hello world'
```

## Why

When you want to accept an unknown amount of binary data from a caller and you'd
prefer to avoid disk I/O for small payloads, but you also can't risk unbounded
memory growth. The standard library's `tempfile.SpooledTemporaryFile` exists
for this, but its API surface and roll semantics differ enough from a plain
file that wrapping it cleanly is awkward. This library is a from-scratch
implementation with a simpler contract: the threshold is a byte count, the roll
is one-way, and the object is a binary file end-to-end.

## Edge cases worth knowing

- **The roll is one-way.** If a write pushes you over the threshold the data
  moves to disk and stays there. Truncating back below the threshold does not
  pull it back into memory. This keeps the state machine simple and predictable.
- **`max_size=0` rolls on the first non-empty write.** An empty write does not
  trigger a roll.
- **`max_size=None` means "never roll."** The buffer grows without bound. This
  is useful when you want a pure in-memory buffer with a file-like interface.
  Calling `fileno()` still forces a roll, because a real file descriptor
  requires a real file.
- **Seeking past the end and writing NUL-fills the gap**, matching filesystem
  behaviour. This works in both the in-memory and on-disk states.
- **Only binary data is accepted.** Passing `str` to `write` raises `TypeError`;
  the library does not guess an encoding.

## Exports

- `SpooledTemporaryFile(max_size=None, *, dir=None, prefix=None, suffix=None)`

Implements: `read`, `readline`, `readlines`, `write`, `seek`, `tell`,
`truncate`, `flush`, `fileno`, `close`, `writable`, `readable`, `seekable`,
`rolled`, `closed`, and the context-manager and iterator protocols.

## Design notes

The window stores values eagerly rather than keeping running aggregates. Running
sums drift with floating point over long streams, and recomputing from a small
buffer is cheap enough that the drift is not worth the speed.

