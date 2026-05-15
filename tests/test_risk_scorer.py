"""Tests for the risk scorer tool."""
from tools.risk_scorer import compute_risk_score

def test_first_season_farmer_is_high_risk():
    farmer = {"season_number": 1, "has_inoculant": True, "nudge_responses": {}}
    assert compute_risk_score(farmer) > 0.45

def test_experienced_farmer_with_inoculant_is_low_risk():
    farmer = {
        "season_number": 4,
        "has_inoculant": True,
        "nudge_responses": {"total": 10, "done": 9, "help": 0, "skip": 1},
        "days_to_plant": 5,
    }
    assert compute_risk_score(farmer) < 0.45

def test_no_inoculant_adds_risk():
    base = {"season_number": 3, "has_inoculant": True, "nudge_responses": {}}
    with_inoculant = compute_risk_score(base)
    base["has_inoculant"] = False
    without_inoculant = compute_risk_score(base)
    assert without_inoculant > with_inoculant

def test_score_is_capped_at_one():
    farmer = {
        "season_number": 1,
        "has_inoculant": False,
        "nudge_responses": {"total": 10, "done": 0, "help": 5, "skip": 5},
        "total_in_kind_repay": 500,
        "yield_estimate_kg": 100,
        "days_to_plant": 60,
    }
    assert compute_risk_score(farmer) <= 1.0
