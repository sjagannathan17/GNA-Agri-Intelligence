# GNA Agri-Intelligence — WhatsApp-Native AI for 22,000 Smallholder Farmers

> **A multi-agent AI system for Good Nature Agro (Zambia) that turns farmer-level data into per-zone, per-risk-tier WhatsApp nudges — and lets farmers ask any question, in English / Bemba / Nyanja, with answers grounded in agronomy, peer benchmarks, rainfall, and market prices.**

**22,597 farmers · 5 agroecological zones · 3 languages · 6 tool-augmented agents · Built on the SCU Spring 2026 Analytics Showdown findings**

---

## The Problem

Good Nature Agro (GNA) is a Zambian agribusiness working with **22,000+ smallholder farmers** under a contract-farming model: GNA provides inputs (seed, inoculant, fertilizer) on credit, the farmer plants and grows, GNA buys back the harvest. Two structural problems make this fragile:

1. **Information asymmetry.** GNA knows what works (per-zone planting windows, which inputs lift yield, which farmers are at risk). Farmers don't — most farmer support is reactive, via field-agent visits that don't scale.
2. **Repayment risk.** **23% of farmers don't fully buy back** their loan. Some are unlucky (drought); some are mis-managed (planted late, skipped inoculant). GNA's existing field network can't visit everyone, so the *highest-risk farmers often get the least attention*.

A linear sum: better information → better outcomes → better repayment → better unit economics → more farmers served.

> **Why now?** WhatsApp is the *de facto* messaging layer in rural Zambia. Multi-language LLMs handle Bemba and Nyanja well enough to be useful. Together, the cost of reaching a farmer with personalized, grounded advice has gone from "send a field agent" to "send a message" — and that's a different business model.

---

## Users & Jobs-to-be-Done

| User | Job-to-be-Done | Today's Workaround | Pain |
|------|----------------|--------------------|------|
| **High-Risk Farmer (4,948 of them)** | When something looks wrong with my crop, I want a fast, trustworthy answer in *my* language so I don't lose the season. | Wait for field agent (days/weeks); ask neighbours | Late, generic, sometimes wrong |
| **Low-Risk / Experienced Farmer** | When I have a question, I want help that doesn't waste my time on basics I already know. | Field agent visit | Over-served on basics, under-served on edge cases |
| **Field Agent** | When a farmer's situation is escalating, I want to *call them with context* — not show up cold. | Reactive visits | Most calls are wasted on healthy farmers; the at-risk ones go missed |
| **GNA Executive** | When I plan procurement, I want a forecast grounded in *current* per-zone yield trajectories — not last year's average. | Spreadsheet + gut | Procurement misses, working-capital lockup |

---

## The Solution

A **5-agent orchestrated system** that pushes (nudges) and pulls (chat), with a separate executive briefing layer. Every farmer interaction goes through a tool-augmented Claude agent that can call up to 6 grounding tools per turn.

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
   WhatsApp (farmers)                              GNA Dashboard (execs)
   ←─────────────────                             ─────────────────────→
```

### Key product decisions (and the tradeoffs)

| Decision | What I picked | What I rejected | Why |
|----------|---------------|-----------------|-----|
| **WhatsApp as the channel, not an app** | Meta WhatsApp Business API + dev-mode simulator | Native Android app | Smallholder farmers in Zambia already use WhatsApp daily, often on shared devices. Building an app is a *behaviour change* on top of a product change. Meeting users in their existing channel is the entire reason this can work. |
| **Per-zone, per-risk-tier nudges** | High-Risk = every 3 days, Medium = weekly, Low = bi-weekly. Zone IIa plants 2 weeks after Zone III; nudge content shifts accordingly. | One uniform daily nudge to all 22K farmers | Uniform messaging is *spam* to low-risk farmers and *too rare* for high-risk. The cadence + content split means each farmer gets the right amount of attention for their actual situation. |
| **Tool-augmented chat, not pure LLM** | Claude with 6 tools (farmer analysis, peer benchmarks, rainfall, agronomy KB, market prices, web search) | Single fine-tuned model | Farmers ask hard questions ("Is rainfall normal in my zone this year?") that a base LLM would *guess* on. Tool-grounding turns guesses into citations. For a high-trust use case, this is the difference between a useful product and a dangerous one. |
| **Language detection per turn** | English / Bemba / Nyanja, detected per message, with keyword-override + langdetect fallback | English-only, with translation later | Farmers code-switch mid-conversation. Detecting per turn (not per session) means the reply is always in the language of the question. |
| **Demo-mode with keyless fallbacks** | Every external dependency degrades gracefully (Tavily → DuckDuckGo, Claude → templated reply, WhatsApp send → printed log) | "You need 4 API keys to even run this" | A demo that requires keys is a demo that never gets shown. Keyless fallback = anyone can `git clone && python main.py` and see the full system. **This was a deliberate choice to maximize who can evaluate the product.** |
| **Honest about what's simulated** | Top-of-README "Demo Data Disclosure" section listing exactly which numbers are simulated vs canonical | Quiet hand-waving | This is the most important PM-honesty muscle. Hiring managers spot inflated numbers immediately. Calling the line explicitly *raises* trust. |

---

## Impact & Metrics

> Findings carried forward from the **GNA Analytics Showdown** notebook (the canonical source of truth):

| Finding | Number | Why it matters |
|---------|--------|----------------|
| Farmer base | 22,597 | TAM for the platform |
| Buyback rate | 76.6% | The 23.4% gap is the business problem |
| Top-10% Pareto | 41% of volume | Retention of top farmers is critical |
| **Inoculant lift** | **+486 kg/ha** (p<0.001, zone-controlled) | Highest-confidence input recommendation |
| Fertilizer lift | +312 kg/ha (zone-controlled) | Second strongest input |
| Season-1 yield gap | 3.3× lower than experienced farmers | Auto-flag every first-season farmer as elevated risk |
| High-risk farmers | 4,948 (risk score > 0.45) | Pre-seeded high-priority outreach list |

**Product-side metrics (prototype):**
- 5 agroecological zones modeled (I, IIa, IIb, III, IV)
- 3 languages supported
- 6 tools the chat agent can call to ground a single answer
- Full system runs end-to-end with bundled `.env` (no API keys required)

---

## What I'd Build Next

| Priority | Feature | Why this, why now |
|----------|---------|-------------------|
| **P0** | **Real-world A/B of nudge cadence** | Today the High/Medium/Low cadence is set by intuition. A controlled trial (3-day vs 7-day for High Risk) gives a defensible answer on the cost-vs-engagement frontier and makes the next investment defensible. |
| **P0** | **Field-agent app integration** | Today escalations land as a printed event. A real WhatsApp-in / app-out for the 30 field agents closes the loop from *detect risk* → *triage* → *visit*. This is the highest-leverage human-in-the-loop. |
| **P1** | **Voice-note input** | Many farmers prefer voice to text. Whisper handles Bemba/Nyanja reasonably; supporting voice would expand the addressable user base meaningfully. |
| **P1** | **Inputs / loan top-up flow inside chat** | When the chat detects "I need more inoculant," the next message could be an actual order link + agreed loan addition. Closes the loop from *insight* → *transaction*. |
| **P2** | **Carbon credit data pipeline** | Smallholder regenerative ag is increasingly carbon-creditable. The platform already collects per-farmer planting/input data; with one extra schema, that's an MRV-grade dataset. New revenue line, no new product. |

**What I would NOT build next:** A general-purpose "ag chatbot." The defensibility here is *being deeply opinionated about GNA's farmers, GNA's contract structure, and Zambian zones*. Generic = irrelevant.

---

## My Role

**Practicum project for the SCU Spring 2026 Analytics Showdown** — a real engagement with Good Nature Agro.

**What I personally owned:**
- Translation of analytical findings (notebook) → live product (this platform)
- The per-zone × per-risk-tier nudge taxonomy and the schemas behind it (`season_calendar_by_zone.json`, `risk_tier_modifiers.json`)
- The "demo-mode with keyless fallbacks" architecture — letting evaluators run the full system with no setup
- The agent tool design (which 6 tools the chat agent can call, and when)
- The "Demo Data Disclosure" honesty section — explicitly labeling simulated vs canonical numbers
- The executive briefing report and dashboard design

---

## What I Learned

- **Channel choice is a product choice.** WhatsApp wasn't a "delivery" decision — it was the strategic call that made the whole product possible. If I'd defaulted to "build an app," nothing else mattered.
- **Cadence is a feature.** Sending the *same* message to 22K farmers feels efficient and is actually wasteful. Per-tier cadence multiplied attention where it matters and respect where it didn't.
- **Tool-augmented LLMs unlock high-trust use cases.** A pure LLM would be too risky for advice that affects a farmer's livelihood. Grounding every answer in 6 tools turns "AI says…" into "agronomy KB + your peer's benchmark + your zone's rainfall says…" — that's the difference between a product and a liability.
- **Demo-mode is a trust signal.** A system that runs end-to-end *without keys* signals confidence and respects the evaluator's time. Building it that way also forced cleaner abstraction throughout the codebase.
- **Honesty about what's simulated raises trust, doesn't lower it.** Most demos hand-wave; calling the line explicitly is what serious teams do.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Agent framework | Python + Anthropic Claude (`claude-sonnet-4-6`) |
| WhatsApp | Meta WhatsApp Business Cloud API (with dev-mode simulator) |
| Dashboard | React + Recharts |
| Memory / state | SQLite (dev) → PostgreSQL (prod) |
| Scheduler | APScheduler |
| Tools | Open-Meteo (rainfall), Tavily / DuckDuckGo (web search), bundled agronomy KB (rapidfuzz) |
| Languages | langdetect + Bemba / Nyanja keyword overlay |

---

## Quick Start

```bash
git clone https://github.com/sjagannathan17/GNA-Agri-Intelligence.git
cd GNA-Agri-Intelligence
pip install -r requirements.txt

cp .env.example .env # Bundled placeholders are enough to run end-to-end
python main.py # Starts the orchestrator + scheduler
python chat_repl.py # Talk to a simulated farmer
```

For the executive dashboard:
```bash
cd dashboard && npm install && npm run dev
```

> The full system runs **without any API keys** — see "Demo vs Production Posture" in the architecture docs. Adding `ANTHROPIC_API_KEY` and `TAVILY_API_KEY` upgrades to the production tool path.

---

## Repo Structure

```
gna-agri-intelligence/
├── README.md / ARCHITECTURE.md
├── main.py / chat_repl.py / build_ppt.py
├── agents/ # 5 agents (orchestrator, nudge, chat, risk, exec)
├── tools/ # 6 tools (analysis, peers, rainfall, agronomy KB, market, web)
├── memory/ # State, conversation history, risk scores
├── data/ # season_calendar_by_zone.json, risk_tier_modifiers.json, master_table.csv
├── dashboard/ # React executive dashboard
├── whatsapp/ # Meta API integration + dev-mode simulator
└── tests/
```

---

## License & Disclaimer

Educational + research project. Built on findings from the **GNA Analytics Showdown (Spring 2026)** at Santa Clara University. **Not a deployed production system** — see the "Demo Data Disclosure" section for what's simulated vs canonical.

---

**Built by [Srinidhi Jagannathan](https://github.com/sjagannathan17)** · [Portfolio](https://portfolio-pi-olive-yfvgxx81kp.vercel.app) · [LinkedIn](https://linkedin.com/in/srinidhi-jagannathan) · srinidhi.jagan11@gmail.com
