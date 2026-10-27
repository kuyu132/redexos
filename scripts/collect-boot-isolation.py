#!/usr/bin/env python3
"""Read an isolation data file from a mounted FAT or captured FAT image; never write source."""
import argparse
import hashlib
import re
import runpy
import shutil
from pathlib import Path

from prepare import ROOT
from sd_image import copy_out, sha256, write_json

expected_bytes = runpy.run_path(str(ROOT / "scripts/verify-boot-isolation.py"))["expected_bytes"]


def collect(source, output, ident, records, offset=0, volume=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not re.fullmatch(r"[A-Za-z0-9-]{1,24}", ident) or not 1 <= records <= 512:
        raise ValueError("Invalid experiment ID or record count")
    if source.is_dir() != volume or (not volume and not source.is_file()):
        raise ValueError("Use a mounted directory or a captured image file, not a raw device")
    if output == source or (volume and output.is_relative_to(source)):
        raise ValueError("Evidence output must be outside source")
    output.mkdir(parents=True, exist_ok=False)
    result = {"status": "running", "id": ident, "records": records,
              "scope": "offline byte readback only; does not prove a hardware reboot or kernel boot",
              "source": str(source), "source_modified": False}
    try:
        expected = expected_bytes(ident, records * 2048)
        result["expected_sha256"] = hashlib.sha256(expected).hexdigest()
        guest = f"redexos/isolation/{ident}.BIN"
        saved = output / (ident + ".BIN")
        if volume:
            shutil.copy2(source / guest, saved)
        else:
            copy_out(source, offset, "/" + guest, saved)
        result.update(actual_sha256=sha256(saved), actual_bytes=saved.stat().st_size,
                      status="passed" if saved.read_bytes() == expected else "failed")
    except Exception as error:
        result.update(status="failed", error=repr(error))
        raise
    finally:
        write_json(output / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path)
    source.add_argument("--volume", type=Path)
    parser.add_argument("--offset", type=int, default=0, help="0 for the captured FAT partition; 1048576 for current whole SD images")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--id", required=True)
    parser.add_argument("--records", required=True, type=int)
    args = parser.parse_args()
    result = collect(args.image or args.volume, args.output, args.id, args.records, args.offset, bool(args.volume))
    print(f"{result['status']}: {args.output / 'result.json'}")
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
