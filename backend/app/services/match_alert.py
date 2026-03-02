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
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models.signal import Signal
from app.services.email_orchestrator import trigger as email_trigger, EmailType

logger = logging.getLogger(__name__)


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
            }
            for d in sorted_deals
        ]

        context = {
            "signal_id": signal_id_str,
            "run_id": run_id,
            "signal_name": signal.name,
            "route": route,
            "deal_count": len(deals),
            "new_low": is_new_low,
            "pct_drop": pct_drop,
            "deals": template_deals,
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
