<p align="center">
  <img src="site/public/ariadne-thread-v2.png" alt="A golden conversation thread crossing several archives" width="100%">
</p>

<h1 align="center">⌇ ariadne</h1>

<p align="center">
  <strong>Find the conversation.</strong><br>
  Turn scattered X/Twitter archives and tweet datasets into readable, attributable reply branches.
</p>

<p align="center">
  <a href="https://ariadne.hyperplex.org">documentation</a> ·
  <a href="https://pypi.org/project/ariadne-x/">PyPI</a> ·
  <a href="docs/API.md">Python API</a> ·
  <a href="LICENSE">MIT</a>
</p>

---

A social export remembers posts. The conversation around them is often somewhere
else: a parent in another archive, a quote in a community dataset, an older post in
the cache.

Ariadne merges those sources, selects the posts you care about, follows every known
reply-parent chain toward its root, attaches quote context, and renders the result
root → target.

It does not pretend sparse data is complete. A post it cannot resolve keeps its
place in the branch rather than vanishing from it: one it knows nothing about
renders as `[deleted]` and raises a warning, while one it can name but never
fetched keeps its author and reads `[tweet <id> has no text]`. Either way the
gap is visible and countable. Ask for `--strict` when you would rather fail
than keep a partial branch.

## Start here

Requires Python 3.11 or newer. The distribution is `ariadne-x`; the command and
import are both `ariadne`.

```bash
uv tool install ariadne-x
ariadne interactive
```

Or build directly from a personal archive:

```bash
ariadne build \
  --archive ~/Downloads/twitter-archive.zip \
  --for-user alice \
  --since 2024-01-01 \
  --format markdown \
  --output conversations.md
```

That is the whole basic loop:

```text
archives + dumps + cache
          ↓
     choose targets
          ↓
follow known parent IDs
          ↓
 quotes + root-to-target branches
```

## Choose your path

| You have… | Use… |
| --- | --- |
| One X/Twitter export | `ariadne build --archive PATH …` |
| CSV, JSON, JSONL, or NDJSON | `ariadne build --tweets-file PATH …` |
| Tweet IDs or X URLs | Pass them after `ariadne build` |
| Archives you will reuse | `ariadne dumps import PATH` |
| A public X account | `ariadne build --target-user USER …` |
| A Bluesky handle | `ariadne bluesky HANDLE …` |

Imported archives form a local, searchable library:

```bash
ariadne dumps import ~/Downloads/twitter-archive.zip --name personal
ariadne dumps search "remembered phrase" --user alice
ariadne dumps show https://x.com/alice/status/1234567890123456789

# Imported dumps join ordinary builds automatically.
ariadne build --for-user alice --since 2024-01-01 --format raft -o alice.jsonl
```

Each import becomes a self-contained SQLite database under `~/.ariadne/dumps`.
The source is never modified, and removing an import never removes the source.
Parquet imports additionally need DuckDB:

```bash
uv tool install 'ariadne-x[parquet]'
```

[Read the archive library guide →](docs/DUMPS.md)

## From Python

The CLI is a thin front end over a typed synchronous API:

```python
from pathlib import Path
import ariadne

options = ariadne.BuildOptions(
    archive=Path.home() / "Downloads" / "twitter-archive.zip",
    for_user="alice",
    since="2024-01-01",
)

result = ariadne.build(options)

for conversation in result:
    print(conversation.target_id)

documents = result.raft_documents()
result.save("out/branches.jsonl", "raft")
```

Use `no_dumps=True` when a build must ignore the persistent archive library.
Named failures derive from `AriadneError`, including `ConfigurationError`,
`NoTargetsError`, `ReconstructionError`, and `SourceError`.

[Read the Python API reference →](docs/API.md)

## Pick an output

| Format | Shape | Good for |
| --- | --- | --- |
| `messages` | enriched JSON conversations | chat-like data with tweet metadata; the CLI default |
| `openai` | reduced JSON conversations | nested `role`, `name`, and `content` messages |
| `json` | normalized graph + tweets | analysis, provenance, and custom rendering |
| `markdown` | text | humans, notebooks, and review |
| `raft` | one JSON object per line | retrieval, chunking, and embedding |

The `openai` renderer keeps Ariadne's conversation envelope; consumers extract
`conversations[i].messages`. Roles follow authorship: the collected user's
tweets speak as `assistant`, everyone else's as `user` (`participant` in the
raft format) — even when a conversation's own tweets can't tell, because the
build knows who was collected. In `markdown`, consecutive tweets by the same
person merge into one message: one role header, the metadata of every tweet in
the run, then the texts separated by `---` rules.

Raft output classifies every branch before handoff. A substantive reply to a
known different author is a `tweet_conversation`; standalone posts,
self-threads, quote commentary, and replies with unresolved prompts are
`tweet_thread` corpus rows. This keeps authored threads as long-form source
material without inventing Q/A turns from missing or self-authored parents.

Pass `--responses-only` (API: `responses_only=True`) only when you explicitly
want to discard those corpus rows and retain proven cross-user replies.
`ariadne interactive` keeps both by default.

[Inspect the schemas →](docs/SCHEMA.md)

## What Ariadne follows

- One target's ancestor path back to its root—not sibling replies or a whole tree.
- Older parents even when `--since` limits the starting targets.
- Reply and quote edges across different imported dumps.
- Quote context, with root quote-tweets spliced onto their quoted post by default.

Ordinary archive builds stay local. `--target-user` is the convenience exception: it
tries unofficial RSS and oEmbed unless disabled. Those sources can recover recent text
but usually cannot prove reply edges. X API reads are separately opt-in through
`--fetch` and `--fetch-user-timeline` and may be billable.

`ariadne interactive` runs the free sources first and, before it offers the X API,
says how many tweets are missing and how many conversations they would complete —
so a billable pass is a decision made against a number.

[Read the source and network policy →](docs/SOURCES.md)

## A few useful commands

```bash
ariadne inspect-archive ~/Downloads/twitter-archive.zip
ariadne dumps interactive
ariadne build --help

# Bluesky uses its public API and the same renderers.
ariadne bluesky alice.bsky.social --since 2024-01-01 --format raft
```

## Picking up where a build left off

Every build that fetches anything writes a cache — `.ariadne-cache.json` unless
you point `--cache PATH` somewhere else or pass `--no-cache` — and since 0.6 it
also records the tweet ids it could *not* resolve, so the holes survive the
session:

```bash
ariadne cache list                 # what each cache holds, and what is still missing
ariadne cache missing              # the missing ids, one per line
ariadne cache retry                # fetch them now, updating the cache in place
```

`cache retry` (API: `ariadne.retry_cache()`) tries local dumps and free
oEmbed by default; add `--community-archive`, `--twitterapi-key`, or
`--fetch` with an X API token for the stubborn ones, and `--limit` to bound
a run. Re-running the original `ariadne build` afterwards picks the
recovered tweets up from the cache. The cache write is last-writer-wins, so
retry after a build using the same cache has finished, not alongside it.

## When the X API says no

X is the last and most expensive rung, so a failure there does not end the
run. Out of credits, rejected token, rate limit, or simply down: the failure
becomes a warning, X switches off for the rest of the build, and everything
the free sources already reconstructed is still rendered.

```text
warning: X API unavailable, continuing without it: the account is out of API
credits (HTTP 402). Everything the other sources found is kept.
```

Paid reads are never held in memory waiting for a run to succeed. Each batch
is appended to `<cache>.stream.jsonl` the moment it arrives, so a run that
dies later has still banked what it paid for; the next build folds that file
back in and clears it once the tweets are safely in the cache.

A bearer token that works is remembered in `~/.ariadne/credentials.json`
(owner-only) so you are not asked for it again. A token the API *rejects* is
forgotten immediately — but one that merely ran out of credits is kept, since
it will work again once the account is topped up.

`ariadne interactive` climbs the rungs in cost order, recounting the gap
between each, and stops as soon as it closes: local and cache, then free
RSS and oEmbed, then the free Community Archive, then twitterapi.io, and only
then X.

## Reference

- [Documentation site](https://ariadne.hyperplex.org)
- [Persistent archive library](docs/DUMPS.md)
- [Python API](docs/API.md)
- [Source behavior](docs/SOURCES.md)
- [Output schemas](docs/SCHEMA.md)
- [Raft handoff](docs/RAFT.md)

## Development

```bash
uv sync --extra dev --extra parquet
uv run pytest
uv run ruff check .
```

Those two are what CI runs. The package ships `py.typed`, so type-checking a
consumer's code against it is expected to work; the repo's own tree is not
mypy-clean yet, and mypy is deliberately not in the dev extra.

MIT licensed. The thread was there all along.
