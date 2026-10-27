#!/usr/bin/env python3
"""Verify a marker survives two separate QEMU processes on the persistent desktop disk."""
import json
import os
import socket
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from prepare import ROOT
from qemu_serial_command import execute


def qmp(path, command):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(10); sock.connect(str(path))
        with sock.makefile('rwb', buffering=0) as stream:
            stream.readline()
            for request in ('qmp_capabilities', command):
                stream.write(json.dumps({'execute': request}).encode()+b'\n')
                while True:
                    response = json.loads(stream.readline())
                    if 'error' in response:
                        raise RuntimeError(response)
                    if 'return' in response:
                        break


def main():
    prefix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    evidence = ROOT/'artifacts/qemu-persistence'
    evidence.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix=prefix, dir=evidence))
    # Short socket paths remain below macOS's Unix socket path limit.
    sockets = Path(tempfile.mkdtemp(prefix='redox-persist-', dir='/tmp'))
    serial, control = sockets/'uart', sockets/'qmp'
    token = uuid.uuid4().hex
    guest = '/home/root/qemu-persistence-'+token
    expected = 'PERSISTENCE_'+token+'\n'
    result = {'status':'failed', 'guest_test_path':guest, 'boots':[]}
    print(f'Evidence: {evidence}', flush=True)
    try:
        for index in range(2):
            command = [sys.executable, str(ROOT/'scripts/run-qemu-arm64.py'), '--desktop', '--persistent',
                       '--display', 'none', '--serial-socket', str(serial), '--qmp', str(control)]
            with (evidence/f'boot-{index+1}.log').open('w') as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    deadline = time.monotonic()+120
                    serial_log = None
                    selected = False
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError(f'Runner exited early; see boot-{index+1}.log')
                        lines = (evidence/f'boot-{index+1}.log').read_text().splitlines()
                        locations = [Path(line.removeprefix('Evidence: ')) for line in lines if line.startswith('Evidence: ')]
                        if locations:
                            serial_log = locations[0]/'serial.log'
                        text = serial_log.read_text(errors='replace') if serial_log and serial_log.exists() else ''
                        if 'Arrow keys and enter select mode' in text and not selected and serial.exists():
                            with socket.socket(socket.AF_UNIX) as sock:
                                sock.connect(str(serial)); sock.sendall(b'\r')
                            selected = True
                        if 'redox login:' in text:
                            break
                        time.sleep(.25)
                    else:
                        raise TimeoutError('No login prompt within 120 seconds')
                    if index == 0:
                        code, smoke = execute(serial, 'bash /usr/lib/qemu-smoke.sh', timeout=60)
                        (evidence/'userspace-smoke.txt').write_text(smoke)
                        if code or 'QEMU_ARM64_SMOKE_PASS' not in smoke or 'PASS tls-isolation' not in smoke:
                            raise AssertionError('Userspace smoke failed on persistent root')
                        code, output = execute(serial, f'printf "{expected.rstrip()}\\n" > {guest}; cat {guest}')
                    else:
                        code, output = execute(serial, f'cat {guest}')
                    (evidence/f'readback-{index+1}.txt').write_text(output)
                    if code or output != expected:
                        raise AssertionError(f'Persistence readback mismatch on boot {index+1}: {output!r}')
                    if index == 1:
                        code, _ = execute(serial, f'rm {guest}')
                        if code:
                            raise RuntimeError('Test marker cleanup failed')
                    result['boots'].append(str(serial_log.parent))
                    qmp(control, 'quit')
                    if process.wait(timeout=30) != 0:
                        raise RuntimeError('QEMU runner failed during shutdown')
                finally:
                    if process.poll() is None:
                        try:
                            qmp(control, 'quit')
                        except (OSError, RuntimeError, ValueError):
                            os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=10)
                for path in (serial, control):
                    if path.exists():
                        path.unlink()
        result['status'] = 'passed'
        print('PASS: exact marker survived full QEMU exit and fresh-process restart')
    finally:
        (evidence/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        try:
            sockets.rmdir()
        except OSError:
            pass


if __name__ == '__main__':
    main()
