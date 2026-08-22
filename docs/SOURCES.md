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

Interactive mode follows the same policy. It asks for an archive or dump path,
a username, a date, an output format and file, a maximum reply depth, and then
which sources to allow: oEmbed hydration, unofficial RSS (with bases and
templates), whether to start only from replies, and whether to keep only
conversations in which the user responds to somebody else — the interactive
form of `--responses-only`, which defaults to yes here.

If the cache from an earlier session holds tweets, it reports how many are
still missing and offers to retry them before building, so anything recovered
flows straight into this run.

It then runs the cheap-source pass and prints a summary — tweets in store,
selected targets, reconstructed conversations, unavailable output tweets,
warnings, and a count per source. Before offering the X API it states the size
of the gap: how many tweets are missing, and how many conversations they would
complete.

```text
◆ Cheap-source pass
  tweets in store: 412
  selected target tweets: 38
  reconstructed conversations: 31
  unavailable tweets in reconstructed output: 7
  warnings: 4
  sources:
    dump:personal: 380
    oembed: 25
    unofficial-rss:nitter.net: 7
  7 tweet(s) are missing to complete 6 of 31 conversation(s)
» Continue with X API now? This may cost API reads [Y/n]
```

The count is deliberately shown before the prompt, not after it: X API reads
may be billable, so the decision to spend them should be made against a number
rather than a guess. When the cheap pass left nothing missing, it says so
instead — and the default answer flips to no, since there is nothing to buy.

## Recovering What Was Missing

A build's cache records the tweet IDs it could not resolve, so a gap is not
lost when the session ends. A later run can attempt them against whatever
sources are available then — including ones you did not have or did not want
to pay for at the time:

```bash
ariadne cache list      # what each cache holds, and what is still missing
ariadne cache missing   # the missing ids, one per line
ariadne cache retry     # fetch them now, updating the cache in place
```

`ariadne cache retry` uses local dumps and free oEmbed by default. Add
`--community-archive`, `--twitterapi-key`, or `--fetch` with an X bearer token
for the stubborn ones, and `--limit` to bound a run. Re-running the original
build afterwards picks the recovered tweets up from the cache.

The cache write is last-writer-wins, so run a retry after a build using the
same cache has finished, not alongside it.

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
reconstruction, and accepts `--responses-only` as well. Use `--no-replies` to
walk only the actor's own posts, and `--limit` to bound how many recent posts
are read.

Every command's flags are listed by `ariadne <command> --help`, which is the
authoritative reference; this page describes policy rather than repeating it.
