from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TweetRef:
    type: str
    id: str

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "id": self.id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TweetRef":
        return cls(type=str(data.get("type") or "unknown"), id=str(data["id"]))


@dataclass
class Tweet:
    id: str
    text: str = ""
    author_id: str | None = None
    username: str | None = None
    name: str | None = None
    created_at: str | None = None
    conversation_id: str | None = None
    in_reply_to_id: str | None = None
    in_reply_to_user_id: str | None = None
    in_reply_to_username: str | None = None
    referenced_tweets: list[TweetRef] = field(default_factory=list)
    source: str | None = None
    url: str | None = None
    available: bool = True
    raw: dict[str, Any] | None = None

    def reply_parent_id(self) -> str | None:
        for ref in self.referenced_tweets:
            if ref.type == "replied_to":
                return ref.id
        return self.in_reply_to_id

    def quote_ids(self) -> list[str]:
        seen: set[str] = set()
        quote_ids: list[str] = []
        for ref in self.referenced_tweets:
            if ref.type == "quoted" and ref.id not in seen:
                seen.add(ref.id)
                quote_ids.append(ref.id)
        return quote_ids

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "text": self.text,
            "author_id": self.author_id,
            "username": self.username,
            "name": self.name,
            "created_at": self.created_at,
            "conversation_id": self.conversation_id,
            "in_reply_to_id": self.in_reply_to_id,
            "in_reply_to_user_id": self.in_reply_to_user_id,
            "in_reply_to_username": self.in_reply_to_username,
            "referenced_tweets": [ref.to_dict() for ref in self.referenced_tweets],
            "source": self.source,
            "url": self.url,
            "available": self.available,
        }
        if include_raw:
            data["raw"] = self.raw
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Tweet":
        return cls(
            id=str(data["id"]),
            text=str(data.get("text") or ""),
            author_id=_optional_str(data.get("author_id")),
            username=_optional_str(data.get("username")),
            name=_optional_str(data.get("name")),
            created_at=_optional_str(data.get("created_at")),
            conversation_id=_optional_str(data.get("conversation_id")),
            in_reply_to_id=_optional_str(data.get("in_reply_to_id")),
            in_reply_to_user_id=_optional_str(data.get("in_reply_to_user_id")),
            in_reply_to_username=_optional_str(data.get("in_reply_to_username")),
            referenced_tweets=[
                TweetRef.from_dict(ref) for ref in data.get("referenced_tweets", [])
            ],
            source=_optional_str(data.get("source")),
            url=_optional_str(data.get("url")),
            available=bool(data.get("available", True)),
            raw=data.get("raw"),
        )


@dataclass
class QuoteContext:
    quoted_by_id: str
    quote_id: str
    path: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "quoted_by_id": self.quoted_by_id,
            "quote_id": self.quote_id,
            "path": self.path,
        }


@dataclass
class Conversation:
    target_id: str
    path: list[str]
    quotes: list[QuoteContext] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def all_ids(self) -> set[str]:
        ids = set(self.path)
        for quote in self.quotes:
            ids.update(quote.path)
        return ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "path": self.path,
            "quotes": [quote.to_dict() for quote in self.quotes],
            "all_tweet_ids": sorted(self.all_ids, key=_sort_id),
            "warnings": self.warnings,
        }


def missing_tweet(tweet_id: str, *, source: str | None = None) -> Tweet:
    return Tweet(
        id=tweet_id,
        text="[deleted]",
        source=source,
        available=False,
    )


def _optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _sort_id(value: str) -> tuple[int, str]:
    try:
        return (0, f"{int(value):040d}")
    except ValueError:
        return (1, value)

