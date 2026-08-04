from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return _ensure_utc(datetime.fromisoformat(normalized))
    except ValueError:
        pass

    try:
        return _ensure_utc(parsedate_to_datetime(text))
    except (TypeError, ValueError, IndexError):
        pass

    for pattern in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_since(value: str | None) -> datetime | None:
    parsed = parse_datetime(value)
    if value and parsed is None:
        raise ValueError(f"Could not parse date: {value}")
    return parsed


def is_on_or_after(value: str | None, since: datetime | None) -> bool:
    if since is None:
        return True
    parsed = parse_datetime(value)
    if parsed is None:
        return False
    return parsed >= since


def api_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

