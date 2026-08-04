from __future__ import annotations

import json
from pathlib import Path

from .models import Tweet, TweetRef


class TweetStore:
    def __init__(self) -> None:
        self._tweets: dict[str, Tweet] = {}
        self._cacheable_ids: set[str] = set()

    def add(self, tweet: Tweet, *, cacheable: bool = False) -> None:
        existing = self._tweets.get(tweet.id)
        if existing is None:
            self._tweets[tweet.id] = tweet
        else:
            self._tweets[tweet.id] = merge_tweets(existing, tweet)
        if cacheable:
            self._cacheable_ids.add(tweet.id)

    def add_many(self, tweets: list[Tweet], *, cacheable: bool = False) -> None:
        for tweet in tweets:
            self.add(tweet, cacheable=cacheable)

    def get(self, tweet_id: str) -> Tweet | None:
        return self._tweets.get(tweet_id)

    def contains(self, tweet_id: str) -> bool:
        return tweet_id in self._tweets

    def all(self) -> list[Tweet]:
        return list(self._tweets.values())

    def tweets_for_ids(self, ids: set[str]) -> list[Tweet]:
        return [self._tweets[tweet_id] for tweet_id in sorted(ids) if tweet_id in self._tweets]

    def cacheable_tweets(self) -> list[Tweet]:
        return [
            self._tweets[tweet_id]
            for tweet_id in sorted(self._cacheable_ids)
            if tweet_id in self._tweets
        ]


def merge_tweets(existing: Tweet, incoming: Tweet) -> Tweet:
    if not existing.available and incoming.available:
        return incoming
    if existing.available and not incoming.available:
        return existing

    refs = _merge_refs(existing.referenced_tweets, incoming.referenced_tweets)
    return Tweet(
        id=existing.id,
        text=_prefer(existing.text, incoming.text) or "",
        author_id=_prefer(existing.author_id, incoming.author_id),
        username=_prefer(existing.username, incoming.username),
        name=_prefer(existing.name, incoming.name),
        created_at=_prefer(existing.created_at, incoming.created_at),
        conversation_id=_prefer(existing.conversation_id, incoming.conversation_id),
        in_reply_to_id=_prefer(existing.in_reply_to_id, incoming.in_reply_to_id),
        in_reply_to_user_id=_prefer(existing.in_reply_to_user_id, incoming.in_reply_to_user_id),
        in_reply_to_username=_prefer(existing.in_reply_to_username, incoming.in_reply_to_username),
        referenced_tweets=refs,
        source=_merge_source(existing.source, incoming.source),
        url=_prefer(existing.url, incoming.url),
        available=existing.available or incoming.available,
        raw=existing.raw or incoming.raw,
    )


def load_cache(path: str | Path) -> list[Tweet]:
    cache_path = Path(path).expanduser()
    if not cache_path.exists():
        return []
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        rows = payload.get("tweets", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return [Tweet.from_dict(row) for row in rows if isinstance(row, dict) and row.get("id")]


def save_cache(path: str | Path, tweets: list[Tweet]) -> None:
    cache_path = Path(path).expanduser()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "tweets": [tweet.to_dict(include_raw=True) for tweet in tweets if tweet.available],
    }
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(cache_path)


def _prefer(first: str | None, second: str | None) -> str | None:
    return first if first not in (None, "") else second


def _merge_source(first: str | None, second: str | None) -> str | None:
    if not first:
        return second
    if not second or second == first:
        return first
    parts = []
    for source in (first, second):
        for part in source.split(","):
            part = part.strip()
            if part and part not in parts:
                parts.append(part)
    return ",".join(parts)


def _merge_refs(first: list[TweetRef], second: list[TweetRef]) -> list[TweetRef]:
    seen: set[tuple[str, str]] = set()
    refs: list[TweetRef] = []
    for ref in first + second:
        key = (ref.type, ref.id)
        if key not in seen:
            seen.add(key)
            refs.append(ref)
    return refs

