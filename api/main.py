"""
FastAPI backend for SignalEdge.

Wraps all existing Python logic (SQLite, ChromaDB, Claude, Polymarket) behind
a REST API so the Next.js frontend can read and trigger actions without any
direct database access.

Run:
    uvicorn api.main:app --reload --port 8000
"""

import json
import os
import sys
from datetime import datetime, timezone

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Project root → sys.path so all existing modules import correctly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db.schema import get_connection, init_db
from agent.rag import find_relevant
from agent.scorer import get_track_record
from ingestion.news import hours_ago, is_sensitive
from ingestion import markets as markets_module
from ingestion import news as news_module
from agent import brain as brain_module

app = FastAPI(title="SignalEdge API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ─────────────────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    query: str
    top_k: int = 8


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ── Markets ────────────────────────────────────────────────────────────────────

@app.get("/api/markets")
def get_markets():
    conn = get_connection()
    rows = conn.execute("""
        SELECT m.id, m.question, m.category, m.end_date,
               mp.yes_price, mp.no_price, mp.volume, mp.fetched_at
        FROM markets m
        LEFT JOIN market_prices mp ON mp.market_id = m.id
        WHERE m.active = 1
        GROUP BY m.id
        HAVING mp.fetched_at = MAX(mp.fetched_at)
        ORDER BY mp.volume DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/markets/{market_id}/prices")
def get_market_prices(market_id: str, limit: int = 200):
    conn = get_connection()
    rows = conn.execute("""
        SELECT yes_price, fetched_at FROM market_prices
        WHERE market_id = ?
        ORDER BY fetched_at ASC
        LIMIT ?
    """, (market_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Judgments (Divergence Feed) ───────────────────────────────────────────────

@app.get("/api/judgments")
def get_judgments(limit: int = 50, direction: str = "all"):
    conn = get_connection()
    if direction != "all":
        rows = conn.execute("""
            SELECT j.*, m.question FROM agent_judgments j
            JOIN markets m ON m.id = j.market_id
            WHERE j.direction = ?
            ORDER BY j.created_at DESC LIMIT ?
        """, (direction, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT j.*, m.question FROM agent_judgments j
            JOIN markets m ON m.id = j.market_id
            ORDER BY j.created_at DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()

    results = []
    for r in rows:
        d = dict(r)
        try:
            d["cited_urls"] = json.loads(d.get("cited_urls") or "[]")
        except Exception:
            d["cited_urls"] = []
        try:
            d["news_ids"] = json.loads(d.get("news_ids") or "[]")
        except Exception:
            d["news_ids"] = []
        if d.get("confidence_low") is not None and d.get("confidence_high") is not None:
            mid = (d["confidence_low"] + d["confidence_high"]) / 2
            d["divergence"] = round(mid - d["market_price_at_call"], 4)
        d["age"] = hours_ago(d.get("created_at", ""))
        d["was_sufficient"] = bool(d.get("was_sufficient"))
        results.append(d)
    return results


# ── Track record ───────────────────────────────────────────────────────────────

@app.get("/api/track-record")
def track_record():
    return get_track_record()


# ── Research chat ──────────────────────────────────────────────────────────────

@app.post("/api/research")
def research(body: ResearchRequest):
    if is_sensitive(body.query):
        return {"articles": [], "sensitive": True, "query": body.query}

    articles = find_relevant(body.query, top_k=body.top_k)
    for a in articles:
        a["age"] = hours_ago(a.get("published_at", ""))

    return {"articles": articles, "sensitive": False, "query": body.query}


# ── Pipeline triggers ──────────────────────────────────────────────────────────

@app.post("/api/pipeline/markets")
def pipeline_markets():
    init_db()
    fetched = markets_module.run()
    return {"status": "ok", "count": len(fetched)}


@app.post("/api/pipeline/news")
def pipeline_news():
    conn = get_connection()
    rows = conn.execute("""
        SELECT m.id, m.question, mp.yes_price, mp.volume
        FROM markets m
        LEFT JOIN market_prices mp ON mp.market_id = m.id
        WHERE m.active = 1
        GROUP BY m.id HAVING mp.fetched_at = MAX(mp.fetched_at)
    """).fetchall()
    conn.close()
    markets = [dict(r) for r in rows]
    if not markets:
        return {"status": "error", "message": "No markets found — run /api/pipeline/markets first."}
    articles = news_module.run(markets)
    return {"status": "ok", "count": len(articles)}


@app.post("/api/pipeline/agent")
def pipeline_agent():
    conn = get_connection()
    rows = conn.execute("""
        SELECT m.id, m.question, m.end_date, m.category,
               mp.yes_price, mp.no_price, mp.volume
        FROM markets m
        LEFT JOIN market_prices mp ON mp.market_id = m.id
        WHERE m.active = 1
        GROUP BY m.id HAVING mp.fetched_at = MAX(mp.fetched_at)
    """).fetchall()
    conn.close()
    markets = [dict(r) for r in rows]
    if not markets:
        return {"status": "error", "message": "No markets found — run /api/pipeline/markets first."}
    judgments = brain_module.run(markets)
    return {"status": "ok", "count": len(judgments)}
