"""Privacy guard: public files must not leak personal data or secrets."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("privacy_scan", ROOT / "scripts" / "privacy_scan.py")
privacy_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(privacy_scan)

# Fake leak samples are assembled at runtime so this file itself stays clean.
HOME = "/" + "home/someone/x"
MAIL = "a.person" + "@" + "gmail.com"
PHONE = "555-123" + "-4567"
GH = "gh" + "p_" + "A" * 36
ADDR = "742 Evergreen " + "Terrace Ave"
LISTING = "https://www." + "zillow.com/homedetails/123_zpid"
MONEY = "$" + "1,234"


def names_for(tmp_path, text):
    f = tmp_path / "sample.md"
    f.write_text(text, encoding="utf-8")
    return {n for _, _, n in privacy_scan.scan([f], privacy_scan.load_patterns({}))}


def test_builtin_patterns_catch_generic_leaks(tmp_path):
    names = names_for(tmp_path, "\n".join([HOME, MAIL, PHONE, GH, ADDR, LISTING, MONEY]))
    assert {"home_path_linux", "email", "phone", "secret_github", "street_address",
            "listing_url", "money_figure"} <= names


@pytest.mark.parametrize("address", [
    "7 fictional " + "terrace", "42 NW 9th " + "Street", "123 Synthetic " + "Pkwy",
    "9 Imaginary " + "Circle", "88 Sample " + "Highway", "Apartment " + "4B", "Unit " + "12",
])
def test_street_and_unit_variants(tmp_path, address):
    assert names_for(tmp_path, address) & {"street_address", "unit_address"}


def test_clean_text_passes(tmp_path):
    ok = "ok@example.com 1@users.noreply.github.com lat 40.0 -75.0 100 Example Ave, Harborview 2200\n"
    assert names_for(tmp_path, ok) == set()


def test_inline_marker_cannot_suppress_secrets_or_denylist(tmp_path):
    f = tmp_path / "production.anything"
    f.write_text(GH + " sample-forbidden-value # privacy-scan: allow\n")
    deny = tmp_path / "deny.txt"
    deny.write_text("sample-forbidden-value\n")
    pats = privacy_scan.load_patterns({privacy_scan.ENV_VAR: str(deny)})
    names = {n for _, _, n in privacy_scan.scan([f], pats)}
    assert {"secret_github", "denylist:1"} <= names


def test_binary_file_is_rejected(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00\x01" + GH.encode() + b"\x00")
    assert (str(f), 0, "scan_error:unsupported_binary") in privacy_scan.scan([f], privacy_scan.load_patterns({}))


@pytest.mark.parametrize("case", ["missing", "oversize", "unreadable", "symlink", "invalid_utf8"])
def test_unscannable_inputs_fail_closed(tmp_path, monkeypatch, case):
    f = tmp_path / "unusual.payload"
    f.write_text("safe")
    if case == "missing":
        f.unlink()
    elif case == "oversize":
        monkeypatch.setattr(privacy_scan, "MAX_BYTES", 2)
    elif case == "unreadable":
        f.chmod(0)
    elif case == "symlink":
        f.unlink()
        f.symlink_to(tmp_path / "absent")
    else:
        f.write_bytes(bytes([255]))
    assert privacy_scan.scan([f], privacy_scan.load_patterns({})), case


def test_denylist_file_is_applied(tmp_path):
    deny = tmp_path / "deny.txt"
    deny.write_text("# comment\nsecretword\n", encoding="utf-8")
    f = tmp_path / "f.txt"
    f.write_text("has SecretWord inside\n", encoding="utf-8")
    pats = privacy_scan.load_patterns({privacy_scan.ENV_VAR: str(deny)})
    assert privacy_scan.scan([f], pats) == [(str(f), 1, "denylist:2")]


def test_vetted_binary_requires_exact_path_hash_and_still_scans_secrets(tmp_path, monkeypatch):
    import hashlib
    f = tmp_path / "app" / "static" / "vendor" / "sample.woff2"
    f.parent.mkdir(parents=True)
    data = b"\x00\xffsynthetic font"
    f.write_bytes(data)
    relative = f.relative_to(tmp_path).as_posix()
    assert hasattr(privacy_scan, "TRUSTED_BINARY_ASSETS")
    monkeypatch.setattr(privacy_scan, "TRUSTED_BINARY_ASSETS", {relative: {
        "sha256": hashlib.sha256(data).hexdigest(), "reason": "Synthetic test asset; not a production approval"
    }})
    pats = privacy_scan.load_patterns({})
    assert privacy_scan.scan([f], pats, base_root=tmp_path) == []
    f.write_bytes(data + b"changed")
    assert privacy_scan.scan([f], pats, base_root=tmp_path)
    data += GH.encode()
    f.write_bytes(data)
    monkeypatch.setattr(privacy_scan, "TRUSTED_BINARY_ASSETS", {relative: {
        "sha256": hashlib.sha256(data).hexdigest(), "reason": "Test secret remains detectable"
    }})
    assert "secret_github" in {name for _, _, name in privacy_scan.scan([f], pats, base_root=tmp_path)}


def test_only_exact_reviewed_money_line_can_be_excepted(tmp_path, monkeypatch):
    import hashlib
    f = tmp_path / "doc.md"
    f.write_text(MONEY)
    key = ("doc.md", "money_figure", hashlib.sha256(MONEY.encode()).hexdigest())
    assert hasattr(privacy_scan, "EXCEPTIONS"), "hash-bound exceptions required"
    monkeypatch.setattr(privacy_scan, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(privacy_scan, "EXCEPTIONS", {key: "Reviewed fictional budget"})
    assert privacy_scan.scan([f], privacy_scan.load_patterns({})) == []
    f.write_text(MONEY + " changed")
    assert privacy_scan.scan([f], privacy_scan.load_patterns({}))
    f.write_text(MONEY)
    other = tmp_path / "other.md"
    other.write_text(MONEY)
    assert privacy_scan.scan([other], privacy_scan.load_patterns({}))
    for pattern, text in [("secret_github", GH), ("denylist:1", "fabricated-phrase")]:
        f.write_text(text)
        monkeypatch.setattr(privacy_scan, "EXCEPTIONS", {("doc.md", pattern, hashlib.sha256(text.encode()).hexdigest()): "not allowed"})
        import re
        assert privacy_scan.scan([f], {pattern: re.compile(re.escape(text))})


def test_author_credit_waives_only_exact_lines_and_only_denylist(tmp_path, monkeypatch):
    import hashlib, re
    f = tmp_path / "LICENSE"
    credit = "Copyright (c) 2026 Pat Example"
    f.write_text(credit + "\nPat Example also lives at the same place\n")
    monkeypatch.setattr(privacy_scan, "AUTHOR_CREDIT_LINES",
                        {("LICENSE", hashlib.sha256(credit.encode()).hexdigest())})
    deny = {"denylist:1": re.compile("pat example", re.I)}
    # exact credit line passes; the same name on any other line is still caught
    assert privacy_scan.scan([f], deny, base_root=tmp_path) == [(str(f), 2, "denylist:1")]
    # built-in patterns are never waived on a credit line
    f.write_text(credit + " " + GH + "\n")
    monkeypatch.setattr(privacy_scan, "AUTHOR_CREDIT_LINES",
                        {("LICENSE", hashlib.sha256((credit + " " + GH).encode()).hexdigest())})
    assert "secret_github" in {n for _, _, n in privacy_scan.scan([f], privacy_scan.load_patterns({}), base_root=tmp_path)}


def test_default_public_paths_have_no_builtin_leaks():
    findings = privacy_scan.scan(privacy_scan.default_roots(), privacy_scan.load_patterns({}))
    assert findings == [], "\n".join(f"{p}:{n}:{k}" for p, n, k in findings)


def test_public_paths_pass_local_denylist():
    if not os.environ.get(privacy_scan.ENV_VAR):
        pytest.skip(f"{privacy_scan.ENV_VAR} not set")
    findings = privacy_scan.scan(privacy_scan.default_roots(), privacy_scan.load_patterns())
    assert findings == [], "\n".join(f"{p}:{n}:{k}" for p, n, k in findings)
