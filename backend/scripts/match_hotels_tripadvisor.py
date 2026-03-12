#!/usr/bin/env python3
"""CLI for matching hotel records against TripAdvisor seed data.

Usage:
    python -m scripts.match_hotels_tripadvisor \
        --input data/tripadvisor/source_hotels.json \
        --seed data/tripadvisor/tripadvisor_seed.csv \
        --output data/tripadvisor/match_results.json

Flags:
    --input              Source hotel file (JSON or CSV)
    --seed               TripAdvisor seed CSV (tripadvisor_url, tripadvisor_name, destination)
    --output             Output directory (default: backend/data/tripadvisor/)
    --limit N            Process only first N hotels (for testing)
    --dry-run            Show what would be matched without writing files
    --only-unmatched     Only process hotels not already in a previous results file
    --previous-results   Path to previous results JSON (for --only-unmatched)

Run from the backend directory:
    cd backend && python -m scripts.match_hotels_tripadvisor --help
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from backend/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.enrichment.tripadvisor_matcher import (
    TripAdvisorMatcher,
    load_source_hotels_from_csv,
    load_source_hotels_from_json,
)
from app.enrichment.outputs import (
    print_summary,
    write_csv,
    write_json,
    write_manual_review,
)

logger = logging.getLogger("match_hotels")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Match hotel records against TripAdvisor seed data"
    )
    parser.add_argument(
        "--input", required=True,
        help="Source hotel file (JSON or CSV with hotel_name, hotel_id, destination, destination_str)",
    )
    parser.add_argument(
        "--seed", required=True,
        help="TripAdvisor seed CSV (tripadvisor_url, tripadvisor_name, destination)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output directory (default: backend/data/tripadvisor/)",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Process only first N hotels (0 = all)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview matches without writing output files",
    )
    parser.add_argument(
        "--only-unmatched", action="store_true",
        help="Skip hotels already matched in previous results",
    )
    parser.add_argument(
        "--previous-results", default=None,
        help="Path to previous results JSON (used with --only-unmatched)",
    )
    return parser.parse_args()


def _load_previously_matched(path: str) -> set[str]:
    """Load hotel_ids that were already successfully matched."""
    matched = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for row in data:
            if row.get("review_status") == "matched" and row.get("source_hotel_id"):
                matched.add(row["source_hotel_id"])
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("Could not load previous results from %s: %s", path, e)
    return matched


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Determine output directory
    output_dir = Path(args.output) if args.output else Path(__file__).resolve().parent.parent / "data" / "tripadvisor"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load source hotels
    input_path = Path(args.input)
    if input_path.suffix == ".json":
        hotels = load_source_hotels_from_json(input_path)
    elif input_path.suffix == ".csv":
        hotels = load_source_hotels_from_csv(input_path)
    else:
        logger.error("Unsupported input format: %s (use .json or .csv)", input_path.suffix)
        sys.exit(1)

    logger.info("Loaded %d source hotels from %s", len(hotels), input_path)

    # Filter to unmatched only if requested
    if args.only_unmatched:
        prev_path = args.previous_results or str(output_dir / "match_results.json")
        previously_matched = _load_previously_matched(prev_path)
        before = len(hotels)
        hotels = [h for h in hotels if h.hotel_id not in previously_matched]
        logger.info("Filtered to %d unmatched hotels (skipped %d previously matched)", len(hotels), before - len(hotels))

    # Apply limit
    if args.limit > 0:
        hotels = hotels[:args.limit]
        logger.info("Limited to first %d hotels", args.limit)

    if not hotels:
        logger.info("No hotels to process. Exiting.")
        return

    # Load seed data
    seed_path = Path(args.seed)
    matcher = TripAdvisorMatcher.from_csv(seed_path)

    # Run matching
    logger.info("Starting matching for %d hotels...", len(hotels))
    results = matcher.match_all(hotels)

    # Print summary
    print_summary(results)

    # Write outputs
    if args.dry_run:
        logger.info("Dry run — skipping file output")
        for r in results:
            status = r.review_status.upper()
            conf = f"{r.match_confidence:.2f}" if r.match_confidence else "—"
            print(f"  [{status:20s}] {conf}  {r.source_hotel_name}")
            if r.tripadvisor_matched_name:
                print(f"  {'':22s} → {r.tripadvisor_matched_name}")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = write_json(results, output_dir / "match_results.json")
    csv_path = write_csv(results, output_dir / "match_results.csv")
    review_path = write_manual_review(results, output_dir / "manual_review.csv")

    # Also write a timestamped copy for audit trail
    write_json(results, output_dir / f"match_results_{timestamp}.json")

    print(f"  Output files:")
    print(f"    Results JSON:   {json_path}")
    print(f"    Results CSV:    {csv_path}")
    print(f"    Manual review:  {review_path}")


if __name__ == "__main__":
    main()
