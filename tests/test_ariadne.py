from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ariadne.archive import load_archive
from ariadne.cli import CheapFirstFetcher, collect_conversations, is_reply_start, needs_official_metadata
from ariadne.fetch import FetchResult, tweet_from_oembed_payload
from ariadne.ids import extract_tweet_ids
from ariadne.models import Tweet, TweetRef
from ariadne.reconstruct import ConversationBuilder
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

    def test_messages_render_root_as_assistant(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="1", text="root", username="alice"))
        store.add(Tweet(id="2", text="reply", username="bob", referenced_tweets=[TweetRef("replied_to", "1")]))
        conversations = ConversationBuilder(store).build(["2"])

        payload = json.loads(render(conversations, store, output_format="openai"))

        messages = payload["conversations"][0]["messages"]
        self.assertEqual(messages[0]["role"], "assistant")
        self.assertEqual(messages[0]["name"], "alice")
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["name"], "bob")

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
        self.assertEqual(rows[0]["messages"][1]["role"], "participant")

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


if __name__ == "__main__":
    unittest.main()
