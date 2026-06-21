"""
Tests for agent/rag.py and agent/brain.py

RAG tests use chromadb.EphemeralClient() — in-memory, no disk I/O, no model download.
Brain tests mock anthropic.Anthropic so no real API calls are made (zero cost).

Interview note: this demonstrates two key testing patterns:
  1. Dependency injection — RAG functions accept a `client` param → testable without filesystem
  2. Mock objects — mocker.MagicMock() replaces the Anthropic SDK response shape exactly
"""

import json
import pytest
import chromadb
from datetime import datetime, timezone, timedelta

import db.schema as schema_module
from agent import rag, brain


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Redirect all DB writes to a fresh temp file for each test."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(schema_module, "DB_PATH", db_file)
    schema_module.init_db()
    return db_file


@pytest.fixture
def chroma_client(tmp_path):
    """Fresh ChromaDB client per test using a unique temp directory.
    EphemeralClient shares state across instances in chromadb>=1.0, so we use
    PersistentClient with tmp_path to guarantee complete test isolation."""
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))


@pytest.fixture
def sample_articles():
    return [
        {
            "id": "art-1",
            "title": "Fed officials signal patience on rate cuts",
            "description": "Federal Reserve policymakers said they need more evidence before cutting.",
            "url": "https://reuters.com/1",
            "source": "Reuters",
            "published_at": "2025-06-19T10:00:00+00:00",
        },
        {
            "id": "art-2",
            "title": "CPI data shows inflation stall, reducing rate cut urgency",
            "description": "Consumer prices remained flat, giving the Fed less reason to cut.",
            "url": "https://bloomberg.com/2",
            "source": "Bloomberg",
            "published_at": "2025-06-19T08:00:00+00:00",
        },
    ]


@pytest.fixture
def sample_market():
    return {
        "id": "mkt-001",
        "question": "Will the Fed cut rates in September 2025?",
        "yes_price": 0.42,
        "no_price": 0.58,
        "volume": 50000.0,
        "end_date": "2025-09-30",
        "category": "economics",
    }


def _make_mock_response(mocker, json_content: dict):
    """Helper: build the MagicMock that mirrors anthropic.messages.create() return shape."""
    mock_response = mocker.MagicMock()
    mock_response.content = [mocker.MagicMock(text=json.dumps(json_content))]
    return mock_response


def _valid_claude_output():
    return {
        "direction": "down",
        "confidence_low": 0.25,
        "confidence_high": 0.35,
        "rationale": "Headlines [1] and [2] indicate the Fed is unlikely to cut rates soon.",
        "cited_urls": ["https://reuters.com/1", "https://bloomberg.com/2"],
        "was_sufficient": True,
    }


# ── RAG: index_articles ───────────────────────────────────────────────────────

def test_index_articles_returns_count(chroma_client, sample_articles):
    count = rag.index_articles(sample_articles, client=chroma_client)
    assert count == 2


def test_index_articles_is_idempotent(chroma_client, sample_articles):
    rag.index_articles(sample_articles, client=chroma_client)
    second_count = rag.index_articles(sample_articles, client=chroma_client)
    # Articles already exist — no new inserts
    assert second_count == 0


def test_index_articles_partial_new(chroma_client, sample_articles):
    rag.index_articles([sample_articles[0]], client=chroma_client)
    # Only the second article is new
    count = rag.index_articles(sample_articles, client=chroma_client)
    assert count == 1


def test_index_articles_empty_list(chroma_client):
    count = rag.index_articles([], client=chroma_client)
    assert count == 0


# ── RAG: find_relevant ────────────────────────────────────────────────────────

def test_find_relevant_returns_articles(chroma_client, sample_articles):
    rag.index_articles(sample_articles, client=chroma_client)
    results = rag.find_relevant("Will the Fed cut interest rates?", top_k=2, client=chroma_client)
    assert len(results) > 0
    assert "url" in results[0]
    assert "title" in results[0]


def test_find_relevant_respects_top_k(chroma_client, sample_articles):
    rag.index_articles(sample_articles, client=chroma_client)
    results = rag.find_relevant("Federal Reserve policy", top_k=1, client=chroma_client)
    assert len(results) == 1


def test_find_relevant_empty_store(chroma_client):
    """Querying an empty ChromaDB collection must return [] not raise."""
    results = rag.find_relevant("Will the Fed cut rates?", top_k=5, client=chroma_client)
    assert results == []


def test_find_relevant_top_k_larger_than_store(chroma_client, sample_articles):
    """Asking for 10 results when only 2 exist should return 2, not raise."""
    rag.index_articles(sample_articles, client=chroma_client)
    results = rag.find_relevant("monetary policy", top_k=10, client=chroma_client)
    assert len(results) == 2


# ── Brain: _parse_response ────────────────────────────────────────────────────

def test_parse_response_valid_json():
    text = json.dumps(_valid_claude_output())
    result = brain._parse_response(text)
    assert result is not None
    assert result["direction"] == "down"


def test_parse_response_strips_markdown_fences():
    """Claude sometimes wraps its JSON in ```json ... ``` — we must handle this."""
    text = "```json\n" + json.dumps(_valid_claude_output()) + "\n```"
    result = brain._parse_response(text)
    assert result is not None
    assert result["direction"] == "down"


def test_parse_response_invalid_json_returns_none():
    result = brain._parse_response("this is not json at all")
    assert result is None


def test_parse_response_invalid_direction_returns_none():
    data = _valid_claude_output()
    data["direction"] = "maybe"   # not a valid direction
    result = brain._parse_response(json.dumps(data))
    assert result is None


def test_parse_response_missing_field_returns_none():
    data = _valid_claude_output()
    del data["cited_urls"]
    result = brain._parse_response(json.dumps(data))
    assert result is None


def test_parse_response_confidence_out_of_range_returns_none():
    data = _valid_claude_output()
    data["confidence_low"] = -0.1   # invalid: below 0
    result = brain._parse_response(json.dumps(data))
    assert result is None


# ── Brain: analyze_market ─────────────────────────────────────────────────────

def test_analyze_market_returns_judgment(mocker, sample_market, sample_articles):
    mock_client = mocker.MagicMock()
    mock_client.messages.create.return_value = _make_mock_response(mocker, _valid_claude_output())

    result = brain.analyze_market(sample_market, sample_articles, anthropic_client=mock_client)

    assert result is not None
    assert result["direction"] == "down"
    assert result["market_id"] == "mkt-001"
    assert result["headline_count"] == 2


def test_analyze_market_computes_divergence(mocker, sample_market, sample_articles):
    """
    divergence = agent_midpoint - market_price
    agent says 25%–35%, midpoint = 30%. Market price = 42%.
    divergence = 0.30 - 0.42 = -0.12 (market is overpriced → signal is "down")
    """
    mock_client = mocker.MagicMock()
    mock_client.messages.create.return_value = _make_mock_response(mocker, _valid_claude_output())

    result = brain.analyze_market(sample_market, sample_articles, anthropic_client=mock_client)

    expected_divergence = (0.25 + 0.35) / 2 - 0.42
    assert abs(result["divergence"] - round(expected_divergence, 4)) < 0.001


def test_analyze_market_skips_empty_articles(mocker, sample_market):
    """No API call should happen when there are no articles."""
    mock_client = mocker.MagicMock()
    result = brain.analyze_market(sample_market, [], anthropic_client=mock_client)

    assert result is None
    mock_client.messages.create.assert_not_called()


def test_analyze_market_handles_invalid_response(mocker, sample_market, sample_articles):
    mock_client = mocker.MagicMock()
    mock_client.messages.create.return_value.content = [
        mocker.MagicMock(text="I can't help with that.")
    ]

    result = brain.analyze_market(sample_market, sample_articles, anthropic_client=mock_client)
    assert result is None


def test_analyze_market_handles_api_error(mocker, sample_market, sample_articles):
    import anthropic as anthropic_module
    mock_client = mocker.MagicMock()
    mock_client.messages.create.side_effect = anthropic_module.APIError(
        message="rate limit",
        request=mocker.MagicMock(),
        body=None,
    )

    result = brain.analyze_market(sample_market, sample_articles, anthropic_client=mock_client)
    assert result is None


def test_analyze_market_raises_without_api_key(monkeypatch, sample_market, sample_articles):
    """With no injected client AND no env var, should raise EnvironmentError."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
        brain.analyze_market(sample_market, sample_articles)


# ── Brain: save_judgment ──────────────────────────────────────────────────────

def _make_judgment(market_id="mkt-001"):
    return {
        "market_id": market_id,
        "news_ids": json.dumps(["art-1", "art-2"]),
        "direction": "down",
        "confidence_low": 0.25,
        "confidence_high": 0.35,
        "rationale": "Headlines suggest the Fed will not cut rates.",
        "cited_urls": json.dumps(["https://reuters.com/1"]),
        "market_price_at_call": 0.42,
        "headline_count": 2,
        "was_sufficient": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def test_save_judgment_inserts_row(temp_db):
    # Must insert market row first (foreign key constraint)
    conn = schema_module.get_connection()
    conn.execute(
        "INSERT INTO markets (id, question, first_seen) VALUES (?, ?, ?)",
        ("mkt-001", "Will the Fed cut rates?", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    row_id = brain.save_judgment(_make_judgment())
    assert row_id is not None and row_id > 0

    conn = schema_module.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM agent_judgments").fetchone()[0]
    conn.close()
    assert count == 1


def test_save_judgment_returns_incrementing_ids(temp_db):
    conn = schema_module.get_connection()
    conn.execute(
        "INSERT INTO markets (id, question, first_seen) VALUES (?, ?, ?)",
        ("mkt-001", "Will the Fed cut rates?", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    id1 = brain.save_judgment(_make_judgment())
    id2 = brain.save_judgment(_make_judgment())
    assert id2 > id1
