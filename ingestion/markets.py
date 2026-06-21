"""
Fetches open prediction markets from the Polymarket Gamma API.

Polymarket is a prediction market where each "market" is a yes/no question
(e.g. "Will the Fed cut rates in Sept 2025?"). The current YES price = the
crowd's implied probability (0.35 = 35% chance).

No API key required — Gamma API is fully public.
"""

import json
import os
import requests
from datetime import datetime, timezone

from db.schema import get_connection

GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# Only fetch markets with at least this much trading volume (USD).
# Low-volume markets have unreliable prices — easy to manipulate with small trades.
MIN_VOLUME_USD = 1_000


def fetch_markets(limit: int = 100) -> list[dict]:
    """
    Pull active, open markets from Polymarket.
    Returns a list of cleaned market dicts — one per market.

    Args:
        limit: max number of markets to fetch per request (Polymarket max is 100).
    """
    params = {
        "limit": limit,
        "active": "true",   # only live markets (guardrail: skip resolved ones)
        "closed": "false",  # belt-and-suspenders: also exclude closed markets
        "order": "volume",  # most-traded markets first (most relevant for our use case)
        "ascending": "false",
    }

    try:
        response = requests.get(
            f"{GAMMA_API_BASE}/markets",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        print("ERROR: Polymarket API timed out after 15s")
        return []
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: Polymarket API returned {e.response.status_code}")
        return []
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Could not reach Polymarket API — {e}")
        return []

    raw_markets = response.json()

    cleaned = []
    for m in raw_markets:
        parsed = _parse_market(m)
        if parsed is not None:
            cleaned.append(parsed)

    print(f"Fetched {len(cleaned)} active markets from Polymarket")
    return cleaned


def _parse_market(raw: dict) -> dict | None:
    """
    Extract only the fields we care about from a raw Polymarket API response.
    Returns None if the market should be skipped (low volume, missing price, etc.).
    """
    # outcomePrices is a JSON-encoded list like '["0.35", "0.65"]'
    # Index 0 = YES price, index 1 = NO price
    try:
        outcome_prices = json.loads(raw.get("outcomePrices", "[]"))
        yes_price = float(outcome_prices[0])
        no_price = float(outcome_prices[1])
    except (json.JSONDecodeError, IndexError, ValueError, TypeError):
        # Skip markets with malformed or missing price data
        return None

    volume = float(raw.get("volume", 0) or 0)
    if volume < MIN_VOLUME_USD:
        # Skip thinly traded markets — prices are unreliable (manipulation guardrail)
        return None

    return {
        "id": raw.get("id", ""),
        "question": raw.get("question", ""),
        "category": _extract_category(raw),
        "end_date": raw.get("endDateIso") or raw.get("end_date_iso", ""),
        "active": True,
        "yes_price": yes_price,
        "no_price": no_price,
        "volume": volume,
    }


def _extract_category(raw: dict) -> str:
    """Pull the most descriptive category tag from a market's tag list."""
    tags = raw.get("tags", [])
    if isinstance(tags, list) and tags:
        # Tags are dicts with a "label" field, or plain strings
        first = tags[0]
        if isinstance(first, dict):
            return first.get("label", "general")
        return str(first)
    return raw.get("category", "general")


def save_markets(markets: list[dict]) -> None:
    """
    Upsert market metadata and insert a fresh price snapshot for each market.

    Why upsert (INSERT OR IGNORE) for markets but always INSERT for prices?
    - The markets table is a registry — we only need one row per market ever.
    - The market_prices table is a time series — every run adds a new data point,
      which is how we build the 7-day price history chart.
    """
    if not markets:
        print("No markets to save.")
        return

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cursor = conn.cursor()

    markets_saved = 0
    prices_saved = 0

    for m in markets:
        # INSERT OR IGNORE: if we've seen this market before, skip the insert
        # but still fall through to insert a fresh price snapshot below.
        cursor.execute("""
            INSERT OR IGNORE INTO markets (id, question, category, end_date, active, first_seen)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (m["id"], m["question"], m["category"], m["end_date"], 1, now))

        if cursor.rowcount == 1:
            markets_saved += 1

        # Always insert a price snapshot — this builds our time series.
        cursor.execute("""
            INSERT INTO market_prices (market_id, yes_price, no_price, volume, fetched_at)
            VALUES (?, ?, ?, ?, ?)
        """, (m["id"], m["yes_price"], m["no_price"], m["volume"], now))

        prices_saved += 1

    conn.commit()
    conn.close()
    print(f"Saved {markets_saved} new markets, {prices_saved} price snapshots")


def run() -> list[dict]:
    """Fetch markets and persist to DB. Returns the list for downstream use."""
    markets = fetch_markets()
    save_markets(markets)
    return markets
