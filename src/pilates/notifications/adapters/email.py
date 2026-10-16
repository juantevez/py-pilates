import logging
import smtplib
from email.message import EmailMessage as MimeMessage

from pilates.notifications.application.ports import EmailMessage, EmailSender

logger = logging.getLogger(__name__)


class SmtpEmailSender(EmailSender):
    def __init__(
        self, host: str, port: int, sender: str, username: str | None = None, password: str | None = None,
        use_tls: bool = False,
    ) -> None:
        self._host, self._port, self._from = host, port, sender
        self._username, self._password, self._use_tls = username, password, use_tls

    def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = self._from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body)
        with smtplib.SMTP(self._host, self._port, timeout=15) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._username:
                smtp.login(self._username, self._password or "")
            smtp.send_message(mime)


class ConsoleEmailSender(EmailSender):
    """Para desarrollo/tests: sólo loguea el correo."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)
        logger.info("EMAIL to=%s subject=%s\n%s", message.to, message.subject, message.body)
