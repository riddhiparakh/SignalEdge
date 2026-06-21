"""
Phase 3: Evaluation and scoring.

When Polymarket resolves a market (final price settles near 1.0 for YES or 0.0 for NO),
we grade every agent judgment we made for that market:
  - "up" judgment + resolved YES → correct (market WAS underpriced)
  - "up" judgment + resolved NO  → wrong
  - "down" judgment + resolved NO  → correct (market WAS overpriced)
  - "down" judgment + resolved YES → wrong
  - "neutral" judgment → no grade (the agent abstained — can't grade an abstention)

Hit rate is reported honestly with a sample-size warning below 30 graded judgments,
because small-sample statistics can mislead. This framing is intentional and important.
"""

import requests
from datetime import datetime, timezone

from db.schema import get_connection

GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# Resolution thresholds: binary markets settle at exactly 0 or 1,
# but we use a 0.1 buffer to be safe against partial resolutions.
RESOLUTION_YES = 0.9   # final_price >= 0.9  → YES won
RESOLUTION_NO  = 0.1   # final_price <= 0.1  → NO won

# Minimum graded judgments before we report a hit rate — below this,
# any % is statistically meaningless.
MIN_SAMPLE_FOR_RATE = 30


def _is_correct(direction: str, final_price: float) -> int | None:
    """
    Grade a single agent judgment against the market's final resolution price.

    Returns:
      1    → agent was correct
      0    → agent was wrong
      None → no grade (neutral direction, or ambiguous final price)

    Interview note: this is a pure function — no DB, no side effects, fully testable.
    Binary markets settle at exactly 1.0 or 0.0, so the 0.9 / 0.1 thresholds
    are a defensive buffer; in practice resolved_yes and resolved_no are always clear.
    """
    if direction == "neutral":
        return None

    resolved_yes = final_price >= RESOLUTION_YES
    resolved_no  = final_price <= RESOLUTION_NO

    # Ambiguous final price (shouldn't happen in binary markets, but be safe)
    if not (resolved_yes or resolved_no):
        return None

    if direction == "up":
        return 1 if resolved_yes else 0
    if direction == "down":
        return 1 if resolved_no else 0

    return None  # unknown direction (defensive)


def _load_active_market_ids() -> list[str]:
    """Return IDs of markets we're currently tracking as active."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id FROM markets WHERE active = 1"
    ).fetchall()
    conn.close()
    return [row["id"] for row in rows]


def fetch_resolved_markets(tracked_ids: list[str]) -> list[dict]:
    """
    Query Polymarket for recently closed markets and return those matching our tracked IDs.

    Returns a list of dicts: {id, question, final_yes_price}

    We fetch Polymarket's closed markets list rather than querying each ID individually
    (one API call vs N calls). We then intersect with our tracked IDs so we only score
    markets we've actually been following.
    """
    if not tracked_ids:
        return []

    try:
        response = requests.get(
            f"{GAMMA_API_BASE}/markets",
            params={
                "active": "false",
                "closed": "true",
                "order": "updatedAt",
                "ascending": "false",
                "limit": 200,
            },
            timeout=15,
        )
        response.raise_for_status()
        closed_markets = response.json()
    except requests.exceptions.RequestException as e:
        print(f"  WARNING: Could not fetch closed markets from Polymarket: {e}")
        return []

    tracked_set = set(tracked_ids)
    resolved = []

    for m in closed_markets:
        market_id = str(m.get("id", ""))
        if market_id not in tracked_set:
            continue  # we weren't tracking this market

        # outcomePrices is a JSON array: ["1", "0"] = YES won, ["0", "1"] = NO won
        try:
            outcome_prices = m.get("outcomePrices", "[]")
            if isinstance(outcome_prices, str):
                import json
                outcome_prices = json.loads(outcome_prices)
            final_yes_price = float(outcome_prices[0])
        except (IndexError, ValueError, TypeError):
            continue  # can't determine final price — skip

        resolved.append({
            "id": market_id,
            "question": m.get("question", ""),
            "final_yes_price": final_yes_price,
        })

    return resolved


def _mark_market_resolved(market_id: str, final_price: float) -> None:
    """Update the markets table: mark as inactive and record resolution price."""
    conn = get_connection()
    conn.execute("""
        UPDATE markets
        SET active = 0,
            resolution_price = ?,
            resolved_at = ?
        WHERE id = ?
    """, (final_price, datetime.now(timezone.utc).isoformat(), market_id))
    conn.commit()
    conn.close()


def score_judgments(market_id: str, final_price: float) -> int:
    """
    Find all unscored agent_judgments for market_id, grade each, and insert into scores.
    Returns the number of judgments newly graded.

    Uses a LEFT JOIN to find judgments with no corresponding scores row — this makes
    the function idempotent: calling it twice on the same market is safe.
    """
    conn = get_connection()

    # Find judgments that have no score row yet
    unscored = conn.execute("""
        SELECT aj.id, aj.direction
        FROM agent_judgments aj
        LEFT JOIN scores s ON s.judgment_id = aj.id
        WHERE aj.market_id = ? AND s.id IS NULL
    """, (market_id,)).fetchall()

    if not unscored:
        conn.close()
        return 0

    now = datetime.now(timezone.utc).isoformat()
    graded = 0

    for row in unscored:
        verdict = _is_correct(row["direction"], final_price)
        # Neutral judgments get a scores row too (was_correct=NULL) so we don't
        # re-attempt them on future runs, but they're excluded from hit rate calc.
        conn.execute("""
            INSERT INTO scores (judgment_id, was_correct, market_final_price, scored_at)
            VALUES (?, ?, ?, ?)
        """, (row["id"], verdict, final_price, now))
        graded += 1

    conn.commit()
    conn.close()
    return graded


def get_track_record() -> dict:
    """
    Compute aggregate scoring stats from all graded judgments.

    Returns a dict ready for the Phase 4 dashboard's Track Record tab.
    Neutral judgments (was_correct IS NULL) are excluded from the hit rate —
    they don't count as correct or wrong.

    The sample_size_warning flag is INTENTIONAL: we should never claim edge
    with fewer than 30 graded judgments. Honest framing is core to the project.
    """
    conn = get_connection()

    # All judgments that have been scored (neutral or not)
    total_scored = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]

    # Judgments with an actual verdict (excludes neutrals)
    rows = conn.execute("""
        SELECT aj.direction, s.was_correct
        FROM scores s
        JOIN agent_judgments aj ON aj.id = s.judgment_id
        WHERE s.was_correct IS NOT NULL
    """).fetchall()

    conn.close()

    total_graded = len(rows)
    correct = sum(1 for r in rows if r["was_correct"] == 1)

    # Break down by direction
    by_direction: dict[str, dict] = {}
    for direction in ("up", "down"):
        dir_rows = [r for r in rows if r["direction"] == direction]
        dir_correct = sum(1 for r in dir_rows if r["was_correct"] == 1)
        by_direction[direction] = {
            "graded": len(dir_rows),
            "correct": dir_correct,
            "hit_rate": round(dir_correct / len(dir_rows), 3) if dir_rows else None,
        }

    hit_rate = round(correct / total_graded, 3) if total_graded > 0 else None

    return {
        "total_scored": total_scored,       # includes neutral abstentions
        "total_graded": total_graded,        # excludes neutrals (has actual verdict)
        "correct": correct,
        "hit_rate": hit_rate,
        "by_direction": by_direction,
        # Warn the user when results are based on too few observations
        "sample_size_warning": total_graded < MIN_SAMPLE_FOR_RATE,
        "sample_size_note": (
            f"Results based on {total_graded} graded judgment(s). "
            + ("Insufficient data for reliable hit rate." if total_graded < MIN_SAMPLE_FOR_RATE else "")
        ),
    }


def run() -> dict:
    """
    Main entry point for the scoring phase.
    1. Load the IDs of markets we're currently tracking.
    2. Ask Polymarket which of those have now resolved.
    3. Score all ungraded judgments for each resolved market.
    4. Return the current track record.
    """
    tracked_ids = _load_active_market_ids()
    print(f"  Checking {len(tracked_ids)} tracked markets for resolutions...")

    resolved = fetch_resolved_markets(tracked_ids)

    if not resolved:
        print("  No newly resolved markets found.")
    else:
        print(f"  Found {len(resolved)} newly resolved market(s):")

    total_newly_graded = 0
    for market in resolved:
        market_id = market["id"]
        final_price = market["final_yes_price"]
        outcome = "YES" if final_price >= RESOLUTION_YES else "NO"

        count = score_judgments(market_id, final_price)
        _mark_market_resolved(market_id, final_price)

        print(f"    ✓ {market['question'][:55]}... → resolved {outcome} | {count} judgment(s) graded")
        total_newly_graded += count

    track_record = get_track_record()
    print(f"\n  Track record: {track_record['correct']}/{track_record['total_graded']} correct", end="")
    if track_record["hit_rate"] is not None:
        print(f" ({track_record['hit_rate']:.1%})", end="")
    if track_record["sample_size_warning"]:
        print(" [insufficient data for reliable rate]", end="")
    print()

    return track_record
