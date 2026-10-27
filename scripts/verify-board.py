#!/usr/bin/env python3
"""Capture a real Pi serial session and run repeatable boot/stability tests."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import select
import shlex
import subprocess
import sys
import termios
import time
import tty
import uuid


SMOKE_MARKERS = [
    "REDOX_BOARD_SMOKE_BEGIN",
    "PASS port-target=",
    "PASS file-write-read",
    "PASS pipe",
    "PASS child-exec-wait-exit-37",
    "PASS timer-sleep",
    "REDOX_BOARD_SMOKE_PASS",
]
FATAL_MARKERS = ("KERNEL PANIC", "UNHANDLED EXCEPTION")


def clean(data):
    text = bytes(data).decode("utf-8", "replace") if isinstance(data, (bytes, bytearray, memoryview)) else data
    text = re.sub(r"\x1b\][^\x07]*\x07", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return text.replace("\r", "")


def smoke_result(output, board):
    missing = [marker for marker in SMOKE_MARKERS if marker not in output]
    if f"PASS port-target={board}:" not in output:
        missing.append(f"PASS port-target={board}:")
    return {"passed": not missing and not any(item in output for item in FATAL_MARKERS), "missing": missing}


def stability_result(output, rounds):
    expected = f"REDOX_BOARD_STABILITY_PASS rounds={rounds}"
    heartbeat = f"REDOX_BOARD_HEARTBEAT iteration={rounds}/{rounds}"
    missing = [item for item in (expected, heartbeat) if item not in output]
    return {"passed": not missing and not any(item in output for item in FATAL_MARKERS), "missing": missing}


def last_milestone(text):
    milestones = [
        ("stability", "REDOX_BOARD_STABILITY_PASS"),
        ("functional-smoke", "REDOX_BOARD_SMOKE_PASS"),
        ("shell", "root:"),
        ("login", "redox login:"),
        ("userspace", "init: switchroot to /usr /etc"),
        ("kernel", "Redox OS starting..."),
        ("uefi", "UEFI"),
    ]
    return next((name for name, marker in milestones if marker in text), "no-output")


class SerialSession:
    def __init__(self, device, directory):
        self.device = device
        self.directory = directory
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.previous = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        attributes = termios.tcgetattr(self.fd)
        attributes[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE | getattr(termios, "CRTSCTS", 0))
        attributes[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
        attributes[4] = attributes[5] = termios.B115200
        termios.tcsetattr(self.fd, termios.TCSANOW, attributes)
        self.raw = (directory / "serial.raw.log").open("xb", buffering=0)
        self.data = bytearray()

    def event(self, kind, **details):
        record = {"utc": datetime.now(timezone.utc).isoformat(), "event": kind, **details}
        with (self.directory / "events.jsonl").open("a") as output:
            output.write(json.dumps(record, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())

    def read_once(self, wait=1):
        if not select.select([self.fd], [], [], wait)[0]:
            return b""
        chunk = os.read(self.fd, 65536)
        if not chunk:
            raise OSError("Serial device disconnected")
        self.raw.write(chunk)
        os.fsync(self.raw.fileno())
        self.data.extend(chunk)
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        return chunk

    def send(self, line):
        payload = line.encode() if isinstance(line, str) else line
        self.event("serial-write", bytes=len(payload), preview=clean(payload)[:120])
        for offset in range(0, len(payload), 16):
            os.write(self.fd, payload[offset:offset + 16])
            time.sleep(0.004)

    def wait_for_shell(self, start, timeout):
        deadline = time.monotonic() + timeout
        login_sent = password_sent = False
        while time.monotonic() < deadline:
            self.read_once()
            text = clean(self.data[start:])
            if any(marker in text for marker in FATAL_MARKERS):
                raise RuntimeError("Guest fatal marker detected")
            if re.search(r"root:[^\n]*# ", text):
                return
            if "password:" in text and not password_sent:
                self.send("password\r")
                password_sent = True
            elif "redox login:" in text and not login_sent:
                self.send("root\r")
                login_sent = True
        raise TimeoutError("No root shell before boot timeout")

    def command(self, command, timeout):
        token = "BOARD_TEST_" + uuid.uuid4().hex
        begin, end = token + "_BEGIN", token + "_END"
        script = f"echo {begin}; ( {command} ); result=$?; echo; echo {end}:$result"
        start = len(self.data)
        self.send("    bash -c " + shlex.quote(script) + "\r")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.read_once()
            text = clean(self.data[start:])
            if any(marker in text for marker in FATAL_MARKERS):
                raise RuntimeError("Guest fatal marker detected during command")
            match = re.search(rf"(?m)^{end}:(\d+)$", text)
            if match:
                opening = re.search(rf"(?m)^{begin}\n", text)
                if not opening:
                    raise RuntimeError("Command end arrived without start marker")
                output = text[opening.end():match.start()].removesuffix("\n")
                return int(match.group(1)), output
        raise TimeoutError(f"Guest command timed out: {command}")

    def close(self):
        try:
            (self.directory / "serial-clean.log").write_text(clean(self.data))
            self.raw.close()
        finally:
            termios.tcsetattr(self.fd, termios.TCSANOW, self.previous)
            os.close(self.fd)


def write_result(path, value):
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    partial.replace(path)


def power_cycle(args, boot):
    if args.power_cycle_command:
        subprocess.run(args.power_cycle_command, shell=True, check=True)
    elif not args.no_prompt:
        input(f"Power-cycle the {args.board} for boot {boot}/{args.boots}, then press Enter: ")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", choices=("pi4b", "pi5"))
    parser.add_argument("device")
    parser.add_argument("output", type=Path)
    parser.add_argument("--image-manifest", required=True, type=Path)
    parser.add_argument("--boots", type=int, default=1)
    parser.add_argument("--stability-rounds", type=int, default=100)
    parser.add_argument("--boot-timeout", type=int, default=300)
    parser.add_argument("--command-timeout", type=int, default=600)
    parser.add_argument("--power-cycle-command", help="Operator-supplied relay command run before every boot")
    parser.add_argument("--no-prompt", action="store_true", help="Assume power cycling is handled externally")
    args = parser.parse_args()
    if min(args.boots, args.stability_rounds, args.boot_timeout, args.command_timeout) <= 0:
        parser.error("counts and timeouts must be positive")
    manifest = json.loads(args.image_manifest.read_text())
    if manifest.get("board") != args.board:
        parser.error("Image manifest board does not match")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "image-manifest.json").write_text(args.image_manifest.read_text())
    result = {
        "schema": 1,
        "board": args.board,
        "device": args.device,
        "image_sha256": manifest["image_sha256"],
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "requested_boots": args.boots,
        "stability_rounds": args.stability_rounds,
        "automated_scope": [
            "serial boot capture with host-side fsync",
            "kernel, userspace, login and shell milestones",
            "board identity, temporary file I/O, pipe, child exit/wait and timer",
            "repeated functional rounds and cold-boot trials",
        ],
        "not_tested_by_this_suite": [
            "Redox runtime SD persistence and power-loss recovery",
            "board Ethernet/DHCP/DNS/HTTPS",
            "USB input, display, GPU acceleration and SMP",
        ],
        "boots": [],
        "status": "running",
        "hardware_validated": False,
    }
    write_result(args.output / "result.json", result)
    session = SerialSession(args.device, args.output)
    try:
        for number in range(1, args.boots + 1):
            boot = {"number": number, "started_utc": datetime.now(timezone.utc).isoformat(), "status": "running"}
            result["boots"].append(boot)
            write_result(args.output / "result.json", result)
            termios.tcflush(session.fd, termios.TCIFLUSH)
            power_cycle(args, number)
            start = len(session.data)
            session.event("boot-start", number=number)
            session.wait_for_shell(start, args.boot_timeout)
            boot_text = clean(session.data[start:])
            boot["kernel_marker"] = "Redox OS starting..." in boot_text
            boot["userspace_marker"] = "init: switchroot to /usr /etc" in boot_text
            if not boot["kernel_marker"] or not boot["userspace_marker"]:
                raise AssertionError("Fresh-boot kernel/userspace markers are incomplete")

            code, smoke = session.command("bash /usr/lib/board-smoke.sh", args.command_timeout)
            (args.output / f"boot-{number:02d}-smoke.txt").write_text(smoke)
            check = smoke_result(smoke, args.board)
            if code or not check["passed"]:
                raise AssertionError(f"Functional smoke failed: exit={code}, missing={check['missing']}")
            boot["functional_smoke"] = check

            code, stability = session.command(
                f"bash /usr/lib/board-stability.sh {args.stability_rounds}", args.command_timeout
            )
            (args.output / f"boot-{number:02d}-stability.txt").write_text(stability)
            check = stability_result(stability, args.stability_rounds)
            if code or not check["passed"]:
                raise AssertionError(f"Stability test failed: exit={code}, missing={check['missing']}")
            boot["stability"] = check
            boot["status"] = "passed"
            boot["ended_utc"] = datetime.now(timezone.utc).isoformat()
            session.event("boot-pass", number=number)
            write_result(args.output / "result.json", result)
            if number != args.boots:
                try:
                    session.command("shutdown", 30)
                except (TimeoutError, OSError):
                    session.event("shutdown-no-response", number=number)
        result["status"] = "passed"
        result["hardware_validated"] = True
    except Exception as error:
        text = clean(session.data)
        result.update(
            status="failed",
            hardware_validated=False,
            error=f"{type(error).__name__}: {error}",
            last_milestone=last_milestone(text),
            serial_tail=text[-4000:],
        )
        session.event("run-failed", error=result["error"], last_milestone=result["last_milestone"])
        raise
    finally:
        result["ended_utc"] = datetime.now(timezone.utc).isoformat()
        session.close()
        write_result(args.output / "result.json", result)
    print(f"BOARD_VERIFY_PASS boots={args.boots} rounds_per_boot={args.stability_rounds}")


if __name__ == "__main__":
    main()
