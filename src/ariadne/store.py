from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .ids import unique_preserve_order
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


def stream_path(cache: str | Path) -> Path:
    """The append-only sidecar that paid fetches are written to as they land."""
    cache_path = Path(cache).expanduser()
    return cache_path.with_name(cache_path.name + ".stream.jsonl")


def append_stream(path: str | Path, tweets: list[Tweet]) -> None:
    """Append tweets to the stream file, one JSON object per line.

    Called from inside a fetch, so it must never raise into the fetch: losing
    the sidecar is worse than useless only if it also loses the tweets that
    are still on their way. Failures here are silent by design.
    """
    if not tweets:
        return
    target = Path(path).expanduser()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            for tweet in tweets:
                handle.write(json.dumps(tweet.to_dict(include_raw=True), sort_keys=True) + "\n")
    except OSError:
        return


def load_stream(path: str | Path) -> list[Tweet]:
    """Tweets recorded by a previous run's stream file, ignoring bad lines.

    A truncated final line is expected after a crash — the whole point of the
    file is that it survives one — so unparseable lines are skipped rather
    than failing the load.
    """
    target = Path(path).expanduser()
    if not target.exists():
        return []
    tweets: list[Tweet] = []
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("id"):
                tweets.append(Tweet.from_dict(row))
    return tweets


def clear_stream(path: str | Path) -> None:
    """Delete a stream file. `path` is the stream itself, not the cache."""
    target = Path(path).expanduser()
    if target.name.endswith(".stream.jsonl"):
        target.unlink(missing_ok=True)


def save_cache(
    path: str | Path, tweets: list[Tweet], *, missing: list[str] | None = None
) -> None:
    """Persist available tweets, and optionally the ids a build could not get.

    The cache itself only holds *fetched* tweets, so the reply/quote edges
    pointing at unresolved parents mostly live in archives and dumps that are
    not cached; recording `missing` here is what lets a later session retry
    them from the cache file alone. When `missing` is None the previously
    recorded list is preserved.
    """
    cache_path = Path(path).expanduser()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if missing is None:
        missing = load_missing_ids(cache_path)
    payload = {
        "version": 1,
        "missing": sorted(set(missing)),
        "tweets": [tweet.to_dict(include_raw=True) for tweet in tweets if tweet.available],
    }
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(cache_path)
    # Everything the stream file was protecting is now in the cache, so it can
    # go; otherwise it would grow without bound across runs.
    clear_stream(stream_path(cache_path))


def load_missing_ids(path: str | Path) -> list[str]:
    """The unresolved tweet ids a previous build recorded in this cache."""
    cache_path = Path(path).expanduser()
    if not cache_path.exists():
        return []
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return []
    return [str(value) for value in payload.get("missing", []) if str(value).strip()]


def empty_tweet_ids(tweets: list[Tweet]) -> list[str]:
    """Ids present in the set but carrying no text (stubs and tombstones)."""
    return [tweet.id for tweet in tweets if not tweet.text or not tweet.available]


def missing_referenced_ids(tweets: list[Tweet]) -> list[str]:
    """Ids referenced by reply/quote edges but absent from the set.

    These are the holes a build left behind: reply parents and quoted
    tweets that no source could produce at the time.
    """
    present = {tweet.id for tweet in tweets}
    wanted: list[str] = []
    for tweet in tweets:
        for ref_id in [tweet.reply_parent_id(), *tweet.quote_ids()]:
            if ref_id and ref_id not in present:
                wanted.append(ref_id)
    return unique_preserve_order(wanted)


def cache_retry_ids(
    tweets: list[Tweet], *, recorded_missing: list[str] | None = None
) -> list[str]:
    """Everything a later session could try to fetch again for this cache.

    Combines the ids a build recorded as unresolved (minus any resolved
    since), the reply/quote references absent from the cached set, and the
    cached stubs that still have no text.
    """
    resolved = {tweet.id for tweet in tweets if tweet.available and tweet.text}
    recorded = [
        tweet_id for tweet_id in (recorded_missing or []) if tweet_id not in resolved
    ]
    return unique_preserve_order(
        recorded + missing_referenced_ids(tweets) + empty_tweet_ids(tweets)
    )


def cache_summary(path: str | Path) -> dict[str, Any] | None:
    """Summarize one cache file, or None when it does not exist.

    Keys: path, bytes, tweets, with_text, empty, missing, first, last,
    authors (Counter.most_common list), sources (same).
    """
    cache_path = Path(path).expanduser()
    if not cache_path.exists():
        return None
    tweets = load_cache(cache_path)
    empty = set(empty_tweet_ids(tweets))
    retryable = cache_retry_ids(tweets, recorded_missing=load_missing_ids(cache_path))
    dates = sorted(tweet.created_at for tweet in tweets if tweet.created_at)
    authors: Counter[str] = Counter(
        f"@{tweet.username}" for tweet in tweets if tweet.username
    )
    sources: Counter[str] = Counter()
    for tweet in tweets:
        for part in (tweet.source or "").split(","):
            part = part.strip()
            if part:
                sources[part] += 1
    return {
        "path": str(cache_path),
        "bytes": cache_path.stat().st_size,
        "tweets": len(tweets),
        "with_text": sum(1 for tweet in tweets if tweet.available and tweet.text),
        "empty": len(empty),
        "missing": len([tweet_id for tweet_id in retryable if tweet_id not in empty]),
        "first": dates[0] if dates else "",
        "last": dates[-1] if dates else "",
        "authors": authors.most_common(5),
        "sources": sources.most_common(5),
    }


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

