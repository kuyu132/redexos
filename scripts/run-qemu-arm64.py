#!/usr/bin/env python3
"""Run the pinned ARM64 QEMU image interactively or verify boot and userspace."""
import argparse
import fcntl
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

from prepare import ROOT, sha256


def clean(data):
    text = data.decode("utf-8", "replace")
    text = re.sub(r"\x1b\][^\x07]*\x07", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return text.replace("\r", "")


def find_evidence_line(text, marker):
    """Match a complete marker line while allowing appended diagnostics."""
    return re.search(rf"(?m)^{re.escape(marker)}(?:[ \t]+.*)?$", text)


def verify(command, directory, timeout, probe_ion=False, repeat=1, probe_signals=False):
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, bufsize=0)
    transcript = bytearray()
    phase = "login"
    cursor = 0
    completed = 0
    signal_marker = "PASS signal-registers x1=1111 x2=2222"
    required = ([signal_marker] if probe_signals else ["ION_BASH_STARTED"] if probe_ion else [
        "QEMU_ARM64_SMOKE_BEGIN", "PASS file-write-read", "PASS pipe",
        "PASS child-exec-wait-exit-37", "PASS timer-sleep", "PASS signal-delivery-5",
        signal_marker, "PASS tls-isolation",
    ])
    marker = signal_marker if probe_signals else "ION_BASH_STARTED" if probe_ion else "QEMU_ARM64_SMOKE_PASS"
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if not select.select([process.stdout], [], [], 1)[0]:
                if process.poll() is not None:
                    raise RuntimeError(f"QEMU exited before smoke test: {process.returncode}")
                continue
            data = os.read(process.stdout.fileno(), 65536)
            if not data:
                raise RuntimeError("QEMU serial stream closed")
            transcript.extend(data)
            text = clean(transcript)
            if "KERNEL PANIC" in text or "UNHANDLED EXCEPTION" in text or "FAIL signal-registers" in text:
                raise RuntimeError("Guest exception detected; inspect serial.log")
            # Consume already-buffered prompts too: a PASS and the following
            # shell prompt may arrive in the same serial read.
            while True:
                scope = text[cursor:]
                if phase == "login" and "Arrow keys and enter select mode" in scope:
                    # The graphical bootloader waits for a mode selection.
                    process.stdin.write(b"\r")
                    cursor = len(text)
                elif phase == "login" and "redox login:" in scope:
                    process.stdin.write(b"root\r")
                    cursor = len(text)
                    phase = "password"
                elif phase == "password" and "password:" in scope:
                    process.stdin.write(b"password\r")
                    cursor = len(text)
                    phase = "shell"
                elif phase in ("shell", "return") and re.search(r"root:[^\n]*# ", scope):
                    if phase == "shell":
                        process.stdin.write(b"signal-registers\r" if probe_signals
                                            else b"bash -c 'echo ION_BASH_STARTED'\r" if probe_ion
                                            else b"bash /usr/lib/qemu-smoke.sh\r")
                        phase = "smoke"
                    else:
                        process.stdin.write(b"echo ION_RETURN_OK\r")
                        phase = "ack"
                    cursor = len(text)
                elif phase == "smoke" and (match := find_evidence_line(scope, marker)):
                    missing = [item for item in required
                               if not find_evidence_line(scope, item)]
                    if missing:
                        raise RuntimeError(f"Missing evidence: {missing}")
                    cursor += match.end()
                    phase = "return"
                elif phase == "ack" and (match := re.search(r"(?m)^ION_RETURN_OK$", scope)):
                    completed += 1
                    if completed == repeat:
                        boot = ["Redox OS starting...", "init: switchroot to /usr /etc"]
                        if any(item not in text for item in boot):
                            raise RuntimeError("Missing kernel/init boot evidence")
                        return boot + required + [f"Ion ordinary launch and return: {completed}/{repeat}"]
                    cursor += match.end()
                    phase = "shell"
                else:
                    break
        raise TimeoutError(f"No userspace success within {timeout}s; phase={phase}")
    finally:
        process.terminate()
        try:
            remainder, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            remainder, _ = process.communicate()
        transcript.extend(remainder)
        (directory / "console.raw.log").write_bytes(transcript)
        (directory / "serial-clean.log").write_text(clean(transcript))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Login and run smoke tests, then stop QEMU")
    parser.add_argument("--probe-ion", action="store_true", help="Minimal ordinary Bash launch regression (implies --verify)")
    parser.add_argument("--probe-signals", action="store_true", help="Check x1/x2 across an asynchronous signal (implies --verify)")
    parser.add_argument("--repeat", type=int, default=1, help="Ordinary launches per verification run")
    parser.add_argument("--image-metadata", type=Path)
    parser.add_argument("--gdb-port", type=int, help="Expose QEMU debugger on 127.0.0.1:PORT for host LLDB/GDB")
    parser.add_argument("--network", action=argparse.BooleanOptionalAction, default=None, help="User-mode NAT with E1000 (default: enabled for persistent desktop)")
    parser.add_argument("--network-capture", action="store_true", help="Capture NIC packets to the evidence directory")
    parser.add_argument("--persistent", action="store_true", help="Use the writable desktop disk; prepare-qemu-persistent.py creates it once")
    parser.add_argument("--desktop", action="store_true", help="Enable ramfb, USB keyboard/tablet and the desktop image")
    parser.add_argument("--display", choices=("cocoa", "none"), help="Desktop display backend (default: cocoa on macOS)")
    parser.add_argument("--serial-socket", type=Path, help="Expose UART on a local Unix socket instead of stdio")
    parser.add_argument("--qmp", type=Path, help="Optional local Unix socket for QEMU control and screenshots")
    parser.add_argument("--usb-capture", action="store_true", help="Capture USB keyboard reports for diagnosis")
    parser.add_argument("--memory", type=int, help="Guest RAM in MiB (desktop: 4096, server: 2048)")
    parser.add_argument("--cpus", type=int, default=1, help="Guest CPU count (default: 1)")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--firmware", type=Path, default=Path("/opt/homebrew/share/qemu/edk2-aarch64-code.fd"))
    args = parser.parse_args()
    if args.gdb_port is not None and not 1024 <= args.gdb_port <= 65535:
        parser.error("--gdb-port must be between 1024 and 65535")
    args.verify = args.verify or args.probe_ion or args.probe_signals
    if args.probe_ion and args.probe_signals:
        parser.error("Choose one probe at a time")
    if args.timeout <= 0 or args.repeat <= 0:
        parser.error("--timeout and --repeat must be positive")
    if args.persistent and not args.desktop:
        parser.error("--persistent requires --desktop")
    if args.display and not args.desktop:
        parser.error("--display requires --desktop")
    if args.memory is not None and args.memory <= 0:
        parser.error("--memory must be positive")
    if args.cpus <= 0:
        parser.error("--cpus must be positive")
    network = args.persistent if args.network is None else args.network
    if args.network_capture and not network:
        parser.error("--network-capture requires networking")
    memory = args.memory if args.memory is not None else (4096 if args.desktop else 2048)
    if args.image_metadata is None:
        args.image_metadata = ROOT / "work/qemu-arm64" / ("persistent-desktop-image.json" if args.persistent else "desktop-image.json" if args.desktop else "image.json")
    metadata = json.loads(args.image_metadata.read_text())
    if bool(metadata.get("persistent")) != args.persistent:
        parser.error("Image metadata storage mode differs; use --persistent for a persistent image")
    root_disk = None
    disk_lock = None
    if args.persistent:
        root_disk = Path(metadata["root_disk"])
        if not root_disk.is_file() or root_disk.stat().st_size != metadata["root_disk_size"]:
            raise RuntimeError("Persistent root disk missing or size changed; preserve it for inspection")
        disk_lock = root_disk.with_suffix(".lock").open("a+")
        try:
            fcntl.flock(disk_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Persistent disk is already in use by another runner")
        metadata["root_before_sha256"] = sha256(root_disk)
    image = Path(metadata["image"])
    if sha256(image) != metadata["image_sha256"]:
        raise RuntimeError("Image changed since preparation")
    if not args.firmware.is_file():
        raise RuntimeError("UEFI firmware missing; provide --firmware PATH")
    qemu = shutil.which("qemu-system-aarch64")
    if not qemu:
        raise RuntimeError("qemu-system-aarch64 is not installed")
    parent = ROOT / "artifacts/qemu-arm64"
    parent.mkdir(parents=True, exist_ok=True)
    prefix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    command = [qemu, "-machine", "virt,gic-version=2,acpi=off", "-accel", "tcg",
               "-cpu", "cortex-a72", "-smp", str(args.cpus), "-m", str(memory), "-bios", str(args.firmware),
               "-vga", "none",
               "-drive", f"file={image},format=raw,if=none,id=esp," + ("readonly=on" if args.persistent else "snapshot=on"),
               "-device", "virtio-blk-device,drive=esp",
               "-chardev", f"stdio,id=uart,signal={'off' if args.verify else 'on'},logfile={directory / 'serial.log'}",
               "-serial", "chardev:uart", "-monitor", "none", "-no-reboot"]
    if args.persistent:
        if root_disk.resolve() == image.resolve():
            raise RuntimeError("Boot and writable disks must be different files")
        command += ["-drive", f"file={root_disk},format=raw,if=none,id=rootdisk,cache=writethrough",
                    "-device", "ich9-ahci,id=sata", "-device", "ide-hd,drive=rootdisk,bus=sata.0"]
    if network:
        command += ["-netdev", "user,id=net0,ipv6=off,net=10.0.2.0/24,dhcpstart=10.0.2.15,dns=10.0.2.3",
                    "-device", "e1000,netdev=net0,mac=52:54:00:12:34:56"]
        if args.network_capture:
            command += ["-object", f"filter-dump,id=netcapture,netdev=net0,file={directory / 'network.pcap'}"]
    else:
        command += ["-net", "none"]
    if args.serial_socket:
        if args.verify:
            parser.error("--serial-socket cannot be combined with --verify")
        command[command.index("-chardev") + 1] = (
            f"socket,id=uart,path={args.serial_socket.resolve()},server=on,wait=off,"
            f"logfile={directory / 'serial.log'}")
    if args.desktop:
        if metadata.get("usb_layout") != "separate-xhci-controllers":
            raise RuntimeError("Rebuild desktop image with prepare-qemu-arm64.py --profile desktop")
        keyboard = "usb-kbd,usb_version=2"
        if args.usb_capture:
            keyboard += f",pcap={directory / 'keyboard.pcap'}"
        command += ["-display", args.display or "cocoa", "-device", "ramfb",
                    "-device", "qemu-xhci,id=kbd", "-device", keyboard + ",bus=kbd.0",
                    "-device", "qemu-xhci,id=pointer", "-device", "usb-tablet,bus=pointer.0,usb_version=2"]
    else:
        command += ["-display", "none"]
    if args.gdb_port is not None:
        command += ["-gdb", f"tcp:127.0.0.1:{args.gdb_port}"]
    if args.qmp:
        command += ["-qmp", f"unix:{args.qmp.resolve()},server=on,wait=off"]
    (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    metadata["gdb_address"] = f"127.0.0.1:{args.gdb_port}" if args.gdb_port else None
    metadata["network"] = {"enabled": network, "backend": "user" if network else None, "nic": "e1000" if network else None}
    metadata["firmware"] = str(args.firmware)
    metadata["firmware_sha256"] = sha256(args.firmware)
    metadata["qemu_version"] = subprocess.check_output([qemu, "--version"], text=True)
    (directory / "inputs.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Evidence: {directory}", flush=True)
    result = {"qemu_machine": "virt", "cpu_count": args.cpus, "board_hardware_pass": False,
              "automatic_verification": args.verify, "status": "not-tested",
              "launch_mode": "ordinary", "probe_ion": args.probe_ion,
              "probe_signals": args.probe_signals, "repeat": args.repeat}
    result["network_enabled"] = network
    result["persistent"] = args.persistent
    result["desktop_enabled"] = args.desktop
    result["memory_mib"] = memory
    result["acceleration"] = "tcg"
    result["desktop_verification"] = "not-tested"
    try:
        if args.verify:
            result["checks"] = verify(command, directory, args.timeout, args.probe_ion, args.repeat, args.probe_signals)
            result["status"] = "passed"
            print(f"PASS: {metadata.get('kernel_origin', 'local')} ARM64 kernel -> Ion -> ordinary Bash launch/return ({args.repeat} runs)")
        else:
            print("Login root / password. Run: bash /usr/lib/qemu-smoke.sh. Ctrl-C stops QEMU.", flush=True)
            if args.persistent:
                print(f"Persistent root: {root_disk}. Writes survive QEMU exit/restart.", flush=True)
            if args.desktop:
                print("Desktop: select a resolution and press Enter; login user with an empty password.", flush=True)
            process = subprocess.Popen(command)
            try:
                if process.wait() != 0:
                    raise RuntimeError(f"QEMU exited with status {process.returncode}")
            except KeyboardInterrupt:
                process.terminate()
                process.wait(timeout=10)
    except Exception as error:
        result.update(status="failed", error=str(error))
        raise
    finally:
        if root_disk is not None:
            result["root_after_sha256"] = sha256(root_disk)
            result["root_before_sha256"] = metadata["root_before_sha256"]
            disk_lock.close()
        (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
