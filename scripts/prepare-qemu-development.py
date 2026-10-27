#!/usr/bin/env python3
"""Install a matching Rust link SDK into the existing stopped persistent desktop."""
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess

from prepare import ROOT, sha256


def main():
    metadata = json.loads((ROOT/'work/qemu-arm64/persistent-desktop-image.json').read_text())
    disk = Path(metadata['root_disk'])
    tool = ROOT/'tools/redox-image/target/release/redox-image'
    libc = Path(metadata['relibc_components'])
    gcc = Path(metadata['ahcid_components'])/'lib/libgcc_eh.a'
    with disk.with_suffix('.lock').open('a+') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Close QEMU before installing the SDK into the disk')
        def read(path):
            return subprocess.check_output([str(tool), 'cat', str(disk), path])
        if read('/usr/bin/rustc')[:4] != b'\x7fELF':
            raise RuntimeError('First install rust and gnu-make with guest pkg')
        if hashlib.sha256(read('/usr/lib/libc.so')).hexdigest() != sha256(libc/'libc.so'):
            raise RuntimeError('Installed libc no longer matches the patched SDK')
        if sha256(gcc) != metadata['sources']['gcc_unwind']['member_sha256']:
            raise RuntimeError('Unwind archive hash mismatch')
        subprocess.run(['shasum','-a','256','-c','SHA256SUMS'],cwd=libc,check=True,stdout=subprocess.DEVNULL)
        files = {
            '/usr/lib/devkit/libc.a':libc/'libc.a',
            '/usr/lib/devkit/crt0.o':libc/'crt0.o',
            '/usr/lib/devkit/libgcc_eh.a':gcc,
            '/usr/lib/devkit/arrays.ld':ROOT/'qemu/development/arrays.ld',
            '/usr/lib/devkit/Makefile':ROOT/'qemu/development/Makefile',
            '/usr/bin/dev-rustc':ROOT/'qemu/development/dev-rustc',
        }
        record = {'libc_build':str(libc),'description':'Native Redox Rust with matching patched libc, static linking, fixed debug text address','files':{}}
        for destination, source in files.items():
            subprocess.run([str(tool), 'put-exec' if destination.endswith('/dev-rustc') else 'put',str(disk),destination,str(source)],check=True)
            expected = source.read_bytes()
            if read(destination) != expected:
                raise RuntimeError(f'SDK readback mismatch: {destination}')
            record['files'][destination] = sha256(source)
        output = ROOT/'artifacts/qemu-development'
        output.mkdir(parents=True,exist_ok=True)
        (output/'sdk-install.json').write_text(json.dumps(record,indent=2)+'\n')
        print('Installed native Rust link SDK; existing persistent desktop data retained.')
        print('Guest: copy /usr/lib/devkit/Makefile into your project, then make run.')


if __name__ == '__main__':
    main()
