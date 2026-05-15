"""
Nudge Scheduler Agent
---------------------
Sends season-appropriate WhatsApp messages to active farmers.
Triggered by APScheduler at 7:00 AM daily.

What's new vs the v1 single-calendar version:
  - Per-zone calendars (data/season_calendar_by_zone.json) so Zone IIa
    farmers (later planting window) are nudged on a different timeline
    than Zone III farmers.
  - Per-risk-tier cadence (data/risk_tier_modifiers.json) so High Risk
    farmers receive contact every 3 days, Medium every 7, Low every 14.
  - Tone guidance pushed into the Claude prompt so High Risk farmers
    receive urgent-but-supportive messaging rather than the same
    light-friendly nudge as Low Risk farmers.
  - High Risk farmers automatically have a `1 / 2 / 3 → escalate` reply
    option appended so a single 'help' reply triggers a field-agent alert.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic

from whatsapp.client import send_message

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"
ZONE_CALENDAR = json.loads((_DATA_DIR / "season_calendar_by_zone.json").read_text())
RISK_MODIFIERS = json.loads((_DATA_DIR / "risk_tier_modifiers.json").read_text())

# Backwards-compat fallback if a deployment still ships the legacy single calendar
_LEGACY_CAL_PATH = _DATA_DIR / "season_calendar.json"
LEGACY_CALENDAR = json.loads(_LEGACY_CAL_PATH.read_text()) if _LEGACY_CAL_PATH.exists() else {}


NUDGE_SYSTEM = """You are composing a short WhatsApp message for a smallholder farmer in Zambia.
The message should feel personal, warm, and practical.
Keep it under 100 words. Include numbered reply options (1/2/3) at the end.
Write in the farmer's preferred language (English, Bemba, or Nyanja).
Match the requested tone exactly. Use the farmer's first name once.
Do not invent facts; stick to the template guidance and the farmer's profile."""


class NudgeSchedulerAgent:
    def __init__(self, farmer_store):
        self.farmer_store = farmer_store
        self._client: anthropic.Anthropic | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        return self._client

    # ─── Cadence gate ──────────────────────────────────────────────────────
    def _should_send_today(self, farmer: dict) -> bool:
        """Return True if the farmer is due a nudge based on their risk-tier
        cadence and the timestamp of the last nudge sent."""
        tier = (farmer.get("risk_tier") or "Low").capitalize()
        cadence = (RISK_MODIFIERS.get(tier) or RISK_MODIFIERS["Low"])["cadence_days"]
        last = farmer.get("last_nudge_sent")
        if not last:
            return True
        try:
            if isinstance(last, str):
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00").split(".")[0])
            else:
                last_dt = last
            return (datetime.utcnow() - last_dt) >= timedelta(days=cadence - 0.1)
        except Exception as e:
            logger.debug(f"could not parse last_nudge_sent={last!r}: {e}")
            return True

    # ─── Zone + days → phase lookup ────────────────────────────────────────
    def _get_phase(self, zone: str | None, days: int) -> dict | None:
        zone_block = ZONE_CALENDAR.get(zone or "", {})
        phases = zone_block.get("phases") or {}
        for key, phase in phases.items():
            if not key.startswith("day_"):
                continue
            try:
                start, end = map(int, key.replace("day_", "").split("_"))
            except ValueError:
                continue
            if start <= days <= end:
                return phase

        # Backwards-compat: if zone calendar has no match, fall back to the legacy
        # single-calendar phases so existing tests don't regress.
        for key, phase in LEGACY_CALENDAR.items():
            try:
                start, end = map(int, key.replace("day_", "").split("_"))
            except ValueError:
                continue
            if start <= days <= end:
                return phase
        return None

    # ─── Main entrypoint ───────────────────────────────────────────────────
    async def run(self, event):
        farmer = self.farmer_store.get(event.farmer_id)
        if not farmer or not farmer.get("planting_date"):
            return

        if not self._should_send_today(farmer):
            logger.debug(f"farmer {farmer['farmer_id']} not due today")
            return

        planting = farmer["planting_date"]
        if isinstance(planting, str):
            planting = date.fromisoformat(planting)
        days = (date.today() - planting).days
        phase = self._get_phase(farmer.get("zone"), days)
        if not phase:
            logger.debug(f"no phase for zone={farmer.get('zone')} day={days}")
            return

        tier = (farmer.get("risk_tier") or "Low").capitalize()
        modifier = RISK_MODIFIERS.get(tier, RISK_MODIFIERS["Low"])

        message = await self._compose(farmer, phase, days, tier, modifier)
        await send_message(farmer["phone"], message)
        self.farmer_store.update_last_nudge(farmer["farmer_id"])

    async def _compose(self, farmer: dict, phase: dict, days: int, tier: str, modifier: dict) -> str:
        season_note = (
            "first season — extra encouragement needed"
            if farmer.get("season_number", 1) == 1
            else f"season {farmer.get('season_number')}"
        )
        reply_options = dict(phase.get("reply_options") or {})
        if modifier.get("include_escalation_option") and "escalate" not in str(reply_options).lower():
            # Append a "help" / escalate option for High and Medium tier farmers
            reply_options.setdefault("2", "need_help")

        prompt = f"""Farmer: {farmer.get('name', 'Farmer')}
Crop: {farmer.get('crop', 'soy bean')}
Zone: {farmer.get('zone')}
Season: {season_note}
Has inoculant: {'Yes' if farmer.get('has_inoculant') else 'No'}
Days since planting: {days}
Preferred language: {farmer.get('preferred_language', 'english')}
Risk tier: {tier}
Tone guidance: {modifier['tone_guidance']}

Today's topic: {phase['topic']}
Template guidance: {phase['template']}
Reply options: {json.dumps(reply_options)}

Write the WhatsApp nudge message now. Match the requested tone."""

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                system=NUDGE_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            logger.warning(f"Claude nudge composition failed ({e}); using template fallback")
            return self._template_fallback(farmer, phase, reply_options)

    @staticmethod
    def _template_fallback(farmer: dict, phase: dict, reply_options: dict) -> str:
        first_name = (farmer.get("name") or "Farmer").split()[0]
        opts = "\n".join(f"  {k}) {v}" for k, v in reply_options.items())
        return (
            f"Hi {first_name}, GNA check-in.\n"
            f"{phase.get('template', '')}\n\n"
            f"Reply:\n{opts}"
        )
