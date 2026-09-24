import json
import threading

import pytest

from housing_agent.models import ValidationError
from housing_agent.store import ConflictError, Store, atomic_write_json
from tests.conftest import make_candidate


def test_put_increments_version_and_detects_conflict(tmp_path):
    s = Store(tmp_path)
    a = s.put(make_candidate())
    assert a["version"] == 1
    assert s.put(make_candidate(), expected_version=1)["version"] == 2
    with pytest.raises(ConflictError):
        s.put(make_candidate(), expected_version=1)


def test_ids_cannot_escape_directory(tmp_path):
    s = Store(tmp_path)
    for bad in ("../x", "a/b", "", "UPPER", ".hidden"):
        with pytest.raises(ValidationError):
            s.get(bad)


def test_atomic_write_leaves_no_temp_files(tmp_path):
    atomic_write_json(tmp_path / "x.json", {"a": 1})
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]
    assert json.loads((tmp_path / "x.json").read_text()) == {"a": 1}


def test_ledger_is_append_only_jsonl(tmp_path):
    s = Store(tmp_path)
    s.append_event("ui", "create", "a", before=None, after={"x": 1})
    s.append_event("cli", "update", "a", before={"x": 1}, after={"x": 2})
    events = s.events()
    assert [e["action"] for e in events] == ["create", "update"]
    assert events[0]["before_hash"] is None and events[1]["before_hash"] == events[0]["after_hash"]


def test_concurrent_puts_do_not_lose_versions(tmp_path):
    s = Store(tmp_path)
    threads = [threading.Thread(target=lambda: s.put(make_candidate())) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert s.get("alpha")["version"] == 20
