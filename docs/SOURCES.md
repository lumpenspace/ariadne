# Sources

ariadne merges tweet-like records from several sources before reconstructing reply branches.

## Preferred Sources

1. Local X/Twitter archive: best for a user's own tweets and reply metadata.
2. Generic CSV/JSON/JSONL dumps: useful for scraper exports and future Raft pipelines.
3. Local cache: prevents repeating prior oEmbed, RSS, or X API lookups.
4. Cheap public sources: oEmbed and unofficial RSS where available.
5. X API v2: structured and reliable, but opt-in because it can cost money.

## Optional Network Sources

- oEmbed: public endpoint that can hydrate content when a canonical tweet URL is known. It does not provide reply-parent metadata.
- Unofficial RSS: Nitter/XCancel-style feeds. This is unsupported, often recent-only, and may omit reply metadata.
- X API v2: structured and reliable, but opt-in because it can cost money.

## Cheap-First Target Mode

`--target-user USER` is the convenience mode for arbitrary public accounts. It
tries cache/archive/dump data, then unofficial RSS, then oEmbed hydration before
any X API request. If `--fetch` or `--fetch-user-timeline` is enabled, X API runs
after those cheap sources and is used to backfill timeline pages, reply parents,
quotes, and structural metadata that oEmbed/RSS cannot expose.

Interactive mode follows the same policy: it asks for a username and/or archive
plus a date, runs the cheap-source pass, prints a summary of tweets,
conversations, unavailable output tweets, warnings, and sources, then asks
whether to continue with X API.

## Reconstruction Rule

The branch builder can only complete a conversation when it knows the parent tweet ID for each reply. Text-only sources can improve content quality, but they cannot infer missing edges.
