"""Extra unauthenticated / low-friction tweet sources.

Both clients satisfy the :class:`~ariadne.reconstruct.TweetFetcher` protocol
(``get_posts(ids) -> FetchResult``) and additionally expose ``get_user_posts``
for timeline enumeration, so each can serve as both a branch-fetcher (resolving
reply/quote parents, including other people's tweets) and a target source.

- :class:`CommunityArchiveClient` — the Community Archive
  (community-archive.org), a public pool of user-donated Twitter exports served
  as a PostgREST API. No key required: the anon key is public by design. Its
  rows carry the reply and quote graph natively, so it completes threads whose
  parents were authored by anyone in the archive.
- :class:`TwitterApiIoClient` — twitterapi.io, a paid pay-as-you-go gateway.
  Requires the caller's API key.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .fetch import FetchResult
from .ids import unique_preserve_order
from .models import Tweet, TweetRef
from .timeutil import parse_datetime

# The Community Archive anon key is public (role "anon", published in the
# project's llms.txt); embedding it needs no secret handling. Override via
# COMMUNITY_ARCHIVE_KEY / COMMUNITY_ARCHIVE_URL if the project rotates them.
COMMUNITY_ARCHIVE_URL = "https://fabxmporizzqflnftavs.supabase.co/rest/v1"
COMMUNITY_ARCHIVE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZhYnhtcG9yaXp6cWZsbmZ0YXZzIiwicm9sZSI6ImFub24i"
    "LCJpYXQiOjE3MjIyNDQ5MTIsImV4cCI6MjAzNzgyMDkxMn0."
    "UIEJiUNkLsW28tBHmG-RQDW-I5JNlJLt62CSk9D_qG8"
)

_CA_COLUMNS = (
    "tweet_id,account_id,username,account_display_name,created_at,full_text,"
    "reply_to_tweet_id,reply_to_user_id,reply_to_username,quoted_tweet_id,"
    "conversation_id,favorite_count,retweet_count"
)


def _get_json(url: str, headers: dict[str, str], timeout: float) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


class CommunityArchiveClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        anon_key: str | None = None,
        timeout: float = 25.0,
        page_size: int = 200,
    ) -> None:
        self.base_url = (base_url or os.environ.get("COMMUNITY_ARCHIVE_URL") or COMMUNITY_ARCHIVE_URL).rstrip("/")
        self.anon_key = anon_key or os.environ.get("COMMUNITY_ARCHIVE_KEY") or COMMUNITY_ARCHIVE_ANON_KEY
        self.timeout = timeout
        self.page_size = page_size

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.anon_key}",
            "Accept": "application/json",
            "User-Agent": "ariadne",
        }

    def get_posts(self, ids: list[str]) -> FetchResult:
        result = FetchResult()
        wanted = [i for i in unique_preserve_order(ids) if i.isdigit()]
        for start in range(0, len(wanted), 100):
            batch = wanted[start : start + 100]
            id_list = ",".join(batch)
            url = f"{self.base_url}/enriched_tweets?select={_CA_COLUMNS}&tweet_id=in.({id_list})"
            try:
                rows = _get_json(url, self._headers(), self.timeout)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                for tweet_id in batch:
                    result.errors[tweet_id] = f"community archive request failed: {exc}"
                continue
            for row in rows if isinstance(rows, list) else []:
                result.tweets.append(_tweet_from_ca_row(row))
        return result

    def get_user_posts(self, username: str, *, since=None, max_tweets: int = 3200) -> FetchResult:
        result = FetchResult()
        handle = username.strip("@")
        offset = 0
        since_iso = _since_iso(since)
        while offset < max_tweets:
            params = {
                "select": _CA_COLUMNS,
                "username": f"ilike.{handle}",
                "order": "created_at.desc",
                "limit": str(min(self.page_size, max_tweets - offset)),
                "offset": str(offset),
            }
            if since_iso:
                params["created_at"] = f"gte.{since_iso}"
            url = f"{self.base_url}/enriched_tweets?{urllib.parse.urlencode(params)}"
            try:
                rows = _get_json(url, self._headers(), self.timeout)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                result.errors[handle] = f"community archive timeline failed: {exc}"
                break
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                result.tweets.append(_tweet_from_ca_row(row))
            if len(rows) < self.page_size:
                break
            offset += len(rows)
        return result

    def has_user(self, username: str) -> bool:
        handle = username.strip("@")
        url = (
            f"{self.base_url}/all_account?select=username&username=ilike.{urllib.parse.quote(handle)}&limit=1"
        )
        try:
            rows = _get_json(url, self._headers(), self.timeout)
        except (urllib.error.URLError, TimeoutError, ValueError):
            return False
        return isinstance(rows, list) and len(rows) > 0


def _tweet_from_ca_row(row: dict[str, Any]) -> Tweet:
    tweet_id = str(row["tweet_id"])
    username = _optional_str(row.get("username"))
    refs: list[TweetRef] = []
    reply_to = _optional_str(row.get("reply_to_tweet_id"))
    if reply_to:
        refs.append(TweetRef("replied_to", reply_to))
    quoted = _optional_str(row.get("quoted_tweet_id"))
    if quoted:
        refs.append(TweetRef("quoted", quoted))
    return Tweet(
        id=tweet_id,
        text=str(row.get("full_text") or ""),
        author_id=_optional_str(row.get("account_id")),
        username=username,
        name=_optional_str(row.get("account_display_name")),
        created_at=_optional_str(row.get("created_at")),
        conversation_id=_optional_str(row.get("conversation_id")),
        in_reply_to_id=reply_to,
        in_reply_to_user_id=_optional_str(row.get("reply_to_user_id")),
        in_reply_to_username=_optional_str(row.get("reply_to_username")),
        referenced_tweets=refs,
        source="community-archive",
        url=f"https://x.com/{username}/status/{tweet_id}" if username else None,
        raw=row,
    )


class TwitterApiIoClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.twitterapi.io",
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key, "Accept": "application/json", "User-Agent": "ariadne"}

    def get_posts(self, ids: list[str]) -> FetchResult:
        result = FetchResult()
        wanted = unique_preserve_order(ids)
        for start in range(0, len(wanted), 100):
            batch = wanted[start : start + 100]
            url = f"{self.base_url}/twitter/tweets?tweet_ids={','.join(batch)}"
            try:
                payload = _get_json(url, self._headers(), self.timeout)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                for tweet_id in batch:
                    result.errors[tweet_id] = f"twitterapi.io request failed: {exc}"
                continue
            for item in _iter_tapio_tweets(payload):
                result.tweets.append(_tweet_from_tapio(item))
        return result

    def get_user_posts(self, username: str, *, since=None, max_pages: int | None = None) -> FetchResult:
        result = FetchResult()
        handle = username.strip("@")
        cursor: str | None = None
        pages = 0
        while True:
            params = {"userName": handle}
            if cursor:
                params["cursor"] = cursor
            url = f"{self.base_url}/twitter/user/last_tweets?{urllib.parse.urlencode(params)}"
            try:
                payload = _get_json(url, self._headers(), self.timeout)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                result.errors[handle] = f"twitterapi.io timeline failed: {exc}"
                break
            if isinstance(payload, dict) and payload.get("status") == "error":
                result.errors[handle] = str(payload.get("message") or "twitterapi.io returned an error")
                break
            for item in _iter_tapio_tweets(payload):
                result.tweets.append(_tweet_from_tapio(item))
            pages += 1
            if max_pages is not None and pages >= max_pages:
                break
            if not isinstance(payload, dict) or not payload.get("has_next_page"):
                break
            cursor = payload.get("next_cursor")
            if not cursor:
                break
        return result


def _iter_tapio_tweets(payload: Any):
    if isinstance(payload, dict):
        container = payload.get("tweets")
        if isinstance(container, list):
            yield from (item for item in container if isinstance(item, dict))
        elif isinstance(payload.get("data"), list):
            yield from (item for item in payload["data"] if isinstance(item, dict))
    elif isinstance(payload, list):
        yield from (item for item in payload if isinstance(item, dict))


def _tweet_from_tapio(item: dict[str, Any]) -> Tweet:
    tweet_id = str(item["id"])
    raw_author = item.get("author")
    author: dict[str, Any] = raw_author if isinstance(raw_author, dict) else {}
    username = _optional_str(author.get("userName") or item.get("userName"))
    refs: list[TweetRef] = []
    reply_to = _optional_str(item.get("inReplyToId"))
    if reply_to:
        refs.append(TweetRef("replied_to", reply_to))
    quoted = item.get("quoted_tweet")
    quoted_id = None
    if isinstance(quoted, dict):
        quoted_id = _optional_str(quoted.get("id"))
    elif quoted:
        quoted_id = _optional_str(quoted)
    if quoted_id:
        refs.append(TweetRef("quoted", quoted_id))
    return Tweet(
        id=tweet_id,
        text=str(item.get("text") or ""),
        author_id=_optional_str(author.get("id")),
        username=username,
        name=_optional_str(author.get("name")),
        created_at=_optional_str(item.get("createdAt")),
        conversation_id=_optional_str(item.get("conversationId")),
        in_reply_to_id=reply_to,
        in_reply_to_user_id=_optional_str(item.get("inReplyToUserId")),
        in_reply_to_username=_optional_str(item.get("inReplyToUsername")),
        referenced_tweets=refs,
        source="twitterapi.io",
        url=f"https://x.com/{username}/status/{tweet_id}" if username else None,
        raw=item,
    )


def _since_iso(since) -> str | None:
    if since is None:
        return None
    if hasattr(since, "isoformat"):
        return since.isoformat()
    parsed = parse_datetime(str(since))
    return parsed.isoformat() if parsed else None
