import io
import os
import unittest

from temporary_file_spooler import SpooledTemporaryFile


class TestConstruction(unittest.TestCase):
    def test_default_max_size_is_none(self):
        # max_size=None means "never roll" — the documented behaviour.
        f = SpooledTemporaryFile()
        f.write(b"x" * 10_000)
        self.assertFalse(f.rolled)
        f.close()

    def test_negative_max_size_rejected(self):
        with self.assertRaises(ValueError):
            SpooledTemporaryFile(max_size=-1)

    def test_max_size_zero_rolls_on_first_write(self):
        f = SpooledTemporaryFile(max_size=0)
        f.write(b"a")
        self.assertTrue(f.rolled)
        f.close()

    def test_max_size_zero_empty_write_does_not_roll(self):
        f = SpooledTemporaryFile(max_size=0)
        f.write(b"")
        self.assertFalse(f.rolled)
        f.close()


class TestWriteAndRead(unittest.TestCase):
    def test_write_returns_byte_count(self):
        f = SpooledTemporaryFile(max_size=1000)
        self.assertEqual(f.write(b"hello"), 5)
        self.assertEqual(f.write(b""), 0)
        f.close()

    def test_write_str_rejected(self):
        f = SpooledTemporaryFile()
        with self.assertRaises(TypeError):
            f.write("hello")
        f.close()

    def test_write_memoryview(self):
        f = SpooledTemporaryFile()
        f.write(memoryview(b"abc"))
        f.seek(0)
        self.assertEqual(f.read(), b"abc")
        f.close()

    def test_write_bytearray(self):
        f = SpooledTemporaryFile()
        f.write(bytearray(b"xyz"))
        f.seek(0)
        self.assertEqual(f.read(), b"xyz")
        f.close()

    def test_read_after_write_in_memory(self):
        f = SpooledTemporaryFile(max_size=1000)
        f.write(b"hello world")
        f.seek(0)
        self.assertEqual(f.read(), b"hello world")
        f.close()

    def test_read_with_size(self):
        f = SpooledTemporaryFile(max_size=1000)
        f.write(b"hello world")
        f.seek(0)
        self.assertEqual(f.read(5), b"hello")
        self.assertEqual(f.read(5), b" worl")
        self.assertEqual(f.read(100), b"d")
        self.assertEqual(f.read(), b"")
        f.close()

    def test_read_past_end_returns_empty(self):
        f = SpooledTemporaryFile()
        f.write(b"ab")
        f.seek(0)
        f.read()
        self.assertEqual(f.read(), b"")
        f.close()


class TestRoll(unittest.TestCase):
    def test_roll_happens_on_crossing_write(self):
        f = SpooledTemporaryFile(max_size=10)
        f.write(b"0123456789")  # exactly at threshold — stays in memory
        self.assertFalse(f.rolled)
        f.write(b"!")  # now exceeds — rolls
        self.assertTrue(f.rolled)
        f.seek(0)
        self.assertEqual(f.read(), b"0123456789!")
        f.close()

    def test_single_write_exceeding_threshold_rolls(self):
        f = SpooledTemporaryFile(max_size=5)
        f.write(b"abcdefgh")
        self.assertTrue(f.rolled)
        f.seek(0)
        self.assertEqual(f.read(), b"abcdefgh")
        f.close()

    def test_data_preserved_across_roll(self):
        f = SpooledTemporaryFile(max_size=8)
        f.write(b"abc")
        f.write(b"def")
        f.write(b"ghijkl")  # triggers roll
        f.seek(0)
        self.assertEqual(f.read(), b"abcdefghijkl")
        f.close()

    def test_position_preserved_across_roll(self):
        f = SpooledTemporaryFile(max_size=8)
        f.write(b"abcdef")
        self.assertEqual(f.tell(), 6)
        f.write(b"ghijkl")  # roll
        # After the roll the position must be at the end of what was
        # just written, not reset.
        self.assertEqual(f.tell(), 12)
        f.close()

    def test_read_after_roll_without_seek(self):
        f = SpooledTemporaryFile(max_size=4)
        f.write(b"hello")
        # Position is at end; read should return empty.
        self.assertEqual(f.read(), b"")
        f.seek(0)
        self.assertEqual(f.read(), b"hello")
        f.close()

    def test_write_after_read_after_roll(self):
        f = SpooledTemporaryFile(max_size=4)
        f.write(b"hello")
        f.seek(2)
        self.assertEqual(f.read(2), b"ll")
        f.write(b"!")
        f.seek(0)
        self.assertEqual(f.read(), b"hell!")
        f.close()


class TestSeek(unittest.TestCase):
    def test_seek_and_tell_in_memory(self):
        f = SpooledTemporaryFile()
        f.write(b"abcdef")
        f.seek(2)
        self.assertEqual(f.tell(), 2)
        self.assertEqual(f.read(2), b"cd")
        f.close()

    def test_seek_from_end(self):
        f = SpooledTemporaryFile()
        f.write(b"abcdef")
        f.seek(-2, os.SEEK_END)
        self.assertEqual(f.read(), b"ef")
        f.close()

    def test_seek_from_current(self):
        f = SpooledTemporaryFile()
        f.write(b"abcdef")
        f.seek(2)
        f.seek(2, os.SEEK_CUR)
        self.assertEqual(f.read(), b"ef")
        f.close()

    def test_seek_negative_rejected(self):
        f = SpooledTemporaryFile()
        f.write(b"abc")
        with self.assertRaises(ValueError):
            f.seek(-1)
        f.close()

    def test_seek_past_end_then_write_fills_gap(self):
        # Seeking past the end and writing should NUL-fill the gap,
        # matching how a real filesystem file behaves.
        f = SpooledTemporaryFile()
        f.write(b"ab")
        f.seek(5)
        f.write(b"cd")
        f.seek(0)
        self.assertEqual(f.read(), b"ab\x00\x00\x00cd")
        f.close()

    def test_seek_after_roll(self):
        f = SpooledTemporaryFile(max_size=3)
        f.write(b"hello")
        f.seek(1)
        self.assertEqual(f.read(2), b"el")
        f.close()


class TestTruncate(unittest.TestCase):
    def test_truncate_to_current_position_in_memory(self):
        f = SpooledTemporaryFile()
        f.write(b"abcdef")
        f.seek(3)
        f.truncate()
        f.seek(0)
        self.assertEqual(f.read(), b"abc")
        f.close()

    def test_truncate_to_explicit_size(self):
        f = SpooledTemporaryFile()
        f.write(b"abcdef")
        f.truncate(2)
        f.seek(0)
        self.assertEqual(f.read(), b"ab")
        f.close()

    def test_truncate_extending(self):
        f = SpooledTemporaryFile()
        f.write(b"ab")
        f.truncate(4)
        f.seek(0)
        self.assertEqual(f.read(), b"ab\x00\x00")
        f.close()

    def test_truncate_after_roll(self):
        f = SpooledTemporaryFile(max_size=3)
        f.write(b"hello")
        f.truncate(2)
        f.seek(0)
        self.assertEqual(f.read(), b"he")
        f.close()

    def test_truncate_negative_rejected(self):
        f = SpooledTemporaryFile()
        with self.assertRaises(ValueError):
            f.truncate(-1)
        f.close()


class TestReadline(unittest.TestCase):
    def test_readline_single_line(self):
        f = SpooledTemporaryFile()
        f.write(b"hello\nworld\n")
        f.seek(0)
        self.assertEqual(f.readline(), b"hello\n")
        self.assertEqual(f.readline(), b"world\n")
        self.assertEqual(f.readline(), b"")
        f.close()

    def test_readline_no_trailing_newline(self):
        f = SpooledTemporaryFile()
        f.write(b"abc")
        f.seek(0)
        self.assertEqual(f.readline(), b"abc")
        f.close()

    def test_readline_with_size_limit(self):
        f = SpooledTemporaryFile()
        f.write(b"hello\n")
        f.seek(0)
        self.assertEqual(f.readline(3), b"hel")
        self.assertEqual(f.readline(3), b"lo\n")
        f.close()

    def test_readline_after_roll(self):
        f = SpooledTemporaryFile(max_size=3)
        f.write(b"hello\nworld\n")
        f.seek(0)
        self.assertEqual(f.readline(), b"hello\n")
        self.assertEqual(f.readline(), b"world\n")
        f.close()

    def test_readlines(self):
        f = SpooledTemporaryFile()
        f.write(b"a\nb\nc\n")
        f.seek(0)
        self.assertEqual(f.readlines(), [b"a\n", b"b\n", b"c\n"])
        f.close()

    def test_iteration(self):
        f = SpooledTemporaryFile()
        f.write(b"a\nb\n")
        f.seek(0)
        lines = list(f)
        self.assertEqual(lines, [b"a\n", b"b\n"])
        f.close()


class TestContextManagerAndClose(unittest.TestCase):
    def test_context_manager_closes(self):
        with SpooledTemporaryFile() as f:
            f.write(b"abc")
            self.assertFalse(f.closed)
        self.assertTrue(f.closed)

    def test_operation_after_close_raises(self):
        f = SpooledTemporaryFile()
        f.close()
        with self.assertRaises(ValueError):
            f.write(b"x")
        with self.assertRaises(ValueError):
            f.read()
        with self.assertRaises(ValueError):
            f.seek(0)
        with self.assertRaises(ValueError):
            f.tell()

    def test_close_is_idempotent(self):
        f = SpooledTemporaryFile()
        f.close()
        f.close()  # must not raise

    def test_fileno_rolls_to_disk(self):
        f = SpooledTemporaryFile()
        f.write(b"abc")
        self.assertFalse(f.rolled)
        fd = f.fileno()
        self.assertIsInstance(fd, int)
        self.assertTrue(f.rolled)
        f.close()

    def test_fileno_after_close_raises(self):
        f = SpooledTemporaryFile()
        f.close()
        with self.assertRaises(ValueError):
            f.fileno()


class TestCapabilityFlags(unittest.TestCase):
    def test_flags_true_when_open(self):
        f = SpooledTemporaryFile()
        self.assertTrue(f.writable())
        self.assertTrue(f.readable())
        self.assertTrue(f.seekable())
        f.close()

    def test_flags_false_when_closed(self):
        f = SpooledTemporaryFile()
        f.close()
        self.assertFalse(f.writable())
        self.assertFalse(f.readable())
        self.assertFalse(f.seekable())


class TestTempFileOptions(unittest.TestCase):
    def test_prefix_suffix_dir_forwarded(self):
        # We can't assert the exact path (the temp dir is platform
        # dependent), but we can check that the rolled file's name
        # carries the prefix and suffix we asked for.
        f = SpooledTemporaryFile(
            max_size=2, prefix="spool_test_", suffix=".dat"
        )
        f.write(b"abc")
        self.assertTrue(f.rolled)
        name = f._file.name  # accessing the underlying NamedTemporaryFile
        base = os.path.basename(name)
        self.assertTrue(base.startswith("spool_test_"))
        self.assertTrue(base.endswith(".dat"))
        f.close()


class TestNeverRoll(unittest.TestCase):
    def test_none_max_size_never_rolls_on_large_write(self):
        f = SpooledTemporaryFile(max_size=None)
        f.write(b"x" * 1_000_000)
        self.assertFalse(f.rolled)
        f.seek(0)
        self.assertEqual(len(f.read()), 1_000_000)
        f.close()

    def test_none_max_size_fileno_still_rolls(self):
        # fileno() requires a real fd, so it forces a roll regardless
        # of max_size.
        f = SpooledTemporaryFile(max_size=None)
        f.write(b"abc")
        f.fileno()
        self.assertTrue(f.rolled)
        f.close()


if __name__ == "__main__":
    unittest.main()
