#!/usr/bin/env python3
"""Create a persistent desktop from the prepared image without replacing existing data."""
import argparse
import json
import hashlib
import shutil
import subprocess
from pathlib import Path

from prepare import ROOT, sha256
from source_patches import expected_patch
from initfs_image import Initfs
from persistent_disk import grow_gpt


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT/'work/qemu-arm64/persistent-desktop')
    parser.add_argument('--size-gib', type=int, default=4)
    args = parser.parse_args()
    if args.size_gib < 2:
        parser.error('--size-gib must be at least 2')
    destination = args.directory.resolve()
    if destination.exists():
        raise RuntimeError(f'Preserving existing disk at {destination}; run with --persistent, or choose a new --directory')
    manifest = json.loads((ROOT/'qemu/sources.lock.json').read_text())
    base = json.loads((ROOT/'work/qemu-arm64/desktop-image.json').read_text())
    if base['kernel_origin'] != 'local':
        raise RuntimeError('Persistent ARM64 requires the locally patched kernel')
    if base['sources']['repositories'] != manifest['repositories'] or base['sources']['base'] != manifest['base']:
        raise RuntimeError('Rebuild the current desktop image first: prepare-qemu-arm64.py --profile desktop')
    if sha256(Path(base['image'])) != base['image_sha256']:
        raise RuntimeError('Prepared base image hash mismatch')
    libc_spec = manifest['relibc']
    drivers = sorted((ROOT/'artifacts/macos-ahcid').glob('20*'), reverse=True)
    driver = next((p for p in drivers if (p/'exit-code.txt').is_file()
                   and (p/'exit-code.txt').read_text().strip() == '0'
                   and json.loads((p/'sources.lock.json').read_text())['base'] == manifest['base']
                   and json.loads((p/'sources.lock.json').read_text())['relibc'] == libc_spec
                   and (p/'base.patch').read_bytes() == expected_patch(manifest['base'])), None)
    if driver is None:
        raise RuntimeError('First run: python3 scripts/build-macos-usbhidd.py --driver ahcid')
    run('shasum', '-a', '256', '-c', 'SHA256SUMS', cwd=driver)
    run('cargo', '+'+manifest['rust_toolchain'], 'build', '--locked', '--release',
        '--manifest-path', ROOT/'upstream/redoxfs/Cargo.toml', '--no-default-features',
        '--features', 'std,log', '--bin', 'redoxfs-resize')
    tool = ROOT/'tools/redox-image/target/release/redox-image'
    source = Path(base['image']).with_name('redox-live.iso')
    if sha256(source) != base['live_image_sha256']:
        raise RuntimeError('Base filesystem hash mismatch')
    destination.mkdir(parents=True)
    boot = destination/'boot.img'
    # APFS clone when available; ordinary copy on other hosts/filesystems.
    try:
        run('cp', '-c', source, boot)
    except subprocess.CalledProcessError:
        if boot.exists():
            boot.unlink()
        shutil.copyfile(source, boot)
    original = subprocess.check_output([str(tool), 'cat', str(boot), '/usr/lib/boot/initfs'])
    initfs = Initfs(original).add_executable('/lib/drivers/ahcid', (driver/'ahcid').read_bytes())
    (destination/'initfs').write_bytes(initfs)
    run(tool, 'put', boot, '/usr/lib/boot/initfs', destination/'initfs')
    actual = subprocess.check_output([str(tool), 'cat', str(boot), '/usr/lib/boot/initfs'])
    if actual != initfs or Initfs(actual).read('/lib/drivers/ahcid') != (driver/'ahcid').read_bytes():
        raise RuntimeError('AHCI initfs readback mismatch')
    efi = Path(base['components'])/'bootloader.efi'
    # The pinned official GPT has its small FAT ESP at LBA 2048.
    run('mcopy', '-o', '-i', str(boot)+'@@1048576', efi, '::/EFI/BOOT/BOOTAA64.EFI')
    if subprocess.check_output(['mtype', '-i', str(boot)+'@@1048576', '::/EFI/BOOT/BOOTAA64.EFI']) != efi.read_bytes():
        raise RuntimeError('Normal EFI readback mismatch')
    root_disk = destination/'root.img'
    try:
        run('cp', '-c', boot, root_disk)
    except subprocess.CalledProcessError:
        if root_disk.exists():
            root_disk.unlink()
        shutil.copyfile(boot, root_disk)
    filesystem_size = grow_gpt(root_disk, args.size_gib*1024**3)
    resize = ROOT/'upstream/redoxfs/target/release/redoxfs-resize'
    with (destination/'resize.log').open('w') as log:
        run(resize, root_disk, filesystem_size, stdout=log, stderr=subprocess.STDOUT)
    metadata = dict(base)
    metadata.update(persistent=True, image=str(boot), image_sha256=sha256(boot),
                    bootloader_mode='normal; live disabled', bootloader_sha256=sha256(efi),
                    root_disk=str(root_disk), root_disk_size=root_disk.stat().st_size,
                    root_initial_sha256=sha256(root_disk), filesystem_size=filesystem_size,
                    root_controller='ich9-ahci', root_cache='writethrough', root_snapshot=False,
                    ahcid_components=str(driver), ahcid_sha256=sha256(driver/'ahcid'),
                    initfs_original_sha256=hashlib.sha256(original).hexdigest(),
                    initfs_sha256=sha256(destination/'initfs'),
                    persistence_note='boot.img is read-only; root.img holds the writable system and home directories')
    encoded = json.dumps(metadata, indent=2)+'\n'
    (destination/'image.json').write_text(encoded)
    (ROOT/'work/qemu-arm64/persistent-desktop-image.json').write_text(encoded)
    print(f'Created persistent desktop: {root_disk}')
    print('Run: python3 scripts/run-qemu-arm64.py --desktop --persistent')


if __name__ == '__main__':
    main()
