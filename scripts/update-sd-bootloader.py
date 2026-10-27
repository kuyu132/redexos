#!/usr/bin/env python3
"""Create a new SD image with a replaced EFI while preserving the exact live filesystem."""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import shutil
import struct
import tempfile

from prepare import ROOT
from sd_image import copy_out, image_spec, read_mbr, require_tools, run, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-manifest", type=Path, required=True)
    parser.add_argument("--efi", type=Path, required=True)
    parser.add_argument("--revision", default="v2")
    args = parser.parse_args()
    require_tools("mcopy", "zstd")
    base = json.loads(args.base_manifest.read_text())
    assert base["board"] in ("pi4b", "pi5")
    source = Path(base["image"])
    if source.stat().st_size != base["image_size"] or sha256(source) != base["image_sha256"]:
        raise RuntimeError("Base image does not match its manifest")
    efi = args.efi.resolve()
    binary = efi.read_bytes()
    if binary[:2] != b"MZ":
        raise ValueError("EFI is not a PE executable")
    pe = struct.unpack_from("<I", binary, 0x3c)[0]
    if binary[pe:pe + 4] != b"PE\0\0" or struct.unpack_from("<H", binary, pe + 4)[0] != 0xaa64:
        raise ValueError("EFI must target AArch64")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = Path(tempfile.mkdtemp(prefix=f"{stamp}-{base['board']}-", dir=ROOT / "artifacts/sd-image"))
    print(f"Evidence: {output}", flush=True)
    image = output / source.name
    shutil.copy2(source, image)
    shutil.copy2(args.base_manifest, output / "base-manifest.json")
    offset = base["layout"]["partition_offset"]
    manifest = copy.deepcopy(base)
    for key in ("boot_diagnostics", "boot_log_tests", "component_build_evidence", "source_snapshot",
                "source_snapshot_sha256", "compressed_image", "compressed_sha256", "compressed_size"):
        manifest.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="redexos-efi-update-") as tmp:
        temporary = Path(tmp)
        inside_path = temporary / "image-inputs.json"
        copy_out(source, offset, "/redexos/image-inputs.json", inside_path)
        inside = json.loads(inside_path.read_text())
        inside.update(created_utc=datetime.now(timezone.utc).isoformat(), bootaa64_sha256=sha256(efi),
                      hardware_validated=False, boot_diagnostic_revision=args.revision,
                      base_image_sha256=base["image_sha256"])
        write_json(inside_path, inside)
        first_boot = temporary / "FIRST-BOOT.txt"
        first_boot.write_text(runpy.run_path(str(ROOT / "scripts/prepare-sd-image.py"))["first_boot_text"](
            base["board"], base["firmware"]))
        replacements = {"EFI/BOOT/BOOTAA64.EFI": efi,
                        "redexos/image-inputs.json": inside_path, "redexos/FIRST-BOOT.txt": first_boot}
        for guest, host in replacements.items():
            run("mcopy", "-o", "-i", image_spec(image, offset), host, f"::/{guest}")
            manifest["staged_files"][guest] = {"sha256": sha256(host), "size": host.stat().st_size}
        # Read back every recorded file, including the unmodified 512 MiB filesystem.
        for guest, expected in manifest["staged_files"].items():
            destination = temporary / "readback"
            copy_out(image, offset, f"/{guest}", destination)
            if destination.stat().st_size != expected["size"] or sha256(destination) != expected["sha256"]:
                raise RuntimeError(f"Updated image file mismatch: {guest}")
        manifest["critical_readback"]["/EFI/BOOT/BOOTAA64.EFI"] = sha256(efi)
    if read_mbr(image) != read_mbr(source):
        raise RuntimeError("Updating EFI unexpectedly changed the partition layout")
    manifest.update(image=str(image), image_sha256=sha256(image), image_size=image.stat().st_size,
                    created_utc=inside["created_utc"], bootaa64_sha256=sha256(efi),
                    critical_readback_passed=True, all_recorded_files_readback_passed=True,
                    hardware_validated=False, base_image_sha256=base["image_sha256"],
                    exact_live_filesystem_preserved=True, boot_diagnostic_revision=args.revision,
                    serial_test_command=f"python3 scripts/verify-board.py {base['board']} DEVICE OUTPUT --image-manifest {output / 'manifest.json'}")
    compressed = image.with_suffix(".img.zst")
    run("zstd", "-T0", "-15", image, "-o", compressed)
    manifest.update(compressed_image=str(compressed), compressed_sha256=sha256(compressed),
                    compressed_size=compressed.stat().st_size)
    write_json(output / "manifest.json", manifest)
    (output / "SHA256SUMS").write_text(f"{manifest['image_sha256']}  {image.name}\n{manifest['compressed_sha256']}  {compressed.name}\n")
    print(json.dumps({key: manifest[key] for key in ("image", "image_sha256", "exact_live_filesystem_preserved")}), flush=True)


if __name__ == "__main__":
    main()
