"""HTTP adapter tests: security guards + UI round-trip through the service."""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from housing_agent.web import make_server, to_view
from tests.conftest import make_candidate

TOKEN = "test-token-123"


@pytest.fixture
def server(service):
    service.put_candidate(make_candidate("base", status="baseline", is_baseline=True), actor="test")
    service.put_candidate(make_candidate("alpha"), actor="test")
    srv = make_server(service, "127.0.0.1", 0, token=TOKEN)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def req(srv, method, path, body=None, headers=None, token=True, raw=None):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": f"127.0.0.1:{port}"}
    if token:
        h["X-Housing-Token"] = TOKEN
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    conn.request(method, path, body=data, headers=h)
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(payload), resp
    except json.JSONDecodeError:
        return resp.status, payload.decode(), resp


def test_state_requires_token(server):
    assert req(server, "GET", "/api/state", token=False)[0] == 403
    status, data, _ = req(server, "GET", "/api/state")
    assert status == 200 and {c["id"] for c in data["candidates"]} == {"base", "alpha"}
    assert data["profile"]["baseline_id"] == "base"


def test_bad_host_rejected_even_for_static(server):
    assert req(server, "GET", "/", headers={"Host": "evil.example:80"})[0] == 403
    assert req(server, "GET", "/api/state", headers={"Host": "evil.example"})[0] == 403


def test_cross_origin_rejected(server):
    status, _, _ = req(server, "POST", "/api/candidates", {"name": "x"},
                       headers={"Origin": "http://evil.example"})
    assert status == 403


def test_index_injects_token_and_sets_csp(server):
    status, body, resp = req(server, "GET", "/", token=False)
    assert status == 200 and TOKEN in body and "__HOUSING_TOKEN__" not in body
    assert "frame-ancestors 'none'" in resp.getheader("Content-Security-Policy")


def test_static_traversal_blocked(server):
    for path in ("/../server.py", "/..%2f..%2fpyproject.toml", "/../../pyproject.toml"):
        assert req(server, "GET", path, token=False)[0] == 404


def test_health_has_no_paths(server):
    status, data, _ = req(server, "GET", "/api/health")
    assert status == 200 and "project_root" not in data and "/" not in json.dumps(data["profile_source"])


def test_body_limits_and_content_type(server):
    assert req(server, "POST", "/api/candidates", raw=b'{"name":"x"}',
               headers={"Content-Type": "text/plain"})[0] == 400
    assert req(server, "POST", "/api/candidates", raw=b"[1]",
               headers={"Content-Type": "application/json"})[0] == 400
    assert req(server, "POST", "/api/candidates", raw=b"{",
               headers={"Content-Type": "application/json"})[0] == 400
    # Oversized: send headers only; the server must answer 400 without reading the body.
    import socket
    port = server.server_address[1]
    token = TOKEN
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        head = (f"POST /api/candidates HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                f"Content-Type: application/json\r\nContent-Length: 1100000\r\n")
        if token:
            head += f"X-Housing-Token: {token}\r\n"
        s.sendall((head + "\r\n").encode())
        reply = s.recv(4096).decode(errors="replace")
    assert reply.split()[1] == "400"


def test_create_update_conflict_archive(server, service):
    status, data, _ = req(server, "POST", "/api/candidates", {
        "name": "New Place", "status": "active", "base_rent": "2300", "neighborhood": "Test",
        "walk_minutes": "12", "scores": {c: 7 for c in data_cats(service)},
        "source_urls": ["https://example.com/a"], "risk_flags": [],
    })
    assert status == 201
    view = data["candidate"]
    assert view["base_rent"] == 2300 and view["commute_estimate"]["walk_minutes"] == 12
    assert view["score_summary"]["weighted_score"] is not None
    stored = service.store.get(view["id"])
    assert stored["facts"]["base_rent"]["source"] == "https://example.com/a"
    assert stored["judgments"][data_cats(service)[0]]["by"] == "ui:dashboard"

    # stale version -> 409
    status, _, _ = req(server, "POST", "/api/candidates",
                       {"id": view["id"], "version": 0, "status": "watchlist"})
    assert status == 409
    status, data, _ = req(server, "POST", "/api/candidates",
                          {"id": view["id"], "version": view["version"], "status": "watchlist"})
    assert status == 201 and data["candidate"]["status"] == "watchlist"
    # unchanged facts keep provenance
    assert service.store.get(view["id"])["facts"]["base_rent"]["checked"] == stored["facts"]["base_rent"]["checked"]

    assert req(server, "DELETE", "/api/candidates?id=base")[0] == 400
    status, data, _ = req(server, "DELETE", f"/api/candidates?id={view['id']}")
    assert status == 200 and service.store.get(view["id"])["status"] == "archived"
    actors = {e["actor"] for e in service.store.events()}
    assert "ui:dashboard" in actors


def test_bad_id_rejected(server):
    status, _, _ = req(server, "POST", "/api/candidates", {"id": "../evil", "name": "x"})
    assert status == 400


def test_read_only_example_blocks_writes(tmp_path, profile_dict):
    from housing_agent.profile import resolve_profile_dir
    from housing_agent.service import HousingService
    ex = tmp_path / "profile.example"
    ex.mkdir()
    (ex / "profile.json").write_text(json.dumps(profile_dict))
    svc = HousingService(resolve_profile_dir(None, repo_root=tmp_path, environ={}))
    srv = make_server(svc, "127.0.0.1", 0, token=TOKEN)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        status, data, _ = req(srv, "GET", "/api/state")
        assert data["read_only"] is True
        assert req(srv, "POST", "/api/candidates", {"name": "x"})[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()


def test_to_view_shape(service):
    service.put_candidate(make_candidate("v"), actor="test")
    view = to_view(service.get_candidate("v"), service.profile)
    for key in ("score_summary", "commute_estimate", "all_in_monthly_cost", "stale_facts", "version"):
        assert key in view


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "example.com"])
def test_nonloopback_bind_refused(service, host, monkeypatch):
    def no_bind(*args, **kwargs):
        pytest.fail("non-loopback must be refused before any socket bind")
    monkeypatch.setattr("housing_agent.web.ThreadingHTTPServer", no_bind)
    with pytest.raises(ValueError, match="loopback"):
        make_server(service, host, 0)


@pytest.mark.parametrize("authority", ["localhost:1", "127.0.0.1", "[::1]:1", "localhost:garbage"])
def test_host_requires_exact_port(server, authority):
    assert req(server, "GET", "/", headers={"Host": authority})[0] == 403


@pytest.mark.parametrize("suffix", ["/", "/path", "?query", "#fragment"])
def test_origin_is_exact_not_url_with_path(server, suffix):
    port = server.server_address[1]
    assert req(server, "GET", "/api/state", headers={"Origin": f"http://127.0.0.1:{port}{suffix}"})[0] == 403


def test_localhost_alias_with_correct_origin(server):
    authority = f"localhost:{server.server_address[1]}"
    assert req(server, "GET", "/api/state", headers={"Host": authority, "Origin": f"http://{authority}"})[0] == 200


def test_wrong_origin_port_and_scheme(server):
    port = server.server_address[1]
    for origin in ("http://localhost:1", f"https://127.0.0.1:{port}", f"http://user@127.0.0.1:{port}", "null"):
        assert req(server, "GET", "/api/state", headers={"Origin": origin})[0] == 403


def test_origin_must_match_request_authority(server):
    port = server.server_address[1]
    assert req(server, "GET", "/api/state", headers={"Origin": f"http://localhost:{port}"})[0] == 403


def test_non_ascii_csrf_token_fails_closed(server):
    assert req(server, "GET", "/api/state", headers={"X-Housing-Token": "é"})[0] == 403


def test_duplicate_authority_headers_fail_closed(server):
    for duplicate in ("Host", "Origin", "X-Housing-Token"):
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        conn.putrequest("GET", "/api/state", skip_host=True)
        values = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}", "X-Housing-Token": TOKEN}
        for name, value in values.items():
            conn.putheader(name, value)
            if name == duplicate:
                conn.putheader(name, value)
        conn.endheaders()
        response = conn.getresponse()
        assert response.status == 403
        response.read()
        conn.close()


def test_csp_local_executable_resources_only(server):
    _, _, resp = req(server, "GET", "/")
    csp = resp.getheader("Content-Security-Policy")
    directives = dict(part.strip().split(" ", 1) for part in csp.split(";"))
    assert directives["script-src"] == "'self'"
    assert "https:" not in directives["style-src"]
    assert directives["font-src"] == "'self'"


def test_custom_static_directory(service, tmp_path):
    (tmp_path / "index.html").write_text("custom __HOUSING_TOKEN__")
    srv = make_server(service, "127.0.0.1", 0, token=TOKEN, static_dir=tmp_path)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        assert req(srv, "GET", "/", token=False)[1] == f"custom {TOKEN}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join()


def data_cats(service):
    return [c["id"] for c in service.profile["categories"]]
