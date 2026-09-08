from __future__ import annotations

from dataclasses import dataclass

from .models import Conversation, Tweet
from .store import TweetStore


@dataclass(frozen=True)
class ConversationClassification:
    """How a reconstructed branch should enter a downstream dataset."""

    dataset_role: str
    kind: str
    label: str
    subject: str | None
    has_subject_response: bool
    response_tweet_ids: tuple[str, ...]
    subject_tweet_ids: tuple[str, ...]


def classify_conversation(
    conversation: Conversation,
    store: TweetStore,
    *,
    subject: str | None = None,
) -> ConversationClassification:
    """Classify a branch using authorship and reply edges, not position.

    Only a substantive subject-authored reply to a substantive tweet by a
    known different author is a training conversation.  Standalone posts,
    self-threads, quote commentary, and replies whose prompt is unresolved
    are authored corpus material.
    """

    target = store.get(conversation.target_id)
    normalized_subject = _normalize_username(subject) or _normalize_username(
        target.username if target else None
    )
    target_author_id = target.author_id if target else None

    def is_subject(tweet: Tweet | None) -> bool:
        if tweet is None:
            return False
        username = _normalize_username(tweet.username)
        if normalized_subject and username:
            return username == normalized_subject
        if subject is None and target_author_id and tweet.author_id:
            return tweet.author_id == target_author_id
        return False

    subject_ids: list[str] = []
    response_ids: list[str] = []
    saw_reply = False
    saw_self_reply = False
    saw_incomplete_reply = False
    saw_quote = False

    for tweet_id in conversation.path:
        tweet = store.get(tweet_id)
        if not is_subject(tweet):
            continue
        subject_ids.append(tweet_id)
        if tweet is None:
            continue
        saw_quote = saw_quote or bool(tweet.quote_ids())
        parent_id = tweet.reply_parent_id()
        if not parent_id:
            continue
        saw_reply = True
        parent = store.get(parent_id)
        if is_subject(parent):
            saw_self_reply = True
            continue
        if not _author_known(parent) or not _substantive(parent) or not _substantive(tweet):
            saw_incomplete_reply = True
            continue
        response_ids.append(tweet_id)

    if response_ids:
        return ConversationClassification(
            dataset_role="conversation",
            kind="tweet_conversation",
            label="reply",
            subject=normalized_subject,
            has_subject_response=True,
            response_tweet_ids=tuple(response_ids),
            subject_tweet_ids=tuple(subject_ids),
        )

    if saw_incomplete_reply or (saw_reply and not saw_self_reply):
        label = "incomplete_reply"
    elif saw_self_reply or len(subject_ids) > 1:
        label = "self_thread"
    elif saw_quote:
        label = "quote"
    elif subject_ids:
        label = "standalone"
    else:
        label = "unknown"
    return ConversationClassification(
        dataset_role="corpus",
        kind="tweet_thread",
        label=label,
        subject=normalized_subject,
        has_subject_response=False,
        response_tweet_ids=(),
        subject_tweet_ids=tuple(subject_ids),
    )


def _normalize_username(value: str | None) -> str | None:
    normalized = (value or "").strip().strip("@").casefold()
    return normalized or None


def _author_known(tweet: Tweet | None) -> bool:
    return bool(tweet and (tweet.username or tweet.author_id))


def _substantive(tweet: Tweet | None) -> bool:
    return bool(tweet and tweet.available and tweet.text.strip())
