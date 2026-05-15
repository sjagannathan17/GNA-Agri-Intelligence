# Architecture Deep Dive

## Agent Design Principles

Each agent in this system follows the same contract:

```python
async def run(event: dict, memory: MemoryStore) -> AgentResult:
    # 1. Read relevant farmer state from memory
    # 2. Call Claude API with a focused system prompt
    # 3. Execute any tool calls Claude requests
    # 4. Write updated state back to memory
    # 5. Return result (message to send, alert to fire, etc.)
```

No agent talks directly to another agent. **Everything goes through the orchestrator.** This makes the system easy to debug — every decision is logged at the orchestrator level.

---

## Orchestrator Agent

**File:** `agents/orchestrator.py`

The orchestrator receives every incoming event and decides what to do:

```
Incoming event types:
  - WHATSAPP_MESSAGE   → from a farmer
  - WHATSAPP_REPLY     → farmer replied to a nudge (1/2/3)
  - SCHEDULED_NUDGE    → daily 7am job fired for a farmer
  - RISK_BATCH         → nightly risk re-scoring job
  - EXEC_REPORT_REQ    → weekly or on-demand briefing request
```

Routing logic (pseudo-code):

```python
match event.type:
    case "WHATSAPP_MESSAGE":
        if event.text in ["1", "2", "3"]:
            → nudge_reply_handler()
        else:
            → farmer_chat_agent.run(event)
    case "SCHEDULED_NUDGE":
        → nudge_scheduler_agent.run(event)
    case "RISK_BATCH":
        → risk_monitor_agent.run_batch()
    case "EXEC_REPORT_REQ":
        → exec_briefing_agent.run(event)
```

The orchestrator also maintains **conversation context** — it passes the last N messages from `conversation_store` into each agent call so the farmer chat agent has memory within a session.

---

## Nudge Scheduler Agent

**File:** `agents/nudge_scheduler.py`

Triggered every morning by APScheduler. For each active farmer:

1. Reads `farmer.planting_date` from memory
2. Computes `days_since_planting`
3. Looks up the matching entry in `data/season_calendar.json`
4. Calls Claude with: farmer profile + season day + calendar entry → personalized message
5. Sends via WhatsApp client

**Season calendar structure** (`data/season_calendar.json`):

```json
{
  "day_0_7": {
    "phase": "planting",
    "topic": "inoculant_confirmation",
    "template": "Have you applied your inoculant? It can add up to 486 kg/ha.",
    "reply_options": {"1": "done", "2": "need_help", "3": "skip"}
  },
  "day_14_30": {
    "phase": "early_growth",
    "topic": "weed_management",
    ...
  }
}
```

**Personalization variables Claude uses:**
- Farmer name, crop type, zone
- Whether they received inoculant in loan package
- Their season number (1st-season farmers get more check-ins)
- Their risk score (high-risk farmers get escalation prompts)

---

## Farmer Chat Agent

**File:** `agents/farmer_chat.py`

The most conversational agent. Called when a farmer sends a free-text message.

**System prompt includes:**
- GNA agronomic knowledge base (crops, pests, diseases, spacing, timing)
- Key findings from the analytics model (inoculant lift, season experience gaps)
- Farmer's profile (crop, zone, inputs received, season number)
- Last 5 messages (conversation context)
- Escalation instruction: if the question can't be answered confidently → recommend field agent

**Example exchange:**

```
Farmer: "My plants look yellow at the tips"
Agent:  Looks at farmer profile → has_inoculant=True, crop=soy_bean, zone=IIa
        → Calls Claude with context
Claude: "Yellow tips on soy bean usually means nitrogen deficiency. 
         Since you received inoculant, make sure it was applied at planting.
         If the yellowing spreads to the whole leaf, reply HELP and Joseph 
         (your field agent) will call you today."
```

**Language handling:** The system prompt instructs Claude to respond in the language the farmer writes in. Bemba and Nyanja patterns are included in `data/agronomy_kb.json`.

---

## Risk Monitor Agent

**File:** `agents/risk_monitor.py`

Two modes:

### Real-time mode
Triggered when a farmer replies "2" (need help) to any nudge. Immediately:
1. Pulls farmer's current risk score from memory
2. If score > 0.45 OR farmer is Season 1 → fires field agent alert
3. Updates `last_risk_event` timestamp

### Batch mode (nightly)
Runs at 11 PM for all active farmers. For each farmer:
1. Collects features: season number, days since planting, nudge response rate, yield estimate (if provided), in-kind repayment gap
2. Calls `tools/risk_scorer.py` → produces score 0–1
3. Writes score to memory
4. If score crossed 0.45 threshold → fires alert to assigned field agent

**Risk features (from your notebook's XGBoost model):**

```python
RISK_FEATURES = [
    "season_number",           # Season 1 = huge risk flag
    "has_inoculant",           # No inoculant = yield risk
    "days_to_plant",           # Late planting = risk
    "total_hectares",          # Very small farms = risk
    "nudge_response_rate",     # Non-responsive = disengaged
    "in_kind_repayment_gap",   # Can't repay = financial risk
    "agroecological_zone",     # Zone IIa historically higher risk
]
```

---

## Exec Briefing Agent

**File:** `agents/exec_briefing.py`

Runs weekly (Monday 6 AM) or on-demand from the dashboard.

Produces a structured JSON report consumed by the dashboard:

```json
{
  "generated_at": "2025-05-05T06:00:00",
  "season_summary": {
    "total_farmers": 22597,
    "buyback_rate": 0.77,
    "high_risk_count": 6129,
    "procurement_forecast_kg": {
      "low": 2100000,
      "mid": 2600000,
      "high": 3200000
    }
  },
  "zone_breakdown": [...],
  "input_effectiveness": {...},
  "top_farmer_alert": {...},
  "narrative": "This week's key concern is Zone IIa..."
}
```

The `narrative` field is Claude-generated — a 3-sentence plain-English summary for executives who won't read the numbers.

---

## Shared Memory Layer

**File:** `memory/schema.sql`

```sql
CREATE TABLE farmers (
    farmer_id       TEXT PRIMARY KEY,
    name            TEXT,
    phone           TEXT UNIQUE,
    zone            TEXT,
    season_number   INTEGER,
    crop            TEXT,
    planting_date   DATE,
    total_hectares  REAL,
    has_inoculant   INTEGER,
    risk_score      REAL DEFAULT 0,
    field_agent_id  TEXT,
    last_nudge_sent DATETIME,
    nudge_responses TEXT  -- JSON: {"total": 14, "done": 9, "help": 3, "skip": 2}
);

CREATE TABLE conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id   TEXT,
    role        TEXT,  -- 'farmer' or 'agent'
    message     TEXT,
    sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE risk_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id   TEXT,
    score       REAL,
    triggered   TEXT,  -- what caused the alert
    alerted_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE field_agents (
    agent_id    TEXT PRIMARY KEY,
    name        TEXT,
    phone       TEXT,
    zone        TEXT
);
```

---

## WhatsApp Integration

**Files:** `whatsapp/webhook.py`, `whatsapp/client.py`

Uses the **WhatsApp Business Cloud API** (Meta). Free for the first 1,000 conversations/month.

### Webhook flow

```
Meta servers → POST /webhook → FastAPI handler → Orchestrator
```

```python
# whatsapp/webhook.py
@app.post("/webhook")
async def receive_message(request: Request):
    body = await request.json()
    message = parse_whatsapp_message(body)
    event = {
        "type": "WHATSAPP_MESSAGE",
        "farmer_phone": message.from_number,
        "text": message.text,
        "timestamp": message.timestamp
    }
    await orchestrator.handle(event)
    return {"status": "ok"}
```

### Sending messages

```python
# whatsapp/client.py
async def send_message(to: str, text: str):
    # Calls POST https://graph.facebook.com/v19.0/{phone_id}/messages
    ...
```

---

## Dashboard

**Files:** `dashboard/`

A React single-page app. No backend — it reads the weekly JSON report file generated by the exec briefing agent.

Four views:
1. **Season overview** — key metrics, procurement forecast, non-buyback rate
2. **Zone risk map** — farmer counts by zone and risk tier
3. **Input effectiveness** — bar chart of yield lift per input type
4. **Farmer advisor** — WhatsApp-style chat interface for demos

---

## Environment Variables

See `.env.example` for the full list. The critical ones:

```
ANTHROPIC_API_KEY=sk-ant-...
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_VERIFY_TOKEN=...   # any string you choose for webhook verification
DATABASE_URL=sqlite:///./gna.db
```

---

## Deployment (Recommended Path)

For a prototype/demo:

1. **Backend:** Deploy to [Railway](https://railway.app) — connects to GitHub, auto-deploys on push, free tier
2. **Database:** Railway provides a managed PostgreSQL — change `DATABASE_URL` in env vars
3. **Dashboard:** Deploy `dashboard/` to [Vercel](https://vercel.com) — free, instant
4. **WhatsApp:** Register for Meta's WhatsApp Business API (takes ~1 day for approval)

For production (GNA actual deployment):
- Backend: AWS EC2 or Azure (GNA may already have Azure credits through NGO programs)
- Database: AWS RDS PostgreSQL
- WhatsApp: Meta Business Manager with verified business account
