"""
Agent brain: the core reasoning engine for SignalEdge.

For each market, the agent:
  1. Retrieves semantically relevant headlines from the ChromaDB store (RAG)
  2. Bundles them with the current YES price into a structured prompt
  3. Calls Claude Haiku to assess whether the market is correctly priced
  4. Parses the JSON response and computes the divergence signal
  5. Saves the judgment to agent_judgments for display and scoring

This is NOT a trading bot. The agent outputs a signal (direction + rationale)
so a human can do their own research. It never recommends placing a bet.
"""

import json
import os
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

from db.schema import get_connection
from agent import rag

load_dotenv()

# Use Haiku for this project: ~$0.03/run vs $0.15 for Opus. Structured JSON
# analysis is well within Haiku's capabilities.
MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = """You are SignalEdge — a prediction market signal detector.

Your job: analyse recent news headlines and assess whether a Polymarket prediction market is correctly priced.

The market's YES price is a crowd-sourced probability (e.g. 0.42 means the crowd thinks there's a 42% chance of YES).

Rules:
1. Base your assessment ONLY on the provided headlines. Do not use prior knowledge or outside information.
2. Express uncertainty honestly: give a probability RANGE (confidence_low to confidence_high), not a point estimate.
3. "direction" is the implied signal:
   - "up"      → headlines suggest YES is MORE likely than the market price implies (market is underpriced)
   - "down"    → headlines suggest YES is LESS likely than the market price implies (market is overpriced)
   - "neutral" → headlines don't suggest a meaningful mispricing
4. Cite ONLY URLs from the provided headlines. Never invent or hallucinate sources.
5. If the headlines are irrelevant, outdated, or insufficient to form a view, set was_sufficient to false and direction to "neutral".
6. Do NOT recommend placing bets or express personal opinions. This is signal analysis only.
7. Do NOT comment on sensitive topics (violence, personal medical details, private individuals).

Respond with ONLY valid JSON — no markdown fences, no explanation outside the JSON:
{
  "direction": "up" | "down" | "neutral",
  "confidence_low": <float 0.0–1.0>,
  "confidence_high": <float 0.0–1.0>,
  "rationale": "<2-3 sentences citing specific headlines by number, explaining the signal>",
  "cited_urls": ["<url>", ...],
  "was_sufficient": <true | false>
}"""


def _build_user_message(market: dict, articles: list[dict]) -> str:
    """
    Format a market + its relevant headlines into the user message Claude receives.

    Numbering each headline [1], [2], ... lets Claude reference them by number
    in its rationale, which is easier to verify than quoting long URLs inline.
    """
    yes_pct = round(market.get("yes_price", 0) * 100, 1)

    lines = [
        f"MARKET: {market['question']}",
        f"CURRENT YES PRICE: {market.get('yes_price', '?')} (market implies {yes_pct}% probability of YES)",
        f"RESOLUTION DATE: {market.get('end_date', 'unknown')}",
        "",
        f"HEADLINES ({len(articles)} total, ordered by relevance to this market):",
    ]

    for i, article in enumerate(articles, start=1):
        from ingestion.news import hours_ago
        age = hours_ago(article.get("published_at", ""))
        lines.append(f"\n[{i}] {article.get('source', 'unknown')} · {age}")
        lines.append(f"Title: {article.get('title', '')}")
        lines.append(f"URL: {article.get('url', '')}")
        description = article.get("description", "").strip()
        if description:
            lines.append(f"Summary: {description[:300]}")

    return "\n".join(lines)


def _parse_response(text: str) -> dict | None:
    """
    Extract and validate the JSON object from Claude's response.
    Returns None if parsing fails or required fields are missing/invalid.
    Claude occasionally wraps JSON in markdown fences — strip them first.
    """
    # Strip markdown code fences if present (```json ... ```)
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    # Validate required fields
    required = {"direction", "confidence_low", "confidence_high", "rationale", "cited_urls", "was_sufficient"}
    if not required.issubset(data.keys()):
        return None

    if data["direction"] not in ("up", "down", "neutral"):
        return None

    # Clamp confidence values to valid range
    low = float(data["confidence_low"])
    high = float(data["confidence_high"])
    if not (0.0 <= low <= 1.0 and 0.0 <= high <= 1.0 and low <= high):
        return None

    return data


def analyze_market(market: dict, articles: list[dict], anthropic_client=None) -> dict | None:
    """
    Call Claude to judge whether a market is correctly priced given its headlines.

    Returns a judgment dict ready to pass to save_judgment(), or None on failure.
    Inject anthropic_client in tests to mock the API call (no real cost in CI).
    """
    if not articles:
        print(f"  SKIPPED (no relevant articles): {market.get('question', '')[:60]}")
        return None

    if anthropic_client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key."
            )
        anthropic_client = anthropic.Anthropic(api_key=api_key)

    user_message = _build_user_message(market, articles)

    try:
        response = anthropic_client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as e:
        print(f"  ERROR: Claude API error — {e}")
        return None

    raw_text = response.content[0].text
    parsed = _parse_response(raw_text)

    if parsed is None:
        print(f"  ERROR: Could not parse Claude response: {raw_text[:200]}")
        return None

    market_price = market.get("yes_price", 0.0)
    agent_midpoint = (parsed["confidence_low"] + parsed["confidence_high"]) / 2

    # Divergence: positive = market underpriced, negative = market overpriced
    # e.g. agent says 0.68, market says 0.42 → divergence = +0.26 → signal is "up"
    divergence = round(agent_midpoint - market_price, 4)

    return {
        "market_id": market["id"],
        "news_ids": json.dumps([a["id"] for a in articles]),
        "direction": parsed["direction"],
        "confidence_low": parsed["confidence_low"],
        "confidence_high": parsed["confidence_high"],
        "rationale": parsed["rationale"],
        "cited_urls": json.dumps(parsed["cited_urls"]),
        "market_price_at_call": market_price,
        "headline_count": len(articles),
        "was_sufficient": 1 if parsed["was_sufficient"] else 0,
        "divergence": divergence,       # computed field for display (not in DB schema)
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_judgment(judgment: dict) -> int:
    """
    Persist an agent judgment to the agent_judgments table.
    Returns the new row's primary key (useful for scoring in Phase 3).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO agent_judgments
            (market_id, news_ids, direction, confidence_low, confidence_high,
             rationale, cited_urls, market_price_at_call, headline_count, was_sufficient, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        judgment["market_id"],
        judgment["news_ids"],
        judgment["direction"],
        judgment["confidence_low"],
        judgment["confidence_high"],
        judgment["rationale"],
        judgment["cited_urls"],
        judgment["market_price_at_call"],
        judgment["headline_count"],
        judgment["was_sufficient"],
        judgment["created_at"],
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def _load_recent_articles(max_hours: int = 48) -> list[dict]:
    """Load articles fetched in the last max_hours hours from SQLite."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT id, title, description, url, source, published_at
        FROM news_items
        WHERE fetched_at >= datetime('now', ?)
        ORDER BY published_at DESC
    """, (f"-{max_hours} hours",)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def run(markets: list[dict], chroma_client=None, anthropic_client=None) -> list[dict]:  # noqa: E501
    """
    Main entry point for the agent phase.

    1. Load recent articles from SQLite and index them into ChromaDB.
    2. For each market, retrieve semantically relevant headlines via RAG.
    3. Call Claude to generate a divergence judgment.
    4. Save each judgment to the DB.
    Returns a list of all saved judgments.
    """
    articles = _load_recent_articles(max_hours=168)
    print(f"  Loaded {len(articles)} recent articles from DB")

    indexed = rag.index_articles(articles, client=chroma_client)
    print(f"  ChromaDB: {indexed} new articles indexed ({len(articles)} total in store)")

    judgments = []

    for market in markets:
        question = market.get("question", "")
        print(f"\n  Analysing: {question[:65]}...")

        relevant = rag.find_relevant(question, top_k=10, client=chroma_client)
        if not relevant:
            print("    → No relevant articles found — skipping")
            continue

        judgment = analyze_market(market, relevant, anthropic_client=anthropic_client)
        if judgment is None:
            continue

        judgment_id = save_judgment(judgment)
        judgment["id"] = judgment_id

        direction_arrow = {"up": "↑", "down": "↓", "neutral": "→"}.get(judgment["direction"], "?")
        print(
            f"    → {direction_arrow} {judgment['direction'].upper()} | "
            f"agent: {judgment['confidence_low']:.0%}–{judgment['confidence_high']:.0%} | "
            f"market: {judgment['market_price_at_call']:.0%} | "
            f"divergence: {judgment['divergence']:+.1%}"
        )
        judgments.append(judgment)

    print(f"\nAgent phase complete: {len(judgments)} judgments saved")
    return judgments
