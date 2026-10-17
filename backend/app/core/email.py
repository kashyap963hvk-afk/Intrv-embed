"""Outbound email. Without SMTP configured, non-production environments log the message instead."""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger("app.email")


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


class EmailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return bool(self._settings.smtp_host and self._settings.mail_from)

    async def send(self, message: OutgoingEmail) -> bool:
        """Send the email; returns False when it could not be delivered (never raises)."""
        settings = self._settings
        if not self.configured:
            if not settings.is_prod:
                log.info("email.dev_outbox", to=message.to, subject=message.subject, body=message.body)
                return True
            log.warning("email.not_configured", to=message.to, subject=message.subject)
            return False

        email = EmailMessage()
        email["From"] = settings.mail_from
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.body)
        try:
            await aiosmtplib.send(
                email,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user or None,
                password=(settings.smtp_password.get_secret_value() or None) if settings.smtp_password else None,
                start_tls=settings.smtp_tls == "starttls",
                use_tls=settings.smtp_tls == "ssl",
                timeout=15,
            )
        except (aiosmtplib.SMTPException, OSError) as exc:
            log.warning("email.send_failed", to=message.to, exc_type=type(exc).__name__, error=str(exc))
            return False
        log.info("email.sent", to=message.to, subject=message.subject)
        return True
