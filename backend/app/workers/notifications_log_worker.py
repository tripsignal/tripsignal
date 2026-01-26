import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import List

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models.notification_outbox import NotificationOutbox

logger = logging.getLogger("notifications_worker")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

POLL_SECONDS = int(os.getenv("NOTIFICATIONS_POLL_SECONDS", "5"))
BATCH_SIZE = int(os.getenv("NOTIFICATIONS_BATCH_SIZE", "25"))
BACKOFF_SECONDS = [30, 120, 600, 1800, 7200]  # 30s, 2m, 10m, 30m, 2h
MAX_ATTEMPTS = 8
CHAOS_ENABLED = os.getenv("OUTBOX_LOG_WORKER_CHAOS") == "1"
CHAOS_MARKER = "[CHAOS]"

def claim_batch(db: Session, batch_size: int = BATCH_SIZE) -> List[NotificationOutbox]:
    stale_cutoff = func.now() - timedelta(minutes=5)

    stmt = (
        select(NotificationOutbox)
        .where(
            NotificationOutbox.channel == "log",
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
    row.last_error = None
    row.next_attempt_at = datetime.now(timezone.utc)  # keep NOT NULL happy
    row.updated_at = datetime.now(timezone.utc)
    db.flush()



def mark_retry(db: Session, row: NotificationOutbox, err: Exception) -> None:
    row.last_error = str(err)[:2000]
    row.updated_at = datetime.now(timezone.utc)

    if (row.attempts or 0) >= MAX_ATTEMPTS:
        row.status = "dead"
        row.next_attempt_at = datetime.now(timezone.utc)  # keep NOT NULL happy
        db.flush()
        return

    idx = min(max((row.attempts or 1) - 1, 0), len(BACKOFF_SECONDS) - 1)
    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=BACKOFF_SECONDS[idx])
    row.status = "pending"
    db.flush()

def process_row(row: NotificationOutbox) -> None:
    subject = (getattr(row, "subject", "") or "").strip()

    # Chaos test: only fails when OUTBOX_LOG_WORKER_CHAOS=1 AND subject contains [CHAOS]
    if CHAOS_ENABLED and CHAOS_MARKER in subject:
        raise Exception("Intentional chaos failure for retry test")

    body = (getattr(row, "body_text", "") or "")
    preview = (body[:500] + "…") if len(body) > 500 else body

    logger.info(
        "OUTBOX LOG-ONLY: id=%s to=%s subject=%s body=%s",
        row.id,
        getattr(row, "to_email", getattr(row, "to", None)),
        row.subject,
        preview,
    )





def main(once: bool = False) -> None:
    logger.info("notifications_log_worker starting (once=%s)", once)

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
                    except Exception as e:
                        db.rollback()
                        logger.exception("row processing failed: id=%s", row_id)
                        mark_retry(db, row, e)
                        db.commit()

                if once:
                    logger.info("processed batch; exiting (once)")
                    return

        except Exception:
            logger.exception("worker loop crashed; sleeping then retrying")
            time.sleep(2)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    main(once=args.once)
        
