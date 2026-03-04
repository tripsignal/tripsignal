"""
Match alert service — batches deal matches per signal per run and triggers
one MATCH_ALERT email via the orchestrator.

Called by the scraper AFTER all deals in a cycle have been matched.
Never sends email directly — always goes through EmailOrchestratorService.

Flow:
1. Accept a dict of {signal_id: [matched Deal objects]} + run_id.
2. For each signal: compute intelligence (min price, new low, pct drop).
3. Update signal.last_check_min_price, last_check_at, all_time_low_price/at.
4. Build context and call orchestrator with idempotency_key: match_alert:{signalId}:{runId}.
5. One email per signal per run — multiple deals batched into one email.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.signal import Signal
from app.db.models.signal_intel_cache import SignalIntelCache
from app.db.models.user import User
from app.services.email_orchestrator import trigger as email_trigger, EmailType

logger = logging.getLogger(__name__)


def _build_intel_sentence(
    intel: SignalIntelCache | None,
    signal: Signal,
    is_new_low: bool,
    pct_drop: int,
) -> str:
    """Build the 'one sentence nobody else can say' for email copy."""
    days_monitoring = (datetime.now(timezone.utc) - signal.created_at).days if signal.created_at else 0

    if intel and intel.total_matches and intel.total_matches > 1:
        pct = intel.current_deal_percentile
        if pct is not None and pct <= 0.25:
            rank_pct = max(1, int(pct * 100))
            return f"Top {rank_pct}% cheapest deal in {days_monitoring} days of monitoring."

        if intel.trend_direction == "down" and is_new_low:
            return "Prices have been dropping. This is the lowest point yet."

        if intel.trend_direction == "up" and pct_drop > 0:
            weeks = intel.trend_consecutive_weeks or 1
            return f"Prices have been rising for {weeks} weeks. This one bucks the trend."

        if intel.trend_direction == "down" and intel.trend_consecutive_weeks and intel.trend_consecutive_weeks >= 2:
            weeks = intel.trend_consecutive_weeks
            return f"Prices on this route have dropped {weeks} weeks in a row."

    # Fallback for new signals with insufficient history
    if days_monitoring < 7:
        return "New deal \u2014 no price history yet, but it fits your criteria."
    return "First time we\u2019ve seen this hotel on your route."


def _filter_repeat_deals(
    db: Session,
    signal_id,
    deals: list[dict],
    current_run_id: str,
) -> list[dict]:
    """Remove deals already alerted in recent cycles at similar price (±3%).

    Checks DealMatch+Deal records from the last 7 days for the same signal,
    excluding the current run. If same hotel_name + depart_date appeared at
    a price within ±3%, the deal is suppressed from the alert.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    recent = db.execute(
        select(Deal.hotel_name, Deal.depart_date, Deal.price_cents)
        .join(DealMatch, DealMatch.deal_id == Deal.id)
        .where(DealMatch.signal_id == signal_id)
        .where(DealMatch.matched_at >= cutoff)
        .where(DealMatch.run_id != current_run_id)
    ).all()

    # Build lookup: (hotel_name, depart_date_str) -> [price_cents, ...]
    seen: dict[tuple[str, str], list[int]] = {}
    for hotel, depart, price in recent:
        key = (hotel or "", str(depart))
        seen.setdefault(key, []).append(price)

    filtered = []
    for deal in deals:
        key = (deal.get("hotel_name", ""), str(deal.get("depart_date", "")))
        if key in seen:
            deal_price = deal["price_cents"]
            if any(abs(deal_price - p) / max(p, 1) <= 0.03 for p in seen[key]):
                logger.debug(
                    "repeat deal filtered: %s %s at %d cents",
                    key[0], key[1], deal_price,
                )
                continue
        filtered.append(deal)

    return filtered


def process_signal_matches(
    db: Session,
    signal_deals: dict[str, list[dict]],
    run_id: str,
) -> list[dict]:
    """Process all matched deals for all signals in one scan run.

    Args:
        db: Database session.
        signal_deals: Mapping of signal_id (str) -> list of deal dicts.
            Each deal dict has: deal_id, price_cents, hotel_name, star_rating,
            depart_date, return_date, duration_nights, deeplink_url,
            destination_str, origin, price_dropped (bool), price_delta (int cents).
        run_id: The SignalRun ID for this scan cycle.

    Returns:
        List of orchestrator results (one per signal).
    """
    results = []
    now = datetime.now(timezone.utc)

    for signal_id_str, deals in signal_deals.items():
        if not deals:
            continue

        signal = db.query(Signal).filter(Signal.id == signal_id_str).first()
        if not signal:
            logger.warning("match_alert: signal %s not found, skipping", signal_id_str)
            continue

        # ── 1. Compute intelligence ──────────────────────────────────────
        min_price_cents = min(d["price_cents"] for d in deals)
        previous_min = signal.last_check_min_price

        # All-time low check
        is_new_low = False
        if signal.all_time_low_price is None or min_price_cents < signal.all_time_low_price:
            is_new_low = True

        # Percentage drop from previous check (only if we have a previous check)
        pct_drop = 0
        if previous_min and previous_min > 0 and min_price_cents < previous_min:
            pct_drop = int(round((previous_min - min_price_cents) / previous_min * 100))

        # ── 2. Update signal intelligence fields (DB first) ──────────────
        signal.last_check_min_price = min_price_cents
        signal.last_check_at = now
        if is_new_low:
            signal.all_time_low_price = min_price_cents
            signal.all_time_low_at = now
        # Clear no-match guard since we now have matches
        signal.no_match_email_sent_at = None
        db.flush()

        # ── 3. Build route string ────────────────────────────────────────
        route = _build_route(signal, deals)

        # ── 3b. Fetch intel cache for this signal ─────────────────────────
        intel = db.execute(
            select(SignalIntelCache).where(SignalIntelCache.signal_id == signal.id)
        ).scalar_one_or_none()

        intel_sentence = _build_intel_sentence(intel, signal, is_new_low, pct_drop)
        days_monitoring = (datetime.now(timezone.utc) - signal.created_at).days if signal.created_at else 0
        is_top_25 = bool(intel and intel.current_deal_percentile is not None and intel.current_deal_percentile <= 0.25)

        # ── 3c. Fetch user for mode + threshold checks ─────────────────
        user = db.query(User).filter(User.id == signal.user_id).first()
        if not user:
            logger.warning("match_alert: user for signal %s not found, skipping", signal_id_str)
            continue

        # ── 3d. User mode routing ──────────────────────────────────────
        if user.email_mode == "dormant":
            logger.debug("match_alert: signal %s skipped — user is dormant", signal_id_str)
            continue
        if user.email_mode == "passive":
            # Passive users accumulate for weekly digest (Phase 5), skip instant
            logger.debug("match_alert: signal %s skipped — user is passive", signal_id_str)
            continue

        # ── 3e. Alert threshold gate ───────────────────────────────────
        threshold = user.alert_threshold or "any"
        if threshold == "drops" and pct_drop < 8:
            logger.debug(
                "match_alert: signal %s skipped — threshold='drops' pct_drop=%d",
                signal_id_str, pct_drop,
            )
            continue
        if threshold == "records" and not is_new_low:
            logger.debug(
                "match_alert: signal %s skipped — threshold='records' not new low",
                signal_id_str,
            )
            continue

        # ── 3f. Repeat deal filter ─────────────────────────────────────
        deals = _filter_repeat_deals(db, signal.id, deals, run_id)
        if not deals:
            logger.debug("match_alert: signal %s — all deals filtered as repeats", signal_id_str)
            continue

        # ── 3g. Noise filter ──────────────────────────────────────────
        # Skip instant alert if min price barely changed (±3%) and deal isn't notable
        if previous_min and previous_min > 0 and not is_new_low and not is_top_25:
            price_change_pct = abs(min_price_cents - previous_min) / previous_min * 100
            if price_change_pct <= 3:
                logger.info(
                    "match_alert: signal %s noise-filtered — price change %.1f%%",
                    signal_id_str, price_change_pct,
                )
                continue

        # ── 4. Build context for template ────────────────────────────────
        # Sort deals by price ascending — best first
        sorted_deals = sorted(deals, key=lambda d: d["price_cents"])
        template_deals = [
            {
                "hotel_name": d.get("hotel_name", ""),
                "star_rating": d.get("star_rating"),
                "price_cents": d["price_cents"],
                "duration_nights": d.get("duration_nights", 7),
                "depart_date": str(d.get("depart_date", "")),
                "deeplink_url": d.get("deeplink_url", "https://tripsignal.ca/signals"),
                "price_delta": d.get("price_delta", 0),
            }
            for d in sorted_deals
        ]

        # Best deal's price delta for subject line use
        best_deal = sorted_deals[0] if sorted_deals else {}
        best_price_delta = best_deal.get("price_delta", 0)

        context = {
            "signal_id": signal_id_str,
            "run_id": run_id,
            "signal_name": signal.name,
            "route": route,
            "deal_count": len(deals),
            "new_low": is_new_low,
            "pct_drop": pct_drop,
            "deals": template_deals,
            # Intelligence data
            "intel_sentence": intel_sentence,
            "days_monitoring": days_monitoring,
            "is_top_25": is_top_25,
            "percentile_rank": intel.current_deal_percentile if intel else None,
            "trend_direction": intel.trend_direction if intel else "stable",
            "trend_weeks": intel.trend_consecutive_weeks if intel else 0,
            "min_price_ever_cents": intel.min_price_ever_cents if intel else None,
            "total_matches": intel.total_matches if intel else 0,
            "best_price_delta": best_price_delta,
            "best_price_cents": best_deal.get("price_cents") if best_deal else None,
            # Destination for subject line
            "destination": (
                deals[0].get("destination_str", "").split(",")[0].strip()
                if deals and deals[0].get("destination_str")
                else signal.name
            ),
        }

        # ── 5. Trigger via orchestrator (one email per signal per run) ───
        try:
            result = email_trigger(
                db=db,
                email_type=EmailType.MATCH_ALERT,
                user_id=str(signal.user_id),
                context=context,
            )
            results.append(result)
        except Exception:
            logger.exception(
                "match_alert: failed to trigger email for signal %s run %s",
                signal_id_str, run_id,
            )
            results.append({"status": "error", "reason": "trigger_exception"})

    return results


def _build_route(signal: Signal, deals: list[dict]) -> str:
    """Build a human-readable route string like 'Regina (YQR) → Cancun'.

    Uses the signal's departure airports and the deal destinations.
    """
    # Departure: use first airport code
    airports = signal.departure_airports or []
    if airports:
        origin = airports[0]
    else:
        # Fallback to first deal's origin
        origin = deals[0].get("origin", "") if deals else ""

    # Destination: use signal name or first deal's destination
    dest_str = ""
    if deals:
        dest_str = deals[0].get("destination_str", "")
    if not dest_str:
        dest_str = signal.name

    if origin:
        return f"{origin} → {dest_str}"
    return dest_str
