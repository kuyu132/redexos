#!/usr/bin/env bash
set -euo pipefail
if [[ $(uname -s) != Linux || ! -r /etc/os-release ]]; then
    echo 'Run on Ubuntu 22.04 or newer.' >&2; exit 1
fi
source /etc/os-release
if [[ ${ID:-} != ubuntu ]]; then
    echo 'This dependency installer is for Ubuntu.' >&2; exit 1
fi
sudo apt-get update
sudo apt-get install -y git make python3 curl podman uidmap \
    fuse3 fuse-overlayfs slirp4netns qemu-system-arm qemu-efi-aarch64 \
    device-tree-compiler util-linux mtools zstd
podman info >/dev/null
echo 'Dependencies installed. Build as your normal user, not with sudo.'
