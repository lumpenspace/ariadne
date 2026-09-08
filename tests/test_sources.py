"""Offline tests for the added sources and reconstruction behaviour:
quote-as-reply-root, Community Archive / twitterapi.io parsing, the chain
fetcher, and Bluesky thread reconstruction. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ariadne import bluesky
from ariadne.api import ChainFetcher
from ariadne.fetch import FetchResult
from ariadne.models import Tweet, TweetRef
from ariadne.providers import _tweet_from_ca_row, _tweet_from_tapio
from ariadne.reconstruct import ConversationBuilder
from ariadne.store import TweetStore


class QuoteAsReplyTests(unittest.TestCase):
    def _store(self):
        store = TweetStore()
        store.add(Tweet(id="Q", text="quoted original", username="orig"))
        store.add(Tweet(id="A", text="my take", username="me", referenced_tweets=[TweetRef("quoted", "Q")]))
        store.add(Tweet(id="B", text="a reply", username="fan", referenced_tweets=[TweetRef("replied_to", "A")]))
        return store

    def test_root_quote_becomes_reply_parent(self):
        [conv] = ConversationBuilder(self._store(), fetcher=None).build(["B"])
        self.assertEqual(conv.path, ["Q", "A", "B"])
        self.assertEqual(conv.quotes, [])  # not double-attached

    def test_disabled_keeps_quote_as_context(self):
        [conv] = ConversationBuilder(self._store(), fetcher=None, quote_as_reply=False).build(["B"])
        self.assertEqual(conv.path, ["A", "B"])
        self.assertEqual([(q.quoted_by_id, q.quote_id) for q in conv.quotes], [("A", "Q")])

    def test_mid_thread_quote_is_not_followed(self):
        store = TweetStore()
        store.add(Tweet(id="Q", text="q", username="o"))
        store.add(Tweet(id="R", text="root", username="r"))
        store.add(Tweet(id="A", text="mid", username="me",
                        referenced_tweets=[TweetRef("replied_to", "R"), TweetRef("quoted", "Q")]))
        [conv] = ConversationBuilder(store, fetcher=None).build(["A"])
        self.assertEqual(conv.path, ["R", "A"])
        self.assertEqual([(q.quoted_by_id, q.quote_id) for q in conv.quotes], [("A", "Q")])


class ProviderParsingTests(unittest.TestCase):
    def test_community_archive_row(self):
        row = {
            "tweet_id": "111", "account_id": "9", "username": "me",
            "account_display_name": "Me", "created_at": "2024-01-01T00:00:00Z",
            "full_text": "hello", "reply_to_tweet_id": "100",
            "reply_to_username": "you", "reply_to_user_id": "8", "quoted_tweet_id": "200",
            "conversation_id": "100",
        }
        t = _tweet_from_ca_row(row)
        self.assertEqual((t.id, t.username, t.text), ("111", "me", "hello"))
        self.assertEqual(t.reply_parent_id(), "100")
        self.assertEqual(t.in_reply_to_username, "you")
        self.assertEqual(t.quote_ids(), ["200"])
        self.assertEqual(t.url, "https://x.com/me/status/111")

    def test_twitterapi_io_item(self):
        item = {
            "id": "111", "text": "hi", "createdAt": "Wed Jan 01 00:00:00 +0000 2025",
            "author": {"userName": "me", "id": "9", "name": "Me"},
            "inReplyToId": "100", "inReplyToUsername": "you", "inReplyToUserId": "8",
            "quoted_tweet": {"id": "200"},
        }
        t = _tweet_from_tapio(item)
        self.assertEqual((t.id, t.username), ("111", "me"))
        self.assertEqual(t.reply_parent_id(), "100")
        self.assertEqual(t.quote_ids(), ["200"])

    def test_twitterapi_io_quote_as_bare_id(self):
        t = _tweet_from_tapio({"id": "1", "text": "x", "author": {"userName": "a"}, "quoted_tweet": "200"})
        self.assertEqual(t.quote_ids(), ["200"])


class ChainFetcherTests(unittest.TestCase):
    class Fake:
        def __init__(self, tweets, errors=None):
            self.tweets = tweets
            self.errors = errors or {}
            self.calls = []

        def get_posts(self, ids):
            self.calls.append(list(ids))
            return FetchResult(
                tweets=[t for t in self.tweets if t.id in ids],
                errors={k: v for k, v in self.errors.items() if k in ids},
            )

    def test_second_fetcher_only_sees_unresolved(self):
        first = self.Fake([Tweet(id="A", text="got A", username="x")], errors={"B": "missing"})
        second = self.Fake([Tweet(id="B", text="got B", username="y")])
        chain = ChainFetcher([first, second])
        result = chain.get_posts(["A", "B"])
        got = {t.id: t.text for t in result.tweets}
        self.assertEqual(got, {"A": "got A", "B": "got B"})
        self.assertEqual(second.calls, [["B"]])  # A already resolved, not re-asked
        self.assertEqual(result.errors, {})  # B's error cleared once resolved

    def test_unresolved_error_survives(self):
        first = self.Fake([], errors={"Z": "nope"})
        second = self.Fake([])
        result = ChainFetcher([first, second]).get_posts(["Z"])
        self.assertEqual(result.tweets, [])
        self.assertIn("Z", result.errors)

    def test_provider_skip_reaches_later_fallback(self):
        first = self.Fake([])
        first.get_posts = lambda ids: FetchResult(skipped={tweet_id: "no URL" for tweet_id in ids})
        second = self.Fake([Tweet(id="Z", text="found", username="alice")])
        result = ChainFetcher([first, second]).get_posts(["Z"])
        self.assertEqual([tweet.id for tweet in result.tweets], ["Z"])
        self.assertEqual(result.errors, {})
        self.assertEqual(result.skipped, {})


class BlueskyTests(unittest.TestCase):
    ROOT = "at://did:plc:root/app.bsky.feed.post/r0"
    MID = "at://did:plc:root/app.bsky.feed.post/r1"
    TARGET = "at://did:plc:me/app.bsky.feed.post/r2"

    def _post(self, uri, handle, text, parent=None):
        record = {"text": text, "createdAt": "2026-01-01T00:00:00Z"}
        if parent:
            record["reply"] = {"parent": {"uri": parent}, "root": {"uri": self.ROOT}}
        return {"uri": uri, "author": {"did": f"did:{handle}", "handle": handle,
                                       "displayName": handle}, "record": record}

    def test_web_url(self):
        self.assertEqual(
            bluesky._web_url(self.TARGET, "me.bsky.social"),
            "https://bsky.app/profile/me.bsky.social/post/r2",
        )

    def test_build_reconstructs_ancestor_chain(self):
        posts = {
            self.ROOT: self._post(self.ROOT, "alice", "the root"),
            self.MID: self._post(self.MID, "bob", "middle reply", parent=self.ROOT),
            self.TARGET: self._post(self.TARGET, "me", "my reply", parent=self.MID),
        }

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def get_author_feed(self, actor, *, limit=60):
                return [{"post": posts[BlueskyTests.TARGET]}]

            def ancestor_path(self, uri, store, *, max_depth=60):
                for p in posts.values():
                    bluesky._store_post(store, p)
                return [BlueskyTests.ROOT, BlueskyTests.MID, BlueskyTests.TARGET]

        original = bluesky.BlueskyClient
        bluesky.BlueskyClient = FakeClient
        try:
            result = bluesky.build_bluesky("me", allow_empty=True)
        finally:
            bluesky.BlueskyClient = original

        self.assertEqual(len(result.conversations), 1)
        doc = result.raft_documents()[0]
        self.assertEqual([m["role"] for m in doc["messages"]], ["participant", "participant", "assistant"])
        self.assertEqual([m["author"] for m in doc["messages"]], ["@alice", "@bob", "@me"])
        self.assertEqual(doc["metadata"]["target_url"], "https://bsky.app/profile/me/post/r2")

    def test_reposts_are_skipped(self):
        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def get_author_feed(self, actor, *, limit=60):
                return [{"post": BlueskyTests()._post(BlueskyTests.ROOT, "someone", "not mine"),
                         "reason": {"$type": "app.bsky.feed.defs#reasonRepost"}}]

            def ancestor_path(self, uri, store, *, max_depth=60):
                raise AssertionError("should not reconstruct a repost")

        original = bluesky.BlueskyClient
        bluesky.BlueskyClient = FakeClient
        try:
            result = bluesky.build_bluesky("me", allow_empty=True)
        finally:
            bluesky.BlueskyClient = original
        self.assertEqual(result.conversations, [])


if __name__ == "__main__":
    unittest.main()
