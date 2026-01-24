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

# simple retry schedule
BACKOFF_SECONDS = [30, 120, 600, 1800, 7200]  # 30s, 2m, 10m, 30m, 2h


def claim_batch(db: Session, batch_size: int = BATCH_SIZE) -> List[NotificationOutbox]:
    stmt = (
        select(NotificationOutbox)
        .where(
            NotificationOutbox.status == "pending",
            NotificationOutbox.channel == "log",
            NotificationOutbox.next_attempt_at <= func.now(),
        )
        .order_by(NotificationOutbox.created_at.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    rows = list(db.execute(stmt).scalars().all())
    return rows


def mark_sent(db: Session, row: NotificationOutbox):
    row.status = "sent"
    row.last_error = None
    row.updated_at = datetime.now(timezone.utc)
    db.commit()


def mark_retry(db: Session, row: NotificationOutbox, err: Exception):
    row.attempts = (row.attempts or 0) + 1
    idx = min(row.attempts - 1, len(BACKOFF_SECONDS) - 1)
    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=BACKOFF_SECONDS[idx])
    row.status = "pending"
    row.last_error = str(err)[:2000]
    row.updated_at = datetime.now(timezone.utc)
    db.commit()


def process_row(db: Session, row: NotificationOutbox):
    # LOG-ONLY send
    body = row.body_text or ""
    preview = (body[:500] + "…") if len(body) > 500 else body

    logger.info(
        "OUTBOX LOG-ONLY: id=%s to=%s subject=%s body=%s",
        row.id,
        row.to_email,
        row.subject,
        preview,
    )

    mark_sent(db, row)


def main(once: bool = False):
    logger.info("notifications_log_worker starting (once=%s)", once)

    while True:
        try:
            with next(get_db()) as db:
                rows = claim_batch(db)

                if not rows:
                    if once:
                        logger.info("no eligible rows; exiting (once)")
                        return
                    time.sleep(POLL_SECONDS)
                    continue

                for row in rows:
                    try:
                        process_row(db, row)
                    except Exception as e:
                        logger.exception("row processing failed: id=%s", row.id)
                        mark_retry(db, row, e)

            if once:
                logger.info("processed batch; exiting (once)")
                return

        except KeyboardInterrupt:
            logger.info("worker interrupted; exiting")
            return
        except Exception as e:
            logger.exception("Worker loop error: %s", e)
            if once:
                raise
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    main(once=args.once)
