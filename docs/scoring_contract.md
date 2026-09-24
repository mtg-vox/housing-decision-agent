# Scoring Contract

How the engine turns facts and judgments into a score. All weights, caps, rejects and anchors come from the active `profile.json`; nothing below is hard-coded to a person or city.

## Inputs

- **Categories** (`profile.categories`): each has `id`, `label`, `weight`. Weights must sum to 1.0.
- **Judgments** (`candidate.judgments[category]`): `score` 0–10 plus a required `rationale`, and optional `evidence` (fact names or URLs).
- **Facts** (`candidate.facts[name]`): `value`, `kind`, `source`, `checked` (YYYY-MM-DD), `confidence`.
- **Risk flags** (`candidate.risk_flags`): ids matched against `profile.risk_caps` and `profile.reject_flags`.
- **Anchors** (`profile.anchors`): places that matter. `facts.anchor_minutes.value` holds minutes per anchor per mode, e.g. `{"work": {"walk": 14, "drive": 9}}`.

## Evaluation order

1. If any category has no judgment, the candidate is **unscored** (`final_score: null`) and listed with its `missing_categories`.
2. Weighted score = Σ weight × score.
3. **Category caps**: for each flag in `risk_caps` whose target is a category, that category score is limited to `cap` before weighting.
4. **Anchor adjustments**: for each anchor, take the fastest available mode; the first `adjustments` step with `max_minutes >= minutes` gives a `delta` (the last step, `max_minutes: null`, is the catch-all). Deltas are added.
5. Clamp to 0–10.
6. **Total caps**: flags whose target is `total_score` limit the final score.
7. **Rejects**: any flag in `reject_flags` marks the candidate rejected (kept, but excluded from the ranking).

## Outputs (computed, never stored)

- `final_score`, `raw_weighted_score`, `capped_scores`, `caps_applied`, `total_cap`
- `anchor_adjustments`: per anchor, the best mode, its minutes, the delta and a note
- `rejected`, `reject_reasons`, `unknown_flags`
- `costs`: all-in monthly (explicit, or base rent + parking + required fees), move-in, annualized
- `budget_band`: `ideal`, `within`, `over_soft`, `over_hard` or `unknown`
- `stale_facts`: facts older than `profile.staleness_days[kind]`
- `confidence`: `high`, `medium` or `low`, from evidence coverage and staleness

## Recording facts

For every serious candidate, keep the facts behind the scores: base rent, required fees, parking, concessions (with terms and expiry), move-in cost, and anchor minutes. Each needs a source and a checked date. Do not fold a promotion into the cost figures unless its source and lease-term math are recorded.
