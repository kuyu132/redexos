#!/usr/bin/env python3
"""Build a QEMU ESP from locally compiled core components and pinned official userspace."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse

from prepare import ROOT, checkout, sha256
from source_patches import expected_patch


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--components", type=Path, help="Successful build-macos-components.sh qemu output")
    parser.add_argument("--libc-components", type=Path, help="Successful build-macos-relibc.sh output")
    parser.add_argument("--kernel", choices=("local", "official"), default="local")
    parser.add_argument("--profile", choices=("server", "desktop"), default="server")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "qemu/sources.lock.json").read_text())
    patches = {name: expected_patch(source) for name, source in manifest["repositories"].items()}
    candidates = ([args.components.resolve()] if args.components else
                  sorted((ROOT / "artifacts/macos-arm64").glob("20*"), reverse=True))
    components = None
    for candidate in candidates:
        if not (candidate / "exit-code.txt").exists() or (candidate / "exit-code.txt").read_text().strip() != "0":
            continue
        if all((candidate / f"{name}.commit").read_text().strip() == source["rev"]
               and (candidate / f"{name}.patch").read_bytes() == patches[name]
               for name, source in manifest["repositories"].items()):
            components = candidate
            break
    if components is None:
        raise RuntimeError("First run: bash scripts/build-macos-components.sh qemu")
    run("shasum", "-a", "256", "-c", "SHA256SUMS", cwd=components)
    libc_spec = manifest["relibc"]
    libc_patch = expected_patch(libc_spec)
    libc_candidates = ([args.libc_components.resolve()] if args.libc_components else
                       sorted((ROOT / "artifacts/macos-relibc").glob("20*"), reverse=True))
    libc_components = next((path for path in libc_candidates
                            if (path / "exit-code.txt").is_file()
                            and (path / "exit-code.txt").read_text().strip() == "0"
                            and json.loads((path / "sources.lock.json").read_text())["relibc"] == libc_spec
                            and (path / "relibc.commit").read_text().strip() == libc_spec["rev"]
                            and (path / "relibc.patch").read_bytes() == libc_patch), None)
    if libc_components is None:
        raise RuntimeError("First run: bash scripts/build-macos-relibc.sh")
    run("shasum", "-a", "256", "-c", "SHA256SUMS", cwd=libc_components)
    driver_components = None
    if args.profile == "desktop":
        driver_components = next((p for p in sorted((ROOT / "artifacts/macos-usbhidd").glob("20*"), reverse=True)
                                  if (p / "exit-code.txt").is_file()
                                  and (p / "exit-code.txt").read_text().strip() == "0"
                                  and json.loads((p / "sources.lock.json").read_text())["base"] == manifest["base"]
                                  and json.loads((p / "sources.lock.json").read_text())["relibc"] == libc_spec
                                  and (p / "base.patch").read_bytes() == expected_patch(manifest["base"])
                                  and json.loads((p / "libc-input.json").read_text())["libc.a"] == sha256(libc_components / "libc.a")), None)
        if driver_components is None:
            raise RuntimeError("First run: python3 scripts/build-macos-usbhidd.py")
        run("shasum", "-a", "256", "-c", "SHA256SUMS", cwd=driver_components)
    for command in ("curl", "zstd", "mformat", "mmd", "mcopy", "cargo"):
        if shutil.which(command) is None:
            raise RuntimeError(f"Missing {command}; macOS packages: brew install qemu zstd mtools llvm")

    work = ROOT / "work/qemu-arm64"
    downloads = work / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    source = manifest["desktop_image" if args.profile == "desktop" else "userspace_image"]
    compressed = downloads / Path(urlparse(source["url"]).path).name
    if not compressed.exists():
        partial = compressed.with_suffix(compressed.suffix + ".partial")
        run("curl", "-fL", "--retry", "2", "--connect-timeout", "15", source["url"], "-o", partial)
        if sha256(partial) != source["sha256"]:
            raise RuntimeError("Official image checksum mismatch")
        partial.replace(compressed)
    if sha256(compressed) != source["sha256"]:
        raise RuntimeError("Cached official image checksum mismatch")

    checkout(ROOT / "upstream/redoxfs", manifest["redoxfs"])
    run("cargo", "+" + manifest["rust_toolchain"], "build", "--release", "--locked",
        "--manifest-path", ROOT / "tools/redox-image/Cargo.toml")
    tool = ROOT / "tools/redox-image/target/release/redox-image"
    stage = Path(tempfile.mkdtemp(prefix="image-", dir=work))
    live = stage / "redox-live.iso"
    run("zstd", "-d", compressed, "-o", live)
    if args.kernel == "local":
        for name in ("kernel", "kernel.all", "kernel.sym"):
            run(tool, "put", live, f"/usr/lib/boot/{name}", components / name)
    run(tool, "put", live, "/usr/lib/qemu-smoke.sh", ROOT / "qemu/userspace-smoke.sh")
    if args.profile == "desktop":
        run(tool, "put", live, "/usr/lib/init.d/30_console_switch", ROOT / "qemu/desktop-console-switch")
        run(tool, "put-exec", live, "/usr/lib/drivers/usbhidd", driver_components / "usbhidd")
        driver_readback = subprocess.check_output([str(tool), "cat", str(live), "/usr/lib/drivers/usbhidd"])
        if hashlib.sha256(driver_readback).hexdigest() != sha256(driver_components / "usbhidd"):
            raise RuntimeError("USB HID driver readback mismatch")
    run(tool, "put", live, "/usr/lib/libc.so", libc_components / "libc.so")
    run(tool, "put-exec", live, "/usr/bin/signal-registers", libc_components / "signal-registers")
    run(tool, "put-exec", live, "/usr/bin/tls-isolation", libc_components / "tls-isolation")
    run(tool, "put", live, "/usr/lib/tls-module.so", libc_components / "tls-module.so")
    for guest, host in (("/usr/lib/libc.so", "libc.so"), ("/usr/bin/signal-registers", "signal-registers"),
                        ("/usr/bin/tls-isolation", "tls-isolation"), ("/usr/lib/tls-module.so", "tls-module.so")):
        actual = subprocess.check_output([str(tool), "cat", str(live), guest])
        if hashlib.sha256(actual).hexdigest() != sha256(libc_components / host):
            raise RuntimeError(f"Readback mismatch: {guest}")
    readback = subprocess.check_output([str(tool), "cat", str(live), "/usr/lib/boot/kernel"])
    kernel_sha256 = hashlib.sha256(readback).hexdigest()
    if args.kernel == "local" and kernel_sha256 != sha256(components / "kernel"):
        raise RuntimeError("Kernel image readback mismatch")

    image = stage / "esp.img"
    with image.open("xb") as stream:
        stream.truncate(768 * 1024 * 1024)
    run("mformat", "-i", image, "-F", "::")
    run("mmd", "-i", image, "::/EFI", "::/EFI/BOOT")
    run("mcopy", "-i", image, components / "bootloader-live.efi", "::/EFI/BOOT/BOOTAA64.EFI")
    run("mcopy", "-i", image, live, "::/redox-live.iso")
    metadata = {
        "image": str(image), "image_sha256": sha256(image), "sources": manifest,
        "profile": args.profile, "userspace_source": source,
        "components": str(components), "kernel_sha256": kernel_sha256,
        "kernel_origin": args.kernel,
        "kernel_source_rev": manifest["repositories"]["kernel"]["rev"] if args.kernel == "local" else None,
        "kernel_patch": manifest["repositories"]["kernel"].get("patch") if args.kernel == "local" else None,
        "bootloader_origin": "local",
        "relibc_components": str(libc_components), "relibc_source": libc_spec,
        "relibc_sha256": sha256(libc_components / "libc.so"),
        "signal_test_sha256": sha256(libc_components / "signal-registers"),
        "tls_test_sha256": sha256(libc_components / "tls-isolation"),
        "tls_module_sha256": sha256(libc_components / "tls-module.so"),
        "bootloader_sha256": sha256(components / "bootloader-live.efi"),
        "live_image_sha256": sha256(live),
        "smoke_script_sha256": sha256(ROOT / "qemu/userspace-smoke.sh"),
        "desktop_console_switch_sha256": sha256(ROOT / "qemu/desktop-console-switch") if args.profile == "desktop" else None,
        "usbhidd_components": str(driver_components) if driver_components else None,
        "usbhidd_sha256": sha256(driver_components / "usbhidd") if driver_components else None,
        "usb_layout": "separate-xhci-controllers" if driver_components else None,
        "kernel_readback_verified": True, "hardware_validated": False,
    }
    encoded = json.dumps(metadata, indent=2) + "\n"
    (stage / "image.json").write_text(encoded)
    (work / ("desktop-image.json" if args.profile == "desktop" else "image.json")).write_text(encoded)
    print(f"Prepared QEMU image: {image}")
    print("Run: python3 scripts/run-qemu-arm64.py " + ("--desktop" if args.profile == "desktop" else "--verify"))


if __name__ == "__main__":
    main()
