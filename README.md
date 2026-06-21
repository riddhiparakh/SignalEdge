# 📡 SignalEdge

**AI-powered prediction market signal detector.**  
Monitors [Polymarket](https://polymarket.com) prices + breaking news to flag when market probabilities haven't caught up to what the evidence implies.

> **Not financial advice. Not a trading bot. Research and signal-detection tool only.**

---

## What it does

Prediction markets are crowd-sourced probability estimates. When a market says "42% chance the Fed cuts rates in September," it's aggregating thousands of traders' views. But markets can be slow to react to news — creating a brief window where the price hasn't caught up.

SignalEdge closes that gap:

1. **Fetches** active Polymarket markets (35+ at a time, filtered by volume)
2. **Finds** relevant breaking headlines using semantic search (ChromaDB RAG)
3. **Asks Claude** to assess whether the market is correctly priced given the evidence
4. **Flags divergences** — showing where the signal and the crowd disagree, with cited sources
5. **Tracks accuracy** over time with honest hit-rate reporting (sample-size warnings included)

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│              Next.js Frontend (port 3000)                  │
│   /markets  /signals  /research  /track-record            │
└─────────────────────┬────────────────────────────────────┘
                      │ REST (fetch)
┌─────────────────────▼────────────────────────────────────┐
│              FastAPI Backend (port 8000)                   │
│   GET /api/markets   GET /api/judgments                   │
│   POST /api/research  POST /api/pipeline/*                │
└──────┬───────────────┬──────────────────┬────────────────┘
       │               │                  │
┌──────▼──────┐ ┌──────▼──────┐  ┌───────▼──────┐
│  Polymarket │ │ newsdata.io │  │  Claude      │
│  Gamma API  │ │ (headlines) │  │  Haiku 4.5   │
└──────┬──────┘ └──────┬──────┘  └───────┬──────┘
       │               │                  │
┌──────▼───────────────▼──────────────────▼──────┐
│           SQLite DB  +  ChromaDB                │
│  markets · market_prices · news_articles        │
│  agent_judgments · scores  |  vector embeddings │
└─────────────────────────────────────────────────┘
```

---

## Quick start (local)

### Prerequisites

- Python 3.10+, Node.js 18+
- API keys: `ANTHROPIC_API_KEY`, `NEWSDATA_API_KEY`

### 1. Clone and install Python deps

```bash
git clone https://github.com/riddhiparakh/SignalEdge.git
cd SignalEdge
pip install -r requirements.txt
cp .env.example .env    # fill in your API keys
```

### 2. Run the pipeline

```bash
python run_pipeline.py --ingest-only   # no Anthropic cost — just data
python run_pipeline.py                 # full run including Claude agent
```

### 3. Start the FastAPI backend

```bash
uvicorn api.main:app --reload
# → http://localhost:8000/docs  (interactive Swagger UI)
```

### 4. Start the Next.js frontend

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
# → http://localhost:3000
```

Use the **sidebar pipeline controls** in the UI to fetch markets, news, and run the agent without a terminal.

### 5. (Optional) Streamlit dashboard

```bash
streamlit run dashboard/app.py
# → http://localhost:8501
```

---

## Tech stack

| Layer | Technology | Why |
|---|---|---|
| Market data | Polymarket Gamma API | Real-time prices, no auth required |
| News data | newsdata.io (free tier) | 200 credits/day, 10 articles/query |
| Vector store | ChromaDB | Semantic headline retrieval (RAG) |
| LLM | Claude Haiku 4.5 | ~$0.004/call — 800 runs for $25 |
| Database | SQLite | Zero-infrastructure time-series + registry |
| Backend API | FastAPI + Uvicorn | REST layer for Next.js consumption |
| Frontend | Next.js 14 + Tailwind CSS | Dark-theme dashboard, App Router |
| Charts | Recharts | Price history line charts |
| Alt dashboard | Streamlit | Single-file UI, 1-command deploy |
| Testing | pytest + pytest-mock | 40+ tests, zero real API calls |

---

## Project structure

```
SignalEdge/
├── run_pipeline.py          # Entry point — runs all 5 pipeline steps
├── requirements.txt
│
├── api/
│   └── main.py              # FastAPI REST endpoints (9 routes)
│
├── frontend/                # Next.js 14 frontend
│   ├── app/                 # App Router pages
│   │   ├── markets/         # Live market cards + price history
│   │   ├── signals/         # Divergence signals with filter tabs
│   │   ├── research/        # Semantic headline search chat
│   │   └── track-record/    # Accuracy grading + hit-rate table
│   ├── components/          # Sidebar, MarketCard, SignalCard, PriceChart, ChatInterface
│   └── lib/                 # TypeScript types + API client (api.ts, types.ts)
│
├── db/
│   └── schema.py            # Single source of truth for all 5 tables + migrations
│
├── markets/
│   └── polymarket.py        # Polymarket fetcher + price time-series writer
│
├── news/
│   └── newsdata.py          # newsdata.io client + sensitive-topic guardrails
│
├── agent/
│   ├── rag.py               # ChromaDB: index headlines, semantic search
│   ├── brain.py             # Claude prompt + JSON parsing + divergence calc
│   └── scorer.py            # Resolution detection + hit-rate tracking
│
├── dashboard/
│   └── app.py               # Streamlit 4-tab dashboard (alternative UI)
│
└── tests/                   # pytest — run with `pytest tests/ -v`
```

---

## How divergence signals work

A market trades at `YES = 0.40` (40% implied probability). The agent reads 8 recent headlines, reasons about them, and concludes the probability is 60–70%. The midpoint is 65%.

```
divergence = 0.65 − 0.40 = +0.25  →  market underpriced by 25pp
direction  = "up"
```

Signals are graded automatically when markets resolve:
- `up` + resolved YES → ✓ correct
- `up` + resolved NO  → ✗ wrong
- `neutral` → never graded (abstention)

Hit rates are reported honestly with a sample-size warning below 30 graded calls.

---

## Guardrails

| Guardrail | Where | Behaviour |
|---|---|---|
| Live markets only | `markets.py` | `active=true, closed=false` |
| Low-volume filter | `markets.py` | Skip markets under $1,000 volume |
| Sensitive topics | `news.py is_sensitive()` | Block API call before it's made |
| No invented sources | Agent prompt + `_parse_response()` | Cited URLs must come from provided headlines |
| Probability ranges | Agent prompt | Ranges only — no false point-estimate precision |
| Sample-size warning | `scorer.py` | Warn when n < 30 graded judgments |
| "Not financial advice" | Dashboard + agent prompt | Framing preserved at every layer |

---

## Running tests

```bash
pytest tests/ -v
# 40+ tests — all mocked, zero real API calls
```

---

## Deployment

**Frontend → Vercel**

```bash
cd frontend && npx vercel
# Set NEXT_PUBLIC_API_URL to your backend URL in Vercel's environment settings
```

**Backend → Railway / Render / Fly.io**

```bash
uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

**Alt: Streamlit Cloud**

Connect this repo at [share.streamlit.io](https://share.streamlit.io) with main file `dashboard/app.py`. Add secrets in the UI:

```toml
NEWSDATA_API_KEY  = "pub_your_key_here"
ANTHROPIC_API_KEY = "sk-ant-your_key_here"
```

---

## About this project

Built as a portfolio project for a Columbia MSBA program. Demonstrates:

- **End-to-end AI system design** — raw API data → vector store → LLM reasoning → REST API → React frontend
- **RAG architecture** — ChromaDB stores all headlines as embeddings; semantic search surfaces relevant articles for any query without keyword matching
- **Honest evaluation** — the system tracks its own accuracy and flags when sample sizes are too small to mean anything
- **Production-minded testing** — 40+ tests with zero real API calls; dependency injection throughout
- **Full-stack AI product** — FastAPI backend + Next.js frontend + Streamlit alternative, all from one Python pipeline

The agent uses **Claude Haiku 4.5** for structured JSON analysis — chosen over larger models because the task (classify headlines into structured JSON) doesn't need deep reasoning, and the cost difference is 5×.

---

*Riddhi Parakh · Columbia MSBA · [rp3326@columbia.edu](mailto:rp3326@columbia.edu) · [github.com/riddhiparakh](https://github.com/riddhiparakh)*
