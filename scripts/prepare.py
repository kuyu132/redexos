#!/usr/bin/env python3
"""Create isolated, pinned build checkouts; never reset an existing checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BOARDS = ("pi4b", "pi5")
GENERATED_STATE = ".redexos-port-generated.json"


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_sha256(content):
    return hashlib.sha256(content.encode()).hexdigest()


def lock():
    data = json.loads((ROOT / "sources.lock.json").read_text())
    for item in data["repositories"].values():
        if not re.fullmatch(r"[0-9a-f]{40}", item["rev"]):
            raise ValueError("Source revisions must be full commit hashes")
    return data


def checkout(path, source):
    if path.exists():
        if not (path / ".git").exists():
            raise ValueError(f"Refusing existing non-repository directory: {path}")
        if git(path, "rev-parse", "HEAD") != source["rev"]:
            raise ValueError(f"Revision differs: {path}; preserve your work in a separate directory")
        return
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", source["url"]], check=True)
    subprocess.run(["git", "-C", str(path), "fetch", "--depth=1", "origin", source["rev"]], check=True)
    subprocess.run(["git", "-C", str(path), "checkout", "--detach", source["rev"]], check=True)


def recipe_with_pin(original, source):
    # Modify only [source]; preserve dependency/build sections exactly.
    match = re.search(r"(?m)^\[source\]\s*\n", original)
    if match is None:
        raise ValueError("Recipe has no [source]")
    next_section = re.search(r"(?m)^\[", original[match.end():])
    end = match.end() + next_section.start() if next_section else len(original)
    section = original[match.end():end]
    section, count = re.subn(r'(?m)^git\s*=.*$', 'git = ' + json.dumps(source["url"]), section)
    if count != 1:
        raise ValueError("Expected one git source in recipe")
    section = re.sub(r"(?m)^(rev|branch)\s*=.*\n?", "", section)
    section = 'rev = ' + json.dumps(source["rev"]) + "\n" + section
    patch = source.get("patch")
    if patch is not None:
        if re.search(r"(?m)^patches\s*=", section):
            raise ValueError("Core recipe already defines patches; merge must be reviewed")
        section = 'patches = ["redexos.patch"]\n' + section
    return original[:match.end()] + section + original[end:]


def locked_patch(source):
    spec = source.get("patch")
    if spec is None:
        return None
    path = (ROOT / spec["path"]).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError("Source patch escapes the project root")
    if not path.is_file() or sha256(path) != spec["sha256"]:
        raise ValueError(f"Source patch checksum mismatch: {spec['path']}")
    return path.read_text()


def generated_files(board, tree, manifest):
    result = {
        "config/aarch64/pi-port-common.toml": (ROOT / "config/common/minimal.toml").read_text(),
        f"config/aarch64/{board}/minimal.toml": (ROOT / f"config/{board}/minimal.toml").read_text(),
    }
    for name, source in manifest["repositories"].items():
        if name == "redox":
            continue
        relative = f"recipes/core/{name}/recipe.toml"
        original = git(tree, "show", f'{manifest["repositories"]["redox"]["rev"]}:{relative}') + "\n"
        result[relative] = recipe_with_pin(original, source)
        patch = locked_patch(source)
        if patch is not None:
            result[f"recipes/core/{name}/redexos.patch"] = patch
    return result


def prepare(board):
    manifest = lock()
    tree = ROOT / "work" / board / "redox"
    checkout(tree, manifest["repositories"]["redox"])
    desired = generated_files(board, tree, manifest)
    state_path = tree / GENERATED_STATE
    previous = json.loads(state_path.read_text()).get("files", {}) if state_path.exists() else {}
    # Preflight ALL files before writing. Generated files can be reused, never overwritten.
    for relative, content in desired.items():
        path = tree / relative
        if not path.exists() or path.read_text() == content:
            continue
        if previous.get(relative) == sha256(path):
            continue
        original = git(tree, "show", f"HEAD:{relative}") + "\n" if relative.startswith("recipes/") else None
        if original is None or path.read_text() != original:
            raise ValueError(f"Refusing to overwrite local changes: {path}")
    for relative, content in desired.items():
        path = tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    state_path.write_text(json.dumps({
        "schema": 1,
        "board": board,
        "files": {relative: content_sha256(content) for relative, content in sorted(desired.items())},
    }, indent=2) + "\n")
    verify(board)
    print(f"Prepared {board}: {tree}")


def verify(board):
    manifest = lock()
    tree = ROOT / "work" / board / "redox"
    expected = manifest["repositories"]["redox"]["rev"]
    if git(tree, "rev-parse", "HEAD") != expected:
        raise ValueError(f"{board}: build-system HEAD does not match lock")
    generated = generated_files(board, tree, manifest)
    for relative, content in generated.items():
        if (tree / relative).read_text() != content:
            raise ValueError(f"{board}: configuration drift: {relative}")
    state_path = tree / GENERATED_STATE
    if not state_path.is_file():
        raise ValueError(f"{board}: generated-file state is missing")
    state = json.loads(state_path.read_text())
    expected_state = {relative: content_sha256(content) for relative, content in sorted(generated.items())}
    if state.get("schema") != 1 or state.get("board") != board or state.get("files") != expected_state:
        raise ValueError(f"{board}: generated-file state drift")
    changed = set(git(tree, "diff", "HEAD", "--name-only").splitlines())
    if changed - set(generated):
        raise ValueError(f"{board}: unrecorded tracked changes: {sorted(changed - set(generated))}")
    if (tree / ".config").exists():
        raise ValueError("Remove or review the local .config; this baseline uses explicit build arguments")
    print(f"Verified {board}: core source pins and build configuration (not hardware)")


def snapshot(board, output):
    tree = ROOT / "work" / board / "redox"
    sources = {}
    for path in sorted((tree / "recipes").glob("**/source/.git")):
        source = path.parent
        sources[str(source.relative_to(tree))] = {
            "head": git(source, "rev-parse", "HEAD"),
            "dirty": bool(git(source, "status", "--porcelain")),
        }
    files = {}
    for name in ("cookbook.lock", "cookbook.toml", "Cargo.lock", "rust-toolchain.toml"):
        path = tree / name
        if path.exists():
            files[name] = path.read_text()
    configs = {name: (tree / name).read_text()
               for name in generated_files(board, tree, lock())}
    provenance = {str(path.relative_to(ROOT)): sha256(path)
                  for folder in ("scripts", "boards", "config", "board-config")
                  for path in sorted((ROOT / folder).rglob("*"))
                  if path.is_file() and "__pycache__" not in path.parts}
    for name in ("sources.lock.json", "board-firmware.lock.json"):
        path = ROOT / name
        if path.is_file():
            provenance[name] = sha256(path)
    record = {"board": board, "source_lock": lock(), "sources": sources, "lock_files": files,
              "generated_configuration": configs, "project_input_sha256": provenance,
              "build_diff": git(tree, "diff", "HEAD"), "hardware_validated": False}
    output.write_text(json.dumps(record, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "snapshot"))
    parser.add_argument("board", choices=BOARDS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "snapshot":
        if args.output is None:
            parser.error("snapshot requires --output")
        snapshot(args.board, args.output)
    else:
        globals()[args.action](args.board)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"ERROR: {error}")
