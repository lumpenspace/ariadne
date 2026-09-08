# Raft Handoff

[raft](https://github.com/lumpenspace/raft) builds persona datasets — a grounding
corpus plus question/answer transcripts — and finetunes models on them. ariadne
prepares tweet conversations as raft's tweet-shaped input.

Since raft 2.0 the handoff is direct: `raft tweets` calls ariadne's Python API
(`ariadne.build(...)` → `result.raft_documents()`), so no intermediate file is
needed. The handoff is exclusive: `metadata.dataset_role: corpus` becomes
grounding material, while `metadata.dataset_role: conversation` becomes a Q/A
transcript. A branch is never duplicated into both destinations.

## Why Tweets Need Preprocessing

Tweets are rarely useful as isolated rows. Replies depend on their ancestors, quote
tweets depend on quoted context, and overlapping reply branches can create duplicate
training memories. ariadne handles those concerns before raft sees the data.

## What makes a good row

Two Ariadne behaviours matter for that:

- **Roles follow authorship.** The user you collected is `assistant` wherever
  they appear in a branch, and everyone else is `participant`. The answers are
  therefore the subject's turns, not whichever turn happened to come last.
- **Dataset roles are evidence-based.** Only a substantive subject reply to a
  substantive post by a known different author becomes a conversation.
  Standalone posts and self-threads remain useful corpus material; incomplete
  replies and quote commentary also stay out of Q/A until their prompt is
  genuinely available.

Posts that no source could resolve stay visible in the branch as `[deleted]`,
but they are never used as synthetic questions. Run `ariadne cache retry` and
rebuild to promote an incomplete reply into a conversation once its parent is
recovered.

## Manual flow

The file-based handoff still works for driving the steps separately:

```bash
ariadne build \
  --archive ~/Downloads/twitter-archive.zip \
  --for-user alice \
  --since 2020-01-01 \
  --oembed \
  --format raft \
  --output data/alice-tweet-conversations.jsonl
```

Each JSONL row declares its destination in `metadata.dataset_role`. Use
`metadata.tweet_ids` and `metadata.target_id` for provenance, dedupe, and
evaluation reporting. Add `--responses-only` only if you intentionally do not
want authored threads in the corpus.
