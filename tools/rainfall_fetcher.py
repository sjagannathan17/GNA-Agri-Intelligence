"""
Rainfall Fetcher Tool
---------------------
Fetches seasonal rainfall totals and anomalies for GNA's agroecological zones
using the Open-Meteo historical archive API (free, no API key required).

Zone representative centroids (Zambia):
  Zone I   (Eastern)   → Chipata:   -13.5, 32.5
  Zone IIa (Central)   → Mkushi:    -14.0, 28.5
  Zone IIb (Southern)  → Choma:     -16.0, 27.5
  Zone III (Northern)  → Kasama:    -10.5, 29.5
  Zone IV  (Western)   → Mongu:     -15.2, 23.1

Zambia's growing season runs November – April.
Historical baseline = mean of the previous 5 seasons.
"""

import httpx
from datetime import date, timedelta
from collections import defaultdict

ZONE_COORDS = {
    "I":   {"lat": -13.5, "lon": 32.5},
    "IIa": {"lat": -14.0, "lon": 28.5},
    "IIb": {"lat": -16.0, "lon": 27.5},
    "III": {"lat": -10.5, "lon": 29.5},
    "IV":  {"lat": -15.2, "lon": 23.1},
}

CURRENT_SEASON_START = date(2025, 11, 1)
CURRENT_SEASON_END   = date(2026, 4, 30)

# 5-season historical window (2020/21 – 2024/25)
HIST_YEARS = [
    (date(2020, 11, 1), date(2021, 4, 30)),
    (date(2021, 11, 1), date(2022, 4, 30)),
    (date(2022, 11, 1), date(2023, 4, 30)),
    (date(2023, 11, 1), date(2024, 4, 30)),
    (date(2024, 11, 1), date(2025, 4, 30)),
]

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def _fetch_daily(lat: float, lon: float, start: date, end: date) -> list[float]:
    resp = httpx.get(
        ARCHIVE_URL,
        params={
            "latitude": lat, "longitude": lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": "precipitation_sum",
            "timezone": "Africa/Lusaka",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["daily"].get("precipitation_sum", [])


def _daily_to_monthly(daily: list[float], start: date) -> dict[str, float]:
    monthly: dict[str, float] = defaultdict(float)
    d = start
    for v in daily:
        if v is not None:
            monthly[d.strftime("%b")] += v
        d += timedelta(days=1)
    return dict(monthly)


def fetch_zone_rainfall(zone: str) -> dict:
    coords = ZONE_COORDS[zone]
    lat, lon = coords["lat"], coords["lon"]

    # Current season
    season_daily = _fetch_daily(lat, lon, CURRENT_SEASON_START, CURRENT_SEASON_END)
    season_total = round(sum(v for v in season_daily if v is not None), 1)
    season_monthly = _daily_to_monthly(season_daily, CURRENT_SEASON_START)

    # Historical average (per-month mean over 5 seasons)
    hist_monthly_acc: dict[str, list[float]] = defaultdict(list)
    for h_start, h_end in HIST_YEARS:
        h_daily = _fetch_daily(lat, lon, h_start, h_end)
        for month, val in _daily_to_monthly(h_daily, h_start).items():
            hist_monthly_acc[month].append(val)

    MONTHS = ["Nov", "Dec", "Jan", "Feb", "Mar", "Apr"]
    hist_monthly = {m: round(sum(hist_monthly_acc[m]) / len(hist_monthly_acc[m]), 1)
                    for m in MONTHS if hist_monthly_acc[m]}
    hist_total = round(sum(hist_monthly.values()), 1)

    anomaly_pct = round((season_total - hist_total) / hist_total * 100, 1) if hist_total else 0.0

    return {
        "zone": zone,
        "lat": lat,
        "lon": lon,
        "season_total_mm": season_total,
        "historical_avg_mm": hist_total,
        "anomaly_pct": anomaly_pct,
        "monthly_mm": {m: round(season_monthly.get(m, 0), 1) for m in MONTHS},
        "historical_monthly_mm": hist_monthly,
    }


def fetch_all_zones(zones: list[str] | None = None) -> list[dict]:
    results = []
    for zone in (zones or list(ZONE_COORDS.keys())):
        try:
            results.append(fetch_zone_rainfall(zone))
        except Exception as e:
            print(f"Warning: rainfall fetch failed for Zone {zone}: {e}")
    return results
