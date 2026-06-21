"""
Tests for agent/scorer.py

_is_correct() needs no fixtures — it's a pure function.
Everything else uses temp_db + mocked requests so no real APIs are hit.
"""

import json
import pytest
from datetime import datetime, timezone

import db.schema as schema_module
from agent import scorer


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(schema_module, "DB_PATH", db_file)
    schema_module.init_db()
    return db_file


def _insert_market(market_id="mkt-001", question="Will the Fed cut rates?", active=1):
    """Insert a market row so FK constraints pass."""
    conn = schema_module.get_connection()
    conn.execute(
        "INSERT INTO markets (id, question, active, first_seen) VALUES (?, ?, ?, ?)",
        (market_id, question, active, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def _insert_judgment(market_id="mkt-001", direction="up", market_price=0.42) -> int:
    """Insert an agent_judgment and return its row ID."""
    conn = schema_module.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO agent_judgments
            (market_id, news_ids, direction, confidence_low, confidence_high,
             rationale, cited_urls, market_price_at_call, headline_count, was_sufficient, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        market_id, "[]", direction, 0.6, 0.75,
        "Evidence suggests YES.", "[]",
        market_price, 5, 1, datetime.now(timezone.utc).isoformat(),
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


# ── _is_correct (pure function — no fixtures needed) ─────────────────────────

def test_is_correct_up_resolves_yes():
    assert scorer._is_correct("up", 1.0) == 1


def test_is_correct_up_resolves_no():
    assert scorer._is_correct("up", 0.0) == 0


def test_is_correct_down_resolves_no():
    assert scorer._is_correct("down", 0.0) == 1


def test_is_correct_down_resolves_yes():
    assert scorer._is_correct("down", 1.0) == 0


def test_is_correct_neutral_returns_none():
    """Neutral abstentions are never graded — no verdict assigned."""
    assert scorer._is_correct("neutral", 1.0) is None
    assert scorer._is_correct("neutral", 0.0) is None


def test_is_correct_ambiguous_price_returns_none():
    """A final price of 0.5 is neither YES nor NO — no grade possible."""
    assert scorer._is_correct("up", 0.5) is None
    assert scorer._is_correct("down", 0.5) is None


def test_is_correct_uses_threshold():
    """0.9 threshold: final_price=0.91 is close enough to count as YES resolved."""
    assert scorer._is_correct("up", 0.91) == 1
    assert scorer._is_correct("up", 0.89) is None   # ambiguous (between thresholds)


# ── score_judgments ───────────────────────────────────────────────────────────

def test_score_judgments_inserts_scores(temp_db):
    _insert_market()
    _insert_judgment(direction="up")
    _insert_judgment(direction="down")

    count = scorer.score_judgments("mkt-001", final_price=1.0)

    assert count == 2

    conn = schema_module.get_connection()
    scores = conn.execute("SELECT * FROM scores").fetchall()
    conn.close()
    assert len(scores) == 2


def test_score_judgments_correct_verdict_for_up_yes(temp_db):
    _insert_market()
    _insert_judgment(direction="up")

    scorer.score_judgments("mkt-001", final_price=1.0)

    conn = schema_module.get_connection()
    row = conn.execute("SELECT was_correct FROM scores").fetchone()
    conn.close()
    assert row["was_correct"] == 1


def test_score_judgments_wrong_verdict_for_down_yes(temp_db):
    _insert_market()
    _insert_judgment(direction="down")

    scorer.score_judgments("mkt-001", final_price=1.0)

    conn = schema_module.get_connection()
    row = conn.execute("SELECT was_correct FROM scores").fetchone()
    conn.close()
    assert row["was_correct"] == 0


def test_score_judgments_is_idempotent(temp_db):
    """Calling score_judgments twice on the same market must not double-count."""
    _insert_market()
    _insert_judgment(direction="up")

    scorer.score_judgments("mkt-001", final_price=1.0)
    second_count = scorer.score_judgments("mkt-001", final_price=1.0)

    assert second_count == 0  # already scored — nothing new

    conn = schema_module.get_connection()
    total = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    conn.close()
    assert total == 1  # still only one score row


def test_score_judgments_neutral_gets_null_verdict(temp_db):
    """Neutral judgments get a scores row (so they're not re-attempted) but was_correct=NULL."""
    _insert_market()
    _insert_judgment(direction="neutral")

    scorer.score_judgments("mkt-001", final_price=1.0)

    conn = schema_module.get_connection()
    row = conn.execute("SELECT was_correct FROM scores").fetchone()
    conn.close()
    assert row["was_correct"] is None


def test_score_judgments_no_judgments_returns_zero(temp_db):
    _insert_market()
    count = scorer.score_judgments("mkt-001", final_price=1.0)
    assert count == 0


# ── _mark_market_resolved ─────────────────────────────────────────────────────

def test_mark_market_resolved_updates_active_flag(temp_db):
    _insert_market(active=1)
    scorer._mark_market_resolved("mkt-001", final_price=1.0)

    conn = schema_module.get_connection()
    row = conn.execute("SELECT active, resolution_price FROM markets WHERE id='mkt-001'").fetchone()
    conn.close()
    assert row["active"] == 0
    assert row["resolution_price"] == 1.0


# ── get_track_record ──────────────────────────────────────────────────────────

def test_get_track_record_empty(temp_db):
    record = scorer.get_track_record()
    assert record["total_graded"] == 0
    assert record["hit_rate"] is None
    assert record["sample_size_warning"] is True


def test_get_track_record_with_data(temp_db):
    _insert_market()
    # Two judgments: one correct (up, resolved YES), one wrong (down, resolved YES)
    id1 = _insert_judgment(direction="up")
    id2 = _insert_judgment(direction="down")

    conn = schema_module.get_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO scores (judgment_id, was_correct, market_final_price, scored_at) VALUES (?,?,?,?)",
        (id1, 1, 1.0, now),
    )
    conn.execute(
        "INSERT INTO scores (judgment_id, was_correct, market_final_price, scored_at) VALUES (?,?,?,?)",
        (id2, 0, 1.0, now),
    )
    conn.commit()
    conn.close()

    record = scorer.get_track_record()

    assert record["total_graded"] == 2
    assert record["correct"] == 1
    assert record["hit_rate"] == 0.5
    assert record["sample_size_warning"] is True   # 2 < 30


def test_get_track_record_excludes_neutral(temp_db):
    """Neutral judgments (was_correct=NULL) must not appear in total_graded or hit rate."""
    _insert_market()
    neutral_id = _insert_judgment(direction="neutral")

    conn = schema_module.get_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO scores (judgment_id, was_correct, market_final_price, scored_at) VALUES (?,?,?,?)",
        (neutral_id, None, 1.0, now),
    )
    conn.commit()
    conn.close()

    record = scorer.get_track_record()

    assert record["total_scored"] == 1   # the row exists
    assert record["total_graded"] == 0   # but not graded (NULL verdict)
    assert record["hit_rate"] is None


# ── fetch_resolved_markets ────────────────────────────────────────────────────

def _make_polymarket_response(market_id="mkt-001", yes_price=1.0):
    return [
        {
            "id": market_id,
            "question": "Will the Fed cut rates?",
            "active": False,
            "closed": True,
            "outcomePrices": [str(yes_price), str(1.0 - yes_price)],
        }
    ]


def test_fetch_resolved_markets_filters_by_tracked_ids(mocker):
    mocker.patch(
        "agent.scorer.requests.get",
        return_value=mocker.MagicMock(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: _make_polymarket_response("mkt-001", yes_price=1.0),
        ),
    )

    # mkt-001 is tracked, mkt-999 is not
    result = scorer.fetch_resolved_markets(["mkt-001"])
    assert len(result) == 1
    assert result[0]["id"] == "mkt-001"
    assert result[0]["final_yes_price"] == 1.0


def test_fetch_resolved_markets_excludes_untracked(mocker):
    mocker.patch(
        "agent.scorer.requests.get",
        return_value=mocker.MagicMock(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: _make_polymarket_response("mkt-999", yes_price=1.0),
        ),
    )

    result = scorer.fetch_resolved_markets(["mkt-001"])   # tracking mkt-001, not mkt-999
    assert result == []


def test_fetch_resolved_markets_handles_network_error(mocker):
    import requests as req
    mocker.patch("agent.scorer.requests.get", side_effect=req.exceptions.Timeout)
    result = scorer.fetch_resolved_markets(["mkt-001"])
    assert result == []


def test_fetch_resolved_markets_empty_tracked_ids(mocker):
    mock_get = mocker.patch("agent.scorer.requests.get")
    result = scorer.fetch_resolved_markets([])
    mock_get.assert_not_called()
    assert result == []
