# profile.example

A fictional, public-safe sample profile so the app works out of the box.
The app loads it **read-only** when no `profile.local/` exists and `HOUSING_PROFILE_DIR` is unset.

## Start your own profile

Recommended: run `housing init` and follow [docs/SETUP.md](../docs/SETUP.md). Manual alternative:

```bash
cp -r profile.example profile.local
# edit profile.local/profile.json and profile.local/USER_PROFILE.md
# delete or replace profile.local/candidates/*.json
```

`profile.local/` is git-ignored — keep your real data there (or point `HOUSING_PROFILE_DIR` elsewhere).
Never commit real names, addresses, emails, or phone numbers to `profile.example/`;
`scripts/privacy_scan.py` and `tests/test_privacy_scan.py` guard against that.

## What's inside

- `profile.json` — Harborview (fictional) profile, six generic score categories, two anchors.
- `candidates/` — 8 sample listings: a baseline, a rejected studio, one with stale pricing,
  one missing judgments, and several fully scored. All sources are `https://example.com/...`.
