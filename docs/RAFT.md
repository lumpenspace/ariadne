# Raft Handoff

The old RAFT repository describes a pipeline that uses a target person's past written output as memories for retrieval-augmented fine-tuning. ariadne prepares tweet conversations as that written-output layer.

## Why Tweets Need Preprocessing

Tweets are rarely useful as isolated rows. Replies depend on their ancestors, quote tweets depend on quoted context, and overlapping reply branches can create duplicate training memories. ariadne handles those concerns before Raft sees the data.

## Recommended Flow

```bash
ariadne \
  --archive ~/Downloads/twitter-archive.zip \
  --for-user alice \
  --since 2020-01-01 \
  --oembed \
  --format raft \
  --output data/alice-tweet-conversations.jsonl
```

Then a Raft importer can treat each JSONL row as one candidate memory document. Use `metadata.tweet_ids` and `metadata.target_id` for provenance, dedupe, and evaluation reporting.

