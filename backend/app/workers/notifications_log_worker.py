from __future__ import annotations

import logging
import os
import time
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.models.notification_outbox import NotificationOutbox
from app.workers.notifications_email_worker import mark_sent, mark_failed

logger = logging.getLogger("notifications_log_worker")
logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))

POLL_SECONDS = float(os.getenv("NOTIFICATIONS_POLL_SECONDS", "10"))
BATCH_SIZE = int(os.getenv("OUTBOX_BATCH_SIZE", "25"))


def claim_log_ids(db: Session, limit: int) -> list:
    return db.execute(
        text("""
        SELECT id
        FROM notifications_outbox
        WHERE status = 'queued'
          AND channel = 'log'
          AND next_attempt_at <= now()
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT :limit
        """),
        {"limit": limit},
    ).scalars().all()


def run(once: bool = False) -> None:
    logger.info("notifications_log_worker starting (once=%s)", once)

    while True:
        db = SessionLocal()
        try:
            ids = claim_log_ids(db, BATCH_SIZE)
            logger.info("claim_log_ids returned %s rows", len(ids))

            for row_id in ids:
                try:
                    row = db.get(NotificationOutbox, row_id)
                    if not row:
                        continue

                    logger.info(
                        "LOG NOTIFICATION sent: id=%s subject=%s",
                        str(row.id),
                        row.subject,
                    )

                    mark_sent(db, row)
                    db.commit()

                except Exception as e:
                    db.rollback()
                    logger.exception("log row failed: id=%s", str(row_id))
                    row = db.get(NotificationOutbox, row_id)
                    if row:
                        mark_failed(db, row, f"log worker failed: {e}")
                        db.commit()

            if once:
                logger.info("once=True, exiting")
                return
        finally:
            db.close()

        time.sleep(POLL_SECONDS)
