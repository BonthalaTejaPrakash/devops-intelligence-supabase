import os
from typing import Optional

import httpx


class NotificationConfigurationError(RuntimeError):
    pass


class NotificationDeliveryError(RuntimeError):
    pass


class ResendNotifier:
    def __init__(self, api_key: str, from_email: str, client: Optional[httpx.Client] = None):
        self.api_key = api_key
        self.from_email = from_email
        self.client = client or httpx.Client(timeout=15)

    @classmethod
    def from_environment(cls) -> "ResendNotifier":
        api_key = os.getenv("RESEND_API_KEY")
        from_email = os.getenv("NOTIFICATION_FROM_EMAIL")
        if not api_key or not from_email:
            raise NotificationConfigurationError(
                "Set RESEND_API_KEY and NOTIFICATION_FROM_EMAIL on the backend."
            )
        return cls(api_key, from_email)

    def send(self, recipient: str, subject: str, text: str, idempotency_key: str) -> str:
        try:
            response = self.client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Idempotency-Key": idempotency_key,
                },
                json={
                    "from": self.from_email,
                    "to": [recipient],
                    "subject": subject,
                    "text": text,
                },
            )
        except httpx.HTTPError as error:
            raise NotificationDeliveryError("Email provider request could not reach Resend.") from error
        if response.status_code not in {200, 201}:
            raise NotificationDeliveryError(
                f"Resend rejected the email request with HTTP {response.status_code}."
            )
        message_id = response.json().get("id")
        if not isinstance(message_id, str) or not message_id:
            raise NotificationDeliveryError("Resend returned an invalid delivery response.")
        return message_id
