#!/usr/bin/env python3
"""Cross-build native ARM64 GNU GDB and the process manager, with pinned inputs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from prepare import ROOT, checkout, sha256
from source_patches import ensure_patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--libc', type=Path, help='Matching successful build-macos-relibc.sh output')
    args = parser.parse_args()
    lock = json.loads((ROOT/'qemu/sources.lock.json').read_text())
    metadata = json.loads((ROOT/'work/qemu-arm64/persistent-desktop-image.json').read_text())
    libc = (args.libc or Path(metadata['relibc_components'])).resolve()
    if (libc/'relibc.patch').read_bytes() != (ROOT/lock['relibc']['patch']['path']).read_bytes():
        raise RuntimeError('Build the current pinned relibc first')
    subprocess.run(['shasum','-a','256','-c','SHA256SUMS'],cwd=libc,check=True)
    source = ROOT/'upstream/qemu-binutils-gdb'
    base = ROOT/'upstream/qemu-base'
    for tree, pin in ((source,lock['native_gdb']),(base,lock['base'])):
        checkout(tree,pin); ensure_patch(tree,pin)
    work = ROOT/'work/native-debug'
    work.mkdir(parents=True,exist_ok=True)
    sdk = work/'sysroot'
    # Dependency installs overwrite unchanged headers; retain their timestamps so
    # incremental builds only recompile against headers whose bytes changed.
    headers = {p:(p.read_bytes(),p.stat().st_atime_ns,p.stat().st_mtime_ns)
               for p in (sdk/'include').rglob('*') if p.is_file()}
    def restore_unchanged_headers():
        for p,(data,atime,mtime) in headers.items():
            if p.exists() and p.read_bytes()==data:os.utime(p,ns=(atime,mtime))
    llvm = Path('/opt/homebrew/opt/llvm/bin')
    env = os.environ.copy()
    for key in ('CPPFLAGS','LDFLAGS','LIBRARY_PATH','CPATH','C_INCLUDE_PATH','CPLUS_INCLUDE_PATH','SDKROOT','MACOSX_DEPLOYMENT_TARGET'):
        env.pop(key,None)
    rust = subprocess.check_output(['rustup','run',lock['rust_toolchain'],'rustc','--print','sysroot'],text=True).strip()
    linker = Path(rust)/'lib/rustlib/aarch64-apple-darwin/bin/rust-lld'
    env.update(AR=str(llvm/'llvm-ar'), RANLIB=str(llvm/'llvm-ranlib'),
        REDOX_DEBUG_SYSROOT=str(sdk), REDOX_DEBUG_LD=str(work/'redox-ld'),
        REDOX_DEBUG_ARRAYS=str(ROOT/'qemu/static-arrays.ld'), LLVM_BIN=str(llvm))
    parent=ROOT/'artifacts/macos-native-debug';parent.mkdir(parents=True,exist_ok=True)
    output=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=parent))
    print(f'Output: {output}',flush=True)
    with (output/'build.log').open('w') as log:
        def run(command,cwd=ROOT,extra=None):
            subprocess.run([str(x) for x in command],cwd=cwd,env=env| (extra or {}),stdout=log,stderr=subprocess.STDOUT,check=True)
        try:
            archive=ROOT/'work/qemu-arm64/downloads/aarch64-gcc-install.tar.gz'
            if sha256(archive)!=lock['gcc_unwind']['sha256']:raise RuntimeError('Cross SDK archive hash mismatch')
            # Only target headers/libraries; never execute the Linux host tools.
            with tarfile.open(archive) as tf:
                for item in tf.getmembers():
                    name=item.name.removeprefix('./')
                    destination=None
                    for prefix,target in [('aarch64-unknown-redox/include/','include/'),('aarch64-unknown-redox/lib/','lib/'),('lib/gcc/aarch64-unknown-redox/13.2.0/','gcc/')]:
                        if name.startswith(prefix):destination=sdk/(target+name[len(prefix):]);break
                    if destination is None or item.isdir():continue
                    if not destination.resolve().is_relative_to(sdk.resolve()):raise ValueError('Unsafe archive member')
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    if item.isfile():
                        data=tf.extractfile(item).read()
                        if not destination.exists() or destination.read_bytes()!=data:
                            destination.write_bytes(data)
            shutil.copy2(sdk/'lib/libm.a',sdk/'lib/libm-original.a')
            for name in ('libc.a','crt0.o'):shutil.copy2(libc/name,sdk/'lib'/name)
            # C++ linkage must match the newly generated ptrace ABI.
            header=sdk/'include/sys/ptrace.h'
            text=header.read_text().replace('int ptrace(int request, ...);','long ptrace(int request, ...);')
            if 'extern "C"' not in text:text=text.replace('long ptrace(int request, ...);','#ifdef __cplusplus\nextern "C"\n#endif\nlong ptrace(int request, ...);')
            header.write_text(text)
            # Current stripped libc supplies most OpenLibm symbols, but not fmod.
            # Keep only these two pinned target objects to avoid duplicate symbols.
            for obj in ('e_fmod.c.o','e_fmodf.c.o'):
                with (work/obj).open('wb') as dst:subprocess.run([str(llvm/'llvm-ar'),'p',str(sdk/'lib/libm-original.a'),obj],stdout=dst,check=True)
            (sdk/'lib/libm.a').unlink()
            run([llvm/'llvm-ar','rcs',sdk/'lib/libm.a',work/'e_fmod.c.o',work/'e_fmodf.c.o'])
            wrapper=ROOT/'qemu/development/redox-cross.py';wrapper.chmod(0o755)
            for name in ('redox-cc','redox-c++'):
                dest=work/name
                if dest.is_symlink():dest.unlink()
                elif dest.exists():dest.rename(work/(name+'.previous'))
                dest.symlink_to(wrapper)
            (work/'redox-ld').write_text('#!/bin/sh\nexec '+__import__('shlex').quote(str(linker))+' -flavor gnu "$@"\n')
            (work/'redox-ld').chmod(0o755)
            env.update(CC=str(work/'redox-cc'),CXX=str(work/'redox-c++'),CC_FOR_BUILD='/usr/bin/clang',CXX_FOR_BUILD='/usr/bin/clang++',CFLAGS='-O2',CXXFLAGS='-O2 -Wno-error')
            downloads=work/'downloads';downloads.mkdir(exist_ok=True)
            for name,version,options in [('gmp','6.3.0',['--disable-assembly','--disable-cxx']),('mpfr','4.2.2',['--with-gmp='+str(sdk)]),('libiconv','1.18',['--disable-nls'])]:
                pin=lock['native_gdb'][name];packed=downloads/(name+('.tar.gz' if name=='libiconv' else '.tar.xz'))
                if not packed.exists():urllib.request.urlretrieve(pin['url'],packed)
                if sha256(packed)!=pin['sha256']:raise RuntimeError(name+' archive hash mismatch')
                src=work/(name+'-'+version)
                if not src.exists():
                    with tarfile.open(packed) as tf:tf.extractall(work,filter='data')
                for config in src.rglob('config.sub'):
                    shutil.copy2(source/'config.sub',config)
                build=work/(name+'-build');build.mkdir(exist_ok=True)
                if not (build/'Makefile').exists():run([src/'configure','--host=aarch64-unknown-redox','--build=aarch64-apple-darwin','--prefix='+str(sdk),'--enable-static','--disable-shared']+options,build)
                run(['make','-j6'],build);run(['make','install'],build)
                restore_unchanged_headers()
            build=work/'gdb-iconv-build';build.mkdir(exist_ok=True)
            if not (build/'Makefile').exists():
                run([source/'configure','--host=aarch64-unknown-redox','--target=aarch64-unknown-redox','--build=aarch64-apple-darwin','--prefix=/usr',
                    '--disable-binutils','--disable-gas','--disable-ld','--disable-gold','--disable-gprof','--disable-gprofng','--disable-sim','--disable-nls','--disable-werror','--disable-tui','--disable-threading',
                    '--without-python','--without-guile','--without-debuginfod','--without-expat','--without-lzma','--without-zstd','--without-source-highlight','--without-babeltrace','--without-intel-pt',
                    '--with-gmp='+str(sdk),'--with-mpfr='+str(sdk),'--with-libiconv-prefix='+str(sdk)],build)
            # An external libc archive is not tracked by GNU Make's dependency files.
            (build/'gdb/gdb').unlink(missing_ok=True)
            run(['make','-j6','MAKEINFO=true','CXXFLAGS=-O2 -Wno-error','all-gdb'],build)
            shutil.copy2(build/'gdb/gdb',output/'gdb')
            for test in ('ptrace-smoke','wait-continued','stdio-unbuffered','tty-drain','exec-saved-ids'):
                run([work/'redox-cc','-O0','-g',ROOT/('qemu/development/'+test+'.c'),'-o',output/test])
            run(['cargo','+'+lock['rust_toolchain'],'build','--manifest-path',base/'bootstrap/Cargo.toml','--release','--locked','--target','aarch64-unknown-redox','-Zbuild-std=core,alloc,compiler_builtins','-Zbuild-std-features=compiler-builtins-mem'],extra={'CARGO_TARGET_DIR':str(ROOT/'work/macos-arm64/bootstrap-target'),'RUSTFLAGS':f'-C linker={linker} -C linker-flavor=ld.lld -C target-feature=+crt-static -C panic=abort'})
            shutil.copy2(ROOT/'work/macos-arm64/bootstrap-target/aarch64-unknown-redox/release/bootstrap',output/'bootstrap')
            run(['cargo','+'+lock['rust_toolchain'],'build','--manifest-path',base/'Cargo.toml','--locked','--release','-p','redox-initfs-tools','--bin','redox-initfs-ar'])
            shutil.copy2(base/'target/release/redox-initfs-ar',output/'redox-initfs-ar')
            for name,pin in [('base',lock['base']),('relibc',lock['relibc']),('gdb',lock['native_gdb'])]:
                shutil.copy2(ROOT/pin['patch']['path'],output/(name+'.patch'))
            shutil.copy2(build/'gdb/config.h',output/'gdb-config.h')
            shutil.copy2(build/'gdb/config.log',output/'gdb-config.log')
            (output/'sources.lock.json').write_text(json.dumps(lock,indent=2)+'\n')
            (output/'build-inputs.json').write_text(json.dumps({'libc_build':str(libc),'libc_a_sha256':sha256(libc/'libc.a'),'clang':subprocess.check_output([str(llvm/'clang'),'--version'],text=True),'cross_wrapper_sha256':sha256(wrapper),'aarch64_signal_return':'kernel restores x0-x30, SP, PC, and NZCV','configure_options':['--disable-threading','--with-libiconv-prefix='+str(sdk)]},indent=2)+'\n')
            files=('gdb','bootstrap','ptrace-smoke','wait-continued','stdio-unbuffered','tty-drain','exec-saved-ids','redox-initfs-ar','gdb.patch','base.patch','relibc.patch','sources.lock.json','build-inputs.json')
            (output/'SHA256SUMS').write_text(''.join(sha256(output/name)+'  '+name+'\n' for name in files))
            (output/'exit-code.txt').write_text('0\n')
        except Exception:
            (output/'exit-code.txt').write_text('1\n');raise
    print(f'Native debugger build complete: {output}',flush=True)


if __name__=='__main__':main()
