import copy
import json
from datetime import date

import pytest

from housing_agent.models import ValidationError
from housing_agent.profile import validate_profile, load_profile
from housing_agent import scoring
from tests.conftest import make_candidate


@pytest.mark.parametrize("patch", [
    {"categories": "wrong"}, {"categories": [None]},
    {"categories": [{"id": [], "weight": 1}]},
    {"categories": [{"id": "one", "weight": float("nan")}]},
    {"categories": [{"id": "one", "weight": True}]},
    {"risk_caps": {"x": []}}, {"risk_caps": []}, {"reject_flags": [{}]},
    {"anchors": [None]}, {"anchors": [{"id": "x", "adjustments": [1]}]},
    {"budget": {"soft_ceiling": "lots"}}, {"budget": {"ideal_max": float("inf")}},
    {"staleness_days": {"pricing": "no"}}, {"unit": {"allow_studio": "no"}},
    {"areas": [{"id": []}]}, {"display": {"map_center": [float("nan"), 1]}},
    {"revision": -1}, {"revision": True}, {"revision": "1"},
])
def test_malformed_profile_is_validation_error(profile_dict, patch):
    profile_dict.update(patch)
    with pytest.raises(ValidationError):
        validate_profile(profile_dict)


@pytest.mark.parametrize("payload", [None, [], 1, "bad"])
def test_profile_root_type(payload):
    with pytest.raises(ValidationError):
        validate_profile(payload)


def test_malformed_profile_json_fails_clearly(service):
    (service.location.path / "profile.json").write_text("{")
    with pytest.raises(ValidationError):
        load_profile(service.location)


@pytest.mark.parametrize("patch", [
    {"name": []}, {"status": []}, {"id": 3}, {"id": ""}, {"schema_version": 2},
    {"facts": []}, {"judgments": []}, {"risk_flags": [None]}, {"risk_flags": [{}]},
    {"is_baseline": "false"}, {"facts": {"base_rent": {"value": float("nan")}}},
    {"facts": {"base_rent": {"value": "unknown price"}}},
    {"facts": {"base_rent": {"value": -10}}},
    {"facts": {"anchor_minutes": {"value": [1]}}},
    {"facts": {"anchor_minutes": {"value": {"office": {"walk": "maybe"}}}}},
    {"facts": {"x": {"value": 1, "confidence": "certain"}}},
    {"facts": {"x": {"value": 1, "checked": "not-a-date"}}},
    {"facts": {"x": {"value": 1, "source": []}}},
    {"judgments": {"social_upside": {"score": 11, "rationale": "r"}}},
    {"judgments": {"social_upside": {"score": "oops", "rationale": "r"}}},
    {"judgments": {"social_upside": {"score": 1, "rationale": []}}},
    {"judgments": {"social_upside": {"score": 1, "rationale": "r", "evidence": "url"}}},
    {"judgments": {"unknown": {"score": 1, "rationale": "r"}}},
])
def test_candidate_writes_validate_before_persisting(service, patch):
    payload = make_candidate()
    payload.update(patch)
    with pytest.raises(ValidationError):
        service.put_candidate(payload, "test")
    assert service.store.list() == []
    assert service.store.events() == []


@pytest.mark.parametrize("score", [None, "oops", True, float("nan"), float("inf")])
def test_judgment_score_errors_are_validation(service, score):
    service.put_candidate(make_candidate(), "test")
    with pytest.raises(ValidationError):
        service.set_judgment("alpha", "social_upside", score, "r", "test")


@pytest.mark.parametrize("evidence", ["facts.base_rent", [1], [{}]])
def test_judgment_evidence_shape(service, evidence):
    service.put_candidate(make_candidate(), "test")
    with pytest.raises(ValidationError):
        service.set_judgment("alpha", "social_upside", 5, "r", "test", evidence=evidence)


@pytest.mark.parametrize("source,confidence,reference", [
    ("", "high", "facts.base_rent"), ("https://example.test", "low", "facts.base_rent"),
    ("https://example.test", "legacy", "facts.base_rent"),
    ("https://example.test", "high", "facts.nonexistent"),
    ("https://example.test", "high", "notes/nonexistent.md"),
])
def test_unsupported_evidence_never_high(profile_dict, source, confidence, reference):
    c = make_candidate()
    c["facts"]["base_rent"].update(source=source, confidence=confidence)
    for j in c["judgments"].values():
        j["evidence"] = [reference]
    assert scoring.evaluate(c, profile_dict, date(2026, 4, 26))["confidence"] != "high"


def test_legacy_records_read_without_forcing_rationale(service):
    c = make_candidate()
    c["facts"]["base_rent"]["confidence"] = "legacy"
    for j in c["judgments"].values():
        j["rationale"] = ""
        j["evidence"] = []
    service.store.put(c)
    assert service.get_candidate("alpha")["evaluation"]["confidence"] == "low"
    assert service.put_candidate({"id": "alpha", "name": "Renamed"}, "test")["name"] == "Renamed"
