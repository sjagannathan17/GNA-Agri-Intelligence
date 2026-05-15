"""
Nudge Composer Tool
-------------------
Composes a personalized WhatsApp nudge message for a farmer given
their profile and the current season calendar phase.

Used by NudgeSchedulerAgent; can also be called standalone for testing.
"""

import os
import json
import anthropic

NUDGE_SYSTEM = """You are composing a short WhatsApp message for a smallholder farmer in Zambia.
The message should feel personal, warm, and practical.
Keep it under 100 words. Include numbered reply options (1/2/3) at the end.
Write in the farmer's preferred language (english, bemba, or nyanja)."""

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def compose_nudge(farmer: dict, phase: dict, days_since_planting: int) -> str:
    """
    Returns a personalized nudge message string.

    farmer: dict from FarmerStore (name, crop, zone, season_number, has_inoculant, risk_score, preferred_language)
    phase: dict from season_calendar.json (phase, topic, template, reply_options)
    days_since_planting: integer
    """
    language = farmer.get("preferred_language", "english")
    season_note = "first season — extra encouragement needed" if farmer.get("season_number", 1) == 1 else f"season {farmer.get('season_number')}"
    risk_note = "HIGH RISK farmer — keep message extra supportive" if farmer.get("risk_score", 0) > 0.45 else ""

    prompt = f"""Farmer: {farmer['name']}
Crop: {farmer.get('crop', 'soy bean')}, Zone: {farmer.get('zone', '')}, {season_note}
Has inoculant: {'Yes' if farmer.get('has_inoculant') else 'No'}
Days since planting: {days_since_planting}
Language: {language}
Today's topic: {phase['topic']}
Template guidance: {phase['template']}
Reply options: {json.dumps(phase['reply_options'])}
{risk_note}

Write the WhatsApp nudge message now."""

    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        system=NUDGE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
