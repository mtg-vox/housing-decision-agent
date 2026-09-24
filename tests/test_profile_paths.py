import json
import shutil
from pathlib import Path

import pytest

from housing_agent.profile import ENV_VAR, ProfileLocation, resolve_profile_dir
from housing_agent.service import HousingService
from housing_agent.store import ReadOnlyProfileError
from tests.conftest import PROFILE, make_candidate


def test_example_canonical_path_cannot_be_made_writable(tmp_path):
    example = Path(__file__).resolve().parents[1] / "profile.example"
    link = tmp_path / "profile.local"
    link.symlink_to(example, target_is_directory=True)
    locations = [resolve_profile_dir(example, environ={}),
                 resolve_profile_dir(environ={ENV_VAR: str(example)}),
                 resolve_profile_dir(repo_root=tmp_path, environ={}),
                 ProfileLocation(link, "argument")]
    for loc in locations:
        assert loc.read_only
        with pytest.raises(ReadOnlyProfileError):
            HousingService(loc).put_candidate(make_candidate(), "test")
    link.unlink()
    shutil.copytree(example, link)
    assert not resolve_profile_dir(link, environ={}).read_only


def test_fallback_example_explicit_and_symlink_read_only(tmp_path):
    ex = tmp_path / "profile.example"
    ex.mkdir()
    (ex / "profile.json").write_text(json.dumps(PROFILE))
    alias = tmp_path / "alias"
    alias.symlink_to(ex, target_is_directory=True)
    assert resolve_profile_dir(alias, repo_root=tmp_path, environ={}).read_only


def test_broken_local_symlink_does_not_fall_back(tmp_path):
    (tmp_path / "profile.local").symlink_to(tmp_path / "missing", target_is_directory=True)
    ex = tmp_path / "profile.example"
    ex.mkdir()
    (ex / "profile.json").write_text(json.dumps(PROFILE))
    with pytest.raises(FileNotFoundError):
        resolve_profile_dir(repo_root=tmp_path, environ={})
