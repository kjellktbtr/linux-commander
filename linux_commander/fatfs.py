"""FAT12/FAT16 floppy filesystem core library.

Pure Python implementation for reading and writing FAT12/FAT16 floppy disk images.
Supports standard floppy formats: 360K, 720K, 1.2M, 1.44M, 2.88M.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# ---------------------------------------------------------------------------
# Floppy format geometry constants
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FloppyFormat:
    """Geometry and BPB parameters for a standard floppy format."""

    name: str
    fat_type: Literal["fat12", "fat16"]
    heads: int
    sectors_per_track: int
    cylinders: int
    total_sectors: int
    bytes_per_sector: int = 512
    sectors_per_cluster: int = 1
    reserved_sectors: int = 1
    fat_count: int = 2
    fat_sectors: int = 0
    root_entries: int = 0
    root_dir_sectors: int = 0
    data_start_sector: int = 0
    media_descriptor: int = 0xF9

    @property
    def root_dir_sector(self) -> int:
        return self.reserved_sectors + self.fat_count * self.fat_sectors

    @property
    def total_clusters(self) -> int:
        return self.total_sectors - self.data_start_sector

    @property
    def capacity_bytes(self) -> int:
        return self.total_sectors * self.bytes_per_sector


# Pre-defined floppy formats
FLOPPY_FORMATS: dict[str, FloppyFormat] = {
    "360K": FloppyFormat(
        name="360K",
        fat_type="fat12",
        heads=2,
        sectors_per_track=9,
        cylinders=40,
        total_sectors=720,
        sectors_per_cluster=2,
        fat_sectors=2,
        root_entries=112,
        media_descriptor=0xFD,
    ),
    "720K": FloppyFormat(
        name="720K",
        fat_type="fat12",
        heads=2,
        sectors_per_track=9,
        cylinders=80,
        total_sectors=1440,
        sectors_per_cluster=2,
        fat_sectors=3,
        root_entries=112,
        media_descriptor=0xF9,
    ),
    "1.2M": FloppyFormat(
        name="1.2M",
        fat_type="fat12",
        heads=2,
        sectors_per_track=15,
        cylinders=80,
        total_sectors=2400,
        fat_sectors=7,
        root_entries=224,
        media_descriptor=0xF9,
    ),
    "1.44M": FloppyFormat(
        name="1.44M",
        fat_type="fat12",
        heads=2,
        sectors_per_track=18,
        cylinders=80,
        total_sectors=2880,
        fat_sectors=9,
        root_entries=224,
        media_descriptor=0xF0,
    ),
    "2.88M": FloppyFormat(
        name="2.88M",
        fat_type="fat16",
        heads=2,
        sectors_per_track=36,
        cylinders=80,
        total_sectors=5760,
        fat_sectors=23,
        root_entries=224,
        media_descriptor=0xF0,
    ),
}

# Initialize derived fields
for fmt in FLOPPY_FORMATS.values():
    object.__setattr__(
        fmt,
        "root_dir_sectors",
        (fmt.root_entries * 32 + fmt.bytes_per_sector - 1) // fmt.bytes_per_sector,
    )
    object.__setattr__(fmt, "data_start_sector", fmt.root_dir_sector + fmt.root_dir_sectors)


# ---------------------------------------------------------------------------
# Directory entry representation
# ---------------------------------------------------------------------------

# FAT attribute bits (from fat16.h)
ATTR_READONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_VOLUME = 0x08
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_LFN = 0x0F


@dataclass(frozen=True, slots=True)
class FATDirEntry:
    """Parsed FAT directory entry (32 bytes)."""

    name: str  # 8 chars, padded with spaces
    extension: str  # 3 chars, padded with spaces
    attributes: int
    first_cluster: int
    size: int
    mtime: float  # Unix timestamp
    is_dir: bool

    @property
    def display_name(self) -> str:
        """Return name in 8.3 format for display."""
        n = self.name.rstrip()
        e = self.extension.rstrip()
        return f"{n}.{e}" if e else n


# ---------------------------------------------------------------------------
# FAT12/16 packing helpers
# ---------------------------------------------------------------------------

# EOF markers
FAT12_EOF_MIN = 0xFF8
FAT12_EOF_MAX = 0xFFF
FAT16_EOF_MIN = 0xFFF8
FAT16_EOF_MAX = 0xFFFF
FAT_FREE = 0x000
FAT_RESERVED = 0x001
FAT_BAD = 0xFF7  # FAT12: 0xFF7, FAT16: 0xFFF7


def fat12_get(fat: bytearray, cluster: int) -> int:
    """Read a FAT12 entry value."""
    offset = cluster * 3 // 2
    if cluster % 2 == 0:
        return fat[offset] | ((fat[offset + 1] & 0x0F) << 8)
    else:
        return (fat[offset] >> 4) | (fat[offset + 1] << 4)


def fat12_set(fat: bytearray, cluster: int, value: int) -> None:
    """Write a FAT12 entry value."""
    offset = cluster * 3 // 2
    if cluster % 2 == 0:
        fat[offset] = value & 0xFF
        fat[offset + 1] = (fat[offset + 1] & 0xF0) | ((value >> 8) & 0x0F)
    else:
        fat[offset] = (fat[offset] & 0x0F) | ((value & 0x0F) << 4)
        fat[offset + 1] = (value >> 4) & 0xFF


def fat16_get(fat: bytearray, cluster: int) -> int:
    """Read a FAT16 entry value."""
    return struct.unpack_from("<H", fat, cluster * 2)[0]


def fat16_set(fat: bytearray, cluster: int, value: int) -> None:
    """Write a FAT16 entry value."""
    struct.pack_into("<H", fat, cluster * 2, value)


def is_eof(fat_type: str, cluster: int) -> bool:
    """Check if a cluster value is an end-of-chain marker."""
    if fat_type == "fat12":
        return FAT12_EOF_MIN <= cluster <= FAT12_EOF_MAX
    else:
        return FAT16_EOF_MIN <= cluster <= FAT16_EOF_MAX


def get_fat_entry(fat: bytearray, fat_type: str, cluster: int) -> int:
    """Get FAT entry for cluster."""
    if fat_type == "fat12":
        return fat12_get(fat, cluster)
    else:
        return fat16_get(fat, cluster)


def set_fat_entry(fat: bytearray, fat_type: str, cluster: int, value: int) -> None:
    """Set FAT entry for cluster."""
    if fat_type == "fat12":
        fat12_set(fat, cluster, value)
    else:
        fat16_set(fat, cluster, value)


# ---------------------------------------------------------------------------
# Time/date encoding (DOS format)
# ---------------------------------------------------------------------------


def encode_dos_time(dt: datetime) -> tuple[int, int]:
    """Encode datetime to DOS time/date format.
    Returns (time, date) as 16-bit integers.
    """
    # Time: bits 0-4 = seconds/2, 5-10 = minutes, 11-15 = hours
    dos_time = (dt.hour << 11) | (dt.minute << 5) | (dt.second // 2)
    # Date: bits 0-4 = day, 5-8 = month, 9-15 = year-1980
    dos_date = ((dt.year - 1980) << 9) | (dt.month << 5) | dt.day
    return dos_time, dos_date


def decode_dos_time(dos_time: int, dos_date: int) -> datetime:
    """Decode DOS time/date to datetime."""
    hour = (dos_time >> 11) & 0x1F
    minute = (dos_time >> 5) & 0x3F
    second = (dos_time & 0x1F) * 2
    year = ((dos_date >> 9) & 0x7F) + 1980
    month = (dos_date >> 5) & 0x0F
    day = dos_date & 0x1F
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return datetime(1980, 1, 1, 0, 0, 0)


# ---------------------------------------------------------------------------
# 8.3 name truncation with collision handling
# ---------------------------------------------------------------------------


def truncate_to_83(name: str, name_counts: dict[str, int]) -> tuple[str, str]:
    """Truncate a long filename to 8.3 format with collision suffixes.

    Args:
        name: Original filename (may include path)
        name_counts: Dict tracking used base names for collision suffixes

    Returns:
        Tuple of (8-char name, 3-char extension)
    """
    # Extract basename
    basename = name.split("/")[-1].split("\\")[-1]

    # Split into stem and extension
    if "." in basename:
        stem, ext = basename.rsplit(".", 1)
        ext = ext[:3].upper()
    else:
        stem, ext = basename, ""

    # Truncate stem to 8 chars, uppercase, replace invalid chars
    stem = "".join(c if c.isalnum() or c in "_-~!@#$%^&(){}" else "_" for c in stem)
    stem = stem[:8].upper()
    if not stem:
        stem = "FILE"

    base_name = f"{stem}.{ext}" if ext else stem
    if base_name in name_counts:
        name_counts[base_name] += 1
        count = name_counts[base_name]
        suffix = f"~{count}"
        available = 8 - len(ext) - len(suffix)
        if available < 1:
            available = 1
        stem = f"{stem[:available]}{suffix}"
    else:
        name_counts[base_name] = 0

    return stem.ljust(8)[:8], ext.ljust(3)[:3]


# ---------------------------------------------------------------------------
# FATImage: read existing floppy images
# ---------------------------------------------------------------------------


class FATImage:
    """Read-only access to a FAT12/FAT16 floppy image."""

    def __init__(self, data: bytes) -> None:
        self._data = bytearray(data)
        self._parse_bpb()

    def _parse_bpb(self) -> None:
        """Parse BIOS Parameter Block from boot sector."""
        if len(self._data) < 512:
            raise ValueError("Image too small for boot sector")

        # Check boot signature
        if self._data[510] != 0x55 or self._data[511] != 0xAA:
            raise ValueError("Invalid boot signature (0x55AA not found)")

        # BPB at offset 0x0B (relative to sector start)
        bpb = self._data[0x0B:]
        self.bytes_per_sector = struct.unpack_from("<H", bpb, 0)[0]
        self.sectors_per_cluster = bpb[2]
        self.reserved_sectors = struct.unpack_from("<H", bpb, 3)[0]
        self.fat_count = bpb[5]
        self.root_entries = struct.unpack_from("<H", bpb, 6)[0]
        self.total_sectors_16 = struct.unpack_from("<H", bpb, 8)[0]
        self.media_descriptor = bpb[10]
        self.fat_sectors = struct.unpack_from("<H", bpb, 11)[0]
        self.sectors_per_track = struct.unpack_from("<H", bpb, 13)[0]
        self.heads = struct.unpack_from("<H", bpb, 15)[0]
        self.hidden_sectors = struct.unpack_from("<I", bpb, 17)[0]
        self.total_sectors_32 = struct.unpack_from("<I", bpb, 21)[0]

        self.total_sectors = self.total_sectors_16 or self.total_sectors_32
        self.root_dir_sectors = (
            self.root_entries * 32 + self.bytes_per_sector - 1
        ) // self.bytes_per_sector
        self.root_dir_sector = self.reserved_sectors + self.fat_count * self.fat_sectors
        self.data_start_sector = self.root_dir_sector + self.root_dir_sectors

        # Determine FAT type from total clusters
        data_sectors = self.total_sectors - self.data_start_sector
        total_clusters = data_sectors // self.sectors_per_cluster
        self.fat_type: Literal["fat12", "fat16"] = "fat12" if total_clusters < 4085 else "fat16"

        # Load FAT tables
        fat_start = self.reserved_sectors * self.bytes_per_sector
        fat_size = self.fat_sectors * self.bytes_per_sector
        self._fat = self._data[fat_start : fat_start + fat_size]

        # Load root directory
        root_start = self.root_dir_sector * self.bytes_per_sector
        root_size = self.root_dir_sectors * self.bytes_per_sector
        self._root_dir = self._data[root_start : root_start + root_size]

    def is_valid(self) -> bool:
        """Check if image has valid FAT signature and BPB."""
        try:
            return (
                len(self._data) >= 512
                and self._data[510] == 0x55
                and self._data[511] == 0xAA
                and self.bytes_per_sector == 512
                and self.fat_type in ("fat12", "fat16")
            )
        except Exception:
            return False

    def list_root_dir(self) -> list[FATDirEntry]:
        """List all entries in the root directory."""
        return self._list_dir(self._root_dir, 0)

    def list_cluster_dir(self, first_cluster: int) -> list[FATDirEntry]:
        """List entries in a subdirectory given its first cluster."""
        if first_cluster < 2:
            return []
        data = self._read_cluster_chain(first_cluster)
        return self._list_dir(data, first_cluster)

    def _list_dir(self, data: bytearray, _cluster: int) -> list[FATDirEntry]:
        """Parse directory entries from raw sector data."""
        entries = []
        for i in range(0, len(data), 32):
            if i + 32 > len(data):
                break
            entry = data[i : i + 32]
            if entry[0] == 0x00:  # End of directory
                break
            if entry[0] == 0xE5:  # Deleted entry
                continue
            if entry[11] == ATTR_LFN:  # Long filename entry
                continue
            if entry[11] & ATTR_VOLUME:  # Volume label
                continue

            name = entry[0:8].decode("ascii", errors="replace").rstrip()
            ext = entry[8:11].decode("ascii", errors="replace").rstrip()
            attrs = entry[11]
            first_cl = struct.unpack_from("<H", entry, 26)[0]
            size = struct.unpack_from("<I", entry, 28)[0]
            dos_time = struct.unpack_from("<H", entry, 22)[0]
            dos_date = struct.unpack_from("<H", entry, 24)[0]
            mtime = decode_dos_time(dos_time, dos_date).timestamp()
            is_dir = bool(attrs & ATTR_DIRECTORY)

            entries.append(
                FATDirEntry(
                    name=name,
                    extension=ext,
                    attributes=attrs,
                    first_cluster=first_cl,
                    size=size,
                    mtime=mtime,
                    is_dir=is_dir,
                )
            )
        return entries

    def _read_cluster_chain(self, start_cluster: int) -> bytearray:
        """Read all sectors in a cluster chain."""
        if start_cluster < 2:
            return bytearray()

        result = bytearray()
        cluster = start_cluster
        cluster_size = self.sectors_per_cluster * self.bytes_per_sector

        while cluster >= 2 and not is_eof(self.fat_type, cluster):
            sector = self.data_start_sector + (cluster - 2) * self.sectors_per_cluster
            offset = sector * self.bytes_per_sector
            result.extend(self._data[offset : offset + cluster_size])
            cluster = get_fat_entry(self._fat, self.fat_type, cluster)

        return result

    def read_file(self, dir_entry: FATDirEntry) -> bytes:
        """Read file data following cluster chain."""
        data = self._read_cluster_chain(dir_entry.first_cluster)
        return bytes(data[: dir_entry.size])

    def find_entry(self, name_83: str, extension: str = "") -> FATDirEntry | None:
        """Find a directory entry by 8.3 name in root directory."""
        name_stripped = name_83.strip().upper()
        ext_stripped = extension.strip().upper()
        for entry in self.list_root_dir():
            if entry.name == name_stripped and entry.extension == ext_stripped:
                return entry
        return None


# ---------------------------------------------------------------------------
# FATImageBuilder: create new floppy images
# ---------------------------------------------------------------------------


class FATImageBuilder:
    """Build a new FAT12/FAT16 floppy image from files and directories."""

    def __init__(self, fmt: FloppyFormat, volume_label: str = "") -> None:
        self.fmt = fmt
        self.volume_label = volume_label[:11].upper().ljust(11)
        self._image = bytearray(fmt.total_sectors * fmt.bytes_per_sector)
        self._fat = bytearray(fmt.fat_sectors * fmt.bytes_per_sector)
        self._root_dir = bytearray(fmt.root_dir_sectors * fmt.bytes_per_sector)
        self._next_cluster = 2
        self._dir_entries: dict[str, list[FATDirEntry]] = {}  # path -> entries
        self._name_counts: dict[str, int] = {}
        self._data_written: dict[int, bytes] = {}  # cluster -> data

        self._init_fat()
        self._write_boot_sector()

    def _init_fat(self) -> None:
        """Initialize FAT with reserved entries."""
        # Byte 0 = media descriptor. Bytes 1-2 = 0xFF for reserved entries.
        # For FAT12, bytes 0-2 cover clusters 0-1 (reserved). Cluster 2 starts
        # at byte 3, so setting bytes 1-2 to 0xFF is safe and ensures
        # mtools/fsck.fat compatibility.
        self._fat[0] = self.fmt.media_descriptor
        self._fat[1] = 0xFF
        self._fat[2] = 0xFF

    def _write_boot_sector(self) -> None:
        """Write boot sector with BPB."""
        bs = bytearray(512)
        # Jump instruction + NOP
        bs[0:3] = b"\xeb\x3c\x90"
        # OEM name
        bs[3:11] = b"LINUXCMD"
        # BPB at offset 0x0B
        struct.pack_into("<H", bs, 0x0B, self.fmt.bytes_per_sector)
        bs[0x0D] = self.fmt.sectors_per_cluster
        struct.pack_into("<H", bs, 0x0E, self.fmt.reserved_sectors)
        bs[0x10] = self.fmt.fat_count
        struct.pack_into("<H", bs, 0x11, self.fmt.root_entries)
        total_16 = self.fmt.total_sectors if self.fmt.total_sectors < 65536 else 0
        struct.pack_into("<H", bs, 0x13, total_16)
        bs[0x15] = self.fmt.media_descriptor
        struct.pack_into("<H", bs, 0x16, self.fmt.fat_sectors)
        struct.pack_into("<H", bs, 0x18, self.fmt.sectors_per_track)
        struct.pack_into("<H", bs, 0x1A, self.fmt.heads)
        struct.pack_into("<I", bs, 0x1C, 0)  # hidden sectors
        struct.pack_into("<I", bs, 0x20, self.fmt.total_sectors)
        bs[0x24] = 0x00  # drive number
        bs[0x25] = 0x00  # reserved
        bs[0x26] = 0x29  # extended boot signature
        struct.pack_into("<I", bs, 0x27, 0x12345678)  # volume serial
        bs[0x2B:0x36] = self.volume_label.encode("ascii")
        bs[0x36:0x3E] = b"FAT12   " if self.fmt.fat_type == "fat12" else b"FAT16   "
        # Boot signature
        bs[510] = 0x55
        bs[511] = 0xAA

        self._image[0:512] = bs

    def _allocate_clusters(self, size: int) -> list[int]:
        """Allocate clusters for a file of given size."""
        if size == 0:
            return []
        clusters = []
        cluster_size = self.fmt.sectors_per_cluster * self.fmt.bytes_per_sector
        needed = (size + cluster_size - 1) // cluster_size
        for _ in range(needed):
            clusters.append(self._next_cluster)
            self._next_cluster += 1
        # Link clusters
        for i in range(len(clusters) - 1):
            set_fat_entry(self._fat, self.fmt.fat_type, clusters[i], clusters[i + 1])
        # EOF marker
        eof = FAT12_EOF_MAX if self.fmt.fat_type == "fat12" else FAT16_EOF_MAX
        set_fat_entry(self._fat, self.fmt.fat_type, clusters[-1], eof)
        return clusters

    def _write_cluster_data(self, first_cluster: int, data: bytes) -> None:
        """Write file data to clusters."""
        cluster_size = self.fmt.sectors_per_cluster * self.fmt.bytes_per_sector
        offset = 0
        cluster = first_cluster
        while cluster >= 2 and offset < len(data):
            chunk = data[offset : offset + cluster_size]
            if len(chunk) < cluster_size:
                chunk += b"\x00" * (cluster_size - len(chunk))
            self._data_written[cluster] = chunk
            offset += cluster_size
            cluster = get_fat_entry(self._fat, self.fmt.fat_type, cluster)

    def _make_dir_entry(
        self,
        name: str,
        ext: str,
        attrs: int,
        first_cluster: int,
        size: int,
        dt: datetime | None = None,
    ) -> bytes:
        """Create a 32-byte directory entry."""
        e = bytearray(32)
        name_bytes = name.encode("ascii").ljust(8)
        ext_bytes = ext.encode("ascii").ljust(3)
        e[0:8] = name_bytes
        e[8:11] = ext_bytes
        e[11] = attrs
        if dt is None:
            dt = datetime.now()
        dos_time, dos_date = encode_dos_time(dt)
        struct.pack_into("<H", e, 22, dos_time)
        struct.pack_into("<H", e, 24, dos_date)
        struct.pack_into("<H", e, 26, first_cluster)
        struct.pack_into("<I", e, 28, size)
        return bytes(e)

    def _ensure_dir(self, path: str) -> int:
        """Ensure a directory exists, return its first cluster."""
        if path == "" or path == "/":
            return 0  # Root directory

        parts = path.strip("/").split("/")
        current_path = ""
        parent_cluster = 0

        for part in parts:
            current_path = f"{current_path}/{part}" if current_path else part
            if current_path in self._dir_entries:
                # Find the directory entry
                for entry in self._dir_entries[current_path]:
                    if entry.is_dir and entry.name == part.upper()[:8]:
                        parent_cluster = entry.first_cluster
                        break
                continue

            # Create new directory
            name, ext = truncate_to_83(part, self._name_counts)
            # Allocate one cluster for the directory
            clusters = self._allocate_clusters(self.fmt.bytes_per_sector)
            first_cluster = clusters[0] if clusters else 0
            if first_cluster == 0:
                raise OSError("No free clusters for directory")
            # Create . and .. entries
            dot_entry = self._make_dir_entry(".       ", "", ATTR_DIRECTORY, first_cluster, 0)
            dotdot_cluster = parent_cluster if parent_cluster >= 2 else 0
            dotdot_entry = self._make_dir_entry("..      ", "", ATTR_DIRECTORY, dotdot_cluster, 0)
            padding = b"\x00" * (self.fmt.bytes_per_sector - 64)
            self._data_written[first_cluster] = dot_entry + dotdot_entry + padding

            # Add to parent directory
            if parent_cluster == 0:
                self._add_root_entry(name, ext, ATTR_DIRECTORY, first_cluster, 0)
            else:
                self._add_subdir_entry(parent_cluster, name, ext, ATTR_DIRECTORY, first_cluster, 0)

            self._dir_entries[current_path] = [
                FATDirEntry(
                    name=name.rstrip(),
                    extension=ext.rstrip(),
                    attributes=ATTR_DIRECTORY,
                    first_cluster=first_cluster,
                    size=0,
                    mtime=datetime.now().timestamp(),
                    is_dir=True,
                )
            ]
            parent_cluster = first_cluster

        return parent_cluster

    def _add_root_entry(
        self, name: str, ext: str, attrs: int, first_cluster: int, size: int
    ) -> None:
        """Add an entry to the root directory."""
        entry = self._make_dir_entry(name, ext, attrs, first_cluster, size)
        # Find first free slot
        for i in range(self.fmt.root_entries):
            offset = i * 32
            if self._root_dir[offset] == 0x00 or self._root_dir[offset] == 0xE5:
                self._root_dir[offset : offset + 32] = entry
                break

    def _add_subdir_entry(
        self, dir_cluster: int, name: str, ext: str, attrs: int, first_cluster: int, size: int
    ) -> None:
        """Add an entry to a subdirectory."""
        # Read existing directory data
        data = self._read_subdir(dir_cluster)
        # Find free slot
        for i in range(0, len(data), 32):
            if data[i] == 0x00 or data[i] == 0xE5:
                entry = self._make_dir_entry(name, ext, attrs, first_cluster, size)
                data[i : i + 32] = entry
                self._write_subdir(dir_cluster, data)
                break

    def _read_subdir(self, cluster: int) -> bytearray:
        """Read subdirectory data."""
        if cluster in self._data_written:
            return bytearray(self._data_written[cluster])
        return bytearray(self.fmt.bytes_per_sector)

    def _write_subdir(self, cluster: int, data: bytearray) -> None:
        """Write subdirectory data."""
        self._data_written[cluster] = bytes(data)

    def add_file(self, path: str, data: bytes) -> None:
        """Add a file to the image."""
        # Ensure parent directories exist
        parts = path.strip("/").split("/")
        dir_path = "/".join(parts[:-1])
        filename = parts[-1]
        parent_cluster = self._ensure_dir(dir_path)

        name, ext = truncate_to_83(filename, self._name_counts)
        clusters = self._allocate_clusters(len(data))
        first_cluster = clusters[0] if clusters else 0

        if clusters:
            self._write_cluster_data(first_cluster, data)

        if parent_cluster == 0:
            self._add_root_entry(name, ext, ATTR_ARCHIVE, first_cluster, len(data))
        else:
            self._add_subdir_entry(
                parent_cluster, name, ext, ATTR_ARCHIVE, first_cluster, len(data)
            )

    def add_dir(self, path: str) -> None:
        """Add a directory to the image."""
        self._ensure_dir(path.strip("/"))

    def finalize(self) -> bytes:
        """Finalize and return the complete floppy image."""
        # Write FAT copies
        fat_start = self.fmt.reserved_sectors * self.fmt.bytes_per_sector
        for i in range(self.fmt.fat_count):
            start = fat_start + i * self.fmt.fat_sectors * self.fmt.bytes_per_sector
            self._image[start : start + len(self._fat)] = self._fat

        # Write root directory
        root_start = self.fmt.root_dir_sector * self.fmt.bytes_per_sector
        self._image[root_start : root_start + len(self._root_dir)] = self._root_dir

        # Write data clusters
        for cluster, data in self._data_written.items():
            sector = self.fmt.data_start_sector + (cluster - 2) * self.fmt.sectors_per_cluster
            offset = sector * self.fmt.bytes_per_sector
            self._image[offset : offset + len(data)] = data

        return bytes(self._image)


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def detect_floppy_format(size: int, data_prefix: bytes) -> FloppyFormat | None:
    """Detect floppy format from image size and boot sector prefix."""
    # Match by size first
    candidates = [fmt for fmt in FLOPPY_FORMATS.values() if fmt.capacity_bytes == size]
    if not candidates:
        return None

    if len(data_prefix) < 512:
        return candidates[0]

    # Verify boot signature
    if data_prefix[510] != 0x55 or data_prefix[511] != 0xAA:
        return None

    # Verify BPB matches
    try:
        bpb = data_prefix[0x0B:]
        bytes_per_sector = struct.unpack_from("<H", bpb, 0)[0]
        if bytes_per_sector != 512:
            return None
        sectors_per_cluster = bpb[2]
        if sectors_per_cluster not in (1, 2, 4, 8):
            return None
        reserved = struct.unpack_from("<H", bpb, 3)[0]
        if reserved != 1:
            return None
        fat_count = bpb[5]
        if fat_count != 2:
            return None
        root_entries = struct.unpack_from("<H", bpb, 6)[0]
        fat_sectors = struct.unpack_from("<H", bpb, 11)[0]
        media = bpb[10]

        for fmt in candidates:
            if (
                fmt.root_entries == root_entries
                and fmt.fat_sectors == fat_sectors
                and fmt.media_descriptor == media
            ):
                return fmt
    except Exception:
        pass

    return candidates[0]
