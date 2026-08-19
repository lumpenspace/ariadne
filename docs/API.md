# Python API

Everything the `ariadne build` command does is available as a function call.
The CLI is a thin argparse front-end over this module.

```python
import ariadne

result = ariadne.build(archive="~/twitter-archive.zip", for_user="alice")

for document in result.raft_documents():
    print(document["metadata"]["target_id"], document["text"][:80])
```

## `build(**options)`

Runs the whole pipeline — load sources, select targets, reconstruct reply
branches, hydrate missing text — and returns a `BuildResult`.

Keyword names match the CLI flags with dashes turned into underscores:
`--for-user` is `for_user`, `--no-quotes` is `no_quotes`. List-valued options
accept a bare string, so `archive="a.zip"` and `archive=["a.zip"]` are the same.

```python
# From a local archive
ariadne.build(archive="archive.zip", for_user="alice", since="2024-01-01")

# From a generic dump, replies only
ariadne.build(tweets_file="dump.jsonl", for_user="alice", replies_only=True)

# From explicit tweet IDs or URLs
ariadne.build(items=["https://x.com/alice/status/123", "456"])

# An arbitrary public account, cheap sources before any paid API reads
ariadne.build(target_user="alice")

# Allow the X API, after the cheap pass
ariadne.build(target_user="alice", fetch=True, fetch_user_timeline=True,
              bearer_token="...")
```

Common options, all optional:

| Option | Meaning |
| --- | --- |
| `archive`, `tweets_file`, `items`, `input_file` | Where tweets come from |
| `for_user`, `target_user`, `author_id`, `all_loaded` | Which tweets become branch targets |
| `since` | Only select targets on or after this date |
| `replies_only` | Only start from tweets that are replies |
| `oembed`, `unofficial_rss`, `cheap_first` | Free hydration sources |
| `fetch`, `fetch_user_timeline`, `bearer_token`, `max_user_pages` | X API v2 |
| `cache`, `no_cache` | Tweet cache, default `.ariadne-cache.json` |
| `strict`, `max_depth`, `no_quotes` | Reconstruction behaviour |
| `allow_empty` | Return an empty result instead of raising when nothing matches |

`build()` raises `RuntimeError` when no tweet IDs match and `allow_empty` is
false, and when a source is requested without what it needs (for example
`fetch=True` with no bearer token).

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

## `BuildOptions`

`build(**kwargs)` is sugar for constructing `BuildOptions` and calling
`build_conversations`. Use the object directly when you want to reuse or
modify a configuration:

```python
from dataclasses import replace
from ariadne import BuildOptions, build_conversations

cheap = BuildOptions(target_user="alice", allow_empty=True)
first = build_conversations(cheap)

# Second pass, reusing the tweets already in memory
paid = replace(cheap, fetch=True, bearer_token="...", allow_empty=False)
second = build_conversations(paid, base_store=first.store)
```

`BuildOptions.coerce()` accepts an `argparse.Namespace`, a dict, or any object
carrying the option names as attributes, which is how the CLI hands its parsed
arguments to the pipeline.

## Lower-level pieces

```python
from ariadne import (
    load_archive, load_tweets_file,     # sources
    TweetStore, Tweet, Conversation,    # models
    render, raft_documents,             # rendering
    load_cache, save_cache,             # cache
)
```

These are the same building blocks `build()` composes, if you want to drive
the reconstruction yourself.
