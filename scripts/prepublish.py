#!/usr/bin/env python3
"""Build and verify a fresh, history-free, manifest-exact public export. Never publish."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_boundary import manifest_paths, regular_file, selected_files
import privacy_scan


def checksum_text(root):
    return "".join(f"{hashlib.sha256(regular_file(root, name).read_bytes()).hexdigest()}  {name}\n"
                   for name in manifest_paths(root))


def verify_export(root, checksums):
    root = Path(root)
    names = manifest_paths(root)
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("symlink in export")
        if not path.is_dir():
            actual.add(path.relative_to(root).as_posix())
        elif path.relative_to(root).as_posix() not in {
            p.as_posix() for n in names for p in Path(n).parents if p != Path(".")
        }:
            raise ValueError("unexpected directory in export")
    if actual != set(names):
        raise ValueError("export file inventory mismatch")
    supplied = Path(checksums).read_text(encoding="utf-8")
    if supplied != checksum_text(root):
        raise ValueError("SHA256 manifest mismatch")


def run_gitleaks(root, *, required=False):
    executable = shutil.which("gitleaks")
    if not executable:
        if required:
            raise ValueError("gitleaks required but unavailable")
        return "not-run (not installed; not a publication approval)"
    # Do not honor repo configuration, ignore files, or inline allow markers.
    with tempfile.TemporaryDirectory(prefix="housing-gitleaks-") as tmp:
        config = Path(tmp) / "config.toml"
        config.write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
        ignore = Path(tmp) / "empty-ignore"
        ignore.write_text("", encoding="utf-8")
        result = subprocess.run(
            [executable, "dir", str(root), "--config", str(config), "--gitleaks-ignore-path", str(ignore),
             "--ignore-gitleaks-allow", "--redact", "--no-banner", "--log-level", "error",
             "--exit-code", "1", "--max-target-megabytes", "10"],
            capture_output=True, timeout=180, cwd=tmp,
        )
        if result.returncode:
            # No matched content is printed; gitleaks can include source lines even with redaction.
            raise ValueError("gitleaks failed or found secrets (output withheld)")
    return "passed"


def build_export(source, destination, *, require_secrets=False):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    files = selected_files(source)
    if destination == source or source in destination.parents:
        raise ValueError("export must be outside the source checkout")
    if destination.is_symlink() or any(p.is_symlink() for p in destination.parents):
        raise ValueError("symlink destination forbidden")
    checksums = destination.with_name(destination.name + ".sha256")
    if destination.exists() or checksums.exists() or checksums.is_symlink():
        raise ValueError("destination and checksum sidecar must be NEW")
    destination.mkdir(mode=0o700)  # Parent must already exist. Never merge with old files.
    created_checksums = False
    try:
        expected = {}
        for path in files:
            relative = path.relative_to(source)
            # Recheck source immediately before reading; never traverse a symlink intentionally.
            path = regular_file(source, relative.as_posix())
            if path.stat().st_size > privacy_scan.MAX_BYTES:
                raise ValueError("oversize source file")
            data = path.read_bytes()
            if len(data) > privacy_scan.MAX_BYTES:
                raise ValueError("oversize source file")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            target.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
            expected[relative.as_posix()] = hashlib.sha256(data).hexdigest()
        # Export manifest must match the exact source inventory selected before copying.
        if set(manifest_paths(destination)) != set(expected):
            raise ValueError("source manifest changed during export")
        _require_only(destination, expected)
        findings = privacy_scan.scan([destination / n for n in sorted(expected)],
                                     privacy_scan.load_patterns(), base_root=destination)
        if findings:
            raise ValueError("privacy scan failed (matched content withheld)")
        secrets = run_gitleaks(destination, required=require_secrets)
        with checksums.open("x", encoding="utf-8") as stream:
            created_checksums = True
            stream.write("".join(f"{expected[n]}  {n}\n" for n in sorted(expected)))
        verify_export(destination, checksums)
        _require_only(destination, expected)
        return {"export": str(destination), "checksums": str(checksums), "files": len(expected),
                "privacy": "passed", "local_denylist": bool(os.environ.get(privacy_scan.ENV_VAR)),
                "gitleaks": secrets}
    except BaseException:
        shutil.rmtree(destination)
        if created_checksums:
            checksums.unlink(missing_ok=True)
        raise


def _require_only(destination: Path, expected) -> None:
    """Fail if anything besides the manifest files exists (e.g. __pycache__ written mid-run)."""
    present = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if not p.is_dir()}
    if present != set(expected):
        raise ValueError("unexpected files in export")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="NEW export directory outside source checkout")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--require-secrets", action="store_true", help="fail if gitleaks is unavailable")
    parser.add_argument("--verify", type=Path, metavar="CHECKSUMS", help="verify an existing export instead")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            verify_export(args.destination, args.verify)
            print("Export inventory and SHA256 checks passed.")
        else:
            print(json.dumps(build_export(args.source, args.destination, require_secrets=args.require_secrets), indent=2))
    except (ValueError, OSError, UnicodeError, re.error, subprocess.SubprocessError):
        # Environment paths and denylist regex errors may contain private text.
        print("Prepublish gate failed; no publication is approved. Check inventory, scanner dependencies and local policy.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
