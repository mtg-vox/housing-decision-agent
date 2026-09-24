"""Shared fixtures: a tiny fictional profile in a temp dir."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from housing_agent.profile import ProfileLocation
from housing_agent.service import HousingService

TODAY = date(2026, 5, 1)

PROFILE = {
    "schema_version": 1,
    "name": "Test Renter",
    "region": "testville",
    "currency": "USD",
    "budget": {"ideal_min": 2000, "ideal_max": 2400, "soft_ceiling": 2600, "hard_ceiling": 2800,
               "basis": "all_in_monthly"},
    "unit": {"min_bedrooms": 1, "allow_studio": False},
    "categories": [
        {"id": "financial_efficiency", "label": "Money", "weight": 0.25},
        {"id": "daily_lifestyle_quality", "label": "Daily life", "weight": 0.25},
        {"id": "social_upside", "label": "Social life", "weight": 0.125},
        {"id": "strategic_networking_value", "label": "Networking", "weight": 0.125},
        {"id": "peace_build_quality", "label": "Peace", "weight": 0.125},
        {"id": "friction_risk", "label": "Friction", "weight": 0.125},
    ],
    "anchors": [{"id": "office", "label": "Office", "visits_per_week": 1, "modes": ["walk", "transit"],
                 "adjustments": [{"max_minutes": 10, "delta": 0.5},
                                 {"max_minutes": 30, "delta": 0.0},
                                 {"max_minutes": None, "delta": -0.5}]}],
    "risk_caps": {
        "adjacent_high_disruption_construction": {"target": "total_score", "cap": 7.5},
        "ground_level_or_flood_exposed_parking": {"target": "friction_risk", "cap": 5.0},
    },
    "reject_flags": ["studio_unit"],
    "staleness_days": {"pricing": 14, "commute": 180},
}

SCORES8 = {"financial_efficiency": 8, "daily_lifestyle_quality": 8, "social_upside": 9,
           "strategic_networking_value": 8, "peace_build_quality": 8, "friction_risk": 8}


def judgments(scores=SCORES8, evidence=True):
    return {k: {"score": v, "rationale": "test", "evidence": ["facts.base_rent"] if evidence else []}
            for k, v in scores.items()}


def make_candidate(cid="alpha", **overrides):
    c = {"schema_version": 1, "id": cid, "name": cid.title(), "status": "active",
         "facts": {"base_rent": {"value": 2200, "kind": "pricing", "checked": "2026-04-25",
                                 "source": "https://example.com", "confidence": "high"}},
         "judgments": judgments(), "risk_flags": []}
    c.update(overrides)
    return c


@pytest.fixture
def profile_dict():
    return copy.deepcopy(PROFILE)


@pytest.fixture
def profile_dir(tmp_path: Path) -> Path:
    d = tmp_path / "profile"
    d.mkdir()
    (d / "profile.json").write_text(json.dumps(PROFILE), encoding="utf-8")
    return d


@pytest.fixture
def service(profile_dir: Path) -> HousingService:
    return HousingService(ProfileLocation(profile_dir, "argument"), today=TODAY)
