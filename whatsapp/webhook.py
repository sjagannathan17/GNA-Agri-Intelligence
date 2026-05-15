"""
WhatsApp Webhook — FastAPI receiver
------------------------------------
Receives incoming messages from Meta's WhatsApp Business Cloud API.
Expose this with ngrok for local dev, or deploy to Railway/Render.
"""

import json
import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from agents.orchestrator import Orchestrator, Event

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
orchestrator = Orchestrator()
VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "gna_verify_token")


@app.get("/webhook")
async def verify_webhook(request: Request):
    """Meta sends a GET to verify the webhook on first setup."""
    params = dict(request.query_params)
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == VERIFY_TOKEN
    ):
        return int(params["hub.challenge"])
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive_message(request: Request):
    body = await request.json()

    try:
        entry = body["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        messages = value.get("messages", [])

        for msg in messages:
            if msg.get("type") == "text":
                event = Event(
                    type="WHATSAPP_MESSAGE",
                    farmer_phone=msg["from"],
                    text=msg["text"]["body"],
                )
                await orchestrator.handle(event)

    except (KeyError, IndexError):
        pass  # Status updates, delivery receipts — ignore

    return {"status": "ok"}


@app.post("/api/chat")
async def farmer_chat_api(request: Request):
    """Dashboard chat — runs the tool-augmented Claude agent with all six tools.

    Backwards-compatible with the old endpoint: still returns `{reply: str}`.
    Now also returns `tools_used: [...]` and `language: str` for the dashboard
    to optionally display.

    Returns HTTP 503 when Claude is unavailable so the dashboard's smart-reply
    fallback fires instead of showing the error in the chat.
    """
    from fastapi.responses import JSONResponse

    body = await request.json()

    # Build a farmer dict that the tool-use loop and tools both understand.
    farmer = {
        "farmer_id":          body.get("farmer_id") or "DASH",
        "name":               body.get("farmer_name", "Farmer"),
        "phone":              body.get("phone", "+260000000000"),
        "zone":               body.get("zone") or "IIa",
        "camp_name":          body.get("camp_name") or "Mwandi Hub",
        "district_name":      body.get("district_name") or "Mkushi",
        "season_number":      int(body.get("season", 1) or 1),
        "crop":               body.get("crop", "soy_bean"),
        "has_inoculant":      bool(body.get("has_inoculant")),
        "has_fertilizer":     bool(body.get("has_fertilizer")),
        "days_to_plant":      int(body.get("days_to_plant", 0) or 0),
        "total_hectares":     float(body.get("total_hectares", 1.0) or 1.0),
        "risk_score":         float(body.get("risk_score", 0.0) or 0.0),
        "risk_tier":          body.get("risk_tier") or _derive_tier(body.get("risk_score", 0.0)),
        "preferred_language": body.get("preferred_language") or "english",
        "nudge_responses":    body.get("nudge_responses") or {"total": 0, "done": 0, "help": 0, "skip": 0},
    }
    user_message = (body.get("message") or "").strip()
    if not user_message:
        return {"reply": "Please type a message.", "tools_used": [], "language": "en"}

    # Pick the language Claude should reply in. We always respect the language
    # of the actual message — if the farmer writes in English, reply in English,
    # even when their stored preferred_language is Bemba/Nyanja/etc. This makes
    # the system feel responsive instead of locked into a language.
    # Short / ambiguous messages default to English (safe baseline); detected
    # Bemba or Nyanja always overrides. The farmer's `preferred_language` is
    # still passed to Claude as context so it knows their background language.
    from tools.language_detect import detect_language

    lang = detect_language(user_message, default="en")

    # Run the tool-use loop (same as chat_repl.py)
    reply, tools_used, ok = await _run_chat_tool_loop(user_message, farmer, lang)
    if not ok:
        # Claude is unavailable (no credits, rate limit, etc).
        # Run our deterministic tool-router fallback so the answer is still
        # grounded in real data (master_table peer benchmarks, live rainfall,
        # agronomy KB, market prices, Tavily web search) instead of hardcoded
        # frontend strings.
        fallback_reply, fallback_tools = _run_router_fallback(user_message, farmer, lang)
        return {
            "reply":      fallback_reply,
            "tools_used": fallback_tools,
            "language":   lang,
            "mode":       "offline-router",
            "note":       "Claude unavailable; reply composed deterministically from tool outputs.",
        }
    return {
        "reply":      reply,
        "tools_used": tools_used,
        "language":   lang,
        "mode":       "claude-tool-use",
    }


def _run_router_fallback(message: str, farmer: dict, lang: str):
    """Deterministic keyword router → tool execution → reply synthesis.
    Used when Claude is unavailable. Mirrors chat_repl.py's demo router but
    runs server-side so the dashboard frontend can stay simple."""
    ql = (message or "").lower().strip()
    name = (farmer.get("name") or "Farmer").split()[0]

    calls: list[tuple[str, dict]] = []
    if any(k in ql for k in ["my risk", "my yield", "what should i", "why am i",
                             "what about me", "how am i doing", "what's my",
                             "expected yield", "should i", "risk score"]):
        calls.append(("get_farmer_analysis", {}))
    if any(k in ql for k in ["compared", "other farmers", "in my camp", "vs others",
                             "average farmer", "best in", "top farmers", "peer",
                             "neighbours", "neighbors", "compare"]):
        calls.append(("get_peer_benchmarks", {}))
    if any(k in ql for k in ["rain", "rainfall", "drought", "wet", "dry", "weather"]):
        calls.append(("get_zone_rainfall", {}))
    if any(k in ql for k in ["pest", "aphid", "borer", "rust", "yellow", "leaves",
                             "spots", "disease", "fungus", "insect", "bug",
                             "stunted", "holes", "brown", "withering"]):
        calls.append(("lookup_agronomy_kb", {"query": message}))
    if any(k in ql for k in ["price", "buyback", "kwacha", "zmw", "earn",
                             "selling", "sell", "market", "trader"]):
        calls.append(("get_market_price", {}))
    if any(k in ql for k in ["news", "outbreak", "fertilizer brand", "current",
                             "latest", "today's", "report", "warning", "advisory",
                             "forecast"]):
        calls.append(("web_search", {"query": message + " Zambia farming"}))

    if not calls:
        # Default — agronomy KB on whatever the farmer typed
        calls.append(("lookup_agronomy_kb", {"query": message}))

    executed: list[dict] = []
    parts: list[str] = []
    for tool_name, inputs in calls:
        try:
            result = _execute_tool(tool_name, inputs, farmer)
        except Exception as e:
            result = {"error": f"tool_failed: {e!r}"}
        executed.append({"tool": tool_name, "input": inputs})

        # Synthesize a paragraph per tool — short, plain English, real numbers.
        if tool_name == "get_farmer_analysis":
            tier = result.get("risk_tier")
            score = result.get("risk_score")
            yld = result.get("yield_estimate_kg_ha")
            recs = result.get("recommendations", [])[:2]
            rec_lines = "\n".join(f"  • {r['title']}" for r in recs)
            parts.append(
                f"*Your risk:* {tier} (score {score:.2f}). "
                f"Expected yield ~{yld:.0f} kg/ha.\n*Top actions:*\n{rec_lines}"
            )
        elif tool_name == "get_peer_benchmarks":
            cs = result.get("camp_stats")
            zs = result.get("zone_stats")
            block = cs if (cs and cs.get("median_yield_kg_ha") is not None) else zs
            if block and block.get("median_yield_kg_ha") is not None:
                top = block.get("top10_yield_threshold_kg_ha")
                inoc = (block.get("inoculant_adoption") or 0) * 100
                top_text = f"; top 10% reach {top:.0f}+ kg/ha" if top else ""
                parts.append(
                    f"*Peer benchmark — {block['label_value']}:* median yield "
                    f"{block['median_yield_kg_ha']:.0f} kg/ha across "
                    f"{block['n_farmers']:,} farmers{top_text}. Inoculant "
                    f"adoption {inoc:.0f}%."
                )
        elif tool_name == "get_zone_rainfall":
            if "error" in result:
                parts.append("Live rainfall unavailable right now.")
            else:
                anom = result.get("anomaly_pct", 0)
                trend = "above" if anom > 5 else "below" if anom < -5 else "near"
                parts.append(
                    f"*Rainfall — Zone {result['zone']}:* "
                    f"{result['season_total_mm']}mm season-to-date vs "
                    f"5-yr average of {result['historical_avg_mm']}mm "
                    f"({anom:+.0f}%, {trend} normal)."
                )
        elif tool_name == "lookup_agronomy_kb":
            matches = result.get("matches") or []
            if matches:
                top = matches[0]
                payload = top["payload"]
                if isinstance(payload, dict) and "action" in payload:
                    parts.append(
                        f"*{top['key'].replace('_', ' ').title()}:* "
                        f"{payload.get('likely_cause', '')}. "
                        f"_Action:_ {payload['action']}"
                    )
                elif isinstance(payload, dict) and "common_pests" in payload:
                    pests = ", ".join(payload.get("common_pests", [])[:3])
                    diseases = ", ".join(payload.get("common_diseases", [])[:3])
                    parts.append(
                        f"*{top['key'].replace('_', ' ').title()}:* "
                        f"common pests — {pests}; common diseases — {diseases}."
                    )
                else:
                    parts.append(f"KB hit: {top['key']} (relevance {top.get('score', 0)}).")
            else:
                parts.append("I don't have specific guidance in my agronomy KB for that yet.")
        elif tool_name == "get_market_price":
            gna = result.get("gna_buyback") or {}
            om = result.get("open_market") or {}
            line = (
                f"*GNA buyback ({result.get('crop')}):* "
                f"{gna.get('gross_per_kg')} ZMW/kg gross, "
                f"~{gna.get('net_after_loan_per_kg')} ZMW/kg net after loan."
            )
            if om:
                line += (
                    f"\n_Open market reference:_ {om.get('low_per_kg')}–"
                    f"{om.get('high_per_kg')} ZMW/kg ({om.get('as_of')})."
                )
            parts.append(line)
        elif tool_name == "web_search":
            results = result.get("results") or []
            provider = result.get("provider")
            if results:
                top = results[0]
                snippet = (top.get("snippet") or "")[:160]
                parts.append(
                    f"*Web search ({provider}):* {top.get('title')} — "
                    f"{snippet}{'…' if len(snippet) == 160 else ''}"
                )
            else:
                parts.append("No recent web results found.")

    body = "\n\n".join(parts) or "Let me ask a field agent to follow up with you."

    # Language prefix
    if lang == "bem":
        prefix = f"Mwapoleni {name}!\n\n"
    elif lang == "nya":
        prefix = f"Moni {name}!\n\n"
    else:
        prefix = f"Hi {name}, "

    return (prefix + body, executed)


def _derive_tier(score: float) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        s = 0.0
    if s >= 0.35:
        return "High"
    if s >= 0.20:
        return "Medium"
    return "Low"


async def _run_chat_tool_loop(message: str, farmer: dict, lang: str):
    """Anthropic tool-use loop — returns (reply_text, tools_used_list, ok_bool).
    `ok` is False when Claude call fails so the caller can return 503."""
    import logging as _lg
    import anthropic
    from agents.farmer_chat import MAX_TOOL_TURNS, MODEL_NAME, SYSTEM_PROMPT_TEMPLATE, TOOL_SCHEMAS
    from tools.language_detect import language_name

    _log = _lg.getLogger("chat_loop")
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    system = SYSTEM_PROMPT_TEMPLATE.format(
        lang_name=language_name(lang), max_turns=MAX_TOOL_TURNS,
    )
    farmer_block = (
        "Farmer profile:\n"
        f"- Name: {farmer['name']}\n"
        f"- Crop: {farmer['crop']}\n"
        f"- Zone: {farmer['zone']}\n"
        f"- Camp: {farmer['camp_name']}\n"
        f"- District: {farmer['district_name']}\n"
        f"- Season: {farmer['season_number']} "
        f"({'first season' if farmer['season_number'] == 1 else 'experienced'})\n"
        f"- Has inoculant: {'Yes' if farmer['has_inoculant'] else 'No'}\n"
        f"- Has fertilizer: {'Yes' if farmer['has_fertilizer'] else 'No'}\n"
        f"- Risk: {farmer['risk_tier']} (score {farmer['risk_score']:.2f})"
    )
    messages = [
        {"role": "user",      "content": farmer_block},
        {"role": "assistant", "content": "Got it. What is the farmer's question?"},
        {"role": "user",      "content": message},
    ]

    tools_used: list[dict] = []
    final_text = ""
    for _ in range(MAX_TOOL_TURNS):
        try:
            resp = client.messages.create(
                model=MODEL_NAME, max_tokens=600,
                system=system, tools=TOOL_SCHEMAS, messages=messages,
            )
        except Exception as e:
            # Surface a short, human reason for the dashboard logs.
            err_msg = str(e)
            _log.warning(f"Anthropic create() failed (turn {_+1}): {type(e).__name__}: {err_msg[:300]}")
            if "credit balance is too low" in err_msg:
                short = "Anthropic account has no credit balance"
            elif "invalid_api_key" in err_msg or "authentication" in err_msg.lower():
                short = "Anthropic API key invalid"
            elif "rate_limit" in err_msg.lower():
                short = "Anthropic rate limit hit"
            else:
                short = f"{type(e).__name__}: {err_msg[:100]}"
            return (f"Claude unavailable ({short}). Falling back.", tools_used, False)

        content_blocks = list(resp.content)
        block_types = [getattr(b, "type", None) for b in content_blocks]
        _log.info(f"chat_loop turn {_+1}: stop_reason={getattr(resp, 'stop_reason', None)} blocks={block_types}")
        has_tool_use = any(t == "tool_use" for t in block_types)
        if not has_tool_use:
            final_text = "".join(
                b.text for b in content_blocks if getattr(b, "type", None) == "text"
            )
            break

        # Serialize the assistant's content blocks to plain dicts so the
        # next Anthropic call can JSON-encode them. Passing the raw pydantic
        # model objects back causes "by_alias: NoneType" TypeError on some
        # SDK versions.
        asst_content = []
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

        tool_results = []
        for block in content_blocks:
            if getattr(block, "type", None) != "tool_use":
                continue
            name = block.name
            inputs = block.input or {}
            try:
                result = _execute_tool(name, inputs, farmer)
            except Exception as e:
                result = {"error": f"tool_failed: {e!r}"}
            tools_used.append({"tool": name, "input": inputs})
            tool_results.append({
                "type":         "tool_result",
                "tool_use_id":  block.id,
                "content":      json.dumps(result, default=str)[:6000],
            })
        messages.append({"role": "user", "content": tool_results})
    else:
        final_text = "I need a bit more detail to help you here. A field agent will reach out within 24 hours."

    return (final_text.strip() or "(no response)", tools_used, True)


def _execute_tool(name: str, inputs: dict, farmer: dict):
    from tools.agronomy_rag import lookup_agronomy
    from tools.farmer_analysis import analyze_farmer
    from tools.market_price import get_market_price
    from tools.peer_benchmarks import get_peer_benchmarks
    from tools.web_search import web_search
    try:
        from tools.rainfall_fetcher import fetch_zone_rainfall
    except Exception:
        fetch_zone_rainfall = None  # type: ignore

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
        if fetch_zone_rainfall is None:
            return {"error": "rainfall_unavailable"}
        try:
            return fetch_zone_rainfall(inputs.get("zone") or farmer.get("zone") or "IIa")
        except Exception as e:
            return {"error": f"rainfall_failed: {e!r}"}
    if name == "lookup_agronomy_kb":
        return lookup_agronomy(inputs.get("query", ""), max_results=int(inputs.get("max_results") or 5))
    if name == "get_market_price":
        return get_market_price(
            inputs.get("crop") or farmer.get("crop") or "soy_bean",
            include_open_market=bool(inputs.get("include_open_market", True)),
        )
    if name == "web_search":
        return web_search(inputs.get("query", ""), max_results=int(inputs.get("max_results") or 5))
    if name == "set_topic":
        return {"ack": True, "topic": (inputs.get("topic") or "general").lower()}
    if name == "escalate_to_field_agent":
        return {"ack": True, "reason": inputs.get("reason"),
                "note": "Field agent will reach out within 24 hours."}
    return {"error": f"unknown_tool: {name}"}


@app.post("/api/ask")
async def exec_ask_api(request: Request):
    """Dashboard: answer executive questions with live season context."""
    body = await request.json()
    try:
        with open("dashboard/report.json") as f:
            report = json.load(f)
    except Exception:
        report = {}
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    ctx = json.dumps({
        "season_summary":    report.get("season_summary", {}),
        "zone_breakdown":    report.get("zone_breakdown", []),
        "rainfall":          report.get("rainfall", []),
        "input_effectiveness": report.get("input_effectiveness", {}),
        "financial":         report.get("financial", {}),
    })
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        system="You are a senior analyst briefing GNA leadership. Answer in 2-3 sentences with specific numbers. Plain English only.",
        messages=[{"role": "user", "content": f"Season data:\n{ctx}\n\nQuestion: {body.get('question','')}"}],
    )
    return {"answer": response.content[0].text}
