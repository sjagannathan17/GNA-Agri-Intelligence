"""
Yield Predictor Tool
--------------------
Estimates a farmer's end-of-season yield in kg/ha based on their profile.

Derived from the GNA Analytics Showdown XGBoost model findings.
In production: load a serialized model (model.pkl). For the prototype:
a rule-based approximation grounded in the notebook's key coefficients.
"""

# Baseline from GNA data: median yield across all farmers
_BASELINE_KG_HA = 1_240.0


def predict_yield(farmer: dict) -> dict:
    """
    Returns a dict with:
      - estimate_kg_ha: point estimate
      - low_kg_ha / high_kg_ha: ±1 confidence band
      - drivers: list of (label, delta_kg_ha) explaining the prediction
    """
    estimate = _BASELINE_KG_HA
    drivers: list[tuple[str, float]] = []

    # Season experience — biggest driver from notebook
    season = farmer.get("season_number", 1)
    if season == 1:
        delta = -830.0   # first-season farmers yield 3.3x less (÷3.3 ≈ −62%)
        estimate += delta
        drivers.append(("First season (less experience)", delta))
    elif season == 2:
        delta = -300.0
        estimate += delta
        drivers.append(("Second season", delta))

    # Inoculant — +486 kg/ha from the trial data
    if farmer.get("has_inoculant"):
        estimate += 486.0
        drivers.append(("Inoculant applied", 486.0))
    else:
        drivers.append(("No inoculant", 0.0))

    # Fertilizer (zone-controlled lift from notebook §5)
    if farmer.get("has_fertilizer"):
        estimate += 312.0
        drivers.append(("Fertilizer applied", 312.0))

    # Late planting penalty
    days_to_plant = farmer.get("days_to_plant", 0)
    if days_to_plant > 30:
        delta = -180.0
        estimate += delta
        drivers.append(("Late planting (>30 days past window)", delta))
    elif days_to_plant > 14:
        delta = -60.0
        estimate += delta
        drivers.append(("Slightly late planting", delta))

    # Scale by hectares for total volume estimate
    hectares = farmer.get("total_hectares", 1.0)
    estimate_total = max(estimate, 100.0)  # floor at 100 kg/ha

    return {
        "estimate_kg_ha": round(estimate_total, 1),
        "estimate_kg_total": round(estimate_total * hectares, 1),
        "low_kg_ha": round(estimate_total * 0.75, 1),
        "high_kg_ha": round(estimate_total * 1.25, 1),
        "drivers": drivers,
    }
