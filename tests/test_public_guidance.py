"""Region guidance must defer individual priorities to the selected profile."""
from pathlib import Path


def test_ci_requires_public_gate_before_tests():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text()
    assert "privacy_scan.py --public-check" in workflow
    assert "install_gitleaks.py" in workflow
    assert "--require-secrets" in workflow
    assert "needs: public-gate" in workflow
    assert workflow.index("--public-check") < workflow.index("install_gitleaks.py")


def test_region_sources_do_not_prescribe_personal_priorities():
    root = Path(__file__).resolve().parents[1] / "regions" / "miami"
    plan = (root / "data_source_plan.md").read_text()
    gaps = (root / "source_gap_audit.md").read_text()
    # Region packs describe sources; weighting any topic is the selected profile's job.
    assert "profile" in next(line for line in plan.splitlines() if "| Transit |" in line)
    for text in (plan, gaps):
        assert "unless" not in text.lower() or "profile" in text.lower()
        assert not any(w in text.lower() for w in ("my office", "my commute", "i need", "we need"))
