"""Cost helpers. Values come from candidate facts."""

from __future__ import annotations

from datetime import date
import math


def fact_value(candidate: dict, name: str):
    fact = (candidate.get("facts") or {}).get(name)
    return fact.get("value") if isinstance(fact, dict) else None


def number(value) -> float | None:
    if value in ("", None) or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def all_in_monthly(candidate: dict) -> float | None:
    explicit = number(fact_value(candidate, "all_in_monthly_cost"))
    if explicit is not None:
        return explicit
    base = number(fact_value(candidate, "base_rent"))
    if base is None:
        return None
    extras = [number(fact_value(candidate, k)) for k in ("parking_monthly", "required_monthly_fees")]
    return round(base + sum(x for x in extras if x is not None), 2)


def costs(candidate: dict) -> dict:
    monthly = all_in_monthly(candidate)
    return {
        "all_in_monthly": monthly,
        "move_in": number(fact_value(candidate, "move_in_cost")),
        "annualized": round(monthly * 12, 2) if monthly is not None else None,
    }


def budget_band(monthly: float | None, budget: dict) -> str:
    if monthly is None:
        return "unknown"
    if budget.get("hard_ceiling") is not None and monthly > budget["hard_ceiling"]:
        return "over_hard"
    if budget.get("soft_ceiling") is not None and monthly > budget["soft_ceiling"]:
        return "over_soft"
    lo, hi = budget.get("ideal_min"), budget.get("ideal_max")
    if lo is not None and hi is not None and lo <= monthly <= hi:
        return "ideal"
    return "within"


def stale_facts(candidate: dict, staleness_days: dict, today: date | None = None) -> list[str]:
    today = today or date.today()
    stale: list[str] = []
    for name, fact in (candidate.get("facts") or {}).items():
        if not isinstance(fact, dict):
            continue
        limit = staleness_days.get(fact.get("kind"))
        checked = fact.get("checked")
        if limit is None:
            continue
        try:
            age = (today - date.fromisoformat(str(checked)[:10])).days
        except ValueError:
            stale.append(name)
            continue
        if age > limit:
            stale.append(name)
    return sorted(stale)
