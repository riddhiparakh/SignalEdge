"""
Tests for db/schema.py

Verifies:
- init_db() creates all expected tables
- Tables have the right columns
- Running init_db() twice doesn't crash (idempotent)
- get_connection() returns rows accessible by column name
"""

import os
import sqlite3
import tempfile
import pytest

# Patch DB_PATH before importing schema so tests use a temp file, not the real DB
import db.schema as schema_module


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a throwaway temp file for each test."""
    db_file = str(tmp_path / "test_signalEdge.db")
    monkeypatch.setattr(schema_module, "DB_PATH", db_file)
    return db_file


def test_init_db_creates_all_tables(temp_db):
    schema_module.init_db()

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    expected = {"markets", "market_prices", "news_items", "agent_judgments", "scores"}
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


def test_init_db_is_idempotent(temp_db):
    """Calling init_db() twice should not raise any errors."""
    schema_module.init_db()
    schema_module.init_db()  # second call must not crash


def test_markets_table_columns(temp_db):
    schema_module.init_db()
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(markets)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()

    # Phase 3 adds resolution_price and resolved_at via ALTER TABLE migration
    expected_columns = {"id", "question", "category", "end_date", "active", "first_seen",
                        "resolution_price", "resolved_at"}
    assert expected_columns == columns


def test_market_prices_table_columns(temp_db):
    schema_module.init_db()
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(market_prices)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()

    expected_columns = {"id", "market_id", "yes_price", "no_price", "volume", "fetched_at"}
    assert expected_columns == columns


def test_news_items_table_columns(temp_db):
    schema_module.init_db()
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(news_items)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()

    expected_columns = {
        "id", "title", "description", "content", "url", "source",
        "published_at", "category", "query_used", "fetched_at"
    }
    assert expected_columns == columns


def test_get_connection_row_factory(temp_db):
    """Rows should be accessible by column name, not just index."""
    schema_module.init_db()
    conn = schema_module.get_connection()
    conn.execute(
        "INSERT INTO markets (id, question, category, end_date, active, first_seen) "
        "VALUES ('test-1', 'Will it rain?', 'weather', '2025-12-31', 1, '2025-01-01T00:00:00+00:00')"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM markets WHERE id = 'test-1'").fetchone()
    conn.close()

    assert row["question"] == "Will it rain?"
    assert row["active"] == 1
