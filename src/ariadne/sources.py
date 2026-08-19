from __future__ import annotations

import csv
import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .archive import tweet_from_archive
from .ids import tweet_id_from_url, unique_preserve_order
from .models import Tweet, TweetRef
from .timeutil import is_on_or_after


@dataclass
class SourceLoadResult:
    tweets: list[Tweet] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_tweets_file(path: str | Path) -> SourceLoadResult:
    source_path = Path(path).expanduser()
    if not source_path.exists():
        raise FileNotFoundError(f"Tweet dump path does not exist: {source_path}")

    suffix = source_path.suffix.lower()
    if suffix == ".csv":
        rows = _load_csv(source_path)
    elif suffix in {".jsonl", ".ndjson"}:
        rows = _load_jsonl(source_path)
    else:
        rows = _records_from_payload(_load_jsonish(source_path))

    result = SourceLoadResult(files_read=[str(source_path)])
    for row in rows:
        tweet = tweet_from_any(row, source=f"file:{source_path.name}")
        if tweet:
            result.tweets.append(tweet)
    if not result.tweets:
        result.warnings.append(f"No tweet-like rows found in {source_path}")
    return result


def select_user_tweet_ids(
    tweets: list[Tweet],
    *,
    username: str | None = None,
    author_id: str | None = None,
    since=None,
) -> list[str]:
    username = username.strip("@").lower() if username else None
    selected: list[str] = []
    for tweet in tweets:
        if username and (tweet.username or "").strip("@").lower() != username:
            continue
        if author_id and tweet.author_id != author_id:
            continue
        if not is_on_or_after(tweet.created_at, since):
            continue
        selected.append(tweet.id)
    return unique_preserve_order(selected)


def tweet_from_any(data: dict[str, Any], *, source: str) -> Tweet | None:
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("tweet"), dict):
        data = data["tweet"]

    archive_tweet = tweet_from_archive(data, source=source)
    if archive_tweet:
        return archive_tweet

    tweet_id = _clean_id(
        first_value(data, "id", "id_str", "tweet_id", "tweetId", "status_id", "statusId")
    )
    if not tweet_id:
        url = first_value(data, "url", "tweet_url", "tweetUrl", "link", "permalink")
        tweet_id = tweet_id_from_url(str(url)) if url else None
    if not tweet_id:
        return None

    refs: list[TweetRef] = []
    for ref in data.get("referenced_tweets", []) or []:
        if isinstance(ref, dict) and ref.get("id"):
            refs.append(TweetRef(type=str(ref.get("type") or "unknown"), id=str(ref["id"])))

    reply_id = _clean_id(
        first_value(
            data,
            "in_reply_to_id",
            "inReplyToId",
            "in_reply_to_status_id",
            "in_reply_to_status_id_str",
            "reply_to_id",
            "replyToId",
            "parent_id",
            "parentId",
            "conversation.parent_id",
        )
    )
    if reply_id:
        refs.append(TweetRef("replied_to", reply_id))

    for quote_id in _quote_ids(data):
        refs.append(TweetRef("quoted", quote_id))

    username = _clean_username(
        first_value(
            data,
            "username",
            "screen_name",
            "screenName",
            "author.username",
            "author.screen_name",
            "author.userName",
            "user.username",
            "user.screen_name",
            "user.userName",
        )
    )
    author_id = _clean_id(
        first_value(data, "author_id", "authorId", "user_id", "userId", "author.id", "user.id")
    )
    url = _optional_str(first_value(data, "url", "tweet_url", "tweetUrl", "link", "permalink"))
    if not url and username:
        url = f"https://x.com/{username}/status/{tweet_id}"

    return Tweet(
        id=tweet_id,
        text=html.unescape(_optional_str(first_value(data, "text", "full_text", "fullText", "body", "content")) or ""),
        author_id=author_id,
        username=username,
        name=_optional_str(first_value(data, "name", "author.name", "user.name", "display_name")),
        created_at=_optional_str(first_value(data, "created_at", "createdAt", "date", "timestamp")),
        conversation_id=_clean_id(first_value(data, "conversation_id", "conversationId")),
        in_reply_to_id=reply_id,
        in_reply_to_user_id=_clean_id(first_value(data, "in_reply_to_user_id", "inReplyToUserId")),
        in_reply_to_username=_clean_username(
            first_value(data, "in_reply_to_username", "inReplyToUsername", "in_reply_to_screen_name")
        ),
        referenced_tweets=_dedupe_refs(refs),
        source=source,
        url=url,
        available=bool(data.get("available", True)),
        raw=data,
    )


def first_value(data: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = data
        found = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current not in (None, ""):
            return current
    return None


def _load_jsonish(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip("\ufeff\n\r\t ")
    if not stripped.startswith(("[", "{")):
        starts = [index for index in (stripped.find("["), stripped.find("{")) if index >= 0]
        if not starts:
            return []
        stripped = stripped[min(starts) :]
    if stripped.rstrip().endswith(";"):
        stripped = stripped.rstrip()[:-1]
    return json.loads(stripped)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def _records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("tweets", "data", "results", "items", "posts", "statuses"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    if isinstance(payload.get("tweet"), dict):
        return [payload]
    if any(key in payload for key in ("id", "id_str", "tweet_id", "tweetId", "url")):
        return [payload]
    return []


def _quote_ids(data: dict[str, Any]) -> list[str]:
    quote_ids: list[str] = []
    for key in ("quoted_id", "quotedId", "quoted_status_id", "quoted_status_id_str", "quote_id"):
        quote_id = _clean_id(data.get(key))
        if quote_id:
            quote_ids.append(quote_id)
    for key in ("quoted_url", "quotedUrl", "quote_url", "quoteUrl"):
        value = data.get(key)
        quote_id = tweet_id_from_url(str(value)) if value else None
        if quote_id:
            quote_ids.append(quote_id)
    return unique_preserve_order(quote_ids)


def _dedupe_refs(refs: list[TweetRef]) -> list[TweetRef]:
    seen: set[tuple[str, str]] = set()
    result: list[TweetRef] = []
    for ref in refs:
        key = (ref.type, ref.id)
        if key not in seen:
            seen.add(key)
            result.append(ref)
    return result


def _clean_id(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value)
    return text if text.isdigit() else None


def _clean_username(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value).strip().strip("@") or None


def _optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)

