# Output Schemas

tweet-threader currently emits four families of output.

## `json`

Machine-readable graph output:

- `format`: `tweet-threader.json.v1`
- `conversations`: branch paths, quote paths, warnings, and IDs
- `tweets`: normalized tweet records keyed by ID

## `messages`

Conversation output with OpenAI-like roles plus tweet metadata. The first branch tweet is `assistant`; later branch tweets are `user`.

## `openai`

Strict message objects with `role`, `name`, and `content` only.

## `raft`

JSONL documents intended for retrieval, chunking, and future RAFT ingestion. Each line is one reconstructed conversation:

```json
{
  "format": "raft.documents.v1",
  "id": "tweet-thread:1002",
  "source": "tweet-threader",
  "kind": "tweet_conversation",
  "text": "@alice: root tweet\n\n@bob: reply tweet",
  "metadata": {
    "target_id": "1002",
    "tweet_ids": ["1001", "1002"],
    "participants": ["@alice", "@bob"]
  },
  "messages": []
}
```

The `text` field is optimized for retrieval and embedding. The structured fields preserve provenance.

