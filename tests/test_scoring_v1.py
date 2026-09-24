from housing_agent.scoring import evaluate
from tests.conftest import TODAY, judgments, make_candidate


def test_fixture_is_independently_synthetic(profile_dict):
    assert [c["weight"] for c in profile_dict["categories"]] == [0.25, 0.25, 0.125, 0.125, 0.125, 0.125]
    anchor = profile_dict["anchors"][0]
    assert anchor["visits_per_week"] == 1
    assert anchor["modes"] == ["walk", "transit"]
    assert anchor["adjustments"] == [
        {"max_minutes": 10, "delta": 0.5},
        {"max_minutes": 30, "delta": 0.0},
        {"max_minutes": None, "delta": -0.5},
    ]


def ev(candidate, profile):
    return evaluate(candidate, profile, TODAY)


def test_weighted_score_uses_profile_weights(profile_dict):
    assert ev(make_candidate(), profile_dict)["final_score"] == round(8 + 1 / 8, 2)


def test_caps_and_total_cap(profile_dict):
    c = make_candidate(risk_flags=[{"id": "ground_level_or_flood_exposed_parking"},
                                   {"id": "adjacent_high_disruption_construction"}])
    r = ev(c, profile_dict)
    assert r["raw_weighted_score"] == round(8 + 1 / 8, 2)
    assert r["capped_scores"]["friction_risk"] == 5.0
    assert r["total_cap"] == 7.5 and r["final_score"] == 7.5
    assert not r["rejected"]


def test_anchor_adjustment_steps(profile_dict):
    def score(walk, transit):
        c = make_candidate(facts={"anchor_minutes": {"value": {"office": {"walk": walk, "transit": transit}},
                                                     "kind": "commute", "checked": "2026-04-01"}})
        return ev(c, profile_dict)
    assert score(28, 18)["final_score"] == round(8 + 1 / 8, 2)
    assert score(10, None)["final_score"] == round(8 + 1 / 8 + 0.5, 2)
    assert score(40, 38)["final_score"] == round(8 + 1 / 8 - 0.5, 2)
    assert score(None, None)["anchor_adjustments"][0]["delta"] == 0.0


def test_reject_flag(profile_dict):
    r = ev(make_candidate(risk_flags=["studio_unit"]), profile_dict)
    assert r["rejected"] and r["reject_reasons"] == ["studio_unit"]


def test_unknown_flags_reported(profile_dict):
    assert ev(make_candidate(risk_flags=["mystery"]), profile_dict)["unknown_flags"] == ["mystery"]


def test_missing_judgment_is_unscored_not_defaulted(profile_dict):
    j = judgments()
    del j["social_upside"]
    r = ev(make_candidate(judgments=j), profile_dict)
    assert r["final_score"] is None and r["missing_categories"] == ["social_upside"]


def test_out_of_range_judgment_is_problem(profile_dict):
    j = judgments()
    j["social_upside"]["score"] = 11
    r = ev(make_candidate(judgments=j), profile_dict)
    assert r["final_score"] is None and r["problems"]


def test_budget_bands_and_costs(profile_dict):
    def band(rent, parking=None):
        facts = {"base_rent": {"value": rent, "kind": "pricing", "checked": "2026-04-30"}}
        if parking is not None:
            facts["parking_monthly"] = {"value": parking, "kind": "pricing", "checked": "2026-04-30"}
        return ev(make_candidate(facts=facts), profile_dict)
    assert band(2200)["budget_band"] == "ideal"
    assert band(2450)["budget_band"] == "within"
    r = band(2650, parking=200)  # 2850 all-in > 2800 hard ceiling
    assert r["budget_band"] == "over_hard" and r["costs"]["annualized"] == 34200
    assert band(2650)["budget_band"] == "over_soft"


def test_staleness_and_confidence(profile_dict):
    fresh = ev(make_candidate(), profile_dict)
    assert fresh["stale_facts"] == [] and fresh["confidence"] == "high"
    old = make_candidate(facts={"base_rent": {"value": 2200, "kind": "pricing", "checked": "2026-01-01"}})
    r = ev(old, profile_dict)
    assert r["stale_facts"] == ["base_rent"] and r["confidence"] == "low"


def test_custom_categories_need_no_code(profile_dict):
    profile_dict["categories"] = [{"id": "gym", "label": "Gym", "weight": 0.5},
                                  {"id": "quiet", "label": "Quiet", "weight": 0.5}]
    profile_dict["risk_caps"] = {}
    c = make_candidate(judgments=judgments({"gym": 9, "quiet": 7}))
    assert ev(c, profile_dict)["final_score"] == 8.0
