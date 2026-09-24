#!/usr/bin/env python3
"""Scan files for personal-data and secret leaks. Exit 1 on findings; prints file:line:pattern.

Built-in patterns cover home paths, non-example emails, phone numbers, common secrets
(API keys, tokens, private keys), street addresses, real listing-site URLs and money figures.
An optional local denylist file named by $HOUSING_PRIVACY_DENYLIST (one regex per line,
`#` comments) adds personal strings. Keep that file OUTSIDE the repo.

Inline allow markers have no effect. Only hash-bound fictional money exceptions apply.
Unreadable, oversized, symlinked, or unsupported binary inputs fail closed.
Names and numeric preferences require manual provenance review: regex is not certification.

Usage:
  python3 scripts/privacy_scan.py              # scan the default public paths
  python3 scripts/privacy_scan.py PATH [...]   # scan specific files/dirs
  python3 scripts/privacy_scan.py --all        # every git-tracked file (including private legacy)
  python3 scripts/privacy_scan.py --public-check  # strict manifest-only public checkout
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_boundary import selected_files, check_public_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_VAR = "HOUSING_PRIVACY_DENYLIST"
# Only money figures can be excepted, never secrets or local denylist matches.
# Exact relative file + pattern + UTF-8 line hash; changing any byte invalidates it.
EXCEPTIONS = {
    ("profile.example/USER_PROFILE.md", "money_figure", "5d593f56f312ac8360d07428bdb7fca4fff57dbb7af5b51d1745a3043cee135c"):
        "Independently fictional example persona budget, explicitly labelled as invented.",
}
# The owner's intentional public author credit. Only these exact lines (file + SHA256 of the
# line) may match the local denylist; any other occurrence of the name is still a finding,
# and built-in secret/path/email patterns are never waived.
AUTHOR_CREDIT_LINES = {
    ("LICENSE", "63a772b27ed5bf8307a2dcacbfe1f2ca8d14f4e6635fc36e1a7d478f22e00e0c"),
    ("pyproject.toml", "0857bd8b2dcf7ab6a8848abc172e891f2328852ad535406729d0877bd853c133"),
    ("README.md", "4ee8950ccf7d21653b97b916a747cb7fb0ea4f2a7f6daa2963235a2f9b98a184"),
}
MAX_BYTES = 5_000_000
# Add only independently verified vendor assets, with exact bytes and provenance.
# Keys must be app/static/vendor/... or docs/images/...; never profiles, configs, or arbitrary binaries.
TRUSTED_BINARY_ASSETS: dict[str, dict[str, str]] = {
    "docs/images/ranking.png": {"sha256": "b3e6ff232089c682574670fa18b87b58aa0a3549e34f0200820724f4085d87f1", "reason": "README screenshot of the fictional Harborview demo; OCR-scanned, no metadata chunks."},
    "docs/images/social-preview.png": {"sha256": "d0e6dda6463351b973b7233ed9322af127595f7d1d8089d417b70d1f7e3f1504", "reason": "Social preview built from the fictional demo screenshot; OCR-scanned, no metadata chunks."},
    "docs/images/overview.png": {"sha256": "8e35d762f7cb8877c5e8bf4e0b0ab592ce59cee6be275e3a6ad45c6f3b487d83", "reason": "README screenshot of the fictional Harborview demo; OCR-scanned, no metadata chunks."},
    "app/static/vendor/leaflet-1.9.4/images/layers-2x.png": {"sha256": "066daca850d8ffbef007af00b06eac0015728dee279c51f3cb6c716df7c42edf", "reason": "Byte-identical to official npm leaflet-1.9.4 dist image."},
    "app/static/vendor/leaflet-1.9.4/images/layers.png": {"sha256": "1dbbe9d028e292f36fcba8f8b3a28d5e8932754fc2215b9ac69e4cdecf5107c6", "reason": "Byte-identical to official npm leaflet-1.9.4 dist image."},
    "app/static/vendor/leaflet-1.9.4/images/marker-icon-2x.png": {"sha256": "00179c4c1ee830d3a108412ae0d294f55776cfeb085c60129a39aa6fc4ae2528", "reason": "Byte-identical to official npm leaflet-1.9.4 dist image."},
    "app/static/vendor/leaflet-1.9.4/images/marker-icon.png": {"sha256": "574c3a5cca85f4114085b6841596d62f00d7c892c7b03f28cbfa301deb1dc437", "reason": "Byte-identical to official npm leaflet-1.9.4 dist image."},
    "app/static/vendor/leaflet-1.9.4/images/marker-shadow.png": {"sha256": "264f5c640339f042dd729062cfc04c17f8ea0f29882b538e3848ed8f10edb4da", "reason": "Byte-identical to official npm leaflet-1.9.4 dist image."},
}
# Upstream vendor text files verified byte-identical to official npm tarballs.
# Only the local personal denylist is waived for these exact bytes (legacy IE CSS filter
# names can collide with personal terms); built-in secret/path/email patterns still apply.
TRUSTED_VENDOR_TEXT: dict[str, str] = {
    "app/static/vendor/leaflet-1.9.4/leaflet.css": "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6",
    "app/static/vendor/leaflet-1.9.4/leaflet.js": "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a",
}

BUILTIN_PATTERNS: dict[str, str] = {
    "home_path_linux": r"/home/[a-z_][a-z0-9_-]*/",
    "home_path_mac": r"/Users/[A-Za-z][A-Za-z0-9_-]*/",
    "home_path_windows": r"\b[A-Z]:\\\\?(?:Users|AI)\b",
    "email": r"[A-Za-z0-9._%+-]+@(?!example\.(?:com|org|net)\b|users\.noreply\.github\.com\b)"
             r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}",
    "phone": r"(?<![\w.])(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w.])",
    "private_ip": r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01])|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7]))\.\d{1,3}\.\d{1,3}\b",
    "secret_github": r"gh[pousr]_[A-Za-z0-9]{20,}",
    "secret_openai": r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}",
    "secret_aws": r"\bAKIA[0-9A-Z]{16}\b",
    "secret_google": r"\bAIza[0-9A-Za-z_-]{30,}",
    "secret_slack": r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    "private_key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "bearer_token": r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}",
    "assigned_secret": r"(?i)\b(?:api[_-]?key|secret|password|passwd)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]",
    # Specific listing/unit pages (site homepages and search pages are fine).
    "listing_url": r"(?i)https?://(?:www\.)?(?:redfin|zillow|trulia|apartments|realtor|hotpads|streeteasy|"
                   r"rent|zumper|craigslist)\.(?:com|org)/\S*(?:/home/\d|homedetails|/unit-|/apt-|"
                   r"_zpid|/listing|/rentals/\d|/b/)\S*",
    "street_address": r"(?i)\b\d{1,6}[A-Z]?(?:-\d+)?\s+(?:[A-Z0-9][A-Z0-9.'-]*\s+){1,5}"
                      r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ct|Court|Ln|Lane|Way|Pl|Place|Ter|Terrace|Cir|Circle|Pkwy|Parkway|Hwy|Highway|Cres|Crescent|Trail|Trl)\b\.?",
    "unit_address": r"(?i)\b(?:apt\.?|apartment|unit|suite|ste\.?)\s*#?\s*\d+[A-Z]?\b",
    "money_figure": r"\$\s?\d{1,3}(?:,\d{3})+(?:\.\d{2})?\b|\$\s?\d{4,}\b",
}
# Fictional sample data is allowed to have street addresses (all on "Example Ave").
ADDRESS_OK = re.compile(r"\bExample\s+(?:Ave|Avenue|St|Street)\b")


def load_patterns(environ=None) -> dict[str, re.Pattern]:
    env = os.environ if environ is None else environ
    pats = {name: re.compile(p) for name, p in BUILTIN_PATTERNS.items()}
    path = env.get(ENV_VAR)
    if path:
        for i, line in enumerate(Path(path).expanduser().read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if line and not line.startswith("#"):
                pats[f"denylist:{i}"] = re.compile(line, re.IGNORECASE)
    return pats


def _tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True)
    return [REPO_ROOT / p for p in out.stdout.decode().split("\0") if p]


def iter_files(roots):
    for root in roots:
        root = Path(root)
        if root.is_symlink() or not root.is_dir():
            yield root
        else:
            try:
                children = sorted(root.iterdir())
            except OSError:
                yield root
                continue
            yield from iter_files(children)


def _read(path: Path, *, relative="") -> str:
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("symlink")
    try:
        st = path.stat()
        if not path.is_file() or not st.st_mode & 0o444:
            raise ValueError("unreadable")
        if st.st_size > MAX_BYTES:
            raise ValueError("oversize")
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except OSError:
        raise ValueError("unreadable") from None
    if len(data) > MAX_BYTES:
        raise ValueError("oversize")
    binary = any(byte < 32 and byte not in {9, 10, 13} for byte in data)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        binary, text = True, ""
    if binary:
        trust = TRUSTED_BINARY_ASSETS.get(relative, {})
        if (not relative.startswith(("app/static/vendor/", "docs/images/")) or not trust.get("reason")
                or hashlib.sha256(data).hexdigest() != trust.get("sha256")):
            raise ValueError("unsupported_binary")
        # Vetted assets still have printable strings checked for denylist/secret hits.
        return "\n".join(m.decode("ascii") for m in re.findall(rb"[\x20-\x7e]+", data))
    return text


def scan(roots, patterns, *, base_root=None) -> list[tuple[str, int, str]]:
    findings = []
    for path in iter_files(roots):
        try:
            relative = path.absolute().relative_to(Path(base_root or REPO_ROOT).absolute()).as_posix()
        except ValueError:
            relative = ""
        try:
            text = _read(path, relative=relative)
        except ValueError as exc:
            findings.append((str(path), 0, "scan_error:" + str(exc)))
            continue
        vendor_ok = (TRUSTED_VENDOR_TEXT.get(relative) is not None and
                     hashlib.sha256(path.read_bytes()).hexdigest() == TRUSTED_VENDOR_TEXT[relative])
        for lineno, line in enumerate(text.splitlines(), 1):
            credit_ok = (relative, hashlib.sha256(line.encode("utf-8")).hexdigest()) in AUTHOR_CREDIT_LINES
            for name, rx in patterns.items():
                if (vendor_ok or credit_ok) and name.startswith("denylist:"):
                    continue
                if name == "money_figure" and EXCEPTIONS.get(
                    (relative, name, hashlib.sha256(line.encode("utf-8")).hexdigest())
                ):
                    continue
                if name == "street_address":
                    hits = [m for m in rx.finditer(line) if not ADDRESS_OK.search(m.group(0))]
                    if hits:
                        findings.append((str(path), lineno, name))
                elif rx.search(line):
                    findings.append((str(path), lineno, name))
    return findings


def default_roots() -> list[Path]:
    return selected_files(REPO_ROOT)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args == ["--all"]:
            roots = _tracked_files()
        elif args == ["--public-check"]:
            roots = check_public_tree(REPO_ROOT)
        else:
            if any(a.startswith("--") for a in args):
                raise ValueError("unknown option")
            roots = [Path(a) for a in args] or default_roots()
        findings = scan(roots, load_patterns())
    except (OSError, ValueError, re.error, subprocess.SubprocessError):
        print("Privacy scan failed: invalid inventory, unreadable input, or invalid local policy.", file=sys.stderr)
        return 1
    for path, lineno, name in findings:
        try:
            path = str(Path(path).resolve().relative_to(REPO_ROOT))
        except ValueError:
            path = "<external-input>"
        print(f"{path}:{lineno}:{name}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
