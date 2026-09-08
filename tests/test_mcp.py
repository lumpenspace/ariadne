from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from ariadne.dumps import import_dump

try:  # the MCP SDK is an optional extra
    import mcp  # noqa: F401

    HAS_MCP = True
except ModuleNotFoundError:  # pragma: no cover - exercised without the extra
    HAS_MCP = False


TWEETS = [
    # carol opens, alice answers her, then alice continues her own thread
    {"id": "1", "text": "the opening claim", "username": "carol",
     "created_at": "2024-01-01T10:00:00Z"},
    {"id": "2", "text": "answering carol", "username": "alice",
     "created_at": "2024-01-02T10:00:00Z", "in_reply_to_id": "1"},
    {"id": "3", "text": "and a second thought", "username": "alice",
     "created_at": "2024-01-02T10:05:00Z", "in_reply_to_id": "2"},
    # a standalone post: corpus material, not a conversation
    {"id": "4", "text": "a standalone remark about labyrinths", "username": "alice",
     "created_at": "2024-01-03T10:00:00Z"},
    # a reply whose parent nothing holds
    {"id": "5", "text": "replying into the void", "username": "alice",
     "created_at": "2024-01-04T10:00:00Z", "in_reply_to_id": "9999"},
]


@unittest.skipUnless(HAS_MCP, "the mcp extra is not installed")
class McpServerTests(unittest.TestCase):
    """The MCP server answers from imported dumps and never uses the network."""

    def setUp(self) -> None:
        self._previous_home = os.environ.get("ARIADNE_HOME")
        self.home = tempfile.mkdtemp(prefix="ariadne-mcp-")
        os.environ["ARIADNE_HOME"] = self.home
        source = Path(self.home) / "dump.jsonl"
        source.write_text(
            "\n".join(json.dumps(row) for row in TWEETS) + "\n", encoding="utf-8"
        )
        import_dump(source, name="fixture")
        from ariadne.mcp_server import build_server

        self.server = build_server()

    def tearDown(self) -> None:
        if self._previous_home is None:
            os.environ.pop("ARIADNE_HOME", None)
        else:
            os.environ["ARIADNE_HOME"] = self._previous_home

    # -- helpers ----------------------------------------------------------

    def call(self, name: str, **arguments) -> str:
        async def run() -> str:
            result = await self.server.call_tool(name, arguments)
            return "\n".join(getattr(c, "text", str(c)) for c in result.content)

        return asyncio.run(run())

    def call_json(self, name: str, **arguments) -> dict:
        return json.loads(self.call(name, response_format="json", **arguments))

    def error(self, name: str, **arguments) -> str:
        from mcp.server.mcpserver.exceptions import ToolError

        with self.assertRaises(ToolError) as caught:
            self.call(name, **arguments)
        return str(caught.exception)

    # -- registration -----------------------------------------------------

    def test_every_tool_is_read_only_and_closed_world(self) -> None:
        """Nothing here may modify state or reach the network."""
        tools = asyncio.run(self.server.list_tools())
        self.assertEqual(len(tools), 7)
        for tool in tools:
            with self.subTest(tool=tool.name):
                self.assertTrue(tool.name.startswith("ariadne_"))
                self.assertTrue(tool.description, "every tool needs a description")
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)
                self.assertFalse(
                    tool.annotations.open_world_hint,
                    "the server is local-only, so nothing is open-world",
                )

    def test_wrapping_preserves_the_generated_schema(self) -> None:
        """The error-translating wrapper must not flatten tool signatures."""
        tools = {tool.name: tool for tool in asyncio.run(self.server.list_tools())}
        search = tools["ariadne_search_tweets"]
        self.assertEqual(search.input_schema.get("required"), ["query"])
        self.assertIn("since", search.input_schema["properties"])

    # -- reading ----------------------------------------------------------

    def test_list_dumps_reports_the_fixture(self) -> None:
        payload = self.call_json("ariadne_list_dumps")
        names = [item["name"] for item in payload["items"]]
        self.assertEqual(names, ["fixture"])
        self.assertEqual(payload["items"][0]["tweets"], len(TWEETS))

    def test_list_users_counts_posts(self) -> None:
        payload = self.call_json("ariadne_list_users")
        by_name = {u["username"]: u["tweets"] for u in payload["items"]}
        self.assertEqual(by_name["alice"], 4)
        self.assertEqual(by_name["carol"], 1)

    def test_search_finds_text(self) -> None:
        payload = self.call_json("ariadne_search_tweets", query="labyrinths")
        self.assertEqual([i["id"] for i in payload["items"]], ["4"])

    def test_paging_reports_more(self) -> None:
        first = self.call_json("ariadne_get_user_posts", username="alice", limit=2)
        self.assertEqual(first["count"], 2)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 2)
        second = self.call_json(
            "ariadne_get_user_posts", username="alice", limit=2, offset=2
        )
        self.assertFalse(second["has_more"])
        self.assertEqual(
            set(i["id"] for i in first["items"]) & set(i["id"] for i in second["items"]),
            set(),
            "pages must not overlap",
        )

    def test_get_thread_walks_ancestors(self) -> None:
        payload = self.call_json("ariadne_get_thread", tweet="3")
        self.assertEqual(payload["target"]["id"], "3")
        self.assertEqual([a["id"] for a in payload["ancestors"]], ["1", "2"])

    def test_get_thread_accepts_a_url(self) -> None:
        payload = self.call_json(
            "ariadne_get_thread", tweet="https://x.com/alice/status/2"
        )
        self.assertEqual(payload["target"]["id"], "2")

    def test_text_is_truncated_on_request(self) -> None:
        payload = self.call_json(
            "ariadne_search_tweets", query="labyrinths", text_chars=12
        )
        self.assertLessEqual(len(payload["items"][0]["text"]), 12)

    # -- conversations ----------------------------------------------------

    def test_build_labels_conversations_and_corpus(self) -> None:
        payload = self.call_json("ariadne_build_conversations", username="alice")
        labels = payload["label_counts"]
        self.assertGreaterEqual(labels.get("reply", 0), 1, payload)
        # the standalone post must not count as a conversation
        conversation_targets = {
            item["target_id"] for item in payload["items"] if item["is_conversation"]
        }
        self.assertNotIn("4", conversation_targets)

    def test_conversations_only_drops_one_sided_branches(self) -> None:
        everything = self.call_json("ariadne_build_conversations", username="alice")
        exchanges = self.call_json(
            "ariadne_build_conversations", username="alice", conversations_only=True
        )
        self.assertLess(exchanges["total"], everything["total"])
        self.assertTrue(all(i["is_conversation"] for i in exchanges["items"]))

    def test_missing_parents_are_counted_not_dropped(self) -> None:
        payload = self.call_json("ariadne_build_conversations", username="alice")
        self.assertGreaterEqual(payload["missing_posts"], 1)
        void = next(i for i in payload["items"] if i["target_id"] == "5")
        self.assertEqual(
            [m["id"] for m in void["messages"]],
            ["9999", "5"],
            "the unresolved parent keeps its place in the branch",
        )
        self.assertFalse(void["messages"][0]["available"])

    def test_subject_turns_are_marked(self) -> None:
        payload = self.call_json("ariadne_build_conversations", username="alice")
        item = next(i for i in payload["items"] if i["target_id"] == "3")
        roles = {m["id"]: m["role"] for m in item["messages"]}
        self.assertEqual(roles["1"], "other")
        self.assertEqual(roles["2"], "subject")

    # -- errors -----------------------------------------------------------

    def test_errors_name_the_problem_and_a_way_forward(self) -> None:
        cases = [
            (("ariadne_list_users",), {"dump": ["nope"]}, "fixture"),
            (("ariadne_get_thread",), {"tweet": "banana"}, "numeric id"),
            (("ariadne_search_tweets",), {"query": "   "}, "search terms"),
            (("ariadne_get_user_posts",), {"username": "nobody"}, "ariadne_list_users"),
            (("ariadne_cache_status",), {"cache": "/nonexistent/x.json"}, "no-cache"),
        ]
        for name, kwargs, expected in cases:
            with self.subTest(tool=name[0]):
                message = self.error(name[0], **kwargs)
                self.assertIn(expected, message)

    def test_unknown_dump_lists_the_real_ones(self) -> None:
        message = self.error("ariadne_list_users", dump=["typo"])
        self.assertIn("typo", message)
        self.assertIn("fixture", message)


@unittest.skipUnless(HAS_MCP, "the mcp extra is not installed")
class McpEntryPointTests(unittest.TestCase):
    def test_cli_exposes_the_mcp_command(self) -> None:
        from ariadne.cli import COMMANDS, build_parser

        self.assertIn("mcp", COMMANDS)
        args = build_parser().parse_args(["mcp"])
        self.assertEqual(args.command, "mcp")


if __name__ == "__main__":
    unittest.main()
