#!/usr/bin/env python3
"""Exercise the actual EFI FAT logger across abrupt QEMU termination and reboot."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import select
import shutil
import subprocess
import sys
import tempfile
import time

from prepare import ROOT
from sd_image import copy_out, format_and_copy, sha256, write_json

collect = runpy.run_path(str(ROOT / "scripts/collect-boot-logs.py"))["collect"]
verify_userland = runpy.run_path(str(ROOT / "scripts/run-qemu-arm64.py"))["verify"]


def command(image, output, readonly=False):
    return ["qemu-system-aarch64", "-machine", "virt,gic-version=2,acpi=off,virtualization=on",
            "-accel", "tcg", "-cpu", "cortex-a76", "-smp", "1", "-m", "2048",
            "-bios", "/opt/homebrew/share/qemu/edk2-aarch64-code.fd",
            "-drive", f"file={image},format=raw,if=none,id=esp,cache=directsync" + (",readonly=on" if readonly else ""),
            "-device", "virtio-blk-device,drive=esp", "-display", "none",
            "-chardev", f"stdio,id=uart,signal=off,logfile={output / 'serial.log'}",
            "-serial", "chardev:uart", "-monitor", "none", "-net", "none", "-no-reboot"]


def abrupt_boot(image, output, marker, timeout):
    output.mkdir()
    cmd = command(image, output)
    write_json(output / "command.json", cmd)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    raw = bytearray()
    found = False
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([proc.stdout], [], [], 0.5)[0]:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                raw.extend(chunk)
                if marker in raw:
                    found = True
                    break
            elif proc.poll() is not None:
                break
    finally:
        # SIGKILL: no guest shutdown and no opportunity for QEMU to flush on exit.
        proc.kill()
        tail, _ = proc.communicate(timeout=10)
        raw.extend(tail)
        (output / "output.log").write_bytes(raw)
    if not found:
        raise RuntimeError(f"Never reached abort marker {marker!r}: {raw[-1500:]!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-manifest", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    manifest = json.loads(args.image_manifest.read_text())
    image = Path(manifest["image"])
    assert sha256(image) == manifest["image_sha256"]
    parent = ROOT / "artifacts/boot-log-tests"
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=parent))
    print(f"Evidence: {output}", flush=True)
    shutil.copy2(__file__, output / "verify-boot-log.py")
    shutil.copy2(ROOT / "scripts/collect-boot-logs.py", output / "collect-boot-logs.py")
    result = {"status": "running", "environment": "QEMU Cortex-A76 EL2", "hardware_validated": False,
              "image_sha256": manifest["image_sha256"], "tests": {}}
    try:
        existing = collect(image, output / "before-test")
        previous = {item["file"]: (output / "before-test" / item["file"]).read_bytes()
                    for item in existing["logs"]}
        with tempfile.TemporaryDirectory(prefix="redexos-log-test-") as tmp:
            tmp = Path(tmp)
            marker = b"[FS-READ] #259 block=0x2f70 returned=0x1000 processing"
            if args.baseline_manifest:
                old = json.loads(args.baseline_manifest.read_text())
                assert sha256(old["image"]) == old["image_sha256"]
                legacy = tmp / "legacy.img"
                shutil.copy2(old["image"], legacy)
                abrupt_boot(legacy, output / "legacy-abort", marker, args.timeout)
                report = collect(legacy, output / "legacy-readback")
                assert report["status"] == "no_logs", "Legacy baseline unexpectedly supports FAT boot logging"
                result["tests"]["legacy_red_control"] = "expected failure: no logs after reaching #259"
                print("RED control: v2 reached #259 but recovered no boot logs", flush=True)

            writable = tmp / "writable.img"
            shutil.copy2(image, writable)
            abrupt_boot(writable, output / "abrupt", marker, args.timeout)
            report = collect(writable, output / "after-abrupt")
            assert len(report["logs"]) == len(previous) + 1
            for name, data in previous.items():
                assert (output / "after-abrupt" / name).read_bytes() == data
            added = [item["file"] for item in report["logs"] if item["file"] not in previous]
            assert len(added) == 1
            first_name = added[0]
            first = (output / "after-abrupt" / first_name).read_bytes()
            assert b"[FS-READ] #259" in first, first[-1500:]
            assert b"[FS-HASH] valid=true" in first
            assert b"[SD-LOG] END" not in first
            result["tests"]["sigkill_readback"] = report
            print("PASS abrupt power-off simulation: FAT contains header result + #259 checkpoint", flush=True)

            boot = output / "reboot"
            boot.mkdir()
            cmd = command(writable, boot)
            write_json(boot / "command.json", cmd)
            checks = verify_userland(cmd, boot, args.timeout)
            report = collect(writable, output / "after-reboot")
            assert len(report["logs"]) == len(previous) + 2
            for name, data in previous.items():
                assert (output / "after-reboot" / name).read_bytes() == data
            assert (output / "after-reboot" / first_name).read_bytes() == first
            added = [item["file"] for item in report["logs"] if item["file"] not in previous and item["file"] != first_name]
            assert len(added) == 1
            second = (output / "after-reboot" / added[0]).read_text()
            assert "[BOOT-08]" in second and "[SD-LOG] END UEFI;" in second
            assert "[SD-IO]" not in second  # Firmware-call traces must not recursively write FAT.
            assert "QEMU_ARM64_SMOKE_PASS" not in second  # Kernel/userland are outside this sink.
            assert "QEMU_ARM64_SMOKE_PASS" in (boot / "serial-clean.log").read_text()
            result["tests"]["reboot_preserves_failed_attempt"] = {"logs": report, "userspace": checks}
            print("PASS reboot: old log byte-identical, new log closed before kernel, userspace smoke passes", flush=True)

            ro = output / "readonly"
            ro.mkdir()
            before = sha256(writable)
            cmd = command(writable, ro, readonly=True)
            write_json(ro / "command.json", cmd)
            checks = verify_userland(cmd, ro, args.timeout)
            assert "[SD-LOG] DISABLED:" in (ro / "serial-clean.log").read_text()
            assert sha256(writable) == before
            result["tests"]["readonly_boot"] = {"status": "passed", "userspace": checks, "image_unchanged": True}
            print("PASS write-protected FAT: diagnostic warning + successful userland, no disk change", flush=True)

            stage = tmp / "error-stage"
            (stage / "EFI/BOOT").mkdir(parents=True)
            copy_out(image, manifest["layout"]["partition_offset"], "/EFI/BOOT/BOOTAA64.EFI", stage / "EFI/BOOT/BOOTAA64.EFI")
            (stage / "redox-live.iso").write_bytes(bytes(128))
            invalid = tmp / "invalid.img"
            with invalid.open("wb") as file:
                file.truncate(64 * 1024 * 1024)
            format_and_copy(invalid, 0, "LOGTEST", stage)
            # Panic text follows BOOT-ERROR; waiting for it ensures the prior error record flushed.
            abrupt_boot(invalid, output / "error", b"SETUP PANIC", args.timeout)
            report = collect(invalid, output / "after-error", offset=0)
            assert len(report["logs"]) == 1
            assert any("SD-04 invalid live image size: 128" in line for line in report["logs"][0]["boot_errors"])
            result["tests"]["error_persisted"] = report
            print("PASS actual EFI failure: SD-04 error recovered from FAT after SIGKILL", flush=True)
        assert sha256(image) == manifest["image_sha256"]
        result.update(status="passed", release_image_unchanged=True)
    except Exception as error:
        result.update(status="failed", error=repr(error))
        raise
    finally:
        write_json(output / "result.json", result)


if __name__ == "__main__":
    main()
