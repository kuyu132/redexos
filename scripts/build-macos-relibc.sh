#!/usr/bin/env bash
# Build patched ARM64 relibc and its signal-register regression from source.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]] || {
    echo 'This entry point is for Apple Silicon macOS.' >&2; exit 1;
}
manifest="$root/qemu/sources.lock.json"
source_dir="$root/upstream/qemu-relibc"
toolchain=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["rust_toolchain"])' "$manifest")
features=$(python3 -c 'import json,sys; print(",".join(json.load(open(sys.argv[1]))["relibc"]["features"]))' "$manifest")
llvm_bin=${LLVM_BIN:-/opt/homebrew/opt/llvm/bin}
for tool in clang llvm-ar llvm-nm llvm-objcopy; do
    [[ -x "$llvm_bin/$tool" ]] || { echo "Missing $llvm_bin/$tool (brew install llvm)" >&2; exit 1; }
done
rust_sysroot=$(rustc +"$toolchain" --print sysroot)
linker="$rust_sysroot/lib/rustlib/aarch64-apple-darwin/bin/rust-lld"
[[ -x "$linker" ]] || { echo "Missing Rust LLD: $linker" >&2; exit 1; }

python3 - "$root" <<'PY'
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'scripts'))
from prepare import ROOT, checkout, git
from source_patches import ensure_patch
spec = json.loads((ROOT / 'qemu/sources.lock.json').read_text())['relibc']
tree = ROOT / 'upstream/qemu-relibc'
checkout(tree, spec)
for name, revision in spec['submodules'].items():
    if git(tree, 'ls-tree', 'HEAD', name).split()[2] != revision:
        raise ValueError(f'Submodule pin differs from gitlink: {name}')
    child = tree / name
    if (child / '.git').exists():
        if git(child, 'rev-parse', 'HEAD') != revision or git(child, 'status', '--porcelain'):
            raise ValueError(f'Preserve/review local submodule changes: {name}')
    else:
        subprocess.run(['git', '-C', str(tree), 'submodule', 'update', '--init', '--depth', '1', '--', name], check=True)
ensure_patch(tree, spec)
PY

mkdir -p "$root/artifacts/macos-relibc" "$root/work/macos-arm64"
run_dir=$(mktemp -d "$root/artifacts/macos-relibc/$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
exec > >(tee "$run_dir/build.log") 2>&1
finish() { result=$?; trap - EXIT; printf '%s\n' "$result" >"$run_dir/exit-code.txt"; exit "$result"; }
trap finish EXIT
echo "Output: $run_dir"
cp "$manifest" "$run_dir/sources.lock.json"
git -C "$source_dir" rev-parse HEAD >"$run_dir/relibc.commit"
git -C "$source_dir" diff --no-ext-diff --no-textconv --no-color --binary HEAD >"$run_dir/relibc.patch"
git -C "$source_dir" submodule status >"$run_dir/submodules.txt"
cp "$source_dir/Cargo.lock" "$run_dir/Cargo.lock"
rustc +"$toolchain" -vV >"$run_dir/toolchain.txt"
"$llvm_bin/clang" --version >"$run_dir/clang-version.txt"
"$linker" -flavor gnu --version >"$run_dir/linker-version.txt"
cp "$root/qemu/signal-registers.c" "$root/qemu/test-arrays.ld" "$root/qemu/tls-isolation.c" "$root/qemu/tls-module.c" "$run_dir/"
cp "$0" "$run_dir/build-script.sh"

export CARGO_TARGET_DIR="$root/work/macos-arm64/relibc-target"
export CARGO_NET_GIT_FETCH_WITH_CLI=true
export CC_aarch64_unknown_redox="$llvm_bin/clang"
export AR_aarch64_unknown_redox="$llvm_bin/llvm-ar"
export CFLAGS_aarch64_unknown_redox='--target=aarch64-unknown-none -fPIC'
export RUSTFLAGS=''
export NM="$llvm_bin/llvm-nm" OBJCOPY="$llvm_bin/llvm-objcopy" LC_ALL=C
cd "$source_dir"
cargo +"$toolchain" rustc --locked --release --target aarch64-unknown-redox \
    -Zbuild-std=core,alloc,compiler_builtins --features "$features" -- --emit "link=$run_dir/libc.a"
bash renamesyms.sh "$run_dir/libc.a" "$CARGO_TARGET_DIR/aarch64-unknown-redox/release/deps"
bash stripcore.sh "$run_dir/libc.a"
for component in ld_so src/crt0; do
    name=${component##*/}
    cargo +"$toolchain" rustc --manifest-path "$component/Cargo.toml" --locked --release \
        --target aarch64-unknown-redox -Zbuild-std=core,alloc,compiler_builtins -- \
        --emit "obj=$run_dir/$name.o" -C panic=abort
done
"$linker" -flavor gnu -m aarch64elf -shared --gc-sections --dynamic-list=dynlst.txt \
    -z pack-relative-relocs --sort-common --whole-archive "$run_dir/ld_so.o" "$run_dir/libc.a" \
    --no-whole-archive -soname libc.so.6 -o "$run_dir/libc.so"
"$llvm_bin/clang" --target=aarch64-unknown-none -fPIC -ffreestanding -O2 \
    -c "$run_dir/tls-module.c" -o "$run_dir/tls-module.o"
"$linker" -flavor gnu -m aarch64elf -shared -soname tls-module.so \
    "$run_dir/tls-module.o" -o "$run_dir/tls-module.so"
for test in signal-registers tls-isolation; do
    "$llvm_bin/clang" --target=aarch64-unknown-none -fno-stack-protector -ffreestanding -O2 \
        -c "$run_dir/$test.c" -o "$run_dir/$test.o"
    extra=("$run_dir/libc.so")
    [[ "$test" != tls-isolation ]] || extra+=("$run_dir/tls-module.so")
    "$linker" -flavor gnu -m aarch64elf -e _start --export-dynamic --hash-style=gnu \
        -T "$run_dir/test-arrays.ld" --dynamic-linker /lib/ld.so.1 \
        "$run_dir/crt0.o" "$run_dir/$test.o" "${extra[@]}" -o "$run_dir/$test"
done
cd "$run_dir"
file libc.so signal-registers tls-isolation tls-module.so | tee formats.txt
shasum -a 256 libc.a libc.so signal-registers tls-isolation tls-module.so tls-isolation.c tls-module.c sources.lock.json relibc.commit relibc.patch \
    Cargo.lock crt0.o signal-registers.c test-arrays.ld build-script.sh >SHA256SUMS
echo 'ARM64 relibc and signal-register regression compiled; run QEMU verification next.'
