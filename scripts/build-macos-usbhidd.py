#!/usr/bin/env python3
"""Build locked static drivers or RedoxFS with macOS Rust and ARM64 target archives."""
import argparse, json, os, platform, shutil, subprocess, tarfile, tempfile
from datetime import datetime, timezone
from pathlib import Path
from prepare import ROOT, checkout, sha256
from source_patches import ensure_patch, expected_patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver', choices=('usbhidd', 'ahcid', 'redoxfs'), default='usbhidd')
    driver = parser.parse_args().driver
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('This build entry point requires Apple Silicon macOS')
    spec = json.loads((ROOT / 'qemu/sources.lock.json').read_text())
    source = spec['redoxfs'] if driver == 'redoxfs' else spec['base']
    tree = ROOT / ('upstream/redoxfs' if driver == 'redoxfs' else 'upstream/qemu-base')
    checkout(tree, source)
    ensure_patch(tree, source)
    libc = next((p for p in sorted((ROOT / 'artifacts/macos-relibc').glob('20*'), reverse=True)
                 if (p / 'exit-code.txt').is_file() and (p / 'exit-code.txt').read_text().strip() == '0'
                 and json.loads((p / 'sources.lock.json').read_text())['relibc'] == spec['relibc']
                 and (p / 'relibc.patch').read_bytes() == expected_patch(spec['relibc'])), None)
    if libc is None:
        raise RuntimeError('First run bash scripts/build-macos-relibc.sh')
    subprocess.run(['shasum', '-a', '256', '-c', 'SHA256SUMS'], cwd=libc, check=True)
    downloads = ROOT / 'work/qemu-arm64/downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / 'aarch64-gcc-install.tar.gz'
    gcc = spec['gcc_unwind']
    if not archive.exists():
        partial = archive.with_suffix('.partial')
        subprocess.run(['curl', '-fL', '--retry', '2', gcc['url'], '-o', str(partial)], check=True)
        if sha256(partial) != gcc['sha256']:
            raise ValueError('Unwind archive checksum mismatch')
        partial.replace(archive)
    if sha256(archive) != gcc['sha256']:
        raise ValueError('Cached unwind archive checksum mismatch')
    output = ROOT / f'artifacts/macos-{driver}'
    output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=output))
    libs = run / 'lib'
    libs.mkdir()
    for name in ('libc.a', 'crt0.o'):
        shutil.copy2(libc / name, libs / name)
    with tarfile.open(archive) as tar:
        (libs / 'libgcc_eh.a').write_bytes(tar.extractfile(gcc['member']).read())
    if sha256(libs / 'libgcc_eh.a') != gcc['member_sha256']:
        raise ValueError('Unwind member checksum mismatch')
    shutil.copy2(ROOT / 'qemu/static-arrays.ld', run / 'static-arrays.ld')
    shutil.copy2(ROOT / 'qemu/sources.lock.json', run / 'sources.lock.json')
    shutil.copy2(__file__, run / 'build-script.py')
    shutil.copy2(tree / 'Cargo.lock', run / 'Cargo.lock')
    prefix = 'redoxfs' if driver == 'redoxfs' else 'base'
    (run / (prefix+'.patch')).write_bytes(expected_patch(source))
    (run / (prefix+'.commit')).write_text(source['rev'] + '\n')
    (run / 'libc-input.json').write_text(json.dumps({'directory':str(libc), 'libc.a':sha256(libs/'libc.a'), 'crt0.o':sha256(libs/'crt0.o')}, indent=2)+'\n')
    toolchain = spec['rust_toolchain']
    sysroot = subprocess.check_output(['rustc', '+'+toolchain, '--print', 'sysroot'], text=True).strip()
    linker = Path(sysroot) / 'lib/rustlib/aarch64-apple-darwin/bin/rust-lld'
    env = dict(os.environ, CARGO_TARGET_DIR=str(ROOT / ('work/macos-arm64/redoxfs-target' if driver == 'redoxfs' else 'work/macos-arm64/base-target')),
               CARGO_NET_GIT_FETCH_WITH_CLI='true', CARGO_HTTP_MULTIPLEXING='false',
               RUSTFLAGS=f'-C linker={linker} -C linker-flavor=ld.lld -C link-arg={libs}/crt0.o -C link-arg=-T{run}/static-arrays.ld -L native={libs} -C panic=abort -C target-feature=+crt-static -C link-arg=--export-dynamic')
    print(f'Output: {run}', flush=True)
    with (run / 'build.log').open('w') as log:
        selection = ['--no-default-features', '--features', 'std,log', '--bin', 'redoxfs'] if driver == 'redoxfs' else ['-p', driver]
        result = subprocess.run(['cargo', '+'+toolchain, 'build', '--manifest-path', str(tree/'Cargo.toml'), '--locked', '--release', '--target', 'aarch64-unknown-redox', '-Zbuild-std=std,panic_abort']+selection, env=env, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        (run/'exit-code.txt').write_text(str(result.returncode)+'\n')
        raise RuntimeError(f'Build failed; see {run}/build.log')
    shutil.copy2(Path(env['CARGO_TARGET_DIR'])/f'aarch64-unknown-redox/release/{driver}', run/driver)
    if driver == 'redoxfs':
        shutil.copy2(ROOT/'qemu/development/fs-cache-reuse.c', run/'fs-cache-reuse.c')
        clang = Path(subprocess.check_output(['brew', '--prefix', 'llvm'], text=True).strip())/'bin/clang'
        subprocess.run([str(clang), '--target=aarch64-unknown-none', '-fno-stack-protector', '-ffreestanding', '-O2',
                        '-c', str(run/'fs-cache-reuse.c'), '-o', str(run/'fs-cache-reuse.o')], check=True)
        subprocess.run([str(linker), '-flavor', 'gnu', '-m', 'aarch64elf', '-e', '_start', '--export-dynamic',
                        '--hash-style=gnu', '-T', str(ROOT/'qemu/test-arrays.ld'), '--dynamic-linker', '/lib/ld.so.1',
                        str(libs/'crt0.o'), str(run/'fs-cache-reuse.o'), str(libc/'libc.so'),
                        '-o', str(run/'fs-cache-reuse')], check=True)
    files = sorted(p for p in run.rglob('*') if p.is_file())
    (run/'SHA256SUMS').write_text(''.join(f'{sha256(p)}  {p.relative_to(run)}\n' for p in files))
    (run/'exit-code.txt').write_text('0\n')
    print(f'Built static driver: {run}/{driver}')

if __name__ == '__main__':
    main()
