#!/usr/bin/env python3
"""Create a checksum-locked Raspberry Pi 4B/5 Redox SD-card image."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import tomllib

from board_dtb import validate
from prepare import ROOT
from sd_image import (
    copy_out, fetch, format_and_copy, read_mbr, require_tools, run, safe_extract,
    sha256, tree_hashes, write_json, write_mbr,
)


def resolve_payload(args):
    if args.payload:
        if args.efi:
            raise ValueError("--payload cannot be combined with --efi")
        live = args.payload / "redox-live.iso"
        efi = args.payload / "EFI/BOOT/BOOTAA64.EFI"
    elif args.live or args.efi:
        if not args.live or not args.efi:
            raise ValueError("--live and --efi must be supplied together")
        live, efi = args.live, args.efi
    else:
        metadata = json.loads((ROOT / "work/qemu-arm64/image.json").read_text())
        live = Path(metadata["image"]).with_name("redox-live.iso")
        efi = Path(metadata["components"]) / "bootloader-live.efi"
    if not live.is_file() or not efi.is_file():
        raise ValueError(f"Payload missing: live={live}, efi={efi}")
    return live.resolve(), efi.resolve()


def add_config(stage, board):
    path = stage / "config.txt"
    original = path.read_text(errors="strict").replace("\r\n", "\n").rstrip() + "\n"
    additions = []
    for line in ("arm_64bit=1", "enable_uart=1", "uart_2ndstage=1"):
        key = line.split("=", 1)[0]
        if not any(item.strip().startswith(key + "=") for item in original.splitlines()):
            additions.append(line)
    if "dtoverlay=redox-console" not in original.splitlines():
        additions.append("dtoverlay=redox-console")
    banner = "\n# Redox early console and DT handoff contract.\n"
    path.write_text(original + banner + "\n".join(additions) + "\n")


def configured_file(path, guest_path):
    data = tomllib.loads(Path(path).read_text())
    return next(item for item in data.get("files", []) if item.get("path") == guest_path)


def prepare_board_live(board, source, temporary, configured_payload=False):
    destination = temporary / "redox-live.iso"
    shutil.copy2(source, destination)
    tool = ROOT / "tools/redox-image/target/release/redox-image"
    if not tool.is_file() and configured_payload:
        generated_common = ROOT / f"work/{board}/redox/config/aarch64/pi-port-common.toml"
        generated_board = ROOT / f"work/{board}/redox/config/aarch64/{board}/minimal.toml"
        if (not generated_common.is_file()
                or generated_common.read_text() != (ROOT / "config/common/minimal.toml").read_text()
                or not generated_board.is_file()
                or generated_board.read_text() != (ROOT / f"config/{board}/minimal.toml").read_text()):
            raise RuntimeError("Configured payload cannot be tied to the current generated board config")
        return destination, {
            "source_live_sha256": sha256(source),
            "prepared_live_sha256": sha256(destination),
            "method": "upstream board filesystem configuration",
            "inside_image_readback": False,
            "expected_files": [
                "/etc/port-target", "/usr/lib/board-smoke.sh", "/usr/lib/board-stability.sh",
            ],
        }
    if not tool.is_file():
        raise RuntimeError(
            "redox-image tool is missing; build the local image tooling before packaging "
            "so board test files can be injected and read back"
        )
    inputs = []
    common = ROOT / "config/common/minimal.toml"
    for guest in ("/usr/lib/board-smoke.sh", "/usr/lib/board-stability.sh"):
        entry = configured_file(common, guest)
        host = temporary / Path(guest).name
        host.write_text(entry["data"])
        run(tool, "put-exec", destination, guest, host)
        inputs.append((guest, host))
    target = configured_file(ROOT / f"config/{board}/minimal.toml", "/etc/port-target")
    host = temporary / "port-target"
    host.write_text(target["data"])
    run(tool, "put", destination, "/etc/port-target", host)
    inputs.append(("/etc/port-target", host))
    readback = {}
    for guest, host in inputs:
        actual = subprocess.check_output([str(tool), "cat", str(destination), guest])
        expected = host.read_bytes()
        if actual != expected:
            raise ValueError(f"Board live-image readback mismatch: {guest}")
        readback[guest] = {"sha256": sha256(host), "size": len(expected)}
    return destination, {
        "source_live_sha256": sha256(source),
        "prepared_live_sha256": sha256(destination),
        "redox_image_tool_sha256": sha256(tool),
        "method": "offline injection with exact readback",
        "inside_image_readback": True,
        "injected_and_read_back": readback,
    }


def build_offline_dtb(board, firmware, stage, temporary):
    overlay_source = ROOT / "board-config" / board / "redox-console-overlay.dts"
    overlay = stage / "overlays/redox-console.dtbo"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    run("dtc", "-q", "-@", "-I", "dts", "-O", "dtb", "-o", overlay, overlay_source)
    inputs = []
    for name in firmware["overlays"]:
        candidate = overlay if name == "redox-console" else stage / f"overlays/{name}.dtbo"
        if not candidate.is_file():
            raise ValueError(f"Firmware overlay missing: {candidate.relative_to(stage)}")
        inputs.append(candidate)
    source = stage / firmware["dtb"]
    output = temporary / "offline-final.dtb"
    run("fdtoverlay", "-i", source, "-o", output, *inputs)
    contract = validate(board, output)
    shutil.copy2(output, stage / "redexos/offline-final.dtb")
    contract.update({
        "base_dtb": firmware["dtb"],
        "base_dtb_sha256": sha256(source),
        "applied_overlays": firmware["overlays"],
        "offline_final_dtb_sha256": sha256(output),
        "hardware_validated": False,
    })
    return contract


def first_boot_text(board, firmware):
    connector = "GPIO14/15 PL011" if board == "pi4b" else "dedicated UART10 debug connector"
    return f"""Redox experimental {board} bring-up image

1. Connect HDMI and a USB keyboard before power-on.
   Optional serial capture: 3.3 V adapter to {connector}, 115200 8N1.
2. Enter UEFI with Esc, then set: {firmware['required_uefi_setting']}.
3. Save and reboot. Do not select ACPI mode: Redox currently requires the DTB.
4. Diagnostic bootloaders show [SD-READ] progress and the pending byte offset.
   UEFI checkpoints are saved to redexos/logs/BOOTnnnn.LOG on this FAT.
   v4 prints SD-IO call boundaries at reads #2 and #259; photograph the last line.
   Each boot uses a new file; existing logs are never overwritten.
   Check for [SD-LOG] BEGIN. DISABLED means disk logging is unavailable.
   After power-off, insert the SD into your computer and use collect-boot-logs.py.
   Logs end BEFORE ExitBootServices and do not prove kernel/userland success.
   Firmware hangs before logging/inside Write or Flush may leave a partial log.
   Photograph the last stage/offset or [BOOT-ERROR] if progress stops.
   When using serial capture, keep it running through boot and any failure.
5. Login as root/password and run:
     bash /usr/lib/board-smoke.sh
     bash /usr/lib/board-stability.sh 100

After the one-time UEFI setting, read back the flashed image with snapshot-sd.sh.
Also photograph UEFI settings; an SD snapshot cannot preserve off-card NVRAM.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", choices=("pi4b", "pi5"))
    payload = parser.add_mutually_exclusive_group()
    payload.add_argument("--payload", type=Path, help="Directory containing redox-live.iso and EFI/BOOT/BOOTAA64.EFI")
    payload.add_argument("--live", type=Path, help="Live filesystem image; pair with --efi")
    parser.add_argument("--efi", type=Path, help="AArch64 Redox live UEFI application")
    parser.add_argument("--output", type=Path, help="New evidence directory")
    parser.add_argument("--size-mib", type=int, default=768)
    parser.add_argument("--no-compress", action="store_true")
    parser.add_argument(
        "--configured-payload", action="store_true",
        help="Payload was just built from this board's generated filesystem config",
    )
    args = parser.parse_args()
    if args.configured_payload and args.payload is None:
        parser.error("--configured-payload requires --payload")
    require_tools("dtc", "fdtoverlay", "mformat", "mcopy", "mdir")
    live, efi = resolve_payload(args)
    lock = json.loads((ROOT / "board-firmware.lock.json").read_text())
    firmware = lock["boards"][args.board]
    archive_name = firmware["url"].rsplit("/", 1)[-1]
    archive = ROOT / "work/board-firmware/downloads" / archive_name
    fetch(firmware["url"], archive, firmware["sha256"], firmware["size"])

    if args.output:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = ROOT / "artifacts/sd-image" / f"{stamp}-{args.board}-{secrets.token_hex(4)}"
        output.mkdir(parents=True)
    image = output / f"redox-{args.board}-sd.img"

    with tempfile.TemporaryDirectory(prefix="redexos-sd-stage-") as temporary_name:
        temporary = Path(temporary_name)
        stage = temporary / "stage"
        stage.mkdir()
        safe_extract(archive, stage)
        add_config(stage, args.board)
        (stage / "EFI/BOOT").mkdir(parents=True, exist_ok=True)
        (stage / "redexos").mkdir(parents=True, exist_ok=True)
        board_live, live_preparation = prepare_board_live(
            args.board, live, temporary, configured_payload=args.configured_payload
        )
        shutil.copy2(efi, stage / "EFI/BOOT/BOOTAA64.EFI")
        shutil.copy2(board_live, stage / "redox-live.iso")
        (stage / "redexos/FIRST-BOOT.txt").write_text(first_boot_text(args.board, firmware))
        contract = build_offline_dtb(args.board, firmware, stage, temporary)
        inside = {
            "schema": 1,
            "board": args.board,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "firmware": firmware,
            "firmware_archive_sha256": sha256(archive),
            "redox_live_sha256": sha256(board_live),
            "live_preparation": live_preparation,
            "bootaa64_sha256": sha256(efi),
            "offline_dtb_contract": contract,
            "required_first_boot_setting": firmware["required_uefi_setting"],
            "hardware_validated": False,
        }
        write_json(stage / "redexos/image-inputs.json", inside)
        staged_files = tree_hashes(stage)
        layout = write_mbr(image, args.size_mib)
        spec = format_and_copy(image, layout["partition_offset"], f"REDOX{args.board.upper()}", stage)

        extracted = temporary / "verify"
        extracted.mkdir()
        critical = {
            "/EFI/BOOT/BOOTAA64.EFI": sha256(efi),
            "/redox-live.iso": sha256(board_live),
            "/RPI_EFI.fd": sha256(stage / "RPI_EFI.fd"),
            "/config.txt": sha256(stage / "config.txt"),
            "/overlays/redox-console.dtbo": sha256(stage / "overlays/redox-console.dtbo"),
        }
        for guest, expected in critical.items():
            destination = extracted / Path(guest).name
            copy_out(image, layout["partition_offset"], guest, destination)
            if sha256(destination) != expected:
                raise ValueError(f"SD image readback mismatch: {guest}")
        directory_listing = subprocess.check_output(["mdir", "-i", spec, "::/"], text=True)
        (output / "fat-root.txt").write_text(directory_listing)

    parsed_mbr = read_mbr(image)
    if parsed_mbr != {"partition_type": 0xEF, "start_lba": layout["partition_start_lba"], "sectors": layout["partition_sectors"]}:
        raise ValueError(f"Unexpected MBR readback: {parsed_mbr}")
    manifest = {
        **inside,
        "image": str(image),
        "image_sha256": sha256(image),
        "image_size": image.stat().st_size,
        "layout": layout,
        "staged_files": staged_files,
        "critical_readback": critical,
        "critical_readback_passed": True,
        "hardware_validated": False,
        "serial_test_command": f"python3 scripts/verify-board.py {args.board} DEVICE OUTPUT --image-manifest {output / 'manifest.json'}",
    }
    if not args.no_compress:
        require_tools("zstd")
        compressed = image.with_suffix(image.suffix + ".zst")
        run("zstd", "-T0", "-15", "-f", image, "-o", compressed)
        manifest["compressed_image"] = str(compressed)
        manifest["compressed_sha256"] = sha256(compressed)
        manifest["compressed_size"] = compressed.stat().st_size
    write_json(output / "manifest.json", manifest)
    (output / "SHA256SUMS").write_text(
        f'{manifest["image_sha256"]}  {image.name}\n' +
        (f'{manifest["compressed_sha256"]}  {Path(manifest["compressed_image"]).name}\n' if "compressed_image" in manifest else "")
    )
    print(json.dumps({key: manifest[key] for key in (
        "board", "image", "image_sha256", "image_size", "critical_readback_passed",
    )}, indent=2))
    print(f"SD image evidence: {output}")


if __name__ == "__main__":
    main()
