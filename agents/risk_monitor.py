"""
Risk Monitor Agent
------------------
Two modes:
  - Real-time: triggered when a farmer replies "need help"
  - Batch: nightly re-scoring of all active farmers
"""

import os
import anthropic
from tools.risk_scorer import compute_risk_score
from tools.field_agent_alert import send_field_agent_alert

RISK_THRESHOLD = 0.45


class RiskMonitorAgent:
    def __init__(self, farmer_store):
        self.farmer_store = farmer_store
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    async def run_realtime(self, event, farmer: dict):
        score = compute_risk_score(farmer)
        self.farmer_store.update_risk_score(farmer["farmer_id"], score)

        if score > RISK_THRESHOLD or farmer["season_number"] == 1:
            await send_field_agent_alert(
                agent_id=farmer["field_agent_id"],
                farmer=farmer,
                score=score,
                trigger="farmer_requested_help",
            )

    async def run_batch(self):
        farmers = self.farmer_store.get_all_active()
        alerts_fired = 0

        for farmer in farmers:
            old_score = farmer.get("risk_score", 0)
            new_score = compute_risk_score(farmer)
            self.farmer_store.update_risk_score(farmer["farmer_id"], new_score)

            # Only alert if score crossed threshold (avoid repeat alerts)
            if new_score > RISK_THRESHOLD and old_score <= RISK_THRESHOLD:
                await send_field_agent_alert(
                    agent_id=farmer["field_agent_id"],
                    farmer=farmer,
                    score=new_score,
                    trigger="nightly_batch_threshold_crossed",
                )
                alerts_fired += 1

        print(f"Risk batch complete. {len(farmers)} farmers scored. {alerts_fired} new alerts fired.")
