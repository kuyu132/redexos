#!/usr/bin/env python3
"""Build and verify a locked Raspberry Pi handoff-DTB candidate."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import urllib.request

from board_dtb import validate
from prepare import ROOT, sha256


def fetch(url, destination, checksum):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and sha256(destination) == checksum:
        return
    partial = destination.with_suffix(destination.suffix + ".partial")
    with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as output:
        shutil.copyfileobj(response, output)
    if sha256(partial) != checksum:
        raise ValueError(f"Downloaded checksum mismatch: {destination.name}")
    partial.replace(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", choices=("pi4b", "pi5"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    lock = json.loads((ROOT / "sources.lock.json").read_text())
    source = lock["firmware_device_tree"]
    cache = ROOT / "work/board-dtb/reference" / source["rev"]
    needed = ["bcm2711-rpi-4-b.dtb", "overlays/disable-bt.dtbo"] if args.board == "pi4b" else ["bcm2712-rpi-5-b.dtb"]
    for relative in needed:
        fetch(f'{source["raw_base"]}/{relative}', cache / relative, source["files"][relative])

    if args.output:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = ROOT / "artifacts/board-dtb" / f"{stamp}-{args.board}-{secrets.token_hex(4)}"
        output.mkdir(parents=True)
    dtb = output / "redox.dtb"
    if args.board == "pi4b":
        tool = shutil.which("fdtoverlay")
        if tool is None:
            raise RuntimeError("fdtoverlay is required (Ubuntu package: device-tree-compiler)")
        subprocess.run([tool, "-i", str(cache / needed[0]), "-o", str(dtb), str(cache / needed[1])], check=True)
    else:
        shutil.copyfile(cache / needed[0], dtb)

    result = validate(args.board, dtb)
    config = ROOT / "board-config" / args.board / "config.txt"
    shutil.copyfile(config, output / "config.txt")
    result.update({
        "source_repository": source["url"],
        "source_revision": source["rev"],
        "input_sha256": {relative: source["files"][relative] for relative in needed},
        "dtb_sha256": sha256(dtb),
        "config_sha256": sha256(output / "config.txt"),
        "offline_contract_passed": True,
        "hardware_validated": False,
        "note": "Firmware/UEFI must publish its actual final DTB; capture and compare it on hardware.",
    })
    (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    (output / "SHA256SUMS").write_text(
        f'{result["dtb_sha256"]}  redox.dtb\n{result["config_sha256"]}  config.txt\n'
    )
    print(json.dumps(result, indent=2))
    print(f"Board DTB evidence: {output}")


if __name__ == "__main__":
    main()
