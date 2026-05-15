"""
Interactive Chat REPL — talk to the upgraded farmer chatbot from your terminal.

Usage:
    cd gna-agri-intelligence
    python3 chat_repl.py

Modes (auto-detected):
  - Real Claude: ANTHROPIC_API_KEY set to a real key → runs the actual tool-use
    loop. Claude picks which tools to call. You see every tool call + result.
  - Demo mode:   ANTHROPIC_API_KEY is the placeholder → uses a keyword router
    so you can still see what tools would fire.

Switch farmers with:  /farmer high      # high-risk Zone IIa, season 1
                       /farmer medium    # medium-risk Zone III, season 2
                       /farmer low       # low-risk Zone III, season 4

Other commands:
    /tools     — print the registered tool schemas
    /profile   — show the active farmer's profile
    /quit      — exit
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Make `agents`, `tools`, `memory` importable when run from this directory
sys.path.insert(0, str(Path(__file__).parent))

# Load .env so TAVILY_API_KEY and ANTHROPIC_API_KEY are picked up
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-placeholder-demo")

from agents.farmer_chat import MAX_TOOL_TURNS, MODEL_NAME, SYSTEM_PROMPT_TEMPLATE, TOOL_SCHEMAS
from tools.agronomy_rag import lookup_agronomy
from tools.farmer_analysis import analyze_farmer
from tools.language_detect import detect_language, language_name
from tools.market_price import get_market_price
from tools.peer_benchmarks import get_peer_benchmarks
from tools.web_search import web_search

try:
    from tools.rainfall_fetcher import fetch_zone_rainfall
except Exception:
    fetch_zone_rainfall = None  # type: ignore


# ─── Demo farmer profiles ────────────────────────────────────────────────────

FARMERS = {
    "high": {
        "farmer_id":          "F001",
        "name":               "Joseph Phiri",
        "phone":              "+260971000001",
        "zone":               "IIa",
        "camp_name":          "Mwandi Hub",
        "district_name":      "Mkushi",
        "season_number":      1,
        "crop":               "soy_bean",
        "has_inoculant":      False,
        "has_fertilizer":     False,
        "days_to_plant":      35,
        "total_hectares":     1.0,
        "risk_score":         0.55,
        "risk_tier":          "High",
        "preferred_language": "english",
        "nudge_responses":    {"total": 2, "done": 0, "help": 2, "skip": 0},
    },
    "medium": {
        "farmer_id":          "F002",
        "name":               "Mary Banda",
        "phone":              "+260971000002",
        "zone":               "III",
        "camp_name":          "Lwangeni",
        "district_name":      "Kasama",
        "season_number":      2,
        "crop":               "soy_bean",
        "has_inoculant":      True,
        "has_fertilizer":     False,
        "days_to_plant":      18,
        "total_hectares":     0.75,
        "risk_score":         0.27,
        "risk_tier":          "Medium",
        "preferred_language": "english",
        "nudge_responses":    {"total": 5, "done": 4, "help": 1, "skip": 0},
    },
    "low": {
        "farmer_id":          "F003",
        "name":               "Patrick Mwale",
        "phone":              "+260971000003",
        "zone":               "III",
        "camp_name":          "Lwangeni",
        "district_name":      "Kasama",
        "season_number":      4,
        "crop":               "soy_bean",
        "has_inoculant":      True,
        "has_fertilizer":     True,
        "days_to_plant":      5,
        "total_hectares":     1.5,
        "risk_score":         0.08,
        "risk_tier":          "Low",
        "preferred_language": "english",
        "nudge_responses":    {"total": 8, "done": 8, "help": 0, "skip": 0},
    },
}


# ─── Pretty printing ─────────────────────────────────────────────────────────

C_RESET = "\033[0m"
C_BOLD  = "\033[1m"
C_DIM   = "\033[2m"
C_GREEN = "\033[32m"
C_BLUE  = "\033[34m"
C_CYAN  = "\033[36m"
C_YELL  = "\033[33m"
C_GREY  = "\033[90m"
C_MAG   = "\033[35m"


def banner(text: str, color: str = C_CYAN) -> None:
    print(f"\n{color}{'─' * 70}{C_RESET}")
    print(f"{color}{C_BOLD}{text}{C_RESET}")
    print(f"{color}{'─' * 70}{C_RESET}")


def tool_call(name: str, inputs: dict) -> None:
    print(f"  {C_MAG}>> tool_call: {C_BOLD}{name}{C_RESET}{C_MAG}({json.dumps(inputs, default=str, ensure_ascii=False)}){C_RESET}")


def tool_result(name: str, result, *, max_len: int = 500) -> None:
    text = json.dumps(result, default=str, ensure_ascii=False, indent=2)
    if len(text) > max_len:
        text = text[:max_len] + f"\n  {C_GREY}... ({len(text) - max_len} more chars){C_RESET}"
    print(f"  {C_GREY}<< {name} returned:{C_RESET}")
    for line in text.splitlines():
        print(f"  {C_GREY}{line}{C_RESET}")


# ─── Tool execution (shared between demo + real Claude paths) ────────────────

def execute_tool(name: str, inputs: dict, farmer: dict):
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
            return {"error": "rainfall_fetcher_unavailable"}
        try:
            return fetch_zone_rainfall(inputs.get("zone") or farmer.get("zone") or "IIa")
        except Exception as e:
            return {"error": f"rainfall_fetch_failed: {e!r}"}
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


# ─── Real Claude tool-use loop ───────────────────────────────────────────────

def chat_with_claude(query: str, farmer: dict, lang: str) -> str:
    """Run the actual Anthropic tool-use loop. Prints every tool call as it happens.
    Returns the final reply string."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    system = SYSTEM_PROMPT_TEMPLATE.format(
        lang_name=language_name(lang), max_turns=MAX_TOOL_TURNS,
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
    )

    messages = [
        {"role": "user",      "content": farmer_block},
        {"role": "assistant", "content": "Got it. I have the farmer profile. What is their question?"},
        {"role": "user",      "content": query},
    ]

    final_text = ""
    for turn in range(MAX_TOOL_TURNS):
        try:
            resp = client.messages.create(
                model=MODEL_NAME,
                max_tokens=600,
                system=system,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
        except Exception as e:
            print(f"  {C_YELL}Anthropic error on turn {turn}: {e!r}{C_RESET}")
            return f"(Anthropic call failed: {e!r})"

        stop_reason = getattr(resp, "stop_reason", None)
        content_blocks = list(resp.content)
        has_tool_use = any(getattr(b, "type", None) == "tool_use" for b in content_blocks)

        if not has_tool_use:
            final_text = "".join(b.text for b in content_blocks if getattr(b, "type", None) == "text")
            break

        # Serialize blocks to plain dicts so the next create() call can
        # JSON-encode them (raw pydantic objects break some SDK versions).
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
            tool_call(name, inputs)
            try:
                result = execute_tool(name, inputs, farmer)
            except Exception as e:
                result = {"error": f"tool_failed: {e!r}"}
            tool_result(name, result)
            tool_results.append({
                "type":         "tool_result",
                "tool_use_id":  block.id,
                "content":      json.dumps(result, default=str)[:6000],
            })
        messages.append({"role": "user", "content": tool_results})
    else:
        final_text = "(tool-call cap reached — please rephrase or ask a simpler question)"

    return final_text.strip() or "(no text returned)"


# ─── Demo-mode keyword router (used when key is the placeholder) ─────────────

def route_query_demo(q: str, farmer: dict) -> list[tuple[str, dict]]:
    ql = q.lower().strip()
    calls: list[tuple[str, dict]] = []
    if any(k in ql for k in ["my risk", "my yield", "what should i", "why am i", "what about me", "how am i"]):
        calls.append(("get_farmer_analysis", {}))
    if any(k in ql for k in ["compared", "other farmers", "in my camp", "vs others", "average farmer", "best in", "top farmers"]):
        calls.append(("get_peer_benchmarks", {
            "camp_name": farmer["camp_name"], "district_name": farmer["district_name"], "zone": farmer["zone"],
        }))
    if any(k in ql for k in ["rain", "rainfall", "drought", "wet", "dry"]):
        calls.append(("get_zone_rainfall", {"zone": farmer["zone"]}))
    if any(k in ql for k in ["pest", "aphid", "borer", "rust", "yellow", "leaves", "spots", "disease", "fungus"]):
        calls.append(("lookup_agronomy_kb", {"query": q}))
    if any(k in ql for k in ["price", "buyback", "kwacha", "zmw", "earn", "selling"]):
        calls.append(("get_market_price", {"crop": farmer["crop"], "include_open_market": True}))
    if any(k in ql for k in ["weather forecast", "news", "fertilizer brand", "outbreak", "trader", "market today", "current"]):
        calls.append(("web_search", {"query": q + " Zambia"}))
    if not calls:
        calls.append(("lookup_agronomy_kb", {"query": q}))
    return calls


def synthesize_demo_reply(farmer: dict, lang: str, executed: list) -> str:
    name = farmer["name"].split()[0]
    parts: list[str] = []
    for tool, _, result in executed:
        if tool == "get_farmer_analysis":
            recs = "\n".join(f"  - {r['title']}" for r in result["recommendations"][:2])
            parts.append(f"Risk: {result['risk_tier']} ({result['risk_score']}). "
                         f"Expected yield ~{result['yield_estimate_kg_ha']:.0f} kg/ha.\nTop actions:\n{recs}")
        elif tool == "get_peer_benchmarks":
            cs = result.get("camp_stats")
            if cs and cs.get("median_yield_kg_ha") is not None:
                parts.append(f"Camp {cs['label_value']}: median {cs['median_yield_kg_ha']:.0f} kg/ha "
                             f"({cs['n_farmers']} farmers).")
        elif tool == "get_zone_rainfall" and "error" not in result:
            anom = result.get("anomaly_pct", 0)
            parts.append(f"Zone {result['zone']}: {result['season_total_mm']}mm vs "
                         f"avg {result['historical_avg_mm']}mm ({anom:+.0f}%).")
        elif tool == "lookup_agronomy_kb":
            matches = result.get("matches") or []
            if matches:
                m = matches[0]
                if isinstance(m["payload"], dict) and "action" in m["payload"]:
                    parts.append(f"For {m['key'].replace('_', ' ')}: {m['payload']['action']}")
        elif tool == "get_market_price":
            gna = result["gna_buyback"]
            parts.append(f"GNA buyback: {gna['gross_per_kg']} ZMW/kg gross, "
                         f"{gna['net_after_loan_per_kg']} ZMW/kg net after loan.")
        elif tool == "web_search":
            results = result.get("results") or []
            if results:
                parts.append(f"Web: {results[0]['title']} — {results[0]['snippet'][:140]}")
    body = "\n\n".join(parts) or "Let me ask a field agent to follow up with you."
    prefix = {"bem": f"Mwapoleni {name}!\n", "nya": f"Moni {name}!\n"}.get(lang, f"Hi {name}, ")
    return prefix + body


# ─── REPL ─────────────────────────────────────────────────────────────────────

def print_profile(farmer: dict) -> None:
    print(f"  {C_BOLD}Name:{C_RESET}     {farmer['name']}")
    print(f"  {C_BOLD}Zone:{C_RESET}     {farmer['zone']} ({farmer['camp_name']}, {farmer['district_name']})")
    print(f"  {C_BOLD}Season:{C_RESET}   {farmer['season_number']}")
    print(f"  {C_BOLD}Risk:{C_RESET}     {farmer['risk_tier']} (score {farmer['risk_score']})")
    print(f"  {C_BOLD}Inputs:{C_RESET}   inoculant={'Y' if farmer['has_inoculant'] else 'N'}, "
          f"fertilizer={'Y' if farmer['has_fertilizer'] else 'N'}")
    print(f"  {C_BOLD}Farm:{C_RESET}     {farmer['total_hectares']} ha · planted {farmer['days_to_plant']} days into season")


def has_real_anthropic_key() -> bool:
    k = os.environ.get("ANTHROPIC_API_KEY", "")
    return bool(k) and "placeholder" not in k.lower() and "demo" not in k.lower()


def main():
    farmer = dict(FARMERS["high"])

    banner("GNA Intelligent Farmer Chatbot — interactive REPL", C_GREEN)
    real_key = has_real_anthropic_key()
    has_tavily = bool(os.environ.get("TAVILY_API_KEY", "").strip())

    print(f"{C_DIM}Anthropic key:  "
          f"{'real (Claude tool-use loop active)' if real_key else 'placeholder (demo router)'}{C_RESET}")
    print(f"{C_DIM}Tavily key:     "
          f"{'set (web_search uses Tavily)' if has_tavily else 'unset (web_search falls back to DuckDuckGo)'}{C_RESET}")
    print()
    print(f"Active farmer: {C_BOLD}{farmer['name']}{C_RESET}  (Zone {farmer['zone']}, {farmer['risk_tier']} Risk)")
    print(f"Type {C_CYAN}/help{C_RESET} for commands, {C_CYAN}/quit{C_RESET} to exit.")
    print()
    print(f"{C_DIM}Sample prompts to try:{C_RESET}")
    print('  "Why am I high risk?"')
    print('  "How am I doing compared to other farmers in my camp?"')
    print('  "Is rainfall normal in my zone this year?"')
    print('  "What price will GNA pay for my soy beans?"')
    print('  "I see yellow tips on my leaves — what should I do?"')
    print('  "What is the latest news on fall armyworm in Zambia?"')
    print('  "Muli shani, ndakutotela"   (Bemba greeting)')
    print('  "Moni bambo, zikomo"        (Nyanja greeting)')
    print()

    while True:
        try:
            q = input(f"{C_BOLD}{C_GREEN}{farmer['name'].split()[0]}>{C_RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue

        if q.startswith("/"):
            cmd, *rest = q[1:].split(maxsplit=1)
            if cmd in ("quit", "q", "exit"):
                break
            if cmd == "help":
                print(__doc__ or "")
                continue
            if cmd == "profile":
                print_profile(farmer)
                continue
            if cmd == "tools":
                for t in TOOL_SCHEMAS:
                    print(f"  {C_BOLD}{t['name']}{C_RESET}: {t['description'][:90]}...")
                continue
            if cmd == "farmer":
                key = (rest[0].strip().lower() if rest else "").lower()
                if key in FARMERS:
                    farmer = dict(FARMERS[key])
                    print(f"{C_GREEN}Switched to {farmer['name']} "
                          f"({farmer['risk_tier']} Risk, Zone {farmer['zone']}){C_RESET}")
                else:
                    print(f"  Use one of: high, medium, low")
                continue
            print(f"  {C_YELL}unknown command. Try /help{C_RESET}")
            continue

        # 1. Detect language
        lang = detect_language(q, default=farmer.get("preferred_language", "en"))
        print(f"  {C_BLUE}-- detected language: {language_name(lang)} ({lang}){C_RESET}")

        # 2. Real Claude path or demo router path
        if real_key:
            reply = chat_with_claude(q, farmer, lang)
        else:
            calls = route_query_demo(q, farmer)
            executed = []
            for name, inputs in calls:
                tool_call(name, inputs)
                try:
                    res = execute_tool(name, inputs, farmer)
                except Exception as e:
                    res = {"error": f"crashed: {e!r}"}
                tool_result(name, res)
                executed.append((name, inputs, res))
            reply = synthesize_demo_reply(farmer, lang, executed)

        print()
        print(f"{C_BOLD}{C_GREEN}reply >{C_RESET} {reply}")
        print()

    print(f"{C_DIM}Bye.{C_RESET}")


if __name__ == "__main__":
    main()
