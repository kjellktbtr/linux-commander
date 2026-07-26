"""Tests for the FAT12/FAT16 floppy filesystem core library."""

from __future__ import annotations

import datetime

import pytest

from linux_commander.fatfs import (
    FAT12_EOF_MAX,
    FAT16_EOF_MAX,
    FLOPPY_FORMATS,
    FATImage,
    FATImageBuilder,
    detect_floppy_format,
    encode_dos_time,
    fat12_get,
    fat12_set,
    fat16_get,
    fat16_set,
    is_eof,
    truncate_to_83,
)

# ---------------------------------------------------------------------------
# FloppyFormat tests
# ---------------------------------------------------------------------------


class TestFloppyFormat:
    def test_known_formats_exist(self) -> None:
        for name in ("360K", "720K", "1.2M", "1.44M", "2.88M"):
            assert name in FLOPPY_FORMATS

    def test_fat12_formats(self) -> None:
        for name in ("360K", "720K", "1.2M"):
            fmt = FLOPPY_FORMATS[name]
            assert fmt.fat_type == "fat12"

    def test_fat16_formats(self) -> None:
        # 2.88M has > 4085 clusters, so it uses FAT16
        fmt = FLOPPY_FORMATS["2.88M"]
        assert fmt.fat_type == "fat16"
        # All other standard floppy formats use FAT12 (< 4085 clusters)
        for name in ("360K", "720K", "1.2M", "1.44M"):
            f = FLOPPY_FORMATS[name]
            assert f.fat_type == "fat12"

    def test_capacity_bytes(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        assert fmt.capacity_bytes == 1474560  # 2880 * 512

    def test_derived_fields(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        assert fmt.root_dir_sector == fmt.reserved_sectors + fmt.fat_count * fmt.fat_sectors
        assert fmt.data_start_sector == fmt.root_dir_sector + fmt.root_dir_sectors

    def test_144m_geometry(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        assert fmt.heads == 2
        assert fmt.sectors_per_track == 18
        assert fmt.cylinders == 80
        assert fmt.total_sectors == 2880
        assert fmt.bytes_per_sector == 512
        assert fmt.sectors_per_cluster == 1
        assert fmt.reserved_sectors == 1
        assert fmt.fat_count == 2
        assert fmt.fat_sectors == 9
        assert fmt.root_entries == 224
        assert fmt.media_descriptor == 0xF0

    def test_720k_geometry(self) -> None:
        fmt = FLOPPY_FORMATS["720K"]
        assert fmt.total_sectors == 1440
        assert fmt.fat_type == "fat12"
        assert fmt.media_descriptor == 0xF9


# ---------------------------------------------------------------------------
# FAT12 packing tests (12-bit values only)
# ---------------------------------------------------------------------------


class TestFAT12Packing:
    def test_set_and_get_even_cluster(self) -> None:
        fat = bytearray(512)
        fat12_set(fat, 2, 0x0003)  # valid 12-bit value
        assert fat12_get(fat, 2) == 0x0003

    def test_set_and_get_odd_cluster(self) -> None:
        fat = bytearray(512)
        fat12_set(fat, 3, 0x0004)
        assert fat12_get(fat, 3) == 0x0004

    def test_set_and_get_eof(self) -> None:
        fat = bytearray(512)
        fat12_set(fat, 10, FAT12_EOF_MAX)
        assert fat12_get(fat, 10) == FAT12_EOF_MAX
        assert is_eof("fat12", fat12_get(fat, 10))

    def test_adjacent_clusters_dont_interfere(self) -> None:
        fat = bytearray(512)
        fat12_set(fat, 2, 0x0003)
        fat12_set(fat, 3, FAT12_EOF_MAX)
        assert fat12_get(fat, 2) == 0x0003
        assert fat12_get(fat, 3) == FAT12_EOF_MAX

    def test_max_12bit_value(self) -> None:
        fat = bytearray(512)
        fat12_set(fat, 5, 0x0FFF)
        assert fat12_get(fat, 5) == 0x0FFF


# ---------------------------------------------------------------------------
# FAT16 packing tests
# ---------------------------------------------------------------------------


class TestFAT16Packing:
    def test_set_and_get(self) -> None:
        fat = bytearray(512)
        fat16_set(fat, 2, 0x1234)
        assert fat16_get(fat, 2) == 0x1234

    def test_eof_marker(self) -> None:
        fat = bytearray(512)
        fat16_set(fat, 5, FAT16_EOF_MAX)
        assert fat16_get(fat, 5) == FAT16_EOF_MAX
        assert is_eof("fat16", fat16_get(fat, 5))


# ---------------------------------------------------------------------------
# Time/date encoding tests
# ---------------------------------------------------------------------------


class TestTimeEncoding:
    def test_encode_decode_roundtrip(self) -> None:
        dt = datetime.datetime(2024, 6, 15, 14, 30, 44)
        dos_time, dos_date = encode_dos_time(dt)
        decoded = dt.replace(second=(dt.second // 2) * 2)  # 2-second granularity
        t2, d2 = encode_dos_time(decoded)
        assert t2 == dos_time
        assert d2 == dos_date

    def test_min_date(self) -> None:
        dt = datetime.datetime(1980, 1, 1, 0, 0, 0)
        dos_time, dos_date = encode_dos_time(dt)
        assert dos_time == 0
        # date = (0 << 9) | (1 << 5) | 1 = 0x0021
        assert dos_date == 0x0021

    def test_second_granularity(self) -> None:
        dt1 = datetime.datetime(2000, 1, 1, 12, 0, 1)
        dt2 = datetime.datetime(2000, 1, 1, 12, 0, 2)
        t1, _ = encode_dos_time(dt1)
        t2, _ = encode_dos_time(dt2)
        # 1//2 = 0, 2//2 = 1 -> different encoded seconds
        assert t1 != t2


# ---------------------------------------------------------------------------
# 8.3 name truncation tests
# ---------------------------------------------------------------------------


class TestTruncateTo83:
    def test_simple_name(self) -> None:
        counts: dict[str, int] = {}
        name, ext = truncate_to_83("hello.txt", counts)
        assert name == "HELLO   "
        assert ext == "TXT"

    def test_long_name_truncated(self) -> None:
        counts = {}
        name, ext = truncate_to_83("verylongfilename.txt", counts)
        assert len(name) == 8
        assert name.startswith("VERYLONG")
        assert ext == "TXT"

    def test_no_extension(self) -> None:
        counts = {}
        name, ext = truncate_to_83("README", counts)
        assert name == "README  "
        assert ext == "   "

    def test_collision_suffix(self) -> None:
        counts = {}
        n1, e1 = truncate_to_83("file.txt", counts)
        n2, e2 = truncate_to_83("file.txt", counts)
        assert n1 == "FILE    "
        assert "~1" in n2
        assert e1 == e2 == "TXT"

    def test_multiple_collisions(self) -> None:
        counts = {}
        names = []
        for _ in range(4):
            n, _ = truncate_to_83("test.log", counts)
            names.append(n)
        assert names[0] == "TEST    "
        assert "~1" in names[1]
        assert "~2" in names[2]
        assert "~3" in names[3]


# ---------------------------------------------------------------------------
# FATImageBuilder tests
# ---------------------------------------------------------------------------


class TestFATImageBuilder:
    def _build_simple(self, fmt_name: str = "1.44M") -> bytes:
        fmt = FLOPPY_FORMATS[fmt_name]
        builder = FATImageBuilder(fmt, "TESTVOL")
        builder.add_file("hello.txt", b"Hello World!")
        builder.add_file("data.bin", b"\x00\x01\x02\x03")
        return builder.finalize()

    def test_creates_valid_image_144m(self) -> None:
        data = self._build_simple("1.44M")
        img = FATImage(data)
        assert img.is_valid()
        assert img.fat_type == "fat12"  # 1.44M uses FAT12 (< 4085 clusters)

    def test_creates_valid_image_720k(self) -> None:
        data = self._build_simple("720K")
        img = FATImage(data)
        assert img.is_valid()
        assert img.fat_type == "fat12"

    def test_image_size_matches_format(self) -> None:
        data = self._build_simple("1.44M")
        assert len(data) == FLOPPY_FORMATS["1.44M"].capacity_bytes

    def test_boot_signature(self) -> None:
        data = self._build_simple()
        assert data[510] == 0x55
        assert data[511] == 0xAA

    def test_list_root_dir(self) -> None:
        data = self._build_simple()
        img = FATImage(data)
        entries = img.list_root_dir()
        names = [e.display_name for e in entries]
        assert "HELLO.TXT" in names
        assert "DATA.BIN" in names

    def test_read_file(self) -> None:
        data = self._build_simple()
        img = FATImage(data)
        entries = img.list_root_dir()
        hello_entry = next(e for e in entries if e.display_name == "HELLO.TXT")
        file_data = img.read_file(hello_entry)
        assert file_data == b"Hello World!"

    def test_file_metadata(self) -> None:
        data = self._build_simple()
        img = FATImage(data)
        entries = img.list_root_dir()
        hello_entry = next(e for e in entries if e.display_name == "HELLO.TXT")
        assert hello_entry.size == 12
        assert not hello_entry.is_dir

    def test_empty_file(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        builder = FATImageBuilder(fmt)
        builder.add_file("empty.txt", b"")
        data = builder.finalize()
        img = FATImage(data)
        entries = img.list_root_dir()
        empty_entry = next(e for e in entries if e.display_name == "EMPTY.TXT")
        assert empty_entry.size == 0

    def test_directory_creation(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        builder = FATImageBuilder(fmt)
        builder.add_dir("mydir")
        data = builder.finalize()
        img = FATImage(data)
        entries = img.list_root_dir()
        dir_entry = next(e for e in entries if e.display_name == "MYDIR")
        assert dir_entry.is_dir

    def test_file_in_subdirectory(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        builder = FATImageBuilder(fmt)
        builder.add_file("subdir/file.txt", b"nested content")
        data = builder.finalize()
        img = FATImage(data)
        entries = img.list_root_dir()
        dir_entry = next(e for e in entries if e.display_name == "SUBDIR")
        assert dir_entry.is_dir

    def test_multiple_formats(self) -> None:
        for name in ("360K", "720K", "1.2M", "1.44M", "2.88M"):
            fmt = FLOPPY_FORMATS[name]
            builder = FATImageBuilder(fmt)
            builder.add_file("test.txt", b"test data")
            data = builder.finalize()
            img = FATImage(data)
            assert img.is_valid(), f"Failed for {name}"
            entries = img.list_root_dir()
            assert any(e.display_name == "TEST.TXT" for e in entries)

    def test_large_file_multiple_clusters(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        builder = FATImageBuilder(fmt)
        large_data = b"X" * 2000  # Spans multiple 512-byte clusters
        builder.add_file("large.dat", large_data)
        data = builder.finalize()
        img = FATImage(data)
        entries = img.list_root_dir()
        entry = next(e for e in entries if e.display_name == "LARGE.DAT")
        assert entry.size == 2000
        file_data = img.read_file(entry)
        assert file_data == large_data

    def test_volume_label(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        builder = FATImageBuilder(fmt, volume_label="MYLABEL")
        data = builder.finalize()
        label = data[0x2B:0x36].decode("ascii").strip()
        assert label == "MYLABEL"


# ---------------------------------------------------------------------------
# FATImage reading tests
# ---------------------------------------------------------------------------


class TestFATImage:
    def test_invalid_data_raises(self) -> None:
        with pytest.raises(ValueError):
            FATImage(b"too short")

    def test_invalid_signature_raises(self) -> None:
        data = bytearray(512)
        with pytest.raises(ValueError):
            FATImage(bytes(data))

    def test_roundtrip_all_formats(self) -> None:
        test_files = [
            ("file1.txt", b"content one"),
            ("file2.dat", b"\xde\xad\xbe\xef"),
            ("empty.txt", b""),
        ]
        for fmt_name in FLOPPY_FORMATS:
            fmt = FLOPPY_FORMATS[fmt_name]
            builder = FATImageBuilder(fmt, "TEST")
            for name, content in test_files:
                builder.add_file(name, content)
            data = builder.finalize()
            img = FATImage(data)
            assert img.is_valid()
            entries = img.list_root_dir()
            for name, content in test_files:
                entry = next(
                    (e for e in entries if e.display_name == name.upper()),
                    None,
                )
                assert entry is not None, f"Missing {name} in {fmt_name}"
                assert entry.size == len(content)
                if content:
                    file_data = img.read_file(entry)
                    assert file_data == content

    def test_find_entry(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        builder = FATImageBuilder(fmt)
        builder.add_file("hello.txt", b"world")
        data = builder.finalize()
        img = FATImage(data)
        entry = img.find_entry("HELLO", "TXT")
        assert entry is not None
        assert entry.size == 5

    def test_find_nonexistent_entry(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        builder = FATImageBuilder(fmt)
        builder.add_file("hello.txt", b"world")
        data = builder.finalize()
        img = FATImage(data)
        entry = img.find_entry("MISSING ", "TXT")
        assert entry is None


# ---------------------------------------------------------------------------
# Format detection tests
# ---------------------------------------------------------------------------


class TestDetectFloppyFormat:
    def test_detect_144m(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        builder = FATImageBuilder(fmt)
        data = builder.finalize()
        detected = detect_floppy_format(len(data), bytes(data[:512]))
        assert detected is not None
        assert detected.name == "1.44M"

    def test_detect_720k(self) -> None:
        fmt = FLOPPY_FORMATS["720K"]
        builder = FATImageBuilder(fmt)
        data = builder.finalize()
        detected = detect_floppy_format(len(data), bytes(data[:512]))
        assert detected is not None
        assert detected.name == "720K"

    def test_wrong_size_returns_none(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        builder = FATImageBuilder(fmt)
        data = builder.finalize()
        detected = detect_floppy_format(12345, bytes(data[:512]))
        assert detected is None

    def test_invalid_signature_returns_none(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        bad_data = bytearray(fmt.capacity_bytes)
        detected = detect_floppy_format(len(bad_data), bytes(bad_data[:512]))
        assert detected is None

    def test_short_prefix_falls_back_to_size(self) -> None:
        fmt = FLOPPY_FORMATS["1.44M"]
        detected = detect_floppy_format(fmt.capacity_bytes, b"")
        assert detected is not None
        assert detected.name == "1.44M"
