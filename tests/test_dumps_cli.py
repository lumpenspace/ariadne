"""CLI coverage for the persistent local dump explorer."""

from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import ariadne.dumps as dumps_module
from ariadne.cli import main
from ariadne.dumps import import_dump


ACCOUNT_CSV = """account_id,created_via,username,created_at,account_display_name
1,web,alice,2020-01-01 00:00:00+00,Alice A
2,web,bob,2020-01-01 00:00:00+00,Bob B
"""

TWEETS_CSV = """tweet_id,account_id,created_at,full_text,retweet_count,favorite_count,reply_to_tweet_id,reply_to_user_id,reply_to_username,archive_upload_id
101,1,2024-01-01 10:00:00+00,root tweet about the labyrinth,1,2,,,,1
102,2,2024-02-01 10:00:00+00,reply holding the thread,0,1,101,1,alice,1
"""


class DumpsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_home = os.environ.get("ARIADNE_HOME")
        self._previous_plain = os.environ.get("HYPERPLEX_PLAIN")
        self._tmp = tempfile.TemporaryDirectory(prefix="ariadne-dumps-cli-")
        os.environ["ARIADNE_HOME"] = self._tmp.name
        os.environ["HYPERPLEX_PLAIN"] = "1"
        source = Path(self._tmp.name) / "source"
        source.mkdir()
        (source / "account.csv").write_text(ACCOUNT_CSV, encoding="utf-8")
        (source / "tweets.csv").write_text(TWEETS_CSV, encoding="utf-8")
        import_dump(source, name="mini")

    def tearDown(self) -> None:
        self._tmp.cleanup()
        if self._previous_home is None:
            os.environ.pop("ARIADNE_HOME", None)
        else:
            os.environ["ARIADNE_HOME"] = self._previous_home
        if self._previous_plain is None:
            os.environ.pop("HYPERPLEX_PLAIN", None)
        else:
            os.environ["HYPERPLEX_PLAIN"] = self._previous_plain

    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(argv)
        return status, stdout.getvalue(), stderr.getvalue()

    def import_jsonl(self, name: str, rows: list[str]) -> None:
        source = Path(self._tmp.name) / f"{name}.jsonl"
        source.write_text("\n".join(rows) + "\n", encoding="utf-8")
        import_dump(source, name=name)

    def test_exploration_subcommands(self) -> None:
        status, output, _ = self.run_cli(["dumps", "list"])
        self.assertEqual(status, 0)
        self.assertIn("mini", output)
        self.assertIn("2 tweets", output)

        status, output, _ = self.run_cli(["dumps", "users", "--find", "ali"])
        self.assertEqual(status, 0)
        self.assertIn("alice", output)
        self.assertNotIn("bob", output)

        status, output, _ = self.run_cli(["dumps", "search", "labyrinth"])
        self.assertEqual(status, 0)
        self.assertIn("101", output)

        status, output, _ = self.run_cli(["dumps", "user", "bob"])
        self.assertEqual(status, 0)
        self.assertIn("102", output)

        status, output, _ = self.run_cli(["dumps", "show", "102"])
        self.assertEqual(status, 0)
        self.assertIn("101", output)
        self.assertIn("102", output)

    def test_interactive_search_then_quit(self) -> None:
        with (
            patch("ariadne.cli.hx.choose", side_effect=[0, 6]),
            patch("ariadne.cli.prompt", side_effect=["labyrinth", "", "", "5"]),
        ):
            status, output, error = self.run_cli(["dumps", "interactive"])

        self.assertEqual(status, 0)
        self.assertIn("101", output)
        self.assertIn("scope: mini", error)

    def test_interactive_can_switch_scope(self) -> None:
        source = Path(self._tmp.name) / "second.jsonl"
        source.write_text('{"id":"201","text":"second dump","username":"carol"}\n', encoding="utf-8")
        import_dump(source, name="second")

        # Main menu: change scope; scope menu: second; main menu: quit.
        with patch("ariadne.cli.hx.choose", side_effect=[4, 2, 6]):
            status, _, error = self.run_cli(["dumps", "interactive"])

        self.assertEqual(status, 0)
        self.assertIn("scope: all (mini, second)", error)
        self.assertIn("scope: second", error)

    def test_interactive_labels_a_multi_dump_subset_as_selected(self) -> None:
        self.import_jsonl("second", ['{"id":"201","text":"second","username":"carol"}'])
        self.import_jsonl("third", ['{"id":"301","text":"third","username":"dan"}'])

        with patch("ariadne.cli.hx.choose", return_value=6):
            status, _, error = self.run_cli(
                ["dumps", "interactive", "--dump", "mini", "--dump", "second"]
            )

        self.assertEqual(status, 0)
        self.assertIn("scope: selected (mini, second)", error)

    def test_interactive_recovers_from_action_errors(self) -> None:
        # Missing tweet (RuntimeError), invalid date (ValueError), and invalid
        # positive integer (argparse error) must each return to the main menu.
        with (
            patch("ariadne.cli.hx.choose", side_effect=[3, 0, 1, 6]),
            patch(
                "ariadne.cli.prompt",
                side_effect=[
                    "999",
                    "5",
                    "labyrinth",
                    "",
                    "not-a-date",
                    "5",
                    "",
                    "0",
                ],
            ),
        ):
            status, _, error = self.run_cli(["dumps", "interactive"])

        self.assertEqual(status, 0)
        self.assertIn("Tweet 999 is not in the selected dumps", error)
        self.assertIn("Could not parse date: not-a-date", error)
        self.assertIn("Expected a positive integer", error)

    def test_show_caps_direct_replies_and_reports_truncation(self) -> None:
        self.import_jsonl(
            "many-replies",
            [
                '{"id":"301","text":"reply root","username":"alice","created_at":"2024-01-01T00:00:00Z"}',
                '{"id":"302","text":"first direct reply","username":"bob","created_at":"2024-01-02T00:00:00Z","in_reply_to_status_id":"301"}',
                '{"id":"303","text":"second direct reply","username":"carol","created_at":"2024-01-03T00:00:00Z","in_reply_to_status_id":"301"}',
                '{"id":"304","text":"third direct reply","username":"dan","created_at":"2024-01-04T00:00:00Z","in_reply_to_status_id":"301"}',
            ],
        )

        status, output, error = self.run_cli(
            ["dumps", "show", "301", "--dump", "many-replies", "--reply-limit", "2"]
        )

        self.assertEqual(status, 0)
        self.assertIn("replies in dumps (showing first 2)", output)
        self.assertIn("first direct reply", output)
        self.assertIn("second direct reply", output)
        self.assertNotIn("third direct reply", output)
        self.assertIn("more direct replies omitted", error)

    def test_show_stops_at_an_ancestor_cycle(self) -> None:
        self.import_jsonl(
            "cycle",
            [
                '{"id":"401","text":"cycle target","username":"alice","in_reply_to_status_id":"402"}',
                '{"id":"402","text":"cycle parent","username":"bob","in_reply_to_status_id":"401"}',
            ],
        )

        status, output, error = self.run_cli(["dumps", "show", "401", "--dump", "cycle"])

        self.assertEqual(status, 0)
        self.assertEqual(output.count("cycle target"), 1)
        self.assertIn("ancestor cycle detected at tweet 401", error)

    def test_interactive_show_prompts_with_configured_reply_limit(self) -> None:
        with (
            patch("ariadne.cli.hx.choose", side_effect=[3, 6]),
            patch("ariadne.cli.prompt", side_effect=["101", "7"]) as prompt_mock,
        ):
            status, output, _ = self.run_cli(
                ["dumps", "interactive", "--reply-limit", "7"]
            )

        self.assertEqual(status, 0)
        self.assertIn("101", output)
        prompt_mock.assert_any_call("Maximum direct replies", default="7")

    def test_interactive_handles_closed_input_and_interrupt(self) -> None:
        for error, expected_status, message in (
            (EOFError(), 0, "input closed"),
            (KeyboardInterrupt(), 130, "interrupted"),
        ):
            with self.subTest(error=type(error).__name__):
                with patch("ariadne.cli.hx.choose", side_effect=error):
                    status, _, stderr = self.run_cli(["dumps", "interactive"])
                self.assertEqual(status, expected_status)
                self.assertIn(message, stderr)

    def test_storage_errors_are_reported_without_a_traceback(self) -> None:
        with patch("ariadne.cli.list_dumps", side_effect=sqlite3.OperationalError("disk I/O error")):
            status, _, stderr = self.run_cli(["dumps", "list"])

        self.assertEqual(status, 2)
        self.assertIn("ariadne: disk I/O error", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_import_backend_errors_are_normalized_for_the_cli(self) -> None:
        source = Path(self._tmp.name) / "backend.jsonl"
        source.write_text('{"id":"999","text":"unused"}\n', encoding="utf-8")

        class BackendFailure(Exception):
            pass

        def fail_backend(*_args, **_kwargs):
            raise BackendFailure("backend rejected input")

        with patch.dict(dumps_module._IMPORTERS, {"tweets-file": fail_backend}):
            status, _, stderr = self.run_cli(["dumps", "import", str(source), "--name", "broken"])

        self.assertEqual(status, 2)
        self.assertIn("ariadne: Could not import", stderr)
        self.assertIn("backend rejected input", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(list((Path(self._tmp.name) / "dumps").glob(".*.db.tmp")), [])


if __name__ == "__main__":
    unittest.main()
