"""Apply the exact locked diff without overwriting unrelated source edits."""
import subprocess

from prepare import ROOT, sha256


def expected_patch(source):
    spec = source.get("patch")
    if spec is None:
        return b""
    path = (ROOT / spec["path"]).resolve()
    if not path.is_relative_to(ROOT.resolve()) or sha256(path) != spec["sha256"]:
        raise ValueError("Locked source patch path/checksum mismatch")
    return path.read_bytes()


def source_diff(tree):
    return subprocess.check_output([
        "git", "-C", str(tree), "diff", "--no-ext-diff", "--no-textconv",
        "--no-color", "--binary", "HEAD",
    ])


def ensure_patch(tree, source):
    expected = expected_patch(source)
    actual = source_diff(tree)
    untracked = subprocess.check_output([
        "git", "-C", str(tree), "ls-files", "--others", "--exclude-standard",
    ])
    if untracked:
        raise ValueError(f"Unrecorded source files in {tree}; preserve/review them first")
    if actual == expected:
        return
    if actual:
        raise ValueError(f"Refusing to overwrite source edits in {tree}")
    subprocess.run(["git", "-C", str(tree), "apply", "--check", "-"],
                   input=expected, check=True)
    subprocess.run(["git", "-C", str(tree), "apply", "-"], input=expected, check=True)
    # No untracked paths existed before applying the patch. Include its new
    # files in the canonical diff without staging their contents.
    added = subprocess.check_output([
        "git", "-C", str(tree), "ls-files", "--others", "--exclude-standard", "-z",
    ])
    if added:
        subprocess.run(["git", "-C", str(tree), "add", "--intent-to-add", "--",
                        *[path.decode() for path in added.split(b"\0") if path]], check=True)
    if source_diff(tree) != expected:
        raise ValueError(f"Applied patch differs from canonical locked diff: {tree}")
