from __future__ import annotations

import os
from datetime import datetime, time
from typing import Any

import httpx

from .common import IST, Store, now_ist


def send(
    recipient: dict[str, Any] | str,
    subject: str,
    html: str,
    when_ist: datetime | None,
    *,
    force_immediate: bool = False,
) -> str:
    current = when_ist or now_ist()
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    current = current.astimezone(IST)
    recipient_id = recipient["id"] if isinstance(recipient, dict) else recipient
    api_key = os.environ.get("BREVO_API_KEY", "")
    sender_email = os.environ.get("SENDER_EMAIL", "")
    if not api_key or not sender_email:
        raise RuntimeError("BREVO_API_KEY and SENDER_EMAIL are required to send")
    payload: dict[str, Any] = {
        "sender": {"name": "Daily News Briefing", "email": sender_email},
        "to": [{"email": recipient_id}],
        "subject": subject,
        "htmlContent": html,
    }
    send_at = datetime.combine(current.date(), time(8, 0), tzinfo=IST)
    if not force_immediate and current < send_at:
        payload["scheduledAt"] = send_at.isoformat()

    store = Store()
    log_base = {
        "date": current.date().isoformat(),
        "recipient": recipient_id,
        "message_id": "",
    }
    try:
        response = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if not 200 <= response.status_code < 300:
            detail = response.text[:1000]
            store.upsert(
                "send_log",
                {**log_base, "status": "failed", "detail": detail},
                on=("date", "recipient"),
            )
            raise RuntimeError(f"Brevo returned {response.status_code}: {detail}")
        result = response.json() if response.content else {}
        message_id = str(result.get("messageId", ""))
        store.upsert(
            "send_log",
            {
                **log_base,
                "message_id": message_id,
                "status": "scheduled" if "scheduledAt" in payload else "sent",
                "detail": "",
            },
            on=("date", "recipient"),
        )
        return message_id
    except httpx.HTTPError as exc:
        store.upsert(
            "send_log",
            {**log_base, "status": "failed", "detail": str(exc)[:1000]},
            on=("date", "recipient"),
        )
        raise RuntimeError(f"Brevo request failed: {exc}") from exc
