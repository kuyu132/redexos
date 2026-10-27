#!/usr/bin/env python3
"""Check automatic DHCP, real DNS and a verified HTTPS download across two cold boots."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

from prepare import ROOT
from qemu_serial_command import execute
from network_capture import inspect_capture, require_network_evidence
from importlib.machinery import SourceFileLoader
_persistence = SourceFileLoader('persistence_verifier', str(ROOT/'scripts/verify-qemu-persistence.py')).load_module()


CONFIG = 'cat /scheme/netcfg/ifaces/eth0/addr/list /scheme/netcfg/route/list /scheme/netcfg/resolv/nameserver'


def main():
    manifest = json.loads((ROOT/'qemu/sources.lock.json').read_text())
    revision = manifest['base']['rev']
    reference = subprocess.check_output(['git', '-C', str(ROOT/'upstream/qemu-base'), 'show', revision+':Cargo.lock'])
    digest = hashlib.sha256(reference).hexdigest()
    url = f'https://gitlab.redox-os.org/redox-os/base/-/raw/{revision}/Cargo.lock'
    parent = ROOT/'artifacts/qemu-network'
    parent.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
    sockets = Path(tempfile.mkdtemp(prefix='redox-net-', dir='/tmp'))
    serial, control = sockets/'uart', sockets/'qmp'
    guest_prefix = '/home/root/qemu-network-'+uuid.uuid4().hex
    (evidence/'reference-Cargo.lock').write_bytes(reference)
    metadata = json.loads((ROOT/'work/qemu-arm64/persistent-desktop-image.json').read_text())
    result = {'status':'failed', 'url':url, 'sha256':digest, 'bytes':len(reference),
              'root_disk':metadata['root_disk'], 'boots':[], 'downloads':[]}
    print(f'Evidence: {evidence}', flush=True)
    try:
        for index in range(2):
            command = [sys.executable, str(ROOT/'scripts/run-qemu-arm64.py'), '--desktop', '--persistent',
                       '--network', '--network-capture', '--display', 'none',
                       '--serial-socket', str(serial), '--qmp', str(control)]
            with (evidence/f'boot-{index+1}.log').open('w') as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    deadline, location, selected = time.monotonic()+120, None, False
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError(f'Runner exited early; inspect boot-{index+1}.log')
                        for line in (evidence/f'boot-{index+1}.log').read_text().splitlines():
                            if line.startswith('Evidence: '):
                                location = Path(line.removeprefix('Evidence: '))
                        text = (location/'serial.log').read_text(errors='replace') if location and (location/'serial.log').exists() else ''
                        if 'Arrow keys and enter select mode' in text and not selected and serial.exists():
                            with socket.socket(socket.AF_UNIX) as sock:
                                sock.connect(str(serial)); sock.sendall(b'\r')
                            selected = True
                        if 'redox login:' in text:
                            break
                        time.sleep(.25)
                    else:
                        raise TimeoutError('No login prompt within 120 seconds')
                    deadline = time.monotonic()+45
                    while time.monotonic() < deadline:
                        code, config = execute(serial, CONFIG, timeout=20)
                        if code == 0 and re.search(r'(?m)^10\.0\.2\.15/24$', config) and re.search(r'(?m)^10\.0\.2\.3$', config) and 'default via 10.0.2.2 dev eth0 src 10.0.2.15' in config:
                            break
                        time.sleep(.5)
                    else:
                        raise AssertionError('Automatic DHCP configuration missing')
                    (evidence/f'config-{index+1}.txt').write_text(config)
                    if index == 1:
                        code, previous = execute(serial, f'sha256sum {guest_prefix}-1.lock')
                        (evidence/'previous-download-after-restart.txt').write_text(previous)
                        if code or previous.split()[0] != digest:
                            raise AssertionError('Previous download did not survive restart')
                    destination = f'{guest_prefix}-{index+1}.lock'
                    request = f'curl --noproxy "*" --connect-timeout 10 --max-time 45 --max-filesize 1048576 -fL -v {shlex.quote(url)} -o {destination}'
                    code, output = execute(serial, request, timeout=70)
                    (evidence/f'curl-{index+1}.txt').write_text(output)
                    if code or 'SSL certificate verified' not in output:
                        raise AssertionError(f'HTTPS/certificate check failed on boot {index+1}; status {code}')
                    code, checksum = execute(serial, f'sha256sum {destination}; wc -c < {destination}')
                    (evidence/f'download-{index+1}.txt').write_text(checksum)
                    if code or checksum.split()[0] != digest or checksum.split()[-1] != str(len(reference)):
                        raise AssertionError('Downloaded payload hash/length mismatch')
                    result['downloads'].append(destination)
                    if index == 1:
                        code, _ = execute(serial, f'rm {guest_prefix}-1.lock {guest_prefix}-2.lock')
                        if code:
                            raise RuntimeError('Temporary download cleanup failed')
                    _persistence.qmp(control, 'quit')
                    if process.wait(timeout=30) != 0:
                        raise RuntimeError('Runner failed while closing QEMU')
                    capture = inspect_capture(location/'network.pcap')
                    require_network_evidence(capture, 'gitlab.redox-os.org')
                    (evidence/f'packets-{index+1}.json').write_text(json.dumps(capture, indent=2)+'\n')
                    result['boots'].append(str(location))
                    print(f'PASS boot {index+1}: DHCP exchange, DNS response, verified HTTPS {len(reference)} bytes', flush=True)
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
                    if path.exists():
                        path.unlink()
        result['status'] = 'passed'
        result['checks'] = ['DHCP DORA and runtime configuration each boot', 'paired DNS A lookup each boot',
                            'TLS certificate verification each boot', 'HTTPS exact SHA256 and size each boot',
                            'download persisted across fresh QEMU process', 'automatic network recovery without manual guest configuration']
        print('PASS: network recovered automatically after a full QEMU exit/restart')
    finally:
        (evidence/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        try:
            sockets.rmdir()
        except OSError:
            pass


if __name__ == '__main__':
    main()
