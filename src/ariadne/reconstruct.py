from __future__ import annotations

from typing import Protocol

from .errors import ReconstructionError
from .fetch import FetchResult
from .ids import unique_preserve_order
from .models import Conversation, QuoteContext, Tweet, missing_tweet
from .store import TweetStore


class TweetFetcher(Protocol):
    def get_posts(self, ids: list[str]) -> FetchResult: ...


class ConversationBuilder:
    def __init__(
        self,
        store: TweetStore,
        *,
        fetcher: TweetFetcher | None = None,
        include_quotes: bool = True,
        quote_as_reply: bool = True,
        strict: bool = False,
        max_depth: int = 50,
    ) -> None:
        self.store = store
        self.fetcher = fetcher
        self.include_quotes = include_quotes
        # When the first tweet of a thread is a quote-tweet (it quotes something
        # but replies to nothing), treat the quoted tweet as its reply-parent and
        # keep walking, so the branch continues into the quoted conversation.
        self.quote_as_reply = quote_as_reply
        self.strict = strict
        self.max_depth = max_depth
        self.warnings: list[str] = []

    def build(self, target_ids: list[str]) -> list[Conversation]:
        target_ids = unique_preserve_order(target_ids)
        self._ensure(target_ids)
        conversations = [self._build_conversation(target_id) for target_id in target_ids]
        return prune_subset_conversations(conversations)

    def _build_conversation(self, target_id: str) -> Conversation:
        before = len(self.warnings)
        # Quotes consumed as reply-parents (root-quote splicing) are excluded from
        # the separate quote-context pass so they are not attached twice.
        consumed_quotes: set[str] = set()
        path = self._build_path(target_id, consumed_quotes=consumed_quotes)
        quotes = (
            self._collect_quote_contexts(path, seen_quote_ids=set(consumed_quotes))
            if self.include_quotes
            else []
        )
        return Conversation(
            target_id=target_id,
            path=path,
            quotes=quotes,
            warnings=self.warnings[before:],
        )

    def _build_path(self, target_id: str, *, consumed_quotes: set[str] | None = None) -> list[str]:
        path: list[str] = []
        current_id = target_id
        seen: set[str] = set()

        for _ in range(self.max_depth):
            if current_id in seen:
                self.warnings.append(f"Cycle detected while following replies at {current_id}")
                break
            seen.add(current_id)
            self._ensure([current_id])

            tweet = self.store.get(current_id)
            if tweet is None:
                message = f"Tweet {current_id} is missing and could not be fetched"
                if self.strict:
                    raise ReconstructionError(message)
                self.warnings.append(message)
                tweet = missing_tweet(current_id, source="missing")
                self.store.add(tweet)
            elif not tweet.text and tweet.available:
                self._ensure([current_id], hydrate_empty=True)
                tweet = self.store.get(current_id) or tweet

            path.append(current_id)
            parent_id = tweet.reply_parent_id()
            if parent_id:
                self._add_reply_parent_stub(tweet, parent_id)
                current_id = parent_id
                continue

            # No reply-parent: this is the first tweet of the thread. If it quotes
            # something, follow the quote as the reply-parent (the quoted tweet
            # becomes the branch root) and record it so it is not double-attached.
            quote_id = self._root_quote_parent(tweet, seen, consumed_quotes)
            if quote_id is None:
                break
            consumed_quotes.add(quote_id)  # type: ignore[union-attr]
            current_id = quote_id
        else:
            self.warnings.append(
                f"Stopped at max depth {self.max_depth} while following replies from {target_id}"
            )

        path.reverse()
        return path

    def _root_quote_parent(
        self, tweet: Tweet, seen: set[str], consumed_quotes: set[str] | None
    ) -> str | None:
        if consumed_quotes is None or not self.quote_as_reply or not self.include_quotes:
            return None
        return next((q for q in tweet.quote_ids() if q not in seen), None)

    def _collect_quote_contexts(
        self, path: list[str], *, seen_quote_ids: set[str]
    ) -> list[QuoteContext]:
        contexts: list[QuoteContext] = []
        for tweet_id in path:
            tweet = self.store.get(tweet_id)
            if tweet is None:
                continue
            for quote_id in tweet.quote_ids():
                if quote_id in seen_quote_ids:
                    continue
                seen_quote_ids.add(quote_id)
                quote_path = self._build_path(quote_id)
                contexts.append(
                    QuoteContext(quoted_by_id=tweet_id, quote_id=quote_id, path=quote_path)
                )
                contexts.extend(
                    self._collect_quote_contexts(quote_path, seen_quote_ids=seen_quote_ids)
                )
        return contexts

    def _ensure(self, ids: list[str], *, hydrate_empty: bool = False) -> None:
        wanted = []
        for tweet_id in unique_preserve_order(ids):
            tweet = self.store.get(tweet_id)
            if tweet is None:
                wanted.append(tweet_id)
            elif hydrate_empty and tweet.available and not tweet.text:
                wanted.append(tweet_id)
        if not wanted:
            return
        if self.fetcher is None:
            return
        result = self.fetcher.get_posts(wanted)
        self.store.add_many(result.tweets, cacheable=True)
        for tweet_id, message in result.errors.items():
            if not self.store.contains(tweet_id):
                self.warnings.append(f"Could not fetch tweet {tweet_id}: {message}")
                if not self.strict:
                    self.store.add(missing_tweet(tweet_id, source=f"fetch-error:{message}"))

    def _add_reply_parent_stub(self, tweet: Tweet, parent_id: str) -> None:
        if self.store.contains(parent_id) or not tweet.in_reply_to_username:
            return
        username = tweet.in_reply_to_username.strip("@")
        self.store.add(
            Tweet(
                id=parent_id,
                username=username,
                url=f"https://x.com/{username}/status/{parent_id}",
                source="reply-reference",
            )
        )


def prune_subset_conversations(conversations: list[Conversation]) -> list[Conversation]:
    """Drop branches contained by another branch without an O(n²) scan.

    Largest branches are considered first. For each smaller branch, any
    possible superset must contain every one of its tweet ids, so the rarest
    id's posting list gives a small candidate set to check. Equal branches are
    ordered by their original position, preserving the historical first-one
    wins behavior.
    """
    id_sets = [conversation.all_ids for conversation in conversations]
    ordered = sorted(range(len(conversations)), key=lambda index: (-len(id_sets[index]), index))
    postings: dict[str, list[int]] = {}
    kept: set[int] = set()

    for index in ordered:
        ids = id_sets[index]
        if ids:
            pivot = min(ids, key=lambda tweet_id: len(postings.get(tweet_id, ())))
            candidates = postings.get(pivot, ())
            if any(ids <= id_sets[other] for other in candidates):
                continue
        elif kept:
            continue
        kept.add(index)
        for tweet_id in ids:
            postings.setdefault(tweet_id, []).append(index)

    return [conversation for index, conversation in enumerate(conversations) if index in kept]
