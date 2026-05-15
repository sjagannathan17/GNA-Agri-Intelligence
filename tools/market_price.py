"""
Market Price Tool
-----------------
Returns current crop prices in ZMW so the chat agent can answer:
  - "What is GNA paying for soybean this season?"
  - "What is the open-market price for maize today?"
  - "Will I be better off selling to GNA or to a trader?"

Two layers:
  1. **GNA buyback rate** — the contracted in-kind rate from the analysis
     notebook (12 ZMW/kg gross, ~3.5 ZMW/kg net after loan offsets, both
     surfaced so the agent can frame the comparison correctly).
  2. **Open-market reference** — fetched via web search if the chat agent
     wants a live external comparison. Falls back to a static reasonable
     range if no internet provider is available.

This tool intentionally does not pull from a paid commodities API.
The free World Bank Pink Sheet endpoint and FAO are tracked weekly,
which is fast enough for farmer-facing comparisons. The chat agent
can always invoke `web_search` if it needs the very latest spot price.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ─── GNA contracted prices (canonical, from notebook §7.4) ───────────────────
GNA_BUYBACK_PRICES_ZMW_KG = {
    "soy_bean":    {"gross": 12.0, "net_after_loan": 3.5, "currency": "ZMW"},
    "groundnut":   {"gross": 14.0, "net_after_loan": 4.0, "currency": "ZMW"},
    "maize":       {"gross": 8.0,  "net_after_loan": 2.5, "currency": "ZMW"},
}

# Static fallback for open-market reference. Kept conservative.
# Refresh from World Bank Pink Sheet (https://www.worldbank.org/en/research/commodity-markets)
# or from local Zambia exchange data when possible.
OPEN_MARKET_FALLBACK_ZMW_KG = {
    "soy_bean":  {"low": 10.0, "high": 14.0, "as_of": "2024-Q4 reference"},
    "groundnut": {"low": 11.0, "high": 16.0, "as_of": "2024-Q4 reference"},
    "maize":     {"low": 7.0,  "high": 10.0, "as_of": "2024-Q4 reference"},
}


def _normalize_crop(crop: str) -> str:
    if not crop:
        return "soy_bean"
    norm = crop.lower().replace("-", "_").replace(" ", "_").strip()
    aliases = {
        "soy":         "soy_bean",
        "soya":        "soy_bean",
        "soybean":     "soy_bean",
        "soyabean":    "soy_bean",
        "soy_beans":   "soy_bean",
        "soybeans":    "soy_bean",
        "groundnuts":  "groundnut",
        "peanut":      "groundnut",
        "peanuts":     "groundnut",
    }
    return aliases.get(norm, norm)


def get_market_price(crop: str = "soy_bean", *, include_open_market: bool = False) -> dict[str, Any]:
    """Return GNA's buyback rate plus an optional open-market reference range.

    Returns:
        {
            "crop":        str (normalized),
            "currency":    "ZMW",
            "as_of":       isoformat date,
            "gna_buyback": {"gross_per_kg": float, "net_after_loan_per_kg": float, "note": str},
            "open_market": {"low_per_kg": float, "high_per_kg": float, "as_of": str} | None,
            "comparison_note": str,
        }
    """
    crop_norm = _normalize_crop(crop)

    gna = GNA_BUYBACK_PRICES_ZMW_KG.get(crop_norm)
    if not gna:
        return {
            "crop":         crop_norm,
            "currency":     "ZMW",
            "as_of":        datetime.now(timezone.utc).date().isoformat(),
            "error":        f"crop_not_supported: {crop}",
            "gna_buyback":  None,
            "open_market":  None,
            "available_crops": list(GNA_BUYBACK_PRICES_ZMW_KG.keys()),
        }

    out: dict[str, Any] = {
        "crop":        crop_norm,
        "currency":    "ZMW",
        "as_of":       datetime.now(timezone.utc).date().isoformat(),
        "gna_buyback": {
            "gross_per_kg":          gna["gross"],
            "net_after_loan_per_kg": gna["net_after_loan"],
            "note": (
                "GNA pays the gross rate. The net-after-loan figure is what "
                "the farmer actually receives in cash after the input loan is "
                "deducted in kind. The two figures are different and both matter "
                "depending on the question."
            ),
        },
        "open_market": None,
        "error":       None,
    }

    if include_open_market:
        ref = OPEN_MARKET_FALLBACK_ZMW_KG.get(crop_norm)
        if ref:
            out["open_market"] = {
                "low_per_kg":  ref["low"],
                "high_per_kg": ref["high"],
                "as_of":       ref["as_of"],
                "note":        "Open-market reference range, static fallback. For a live spot price the chat agent can call web_search for 'soybean price Zambia today'.",
            }

    out["comparison_note"] = _build_comparison(crop_norm, gna, out["open_market"])
    return out


def _build_comparison(crop: str, gna: dict, open_market: dict | None) -> str:
    if not open_market:
        return (
            f"GNA buyback gross is {gna['gross']:.2f} ZMW/kg. Net cash after loan offset "
            f"is about {gna['net_after_loan']:.2f} ZMW/kg. Both numbers are correct and "
            "represent different things — gross is the per-kilo credit toward your loan, "
            "net is the cash that lands in your hand after the loan is repaid."
        )
    lo = open_market["low_per_kg"]
    hi = open_market["high_per_kg"]
    if gna["gross"] >= lo:
        return (
            f"GNA's gross rate of {gna['gross']:.2f} ZMW/kg is competitive with the open "
            f"market reference range ({lo:.2f}-{hi:.2f} ZMW/kg). The big difference is "
            "that selling to GNA settles your input loan automatically, while open-market "
            "side-selling means you still owe the loan in cash."
        )
    return (
        f"Open-market reference is {lo:.2f}-{hi:.2f} ZMW/kg vs GNA gross of {gna['gross']:.2f}. "
        "If you side-sell, remember the loan still has to be repaid in cash and you lose "
        "the in-kind settlement convenience."
    )
