# Persistent Archive Library

The archive library is for tweet datasets you want to search and reuse across
many runs. Ariadne imports each source into a normalized SQLite database, keeps
it under its settings directory, and automatically uses it when reconstructing
conversations.

This is different from `--archive` and `--tweets-file`, which load a source for
one build only.

## Storage

Imports live under `~/.ariadne/dumps`. Set `ARIADNE_HOME` to move the whole
settings directory:

```bash
export ARIADNE_HOME=/Volumes/research/ariadne
```

On POSIX systems Ariadne creates the settings directory and databases with
owner-only permissions. A successful import is self-contained: later queries
read the normalized database, not the source. Removing an import never removes
or edits its source.

## Supported sources

| Kind | Accepted shape | Explicit `--kind` |
| --- | --- | --- |
| Personal X/Twitter archive | folder, ZIP, or archive data file | `twitter-archive` |
| Community Archive export | CSV folder or ZIP | `community-csv` |
| Parquet collection | one `.parquet` file, or a directory of them | `parquet` |
| Generic tweet dump | CSV, JSON, JSONL, or NDJSON file | `tweets-file` |

### Parquet with a separate profiles file

Bulk parquet exports usually split the data in two: a tweets file keyed by
numeric `account_id`, and a companion file holding the handles. Put both in one
directory and import the directory, not the individual files — imported alone,
the tweets file has no handles to attach, so every post lands with an
unresolved author and `--for-user` can never match it.

```bash
ls ~/DATA/my-export/
# profiles.parquet  tweets.parquet
ariadne dumps import ~/DATA/my-export --name my-export
# resolved 737 accounts from 1 profile file(s)
```

Both spellings of the companion file are recognized: `account_id` +
`username` + `display_name` (Community Archive profile exports) and
`platform_account_id` + `screen_name` + `name` (Borg/Hive `rankings/*.parquet`).
Tweet-side column names that differ between exports — `reply_to_account_id`
for `reply_to_user_id`, `retweeted_tweet_id` for `retweet_id` — are accepted
too, so reply and retweet edges survive the import.

The import reports what it resolved. `0 accounts`, a `parquet files not
imported: ...` note naming your profiles file, or a warning that tweets have
no resolved username all mean the handles did not get attached.

Kind detection is automatic. Use `--kind` when the source is ambiguous.
Parquet support requires the optional extra:

```bash
uv tool install 'ariadne-x[parquet]'
```

## Import

```bash
ariadne dumps import ~/DATA/tpot_dump --name tpot
ariadne dumps import ~/Downloads/community-archive.zip --name community
ariadne dumps import ~/Downloads/twitter-archive.zip --name personal
ariadne dumps import ./research.jsonl --name research
```

Each import stores normalized tweets, accounts, reply and quote references,
provenance, indexes, and (unless `--no-fts` is used) a full-text index.
Re-importing the same name replaces the old database only after the new import
has completed successfully.

```bash
ariadne dumps list
ariadne dumps remove research
```

## Explore

The menu-driven explorer works across all imports by default:

```bash
ariadne dumps interactive
```

The same operations are available as individual commands:

```bash
ariadne dumps users --top 25
ariadne dumps users --find alice --dump community
ariadne dumps search 'local-first' --user alice --since 2020-01-01
ariadne dumps user alice --since 2024-01-01 --limit 50
ariadne dumps show https://x.com/alice/status/1234567890123456789
```

Add repeatable `--dump NAME` flags to restrict `interactive`, `users`,
`search`, `user`, or `show`. With no restriction, duplicate tweet IDs are
merged across imports and complementary metadata is preserved.

`ariadne interactive` and `ariadne dumps interactive` are intentionally
different: the first builds conversation output, while the second explores the
archive library.

## Build conversations across archives

Imported dumps automatically participate in normal builds. The target tweet,
its recursive reply ancestors, and quote context may all come from different
imports:

```bash
ariadne build \
  --for-user alice \
  --since 2024-01-01 \
  --format raft \
  --output alice-conversations.jsonl
```

`--since` limits the user's starting tweets, not older ancestors needed to
complete those branches. Repeat `--dump` to narrow the library, or add
`--no-dumps` to ignore it:

```bash
ariadne build --dump personal --dump community --for-user alice
ariadne build --no-dumps --archive archive.zip 1234567890123456789
```

For safety, a dump-backed user or author timeline above 10,000 posts must be
narrowed with `--since` or given an explicit `--dump-limit`. Explicit limits
select the newest posts first.

## Python API

```python
from ariadne import LocalDumpsClient, import_dump, list_dumps

import_dump("~/Downloads/community.zip", name="community")

client = LocalDumpsClient(names=["community"])
try:
    hits = client.search("golden thread", username="alice", limit=20)
    timeline = client.get_user_posts("alice", since="2024-01-01", limit=50)
finally:
    client.close()

for info in list_dumps():
    print(info.name, info.tweets, info.first_tweet, info.last_tweet)
```

See [Python API](API.md) for the complete build and query interfaces.

## Troubleshooting

- **A Parquet import asks for DuckDB:** install `ariadne-x[parquet]`.
- **Search is slow:** the import may have been created with `--no-fts`; import
  it again without that flag.
- **A build selects too many posts:** add `--since` or `--dump-limit`.
- **A parent is still unavailable:** none of the selected archives knows that
  tweet ID, so it renders as `[deleted]`. Importing another archive that covers
  the same period often fixes it — reply and quote edges are followed across
  every selected import. Enable a structured network source only if local
  coverage is not enough; `ariadne cache retry` can pick the gaps up later
  without repeating the build.
- **Disk usage is high:** each imported database is a normalized, indexed copy
  of the relevant source data. `ariadne dumps remove NAME` removes only that
  copy.
