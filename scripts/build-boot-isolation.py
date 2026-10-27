#!/usr/bin/env python3
"""Build standalone EFI/host probes using the bootloader's exact dependency lock."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare import ROOT
from source_patches import ensure_patch
from sd_image import sha256, write_json


def profile(mode, ident, log=False, preload=False, records=1):
    return (f"ISOLATE1\nid={ident}\nmode={mode}\nfat_log={'on' if log else 'off'}\n"
            f"preload={'live' if preload else 'none'}\nrecords={records}\n")


def main():
    lock = json.loads((ROOT / "sources.lock.json").read_text())
    tree = ROOT / "upstream/bootloader"
    ensure_patch(tree, lock["repositories"]["bootloader"])
    revision = subprocess.check_output(["git", "-C", str(tree), "rev-parse", "HEAD"], text=True).strip()
    if revision != lock["repositories"]["bootloader"]["rev"]:
        raise ValueError("Unexpected bootloader revision")
    parent = ROOT / "artifacts/boot-isolation-build"
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=parent))
    print(f"Evidence: {output}", flush=True)
    manifest = {"status": "running", "purpose": "isolation probes, not a hardware fix or SD release",
                "hardware_validated": False, "revision": revision, "toolchain": lock["rust_toolchain"]}
    environment = os.environ.copy()
    environment.update(CARGO_TARGET_DIR=str(ROOT / "work/boot-isolation/target"),
                       RUSTFLAGS="--cfg aes_force_soft -Zunstable-options", CARGO_NET_GIT_FETCH_WITH_CLI="true")
    commands = [
        ["cargo", "+" + lock["rust_toolchain"], "rustc", "--locked", "--release", "--features", "isolation-probes", "--bin", "boot-isolation",
         "--target", "aarch64-unknown-uefi", "-Z", "build-std=core,alloc", "-Z", "build-std-features=compiler-builtins-mem",
         "--", "-C", "debuginfo=2", "--emit", f"link={output / 'boot-isolation.efi'},asm={output / 'boot-isolation.s'}"],
        ["cargo", "+" + lock["rust_toolchain"], "build", "--locked", "--release", "--features", "isolation-probes", "--bin", "boot-isolation-host"],
    ]
    write_json(output / "commands.json", commands)
    try:
        with (output / "build.log").open("w") as log:
            for command in commands:
                subprocess.run(command, cwd=tree, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
        shutil.copy2(ROOT / "work/boot-isolation/target/release/boot-isolation-host", output / "boot-isolation-host")
        for name, source in {"sources.lock.json": ROOT / "sources.lock.json", "Cargo.lock": tree / "Cargo.lock",
                             "bootloader.patch": ROOT / lock["repositories"]["bootloader"]["patch"]["path"]}.items():
            shutil.copy2(source, output / name)
        shutil.copytree(tree / "src/isolation", output / "source/isolation")
        for name in ("boot_log.rs", "boot_log_buffer.rs", "live_read.rs", "fs_trace.rs"):
            shutil.copy2(tree / "src" / name, output / "source" / name)
        shutil.copy2(__file__, output / Path(__file__).name)
        (output / "toolchain.txt").write_bytes(subprocess.check_output(["rustup", "run", lock["rust_toolchain"], "rustc", "-vV"]))
        profiles = output / "profiles"
        profiles.mkdir()
        for name, text in {
            "ram-off.cfg": profile("ramfs", "ram-ab", preload=True),
            "ram-on.cfg": profile("ramfs", "ram-ab", log=True, preload=True),
            "fat-small-write.cfg": profile("fat-write", "fat-small", records=16),
            "fat-small-read.cfg": profile("fat-read", "fat-small", records=16),
            "fat-loaded-write.cfg": profile("fat-write", "fat-loaded", preload=True, records=300),
            "fat-loaded-read.cfg": profile("fat-read", "fat-loaded", preload=True, records=300),
        }.items():
            (profiles / name).write_text(text)
        manifest.update(status="passed", efi=str(output / "boot-isolation.efi"), host=str(output / "boot-isolation-host"),
                        files={str(p.relative_to(output)): {"sha256": sha256(p), "size": p.stat().st_size}
                               for p in output.rglob("*") if p.is_file()})
    except Exception as error:
        manifest.update(status="failed", error=repr(error))
        raise
    finally:
        write_json(output / "manifest.json", manifest)


if __name__ == "__main__":
    main()
