# Sources

ariadne merges tweet-like records from several sources before reconstructing reply branches.

## Preferred Sources

1. **Imported local dumps**: persistent normalized databases built from personal
   archives, Community CSV/ZIP exports, Parquet collections, or generic tweet
   files. All imports are queried together by default, so a target and its
   reply/quote ancestors may come from different archives.
2. **One-shot local sources**: `--archive` and `--tweets-file` load a source for
   one build without importing it into the persistent library.
3. **Local cache**: prevents repeating prior oEmbed, RSS, or API lookups.
4. **Structured community or gateway sources**: Community Archive and
   twitterapi.io can provide reply and quote references beyond a personal
   export when explicitly enabled.
5. **Cheap public sources**: oEmbed and unofficial RSS can often hydrate text,
   but usually do not expose reply-parent structure.
6. **X API v2**: structured and reliable, but opt-in because it may consume
   paid reads.

See [Persistent Archive Library](DUMPS.md) for import, exploration, and
cross-archive build behavior.

## Optional Network Sources

- oEmbed: public endpoint that can hydrate content when a canonical tweet URL is known. It does not provide reply-parent metadata.
- Unofficial RSS: Nitter/XCancel-style feeds. This is unsupported, often recent-only, and may omit reply metadata.
- Community Archive: public, donor-scoped structured data. It can enumerate a
  target and resolve parents or quotes by other donor accounts.
- twitterapi.io: paid pay-as-you-go structured gateway, enabled with
  `--twitterapi-key` or `TWITTERAPI_IO_KEY`.
- X API v2: structured and reliable, but opt-in because it can cost money.

## Cheap-First Target Mode

`--target-user USER` is the convenience mode for arbitrary public accounts. It
tries cache/archive/dump data, then unofficial RSS, then oEmbed hydration before
any X API request. If `--fetch` or `--fetch-user-timeline` is enabled, X API runs
after those cheap sources and is used to backfill timeline pages, reply parents,
quotes, and structural metadata that oEmbed/RSS cannot expose.

`--replies-only` is strict: a tweet only becomes a starting target when Ariadne
has reply-parent metadata for it. This prevents mixed RSS feeds from silently
seeding top-level posts. When cheap sources return only text and IDs, Ariadne can
use those IDs as candidates, but X API or a richer dump is needed to prove which
ones are replies.

Interactive mode follows the same policy: it asks for a username and/or archive
plus a date, runs the cheap-source pass, prints a summary of tweets,
conversations, unavailable output tweets, warnings, and sources, then asks
whether to continue with X API.

## Reconstruction Rule

The branch builder can only complete a conversation when it knows the parent tweet ID for each reply. Text-only sources can improve content quality, but they cannot infer missing edges.

`--since` limits which user-authored posts become branch targets. It does not
discard older parents needed to complete those selected branches. Repeat
`--dump NAME` to restrict imported-dump scope, or use `--no-dumps` to disable
the persistent library for a build.

## Bluesky

Bluesky uses a separate command because its public API returns whole reply
threads as nested data and does not require authentication:

```bash
ariadne bluesky alice.bsky.social --since 2024-01-01 --format raft
```

The result uses the same `BuildResult` renderers and output schemas as X/Twitter
reconstruction.
