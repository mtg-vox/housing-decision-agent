# Setup: create your profile

New users start here. The same steps work for a person at a terminal and for an AI agent.

Until you create a profile, the app shows a **read-only fictional demo** ("Alex Example" in the made-up city Harborview). The demo is there to look at, not to save your own data in.

## Fastest path

```sh
PYTHONPATH=src python3 -m housing_agent init     # or `housing init` after pip install
PYTHONPATH=src python3 -m housing_agent serve    # open the printed 127.0.0.1 address
```

`init` asks a few questions. Press **Enter to skip any question**; everything can be changed later in the dashboard's **Preferences** panel or with `housing preferences`. It creates `./profile.local/`, which git ignores. To keep your data elsewhere (for example in a notes vault), pass `--dir PATH` and set `HOUSING_PROFILE_DIR=PATH`, or make `profile.local` a symlink to it.

`init` never overwrites an existing profile.

## What you will be asked

| Item | Required? | What it means | Example |
|---|---|---|---|
| Name | Optional | A label for this search | `Spring move` |
| Region | Optional | City/region pack in `regions/` (research sources). Blank if none exists yet | `miami` |
| Budget | **Recommended** | Monthly **all-in** cost (rent + required fees + parking): ideal min, ideal max, soft ceiling, hard ceiling. Each must be ≥ the one before it | `1800, 2200, 2400, 2700` |
| Unit | Optional | Minimum bedrooms; whether studios are acceptable (a "no" rules studios out) | `1`, `no` |
| Places you travel to (anchors) | **Recommended** | Work, gym, family… with visits/week, travel modes (`walk`, `bike`, `transit`, `drive`), ideal minutes and longest acceptable minutes. Closer homes score higher | `Work, 3/week, transit, 20, 45` |
| Category weights | Optional | How much each scoring category matters. Defaults are balanced; values are rescaled to total 100% | `cost_value: 3, friction_risk: 1` |
| Deal-breakers | Optional | Flags that rule a place out entirely | `studio_unit` |

Nothing is technically required: an empty setup still produces a working profile with balanced weights. Without a budget, homes aren't compared against a price range; without anchors, travel time doesn't affect scores.

Default scoring categories: **Cost & value**, **Daily lifestyle**, **Social life**, **Work & focus**, **Space & quiet**, **Friction & risk**. You can rename them, add your own, and add custom note fields later by editing `profile.json` (see [scoring contract](scoring_contract.md)).

## After setup

1. **Add homes** in the dashboard form, or with `housing add --name "Some Building"`.
2. **Record facts** with sources and dates (`housing set-fact`): rent, fees, parking, availability, travel minutes.
3. **Score categories** 0–10 with a short reason (`housing judge`, or the sliders in the dashboard).
4. **Compare**: the ranked list, `housing compare ID ID`, and `housing stale` for facts that need re-checking.

Check which profile is active at any time with `housing doctor`.

## For AI agents

If a user asks you to set this up or `housing doctor` reports `profile_source: example`:

1. Run `housing --json init --questions` to get the question list (key, required/recommended/optional, prompt).
2. Ask the user those questions conversationally. **Never guess or invent** a budget, address, or travel time; leave anything they don't answer out.
3. Create the profile in one step:

   ```sh
   housing --json init --answers '{
     "name": "Spring move",
     "budget": {"ideal_min": 1800, "ideal_max": 2200, "soft_ceiling": 2400, "hard_ceiling": 2700},
     "unit": {"min_bedrooms": 1, "allow_studio": false},
     "anchors": [{"label": "Work", "visits_per_week": 3, "modes": ["transit"], "ideal_minutes": 20, "max_minutes": 45}]
   }'
   ```

   Unknown keys and inconsistent values (for example a budget minimum above the maximum) are rejected with an explanation; fix them with the user and retry.
4. Confirm with `housing --json doctor`, then follow [AGENTS_CLI.md](../AGENTS_CLI.md) for everyday use.

Keep the user's answers private: they live only in their profile folder, never in this repository.
