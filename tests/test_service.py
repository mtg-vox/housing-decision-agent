import json

import pytest

from housing_agent.models import ValidationError
from tests.conftest import make_candidate


def test_put_get_state_and_ranking(service):
    service.put_candidate(make_candidate("alpha"), actor="ui")
    service.put_candidate(make_candidate("beta", risk_flags=["adjacent_high_disruption_construction"]), actor="cli")
    service.put_candidate(make_candidate("home", is_baseline=True, status="baseline"), actor="ui")
    state = service.get_state()
    assert state["ranking"] == ["alpha", "beta"]  # baseline excluded from ranking
    assert "evaluation" in state["candidates"][0]
    on_disk = json.loads((service.location.path / "candidates" / "alpha.json").read_text())
    assert "evaluation" not in on_disk and "score_summary" not in on_disk


def test_every_write_is_logged_with_actor(service):
    service.put_candidate(make_candidate(), actor="llm:claude", reason="new lead")
    service.patch_facts("alpha", {"parking_monthly": {"value": 150, "kind": "pricing"}}, actor="ui")
    service.set_judgment("alpha", "social_upside", 6, "fewer venues nearby", actor="llm:claude",
                         evidence=["notes/x.md"])
    actions = [(e["actor"], e["action"]) for e in service.store.events()]
    assert actions == [("llm:claude", "create"), ("ui", "patch_facts"), ("llm:claude", "set_judgment")]
    assert service.get_candidate("alpha")["evaluation"]["costs"]["all_in_monthly"] == 2350


def test_judgment_requires_rationale_and_known_category(service):
    service.put_candidate(make_candidate(), actor="ui")
    with pytest.raises(ValidationError):
        service.set_judgment("alpha", "social_upside", 7, "", actor="ui")
    with pytest.raises(ValidationError):
        service.set_judgment("alpha", "nope", 7, "why", actor="ui")


def test_facts_must_have_value(service):
    service.put_candidate(make_candidate(), actor="ui")
    with pytest.raises(ValidationError):
        service.patch_facts("alpha", {"base_rent": 2000}, actor="ui")


def test_actor_is_attribution_only(service):
    service.put_candidate(make_candidate(), actor="custom-client")
    assert service.store.events()[0]["actor"] == "custom-client"
    with pytest.raises(ValidationError):
        service.put_candidate(make_candidate(), actor="")


def test_soft_delete_archives_and_hard_delete_is_cli_only(service):
    service.put_candidate(make_candidate(), actor="ui")
    assert service.delete_candidate("alpha", actor="ui")
    assert service.get_candidate("alpha")["status"] == "archived"
    assert service.delete_candidate("alpha", actor="llm:x", hard=True)
    assert service.get_candidate("alpha") is None


def test_actor_can_propose_and_apply(service):
    pid = service.propose_profile_change({"budget": {"soft_ceiling": 2700}}, actor="llm:claude",
                                         reason="user said budget went up")
    assert service.profile["budget"]["soft_ceiling"] == 2600
    service.apply_proposal(pid, actor="llm:claude")
    assert service.profile["budget"]["soft_ceiling"] == 2700
    on_disk = json.loads((service.location.path / "profile.json").read_text())
    assert on_disk["budget"]["soft_ceiling"] == 2700
    assert service.list_proposals()[0]["state"] == "applied"
    with pytest.raises(ValidationError):
        service.apply_proposal(pid, actor="ui")


def test_invalid_proposal_rejected_up_front(service):
    with pytest.raises(ValidationError):
        service.propose_profile_change({"categories": []}, actor="llm:x", reason="oops")


def test_reject_proposal(service):
    pid = service.propose_profile_change({"budget": {"soft_ceiling": 2700}}, actor="llm:x", reason="r")
    service.reject_proposal(pid, actor="ui")
    assert service.get_state()["pending_proposals"] == []
    assert service.profile["budget"]["soft_ceiling"] == 2600


def test_compare_and_brief(service):
    service.put_candidate(make_candidate("alpha"), actor="ui")
    service.put_candidate(make_candidate("home", is_baseline=True, status="baseline"), actor="ui")
    rows = service.compare(["alpha", "home"])
    assert [r["id"] for r in rows] == ["alpha", "home"] and rows[1]["is_baseline"]
    brief = service.research_brief("alpha")
    assert "Social life" in brief and "Compare against baseline: Home" in brief
    assert "Test Renter" not in brief  # profile name is not needed for research
