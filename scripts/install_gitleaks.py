#!/usr/bin/env python3
"""Install pinned gitleaks into a NEW caller-named file (Linux x64 only)."""
import argparse
import hashlib
import io
from pathlib import Path
import platform
import tarfile
import urllib.request

VERSION = "8.24.3"
ARCHIVE = f"gitleaks_{VERSION}_linux_x64.tar.gz"
# Pinned from the upstream v8.24.3 release checksums, not a runtime-downloaded checksum.
SHA256 = "9991e0b2903da4c8f6122b5c3186448b927a5da4deef1fe45271c3793f4ee29c"
URL = f"https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/{ARCHIVE}"
MAX_DOWNLOAD = 50_000_000


def verified_binary(data, expected=SHA256):
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("gitleaks download checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = [m for m in archive.getmembers() if m.name == "gitleaks"]
        if len(members) != 1 or not members[0].isfile() or members[0].size > MAX_DOWNLOAD:
            raise ValueError("archive must contain exactly one regular gitleaks executable")
        stream = archive.extractfile(members[0])
        if stream is None:
            raise ValueError("missing executable")
        return stream.read(MAX_DOWNLOAD + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        parser.error("installer supports Linux x64; install a separately verified gitleaks for your OS")
    if args.output.exists() or args.output.is_symlink() or any(p.is_symlink() for p in args.output.absolute().parents):
        parser.error("output must be a new regular file, with no symlink parents")
    with urllib.request.urlopen(URL, timeout=60) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError("download too large")
    binary = verified_binary(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        stream.write(binary)
    args.output.chmod(0o755)
    print(f"Installed verified gitleaks {VERSION}")


if __name__ == "__main__":
    main()
