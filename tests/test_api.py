"""Tests for the programmatic API (`ariadne.build` and friends).

Offline only: everything here runs off the fixture archive and local dumps,
no X API, oEmbed, or RSS calls.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ariadne
from ariadne.api import BuildOptions, BuildResult, build_conversations

FIXTURE_ARCHIVE = Path(__file__).resolve().parents[1] / "examples" / "fixture_archive"


def offline(**overrides) -> dict:
    """Build kwargs that never touch the network or the on-disk cache."""
    base = {"no_cache": True, "no_oembed": True, "no_unofficial_rss": True}
    base.update(overrides)
    return base


class BuildOptionsTests(unittest.TestCase):
    def test_bare_string_is_accepted_for_list_fields(self) -> None:
        options = BuildOptions(archive="a.zip", tweets_file="b.jsonl", items="123")
        self.assertEqual(options.archive, ["a.zip"])
        self.assertEqual(options.tweets_file, ["b.jsonl"])
        self.assertEqual(options.items, ["123"])

    def test_none_list_fields_become_empty_lists(self) -> None:
        options = BuildOptions(archive=None, input_file=None)
        self.assertEqual(options.archive, [])
        self.assertEqual(options.input_file, [])

    def test_coerce_accepts_namespace_mapping_and_attribute_objects(self) -> None:
        expected = ["x.zip"]
        namespace = BuildOptions.coerce(argparse.Namespace(archive=["x.zip"], strict=True))
        self.assertEqual(namespace.archive, expected)
        self.assertTrue(namespace.strict)

        mapping = BuildOptions.coerce({"archive": ["x.zip"], "strict": True})
        self.assertEqual(mapping.archive, expected)
        self.assertTrue(mapping.strict)

        # Objects exposing options as class attributes (not in __dict__).
        attrs = BuildOptions.coerce(type("Args", (), {"archive": ["x.zip"], "strict": True})())
        self.assertEqual(attrs.archive, expected)
        self.assertTrue(attrs.strict)

    def test_coerce_ignores_unknown_keys_and_is_identity_for_options(self) -> None:
        options = BuildOptions(archive=["a.zip"])
        self.assertIs(BuildOptions.coerce(options), options)
        self.assertEqual(BuildOptions.coerce({"archive": ["a.zip"], "nonsense": 1}).archive, ["a.zip"])


class BuildResultTests(unittest.TestCase):
    def build(self) -> BuildResult:
        return ariadne.build(**offline(archive=str(FIXTURE_ARCHIVE), all_loaded=True))

    def test_build_reconstructs_the_fixture_branch(self) -> None:
        result = self.build()
        self.assertEqual(len(result.conversations), 1)
        self.assertEqual(result.conversations[0].path, ["1001", "1002"])
        self.assertTrue(result)
        self.assertEqual(len(result), 1)
        self.assertEqual([c.target_id for c in result], ["1002"])

    def test_raft_documents_match_the_rendered_jsonl(self) -> None:
        result = self.build()
        documents = result.raft_documents()
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["format"], "raft.documents.v1")
        self.assertEqual(documents[0]["metadata"]["target_id"], "1002")
        rendered = [json.loads(line) for line in result.render("raft").splitlines()]
        self.assertEqual(rendered, documents)

    def test_messages_match_the_rendered_json(self) -> None:
        result = self.build()
        messages = result.messages()
        self.assertEqual(
            [message["role"] for message in messages[0]["messages"]],
            ["assistant", "user"],
        )
        rendered = json.loads(result.render("messages"))
        self.assertEqual(rendered["conversations"], messages)

    def test_strict_openai_messages_drop_extra_keys(self) -> None:
        result = self.build()
        for message in result.messages(strict_openai=True)[0]["messages"]:
            self.assertEqual(set(message), {"role", "name", "content"})

    def test_json_payload_matches_rendered_json(self) -> None:
        result = self.build()
        self.assertEqual(json.loads(result.render("json")), result.json_payload())

    def test_save_writes_the_requested_format(self) -> None:
        result = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            path = result.save(Path(tmp) / "nested" / "out.jsonl", "raft")
            self.assertTrue(path.exists())
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows, result.raft_documents())

    def test_tweets_exposes_the_store(self) -> None:
        result = self.build()
        self.assertEqual(sorted(tweet.id for tweet in result.tweets), ["1001", "1002"])

    def test_save_cache_is_skipped_when_nothing_was_fetched(self) -> None:
        result = self.build()
        self.assertFalse(result.should_save_cache)
        self.assertIsNone(result.save_cache())


class PipelineTests(unittest.TestCase):
    def test_allow_empty_returns_an_empty_result_instead_of_raising(self) -> None:
        result = ariadne.build(**offline(archive=str(FIXTURE_ARCHIVE), for_user="nobody", allow_empty=True))
        self.assertEqual(result.conversations, [])
        self.assertEqual(result.target_ids, [])
        self.assertFalse(result)
        self.assertTrue(any("No tweet IDs matched" in w for w in result.warnings))

    def test_no_matching_targets_raises_without_allow_empty(self) -> None:
        with self.assertRaises(RuntimeError):
            ariadne.build(**offline(archive=str(FIXTURE_ARCHIVE), for_user="nobody"))

    def test_fetch_without_a_token_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            ariadne.build(**offline(archive=str(FIXTURE_ARCHIVE), all_loaded=True, fetch=True, bearer_token=None))

    def test_generic_dump_selects_by_user_and_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dump.json"
            path.write_text(
                json.dumps(
                    {
                        "tweets": [
                            {"id": "1", "text": "old", "username": "alice", "created_at": "2023-12-31T00:00:00Z"},
                            {"id": "2", "text": "new", "username": "alice", "created_at": "2024-01-02T00:00:00Z"},
                            {"id": "3", "text": "other", "username": "bob", "created_at": "2024-01-02T00:00:00Z"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = ariadne.build(**offline(tweets_file=str(path), for_user="alice", since="2024-01-01"))
        self.assertEqual(result.target_ids, ["2"])

    def test_build_conversations_accepts_options_object(self) -> None:
        options = BuildOptions(**offline(archive=str(FIXTURE_ARCHIVE), all_loaded=True))
        result = build_conversations(options)
        self.assertIs(result.options, options)
        self.assertEqual(len(result.conversations), 1)

    def test_base_store_is_reused_across_passes(self) -> None:
        first = ariadne.build(**offline(archive=str(FIXTURE_ARCHIVE), all_loaded=True))
        second = build_conversations(
            BuildOptions(**offline(all_loaded=True)),
            base_store=first.store,
        )
        self.assertIs(second.store, first.store)
        self.assertEqual(second.conversations[0].path, ["1001", "1002"])


class PackageSurfaceTests(unittest.TestCase):
    def test_public_names_are_exported(self) -> None:
        for name in ("build", "build_conversations", "BuildOptions", "BuildResult", "Tweet", "TweetStore", "render"):
            self.assertTrue(hasattr(ariadne, name), name)

    def test_cli_still_exposes_its_historical_names(self) -> None:
        from ariadne import cli

        for name in ("collect_conversations", "CheapFirstFetcher", "is_reply_start", "needs_official_metadata"):
            self.assertTrue(hasattr(cli, name), name)
        self.assertIs(cli.collect_conversations, build_conversations)


if __name__ == "__main__":
    unittest.main()
