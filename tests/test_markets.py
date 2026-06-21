"""
Tests for ingestion/markets.py

Verifies:
- _parse_market() correctly extracts fields and filters low-volume markets
- fetch_markets() handles API errors gracefully (returns empty list, no crash)
- save_markets() inserts into both markets and market_prices tables
- save_markets() is idempotent for the markets table (INSERT OR IGNORE)
"""

import json
import pytest
import db.schema as schema_module
from ingestion import markets as markets_module


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Each test gets a fresh database."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(schema_module, "DB_PATH", db_file)
    schema_module.init_db()
    return db_file


def _make_raw_market(
    market_id="mkt-001",
    question="Will the Fed cut rates in Sept 2025?",
    yes_price="0.35",
    no_price="0.65",
    volume=50_000,
    active=True,
    closed=False,
    end_date="2025-09-30T00:00:00Z",
):
    """Helper: build a fake Polymarket API response dict."""
    return {
        "id": market_id,
        "question": question,
        "outcomePrices": json.dumps([yes_price, no_price]),
        "volume": volume,
        "active": active,
        "closed": closed,
        "endDateIso": end_date,
        "tags": [{"label": "economics"}],
    }


# ── Unit tests for _parse_market ────────────────────────────────────────────

def test_parse_market_returns_correct_fields():
    raw = _make_raw_market()
    result = markets_module._parse_market(raw)

    assert result is not None
    assert result["id"] == "mkt-001"
    assert result["question"] == "Will the Fed cut rates in Sept 2025?"
    assert result["yes_price"] == pytest.approx(0.35)
    assert result["no_price"] == pytest.approx(0.65)
    assert result["volume"] == 50_000
    assert result["category"] == "economics"


def test_parse_market_skips_low_volume():
    raw = _make_raw_market(volume=500)  # below MIN_VOLUME_USD = 1000
    result = markets_module._parse_market(raw)
    assert result is None


def test_parse_market_skips_missing_prices():
    raw = _make_raw_market()
    raw["outcomePrices"] = "[]"  # empty price list
    result = markets_module._parse_market(raw)
    assert result is None


def test_parse_market_skips_malformed_prices():
    raw = _make_raw_market()
    raw["outcomePrices"] = "not-json"
    result = markets_module._parse_market(raw)
    assert result is None


# ── Unit tests for fetch_markets (API mocked) ─────────────────────────────

def test_fetch_markets_returns_cleaned_list(mocker):
    fake_response = [_make_raw_market(), _make_raw_market(market_id="mkt-002", volume=20_000)]

    mock_get = mocker.patch("ingestion.markets.requests.get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.raise_for_status = lambda: None
    mock_get.return_value.json.return_value = fake_response

    results = markets_module.fetch_markets()
    assert len(results) == 2
    assert results[0]["id"] == "mkt-001"


def test_fetch_markets_handles_timeout(mocker):
    import requests as req
    mocker.patch("ingestion.markets.requests.get", side_effect=req.exceptions.Timeout)
    results = markets_module.fetch_markets()
    assert results == []


def test_fetch_markets_handles_http_error(mocker):
    import requests as req
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 503
    mocker.patch(
        "ingestion.markets.requests.get",
        side_effect=req.exceptions.HTTPError(response=mock_resp),
    )
    results = markets_module.fetch_markets()
    assert results == []


# ── Unit tests for save_markets ──────────────────────────────────────────

def test_save_markets_inserts_rows(temp_db):
    markets = [
        {**markets_module._parse_market(_make_raw_market())},
        {**markets_module._parse_market(_make_raw_market(market_id="mkt-002", volume=20_000))},
    ]
    markets_module.save_markets(markets)

    conn = schema_module.get_connection()
    market_count = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    price_count = conn.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0]
    conn.close()

    assert market_count == 2
    assert price_count == 2


def test_save_markets_is_idempotent_for_markets_table(temp_db):
    """Saving the same market twice should NOT duplicate rows in the markets table,
    but SHOULD add a second row to market_prices (that's the time series)."""
    market = markets_module._parse_market(_make_raw_market())
    markets_module.save_markets([market])
    markets_module.save_markets([market])  # second run

    conn = schema_module.get_connection()
    market_count = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    price_count = conn.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0]
    conn.close()

    assert market_count == 1   # only one market row (INSERT OR IGNORE)
    assert price_count == 2    # two price snapshots (time series grows each run)


def test_save_markets_empty_list_does_not_crash(temp_db):
    markets_module.save_markets([])  # should not raise
