"""Preferences editor API: profile read, preview (sensitivity), proposals apply/reject."""

from __future__ import annotations

import json
import threading

import pytest

from housing_agent.web import make_server
from tests.conftest import judgments, make_candidate
from tests.test_web import TOKEN, req

MONEY_HEAVY = {"financial_efficiency": 10, "daily_lifestyle_quality": 2, "social_upside": 2,
               "strategic_networking_value": 2, "peace_build_quality": 2, "friction_risk": 2}
SOCIAL_HEAVY = {"financial_efficiency": 2, "daily_lifestyle_quality": 6, "social_upside": 10,
                "strategic_networking_value": 6, "peace_build_quality": 6, "friction_risk": 6}


def weights_patch(profile, weights: dict) -> dict:
    return {"categories": [{**c, "weight": weights[c["id"]]} for c in profile["categories"]]}


MONEY_WEIGHTS = {"financial_efficiency": 0.9, "daily_lifestyle_quality": 0.02, "social_upside": 0.02,
                 "strategic_networking_value": 0.02, "peace_build_quality": 0.02, "friction_risk": 0.02}


@pytest.fixture
def server(service):
    service.put_candidate(make_candidate("base", status="baseline", is_baseline=True), actor="test")
    service.put_candidate(make_candidate("cheap", judgments=judgments(MONEY_HEAVY)), actor="test")
    service.put_candidate(make_candidate("social", judgments=judgments(SOCIAL_HEAVY)), actor="test")
    srv = make_server(service, "127.0.0.1", 0, token=TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def ranking(state):
    live = [c for c in state["candidates"] if not c["is_baseline"]]
    return [c["id"] for c in sorted(live, key=lambda c: -c["score_summary"]["weighted_score"])]


def test_get_profile_and_token_required(server):
    assert req(server, "GET", "/api/profile", token=False)[0] == 403
    assert req(server, "POST", "/api/profile/preview", {"patch": {}}, token=False)[0] == 403
    assert req(server, "POST", "/api/profile/proposals", {"patch": {}}, token=False)[0] == 403
    status, data, _ = req(server, "GET", "/api/profile")
    assert status == 200
    assert {c["id"] for c in data["categories"]} >= {"financial_efficiency", "friction_risk"}
    for key in ("budget", "risk_caps", "reject_flags", "anchors", "staleness_days"):
        assert key in data


def test_preview_does_not_write(server, service, profile_dir):
    before = (profile_dir / "profile.json").read_bytes()
    events = len(service.store.events())
    status, data, _ = req(server, "POST", "/api/profile/preview",
                          {"patch": weights_patch(service.profile, MONEY_WEIGHTS)})
    assert status == 200
    assert [r["id"] for r in data["before"]] == ["social", "cheap"]
    assert [r["id"] for r in data["after"]] == ["cheap", "social"]
    assert data["after"][0]["moved"] == 1 and data["after"][1]["moved"] == -1
    assert (profile_dir / "profile.json").read_bytes() == before
    assert len(service.store.events()) == events
    assert service.profile["categories"][0]["weight"] == 0.25


def test_invalid_weights_400(server, service):
    bad = weights_patch(service.profile, {c["id"]: 0.5 for c in service.profile["categories"]})
    for path in ("/api/profile/preview", "/api/profile/proposals"):
        status, data, _ = req(server, "POST", path, {"patch": bad, "reason": "x"})
        assert status == 400 and any("sum to 1.0" in p for p in data["problems"])
    assert req(server, "POST", "/api/profile/proposals/prop-deadbeef/apply", {})[0] == 400


def test_propose_apply_changes_weights_and_ranking(server, service, profile_dir):
    status, data, _ = req(server, "GET", "/api/state")
    assert ranking(data) == ["social", "cheap"]
    status, data, _ = req(server, "POST", "/api/profile/proposals",
                          {"patch": weights_patch(service.profile, MONEY_WEIGHTS), "reason": "money matters"})
    assert status == 201
    pid = data["id"]
    pending = data["state"]["pending_proposals"]
    assert pending[0]["id"] == pid and pending[0]["actor"] == "ui:dashboard"
    status, data, _ = req(server, "POST", f"/api/profile/proposals/{pid}/apply", {})
    assert status == 200 and data["proposal"]["state"] == "applied"
    assert ranking(data["state"]) == ["cheap", "social"]
    assert data["state"]["pending_proposals"] == []
    on_disk = json.loads((profile_dir / "profile.json").read_text())
    assert on_disk["categories"][0]["weight"] == 0.9
    # budget patch (nested dict merge) also works
    status, data, _ = req(server, "POST", "/api/profile/proposals",
                          {"patch": {"budget": {"soft_ceiling": 2700}}, "reason": "b"})
    assert status == 201
    assert req(server, "POST", f"/api/profile/proposals/{data['id']}/apply", {})[0] == 200
    assert service.profile["budget"]["soft_ceiling"] == 2700 and service.profile["budget"]["hard_ceiling"] == 2800


def test_reject_leaves_profile_unchanged(server, service, profile_dir):
    before = (profile_dir / "profile.json").read_bytes()
    _, data, _ = req(server, "POST", "/api/profile/proposals",
                     {"patch": {"reject_flags": []}, "reason": "drop"})
    pid = data["id"]
    status, data, _ = req(server, "POST", f"/api/profile/proposals/{pid}/reject", {"reason": "nope"})
    assert status == 200 and data["proposal"]["state"] == "rejected"
    assert (profile_dir / "profile.json").read_bytes() == before
    assert service.profile["reject_flags"] == ["studio_unit"]
    assert req(server, "POST", f"/api/profile/proposals/{pid}/apply", {})[0] == 400


def test_computed_rejections_excluded_from_metrics_and_preview(server, service):
    service.put_candidate(make_candidate("studio", risk_flags=["studio_unit"]), actor="test")
    _, state, _ = req(server, "GET", "/api/state")
    assert state["metrics"]["live_candidates"] == 2
    assert state["metrics"]["rejected_candidates"] == 1
    studio = next(c for c in state["candidates"] if c["id"] == "studio")
    assert studio["evaluation"]["rejected"] is True
    _, preview, _ = req(server, "POST", "/api/profile/preview", {"patch": {"reject_flags": []}})
    assert "studio" not in [c["id"] for c in preview["before"]]


def test_proposal_snapshots_and_profile_revision(server, service, profile_dir):
    _, before, _ = req(server, "GET", "/api/profile")
    assert before["profile_revision"]
    _, result, _ = req(server, "POST", "/api/profile/proposals",
                       {"patch": {"anchors": []}, "profile_revision": before["profile_revision"]})
    proposal = result["state"]["pending_proposals"][0]
    assert proposal["before"]["anchors"] == service.profile["anchors"]
    assert proposal["after"]["anchors"] == []
    external = json.loads((profile_dir / "profile.json").read_text())
    external["budget"]["soft_ceiling"] = 2650
    (profile_dir / "profile.json").write_text(json.dumps(external))
    status, _, _ = req(server, "POST", f"/api/profile/proposals/{proposal['id']}/apply",
                       {"profile_revision": before["profile_revision"]})
    assert status == 409
    _, refreshed, _ = req(server, "GET", "/api/profile")
    assert refreshed["budget"]["soft_ceiling"] == 2650
    assert refreshed["profile_revision"] != before["profile_revision"]


def test_list_replacement(server, service):
    _, data, _ = req(server, "POST", "/api/profile/proposals",
                     {"patch": {"reject_flags": ["a_flag", "b_flag"]}, "reason": "replace"})
    assert req(server, "POST", f"/api/profile/proposals/{data['id']}/apply", {})[0] == 200
    assert service.profile["reject_flags"] == ["a_flag", "b_flag"]


def test_llm_proposal_applied_by_ui(server, service):
    pid = service.propose_profile_change(weights_patch(service.profile, MONEY_WEIGHTS),
                                         actor="llm:assistant", reason="suggested")
    _, state, _ = req(server, "GET", "/api/state")
    assert any(p["id"] == pid and p["actor"] == "llm:assistant" for p in state["pending_proposals"])
    status, data, _ = req(server, "POST", f"/api/profile/proposals/{pid}/apply", {})
    assert status == 200 and ranking(data["state"]) == ["cheap", "social"]
    applied = [e for e in service.store.events() if e["action"] == "apply_proposal"]
    assert applied[-1]["actor"] == "ui:dashboard"


def test_read_only_403(tmp_path, profile_dict):
    from housing_agent.profile import resolve_profile_dir
    from housing_agent.service import HousingService
    ex = tmp_path / "profile.example"
    ex.mkdir()
    (ex / "profile.json").write_text(json.dumps(profile_dict))
    svc = HousingService(resolve_profile_dir(None, repo_root=tmp_path, environ={}))
    srv = make_server(svc, "127.0.0.1", 0, token=TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert req(srv, "GET", "/api/profile")[2].status == 200
        patch = {"budget": {"soft_ceiling": 2700}}
        assert req(srv, "POST", "/api/profile/proposals", {"patch": patch, "reason": "x"})[0] == 403
        assert req(srv, "POST", "/api/profile/proposals/prop-00000000/apply", {})[0] == 403
        assert req(srv, "POST", "/api/profile/proposals/prop-00000000/reject", {})[0] == 403
        assert req(srv, "POST", "/api/profile/preview", {"patch": patch})[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()
