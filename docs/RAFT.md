# Raft Handoff

[raft](https://github.com/lumpenspace/raft) builds persona datasets — a grounding
corpus plus question/answer transcripts — and finetunes models on them. ariadne
prepares tweet conversations as raft's tweet-shaped input.

Since raft 2.0 the handoff is direct: `raft tweets` calls ariadne's Python API
(`ariadne.build(...)` → `result.raft_documents()`), so no intermediate file is
needed. Each `raft.documents.v1` row becomes one grounding document (its `text`)
and its `messages` become question/answer exchanges, with the target's own tweets
as the answers.

## Why Tweets Need Preprocessing

Tweets are rarely useful as isolated rows. Replies depend on their ancestors, quote
tweets depend on quoted context, and overlapping reply branches can create duplicate
training memories. ariadne handles those concerns before raft sees the data.

## Manual flow

The file-based handoff still works for driving the steps separately:

```bash
ariadne \
  --archive ~/Downloads/twitter-archive.zip \
  --for-user alice \
  --since 2020-01-01 \
  --oembed \
  --format raft \
  --output data/alice-tweet-conversations.jsonl
```

Each JSONL row is one candidate memory document. Use `metadata.tweet_ids` and
`metadata.target_id` for provenance, dedupe, and evaluation reporting.
