"""Optional real-Chrome tests: ephemeral loopback, fictional temp profiles only.

Run with pytest + playwright installed and Chrome on PATH. External requests
are intercepted/aborted, including opt-in OSM tiles; no public network needed.
"""
import json
import shutil
import threading

import pytest

from housing_agent.profile import ProfileLocation
from housing_agent.service import HousingService
from housing_agent.web import make_server
from tests.conftest import make_candidate

playwright = pytest.importorskip("playwright.sync_api")
CHROME = shutil.which("google-chrome") or shutil.which("chromium")
pytestmark = pytest.mark.skipif(not CHROME, reason="Chrome/Chromium required")


@pytest.fixture
def browser_page():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True,
                                    args=["--no-sandbox", "--disable-background-networking"])
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(5000)
        yield page
        context.close()
        browser.close()


@pytest.mark.parametrize("read_only", [False, True])
def test_real_browser_privacy_and_rendering(browser_page, service, profile_dir, read_only):
    profile = service.profile
    profile["categories"][0]["label"] = '<img src="https://invalid.test/leak" onerror="window.pwned=1">'
    profile["display"] = {"move_window": {"start": "2028-07-01", "end": "2028-08-01"}}
    (profile_dir / "profile.json").write_text(json.dumps(profile))
    service.reload()
    c = make_candidate("fictional")
    c["facts"]["latitude"] = {"value": 0.5}
    c["facts"]["longitude"] = {"value": 0.5}
    c["facts"]["unit_options"] = {"value": [{"label": "3BR", "availability": "Available 2027-01-15", "rent": 2200}]}
    service.put_candidate(c, actor="test")
    service.propose_profile_change({"anchors": []}, actor="ui:test", reason="Review every removed anchor")
    if read_only:
        service = HousingService(ProfileLocation(profile_dir, "example"), today=service.today)
    before_files = {p.relative_to(profile_dir): p.read_bytes() for p in profile_dir.rglob("*") if p.is_file()}
    server = make_server(service, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser_page
    base = f"http://127.0.0.1:{server.server_address[1]}"
    external, errors, failed_local = [], [], []
    def route(request_route):
        if request_route.request.url.startswith(base + "/"):
            request_route.continue_()
        else:
            external.append(request_route.request.url)
            request_route.abort()
    page.route("**/*", route)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("response", lambda response: failed_local.append((response.url, response.status))
            if response.url.startswith(base + "/") and response.status >= 400 else None)
    try:
        page.goto(base, wait_until="networkidle")
        assert page.locator(".candidate-title").inner_text() == "Fictional"
        assert page.locator("#scoreInputs label").first.text_content() == profile["categories"][0]["label"]
        assert page.locator("#scoreInputs img").count() == 0
        assert "Available 2027-01-15" in page.locator("body").inner_text()
        assert external == []
        assert errors == []
        assert failed_local == []
        assert page.evaluate("L.version") == "1.9.4"
        assert page.evaluate("typeof L.markerClusterGroup") == "function"
        page.locator("#candidateSearch").fill("fictional")
        assert page.evaluate("Object.keys(localStorage)") == []
        page.locator("#prefsWrap").evaluate("el => el.open=true")
        assert "max_minutes" in page.locator(".proposal-diff").inner_text()
        assert "Office" in page.locator(".proposal-diff").inner_text()
        assert "[]" in page.locator(".proposal-diff").inner_text()
        if read_only:
            assert page.locator("#editSelectedBtn").is_disabled()
            assert page.locator("#candidateForm :enabled").count() == 0
            assert page.locator("[data-prop-apply]").is_disabled()
            assert page.locator("[data-row-status]").count() == 0
        else:
            assert page.locator("[data-prop-apply]").is_enabled()
        page.locator("#externalMapsToggle").check()
        page.wait_for_function("document.querySelector('.leaflet-tile-container') !== null")
        page.wait_for_timeout(200)
        assert external and all(".tile.openstreetmap.org/" in url for url in external)
        page.locator("#externalMapsToggle").uncheck()
        assert page.locator(".leaflet-tile-container").count() == 0
        page.reload(wait_until="networkidle")
        assert not page.locator("#externalMapsToggle").is_checked()
        assert page.locator("#candidateSearch").input_value() == ""
        assert errors == []
        after_files = {p.relative_to(profile_dir): p.read_bytes() for p in profile_dir.rglob("*") if p.is_file()}
        assert after_files == before_files
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
