"""Profile discovery, loading, and validation."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .models import (SCHEMA_VERSION, TOTAL_SCORE, ValidationError, require,
                     finite_number, valid_id, validate_json, validate_version)

ENV_VAR = "HOUSING_PROFILE_DIR"
LOCAL_DIRNAME = "profile.local"
EXAMPLE_DIRNAME = "profile.example"
PROFILE_FILE = "profile.json"


@dataclass(frozen=True)
class ProfileLocation:
    path: Path
    source: str  # "argument" | "env" | "local" | "example"

    def __post_init__(self):
        object.__setattr__(self, "path", Path(self.path).expanduser().resolve())

    @property
    def read_only(self) -> bool:
        # Attribution of discovery is not a write permission. Resolve symlink aliases.
        canonical = self.path.resolve()
        return (self.source == "example" or canonical.name == EXAMPLE_DIRNAME
                or canonical == bundled_example_dir().resolve())


def bundled_example_dir() -> Path:
    """Canonical bundled sample (source checkout or installed wheel)."""
    from .resources import asset_dir
    return asset_dir(EXAMPLE_DIRNAME)


def resolve_profile_dir(
    explicit: str | os.PathLike | None = None,
    repo_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ProfileLocation:
    """Resolve the profile directory: argument > env > ./profile.local > ./profile.example."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    repo_root = Path(repo_root) if repo_root else Path.cwd()

    if explicit:
        return _require(Path(explicit).expanduser(), "argument")
    if env.get(ENV_VAR):
        return _require(Path(env[ENV_VAR]).expanduser(), "env")
    local = repo_root / LOCAL_DIRNAME
    if local.exists() or local.is_symlink():
        return _require(local, "local")
    example = repo_root / EXAMPLE_DIRNAME
    if (example / PROFILE_FILE).is_file():
        return ProfileLocation(example.resolve(), "example")
    bundled = bundled_example_dir()
    if (bundled / PROFILE_FILE).is_file():
        return ProfileLocation(bundled.resolve(), "example")
    raise FileNotFoundError(
        f"No profile found. Pass --profile, set {ENV_VAR}, or create ./{LOCAL_DIRNAME}/{PROFILE_FILE}."
    )


def _require(path: Path, source: str) -> ProfileLocation:
    if not (path / PROFILE_FILE).is_file():
        raise FileNotFoundError(f"{source} profile directory has no {PROFILE_FILE}: {path}")
    return ProfileLocation(path.resolve(), source)


def load_profile(location: ProfileLocation) -> dict:
    try:
        data = json.loads((location.path / PROFILE_FILE).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValidationError([f"invalid profile JSON: {exc}"]) from exc
    validate_profile(data)
    data.setdefault("revision", 0)
    return data


def category_ids(profile: dict) -> list[str]:
    return [category["id"] for category in profile["categories"]]


def validate_profile(profile: dict) -> None:
    require(isinstance(profile, dict), "profile must be an object")
    validate_json(profile, "profile")
    problems = []

    def check(condition, message):
        if not condition:
            problems.append(message)
    require(type(profile.get("schema_version")) is int and profile["schema_version"] == SCHEMA_VERSION,
            f"schema_version must be {SCHEMA_VERSION}")
    validate_version(profile.get("revision", 0), "revision")
    require(profile.get("revision", 0) is not None, "revision must be an integer")
    for key in ("name", "region", "currency"):
        if key in profile:
            require(isinstance(profile[key], str), f"{key} must be a string")

    def obj(value, label):
        require(isinstance(value, dict), f"{label} must be an object")
        return value

    def strings(value, label, ids=False):
        require(isinstance(value, list), f"{label} must be a list")
        require(all(valid_id(v) if ids else isinstance(v, str) and bool(v.strip()) for v in value),
                f"{label} must contain {'ids' if ids else 'strings'}")
        return value

    def objects(key):
        rows = profile.get(key, [])
        require(isinstance(rows, list), f"{key} must be a list")
        seen = set()
        for row in rows:
            obj(row, key)
            require(valid_id(row.get("id")), f"{key}: valid id required")
            require(row["id"] not in seen, f"{key}: ids must be unique")
            seen.add(row["id"])
            if "label" in row:
                require(isinstance(row["label"], str), f"{key}: label must be a string")
        return rows

    cats = objects("categories")
    require(bool(cats), "categories must be a non-empty list")
    for c in cats:
        require(finite_number(c.get("weight")) and c["weight"] >= 0,
                f"category {c['id']}: weight must be a non-negative number")
    total = sum(c["weight"] for c in cats)
    check(abs(total - 1.0) <= .001, f"category weights must sum to 1.0 (got {total:.3f})")
    targets = {c["id"] for c in cats} | {TOTAL_SCORE}
    for flag, rule in obj(profile.get("risk_caps", {}), "risk_caps").items():
        require(valid_id(flag), "risk_caps: invalid flag id")
        obj(rule, f"risk_caps.{flag}")
        check(isinstance(rule.get("target"), str) and rule["target"] in targets,
                f"risk_caps.{flag}: unknown target")
        check(finite_number(rule.get("cap")) and 0 <= rule["cap"] <= 10,
                f"risk_caps.{flag}: cap must be between 0 and 10")
    strings(profile.get("reject_flags", []), "reject_flags", ids=True)
    for anchor in objects("anchors"):
        require(isinstance(anchor.get("label"), str), "anchor label required")
        strings(anchor.get("modes", []), "anchor modes", ids=True)
        if "visits_per_week" in anchor:
            require(finite_number(anchor["visits_per_week"]) and anchor["visits_per_week"] >= 0,
                    "anchor visits_per_week must be non-negative")
        steps = anchor.get("adjustments")
        require(isinstance(steps, list) and bool(steps), "anchor adjustments required")
        limits = []
        for index, step in enumerate(steps):
            obj(step, "anchor adjustment")
            require("max_minutes" in step, "adjustment max_minutes required")
            limit = step["max_minutes"]
            require((limit is None and index == len(steps) - 1) or
                    (index < len(steps) - 1 and finite_number(limit) and limit >= 0),
                    "only last adjustment must have max_minutes null; others must be non-negative")
            require(finite_number(step.get("delta")), "adjustment delta must be finite")
            if limit is not None:
                limits.append(limit)
        require(limits == sorted(limits), "anchor adjustments must be sorted by max_minutes")
    budget = obj(profile.get("budget", {}), "budget")
    ordered = [budget.get(k) for k in ("ideal_min", "ideal_max", "soft_ceiling", "hard_ceiling")]
    present = [v for v in ordered if v is not None]
    require(all(finite_number(v) and v >= 0 for v in present), "budget values must be non-negative numbers")
    check(present == sorted(present), "budget must satisfy ideal_min <= ideal_max <= soft_ceiling <= hard_ceiling")
    unit = obj(profile.get("unit", {}), "unit")
    if "allow_studio" in unit:
        require(type(unit["allow_studio"]) is bool, "unit.allow_studio must be boolean")
    if "min_bedrooms" in unit:
        require(finite_number(unit["min_bedrooms"]) and unit["min_bedrooms"] >= 0,
                "unit.min_bedrooms must be non-negative")
    for key, value in obj(profile.get("staleness_days", {}), "staleness_days").items():
        require(valid_id(key) and finite_number(value) and value >= 0, "staleness_days must be non-negative numbers")
    for area in objects("areas"):
        strings(area.get("match", []), "area match")
    objects("note_fields")
    for key, value in obj(profile.get("flag_labels", {}), "flag_labels").items():
        require(valid_id(key) and isinstance(value, str), "flag_labels must map ids to strings")
    display = obj(profile.get("display", {}), "display")
    if display.get("map_center") is not None:
        mc = display["map_center"]
        require(isinstance(mc, list) and len(mc) == 2 and all(finite_number(x) for x in mc),
                "display.map_center must be [lat, lng]")
        require(-90 <= mc[0] <= 90 and -180 <= mc[1] <= 180, "display.map_center out of range")
    if "move_window" in display:
        obj(display["move_window"], "display.move_window")
    if "facts" in display:
        require(isinstance(display["facts"], list), "display.facts must be a list")
        for fact in display["facts"]:
            obj(fact, "display.fact")
            require(isinstance(fact.get("label"), str) and isinstance(fact.get("value"), str),
                    "display facts require string label and value")
    if problems:
        raise ValidationError(problems)
