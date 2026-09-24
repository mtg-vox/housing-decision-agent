import multiprocessing as mp

import pytest

from housing_agent.profile import ProfileLocation
from housing_agent.service import HousingService
from housing_agent.store import ConflictError


def test_stale_services_read_and_merge_latest_profile(service):
    other = HousingService(service.location)
    service.update_profile({"name": "New Name"}, "test", expected_version=0)
    assert other.profile_summary()["name"] == "New Name"
    other.update_profile({"currency": "EUR"}, "test")
    assert service.profile["name"] == "New Name"
    assert service.profile["currency"] == "EUR"
    assert service.profile_summary()["revision"] == 2
    with pytest.raises(ConflictError):
        service.update_profile({"name": "Old Name"}, "test", expected_version=0)


def test_stale_proposal_conflicts_instead_of_overwriting(service):
    pid = service.propose_profile_change({"budget": {"soft_ceiling": 2700}}, "test", "draft")
    assert service.list_proposals()[0]["base_revision"] == 0
    other = HousingService(service.location)
    other.update_profile({"name": "New Name"}, "test")
    with pytest.raises(ConflictError):
        service.apply_proposal(pid, "cli")
    assert service.list_proposals()[0]["state"] == "pending"
    assert service.profile["name"] == "New Name"


def _resolve(root, pid, barrier, queue):
    svc = HousingService(ProfileLocation(root, "argument"))
    barrier.wait(timeout=10)
    try:
        svc.apply_proposal(pid, "cli")
        queue.put("ok")
    except (ValueError, ConflictError):
        queue.put("conflict")


def test_competing_processes_resolve_proposal_once(service):
    pid = service.propose_profile_change({"name": "Changed"}, "test", "draft")
    ctx = mp.get_context("spawn")
    barrier, queue = ctx.Barrier(2), ctx.Queue()
    ps = [ctx.Process(target=_resolve, args=(service.location.path, pid, barrier, queue)) for _ in range(2)]
    for p in ps:
        p.start()
    for p in ps:
        p.join(15)
        assert not p.is_alive() and p.exitcode == 0
    assert sorted(queue.get(timeout=3) for _ in ps) == ["conflict", "ok"]
    assert service.profile["revision"] == 1
    assert len(service.store.events()) == 2
