# Output Schemas

Ariadne emits five formats. All five describe the same reconstruction — a
root-to-target branch, its quote context, and the warnings collected on the way
— so the choice is about shape, not content.

| Format | Encoding | Use it for |
| --- | --- | --- |
| `messages` | JSON | chat-like conversations with tweet metadata; the CLI default |
| `openai` | JSON | the same envelope, messages reduced to `role`/`name`/`content` |
| `json` | JSON | the normalized graph: branches, quote paths, and every tweet record |
| `markdown` | text | reading and review |
| `raft` | JSONL | retrieval, chunking, embedding |

## Two rules that apply to every format

### Roles follow authorship

The subject you collected — the user named by `--for-user` / `--target-user`,
or `for_user` / `target_user` in the API — speaks as `assistant` wherever they
appear in a branch. Everybody else is `user` in `messages` and `openai`, and
`participant` in `raft`.

This is authorship, not position: the subject is the assistant even when they
opened the thread, and a post in the middle of a branch is a `user` turn if
somebody else wrote it. When no subject was named, the author of each branch's
target tweet plays that role instead. A target tweet whose author cannot be
determined at all still speaks as `assistant`, since it is the post you asked
for.

### Unresolvable posts render as `[deleted]`

A post that no source could resolve — deleted, suspended, protected, or simply
absent from every archive you gave it — keeps its place in the branch and
renders as `[deleted]` in both the author field and the text. A post that was
resolved but whose author is unknown is attributed to `[deleted]` too, while
keeping its own text.

Nothing is silently dropped: the branch also carries a warning naming the
tweet ID, and the ID stays in `tweet_ids` / `all_tweet_ids` so you can count
the holes or go fetch them later with `ariadne cache retry`. Pass `--strict`
to fail the build instead of rendering a partial branch.

## `json`

The normalized graph, for analysis, provenance, and custom rendering.

- `format`: `ariadne.json.v1`
- `conversations`: branch paths, quote paths, warnings, and IDs
- `tweets`: normalized tweet records keyed by ID

Tweet records carry `available: false` when they are placeholders, which is the
machine-readable form of the `[deleted]` rendering above.

## `messages`

Conversations of enriched messages. Each message carries `role`, `name`, and
`content` plus `tweet_id`, `created_at`, `url`, and `author`.

## `openai`

The same conversation envelope, with each message reduced to `role`, `name`,
and `content`. Consumers extract `conversations[i].messages`.

## `markdown`

A human-readable view: one section per branch, headed by its target ID, in
root-to-target order with attribution, quote context, and warnings.

Consecutive posts by the same author merge into one message — a single role
header, then the timestamp and URL of every post in the run, then the texts
separated by `---` rules. A self-thread therefore reads as one turn rather than
five. Consecutive `[deleted]` posts never merge, since nothing proves they
share an author.

```markdown
### user: @carol
`2024-01-01T10:00:00Z`
https://x.com/carol/status/1001

the original claim

### assistant: @alice
`2024-01-02T10:00:00Z`
https://x.com/alice/status/1002
`2024-01-02T10:01:00Z`
https://x.com/alice/status/1003

replying to carol

---

and a follow-up in the same breath
```

## `raft`

JSONL documents for retrieval, chunking, and RAFT ingestion. Each line is one
reconstructed conversation:

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

`text` is optimized for retrieval and embedding. `messages` keeps per-post
attribution and timestamps, `quotes` preserves attached quote paths, and the
metadata fields keep selection, dedupe, and warning provenance.

## Which branches are emitted

Every format renders the same set of branches, chosen before rendering:

- A branch is one target's ancestor path back to its root. Sibling replies are
  not discovered or appended.
- A branch fully contained inside another is pruned, so overlapping targets in
  one thread do not produce duplicate documents.
- With `--responses-only` (API: `responses_only=True`), only branches in which
  the subject actually responds to somebody else survive — replies and quote
  tweets. Standalone posts and pure self-threads are dropped. A reply to a
  `[deleted]` post still counts, because the missing parent was somebody.
