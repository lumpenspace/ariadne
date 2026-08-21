# Output Schemas

ariadne currently emits five families of output.

## `json`

Machine-readable graph output:

- `format`: `ariadne.json.v1`
- `conversations`: branch paths, quote paths, warnings, and IDs
- `tweets`: normalized tweet records keyed by ID

## `messages`

Conversation output with OpenAI-like roles plus tweet metadata. Tweets authored by the target user (the author of the branch's target tweet) are `assistant`; tweets from everyone else are `user`.

## `openai`

Strict message objects with `role`, `name`, and `content` only.

## `markdown`

A human-readable conversation view. Each branch is headed by its target ID and
renders the root-to-target path, author attribution, unavailable placeholders,
quote context, and warnings without exposing the normalized store separately.

## `raft`

JSONL documents intended for retrieval, chunking, and future RAFT ingestion. Each line is one reconstructed conversation:

```json
{
  "format": "raft.documents.v1",
  "id": "ariadne:1002",
  "source": "ariadne",
  "kind": "tweet_conversation",
  "text": "@alice: root tweet\n\n@bob: reply tweet",
  "metadata": {
    "target_id": "1002",
    "target_author": "@bob",
    "target_created_at": "2024-01-02T12:00:00Z",
    "target_url": "https://x.com/bob/status/1002",
    "tweet_ids": ["1001", "1002"],
    "all_tweet_ids": ["1001", "1002"],
    "participants": ["@alice", "@bob"],
    "quote_count": 0,
    "warnings": []
  },
  "messages": [
    {
      "role": "participant",
      "tweet_id": "1001",
      "author": "@alice",
      "username": "alice",
      "created_at": "2024-01-01T12:00:00Z",
      "url": "https://x.com/alice/status/1001",
      "text": "root tweet"
    },
    {
      "role": "assistant",
      "tweet_id": "1002",
      "author": "@bob",
      "username": "bob",
      "created_at": "2024-01-02T12:00:00Z",
      "url": "https://x.com/bob/status/1002",
      "text": "reply tweet"
    }
  ],
  "quotes": []
}
```

The `text` field is optimized for retrieval and embedding. `messages` keeps
per-post attribution and timestamps — roles follow the same target-user rule
as the `messages` format, with `participant` in place of `user` — `quotes`
preserves attached quote paths,
and the metadata fields keep selection, dedupe, and warning provenance.
