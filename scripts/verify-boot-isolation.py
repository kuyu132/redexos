#!/usr/bin/env python3
"""Exercise isolation probes on host/QEMU copies; never access a physical device."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import tempfile
import time

from prepare import ROOT
from sd_image import copy_out, format_and_copy, image_spec, sha256, write_json


def config(mode, ident, log=False, preload=False, records=1):
    return (f"ISOLATE1\nid={ident}\nmode={mode}\nfat_log={'on' if log else 'off'}\n"
            f"preload={'live' if preload else 'none'}\nrecords={records}\n")


def expected_bytes(ident, size):
    seed = 2166136261
    for b in ident.encode("ascii"):
        seed = ((seed ^ b) * 16777619) & 0xFFFFFFFF
    return bytes(((seed + i * 131) ^ (i >> 8)) & 255 for i in range(size))


def put(image, source, guest):
    subprocess.run(["mcopy", "-o", "-i", image_spec(image, 0), str(source), "::/" + guest],
                   check=True, capture_output=True)


def set_config(image, text, tmp):
    path = tmp / "ISOLATE.CFG"
    path.write_text(text)
    put(image, path, "redexos/ISOLATE.CFG")


def boot(image, output, firmware, timeout, expected, required=(), readonly=False, screenshot=False, el2=True):
    output.mkdir()
    before = sha256(image)
    with tempfile.TemporaryDirectory(prefix="iso-qmp-", dir="/tmp") as tmp:
        qmp = Path(tmp) / "qmp.sock"
        command = ["qemu-system-aarch64", "-machine", "virt,gic-version=2,acpi=off" + (",virtualization=on" if el2 else ""),
                   "-cpu", "cortex-a76" if el2 else "cortex-a72", "-accel", "tcg", "-smp", "1", "-m", "2048",
                   "-bios", str(firmware), "-drive",
                   f"file={image},format=raw,if=none,id=esp,cache=directsync" + (",readonly=on" if readonly else ""),
                   "-device", "virtio-blk-device,drive=esp", "-display", "none", "-serial", "stdio",
                   "-monitor", "none", "-net", "none", "-no-reboot"]
        if screenshot:
            command += ["-device", "ramfb", "-qmp", f"unix:{qmp},server=on,wait=off"]
        write_json(output / "command.json", command)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        raw = bytearray()
        result = {"status": "running", "expected": expected, "image_before_sha256": before,
                  "hardware_validated": False, "expected_el": 2 if el2 else 1}
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if select.select([process.stdout], [], [], 0.5)[0]:
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    # Wait through final sink close before killing the VM.
                    if b"[ISO-HALT] Test ended. Photograph result; power off to retrieve files." in raw:
                        break
                elif process.poll() is not None:
                    break
            text = raw.decode("utf-8", "replace")
            if "[ISO-HALT] Test ended. Photograph result; power off to retrieve files." not in text:
                result["outcome"] = "timeout" if process.poll() is None else "process-exited"
                raise RuntimeError(f"Probe did not complete: {result['outcome']}; {text[-1000:]}")
            passed = re.findall(r"\[ISO-RESULT\] id=([A-Za-z0-9-]+) PASS", text)
            failed = re.findall(r"\[ISO-RESULT\] id=([A-Za-z0-9-]+) FAIL", text)
            outcome = "PASS" if len(passed) == 1 and not failed and "[ISO-ERROR]" not in text else "FAIL"
            result["outcome"] = outcome
            if outcome != expected or any(marker not in text for marker in required):
                raise RuntimeError(f"Unexpected probe result: expected {expected}, got {outcome}; {text[-1500:]}")
            if expected == "PASS" and f"[ISO-CPU] EL{2 if el2 else 1}" not in text:
                raise RuntimeError("Wrong exception-level environment")
            if screenshot:
                import qemu_desktop_control as control
                os.environ["QMP_SOCKET"] = str(qmp)
                control.EVIDENCE = output
                client = control.QMP()
                try:
                    # Console splitter/ramfb refresh can lag the UART marker.
                    # Keep the CPU running and wait for the rendered frame to settle.
                    previous, stable = None, 0
                    for _ in range(25):
                        client.shot("screen.png")
                        current = sha256(output / "screen.png")
                        stable = stable + 1 if current == previous else 0
                        if stable >= 3:
                            break
                        previous = current
                        time.sleep(0.2)
                    else:
                        raise RuntimeError("GOP screenshot did not settle")
                    result["screenshot_review"] = "pending visual inspection"
                finally:
                    client.stream.close()
                    client.sock.close()
            result["status"] = "passed"
        except Exception as error:
            result.update(status="failed", error=repr(error))
            raise
        finally:
            # Abrupt host termination also checks that explicit Flush persisted data.
            if process.poll() is None:
                process.kill()
            tail, _ = process.communicate(timeout=10)
            raw.extend(tail)
            (output / "serial.raw.log").write_bytes(raw)
            text = raw.decode("utf-8", "replace")
            (output / "serial-clean.log").write_text(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text).replace("\r", ""))
            result["image_after_sha256"] = sha256(image)
            write_json(output / "result.json", result)
    print(f"PASS {output.name}: expected {expected}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-manifest", required=True, type=Path)
    parser.add_argument("--live-image", required=True, type=Path)
    parser.add_argument("--live-sha256", required=True)
    parser.add_argument("--firmware", type=Path, default=Path("/opt/homebrew/share/qemu/edk2-aarch64-code.fd"))
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    manifest = json.loads(args.build_manifest.read_text())
    base = args.build_manifest.resolve().parent
    for name in ("boot-isolation.efi", "boot-isolation-host"):
        if sha256(base / name) != manifest["files"][name]["sha256"]:
            raise ValueError(f"Changed probe artifact: {name}")
    if sha256(args.live_image) != args.live_sha256:
        raise ValueError("Input live image hash mismatch")
    parent = ROOT / "artifacts/boot-isolation-tests"
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=parent))
    print(f"Evidence: {output}", flush=True)
    shutil.copy2(__file__, output / Path(__file__).name)
    shutil.copy2(args.build_manifest, output / "build-manifest.json")
    result = {"status": "running", "environment": "host and QEMU virt; not Pi 5 firmware",
              "hardware_validated": False, "hardware_root_cause": "undetermined",
              "live_sha256": args.live_sha256, "firmware_sha256": sha256(args.firmware), "tests": {}}
    tests = result["tests"]
    try:
        # The selected ring header can be newer than the first header shown in photos.
        completed = subprocess.run([str(base / "boot-isolation-host"), str(args.live_image)], capture_output=True, timeout=args.timeout)
        (output / "host-ramfs.log").write_bytes(completed.stdout + completed.stderr)
        match = re.search(rb"HOST_RAMFS_PASS reads=(\d+) generation=(\d+) block=(0x[0-9a-f]+)", completed.stdout)
        assert completed.returncode == 0 and match, "Host production RedoxFS did not open the frozen input"
        reads, generation, block = (value.decode() for value in match.groups())
        expected_open = f"[FS-READY] reads={reads} generation={generation} block={block}"
        tests["host-ramfs"] = {"status": "passed", "exit_code": completed.returncode,
                               "reads": int(reads), "selected_generation": int(generation), "block": block}
        with tempfile.TemporaryDirectory(prefix="redexos-isolation-") as temporary:
            tmp = Path(temporary)
            stage = tmp / "stage"
            (stage / "EFI/BOOT").mkdir(parents=True)
            (stage / "redexos").mkdir()
            shutil.copy2(base / "boot-isolation.efi", stage / "EFI/BOOT/BOOTAA64.EFI")
            shutil.copy2(args.live_image, stage / "redox-live.iso")
            (stage / "redexos/ISOLATE.CFG").write_text(config("ramfs", "ram-ab", preload=True))
            blank = tmp / "blank.img"
            with blank.open("wb") as stream:
                stream.truncate(768 * 1024 * 1024)
            format_and_copy(blank, 0, "ISOLATION", stage)

            def trial(name, cfg, expected="PASS", required=(), image=None, **kwargs):
                if image is None:
                    image = tmp / (name + ".img")
                    shutil.copy2(blank, image)
                set_config(image, cfg, tmp)
                tests[name] = boot(image, output / name, args.firmware, args.timeout, expected, required, **kwargs)
                return image

            # A/B/A, exactly one binary and only the fat_log option changes.
            for name, enabled in (("ram-off-a", False), ("ram-on-b", True), ("ram-off-a2", False)):
                image = trial(name, config("ramfs", "ram-ab", log=enabled, preload=True),
                              required=(expected_open,), screenshot=enabled)
                if enabled:
                    log = output / name / "BOOT0001.LOG"
                    copy_out(image, 0, "/redexos/logs/BOOT0001.LOG", log)
                    content = log.read_text()
                    assert expected_open in content and "[ISO-COMPLETE] id=ram-ab" in content
                    assert "[SD-IO]" not in content
                    tests[name]["fat_log_sha256"] = sha256(log)
                else:
                    assert tests[name]["image_before_sha256"] == tests[name]["image_after_sha256"], "log-off wrote FAT"
                    tests[name]["zero_disk_changes"] = True
            trial("ram-el1", config("ramfs", "ram-el1", preload=True), required=("[FS-READY]",), el2=False, readonly=True)

            for name, preload, records in (("fat-small", False, 16), ("fat-loaded", True, 300)):
                image = trial(name + "-write", config("fat-write", name, preload=preload, records=records),
                              required=(f"[ISO-VERIFY] bytes={records * 2048} records={records} exact=true",),
                              screenshot=preload)
                saved = output / (name + ".BIN")
                copy_out(image, 0, f"/redexos/isolation/{name}.BIN", saved)
                assert saved.read_bytes() == expected_bytes(name, records * 2048)
                tests[name + "-write"]["offline_exact_bytes"] = records * 2048
                tests[name + "-write"]["data_sha256"] = sha256(saved)
                trial(name + "-read-reboot", config("fat-read", name, preload=preload, records=records), image=image,
                      required=(f"[ISO-VERIFY] bytes={records * 2048}",))
                record = tests[name + "-read-reboot"]
                assert record["image_before_sha256"] == record["image_after_sha256"]
                if not preload:
                    trial("refuse-existing-file", config("fat-write", name, records=records), image=image,
                          expected="FAIL", required=("test file already exists",))
                    damaged = bytearray(saved.read_bytes())
                    damaged[4097] ^= 1
                    corrupt = tmp / "corrupt.BIN"
                    corrupt.write_bytes(damaged)
                    put(image, corrupt, f"redexos/isolation/{name}.BIN")
                    trial("reject-corrupt-readback", config("fat-read", name, records=records), image=image,
                          expected="FAIL", required=("file content mismatch",))

            trial("reject-readonly-write", config("fat-write", "readonly", records=1), expected="FAIL", readonly=True,
                  required=("[ISO-RESULT] id=readonly FAIL",))
            trial("reject-unavailable-logger", config("ramfs", "log-ro", log=True, preload=True),
                  expected="FAIL", readonly=True, required=("requested FAT logger unavailable",))
            trial("reject-recursive-logging", config("fat-write", "bad-config", log=True),
                  expected="FAIL", required=("FAT probe must not log to FAT",))

            truncated = tmp / "truncated.iso"
            with args.live_image.open("rb") as file:
                truncated.write_bytes(file.read(3 * 1024 * 1024))
            completed = subprocess.run([str(base / "boot-isolation-host"), str(truncated)], capture_output=True, timeout=args.timeout)
            (output / "host-truncated.log").write_bytes(completed.stdout + completed.stderr)
            assert completed.returncode == 1 and b"HOST_RAMFS_FAIL errno=5" in completed.stderr
            tests["host-truncated"] = {"status": "passed", "expected_failure": "EIO", "exit_code": 1}
            image = tmp / "truncated.img"
            shutil.copy2(blank, image)
            put(image, truncated, "redox-live.iso")
            trial("ram-truncated", config("ramfs", "ram-truncated", preload=True), image=image,
                  expected="FAIL", required=("Filesystem(5)",))
            assert tests["ram-truncated"]["image_before_sha256"] == tests["ram-truncated"]["image_after_sha256"]
        assert sha256(args.live_image) == args.live_sha256
        result.update(status="passed", input_live_unchanged=True)
    except Exception as error:
        result.update(status="failed", error=repr(error))
        raise
    finally:
        write_json(output / "result.json", result)


if __name__ == "__main__":
    main()
