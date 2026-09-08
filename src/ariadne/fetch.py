from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any

from .errors import SourceError
from .ids import unique_preserve_order
from .models import Tweet, TweetRef
from .timeutil import api_time


TWEET_FIELDS = (
    "id",
    "text",
    "author_id",
    "created_at",
    "conversation_id",
    "in_reply_to_user_id",
    "referenced_tweets",
    "entities",
    "attachments",
    "note_tweet",
)
EXPANSIONS = (
    "author_id",
    "in_reply_to_user_id",
    "referenced_tweets.id",
    "referenced_tweets.id.author_id",
)
USER_FIELDS = ("id", "name", "username")


@dataclass
class FetchResult:
    tweets: list[Tweet] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    # An id may be unsupported by a provider without that provider having
    # failed.  Keeping skips separate prevents local capability checks (such
    # as oEmbed lacking a URL) from being reported as network errors while
    # still allowing later providers to try the id.
    skipped: dict[str, str] = field(default_factory=dict)


class XApiError(SourceError):
    """An X API call failed. `status` is the HTTP code when there was one."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def is_auth_failure(self) -> bool:
        """The token itself was rejected, so it will not work again as-is."""
        return self.status in (401, 403)

    @property
    def is_billing_failure(self) -> bool:
        """The token is fine but the account cannot pay for the read."""
        return self.status == 402

    @property
    def is_rate_limited(self) -> bool:
        return self.status == 429

    def explain(self) -> str:
        """A short reason suitable for a warning line."""
        if self.is_billing_failure:
            return "the account is out of API credits (HTTP 402)"
        if self.is_auth_failure:
            return f"the bearer token was rejected (HTTP {self.status})"
        if self.is_rate_limited:
            return "the API rate limit was hit (HTTP 429)"
        return str(self)


class XApiClient:
    """X API v2 client.

    `sink` receives every batch of tweets the moment it is parsed, before any
    later request can fail. Paid reads are expensive, so they are handed off
    for durable storage immediately rather than at the end of the run.
    """

    def __init__(
        self,
        bearer_token: str,
        *,
        base_url: str = "https://api.x.com/2",
        timeout: float = 30.0,
        sink: "Callable[[list[Tweet]], None] | None" = None,
    ) -> None:
        self.bearer_token = bearer_token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.sink = sink

    def _emit(self, tweets: list[Tweet]) -> None:
        if self.sink is not None and tweets:
            self.sink(tweets)

    def get_posts(self, ids: list[str]) -> FetchResult:
        ids = unique_preserve_order(ids)
        result = FetchResult()
        for start in range(0, len(ids), 100):
            batch = ids[start : start + 100]
            batch_result = tweets_from_api_payload(
                self._request_json(
                    "/tweets",
                    {
                        "ids": ",".join(batch),
                        "tweet.fields": ",".join(TWEET_FIELDS),
                        "expansions": ",".join(EXPANSIONS),
                        "user.fields": ",".join(USER_FIELDS),
                    },
                )
            )
            self._emit(batch_result.tweets)
            result.tweets.extend(batch_result.tweets)
            result.errors.update(batch_result.errors)
        return result

    def get_user_by_username(self, username: str) -> dict[str, Any]:
        payload = self._request_json(
            f"/users/by/username/{urllib.parse.quote(username.strip('@'))}",
            {"user.fields": ",".join(USER_FIELDS)},
        )
        user = payload.get("data")
        if not isinstance(user, dict) or not user.get("id"):
            raise XApiError(f"X API did not return a user for @{username.strip('@')}")
        return user

    def get_user_posts(
        self,
        user_id: str,
        *,
        start_time=None,
        max_pages: int | None = None,
    ) -> FetchResult:
        result = FetchResult()
        next_token: str | None = None
        pages = 0
        while True:
            params = {
                "max_results": "100",
                "tweet.fields": ",".join(TWEET_FIELDS),
                "expansions": ",".join(EXPANSIONS),
                "user.fields": ",".join(USER_FIELDS),
            }
            start = api_time(start_time)
            if start:
                params["start_time"] = start
            if next_token:
                params["pagination_token"] = next_token
            payload = self._request_json(f"/users/{urllib.parse.quote(str(user_id))}/tweets", params)
            page = tweets_from_api_payload(payload)
            self._emit(page.tweets)
            result.tweets.extend(page.tweets)
            result.errors.update(page.errors)
            pages += 1
            if max_pages is not None and pages >= max_pages:
                break
            next_token = payload.get("meta", {}).get("next_token")
            if not next_token:
                break
        return result

    def _request_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{self.base_url}{path}?{query}",
            headers={
                "Authorization": f"Bearer {self.bearer_token}",
                "Accept": "application/json",
                "User-Agent": "ariadne/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise XApiError(
                f"X API request failed with HTTP {exc.code}: {body}", status=exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise XApiError(f"X API request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise XApiError("X API returned a non-object response")
        return payload


class OEmbedClient:
    def __init__(
        self,
        *,
        url_by_id: dict[str, str] | None = None,
        tweet_lookup=None,
        endpoint: str = "https://publish.x.com/oembed",
        timeout: float = 20.0,
    ) -> None:
        self.url_by_id = url_by_id or {}
        self.tweet_lookup = tweet_lookup
        self.endpoint = endpoint
        self.timeout = timeout

    def get_posts(self, ids: list[str]) -> FetchResult:
        result = FetchResult()
        for tweet_id in unique_preserve_order(ids):
            url = self._url_for(tweet_id)
            if not url:
                result.skipped[tweet_id] = "oEmbed has no tweet URL or author handle"
                continue
            try:
                payload = self._request(url)
            except urllib.error.HTTPError as exc:
                result.errors[tweet_id] = f"oEmbed HTTP {exc.code}"
                continue
            except urllib.error.URLError as exc:
                result.errors[tweet_id] = f"oEmbed request failed: {exc}"
                continue
            tweet = tweet_from_oembed_payload(tweet_id, payload, url=url)
            if tweet:
                result.tweets.append(tweet)
            else:
                result.errors[tweet_id] = "oEmbed response did not contain tweet content"
        return result

    def _url_for(self, tweet_id: str) -> str | None:
        if tweet_id in self.url_by_id:
            return self.url_by_id[tweet_id]
        tweet = self.tweet_lookup(tweet_id) if self.tweet_lookup else None
        if tweet and tweet.url:
            return tweet.url
        if tweet and tweet.username:
            return f"https://x.com/{tweet.username}/status/{tweet_id}"
        return None

    def _request(self, url: str) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {"url": url, "omit_script": "1", "hide_thread": "1"}
        )
        request = urllib.request.Request(
            f"{self.endpoint}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "ariadne/0.1",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise XApiError("oEmbed returned a non-object response")
        return payload


def tweets_from_api_payload(payload: dict[str, Any]) -> FetchResult:
    users = {
        str(user["id"]): user
        for user in payload.get("includes", {}).get("users", [])
        if isinstance(user, dict) and user.get("id")
    }
    result = FetchResult()
    for data in payload.get("data", []) or []:
        if isinstance(data, dict):
            result.tweets.append(tweet_from_api(data, users, source="x-api"))
    for data in payload.get("includes", {}).get("tweets", []) or []:
        if isinstance(data, dict):
            result.tweets.append(tweet_from_api(data, users, source="x-api:include"))
    for error in payload.get("errors", []) or []:
        tweet_id = _error_tweet_id(error)
        if tweet_id:
            result.errors[tweet_id] = _error_message(error)
    return result


def tweet_from_api(data: dict[str, Any], users: dict[str, dict[str, Any]], *, source: str) -> Tweet:
    author_id = _optional_str(data.get("author_id"))
    user = users.get(author_id or "", {})
    refs = [
        TweetRef(type=str(ref.get("type") or "unknown"), id=str(ref["id"]))
        for ref in data.get("referenced_tweets", []) or []
        if isinstance(ref, dict) and ref.get("id")
    ]
    reply_id = next((ref.id for ref in refs if ref.type == "replied_to"), None)
    username = _optional_str(user.get("username"))
    tweet_id = str(data["id"])
    text = _note_text(data) or str(data.get("text") or "")
    return Tweet(
        id=tweet_id,
        text=text,
        author_id=author_id,
        username=username,
        name=_optional_str(user.get("name")),
        created_at=_optional_str(data.get("created_at")),
        conversation_id=_optional_str(data.get("conversation_id")),
        in_reply_to_id=reply_id,
        in_reply_to_user_id=_optional_str(data.get("in_reply_to_user_id")),
        referenced_tweets=refs,
        source=source,
        url=f"https://x.com/{username}/status/{tweet_id}" if username else None,
        raw=data,
    )


def tweet_from_oembed_payload(tweet_id: str, payload: dict[str, Any], *, url: str) -> Tweet | None:
    html_text = str(payload.get("html") or "")
    parsed = _TweetEmbedParser.parse(html_text)
    text = parsed.text
    if not text:
        return None
    username = _username_from_oembed(payload, url)
    return Tweet(
        id=tweet_id,
        text=text,
        username=username,
        name=_optional_str(payload.get("author_name")) or username,
        created_at=parsed.date_text,
        source="oembed",
        url=_optional_str(payload.get("url")) or url,
        raw=payload,
    )


class _TweetEmbedParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_p = False
        self._in_anchor = False
        self._p_parts: list[str] = []
        self._anchor_parts: list[str] = []
        self.anchor_texts: list[str] = []

    @classmethod
    def parse(cls, html_text: str):
        parser = cls()
        parser.feed(html_text)
        text = unescape("".join(parser._p_parts)).strip()
        date_text = parser.anchor_texts[-1].strip() if parser.anchor_texts else None
        return _ParsedEmbed(text=text, date_text=date_text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "p":
            self._in_p = True
        elif tag == "a":
            self._in_anchor = True
            self._anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self._in_p = False
        elif tag == "a":
            self._in_anchor = False
            text = unescape("".join(self._anchor_parts)).strip()
            if text:
                self.anchor_texts.append(text)

    def handle_data(self, data: str) -> None:
        if self._in_p:
            self._p_parts.append(data)
        if self._in_anchor:
            self._anchor_parts.append(data)


@dataclass
class _ParsedEmbed:
    text: str
    date_text: str | None


def _note_text(data: dict[str, Any]) -> str | None:
    note = data.get("note_tweet")
    if isinstance(note, dict) and note.get("text"):
        return str(note["text"])
    return None


def _optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _username_from_oembed(payload: dict[str, Any], url: str) -> str | None:
    author_url = str(payload.get("author_url") or "")
    for candidate in (author_url, str(payload.get("url") or ""), url):
        match = re.search(r"https?://(?:mobile\.)?(?:x|twitter)\.com/([^/?#]+)/?", candidate)
        if match and match.group(1) not in {"i", "intent"}:
            return match.group(1).strip("@")
    return None


def _error_tweet_id(error: Any) -> str | None:
    if not isinstance(error, dict):
        return None
    for key in ("resource_id", "value"):
        value = error.get(key)
        if value and str(value).isdigit():
            return str(value)
    return None


def _error_message(error: dict[str, Any]) -> str:
    parts = [
        str(error[key])
        for key in ("title", "detail")
        if error.get(key) is not None
    ]
    return ": ".join(parts) if parts else json.dumps(error, sort_keys=True)
