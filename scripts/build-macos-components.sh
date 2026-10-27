#!/usr/bin/env bash
# Cross-compile shared AArch64 components on Apple Silicon; no root filesystem.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
profile=${1:-baseline}
case "$profile" in
    baseline) manifest="$root/sources.lock.json"; kernel="$root/upstream/kernel" ;;
    qemu) manifest="$root/qemu/sources.lock.json"; kernel="$root/upstream/qemu-kernel" ;;
    *) echo "Usage: $0 [baseline|qemu]" >&2; exit 2 ;;
esac
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]] || {
    echo 'This entry point is for Apple Silicon macOS.' >&2; exit 1;
}
for command in python3 git cargo rustup file shasum; do
    command -v "$command" >/dev/null || { echo "Missing: $command" >&2; exit 1; }
done
toolchain=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["rust_toolchain"])' "$manifest")
rustup run "$toolchain" rustc --version || {
    echo "Install with: rustup toolchain install $toolchain --profile minimal --component rust-src" >&2
    exit 1
}
objcopy=$(command -v llvm-objcopy || true)
if [[ -z "$objcopy" && -x /opt/homebrew/opt/llvm/bin/llvm-objcopy ]]; then
    objcopy=/opt/homebrew/opt/llvm/bin/llvm-objcopy
fi
[[ -n "$objcopy" ]] || { echo 'llvm-objcopy is required (Homebrew llvm).' >&2; exit 1; }
python3 - "$root" "$manifest" "$kernel" "$profile" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'scripts'))
from prepare import ROOT, checkout
from source_patches import ensure_patch
manifest = json.loads(Path(sys.argv[2]).read_text())
for name in ('kernel', 'bootloader'):
    path = Path(sys.argv[3]) if name == 'kernel' else ROOT / 'upstream' / name
    checkout(path, manifest['repositories'][name])
    if sys.argv[4] == 'qemu' or name == 'bootloader':
        ensure_patch(path, manifest['repositories'][name])
PY
mkdir -p "$root/artifacts/macos-arm64" "$root/work/macos-arm64"
run_dir=$(mktemp -d "$root/artifacts/macos-arm64/$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
exec > >(tee "$run_dir/build.log") 2>&1
finish() {
    result=$?
    trap - EXIT
    printf '%s\n' "$result" >"$run_dir/exit-code.txt"
    exit "$result"
}
trap finish EXIT
echo "Output: $run_dir"
cp "$manifest" "$run_dir/sources.lock.json"
rustup run "$toolchain" rustc -vV >"$run_dir/toolchain.txt"
for component in kernel bootloader; do
    component_dir="$root/upstream/$component"
    if [[ "$component" == kernel ]]; then component_dir="$kernel"; fi
    git -C "$component_dir" rev-parse HEAD >"$run_dir/$component.commit"
    git -C "$component_dir" diff --no-ext-diff --no-textconv --no-color --binary HEAD >"$run_dir/$component.patch"
done
export CARGO_NET_GIT_FETCH_WITH_CLI=true
(
    cd "$kernel"
    export CARGO_TARGET_DIR="$root/work/macos-arm64/kernel-target"
    export RUSTFLAGS=''
    cargo +"$toolchain" rustc --locked --release --bin kernel \
        --manifest-path "$kernel/Cargo.toml" \
        --target "$kernel/targets/aarch64-unknown-kernel.json" \
        -Z build-std=core,alloc -Z build-std-features=compiler-builtins-mem \
        -Z json-target-spec -- \
        -C link-arg=-T -C "link-arg=$kernel/linkers/aarch64.ld" \
        -C link-arg=-z -C link-arg=max-page-size=0x1000 \
        --emit "link=$run_dir/kernel.all"
)
"$objcopy" --only-keep-debug "$run_dir/kernel.all" "$run_dir/kernel.sym"
"$objcopy" --strip-debug "$run_dir/kernel.all" "$run_dir/kernel"
(
    cd "$root/upstream/bootloader"
    export CARGO_TARGET_DIR="$root/work/macos-arm64/bootloader-target"
    export RUSTFLAGS='--cfg aes_force_soft -Zunstable-options'
    for variant in normal live; do
        features=''
        output=bootloader.efi
        if [[ "$variant" == live ]]; then
            features=live
            output=bootloader-live.efi
        fi
        cargo +"$toolchain" rustc --locked --release --bin bootloader \
            --target aarch64-unknown-uefi \
            -Z build-std=core,alloc -Z build-std-features=compiler-builtins-mem \
            --features "$features" -- --emit "link=$run_dir/$output"
    done
)
(
    cd "$run_dir"
    file kernel.all kernel bootloader.efi bootloader-live.efi | tee formats.txt
    shasum -a 256 kernel.all kernel.sym kernel bootloader.efi bootloader-live.efi \
        sources.lock.json kernel.commit kernel.patch bootloader.commit bootloader.patch >SHA256SUMS
)
echo 'AArch64 components compiled; no userspace, firmware package, or board boot verified.'
