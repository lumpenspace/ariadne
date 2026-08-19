from __future__ import annotations

import re
from collections.abc import Iterable


_STATUS_URL_RE = re.compile(
    r"https?://(?:mobile\.)?(?:x|twitter)\.com/"
    r"(?:#!/)?(?P<username>[^/\s?#]+)/(?P<status>status|statuses)/(?P<id>\d{1,19})",
    re.IGNORECASE,
)
_WEB_STATUS_RE = re.compile(
    r"https?://(?:mobile\.)?(?:x|twitter)\.com/i/web/status/(?P<id>\d{1,19})",
    re.IGNORECASE,
)
_BARE_ID_RE = re.compile(r"(?<!\d)(\d{1,19})(?!\d)")


def extract_tweet_ids(values: Iterable[str]) -> list[str]:
    ids: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        ids.extend(match.group("id") for match in _WEB_STATUS_RE.finditer(text))
        ids.extend(match.group("id") for match in _STATUS_URL_RE.finditer(text))
        scrubbed = _WEB_STATUS_RE.sub(" ", text)
        scrubbed = _STATUS_URL_RE.sub(" ", scrubbed)
        if scrubbed.strip().isdigit():
            ids.append(scrubbed.strip())
            continue
        for token in re.split(r"[\s,]+", scrubbed):
            token = token.strip()
            if token and _BARE_ID_RE.fullmatch(token):
                ids.append(token)
    return unique_preserve_order(ids)


def tweet_id_from_url(value: str) -> str | None:
    ids = extract_tweet_ids([value])
    return ids[0] if ids else None


def extract_tweet_url_map(values: Iterable[str]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for value in values:
        text = str(value)
        for match in _STATUS_URL_RE.finditer(text):
            tweet_id = match.group("id")
            if tweet_id not in urls:
                urls[tweet_id] = _canonical_url(match.group("username"), tweet_id)
    return urls


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _canonical_url(username: str, tweet_id: str) -> str:
    return f"https://x.com/{username.strip('@')}/status/{tweet_id}"
