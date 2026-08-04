from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tweet_threader.archive import load_archive
from tweet_threader.fetch import tweet_from_oembed_payload
from tweet_threader.ids import extract_tweet_ids
from tweet_threader.models import Tweet, TweetRef
from tweet_threader.reconstruct import ConversationBuilder
from tweet_threader.render import render
from tweet_threader.sources import load_tweets_file, select_user_tweet_ids
from tweet_threader.store import TweetStore
from tweet_threader.timeutil import parse_since
from tweet_threader.unofficial import tweets_from_nitter_rss


class TweetThreaderTests(unittest.TestCase):
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
        self.assertEqual(rows[0]["id"], "tweet-thread:2")
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
                from tweet_threader.fetch import FetchResult

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

    def test_fixture_archive_reconstructs_branch(self) -> None:
        archive = Path(__file__).resolve().parents[1] / "examples" / "fixture_archive"
        result = load_archive(archive)
        store = TweetStore()
        store.add_many(result.tweets)

        conversations = ConversationBuilder(store).build(["1002"])

        self.assertEqual(conversations[0].path, ["1001", "1002"])


if __name__ == "__main__":
    unittest.main()
