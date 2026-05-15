"""
Orchestrator Agent
------------------
Central router for all events. Every incoming WhatsApp message,
scheduled nudge, and report request passes through here.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from memory.conversation_store import ConversationStore
from memory.farmer_store import FarmerStore

logger = logging.getLogger(__name__)

EventType = Literal[
    "WHATSAPP_MESSAGE",
    "WHATSAPP_REPLY",
    "SCHEDULED_NUDGE",
    "RISK_BATCH",
    "EXEC_REPORT_REQ",
    "FARMER_ESCALATION",
]


@dataclass
class Event:
    type: EventType
    farmer_phone: str | None = None
    farmer_id: str | None = None
    text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Orchestrator:
    def __init__(self):
        self.farmer_store = FarmerStore()
        self.conversation_store = ConversationStore()
        self._init_agents()

    def _init_agents(self):
        from agents.exec_briefing import ExecBriefingAgent
        from agents.farmer_chat import FarmerChatAgent
        from agents.nudge_scheduler import NudgeSchedulerAgent
        from agents.risk_monitor import RiskMonitorAgent

        self.nudge_agent = NudgeSchedulerAgent(self.farmer_store)
        self.chat_agent = FarmerChatAgent(self.farmer_store, self.conversation_store)
        self.risk_agent = RiskMonitorAgent(self.farmer_store)
        self.exec_agent = ExecBriefingAgent(self.farmer_store)

    async def handle(self, event: Event):
        logger.info(f"Orchestrator received event: {event.type}")

        match event.type:
            case "WHATSAPP_MESSAGE":
                await self._handle_whatsapp(event)
            case "SCHEDULED_NUDGE":
                await self.nudge_agent.run(event)
            case "RISK_BATCH":
                await self.risk_agent.run_batch()
            case "EXEC_REPORT_REQ":
                await self.exec_agent.run(event)
            case "FARMER_ESCALATION":
                await self._handle_escalation(event)
            case _:
                logger.warning(f"Unknown event type: {event.type}")

    async def _handle_whatsapp(self, event: Event):
        farmer = self.farmer_store.get_by_phone(event.farmer_phone)
        if not farmer:
            logger.warning(f"Unknown phone: {event.farmer_phone}")
            return
        event.farmer_id = farmer["farmer_id"]

        # Numbered nudge replies stay on the structured path
        if event.text and event.text.strip() in ["1", "2", "3"]:
            await self._handle_nudge_reply(event, farmer)
            return

        # Free-text questions go to the tool-augmented chat agent
        await self.chat_agent.run(event, farmer)

    async def _handle_nudge_reply(self, event: Event, farmer: dict):
        reply_map = {"1": "done", "2": "help", "3": "skip"}
        reply = reply_map.get((event.text or "").strip(), "skip")

        # Log the structured reply rather than the raw "1"/"2"/"3"
        self.conversation_store.add(
            farmer["farmer_id"], "farmer", event.text or "",
            topic="nudge_reply", language=farmer.get("preferred_language"),
        )
        self.farmer_store.log_nudge_response(farmer["farmer_id"], reply)

        if reply == "help":
            # Refresh farmer to read updated consecutive_help counter (best-effort)
            refreshed = self.farmer_store.get(farmer["farmer_id"])
            if isinstance(refreshed, dict):
                farmer = refreshed
            tier = (farmer.get("risk_tier") or "Low").capitalize() if isinstance(farmer, dict) else "Low"

            from agents.nudge_scheduler import RISK_MODIFIERS
            threshold = RISK_MODIFIERS.get(tier, RISK_MODIFIERS["Low"]).get("auto_escalate_after_help", 3)
            try:
                help_count = int(farmer.get("consecutive_help") or 0) if isinstance(farmer, dict) else 0
            except (TypeError, ValueError):
                help_count = 0
            if help_count >= threshold:
                await self.handle(Event(
                    type="FARMER_ESCALATION",
                    farmer_id=farmer["farmer_id"],
                    farmer_phone=farmer["phone"],
                    metadata={"reason": f"consecutive_help_replies>={threshold}"},
                ))
                self.farmer_store.reset_consecutive_help(farmer["farmer_id"])
            # Also kick off real-time risk re-scoring
            await self.risk_agent.run_realtime(event, farmer)
        elif reply == "done":
            await self.chat_agent.send_confirmation(event, farmer)

    async def _handle_escalation(self, event: Event):
        """Send a field-agent alert with a short conversation summary."""
        from tools.field_agent_alert import send_escalation_alert

        farmer = self.farmer_store.get(event.farmer_id) if event.farmer_id else None
        if not farmer and event.farmer_phone:
            farmer = self.farmer_store.get_by_phone(event.farmer_phone)
        if not farmer:
            logger.warning(f"Escalation skipped: farmer not found for event={event}")
            return

        # Build a compact conversation summary from the last 5 turns
        recent = self.conversation_store.get_recent(farmer["farmer_id"], n=5)
        summary_lines = []
        for msg in recent:
            who = "F" if msg["role"] == "farmer" else "GNA"
            text = (msg.get("message") or "").replace("\n", " ").strip()
            if text:
                summary_lines.append(f"  [{who}] {text[:120]}")
        summary = "\n".join(summary_lines) if summary_lines else "(no recent messages)"

        reason = (event.metadata or {}).get("reason", "chat_agent_flagged")
        await send_escalation_alert(
            farmer,
            reason=reason,
            conversation_summary=summary,
        )
        logger.info(f"Escalation sent for farmer {farmer['farmer_id']} (reason={reason})")
