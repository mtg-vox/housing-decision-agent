"""Locate public assets in a source checkout or an installed distribution.

Only allowlisted public folders are exposed. Personal profiles are resolved
separately at runtime and never bundled here.
"""
from pathlib import Path

_ASSET_PATHS = {"static": "app/static", "profile.example": "profile.example", "regions": "regions"}


def asset_dir(name: str) -> Path:
    if name not in _ASSET_PATHS:
        raise ValueError(f"Unknown public asset: {name}")
    package = Path(__file__).resolve().parent
    bundled = package / "_assets" / name
    if bundled.is_dir():
        return bundled
    source = package.parent.parent / _ASSET_PATHS[name]
    if source.is_dir():
        return source
    raise FileNotFoundError(f"Missing bundled asset {name}; reinstall the complete distribution")
