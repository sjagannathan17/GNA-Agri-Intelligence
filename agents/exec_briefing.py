"""
Exec Briefing Agent
-------------------
Generates weekly executive reports consumed by the GNA dashboard.
Also handles on-demand queries from the "Ask the season" dashboard tab.
"""

import os
import json
from datetime import datetime
import anthropic

try:
    from tools.rainfall_fetcher import fetch_all_zones as _fetch_rainfall
except ImportError:
    _fetch_rainfall = None

EXEC_SYSTEM = """You are a senior data analyst briefing GNA (Good Nature Agro) leadership in Zambia.
Write concisely. Executives are busy — lead with the most important insight.
Use plain English, not statistical jargon.
Format: 3 sentences max for narrative. Numbers must be specific."""


class ExecBriefingAgent:
    def __init__(self, farmer_store):
        self.farmer_store = farmer_store
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    async def run(self, event) -> dict:
        stats = self.farmer_store.get_season_stats()

        # Fetch live rainfall data; fall back gracefully if offline
        rainfall = []
        if _fetch_rainfall:
            try:
                rainfall = _fetch_rainfall()
            except Exception as e:
                print(f"Rainfall fetch skipped: {e}")

        worst_zone = max(rainfall, key=lambda r: abs(r["anomaly_pct"]), default=None)
        rainfall_note = ""
        if worst_zone:
            rainfall_note = f"\n- Worst rainfall deficit: Zone {worst_zone['zone']} at {worst_zone['anomaly_pct']}% vs historical"

        narrative_prompt = f"""Season stats as of today:
- Total active farmers: {stats['total_farmers']:,}
- Buyback rate so far: {stats['buyback_rate']:.1%}
- High-risk farmers (score > 0.45): {stats['high_risk_count']:,}
- Procurement forecast: {stats['forecast_low']/1e6:.1f}M – {stats['forecast_high']/1e6:.1f}M kg
- Top concern zone: {stats['top_risk_zone']}{rainfall_note}

Write a 3-sentence executive narrative for this week's GNA leadership briefing."""

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            system=EXEC_SYSTEM,
            messages=[{"role": "user", "content": narrative_prompt}],
        )

        report = {
            "generated_at": datetime.utcnow().isoformat(),
            "season_summary": stats,
            "narrative": response.content[0].text,
            "zone_breakdown": self.farmer_store.get_zone_breakdown(),
            # Lifts aligned with GNA Analytics Showdown notebook §5 zone-controlled findings.
            # Inoculant (+486) is the highest-confidence lever — smallest cross-zone std.
            # Fungicide value is conservative; notebook headline (+952) has std exceeding mean.
            "input_effectiveness": {
                "inoculant":  {"yield_lift_kg_ha": 486, "adoption_pct": 0.71, "p_value": 0.001, "note": "Highest-confidence lift"},
                "fertilizer": {"yield_lift_kg_ha": 312, "adoption_pct": 0.68, "p_value": 0.002, "note": "Significant; amplified by training"},
                "fungicide":  {"yield_lift_kg_ha": 250, "adoption_pct": 0.44, "p_value": 0.180, "note": "Zone-III specific; high variance"},
                "seed_guard": {"yield_lift_kg_ha": 318, "adoption_pct": 0.55, "p_value": 0.050, "note": "Single-zone evidence only"},
            },
            "rainfall": rainfall,
        }

        # Write to file for dashboard to consume
        with open("dashboard/report.json", "w") as f:
            json.dump(report, f, indent=2)

        return report

    async def answer_question(self, question: str) -> str:
        """On-demand Q&A for the dashboard 'Ask the season' tab."""
        stats = self.farmer_store.get_season_stats()
        rainfall = []
        if _fetch_rainfall:
            try:
                rainfall = _fetch_rainfall()
            except Exception:
                pass

        context = json.dumps({"season": stats, "rainfall": rainfall}, indent=2)

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=EXEC_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Season data:\n{context}\n\nQuestion: {question}"
            }],
        )
        return response.content[0].text
