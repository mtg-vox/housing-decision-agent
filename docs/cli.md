# `housing` CLI reference

Install: `pip install -e .` gives the `housing` command; or run `python -m housing_agent`.

## Global options

| option | meaning |
|---|---|
| `--profile DIR` | profile dir. Else `$HOUSING_PROFILE_DIR`, `./profile.local`, `./profile.example` (read-only). |
| `--actor ID` | `cli` (default), `llm:<name>`, etc. Env: `HOUSING_ACTOR`. Recorded in the ledger. |
| `--json` | JSON-only stdout; errors go to stderr as `{"error", "problems"}`. |

Global options come **before** the command: `housing --json --actor llm:claude list`.

Exit codes: `0` ok · `1` other error · `2` validation/usage · `3` version conflict · `4` read-only / permission.

## Commands

| command | purpose |
|---|---|
| `list [--status S]` | ranked table: rank, id, name, score, all-in, band, status, stale marker |
| `show ID` | candidate + facts (with provenance), judgments, evaluation |
| `compare ID ID...` | side-by-side scores/costs |
| `add --name N [--id ID] [--status S] [--from-json FILE\|-] [--expect-version N]` | create/update a candidate |
| `set-fact ID NAME VALUE --source URL --checked YYYY-MM-DD --confidence high\|medium\|low --kind pricing\|availability\|flood\|construction\|commute\|other [--note T] [--expect-version N]` | record a fact. VALUE is parsed as JSON if valid (`150`, `true`, `[1,2]`), else a string. |
| `judge ID CATEGORY SCORE --rationale TEXT [--evidence REF ...] [--expect-version N]` | 0–10 category score; rationale required |
| `flag ID FLAG [--remove]` | add/remove a risk flag |
| `archive ID --reason T` | soft delete (status → archived) |
| `delete ID --hard --yes` | permanent delete; `cli` actor only |
| `stale` | candidates with stale facts |
| `brief ID` | research brief for an agent |
| `propose --patch JSON --reason T` | propose a profile change (pending) |
| `proposals` | list proposals and their state |
| `apply PROPOSAL_ID` / `reject PROPOSAL_ID --reason T` | resolve a proposal; not allowed for `llm:*` actors |
| `serve [--port P] [--host H]` | run the web dashboard (`housing_agent.web`) |
| `doctor` | profile source/path, read-only, validation, candidate/stale/ledger/proposal counts |
| `init [--dir DIR] [--answers JSON \| --questions]` | first-run setup; prompts interactively, or takes answers as JSON (agents). Never overwrites. See [SETUP.md](SETUP.md) |

## Examples

```sh
housing --profile ~/housing list
housing set-fact alpha base_rent 2250 --source https://example.com/listing \
  --checked 2026-09-20 --confidence high --kind pricing --expect-version 3
housing judge alpha social_upside 7 --rationale "two venues within 5 min walk" --evidence https://...
housing --json doctor
```
