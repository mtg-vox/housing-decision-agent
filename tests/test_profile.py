import json

import pytest

from housing_agent.models import ValidationError
from housing_agent.profile import ENV_VAR, resolve_profile_dir, validate_profile
from housing_agent.service import HousingService
from housing_agent.store import ReadOnlyProfileError
from tests.conftest import PROFILE, make_candidate


def _mk(root, name):
    d = root / name
    d.mkdir()
    (d / "profile.json").write_text(json.dumps(PROFILE))
    return d


def test_resolution_order(tmp_path):
    arg, env = _mk(tmp_path, "arg"), _mk(tmp_path, "env")
    _mk(tmp_path, "profile.local")
    _mk(tmp_path, "profile.example")
    environ = {ENV_VAR: str(env)}
    assert resolve_profile_dir(arg, tmp_path, environ).source == "argument"
    assert resolve_profile_dir(None, tmp_path, environ).source == "env"
    assert resolve_profile_dir(None, tmp_path, {}).source == "local"
    (tmp_path / "profile.local" / "profile.json").unlink()
    with pytest.raises(FileNotFoundError):
        resolve_profile_dir(None, tmp_path, {})
    (tmp_path / "profile.local").rmdir()
    loc = resolve_profile_dir(None, tmp_path, {})
    assert loc.source == "example" and loc.read_only


def test_missing_profile_falls_back_to_bundled_demo_and_bad_paths_error(tmp_path):
    loc = resolve_profile_dir(None, tmp_path, {})
    assert loc.source == "example" and loc.read_only
    with pytest.raises(FileNotFoundError):
        resolve_profile_dir(tmp_path / "nope", tmp_path, {})


def test_validation_reports_all_problems(profile_dict):
    profile_dict["categories"][0]["weight"] = 0.5
    profile_dict["risk_caps"]["x"] = {"target": "nope", "cap": 11}
    profile_dict["budget"]["soft_ceiling"] = 1
    with pytest.raises(ValidationError) as exc:
        validate_profile(profile_dict)
    text = str(exc.value)
    assert "sum to 1.0" in text and "unknown target" in text and "between 0 and 10" in text and "budget" in text


def test_anchor_steps_must_end_open(profile_dict):
    profile_dict["anchors"][0]["adjustments"][-1]["max_minutes"] = 99
    with pytest.raises(ValidationError, match="null"):
        validate_profile(profile_dict)


def test_example_profile_is_read_only(tmp_path):
    _mk(tmp_path, "profile.example")
    svc = HousingService(resolve_profile_dir(None, tmp_path, {}))
    with pytest.raises(ReadOnlyProfileError):
        svc.put_candidate(make_candidate(), actor="ui")
