# Contributing

Thanks for helping people make better moving decisions.

## Good first contributions

- **Region packs** — add `regions/<city>/` with public research sources (flood maps, permits, crime data, transit) and local guidance. See `regions/miami/` for the format. Describe sources; don't prescribe personal priorities.
- **Risk and cost checks** — new risk flags, fee types, or staleness rules that apply broadly.
- **Dashboard improvements** — plain HTML/JS, no build step.
- **Docs and examples** — clearer setup, more example profiles (clearly fictional).

## Ground rules

- **Never commit real personal data.** Your own profile belongs in `profile.local/` (git-ignored) or a folder outside the repo. Examples and tests must be clearly fictional, not anonymized real data.
- **Standard library only** for the Python app. The dashboard bundles its only browser libraries.
- **Tests:** `python3 -m pytest -q` and `node --check app/static/app.js` must pass. Add tests for behaviour changes.
- **Privacy scan:** `python3 scripts/privacy_scan.py` should report nothing for your change.
- Keep scoring explainable: every score should trace back to facts, judgments and profile settings.

## Reporting issues

Open a GitHub issue. Please don't paste your real profile, addresses, or budget; reproduce with the demo (`profile.example/`) when you can.
