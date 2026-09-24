"""First-run setup: turn a few plain answers into a valid private profile.

Used by `housing init` (interactive prompts or `--answers JSON` for agents).
Only `categories` and `schema_version` are strictly required by the engine;
every other answer is optional and can be filled in later.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import SCHEMA_VERSION, ValidationError
from .profile import PROFILE_FILE, validate_profile

DEFAULT_CATEGORIES = [
    {"id": "cost_value", "label": "Cost & value", "weight": 0.2},
    {"id": "daily_lifestyle", "label": "Daily lifestyle", "weight": 0.2},
    {"id": "social_life", "label": "Social life", "weight": 0.15},
    {"id": "work_and_focus", "label": "Work & focus", "weight": 0.15},
    {"id": "space_and_quiet", "label": "Space & quiet", "weight": 0.15},
    {"id": "friction_risk", "label": "Friction & risk", "weight": 0.15},
]
DEFAULT_STALENESS = {"pricing": 30, "availability": 14, "flood": 365, "construction": 90, "commute": 180}
BUDGET_KEYS = ("ideal_min", "ideal_max", "soft_ceiling", "hard_ceiling")
MODES = ("walk", "bike", "transit", "drive")

# Single source for docs/SETUP.md, the interactive prompts and agent onboarding.
QUESTIONS = [
    {"key": "name", "required": False, "ask": "A name for this search (e.g. 'Spring move')"},
    {"key": "region", "required": False, "ask": "City or region pack id (e.g. 'miami'); blank if none"},
    {"key": "budget", "required": False, "recommended": True,
     "ask": "Monthly all-in budget: ideal min, ideal max, soft ceiling, hard ceiling"},
    {"key": "unit", "required": False, "ask": "Minimum bedrooms, and whether studios are acceptable"},
    {"key": "anchors", "required": False, "recommended": True,
     "ask": "Places you travel to often: name, visits/week, travel modes, ideal and max minutes"},
    {"key": "weights", "required": False,
     "ask": "How much each category matters (defaults are balanced)"},
    {"key": "reject_flags", "required": False, "ask": "Deal-breakers that rule a place out (e.g. studio_unit)"},
]


def _slug(text: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(text)).strip("_")
    return "_".join(p for p in out.split("_") if p)[:40] or "place"


def _anchor(raw: dict) -> dict:
    label = str(raw.get("label") or "").strip()
    if not label:
        raise ValidationError(["each anchor needs a label"])
    modes = [m for m in (raw.get("modes") or ["walk", "drive"]) if m in MODES]
    if not modes:
        raise ValidationError([f"anchor '{label}': modes must be from {', '.join(MODES)}"])
    ideal = float(raw.get("ideal_minutes", 15))
    worst = float(raw.get("max_minutes", max(ideal + 15, 30)))
    if not 0 <= ideal < worst:
        raise ValidationError([f"anchor '{label}': need 0 <= ideal_minutes < max_minutes"])
    anchor = {"id": _slug(raw.get("id") or label), "label": label, "modes": modes,
              "adjustments": [{"max_minutes": ideal, "delta": 0.3},
                              {"max_minutes": worst, "delta": 0.0},
                              {"max_minutes": None, "delta": -0.4}]}
    if raw.get("visits_per_week") is not None:
        anchor["visits_per_week"] = float(raw["visits_per_week"])
    return anchor


def build_profile(answers: dict) -> dict:
    """Return a validated profile dict from setup answers. Unknown keys are rejected."""
    if not isinstance(answers, dict):
        raise ValidationError(["answers must be a JSON object"])
    allowed = {q["key"] for q in QUESTIONS} | {"currency"}
    unknown = sorted(set(answers) - allowed)
    if unknown:
        raise ValidationError([f"unknown setup answer(s): {', '.join(unknown)}"])

    categories = [dict(c) for c in DEFAULT_CATEGORIES]
    weights = answers.get("weights") or {}
    if weights:
        ids = {c["id"] for c in categories}
        bad = sorted(set(weights) - ids)
        if bad:
            raise ValidationError([f"unknown categories in weights: {', '.join(bad)}; valid: {', '.join(sorted(ids))}"])
        raw = {c["id"]: float(weights.get(c["id"], c["weight"])) for c in categories}
        total = sum(raw.values())
        if total <= 0 or any(v < 0 for v in raw.values()):
            raise ValidationError(["weights must be non-negative and not all zero"])
        for c in categories:
            c["weight"] = round(raw[c["id"]] / total, 4)
        categories[0]["weight"] = round(1 - sum(c["weight"] for c in categories[1:]), 4)

    profile = {
        "schema_version": SCHEMA_VERSION,
        "name": str(answers.get("name") or "My housing search"),
        "currency": str(answers.get("currency") or "USD"),
        "categories": categories,
        "staleness_days": dict(DEFAULT_STALENESS),
        "reject_flags": list(answers.get("reject_flags") or []),
        "risk_caps": {},
    }
    if answers.get("region"):
        profile["region"] = str(answers["region"])
    budget = {k: float(v) for k, v in (answers.get("budget") or {}).items() if v not in (None, "")}
    if set(budget) - set(BUDGET_KEYS):
        raise ValidationError([f"budget keys must be from {', '.join(BUDGET_KEYS)}"])
    if budget:
        profile["budget"] = {**budget, "basis": "all_in_monthly"}
    unit = answers.get("unit") or {}
    if unit:
        profile["unit"] = {k: unit[k] for k in ("min_bedrooms", "allow_studio") if k in unit}
        if profile["unit"].get("allow_studio") is False and "studio_unit" not in profile["reject_flags"]:
            profile["reject_flags"].append("studio_unit")
    anchors = [_anchor(a) for a in answers.get("anchors") or []]
    if anchors:
        profile["anchors"] = anchors
    validate_profile(profile)
    return profile


def write_new_profile(target: Path, profile: dict) -> Path:
    """Create a new profile folder. Never overwrites an existing profile."""
    target = Path(target)
    if (target / PROFILE_FILE).exists():
        raise FileExistsError(f"a profile already exists at {target}; edit it with `housing preferences`")
    if target.is_symlink() and not target.exists():
        raise FileNotFoundError(f"{target} is a broken link; fix or remove it first")
    (target / "candidates").mkdir(parents=True, exist_ok=True)
    (target / PROFILE_FILE).write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    return target


def prompt_answers(ask=input, say=print) -> dict:
    """Interactive prompts; every question can be skipped with Enter."""
    say("Housing profile setup. Press Enter to skip any question; you can change everything later.\n")
    a: dict = {}
    a["name"] = ask("Name for this search [My housing search]: ").strip() or None
    a["region"] = ask("City/region pack id (blank if none): ").strip() or None
    say("Monthly all-in budget (rent + required fees + parking):")
    budget = {k: ask(f"  {k.replace('_', ' ')}: ").strip() for k in BUDGET_KEYS}
    a["budget"] = {k: v for k, v in budget.items() if v}
    beds = ask("Minimum bedrooms (e.g. 1): ").strip()
    studio = ask("Are studios acceptable? [y/N]: ").strip().lower()
    a["unit"] = {**({"min_bedrooms": float(beds)} if beds else {}), "allow_studio": studio.startswith("y")}
    anchors = []
    while True:
        label = ask("Place you travel to often (e.g. Work, Gym); blank to finish: ").strip()
        if not label:
            break
        visits = ask(f"  visits per week to {label}: ").strip()
        modes = ask(f"  travel modes ({'/'.join(MODES)}, comma-separated) [walk,drive]: ").strip()
        ideal = ask("  ideal travel minutes [15]: ").strip()
        worst = ask("  longest acceptable minutes [30]: ").strip()
        anchors.append({"label": label, "visits_per_week": float(visits) if visits else None,
                        "modes": [m.strip() for m in modes.split(",")] if modes else None,
                        "ideal_minutes": float(ideal) if ideal else 15,
                        "max_minutes": float(worst) if worst else 30})
    a["anchors"] = anchors
    return {k: v for k, v in a.items() if v not in (None, {}, [])}
