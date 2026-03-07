"""
Lifecycle Email Worker — scheduled jobs for trial, inactivity, no-match,
weekly digest, and payment retry reminder emails.

Runs as an infinite-loop polling worker (same pattern as notifications_log_worker).
Each cycle scans for eligible users/signals and calls the orchestrator.

Job execution order matters:
  1. Trial auto-extension (extends before warning fires)
  2. Trial expiring soon
  3. Trial expired
  4. No signal reminder
  5. Inactive re-engagement (dormant users)
  6. No match update (PRO only)
  7. Payment failed reminders
  8. User mode refresh
  9. Weekly digest (passive users, Sundays only)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.deal_match import DealMatch
from app.db.models.email_log import EmailLog
from app.db.models.signal import Signal
from app.db.models.user import User
from app.db.session import SessionLocal
from app.services.email_orchestrator import (
    EmailType,
    drain_deferred_emails,
    trigger as email_trigger,
)
from app.services.email_queue import drain as drain_email_queue

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("LIFECYCLE_POLL_SECONDS", "300"))  # 5 min default


def main() -> None:
    """Entry point: infinite loop polling for lifecycle email jobs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    logger.info("Lifecycle email worker starting (poll=%ds)", POLL_INTERVAL)

    while True:
        try:
            db = SessionLocal()
            try:
                run_cycle(db)
            finally:
                db.close()
        except Exception:
            logger.exception("Lifecycle worker cycle error")

        time.sleep(POLL_INTERVAL)


def run_cycle(db: Session, now: datetime | None = None) -> None:
    """Run all lifecycle jobs once.  Extracted for testability."""
    if now is None:
        now = datetime.now(timezone.utc)

    # Drain the email queue first — deliver waiting emails ASAP.
    _run_queue_drain(db)

    # Drain deferred quiet-hours emails — renders and enqueues them.
    _run_deferred_drain(db)

    # Drain again after deferred emails have been enqueued.
    _run_queue_drain(db)

    # Order matters — extension must run before trial warnings.
    _run_trial_auto_extension(db, now)
    _run_trial_expiring_soon(db, now)
    _run_trial_expired(db, now)
    _run_no_signal_reminder(db, now)
    _run_inactive_reengagement(db, now)
    _run_no_match_update(db, now)
    _run_payment_failed_reminders(db, now)
    _run_user_mode_refresh(db, now)
    _run_weekly_digests(db, now)


# ── Job 0a: Drain email queue ─────────────────────────────────────────────────

def _run_queue_drain(db: Session) -> dict:
    """Send queued emails via rate-limited queue worker."""
    try:
        stats = drain_email_queue(db)
        if stats["sent"] > 0 or stats["failed"] > 0:
            logger.info(
                "queue_drain: sent=%d failed=%d dead=%d elapsed=%dms",
                stats["sent"], stats["failed"], stats["dead"], stats["elapsed_ms"],
            )
        return stats
    except Exception:
        logger.exception("queue_drain failed")
        db.rollback()
        return {}


# ── Job 0b: Drain deferred quiet-hours emails ────────────────────────────────

def _run_deferred_drain(db: Session) -> int:
    """Enqueue emails that were deferred during quiet hours."""
    try:
        return drain_deferred_emails(db)
    except Exception:
        logger.exception("deferred_drain failed")
        db.rollback()
        return 0


# ── Job 1: Automatic 7-day trial extension (48h before expiry) ───────────────

def _run_trial_auto_extension(db: Session, now: datetime) -> int:
    """Extend trial by 7 days for users within 48h of expiry (one-time only).

    Guard: trial_auto_extended_at IS NULL ensures this fires exactly once.
    After extension, clears trial_expiring_email_sent_at so the warning
    re-fires with the correct days_left for the new end date.

    Returns the number of users extended (for testing).
    """
    window_end = now + timedelta(hours=48)

    users = db.execute(
        select(User).where(
            User.plan_type == "free",
            User.plan_status == "active",
            User.trial_ends_at.isnot(None),
            User.trial_ends_at <= window_end,
            User.trial_ends_at > now,  # trial hasn't expired yet
            User.trial_auto_extended_at.is_(None),
            User.deleted_at.is_(None),
        )
    ).scalars().all()

    extended = 0
    for user in users:
        user.trial_ends_at = user.trial_ends_at + timedelta(days=7)
        user.trial_auto_extended_at = now
        # Clear so trial_expiring_soon re-fires for new date
        user.trial_expiring_email_sent_at = None
        extended += 1
        logger.info(
            "trial_auto_extension: extended %s to %s",
            user.email, user.trial_ends_at.isoformat(),
        )

    if extended:
        db.commit()
        logger.info("trial_auto_extension: extended %d users", extended)

    return extended


# ── Job 2: Trial expiring soon (72h before trial_ends_at) ────────────────────

def _run_trial_expiring_soon(db: Session, now: datetime) -> int:
    """Send TRIAL_EXPIRING_SOON to free-trial users whose trial ends in ~72h.

    Window: trial_ends_at between now+48h and now+96h (centered on 72h).
    Returns the number of users processed.
    """
    window_start = now + timedelta(hours=48)
    window_end = now + timedelta(hours=96)

    users = db.execute(
        select(User).where(
            User.plan_type == "free",
            User.plan_status == "active",
            User.trial_ends_at.isnot(None),
            User.trial_ends_at.between(window_start, window_end),
            User.trial_expiring_email_sent_at.is_(None),
            User.deleted_at.is_(None),
        )
    ).scalars().all()

    sent = 0
    for user in users:
        days_left = max(1, round((user.trial_ends_at - now).total_seconds() / 86400))
        trial_end_date = user.trial_ends_at.strftime("%Y-%m-%d")
        try:
            email_trigger(
                db=db,
                email_type=EmailType.TRIAL_EXPIRING_SOON,
                user_id=str(user.id),
                context={
                    "days_left": days_left,
                    "trial_end_date": trial_end_date,
                },
            )
            sent += 1
        except Exception:
            logger.exception("trial_expiring_soon failed for %s", user.email)

    if users:
        logger.info("trial_expiring_soon: processed %d users", len(users))
    return sent


# ── Job 3: Trial expired ─────────────────────────────────────────────────────

def _run_trial_expired(db: Session, now: datetime) -> int:
    """Send TRIAL_EXPIRED_UPSELL to users whose trial ended recently.

    Window: trial_ends_at between now-24h and now (generous window so
    the 5-min poll interval doesn't miss anyone).
    Returns the number of users processed.
    """
    window_start = now - timedelta(hours=24)

    users = db.execute(
        select(User).where(
            User.plan_type == "free",
            User.trial_ends_at.isnot(None),
            User.trial_ends_at <= now,
            User.trial_ends_at >= window_start,
            User.trial_expired_email_sent_at.is_(None),
            User.deleted_at.is_(None),
        )
    ).scalars().all()

    sent = 0
    for user in users:
        trial_end_date = user.trial_ends_at.strftime("%Y-%m-%d")
        try:
            email_trigger(
                db=db,
                email_type=EmailType.TRIAL_EXPIRED_UPSELL,
                user_id=str(user.id),
                context={"trial_end_date": trial_end_date},
            )
            sent += 1
        except Exception:
            logger.exception("trial_expired failed for %s", user.email)

    if users:
        logger.info("trial_expired: processed %d users", len(users))
    return sent


# ── Job 4: No signal reminder (user created >24h ago, 0 signals) ─────────────

def _run_no_signal_reminder(db: Session, now: datetime) -> int:
    """Send NO_SIGNAL_REMINDER to users who signed up >24h ago with no signals."""
    cutoff = now - timedelta(hours=24)

    signal_count = (
        select(func.count(Signal.id))
        .where(Signal.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )

    users = db.execute(
        select(User).where(
            User.created_at <= cutoff,
            User.no_signal_email_sent_at.is_(None),
            User.deleted_at.is_(None),
            User.email != "",
            signal_count == 0,
        )
    ).scalars().all()

    sent = 0
    for user in users:
        try:
            email_trigger(
                db=db,
                email_type=EmailType.NO_SIGNAL_REMINDER,
                user_id=str(user.id),
            )
            sent += 1
        except Exception:
            logger.exception("no_signal_reminder failed for %s", user.email)

    if users:
        logger.info("no_signal_reminder: processed %d users", len(users))
    return sent


# ── Job 5: Inactive re-engagement (dormant users with active signals) ────────

def _run_inactive_reengagement(db: Session, now: datetime) -> int:
    """Send INACTIVE_REENGAGEMENT to dormant users with active signals.

    Triggers when user is dormant (email_mode='dormant').
    60-day cooldown is enforced by the orchestrator suppression rule.
    After sending, move user to passive (weekly-only).
    Returns the number of emails triggered.
    """
    active_signal_count = (
        select(func.count(Signal.id))
        .where(Signal.user_id == User.id, Signal.status == "active")
        .correlate(User)
        .scalar_subquery()
    )

    users = db.execute(
        select(User).where(
            User.email_mode == "dormant",
            User.deleted_at.is_(None),
            User.email_opt_out == False,  # noqa: E712
            User.email != "",
            active_signal_count > 0,
        )
    ).scalars().all()

    sent = 0
    for user in users:
        days_inactive = 0
        if user.last_login_at:
            days_inactive = (now - user.last_login_at).days

        # Build enriched context with proof-of-value data
        context = _build_reengagement_context(db, user, days_inactive, now)

        try:
            result = email_trigger(
                db=db,
                email_type=EmailType.INACTIVE_REENGAGEMENT,
                user_id=str(user.id),
                context=context,
            )
            if result.get("status") in ("sent", "dry_run"):
                # Move user to passive (weekly-only) after re-engagement
                user.email_mode = "passive"
                db.commit()
            sent += 1
        except Exception:
            logger.exception("inactive_reengagement failed for %s", user.email)

    if users:
        logger.info("inactive_reengagement: checked %d users", len(users))
    return sent


def _build_reengagement_context(
    db: Session, user: User, days_inactive: int, now: datetime,
) -> dict:
    """Build enriched context for re-engagement email with proof-of-value data."""
    from app.db.models.deal import Deal

    context: dict = {
        "days_inactive": days_inactive,
        "period": now.strftime("%Y-%m"),
    }

    # Total deals found across all user signals
    total_deals = db.execute(
        select(func.count(DealMatch.id))
        .join(Signal, DealMatch.signal_id == Signal.id)
        .where(Signal.user_id == user.id)
    ).scalar() or 0
    context["total_deals_found"] = total_deals

    # Best missed deal (cheapest match the user didn't click on)
    best_missed = db.execute(
        select(Deal.hotel_name, Deal.depart_date, Deal.price_cents,
               Deal.return_date)
        .join(DealMatch, DealMatch.deal_id == Deal.id)
        .join(Signal, DealMatch.signal_id == Signal.id)
        .where(Signal.user_id == user.id)
        .order_by(Deal.price_cents.asc())
        .limit(1)
    ).first()
    if best_missed:
        nights = 7
        if best_missed.return_date and best_missed.depart_date:
            nights = (best_missed.return_date - best_missed.depart_date).days or 7
        context["best_missed_deal"] = {
            "price_cents": best_missed.price_cents,
            "hotel_name": best_missed.hotel_name or "",
            "duration_nights": nights,
            "depart_date": str(best_missed.depart_date) if best_missed.depart_date else "",
        }
        context["best_missed_price_cents"] = best_missed.price_cents

    # Price range from intel caches
    from app.db.models.signal_intel_cache import SignalIntelCache
    intel_rows = db.execute(
        select(SignalIntelCache)
        .join(Signal, SignalIntelCache.signal_id == Signal.id)
        .where(Signal.user_id == user.id)
    ).scalars().all()

    if intel_rows:
        min_prices = [i.min_price_ever_cents for i in intel_rows if i.min_price_ever_cents]
        if min_prices:
            context["min_price_ever_cents"] = min(min_prices)

        # Get current trend from first intel row with data
        for intel in intel_rows:
            if intel.trend_direction and intel.trend_direction != "stable":
                context["trend_direction"] = intel.trend_direction
                break

    # Current best deal (active deal matching any user signal)
    current_best = db.execute(
        select(Deal.hotel_name, Deal.price_cents, Deal.depart_date, Deal.return_date)
        .join(DealMatch, DealMatch.deal_id == Deal.id)
        .join(Signal, DealMatch.signal_id == Signal.id)
        .where(Signal.user_id == user.id, Deal.is_active == True)  # noqa: E712
        .order_by(Deal.price_cents.asc())
        .limit(1)
    ).first()
    if current_best:
        nights = 7
        if current_best.return_date and current_best.depart_date:
            nights = (current_best.return_date - current_best.depart_date).days or 7
        context["current_best_deal"] = {
            "price_cents": current_best.price_cents,
            "hotel_name": current_best.hotel_name or "",
            "duration_nights": nights,
        }

    return context


# ── Job 6: No match update (PRO only, signal active 14d, 0 matches) ──────────

def _run_no_match_update(db: Session, now: datetime) -> int:
    """Send NO_MATCH_UPDATE for PRO signals active 14+ days with 0 matches.

    PRO only — free users don't get no-match updates.
    One email per signal (guarded by signal.no_match_email_sent_at).
    Suppressed if user received a MATCH_ALERT in the last 7 days.
    Returns the number of emails triggered.
    """
    cutoff = now - timedelta(days=14)

    match_count = (
        select(func.count(DealMatch.id))
        .where(DealMatch.signal_id == Signal.id)
        .correlate(Signal)
        .scalar_subquery()
    )

    signals = db.execute(
        select(Signal).join(User, Signal.user_id == User.id).where(
            Signal.status == "active",
            Signal.created_at <= cutoff,
            Signal.no_match_email_sent_at.is_(None),
            User.plan_type == "pro",
            User.deleted_at.is_(None),
            User.email_opt_out == False,  # noqa: E712
            match_count == 0,
        )
    ).scalars().all()

    sent = 0
    for signal in signals:
        # Suppress if user got a match email in last 7 days
        recent_match_email = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == signal.user_id,
                EmailLog.email_type == EmailType.MATCH_ALERT.value,
                EmailLog.status.in_(("sent", "dry_run")),
                EmailLog.sent_at >= now - timedelta(days=7),
            ).limit(1)
        ).scalar_one_or_none()
        if recent_match_email:
            continue

        days_active = (now - signal.created_at).days
        # window_start for idempotency key — using ISO date of signal creation
        window_start = signal.created_at.strftime("%Y-%m-%d")
        try:
            result = email_trigger(
                db=db,
                email_type=EmailType.NO_MATCH_UPDATE,
                user_id=str(signal.user_id),
                context={
                    "signal_name": signal.name,
                    "signal_id": str(signal.id),
                    "days_active": days_active,
                    "window_start": window_start,
                },
            )
            # Stamp signal-level flag if sent or dry_run
            if result.get("status") in ("sent", "dry_run"):
                signal.no_match_email_sent_at = now
                db.commit()
            sent += 1
        except Exception:
            logger.exception("no_match_update failed for signal %s", signal.id)

    if signals:
        logger.info("no_match_update: checked %d signals", len(signals))
    return sent


# ── Job 7: Payment failed reminders (+3d and +7d) ────────────────────────────

def _run_payment_failed_reminders(db: Session, now: datetime) -> int:
    """Send PAYMENT_FAILED_REMINDER at +3 days and +7 days after initial failure."""
    sent = 0
    for reminder_num, days_after in [(1, 3), (2, 7)]:
        window_start = now - timedelta(days=days_after, hours=6)
        window_end = now - timedelta(days=days_after - 1)

        # Find users who got a PAYMENT_FAILED email in that window
        failed_logs = db.execute(
            select(EmailLog).where(
                EmailLog.email_type == EmailType.PAYMENT_FAILED.value,
                EmailLog.status.in_(("sent", "dry_run")),
                EmailLog.sent_at.between(window_start, window_end),
            )
        ).scalars().all()

        for log_entry in failed_logs:
            user = db.execute(
                select(User).where(User.id == log_entry.user_id)
            ).scalar_one_or_none()
            if not user or user.deleted_at:
                continue
            # Skip if subscription is resolved
            if user.stripe_subscription_status == "active":
                continue

            invoice_id = ""
            if log_entry.metadata_json:
                invoice_id = log_entry.metadata_json.get("invoice_id", "")

            try:
                email_trigger(
                    db=db,
                    email_type=EmailType.PAYMENT_FAILED_REMINDER,
                    user_id=str(user.id),
                    context={
                        "invoice_id": invoice_id,
                        "reminder_num": reminder_num,
                    },
                )
                sent += 1
            except Exception:
                logger.exception("payment_failed_reminder failed for %s", user.email)

    return sent


# ── Job 8: User mode refresh ─────────────────────────────────────────────────

def _run_user_mode_refresh(db: Session, now: datetime) -> dict:
    """Refresh email mode for all users based on engagement timestamps."""
    try:
        from app.services.user_mode import refresh_all_user_modes
        return refresh_all_user_modes(db)
    except Exception:
        logger.exception("User mode refresh failed")
        db.rollback()
        return {}


# ── Job 9: Weekly digest (passive users, Sundays only) ──────────────────────

def _run_weekly_digests(db: Session, now: datetime) -> int:
    """Send WEEKLY_DIGEST to passive users on Sundays.

    For each passive user, find the best deals from their signals over the
    last 7 days. If no deals found that week, skip silently (per spec).
    Returns the number of digests sent.
    """
    # Only send on Sundays (weekday 6)
    if now.weekday() != 6:
        return 0

    # Only send once per Sunday — check if we already ran today
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Use a simple time window: only run between 8-10 AM UTC on Sundays
    if now.hour < 8 or now.hour >= 10:
        return 0

    from app.db.models.deal import Deal
    from app.db.models.signal_intel_cache import SignalIntelCache

    passive_users = db.execute(
        select(User).where(
            User.email_mode == "passive",
            User.deleted_at.is_(None),
            User.email_opt_out == False,  # noqa: E712
            User.email != "",
        )
    ).scalars().all()

    week_ago = now - timedelta(days=7)
    week_iso = now.strftime("%Y-W%W")
    sent = 0

    for user in passive_users:
        # Get all active signals for this user
        signals = db.execute(
            select(Signal).where(
                Signal.user_id == user.id,
                Signal.status == "active",
            )
        ).scalars().all()

        if not signals:
            continue

        # Find best deals across all signals from the past 7 days
        all_deals = []
        best_intel = None
        for sig in signals:
            deals = db.execute(
                select(
                    Deal.hotel_name, Deal.price_cents, Deal.depart_date,
                    Deal.return_date, Deal.star_rating,
                )
                .join(DealMatch, DealMatch.deal_id == Deal.id)
                .where(
                    DealMatch.signal_id == sig.id,
                    DealMatch.matched_at >= week_ago,
                )
                .order_by(Deal.price_cents.asc())
                .limit(10)
            ).all()

            for d in deals:
                nights = 7
                if d.return_date and d.depart_date:
                    nights = (d.return_date - d.depart_date).days or 7
                all_deals.append({
                    "hotel_name": d.hotel_name or "",
                    "star_rating": d.star_rating,
                    "price_cents": d.price_cents,
                    "duration_nights": nights,
                    "depart_date": str(d.depart_date) if d.depart_date else "",
                })

            # Grab intel for context
            if not best_intel:
                best_intel = db.execute(
                    select(SignalIntelCache).where(SignalIntelCache.signal_id == sig.id)
                ).scalar_one_or_none()

        # Spec: skip silently if no deals found that week
        if not all_deals:
            continue

        # Sort by price, take best
        all_deals.sort(key=lambda d: d["price_cents"])

        days_monitoring = 0
        first_signal = signals[0] if signals else None
        if first_signal and first_signal.created_at:
            days_monitoring = (now - first_signal.created_at).days

        # Get destination price index for the user's primary departure airport
        dest_index = None
        primary_airport = None
        for sig in signals:
            if sig.departure_airports:
                primary_airport = sig.departure_airports[0]
                break
        if primary_airport:
            from app.services.signal_intel import get_destination_index, get_departure_heatmap
            dest_index = get_destination_index(db, primary_airport, limit=5)

        # Departure heatmap for the user's primary route
        heatmap = None
        if primary_airport and first_signal:
            # Use the first signal's destination regions to get a heatmap
            dest_regions = first_signal.destination_regions or []
            if dest_regions:
                heatmap = get_departure_heatmap(db, primary_airport, dest_regions[0])

        context = {
            "deal_count": len(all_deals),
            "deals": all_deals[:5],  # Top 5 deals
            "signal_name": first_signal.name if first_signal else "your signals",
            "route": "",
            "destination": "",
            "best_price_cents": all_deals[0]["price_cents"] if all_deals else None,
            "trend_direction": best_intel.trend_direction if best_intel else "stable",
            "trend_weeks": best_intel.trend_consecutive_weeks if best_intel else 0,
            "best_value_nights": best_intel.best_value_nights if best_intel else None,
            "best_value_pct_saving": best_intel.best_value_pct_saving if best_intel else None,
            "total_matches": best_intel.total_matches if best_intel else 0,
            "days_monitoring": days_monitoring,
            "week_iso": week_iso,
            "destination_index": dest_index or None,
            "departure_heatmap": heatmap,
        }

        try:
            email_trigger(
                db=db,
                email_type=EmailType.WEEKLY_DIGEST,
                user_id=str(user.id),
                context=context,
            )
            sent += 1
        except Exception:
            logger.exception("weekly_digest failed for %s", user.email)

    if passive_users:
        logger.info("weekly_digest: checked %d passive users, sent %d", len(passive_users), sent)
    return sent


if __name__ == "__main__":
    main()
