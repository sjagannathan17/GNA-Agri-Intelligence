"""
Farmer Chat Agent (tool-use loop)
---------------------------------
Replaces the previous single-shot Claude call with a tool-augmented agent
loop. Claude can now ask the system to:

  - search the live web for time-sensitive info (weather, market prices,
    pest outbreaks, fertilizer brand availability)
  - look up the farmer's GNA risk + yield analysis
  - look up zone-level rainfall vs the historical baseline
  - search the agronomy KB for crops / pests / diseases / diagnostics
  - pull peer benchmarks from the canonical master table (camp / district / zone)
  - get GNA buyback price + an open-market reference
  - tag a topic for the conversation thread (used by ConversationStore)
  - escalate to a field agent when needed

Everything runs end-to-end without real API keys: web_search falls back to
keyless DuckDuckGo, market_price has static fallbacks, and if
ANTHROPIC_API_KEY is the demo placeholder we emit a deterministic templated
reply instead of calling Claude. This keeps the demo experience identical
to the previous version while enabling production behaviour the moment
real keys are added.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from memory.conversation_store import ConversationStore
from memory.farmer_store import FarmerStore
from tools.agronomy_rag import lookup_agronomy
from tools.farmer_analysis import analyze_farmer
from tools.language_detect import detect_language, language_name
from tools.market_price import get_market_price
from tools.peer_benchmarks import get_peer_benchmarks
from tools.rainfall_fetcher import fetch_zone_rainfall
from tools.web_search import web_search
from whatsapp.client import send_message

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS = 6
MODEL_NAME = "claude-sonnet-4-6"
MAX_REPLY_TOKENS = 600


# ─── System prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """You are a friendly agricultural advisor for Good Nature Agro (GNA) in Zambia.
You help smallholder farmers via WhatsApp.

The farmer's preferred language is **{lang_name}**. You MUST reply in {lang_name}.
- If the farmer types in English, still reply in {lang_name} unless the farmer explicitly switches.
- Use natural rural Zambian phrasing — not a textbook translation.
- Numbers and units (kg/ha, ZMW) stay as-is — these are universally understood.
- Keep messages SHORT (under 120 words). Farmers read on small phone screens.
- Use simple language. Avoid jargon. Use the farmer's first name once.

You have tools available. PREFER tools over guessing.
- For questions about THIS farmer ('what's my risk?', 'what yield should I expect?'), call `get_farmer_analysis`.
- For 'how am I doing vs others?', call `get_peer_benchmarks` with the farmer's camp / district / zone.
- For 'is rain normal?' or 'will it rain?', call `get_zone_rainfall` for the farmer's zone.
- For pest / disease / variety / planting questions, call `lookup_agronomy_kb` first.
- For market or buyback price questions, call `get_market_price`.
- For news, weather forecasts, or anything time-sensitive that the agronomy KB can't answer, call `web_search`.
- ALWAYS call `set_topic` before answering so the conversation gets categorized.
- If the farmer expresses distress, repeated confusion, or asks for in-person help, call `escalate_to_field_agent` and tell the farmer a field agent will reach out.

You must NOT:
- Give medical advice
- Make promises about exact yields or prices
- Discuss loan repayment terms in detail (direct to GNA office)
- Use more than {max_turns} tool calls per question

Farmer profile and risk tier are provided in the first user message."""


# ─── Tool schemas (Anthropic tool-use format) ────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_farmer_analysis",
        "description": (
            "Compute this farmer's risk score, yield estimate, and prioritized "
            "recommendations using GNA's data. Use this for any question about "
            "the farmer's own performance, risk, or expected outcome."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_peer_benchmarks",
        "description": (
            "Look up median yield, top-decile yield, and adoption rates for "
            "farmers in the same camp / district / zone, so the farmer can "
            "compare themselves to peers. Pass the farmer's own yield_kg_ha "
            "to get their percentile rank in each cohort."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "camp_name":           {"type": "string", "description": "Optional camp name for the most specific cohort."},
                "district_name":       {"type": "string", "description": "Optional district name."},
                "zone":                {"type": "string", "description": "Agroecological zone (I, IIa, IIb, III, IV)."},
                "farmer_yield_kg_ha":  {"type": "number", "description": "Optional. The farmer's own yield, used to compute their percentile."},
            },
        },
    },
    {
        "name": "get_zone_rainfall",
        "description": (
            "Get current-season rainfall total + monthly breakdown for an "
            "agroecological zone, with the historical 5-season baseline and "
            "anomaly percentage. Use when farmers ask 'is rainfall normal?' "
            "or 'has it been a dry year?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "zone": {"type": "string", "description": "Zone code (I, IIa, IIb, III, IV)."},
            },
            "required": ["zone"],
        },
    },
    {
        "name": "lookup_agronomy_kb",
        "description": (
            "Search GNA's agronomy knowledge base by free-text keyword. "
            "Returns matches across crops, pests, diseases, and diagnostic "
            "symptoms. Use this for 'what should I do about yellow leaves?', "
            "'what is soybean rust?', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string", "description": "Free-text query, e.g. 'yellow leaves' or 'pod borer'."},
                "max_results": {"type": "integer", "description": "Optional. Defaults to 5."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_market_price",
        "description": (
            "Get GNA's contracted buyback price (gross + net-after-loan) and "
            "an open-market reference range. Use for any pricing question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "crop":                {"type": "string", "description": "soy_bean | groundnut | maize. Defaults to soy_bean."},
                "include_open_market": {"type": "boolean", "description": "If true, also returns open-market reference range."},
            },
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the live web for time-sensitive or external info that the "
            "agronomy KB doesn't cover (e.g. weather forecasts, recent pest "
            "outbreaks in Zambia, fertilizer brand availability). Returns "
            "structured snippets. Use sparingly — most questions can be "
            "answered from the other tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string", "description": "Search query."},
                "max_results": {"type": "integer", "description": "Optional. Defaults to 5."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "set_topic",
        "description": (
            "Tag the conversation turn with one topic for memory. Choose ONE: "
            "planting, pests, diseases, inputs, fertilizer, inoculant, "
            "weather, rainfall, drought, harvest, buyback, repayment, "
            "market, price, loan, training, general."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "One topic from the list above."},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "escalate_to_field_agent",
        "description": (
            "Trigger a field-agent alert when the farmer expresses distress, "
            "repeated confusion, or asks for in-person help. Pass a one-line "
            "reason. The farmer will be told that an agent will reach out."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Short reason."},
            },
            "required": ["reason"],
        },
    },
]


# ─── Agent ────────────────────────────────────────────────────────────────────

class FarmerChatAgent:
    def __init__(
        self,
        farmer_store: FarmerStore,
        conversation_store: ConversationStore,
    ):
        self.farmer_store = farmer_store
        self.conversation_store = conversation_store
        self._client = None
        self._anthropic_unavailable = self._is_demo_anthropic_key()

    @staticmethod
    def _is_demo_anthropic_key() -> bool:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        return (not key) or "placeholder" in key.lower() or "demo" in key.lower()

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        return self._client

    # ─── Public entrypoints ────────────────────────────────────────────────
    async def run(self, event, farmer: dict):
        text = (event.text or "").strip()
        if not text:
            return

        lang = detect_language(text, default="en")
        topic_default = "general"
        self.conversation_store.add(
            farmer["farmer_id"], "farmer", text,
            topic=topic_default, language=lang,
        )

        if self._anthropic_unavailable:
            reply = self._template_reply(text, farmer, lang)
            tool_log: list[dict[str, Any]] = []
            chosen_topic = topic_default
        else:
            reply, tool_log, chosen_topic = await self._run_tool_loop(text, farmer, lang, event)

        await send_message(farmer["phone"], reply)
        self.conversation_store.add(
            farmer["farmer_id"], "agent", reply,
            topic=chosen_topic, language=lang,
        )
        if tool_log:
            logger.info(f"chat tool log for farmer {farmer['farmer_id']}: {tool_log}")

    async def send_confirmation(self, event, farmer: dict):
        first = (farmer.get("name") or "Farmer").split()[0]
        msg = f"Great work, {first}! Keep it up. We'll check in again next time."
        await send_message(farmer["phone"], msg)
        self.conversation_store.add(
            farmer["farmer_id"], "agent", msg,
            topic="general",
            language=farmer.get("preferred_language"),
        )

    # ─── Tool-use loop ─────────────────────────────────────────────────────
    async def _run_tool_loop(
        self, text: str, farmer: dict, lang: str, event,
    ) -> tuple[str, list[dict[str, Any]], str]:
        """Run the Anthropic tool-use loop. Returns (reply, tool_log, chosen_topic)."""
        history = self.conversation_store.get_recent(farmer["farmer_id"], n=6)
        topic_history = self.conversation_store.get_topic_history(farmer["farmer_id"], lookback=10)

        system = SYSTEM_PROMPT_TEMPLATE.format(
            lang_name=language_name(lang), max_turns=MAX_TOOL_TURNS,
        )
        messages: list[dict[str, Any]] = self._build_messages(
            farmer, history, topic_history, text,
        )

        tool_log: list[dict[str, Any]] = []
        chosen_topic: str = "general"
        escalated_reason: str | None = None

        for turn in range(MAX_TOOL_TURNS):
            try:
                resp = self.client.messages.create(
                    model=MODEL_NAME,
                    max_tokens=MAX_REPLY_TOKENS,
                    system=system,
                    tools=TOOL_SCHEMAS,
                    messages=messages,
                )
            except Exception as e:
                logger.warning(f"Anthropic call failed on turn {turn}: {e!r}")
                return self._template_reply(text, farmer, lang), tool_log, chosen_topic

            stop_reason = getattr(resp, "stop_reason", None)
            content_blocks = list(resp.content)

            if stop_reason == "end_turn" or not any(
                b.type == "tool_use" for b in content_blocks if hasattr(b, "type")
            ):
                reply = "".join(b.text for b in content_blocks if getattr(b, "type", None) == "text")
                if escalated_reason and event:
                    await self._dispatch_escalation(farmer, escalated_reason)
                return (reply.strip() or self._template_reply(text, farmer, lang)), tool_log, chosen_topic

            # tool_use turn — execute every tool_use block, append tool_result.
            # Serialize blocks to plain dicts; passing the raw pydantic objects
            # back into the next create() call breaks on some SDK versions
            # with "by_alias: NoneType" TypeError.
            asst_content: list[dict[str, Any]] = []
            for b in content_blocks:
                btype = getattr(b, "type", None)
                if btype == "text":
                    asst_content.append({"type": "text", "text": b.text})
                elif btype == "tool_use":
                    asst_content.append({
                        "type":  "tool_use",
                        "id":    b.id,
                        "name":  b.name,
                        "input": b.input,
                    })
            messages.append({"role": "assistant", "content": asst_content})
            tool_results: list[dict[str, Any]] = []
            for block in content_blocks:
                if getattr(block, "type", None) != "tool_use":
                    continue
                name = block.name
                inputs = block.input or {}
                try:
                    result = self._execute_tool(name, inputs, farmer)
                except Exception as e:
                    logger.warning(f"tool {name} crashed: {e!r}")
                    result = {"error": f"tool_failed: {e!r}"}

                tool_log.append({"tool": name, "input": inputs})

                if name == "set_topic":
                    chosen_topic = (inputs.get("topic") or "general").strip().lower()
                if name == "escalate_to_field_agent":
                    escalated_reason = (inputs.get("reason") or "chat_agent_flagged")[:200]

                tool_results.append({
                    "type":         "tool_result",
                    "tool_use_id":  block.id,
                    "content":      json.dumps(result, default=str)[:6000],
                })
            messages.append({"role": "user", "content": tool_results})

        # Tool-turn cap hit — return graceful fallback
        logger.warning(f"Tool-use cap hit for farmer {farmer.get('farmer_id')}")
        return (
            self._fallback_after_cap(farmer, lang),
            tool_log,
            chosen_topic,
        )

    # ─── Tool dispatch ─────────────────────────────────────────────────────
    def _execute_tool(self, name: str, inputs: dict, farmer: dict) -> Any:
        if name == "get_farmer_analysis":
            return analyze_farmer(farmer)
        if name == "get_peer_benchmarks":
            return get_peer_benchmarks(
                camp_name=inputs.get("camp_name") or farmer.get("camp_name"),
                district_name=inputs.get("district_name") or farmer.get("district_name"),
                zone=inputs.get("zone") or farmer.get("zone"),
                farmer_yield_kg_ha=inputs.get("farmer_yield_kg_ha"),
            )
        if name == "get_zone_rainfall":
            zone = inputs.get("zone") or farmer.get("zone") or "IIa"
            try:
                return fetch_zone_rainfall(zone)
            except Exception as e:
                return {"error": f"rainfall_unavailable: {e!r}", "zone": zone}
        if name == "lookup_agronomy_kb":
            return lookup_agronomy(
                inputs.get("query", ""),
                max_results=int(inputs.get("max_results") or 5),
            )
        if name == "get_market_price":
            return get_market_price(
                inputs.get("crop") or farmer.get("crop") or "soy_bean",
                include_open_market=bool(inputs.get("include_open_market", True)),
            )
        if name == "web_search":
            return web_search(
                inputs.get("query", ""),
                max_results=int(inputs.get("max_results") or 5),
            )
        if name == "set_topic":
            return {"ack": True, "topic": (inputs.get("topic") or "general").lower()}
        if name == "escalate_to_field_agent":
            return {
                "ack": True,
                "reason": inputs.get("reason"),
                "note": "The farmer will be told a field agent will reach out within 24 hours.",
            }
        return {"error": f"unknown_tool: {name}"}

    async def _dispatch_escalation(self, farmer: dict, reason: str):
        """Forward the escalation to the orchestrator path so a field agent
        is notified with the conversation summary."""
        try:
            from agents.orchestrator import Event
            from tools.field_agent_alert import send_escalation_alert

            recent = self.conversation_store.get_recent(farmer["farmer_id"], n=5)
            summary = "\n".join(
                f"  [{ 'F' if m['role']=='farmer' else 'GNA'}] {(m.get('message') or '').strip()[:120]}"
                for m in recent if (m.get("message") or "").strip()
            ) or "(no recent messages)"
            await send_escalation_alert(
                farmer, reason=reason, conversation_summary=summary,
            )
        except Exception as e:
            logger.warning(f"escalation dispatch failed: {e!r}")

    # ─── Helpers ───────────────────────────────────────────────────────────
    def _build_messages(
        self, farmer: dict, history: list[dict], topic_history: dict, current_text: str,
    ) -> list[dict[str, Any]]:
        recent_topic = topic_history.get("recent_topic")
        topic_continuity = (
            f"\nRecent conversation topic with this farmer: {recent_topic}." if recent_topic else ""
        )

        farmer_block = (
            "Farmer profile (use this to ground your answers):\n"
            f"- Name: {farmer.get('name')}\n"
            f"- Crop: {farmer.get('crop', 'soy bean')}\n"
            f"- Zone: {farmer.get('zone')}\n"
            f"- Camp: {farmer.get('camp_name', '?')}\n"
            f"- District: {farmer.get('district_name', '?')}\n"
            f"- Season number: {farmer.get('season_number', 1)} "
            f"({'first season — extra support needed' if farmer.get('season_number', 1) == 1 else 'experienced'})\n"
            f"- Has inoculant: {'Yes' if farmer.get('has_inoculant') else 'No'}\n"
            f"- Has fertilizer: {'Yes' if farmer.get('has_fertilizer') else 'No'}\n"
            f"- Days_to_plant: {farmer.get('days_to_plant', 0)}\n"
            f"- Risk score: {(farmer.get('risk_score') or 0):.2f} "
            f"(tier: {farmer.get('risk_tier', 'Low')})"
            f"{topic_continuity}"
        )

        messages: list[dict[str, Any]] = [
            {"role": "user",      "content": farmer_block},
            {"role": "assistant", "content": "Got it. I have the farmer profile. What is their question?"},
        ]

        for msg in history[:-1]:  # exclude the just-logged current_text
            role = "user" if msg["role"] == "farmer" else "assistant"
            content = (msg.get("message") or "").strip()
            if content:
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": current_text})
        return messages

    @staticmethod
    def _template_reply(text: str, farmer: dict, lang: str) -> str:
        """Deterministic offline fallback used when ANTHROPIC_API_KEY is the
        demo placeholder OR when Claude / tool calls all fail. Keeps demo
        flow working without crashing."""
        first = (farmer.get("name") or "Farmer").split()[0]
        body = (
            "Thanks for your message. Our team is offline right now but a field "
            "agent will get back to you shortly. In the meantime, reply 1 if you "
            "want a check-in call, 2 if it's urgent, or 3 to skip."
        )
        if lang == "bem":
            body = (
                "Twatotela ku message yenu. Ifwe tatuli online lelo lelo, lelo "
                "field agent akamulanga. Asuke 1 ku check-in, 2 nga cili "
                "urgent, 3 ku skip."
            )
        elif lang == "nya":
            body = (
                "Zikomo ndi mlaliki wanu. Nthawi ino sitili online, koma mthandizi "
                "wakumudzi adzakulankhulani. Yankhani 1 kuti tikuyendereni, "
                "2 ngati ndi mwadzidzidzi, kapena 3 kuti mukane."
            )
        return f"Hi {first}, {body}"

    @staticmethod
    def _fallback_after_cap(farmer: dict, lang: str) -> str:
        first = (farmer.get("name") or "Farmer").split()[0]
        if lang == "bem":
            return (
                f"Mwapoleni {first}, ico mwafwaya cikulu sana. Field agent "
                "akamulanga ukuti afumye amapeshyo."
            )
        if lang == "nya":
            return (
                f"Moni {first}, funso lanu lakhala lalikulu. Mthandizi wakumudzi "
                "adzayankhulanitsa nanu kuti akuthandizeni bwino."
            )
        return (
            f"Hi {first}, your question needs more detail than I can pull together "
            "right now. A field agent will reach out within 24 hours to help."
        )
