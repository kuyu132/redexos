#!/usr/bin/env python3
"""Run a command via a local QEMU UART socket and retain clean output."""
import argparse
import re
import shlex
import socket
import time
import uuid
from pathlib import Path

from importlib.machinery import SourceFileLoader
_runner = SourceFileLoader('qemu_runner', str(Path(__file__).with_name('run-qemu-arm64.py'))).load_module()


def execute(path, command, timeout=45):
    token = 'QEMU_CMD_'+uuid.uuid4().hex
    begin, end = token+'_BEGIN', token+'_END'
    script = f'echo {begin}; ( {command} ); result=$?; echo; echo {end}:$result'
    with socket.socket(socket.AF_UNIX) as stream:
        stream.connect(str(path)); stream.settimeout(.5)
        stream.sendall(b'\r')
        data = bytearray(); phase = 'login'
        last_nudge = time.monotonic()
        received_anything = False
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            try:
                chunk = stream.recv(65536)
                if not chunk:
                    raise RuntimeError('UART closed')
                data.extend(chunk)
                received_anything = True
            except socket.timeout:
                # A freshly connected PL011 can lose the first wake-up byte.
                # Retry only while the connection is completely silent. Once
                # getty or a shell responds, its own input state must not be
                # disturbed by repeated carriage returns.
                if phase == 'login' and not received_anything and time.monotonic() - last_nudge >= .25:
                    stream.sendall(b'\r')
                    last_nudge = time.monotonic()
                continue
            text = _runner.clean(data)
            if phase in ('login', 'password', 'shell'):
                if re.search(r'root:[^\n]*# ', text):
                    # The prompt can precede Ion's next input read. Let it settle,
                    # then feed UART-sized chunks without overrunning the PL011 RX path.
                    time.sleep(.1)
                    # Ion can expose its prompt just before the PL011 receive
                    # loop is ready. Leading spaces absorb a lost first byte
                    # without changing the shell command.
                    line = ('    bash -c '+shlex.quote(script)+'\r').encode()
                    for offset in range(0,len(line),16):
                        stream.sendall(line[offset:offset+16]); time.sleep(.003)
                    phase = 'command'; data.clear()
                elif phase in ('login', 'password') and 'password:' in text:
                    stream.sendall(b'password\r'); phase = 'shell'; data.clear()
                elif phase == 'login' and 'redox login:' in text:
                    stream.sendall(b'root\r'); phase = 'password'; data.clear()
            elif (match := re.search(rf'(?m)^{end}:(\d+)$', text)):
                start = re.search(rf'(?m)^{begin}\n', text)
                if not start:
                    raise RuntimeError('Missing command start marker')
                output = text[start.end():match.start()]
                if output.endswith('\n'):
                    output = output[:-1]
                return int(match.group(1)), output
        raise TimeoutError(f'UART command timed out; phase={phase}; tail={_runner.clean(data)[-300:]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout', type=int, default=45)
    parser.add_argument('command')
    args = parser.parse_args()
    code, output = execute(args.socket, args.command, args.timeout)
    if args.output:
        args.output.write_text(output)
    print(output, end='')
    raise SystemExit(code)


if __name__ == '__main__':
    main()
