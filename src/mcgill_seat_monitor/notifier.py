from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

from .config import SmtpConfig
from .models import Observation


class NotificationError(RuntimeError):
    pass


class SmtpNotifier:
    def __init__(self, config: SmtpConfig) -> None:
        self.config = config

    def _environment(self, name: str, required: bool = True) -> str:
        value = os.environ.get(name, "").strip()
        if required and not value:
            raise NotificationError(f"required environment variable is not set: {name}")
        return value

    def send(self, observation: Observation) -> None:
        sender = self._environment(self.config.from_env)
        recipients = [item.strip() for item in self._environment(self.config.to_env).split(",") if item.strip()]
        username = self._environment(self.config.username_env, required=False)
        password = self._environment(self.config.password_env, required=bool(username))

        message = EmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message["Subject"] = f"Seat available: {observation.target.display_name}"
        seats = "unknown number of" if observation.open_seats is None else str(observation.open_seats)
        message.set_content(
            f"McGill VSB reports {seats} open seat(s) for {observation.target.display_name}.\n\n"
            f"Term: {observation.target.term}\n"
            f"State: {observation.state.value}\n"
            f"CRN/block key: {observation.crn or 'unknown'}\n"
            f"Observed (UTC): {observation.observed_at.isoformat()}\n\n"
            "This service only monitors availability; it does not register you."
        )

        context = ssl.create_default_context()
        try:
            if self.config.security == "ssl":
                with smtplib.SMTP_SSL(self.config.host, self.config.port, timeout=30, context=context) as smtp:
                    if username:
                        smtp.login(username, password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(self.config.host, self.config.port, timeout=30) as smtp:
                    if self.config.security == "starttls":
                        smtp.starttls(context=context)
                    if username:
                        smtp.login(username, password)
                    smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise NotificationError(f"SMTP delivery failed: {exc}") from exc
