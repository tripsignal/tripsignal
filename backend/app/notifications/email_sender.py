import smtplib
from email.message import EmailMessage

from app.core.config import settings


class EmailSendError(RuntimeError):
    pass


def send_email(to_email: str, subject: str, body: str) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_FROM_EMAIL:
        raise EmailSendError("SMTP is not configured")

    msg = EmailMessage()
    from_name = settings.SMTP_FROM_NAME or ""
    from_email = settings.SMTP_FROM_EMAIL

    if from_name:
        msg["From"] = f"{from_name} <{from_email}>"
    else:
        msg["From"] = from_email

    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as server:
            server.ehlo()
            if settings.SMTP_USE_TLS:
                server.starttls()
                server.ehlo()
            if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        raise EmailSendError(str(e)) from e
