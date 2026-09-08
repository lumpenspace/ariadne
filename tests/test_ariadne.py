from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ariadne.archive import load_archive
from ariadne.cli import CheapFirstFetcher, collect_conversations, is_reply_start, needs_official_metadata
from ariadne.fetch import FetchResult, OEmbedClient, tweet_from_oembed_payload
from ariadne.ids import extract_tweet_ids
from ariadne.models import Conversation, Tweet, TweetRef
from ariadne.reconstruct import ConversationBuilder, prune_subset_conversations
from ariadne.render import render
from ariadne.sources import load_tweets_file, select_user_tweet_ids
from ariadne.store import TweetStore
from ariadne.timeutil import parse_since
from ariadne.unofficial import NitterRssClient, tweets_from_nitter_rss


def argparse_namespace(**overrides):
    defaults = {
        "items": [],
        "input_file": [],
        "archive": [],
        "tweets_file": [],
        "for_user": None,
        "target_user": None,
        "author_id": None,
        "all_loaded": False,
        "replies_only": False,
        "since": None,
        "cheap_first": True,
        "oembed": False,
        "no_oembed": True,
        "hydrate_all": False,
        "fetch": False,
        "fetch_user_timeline": False,
        "unofficial_rss": False,
        "no_unofficial_rss": True,
        "rss_base": [],
        "rss_url_template": [],
        "max_user_pages": None,
        "bearer_token": None,
        "cache": ".ariadne-cache.json",
        "no_cache": True,
        "format": "messages",
        "output": None,
        "strict": False,
        "max_depth": 50,
        "no_quotes": False,
        "allow_empty": False,
    }
    defaults.update(overrides)
    return type("Args", (), defaults)()


class AriadneTests(unittest.TestCase):
    def test_extracts_ids_from_urls_and_lists(self) -> None:
        ids = extract_tweet_ids(
            [
                "https://x.com/alice/status/1234567890123456789",
                "https://twitter.com/i/web/status/234",
                "345, 456",
            ]
        )
        self.assertEqual(ids, ["1234567890123456789", "234", "345", "456"])

    def test_loads_archive_tweets_js_with_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "account.js").write_text(
                'window.YTD.account.part0 = [{"account":{"accountId":"42","username":"alice","accountDisplayName":"Alice"}}];',
                encoding="utf-8",
            )
            payload = [
                {
                    "tweet": {
                        "id_str": "2",
                        "full_text": "reply &amp; text",
                        "created_at": "Wed Jan 01 00:00:01 +0000 2020",
                        "in_reply_to_status_id_str": "1",
                    }
                }
            ]
            (root / "data" / "tweets.js").write_text(
                f"window.YTD.tweets.part0 = {json.dumps(payload)};",
                encoding="utf-8",
            )

            result = load_archive(root)

        self.assertEqual(len(result.tweets), 1)
        tweet = result.tweets[0]
        self.assertEqual(tweet.id, "2")
        self.assertEqual(tweet.text, "reply & text")
        self.assertEqual(tweet.username, "alice")
        self.assertEqual(tweet.reply_parent_id(), "1")

    def test_prunes_subset_branches_but_keeps_forks(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="root", username="root"))
        store.add(Tweet(id="2", text="middle", username="bob", referenced_tweets=[TweetRef("replied_to", "1")]))
        store.add(Tweet(id="3", text="deep", username="carol", referenced_tweets=[TweetRef("replied_to", "2")]))
        store.add(Tweet(id="4", text="fork", username="dana", referenced_tweets=[TweetRef("replied_to", "1")]))

        conversations = ConversationBuilder(store).build(["2", "3", "4"])

        self.assertEqual([conversation.target_id for conversation in conversations], ["3", "4"])
        self.assertEqual(conversations[0].path, ["1", "2", "3"])
        self.assertEqual(conversations[1].path, ["1", "4"])

    def test_prunes_many_nested_branches_without_quadratic_pairing(self) -> None:
        tweet_ids = [str(index) for index in range(1_000)]
        conversations = [
            Conversation(target_id=tweet_ids[index], path=tweet_ids[: index + 1])
            for index in range(1_000)
        ]

        pruned = prune_subset_conversations(conversations)

        self.assertEqual([conversation.target_id for conversation in pruned], ["999"])

    def test_quote_context_reconstructs_quoted_branch(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="root", username="root"))
        store.add(
            Tweet(
                id="2",
                text="reply with quote",
                username="bob",
                referenced_tweets=[TweetRef("replied_to", "1"), TweetRef("quoted", "9")],
            )
        )
        store.add(Tweet(id="8", text="quoted root", username="quinn"))
        store.add(Tweet(id="9", text="quoted reply", username="quinn", referenced_tweets=[TweetRef("replied_to", "8")]))

        conversations = ConversationBuilder(store).build(["2"])

        self.assertEqual(conversations[0].path, ["1", "2"])
        self.assertEqual(conversations[0].quotes[0].path, ["8", "9"])

    def test_messages_render_target_author_as_assistant(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="root", username="alice"))
        store.add(Tweet(id="2", text="reply", username="bob", referenced_tweets=[TweetRef("replied_to", "1")]))
        store.add(Tweet(id="3", text="counter", username="alice", referenced_tweets=[TweetRef("replied_to", "2")]))
        store.add(Tweet(id="4", text="closing", username="bob", referenced_tweets=[TweetRef("replied_to", "3")]))
        conversations = ConversationBuilder(store).build(["4"])

        payload = json.loads(render(conversations, store, output_format="openai"))

        messages = payload["conversations"][0]["messages"]
        self.assertEqual(
            [(message["role"], message["name"]) for message in messages],
            [("user", "alice"), ("assistant", "bob"), ("user", "alice"), ("assistant", "bob")],
        )

    def test_messages_render_missing_target_tweet_as_assistant(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="root", username="alice"))
        store.add(Tweet(id="2", text="", available=False, referenced_tweets=[TweetRef("replied_to", "1")]))
        conversations = ConversationBuilder(store).build(["2"])

        payload = json.loads(render(conversations, store, output_format="openai"))

        messages = payload["conversations"][0]["messages"]
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])

    def test_raft_render_outputs_jsonl_documents(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="root", username="alice", created_at="2024-01-01T00:00:00Z"))
        store.add(Tweet(id="2", text="reply", username="bob", referenced_tweets=[TweetRef("replied_to", "1")]))
        conversations = ConversationBuilder(store).build(["2"])

        rows = [json.loads(line) for line in render(conversations, store, output_format="raft").splitlines()]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["format"], "raft.documents.v1")
        self.assertEqual(rows[0]["id"], "ariadne:2")
        self.assertIn("@alice: root", rows[0]["text"])
        self.assertEqual(rows[0]["metadata"]["tweet_ids"], ["1", "2"])
        self.assertEqual(rows[0]["messages"][0]["role"], "participant")
        self.assertEqual(rows[0]["messages"][1]["role"], "assistant")

    def test_generic_json_file_selects_user_tweets_since_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dump.json"
            path.write_text(
                json.dumps(
                    {
                        "tweets": [
                            {
                                "id": "1",
                                "text": "old",
                                "username": "alice",
                                "created_at": "2023-12-31T23:59:59Z",
                            },
                            {
                                "id": "2",
                                "text": "new",
                                "username": "alice",
                                "created_at": "2024-01-01T00:00:00Z",
                            },
                            {
                                "id": "3",
                                "text": "other",
                                "username": "bob",
                                "created_at": "2024-01-02T00:00:00Z",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = load_tweets_file(path)

        selected = select_user_tweet_ids(
            result.tweets,
            username="alice",
            since=parse_since("2024-01-01"),
        )
        self.assertEqual(selected, ["2"])

    def test_oembed_payload_hydrates_text_and_author(self) -> None:
        tweet = tweet_from_oembed_payload(
            "463440424141459456",
            {
                "author_name": "US Department of the Interior",
                "author_url": "https://x.com/Interior",
                "url": "https://x.com/Interior/status/463440424141459456",
                "html": (
                    '<blockquote class="twitter-tweet">'
                    '<p lang="en" dir="ltr">Sunsets don&#39;t get much better.</p>'
                    '&mdash; US Department of the Interior (@Interior) '
                    '<a href="https://x.com/Interior/status/463440424141459456">May 5, 2014</a>'
                    "</blockquote>"
                ),
            },
            url="https://x.com/Interior/status/463440424141459456",
        )

        self.assertIsNotNone(tweet)
        assert tweet is not None
        self.assertEqual(tweet.text, "Sunsets don't get much better.")
        self.assertEqual(tweet.username, "Interior")
        self.assertEqual(tweet.created_at, "May 5, 2014")

    def test_reply_parent_username_creates_fetchable_stub(self) -> None:
        class FakeFetcher:
            def __init__(self) -> None:
                self.ids: list[str] = []

            def get_posts(self, ids: list[str]):
                from ariadne.fetch import FetchResult

                self.ids.extend(ids)
                return FetchResult(tweets=[Tweet(id="1", text="parent", username="alice")])

        store = TweetStore()
        store.add(
            Tweet(
                id="2",
                text="reply",
                username="bob",
                in_reply_to_id="1",
                in_reply_to_username="alice",
                referenced_tweets=[TweetRef("replied_to", "1")],
            )
        )
        fetcher = FakeFetcher()

        conversations = ConversationBuilder(store, fetcher=fetcher).build(["2"])

        self.assertEqual(conversations[0].path, ["1", "2"])
        self.assertEqual(fetcher.ids, ["1"])
        parent = store.get("1")
        self.assertIsNotNone(parent)
        assert parent is not None
        self.assertEqual(parent.text, "parent")

    def test_leading_reply_mention_creates_parent_url_stub(self) -> None:
        store = TweetStore()
        store.add(
            Tweet(
                id="2",
                text="  @alice hello",
                username="bob",
                referenced_tweets=[TweetRef("replied_to", "1")],
            )
        )

        class FakeFetcher:
            def get_posts(self, ids):
                parent = store.get("1")
                self.url = parent.url if parent else None
                return FetchResult(tweets=[Tweet(id="1", text="prompt", username="alice")])

        fetcher = FakeFetcher()
        [conversation] = ConversationBuilder(store, fetcher=fetcher).build(["2"])
        self.assertEqual(fetcher.url, "https://x.com/alice/status/1")
        self.assertEqual(conversation.path, ["1", "2"])

    def test_explicit_parent_username_beats_leading_mention(self) -> None:
        store = TweetStore()
        store.add(
            Tweet(
                id="2",
                text="@bob hello",
                username="me",
                in_reply_to_username="alice",
                referenced_tweets=[TweetRef("replied_to", "1")],
            )
        )

        class FakeFetcher:
            def get_posts(self, ids):
                self.url = store.get("1").url
                return FetchResult(tweets=[Tweet(id="1", text="prompt", username="alice")])

        fetcher = FakeFetcher()
        ConversationBuilder(store, fetcher=fetcher).build(["2"])
        self.assertEqual(fetcher.url, "https://x.com/alice/status/1")

    def test_nonleading_mention_does_not_supply_parent_context(self) -> None:
        store = TweetStore()
        store.add(
            Tweet(
                id="2",
                text="hello @alice",
                username="bob",
                referenced_tweets=[TweetRef("replied_to", "1")],
            )
        )

        class FakeFetcher:
            def get_posts(self, ids):
                self.parent = store.get("1")
                return FetchResult(skipped={"1": "unsupported"})

        fetcher = FakeFetcher()
        ConversationBuilder(store, fetcher=fetcher).build(["2"])
        self.assertIsNone(fetcher.parent)

    def test_reply_reference_enriches_and_retries_existing_tombstone(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="[deleted]", available=False, source="missing"))
        store.add(
            Tweet(
                id="2",
                text="@alice hello",
                username="bob",
                referenced_tweets=[TweetRef("replied_to", "1")],
            )
        )

        class FakeFetcher:
            def get_posts(self, ids):
                self.url = store.get("1").url
                return FetchResult(tweets=[Tweet(id="1", text="recovered", username="alice")])

        fetcher = FakeFetcher()
        [conversation] = ConversationBuilder(store, fetcher=fetcher).build(["2"])
        self.assertEqual(fetcher.url, "https://x.com/alice/status/1")
        self.assertEqual(conversation.path, ["1", "2"])
        self.assertEqual(store.get("1").text, "recovered")

    def test_oembed_without_context_is_a_provider_skip(self) -> None:
        class NoRequestOEmbed(OEmbedClient):
            def _request(self, url):
                raise AssertionError("contextless ids must not make an HTTP request")

        result = NoRequestOEmbed().get_posts(["1"])
        self.assertEqual(result.tweets, [])
        self.assertEqual(result.errors, {})
        self.assertIn("1", result.skipped)

    def test_many_missing_parents_produce_one_summary_warning(self) -> None:
        store = TweetStore()
        targets = []
        for index in range(20):
            target = f"t{index}"
            parent = f"p{index}"
            targets.append(target)
            store.add(
                Tweet(
                    id=target,
                    text="reply",
                    username="bob",
                    referenced_tweets=[TweetRef("replied_to", parent)],
                )
            )

        class SkippingFetcher:
            def get_posts(self, ids):
                return FetchResult(skipped={tweet_id: "no URL" for tweet_id in ids})

        builder = ConversationBuilder(store, fetcher=SkippingFetcher())
        builder.build(targets)
        summaries = [warning for warning in builder.warnings if "Still unavailable" in warning]
        self.assertEqual(len(summaries), 1)
        self.assertIn("20 tweets", summaries[0])
        self.assertNotIn("canonical tweet URL", " ".join(builder.warnings))

    def test_provider_skip_preserves_strict_reconstruction(self) -> None:
        from ariadne.errors import ReconstructionError

        store = TweetStore()
        store.add(
            Tweet(
                id="2",
                text="reply",
                username="bob",
                referenced_tweets=[TweetRef("replied_to", "1")],
            )
        )

        class SkippingFetcher:
            def get_posts(self, ids):
                return FetchResult(skipped={tweet_id: "unsupported" for tweet_id in ids})

        with self.assertRaises(ReconstructionError):
            ConversationBuilder(store, fetcher=SkippingFetcher(), strict=True).build(["2"])

    def test_nitter_rss_parses_recent_tweets(self) -> None:
        payload = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Alice Example / @alice</title>
    <item>
      <title>Hello from RSS</title>
      <link>https://nitter.net/alice/status/123456789#m</link>
      <guid>123456789</guid>
      <pubDate>Wed, 15 Jul 2026 23:00:20 GMT</pubDate>
      <description>&lt;p&gt;Hello &lt;b&gt;from&lt;/b&gt; RSS&lt;br&gt;second line&lt;/p&gt;</description>
    </item>
  </channel>
</rss>"""

        tweets = tweets_from_nitter_rss(payload, username="alice", source="test")

        self.assertEqual(len(tweets), 1)
        self.assertEqual(tweets[0].id, "123456789")
        self.assertEqual(tweets[0].username, "alice")
        self.assertEqual(tweets[0].name, "Alice Example")
        self.assertEqual(tweets[0].text, "Hello from RSS\nsecond line")
        self.assertEqual(tweets[0].url, "https://x.com/alice/status/123456789")

    def test_nitter_rss_allows_leading_whitespace(self) -> None:
        payload = """
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Alice Example / @alice</title>
    <item>
      <title>Hello</title>
      <link>https://nitter.net/alice/status/123456789#m</link>
      <guid>123456789</guid>
    </item>
  </channel>
</rss>"""

        tweets = tweets_from_nitter_rss(payload, username="alice", source="test")

        self.assertEqual([tweet.id for tweet in tweets], ["123456789"])

    def test_rss_url_template_formats_encoded_username(self) -> None:
        client = NitterRssClient(url_template="https://feeds.example/{username}.xml")

        self.assertEqual(
            client._feed_url("alice bob"),
            "https://feeds.example/alice%20bob.xml",
        )

    def test_cheap_first_fetcher_backfills_oembed_metadata_with_paid_fetcher(self) -> None:
        class FakeFetcher:
            def __init__(self, tweets: list[Tweet]) -> None:
                self.tweets = tweets
                self.calls: list[list[str]] = []

            def get_posts(self, ids: list[str]) -> FetchResult:
                self.calls.append(ids)
                found = [tweet for tweet in self.tweets if tweet.id in ids]
                missing = {tweet_id: "missing" for tweet_id in ids if tweet_id not in {tweet.id for tweet in found}}
                return FetchResult(tweets=found, errors=missing)

        cheap = FakeFetcher([Tweet(id="1", text="cheap text", source="oembed")])
        paid = FakeFetcher([Tweet(id="1", text="paid text", source="x-api", referenced_tweets=[TweetRef("replied_to", "0")])])

        result = CheapFirstFetcher(cheap, paid).get_posts(["1"])

        self.assertEqual(cheap.calls, [["1"]])
        self.assertEqual(paid.calls, [["1"]])
        self.assertEqual([tweet.source for tweet in result.tweets], ["oembed", "x-api"])
        self.assertEqual(result.errors, {})

    def test_archive_source_does_not_need_paid_metadata_when_it_has_reply_edges(self) -> None:
        tweet = Tweet(
            id="2",
            text="reply",
            source="archive:tweets.js",
            referenced_tweets=[TweetRef("replied_to", "1")],
        )

        self.assertFalse(needs_official_metadata(tweet))

    def test_replies_only_requires_structural_reply_metadata(self) -> None:
        self.assertTrue(
            is_reply_start(
                Tweet(
                    id="2",
                    text="reply",
                    source="archive:tweets.js",
                    referenced_tweets=[TweetRef("replied_to", "1")],
                )
            )
        )
        self.assertFalse(
            is_reply_start(
                Tweet(
                    id="3",
                    text="candidate from a mixed posts-plus-replies RSS feed",
                    source="unofficial-rss:https://nitter.net/with_replies/rss",
                )
            )
        )

    def test_collect_conversations_can_select_all_loaded_archive_tweets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dump.json"
            path.write_text(
                json.dumps(
                    {
                        "tweets": [
                            {
                                "id": "1",
                                "text": "old",
                                "username": "alice",
                                "created_at": "2023-12-31T23:59:59Z",
                            },
                            {
                                "id": "2",
                                "text": "new",
                                "username": "alice",
                                "created_at": "2024-01-01T00:00:00Z",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            args = argparse_namespace(
                tweets_file=[str(path)],
                all_loaded=True,
                since="2024-01-01",
                allow_empty=True,
            )
            result = collect_conversations(args)

        self.assertEqual(result.target_ids, ["2"])
        self.assertEqual(len(result.conversations), 1)
        self.assertEqual(result.conversations[0].path, ["2"])

    def test_replies_only_filters_loaded_targets_to_reply_starts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dump.json"
            path.write_text(
                json.dumps(
                    {
                        "tweets": [
                            {
                                "id": "1",
                                "text": "root",
                                "username": "alice",
                                "created_at": "2024-01-01T00:00:00Z",
                            },
                            {
                                "id": "2",
                                "text": "reply",
                                "username": "alice",
                                "created_at": "2024-01-01T00:01:00Z",
                                "in_reply_to_id": "1",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            args = argparse_namespace(
                tweets_file=[str(path)],
                for_user="alice",
                replies_only=True,
                allow_empty=True,
            )
            result = collect_conversations(args)

        self.assertEqual(result.target_ids, ["2"])
        self.assertEqual(len(result.conversations), 1)
        self.assertEqual(result.conversations[0].path, ["1", "2"])

    def test_fixture_archive_reconstructs_branch(self) -> None:
        archive = Path(__file__).resolve().parents[1] / "examples" / "fixture_archive"
        result = load_archive(archive)
        store = TweetStore()
        store.add_many(result.tweets)

        conversations = ConversationBuilder(store).build(["1002"])

        self.assertEqual(conversations[0].path, ["1001", "1002"])



class UnofficialRssFailureTests(unittest.TestCase):
    RSS = (
        b"<?xml version='1.0'?><rss><channel><title>alice / @alice</title>"
        b"<item><guid>https://x.com/alice/status/12345678901</guid>"
        b"<title>hi</title><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>"
        b"</channel></rss>"
    )

    def client_with(self, request_impl):
        client = NitterRssClient(base_url="https://example.invalid")
        client._request = request_impl
        return client

    def test_one_retry_absorbs_a_transient_failure(self):
        import urllib.error

        calls = []

        def flaky(username):
            calls.append(username)
            if len(calls) == 1:
                raise urllib.error.URLError("connection reset")
            return self.RSS

        result = self.client_with(flaky).get_user_posts("alice")
        self.assertEqual(len(calls), 2)
        self.assertEqual([tweet.id for tweet in result.tweets], ["12345678901"])

    def test_tls_eof_is_explained_as_a_blocked_instance(self):
        import ssl
        import urllib.error

        def blocked(username):
            raise urllib.error.URLError(ssl.SSLEOFError("EOF occurred in violation of protocol"))

        result = self.client_with(blocked).get_user_posts("alice")
        self.assertEqual(result.tweets, [])
        blurb = " ".join(result.warnings)
        self.assertIn("blocks non-browser clients", blurb)
        self.assertIn("not a problem with your network", blurb)

    def test_html_challenge_page_is_named_not_parse_errored(self):
        def challenged(username):
            return b"<!doctype html><html><head><title>Making sure...</title></head></html>"

        result = self.client_with(challenged).get_user_posts("alice")
        self.assertEqual(result.tweets, [])
        self.assertIn("bot challenge", " ".join(result.warnings))


class ResponsesOnlyTests(unittest.TestCase):
    """conversation_has_response: the subject must reply to or QT someone else."""

    def build(self, store, target_id):
        from ariadne.api import conversation_has_response

        conversations = ConversationBuilder(store).build([target_id])
        return conversations[0], conversation_has_response

    def test_reply_to_someone_else_is_a_response(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="root", username="alice"))
        store.add(Tweet(id="2", text="reply", username="bob", referenced_tweets=[TweetRef("replied_to", "1")]))
        conversation, has_response = self.build(store, "2")
        self.assertTrue(has_response(conversation, store, subject="bob"))

    def test_standalone_tweet_is_not_a_response(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="just posting", username="bob"))
        conversation, has_response = self.build(store, "1")
        self.assertFalse(has_response(conversation, store, subject="bob"))

    def test_pure_self_thread_is_not_a_response(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="thread 1/2", username="bob"))
        store.add(Tweet(id="2", text="thread 2/2", username="bob", referenced_tweets=[TweetRef("replied_to", "1")]))
        conversation, has_response = self.build(store, "2")
        self.assertFalse(has_response(conversation, store, subject="bob"))

    def test_quote_tweet_is_corpus_not_a_reply_response(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="9", text="quoted", username="alice"))
        store.add(Tweet(id="1", text="QT commentary", username="bob", referenced_tweets=[TweetRef("quoted", "9")]))
        conversation, has_response = self.build(store, "1")
        self.assertFalse(has_response(conversation, store, subject="bob"))

    def test_reply_to_unknown_author_is_not_a_proven_response(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="2", text="@x nope", username="bob", referenced_tweets=[TweetRef("replied_to", "1")]))
        conversation, has_response = self.build(store, "2")
        self.assertFalse(has_response(conversation, store, subject="bob"))

    def test_raft_routes_self_thread_to_corpus(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="thread one", username="bob"))
        store.add(
            Tweet(
                id="2",
                text="thread two",
                username="bob",
                referenced_tweets=[TweetRef("replied_to", "1")],
            )
        )
        [conversation] = ConversationBuilder(store).build(["2"])
        row = json.loads(render([conversation], store, output_format="raft", subject="bob"))
        self.assertEqual(row["kind"], "tweet_thread")
        self.assertEqual(row["metadata"]["dataset_role"], "corpus")
        self.assertEqual(row["metadata"]["classification"], "self_thread")
        self.assertEqual(row["text"], "thread one\n\nthread two")

    def test_raft_routes_only_substantive_other_reply_to_conversation(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="question", username="alice"))
        store.add(
            Tweet(
                id="2",
                text="answer",
                username="bob",
                referenced_tweets=[TweetRef("replied_to", "1")],
            )
        )
        [conversation] = ConversationBuilder(store).build(["2"])
        row = json.loads(render([conversation], store, output_format="raft", subject="bob"))
        self.assertEqual(row["kind"], "tweet_conversation")
        self.assertEqual(row["metadata"]["dataset_role"], "conversation")
        self.assertEqual(row["metadata"]["classification"], "reply")
        self.assertTrue(row["metadata"]["has_subject_response"])

    def test_pipeline_drops_non_responses(self) -> None:
        store_file_tweets = [
            {"id": "1", "text": "standalone", "username": "bob", "created_at": "2024-01-01T00:00:00Z"},
            {"id": "2", "text": "root", "username": "alice", "created_at": "2024-01-01T00:00:00Z"},
            {
                "id": "3",
                "text": "reply",
                "username": "bob",
                "created_at": "2024-01-02T00:00:00Z",
                "in_reply_to_id": "2",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dump.json"
            path.write_text(json.dumps({"tweets": store_file_tweets}), encoding="utf-8")
            args = argparse_namespace(
                tweets_file=[str(path)], for_user="bob", responses_only=True, no_dumps=True
            )
            result = collect_conversations(args)
        self.assertEqual([c.target_id for c in result.conversations], ["3"])
        self.assertTrue(any("responses_only dropped 1" in w for w in result.warnings))


class SubjectRoleTests(unittest.TestCase):
    def test_subject_keeps_assistant_even_when_target_author_is_unknown(self) -> None:
        # The conversation's own target tweet lost its author (e.g. a failed
        # hydration): with an explicit subject the roles still come out right.
        store = TweetStore()
        store.add(Tweet(id="1", text="bob earlier", username="bob"))
        store.add(Tweet(id="2", text="", available=False, referenced_tweets=[TweetRef("replied_to", "1")]))
        conversations = ConversationBuilder(store).build(["2"])

        payload = json.loads(
            render(conversations, store, output_format="openai", subject="bob")
        )

        messages = payload["conversations"][0]["messages"]
        # bob is assistant by author; the unknown-author target stays assistant
        self.assertEqual([m["role"] for m in messages], ["assistant", "assistant"])

    def test_unknown_root_is_user_when_subject_is_known(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="2", text="@x reply", username="bob", referenced_tweets=[TweetRef("replied_to", "1")]))
        conversations = ConversationBuilder(store).build(["2"])

        payload = json.loads(
            render(conversations, store, output_format="openai", subject="bob")
        )

        messages = payload["conversations"][0]["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])


class MarkdownRunMergingTests(unittest.TestCase):
    def build_markdown(self, store, target_id, subject=None):
        conversations = ConversationBuilder(store).build([target_id])
        return render(conversations, store, output_format="markdown", subject=subject)

    def test_consecutive_tweets_by_same_author_merge_into_one_message(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="root", username="alice", created_at="2024-01-01T00:00:00Z", url="https://x.com/alice/status/1"))
        store.add(
            Tweet(
                id="2",
                text="bob 1/2",
                username="bob",
                created_at="2024-01-01T01:00:00Z",
                url="https://x.com/bob/status/2",
                referenced_tweets=[TweetRef("replied_to", "1")],
            )
        )
        store.add(
            Tweet(
                id="3",
                text="bob 2/2",
                username="bob",
                created_at="2024-01-01T02:00:00Z",
                url="https://x.com/bob/status/3",
                referenced_tweets=[TweetRef("replied_to", "2")],
            )
        )

        output = self.build_markdown(store, "3", subject="bob")

        # one header per author run
        self.assertEqual(output.count("### assistant: @bob"), 1)
        self.assertEqual(output.count("### user: @alice"), 1)
        # the run header carries the metadata of both tweets before the texts
        run = output.split("### assistant: @bob", 1)[1]
        header_block = run.split("\n\n", 1)[0]
        self.assertIn("`2024-01-01T01:00:00Z`", header_block)
        self.assertIn("https://x.com/bob/status/2", header_block)
        self.assertIn("`2024-01-01T02:00:00Z`", header_block)
        self.assertIn("https://x.com/bob/status/3", header_block)
        # texts separated by a --- rule (blank-line framed, so it is not
        # parsed as a setext heading)
        self.assertIn("bob 1/2\n\n---\n\nbob 2/2", output)

    def test_different_authors_do_not_merge(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="root", username="alice"))
        store.add(Tweet(id="2", text="reply", username="bob", referenced_tweets=[TweetRef("replied_to", "1")]))
        output = self.build_markdown(store, "2", subject="bob")
        self.assertIn("### user: @alice", output)
        self.assertIn("### assistant: @bob", output)
        self.assertNotIn("---", output)


class CacheRetryTests(unittest.TestCase):
    def cache_with(self, tmp, tweets, missing=None):
        from ariadne.store import save_cache

        path = Path(tmp) / "cache.json"
        save_cache(path, tweets, missing=missing or [])
        return path

    def test_retry_ids_combine_recorded_references_and_stubs(self) -> None:
        from ariadne.store import cache_retry_ids

        tweets = [
            Tweet(id="1", text="has text", username="alice"),
            Tweet(id="2", text="", username="stub"),  # empty stub
            Tweet(id="3", text="reply", username="bob", referenced_tweets=[TweetRef("replied_to", "77")]),
        ]
        # "1" was recorded missing but has since been resolved; "88" has not
        ids = cache_retry_ids(tweets, recorded_missing=["1", "88"])
        self.assertEqual(sorted(ids), ["2", "77", "88"])

    def test_save_cache_records_missing_and_preserves_it_by_default(self) -> None:
        from ariadne.store import load_missing_ids, save_cache

        with tempfile.TemporaryDirectory() as tmp:
            path = self.cache_with(tmp, [Tweet(id="1", text="t", username="a")], missing=["9", "8"])
            self.assertEqual(load_missing_ids(path), ["8", "9"])
            # a save without `missing` keeps the recorded list
            save_cache(path, [Tweet(id="1", text="t", username="a")])
            self.assertEqual(load_missing_ids(path), ["8", "9"])

    def test_cache_summary_counts(self) -> None:
        from ariadne.store import cache_summary

        with tempfile.TemporaryDirectory() as tmp:
            path = self.cache_with(
                tmp,
                [
                    Tweet(id="1", text="t", username="alice", created_at="2024-01-01T00:00:00Z"),
                    Tweet(id="2", text="reply", username="alice", referenced_tweets=[TweetRef("replied_to", "77")]),
                ],
                missing=["88"],
            )
            summary = cache_summary(path)
        self.assertEqual(summary["tweets"], 2)
        self.assertEqual(summary["with_text"], 2)
        self.assertEqual(summary["empty"], 0)
        self.assertEqual(summary["missing"], 2)  # 77 referenced + 88 recorded
        self.assertEqual(summary["authors"][0], ("@alice", 2))

    def test_retry_recovers_and_rerecords_missing(self) -> None:
        from ariadne.api import retry_cache
        from ariadne.store import load_cache, load_missing_ids

        class FakeFetcher:
            def __init__(self):
                self.asked = []

            def get_posts(self, ids):
                self.asked.append(list(ids))
                return FetchResult(
                    tweets=[Tweet(id="77", text="found it", username="carol")],
                    errors={"88": "no source has it"},
                )

        with tempfile.TemporaryDirectory() as tmp:
            path = self.cache_with(
                tmp,
                [Tweet(id="2", text="reply", username="bob", referenced_tweets=[TweetRef("replied_to", "77")])],
                missing=["88"],
            )
            fetcher = FakeFetcher()
            result = retry_cache(path, fetcher=fetcher)

            self.assertEqual(sorted(fetcher.asked[0]), ["77", "88"])
            self.assertEqual(result.recovered, ["77"])
            self.assertEqual(result.still_missing, ["88"])
            self.assertTrue(any("no source has it" in w for w in result.warnings))
            # the recovered tweet is now cached; 88 stays recorded for next time
            cached_ids = {tweet.id for tweet in load_cache(path)}
            self.assertIn("77", cached_ids)
            self.assertEqual(load_missing_ids(path), ["88"])

    def test_retry_limit_does_not_forget_unattempted_ids(self) -> None:
        from ariadne.api import retry_cache
        from ariadne.store import load_missing_ids

        class EmptyFetcher:
            def get_posts(self, ids):
                return FetchResult(tweets=[], errors={})

        with tempfile.TemporaryDirectory() as tmp:
            path = self.cache_with(
                tmp, [Tweet(id="1", text="t", username="a")], missing=["7", "8", "9"]
            )
            result = retry_cache(path, limit=1, fetcher=EmptyFetcher())
            self.assertEqual(len(result.wanted), 1)
            self.assertEqual(load_missing_ids(path), ["7", "8", "9"])

    def test_build_records_unresolved_ids_in_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dump_path = Path(tmp) / "dump.json"
            dump_path.write_text(
                json.dumps(
                    {
                        "tweets": [
                            {
                                "id": "2",
                                "text": "reply to a hole",
                                "username": "bob",
                                "created_at": "2024-01-01T00:00:00Z",
                                "in_reply_to_id": "1",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            cache_path = Path(tmp) / "cache.json"
            args = argparse_namespace(
                tweets_file=[str(dump_path)],
                for_user="bob",
                no_cache=False,
                cache=str(cache_path),
                no_dumps=True,
            )
            result = collect_conversations(args)
            self.assertEqual(result.unresolved_ids(), ["1"])
            result.save_cache(cache_path, force=True)

            from ariadne.store import load_missing_ids

            self.assertEqual(load_missing_ids(cache_path), ["1"])


class MenuNavigationTests(unittest.TestCase):
    def test_menu_step_keys(self):
        from ariadne.hx import _menu_step

        self.assertEqual(_menu_step("down", 0, 3), (1, False))
        self.assertEqual(_menu_step("j", 2, 3), (0, False))  # wraps
        self.assertEqual(_menu_step("k", 0, 3), (2, False))  # wraps
        self.assertEqual(_menu_step("2", 0, 3), (1, False))  # digit jumps
        self.assertEqual(_menu_step("\r", 1, 3), (1, True))

    def test_choose_falls_back_without_a_tty(self):
        import io
        from unittest import mock

        from ariadne.hx import _choose_with_keys

        with mock.patch.object(sys, "stdin", io.StringIO()):
            self.assertIsNone(_choose_with_keys("Pick", ["a", "b"], 0))


class FailingXClient:
    """An X client that raises on every call, like a depleted account."""

    def __init__(self, status: int) -> None:
        from ariadne.fetch import XApiError

        self.status = status
        self.calls = 0
        self._error = XApiError(f"X API request failed with HTTP {status}", status=status)

    def get_posts(self, ids):
        self.calls += 1
        raise self._error

    def get_user_by_username(self, username):
        self.calls += 1
        raise self._error

    def get_user_posts(self, user_id, **kwargs):
        self.calls += 1
        raise self._error


class XApiFailureTests(unittest.TestCase):
    """A failing X API must not throw away what the free sources found."""

    def dump_with_a_hole(self, tmp):
        path = Path(tmp) / "dump.json"
        path.write_text(
            json.dumps(
                {
                    "tweets": [
                        {
                            "id": "2",
                            "text": "reply to a hole",
                            "username": "bob",
                            "created_at": "2024-01-01T00:00:00Z",
                            "in_reply_to_id": "1",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_credits_depleted_degrades_to_a_warning(self) -> None:
        from ariadne.api import FailSoftXClient

        warnings: list[str] = []
        client = FailSoftXClient(FailingXClient(402), warnings)

        result = client.get_posts(["1"])

        self.assertEqual(result.tweets, [])
        self.assertTrue(client.disabled)
        self.assertIn("out of API credits", warnings[0])

    def test_client_switches_itself_off_after_one_failure(self) -> None:
        from ariadne.api import FailSoftXClient

        failing = FailingXClient(402)
        client = FailSoftXClient(failing, [])

        for _ in range(5):
            client.get_posts(["1"])

        # One real attempt, then it stops asking a dead API.
        self.assertEqual(failing.calls, 1)

    def test_user_lookup_failure_returns_none_instead_of_raising(self) -> None:
        from ariadne.api import FailSoftXClient

        client = FailSoftXClient(FailingXClient(500), [])
        self.assertIsNone(client.get_user_by_username("alice"))
        self.assertEqual(client.get_user_posts("7").tweets, [])

    def test_build_survives_a_depleted_account(self) -> None:
        """The whole point: a 402 mid-build still returns the cheap results."""
        from ariadne import api

        with tempfile.TemporaryDirectory() as tmp:
            dump_path = self.dump_with_a_hole(tmp)
            original = api.make_x_client
            api.make_x_client = lambda options: FailingXClient(402)
            try:
                result = api.build_conversations(
                    api.BuildOptions(
                        tweets_file=[str(dump_path)],
                        for_user="bob",
                        fetch=True,
                        bearer_token="token",
                        no_dumps=True,
                        no_cache=True,
                    )
                )
            finally:
                api.make_x_client = original

        self.assertEqual(len(result.conversations), 1)
        self.assertTrue(
            any("X API unavailable" in warning for warning in result.warnings),
            result.warnings,
        )

    def test_rejected_token_is_forgotten_but_a_broke_one_is_kept(self) -> None:
        from ariadne.api import FailSoftXClient

        for status, should_forget in ((401, True), (403, True), (402, False), (500, False)):
            with self.subTest(status=status):
                forgotten = []
                client = FailSoftXClient(
                    FailingXClient(status), [], forget_token=lambda: forgotten.append(True)
                )
                client.get_posts(["1"])
                self.assertEqual(bool(forgotten), should_forget)


class StreamingTests(unittest.TestCase):
    """Paid reads are written to disk the moment they arrive."""

    def test_fetched_tweets_are_streamed_before_a_later_call_fails(self) -> None:
        from ariadne.fetch import XApiClient
        from ariadne.store import append_stream, load_stream, stream_path

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            target = stream_path(cache)
            client = XApiClient("token", sink=lambda tweets: append_stream(target, tweets))
            payloads = [
                {"data": [{"id": "1", "text": "first", "author_id": "9"}]},
                RuntimeError("network died"),
            ]

            def fake_request(path, params):
                item = payloads.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

            client._request_json = fake_request
            with self.assertRaises(RuntimeError):
                client.get_posts([str(n) for n in range(150)])  # two batches

            recovered = load_stream(target)
            self.assertEqual([tweet.id for tweet in recovered], ["1"])
            self.assertEqual(recovered[0].text, "first")

    def test_stream_survives_a_truncated_final_line(self) -> None:
        from ariadne.store import append_stream, load_stream, stream_path

        with tempfile.TemporaryDirectory() as tmp:
            target = stream_path(Path(tmp) / "cache.json")
            append_stream(target, [Tweet(id="1", text="kept", username="alice")])
            with target.open("a", encoding="utf-8") as handle:
                handle.write('{"id": "2", "text": "trunc')

            recovered = load_stream(target)
            self.assertEqual([tweet.id for tweet in recovered], ["1"])

    def test_saving_the_cache_clears_the_stream(self) -> None:
        from ariadne.store import append_stream, save_cache, stream_path

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            target = stream_path(cache)
            append_stream(target, [Tweet(id="1", text="kept", username="alice")])
            self.assertTrue(target.exists())

            save_cache(cache, [Tweet(id="1", text="kept", username="alice")])

            self.assertFalse(target.exists())
            self.assertTrue(cache.exists())  # the cache itself must survive

    def test_clear_stream_refuses_to_delete_a_cache(self) -> None:
        from ariadne.store import clear_stream

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            cache.write_text("{}", encoding="utf-8")
            clear_stream(cache)
            self.assertTrue(cache.exists())

    def test_a_build_recovers_a_previous_runs_stream(self) -> None:
        from ariadne.api import BuildOptions, load_store
        from ariadne.store import append_stream, stream_path

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            append_stream(
                stream_path(cache), [Tweet(id="55", text="paid for", username="alice")]
            )
            store, warnings = load_store(BuildOptions(cache=str(cache)))

            self.assertIsNotNone(store.get("55"))
            self.assertTrue(any("stream file" in warning for warning in warnings))


class DefaultOutputNameTests(unittest.TestCase):
    def test_name_describes_subject_and_format(self) -> None:
        from ariadne.cli import default_output_name

        self.assertEqual(
            default_output_name("markdown", "alice", "2024-01-01"),
            "ariadne-alice-since-2024-01-01.md",
        )
        self.assertEqual(default_output_name("raft", "@bob"), "ariadne-bob.jsonl")
        self.assertEqual(default_output_name("messages"), "ariadne.json")

    def test_hostile_handles_do_not_escape_the_filename(self) -> None:
        from ariadne.cli import default_output_name

        name = default_output_name("json", "../../etc/passwd")
        self.assertNotIn("/", name)
        self.assertTrue(name.endswith(".json"))


class CredentialTests(unittest.TestCase):
    def use_temp_home(self, tmp):
        import os
        from unittest import mock

        return mock.patch.dict(os.environ, {"ARIADNE_HOME": tmp})

    def test_round_trip_and_delete(self) -> None:
        from ariadne.credentials import (
            X_BEARER_TOKEN,
            delete_credential,
            load_credential,
            save_credential,
        )

        with tempfile.TemporaryDirectory() as tmp, self.use_temp_home(tmp):
            self.assertIsNone(load_credential(X_BEARER_TOKEN))
            save_credential(X_BEARER_TOKEN, "secret")
            self.assertEqual(load_credential(X_BEARER_TOKEN), "secret")
            self.assertTrue(delete_credential(X_BEARER_TOKEN))
            self.assertIsNone(load_credential(X_BEARER_TOKEN))
            self.assertFalse(delete_credential(X_BEARER_TOKEN))

    def test_stored_file_is_not_world_readable(self) -> None:
        import os
        import stat

        from ariadne.credentials import X_BEARER_TOKEN, credentials_path, save_credential

        if os.name != "posix":
            self.skipTest("POSIX permissions only")
        with tempfile.TemporaryDirectory() as tmp, self.use_temp_home(tmp):
            save_credential(X_BEARER_TOKEN, "secret")
            mode = credentials_path().stat().st_mode
            self.assertEqual(stat.S_IMODE(mode) & 0o077, 0)

    def test_forget_only_removes_the_token_that_failed(self) -> None:
        from ariadne.api import forget_bearer_token
        from ariadne.credentials import X_BEARER_TOKEN, load_credential, save_credential

        with tempfile.TemporaryDirectory() as tmp, self.use_temp_home(tmp):
            save_credential(X_BEARER_TOKEN, "the-good-one")
            forget_bearer_token("a-different-token")
            self.assertEqual(load_credential(X_BEARER_TOKEN), "the-good-one")
            forget_bearer_token("the-good-one")
            self.assertIsNone(load_credential(X_BEARER_TOKEN))


if __name__ == "__main__":
    unittest.main()
