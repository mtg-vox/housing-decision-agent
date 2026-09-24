import pytest

from housing_agent import scoring
from tests.conftest import make_candidate


@pytest.mark.parametrize("overrides", [
    {"is_baseline": True}, {"status": "baseline"}, {"status": "rejected"},
    {"status": "archived"}, {"risk_flags": ["studio_unit"]}, {"judgments": {}},
])
def test_single_eligibility_predicate_excludes_non_choices(service, overrides):
    c = service.put_candidate(make_candidate(**overrides), "test")
    assert not scoring.is_eligible(c)
    assert service.get_state()["ranking"] == []


def test_eligible_active_and_no_silent_unit_rule(service):
    service.update_profile({"reject_flags": []}, "test")
    c = service.put_candidate(make_candidate(risk_flags=["studio_unit"]), "test")
    assert scoring.is_eligible(c)
    assert service.get_state()["ranking"] == ["alpha"]
