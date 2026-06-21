"""
SignalEdge pipeline entry point.

Run this script to execute one full ingestion cycle:
  1. Initialise the database (safe to run repeatedly — skips if tables exist)
  2. Fetch active markets from Polymarket
  3. Fetch relevant news headlines for those markets from newsdata.io
  4. (Phase 2) Run the agent to judge divergences
  5. (Phase 3) Score any newly resolved markets

Usage:
    python run_pipeline.py              # full pipeline
    python run_pipeline.py --ingest-only  # skip agent (Phase 1 testing)
"""

import argparse
import sys
from datetime import datetime, timezone

from db.schema import init_db
from ingestion import markets as markets_module
from ingestion import news as news_module
from agent import brain as brain_module
from agent import scorer as scorer_module


def run(ingest_only: bool = False) -> None:
    start = datetime.now(timezone.utc)
    print(f"\n{'='*55}")
    print(f"  SignalEdge Pipeline — {start.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*55}\n")

    # Step 1: ensure DB tables exist
    print("[1/4] Initialising database...")
    init_db()

    # Step 2: fetch markets
    print("\n[2/4] Fetching prediction markets...")
    active_markets = markets_module.run()

    if not active_markets:
        print("  No markets returned — check your internet connection or Polymarket API status.")
        sys.exit(1)

    # Step 3: fetch news
    print("\n[3/4] Fetching news headlines...")
    news_module.run(active_markets)

    # Step 4: agent judgment (skip with --ingest-only)
    judgments = []
    if not ingest_only:
        print("\n[4/5] Running agent analysis...")
        judgments = brain_module.run(active_markets)
    else:
        print("\n[4/5] Agent phase skipped (--ingest-only flag set).")

    # Step 5: score any newly resolved markets
    print("\n[5/5] Checking for resolved markets to score...")
    track_record = scorer_module.run()

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    print(f"\n{'='*55}")
    print(f"  Pipeline complete in {elapsed:.1f}s")
    print(f"  Markets fetched  : {len(active_markets)}")
    print(f"  Judgments saved  : {len(judgments)}")
    print(f"  Track record     : {track_record['correct']}/{track_record['total_graded']} correct", end="")
    if track_record["hit_rate"] is not None:
        print(f" ({track_record['hit_rate']:.1%})", end="")
    print()
    print(f"  Run 'python run_pipeline.py' again in ~1 hour to build price history.")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SignalEdge ingestion pipeline")
    parser.add_argument(
        "--ingest-only",
        action="store_true",
        help="Run data ingestion only; skip the agent judgment step (Phase 2)",
    )
    args = parser.parse_args()
    run(ingest_only=args.ingest_only)
