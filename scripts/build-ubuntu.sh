#!/usr/bin/env bash
# Builds experimental Redox live payloads and board-specific flashable SD images.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
board=${1:-}
mode=${2:-}
case "$board" in pi4b|pi5) ;; *) echo "Usage: $0 {pi4b|pi5} [--dry-run]" >&2; exit 2;; esac
case "$mode" in ''|--dry-run) ;; *) echo "Unknown option: $mode" >&2; exit 2;; esac
[[ $# -le 2 ]] || { echo 'Too many arguments' >&2; exit 2; }
# Upstream Makefiles do not safely quote all paths.
if [[ "$root" =~ [[:space:]] ]]; then
    echo 'Use a workspace path without spaces for the upstream build system.' >&2; exit 1
fi
tree="$root/work/$board/redox"
config_name="${board}-bringup"
target="build/aarch64/$config_name/redox-live.iso"
make_args=(ARCH=aarch64 "BOARD=$board" "CONFIG_NAME=$config_name"
    "FILESYSTEM_CONFIG=config/aarch64/$board/minimal.toml"
    PODMAN_BUILD=1 FSTOOLS_IN_PODMAN=1 PREFIX_BINARY=1 REPO_BINARY=0
    USE_SELINUX=0 "IMAGE_TAG=redexos-$board" PODMAN_CACHE_PULL=0)
if [[ "$mode" == --dry-run ]]; then
    printf 'python3 %q prepare %q\n' "$root/scripts/prepare.py" "$board"
    printf 'python3 %q %q --output %q\n' "$root/scripts/prepare-board-dtb.py" "$board" \
        "$root/artifacts/$board/RUN/board-contract"
    printf 'make -C %q ' "$tree"; printf '%q ' "${make_args[@]}" "$target"; printf '\n'
    printf 'python3 %q %q --payload %q --configured-payload --output %q\n' \
        "$root/scripts/prepare-sd-image.py" "$board" "$root/artifacts/$board/RUN/payload" \
        "$root/artifacts/$board/RUN/sd-image"
    echo 'Output includes the live payload, verified DTB contract and flashable board UEFI SD image.'
    exit 0
fi
[[ $(uname -s) == Linux ]] || { echo 'Run the build on Ubuntu.' >&2; exit 1; }
case $(uname -m) in x86_64|aarch64) ;; *) echo 'Need an x86_64 or aarch64 Linux host.' >&2; exit 1;; esac
[[ $EUID -ne 0 ]] || { echo 'Run as your normal user.' >&2; exit 1; }
for command in python3 git make podman flock sha256sum; do
    command -v "$command" >/dev/null || { echo "Missing: $command (run ubuntu-deps.sh)" >&2; exit 1; }
done
podman info >/dev/null
[[ -c /dev/fuse ]] || { echo '/dev/fuse is required by the upstream container build.' >&2; exit 1; }
mkdir -p "$root/work/$board" "$root/artifacts/$board"
exec 9>"$root/work/$board/build.lock"
flock -n 9 || { echo "Another $board build is running." >&2; exit 1; }
run_dir=$(mktemp -d "$root/artifacts/$board/$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
exec > >(tee "$run_dir/build.log") 2>&1
echo "Build evidence: $run_dir"
# Trap keeps source resolution evidence for failed builds as well.
finish() {
    result=$?
    trap - EXIT
    python3 "$root/scripts/prepare.py" snapshot "$board" --output "$run_dir/resolved-sources.json" || true
    podman image inspect "redexos-$board" >"$run_dir/container.json" 2>/dev/null || true
    printf '%s\n' "$result" >"$run_dir/exit-code.txt"
    exit "$result"
}
trap finish EXIT
python3 "$root/scripts/prepare.py" prepare "$board"
python3 "$root/scripts/prepare-board-dtb.py" "$board" --output "$run_dir/board-contract"
cp "$root/sources.lock.json" "$run_dir/"
cp "$root/boards/$board.json" "$run_dir/board.json"
uname -a >"$run_dir/host.txt"
podman version >>"$run_dir/host.txt"
printf '%q ' make "${make_args[@]}" "$target" >"$run_dir/command.txt"
printf '\n' >>"$run_dir/command.txt"
make -C "$tree" "${make_args[@]}" "$target"
mkdir -p "$run_dir/payload/EFI/BOOT"
cp "$tree/$target" "$run_dir/payload/redox-live.iso"
cp "$tree/build/aarch64/$config_name/bootloader-live.efi" "$run_dir/payload/EFI/BOOT/BOOTAA64.EFI"
(
    cd "$run_dir/payload"
    sha256sum redox-live.iso EFI/BOOT/BOOTAA64.EFI >SHA256SUMS
)
python3 "$root/scripts/prepare-sd-image.py" "$board" --payload "$run_dir/payload" \
    --configured-payload --output "$run_dir/sd-image"
echo 'Live payload and flashable board UEFI SD image built; no hardware boot has been validated.'
