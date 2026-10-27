#!/usr/bin/env python3
"""Read-only 115200 8N1 serial capture on Ubuntu/macOS; Ctrl-C stops capture."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import select
import sys
import termios
import tty


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", choices=("pi4b", "pi5"))
    parser.add_argument("device", help="e.g. /dev/serial/by-id/... or /dev/cu.usbserial...")
    parser.add_argument("output", type=Path, help="New directory for serial evidence")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    fd = os.open(args.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    previous = termios.tcgetattr(fd)
    metadata = {"board": args.board, "device": args.device, "baud": 115200,
                "started_utc": datetime.now(timezone.utc).isoformat(), "hardware_pass": False}
    try:
        tty.setraw(fd)
        attributes = termios.tcgetattr(fd)
        attributes[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE | getattr(termios, "CRTSCTS", 0))
        attributes[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
        attributes[4] = attributes[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attributes)
        print("Capturing serial; power-cycle the board now. Ctrl-C to stop.", file=sys.stderr)
        with (args.output / "serial.log").open("xb", buffering=0) as stream:
            while True:
                if select.select([fd], [], [], 1)[0]:
                    data = os.read(fd, 65536)
                    if not data:
                        raise OSError("Serial device disconnected")
                    stream.write(data)
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
    except KeyboardInterrupt:
        metadata["stop_reason"] = "operator"
    except Exception as error:
        metadata["stop_reason"] = str(error)
        raise
    finally:
        metadata["ended_utc"] = datetime.now(timezone.utc).isoformat()
        try:
            (args.output / "capture.json").write_text(json.dumps(metadata, indent=2) + "\n")
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, previous)
            finally:
                os.close(fd)


if __name__ == "__main__":
    main()
