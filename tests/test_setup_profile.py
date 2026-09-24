"""First-run setup: `housing init` and the answers-to-profile builder."""

import json

import pytest

from housing_agent.cli import main
from housing_agent.models import ValidationError
from housing_agent.profile import ProfileLocation, load_profile
from housing_agent.setup_profile import QUESTIONS, build_profile, prompt_answers, write_new_profile


def test_empty_answers_make_a_valid_profile():
    p = build_profile({})
    assert abs(sum(c["weight"] for c in p["categories"]) - 1) < 1e-9
    assert "budget" not in p and "anchors" not in p


def test_full_answers_map_to_profile():
    p = build_profile({
        "name": "Test move", "region": "harborview",
        "budget": {"ideal_min": 1500, "ideal_max": 1800, "soft_ceiling": 2000, "hard_ceiling": 2200},
        "unit": {"min_bedrooms": 1, "allow_studio": False},
        "anchors": [{"label": "Office", "visits_per_week": 2, "modes": ["transit"], "ideal_minutes": 20,
                     "max_minutes": 45}],
        "weights": {"cost_value": 3, "friction_risk": 1},
    })
    assert p["budget"]["hard_ceiling"] == 2200 and "studio_unit" in p["reject_flags"]
    assert p["anchors"][0]["id"] == "office" and p["anchors"][0]["modes"] == ["transit"]
    assert abs(sum(c["weight"] for c in p["categories"]) - 1) < 1e-9
    assert max(p["categories"], key=lambda c: c["weight"])["id"] == "cost_value"


@pytest.mark.parametrize("answers", [
    {"budget": {"ideal_min": 3000, "ideal_max": 1000}},
    {"weights": {"not_a_category": 1}},
    {"anchors": [{"label": "Gym", "ideal_minutes": 30, "max_minutes": 10}]},
    {"favorite_color": "blue"},
])
def test_bad_answers_are_explained(answers):
    with pytest.raises(ValidationError):
        build_profile(answers)


def test_write_never_overwrites(tmp_path):
    write_new_profile(tmp_path / "p", build_profile({}))
    with pytest.raises(FileExistsError):
        write_new_profile(tmp_path / "p", build_profile({}))


def test_prompts_can_all_be_skipped():
    answers = prompt_answers(ask=lambda _q: "", say=lambda *_: None)
    assert build_profile(answers)["unit"]["allow_studio"] is False


def test_cli_init_with_answers_json(tmp_path, capsys):
    target = tmp_path / "mine"
    answers = json.dumps({"budget": {"ideal_min": 1000, "ideal_max": 1200}})
    assert main(["--json", "init", "--dir", str(target), "--answers", answers]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["created"] == str(target)
    assert load_profile(ProfileLocation(target, "argument"))["budget"]["ideal_max"] == 1200
    assert main(["--json", "init", "--dir", str(target), "--answers", "{}"]) != 0


def test_cli_setup_questions_lists_required_and_optional(capsys):
    assert main(["--json", "init", "--questions"]) == 0
    qs = json.loads(capsys.readouterr().out)
    assert [q["key"] for q in qs] == [q["key"] for q in QUESTIONS]
    assert all("required" in q for q in qs)
