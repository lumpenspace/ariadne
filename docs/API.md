# Python API

Everything the `ariadne build` command does is available as a function call.
The CLI is a thin argparse front-end over this module.

```python
import ariadne

result = ariadne.build(archive="~/twitter-archive.zip", for_user="alice")

for document in result.raft_documents():
    print(document["metadata"]["target_id"], document["text"][:80])
```

## `build(...)`

Runs the whole pipeline — load sources, select targets, reconstruct reply
branches, hydrate missing text — and returns a `BuildResult`.

Keyword names match the CLI flags with dashes turned into underscores:
`--for-user` is `for_user`, `--no-quotes` is `no_quotes`. Two do not line up
exactly: `quote_as_reply` defaults to `True` and is turned off by the
`--no-quote-as-reply` flag, and `allow_empty` is API-only — the CLI always
raises when nothing matches. List-valued options accept a bare string, so
`archive="a.zip"` and `archive=["a.zip"]` are the same.
Filesystem inputs also accept `pathlib.Path` and other `os.PathLike` objects.
`BuildKwargs` and `OutputFormat` describe the complete typed surface, and the
wheel includes a `py.typed` marker.

```python
# From a local archive
ariadne.build(archive="archive.zip", for_user="alice", since="2024-01-01")

# From a generic dump, replies only
ariadne.build(tweets_file="dump.jsonl", for_user="alice", replies_only=True)

# From explicit tweet IDs or URLs
ariadne.build(items=["https://x.com/alice/status/123", "456"])

# Reuse imported local dumps (all by default, or restrict by name)
ariadne.build(for_user="alice", dump=["tpot"], since="2024-01-01")

# An arbitrary public account, cheap sources before any paid API reads
ariadne.build(target_user="alice")

# Allow the X API, after the cheap pass
ariadne.build(target_user="alice", fetch=True, fetch_user_timeline=True,
              bearer_token="...")
```

For a reusable, inspectable configuration, pass a `BuildOptions` object as the
single positional argument:

```python
from pathlib import Path
from ariadne import BuildOptions, build

options = BuildOptions(archive=Path("archive.zip"), for_user="alice")
options.validate()
result = build(options)
```

Do not combine the positional object with keyword options. Credentials stored
on `BuildOptions` are excluded from its representation, so logging a result or
options object does not print bearer tokens or twitterapi.io keys.

Common options, all optional:

| Option | Meaning |
| --- | --- |
| `archive`, `tweets_file`, `items`, `input_file` | Where tweets come from |
| `for_user`, `target_user`, `author_id`, `all_loaded` | Which tweets become branch targets |
| `since` | Only select targets on or after this date |
| `replies_only` | Only start from tweets that are replies |
| `oembed`, `unofficial_rss`, `cheap_first` | Free hydration sources |
| `fetch`, `fetch_user_timeline`, `bearer_token`, `max_user_pages` | X API v2 |
| `community_archive`, `twitterapi_key` | Structured non-X-API network sources |
| `dump`, `no_dumps`, `dump_limit` | Persistent local dump selection and timeline safety bound |
| `cache`, `no_cache` | Tweet cache, default `.ariadne-cache.json` |
| `strict`, `max_depth`, `no_quotes`, `quote_as_reply` | Reconstruction behaviour |
| `responses_only` | Keep only branches in which the subject responds to somebody else |
| `allow_empty` | Return an empty result instead of raising when nothing matches |
| `format`, `output` | Defaults used by `result.render()` and the CLI |

`responses_only=True` drops standalone posts and pure self-threads, keeping
branches where the subject replied to or quote-tweeted someone — the shape most
finetuning and Q/A datasets want. A reply to an unresolvable post still counts,
since the missing parent was somebody. The predicate is public as
`ariadne.conversation_has_response(conversation, store, subject=...)` if you
would rather filter the conversations yourself.

### Errors

All named API failures derive from `AriadneError`:

| Exception | Meaning |
| --- | --- |
| `ConfigurationError` | Invalid/conflicting options or a source missing required configuration |
| `NoTargetsError` | No posts matched and `allow_empty` is false |
| `ReconstructionError` | Strict reconstruction could not resolve a required post |
| `SourceError` | A network/provider source failed |

For compatibility, configuration errors remain both `ValueError` and
`RuntimeError`, while the other concrete errors retain their historical
`RuntimeError` behavior. Provider-specific `XApiError` derives from
`SourceError`.

Imported dumps are used automatically when present. A dump-backed user or
author timeline with more than 10,000 posts requires either a narrower
`since` value or an explicit `dump_limit`; explicit limits keep the newest
posts.

## Persistent dump library

The same normalized archive library exposed by `ariadne dumps` is available
programmatically:

```python
from ariadne import LocalDumpsClient, import_dump, list_dumps, remove_dump

info = import_dump("~/DATA/tpot_dump", name="tpot")
print(info.tweets, info.first_tweet, info.last_tweet)

with LocalDumpsClient(names="tpot") as client:  # omit names to query all imports
    hits = client.search("labyrinth", username="alice", limit=20)
    timeline = client.get_user_posts("alice", since="2024-01-01", limit=50)

for info in list_dumps():
    print(info.name, info.tweets)

remove_dump("tpot")  # removes only the normalized DB, never its source
```

`LocalDumpsClient` closes its SQLite connections when the context exits. The
main build pipeline also closes clients it creates, including on empty results
and exceptions. Parquet imports require the optional `ariadne-x[parquet]`
distribution extra. Imports are stored under `~/.ariadne/dumps`, or
`$ARIADNE_HOME/dumps` when that environment variable is set.

## `BuildResult`

```python
result.conversations      # list[Conversation]
result.store              # TweetStore behind them
result.tweets             # list[Tweet]
result.warnings           # list[str], source quality and failure notes
result.target_ids         # the tweet IDs used as branch starts

len(result)               # number of conversations
bool(result)              # False when nothing was reconstructed
for conversation in result: ...
```

Rendering is available as text *and* as data, so nothing has to round-trip
through a file:

```python
result.render("raft")     # str, one JSON object per line
result.raft_documents()   # list[dict], the same rows already parsed

result.render("messages") # str
result.messages()         # list[dict]
result.messages(strict_openai=True)   # role/name/content only

result.render("json")     # str
result.json_payload()     # dict

result.render("markdown") # str
```

Writing out:

```python
result.save("out.jsonl", "raft")   # returns the Path, makes parent dirs
result.save_cache()                # only writes if this run fetched anything
```

## Retrying a cache's missing posts

A build's cache records the tweet IDs it could not resolve. `retry_cache()`
re-attempts them from the cache file alone, so it can run in a later session,
long after the build, against sources you did not have at the time:

```python
import ariadne

outcome = ariadne.retry_cache(".ariadne-cache.json")

print(len(outcome.wanted), "attempted")
print(len(outcome.recovered), "recovered")
print(len(outcome.still_missing), "still missing")

if outcome:                    # truthy when anything was recovered
    ariadne.build(archive="archive.zip", for_user="alice")   # now sees them
```

It tries local dumps and free oEmbed by default. Enable more sources per call:

```python
ariadne.retry_cache(
    ".ariadne-cache.json",
    limit=200,                 # bound the run; see ordering note below
    community_archive=True,
    twitterapi_key="...",
    fetch=True, bearer_token="...",   # X API, potentially billable
)
```

`CacheRetryResult` carries `path`, `wanted`, `recovered`, `still_missing`, and
`warnings`, and is falsy when nothing was recovered. The cache is rewritten in
place with whatever was found, and the ids that are still missing stay
recorded — including any a `limit` never attempted.

`limit` takes from the front of the retry set, which is ordered by kind rather
than by date: ids the cache explicitly recorded as missing come first (sorted
as strings, which for snowflake ids is not chronological), then reply and quote
references that were never fetched, then textless stubs. Treat a limited run as
"some of them", not "the oldest ones".

The cache write is last-writer-wins: retry after a build using the same cache
has finished, not concurrently with it.

## `BuildOptions`

`build(**kwargs)` is the concise form of `build(BuildOptions(...))`. Use the
object directly when you want to reuse or modify a configuration:

```python
from dataclasses import replace
from ariadne import BuildOptions, build, build_conversations

cheap = BuildOptions(target_user="alice", allow_empty=True)
first = build(cheap)

# Second pass, reusing the tweets already in memory
paid = replace(cheap, fetch=True, bearer_token="...", allow_empty=False)
second = build_conversations(paid, base_store=first.store)
```

`build_conversations()` is the advanced continuation entry point. A supplied
`base_store` is reused and mutated in place; pass `no_dumps=True` when a build
must be isolated from the user's persistent local dump library.

`BuildOptions.coerce()` accepts an `argparse.Namespace`, a dict, or any object
carrying the option names as attributes, which is how the CLI hands its parsed
arguments to the pipeline.

The API is synchronous. In an async application, run `build` in a worker
thread (for example with `asyncio.to_thread`) rather than on the event loop.

## Bluesky

`build_bluesky()` returns the same `BuildResult`, so all rendering and save
methods are shared:

```python
result = ariadne.build_bluesky("alice.bsky.social", limit=60)
documents = result.raft_documents()
```

## Lower-level pieces

```python
from ariadne import (
    load_archive, load_tweets_file,          # sources
    CommunityArchiveClient, TwitterApiIoClient,   # network providers
    TweetStore, Tweet, TweetRef,             # models
    Conversation, QuoteContext,
    render, raft_documents,                  # rendering
    message_conversations, json_payload,
    load_cache, save_cache,                  # cache
    retry_cache, CacheRetryResult,
    conversation_has_response,               # the responses_only predicate
    ariadne_home,                            # settings directory in use
)
```

These are the same building blocks `build()` composes, if you want to drive
the reconstruction yourself. `render()`, `message_conversations()`, and
`raft_documents()` take an optional `subject=` username; pass it to assign
`assistant` by authorship when rendering conversations you assembled by hand.
(`json_payload()` has no roles to assign, so it takes no subject.)
