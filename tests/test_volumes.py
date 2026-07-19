"""Tests for linux_commander.volumes's Linux /proc/mounts parser.

Uses a fixture mounts text (modeled on a real /proc/mounts) rather than
reading the actual file, per the project's testing standards.
"""

from pathlib import Path

from linux_commander.volumes import (
    Volume,
    _drive_letters_from_bitmask,
    _unescape_mount_path,
    parse_proc_mounts,
)

SAMPLE_MOUNTS = r"""proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
sys /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0
dev /dev devtmpfs rw,nosuid,relatime,size=8164244k,nr_inodes=2041061,mode=755,inode64 0 0
run /run tmpfs rw,nosuid,nodev,relatime,mode=755,inode64 0 0
devpts /dev/pts devpts rw,nosuid,noexec,relatime,gid=5,mode=620,ptmxmode=000 0 0
/dev/sda1 / btrfs rw,relatime,compress=zstd:1,space_cache=v2,subvolid=256,subvol=/@ 0 0
securityfs /sys/kernel/security securityfs rw,nosuid,nodev,noexec,relatime 0 0
tmpfs /dev/shm tmpfs rw,nosuid,nodev,inode64,usrquota 0 0
cgroup2 /sys/fs/cgroup cgroup2 rw,nosuid,nodev,noexec,relatime,nsdelegate 0 0
none /sys/fs/pstore pstore rw,nosuid,nodev,noexec,relatime 0 0
bpf /sys/fs/bpf bpf rw,nosuid,nodev,noexec,relatime,mode=700 0 0
systemd-1 /proc/sys/fs/binfmt_misc autofs rw,relatime,fd=43,pgrp=1,timeout=0 0 0
fusectl /sys/fs/fuse/connections fusectl rw,nosuid,nodev,noexec,relatime 0 0
tmpfs /tmp tmpfs rw,nosuid,nodev,size=8183464k,nr_inodes=1048576,inode64,usrquota 0 0
/dev/sda1 /home btrfs rw,relatime,compress=zstd:1,space_cache=v2,subvolid=257,subvol=/@home 0 0
/dev/sda1 /var/cache btrfs rw,relatime,compress=zstd:1,space_cache=v2,subvolid=258 0 0
nast-work /media/sf_nast-work vboxsf rw,nodev,relatime 0 0
tmpfs /run/user/1000 tmpfs rw,nosuid,nodev,relatime,size=1636692k,mode=700,uid=1000 0 0
gvfsd-fuse /run/user/1000/gvfs fuse.gvfsd-fuse rw,nosuid,nodev,relatime,user_id=1000 0 0
portal /run/user/1000/doc fuse.portal rw,nosuid,nodev,relatime,user_id=1000 0 0
/dev/sdb1 /media/My\040Backup vfat rw,relatime,uid=1000,gid=1000 0 0
"""


def test_unescape_mount_path_handles_octal_space_escape() -> None:
    assert _unescape_mount_path(r"/media/My\040Backup") == "/media/My Backup"


def test_unescape_mount_path_leaves_plain_paths_unchanged() -> None:
    assert _unescape_mount_path("/home") == "/home"


def test_parse_proc_mounts_filters_pseudo_filesystems() -> None:
    volumes = parse_proc_mounts(SAMPLE_MOUNTS)
    paths = {v.path for v in volumes}
    for pseudo_path in (
        Path("/proc"),
        Path("/sys"),
        Path("/dev"),
        Path("/run"),
        Path("/dev/pts"),
        Path("/sys/kernel/security"),
        Path("/dev/shm"),
        Path("/sys/fs/cgroup"),
        Path("/sys/fs/pstore"),
        Path("/sys/fs/bpf"),
        Path("/proc/sys/fs/binfmt_misc"),
        Path("/sys/fs/fuse/connections"),
        Path("/tmp"),
        Path("/run/user/1000"),
        Path("/run/user/1000/gvfs"),
        Path("/run/user/1000/doc"),
    ):
        assert pseudo_path not in paths, f"{pseudo_path} should have been filtered out"


def test_parse_proc_mounts_keeps_real_filesystems() -> None:
    volumes = parse_proc_mounts(SAMPLE_MOUNTS)
    paths = {v.path for v in volumes}
    assert Path("/") in paths
    assert Path("/home") in paths
    assert Path("/var/cache") in paths
    assert Path("/media/sf_nast-work") in paths  # vboxsf shared folder


def test_parse_proc_mounts_unescapes_paths_with_spaces() -> None:
    volumes = parse_proc_mounts(SAMPLE_MOUNTS)
    paths = {v.path for v in volumes}
    assert Path("/media/My Backup") in paths


def test_parse_proc_mounts_ignores_blank_and_short_lines() -> None:
    volumes = parse_proc_mounts("\n\nbad_line\n/dev/sda1 / btrfs rw 0 0\n")
    assert volumes == [Volume(label="/", path=Path("/"), kind="mount")]


# ---------------------------------------------------------------------------
# _drive_letters_from_bitmask (Windows helper, pure function)
# ---------------------------------------------------------------------------


def test_drive_letters_from_bitmask_cde() -> None:
    # bits 2 (C), 3 (D), 4 (E) set
    mask = (1 << 2) | (1 << 3) | (1 << 4)
    assert _drive_letters_from_bitmask(mask) == ["C:", "D:", "E:"]


def test_drive_letters_from_bitmask_ac() -> None:
    # bit 0 (A) and bit 2 (C)
    mask = (1 << 0) | (1 << 2)
    assert _drive_letters_from_bitmask(mask) == ["A:", "C:"]


def test_drive_letters_from_bitmask_zero() -> None:
    assert _drive_letters_from_bitmask(0) == []


def test_drive_letters_from_bitmask_all_26() -> None:
    mask = (1 << 26) - 1  # all bits 0-25
    result = _drive_letters_from_bitmask(mask)
    assert len(result) == 26
    assert result[0] == "A:"
    assert result[-1] == "Z:"
