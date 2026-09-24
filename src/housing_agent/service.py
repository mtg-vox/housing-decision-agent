"""Service layer: the only API adapters (HTTP, CLI, future MCP) should call."""

from __future__ import annotations

import copy
import json
import secrets
from datetime import date
from pathlib import Path

from . import scoring
from .models import (CANDIDATE_STATUSES, SCHEMA_VERSION, STATUS_ORDER, ValidationError,
                     validate_actor, require, finite_number, valid_id, validate_json,
                     validate_version, CONFIDENCE_LEVELS)
from .profile import ProfileLocation, category_ids, load_profile, validate_profile
from .store import Store, slugify, utc_now, transactional, ConflictError, content_hash

FACT_FIELDS = ("value", "source", "checked", "confidence", "kind", "note")


class HousingService:
    def __init__(self, location: ProfileLocation, today: date | None = None):
        self.location = location
        self.store = Store(location.path, read_only=location.read_only)
        self.today = today
        load_profile(location)

    # --- reads ---------------------------------------------------------------
    @property
    def profile(self) -> dict:
        # No persistent cache: another process may have changed preferences.
        with self.store.transaction():
            return load_profile(self.location)

    def reload(self) -> None:
        load_profile(self.location)

    def evaluated(self, candidate: dict) -> dict:
        return {**candidate, "evaluation": scoring.evaluate(candidate, self.profile, self.today)}

    @transactional
    def list_candidates(self, status: str | None = None) -> list[dict]:
        items = [self.evaluated(c) for c in self.store.list() if status in (None, c.get("status"))]
        return sorted(items, key=_rank_key)

    @transactional
    def get_candidate(self, candidate_id: str) -> dict | None:
        candidate = self.store.get(candidate_id)
        return self.evaluated(candidate) if candidate else None

    @transactional
    def get_state(self) -> dict:
        candidates = self.list_candidates()
        ranked = [c for c in candidates if scoring.is_eligible(c)]
        ranked.sort(key=lambda c: -c["evaluation"]["final_score"])
        return {
            "profile": self.profile_summary(),
            "candidates": candidates,
            "ranking": [c["id"] for c in ranked],
            "stale_summary": {c["id"]: c["evaluation"]["stale_facts"]
                              for c in candidates if c["evaluation"]["stale_facts"]},
            "pending_proposals": [p for p in self.list_proposals() if p["state"] == "pending"],
            "read_only": self.location.read_only,
        }

    @transactional
    def profile_summary(self) -> dict:
        p = self.profile
        return {k: copy.deepcopy(p.get(k)) for k in
                ("revision", "name", "region", "currency", "budget", "unit", "categories", "anchors",
                 "risk_caps", "reject_flags", "staleness_days")}

    @transactional
    def compare(self, ids: list[str]) -> list[dict]:
        rows = []
        for cid in ids:
            c = self.get_candidate(cid)
            if c is None:
                raise ValidationError([f"unknown candidate '{cid}'"])
            ev = c["evaluation"]
            rows.append({"id": cid, "name": c.get("name"), "final_score": ev["final_score"],
                         "scores": ev["capped_scores"] or ev["scores"], "costs": ev["costs"],
                         "budget_band": ev["budget_band"], "rejected": ev["rejected"],
                         "stale_facts": ev["stale_facts"], "is_baseline": c.get("is_baseline", False)})
        return rows

    # --- writes --------------------------------------------------------------
    @transactional
    def put_candidate(self, payload: dict, actor: str, reason: str = "",
                      expected_version: int | None = None) -> dict:
        validate_actor(actor)
        self.store._guard()
        require(isinstance(payload, dict), "candidate must be an object")
        validate_json(payload, "candidate")
        if "id" in payload:
            self.store._path(payload["id"])
        existing = self.store.get(payload["id"]) if payload.get("id") else None
        _validate_candidate_patch(payload, self.profile, existing)
        candidate = self._normalize(payload, existing)
        saved = self.store.put(candidate, expected_version=expected_version)
        self.store.append_event(actor, "update" if existing else "create", saved["id"], reason,
                                before=existing, after=saved)
        return self.evaluated(saved)

    @transactional
    def patch_facts(self, candidate_id: str, facts: dict, actor: str, reason: str = "",
                    expected_version: int | None = None) -> dict:
        validate_actor(actor)
        require(isinstance(facts, dict), "facts must be an object")
        existing = self._require(candidate_id)
        self.profile  # Validate fresh preferences before any write.
        updated = copy.deepcopy(existing)
        for name, fact in facts.items():
            updated.setdefault("facts", {})[name] = _clean_fact(name, fact)
        updated["updated_at"] = utc_now()
        saved = self.store.put(updated, expected_version=expected_version)
        self.store.append_event(actor, "patch_facts", candidate_id, reason, before=existing,
                                after=saved, extra={"fields": sorted(facts)})
        return self.evaluated(saved)

    @transactional
    def set_judgment(self, candidate_id: str, category: str, score: float, rationale: str,
                     actor: str, evidence: list[str] | None = None,
                     expected_version: int | None = None) -> dict:
        validate_actor(actor)
        if category not in category_ids(self.profile):
            raise ValidationError([f"unknown category '{category}'"])
        require(finite_number(score), "score must be a finite number")
        _validate_judgment({"score": score, "rationale": rationale,
                            "evidence": evidence if evidence is not None else []})
        score = float(score)
        existing = self._require(candidate_id)
        updated = copy.deepcopy(existing)
        updated.setdefault("judgments", {})[category] = {
            "score": score, "rationale": rationale, "evidence": list(evidence or []),
            "by": actor, "at": utc_now(),
        }
        updated["updated_at"] = utc_now()
        saved = self.store.put(updated, expected_version=expected_version)
        self.store.append_event(actor, "set_judgment", candidate_id, rationale, before=existing,
                                after=saved, extra={"category": category, "score": score})
        return self.evaluated(saved)

    @transactional
    def delete_candidate(self, candidate_id: str, actor: str, reason: str = "",
                         hard: bool = False) -> bool:
        validate_actor(actor)
        existing = self.store.get(candidate_id)
        if existing is None:
            return False
        if hard:
            self.store.delete(candidate_id)
            self.store.append_event(actor, "hard_delete", candidate_id, reason, before=existing)
            return True
        updated = {**existing, "status": "archived", "updated_at": utc_now()}
        saved = self.store.put(updated)
        self.store.append_event(actor, "archive", candidate_id, reason, before=existing, after=saved)
        return True

    @transactional
    def update_profile(self, patch: dict, actor: str, reason: str = "",
                       expected_version: int | None = None) -> dict:
        validate_actor(actor)
        self.store._guard()
        before = self.profile
        validate_version(expected_version)
        if expected_version is not None and expected_version != before["revision"]:
            raise ConflictError(f"profile: expected revision {expected_version}, found {before['revision']}")
        after = self._profile_patch(before, patch)
        after["revision"] = before["revision"] + 1
        self.store.write_profile(after)
        self.store.append_event(actor, "update_profile", "profile", reason, before=before, after=after)
        return after

    def _profile_patch(self, before: dict, patch: dict) -> dict:
        if not isinstance(patch, dict) or not patch:
            raise ValidationError(["patch must be a non-empty object"])
        if "revision" in patch or "schema_version" in patch:
            raise ValidationError(["revision and schema_version cannot be patched"])
        after = _merge(copy.deepcopy(before), patch)
        validate_profile(after)
        return after

    # --- profile proposals ---------------------------------------------------
    @transactional
    def propose_profile_change(self, patch: dict, actor: str, reason: str) -> str:
        validate_actor(actor)
        before = self.profile
        candidate_profile = self._profile_patch(before, patch)
        proposal_id = f"prop-{secrets.token_hex(4)}"
        self.store.append_event(actor, "propose_profile_change", proposal_id, reason,
                                before=before, after=candidate_profile,
                                extra={"patch": patch, "base_revision": before["revision"]})
        return proposal_id

    @transactional
    def list_proposals(self) -> list[dict]:
        proposals: dict[str, dict] = {}
        for e in self.store.events():
            if e["action"] == "propose_profile_change":
                proposals[e["target"]] = {"id": e["target"], "patch": e["patch"], "actor": e["actor"],
                                          "reason": e["reason"], "ts": e["ts"], "state": "pending",
                                          "base_revision": e.get("base_revision"),
                                          "base_hash": e.get("before_hash")}
            elif e["action"] in ("apply_proposal", "reject_proposal") and e["target"] in proposals:
                proposals[e["target"]]["state"] = "applied" if e["action"] == "apply_proposal" else "rejected"
        return list(proposals.values())

    def apply_proposal(self, proposal_id: str, actor: str) -> dict:
        return self._resolve_proposal(proposal_id, actor, apply=True)

    def reject_proposal(self, proposal_id: str, actor: str, reason: str = "") -> dict:
        return self._resolve_proposal(proposal_id, actor, apply=False, reason=reason)

    @transactional
    def _resolve_proposal(self, proposal_id: str, actor: str, apply: bool, reason: str = "") -> dict:
        validate_actor(actor)
        proposal = next((p for p in self.list_proposals() if p["id"] == proposal_id), None)
        if proposal is None or proposal["state"] != "pending":
            raise ValidationError([f"no pending proposal '{proposal_id}'"])
        if apply:
            before = self.profile
            # Legacy proposals lack revisions: their original hash is still checked.
            legacy_before = {k: v for k, v in before.items() if k != "revision"}
            hashes = {content_hash(before), content_hash(legacy_before)}
            if (proposal["base_revision"] not in (None, before["revision"])
                    or proposal["base_hash"] not in hashes):
                raise ConflictError("profile changed since proposal; create a fresh proposal")
            after = self._profile_patch(before, proposal["patch"])
            after["revision"] = before["revision"] + 1
            self.store.write_profile(after)
            self.store.append_event(actor, "apply_proposal", proposal_id, reason, before=before, after=after)
        else:
            self.store.append_event(actor, "reject_proposal", proposal_id, reason)
        return {**proposal, "state": "applied" if apply else "rejected"}

    # --- research brief -------------------------------------------------------
    @transactional
    def research_brief(self, candidate_id: str) -> str:
        c = self._require(candidate_id)
        p = self.profile
        baseline = next((x for x in self.store.list() if x.get("is_baseline")), None)
        lines = [f"# Research brief: {c.get('name')}", ""]
        if c.get("location", {}).get("address"):
            lines.append(f"Address: {c['location']['address']}")
        b = p.get("budget") or {}
        lines += ["", "## Profile context (minimum necessary)",
                  f"- Budget (all-in monthly): ideal {b.get('ideal_min')}-{b.get('ideal_max')}, "
                  f"soft ceiling {b.get('soft_ceiling')}, hard ceiling {b.get('hard_ceiling')} {p.get('currency', '')}"]
        for a in p.get("anchors") or []:
            lines.append(f"- Anchor '{a['label']}': ~{a.get('visits_per_week', '?')} visits/week via "
                         f"{', '.join(a.get('modes') or [])}")
        if baseline:
            lines.append(f"- Compare against baseline: {baseline.get('name')}")
        lines += ["", "## Score categories (0-10, each needs rationale + evidence)"]
        lines += [f"- {cat['label']} (`{cat['id']}`, weight {cat['weight']:.0%})" for cat in p["categories"]]
        lines += ["", "## Risk flags to check"]
        lines += [f"- `{f}`" for f in sorted(set(p.get("risk_caps") or {}) | set(p.get("reject_flags") or []))]
        stale = scoring.evaluate(c, p, self.today)["stale_facts"]
        if stale:
            lines += ["", "## Stale facts to refresh", *[f"- {s}" for s in stale]]
        lines += ["", "## Rules",
                  "- Do not fabricate listings, prices, availability, or neighborhood facts.",
                  "- Record facts first (value, source URL, checked date, confidence), then judgments.",
                  "- Submit changes through `housing` commands; do not edit JSON files directly."]
        return "\n".join(lines) + "\n"

    # --- internals ---------------------------------------------------------
    def _require(self, candidate_id: str) -> dict:
        c = self.store.get(candidate_id)
        if c is None:
            raise ValidationError([f"unknown candidate '{candidate_id}'"])
        return c

    def _normalize(self, payload: dict, existing: dict | None) -> dict:
        problems: list[str] = []
        name = str(payload.get("name") or (existing or {}).get("name") or "").strip()
        if not name:
            problems.append("name is required")
        status = payload.get("status") or (existing or {}).get("status") or "active"
        if status not in CANDIDATE_STATUSES:
            problems.append(f"unknown status '{status}'")
        if problems:
            raise ValidationError(problems)
        now = utc_now()
        base = copy.deepcopy(existing) if existing else {
            "schema_version": SCHEMA_VERSION, "id": payload.get("id") or slugify(name),
            "created_at": now, "facts": {}, "judgments": {}, "risk_flags": [],
        }
        for key, value in payload.items():
            if key in ("version", "evaluation", "created_at", "schema_version"):
                continue
            if key == "facts":
                base["facts"] = {n: _clean_fact(n, f) for n, f in (value or {}).items()}
            else:
                base[key] = copy.deepcopy(value)
        base.update(name=name, status=status, updated_at=now)
        return base


def _clean_fact(name: str, fact) -> dict:
    require(valid_id(name), "fact name must be an id")
    require(isinstance(fact, dict) and "value" in fact, f"fact '{name}' must be an object with a 'value'")
    validate_json(fact, f"facts.{name}")
    for key in ("source", "checked", "kind", "note"):
        if fact.get(key) is not None:
            require(isinstance(fact[key], str), f"fact {name}.{key} must be a string")
    if fact.get("confidence") is not None:
        require(fact["confidence"] in CONFIDENCE_LEVELS, f"fact {name}: invalid confidence")
    if fact.get("checked"):
        try:
            date.fromisoformat(fact["checked"][:10])
        except ValueError as exc:
            raise ValidationError([f"fact {name}: checked must be an ISO date"]) from exc
    if name in {"base_rent", "parking_monthly", "required_monthly_fees", "all_in_monthly_cost",
                "move_in_cost", "bedrooms", "bathrooms", "sqft"} and fact["value"] is not None:
        from .costs import number
        value = number(fact["value"])
        require(value is not None and value >= 0, f"fact {name}: value must be a non-negative finite number")
    if name == "anchor_minutes" and fact["value"] is not None:
        require(isinstance(fact["value"], dict), "anchor_minutes.value must be an object")
        for anchor, modes in fact["value"].items():
            require(valid_id(anchor) and isinstance(modes, dict), "anchor_minutes must map anchor ids to modes")
            for mode, value in modes.items():
                require(valid_id(mode) and (value is None or (finite_number(value) and value >= 0)),
                        "anchor minutes must be non-negative finite numbers or null")
    return {k: fact[k] for k in FACT_FIELDS if k in fact}


def _validate_judgment(judgment: dict) -> None:
    require(isinstance(judgment, dict), "judgment must be an object")
    score = judgment.get("score")
    require(score is None or (finite_number(score) and 0 <= score <= 10),
            "score must be between 0 and 10 (finite number)")
    rationale = judgment.get("rationale", "")
    require(isinstance(rationale, str), "rationale must be a string")
    require(score is None or bool(rationale.strip()), "rationale is required for a judgment")
    evidence = judgment.get("evidence", [])
    require(isinstance(evidence, list) and all(isinstance(v, str) and bool(v.strip()) for v in evidence),
            "evidence must be a list of non-empty references")


def _validate_candidate_patch(payload: dict, profile: dict, existing: dict | None) -> None:
    if "schema_version" in payload:
        require(type(payload["schema_version"]) is int and payload["schema_version"] == SCHEMA_VERSION,
                f"candidate schema_version must be {SCHEMA_VERSION}")
    for key in ("name", "status", "neighborhood", "address", "url", "verdict"):
        if key in payload:
            require(isinstance(payload[key], str), f"{key} must be a string")
    if "notes" in payload:
        require(isinstance(payload["notes"], dict) and all(isinstance(v, str) for v in payload["notes"].values()),
                "notes must map field names to strings")
    if "is_baseline" in payload:
        require(type(payload["is_baseline"]) is bool, "is_baseline must be boolean")
    if "facts" in payload:
        require(isinstance(payload["facts"], dict), "facts must be an object")
        for name, fact in payload["facts"].items():
            _clean_fact(name, fact)
    if "judgments" in payload:
        require(isinstance(payload["judgments"], dict), "judgments must be an object")
        for category, judgment in payload["judgments"].items():
            # Old records may lack rationale or refer to retired categories. Preserve
            # unchanged judgments, but never let that exception excuse new judgments.
            if existing and category in (existing.get("judgments") or {}) and judgment == existing["judgments"][category]:
                continue
            require(category in category_ids(profile), f"unknown category '{category}'")
            _validate_judgment(judgment)
    if "risk_flags" in payload:
        require(isinstance(payload["risk_flags"], list), "risk_flags must be a list")
        ids = []
        for flag in payload["risk_flags"]:
            fid = flag.get("id") if isinstance(flag, dict) else flag
            require(valid_id(fid), "risk flags must be ids or objects with an id")
            ids.append(fid)
        require(len(set(ids)) == len(ids), "risk flag ids must be unique")


def _merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _rank_key(c: dict):
    ev = c["evaluation"]
    return (STATUS_ORDER.get(c.get("status", "active"), 99),
            -(ev["final_score"] if ev["final_score"] is not None else -1), c.get("name", ""))


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


__all__ = ["HousingService", "dumps", "Path"]
