from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from ._types import OutputFormat
from .models import Conversation, QuoteContext, Tweet
from .store import TweetStore


def render(
    conversations: list[Conversation],
    store: TweetStore,
    *,
    output_format: OutputFormat,
    subject: str | None = None,
) -> str:
    if output_format == "json":
        return render_json(conversations, store)
    if output_format == "messages":
        return render_messages(conversations, store, strict_openai=False, subject=subject)
    if output_format == "openai":
        return render_messages(conversations, store, strict_openai=True, subject=subject)
    if output_format == "raft":
        return render_raft_jsonl(conversations, store, subject=subject)
    if output_format == "markdown":
        return render_markdown(conversations, store, subject=subject)
    raise ValueError(f"Unsupported output format: {output_format}")


def json_payload(conversations: list[Conversation], store: TweetStore) -> dict[str, Any]:
    """Build the ``ariadne.json.v1`` payload as data."""
    ids: set[str] = set()
    for conversation in conversations:
        ids.update(conversation.all_ids)
    return {
        "format": "ariadne.json.v1",
        "conversations": [conversation.to_dict() for conversation in conversations],
        "tweets": {
            tweet.id: tweet.to_dict(include_raw=False)
            for tweet in store.tweets_for_ids(ids)
        },
    }


def render_json(conversations: list[Conversation], store: TweetStore) -> str:
    return json.dumps(json_payload(conversations, store), indent=2, sort_keys=True) + "\n"


def message_conversations(
    conversations: list[Conversation],
    store: TweetStore,
    *,
    strict_openai: bool,
    subject: str | None = None,
) -> list[dict[str, Any]]:
    """Build the rendered message conversations as data.

    Each entry is ``{"target_id", "messages", "warnings"}``. With
    ``strict_openai`` the messages carry only role/name/content.
    """
    rendered_conversations: list[dict[str, Any]] = []
    for conversation in conversations:
        quote_map = _quotes_by_owner(conversation.quotes)
        target = store.get(conversation.target_id)
        messages: list[dict[str, Any]] = []
        for tweet_id in conversation.path:
            tweet = store.get(tweet_id)
            content = _tweet_content(tweet)
            quote_contexts = quote_map.get(tweet_id, [])
            if quote_contexts:
                content = "\n\n".join([content] + [_quote_block(context, store) for context in quote_contexts])
            message: dict[str, Any] = {
                "role": _role(
                    tweet, tweet_id, conversation, target, other="user", subject=subject
                ),
                "name": message_name(tweet, fallback=tweet_id),
                "content": content,
            }
            if not strict_openai:
                message.update(
                    {
                        "tweet_id": tweet_id,
                        "created_at": tweet.created_at if tweet else None,
                        "url": tweet.url if tweet else None,
                        "author": display_author(tweet),
                    }
                )
            messages.append(message)
        rendered_conversations.append(
            {
                "target_id": conversation.target_id,
                "messages": messages,
                "warnings": conversation.warnings,
            }
        )
    return rendered_conversations


def render_messages(
    conversations: list[Conversation],
    store: TweetStore,
    *,
    strict_openai: bool,
    subject: str | None = None,
) -> str:
    payload = {
        "format": "openai.messages.v1" if strict_openai else "messages",
        "conversations": message_conversations(
            conversations, store, strict_openai=strict_openai, subject=subject
        ),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def raft_documents(
    conversations: list[Conversation],
    store: TweetStore,
    *,
    subject: str | None = None,
) -> list[dict[str, Any]]:
    """Build the ``raft.documents.v1`` rows as data, one per conversation."""
    rows: list[dict[str, Any]] = []
    for conversation in conversations:
        quote_map = _quotes_by_owner(conversation.quotes)
        target = store.get(conversation.target_id)
        messages: list[dict[str, Any]] = []
        text_blocks: list[str] = []
        participants: set[str] = set()
        for tweet_id in conversation.path:
            tweet = store.get(tweet_id)
            author = display_author(tweet)
            participants.add(author)
            role = _role(
                tweet, tweet_id, conversation, target, other="participant", subject=subject
            )
            content = _tweet_content(tweet)
            message = {
                "role": role,
                "tweet_id": tweet_id,
                "author": author,
                "username": tweet.username if tweet else None,
                "created_at": tweet.created_at if tweet else None,
                "url": tweet.url if tweet else None,
                "text": content,
            }
            messages.append(message)
            text_blocks.append(f"{author}: {content}")
            for quote_context in quote_map.get(tweet_id, []):
                quote_text = _quote_block(quote_context, store)
                text_blocks.append(quote_text)

        target = store.get(conversation.target_id)
        row = {
            "format": "raft.documents.v1",
            "id": f"ariadne:{conversation.target_id}",
            "source": "ariadne",
            "kind": "tweet_conversation",
            "text": "\n\n".join(text_blocks),
            "metadata": {
                "target_id": conversation.target_id,
                "target_author": display_author(target),
                "target_created_at": target.created_at if target else None,
                "target_url": target.url if target else None,
                "tweet_ids": conversation.path,
                "all_tweet_ids": sorted(conversation.all_ids),
                "participants": sorted(participants),
                "quote_count": len(conversation.quotes),
                "warnings": conversation.warnings,
            },
            "messages": messages,
            "quotes": [quote.to_dict() for quote in conversation.quotes],
        }
        rows.append(row)
    return rows


def render_raft_jsonl(
    conversations: list[Conversation],
    store: TweetStore,
    *,
    subject: str | None = None,
) -> str:
    lines = [
        json.dumps(row, sort_keys=True)
        for row in raft_documents(conversations, store, subject=subject)
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def render_markdown(
    conversations: list[Conversation],
    store: TweetStore,
    *,
    subject: str | None = None,
) -> str:
    lines: list[str] = []
    for conversation_index, conversation in enumerate(conversations, start=1):
        if len(conversations) > 1:
            lines.append(f"## Conversation {conversation_index}: {conversation.target_id}")
            lines.append("")
        quote_map = _quotes_by_owner(conversation.quotes)
        for run in _author_runs(conversation, store, subject=subject):
            role, author, tweet_ids = run
            lines.append(f"### {role}: {author}")
            for tweet_id in tweet_ids:
                tweet = store.get(tweet_id)
                if tweet and tweet.created_at:
                    lines.append(f"`{tweet.created_at}`")
                if tweet and tweet.url:
                    lines.append(tweet.url)
            lines.append("")
            segments: list[str] = []
            for tweet_id in tweet_ids:
                tweet = store.get(tweet_id)
                segment = _tweet_content(tweet)
                for quote_context in quote_map.get(tweet_id, []):
                    segment += "\n\n" + _quote_block(quote_context, store)
                segments.append(segment)
            # A blank line before --- matters: text followed directly by
            # dashes is a setext heading in markdown, not a rule.
            lines.append("\n\n---\n\n".join(segments))
            lines.append("")
        if conversation.warnings:
            lines.append("Warnings:")
            for warning in conversation.warnings:
                lines.append(f"- {warning}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _author_runs(
    conversation: Conversation,
    store: TweetStore,
    *,
    subject: str | None,
) -> list[tuple[str, str, list[str]]]:
    """Group the path into runs of consecutive tweets by the same author.

    A thread of e.g. three consecutive tweets by the subject renders as one
    message: one role header, the metadata of all three, then the texts.

    Returns:
        (role, display_author, tweet_ids) per run, in path order.
    """
    target = store.get(conversation.target_id)
    runs: list[tuple[str, str, list[str]]] = []
    for tweet_id in conversation.path:
        tweet = store.get(tweet_id)
        role = _role(tweet, tweet_id, conversation, target, other="user", subject=subject)
        author = display_author(tweet)
        # Two [deleted] tweets in a row are not known to share an author, so
        # they never merge into one run.
        if runs and runs[-1][0] == role and runs[-1][1] == author and author != "[deleted]":
            runs[-1][2].append(tweet_id)
        else:
            runs.append((role, author, [tweet_id]))
    return runs


def _role(
    tweet: Tweet | None,
    tweet_id: str,
    conversation: Conversation,
    target: Tweet | None,
    *,
    other: str,
    subject: str | None = None,
) -> str:
    """The subject's tweets speak as the assistant; everyone else as `other`.

    With a known ``subject`` (the collected username), roles come from the
    author alone; a conversation's own target tweet only falls back to
    assistant when its author cannot be determined at all. Without one, the
    author of the conversation's target tweet is the subject, and the id
    check keeps the target tweet an assistant turn even when it is missing
    from the store and its author cannot be compared.
    """
    if subject is not None:
        if _authored_by_username(tweet, subject):
            return "assistant"
        if tweet_id == conversation.target_id and _author_unknown(tweet):
            return "assistant"
        return other
    if tweet_id == conversation.target_id or _authored_by_target(tweet, target):
        return "assistant"
    return other


def _authored_by_username(tweet: Tweet | None, username: str) -> bool:
    if tweet is None or not tweet.username:
        return False
    return tweet.username.strip("@").lower() == username.strip("@").lower()


def _author_unknown(tweet: Tweet | None) -> bool:
    return tweet is None or not (tweet.username or tweet.author_id)


def _authored_by_target(tweet: Tweet | None, target: Tweet | None) -> bool:
    if tweet is None or target is None:
        return False
    if tweet.username and target.username:
        return tweet.username.lower() == target.username.lower()
    if tweet.author_id and target.author_id:
        return tweet.author_id == target.author_id
    return False


def message_name(tweet: Tweet | None, *, fallback: str) -> str:
    raw = None
    if tweet:
        raw = tweet.username or tweet.author_id or tweet.name
    raw = raw or f"tweet_{fallback}"
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", raw.strip("@"))
    return (name or f"tweet_{fallback}")[:64]


def display_author(tweet: Tweet | None) -> str:
    """A deleted tweet, or one whose author is unknown, is attributed to [deleted]."""
    if tweet is None:
        return "[deleted]"
    if tweet.username:
        return f"@{tweet.username}"
    if tweet.name:
        return tweet.name
    if tweet.author_id:
        return tweet.author_id
    return "[deleted]"


def _tweet_content(tweet: Tweet | None) -> str:
    if tweet is None or not tweet.available:
        return "[deleted]"
    return tweet.text or f"[tweet {tweet.id} has no text]"


def _quotes_by_owner(quotes: list[QuoteContext]) -> dict[str, list[QuoteContext]]:
    grouped: dict[str, list[QuoteContext]] = defaultdict(list)
    for quote in quotes:
        grouped[quote.quoted_by_id].append(quote)
    return grouped


def _quote_block(context: QuoteContext, store: TweetStore) -> str:
    lines = [f"Quoted context for tweet {context.quote_id}:"]
    for tweet_id in context.path:
        tweet = store.get(tweet_id)
        lines.append(f"{display_author(tweet)}: {_tweet_content(tweet)}")
    return "\n".join(lines)
