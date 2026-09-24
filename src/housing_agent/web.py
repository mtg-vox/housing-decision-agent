"""Local dashboard HTTP adapter over HousingService.

Security posture (local-first, single user):
- binds 127.0.0.1 by default; Host header must name the bound loopback address (DNS-rebinding guard)
- every /api/ request needs the per-launch token (X-Housing-Token), injected into index.html
- cross-origin requests (Origin header not equal to our own origin) are refused
- JSON bodies are capped and must be application/json
- static files are resolved with Path.is_relative_to (no prefix-match traversal)
- /api/health does not reveal filesystem paths

The browser still speaks the legacy flat candidate shape; `to_view` / `from_view` translate
between it and the v1 facts/judgments model so app.js stays mostly unchanged.
"""

from __future__ import annotations

import argparse
import copy
import hmac
import hashlib
import json
import mimetypes
import re
import secrets
import socket
import sys
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .costs import all_in_monthly, number
from .models import ValidationError
from .profile import resolve_profile_dir
from .resources import asset_dir
from . import scoring
from .profile import validate_profile
from .service import HousingService, _merge
from .store import ConflictError, ReadOnlyProfileError, content_hash

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = asset_dir("static")
REGIONS_DIR = asset_dir("regions")
MAX_BODY = 1_000_000
TOKEN_PLACEHOLDER = "__HOUSING_TOKEN__"
ACTOR = "ui:dashboard"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

# Built-in free-text fields. Profiles add their own via profile["note_fields"]: [{id, label, placeholder}].
BASE_NOTE_FIELDS = ("parking", "floor", "view", "commute_notes", "risk_notes", "pricing_notes", "unit_type")


def note_field_ids(profile: dict) -> tuple[str, ...]:
    extra = [f["id"] for f in profile.get("note_fields") or [] if isinstance(f, dict) and f.get("id")]
    return BASE_NOTE_FIELDS + tuple(x for x in extra if x not in BASE_NOTE_FIELDS)


def region_sources(profile: dict) -> str:
    region = str(profile.get("region") or "")
    if not region.replace("-", "").replace("_", "").isalnum():
        return ""
    path = REGIONS_DIR / region / "research_sources.yaml"
    return path.read_text(encoding="utf-8") if path.is_file() else ""
PRICING_FACTS = ("base_rent", "all_in_monthly_cost", "move_in_cost")


# --- shape translation -----------------------------------------------------------------------
def _fact(candidate: dict, name: str):
    f = (candidate.get("facts") or {}).get(name)
    return f.get("value") if isinstance(f, dict) else None


def _primary_anchor(profile: dict) -> dict | None:
    anchors = profile.get("anchors") or []
    return anchors[0] if anchors else None


def to_view(candidate: dict, profile: dict) -> dict:
    ev = candidate["evaluation"]
    loc = candidate.get("location") or {}
    anchor = _primary_anchor(profile)
    minutes = ((_fact(candidate, "anchor_minutes") or {}).get(anchor["id"], {}) if anchor else {})
    adj = next((a for a in ev["anchor_adjustments"] if anchor and a["anchor"] == anchor["id"]), {})
    judgments = candidate.get("judgments") or {}
    view = {
        "id": candidate["id"],
        "version": candidate.get("version"),
        "name": candidate.get("name"),
        "kind": candidate.get("kind"),
        "status": candidate.get("status"),
        "is_baseline": bool(candidate.get("is_baseline")),
        "address": loc.get("address", ""),
        "neighborhood": loc.get("neighborhood", ""),
        "map_location": {"lat": loc.get("lat"), "lng": loc.get("lng"), "precision": loc.get("precision")}
        if loc.get("lat") is not None else None,
        "map_label": candidate.get("map_label"),
        "notes": {k: (candidate.get("notes") or {}).get(k, "") for k in note_field_ids(profile)},
        **{k: (candidate.get("notes") or {}).get(k, "") for k in note_field_ids(profile)},
        "base_rent": _fact(candidate, "base_rent"),
        "all_in_monthly_cost": ev["costs"]["all_in_monthly"],
        "annualized_housing_spend": ev["costs"]["annualized"],
        "move_in_cost": _fact(candidate, "move_in_cost"),
        "fee_snapshot": _fact(candidate, "fee_snapshot"),
        "concession": _fact(candidate, "concession"),
        "unit_options": candidate.get("unit_options") or [],
        "moving_estimate": candidate.get("moving_estimate") or {},
        "open_questions": candidate.get("open_questions") or [],
        "source_urls": candidate.get("source_urls") or [],
        "verdict": candidate.get("verdict", ""),
        "risk_flags": sorted(f["id"] if isinstance(f, dict) else f for f in candidate.get("risk_flags") or []),
        "scores": {k: j.get("score") for k, j in judgments.items() if isinstance(j, dict)},
        "commute_estimate": {"walk_minutes": minutes.get("walk"), "drive_minutes": minutes.get("drive")},
        "last_checked": max((f.get("checked") or "" for f in (candidate.get("facts") or {}).values()
                             if isinstance(f, dict)), default=""),
        "stale_facts": ev["stale_facts"],
        "confidence": ev["confidence"],
        "evaluation": {"rejected": ev["rejected"]},
        "budget_band": ev["budget_band"],
    }
    view["score_summary"] = {
        "weighted_score": ev["final_score"],
        "scores": ev["scores"],
        "risk_adjusted_scores": ev["capped_scores"] or ev["scores"],
        "risk_cap_notes": ev["caps_applied"],
        "reject_flags": ev["reject_reasons"],
        "recommendation_flag": "reject_unless_exceptional" if ev["rejected"] else "consider",
        "missing_categories": ev["missing_categories"],
        "office_commute_adjustment": {
            "best_minutes": adj.get("best_minutes"), "best_mode": adj.get("best_mode"),
            "score_adjustment": adj.get("delta"),
        },
    }
    return view


def from_view(payload: dict, existing: dict | None, profile: dict, today: str) -> dict:
    """Translate a flat dashboard payload into a v1 candidate, preserving provenance of
    anything the user did not change."""
    out: dict[str, Any] = copy.deepcopy(existing) if existing else {"facts": {}, "judgments": {}, "notes": {}}
    for key in ("name", "kind", "status", "verdict"):
        if key in payload:
            out[key] = payload[key] or ""
    if "id" in payload:
        out["id"] = payload["id"]
    loc = out.setdefault("location", {})
    for key in ("address", "neighborhood"):
        if key in payload:
            loc[key] = payload[key] or ""
    notes = out.setdefault("notes", {})
    for key in note_field_ids(profile):
        if key in payload:
            if payload[key]:
                notes[key] = payload[key]
            else:
                notes.pop(key, None)
    urls = payload.get("source_urls")
    if isinstance(urls, list):
        out["source_urls"] = [u for u in (str(x).strip() for x in urls) if u]
    source = (out.get("source_urls") or [None])[0]

    facts = out.setdefault("facts", {})

    def set_fact(name: str, value, kind: str):
        old = facts.get(name)
        if value is None:
            facts.pop(name, None)
            return
        if isinstance(old, dict) and old.get("value") == value:
            return  # unchanged: keep original source/checked/confidence
        f = {"value": value, "kind": kind, "checked": today, "confidence": "user"}
        if source:
            f["source"] = source
        facts[name] = f

    for name in PRICING_FACTS:
        if name in payload:
            if name == "all_in_monthly_cost" and "all_in_monthly_cost" not in facts:
                # the view shows a computed all-in; only store it if the user typed a different number
                computed = all_in_monthly(existing) if existing else None
                if number(payload[name]) is None or number(payload[name]) == computed:
                    continue
            set_fact(name, number(payload[name]), "pricing")

    anchor = _primary_anchor(profile)
    if anchor and ("walk_minutes" in payload or "drive_minutes" in payload):
        current = copy.deepcopy(_fact(out, "anchor_minutes") or {})
        mins = {m: number(payload.get(f"{m}_minutes")) for m in ("walk", "drive")}
        mins = {m: v for m, v in mins.items() if v is not None}
        if mins:
            current[anchor["id"]] = mins
        else:
            current.pop(anchor["id"], None)
        set_fact("anchor_minutes", current or None, "commute")

    if isinstance(payload.get("risk_flags"), list):
        old_flags = {(f["id"] if isinstance(f, dict) else f): f for f in out.get("risk_flags") or []}
        out["risk_flags"] = [old_flags.get(fid, {"id": fid}) for fid in sorted(set(payload["risk_flags"]))]

    if isinstance(payload.get("scores"), dict):
        judgments = out.setdefault("judgments", {})
        valid = {c["id"] for c in profile["categories"]}
        for cat, raw in payload["scores"].items():
            score = number(raw)
            if cat not in valid or score is None:
                continue
            old = judgments.get(cat)
            if isinstance(old, dict) and number(old.get("score")) == score:
                continue
            judgments[cat] = {"score": max(0.0, min(10.0, score)), "rationale": "Set via dashboard slider",
                              "evidence": [], "by": ACTOR, "at": today}
    return out


# --- state projection ------------------------------------------------------------------------
def _money(v) -> str:
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "-"


def profile_revision(profile: dict) -> str:
    """HTTP optimistic-concurrency token; not an identity/authentication mechanism."""
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


def _eligible(candidate: dict) -> bool:
    return (not candidate.get("is_baseline")
            and candidate.get("status") not in ("baseline", "rejected", "archived")
            and not candidate.get("evaluation", {}).get("rejected", False))


def pending_proposals(service: HousingService, events: list[dict]) -> list[dict]:
    snapshots = {e["target"]: e for e in events if e.get("action") == "propose_profile_change"}
    result = []
    for proposal in service.list_proposals():
        if proposal["state"] != "pending":
            continue
        event = snapshots.get(proposal["id"], {})
        # Show the exact recorded proposal, never silently rebase a stale proposal.
        before = event.get("before", proposal.get("before"))
        after = event.get("after", proposal.get("after"))
        # Older ledgers retain hashes rather than snapshots. Reconstruct only
        # when the persisted base hash still matches; otherwise fail closed.
        if before is None and event.get("before_hash") == content_hash(service.profile):
            before = copy.deepcopy(service.profile)
            after = _merge(copy.deepcopy(before), proposal["patch"])
        result.append({**proposal, "before": before, "after": after,
                       "stale": before is None or before != service.profile})
    return result


def dashboard_state(service: HousingService) -> dict:
    service.reload()
    profile = service.profile
    candidates = [to_view(c, profile) for c in service.list_candidates()]
    baseline = next((c for c in candidates if c["is_baseline"]), None)
    live = [c for c in candidates if _eligible(c)]
    scored = [c["score_summary"]["weighted_score"] for c in live
              if c["score_summary"]["weighted_score"] is not None]
    budget = profile.get("budget") or {}
    display = profile.get("display") or {}
    anchor = _primary_anchor(profile)
    events = service.store.events()
    ledger = []
    for e in events:
        legacy = e.get("legacy")
        if legacy:
            ledger.append({"title": legacy.get("title", ""), "date": legacy.get("date", ""),
                           "status": legacy.get("status", ""), "summary": legacy.get("summary", "")})
        elif e.get("action") != "migrate":
            ledger.append({"title": f"{e['action']} {e.get('target', '')}".strip(),
                           "date": (e.get("ts") or "")[:10], "status": e.get("actor", ""),
                           "summary": e.get("reason", "")})
    flags = sorted(set(profile.get("risk_caps") or {}) | set(profile.get("reject_flags") or []))
    return {
        "profile": {
            "name": profile.get("name", "Housing profile"),
            "current_home": (baseline or {}).get("address") or (baseline or {}).get("name") or "Not set",
            "current_home_name": (baseline or {}).get("name") or "Baseline",
            "current_rent": (baseline or {}).get("base_rent"),
            "baseline_id": (baseline or {}).get("id"),
            "lease_end": display.get("lease_end", ""),
            "move_window": display.get("move_window") or {},
            "ideal_band": f"{_money(budget.get('ideal_min'))}-{_money(budget.get('ideal_max'))}",
            "soft_ceiling": f"{_money(budget.get('soft_ceiling'))} all-in",
            "soft_ceiling_value": budget.get("soft_ceiling"),
            "hard_review_threshold": _money(budget.get("hard_ceiling")),
            "anchor_label": anchor["label"] if anchor else "Anchor",
            "facts": display.get("facts") or [],
            "map_center": display.get("map_center"),
            "unit": copy.deepcopy(profile.get("unit") or {}),
            "reject_flags": list(profile.get("reject_flags") or []),
        },
        "metrics": {
            "total_candidates": len(candidates),
            "live_candidates": len(live),
            "rejected_candidates": sum(1 for c in candidates if c["status"] == "rejected" or c["evaluation"]["rejected"]),
            "best_score": max(scored) if scored else None,
            "stale_candidates": sum(1 for c in candidates if c["stale_facts"]),
        },
        "candidates": candidates,
        "score_categories": [c["id"] for c in profile["categories"]],
        "category_labels": {c["id"]: c.get("label", c["id"]) for c in profile["categories"]},
        "risk_flags": flags,
        "flag_labels": profile.get("flag_labels") or {},
        "areas": profile.get("areas") or [],
        "ledger": ledger,
        "pending_proposals": pending_proposals(service, events),
        "profile_revision": profile_revision(profile),
        "read_only": service.location.read_only,
        "research_sources_yaml": region_sources(profile),
        "note_fields": [f for f in profile.get("note_fields") or [] if isinstance(f, dict) and f.get("id")],
    }


PROFILE_EDITABLE = ("categories", "budget", "risk_caps", "reject_flags", "anchors", "staleness_days")
PREVIEW_LIMIT = 10


def editable_profile(service: HousingService) -> dict:
    service.reload()
    p = service.profile
    out = copy.deepcopy(p)
    out["profile_revision"] = profile_revision(p)
    out["flag_labels"] = copy.deepcopy(p.get("flag_labels") or {})
    out["read_only"] = service.location.read_only
    return out


def _ranking(service: HousingService, profile: dict) -> list[dict]:
    rows = []
    for c in service.store.list():
        if c.get("is_baseline") or c.get("status") in ("rejected", "archived", "baseline"):
            continue
        ev = scoring.evaluate(c, profile, service.today)
        if ev["final_score"] is None or ev["rejected"]:
            continue
        rows.append({"id": c["id"], "name": c.get("name", c["id"]), "final_score": ev["final_score"],
                     "rejected": ev["rejected"]})
    rows.sort(key=lambda r: (-r["final_score"], r["name"]))
    return rows


def preview_profile_patch(service: HousingService, patch) -> dict:
    """Sensitivity preview: rank every candidate under the patched profile, never persisted."""
    if not isinstance(patch, dict) or not patch:
        raise ValidationError(["patch must be a non-empty object"])
    service.reload()
    after_profile = _merge(copy.deepcopy(service.profile), patch)
    validate_profile(after_profile)
    before = _ranking(service, service.profile)
    after = _ranking(service, after_profile)
    before_pos = {r["id"]: i for i, r in enumerate(before)}
    before_score = {r["id"]: r["final_score"] for r in before}
    for i, r in enumerate(after):
        prev = before_pos.get(r["id"])
        r["rank"] = i + 1
        r["previous_rank"] = prev + 1 if prev is not None else None
        r["moved"] = (prev - i) if prev is not None else None
        r["previous_score"] = before_score.get(r["id"])
    for i, r in enumerate(before):
        r["rank"] = i + 1
    return {"before": before[:PREVIEW_LIMIT], "after": after[:PREVIEW_LIMIT]}


def research_brief(service: HousingService, payload: dict) -> str:
    target = str(payload.get("target") or "").strip()
    question = str(payload.get("question") or "").strip()
    needle = target.lower()
    match = next((c for c in service.store.list()
                  if needle and needle in (c["id"].lower(), str(c.get("name", "")).lower())), None)
    if match:
        text = service.research_brief(match["id"])
    else:
        p = service.profile
        text = "\n".join([f"# Research brief: {target or 'new option'}", "",
                          "## Score categories (0-10, each needs rationale + evidence)",
                          *[f"- {c['label']} (`{c['id']}`, weight {c['weight']:.0%})" for c in p["categories"]],
                          "", "## Rules",
                          "- Do not fabricate listings, prices, availability, or neighborhood facts.",
                          "- Record facts first (value, source URL, checked date, confidence), then judgments.",
                          ""])
    if question:
        text += f"\n## User question\n{question}\n"
    return text


# --- HTTP ------------------------------------------------------------------------------------
class DashboardHandler(BaseHTTPRequestHandler):
    service: HousingService
    token: str
    bound: tuple[str, int]
    static_dir: Path
    server_version = "HousingAgent"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002  # quieter; no bodies logged
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?", 1)[0]))

    # guards
    def _host_ok(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1:
            return False
        port = self.bound[1]
        allowed = {f"{name}:{port}" for name in ("localhost", "127.0.0.1", "[::1]")}
        if port == 80:
            allowed.update(("localhost", "127.0.0.1", "[::1]"))
        return hosts[0].lower() in allowed

    def _origin_ok(self) -> bool:
        origins = self.headers.get_all("Origin", [])
        if not origins:
            return True  # non-browser clients still require the per-launch token
        return len(origins) == 1 and origins[0].lower() == f"http://{self.headers['Host'].lower()}"

    def _token_ok(self) -> bool:
        tokens = self.headers.get_all("X-Housing-Token", [])
        return len(tokens) == 1 and hmac.compare_digest(tokens[0].encode(), self.token.encode())

    def _guard(self, api: bool) -> bool:
        if not self._host_ok():
            self.send_json({"error": "Host not allowed"}, HTTPStatus.FORBIDDEN)
            return False
        if api and not (self._origin_ok() and self._token_ok()):
            self.send_json({"error": "Forbidden"}, HTTPStatus.FORBIDDEN)
            return False
        return True

    # responses
    def _headers(self, status, ctype, length):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; font-src 'self'; "
                         "img-src 'self' data: https://*.tile.openstreetmap.org; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def read_body(self) -> dict:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ValidationError(["Content-Type must be application/json"])
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValidationError(["bad Content-Length"]) from None
        if length > MAX_BODY:
            # Don't drain the body; tell the client and drop the connection.
            self.close_connection = True
            raise ValidationError(["request body too large"])
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError([f"invalid JSON: {exc}"]) from None
        if not isinstance(data, dict):
            raise ValidationError(["JSON body must be an object"])
        return data

    def _error(self, exc: Exception):
        if isinstance(exc, ConflictError):
            self.send_json({"error": f"Changed elsewhere; reload and retry ({exc})"}, HTTPStatus.CONFLICT)
        elif isinstance(exc, ReadOnlyProfileError):
            self.send_json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
        elif isinstance(exc, ValidationError):
            self.send_json({"error": str(exc), "problems": exc.problems}, HTTPStatus.BAD_REQUEST)
        elif isinstance(exc, PermissionError):
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        else:
            sys.stderr.write(f"internal error: {type(exc).__name__}: {exc}\n")
            self.send_json({"error": "Internal error"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    # routes
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            if not self._guard(api=True):
                return
            try:
                if path == "/api/state":
                    return self.send_json(dashboard_state(self.service))
                if path == "/api/profile":
                    return self.send_json(editable_profile(self.service))
                if path == "/api/health":
                    return self.send_json({"ok": True, "read_only": self.service.location.read_only,
                                           "profile_source": self.service.location.source})
            except Exception as exc:  # noqa: BLE001
                return self._error(exc)
            return self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        if not self._guard(api=False):
            return
        if path in ("", "/"):
            path = "/index.html"
        static_root = self.static_dir.resolve()
        requested = (static_root / path.lstrip("/")).resolve()
        if not requested.is_relative_to(static_root) or not requested.is_file():
            return self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        body = requested.read_bytes()
        if requested.name == "index.html":
            body = body.replace(TOKEN_PLACEHOLDER.encode(), self.token.encode())
        ctype = mimetypes.guess_type(requested.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._headers(HTTPStatus.OK, ctype, len(body))
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        if not self._guard(api=True):
            return
        path = urlparse(self.path).path
        try:
            payload = self.read_body()
            if path == "/api/candidates":
                svc = self.service
                cid = payload.get("id")
                existing = svc.store.get(cid) if cid else None
                version = payload.get("version") if existing else None
                candidate = from_view(payload, existing, svc.profile, date.today().isoformat())
                saved = svc.put_candidate(candidate, actor=ACTOR,
                                          reason=payload.get("verdict") or "Updated via dashboard",
                                          expected_version=int(version) if version is not None else None)
                state = dashboard_state(svc)
                view = next(c for c in state["candidates"] if c["id"] == saved["id"])
                return self.send_json({"candidate": view, "state": state}, HTTPStatus.CREATED)
            if path == "/api/profile/preview":
                return self.send_json(preview_profile_patch(self.service, payload.get("patch")))
            if path.startswith("/api/profile/proposals"):
                return self._proposal_route(path, payload)
            if path == "/api/research-brief":
                return self.send_json({"brief": research_brief(self.service, payload)})
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)
        self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def _proposal_route(self, path: str, payload: dict):
        svc = self.service
        svc.reload()
        if svc.location.read_only:
            raise ReadOnlyProfileError("The profile is read-only; preferences cannot be changed.")
        revision = payload.get("profile_revision")
        if revision is not None and revision != profile_revision(svc.profile):
            raise ConflictError("Profile preferences changed; review the refreshed values before retrying")
        if path == "/api/profile/proposals":
            reason = str(payload.get("reason") or "").strip() or "Proposed via dashboard"
            pid = svc.propose_profile_change(payload.get("patch"), actor=ACTOR, reason=reason)
            return self.send_json({"id": pid, "state": dashboard_state(svc)}, HTTPStatus.CREATED)
        m = re.fullmatch(r"/api/profile/proposals/(prop-[0-9a-f]+)/(apply|reject)", path)
        if not m:
            return self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        pid, verb = m.groups()
        reason = str(payload.get("reason") or "").strip()
        if verb == "apply":
            pending = next((p for p in pending_proposals(svc, svc.store.events()) if p["id"] == pid), None)
            if pending and pending["stale"]:
                raise ConflictError("Proposal base changed; reject it and propose against current preferences")
            result = svc.apply_proposal(pid, actor=ACTOR)
            svc.reload()  # recompute scores from the persisted profile
        else:
            result = svc.reject_proposal(pid, actor=ACTOR, reason=reason or "Rejected via dashboard")
        return self.send_json({"proposal": result, "state": dashboard_state(svc)})

    def do_DELETE(self):  # noqa: N802
        if not self._guard(api=True):
            return
        parsed = urlparse(self.path)
        if parsed.path != "/api/candidates":
            return self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        cid = parse_qs(parsed.query).get("id", [""])[0]
        try:
            c = self.service.store.get(cid) if cid else None
            if c is None:
                return self.send_json({"error": "Candidate not found"}, HTTPStatus.NOT_FOUND)
            if c.get("is_baseline"):
                return self.send_json({"error": "The baseline cannot be archived"}, HTTPStatus.BAD_REQUEST)
            self.service.delete_candidate(cid, actor=ACTOR, reason="Archived via dashboard")
            return self.send_json({"ok": True, "state": dashboard_state(self.service)})
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)


def make_server(service: HousingService, host: str = "127.0.0.1", port: int = 8765,
                token: str | None = None, static_dir: Path | None = None) -> ThreadingHTTPServer:
    if host not in LOOPBACK:
        raise ValueError("Dashboard host must be loopback (127.0.0.1, localhost, or ::1)")
    token = token or secrets.token_urlsafe(24)
    handler = type("BoundHandler", (DashboardHandler,),
                   {"service": service, "token": token, "bound": (host, port),
                    "static_dir": Path(static_dir) if static_dir is not None else STATIC_DIR})
    server_type = ThreadingHTTPServer
    if host == "::1":
        server_type = type("LoopbackIPv6Server", (ThreadingHTTPServer,), {"address_family": socket.AF_INET6})
    # Never resolve localhost via DNS/hosts into a non-loopback bind.
    server = server_type(("127.0.0.1" if host == "localhost" else host, port), handler)
    handler.bound = (host, server.server_address[1])
    server.token = token  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local housing dashboard.")
    parser.add_argument("--profile", help="profile directory (else $HOUSING_PROFILE_DIR, ./profile.local, ./profile.example)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args(argv)
    if args.host not in LOOPBACK:
        parser.error("--host must be loopback (127.0.0.1, localhost, or ::1)")
    location = resolve_profile_dir(args.profile, repo_root=REPO_ROOT)
    service = HousingService(location)
    server = make_server(service, args.host, args.port)
    mode = " (read-only example)" if location.read_only else ""
    print(f"Housing dashboard: http://{args.host}:{server.server_address[1]}  profile source: {location.source}{mode}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
