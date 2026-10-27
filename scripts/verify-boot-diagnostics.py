#!/usr/bin/env python3
"""Test the final SD image and real EFI error paths on QEMU virt (not Pi hardware)."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import runpy
import select
import shutil
import subprocess
import sys
import tempfile
import time

from prepare import ROOT
from sd_image import copy_out, format_and_copy, sha256, write_json


def check_io_stages(text):
    lines = re.findall(r"\[SD-IO\] ([^\r\n]+)", text)
    expected = ["console returned; acquiring log lock", "formatting record", "formatted len=",
                "calling firmware Write", "firmware Write returned",
                "calling firmware Flush", "firmware Flush returned"]
    if len(lines) != 4 * len(expected):
        raise RuntimeError(f"Expected four complete I/O traces for reads 2/259, got {lines}")
    for i, line in enumerate(lines):
        if not line.startswith(expected[i % len(expected)]):
            raise RuntimeError(f"Unexpected I/O stage order: {lines}")


def visual_boot(image, firmware, output, timeout):
    """Capture the real GOP console during filesystem metadata reads."""
    import qemu_desktop_control

    with tempfile.TemporaryDirectory(prefix="rdx-gop-", dir="/tmp") as temporary:
        socket_path = Path(temporary) / "qmp.sock"
        command = ["qemu-system-aarch64", "-machine", "virt,gic-version=2,acpi=off",
                   "-accel", "tcg", "-cpu", "cortex-a72", "-smp", "1", "-m", "2048",
                   "-bios", str(firmware), "-drive",
                   f"file={image},format=raw,if=none,id=esp,snapshot=on,throttling.bps-read=16777216",
                   "-device", "virtio-blk-device,drive=esp", "-device", "ramfb",
                   "-display", "none", "-serial", "stdio", "-monitor", "none",
                   "-qmp", f"unix:{socket_path},server=on,wait=off", "-net", "none", "-no-reboot"]
        write_json(output / "gop-command.json", command)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        raw = bytearray()
        control = None
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if select.select([process.stdout], [], [], 1)[0]:
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if b"[FS-READ] #16 " in raw:
                        os.environ["QMP_SOCKET"] = str(socket_path)
                        qemu_desktop_control.EVIDENCE = output
                        control = qemu_desktop_control.QMP()
                        control.call("stop")
                        control.shot("gop-fs-progress.png")
                        return {"status": "captured", "screenshot": str(output / "gop-fs-progress.png"),
                                "visual_review": "pending", "read_throttle_bps": 16777216}
                elif process.poll() is not None:
                    break
            raise RuntimeError("GOP progress capture did not reach filesystem read 16")
        finally:
            if control is not None:
                control.stream.close()
                control.sock.close()
            process.terminate()
            try:
                tail, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                tail, _ = process.communicate()
            raw.extend(tail)
            (output / "gop-serial.log").write_bytes(raw)


def error_boot(command, output, required, timeout):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    raw = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if select.select([process.stdout], [], [], 1)[0]:
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                raw.extend(chunk)
                text = raw.decode("utf-8", "replace")
                if all(marker in text for marker in required):
                    if "[BOOT-01]" in text:
                        raise RuntimeError("Invalid image unexpectedly opened as RedoxFS")
                    return {"status": "passed", "required": required}
            elif process.poll() is not None:
                break
        raise RuntimeError(f"EFI error markers missing: {required}; tail={raw[-1000:]!r}")
    finally:
        process.terminate()
        try:
            tail, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            tail, _ = process.communicate()
        raw.extend(tail)
        output.write_bytes(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--firmware", type=Path, default=Path("/opt/homebrew/share/qemu/edk2-aarch64-code.fd"))
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    manifest = json.loads(args.image_manifest.read_text())
    image = Path(manifest["image"])
    if sha256(image) != manifest["image_sha256"]:
        raise RuntimeError("Source SD image hash differs from manifest")
    parent = ROOT / "artifacts/boot-diagnostics"
    parent.mkdir(parents=True, exist_ok=True)
    if args.output:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
    else:
        output = Path(tempfile.mkdtemp(
            prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=parent))
    print(f"Evidence: {output}", flush=True)
    result = {"status": "running", "environment": "qemu-virt", "hardware_validated": False,
              "image_sha256": manifest["image_sha256"], "tests": {}}
    metadata = {"image": str(image), "image_sha256": manifest["image_sha256"], "persistent": False}
    write_json(output / "qemu-image.json", metadata)
    try:
        command = [sys.executable, str(ROOT / "scripts/run-qemu-arm64.py"), "--verify",
                   "--image-metadata", str(output / "qemu-image.json"), "--firmware", str(args.firmware),
                   "--timeout", str(args.timeout)]
        completed = subprocess.run(command, capture_output=True, text=True)
        (output / "normal-run.log").write_text(completed.stdout + completed.stderr)
        completed.check_returncode()
        match = re.search(r"(?m)^Evidence: (.+)$", completed.stdout)
        if not match:
            raise RuntimeError("QEMU runner did not return evidence directory")
        normal = Path(match[1])
        text = (normal / "serial-clean.log").read_text()
        required = [f"[BOOT-DIAG {manifest.get('boot_diagnostic_revision', 'v2')}]", "[SD-ALLOC]", "[SD-READ]", "[SD-DONE]",
                    "[FS-CPU]", "[FS-RAM]", "[FS-HASH] valid=true", "[FS-CALL]", "[FS-READY]",
                    "[FS-OPEN] source=redox-live.iso", "[BOOT-01]", "[BOOT-03]", "[BOOT-04]",
                    "[BOOT-05]", "[BOOT-07]", "[BOOT-08]", "QEMU_ARM64_SMOKE_PASS"]
        if any(marker not in text for marker in required) or "[BOOT-ERROR]" in text:
            raise RuntimeError("Normal boot lacks diagnostics or contains an error")
        if manifest.get("boot_diagnostic_revision") == "v4":
            check_io_stages(text)
        offsets = [int(value, 16) for value in re.findall(r"\[SD-READ\].*?offset=(0x[0-9a-f]+)", text)]
        size = manifest["staged_files"]["redox-live.iso"]["size"]
        if offsets != list(range(0, size, 1024 * 1024)):
            raise RuntimeError("Missing or out-of-order live read offsets")
        if f"bytes={size}" not in text:
            raise RuntimeError("Live image completion byte count is missing")
        pending = re.findall(r"\[FS-READ\] #(\d+) block=(0x[0-9a-f]+) len=(0x[0-9a-f]+) pending", text)
        returned = re.findall(r"\[FS-READ\] #(\d+) block=(0x[0-9a-f]+) returned=(0x[0-9a-f]+) processing", text)
        if not pending or pending != returned or [int(row[0]) for row in pending] != list(range(1, len(pending) + 1)):
            raise RuntimeError("Filesystem read trace is incomplete or not paired")
        if f"[FS-READY] RedoxFS open returned; reads={len(pending)}" not in text:
            raise RuntimeError("Filesystem completion trace count differs")
        result["tests"]["normal"] = {"status": "passed", "evidence": str(normal),
                                    "read_calls": len(offsets), "filesystem_reads": len(pending)}
        write_json(output / "result.json", result)
        print("PASS final SD image: progress + kernel + userspace", flush=True)

        el2 = output / "el2-a76"
        el2.mkdir()
        command = json.loads((normal / "command.json").read_text())
        command[command.index("-machine") + 1] = "virt,gic-version=2,acpi=off,virtualization=on"
        command[command.index("-cpu") + 1] = "cortex-a76"
        command[command.index("-chardev") + 1] = f"stdio,id=uart,signal=off,logfile={el2 / 'serial.log'}"
        write_json(el2 / "command.json", command)
        checks = runpy.run_path(str(ROOT / "scripts/run-qemu-arm64.py"))["verify"](command, el2, args.timeout)
        el2_text = (el2 / "serial-clean.log").read_text()
        if "[FS-CPU] EL2" not in el2_text or "[FS-READY]" not in el2_text:
            raise RuntimeError("EL2 comparison did not confirm EL2 and RedoxFS opening")
        if manifest.get("boot_diagnostic_revision") == "v4":
            check_io_stages(el2_text)
        result["tests"]["el2-a76"] = {"status": "passed", "evidence": str(el2), "checks": checks}
        print("PASS Cortex-A76 / EL2: filesystem open + userspace", flush=True)

        with tempfile.TemporaryDirectory(prefix="redexos-efi-negative-") as temporary:
            temporary = Path(temporary)
            efi = temporary / "BOOTAA64.EFI"
            copy_out(image, manifest["layout"]["partition_offset"], "/EFI/BOOT/BOOTAA64.EFI", efi)
            if sha256(efi) != manifest["bootaa64_sha256"]:
                raise RuntimeError("Extracted diagnostic EFI hash mismatch")
            live = temporary / "source-live.iso"
            copy_out(image, manifest["layout"]["partition_offset"], "/redox-live.iso", live)
            with live.open("rb") as stream:
                stream.seek(2 * 1024 * 1024)
                bad_header = bytearray(stream.read(4096))
            # Preserve signature/version while invalidating the header checksum.
            bad_header[16] ^= 1
            out_of_range = bytearray(4096)
            out_of_range[512:520] = b"EFI PART"
            cases = {
                "missing": (None, ["[SD-MISSING]", "[BOOT-ERROR] FS-SCAN: no valid RedoxFS found"]),
                "truncated": (bytes(128), ["[BOOT-ERROR] SD-04 invalid live image size: 128 bytes"]),
                "invalid-header": (bytes(4096), ["[FS-HASH] valid=false", "[BOOT-ERROR] FS-SCAN: no valid RedoxFS found"]),
                "bad-header-checksum": (bad_header, ["[FS-HASH] valid=false", "[BOOT-ERROR] FS-SCAN: no valid RedoxFS found"]),
                "gpt-out-of-range": (out_of_range, ["[BOOT-ERROR] FS-RANGE block=0x200", "[BOOT-ERROR] FS-PROBE"]),
            }
            for name, (contents, markers) in cases.items():
                stage = temporary / name
                (stage / "EFI/BOOT").mkdir(parents=True)
                shutil.copy2(efi, stage / "EFI/BOOT/BOOTAA64.EFI")
                if contents is not None:
                    (stage / "redox-live.iso").write_bytes(contents)
                fixture = temporary / f"{name}.img"
                with fixture.open("wb") as stream:
                    stream.truncate(64 * 1024 * 1024)
                format_and_copy(fixture, 0, "EFITEST", stage)
                command = ["qemu-system-aarch64", "-machine", "virt,gic-version=2,acpi=off",
                           "-accel", "tcg", "-cpu", "cortex-a72", "-smp", "1", "-m", "2048",
                           "-bios", str(args.firmware), "-drive",
                           f"file={fixture},format=raw,if=none,id=esp,snapshot=on",
                           "-device", "virtio-blk-device,drive=esp", "-display", "none",
                           "-serial", "stdio", "-monitor", "none", "-net", "none", "-no-reboot"]
                write_json(output / f"{name}-command.json", command)
                result["tests"][name] = error_boot(command, output / f"{name}-serial.log", markers, args.timeout)
                print(f"PASS EFI error path: {name}", flush=True)
        result["tests"]["gop"] = visual_boot(image, args.firmware, output, args.timeout)
        print("CAPTURED GOP console progress; review gop-fs-progress.png", flush=True)
        result["status"] = "passed"
    except Exception as error:
        result.update(status="failed", error=str(error))
        raise
    finally:
        write_json(output / "result.json", result)


if __name__ == "__main__":
    main()
