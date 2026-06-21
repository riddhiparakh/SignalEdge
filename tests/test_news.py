"""
Tests for ingestion/news.py

Verifies:
- is_sensitive() correctly flags/passes market questions
- _extract_query() produces clean keyword strings
- fetch_news_for_market() handles API errors and sensitive topic guardrail
- hours_ago() returns correct human-readable age strings
- save_news() persists articles and is idempotent
"""

import pytest
from datetime import datetime, timezone, timedelta

import db.schema as schema_module
from ingestion import news as news_module


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(schema_module, "DB_PATH", db_file)
    schema_module.init_db()
    return db_file


def _make_article(article_id="art-001", title="Fed signals rate cuts", url="https://reuters.com/1"):
    """Helper: build a cleaned article dict (as returned by _parse_article)."""
    return {
        "id": article_id,
        "title": title,
        "description": "The Federal Reserve signalled...",
        "content": "Full article content here...",
        "url": url,
        "source": "Reuters",
        "published_at": "2025-06-19T10:00:00+00:00",
        "category": "business",
        "query_used": "Fed rate cut",
        "market_id": "mkt-001",
    }


def _make_newsdata_response(articles: list[dict]) -> dict:
    """Wrap articles in a newsdata.io-shaped API response."""
    return {
        "status": "success",
        "totalResults": len(articles),
        "results": articles,
    }


def _make_raw_newsdata_article(article_id="art-001", title="Fed signals cuts"):
    """Build a raw newsdata.io article dict (before parsing).
    Uses a dynamic timestamp so _is_recent() doesn't filter it out."""
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "article_id": article_id,
        "title": title,
        "description": "Detailed description here",
        "content": "Article content...",
        "link": f"https://reuters.com/{article_id}",
        "source_id": "reuters",
        "pubDate": recent,
        "category": ["business"],
    }


# ── Tests for is_sensitive ────────────────────────────────────────────────

@pytest.mark.parametrize("question,expected", [
    ("Will the Fed cut rates in 2025?", False),
    ("Will Taylor Swift release an album?", False),
    ("Will USA and Iran sign a nuclear deal?", False),
    ("Will there be a terrorist attack in Europe?", True),
    ("Will [name] be assassinated?", True),
    ("Is [person]'s medical diagnosis cancer?", True),
    ("Will there be a mass shooting in the US?", True),
])
def test_is_sensitive(question, expected):
    assert news_module.is_sensitive(question) == expected


# ── Tests for _extract_query ─────────────────────────────────────────────

def test_extract_query_strips_question_word():
    q = news_module._extract_query("Will the Fed cut rates before September 2025?")
    assert not q.lower().startswith("will")
    assert "Fed" in q


def test_extract_query_max_length():
    long_question = "Will " + "x" * 200 + "?"
    q = news_module._extract_query(long_question)
    assert len(q) <= 100


def test_extract_query_removes_question_mark():
    q = news_module._extract_query("Will Bitcoin hit $100k?")
    assert "?" not in q


# ── Tests for hours_ago ───────────────────────────────────────────────────

def test_hours_ago_minutes():
    recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    assert "m ago" in news_module.hours_ago(recent)


def test_hours_ago_hours():
    two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    result = news_module.hours_ago(two_hours_ago)
    assert result == "2h ago"


def test_hours_ago_days():
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    result = news_module.hours_ago(two_days_ago)
    assert result == "2d ago"


def test_hours_ago_invalid_string():
    result = news_module.hours_ago("not-a-date")
    assert result == "unknown age"


# ── Tests for fetch_news_for_market (API mocked) ─────────────────────────

def test_fetch_news_skips_sensitive_topics(mocker):
    mock_get = mocker.patch("ingestion.news.requests.get")
    result = news_module.fetch_news_for_market("mkt-1", "Will there be a terrorist attack?")
    mock_get.assert_not_called()   # guardrail fires BEFORE the API call
    assert result == []


def test_fetch_news_returns_articles(mocker, monkeypatch):
    monkeypatch.setenv("NEWSDATA_API_KEY", "fake-key")
    raw_articles = [_make_raw_newsdata_article("art-1"), _make_raw_newsdata_article("art-2", "Title 2")]

    mock_get = mocker.patch("ingestion.news.requests.get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.raise_for_status = lambda: None
    mock_get.return_value.json.return_value = _make_newsdata_response(raw_articles)

    results = news_module.fetch_news_for_market("mkt-1", "Will the Fed cut rates?")
    assert len(results) == 2
    assert results[0]["source"] == "reuters"


def test_fetch_news_handles_timeout(mocker, monkeypatch):
    import requests as req
    monkeypatch.setenv("NEWSDATA_API_KEY", "fake-key")
    mocker.patch("ingestion.news.requests.get", side_effect=req.exceptions.Timeout)
    results = news_module.fetch_news_for_market("mkt-1", "Will the Fed cut rates?")
    assert results == []


def test_fetch_news_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("NEWSDATA_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="NEWSDATA_API_KEY"):
        news_module.fetch_news_for_market("mkt-1", "Will the Fed cut rates?")


# ── Tests for save_news ───────────────────────────────────────────────────

def test_save_news_inserts_articles(temp_db):
    articles = [_make_article("art-1"), _make_article("art-2", url="https://bbc.com/2")]
    inserted = news_module.save_news(articles)

    assert inserted == 2
    conn = schema_module.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
    conn.close()
    assert count == 2


def test_save_news_is_idempotent(temp_db):
    """Saving the same article twice should only insert it once."""
    article = _make_article()
    news_module.save_news([article])
    inserted_second = news_module.save_news([article])

    assert inserted_second == 0  # no new rows on second save

    conn = schema_module.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
    conn.close()
    assert count == 1


def test_save_news_empty_list(temp_db):
    inserted = news_module.save_news([])
    assert inserted == 0
