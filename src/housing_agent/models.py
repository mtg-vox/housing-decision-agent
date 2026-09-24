"""Plain data helpers and constants shared by the core.

Candidates and profiles are plain dicts that match JSON on disk; this module
holds the vocabulary and small validators, not heavy classes.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

CANDIDATE_STATUSES = (
    "baseline", "active", "watchlist", "pending", "tour", "negotiate", "rejected", "archived",
)
STATUS_ORDER = {status: index for index, status in enumerate(CANDIDATE_STATUSES)}

CONFIDENCE_LEVELS = ("high", "medium", "low", "legacy", "user")

ACTORS_PREFIXES = ("ui", "cli", "llm", "legacy", "test")

TOTAL_SCORE = "total_score"


class ValidationError(ValueError):
    """Raised with every problem found, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def validate_actor(actor: str) -> str:
    # Attribution is not authentication and must not imply roles or permissions.
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 200 or any(ord(c) < 32 for c in actor):
        raise ValidationError(["actor must be a non-empty attribution string (max 200 characters)"])
    return str(actor)


def is_llm(actor: str) -> bool:
    """Compatibility helper for display only; never an authorization check."""
    return str(actor).split(":", 1)[0] == "llm"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError([message])


def finite_number(value) -> bool:
    import math
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def valid_id(value) -> bool:
    import re
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value) is not None


def validate_json(value, path: str = "data") -> None:
    """Reject non-JSON types and NaN/Infinity, including inside extension fields."""
    if isinstance(value, dict):
        for key, item in value.items():
            require(isinstance(key, str), f"{path}: keys must be strings")
            validate_json(item, f"{path}.{key}")
    elif isinstance(value, list):
        for item in value:
            validate_json(item, path)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        require(finite_number(value), f"{path}: number must be finite")
    else:
        require(value is None or isinstance(value, (str, bool)), f"{path}: invalid JSON value")


def validate_version(value, label: str = "expected_version") -> None:
    require(value is None or (type(value) is int and value >= 0), f"{label} must be a non-negative integer")
