from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .fetch import FetchResult
from .models import Tweet
from .timeutil import is_on_or_after


@dataclass
class UnofficialTimelineResult:
    tweets: list[Tweet] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class NitterRssClient:
    def __init__(
        self,
        *,
        base_url: str = "https://nitter.net",
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_user_posts(self, username: str, *, since=None) -> UnofficialTimelineResult:
        username = username.strip("@")
        warnings = [
            "Using unofficial Nitter/XCancel-style RSS. It may break, be incomplete, be blocked, or omit reply-parent metadata.",
            "RSS timeline fallback usually returns only the latest feed page; older posts may be unavailable even when --since is older.",
        ]
        try:
            payload = self._request(username)
        except urllib.error.HTTPError as exc:
            return UnofficialTimelineResult(
                warnings=warnings + [f"Unofficial RSS request failed with HTTP {exc.code} from {self.base_url}"]
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            return UnofficialTimelineResult(
                warnings=warnings + [f"Unofficial RSS request failed from {self.base_url}: {exc}"]
            )
        except ET.ParseError as exc:
            return UnofficialTimelineResult(
                warnings=warnings + [f"Unofficial RSS response from {self.base_url} was not parseable XML: {exc}"]
            )

        tweets = tweets_from_nitter_rss(payload, username=username, source=f"unofficial-rss:{self.base_url}")
        tweets = [tweet for tweet in tweets if is_on_or_after(tweet.created_at, since)]
        if not tweets:
            warnings.append(f"Unofficial RSS returned no matching tweets for @{username}")
        return UnofficialTimelineResult(tweets=tweets, warnings=warnings)

    def as_fetch_result(self, username: str, *, since=None) -> FetchResult:
        result = self.get_user_posts(username, since=since)
        return FetchResult(tweets=result.tweets)

    def _request(self, username: str) -> bytes:
        url = f"{self.base_url}/{urllib.parse.quote(username)}/rss"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/rss+xml, application/xml, text/xml",
                "User-Agent": "Mozilla/5.0 tweet-threader/0.1",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()


def tweets_from_nitter_rss(payload: bytes | str, *, username: str, source: str) -> list[Tweet]:
    root = ET.fromstring(payload)
    channel = root.find("channel")
    if channel is None:
        return []
    display_name = _display_name(channel.findtext("title"), username)
    tweets: list[Tweet] = []
    for item in channel.findall("item"):
        tweet_id = _item_id(item)
        if not tweet_id:
            continue
        text = _item_text(item)
        tweets.append(
            Tweet(
                id=tweet_id,
                text=text,
                username=username,
                name=display_name,
                created_at=item.findtext("pubDate"),
                source=source,
                url=f"https://x.com/{username}/status/{tweet_id}",
                raw={
                    "title": item.findtext("title"),
                    "link": item.findtext("link"),
                    "guid": item.findtext("guid"),
                    "pubDate": item.findtext("pubDate"),
                    "description": item.findtext("description"),
                },
            )
        )
    return tweets


def _item_id(item: ET.Element) -> str | None:
    for value in (item.findtext("guid"), item.findtext("link")):
        if not value:
            continue
        match = re.search(r"(?<!\d)(\d{8,20})(?!\d)", value)
        if match:
            return match.group(1)
    return None


def _item_text(item: ET.Element) -> str:
    description = item.findtext("description")
    if description:
        text = _html_text(description)
        if text:
            return text
    return html.unescape(item.findtext("title") or "").strip()


def _display_name(title: str | None, username: str) -> str:
    if not title:
        return username
    suffix = f" / @{username}"
    return title.removesuffix(suffix).strip() or username


def _html_text(value: str) -> str:
    parser = _TextParser()
    parser.feed(value)
    text = html.unescape("".join(parser.parts))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag in {"br", "p", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

