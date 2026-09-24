"""Pinned dependency validation with synthetic archives; no downloads in unit tests."""
import hashlib
import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def installer():
    assert importlib.util.find_spec("install_gitleaks") is not None
    import install_gitleaks
    return install_gitleaks


def archive(name="gitleaks", link=False):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        info = tarfile.TarInfo(name)
        data = b"synthetic binary"
        if link:
            info.type = tarfile.SYMTYPE
            info.linkname = "elsewhere"
        else:
            info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def test_installer_requires_exact_download_checksum():
    data = archive()
    module = installer()
    with pytest.raises(ValueError, match="checksum"):
        module.verified_binary(data, "0" * 64)
    assert module.verified_binary(data, hashlib.sha256(data).hexdigest()) == b"synthetic binary"


@pytest.mark.parametrize("name,link", [("../gitleaks", False), ("gitleaks", True)])
def test_installer_never_extracts_archive_paths_or_symlinks(name, link):
    data = archive(name, link)
    with pytest.raises(ValueError):
        installer().verified_binary(data, hashlib.sha256(data).hexdigest())
