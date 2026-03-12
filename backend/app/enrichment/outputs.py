"""Output writers for TripAdvisor match results.

Produces:
- Full results JSON
- Full results CSV
- Manual review CSV (filtered to ambiguous/needs_manual_review/not_found)
"""

import csv
import json
import logging
from pathlib import Path

from app.enrichment.tripadvisor_matcher import MatchResult

logger = logging.getLogger(__name__)

_CSV_COLUMNS = [
    "source_hotel_name",
    "normalized_hotel_name",
    "source_hotel_id",
    "source_destination",
    "source_country",
    "tripadvisor_url",
    "tripadvisor_id",
    "tripadvisor_matched_name",
    "match_confidence",
    "match_method",
    "review_status",
    "notes",
]

_REVIEW_STATUSES = {"ambiguous", "needs_manual_review", "not_found"}


def write_json(results: list[MatchResult], path: str | Path) -> Path:
    """Write all match results to a JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [r.to_dict() for r in results]
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %d results to %s", len(data), path)
    return path


def write_csv(results: list[MatchResult], path: str | Path) -> Path:
    """Write all match results to a CSV file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for r in results:
            writer.writerow(r.to_dict())
    logger.info("Wrote %d results to %s", len(results), path)
    return path


def write_manual_review(results: list[MatchResult], path: str | Path) -> Path:
    """Write only records needing manual review to a CSV file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    review_items = [r for r in results if r.review_status in _REVIEW_STATUSES]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for r in review_items:
            writer.writerow(r.to_dict())
    logger.info("Wrote %d manual review items to %s", len(review_items), path)
    return path


def print_summary(results: list[MatchResult]) -> None:
    """Print a summary of match results to stdout."""
    total = len(results)
    matched = sum(1 for r in results if r.review_status == "matched")
    ambiguous = sum(1 for r in results if r.review_status == "ambiguous")
    needs_review = sum(1 for r in results if r.review_status == "needs_manual_review")
    not_found = sum(1 for r in results if r.review_status == "not_found")

    pct = f"({matched / total * 100:.1f}%)" if total > 0 else "(—)"

    print(f"\n{'='*60}")
    print(f"  TripAdvisor Matching Summary")
    print(f"{'='*60}")
    print(f"  Total hotels:        {total}")
    print(f"  Matched:             {matched}  {pct}")
    print(f"  Ambiguous:           {ambiguous}")
    print(f"  Needs manual review: {needs_review}")
    print(f"  Not found:           {not_found}")
    print(f"{'='*60}\n")

    if ambiguous + needs_review + not_found > 0:
        print("  Hotels needing attention:")
        for r in results:
            if r.review_status in _REVIEW_STATUSES:
                status_tag = r.review_status.upper().replace("_", " ")
                note = f" — {r.notes}" if r.notes else ""
                print(f"    [{status_tag}] {r.source_hotel_name}{note}")
        print()
