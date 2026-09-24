import json
import shutil

import pytest

from housing_agent import cli
from tests.conftest import make_candidate


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("HOUSING_PROFILE_DIR", raising=False)
    monkeypatch.delenv("HOUSING_ACTOR", raising=False)


def run(capsys, profile_dir, *argv):
    code = cli.main(["--profile", str(profile_dir), "--json", *argv])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), (json.loads(err) if err.strip() else None)


def test_list_and_show_json(service, profile_dir, capsys):
    service.put_candidate(make_candidate("alpha"), actor="ui")
    code, data, _ = run(capsys, profile_dir, "list")
    assert code == 0 and [c["id"] for c in data] == ["alpha"] and "evaluation" in data[0]
    code, data, _ = run(capsys, profile_dir, "show", "alpha")
    assert code == 0 and data["id"] == "alpha"
    code, _, err = run(capsys, profile_dir, "show", "nope")
    assert code == 2 and err["problems"]


def test_human_list(service, profile_dir, capsys):
    service.put_candidate(make_candidate("alpha"), actor="ui")
    assert cli.main(["--profile", str(profile_dir), "list"]) == 0
    assert "alpha" in capsys.readouterr().out


def test_set_fact_records_provenance(service, profile_dir, capsys):
    service.put_candidate(make_candidate("alpha"), actor="ui")
    code, _, _ = run(capsys, profile_dir, "set-fact", "alpha", "parking_monthly", "150",
                     "--source", "https://x.test/p", "--checked", "2026-04-30",
                     "--confidence", "medium", "--kind", "pricing")
    assert code == 0
    _, data, _ = run(capsys, profile_dir, "show", "alpha")
    assert data["facts"]["parking_monthly"] == {"value": 150, "source": "https://x.test/p",
                                                "checked": "2026-04-30", "confidence": "medium", "kind": "pricing"}
    code, _, _ = run(capsys, profile_dir, "set-fact", "alpha", "x", "v", "--source", "s",
                     "--checked", "not-a-date", "--confidence", "low", "--kind", "other")
    assert code == 2


def test_judge_without_rationale_is_validation_error(service, profile_dir, capsys):
    service.put_candidate(make_candidate("alpha"), actor="ui")
    code, out, err = run(capsys, profile_dir, "judge", "alpha", "social_upside", "7")
    assert code == 2 and out is None and "rationale" in err["problems"][0]


def test_expect_version_conflict(service, profile_dir, capsys):
    service.put_candidate(make_candidate("alpha"), actor="ui")  # version 1
    code, _, err = run(capsys, profile_dir, "judge", "alpha", "social_upside", "7",
                       "--rationale", "r", "--expect-version", "5")
    assert code == 3 and "conflict" in err["error"]
    code, data, _ = run(capsys, profile_dir, "judge", "alpha", "social_upside", "7",
                        "--rationale", "r", "--expect-version", "1")
    assert code == 0 and data["version"] == 2


def test_actor_does_not_gate_apply_or_hard_delete(service, profile_dir, capsys):
    service.put_candidate(make_candidate("alpha"), actor="ui")
    code, data, _ = run(capsys, profile_dir, "--actor", "llm:test", "propose",
                        "--patch", '{"budget": {"soft_ceiling": 2700}}', "--reason", "r")
    assert code == 0
    code, _, _ = run(capsys, profile_dir, "--actor", "llm:test", "apply", data["proposal_id"])
    assert code == 0
    code, data, _ = run(capsys, profile_dir, "--actor", "legacy", "propose",
                        "--patch", '{"name": "Fictional"}', "--reason", "r")
    code, _, _ = run(capsys, profile_dir, "--actor", "legacy", "reject", data["proposal_id"], "--reason", "x")
    assert code == 0
    code, _, _ = run(capsys, profile_dir, "--actor", "llm:test", "delete", "alpha", "--hard", "--yes")
    assert code == 0
    assert service.get_candidate("alpha") is None


def test_delete_requires_confirmation_and_cli_can_hard_delete(service, profile_dir, capsys):
    service.put_candidate(make_candidate("alpha"), actor="ui")
    assert run(capsys, profile_dir, "delete", "alpha")[0] == 2
    assert run(capsys, profile_dir, "delete", "alpha", "--hard", "--yes")[0] == 0
    assert service.get_candidate("alpha") is None


def test_read_only_example_rejects_writes(profile_dir, tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(profile_dir, repo / "profile.example")
    monkeypatch.chdir(repo)
    assert cli.main(["--json", "list"]) == 0
    capsys.readouterr()
    assert cli.main(["--json", "add", "--name", "New Place"]) == 4
    assert "read-only" in json.loads(capsys.readouterr().err)["error"]


def test_propose_and_apply_by_cli(service, profile_dir, capsys):
    code, data, _ = run(capsys, profile_dir, "propose", "--patch", '{"budget": {"soft_ceiling": 2700}}',
                        "--reason", "raise")
    pid = data["proposal_id"]
    _, props, _ = run(capsys, profile_dir, "proposals")
    assert props[0]["state"] == "pending"
    code, data, _ = run(capsys, profile_dir, "apply", pid)
    assert code == 0 and data["state"] == "applied"
    assert json.loads((profile_dir / "profile.json").read_text())["budget"]["soft_ceiling"] == 2700
    assert run(capsys, profile_dir, "propose", "--patch", "not json", "--reason", "r")[0] == 2


def test_preferences_direct_update_and_conflict(service, profile_dir, capsys):
    assert run(capsys, profile_dir, "preferences")[1]["revision"] == 0
    code, data, _ = run(capsys, profile_dir, "preferences", "--patch", '{"name":"New"}',
                         "--expect-version", "0")
    assert code == 0 and data["revision"] == 1
    assert service.profile["name"] == "New"
    assert run(capsys, profile_dir, "preferences", "--patch", '{"name":"Old"}',
               "--expect-version", "0")[0] == 3


def test_doctor(service, profile_dir, capsys):
    service.put_candidate(make_candidate("alpha"), actor="ui")
    code, data, _ = run(capsys, profile_dir, "doctor")
    assert code == 0 and data["ok"] and data["profile_source"] == "argument"
    assert data["read_only"] is False and data["validation"] == "ok"
    assert data["candidate_count"] == 1 and data["ledger_events"] == 1 and "stale_count" in data


def test_ledger_records_llm_actor(service, profile_dir, capsys, monkeypatch):
    assert run(capsys, profile_dir, "--actor", "llm:test", "add", "--name", "Beta Place")[0] == 0
    monkeypatch.setenv("HOUSING_ACTOR", "llm:test")
    assert run(capsys, profile_dir, "flag", "beta-place", "studio_unit")[0] == 0
    assert run(capsys, profile_dir, "judge", "beta-place", "social_upside", "6", "--rationale", "few venues",
               "--evidence", "https://x.test")[0] == 0
    events = service.store.events()
    assert [e["actor"] for e in events] == ["llm:test"] * 3
    assert service.get_candidate("beta-place")["risk_flags"] == ["studio_unit"]




def test_serve_passes_cli_resolved_profile(monkeypatch, profile_dir):
    from housing_agent import web
    seen = []
    monkeypatch.setenv("HOUSING_PROFILE_DIR", str(profile_dir))
    monkeypatch.setattr(web, "main", lambda argv: seen.extend(argv) or 0)
    assert cli.main(["serve"]) == 0
    assert seen[seen.index("--profile") + 1] == str(profile_dir.resolve())


def test_add_from_json_and_usage_errors(profile_dir, tmp_path, capsys):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(make_candidate("gamma")))
    code, data, _ = run(capsys, profile_dir, "add", "--from-json", str(f))
    assert code == 0 and data["id"] == "gamma"
    code, _, err = run(capsys, profile_dir, "bogus")
    assert code == 2 and "error" in err
    assert run(capsys, profile_dir, "--actor", "", "add", "--name", "x")[0] == 2
