#!/usr/bin/env python3
"""Cold-boot the existing demo, rebuild natively, then check its on-disk ELF.

Does not create or change source files. Closes QEMU on completion.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader

from prepare import ROOT
from qemu_serial_command import execute

_persistence = SourceFileLoader('development_persistence', str(ROOT/'scripts/verify-qemu-persistence.py')).load_module()


def require_debug_elf(data):
    if data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
        raise AssertionError('Not a little-endian ARM64 ELF')
    offset = struct.unpack_from('<Q', data, 40)[0]
    size, count, strings_index = struct.unpack_from('<HHH', data, 58)
    headers = [struct.unpack_from('<IIQQQQIIQQ', data, offset+i*size) for i in range(count)]
    strings = headers[strings_index]
    names = data[strings[4]:strings[4]+strings[5]]
    sections = {}
    for entry in headers:
        name = names[entry[0]:].split(b'\0', 1)[0].decode()
        sections[name] = data[entry[4]:entry[4]+entry[5]]
    for name in ('.text', '.symtab', '.debug_info', '.debug_line'):
        if not any(sections.get(name, b'')):
            raise AssertionError(f'Empty/missing persisted ELF section: {name}')
    return {name: len(sections[name]) for name in ('.text', '.symtab', '.debug_info', '.debug_line')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected', default='44', help='Expected DEV_RESULT of the existing demo')
    args = parser.parse_args()
    if not re.fullmatch(r'-?\d+', args.expected):
        parser.error('--expected must be an integer')
    parent = ROOT/'artifacts/qemu-development'
    parent.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
    sockets = Path(tempfile.mkdtemp(prefix='redox-dev-', dir='/tmp'))
    serial, control = sockets/'uart', sockets/'qmp'
    metadata = json.loads((ROOT/'work/qemu-arm64/persistent-desktop-image.json').read_text())
    disk = Path(metadata['root_disk'])
    result = {'status':'failed', 'expected':args.expected, 'root_disk':str(disk), 'boots':[]}
    print(f'Evidence: {evidence}', flush=True)
    try:
        for index in range(2):
            logpath = evidence/f'boot-{index+1}.log'
            with logpath.open('w') as log:
                process = subprocess.Popen([sys.executable, str(ROOT/'scripts/run-qemu-arm64.py'),
                    '--desktop', '--persistent', '--display', 'none', '--serial-socket', str(serial),
                    '--qmp', str(control)], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    deadline, selected, location = time.monotonic()+120, False, None
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError(f'Runner exited; see {logpath}')
                        for line in logpath.read_text().splitlines():
                            if line.startswith('Evidence: '):
                                location = Path(line.removeprefix('Evidence: '))
                        uart = location/'serial.log' if location else None
                        text = uart.read_text(errors='replace') if uart and uart.exists() else ''
                        if 'Arrow keys and enter select mode' in text and not selected and serial.exists():
                            with socket.socket(socket.AF_UNIX) as sock:
                                sock.connect(str(serial)); sock.sendall(b'\r')
                            selected = True
                        if 'redox login:' in text:
                            break
                        time.sleep(.25)
                    else:
                        raise TimeoutError('No login prompt')
                    def run(name, command, timeout=120):
                        code, output = execute(serial, command, timeout=timeout)
                        (evidence/f'{name}-{index+1}.txt').write_text(output)
                        if code:
                            raise AssertionError(f'{name} failed with exit {code}')
                        return output
                    before = run('before', 'cd /home/user/dev-demo && sha256sum hello.rs hello && ./hello')
                    expected = f'DEV_RESULT={args.expected}'
                    if expected not in before.splitlines():
                        raise AssertionError('Persisted program returned unexpected output')
                    build = run('rebuild', 'cd /home/user/dev-demo && make clean && make run && chown 1000:1000 hello && sha256sum hello.rs hello')
                    if expected not in build.splitlines():
                        raise AssertionError('Native rebuilt program returned unexpected output')
                    hashes = dict((path, digest) for digest, path in re.findall(r'(?m)^([0-9a-f]{64})  (hello(?:\.rs)?)$', build))
                    if len(hashes) != 2 or hashes['hello.rs'] != before.split()[0]:
                        raise AssertionError('Source changed during native rebuild')
                    if index == 0:
                        cache = run('cache-reuse', 'fs-cache-reuse')
                        if 'PASS fs-cache-reuse' not in cache:
                            raise AssertionError('Closed mapping cache regression failed')
                        run('versions', 'rustc --version; cargo --version; make --version; rustfmt --version')
                        smoke = run('smoke', 'bash /usr/lib/qemu-smoke.sh')
                        if 'QEMU_ARM64_SMOKE_PASS' not in smoke or 'PASS tls-isolation' not in smoke:
                            raise AssertionError('Userspace regression failed')
                    _persistence.qmp(control, 'quit')
                    if process.wait(timeout=30):
                        raise RuntimeError('QEMU exit failed')
                    with disk.with_suffix('.lock').open('a+') as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        for name in ('hello', 'hello.rs', 'Makefile'):
                            data = subprocess.check_output([str(ROOT/'tools/redox-image/target/release/redox-image'), 'cat', str(disk), '/home/user/dev-demo/'+name])
                            (evidence/f'{name}-{index+1}').write_bytes(data)
                            if name in hashes and hashlib.sha256(data).hexdigest() != hashes[name]:
                                raise AssertionError(f'{name} bytes differ after QEMU exit')
                            if name == 'hello':
                                sections = require_debug_elf(data)
                    result['boots'].append({'evidence':str(location), 'hashes':hashes, 'sections':sections})
                    print(f'PASS boot {index+1}: existing binary, native rebuild, exact disk bytes and debug ELF', flush=True)
                finally:
                    if process.poll() is None:
                        try:
                            _persistence.qmp(control, 'quit')
                        except (OSError, RuntimeError, ValueError):
                            os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=10)
                for path in (serial, control):
                    path.unlink(missing_ok=True)
        result['status'] = 'passed'
    finally:
        (evidence/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        try:
            sockets.rmdir()
        except OSError:
            pass


if __name__ == '__main__':
    main()
