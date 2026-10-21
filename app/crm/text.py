"""Helpers every connector needs to turn CRM records into clean, prompt-ready text and dates."""

import html
import re
from datetime import date, datetime

# Keep the prompt a sensible size on deals with a long history: the newest activities matter most.
MAX_ACTIVITIES = 40
MAX_BODY_CHARS = 3000


def to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def to_date(value: str | None) -> date | None:
    parsed = to_datetime(value)
    return parsed.date() if parsed else None


def to_number(value) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<(br|/p|/div|/li)\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


def clip(text: str | None) -> str:
    text = (text or "").strip() or "(no content)"
    return text if len(text) <= MAX_BODY_CHARS else text[:MAX_BODY_CHARS] + " […]"
