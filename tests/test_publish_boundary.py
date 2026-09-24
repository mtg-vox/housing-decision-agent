"""Publication boundary tests use miniature fictional temporary repositories."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def boundary():
    spec = importlib.util.find_spec("publish_boundary")
    assert spec is not None, "the exact-manifest boundary must exist"
    import publish_boundary
    return publish_boundary


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def sample_repo(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    (root / "PUBLISH_MANIFEST.txt").write_text("PUBLISH_MANIFEST.txt\npublic.odd\n")
    (root / "public.odd").write_text("fictional public example\n")
    (root / "old-personal.txt").write_text("synthetic excluded material\n")
    git(root, "add", ".")
    return root


@pytest.mark.parametrize("entry", ["../escape", "/absolute", "./public.odd", "dir/", "dir/*", "a/../public.odd", ".git/config", "profile.local/data", "backups/file", "a\\b", "PUBLISH_MANIFEST.txt", "data/candidates.json", "rules/policy.md", "config/paths.yaml",
                                  "research/notes.md", ".env.local", "AGENTS.md"])
def test_manifest_rejects_unsafe_or_duplicate_paths(tmp_path, entry):
    root = sample_repo(tmp_path)
    (root / "PUBLISH_MANIFEST.txt").write_text("PUBLISH_MANIFEST.txt\n" + entry + "\n")
    with pytest.raises(ValueError):
        boundary().manifest_paths(root)


@pytest.mark.parametrize("case", ["symlink", "parent_symlink", "untracked", "missing", "directory"])
def test_selected_files_must_be_regular_tracked_files(tmp_path, case):
    root = sample_repo(tmp_path)
    target = root / "public.odd"
    target.unlink()
    if case == "symlink":
        target.symlink_to(root / "old-personal.txt")
    elif case == "parent_symlink":
        (root / "linked").symlink_to(root, target_is_directory=True)
        (root / "PUBLISH_MANIFEST.txt").write_text("PUBLISH_MANIFEST.txt\nlinked/old-personal.txt\n")
    elif case == "untracked":
        git(root, "rm", "--cached", "public.odd")
        target.write_text("safe")
    elif case == "directory":
        target.mkdir()
    with pytest.raises(ValueError):
        boundary().selected_files(root)


def test_manifest_must_include_itself(tmp_path):
    root = sample_repo(tmp_path)
    (root / "PUBLISH_MANIFEST.txt").write_text("public.odd\n")
    with pytest.raises(ValueError):
        boundary().manifest_paths(root)


def test_public_checkout_rejects_unexpected_tracked_file(tmp_path):
    root = sample_repo(tmp_path)
    assert hasattr(boundary(), "check_public_tree"), "strict public checkout gate required"
    with pytest.raises(ValueError, match="unexpected"):
        boundary().check_public_tree(root)
    git(root, "rm", "-f", "old-personal.txt")
    boundary().check_public_tree(root)
    (root / "new.unknown-extension").write_text("fictional addition")
    git(root, "add", "new.unknown-extension")
    with pytest.raises(ValueError, match="unexpected"):
        boundary().check_public_tree(root)


def test_default_scanner_uses_only_manifest(tmp_path, monkeypatch):
    from tests.test_privacy_scan import privacy_scan
    root = sample_repo(tmp_path)
    monkeypatch.setattr(privacy_scan, "REPO_ROOT", root)
    assert privacy_scan.default_roots() == [root / "PUBLISH_MANIFEST.txt", root / "public.odd"]


def test_manifest_is_exact_source_of_truth(tmp_path):
    root = sample_repo(tmp_path)
    assert boundary().manifest_paths(root) == ["PUBLISH_MANIFEST.txt", "public.odd"]
    assert boundary().selected_files(root) == [root / "PUBLISH_MANIFEST.txt", root / "public.odd"]
