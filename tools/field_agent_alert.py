"""
Field Agent Alert Tool
----------------------
Sends a WhatsApp message to a field agent when a farmer is flagged.

Two entrypoints:
  - send_field_agent_alert(...)        — risk-monitor batch path (legacy)
  - send_escalation_alert(...)         — chat-agent escalation path (new)

The escalation path includes a short conversation summary and the farmer's
current risk tier so the field agent has enough context to call the
farmer back with a clear ask, not a cold "what's wrong?" call.
"""

from __future__ import annotations

import logging

from memory.farmer_store import FarmerStore
from whatsapp.client import send_message

logger = logging.getLogger(__name__)
_store = FarmerStore()


async def send_field_agent_alert(agent_id: str, farmer: dict, score: float, trigger: str):
    """Risk-monitor batch alert (numerical trigger, e.g. score crossed threshold)."""
    agent = _store.get_agent(agent_id)
    if not agent:
        logger.warning(f"send_field_agent_alert: no agent with id={agent_id}")
        return

    risk_label = "HIGH" if score > 0.6 else "MEDIUM"
    message = (
        f"*GNA Alert — Action Needed*\n\n"
        f"Farmer: {farmer['name']} (#{farmer['farmer_id']})\n"
        f"Risk: *{risk_label}* (score: {score:.2f})\n"
        f"Season: {farmer['season_number']} | Zone: {farmer['zone']}\n"
        f"Trigger: {trigger.replace('_', ' ')}\n\n"
        f"Please call or visit this farmer today."
    )
    await send_message(agent["phone"], message)


async def send_escalation_alert(
    farmer: dict,
    *,
    reason: str,
    conversation_summary: str | None = None,
    agent_id: str | None = None,
) -> bool:
    """Chat-agent escalation. Resolves the assigned field agent (or falls back
    to any agent in the farmer's zone) and sends a richer message that
    includes a one-paragraph conversation summary.

    Returns True if a message was queued, False if no agent could be resolved.
    """
    agent = None
    if agent_id:
        agent = _store.get_agent(agent_id)
    if not agent:
        agent_id_from_farmer = farmer.get("field_agent_id")
        if agent_id_from_farmer:
            agent = _store.get_agent(agent_id_from_farmer)
    if not agent:
        agent = _store.get_zone_field_agent(farmer.get("zone") or "")
    if not agent:
        logger.warning(
            f"send_escalation_alert: no agent for farmer {farmer.get('farmer_id')} "
            f"in zone {farmer.get('zone')}"
        )
        return False

    tier = (farmer.get("risk_tier") or "Unknown").upper()
    risk_score = farmer.get("risk_score") or 0.0
    summary = (conversation_summary or "").strip() or "(no conversation summary)"

    message = (
        f"*GNA Chat Escalation — {tier} RISK*\n\n"
        f"Farmer: {farmer.get('name', 'Unknown')} (#{farmer.get('farmer_id')})\n"
        f"Phone:  {farmer.get('phone', '')}\n"
        f"Zone:   {farmer.get('zone')} | Season: {farmer.get('season_number')}\n"
        f"Score:  {risk_score:.2f}  ({tier} tier)\n"
        f"Reason: {reason.replace('_', ' ')}\n\n"
        f"Recent conversation:\n{summary}\n\n"
        f"Please reach out to this farmer within 24 hours."
    )
    await send_message(agent["phone"], message)
    return True
