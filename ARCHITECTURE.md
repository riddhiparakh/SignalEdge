# SignalEdge — Architecture

> **What it is:** A signal-detection research tool that reads breaking news, identifies which
> open prediction markets are affected, and flags where the market price hasn't yet caught up
> to what the news implies. NOT a trading bot. NOT financial advice.

---

## System Overview

```
┌─────────────────────────────────────────────────────┐
│                   run_pipeline.py                    │
│         (entry point — run hourly or on demand)      │
└────────────┬──────────────────────┬─────────────────┘
             │                      │
    ┌────────▼────────┐    ┌────────▼────────┐
    │ ingestion/      │    │ ingestion/      │
    │ markets.py      │    │ news.py         │
    │                 │    │                 │
    │ Polymarket API  │    │ newsdata.io API │
    │ (no auth)       │    │ (free API key)  │
    └────────┬────────┘    └────────┬────────┘
             │                      │
             ▼                      ▼
    ┌─────────────────────────────────────┐
    │              db/                    │
    │  signalEdge.db (SQLite)             │
    │  ├── markets          (registry)    │
    │  ├── market_prices    (time series) │
    │  ├── news_items       (headlines)   │
    │  ├── agent_judgments  (Phase 2)     │
    │  └── scores           (Phase 3)     │
    │                                     │
    │  chroma/  (Phase 2 — vector store)  │
    │  └── headline embeddings            │
    └──────────────────────────────────────┘
```

---

## Phase 1 — Data Ingestion (current)

### What runs
```
python run_pipeline.py --ingest-only
```

### Components

#### `ingestion/markets.py`
- Calls Polymarket Gamma API (no key required)
- Filters: active=true, closed=false, volume ≥ $1,000
- Writes to `markets` (one row per unique market, INSERT OR IGNORE)
- Writes to `market_prices` (one row per run — builds time series)
- Running hourly for 7 days → ~168 price points per market

#### `ingestion/news.py`
- Calls newsdata.io (free API key, 200 credits/day)
- For each market: extracts keyword query, fetches up to 10 headlines
- Guardrails checked BEFORE any API call:
  - Sensitive topic filter (terrorism, private individuals, medical)
  - Only processes top 15 markets per run (free tier budget)
- Each headline stored with: title, URL (for citations), source, published_at
- `hours_ago()` computes display age ("2h ago") at read time

### Data schema (Phase 1)

| Table | Purpose | Grows? |
|---|---|---|
| `markets` | One row per unique market ever seen | Once per new market |
| `market_prices` | Price snapshot per pipeline run | Every run |
| `news_items` | Headlines fetched from newsdata.io | Every run |

---

## Phase 2 — Agent Brain ✓

```
python run_pipeline.py          # full pipeline including agent
python run_pipeline.py --ingest-only  # skip agent (ingestion only)
```

### Components

#### `agent/rag.py` — ChromaDB vector store
- `index_articles(articles, client=None)` — embeds title+description, stores in ChromaDB. Idempotent (skips existing IDs). Returns count of newly added articles.
- `find_relevant(question, top_k=10, client=None)` — semantic nearest-neighbour search. Returns articles ordered by cosine similarity to the query.
- Why vector search over SQL LIKE? SQL matches exact keywords. Vectors match MEANING. "FOMC monetary policy" correctly surfaces articles about "Fed interest rate decisions" with no shared words.
- Default embedding model: `sentence-transformers/all-MiniLM-L6-v2` (~90 MB, auto-downloaded once, then cached)
- Persistence: `db/chroma/` directory survives between pipeline runs — articles accumulate

#### `agent/brain.py` — Claude reasoning engine
- `MODEL = "claude-haiku-4-5"` — chosen for cost (~$0.03/run vs $0.15 for Opus). Structured JSON is well within Haiku's capability.
- `analyze_market(market, articles, anthropic_client=None)`:
  1. Builds a numbered headline bundle (title, source, age, URL, summary)
  2. Calls Claude with a structured system prompt (probability ranges, cite-only-provided-URLs guardrail, no-personal-opinion guardrail)
  3. Parses and validates the JSON response (`_parse_response()` handles markdown fences and field validation)
  4. Computes `divergence = agent_midpoint - market_price` (positive = market underpriced, negative = overpriced)
- `save_judgment(judgment)` — INSERTs to `agent_judgments`, returns row ID
- `run(markets, chroma_client=None, anthropic_client=None)`:
  1. Loads recent articles from SQLite
  2. Indexes them into ChromaDB (idempotent)
  3. For each market: RAG query → Claude call → save judgment
  4. Prints direction arrow (↑/↓/→), agent range, market price, divergence

### Agent output schema

| Field | Type | Meaning |
|---|---|---|
| `direction` | "up"/"down"/"neutral" | Which way the market should move |
| `confidence_low/high` | 0.0–1.0 | Agent's probability range for YES |
| `divergence` | float | `agent_midpoint - market_price` (+ = underpriced) |
| `rationale` | text | 2-3 sentence explanation citing headline numbers |
| `cited_urls` | JSON array | Only URLs from the provided headlines |
| `was_sufficient` | bool | False if agent said "insufficient evidence" |

### System prompt guardrails (enforced in prompt, not code)

- "Base assessment ONLY on provided headlines" → no hallucinated context
- "Give a probability RANGE" → honesty about uncertainty
- "Cite ONLY provided URLs" → no invented sources
- "If insufficient evidence, set was_sufficient=false" → graceful degradation
- "Do NOT recommend placing bets" → tool framing preserved

### Data schema (Phase 2 additions)

| Table | Purpose | Grows? |
|---|---|---|
| `agent_judgments` | One row per market per run | Every run |
| `db/chroma/` | Vector index of all headlines | Accumulates |

---

## Phase 3 — Evaluation ✓

```
python run_pipeline.py   # runs scoring automatically as Step 5
```

### What it does

When Polymarket resolves a market (final price → 1.0 for YES, 0.0 for NO), the scorer:
1. Detects the resolution by comparing Polymarket's closed-market list against our tracked IDs
2. Grades every agent judgment for that market using `_is_correct(direction, final_price)`
3. Inserts a row into `scores` with `was_correct = 1 / 0 / NULL`
4. Marks the market `active = 0` in our DB and records `resolution_price`

### Grading logic (`_is_correct` — pure function, no DB, fully testable)

| Agent direction | Market resolved | `was_correct` |
|---|---|---|
| "up" | YES (price ≥ 0.9) | 1 |
| "up" | NO  (price ≤ 0.1) | 0 |
| "down" | NO  (price ≤ 0.1) | 1 |
| "down" | YES (price ≥ 0.9) | 0 |
| "neutral" | either | NULL (no grade) |
| any | 0.1 < price < 0.9 | NULL (ambiguous) |

### Schema additions (Phase 3 migration)

`init_db()` runs `ALTER TABLE markets ADD COLUMN resolution_price / resolved_at` via
try/except — idempotent, safe to run on old databases.

### Track record output (`get_track_record()`)

```python
{
  "total_scored": 15,       # includes neutral abstentions
  "total_graded": 12,       # excludes neutrals (has actual verdict)
  "correct": 8,
  "hit_rate": 0.667,
  "by_direction": {
    "up":   {"graded": 7, "correct": 5, "hit_rate": 0.714},
    "down": {"graded": 5, "correct": 3, "hit_rate": 0.600},
  },
  "sample_size_warning": True,   # True when total_graded < 30
  "sample_size_note": "Results based on 12 graded judgment(s). Insufficient data..."
}
```

The `sample_size_warning` flag is intentional — hit rates are meaningless below ~30 samples.
This is displayed prominently in the Phase 4 Track Record tab.

---

## Phase 4 — Dashboard ✓

```
streamlit run dashboard/app.py
# → http://localhost:8501
```

### 4 tabs

#### Tab 1 — Live Markets (`tab_live_markets`)
- Summary metrics: market count, total volume, avg YES price
- One expander per market: YES price, volume, category
- `st.line_chart()` of YES price history from `market_prices` time series (one point per pipeline run)
- Empty state with instructions when no data exists

#### Tab 2 — Divergence Feed (`tab_divergence_feed`)
- Shows every agent judgment, newest first
- Radio filter: All / Up / Down / Neutral
- Per-judgment: direction badge (🟢/🔴/⚪), market price vs agent range vs divergence %, rationale text, clickable cited URLs
- Age and evidence-sufficiency flag in caption

#### Tab 3 — Research Chat (`tab_research_chat`)
- `st.chat_input()` + `st.chat_message()` — persisted via `st.session_state`
- Sensitive topic guardrail fires BEFORE ChromaDB call (same `is_sensitive()` as ingestion)
- `search_headlines()` cached 60s — repeated identical queries feel instant
- Returns: title, source, age (`hours_ago()`), summary, clickable URL for each result
- Empty store handled gracefully with instructions to run the pipeline

#### Tab 4 — Track Record (`tab_track_record`)
- Sample-size warning shown FIRST (before any hit rate metric) when n < 30
- Metrics: hit rate, correct/graded, neutral abstentions
- DataFrame breakdown by direction (up/down)
- Honest framing caption: explains confidence intervals at small n

### Key Streamlit patterns used

| Pattern | Why |
|---|---|
| `@st.cache_data(ttl=300)` | DB queries cached 5 min — no re-read on every widget click |
| `st.session_state` | Chat history persists across Streamlit reruns |
| `sys.path.insert(0, _ROOT)` | Fixes imports when launched as `streamlit run dashboard/app.py` |
| Empty-state checks before rendering | Every tab shows helpful instructions instead of errors on fresh install |

---

## Guardrails (current implementation)

| Guardrail | Where enforced | Behaviour |
|---|---|---|
| Live markets only | `markets.py` fetch params | `active=true, closed=false` filter |
| Low volume filter | `markets.py _parse_market()` | Skip markets < $1,000 volume |
| Sensitive topics | `news.py is_sensitive()` | Block API call, return [] |
| No key, no call | `news.py fetch_news_for_market()` | Raise EnvironmentError clearly |
| Age transparency | `news.py hours_ago()` | Every headline shows "Xh ago" |
| Quality threshold | Phase 2 agent | Refuse to flag if < 5 headlines |
| No personal opinion | Phase 2 prompt | "evidence suggests" framing enforced in prompt |
| No false precision | Phase 2 prompt | Probability ranges only (e.g. "55–65%") |
| Resolved market guard | Phase 2 chat | Refuse research on non-live markets |

---

## Interview Questions This Architecture Answers

**"Walk me through what happens from a news headline arriving to a flag on screen."**
> Pipeline runs → markets fetched → for each market, newsdata.io queried with keyword → headlines stored with timestamps → agent retrieves relevant headlines via vector search → sends bundle to Claude → Claude returns direction + rationale + citations → divergence computed vs current market price → flagged if gap exceeds threshold → displayed in dashboard with cited sources.

**"How do you know the agent is actually good and not just lucky?"**
> We log the market price at the exact moment of each agent call. When a market resolves, we check whether the agent's direction was correct. We report hit rate with full sample size and confidence intervals — never claiming edge without statistical backing.

**"What would break if this had 10,000 markets instead of 50?"**
> Three things: (1) newsdata.io free tier would be exhausted in minutes — need a paid tier or multiple API keys; (2) ChromaDB would slow down on similarity search without an index tuned for that scale; (3) the pipeline would take hours to run — would need to parallelize with async/threading.

**"Why prediction markets — what's the real-world use case?"**
> Prediction markets are real-time probability estimates priced by many participants. When markets are slow to react to news (information asymmetry), there's a brief window where the price hasn't caught up. That gap is the signal. A firm doing macro research or event-driven investing would pay for a system that surfaces those gaps with cited evidence.

**"What did you intentionally NOT build, and why?"**
> No execution layer — this is a research tool, not a trading bot. No real-time streaming — polling every hour is sufficient for the signal we care about and keeps infrastructure simple. No LLM fine-tuning — Claude's general reasoning is strong enough, and fine-tuning would require labeled data we don't have yet.

**"What is RAG and why does SignalEdge use it?"**
> RAG = Retrieval-Augmented Generation. Instead of asking the LLM to answer from its training data alone, we first retrieve relevant documents and include them in the prompt. SignalEdge uses it so Claude's judgment is grounded in the actual headlines in our database — not stale training knowledge. ChromaDB converts each headline into a vector embedding; when a market question arrives, we find the nearest-neighbour headlines by cosine similarity and inject them into the prompt.

**"Why does the agent return a range (35–55%) instead of a single number?"**
> A single number implies false precision. With 10 headlines covering a complex event, the true uncertainty is wide. Forcing a range (confidence_low, confidence_high) makes Claude acknowledge what it doesn't know. The divergence signal is computed from the midpoint of the range, but displaying the range in the UI lets a human researcher calibrate how much to trust it.

**"Why Claude Haiku instead of Opus for this task?"**
> This is structured information extraction — Claude receives 10 headlines and must output JSON with five fields. It doesn't require deep multi-step reasoning. Haiku costs ~$0.004 per call vs $0.020 for Opus. With 8 markets per run, that's $0.032 vs $0.16 per pipeline run — a 5× difference. For a portfolio project running hundreds of test cycles, Haiku saves ~$20. Critically, the model choice is a single constant (`MODEL = "claude-haiku-4-5"`) — swapping to Opus takes 30 seconds if accuracy improvements justify the cost.

**"How do you evaluate whether the agent is actually good?"**
> When a market resolves, we grade every judgment we made for it: agent said "up" and market resolved YES → correct; agent said "up" and market resolved NO → wrong. We log the market price at the moment of the call (not after resolution) so we can't cherry-pick. Hit rate is reported with a sample-size warning below 30 graded judgments — at small samples, any % is statistically meaningless and we say so explicitly.

**"Why do neutral judgments get a scores row with was_correct=NULL instead of being skipped?"**
> We insert a row so the scorer's idempotency check (LEFT JOIN where s.id IS NULL) doesn't re-attempt them on future runs. The NULL verdict is excluded from hit rate calculations. Without this, the scorer would re-examine the same judgment every pipeline run thinking it was unscored.

**"How do you handle the fact that markets sometimes resolve ambiguously — at 0.5 for example?"**
> The `_is_correct()` function uses thresholds: final_price >= 0.9 counts as YES resolved, <= 0.1 counts as NO resolved. Anything in between returns None (no grade). This prevents us from grading a market that settled at an intermediate price due to a partial resolution or data error. The 0.1 buffer exists because Polymarket binary markets settle at exactly 0 or 1 in practice.

**"How do you prevent the agent from hallucinating sources?"**
> Three layers: (1) the system prompt explicitly says "cite ONLY URLs from the provided headlines" and "never invent sources"; (2) cited URLs are stored as a JSON array and can be cross-checked against the `news_items` table at render time; (3) the `_parse_response()` validator checks that cited_urls exists as a field — if Claude drops it, the response is rejected.
