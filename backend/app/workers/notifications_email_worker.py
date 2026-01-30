import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import List

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.db.models.notification_outbox import NotificationOutbox
from app.notifications.email_sender import send_email, EmailSendError


logger = logging.getLogger("notifications_email_worker")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

POLL_SECONDS = int(os.getenv("NOTIFICATIONS_POLL_SECONDS", "5"))
BATCH_SIZE = int(os.getenv("NOTIFICATIONS_BATCH_SIZE", "25"))
BACKOFF_SECONDS = [30, 120, 600, 1800, 7200]  # 30s, 2m, 10m, 30m, 2h
MAX_ATTEMPTS = 8


def enqueue_admin_alert(db: Session, failed_row: NotificationOutbox):
    """
    Enqueue a log-only admin alert when a notification becomes terminal-failed.
    """
    try:
        # Prevent infinite loops (never alert on admin alerts)
        if (failed_row.to_email or "").strip() == "admin":
            return

        admin_email = settings.admin_email or "ADMIN_EMAIL_NOT_SET"

        subject = f"ADMIN ALERT: notification failed ({failed_row.id})"
        body = (
            f"ADMIN_EMAIL: {admin_email}\n\n"
            f"id: {failed_row.id}\n"
            f"channel: {failed_row.channel}\n"
            f"to_email: {failed_row.to_email}\n"
            f"subject: {failed_row.subject}\n"
            f"attempts: {failed_row.attempts}\n"
            f"last_error: {failed_row.last_error}\n"
        )

        alert_row = NotificationOutbox(
            status="pending",
            channel="log",
            to_email="admin",
            subject=subject,
            body_text=body,
            signal_id=failed_row.signal_id,
            match_id=failed_row.match_id,
        )

        db.add(alert_row)
        db.flush()

    except Exception as e:
        logger.error(f"[ADMIN ALERT FAILED] {e}")


def claim_batch(db: Session, batch_size: int = BATCH_SIZE) -> List[NotificationOutbox]:
    stale_cutoff = func.now() - timedelta(minutes=5)

    stmt = (
        select(NotificationOutbox)
        .where(
            NotificationOutbox.channel == "email",
            NotificationOutbox.next_attempt_at <= func.now(),
            (
                (NotificationOutbox.status == "pending")
                | (
                    (NotificationOutbox.status == "sending")
                    & (NotificationOutbox.updated_at < stale_cutoff)
                )
            ),
        )
        .order_by(NotificationOutbox.created_at.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )

    rows = list(db.execute(stmt).scalars().all())

    now = datetime.now(timezone.utc)
    for row in rows:
        row.status = "sending"
        row.attempts = (row.attempts or 0) + 1
        row.updated_at = now

    db.commit()
    return rows


def mark_sent(db: Session, row: NotificationOutbox) -> None:
    row.status = "sent"
    row.sent_at = datetime.now(timezone.utc)
    row.last_error = None
    row.next_attempt_at = None
    row.updated_at = datetime.now(timezone.utc)
    db.flush()


def mark_failed(db: Session, row: NotificationOutbox, reason: str) -> None:
    # Terminal state: DB requires next_attempt_at = NULL for dead/sent.
    row.status = "dead"
    row.last_error = reason[:2000]
    row.next_attempt_at = None
    row.updated_at = datetime.now(timezone.utc)

    # Flush the terminal row FIRST so constraints are satisfied.
    db.flush()

    # Now enqueue the admin alert (safe: row is already terminal-valid).
    enqueue_admin_alert(db, row)


def mark_retry(db: Session, row: NotificationOutbox, err: Exception) -> None:
    row.last_error = str(err)[:2000]
    row.updated_at = datetime.now(timezone.utc)

    if (row.attempts or 0) >= MAX_ATTEMPTS:
        row.status = "dead"
        row.next_attempt_at = None  # terminal state
        db.flush()
        return

    idx = min(max((row.attempts or 1) - 1, 0), len(BACKOFF_SECONDS) - 1)
    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=BACKOFF_SECONDS[idx])
    row.status = "pending"
    db.flush()


def process_row(row: NotificationOutbox) -> None:
    """
    Safe-by-default behavior:
    - If email is disabled, do NOT attempt SMTP.
      Just log what would have been sent and return successfully.
    - If enabled, send via SMTP.
    """
    to_email = (row.to_email or "").strip()
    subject = (row.subject or "").strip()
    body = row.body_text or ""

    # SAFE MODE (default): don't touch SMTP
    if not settings.ENABLE_EMAIL_NOTIFICATIONS:
        logger.info(
            "OUTBOX EMAIL DISABLED (log-only): id=%s to=%s subject=%s",
            row.id,
            to_email,
            subject,
        )
        return

    # REAL MODE
    send_email(to_email=to_email, subject=subject, body=body)


def main(once: bool = False) -> None:
    logger.info("notifications_email_worker starting (once=%s)", once)

    while True:
        try:
            with next(get_db()) as db:
                rows = claim_batch(db)
                logger.info("claim_batch returned %d rows", len(rows))

                if not rows:
                    if once:
                        logger.info("no eligible rows; exiting (once)")
                        return
                    time.sleep(POLL_SECONDS)
                    continue

                for row in rows:
                    row_id = row.id
                    try:
                        process_row(row)
                        mark_sent(db, row)
                        db.commit()
                        logger.info("email outbox row completed id=%s to=%s", row_id, row.to_email)

                    except EmailSendError as e:
                        # EmailSendError now represents "real" send failures only
                        db.rollback()
                        msg = str(e)

                        logger.warning(
                            "email send failed (will retry): id=%s err=%s",
                            row_id,
                            msg,
                        )
                        mark_retry(db, row, e)
                        db.commit()

                    except Exception as e:
                        db.rollback()
                        logger.exception("row processing failed: id=%s", row_id)
                        mark_retry(db, row, e)
                        db.commit()

        except Exception:
            logger.exception("worker loop crashed; sleeping then retrying")
            time.sleep(2)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    main(once=args.once)

