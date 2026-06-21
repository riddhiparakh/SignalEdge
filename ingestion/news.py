"""
Fetches news headlines from newsdata.io relevant to active Polymarket markets.

Strategy: for each market question, we extract a short keyword query and search
newsdata.io. Headlines are stored with their URL so the agent can cite sources
with links (RAG guardrail). Every headline records how old it is so the UI can
display "Reuters · 2h ago".

Free tier limits: 200 credits/day (1 credit = 1 article). We stay well within
this by fetching a max of 10 articles per market and capping at 15 markets/run.
"""

import os
import re
import time
import unicodedata
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

from db.schema import get_connection

load_dotenv()

NEWSDATA_API_BASE = "https://newsdata.io/api/1"
MAX_ARTICLES_PER_QUERY = 10   # per market — keeps us within free tier
MAX_MARKETS_PER_RUN = 8       # newsdata.io free tier has ~10 req/session limit; 8 is safe

# Sensitive topic patterns — agent guardrail: refuse to fetch news for these.
# These are checked against the market question before any API call is made.
SENSITIVE_PATTERNS = [
    r"\bterror(ism|ist)?\b",
    r"\bassassinat",
    r"\bsuicide\b",
    r"\bshoot(ing|er)?\b",
    r"\bmass (shooting|casualt)",
    r"\bprivate (individual|person|citizen)\b",
    r"\bmedical diagnosis\b",
    r"\bhealth (condition|status) of\b",
]

_SENSITIVE_RE = re.compile("|".join(SENSITIVE_PATTERNS), re.IGNORECASE)


def is_sensitive(question: str) -> bool:
    """
    Returns True if a market question touches a topic we refuse to research.
    This is checked before any API call so we never even fetch the headlines.
    """
    return bool(_SENSITIVE_RE.search(question))


def _extract_query(question: str) -> str:
    """
    Distill a market question into a short, API-safe keyword search string.

    E.g. "Will the Fed cut rates before September 2025?" → "Fed rate cut 2025"

    Steps:
    1. Strip leading question words
    2. Remove characters that cause 422 errors on newsdata.io ($, <, >, °, non-ASCII)
    3. Collapse whitespace and truncate to 100 chars
    """
    # Remove question marks and leading question words
    q = re.sub(r"^\s*(will|does|is|are|can|could|would|should|who|what|when|where|how)\s+", "", question, flags=re.IGNORECASE)
    q = q.replace("?", "")

    # Normalise unicode (ü → u, é → e, etc.) so queries stay ASCII-safe
    q = unicodedata.normalize("NFKD", q)
    q = q.encode("ascii", errors="ignore").decode("ascii")

    # Strip characters that newsdata.io rejects: $, <, >, #, @, °, %
    q = re.sub(r"[$<>#@°%]", "", q)

    # Collapse multiple spaces
    q = re.sub(r"\s+", " ", q).strip()

    # Truncate to 100 chars — newsdata.io query length limit
    return q[:100]


def fetch_news_for_market(market_id: str, question: str) -> list[dict]:
    """
    Search newsdata.io for headlines relevant to a single market question.
    Returns a list of cleaned article dicts (empty list if sensitive or error).
    """
    if is_sensitive(question):
        print(f"  SKIPPED (sensitive topic): {question[:60]}...")
        return []

    query = _extract_query(question)
    api_key = os.getenv("NEWSDATA_API_KEY")

    if not api_key:
        raise EnvironmentError(
            "NEWSDATA_API_KEY not set. Copy .env.example to .env and add your key."
        )

    params = {
        "apikey": api_key,
        "q": query,
        "language": "en",
        "size": MAX_ARTICLES_PER_QUERY,
        # Note: "timeframe" and "prioritydomain" are paid-tier parameters.
        # Without timeframe, newsdata.io returns the most recent articles by default.
        # We post-filter by published_at in _is_recent() to enforce our freshness window.
    }

    # Retry up to 2 times on transient network errors (DNS flakiness on free tier)
    last_error = None
    for attempt in range(2):
        try:
            response = requests.get(
                f"{NEWSDATA_API_BASE}/news",
                params=params,
                timeout=15,
            )
            response.raise_for_status()
            last_error = None
            break
        except requests.exceptions.Timeout:
            last_error = f"timed out"
        except requests.exceptions.HTTPError as e:
            print(f"  ERROR: newsdata.io returned {e.response.status_code} for query: {query}")
            return []
        except requests.exceptions.RequestException as e:
            last_error = str(e)[:120]
        if attempt == 0:
            time.sleep(5)  # brief backoff before retry

    if last_error:
        print(f"  ERROR: newsdata.io failed for query '{query}': {last_error}")
        return []

    data = response.json()

    if data.get("status") != "success":
        print(f"  ERROR: newsdata.io API error — {data.get('message', 'unknown')}")
        return []

    articles = data.get("results", [])
    cleaned = [_parse_article(a, query, market_id) for a in articles]
    # Filter out articles with missing fields or older than 48 hours
    return [a for a in cleaned if a is not None and _is_recent(a["published_at"], max_hours=48)]


def _parse_article(raw: dict, query_used: str, market_id: str) -> dict | None:
    """Clean a single newsdata.io article response into our storage schema."""
    url = raw.get("link", "")
    title = raw.get("title", "")

    if not url or not title:
        return None  # skip articles without a title or link (can't cite them)

    # newsdata.io returns article_id as a unique identifier per article
    article_id = raw.get("article_id", "") or url  # fall back to URL if no ID

    # published_at comes as "2025-06-19 14:30:00" — normalise to ISO 8601
    published_raw = raw.get("pubDate", "")
    try:
        published_at = datetime.strptime(published_raw, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).isoformat()
    except (ValueError, TypeError):
        published_at = published_raw  # keep as-is if format is unexpected

    return {
        "id": article_id,
        "title": title,
        "description": raw.get("description", ""),
        "content": (raw.get("content") or "")[:500],  # cap at 500 chars
        "url": url,
        "source": raw.get("source_id", "") or raw.get("source_name", ""),
        "published_at": published_at,
        "category": ", ".join(raw.get("category", []) or []),
        "query_used": query_used,
        "market_id": market_id,  # link back to the market that triggered this search
    }


def hours_ago(published_at: str) -> str:
    """
    Return a human-readable age string: "2h ago", "1d ago", etc.
    Used in the UI and agent output to show how fresh each headline is.
    """
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta_seconds = (now - pub).total_seconds()

        if delta_seconds < 3600:
            mins = int(delta_seconds / 60)
            return f"{mins}m ago"
        elif delta_seconds < 86400:
            hrs = int(delta_seconds / 3600)
            return f"{hrs}h ago"
        else:
            days = int(delta_seconds / 86400)
            return f"{days}d ago"
    except (ValueError, TypeError):
        return "unknown age"


def _is_recent(published_at: str, max_hours: int = 48) -> bool:
    """Return True if the article was published within the last max_hours hours.
    Used to post-filter API results since the free plan doesn't support timeframe."""
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        delta = (datetime.now(timezone.utc) - pub).total_seconds()
        return delta <= max_hours * 3600
    except (ValueError, TypeError):
        return True  # if we can't parse the date, include it rather than silently drop


def save_news(articles: list[dict]) -> int:
    """
    Persist articles to the news_items table.
    Uses INSERT OR IGNORE so re-running the pipeline doesn't create duplicates.
    Returns the count of newly inserted articles.
    """
    if not articles:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    inserted = 0

    for a in articles:
        cursor.execute("""
            INSERT OR IGNORE INTO news_items
                (id, title, description, content, url, source, published_at, category, query_used, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            a["id"], a["title"], a["description"], a["content"],
            a["url"], a["source"], a["published_at"], a["category"],
            a["query_used"], now,
        ))
        if cursor.rowcount == 1:
            inserted += 1

    conn.commit()
    conn.close()
    return inserted


def run(markets: list[dict]) -> list[dict]:
    """
    For each market in the list, fetch relevant headlines and save to DB.
    Returns a flat list of all articles fetched across all markets.

    Args:
        markets: list of market dicts returned by ingestion/markets.py
    """
    # Cap how many markets we query to stay within free tier limits
    markets_to_query = markets[:MAX_MARKETS_PER_RUN]
    all_articles = []

    for i, market in enumerate(markets_to_query):
        question = market.get("question", "")
        market_id = market.get("id", "")
        print(f"  Fetching news for: {question[:60]}...")

        articles = fetch_news_for_market(market_id, question)
        total_saved = save_news(articles)
        all_articles.extend(articles)

        print(f"    → {len(articles)} fetched, {total_saved} new")

        # newsdata.io free tier: 10 requests/minute. Wait 7s between calls
        # to stay safely under the rate limit. Skip the delay after the last call.
        if i < len(markets_to_query) - 1:
            time.sleep(7)

    print(f"News ingestion complete: {len(all_articles)} total articles across {len(markets_to_query)} markets")
    return all_articles
