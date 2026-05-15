# GNA Agri-Intelligence Platform

> A multi-agent AI system for Good Nature Agro (Zambia) — connecting 22,000+ smallholder farmers via WhatsApp with **zone-aware** daily nudges, a **tool-augmented** AI advisor, real-time risk monitoring, and an executive analytics dashboard.

---

## What This Is

Built on findings from the GNA Analytics Showdown (Spring 2026), this platform turns a Jupyter notebook's insights into a living system that:

- **Sends farmers per-zone, per-risk-tier WhatsApp nudges** timed to each zone's planting calendar (Zone IIa plants two weeks later than Zone III) and the farmer's risk tier (High Risk = every 3 days, Medium = weekly, Low = bi-weekly)
- **Answers farmer questions** in real time using a tool-augmented Claude agent that can search the live web, look up agronomy KB entries, pull peer-camp benchmarks, fetch zone rainfall, and check market prices — all in one conversation
- **Detects language** (English / Bemba / Nyanja) and replies in the farmer's preferred language
- **Flags at-risk farmers** (4,948 high-risk farmers from the canonical model) and alerts their assigned field agent automatically — including richer escalation alerts that ship a 5-turn conversation summary to the agent
- **Briefs GNA executives** with weekly procurement forecasts, zone-level risk maps, and input effectiveness reports

---

## Demo Data Disclosure

This is a **prototype demonstrator**, not a live production deployment. To make the dashboard, agent flows, and WhatsApp simulator runnable end-to-end without a live data pipeline, the following are **simulated for demonstration purposes**:

| Item | Source |
|---|---|
| **Risk score & threshold** | The current `risk_scorer.py` is a **rule-based approximation** of the calibrated XGBoost model in `GNA_Analytics_Showdown_Final.ipynb`. The threshold `> 0.45` applies to this prototype scale. The notebook's production model uses `1 − P(buyback)` with an F2-optimal cutoff at **0.35** on the calibrated scale — these are different scoring systems, not a contradiction. |
| **Zone breakdown (5 zones incl. Zone IV)** | The notebook's master table contains 4 agroecological zones (I, IIa, IIb, III). Zone IV (Western Barotse Plain) is included in this prototype to illustrate geographic-coverage extension scenarios; real Zone IV farmer counts are not yet in `master_table.csv`. |
| **Previous-season comparison & weekly risk trend** | Only one season of data exists in the source; the W1–W9 trends and 2024/25 vs prior comparisons are illustrative. |
| **Per-farmer financial loan book ($2.85M USD, etc.)** | Aggregated from the in-kind repayment column with assumed ZMW→USD conversion; treat as an order-of-magnitude estimate. |
| **Late-planting +0.05 risk weight** | Applied as agronomic intuition. Note: in the 2024/25 season the notebook found late planting actually correlates with *higher* yield in this dataset (likely waiting for rains). The rule survives in the prototype as a forward-looking default; production should re-derive its sign empirically. |

**Numbers that match the notebook exactly** (canonical, not simulated):

- Total farmer base: 22,597
- Buyback rate: 76.6%
- Top-10% Pareto: 41% of volume
- Inoculant zone-controlled lift: +486 kg/ha (highest-confidence input)
- Fertilizer zone-controlled lift: +312 kg/ha
- Fungicide displayed as +250 kg/ha (conservative — notebook headline is +952 ± 1,931 kg/ha; std exceeds mean, so the average is unreliable as a portfolio number)
- Seed Guard displayed as +318 kg/ha (single-zone evidence; insufficient data for portfolio generalization)

For canonical model performance, risk thresholds, and ROI math, refer to `../GNA_Analytics_Showdown_Final.ipynb` Sections 4–8.

---

## What's New — Intelligent Chat Upgrade

The chat agent (`agents/farmer_chat.py`) has been rewritten as a **tool-augmented Claude agent** that can call up to six tools per question to ground its answers in current data instead of guessing. The nudge scheduler is now zone- and risk-tier aware. Both upgrades are designed to **degrade gracefully in demo mode** — every external dependency has a keyless fallback, so you can run the full system end-to-end with the placeholder `.env`.

### The Six Tools the Chat Agent Can Call

| Tool | Module | Used for | Demo posture |
|---|---|---|---|
| `get_farmer_analysis` | `tools/farmer_analysis.py` | "What's my risk?" "What yield should I expect?" | Always works |
| `get_peer_benchmarks` | `tools/peer_benchmarks.py` | "How am I doing vs my camp?" | Reads `master_table.csv` |
| `get_zone_rainfall` | `tools/rainfall_fetcher.py` | "Is rainfall normal this year?" | Open-Meteo, no key |
| `lookup_agronomy_kb` | `tools/agronomy_rag.py` | Pest, disease, variety, planting questions | Bundled JSON |
| `get_market_price` | `tools/market_price.py` | Buyback price, open-market reference | Static fallback |
| `web_search` | `tools/web_search.py` | Time-sensitive external info | Tavily → DDG fallback |

Plus two control tools used to manage the conversation:

- `set_topic` — tags the turn (`planting`, `pests`, `inputs`, `weather`, `market`, etc.) so memory persists across days
- `escalate_to_field_agent` — emits a `FARMER_ESCALATION` event that ships a 5-turn conversation summary to the assigned (or zone-fallback) field agent

### Per-Zone, Per-Risk-Tier Nudges

`data/season_calendar_by_zone.json` defines five zone calendars (I, IIa, IIb, III, IV) with zone-specific planting windows and topic-by-day mappings. `data/risk_tier_modifiers.json` adds the cadence and tone overlay per risk tier:

| Tier | Threshold | Cadence | Tone | Auto-escalate after |
|---|---|---|---|---|
| **High** | risk_score ≥ 0.35 | Every 3 days | Urgent + supportive | 1 `help` reply |
| **Medium** | 0.20 ≤ score < 0.35 | Weekly | Supportive | 2 `help` replies |
| **Low** | score < 0.20 | Bi-weekly | Light + friendly | 3 `help` replies |

So a Zone IIa first-season High-Risk farmer now gets a *different* sequence of messages than a Zone III experienced Low-Risk farmer — both in cadence and in content.

### Language Detection

`tools/language_detect.py` returns `en` / `bem` / `nya`. It tries a small Bemba/Nyanja keyword override first (high precision on short WhatsApp utterances), then falls back to `langdetect` (offline, deterministic). The chat agent's system prompt is rendered in the detected language so Claude replies in the same language the farmer wrote in.

### Demo vs Production Posture

| Capability | Demo (placeholder keys) | Production |
|---|---|---|
| Chat tool-use loop | Falls back to deterministic templated reply (English / Bemba / Nyanja) | Full Claude `claude-sonnet-4-6` with all 6 tools |
| `web_search` | Keyless DuckDuckGo via `duckduckgo-search` | Tavily (1k/mo free) when `TAVILY_API_KEY` is set |
| `get_market_price` | Static reference range | Same — could be wired to a paid commodity API |
| `get_zone_rainfall` | Open-Meteo, no key needed | Same |
| WhatsApp send | Prints `[DEV MODE] Would send to ...` | Real Meta Business API |

Running with the bundled `.env` exercises every code path *except* the live LLM and live WhatsApp send. Adding `ANTHROPIC_API_KEY` and `TAVILY_API_KEY` upgrades to production behaviour with zero code changes.

### New Event Types

The orchestrator now handles a `FARMER_ESCALATION` event in addition to the original four. It can be raised by:

- The chat agent (when Claude calls `escalate_to_field_agent`)
- The numbered-reply path (when `consecutive_help` crosses the risk-tier threshold)

Each escalation ships the assigned field agent a one-paragraph conversation summary so the call-back is targeted, not cold.

---

## Architecture

```
                        ┌──────────────────────────┐
                        │    Orchestrator Agent     │
                        │  Routes · Decides · Logs  │
                        └───────────┬──────────────┘
              ┌────────────┬────────┴──────┬─────────────────┐
              ▼            ▼               ▼                  ▼
    ┌──────────────┐ ┌───────────┐ ┌─────────────┐ ┌──────────────────┐
    │    Nudge     │ │  Farmer   │ │    Risk     │ │  Exec Briefing   │
    │  Scheduler   │ │   Chat    │ │   Monitor   │ │     Agent        │
    │  Agent       │ │   Agent   │ │   Agent     │ │                  │
    └──────┬───────┘ └─────┬─────┘ └──────┬──────┘ └────────┬─────────┘
           │               │              │                  │
           └───────────────┴──────────────┴──────────────────┘
                                    │
                      ┌─────────────▼────────────┐
                      │      Shared Memory        │
                      │ Farmer profiles · Scores  │
                      │ Season calendar · Loans   │
                      └─────────────┬─────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                      ▼
     ┌────────────────┐   ┌──────────────────┐   ┌────────────────────┐
     │ Yield Predictor│   │  Risk Scorer     │   │  Nudge Composer    │
     │     Tool       │   │     Tool         │   │      Tool          │
     └────────────────┘   └──────────────────┘   └────────────────────┘

   WhatsApp (farmers)                              GNA Dashboard (execs)
   ←─────────────────                             ─────────────────────→
```

### The Five Agents

| Agent | Trigger | Responsibility |
|---|---|---|
| **Orchestrator** | Every incoming event | Routes messages, coordinates agents, maintains conversation state |
| **Nudge Scheduler** | Daily 7:00 AM (per farmer timezone) | Composes and sends season-appropriate daily message |
| **Farmer Chat** | Farmer free-text reply | Answers agronomic questions, guides to field agent if needed |
| **Risk Monitor** | Farmer reply + nightly batch | Scores farmers, fires alerts to field agents |
| **Exec Briefing** | Weekly + on-demand | Generates procurement forecast, zone risk report, input ROI summary |

---

## Key Findings Baked In (from Analytics Showdown)

These are hardcoded as domain knowledge across agents:

- **Inoculant = +486 kg/ha** lift (p<0.001) → nudge agent always prompts for inoculant confirmation
- **Season 1 farmers yield 3.3× less** → risk monitor auto-flags all first-season farmers
- **6,129 farmers with risk score > 0.45** → pre-seeded as high-priority outreach list
- **23% non-buyback rate** → exec briefing tracks this weekly
- **Top 10% of farmers = 41% of volume** → retention alerts for high-performer churn risk
- **16.4% face in-kind repayment gap** → risk monitor cross-checks loan vs. yield estimate

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Agent framework | Python + Anthropic Claude API (`claude-sonnet-4-6`) | Multi-agent orchestration |
| WhatsApp integration | [WhatsApp Business Cloud API](https://developers.facebook.com/docs/whatsapp) | Official Meta API, free tier available |
| Dashboard | React + Recharts | Single-page, no backend needed for demo |
| Memory / state | SQLite (dev) → PostgreSQL (prod) | Farmer state, conversation history, risk scores |
| Scheduler | APScheduler (Python) | Daily nudge jobs per farmer |
| Deployment | Railway / Render (suggested) | Simple, free tier for prototype |

---

## Repo Structure

```
gna-agri-intelligence/
├── README.md                    ← You are here
├── ARCHITECTURE.md              ← Deep dive on agent design
├── .env.example                 ← All env vars you need
├── requirements.txt
│
├── agents/
│   ├── orchestrator.py          ← Central router
│   ├── nudge_scheduler.py       ← Daily message agent
│   ├── farmer_chat.py           ← Conversational Q&A agent
│   ├── risk_monitor.py          ← Risk scoring + field agent alerts
│   └── exec_briefing.py         ← Weekly executive report agent
│
├── tools/
│   ├── yield_predictor.py       ← Rule-based yield estimate (notebook §5)
│   ├── risk_scorer.py           ← Rule-based risk score (notebook §6)
│   ├── farmer_analysis.py       ← NEW: composite tool — risk + yield + recs
│   ├── agronomy_rag.py          ← NEW: rapidfuzz over agronomy_kb.json
│   ├── peer_benchmarks.py       ← NEW: master_table slicer (camp / district / zone)
│   ├── market_price.py          ← NEW: GNA buyback + open-market reference
│   ├── web_search.py            ← NEW: Tavily + DuckDuckGo fallback + cache
│   ├── language_detect.py       ← NEW: en / bem / nya detection
│   ├── rainfall_fetcher.py      ← Open-Meteo per-zone rainfall
│   ├── nudge_composer.py        ← Season-aware message generator
│   └── field_agent_alert.py     ← WhatsApp alert sender (+ escalation)
│
├── memory/
│   ├── schema.sql               ← DB schema (incl. risk_tier, topic, language)
│   ├── farmer_store.py          ← Farmer profile CRUD + risk-tier backfill
│   └── conversation_store.py    ← Message history with topic + language
│
├── whatsapp/
│   ├── webhook.py               ← FastAPI webhook receiver
│   └── client.py                ← WhatsApp API sender
│
├── dashboard/
│   ├── index.html
│   ├── App.jsx                  ← Main React app
│   └── components/
│       ├── SeasonOverview.jsx
│       ├── ZoneRiskMap.jsx
│       ├── ProcurementForecast.jsx
│       └── FarmerAdvisor.jsx    ← WhatsApp-style chat demo
│
├── data/
│   ├── season_calendar.json              ← Legacy single calendar (kept for compat)
│   ├── season_calendar_by_zone.json      ← NEW: per-zone phase calendars
│   ├── risk_tier_modifiers.json          ← NEW: cadence + tone per risk tier
│   └── agronomy_kb.json                  ← Crop / pest / disease KB
│
├── tests/
│   ├── test_orchestrator.py
│   ├── test_risk_scorer.py
│   ├── test_nudge_composer.py
│   ├── test_tools.py            ← NEW: 23 unit tests for the new tools
│   └── test_chat_e2e.py         ← NEW: 6 end-to-end tests with Claude mocked
│
└── .github/
    └── workflows/
        └── ci.yml               ← Run tests on push
```

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/sjagannathan17/gna-agri-intelligence.git
cd gna-agri-intelligence
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Set environment variables

```bash
cp .env.example .env
# Then fill in your keys — see .env.example for details
```

### 3. Initialize the database

```bash
python -c "from memory.farmer_store import init_db; init_db()"
```

### 4. Run the WhatsApp webhook (local dev)

```bash
uvicorn whatsapp.webhook:app --reload --port 8000
# Then use ngrok to expose: ngrok http 8000
```

### 5. Run the dashboard

```bash
cd dashboard && npx serve .
```

---

## Build Order (Recommended)

If you're building this solo, tackle it in this order:

1. **Memory layer** (`memory/`) — get the DB schema right first, everything else reads from it
2. **Risk scorer tool** (`tools/risk_scorer.py`) — core logic from your notebook
3. **Orchestrator skeleton** (`agents/orchestrator.py`) — just routing, no smarts yet
4. **Farmer chat agent** (`agents/farmer_chat.py`) — most satisfying to demo
5. **Nudge scheduler** (`agents/nudge_scheduler.py`) — add the calendar logic
6. **WhatsApp webhook** (`whatsapp/webhook.py`) — wire it all to real messages
7. **Risk monitor** (`agents/risk_monitor.py`) — field agent alerts
8. **Exec briefing + dashboard** — the GNA leadership layer

---

## Running the Tests

The chat upgrade ships with 29 new tests (23 tool unit tests + 6 chat e2e tests with Claude mocked). The full test suite has 44 tests and runs in under one second.

```bash
cd gna-agri-intelligence
pytest tests/                                # full suite
pytest tests/test_tools.py -v                # tool tests only
pytest tests/test_chat_e2e.py -v             # tool-use loop tests only
```

External dependencies are mocked: Tavily, DuckDuckGo, Anthropic, and Open-Meteo are never called during tests.

---

## Contributing

This is a competition project — built for the GNA Analytics Showdown, Spring 2026. If you're extending it, open a PR with a clear description of which agent or tool you modified.

---

## License

MIT
