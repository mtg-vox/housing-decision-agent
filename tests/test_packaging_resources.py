"""Runtime assets must be usable in both source checkout and installed wheel."""
from pathlib import Path

import pytest


def test_checkout_assets_resolve():
    from housing_agent.resources import asset_dir
    assert (asset_dir("static") / "index.html").is_file()
    assert (asset_dir("profile.example") / "profile.json").is_file()
    assert (asset_dir("regions") / "miami" / "README.md").is_file()


def test_unknown_resource_rejected():
    from housing_agent.resources import asset_dir
    with pytest.raises(ValueError):
        asset_dir("../../profile.local")


def test_packaged_assets_preferred(tmp_path, monkeypatch):
    from housing_agent import resources
    package = tmp_path / "housing_agent"
    static = package / "_assets" / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("fixture")
    monkeypatch.setattr(resources, "__file__", str(package / "resources.py"))
    assert resources.asset_dir("static") == static


def test_default_demo_works_outside_source_checkout(tmp_path, monkeypatch):
    from housing_agent.profile import resolve_profile_dir
    from housing_agent.resources import asset_dir
    monkeypatch.chdir(tmp_path)
    location = resolve_profile_dir(environ={})
    assert location.path == asset_dir("profile.example")
    assert location.read_only
