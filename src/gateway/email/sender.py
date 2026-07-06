"""Email sending abstraction.

Provides a pluggable email sender.  Currently ships with a console-only sender
for development.  When SMTP settings are configured, it falls through to the
real SMTP sender automatically.
"""

import logging
from abc import ABC, abstractmethod
from email.mime.text import MIMEText

from src.infra.settings import settings

logger = logging.getLogger(__name__)


class EmailSender(ABC):
    """Abstract email sender."""

    @abstractmethod
    def send(self, to: str, subject: str, text: str, html: str = "") -> None:
        ...


class ConsoleEmailSender(EmailSender):
    """Print emails to console — useful for development."""

    def send(self, to: str, subject: str, text: str, html: str = "") -> None:
        logger.info("=== EMAIL (console sender) ===")
        logger.info("To: %s", to)
        logger.info("Subject: %s", subject)
        logger.info("Text:\n%s", text)
        if html:
            logger.info("HTML: (omitted, %d chars)", len(html))
        logger.info("===============================")


class SmtpEmailSender(EmailSender):
    """Send emails via SMTP."""

    def __init__(self, host: str, port: int, user: str, password: str, from_addr: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_addr = from_addr

    def send(self, to: str, subject: str, text: str, html: str = "") -> None:
        import smtplib

        msg = MIMEText(html, "html") if html else MIMEText(text, "plain")
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = to

        try:
            with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
                smtp.ehlo()
                if self.port == 587:
                    smtp.starttls()
                    smtp.ehlo()
                if self.user:
                    smtp.login(self.user, self.password)
                smtp.sendmail(self.from_addr, [to], msg.as_string())
            logger.info("Email sent to %s via SMTP (%s:%d)", to, self.host, self.port)
        except Exception as exc:
            logger.error("Failed to send email to %s via SMTP (%s:%d): %s", to, self.host, self.port, exc)


# -- Convenience helpers ------------------------------------------------------

_INSTANCE: EmailSender | None = None


def get_sender() -> EmailSender:
    """Return the appropriate sender based on current settings."""
    global _INSTANCE
    if _INSTANCE is None:
        if settings.smtp_host:
            _INSTANCE = SmtpEmailSender(
                host=settings.smtp_host,
                port=settings.smtp_port,
                user=settings.smtp_user,
                password=settings.smtp_password,
                from_addr=settings.smtp_from,
            )
            logger.info("Using SMTP email sender (%s:%d)", settings.smtp_host, settings.smtp_port)
        else:
            _INSTANCE = ConsoleEmailSender()
            logger.info("Using console email sender (no SMTP configured)")
    return _INSTANCE


def send_invite_email(email: str, invite_url: str, expires_in_days: int = 7) -> None:
    """Send an invitation email to *email* with the link *invite_url*.

    Raises RuntimeError if SMTP sending fails, so the caller can surface the error.
    """
    subject = "You've been invited to Agent Platform"
    text = (
        f"You've been invited to join Agent Platform.\n\n"
        f"Click the link below to accept the invitation and set up your account:\n{invite_url}\n\n"
        f"This invitation expires in {expires_in_days} day(s).\n"
    )
    html = (
        "<html><body style='font-family: sans-serif; padding: 20px;'>"
        f"<h2>You've been invited to Agent Platform</h2>"
        f"<p>Click the button below to accept the invitation and set up your account:</p>"
        f"<p style='text-align: center; margin: 30px 0;'>"
        f"<a href='{invite_url}' "
        f"style='background: #2563eb; color: #fff; padding: 12px 32px; "
        f"border-radius: 6px; text-decoration: none; display: inline-block;'>"
        f"Accept Invitation</a></p>"
        f"<p style='color: #888; font-size: 0.85rem;'>This invitation expires in {expires_in_days} day(s).</p>"
        f"</body></html>"
    )
    get_sender().send(to=email, subject=subject, text=text, html=html)
