#!/usr/bin/env python3
"""Clang ELF cross wrapper for the pinned native GDB build (Apple Silicon)."""
import os
from pathlib import Path
import subprocess
import sys

sdk = Path(os.environ['REDOX_DEBUG_SYSROOT'])
llvm = Path(os.environ.get('LLVM_BIN', '/opt/homebrew/opt/llvm/bin'))
cxx = Path(sys.argv[0]).name.endswith('++')
args = sys.argv[1:]
flags = ['--target=aarch64-unknown-none-elf', '--sysroot='+str(sdk),
         '-D__redox__', '-D_GNU_SOURCE', '-nostdinc++']
if cxx:
    for include in ('include/c++/13.2.0', 'include/c++/13.2.0/aarch64-unknown-redox',
                    'include/c++/13.2.0/backward'):
        flags += ['-isystem', str(sdk/include)]
flags += ['-isystem', str(sdk/'include')]
if not any(flag in args for flag in ('-c', '-E', '-S', '-fsyntax-only', '--version', '-v')):
    flags += ['--ld-path='+os.environ['REDOX_DEBUG_LD'], '-nostdlib',
              str(sdk/'lib/crt0.o'), str(sdk/'gcc/crtbegin.o'),
              '-Wl,-T,'+os.environ['REDOX_DEBUG_ARRAYS'],
              '-L'+str(sdk/'lib'), '-L'+str(sdk/'gcc')]
    args += ['-Wl,--start-group'] + (['-lstdc++'] if cxx else [])
    args += ['-lc', '-lm', '-lgcc', '-lgcc_eh', '-Wl,--end-group', str(sdk/'gcc/crtend.o')]
raise SystemExit(subprocess.call([str(llvm/('clang++' if cxx else 'clang'))]+flags+args))
