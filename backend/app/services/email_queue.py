"""
Email Queue Service — rate-limited, prioritized, retry-aware email delivery.

All emails flow through this queue:
  1. ``enqueue()`` inserts a row with priority + rendered HTML.
  2. ``drain()`` pulls eligible rows (queued + retryable) ordered by priority,
     sends via Resend (single or batch), and handles retries with backoff.

Priority levels:
  1 = critical  (transactional, billing — welcome, payment failed, etc.)
  2 = high      (alerts — match alerts, major drops)
  3 = low       (engagement, upsell — trial expiring, inactive, etc.)

Rate limiting:
  Enforces a configurable sends-per-second ceiling (default 2/sec for Resend
  free tier, 10/sec for pro). Uses a token bucket in the drain loop.

Retry policy:
  Failed sends retry up to 3 times with exponential backoff:
    attempt 1 → retry after 1 min
    attempt 2 → retry after 5 min
    attempt 3 → retry after 30 min
  After max attempts, status becomes "dead" (visible in admin, manually retryable).

Batch API:
  When multiple queued emails exist, groups them into batches of up to 100
  and sends via Resend's batch endpoint for fewer API calls.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models.email_queue import EmailQueue
from app.db.models.email_log import EmailLog
from app.db.models.user import User

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = "Trip Signal <hello@tripsignal.ca>"

# Rate limit: requests per second (Resend free = 2/s, pro = 10/s)
RATE_LIMIT_PER_SEC = int(os.getenv("EMAIL_RATE_LIMIT_PER_SEC", "2"))

# Batch size for Resend batch API (max 100 per call)
BATCH_SIZE = int(os.getenv("EMAIL_BATCH_SIZE", "100"))

# Max emails to process per drain cycle
DRAIN_LIMIT = int(os.getenv("EMAIL_DRAIN_LIMIT", "500"))

# Retry backoff schedule (seconds): attempt 1, 2, 3
RETRY_DELAYS = [60, 300, 1800]  # 1 min, 5 min, 30 min


# ── Priority constants ───────────────────────────────────────────────────────

PRIORITY_CRITICAL = 1   # transactional, billing
PRIORITY_HIGH = 2       # alerts
PRIORITY_LOW = 3        # engagement, upsell

CATEGORY_PRIORITY = {
    "transactional": PRIORITY_CRITICAL,
    "billing": PRIORITY_CRITICAL,
    "alert": PRIORITY_HIGH,
    "engagement": PRIORITY_LOW,
    "upsell": PRIORITY_LOW,
}


# ── Enqueue ──────────────────────────────────────────────────────────────────

def enqueue(
    db: Session,
    *,
    to_email: str,
    subject: str,
    html_body: str,
    email_log_id: uuid.UUID | None = None,
    email_type: str | None = None,
    category: str | None = None,
    user_id: str | uuid.UUID | None = None,
    metadata: dict | None = None,
) -> uuid.UUID:
    """Insert an email into the queue for async delivery.

    Returns the queue row ID.
    """
    priority = CATEGORY_PRIORITY.get(category or "", PRIORITY_HIGH)

    row = EmailQueue(
        priority=priority,
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        email_log_id=email_log_id,
        email_type=email_type,
        user_id=uuid.UUID(str(user_id)) if user_id else None,
        metadata_json=metadata,
        status="queued",
    )
    db.add(row)
    db.flush()
    logger.info(
        "email_queue: enqueued %s → %s (priority=%d, type=%s)",
        row.id, to_email, priority, email_type,
    )
    return row.id


# ── Drain ────────────────────────────────────────────────────────────────────

def drain(db: Session) -> dict:
    """Process queued and retryable emails with rate limiting.

    Returns stats dict: {sent, failed, dead, skipped, batches, elapsed_ms}.
    """
    from app.core.config import settings

    now = datetime.now(timezone.utc)
    stats = {"sent": 0, "failed": 0, "dead": 0, "skipped": 0, "batches": 0, "elapsed_ms": 0}
    start = time.monotonic()

    # Fetch eligible rows: queued OR failed-with-retry-ready
    rows = db.execute(
        select(EmailQueue).where(
            (
                (EmailQueue.status == "queued")
                | (
                    (EmailQueue.status == "failed")
                    & (EmailQueue.attempts < EmailQueue.max_attempts)
                    & (
                        (EmailQueue.next_retry_at.is_(None))
                        | (EmailQueue.next_retry_at <= now)
                    )
                )
            )
        ).order_by(
            EmailQueue.priority.asc(),
            EmailQueue.created_at.asc(),
        ).limit(DRAIN_LIMIT)
    ).scalars().all()

    if not rows:
        return stats

    logger.info("email_queue: draining %d emails", len(rows))

    if settings.EMAIL_DRY_RUN:
        # Dry-run: mark all as sent without calling Resend
        for row in rows:
            row.status = "sent"
            row.sent_at = now
            row.attempts += 1
            row.last_attempt_at = now
            row.provider_message_id = "dry_run"
            _sync_email_log(db, row, "sent", "dry_run")
            stats["sent"] += 1
        db.commit()
        stats["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        return stats

    if not RESEND_API_KEY:
        logger.warning("email_queue: RESEND_API_KEY not set, skipping drain")
        return stats

    # Group into batches for the batch API
    batches = [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]

    for batch in batches:
        if len(batch) == 1:
            # Single email — use standard endpoint
            row = batch[0]
            _send_single(db, row, now, stats)
        else:
            # Multiple emails — use batch endpoint
            _send_batch(db, batch, now, stats)
        stats["batches"] += 1

        # Rate limit between batches: sleep to respect per-second limit
        if RATE_LIMIT_PER_SEC > 0 and stats["batches"] < len(batches):
            # For batch API, each batch counts as 1 API call
            time.sleep(1.0 / RATE_LIMIT_PER_SEC)

    stats["elapsed_ms"] = int((time.monotonic() - start) * 1000)
    logger.info(
        "email_queue: drain complete — sent=%d failed=%d dead=%d batches=%d elapsed=%dms",
        stats["sent"], stats["failed"], stats["dead"], stats["batches"], stats["elapsed_ms"],
    )
    return stats


# ── Single send ──────────────────────────────────────────────────────────────

def _send_single(db: Session, row: EmailQueue, now: datetime, stats: dict) -> None:
    """Send a single email via Resend's standard endpoint."""
    row.attempts += 1
    row.last_attempt_at = now
    row.status = "sending"
    db.flush()

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": FROM_EMAIL,
                "to": [row.to_email],
                "subject": row.subject,
                "html": row.html_body,
            },
            timeout=15,
        )

        if resp.status_code in (200, 201):
            message_id = ""
            try:
                message_id = resp.json().get("id", "")
            except Exception:
                pass
            row.status = "sent"
            row.sent_at = now
            row.provider_message_id = message_id or "sent"
            _sync_email_log(db, row, "sent", message_id)
            stats["sent"] += 1
        elif resp.status_code == 429:
            # Rate limited — back off, don't count as attempt
            row.attempts -= 1
            row.status = "queued"
            row.next_retry_at = now + timedelta(seconds=30)
            stats["skipped"] += 1
            logger.warning("email_queue: rate limited by Resend, backing off 30s")
        else:
            _handle_failure(db, row, now, f"HTTP {resp.status_code}: {resp.text[:200]}", stats)
    except Exception as e:
        _handle_failure(db, row, now, str(e), stats)

    db.commit()


# ── Batch send ───────────────────────────────────────────────────────────────

def _send_batch(db: Session, batch: list[EmailQueue], now: datetime, stats: dict) -> None:
    """Send multiple emails via Resend's batch endpoint."""
    for row in batch:
        row.attempts += 1
        row.last_attempt_at = now
        row.status = "sending"
    db.flush()

    payload = [
        {
            "from": FROM_EMAIL,
            "to": [row.to_email],
            "subject": row.subject,
            "html": row.html_body,
        }
        for row in batch
    ]

    try:
        resp = requests.post(
            "https://api.resend.com/emails/batch",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )

        if resp.status_code in (200, 201):
            # Batch response: {"data": [{"id": "msg_id"}, ...]}
            results = []
            try:
                data = resp.json()
                results = data.get("data", [])
            except Exception:
                pass

            for i, row in enumerate(batch):
                msg_id = results[i].get("id", "") if i < len(results) else ""
                row.status = "sent"
                row.sent_at = now
                row.provider_message_id = msg_id or "batch_sent"
                _sync_email_log(db, row, "sent", msg_id)
                stats["sent"] += 1
        elif resp.status_code == 429:
            # Rate limited — re-queue all
            for row in batch:
                row.attempts -= 1
                row.status = "queued"
                row.next_retry_at = now + timedelta(seconds=30)
                stats["skipped"] += 1
            logger.warning("email_queue: batch rate limited by Resend, backing off 30s")
        else:
            error_msg = f"Batch HTTP {resp.status_code}: {resp.text[:200]}"
            for row in batch:
                _handle_failure(db, row, now, error_msg, stats)

    except Exception as e:
        error_msg = str(e)
        for row in batch:
            _handle_failure(db, row, now, error_msg, stats)

    db.commit()


# ── Failure handling with exponential backoff ────────────────────────────────

def _handle_failure(
    db: Session, row: EmailQueue, now: datetime, error_msg: str, stats: dict,
) -> None:
    """Mark a queue row as failed or dead depending on attempt count."""
    row.error_message = error_msg[:500]

    # Append to retry history in metadata_json
    meta = row.metadata_json or {}
    history = meta.get("retry_history", [])
    history.append({
        "attempt": row.attempts,
        "error": error_msg[:300],
        "at": now.isoformat(),
    })
    meta["retry_history"] = history
    row.metadata_json = meta

    if row.attempts >= row.max_attempts:
        row.status = "dead"
        _sync_email_log(db, row, "failed", None)
        stats["dead"] += 1
        logger.error(
            "email_queue: DEAD after %d attempts — %s → %s: %s",
            row.attempts, row.email_type, row.to_email, error_msg[:100],
        )
    else:
        delay_idx = min(row.attempts - 1, len(RETRY_DELAYS) - 1)
        delay = RETRY_DELAYS[delay_idx]
        row.status = "failed"
        row.next_retry_at = now + timedelta(seconds=delay)
        stats["failed"] += 1
        logger.warning(
            "email_queue: attempt %d/%d failed — %s → %s, retry in %ds: %s",
            row.attempts, row.max_attempts, row.email_type, row.to_email,
            delay, error_msg[:100],
        )


# ── Sync queue status back to email_log ──────────────────────────────────────

def _sync_email_log(
    db: Session, row: EmailQueue, status: str, message_id: str | None,
) -> None:
    """Update the linked email_log row when queue delivery succeeds or fails."""
    if not row.email_log_id:
        return
    try:
        values = {"status": status}
        if status == "sent":
            values["sent_at"] = row.sent_at
            values["provider_message_id"] = message_id
        db.execute(
            update(EmailLog)
            .where(EmailLog.id == row.email_log_id)
            .values(**values)
        )
    except Exception:
        logger.exception("email_queue: failed to sync email_log %s", row.email_log_id)


# ── Admin utilities ──────────────────────────────────────────────────────────

def get_queue_stats(db: Session) -> dict:
    """Return current queue statistics for admin dashboard."""
    now = datetime.now(timezone.utc)

    # Status counts
    counts = db.execute(
        select(EmailQueue.status, func.count(EmailQueue.id))
        .group_by(EmailQueue.status)
    ).all()
    status_counts = {row[0]: row[1] for row in counts}

    # Oldest queued
    oldest = db.execute(
        select(EmailQueue.created_at)
        .where(EmailQueue.status == "queued")
        .order_by(EmailQueue.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()

    oldest_age_sec = None
    if oldest:
        oldest_age_sec = int((now - oldest).total_seconds())

    # Throughput: emails sent in last 5 minutes
    five_min_ago = now - timedelta(minutes=5)
    recent_sent = db.execute(
        select(func.count(EmailQueue.id))
        .where(EmailQueue.status == "sent", EmailQueue.sent_at >= five_min_ago)
    ).scalar() or 0
    throughput_per_sec = round(recent_sent / 300, 1) if recent_sent > 0 else 0

    # Estimate drain time
    queued = status_counts.get("queued", 0)
    est_drain_sec = None
    if queued > 0 and RATE_LIMIT_PER_SEC > 0:
        # With batch API, effective rate is BATCH_SIZE * RATE_LIMIT_PER_SEC
        effective_rate = BATCH_SIZE * RATE_LIMIT_PER_SEC
        est_drain_sec = max(1, int(queued / effective_rate))

    # Bounce/complaint counts (last 7 days) from email_log
    seven_days_ago = now - timedelta(days=7)
    bounced_7d = db.execute(
        select(func.count(EmailLog.id))
        .where(EmailLog.bounced_at >= seven_days_ago)
    ).scalar() or 0
    complained_7d = db.execute(
        select(func.count(EmailLog.id))
        .where(EmailLog.complained_at >= seven_days_ago)
    ).scalar() or 0

    return {
        "queued": status_counts.get("queued", 0),
        "sending": status_counts.get("sending", 0),
        "sent": status_counts.get("sent", 0),
        "failed": status_counts.get("failed", 0),
        "dead": status_counts.get("dead", 0),
        "bounced_7d": bounced_7d,
        "complained_7d": complained_7d,
        "oldest_queued_age_sec": oldest_age_sec,
        "throughput_per_sec": throughput_per_sec,
        "est_drain_sec": est_drain_sec,
        "rate_limit_per_sec": RATE_LIMIT_PER_SEC,
        "batch_size": BATCH_SIZE,
    }


def retry_dead(db: Session) -> int:
    """Reset all dead emails back to queued for one more attempt."""
    result = db.execute(
        update(EmailQueue)
        .where(EmailQueue.status == "dead")
        .values(
            status="queued",
            attempts=0,
            next_retry_at=None,
            error_message=None,
        )
    )
    db.commit()
    count = result.rowcount
    if count:
        logger.info("email_queue: retried %d dead emails", count)
    return count


def retry_by_ids(db: Session, ids: list[str]) -> int:
    """Reset specific failed/dead emails back to queued."""
    uuids = [uuid.UUID(i) for i in ids]
    result = db.execute(
        update(EmailQueue)
        .where(
            EmailQueue.id.in_(uuids),
            EmailQueue.status.in_(["failed", "dead"]),
        )
        .values(
            status="queued",
            attempts=0,
            next_retry_at=None,
            error_message=None,
        )
    )
    db.commit()
    count = result.rowcount
    if count:
        logger.info("email_queue: retried %d selected emails", count)
    return count


def pause_queue(db: Session) -> int:
    """Pause all queued emails by setting status to 'paused'."""
    result = db.execute(
        update(EmailQueue)
        .where(EmailQueue.status == "queued")
        .values(status="paused")
    )
    db.commit()
    return result.rowcount


def resume_queue(db: Session) -> int:
    """Resume paused emails."""
    result = db.execute(
        update(EmailQueue)
        .where(EmailQueue.status == "paused")
        .values(status="queued")
    )
    db.commit()
    return result.rowcount


def flush_queue(db: Session) -> int:
    """Delete all queued/paused emails (sent/dead preserved for audit)."""
    from sqlalchemy import delete
    result = db.execute(
        delete(EmailQueue).where(EmailQueue.status.in_(["queued", "paused"]))
    )
    db.commit()
    return result.rowcount


def get_recent_queue_items(db: Session, limit: int = 50, status: str = "", search: str = "") -> list[dict]:
    """Return recent queue items for admin list view."""
    query = (
        select(
            EmailQueue,
            User.first_name,
            EmailLog.bounce_type,
            EmailLog.bounced_at,
            EmailLog.complaint_type,
            EmailLog.complained_at,
        )
        .outerjoin(User, EmailQueue.user_id == User.id)
        .outerjoin(EmailLog, EmailQueue.email_log_id == EmailLog.id)
        .order_by(EmailQueue.created_at.desc())
    )
    if status:
        query = query.where(EmailQueue.status == status)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(EmailQueue.to_email.ilike(f"%{escaped}%"))
    query = query.limit(limit)

    rows = db.execute(query).all()
    return [
        {
            "id": str(r.EmailQueue.id),
            "priority": r.EmailQueue.priority,
            "to_email": r.EmailQueue.to_email,
            "subject": r.EmailQueue.subject,
            "email_type": r.EmailQueue.email_type,
            "status": r.EmailQueue.status,
            "attempts": r.EmailQueue.attempts,
            "max_attempts": r.EmailQueue.max_attempts,
            "error_message": r.EmailQueue.error_message,
            "created_at": r.EmailQueue.created_at.isoformat() if r.EmailQueue.created_at else None,
            "sent_at": r.EmailQueue.sent_at.isoformat() if r.EmailQueue.sent_at else None,
            "next_retry_at": r.EmailQueue.next_retry_at.isoformat() if r.EmailQueue.next_retry_at else None,
            "provider_message_id": r.EmailQueue.provider_message_id,
            "user_first_name": r.first_name,
            "bounce_type": r.bounce_type,
            "bounced_at": r.bounced_at.isoformat() if r.bounced_at else None,
            "complaint_type": r.complaint_type,
            "complained_at": r.complained_at.isoformat() if r.complained_at else None,
            "retry_history": (r.EmailQueue.metadata_json or {}).get("retry_history", []),
        }
        for r in rows
    ]
