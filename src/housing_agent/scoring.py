"""Pure scoring: evaluate(candidate, profile) -> evaluation dict. Nothing here is persisted."""

from __future__ import annotations

from datetime import date

from . import costs as cost_mod
from .models import TOTAL_SCORE, finite_number
from .profile import category_ids


def is_eligible(candidate: dict) -> bool:
    """One ranking gate for service and adapters; expects an evaluated v1 candidate."""
    evaluation = candidate.get("evaluation") or {}
    return (not candidate.get("is_baseline")
            and candidate.get("status") not in ("baseline", "rejected", "archived")
            and not evaluation.get("rejected", False)
            and finite_number(evaluation.get("final_score")))


def _clamp(value: float) -> float:
    return round(min(10.0, max(0.0, value)), 2)


def weighted(scores: dict[str, float], profile: dict) -> float:
    return round(sum(scores[c["id"]] * c["weight"] for c in profile["categories"]), 2)


def judgment_scores(candidate: dict, profile: dict) -> tuple[dict[str, float], list[str], list[str]]:
    """Return (scores, missing_categories, problems)."""
    scores: dict[str, float] = {}
    missing: list[str] = []
    problems: list[str] = []
    judgments = candidate.get("judgments") or {}
    for cid in category_ids(profile):
        j = judgments.get(cid)
        if not isinstance(j, dict) or j.get("score") is None:
            missing.append(cid)
            continue
        try:
            value = float(j["score"])
        except (TypeError, ValueError):
            problems.append(f"{cid}: score is not a number")
            continue
        if not 0 <= value <= 10:
            problems.append(f"{cid}: score must be between 0 and 10")
            continue
        scores[cid] = value
    return scores, missing, problems


def flag_ids(candidate: dict) -> set[str]:
    out: set[str] = set()
    for flag in candidate.get("risk_flags") or []:
        out.add(flag["id"] if isinstance(flag, dict) else str(flag))
    return out


def apply_caps(scores: dict[str, float], flags: set[str], profile: dict):
    capped = dict(scores)
    notes: list[str] = []
    total_cap: float | None = None
    unknown: list[str] = []
    caps = profile.get("risk_caps") or {}
    rejects = set(profile.get("reject_flags") or [])
    for flag in sorted(flags):
        rule = caps.get(flag)
        if rule is None:
            if flag not in rejects:
                unknown.append(flag)
            continue
        target, cap = rule["target"], float(rule["cap"])
        if target == TOTAL_SCORE:
            total_cap = cap if total_cap is None else min(total_cap, cap)
            notes.append(f"{flag}: total score capped at {cap:g}")
        elif target in capped and capped[target] > cap:
            notes.append(f"{flag}: {target} capped from {capped[target]:g} to {cap:g}")
            capped[target] = cap
    return capped, notes, total_cap, unknown


def anchor_adjustments(candidate: dict, profile: dict) -> list[dict]:
    fact = (candidate.get("facts") or {}).get("anchor_minutes") or {}
    minutes_by_anchor = fact.get("value") or {}
    results = []
    for anchor in profile.get("anchors") or []:
        raw = minutes_by_anchor.get(anchor["id"]) or {}
        modes = {}
        for mode in anchor.get("modes") or []:
            value = cost_mod.number(raw.get(mode))
            if value is not None and value >= 0:
                modes[mode] = value
        if not modes:
            results.append({"anchor": anchor["id"], "best_mode": None, "best_minutes": None,
                            "delta": 0.0, "note": f"No {anchor['label']} estimate; no adjustment."})
            continue
        best_mode = min(modes, key=lambda m: modes[m])
        best = modes[best_mode]
        delta = 0.0
        for step in anchor["adjustments"]:
            if step["max_minutes"] is None or best <= step["max_minutes"]:
                delta = float(step["delta"])
                break
        results.append({"anchor": anchor["id"], "best_mode": best_mode, "best_minutes": best,
                        "delta": delta,
                        "note": f"{anchor['label']}: {best:g} min by {best_mode}, adjustment {delta:+.2f}"})
    return results


def evaluate(candidate: dict, profile: dict, today: date | None = None) -> dict:
    """Order: validate -> weighted -> category caps -> anchor adjustments -> clamp -> total cap -> rejects.

    (Anchor adjustment precedes the total cap to preserve legacy behaviour.)
    """
    scores, missing, problems = judgment_scores(candidate, profile)
    flags = flag_ids(candidate)
    rejects = sorted(flags & set(profile.get("reject_flags") or []))
    money = cost_mod.costs(candidate)
    stale = cost_mod.stale_facts(candidate, profile.get("staleness_days") or {}, today)

    result = {
        "scores": scores,
        "missing_categories": missing,
        "problems": problems,
        "raw_weighted_score": None,
        "capped_scores": None,
        "caps_applied": [],
        "total_cap": None,
        "anchor_adjustments": [],
        "final_score": None,
        "rejected": bool(rejects),
        "reject_reasons": rejects,
        "unknown_flags": [],
        "costs": money,
        "budget_band": cost_mod.budget_band(money["all_in_monthly"], profile.get("budget") or {}),
        "stale_facts": stale,
        "confidence": "low",
    }

    anchors = anchor_adjustments(candidate, profile)
    result["anchor_adjustments"] = anchors
    if missing or problems:
        _, _, _, result["unknown_flags"] = apply_caps({}, flags, profile)
        return result

    capped, notes, total_cap, unknown = apply_caps(scores, flags, profile)
    final = _clamp(weighted(capped, profile) + sum(a["delta"] for a in anchors))
    if total_cap is not None:
        final = min(final, total_cap)

    result.update(
        raw_weighted_score=weighted(scores, profile),
        capped_scores=capped,
        caps_applied=notes,
        total_cap=total_cap,
        final_score=round(final, 2),
        unknown_flags=unknown,
        confidence=_confidence(candidate, stale),
    )
    return result


def _confidence(candidate: dict, stale: list[str]) -> str:
    # A reference string is not itself evidence. Only locally present fact records
    # with usable provenance can support confidence; no network claims are verified.
    from urllib.parse import urlsplit

    facts = candidate.get("facts") or {}
    judgments = list((candidate.get("judgments") or {}).values())
    if stale or not judgments:
        return "low"

    def supported(ref):
        if not isinstance(ref, str) or not ref.startswith("facts."):
            return False
        fact = facts.get(ref[len("facts."):])
        if not isinstance(fact, dict) or fact.get("value") is None or fact.get("confidence") != "high":
            return False
        source = fact.get("source")
        if not isinstance(source, str):
            return False
        try:
            url = urlsplit(source)
            date.fromisoformat(fact.get("checked", "")[:10])
            return url.scheme in ("http", "https") and bool(url.hostname)
        except (ValueError, TypeError):
            return False

    supported_count = 0
    for judgment in judgments:
        if not isinstance(judgment, dict) or not isinstance(judgment.get("rationale"), str) or not judgment["rationale"].strip():
            continue
        evidence = judgment.get("evidence")
        if isinstance(evidence, list) and evidence and all(supported(ref) for ref in evidence):
            supported_count += 1
    if not supported_count:
        return "low"
    return "high" if supported_count == len(judgments) else "medium"
