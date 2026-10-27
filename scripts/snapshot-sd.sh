#!/usr/bin/env bash
# Read back the exact boot-image region after UEFI setup for forensic recovery.
set -euo pipefail
device=${1:-}
manifest=${2:-}
output=${3:-}
[[ -n "$device" && -f "$manifest" && -n "$output" ]] || {
    echo "Usage: $0 DEVICE manifest.json OUTPUT.img" >&2; exit 2;
}
[[ ! -e "$output" && ! -e "$output.zst" ]] || { echo 'Output already exists.' >&2; exit 1; }
mkdir -p "$(dirname -- "$output")"
hash_file() {
    if command -v sha256sum >/dev/null; then sha256sum "$1" | awk '{print $1}'
    else shasum -a 256 "$1" | awk '{print $1}'; fi
}
read -r size board < <(python3 - "$manifest" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print(d['image_size'], d['board'])
PY
)
(( size % (4 * 1024 * 1024) == 0 )) || { echo 'Manifest image size is not 4 MiB aligned.' >&2; exit 1; }
count=$((size / 1024 / 1024 / 4))
case $(uname -s) in
    Linux)
        [[ -b "$device" ]] || { echo 'Need a block device.' >&2; exit 1; }
        sudo dd if="$device" of="$output" bs=4M count="$count" status=progress
        lsblk -O -J "$device" >"$output.device.json"
        ;;
    Darwin)
        [[ "$device" =~ ^/dev/disk[0-9]+$ ]] || { echo 'Use /dev/diskN.' >&2; exit 1; }
        diskutil unmountDisk "$device"
        raw=${device/\/dev\/disk/\/dev\/rdisk}
        sudo dd if="$raw" of="$output" bs=4m count="$count"
        diskutil info -plist "$device" >"$output.device.plist"
        ;;
    *) echo 'Unsupported host.' >&2; exit 1;;
esac
actual=$(hash_file "$output")
zstd -T0 -15 "$output" -o "$output.zst"
compressed=$(hash_file "$output.zst")
cat >"$output.snapshot.txt" <<EOF
board=$board
device=$device
bytes=$size
image_sha256=$actual
compressed_sha256=$compressed
source_manifest=$manifest
EOF
echo "SNAPSHOT_PASS image=$output sha256=$actual compressed=$output.zst"
