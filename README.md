# ariadne

Experimental CLI utility for turning reply tweets into complete branch context, LLM-style chat input, and Raft-ready retrieval documents.

It can read an X/Twitter archive export (`data/tweets*.js` / `data/account.js` inside a folder or zip), generic CSV/JSON/JSONL tweet dumps, or explicit tweet IDs/URLs. It can optionally hydrate public tweet text through oEmbed, fetch missing structured metadata through X API v2, prune branches that are subsets of longer branches, and render the result as JSON, Markdown, or chat messages.

## Install

ariadne is not on PyPI — the name belongs to the GraphQL library — so install from git:

```bash
pip install git+https://github.com/lumpenspace/ariadne
```

or, from a checkout:

```bash
python3 -m pip install -e .
```

Without installing, run from the repo with:

```bash
PYTHONPATH=src python3 -m ariadne --help
```

## Examples

Interactive mode asks for a username and/or archive, a date, then runs a
cheap-source pass first. It prints a summary and only then asks whether to
continue with X API:

```bash
ariadne interactive
```

From an archive only:

```bash
ariadne --archive ~/Downloads/twitter-archive.zip --format markdown 1234567890123456789
```

Try the included fixture:

```bash
ariadne \
  --archive examples/fixture_archive \
  --format markdown \
  1002
```

All loaded tweets by a user since a date, including the reply branch around each selected tweet:

```bash
ariadne \
  --archive ~/Downloads/twitter-archive.zip \
  --for-user alice \
  --since 2024-01-01 \
  --oembed \
  --format openai
```

From a generic dump:

```bash
ariadne \
  --tweets-file ./tweets.jsonl \
  --for-user alice \
  --since 2024-01-01 \
  --oembed \
  --format messages
```

From IDs/URLs with live fetching:

```bash
export X_BEARER_TOKEN="..."
ariadne --fetch --format messages \
  https://x.com/someone/status/1234567890123456789 \
  1234567890123456790
```

Strict OpenAI-style messages:

```bash
ariadne --archive archive.zip --fetch --format openai 1234567890123456789
```

Fetch the user's own timeline through X API before building:

```bash
export X_BEARER_TOKEN="..."
ariadne \
  --for-user alice \
  --since 2024-01-01 \
  --fetch-user-timeline \
  --fetch \
  --oembed \
  --format openai
```

No dump and no X API token, trying the unofficial free RSS fallback:

```bash
ariadne \
  --for-user alice \
  --since 2024-01-01 \
  --unofficial-rss \
  --rss-base https://nitter.net \
  --format json
```

Arbitrary public target, cheap-first by default:

```bash
ariadne \
  --target-user alice \
  --since 2024-01-01 \
  --format raft \
  --output data/alice-cheap-pass.jsonl
```

Only start from replies:

```bash
ariadne \
  --target-user alice \
  --replies-only \
  --since 2024-01-01 \
  --format raft
```

Arbitrary public target, but allow paid X API only after RSS/oEmbed/cache have
been tried:

```bash
export X_BEARER_TOKEN="..."
ariadne \
  --target-user alice \
  --since 2024-01-01 \
  --fetch \
  --fetch-user-timeline \
  --max-user-pages 2 \
  --format raft
```

Archive-only interactive runs can leave the username blank; ariadne will
use the archive account when it can infer one, otherwise it selects all loaded
tweets on or after `--since`.

Raft-ready JSONL:

```bash
ariadne \
  --archive ~/Downloads/twitter-archive.zip \
  --for-user alice \
  --since 2020-01-01 \
  --oembed \
  --format raft \
  --output data/alice-tweet-conversations.jsonl
```

Inspect what an archive contributes:

```bash
ariadne inspect-archive ~/Downloads/twitter-archive.zip
```

## Notes

- This is alpha software. It is designed to be clear about source quality and failure modes, not to promise complete reconstruction from incomplete public data.
- `--target-user` is the convenience mode for arbitrary public accounts. It uses local cache/archive data, unofficial RSS, and oEmbed before any X API reads. Add `--fetch --fetch-user-timeline` only when you are ready to spend official API reads.
- `--replies-only` only uses tweets with actual reply-parent metadata as starting targets. Archive files, generic dumps, and X API can provide that; cheap RSS/oEmbed usually cannot, so strict reply-only runs may find no starts until X API is allowed.
- Live X API fetching is opt-in via `--fetch` and `--fetch-user-timeline`; without it, missing posts are represented as unavailable placeholders.
- `--oembed` uses the public X oEmbed endpoint to hydrate tweet text/author/date when a canonical tweet URL is known. oEmbed does not expose reply-parent metadata, so it cannot complete a branch by itself.
- `--unofficial-rss` tries a Nitter/XCancel-style RSS endpoint such as `https://nitter.net/{username}/rss`. `--target-user` tries both `https://nitter.net` and `https://xcancel.com` unless disabled. This is unsupported, fragile, usually limited to recent feed items, and usually omits reply-parent metadata.
- `--rss-url-template` can add other cheap feed services, for example a hosted feed URL containing `{username}`.
- X API fetching uses v2 Post lookup (`GET /2/tweets`) and user timeline lookup (`GET /2/users/{id}/tweets`) with `referenced_tweets.id` and author expansions, then caches responses in `.ariadne-cache.json` by default.
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

## Python API

Everything the `build` command does is callable directly, and rendering is
available as parsed data rather than only as text:

```python
import ariadne

result = ariadne.build(archive="~/twitter-archive.zip", for_user="alice")

for document in result.raft_documents():   # list[dict], no file round-trip
    print(document["metadata"]["target_id"])

result.save("branches.jsonl", "raft")
```

Keyword names are the CLI flags with dashes as underscores. Full reference in
[docs/API.md](docs/API.md).

See also:

- [Python API](docs/API.md)
- [Source behavior](docs/SOURCES.md)
- [Output schemas](docs/SCHEMA.md)
- [Raft handoff](docs/RAFT.md)

## Development

[uv](https://docs.astral.sh/uv/) manages the environment:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```
