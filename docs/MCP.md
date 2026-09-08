# MCP Server

Ariadne can serve your imported archives to an MCP client — Claude Code,
Claude Desktop, or anything else that speaks the protocol — so a model can
search the library and rebuild conversations without you shuttling files
around.

```bash
uv tool install 'ariadne-x[mcp]'
ariadne mcp          # or: ariadne-mcp
```

It speaks [MCP](https://modelcontextprotocol.io) over stdio and reads the same
`~/.ariadne/dumps` library the CLI uses. Import an archive first, or every
tool will tell you there is nothing to read:

```bash
ariadne dumps import ~/Downloads/twitter-archive.zip --name personal
```

## Connecting a client

Most clients take a JSON block like this:

```json
{
  "mcpServers": {
    "ariadne": {
      "command": "ariadne-mcp"
    }
  }
}
```

Set `ARIADNE_HOME` in the server's `env` if your library lives somewhere
other than `~/.ariadne`. In Claude Code the equivalent one-liner is:

```bash
claude mcp add ariadne -- ariadne-mcp
```

## It is local-only, on purpose

Ariadne can reach oEmbed, unofficial RSS, the Community Archive,
twitterapi.io and the X API. **The MCP server reaches none of them.**

Two of those cost money per read, and an agent deciding on its own to
complete a few thousand missing parents is a bill you did not agree to. The
others are slow enough that a single tool call could stall for minutes. So
every tool here answers from local SQLite and nothing else — the tools are
annotated `readOnlyHint: true` and `openWorldHint: false`, and that is
accurate rather than aspirational.

When a run *should* go to the network, drive it from the terminal, where the
cost is visible and the escalation is yours to approve:

```bash
ariadne build --for-user alice --oembed --format raft -o alice.jsonl
ariadne cache retry --cache .ariadne-cache.json
```

## Tools

| Tool | What it answers |
| --- | --- |
| `ariadne_list_dumps` | Which archives are imported, their size and date span |
| `ariadne_list_users` | Which handles are present, with post counts |
| `ariadne_search_tweets` | Full-text search across the archives |
| `ariadne_get_user_posts` | One handle's posts, newest first |
| `ariadne_get_thread` | One tweet with its ancestors, quotes and replies |
| `ariadne_build_conversations` | Rebuilt conversations, labelled by kind |
| `ariadne_cache_status` | What a build cache holds and still lacks |

Every tool takes `response_format`: `markdown` (default, compact and
readable) or `json` (complete records for programmatic use).

### Start here

`ariadne_list_dumps` and `ariadne_list_users` exist because the other tools
need an *exact* handle. A handle absent from `list_users` has no posts in the
library even if other posts mention it — a distinction that otherwise looks
like a bug.

### `ariadne_build_conversations`

The substantive one. For each of a handle's posts it walks reply and quote
edges back to the root, then labels the branch:

- `dataset_role: "conversation"` — the handle substantively answered a
  different, identified author. A real exchange.
- `dataset_role: "corpus"` — one-sided. The `label` says which kind:
  `standalone`, `self_thread`, `quote`, or `incomplete_reply` (a reply whose
  parent no archive holds).

Pass `conversations_only: true` to keep only the exchanges. Expect the count
to drop hard: a corpus is usually mostly one-sided, and a reply whose other
half was never archived cannot be a conversation.

Posts the archives lack keep their place in the branch and are counted in
`missing_posts` rather than being silently dropped, so a gap is visible.

## Result size

Conversations are large, so the defaults are deliberately small: 5
conversations per call (max 50) and 20 items for the other tools (max 200).
Tweet text is truncated to 600 characters unless you raise `text_chars`.

Every list is paginated. Pass `offset` to continue from `next_offset`:

```json
{"count": 5, "offset": 0, "has_more": true, "next_offset": 5, "items": [...]}
```

`total` appears only when it was actually counted. Timelines and searches
over-fetch by one row instead of counting a 48,000-post timeline, so they
report `has_more` honestly and omit `total` rather than inventing one.

## Limits worth knowing

A handle with more than 10,000 archived posts will not build without a bound
— the same guardrail the CLI has. The error says so and names the fix; pass
`since` to narrow the range or `max_posts` to cap how many posts seed the
walk.

Errors generally name a next step: an unknown dump lists the real ones, an
unknown handle points at `ariadne_list_users`, and a missing cache explains
where caches come from.

## Troubleshooting

- **"No archives have been imported yet"** — run `ariadne dumps import
  <path>`, or point `ARIADNE_HOME` at the library you meant.
- **The command is not found** — the SDK is an optional extra; install
  `ariadne-x[mcp]`.
- **A tweet is in the archive but `ariadne_get_thread` cannot find it** —
  check the `dump` scope, and remember ids must be numeric or full status
  URLs.
- **Everything is empty for a handle you can see mentioned** — mentions do
  not mean the library holds that account's posts. Confirm with
  `ariadne_list_users`.
