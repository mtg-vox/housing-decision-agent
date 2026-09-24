"""Golden tests for the fictional profile.example/ sample."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from housing_agent import scoring
from housing_agent.profile import resolve_profile_dir, validate_profile
from housing_agent.service import HousingService
from housing_agent.store import ReadOnlyProfileError
from housing_agent.web import dashboard_state

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 24)

GOLDEN_RANKING = ["harbor-lofts", "midtown-commons", "riverside-flats", "old-mill-residences", "canal-point"]
GOLDEN_SCORES = {"current-home": 6.85, "harbor-lofts": 8.35, "midtown-commons": 7.95,
                 "riverside-flats": 7.6, "old-mill-residences": 7.05, "canal-point": 7.0,
                 "tiny-studio": 7.3, "bayfront-tower": None}


@pytest.fixture
def service(tmp_path):
    shutil.copytree(ROOT / "profile.example", tmp_path / "profile.example")
    loc = resolve_profile_dir(None, repo_root=tmp_path, environ={})
    assert loc.source == "example" and loc.read_only
    return HousingService(loc, today=TODAY)


def test_profile_valid(service):
    validate_profile(service.profile)
    assert service.profile["region"] == "example"


def test_every_candidate_evaluates(service):
    items = service.store.list()
    assert len(items) == 8
    for c in items:
        assert c["schema_version"] == 1
        assert all(u.startswith("https://example.com/") for u in c["source_urls"])
        assert "Harborview" in c["location"]["address"]
        ev = scoring.evaluate(c, service.profile, TODAY)
        assert ev["problems"] == [] and ev["unknown_flags"] == []


def test_golden_ranking_and_scores(service):
    state = service.get_state()
    assert state["ranking"][:3] == GOLDEN_RANKING[:3]
    assert state["ranking"] == GOLDEN_RANKING
    got = {c["id"]: c["evaluation"]["final_score"] for c in state["candidates"]}
    assert got == GOLDEN_SCORES
    assert state["read_only"] is True


def test_special_cases(service):
    rej = service.get_candidate("tiny-studio")["evaluation"]
    assert rej["rejected"] and rej["reject_reasons"] == ["studio_unit"]
    assert "base_rent" in service.get_candidate("old-mill-residences")["evaluation"]["stale_facts"]
    assert "old-mill-residences" in service.get_state()["stale_summary"]
    un = service.get_candidate("bayfront-tower")["evaluation"]
    assert un["final_score"] is None
    assert un["missing_categories"] == ["work_and_focus", "space_and_quiet", "friction_risk"]
    base = service.get_candidate("current-home")
    assert base["is_baseline"] and base["status"] == "baseline"
    assert service.get_candidate("canal-point")["evaluation"]["total_cap"] == 7.0


def test_dashboard_state(service):
    d = dashboard_state(service)
    assert d["read_only"] is True
    assert d["profile"]["baseline_id"] == "current-home"
    assert d["profile"]["map_center"] == [40.0, -75.0]
    assert d["metrics"]["total_candidates"] == 8
    assert d["metrics"]["rejected_candidates"] == 1
    assert d["metrics"]["best_score"] == 8.35
    assert d["category_labels"]["social_life"] == "Social life"


def test_writes_are_read_only(service):
    with pytest.raises(ReadOnlyProfileError):
        service.set_judgment("bayfront-tower", "friction_risk", 7, "x", actor="test:t")
    with pytest.raises(ReadOnlyProfileError):
        service.put_candidate({"name": "New Place"}, actor="test:t")
    with pytest.raises(ReadOnlyProfileError):
        service.store.write_profile(service.profile)
