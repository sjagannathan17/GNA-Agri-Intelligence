"""
Risk Scorer Tool
----------------
Computes a 0–1 risk score for a farmer based on features from
the GNA Analytics Showdown XGBoost model findings.

In production: load the serialized XGBoost model (model.pkl).
For the prototype: rule-based approximation of the model.
"""


def compute_risk_score(farmer: dict) -> float:
    """
    Returns a risk score between 0 and 1.
    Score > 0.45 = high risk, triggers field agent alert.

    Feature weights derived from the XGBoost feature importance
    in the Analytics Showdown notebook.
    """
    score = 0.0

    # Season experience (highest importance feature — Season 1 alone = high risk)
    season = farmer.get("season_number", 1)
    if season == 1:
        score += 0.50
    elif season == 2:
        score += 0.15

    # Inoculant (second highest importance)
    if not farmer.get("has_inoculant"):
        score += 0.20

    # Nudge engagement rate
    responses = farmer.get("nudge_responses", {})
    total = responses.get("total", 0)
    if total > 0:
        help_rate = responses.get("help", 0) / total
        skip_rate = responses.get("skip", 0) / total
        score += help_rate * 0.15
        score += skip_rate * 0.10

    # In-kind repayment gap
    in_kind = farmer.get("total_in_kind_repay", 0)
    yield_est = farmer.get("yield_estimate_kg", 0)
    if in_kind > 0 and yield_est > 0 and yield_est < in_kind:
        score += 0.15

    # Late planting
    days_to_plant = farmer.get("days_to_plant", 0)
    if days_to_plant > 30:
        score += 0.05

    # Rainfall deficit for the farmer's zone (optional — populated by RiskMonitorAgent)
    rainfall_deficit_pct = farmer.get("rainfall_deficit_pct", 0)
    if rainfall_deficit_pct < -15:
        score += 0.10  # significant drought stress
    elif rainfall_deficit_pct < -8:
        score += 0.05  # mild deficit

    return min(score, 1.0)
