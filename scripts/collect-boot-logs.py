#!/usr/bin/env python3
"""Read UEFI FAT boot logs from a mounted SD, whole-disk image, or readable device.

Never modifies the source. Run after powering off the board / stopping QEMU.
For an MBR partition device (e.g. /dev/disk6s1), pass --offset 0 explicitly.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from prepare import ROOT
from sd_image import image_spec, read_mbr, require_tools, sha256, write_json

NAME = re.compile(r"BOOT[0-9]{4}\.LOG", re.IGNORECASE)


def summarize(path):
    text = path.read_text(errors="replace").replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    checkpoints = [line for line in lines if line.startswith("[")]
    return {"file": path.name, "size": path.stat().st_size, "sha256": sha256(path),
            "last_checkpoint": checkpoints[-1] if checkpoints else None,
            "uefi_handoff_recorded": "[SD-LOG] END UEFI;" in text,
            "boot_errors": [line for line in lines if "[BOOT-ERROR]" in line or "ERROR -" in line],
            "kernel_success": "unknown: this file covers UEFI only"}


def collect(source, output, volume=False, offset=None):
    source = Path(source).resolve()
    output = Path(output).resolve()
    if output == source or (source.is_dir() and output.is_relative_to(source)):
        raise ValueError("Evidence directory must be outside the SD / source directory")
    output.mkdir(parents=True, exist_ok=False)
    result = {"source": str(source), "collected_utc": datetime.now(timezone.utc).isoformat(),
              "source_modified": False, "logs": []}
    try:
        if volume:
            if not source.is_dir():
                raise FileNotFoundError(f"Mounted volume directory not found: {source}")
            for path in sorted((source / "redexos/logs").glob("*")):
                if NAME.fullmatch(path.name) and path.is_file():
                    shutil.copy2(path, output / path.name.upper())
            metadata = source / "redexos/image-inputs.json"
            if metadata.is_file():
                shutil.copy2(metadata, output / "image-inputs.json")
        else:
            require_tools("mdir", "mcopy")
            # Also checks access before spawning mtools; permission errors are not "no logs".
            with source.open("rb") as stream:
                if not stream.read(512):
                    raise ValueError("Source is empty")
            if offset is None:
                mbr = read_mbr(source)
                if mbr["partition_type"] not in (0xEF, 0x0B, 0x0C) or not mbr["sectors"]:
                    raise ValueError("Expected a FAT/ESP first MBR partition; specify --offset for a FAT volume")
                offset = mbr["start_lba"] * 512
            spec = image_spec(source, offset)
            result["partition_offset"] = offset
            # Validate FAT separately: a damaged / unreadable FAT is not an empty log directory.
            subprocess.run(["mdir", "-i", spec, "::/"], check=True, capture_output=True, text=True)
            listing = subprocess.run(["mdir", "-b", "-i", spec, "::/redexos/logs/"], capture_output=True, text=True)
            if listing.returncode and "not found" not in listing.stderr.lower():
                raise RuntimeError(listing.stderr.strip())
            for row in sorted(listing.stdout.splitlines()):
                name = row.rsplit("/", 1)[-1]
                if NAME.fullmatch(name):
                    subprocess.run(["mcopy", "-i", spec, f"::/redexos/logs/{name}", str(output / name.upper())],
                                   check=True, capture_output=True, text=True)
            subprocess.run(["mcopy", "-i", spec, "::/redexos/image-inputs.json", str(output / "image-inputs.json")],
                           capture_output=True, text=True)
        result["logs"] = [summarize(path) for path in sorted(output.glob("BOOT*.LOG"))]
        result["status"] = "collected" if result["logs"] else "no_logs"
    except Exception as error:
        result.update(status="failed", error=str(error))
        write_json(output / "report.json", result)
        raise
    write_json(output / "report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--volume", type=Path, help="Mounted boot FAT root, e.g. /Volumes/REDOXPI5")
    source.add_argument("--image", type=Path, help="Whole SD image or readable raw device; opens read-only")
    parser.add_argument("--offset", type=lambda value: int(value, 0), help="FAT byte offset (0 for a partition)")
    parser.add_argument("--output", type=Path, help="New host directory; must not be on the SD")
    args = parser.parse_args()
    if args.offset is not None and args.offset < 0:
        parser.error("--offset must be non-negative")
    output = args.output
    if output is None:
        parent = ROOT / "artifacts/hardware"
        parent.mkdir(parents=True, exist_ok=True)
        # Reserve a unique name; collect() owns directory creation.
        output = Path(tempfile.mkdtemp(prefix="sd-boot-logs-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=parent))
        output.rmdir()
    try:
        result = collect(args.volume or args.image, output, volume=args.volume is not None, offset=args.offset)
    except PermissionError:
        print(f"Cannot read {args.image or args.volume}. No source was modified. On macOS, use a read-only mounted FAT volume or run from a Terminal allowed to access removable media. See docs/boot-logs-v3.md. Evidence: {output}", file=sys.stderr)
        return 1
    print(f"Evidence: {output.resolve()}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["logs"] else 2


if __name__ == "__main__":
    sys.exit(main())
