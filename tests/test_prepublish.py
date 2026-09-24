"""No network or real personal data in export tests."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_publish_boundary import sample_repo, ROOT


def exporter():
    sys.path.insert(0, str(ROOT / "scripts"))
    assert importlib.util.find_spec("prepublish") is not None, "clean exporter required"
    import prepublish
    return prepublish


def test_export_copies_only_exact_public_files_and_checks_hashes(tmp_path):
    root = sample_repo(tmp_path)
    destination = tmp_path / "export"
    result = exporter().build_export(root, destination)
    assert sorted(p.name for p in destination.iterdir()) == ["PUBLISH_MANIFEST.txt", "public.odd"]
    assert not (destination / ".git").exists()
    sums = Path(result["checksums"])
    assert sums.parent == destination.parent
    subprocess.run(["sha256sum", "--check", str(sums)], cwd=destination, check=True, capture_output=True)
    exporter().verify_export(destination, sums)


def test_gitleaks_failure_removes_export_and_uses_unsuppressible_flags(tmp_path, monkeypatch):
    root = sample_repo(tmp_path)
    module = exporter()
    # Dependency injection tests the invocation contract, not a claim of a real scan.
    monkeypatch.setattr(module.shutil, "which", lambda name: "/synthetic/gitleaks")
    calls = []
    original = module.subprocess.run
    def run(args, **kwargs):
        if args[0] != "/synthetic/gitleaks":
            return original(args, **kwargs)
        calls.append(args)
        return subprocess.CompletedProcess(args, 1)
    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(ValueError, match="gitleaks"):
        module.build_export(root, tmp_path / "export", require_secrets=True)
    assert not (tmp_path / "export").exists()
    assert calls and "--ignore-gitleaks-allow" in calls[0]
    assert "--config" in calls[0] and "--gitleaks-ignore-path" in calls[0]


def test_real_gitleaks_rejects_synthetic_secret_even_with_allow_marker(tmp_path):
    import shutil
    if not shutil.which("gitleaks"):
        pytest.skip("install pinned gitleaks to exercise the real scanner")
    f = tmp_path / "arbitrary.new"
    f.write_text("clean synthetic data")
    assert exporter().run_gitleaks(tmp_path, required=True) == "passed"
    # Deliberately invented, nonfunctional high-entropy-looking token, assembled in memory.
    token = "gh" + "p_" + "aB7cD9eF2gH4iJ6kL8mN0pQ3rS5tU1vW9xY2"
    f.write_text(token + " # gitleaks:allow")
    with pytest.raises(ValueError, match="gitleaks"):
        exporter().run_gitleaks(tmp_path, required=True)


def test_stray_file_in_export_fails_closed(tmp_path, monkeypatch):
    """A file written mid-run (e.g. __pycache__) must abort the export and leave nothing."""
    root = sample_repo(tmp_path)
    module = exporter()
    original = module.privacy_scan.scan

    def scan_and_drop_stray(paths, patterns, *, base_root=None):
        (Path(base_root) / "__pycache__").mkdir(exist_ok=True)
        (Path(base_root) / "__pycache__" / "stray.pyc").write_bytes(b"\x00stray")
        return original(paths, patterns, base_root=base_root)

    monkeypatch.setattr(module.privacy_scan, "scan", scan_and_drop_stray)
    with pytest.raises(ValueError, match="unexpected (files|directory)"):
        module.build_export(root, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_export_refuses_existing_destination(tmp_path):
    root = sample_repo(tmp_path)
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "keep").write_text("untouched")
    with pytest.raises((ValueError, FileExistsError)):
        exporter().build_export(root, destination)
    assert (destination / "keep").read_text() == "untouched"


def test_export_fails_closed_on_private_data_and_leaves_no_artifact(tmp_path, monkeypatch):
    root = sample_repo(tmp_path)
    deny = tmp_path / "synthetic-deny.txt"
    deny.write_text("fabricated-needle")
    monkeypatch.setenv("HOUSING_PRIVACY_DENYLIST", str(deny))
    (root / "public.odd").write_text("fabricated-needle # privacy-scan: allow")
    with pytest.raises(ValueError, match="privacy"):
        exporter().build_export(root, tmp_path / "export")
    assert not (tmp_path / "export").exists()
    assert not (tmp_path / "export.sha256").exists()


def test_secret_scanner_requirement_cannot_be_silently_skipped(tmp_path, monkeypatch):
    root = sample_repo(tmp_path)
    module = exporter()
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="gitleaks"):
        module.build_export(root, tmp_path / "export", require_secrets=True)
    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize("mutation", ["content", "extra", "symlink", "checksum"])
def test_checksum_verification_rejects_tampering(tmp_path, mutation):
    root = sample_repo(tmp_path)
    destination = tmp_path / "export"
    result = exporter().build_export(root, destination)
    sums = Path(result["checksums"])
    if mutation == "content":
        (destination / "public.odd").write_text("changed")
    elif mutation == "extra":
        (destination / "unexpected.any").write_text("new")
    elif mutation == "symlink":
        (destination / "public.odd").unlink()
        (destination / "public.odd").symlink_to(root / "public.odd")
    else:
        sums.write_text("0" * 64 + "  public.odd\n")
    with pytest.raises(ValueError):
        exporter().verify_export(destination, sums)
