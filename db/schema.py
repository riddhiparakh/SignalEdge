"""
Database schema for SignalEdge.
All tables are created here and nowhere else — single source of truth for data shape.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "signalEdge.db")


def get_connection() -> sqlite3.Connection:
    """Return a connection with foreign keys enforced and row_factory set so
    rows behave like dicts (access columns by name, not index)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create all tables if they don't already exist. Safe to call on every startup."""
    conn = get_connection()
    cursor = conn.cursor()

    # One row per unique Polymarket market we've ever seen.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            id          TEXT PRIMARY KEY,   -- Polymarket's own market ID
            question    TEXT NOT NULL,       -- e.g. "Will the Fed cut rates in Sept 2025?"
            category    TEXT,               -- e.g. "politics", "crypto"
            end_date    TEXT,               -- ISO 8601 resolution date
            active      INTEGER DEFAULT 1,  -- 1 = still open, 0 = resolved/closed
            first_seen  TEXT NOT NULL       -- ISO timestamp when we first fetched it
        )
    """)

    # One row per hourly snapshot of a market's price.
    # This is how we build 7-day price history: run the pipeline every hour,
    # each run inserts a row here. After 7 days we have ~168 data points per market.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_prices (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id   TEXT NOT NULL REFERENCES markets(id),
            yes_price   REAL NOT NULL,  -- probability of YES (0.0–1.0)
            no_price    REAL NOT NULL,  -- probability of NO  (0.0–1.0)
            volume      REAL,           -- cumulative trading volume in USD
            fetched_at  TEXT NOT NULL   -- ISO timestamp of this snapshot
        )
    """)

    # One row per news headline we've fetched from newsdata.io.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news_items (
            id           TEXT PRIMARY KEY,  -- article_id from newsdata.io
            title        TEXT NOT NULL,
            description  TEXT,
            content      TEXT,              -- first ~200 chars (newsdata.io free tier limit)
            url          TEXT NOT NULL,     -- link to original article (used for RAG citations)
            source       TEXT,              -- publisher name, e.g. "Reuters"
            published_at TEXT NOT NULL,     -- when the article was published (ISO 8601)
            category     TEXT,             -- newsdata.io category tag
            query_used   TEXT,             -- the keyword we searched for to find this article
            fetched_at   TEXT NOT NULL     -- when we stored it (for "X hours ago" display)
        )
    """)

    # One row per agent judgment (populated in Phase 2).
    # We create the table now so the schema is complete and Phase 2 can just INSERT.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_judgments (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id            TEXT NOT NULL REFERENCES markets(id),
            news_ids             TEXT NOT NULL,  -- JSON array of news_items.id values used
            direction            TEXT NOT NULL,  -- "up", "down", or "neutral"
            confidence_low       REAL,           -- lower bound of agent's probability range
            confidence_high      REAL,           -- upper bound
            rationale            TEXT NOT NULL,  -- agent's written reasoning
            cited_urls           TEXT,           -- JSON array of URLs the agent cited
            market_price_at_call REAL NOT NULL,  -- YES price at the moment of the API call
            headline_count       INTEGER,        -- how many headlines were in the bundle
            was_sufficient       INTEGER DEFAULT 1, -- 0 if agent said "insufficient evidence"
            created_at           TEXT NOT NULL
        )
    """)

    # One row per scored judgment (populated in Phase 3).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scores (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            judgment_id        INTEGER NOT NULL REFERENCES agent_judgments(id),
            was_correct        INTEGER,    -- 1 = agent was right, 0 = wrong, NULL = not yet resolved
            market_final_price REAL,       -- price at resolution
            scored_at          TEXT
        )
    """)

    # Phase 3 migration: add resolution columns to markets.
    # ALTER TABLE ADD COLUMN is idempotent via try/except — SQLite doesn't support IF NOT EXISTS.
    for migration_sql in [
        "ALTER TABLE markets ADD COLUMN resolution_price REAL",
        "ALTER TABLE markets ADD COLUMN resolved_at TEXT",
    ]:
        try:
            cursor.execute(migration_sql)
        except sqlite3.OperationalError:
            pass  # column already exists — safe to ignore

    conn.commit()
    conn.close()
    print(f"Database initialised at: {DB_PATH}")


if __name__ == "__main__":
    init_db()
