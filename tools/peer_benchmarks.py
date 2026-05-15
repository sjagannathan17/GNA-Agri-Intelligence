"""
Peer Benchmarks Tool
--------------------
Answers questions of the form "what's normal for someone like me?" by
slicing the canonical `cleaned_dataset/master_table.csv` from the GNA
Analytics Showdown notebook into camp / district / zone cohorts and
returning median / top-quartile yield + buyback statistics.

Used by the chat agent for queries like:
  - "How am I doing compared to other farmers in my camp?"
  - "What's the typical yield in Zone IIa?"
  - "Are most farmers in my district using inoculant?"

The first call lazy-loads the CSV and caches it for the lifetime of the
process. If the CSV is missing (e.g. the notebook hasn't been re-run on
this machine), the tool returns a clear error so the chat agent can
inform the farmer rather than fail silently.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "../cleaned_dataset/master_table.csv"

_df = None  # cached pandas DataFrame
_load_error: str | None = None


def _load_master_table():
    """Lazy-load the canonical master_table.csv. Returns the dataframe or None."""
    global _df, _load_error
    if _df is not None or _load_error is not None:
        return _df

    try:
        import pandas as pd
    except ImportError:
        _load_error = "pandas_not_installed"
        return None

    path = os.environ.get("GNA_MASTER_TABLE", _DEFAULT_PATH)
    candidates = [Path(path), Path(__file__).parent.parent / path, Path.cwd() / path]
    for p in candidates:
        if p.exists():
            try:
                _df = pd.read_csv(p)
                logger.info(f"peer_benchmarks: loaded {len(_df):,} rows from {p}")
                return _df
            except Exception as e:
                _load_error = f"read_failed: {e!r}"
                return None

    _load_error = f"master_table_not_found (looked in: {[str(p) for p in candidates]})"
    return None


def _round(x):
    """Round numeric values, return None for NaN/None."""
    try:
        if x is None:
            return None
        # Handle pandas NA / numpy NaN
        if hasattr(x, "__float__"):
            f = float(x)
            if f != f:  # NaN check
                return None
            return round(f, 1)
        return x
    except (TypeError, ValueError):
        return None


def _cohort_stats(df, label_field: str, label_value: str) -> dict[str, Any]:
    sub = df[df[label_field] == label_value]
    n = len(sub)
    if n == 0:
        return {
            "label_field":    label_field,
            "label_value":    label_value,
            "n_farmers":      0,
            "error":          "no_matching_farmers",
        }

    buyers = sub[sub["has_buyback"] == 1] if "has_buyback" in sub.columns else sub
    yields = buyers["yield_per_hectare"].dropna() if "yield_per_hectare" in buyers.columns else []

    p90 = float(yields.quantile(0.9)) if len(yields) else None
    median_y = float(yields.median()) if len(yields) else None
    top10_threshold = p90

    inoc_rate = float(sub["has_inoculant"].mean()) if "has_inoculant" in sub.columns else None
    train_rate = float(sub["rcvd_training"].mean()) if "rcvd_training" in sub.columns else None
    buyback_rate = float(sub["has_buyback"].mean()) if "has_buyback" in sub.columns else None

    common_variety = (
        sub["dominant_variety"].mode().iloc[0]
        if "dominant_variety" in sub.columns and len(sub["dominant_variety"].mode())
        else None
    )

    return {
        "label_field":               label_field,
        "label_value":               label_value,
        "n_farmers":                 int(n),
        "n_buyers":                  int(len(buyers)),
        "median_yield_kg_ha":        _round(median_y),
        "top10_yield_threshold_kg_ha": _round(top10_threshold),
        "buyback_rate":              _round(buyback_rate),
        "inoculant_adoption":        _round(inoc_rate),
        "training_rate":             _round(train_rate),
        "most_common_variety":       common_variety,
    }


# ─── Public entrypoints ───────────────────────────────────────────────────────

def get_peer_benchmarks(
    *,
    camp_name: str | None = None,
    district_name: str | None = None,
    zone: str | None = None,
    farmer_yield_kg_ha: float | None = None,
) -> dict[str, Any]:
    """Return median / top-decile yield + adoption stats for the requested cohort.

    Calls return a multi-level breakdown: camp (most specific), district,
    zone (least specific). The chat agent picks the level the farmer asked
    about (or shows all three for a self-comparison answer).

    If `farmer_yield_kg_ha` is supplied, also returns the farmer's percentile
    within their camp / district / zone cohorts.
    """
    df = _load_master_table()
    if df is None:
        return {
            "error":           _load_error or "unknown",
            "camp_stats":      None,
            "district_stats":  None,
            "zone_stats":      None,
        }

    out: dict[str, Any] = {"error": None}

    if camp_name:
        out["camp_stats"] = _cohort_stats(df, "camp_name", camp_name)
    if district_name:
        out["district_stats"] = _cohort_stats(df, "district_name", district_name)
    if zone:
        out["zone_stats"] = _cohort_stats(df, "agroecological_zone", zone)

    if farmer_yield_kg_ha is not None:
        out["farmer_yield_kg_ha"] = round(float(farmer_yield_kg_ha), 1)
        for level, key, val in [
            ("camp",     "camp_name",            camp_name),
            ("district", "district_name",        district_name),
            ("zone",     "agroecological_zone",  zone),
        ]:
            if val and out.get(f"{level}_stats", {}).get("n_farmers", 0) > 0:
                sub = df[df[key] == val]
                yields = sub.loc[sub["has_buyback"] == 1, "yield_per_hectare"].dropna()
                if len(yields):
                    pct = float((yields < farmer_yield_kg_ha).mean()) * 100
                    out[f"{level}_stats"]["farmer_percentile"] = round(pct, 0)

    return out


def get_top_camps(*, n: int = 5, by: str = "yield_per_hectare") -> dict[str, Any]:
    """Return the top-N camps by median yield (or buyback rate). Useful for
    'champion-camp' peer-mentor program suggestions."""
    df = _load_master_table()
    if df is None:
        return {"error": _load_error or "unknown", "camps": []}

    if by == "yield_per_hectare":
        agg = (
            df[df["has_buyback"] == 1]
            .groupby("camp_name")["yield_per_hectare"]
            .median()
            .dropna()
            .sort_values(ascending=False)
            .head(n)
        )
    elif by == "buyback_rate":
        agg = (
            df.groupby("camp_name")["has_buyback"]
            .mean()
            .sort_values(ascending=False)
            .head(n)
        )
    else:
        return {"error": f"unknown_metric: {by}", "camps": []}

    return {
        "error": None,
        "metric": by,
        "camps": [
            {"camp_name": str(name), "value": _round(value)}
            for name, value in agg.items()
        ],
    }
