# Sources

tweet-threader merges tweet-like records from several sources before reconstructing reply branches.

## Preferred Sources

1. Local X/Twitter archive: best for a user's own tweets and reply metadata.
2. Generic CSV/JSON/JSONL dumps: useful for scraper exports and future Raft pipelines.
3. Local cache: prevents repeating prior oEmbed, RSS, or X API lookups.

## Optional Network Sources

- oEmbed: public endpoint that can hydrate content when a canonical tweet URL is known. It does not provide reply-parent metadata.
- X API v2: structured and reliable, but opt-in because it can cost money.
- Unofficial RSS: Nitter/XCancel-style feeds. This is unsupported, often recent-only, and may omit reply metadata.

## Reconstruction Rule

The branch builder can only complete a conversation when it knows the parent tweet ID for each reply. Text-only sources can improve content quality, but they cannot infer missing edges.

