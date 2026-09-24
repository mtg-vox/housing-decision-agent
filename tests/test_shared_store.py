"""Cross-process regressions using only fictional temporary profiles."""
import multiprocessing as mp
import time

from housing_agent.profile import ProfileLocation
from housing_agent.service import HousingService
from housing_agent.store import ConflictError
from tests.conftest import make_candidate


def _write(root, barrier, queue, field, expected):
    from housing_agent import store
    original = store.atomic_write_json
    def slow_write(path, payload):
        time.sleep(0.15)
        original(path, payload)
    store.atomic_write_json = slow_write
    svc = HousingService(ProfileLocation(root, "argument"))
    barrier.wait(timeout=10)
    try:
        svc.patch_facts("alpha", {field: {"value": 1}}, "test", expected_version=expected)
        queue.put("ok")
    except ConflictError:
        queue.put("conflict")


def _race(root, expected):
    ctx = mp.get_context("spawn")
    barrier, queue = ctx.Barrier(2), ctx.Queue()
    workers = [ctx.Process(target=_write, args=(root, barrier, queue, f, expected)) for f in ("one", "two")]
    for p in workers:
        p.start()
    for p in workers:
        p.join(15)
        assert not p.is_alive()
        assert p.exitcode == 0
    return sorted(queue.get(timeout=3) for _ in workers)


def test_processes_same_expected_version_one_wins(service):
    service.put_candidate(make_candidate(), "test")
    assert _race(service.location.path, 1) == ["conflict", "ok"]
    assert service.store.get("alpha")["version"] == 2
    assert len(service.store.events()) == 2


def test_processes_unrelated_patches_preserved(service):
    service.put_candidate(make_candidate(), "test")
    assert _race(service.location.path, None) == ["ok", "ok"]
    saved = service.store.get("alpha")
    assert {"one", "two"} <= saved["facts"].keys()
    assert saved["version"] == 3
    events = service.store.events()
    assert len(events) == 3
    assert events[1]["after_hash"] == events[2]["before_hash"]
