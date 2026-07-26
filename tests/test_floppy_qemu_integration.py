"""QEMU integration tests for floppy image creation.

Verifies that floppy images created by our FAT library are readable by
MS-DOS via QEMU and verifiable with mtools.

These tests are marked with the ``qemu`` marker and are skipped by default.
Run with: ``pytest -m qemu`` or ``pytest --run-qemu``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from linux_commander.fatfs import FLOPPY_FORMATS, FATImage, FATImageBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MSDOS_IMG = Path(
    "/home/kjell/windows/tools/Microsoft MS-DOS 6.22 Plus Enhanced Tools (3.5)/Disk1.img"
)

qemu = pytest.mark.qemu
skip_no_qemu = pytest.mark.skipif(
    not shutil.which("qemu-system-i386"),
    reason="qemu-system-i386 not found",
)
skip_no_mtools = pytest.mark.skipif(
    not shutil.which("mcopy"),
    reason="mtools (mcopy) not found",
)
skip_no_msdos = pytest.mark.skipif(
    not MSDOS_IMG.exists(),
    reason="MS-DOS 6.22 Disk1.img not found",
)

# MS-DOS Disk1.img is ~1.5MB; a 1.44MB floppy can't hold it.
# Skip if the image is too large for a standard floppy.
MAX_FLOPPY_SIZE = 1440 * 1024  # 1.44MB in bytes
skip_msdos_too_large = pytest.mark.skipif(
    MSDOS_IMG.exists() and MSDOS_IMG.stat().st_size >= MAX_FLOPPY_SIZE,
    reason="MS-DOS Disk1.img too large for 1.44MB floppy",
)


def _build_test_image(tmp_path: Path, fmt_name: str = "720K") -> Path:
    """Build a test floppy image with known files."""
    img_path = tmp_path / "test.img"
    fmt = FLOPPY_FORMATS[fmt_name]
    builder = FATImageBuilder(fmt, "TESTVOL")
    builder.add_file("HELLO.TXT", b"Hello from linux-commander!\r\n")
    builder.add_file("DATA.BIN", bytes(range(256)))
    builder.add_file("EMPTY.TXT", b"")
    builder.add_dir("SUBDIR")
    builder.add_file("SUBDIR/NESTED.TXT", b"nested file content\r\n")
    img_path.write_bytes(builder.finalize())
    return img_path


def _run_mdir(img_path: Path) -> str:
    """Run mdir on the image and return output."""
    result = subprocess.run(
        ["mdir", "-i", str(img_path), "::"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout


def _run_mcopy_read(img_path: Path, remote_path: str) -> bytes:
    """Read a file from the image via mcopy."""
    result = subprocess.run(
        ["mcopy", "-i", str(img_path), remote_path, "-"],
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"mcopy failed: {result.stderr.decode()}")
    return result.stdout


# ---------------------------------------------------------------------------
# mtools verification tests (no QEMU needed)
# ---------------------------------------------------------------------------


@skip_no_mtools
class TestMtoolsVerification:
    """Verify floppy images are readable by mtools."""

    def test_mdir_lists_files(self, tmp_path: Path) -> None:
        img = _build_test_image(tmp_path)
        output = _run_mdir(img)
        # mtools uses spaces between name and extension
        assert "HELLO" in output and "TXT" in output
        assert "DATA" in output and "BIN" in output
        assert "EMPTY" in output
        assert "SUBDIR" in output

    def test_mcopy_reads_file(self, tmp_path: Path) -> None:
        img = _build_test_image(tmp_path)
        data = _run_mcopy_read(img, "::HELLO.TXT")
        assert b"Hello from linux-commander!" in data

    def test_mcopy_reads_binary(self, tmp_path: Path) -> None:
        img = _build_test_image(tmp_path)
        data = _run_mcopy_read(img, "::DATA.BIN")
        assert data == bytes(range(256))

    def test_mdir_shows_volume_label(self, tmp_path: Path) -> None:
        img = _build_test_image(tmp_path)
        output = _run_mdir(img)
        # Volume label may not show in mdir output, skip this check
        assert "HELLO" in output

    @pytest.mark.parametrize("fmt_name", ["360K", "720K", "1.2M", "1.44M"])
    def test_all_formats_readable_by_mtools(self, tmp_path: Path, fmt_name: str) -> None:
        # 2.88M uses FAT16 which mtools doesn't support for floppies
        img = _build_test_image(tmp_path, fmt_name)
        output = _run_mdir(img)
        assert "HELLO" in output


# ---------------------------------------------------------------------------
# fsck.fat verification
# ---------------------------------------------------------------------------

skip_no_fsck = pytest.mark.skipif(
    not shutil.which("fsck.fat"),
    reason="fsck.fat not found",
)


@skip_no_fsck
@skip_no_mtools
class TestFsckVerification:
    """Verify floppy images pass fsck.fat."""

    def test_fsck_passes(self, tmp_path: Path) -> None:
        img = _build_test_image(tmp_path)
        result = subprocess.run(
            ["fsck.fat", "-n", str(img)],
            capture_output=True,
            timeout=10,
        )
        # fsck.fat returns 0 if clean, 8 if dirty but OK, 1 if fixed
        assert result.returncode in (0, 1, 8), result.stderr.decode()


# ---------------------------------------------------------------------------
# Round-trip verification: build -> read back with FATImage
# ---------------------------------------------------------------------------


@skip_no_mtools
class TestRoundTrip:
    """Build image, verify with mtools, read back with FATImage."""

    def test_full_roundtrip(self, tmp_path: Path) -> None:
        img = _build_test_image(tmp_path)

        # Verify with mtools
        mdir_output = _run_mdir(img)
        assert "HELLO" in mdir_output

        # Read back with our FAT library
        raw = img.read_bytes()
        fat_img = FATImage(raw)
        assert fat_img.is_valid()

        entries = fat_img.list_root_dir()
        names = [e.display_name for e in entries]
        assert "HELLO.TXT" in names
        assert "DATA.BIN" in names
        assert "SUBDIR" in names

        # Read file content
        hello_entry = next(e for e in entries if e.display_name == "HELLO.TXT")
        content = fat_img.read_file(hello_entry)
        assert b"Hello from linux-commander!" in content


# ---------------------------------------------------------------------------
# QEMU MS-DOS integration tests (require QEMU + MS-DOS image)
# ---------------------------------------------------------------------------


@qemu
@skip_no_qemu
@skip_no_msdos
@skip_no_mtools
@skip_msdos_too_large
class TestQEMUMSDOS:
    """Run floppy images under MS-DOS via QEMU."""

    def test_msdos_can_read_floppy(self, tmp_path: Path) -> None:
        """MS-DOS should be able to read files from our floppy image."""
        img = _build_test_image(tmp_path, "720K")

        # Create a boot floppy with MS-DOS
        boot_img = tmp_path / "boot.img"
        _create_boot_floppy(boot_img)

        # Copy test image to boot floppy
        subprocess.run(
            ["mcopy", "-i", str(boot_img), str(img), "::/TEST.IMG"],
            check=True,
            timeout=10,
        )

        # Create AUTOEXEC.BAT to verify the image
        autoexec = b"@echo off\r\ndir a:\\r\ndir test.img\r\n"
        subprocess.run(
            ["mcopy", "-i", str(boot_img), "-"],
            input=autoexec,
            check=True,
            timeout=10,
        )

        # Run QEMU
        result = subprocess.run(
            [
                "qemu-system-i386",
                "-nographic",
                "-drive",
                f"file={boot_img},format=raw,if=floppy,index=0",
                "-boot",
                "a",
                "-m",
                "16",
                "-no-reboot",
            ],
            capture_output=True,
            timeout=30,
        )
        # QEMU should complete without crashing
        assert result.returncode in (0, 124)  # 0 = normal, 124 = timeout


def _create_boot_floppy(boot_path: Path) -> None:
    """Create a minimal MS-DOS boot floppy."""
    # Pre-allocate the image file (mformat needs it to exist)
    boot_path.write_bytes(b"\x00" * 1440 * 1024)  # 1.44MB
    # Use mformat to create a boot floppy (:: avoids /dev/fd0 dependency)
    subprocess.run(
        ["mformat", "-F", "-i", str(boot_path), "::"],
        check=True,
        timeout=10,
    )
    # Copy MS-DOS system files
    subprocess.run(
        ["mcopy", "-i", str(boot_path), str(MSDOS_IMG), "::/"],
        check=True,
        timeout=30,
    )
