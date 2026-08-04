# tweet-threader

Experimental CLI utility for turning reply tweets into complete branch context, LLM-style chat input, and Raft-ready retrieval documents.

It can read an X/Twitter archive export (`data/tweets*.js` / `data/account.js` inside a folder or zip), generic CSV/JSON/JSONL tweet dumps, or explicit tweet IDs/URLs. It can optionally hydrate public tweet text through oEmbed, fetch missing structured metadata through X API v2, prune branches that are subsets of longer branches, and render the result as JSON, Markdown, or chat messages.

## Install

```bash
python3 -m pip install -e .
```

Without installing, run from the repo with:

```bash
PYTHONPATH=src python3 -m tweet_threader --help
```

## Examples

Interactive mode:

```bash
tweet-threader interactive
```

From an archive only:

```bash
tweet-threader --archive ~/Downloads/twitter-archive.zip --format markdown 1234567890123456789
```

Try the included fixture:

```bash
tweet-threader \
  --archive examples/fixture_archive \
  --format markdown \
  1002
```

All loaded tweets by a user since a date, including the reply branch around each selected tweet:

```bash
tweet-threader \
  --archive ~/Downloads/twitter-archive.zip \
  --for-user alice \
  --since 2024-01-01 \
  --oembed \
  --format openai
```

From a generic dump:

```bash
tweet-threader \
  --tweets-file ./tweets.jsonl \
  --for-user alice \
  --since 2024-01-01 \
  --oembed \
  --format messages
```

From IDs/URLs with live fetching:

```bash
export X_BEARER_TOKEN="..."
tweet-threader --fetch --format messages \
  https://x.com/someone/status/1234567890123456789 \
  1234567890123456790
```

Strict OpenAI-style messages:

```bash
tweet-threader --archive archive.zip --fetch --format openai 1234567890123456789
```

Fetch the user's own timeline through X API before building:

```bash
export X_BEARER_TOKEN="..."
tweet-threader \
  --for-user alice \
  --since 2024-01-01 \
  --fetch-user-timeline \
  --fetch \
  --oembed \
  --format openai
```

No dump and no X API token, trying the unofficial free RSS fallback:

```bash
tweet-threader \
  --for-user alice \
  --since 2024-01-01 \
  --unofficial-rss \
  --rss-base https://nitter.net \
  --format json
```

Raft-ready JSONL:

```bash
tweet-threader \
  --archive ~/Downloads/twitter-archive.zip \
  --for-user alice \
  --since 2020-01-01 \
  --oembed \
  --format raft \
  --output data/alice-tweet-conversations.jsonl
```

Inspect what an archive contributes:

```bash
tweet-threader inspect-archive ~/Downloads/twitter-archive.zip
```

## Notes

- This is alpha software. It is designed to be clear about source quality and failure modes, not to promise complete reconstruction from incomplete public data.
- Live X API fetching is opt-in via `--fetch` and `--fetch-user-timeline`; without it, missing posts are represented as unavailable placeholders.
- `--oembed` uses the public X oEmbed endpoint to hydrate tweet text/author/date when a canonical tweet URL is known. oEmbed does not expose reply-parent metadata, so it cannot complete a branch by itself.
- `--unofficial-rss` tries a Nitter/XCancel-style RSS endpoint such as `https://nitter.net/{username}/rss`. This is unsupported, fragile, usually limited to recent feed items, and usually omits reply-parent metadata.
- X API fetching uses v2 Post lookup (`GET /2/tweets`) and user timeline lookup (`GET /2/users/{id}/tweets`) with `referenced_tweets.id` and author expansions, then caches responses in `.tweet-threader-cache.json` by default.
- The cache avoids repeat lookups locally. X API reads may still count toward your account usage according to the current X API plan.
- `--since` filters which user-authored tweets become branch targets. Older parent tweets are still included when needed to complete a selected branch.
- Reply reconstruction follows only the branch from the target reply back to its root. Sibling replies are intentionally not pulled in.
- Quote references are attached as quote contexts. If a quoted post is itself a reply, its branch is reconstructed too.

## Output Shape

`--format messages` returns:

```json
{
  "format": "messages",
  "conversations": [
    {
      "target_id": "123",
      "messages": [
        {
          "role": "assistant",
          "name": "original_author",
          "content": "Root tweet text",
          "tweet_id": "1"
        },
        {
          "role": "user",
          "name": "participant",
          "content": "Reply text",
          "tweet_id": "123"
        }
      ]
    }
  ]
}
```

The first tweet in the reconstructed branch is role `assistant`; later branch posts are role `user` with participant names when available.

See also:

- [Source behavior](docs/SOURCES.md)
- [Output schemas](docs/SCHEMA.md)
- [Raft handoff](docs/RAFT.md)

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```
