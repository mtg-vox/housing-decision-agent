"""`housing` command-line interface over HousingService (stdlib argparse only).

Exit codes: 0 ok, 1 other error, 2 validation/usage, 3 conflict, 4 read-only/permission.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from .models import ValidationError
from .profile import load_profile, resolve_profile_dir
from .service import HousingService
from .store import ConflictError

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_CONFLICT, EXIT_PERMISSION = 0, 1, 2, 3, 4
FACT_KINDS = ("pricing", "availability", "flood", "construction", "commute", "other")
CONFIDENCES = ("high", "medium", "low")


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # exit 2 via our handler, honoring --json
        raise UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="housing", description="Local-first housing decision CLI.")
    p.add_argument("--profile", help="profile dir (else $HOUSING_PROFILE_DIR, ./profile.local, ./profile.example)")
    p.add_argument("--actor", default=None, help="attribution label, not authentication; env HOUSING_ACTOR")
    p.add_argument("--json", action="store_true", help="machine-readable JSON on stdout")
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)

    s = sub.add_parser("list", help="ranked candidates")
    s.add_argument("--status")
    sub.add_parser("show", help="one candidate with evaluation").add_argument("id")
    sub.add_parser("compare", help="side-by-side").add_argument("ids", nargs="+")

    s = sub.add_parser("add", help="create or update a candidate")
    s.add_argument("--name")
    s.add_argument("--id")
    s.add_argument("--status")
    s.add_argument("--from-json", metavar="FILE", help="candidate JSON file or '-' for stdin")
    s.add_argument("--reason", default="")
    s.add_argument("--expect-version", type=int)

    s = sub.add_parser("set-fact", help="record a fact with provenance")
    s.add_argument("id"); s.add_argument("name"); s.add_argument("value")
    s.add_argument("--source", required=True)
    s.add_argument("--checked", required=True, help="YYYY-MM-DD")
    s.add_argument("--confidence", required=True, choices=CONFIDENCES)
    s.add_argument("--kind", required=True, choices=FACT_KINDS)
    s.add_argument("--note")
    s.add_argument("--reason", default="")
    s.add_argument("--expect-version", type=int)

    s = sub.add_parser("judge", help="score a category (rationale required)")
    s.add_argument("id"); s.add_argument("category"); s.add_argument("score", type=float)
    s.add_argument("--rationale", default="")
    s.add_argument("--evidence", nargs="*", default=[])
    s.add_argument("--expect-version", type=int)

    s = sub.add_parser("flag", help="add/remove a risk flag")
    s.add_argument("id"); s.add_argument("flag")
    s.add_argument("--remove", action="store_true")
    s.add_argument("--reason", default="")
    s.add_argument("--expect-version", type=int)

    s = sub.add_parser("archive", help="soft delete")
    s.add_argument("id"); s.add_argument("--reason", required=True)

    s = sub.add_parser("delete", help="permanent delete (requires --hard --yes)")
    s.add_argument("id")
    s.add_argument("--hard", action="store_true")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--reason", default="")

    sub.add_parser("stale", help="candidates with stale facts")
    sub.add_parser("brief", help="research brief").add_argument("id")

    s = sub.add_parser("propose", help="propose a profile change")
    s.add_argument("--patch", required=True, help="JSON object deep-merged into profile")
    s.add_argument("--reason", required=True)
    sub.add_parser("proposals", help="list profile proposals")
    sub.add_parser("apply", help="apply a pending proposal").add_argument("proposal_id")
    s = sub.add_parser("reject", help="reject a pending proposal")
    s.add_argument("proposal_id"); s.add_argument("--reason", required=True)

    s = sub.add_parser("serve", help="run the web dashboard")
    s.add_argument("--port", type=int)
    s.add_argument("--host")
    s = sub.add_parser("preferences", help="read or directly update profile preferences")
    s.add_argument("--patch", help="JSON merge patch object")
    s.add_argument("--expect-version", type=int, help="expected profile revision")
    s.add_argument("--reason", default="")
    sub.add_parser("doctor", help="diagnose profile resolution and health")
    s = sub.add_parser("init", help="first-run setup: create a private profile (see docs/SETUP.md)")
    s.add_argument("--dir", default="profile.local", help="where to create it (default ./profile.local)")
    s.add_argument("--answers", help="setup answers as JSON (for agents); omit to be prompted")
    s.add_argument("--questions", action="store_true", help="print the setup questions and exit")
    return p


# --- output helpers ------------------------------------------------------------
def _emit(args, data, human: str | None = None) -> None:
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    else:
        print(human if human is not None else json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _fmt(v, nd=2):
    if v is None:
        return "-"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def _table(rows: list[list], headers: list[str]) -> str:
    cells = [headers] + [[_fmt(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in cells)


def _row(rank, c):
    ev = c["evaluation"]
    return [rank, c["id"], c.get("name"), ev["final_score"], (ev.get("costs") or {}).get("all_in_monthly"),
            ev.get("budget_band"), c.get("status"), "STALE" if ev["stale_facts"] else ""]


LIST_HEADERS = ["#", "id", "name", "score", "all-in", "band", "status", "stale"]


def _show_human(c) -> str:
    ev = c["evaluation"]
    lines = [f"{c.get('name')} ({c['id']})  status={c.get('status')}  version={c.get('version')}",
             f"score={_fmt(ev['final_score'])}  all-in={_fmt((ev.get('costs') or {}).get('all_in_monthly'))}"
             f"  band={ev.get('budget_band')}  rejected={ev['rejected']}"]
    if c.get("risk_flags"):
        lines.append("flags: " + ", ".join(c["risk_flags"]))
    if c.get("facts"):
        lines += ["", "facts:", _table([[n, json.dumps(f.get("value")), f.get("kind"), f.get("checked"),
                                          f.get("confidence"), f.get("source")]
                                         for n, f in sorted(c["facts"].items())],
                                        ["name", "value", "kind", "checked", "conf", "source"])]
    if c.get("judgments"):
        lines += ["", "judgments:", _table([[k, j.get("score"), j.get("by"), j.get("rationale")]
                                             for k, j in sorted(c["judgments"].items())],
                                            ["category", "score", "by", "rationale"])]
    if ev["stale_facts"]:
        lines += ["", "stale: " + ", ".join(map(str, ev["stale_facts"]))]
    return "\n".join(lines)


def _parse_value(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _check_date(raw: str) -> str:
    try:
        date.fromisoformat(raw)
    except ValueError:
        raise ValidationError([f"--checked must be YYYY-MM-DD (got '{raw}')"])
    return raw


# --- commands -------------------------------------------------------------------
def _service(args) -> HousingService:
    return HousingService(resolve_profile_dir(args.profile, repo_root=Path.cwd()))


def _require(svc, cid):
    c = svc.get_candidate(cid)
    if c is None:
        raise ValidationError([f"unknown candidate '{cid}'"])
    return c


def run(args) -> int:
    cmd = args.command
    if cmd == "serve":
        from . import web
        loc = resolve_profile_dir(args.profile)
        load_profile(loc)
        argv = ["--profile", str(loc.path)] + \
               (["--port", str(args.port)] if args.port else []) + (["--host", args.host] if args.host else [])
        return web.main(argv)
    if cmd == "doctor":
        return _doctor(args)
    if cmd == "init":
        return _init(args)

    svc = _service(args)
    actor = args.actor

    if cmd == "preferences":
        if args.patch is None:
            result = svc.profile
        else:
            try:
                patch = json.loads(args.patch)
            except json.JSONDecodeError as exc:
                raise ValidationError([f"--patch is not valid JSON: {exc}"])
            result = svc.update_profile(patch, actor, args.reason, expected_version=args.expect_version)
        _emit(args, result)
    elif cmd == "list":
        items = svc.list_candidates(args.status)
        _emit(args, items, _table([_row(i + 1, c) for i, c in enumerate(items)], LIST_HEADERS))
    elif cmd == "show":
        c = _require(svc, args.id)
        _emit(args, c, _show_human(c))
    elif cmd == "compare":
        rows = svc.compare(args.ids)
        _emit(args, rows, _table([[r["id"], r["name"], r["final_score"], (r["costs"] or {}).get("all_in_monthly"),
                                   r["budget_band"], "yes" if r["is_baseline"] else "",
                                   "STALE" if r["stale_facts"] else ""] for r in rows],
                                 ["id", "name", "score", "all-in", "band", "baseline", "stale"]))
    elif cmd == "add":
        payload = {}
        if args.from_json:
            text = sys.stdin.read() if args.from_json == "-" else Path(args.from_json).read_text(encoding="utf-8")
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as e:
                raise ValidationError([f"--from-json is not valid JSON: {e}"])
            if not isinstance(payload, dict):
                raise ValidationError(["--from-json must contain an object"])
        for key in ("name", "id", "status"):
            if getattr(args, key):
                payload[key] = getattr(args, key)
        if not payload.get("name") and not payload.get("id"):
            raise ValidationError(["--name (or a name in --from-json) is required"])
        c = svc.put_candidate(payload, actor=actor, reason=args.reason, expected_version=args.expect_version)
        _emit(args, c, f"saved {c['id']} (version {c['version']})")
    elif cmd == "set-fact":
        fact = {"value": _parse_value(args.value), "source": args.source, "checked": _check_date(args.checked),
                "confidence": args.confidence, "kind": args.kind}
        if args.note:
            fact["note"] = args.note
        c = svc.patch_facts(args.id, {args.name: fact}, actor=actor, reason=args.reason,
                            expected_version=args.expect_version)
        _emit(args, c, f"{c['id']}.{args.name} = {json.dumps(fact['value'])} (version {c['version']})")
    elif cmd == "judge":
        c = svc.set_judgment(args.id, args.category, args.score, args.rationale, actor=actor,
                             evidence=args.evidence, expected_version=args.expect_version)
        _emit(args, c, f"{c['id']}.{args.category} = {args.score:g}; final score {_fmt(c['evaluation']['final_score'])}")
    elif cmd == "flag":
        c = svc.store.get(args.id) or _require(svc, args.id)
        flags = list(c.get("risk_flags") or [])
        if args.remove:
            flags = [f for f in flags if f != args.flag]
        elif args.flag not in flags:
            flags.append(args.flag)
        c = svc.put_candidate({"id": args.id, "risk_flags": flags}, actor=actor,
                              reason=args.reason or f"{'remove' if args.remove else 'add'} flag {args.flag}",
                              expected_version=args.expect_version)
        _emit(args, c, f"{c['id']} flags: {', '.join(c['risk_flags']) or '(none)'}")
    elif cmd == "archive":
        _require(svc, args.id)
        svc.delete_candidate(args.id, actor=actor, reason=args.reason)
        _emit(args, {"id": args.id, "status": "archived"}, f"archived {args.id}")
    elif cmd == "delete":
        if not (args.hard and args.yes):
            raise ValidationError(["delete requires --hard --yes (use `archive` for a soft delete)"])
        _require(svc, args.id)
        svc.delete_candidate(args.id, actor=actor, reason=args.reason, hard=True)
        _emit(args, {"id": args.id, "deleted": True}, f"deleted {args.id}")
    elif cmd == "stale":
        items = [c for c in svc.list_candidates() if c["evaluation"]["stale_facts"]]
        data = [{"id": c["id"], "name": c.get("name"), "stale_facts": c["evaluation"]["stale_facts"]} for c in items]
        _emit(args, data, "\n".join(f"{d['id']}: {', '.join(map(str, d['stale_facts']))}" for d in data)
              or "no stale facts")
    elif cmd == "brief":
        text = svc.research_brief(args.id)
        _emit(args, {"id": args.id, "brief": text}, text.rstrip("\n"))
    elif cmd == "propose":
        try:
            patch = json.loads(args.patch)
        except json.JSONDecodeError as e:
            raise ValidationError([f"--patch is not valid JSON: {e}"])
        pid = svc.propose_profile_change(patch, actor=actor, reason=args.reason)
        _emit(args, {"proposal_id": pid, "state": "pending"}, f"proposed {pid} (pending review)")
    elif cmd == "proposals":
        items = svc.list_proposals()
        _emit(args, items, _table([[p["id"], p["state"], p["actor"], p["reason"], json.dumps(p["patch"])]
                                   for p in items], ["id", "state", "actor", "reason", "patch"]))
    elif cmd == "apply":
        r = svc.apply_proposal(args.proposal_id, actor=actor)
        _emit(args, r, f"applied {r['id']}")
    elif cmd == "reject":
        r = svc.reject_proposal(args.proposal_id, actor=actor, reason=args.reason)
        _emit(args, r, f"rejected {r['id']}")
    return EXIT_OK


def _init(args) -> int:
    from .setup_profile import QUESTIONS, build_profile, prompt_answers, write_new_profile
    if args.questions:
        _emit(args, QUESTIONS, "\n".join(
            f"- {q['key']} ({'required' if q['required'] else 'recommended' if q.get('recommended') else 'optional'}): {q['ask']}"
            for q in QUESTIONS))
        return EXIT_OK
    if args.answers is not None:
        try:
            answers = json.loads(args.answers)
        except json.JSONDecodeError as exc:
            raise ValidationError([f"--answers is not valid JSON: {exc}"])
    elif sys.stdin.isatty() and not args.json:
        answers = prompt_answers()
    else:
        raise ValidationError(["non-interactive: pass --answers JSON (see `housing init --questions`)"])
    target = write_new_profile(Path(args.dir), build_profile(answers))
    _emit(args, {"created": str(target)},
          f"Created profile at {target}. Start the dashboard with `housing serve`; "
          f"add homes in the dashboard or with `housing add`.")
    return EXIT_OK


def _doctor(args) -> int:
    report: dict = {"ok": False}
    try:
        loc = resolve_profile_dir(args.profile, repo_root=Path.cwd())
    except FileNotFoundError as e:
        report["error"] = str(e)
        _emit(args, report, f"profile: NOT FOUND\n{e}")
        return EXIT_ERROR
    report.update(profile_source=loc.source, profile_path=str(loc.path), read_only=loc.read_only)
    try:
        svc = HousingService(loc)
        report["validation"] = "ok"
        items = svc.list_candidates()
        report["candidate_count"] = len(items)
        report["stale_count"] = sum(1 for c in items if c["evaluation"]["stale_facts"])
        report["ledger_events"] = len(svc.store.events())
        report["pending_proposals"] = sum(1 for p in svc.list_proposals() if p["state"] == "pending")
        report["ok"] = True
    except ValidationError as e:
        report["validation"] = "failed"
        report["problems"] = e.problems
    human = "\n".join(f"{k}: {v}" for k, v in report.items())
    _emit(args, report, human)
    return EXIT_OK if report["ok"] else EXIT_USAGE


def _fail(json_mode: bool, code: int, message: str, problems: list[str] | None = None) -> int:
    if json_mode:
        sys.stderr.write(json.dumps({"error": message, "problems": problems or []}) + "\n")
    else:
        sys.stderr.write(f"error: {message}\n" + "".join(f"  - {p}\n" for p in problems or []))
    return code


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in argv
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        return _fail(json_mode, EXIT_USAGE, str(e))
    except SystemExit as e:  # --help
        return int(e.code or 0)
    args.actor = args.actor if args.actor is not None else os.environ.get("HOUSING_ACTOR", "cli")
    try:
        return run(args)
    except ValidationError as e:
        return _fail(args.json, EXIT_USAGE, "validation failed", e.problems)
    except ConflictError as e:
        return _fail(args.json, EXIT_CONFLICT, f"conflict: {e}")
    except PermissionError as e:  # includes ReadOnlyProfileError
        return _fail(args.json, EXIT_PERMISSION, str(e))
    except FileNotFoundError as e:
        return _fail(args.json, EXIT_ERROR, str(e))
    except Exception as e:  # noqa: BLE001
        return _fail(args.json, EXIT_ERROR, f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
