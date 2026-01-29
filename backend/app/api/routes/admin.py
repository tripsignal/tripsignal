from datetime import datetime, timezone
import os
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models.notification_outbox import NotificationOutbox


router = APIRouter(prefix="/admin", tags=["admin"])


class TestEmailIn(BaseModel):
    signal_id: UUID
    match_id: UUID | None = None
    to_email: EmailStr
    subject: str
    body_text: str


@router.post("/test-email", status_code=201)
def enqueue_test_email(
    payload: TestEmailIn,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if not admin_token:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not configured")

    if not x_admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    email_enabled = os.getenv("ENABLE_EMAIL_NOTIFICATIONS", "false").lower() == "true"
    channel = "email" if email_enabled else "log"

    to_email = payload.to_email if email_enabled else "log"

    row = NotificationOutbox(
        status="pending",
        channel=channel,
        signal_id=payload.signal_id,
        match_id=payload.match_id,
        to_email=to_email,
        subject=payload.subject,
        body_text=payload.body_text,
        next_attempt_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()

    return {"id": str(row.id), "channel": channel, "to_email": to_email}
