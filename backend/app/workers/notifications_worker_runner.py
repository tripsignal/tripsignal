from __future__ import annotations

import logging
import threading
import time

from app.workers import notifications_email_worker, notifications_log_worker

logger = logging.getLogger("notifications_worker_runner")
logging.basicConfig(level=logging.INFO)


def _run_email() -> None:
    # Email worker exposes main(), not run()
    notifications_email_worker.main(once=False)


def _run_log() -> None:
    # Log worker exposes run()
    notifications_log_worker.run(once=False)


def main() -> None:
    logger.info("notifications_worker_runner starting (email + log)")

    t1 = threading.Thread(target=_run_email, name="email-worker", daemon=True)
    t2 = threading.Thread(target=_run_log, name="log-worker", daemon=True)

    t1.start()
    t2.start()

    # Keep the main process alive
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
