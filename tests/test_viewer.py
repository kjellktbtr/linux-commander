"""Tests for linux_commander.viewer's pure (non-GUI) helpers.

The Toplevel-based TextWindow/view_file/edit_file functions need a real Tk
display and are verified separately with scripted drivers rather than under
pytest.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

from linux_commander.vfs import LocalFileSystem
from linux_commander.viewer import (
    _format_hexdump,
    _read_capped,
    _read_raw_capped,
    _strings_worker,
    _StringsScanner,
    detect_delimiter,
)

_FS = LocalFileSystem()


# ---------------------------------------------------------------------------
# _read_capped (text)
# ---------------------------------------------------------------------------


def test_read_capped_returns_full_content_when_under_limit(tmp_path: Path) -> None:
    path = tmp_path / "small.txt"
    path.write_text("hello world")
    text, truncated = _read_capped(_FS.from_path(path), max_bytes=1024)
    assert text == "hello world"
    assert truncated is False


def test_read_capped_truncates_when_over_limit(tmp_path: Path) -> None:
    path = tmp_path / "big.txt"
    path.write_text("a" * 100)
    text, truncated = _read_capped(_FS.from_path(path), max_bytes=10)
    assert text == "a" * 10
    assert truncated is True


def test_read_capped_replaces_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "binary.dat"
    path.write_bytes(b"hello \xff\xfe world")
    text, truncated = _read_capped(_FS.from_path(path), max_bytes=1024)
    assert "hello" in text
    assert "world" in text
    assert truncated is False


# ---------------------------------------------------------------------------
# _read_raw_capped (bytes)
# ---------------------------------------------------------------------------


def test_read_raw_capped_returns_full_bytes_under_limit(tmp_path: Path) -> None:
    path = tmp_path / "small.bin"
    path.write_bytes(b"\x00\x01\x02\x03")
    data, truncated = _read_raw_capped(_FS.from_path(path), max_bytes=1024)
    assert data == b"\x00\x01\x02\x03"
    assert truncated is False


def test_read_raw_capped_truncates_over_limit(tmp_path: Path) -> None:
    path = tmp_path / "big.bin"
    path.write_bytes(b"ABCDEFGHIJ")
    data, truncated = _read_raw_capped(_FS.from_path(path), max_bytes=5)
    assert data == b"ABCDE"
    assert truncated is True


def test_read_raw_capped_exact_limit_is_not_truncated(tmp_path: Path) -> None:
    path = tmp_path / "exact.bin"
    path.write_bytes(b"12345")
    data, truncated = _read_raw_capped(_FS.from_path(path), max_bytes=5)
    assert data == b"12345"
    assert truncated is False


# ---------------------------------------------------------------------------
# _format_hexdump
# ---------------------------------------------------------------------------


def test_format_hexdump_empty_input() -> None:
    assert _format_hexdump(b"") == ""


def test_format_hexdump_single_row() -> None:
    # 13 bytes: "Hello, World!" + newline
    data = b"Hello, World!\n"
    result = _format_hexdump(data)
    lines = result.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("00000000  ")
    assert "|Hello, World!" in lines[0]


def test_format_hexdump_two_rows() -> None:
    # 20 bytes -> row at 0x00 (16 bytes) and row at 0x10 (4 bytes)
    data = b"A" * 16 + b"BCDE"
    result = _format_hexdump(data)
    lines = result.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("00000000  ")
    assert lines[1].startswith("00000010  ")
    assert "|AAAAAAAAAAAAAAAA|" in lines[0]
    assert "|BCDE|" in lines[1]


def test_format_hexdump_non_printable_shown_as_dot() -> None:
    data = bytes([0x00, 0x01, 0x41, 0x7F])  # NUL, SOH, 'A', DEL
    result = _format_hexdump(data)
    # ASCII gutter: NUL->'.', SOH->'.', 'A'->'A', DEL->'.'
    assert "|..A.|" in result


def test_format_hexdump_offset_increments() -> None:
    data = b"X" * 48  # 3 full rows
    lines = _format_hexdump(data).splitlines()
    assert lines[0].startswith("00000000  ")
    assert lines[1].startswith("00000010  ")
    assert lines[2].startswith("00000020  ")


# ---------------------------------------------------------------------------
# detect_delimiter
# ---------------------------------------------------------------------------


def test_detect_delimiter_comma() -> None:
    text = "a,b,c\n1,2,3\n4,5,6\n"
    assert detect_delimiter(text) == ","


def test_detect_delimiter_semicolon() -> None:
    text = "name;role;note\nAlice;Dev;x\nBob;QA;y\n"
    assert detect_delimiter(text) == ";"


def test_detect_delimiter_tab() -> None:
    text = "a\tb\tc\n1\tb\tc\n"
    assert detect_delimiter(text) == "\t"


def test_detect_delimiter_ignores_quoted_commas_in_semicolon_file() -> None:
    # A comma inside a quoted field must not fool the detector into picking
    # comma over the file's real (semicolon) delimiter.
    text = 'name;role;note\nAlice;Dev;"Uses a, comma"\nBob;QA;"Another, comma, here"\n'
    assert detect_delimiter(text) == ";"


def test_detect_delimiter_falls_back_to_comma_when_no_delimiter_present() -> None:
    text = "just plain text\nwith no delimiters at all\n"
    assert detect_delimiter(text) == ","


def test_detect_delimiter_empty_text_falls_back_to_comma() -> None:
    assert detect_delimiter("") == ","


# ---------------------------------------------------------------------------
# _StringsScanner
# ---------------------------------------------------------------------------


def test_strings_scanner_finds_run_within_single_chunk() -> None:
    scanner = _StringsScanner(min_length=4)
    found = scanner.feed(b"\x00\x00hello\x00\x00world\x00\x00")
    assert found == ["hello", "world"]
    assert scanner.finish() == []


def test_strings_scanner_drops_runs_shorter_than_min_length() -> None:
    scanner = _StringsScanner(min_length=4)
    found = scanner.feed(b"\x00ab\x00cd\x00longenough\x00")
    assert found == ["longenough"]


def test_strings_scanner_completes_run_split_across_chunk_boundary() -> None:
    scanner = _StringsScanner(min_length=4)
    first = scanner.feed(b"\x00hel")
    second = scanner.feed(b"lo\x00world\x00")
    assert first == []
    assert second == ["hello", "world"]
    assert scanner.finish() == []


def test_strings_scanner_finish_flushes_trailing_run_at_eof() -> None:
    scanner = _StringsScanner(min_length=4)
    found = scanner.feed(b"\x00trailing")
    assert found == []
    assert scanner.finish() == ["trailing"]


def test_strings_scanner_finish_drops_short_trailing_run() -> None:
    scanner = _StringsScanner(min_length=4)
    scanner.feed(b"\x00ab")
    assert scanner.finish() == []


def test_strings_scanner_min_length_of_one_keeps_single_byte_runs() -> None:
    scanner = _StringsScanner(min_length=1)
    found = scanner.feed(b"\x00a\x00b\x00")
    assert found == ["a", "b"]


def test_strings_scanner_force_flushes_implausibly_long_run() -> None:
    # A run with no separator byte at all must not grow the carry buffer
    # forever; it gets force-flushed once it crosses _STRINGS_MAX_RUN.
    scanner = _StringsScanner(min_length=4)
    huge = b"a" * (1024 * 1024 + 10)
    found = scanner.feed(huge)
    assert found  # flushed at least once
    assert sum(len(s) for s in found) + len(scanner._carry) == len(huge)


# ---------------------------------------------------------------------------
# _strings_worker
# ---------------------------------------------------------------------------


def _drain(q: queue.Queue[str | None]) -> list[str]:
    results = []
    while True:
        item = q.get_nowait()
        if item is None:
            break
        results.append(item)
    return results


def test_strings_worker_streams_results_and_ends_with_sentinel(tmp_path: Path) -> None:
    path = tmp_path / "binary.dat"
    path.write_bytes(b"\x00\x00hello\x00\x00world\x00\x00")
    out: queue.Queue[str | None] = queue.Queue()
    _strings_worker(_FS.from_path(path), 4, out, threading.Event())
    assert _drain(out) == ["hello", "world"]


def test_strings_worker_respects_cancellation(tmp_path: Path) -> None:
    # A cancelled event should stop the worker before it does any real work;
    # it must still emit the terminal sentinel so pollers don't hang.
    path = tmp_path / "binary.dat"
    path.write_bytes(b"\x00\x00hello\x00\x00world\x00\x00")
    out: queue.Queue[str | None] = queue.Queue()
    cancelled = threading.Event()
    cancelled.set()
    _strings_worker(_FS.from_path(path), 4, out, cancelled)
    assert _drain(out) == []


def test_strings_worker_missing_file_still_emits_sentinel(tmp_path: Path) -> None:
    path = _FS.from_path(tmp_path / "does_not_exist.dat")
    out: queue.Queue[str | None] = queue.Queue()
    _strings_worker(path, 4, out, threading.Event())
    assert _drain(out) == []
