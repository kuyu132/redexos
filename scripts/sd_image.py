"""Helpers for deterministic Raspberry Pi MBR/FAT32 Redox boot images."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import urllib.request
import zipfile


SECTOR_SIZE = 512
PARTITION_START_LBA = 2048
PARTITION_TYPE_EFI = 0xEF


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def require_tools(*names):
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise RuntimeError(f"Missing tools: {', '.join(missing)}")


def fetch(url, destination, expected_sha256, expected_size=None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256(destination) == expected_sha256:
        if expected_size is None or destination.stat().st_size == expected_size:
            return
    partial = destination.with_suffix(destination.suffix + ".partial")
    request = urllib.request.Request(url, headers={"User-Agent": "redexos-sd-builder"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
        shutil.copyfileobj(response, output)
    if sha256(partial) != expected_sha256:
        raise ValueError(f"Firmware checksum mismatch: {destination.name}")
    if expected_size is not None and partial.stat().st_size != expected_size:
        raise ValueError(f"Firmware size mismatch: {destination.name}")
    partial.replace(destination)


def safe_extract(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            relative = Path(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Unsafe firmware archive member: {member.filename}")
            target = (destination / relative).resolve()
            if not target.is_relative_to(destination):
                raise ValueError(f"Firmware archive member escapes staging: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as input_stream, target.open("wb") as output:
                shutil.copyfileobj(input_stream, output)


def write_mbr(image, size_mib, start_lba=PARTITION_START_LBA):
    image = Path(image)
    size_bytes = size_mib * 1024 * 1024
    total_sectors = size_bytes // SECTOR_SIZE
    if size_bytes % SECTOR_SIZE or total_sectors <= start_lba + 65536:
        raise ValueError("Image must leave at least 32 MiB after the aligned partition start")
    partition_sectors = total_sectors - start_lba
    with image.open("wb") as stream:
        stream.truncate(size_bytes)
        entry = struct.pack(
            "<B3sB3sII",
            0,
            b"\x00\x02\x00",
            PARTITION_TYPE_EFI,
            b"\xfe\xff\xff",
            start_lba,
            partition_sectors,
        )
        stream.seek(446)
        stream.write(entry)
        stream.seek(510)
        stream.write(b"\x55\xaa")
    return {
        "partition_table": "MBR",
        "sector_size": SECTOR_SIZE,
        "partition_type": f"0x{PARTITION_TYPE_EFI:02x}",
        "partition_start_lba": start_lba,
        "partition_offset": start_lba * SECTOR_SIZE,
        "partition_sectors": partition_sectors,
        "image_size": size_bytes,
    }


def read_mbr(image):
    with Path(image).open("rb") as stream:
        sector = stream.read(SECTOR_SIZE)
    if len(sector) != SECTOR_SIZE or sector[510:512] != b"\x55\xaa":
        raise ValueError("Invalid MBR signature")
    boot, _, kind, _, start, sectors = struct.unpack("<B3sB3sII", sector[446:462])
    if boot not in (0, 0x80):
        raise ValueError("Invalid MBR boot flag")
    return {"partition_type": kind, "start_lba": start, "sectors": sectors}


def image_spec(image, offset):
    return f"{Path(image).resolve()}@@{offset}"


def format_and_copy(image, offset, label, staging):
    spec = image_spec(image, offset)
    run("mformat", "-i", spec, "-F", "-v", label, "::")
    children = sorted(Path(staging).iterdir())
    if not children:
        raise ValueError("Refusing to build an empty SD image")
    run("mcopy", "-o", "-s", "-i", spec, *children, "::/")
    return spec


def copy_out(image, offset, guest_path, destination):
    run("mcopy", "-o", "-i", image_spec(image, offset), f"::{guest_path}", destination)


def tree_hashes(root):
    root = Path(root)
    return {
        str(path.relative_to(root)): {"sha256": sha256(path), "size": path.stat().st_size}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
