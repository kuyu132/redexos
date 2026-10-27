#!/usr/bin/env bash
# Destructive by design: flash a verified raw image and verify the complete readback.
set -euo pipefail
image=${1:-}
device=${2:-}
shift $(( $# >= 2 ? 2 : $# ))
yes=0
allow_fixed=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes) yes=1 ;;
        --allow-fixed) allow_fixed=1 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
[[ -n "$image" && -n "$device" ]] || {
    echo "Usage: $0 IMAGE.img DEVICE [--yes] [--allow-fixed]" >&2; exit 2;
}
[[ -f "$image" ]] || { echo "Image not found: $image" >&2; exit 1; }
case "$image" in *.img) ;; *) echo 'Decompress .zst first; a raw .img is required.' >&2; exit 2;; esac

image=$(cd -- "$(dirname -- "$image")" && pwd)/$(basename -- "$image")
hash_file() {
    if command -v sha256sum >/dev/null; then sha256sum "$1" | awk '{print $1}'
    else shasum -a 256 "$1" | awk '{print $1}'; fi
}
hash_stream() {
    if command -v sha256sum >/dev/null; then sha256sum | awk '{print $1}'
    else shasum -a 256 | awk '{print $1}'; fi
}
system=$(uname -s)
if [[ "$system" == Darwin ]]; then size=$(stat -f %z "$image"); else size=$(stat -c %s "$image"); fi
(( size % (4 * 1024 * 1024) == 0 )) || { echo 'Image size must be a multiple of 4 MiB.' >&2; exit 1; }
expected=$(hash_file "$image")

if [[ "$system" == Linux ]]; then
    device=$(readlink -f "$device")
    [[ -b "$device" ]] || { echo "Not a block device: $device" >&2; exit 1; }
    [[ $(lsblk -dnro TYPE "$device") == disk ]] || { echo 'Target must be a whole disk.' >&2; exit 1; }
    removable=$(lsblk -dnro RM "$device")
    if [[ "$removable" != 1 && "$allow_fixed" != 1 ]]; then
        echo 'Target is not marked removable; pass --allow-fixed only after checking it manually.' >&2; exit 1
    fi
    root_source=$(findmnt -nro SOURCE / || true)
    [[ "$root_source" != "$device"* ]] || { echo 'Refusing to overwrite the root disk.' >&2; exit 1; }
    mapfile -t mounts < <(lsblk -nrpo MOUNTPOINT "$device" | awk 'NF')
    info=$(lsblk -O -J "$device")
elif [[ "$system" == Darwin ]]; then
    [[ "$device" =~ ^/dev/disk[0-9]+$ ]] || { echo 'Use a whole macOS device such as /dev/disk4.' >&2; exit 1; }
    info=$(diskutil info "$device")
    if ! grep -Eq 'Removable Media:.*(Yes|Removable)|Protocol:.*USB' <<<"$info" && [[ "$allow_fixed" != 1 ]]; then
        echo 'Target is not reported as removable/USB; pass --allow-fixed only after checking it manually.' >&2; exit 1
    fi
    mounts=()
else
    echo "Unsupported host: $system" >&2; exit 1
fi

echo "$info"
echo "IMAGE  $image"
echo "SHA256 $expected"
echo "TARGET $device"
if [[ "$yes" != 1 ]]; then
    read -r -p "Type FLASH $device to erase it: " answer
    [[ "$answer" == "FLASH $device" ]] || { echo 'Cancelled.' >&2; exit 1; }
fi

if [[ "$system" == Linux ]]; then
    for mount in "${mounts[@]}"; do sudo umount "$mount"; done
    sudo dd if="$image" of="$device" bs=4M conv=fsync status=progress
    actual=$(sudo dd if="$device" bs=4M count=$((size / 1024 / 1024 / 4)) status=progress | hash_stream)
else
    diskutil unmountDisk "$device"
    raw=${device/\/dev\/disk/\/dev\/rdisk}
    sudo dd if="$image" of="$raw" bs=4m
    sync
    actual=$(sudo dd if="$raw" bs=4m count=$((size / 1024 / 1024 / 4)) | hash_stream)
    diskutil eject "$device"
fi
[[ "$actual" == "$expected" ]] || { echo "Readback mismatch: $actual" >&2; exit 1; }
echo "FLASH_VERIFY_PASS sha256=$actual bytes=$size"
