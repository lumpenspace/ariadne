"""Tests for the local dump library (`ariadne.dumps`).

Offline only: imports run off tiny synthetic fixtures written to a per-test
ARIADNE_HOME. Parquet coverage is skipped when duckdb is not installed.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ariadne.api import BuildOptions, build_conversations
from ariadne.dumps import (
    SCHEMA_VERSION,
    LocalDumpsClient,
    detect_kind,
    dumps_dir,
    import_dump,
    list_dumps,
    remove_dump,
)
from ariadne.models import Tweet
from ariadne.store import TweetStore

try:
    import duckdb
except ImportError:
    duckdb = None


ACCOUNT_CSV = """account_id,created_via,username,created_at,account_display_name
1,web,alice,2020-01-01 00:00:00+00,Alice A
2,web,bob,2020-01-01 00:00:00+00,Bob B
"""

MENTIONED_CSV = """user_id,name,screen_name,updated_at
3,Carol,carol,2024-01-01 00:00:00+00
"""

TWEETS_CSV = """tweet_id,account_id,created_at,full_text,retweet_count,favorite_count,reply_to_tweet_id,reply_to_user_id,reply_to_username,archive_upload_id
101,1,2024-01-01 10:00:00+00,root tweet about the labyrinth,1,2,,,,1
102,2,2024-02-01 10:00:00+00,reply holding the thread,0,1,101,1,alice,1
103,1,2024-03-01 10:00:00+00,RT @bob: reply holding the thread,0,0,,,,1
"""


class DumpTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_home = os.environ.get("ARIADNE_HOME")
        self.home = tempfile.mkdtemp(prefix="ariadne-dumps-")
        os.environ["ARIADNE_HOME"] = self.home

    def tearDown(self) -> None:
        if self._previous_home is None:
            os.environ.pop("ARIADNE_HOME", None)
        else:
            os.environ["ARIADNE_HOME"] = self._previous_home

    def write_community_csv(self) -> Path:
        source = Path(self.home) / "community-src"
        source.mkdir()
        (source / "account.csv").write_text(ACCOUNT_CSV, encoding="utf-8")
        (source / "mentioned_users.csv").write_text(MENTIONED_CSV, encoding="utf-8")
        (source / "tweets.csv").write_text(TWEETS_CSV, encoding="utf-8")
        (source / "likes.csv").write_text("id,account_id\n", encoding="utf-8")
        return source

    def write_community_zip(self) -> Path:
        source = Path(self.home) / "community-export.zip"
        with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("2024-9-8/account.csv", ACCOUNT_CSV)
            archive.writestr("2024-9-8/mentioned_users.csv", MENTIONED_CSV)
            archive.writestr("2024-9-8/tweets.csv", TWEETS_CSV)
            archive.writestr("2024-9-8/likes.csv", "id,account_id\n")
        return source

    def write_twitter_zip(self, *, account_id: str | None = "7") -> Path:
        source = Path(self.home) / "twitter-export.zip"
        account_id_field = f'"accountId": "{account_id}", ' if account_id else ""
        with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "data/account.js",
                f'window.YTD.account.part0 = [{{"account": {{{account_id_field}'
                '"username": "zipuser", "accountDisplayName": "Zip User"}}]',
            )
            archive.writestr(
                "data/tweets.js",
                'window.YTD.tweets.part0 = [{"tweet": {"id": "701", '
                '"full_text": "from a real archive", "created_at": "2024-05-01T00:00:00Z"}}]',
            )
        return source


class ImportCommunityCsvTests(DumpTestCase):
    def test_detects_and_imports_the_csv_dump(self) -> None:
        source = self.write_community_csv()
        self.assertEqual(detect_kind(source), "community-csv")
        info = import_dump(source, name="mini")

        self.assertEqual(info.tweets, 3)
        self.assertEqual(info.accounts, 3)  # alice, bob + carol from mentioned_users
        self.assertEqual(info.first_tweet, "2024-01-01T10:00:00Z")
        self.assertEqual(info.last_tweet, "2024-03-01T10:00:00Z")
        self.assertTrue(any("likes.csv" in note for note in info.notes))
        self.assertTrue((dumps_dir() / "mini.db").is_file())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(Path(self.home).stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(dumps_dir().stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((dumps_dir() / "mini.db").stat().st_mode), 0o600)

        listed = list_dumps()
        self.assertEqual([dump.name for dump in listed], ["mini"])
        self.assertEqual(listed[0].kind, "community-csv")

    def test_reimport_replaces_and_remove_deletes(self) -> None:
        source = self.write_community_csv()
        import_dump(source, name="mini")
        import_dump(source, name="mini")
        self.assertEqual(len(list_dumps()), 1)
        remove_dump("mini")
        self.assertEqual(list_dumps(), [])
        with self.assertRaises(FileNotFoundError):
            remove_dump("mini")

    def test_detects_and_imports_nested_csv_zip_without_extracting(self) -> None:
        source = self.write_community_zip()
        self.assertEqual(detect_kind(source), "community-csv")

        info = import_dump(source, name="zipped-mini")

        self.assertEqual(info.kind, "community-csv")
        self.assertEqual(info.tweets, 3)
        self.assertEqual(info.accounts, 3)
        self.assertTrue(any("likes.csv" in note for note in info.notes))
        self.assertFalse((Path(self.home) / "2024-9-8").exists())
        tweet = LocalDumpsClient(names=["zipped-mini"]).get_posts(["102"]).tweets[0]
        self.assertEqual(tweet.username, "bob")
        self.assertEqual(tweet.in_reply_to_id, "101")

    def test_mentioned_account_can_resolve_a_tweet_author(self) -> None:
        source = self.write_community_csv()
        with (source / "tweets.csv").open("a", encoding="utf-8") as fp:
            fp.write("104,3,2024-04-01 10:00:00+00,carol authored this,0,0,,,,1\n")

        import_dump(source, name="mentioned-author")

        tweet = LocalDumpsClient(names=["mentioned-author"]).get_posts(["104"]).tweets[0]
        self.assertEqual(tweet.username, "carol")
        self.assertEqual(tweet.name, "Carol")

    def test_zero_tweet_import_never_creates_or_replaces_a_dump(self) -> None:
        source = self.write_community_csv()
        import_dump(source, name="mini")
        (source / "tweets.csv").write_text(TWEETS_CSV.splitlines()[0] + "\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "No tweets could be imported"):
            import_dump(source, name="mini")
        self.assertEqual(LocalDumpsClient(names=["mini"]).get_posts(["101"]).tweets[0].id, "101")
        self.assertEqual(list(dumps_dir().glob("*.db.tmp")), [])

        with self.assertRaisesRegex(ValueError, "No tweets could be imported"):
            import_dump(source, name="empty")
        self.assertFalse((dumps_dir() / "empty.db").exists())

    def test_replace_failure_cleans_its_unique_staging_database(self) -> None:
        source = self.write_community_csv()

        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                import_dump(source, name="replace-fails")

        self.assertFalse((dumps_dir() / "replace-fails.db").exists())
        self.assertEqual(list(dumps_dir().glob(".*.db.tmp")), [])

    def test_concurrent_same_name_imports_do_not_share_staging_files(self) -> None:
        first = Path(self.home) / "race-first.jsonl"
        second = Path(self.home) / "race-second.jsonl"
        first.write_text('{"id":"601","text":"first winner","username":"one"}\n', encoding="utf-8")
        second.write_text('{"id":"602","text":"second winner","username":"two"}\n', encoding="utf-8")
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def run(source: Path) -> None:
            def progress(message: str) -> None:
                if message.startswith("importing"):
                    barrier.wait(timeout=5)

            try:
                import_dump(source, name="race", progress=progress)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(source,)) for source in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        tweets = LocalDumpsClient(names=["race"]).get_posts(["601", "602"]).tweets
        self.assertEqual(len(tweets), 1)
        self.assertIn(tweets[0].id, {"601", "602"})
        self.assertEqual(list(dumps_dir().glob(".*.db.tmp")), [])

    def test_unknown_zip_is_not_misdetected_as_twitter_archive(self) -> None:
        source = Path(self.home) / "unrelated.zip"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("notes/readme.txt", "not an archive")
        with self.assertRaisesRegex(ValueError, "Could not detect the dump kind"):
            detect_kind(source)

    def test_detects_and_imports_personal_twitter_zip(self) -> None:
        source = self.write_twitter_zip()
        self.assertEqual(detect_kind(source), "twitter-archive")
        info = import_dump(source, name="twitter-mini")
        self.assertEqual(info.tweets, 1)
        self.assertEqual(info.accounts, 1)
        tweet = LocalDumpsClient(names=["twitter-mini"]).get_posts(["701"]).tweets[0]
        self.assertEqual(tweet.username, "zipuser")

    def test_personal_archive_without_account_id_counts_its_account_once(self) -> None:
        source = self.write_twitter_zip(account_id=None)

        info = import_dump(source, name="no-account-id")

        self.assertEqual(info.tweets, 1)
        self.assertEqual(info.accounts, 1)


class LocalDumpsClientTests(DumpTestCase):
    def setUp(self) -> None:
        super().setUp()
        import_dump(self.write_community_csv(), name="mini")
        self.client = LocalDumpsClient()

    def tearDown(self) -> None:
        self.client.close()
        super().tearDown()

    def test_scalar_name_and_context_manager_are_supported(self) -> None:
        client = LocalDumpsClient(names="mini")
        self.assertEqual(client.dump_names(), ["mini"])
        with client as opened:
            self.assertIs(opened, client)
            opened.get_posts(["101"])
            self.assertTrue(opened._connections)
        self.assertEqual(client._connections, {})

    def test_get_posts_resolves_reply_metadata(self) -> None:
        result = self.client.get_posts(["102", "999"])
        self.assertEqual([tweet.id for tweet in result.tweets], ["102"])
        tweet = result.tweets[0]
        self.assertEqual(tweet.in_reply_to_id, "101")
        self.assertEqual(tweet.in_reply_to_username, "alice")
        self.assertEqual(tweet.username, "bob")
        self.assertEqual(tweet.source, "dump:mini")
        self.assertEqual(tweet.reply_parent_id(), "101")

    def test_get_user_posts_filters_retweets_and_since(self) -> None:
        alice = self.client.get_user_posts("alice")
        self.assertEqual([tweet.id for tweet in alice.tweets], ["101"])
        with_retweets = self.client.get_user_posts("@ALICE", include_retweets=True)
        self.assertEqual({tweet.id for tweet in with_retweets.tweets}, {"101", "103"})
        since = self.client.get_user_posts("alice", since="2024-01-15", include_retweets=True)
        self.assertEqual([tweet.id for tweet in since.tweets], ["103"])

    def test_get_author_posts_filters_retweets_since_and_limit(self) -> None:
        posts = self.client.get_author_posts("1")
        self.assertEqual([tweet.id for tweet in posts.tweets], ["101"])
        posts = self.client.get_author_posts("1", include_retweets=True)
        self.assertEqual([tweet.id for tweet in posts.tweets], ["103", "101"])
        posts = self.client.get_author_posts(
            "1", since="2024-02-01", limit=1, include_retweets=True
        )
        self.assertEqual([tweet.id for tweet in posts.tweets], ["103"])

    def test_current_schema_summaries_and_unresolved_metadata_avoid_tweet_scans(self) -> None:
        source = Path(self.home) / "community-src"
        with (source / "tweets.csv").open("a", encoding="utf-8") as fp:
            fp.write("104,99,2024-04-01 10:00:00+00,unknown author,0,0,,,,1\n")
        import_dump(source, name="summaries")
        path = dumps_dir() / "summaries.db"
        with sqlite3.connect(path) as connection:
            meta = dict(connection.execute("SELECT key, value FROM meta"))
            self.assertEqual(meta["version"], SCHEMA_VERSION)
            self.assertEqual(meta["unresolved"], "1")
            alice = connection.execute(
                "SELECT tweets, first_tweet, last_tweet FROM user_summary WHERE username = 'alice'"
            ).fetchone()
            self.assertEqual(alice, (2, "2024-01-01T10:00:00Z", "2024-03-01T10:00:00Z"))
            connection.execute("ALTER TABLE tweets RENAME TO tweets_raw")

        client = LocalDumpsClient(names=["summaries"])
        self.assertEqual(client.users()[0]["username"], "alice")
        self.assertEqual(client.unresolved_count(), 1)

    def test_v1_database_falls_back_to_tweet_aggregates(self) -> None:
        path = dumps_dir() / "mini.db"
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TABLE user_summary")
            connection.execute("UPDATE meta SET value = '1' WHERE key = 'version'")
            connection.execute("DELETE FROM meta WHERE key = 'unresolved'")

        client = LocalDumpsClient(names=["mini"])
        users = client.users()
        self.assertEqual(users[0]["username"], "alice")
        self.assertEqual(users[0]["tweets"], 2)
        self.assertEqual(client.unresolved_count(), 0)

    def test_overlapping_dumps_merge_metadata_provenance_and_user_counts(self) -> None:
        first = Path(self.home) / "first.jsonl"
        first.write_text(
            '{"id":"801","text":"overlap tweet",'
            '"user":{"id_str":"77","screen_name":"mergeuser"},'
            '"created_at":"2024-01-01T00:00:00Z","in_reply_to_id":"800"}\n',
            encoding="utf-8",
        )
        second = Path(self.home) / "second.jsonl"
        second.write_text(
            '{"id":"801","text":"overlap tweet",'
            '"user":{"id_str":"77","screen_name":"mergeuser","name":"Merge User"},'
            '"created_at":"2024-01-01T00:00:00Z","in_reply_to_id":"800",'
            '"conversation_id":"800","quoted_status_id":"799"}\n',
            encoding="utf-8",
        )
        import_dump(first, name="first")
        import_dump(second, name="second")
        client = LocalDumpsClient(names=["first", "second"])

        tweets = [
            client.get_posts(["801"]).tweets[0],
            client.get_user_posts("mergeuser").tweets[0],
            client.get_author_posts("77").tweets[0],
            client.search("overlap")[0],
            client.children("800")[0],
        ]
        for tweet in tweets:
            self.assertEqual(tweet.author_id, "77")
            self.assertEqual(tweet.reply_parent_id(), "800")
            self.assertEqual(tweet.conversation_id, "800")
            self.assertEqual(tweet.quote_ids(), ["799"])
            self.assertEqual(tweet.source, "dump:first,dump:second")
        self.assertEqual(client.users()[0]["tweets"], 1)

    def test_user_counts_support_more_than_ten_dumps(self) -> None:
        names = []
        for index in range(11):
            path = Path(self.home) / f"many-{index}.jsonl"
            path.write_text(
                f'{{"id":"9{index:02d}","text":"tweet {index}","username":"manyuser"}}\n',
                encoding="utf-8",
            )
            name = f"many-{index}"
            import_dump(path, name=name)
            names.append(name)

        users = LocalDumpsClient(names=names).users()
        self.assertEqual(users[0]["username"], "manyuser")
        self.assertEqual(users[0]["tweets"], 11)

    def test_overlapping_dump_can_resolve_an_author_missing_from_the_base(self) -> None:
        base = Path(self.home) / "base.jsonl"
        base.write_text(
            '{"id":"910","text":"unresolved in base"}\n'
            '{"id":"911","text":"base user post","username":"baseuser"}\n',
            encoding="utf-8",
        )
        smaller = Path(self.home) / "smaller.jsonl"
        smaller.write_text(
            '{"id":"910","text":"unresolved in base","username":"alice","name":"Alice"}\n',
            encoding="utf-8",
        )
        import_dump(base, name="base")
        import_dump(smaller, name="smaller")

        client = LocalDumpsClient(names=["base", "smaller"])
        by_name = {user["username"]: user for user in client.users()}
        self.assertEqual(by_name["alice"]["tweets"], 1)
        self.assertEqual(by_name["baseuser"]["tweets"], 1)
        self.assertEqual(client.unresolved_count(), 0)
        self.assertEqual(client.get_posts(["910"]).tweets[0].username, "alice")

    def test_search_children_and_users(self) -> None:
        hits = self.client.search("labyrinth")
        self.assertEqual([tweet.id for tweet in hits], ["101"])
        self.assertEqual([tweet.id for tweet in self.client.children("101")], ["102"])
        users = self.client.users()
        self.assertEqual(users[0]["username"], "alice")
        self.assertEqual(users[0]["tweets"], 2)
        self.assertEqual(self.client.users(match="bo")[0]["username"], "bob")

    def test_unknown_dump_name_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            LocalDumpsClient(names=["nope"])

    def test_invalid_query_date_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Could not parse date"):
            self.client.search("labyrinth", since="not-a-date")

    def test_query_limits_must_be_positive_integers(self) -> None:
        calls = (
            lambda value: self.client.get_user_posts("alice", limit=value),
            lambda value: self.client.get_author_posts("1", limit=value),
            lambda value: self.client.search("labyrinth", limit=value),
            lambda value: self.client.children("101", limit=value),
        )
        for call in calls:
            for invalid in (-1, 0, True, 1.5):
                with self.subTest(call=call, invalid=invalid):
                    with self.assertRaisesRegex(ValueError, "must be a positive integer"):
                        call(invalid)


class BuildPipelineTests(DumpTestCase):
    def setUp(self) -> None:
        super().setUp()
        import_dump(self.write_community_csv(), name="mini")

    def offline(self, **overrides) -> BuildOptions:
        base = dict(no_cache=True, no_oembed=True, no_unofficial_rss=True)
        base.update(overrides)
        return BuildOptions(**base)

    def import_limit_timeline(self) -> None:
        path = Path(self.home) / "limit.jsonl"
        path.write_text(
            '{"id":"501","text":"older post","username":"limituser",'
            '"created_at":"2024-01-01T00:00:00Z"}\n'
            '{"id":"502","text":"newer post","username":"limituser",'
            '"created_at":"2024-02-01T00:00:00Z"}\n',
            encoding="utf-8",
        )
        import_dump(path, name="limit")

    def test_dump_completes_the_reply_parent(self) -> None:
        result = build_conversations(self.offline(items=["102"]))
        self.assertEqual(len(result.conversations), 1)
        self.assertEqual(result.conversations[0].path, ["101", "102"])
        self.assertIn("dump:mini", result.store.get("101").source.split(","))

    def test_pipeline_closes_its_dump_client_on_every_return_path(self) -> None:
        cases = (
            (self.offline(items=["102"]), None),
            (self.offline(target_user="nobody", allow_empty=True), None),
            (self.offline(target_user="nobody"), RuntimeError),
        )
        for options, raised in cases:
            with self.subTest(options=options, raised=raised):
                client = LocalDumpsClient(names="mini")
                with patch("ariadne.api.make_dumps_client", return_value=client):
                    if raised is None:
                        build_conversations(options)
                    else:
                        with self.assertRaises(raised):
                            build_conversations(options)
                self.assertEqual(client._connections, {})

    def test_target_user_timeline_comes_from_dumps(self) -> None:
        result = build_conversations(self.offline(target_user="alice"))
        self.assertEqual(result.target_ids, ["101"])
        self.assertTrue(any("Local dumps (mini)" in warning for warning in result.warnings))

    def test_target_user_branch_resolves_its_parent_from_another_dump(self) -> None:
        child_source = Path(self.home) / "child-archive.jsonl"
        child_source.write_text(
            '{"id":"602","text":"cross-archive reply","username":"crossuser",'
            '"created_at":"2024-03-01T00:00:00Z","in_reply_to_status_id":"601"}\n',
            encoding="utf-8",
        )
        parent_source = Path(self.home) / "parent-archive.jsonl"
        parent_source.write_text(
            '{"id":"601","text":"parent from another archive","username":"parentuser",'
            '"created_at":"2024-02-01T00:00:00Z"}\n',
            encoding="utf-8",
        )
        import_dump(child_source, name="child-archive")
        import_dump(parent_source, name="parent-archive")

        result = build_conversations(self.offline(target_user="crossuser"))

        self.assertEqual(result.target_ids, ["602"])
        self.assertEqual(result.conversations[0].path, ["601", "602"])
        self.assertEqual(result.store.get("601").source, "dump:parent-archive")

    def test_author_id_timeline_comes_from_dumps(self) -> None:
        result = build_conversations(self.offline(author_id="2"))
        self.assertEqual(result.target_ids, ["102"])
        self.assertEqual(result.conversations[0].path, ["101", "102"])
        self.assertTrue(any("author 2" in warning for warning in result.warnings))

    def test_dump_enriches_a_nonempty_cached_target(self) -> None:
        store = TweetStore()
        store.add(Tweet(id="102", text="cached reply", username="bob", source="oembed"))

        result = build_conversations(self.offline(items=["102"]), base_store=store)

        self.assertEqual(result.conversations[0].path, ["101", "102"])
        target = result.store.get("102")
        self.assertEqual(target.reply_parent_id(), "101")
        self.assertIn("dump:mini", target.source.split(","))

    def test_no_dumps_disables_the_source(self) -> None:
        result = build_conversations(self.offline(items=["102"], no_dumps=True, allow_empty=True))
        tweet = result.store.get("102")
        self.assertFalse(tweet.available)
        self.assertEqual(tweet.source, "missing")

    def test_large_dump_timeline_requires_an_explicit_limit(self) -> None:
        self.import_limit_timeline()
        with patch("ariadne.api.DEFAULT_MAX_LOCAL_POSTS", 1):
            with self.assertRaisesRegex(RuntimeError, "more than 1 posts"):
                build_conversations(self.offline(target_user="limituser"))

    def test_explicit_dump_limit_selects_the_newest_posts(self) -> None:
        self.import_limit_timeline()
        result = build_conversations(self.offline(target_user="limituser", dump_limit=1))

        self.assertEqual(result.target_ids, ["502"])
        self.assertTrue(any("limited to the newest 1 post" in warning for warning in result.warnings))

    def test_dump_limit_must_be_positive(self) -> None:
        for invalid in (0, -1, True, 1.5):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "dump_limit must be a positive integer"):
                    build_conversations(self.offline(target_user="alice", dump_limit=invalid))


class GenericFileTests(DumpTestCase):
    def test_jsonl_dump_roundtrips(self) -> None:
        path = Path(self.home) / "extra.jsonl"
        path.write_text(
            '{"id": "201", "text": "hello thread", "username": "carol", "created_at": "2023-05-05T00:00:00Z"}\n'
            '{"id": "202", "text": "a reply", "username": "dan", "in_reply_to_status_id": "201"}\n',
            encoding="utf-8",
        )
        self.assertEqual(detect_kind(path), "tweets-file")
        info = import_dump(path, name="extra")
        self.assertEqual(info.tweets, 2)
        self.assertEqual(info.accounts, 2)
        client = LocalDumpsClient(names=["extra"])
        self.assertEqual(client.get_posts(["202"]).tweets[0].in_reply_to_id, "201")
        self.assertEqual(client.get_user_posts("carol").tweets[0].created_at, "2023-05-05T00:00:00Z")


@unittest.skipIf(duckdb is None, "duckdb is not installed")
class ParquetTests(DumpTestCase):
    def test_borg_layout_with_rankings(self) -> None:
        source = Path(self.home) / "tpot-src"
        (source / "rankings").mkdir(parents=True)
        con = duckdb.connect()
        con.execute("SET TimeZone='UTC'")
        con.execute(
            f"""
            COPY (
                SELECT * FROM (VALUES
                    (301, 'first parquet tweet', 11, CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR),
                     CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), 5, 9,
                     TIMESTAMPTZ '2022-03-03 12:00:00+00'),
                    (302, 'parquet reply', 12, '301', '11', NULL, NULL, 0, 1,
                     TIMESTAMPTZ '2022-03-04 12:00:00+00')
                ) AS t(id, text, author_id, in_reply_to_status_id, in_reply_to_user_id,
                       quoted_status_id, retweeted_status_id, retweet_count, favorite_count, created_at)
            ) TO '{source / "tweets.parquet"}' (FORMAT PARQUET)
            """
        )
        con.execute(
            f"""
            COPY (
                SELECT * FROM (VALUES
                    (11, 'dave', 'Dave D'), (12, 'erin', 'Erin E')
                ) AS t(platform_account_id, screen_name, name)
            ) TO '{source / "rankings" / "cluster.parquet"}' (FORMAT PARQUET)
            """
        )
        con.close()

        self.assertEqual(detect_kind(source), "parquet")
        info = import_dump(source, name="tpot-mini")
        self.assertEqual(info.tweets, 2)
        self.assertEqual(info.accounts, 2)

        client = LocalDumpsClient(names=["tpot-mini"])
        reply = client.get_posts(["302"]).tweets[0]
        self.assertEqual(reply.username, "erin")
        self.assertEqual(reply.in_reply_to_id, "301")
        self.assertEqual(reply.created_at, "2022-03-04T12:00:00Z")
        self.assertEqual(client.get_user_posts("dave").tweets[0].id, "301")

    def test_community_archive_profiles_resolve_tweet_authors(self) -> None:
        """A tweets+profiles export must not import as 10M anonymous rows.

        The tweets file carries only account_id; the handles live in a sibling
        profiles file that spells its columns differently from the rankings
        layout, and the reply/retweet columns are named differently again.
        """
        source = Path(self.home) / "ca-src"
        source.mkdir(parents=True)
        con = duckdb.connect()
        con.execute("SET TimeZone='UTC'")
        con.execute(
            f"""
            COPY (
                SELECT * FROM (VALUES
                    ('501', '31', TIMESTAMPTZ '2026-01-01 10:00:00+00', 'the parent',
                     CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR),
                     CAST(NULL AS VARCHAR), '501'),
                    ('502', '32', TIMESTAMPTZ '2026-01-02 10:00:00+00', 'a reply to it',
                     '501', '31', CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), '501'),
                    ('503', '32', TIMESTAMPTZ '2026-01-03 10:00:00+00', 'a quote of it',
                     CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), '501',
                     CAST(NULL AS VARCHAR), '503')
                ) AS t(tweet_id, account_id, created_at, full_text,
                       reply_to_tweet_id, reply_to_account_id, quoted_tweet_id,
                       retweeted_tweet_id, conversation_id)
            ) TO '{source / "tweets.parquet"}' (FORMAT PARQUET)
            """
        )
        con.execute(
            f"""
            COPY (
                SELECT * FROM (VALUES
                    ('31', 'gina', 'Gina G'), ('32', 'hank', 'Hank H')
                ) AS t(account_id, username, display_name)
            ) TO '{source / "profiles.parquet"}' (FORMAT PARQUET)
            """
        )
        con.close()

        info = import_dump(source, name="ca-mini")
        self.assertEqual(info.tweets, 3)
        self.assertEqual(info.accounts, 2)
        self.assertNotIn(
            "profiles.parquet",
            " ".join(info.notes),
            "the profiles file must be imported, not skipped as unrecognized",
        )

        client = LocalDumpsClient(names=["ca-mini"])
        parent = client.get_posts(["501"]).tweets[0]
        self.assertEqual(parent.username, "gina")
        self.assertEqual(parent.name, "Gina G")

        reply = client.get_posts(["502"]).tweets[0]
        self.assertEqual(reply.username, "hank")
        self.assertEqual(reply.in_reply_to_id, "501")
        # reply_to_account_id, not reply_to_user_id, in this layout
        self.assertEqual(reply.in_reply_to_user_id, "31")

        quote = client.get_posts(["503"]).tweets[0]
        self.assertEqual(quote.quote_ids(), ["501"])

        # selection by handle only works when the profiles were applied
        self.assertEqual(
            sorted(t.id for t in client.get_user_posts("hank").tweets), ["502", "503"]
        )

    def test_quoted_tweet_we_do_not_hold_is_still_named(self) -> None:
        """A quoted tweet absent from the dump must still get a handle.

        oEmbed can only fetch a tweet it can build a URL for. A reply parent
        gets one from the reply's own metadata; a quote target has no such
        source except the quoted permalink among the export's expanded urls.
        Without this the tweet is unnameable and unfetchable by any free
        source.
        """
        source = Path(self.home) / "quote-src"
        source.mkdir(parents=True)
        con = duckdb.connect()
        con.execute("SET TimeZone='UTC'")
        con.execute(
            f"""
            COPY (
                SELECT * FROM (VALUES
                    ('701', '51', TIMESTAMPTZ '2026-03-01 10:00:00+00', 'quoting them',
                     '700', [{{'1': 'https://t.co/abc', '2': 'https://twitter.com/jo/status/700', '3': 'x'}}])
                ) AS t(tweet_id, account_id, created_at, full_text, quoted_tweet_id, urls)
            ) TO '{source / "tweets.parquet"}' (FORMAT PARQUET)
            """
        )
        con.execute(
            f"""
            COPY (SELECT * FROM (VALUES ('51', 'kim', 'Kim K'))
                  AS t(account_id, username, display_name)
            ) TO '{source / "profiles.parquet"}' (FORMAT PARQUET)
            """
        )
        con.close()

        import_dump(source, name="quote-mini")
        client = LocalDumpsClient(names=["quote-mini"])

        # the quoted tweet itself is not in the dump...
        self.assertEqual(
            client.get_user_posts("jo").tweets, [], "the quoted tweet is not held"
        )
        # ...but asking for it by id yields a named, fetchable stub
        stub = client.get_posts(["700"]).tweets
        self.assertEqual(len(stub), 1)
        self.assertEqual(stub[0].username, "jo")
        self.assertEqual(stub[0].url, "https://x.com/jo/status/700")
        self.assertEqual(stub[0].text, "", "a stub must carry no text")

        # an unrelated id must not invent anything
        self.assertEqual(client.get_posts(["999"]).tweets, [])

    def test_permalink_author_requires_a_matching_id(self) -> None:
        from ariadne.dumps import author_from_status_url

        self.assertEqual(
            author_from_status_url("https://twitter.com/jo/status/700", "700"), "jo"
        )
        self.assertEqual(author_from_status_url("https://x.com/jo/status/700"), "jo")
        # a link to a *different* tweet must not name this one
        self.assertIsNone(
            author_from_status_url("https://twitter.com/jo/status/701", "700")
        )
        self.assertIsNone(author_from_status_url("https://example.com/jo", "700"))
        self.assertIsNone(author_from_status_url(None, "700"))

    def test_retweeted_tweet_id_marks_a_retweet(self) -> None:
        source = Path(self.home) / "rt-src"
        source.mkdir(parents=True)
        con = duckdb.connect()
        con.execute("SET TimeZone='UTC'")
        con.execute(
            f"""
            COPY (
                SELECT * FROM (VALUES
                    ('601', '41', TIMESTAMPTZ '2026-02-01 10:00:00+00', 'RT text', '600')
                ) AS t(tweet_id, account_id, created_at, full_text, retweeted_tweet_id)
            ) TO '{source / "tweets.parquet"}' (FORMAT PARQUET)
            """
        )
        con.execute(
            f"""
            COPY (SELECT * FROM (VALUES ('41', 'ivy', 'Ivy I'))
                  AS t(account_id, username, display_name)
            ) TO '{source / "profiles.parquet"}' (FORMAT PARQUET)
            """
        )
        con.close()

        import_dump(source, name="rt-mini")
        client = LocalDumpsClient(names=["rt-mini"])
        # retweets are excluded from a timeline unless asked for, which only
        # works if retweeted_tweet_id landed in retweet_of_id
        self.assertEqual(client.get_user_posts("ivy").tweets, [])
        self.assertEqual(
            len(client.get_user_posts("ivy", include_retweets=True).tweets), 1
        )

    def test_rankings_layout_still_detected_as_accounts(self) -> None:
        """Widening profile detection must not break the older layout."""
        from ariadne.dumps import _account_columns

        self.assertEqual(
            _account_columns({"platform_account_id", "screen_name", "name"}),
            ("platform_account_id", "screen_name", "name"),
        )
        self.assertEqual(
            _account_columns({"account_id", "username", "display_name"}),
            ("account_id", "username", "display_name"),
        )
        # a tweets file must never be mistaken for an account file
        self.assertIsNone(_account_columns({"tweet_id", "full_text", "account_id"}))

    def test_enriched_layout_single_file(self) -> None:
        path = Path(self.home) / "enriched.parquet"
        con = duckdb.connect()
        con.execute("SET TimeZone='UTC'")
        con.execute(
            f"""
            COPY (
                SELECT * FROM (VALUES
                    (401, 21, 'fay', 'Fay F', TIMESTAMPTZ '2024-06-01 08:00:00+00', 'an enriched tweet',
                     3, 4, CAST(NULL AS BIGINT), CAST(NULL AS BIGINT), CAST(NULL AS VARCHAR), 400, 401)
                ) AS t(tweet_id, account_id, username, account_display_name, created_at, full_text,
                       retweet_count, favorite_count, reply_to_tweet_id, reply_to_user_id,
                       reply_to_username, quoted_tweet_id, conversation_id)
            ) TO '{path}' (FORMAT PARQUET)
            """
        )
        con.close()

        info = import_dump(path, name="enriched-mini")
        self.assertEqual(info.tweets, 1)
        tweet = LocalDumpsClient(names=["enriched-mini"]).get_posts(["401"]).tweets[0]
        self.assertEqual(tweet.username, "fay")
        self.assertEqual(tweet.quote_ids(), ["400"])
        self.assertEqual(tweet.conversation_id, "401")
        self.assertEqual(tweet.created_at, "2024-06-01T08:00:00Z")


if __name__ == "__main__":
    unittest.main()
