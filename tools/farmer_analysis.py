"""
Farmer Analysis Tool
--------------------
Composite LLM-friendly tool that wraps the existing rule-based
`risk_scorer.py` and `yield_predictor.py` into a single call returning
the analysis the chat agent needs to answer questions like:

  - "Why am I marked high risk?"
  - "What yield should I expect this season?"
  - "What should I do to improve my chances?"

The result is structured so Claude can lift the human-readable strings
straight into a reply, including specific numerical drivers and concrete
recommended actions.
"""

from __future__ import annotations

from typing import Any

from tools.risk_scorer import compute_risk_score
from tools.yield_predictor import predict_yield


# ─── Recommendation rules ────────────────────────────────────────────────────
# Rules are intentionally simple and grounded in the canonical notebook
# findings; the chat agent surfaces them as bullet points / actions.

def _recommendations(farmer: dict, risk: float, yield_info: dict) -> list[dict]:
    recs: list[dict] = []

    if not farmer.get("has_inoculant"):
        recs.append({
            "priority": "high",
            "title":    "Apply inoculant before/at planting",
            "rationale": (
                "Inoculant adds about +486 kg/ha after zone control and is the most "
                "reliable single-input lift in GNA's data."
            ),
            "expected_lift_kg_ha": 486,
        })

    if farmer.get("season_number", 1) == 1:
        recs.append({
            "priority": "high",
            "title":    "Enroll in the first-season onboarding track",
            "rationale": (
                "First-season farmers yield about 3.3x less than Season-3+ farmers "
                "(300 vs 1,000 kg/ha median). Demo-plot visit + WhatsApp planting "
                "calendar + peer-mentor pairing closes a meaningful share of that gap."
            ),
            "expected_lift_kg_ha": 250,
        })

    if farmer.get("days_to_plant", 0) > 30:
        recs.append({
            "priority": "medium",
            "title":    "Plant within the recommended window for your zone",
            "rationale": (
                "Planting more than 30 days into the season costs roughly 180 kg/ha "
                "in expected yield. If the window is gone for this season, plan ahead "
                "for next year and confirm seed availability earlier."
            ),
            "expected_lift_kg_ha": 180,
        })

    nudge = farmer.get("nudge_responses", {}) or {}
    total = nudge.get("total", 0)
    if total >= 3 and nudge.get("help", 0) / max(total, 1) > 0.4:
        recs.append({
            "priority": "high",
            "title":    "Field-agent visit recommended",
            "rationale": (
                "You have asked for help on more than 40% of recent check-ins, "
                "which is a strong signal you would benefit from in-person support. "
                "Reply with your preferred day and we'll arrange a visit."
            ),
            "expected_lift_kg_ha": None,
        })

    if not recs:
        recs.append({
            "priority": "low",
            "title":    "You are on track — keep going",
            "rationale": (
                "Your indicators are within the safe range. Continue replying to "
                "weekly check-ins so we can flag issues early."
            ),
            "expected_lift_kg_ha": None,
        })

    return recs


def analyze_farmer(farmer: dict) -> dict[str, Any]:
    """Run risk + yield + recommendations for one farmer.

    farmer: dict in the FarmerStore shape (zone, season_number, has_inoculant,
            has_fertilizer, days_to_plant, nudge_responses, total_hectares, ...).

    Returns:
        {
          "risk_score": 0.0..1.0,
          "risk_tier":  "Low"|"Medium"|"High",
          "risk_explanation": str,
          "yield_estimate_kg_ha": float,
          "yield_estimate_kg_total": float,
          "yield_band_low":  float,
          "yield_band_high": float,
          "yield_drivers": [{label, delta_kg_ha}, ...],
          "recommendations": [{priority, title, rationale, expected_lift_kg_ha}, ...],
        }
    """
    risk = compute_risk_score(farmer)
    yield_info = predict_yield(farmer)

    if risk >= 0.35:
        tier = "High"
        risk_msg = "high risk of not selling back to GNA"
    elif risk >= 0.20:
        tier = "Medium"
        risk_msg = "moderate risk — worth tightening up the basics"
    else:
        tier = "Low"
        risk_msg = "low risk — on track for a successful season"

    drivers = yield_info["drivers"]
    return {
        "farmer_id":               farmer.get("farmer_id"),
        "zone":                    farmer.get("zone"),
        "season_number":           farmer.get("season_number", 1),
        "risk_score":              round(risk, 3),
        "risk_tier":               tier,
        "risk_explanation":        f"Score = {risk:.2f}. This farmer is at {risk_msg}.",
        "yield_estimate_kg_ha":    yield_info["estimate_kg_ha"],
        "yield_estimate_kg_total": yield_info["estimate_kg_total"],
        "yield_band_low":          yield_info["low_kg_ha"],
        "yield_band_high":         yield_info["high_kg_ha"],
        "yield_drivers": [
            {"label": label, "delta_kg_ha": delta} for (label, delta) in drivers
        ],
        "recommendations":         _recommendations(farmer, risk, yield_info),
    }
