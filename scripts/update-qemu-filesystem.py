#!/usr/bin/env python3
"""Back up and update the stopped persistent desktop's locked filesystem service."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare import ROOT, sha256
from source_patches import expected_patch
from initfs_image import Initfs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--components', type=Path)
    args = parser.parse_args()
    source = json.loads((ROOT/'qemu/sources.lock.json').read_text())['redoxfs']
    candidates = [args.components] if args.components else sorted((ROOT/'artifacts/macos-redoxfs').glob('20*'), reverse=True)
    build = next((p for p in candidates if (p/'exit-code.txt').is_file()
        and (p/'exit-code.txt').read_text().strip() == '0' and (p/'redoxfs.patch').is_file()
        and (p/'redoxfs.patch').read_bytes() == expected_patch(source)
        and (p/'redoxfs.commit').read_text().strip() == source['rev']), None)
    if build is None:
        raise RuntimeError('First run: python3 scripts/build-macos-usbhidd.py --driver redoxfs')
    subprocess.run(['shasum', '-a', '256', '-c', 'SHA256SUMS'], cwd=build, check=True, stdout=subprocess.DEVNULL)
    path = ROOT/'work/qemu-arm64/persistent-desktop-image.json'
    metadata = json.loads(path.read_text())
    disk, boot = Path(metadata['root_disk']), Path(metadata['image'])
    tool = ROOT/'tools/redox-image/target/release/redox-image'
    with disk.with_suffix('.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Close QEMU before updating the persistent filesystem service')
        if sha256(boot) != metadata['image_sha256']:
            raise RuntimeError('Boot image differs from its recorded hash')
        if metadata.get('redoxfs_sha256') == sha256(build/'redoxfs'):
            subprocess.run([str(tool), 'put-exec', str(disk), '/usr/bin/fs-cache-reuse', str(build/'fs-cache-reuse')], check=True)
            if subprocess.check_output([str(tool), 'cat', str(disk), '/usr/bin/fs-cache-reuse']) != (build/'fs-cache-reuse').read_bytes():
                raise RuntimeError('Regression executable readback failed')
            metadata['redoxfs_components'] = str(build.resolve())
            metadata['sources']['redoxfs'] = source
            for destination in (path, disk.parent/'image.json'):
                destination.write_text(json.dumps(metadata, indent=2)+'\n')
            print('Matching filesystem service already installed.')
            return
        backups = ROOT/'work/qemu-arm64/filesystem-backups'
        backups.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=backups))
        for source_path in (boot, disk):
            try:
                subprocess.run(['cp', '-c', str(source_path), str(backup/source_path.name)], check=True)
            except subprocess.CalledProcessError:
                shutil.copyfile(source_path, backup/source_path.name)
        shutil.copy2(path, backup/'image.json')
        original = subprocess.check_output([str(tool), 'cat', str(boot), '/usr/lib/boot/initfs'])
        payload = Initfs(original).replace_executable('/bin/redoxfs', (build/'redoxfs').read_bytes())
        (backup/'new-initfs').write_bytes(payload)
        for image in (boot, disk):
            subprocess.run([str(tool), 'put', str(image), '/usr/lib/boot/initfs', str(backup/'new-initfs')], check=True)
            if subprocess.check_output([str(tool), 'cat', str(image), '/usr/lib/boot/initfs']) != payload:
                raise RuntimeError(f'Initfs readback failed; backup at {backup}')
        subprocess.run([str(tool), 'put-exec', str(disk), '/usr/bin/redoxfs', str(build/'redoxfs')], check=True)
        if subprocess.check_output([str(tool), 'cat', str(disk), '/usr/bin/redoxfs']) != (build/'redoxfs').read_bytes():
            raise RuntimeError('Filesystem executable readback failed')
        subprocess.run([str(tool), 'put-exec', str(disk), '/usr/bin/fs-cache-reuse', str(build/'fs-cache-reuse')], check=True)
        if subprocess.check_output([str(tool), 'cat', str(disk), '/usr/bin/fs-cache-reuse']) != (build/'fs-cache-reuse').read_bytes():
            raise RuntimeError('Regression executable readback failed')
        (disk.parent/'initfs').write_bytes(payload)
        metadata.update(image_sha256=sha256(boot), initfs_sha256=hashlib.sha256(payload).hexdigest(),
            redoxfs_components=str(build.resolve()), redoxfs_sha256=sha256(build/'redoxfs'))
        metadata['sources']['redoxfs'] = source
        for destination in (path, disk.parent/'image.json'):
            destination.write_text(json.dumps(metadata, indent=2)+'\n')
        print(f'Updated filesystem service. Full disk/config backups: {backup}')


if __name__ == '__main__':
    main()
