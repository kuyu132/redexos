#!/usr/bin/env python3
"""Cold-boot, compile and run genuine native GDB on the existing persistent demo.

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
import qemu_desktop_control as desktop

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
    parent = ROOT/'artifacts/qemu-native-debug'
    parent.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
    sockets = Path(tempfile.mkdtemp(prefix='redox-gdb-', dir='/tmp'))
    serial, control = sockets/'uart', sockets/'qmp'
    metadata = json.loads((ROOT/'work/qemu-arm64/persistent-desktop-image.json').read_text())
    disk = Path(metadata['root_disk'])
    desktop.EVIDENCE = evidence
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
                            os.environ['QMP_SOCKET'] = str(control)
                            q = desktop.QMP(); q.key('up'); q.key('ret')
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
                    # Repeatedly enter the dynamic loader while SIGCHLD and UART
                    # activity vary. This caught incomplete AArch64 signal-frame
                    # restoration while the dynamic loader was using x18.
                    for padding in range(0, 257, 4):
                        code, output = execute(serial, ':' + ' ' * padding, timeout=15)
                        if code:
                            raise AssertionError(f'Loader stress failed at padding {padding}: {output}')
                    (evidence/f'loader-stress-{index+1}.txt').write_text('PASS 65 Bash launches\n')
                    persisted = dict((path,digest) for digest,path in re.findall(r'(?m)^([0-9a-f]{64})  (hello(?:\.rs)?)$', before))
                    if index and persisted != result['boots'][-1]['hashes']:
                        raise AssertionError('Persisted source/ELF changed across cold boots')
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
                    if index == 0:
                        run('cleanup', 'rm -f /usr/bin/gdb-start-probe /usr/bin/ptrace-baseline /usr/bin/ptrace-no-step /usr/bin/stdio-unbuffered-old /usr/bin/native-cxx-smoke')
                    native = run('native-gdb', 'cd /home/user/dev-demo && gdb -q -nx --batch -x /usr/lib/devkit/native-debug.gdb ./hello', timeout=90)
                    if not re.search(r'Breakpoint 1, .*dev_double', native) or not re.search(r'\$1 = '+str(int(args.expected)//2)+r'\b', native):
                        raise AssertionError('Native GDB did not report the source breakpoint/argument')
                    if 'exited normally' not in native or '#0' not in native or 'hello.rs' not in native:
                        raise AssertionError('Native GDB step/backtrace/exit failed')
                    pcs = [int(value, 16) for value in re.findall(r'(?m)^pc\s+(0x[0-9a-f]+)\b', native)]
                    if len(pcs) != 2 or pcs[0] == pcs[1]:
                        raise AssertionError('Native GDB did not report a distinct post-step PC')
                    stepped_pc = pcs[1]
                    for program in ('wait-continued','ptrace-smoke','stdio-unbuffered','tty-drain','exec-saved-ids'):
                        output = run(program, program)
                        if 'PASS '+program not in output:
                            raise AssertionError(program+' regression failed')
                    # Drive the real desktop terminal as the ordinary user.
                    q.key_interval = .1
                    def type_command(command, settle=2):
                        q.text(command)
                        time.sleep(.5)
                        q.key('ret')
                        time.sleep(settle)
                    def gui_marker(name, path, expected_lines, commands, dock=False):
                        attempts = []
                        for attempt in range(1, 4):
                            execute(serial, f'rm -f {path}', timeout=15)
                            q.click(120, 744) if dock else q.click(400, 400)
                            time.sleep(4 if dock else 1)
                            q.key('ctrl-c')
                            time.sleep(1)
                            for command, settle in commands:
                                type_command(command, settle)
                            code, output = execute(serial, f'test -s {path} && cat {path}', timeout=15)
                            attempts.append(f'attempt {attempt}: exit={code}\n{output}')
                            q.shot(f'{name}-attempt-{attempt}-{index+1}.png')
                            if code == 0 and output.splitlines() == expected_lines:
                                (evidence/f'{name}-{index+1}.txt').write_text('\n'.join(attempts)+'\n')
                                return
                        (evidence/f'{name}-{index+1}.txt').write_text('\n'.join(attempts)+'\n')
                        raise AssertionError(f'Desktop input handshake failed: {name}')
                    q.key('ret'); time.sleep(3)
                    marker_paths = [
                        f'/tmp/gui{index+1}', f'/tmp/bash{index+1}', f'/tmp/cwd{index+1}',
                        f'/home/user/dev-demo/nb{index+1}',
                        f'/home/user/dev-demo/gst{index+1}',
                        f'/home/user/dev-demo/ga{index+1}',
                        f'/home/user/dev-demo/gs{index+1}',
                    ]
                    run('desktop-reset', 'rm -f ' + ' '.join(marker_paths))
                    # Focus and keyboard delivery can race with desktop startup.
                    # Every phase uses a fresh, short marker and retries only after
                    # clearing the active line. Later checks cannot consume stale
                    # output from a previous boot.
                    gui_marker('terminal-focus-ready', f'/tmp/gui{index+1}', ['OK'],
                               [(f'echo OK >/tmp/gui{index+1}', 3)], dock=True)
                    gui_marker('terminal-bash-ready', f'/tmp/bash{index+1}', ['OK'],
                               [('bash', 3), (f'test -n "$BASH_VERSION" && echo OK >/tmp/bash{index+1}', 3)])
                    gui_marker('terminal-cwd-ready', f'/tmp/cwd{index+1}', ['/home/user/dev-demo'],
                               [('cd /home/user/dev-demo', 3), (f'pwd >/tmp/cwd{index+1}', 3)])
                    type_command('make clean', 5)
                    type_command('make', 15)
                    type_command(f'./hello > nb{index+1}', 4)
                    # Keep native compilation and the diagnostic shell launch sequential.
                    ready = run('terminal-build-ready', f'for i in {{1..30}}; do test -s /home/user/dev-demo/nb{index+1} && cat /home/user/dev-demo/nb{index+1} && exit 0; sleep 1; done; exit 1')
                    if expected not in ready.splitlines():
                        raise AssertionError('Desktop compilation did not finish')
                    q.shot(f'terminal-build-{index+1}.png')
                    type_command('gdb -q -nx ./hello', 5)
                    for command in ('set pagination off', 'set confirm off', 'break dev_double',
                                    'set cwd /no-such-redox-gdb-directory'):
                        type_command(command)
                    type_command('run', 5)
                    q.shot(f'terminal-failed-start-{index+1}.png')
                    type_command('set cwd /home/user/dev-demo')
                    type_command('run', 5)
                    q.shot(f'terminal-hit-before-inspect-{index+1}.png')
                    type_command('print value')
                    type_command('info registers x0 pc sp')
                    type_command(f'dump binary value ga{index+1} value')
                    q.shot(f'terminal-value-dump-{index+1}.png')
                    type_command(f'shell echo BREAKPOINT > gst{index+1}')
                    q.shot(f'terminal-breakpoint-marker-{index+1}.png')
                    arg_sha256 = hashlib.sha256(struct.pack('<q', int(args.expected)//2)).hexdigest()
                    state = run('terminal-gdb-breakpoint-ready', f'for i in {{1..30}}; do test -s /home/user/dev-demo/ga{index+1} && sha256sum /home/user/dev-demo/ga{index+1} && cat /home/user/dev-demo/gst{index+1} && exit 0; sleep 1; done; exit 1')
                    if arg_sha256 not in state or 'BREAKPOINT' not in state.splitlines():
                        raise AssertionError('Desktop GDB did not dump argument 22 at the source breakpoint')
                    q.shot(f'terminal-breakpoint-{index+1}.png')
                    # Other guest processes must keep running while the child is stopped.
                    responsive = run('paused-responsive', 'echo GUEST_RUNNING_WHILE_CHILD_STOPPED; date')
                    if 'GUEST_RUNNING_WHILE_CHILD_STOPPED' not in responsive:
                        raise AssertionError('Guest unresponsive during native breakpoint')
                    type_command('next')
                    type_command(f'dump binary value gs{index+1} $pc')
                    type_command('bt')
                    step_sha256 = hashlib.sha256(struct.pack('<Q', stepped_pc)).hexdigest()
                    step = run('terminal-gdb-step-ready', f'for i in {{1..30}}; do test -s /home/user/dev-demo/gs{index+1} && sha256sum /home/user/dev-demo/gs{index+1} && exit 0; sleep 1; done; exit 1')
                    if step_sha256 not in step:
                        raise AssertionError('Desktop GDB did not dump the stepped return value')
                    q.shot(f'terminal-step-{index+1}.png')
                    type_command('continue', 4)
                    type_command(f'shell echo COMPLETE >> gst{index+1}')
                    type_command('quit', 3)
                    q.shot(f'terminal-exit-{index+1}.png')
                    gui_state = run('terminal-native-gdb', f'cat /home/user/dev-demo/gst{index+1}')
                    if gui_state.splitlines() != ['BREAKPOINT', 'COMPLETE']:
                        raise AssertionError('Desktop user GDB breakpoint/exit markers are incomplete')
                    gui_build = run('terminal-native-build', f'cat /home/user/dev-demo/nb{index+1}')
                    if expected not in gui_build.splitlines():
                        raise AssertionError('Desktop user native compile/run failed')
                    final_hashes = run('terminal-hashes', 'cd /home/user/dev-demo && sha256sum hello.rs hello')
                    if dict((path,digest) for digest,path in re.findall(r'(?m)^([0-9a-f]{64})  (hello(?:\.rs)?)$', final_hashes)) != hashes:
                        raise AssertionError('Desktop rebuild differs from UART rebuild')
                    if 'UNHANDLED EXCEPTION' in (location/'serial.log').read_text(errors='replace'):
                        raise AssertionError('Guest exception during native development verification')
                    q.call('quit')
                    q.stream.close(); q.sock.close()
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
                    result['boots'].append({'evidence':str(location), 'hashes':hashes, 'sections':sections, 'loader_stress_launches':65, 'native_gdb':True, 'desktop_user_native_gdb':True, 'desktop_cwd_verified':True, 'desktop_argument_dump_sha256':arg_sha256, 'desktop_step_dump_sha256':step_sha256, 'guest_responsive_while_stopped':True})
                    print(f'PASS boot {index+1}: native compile/run/GDB breakpoint, argument, step, backtrace, exit; exact disk bytes', flush=True)
                finally:
                    if process.poll() is None:
                        try:
                            q.stream.close(); q.sock.close()
                        except (NameError, OSError):
                            pass
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
