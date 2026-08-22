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

## What makes a good row

raft turns each row's `messages` into question/answer exchanges, with the
subject's own posts as the answers. Two Ariadne behaviours matter for that:

- **Roles follow authorship.** The user you collected is `assistant` wherever
  they appear in a branch, and everyone else is `participant`. The answers are
  therefore the subject's turns, not whichever turn happened to come last.
- **`--responses-only` keeps only the exchanges.** A standalone post has no
  question to answer, and a pure self-thread only answers itself. Filtering
  them out before finetuning usually improves the dataset more than adding
  volume does.

Posts that no source could resolve stay in the branch as `[deleted]`, so a
reply keeps the shape of the exchange it belonged to even when its parent is
gone. If you would rather recover those parents than train on the gaps, run
`ariadne cache retry` and rebuild — or drop the partial branches with
`--strict`.

## Manual flow

The file-based handoff still works for driving the steps separately:

```bash
ariadne build \
  --archive ~/Downloads/twitter-archive.zip \
  --for-user alice \
  --since 2020-01-01 \
  --responses-only \
  --oembed \
  --format raft \
  --output data/alice-tweet-conversations.jsonl
```

Each JSONL row is one candidate memory document. Use `metadata.tweet_ids` and
`metadata.target_id` for provenance, dedupe, and evaluation reporting.
