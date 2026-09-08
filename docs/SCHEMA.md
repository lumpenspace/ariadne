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
somebody else wrote it. When a subject *is* named, a branch's own target tweet
falls back to `assistant` only if its author cannot be determined at all.

When no subject was named — a build driven by bare tweet IDs, for instance —
the author of each branch's target tweet becomes the subject for that branch,
and the target tweet itself is always `assistant`.

### A post Ariadne could not get keeps its place

Missing posts are never dropped from a branch — removing them would silently
change the shape of the conversation. They are rendered as placeholders
instead, and there are two, because "missing" has two flavours:

| Rendered as | When | Author shown as | Warning? |
| --- | --- | --- | --- |
| `[deleted]` | nothing at all is known about the post — deleted, suspended, protected, or absent from every source you gave it | `[deleted]` | yes, naming the id |
| `[tweet <id> has no text]` | the post is known *about* but its text was never retrieved | the real `@handle` | no |

The second case is easy to hit and worth understanding. A personal X archive
records the handle you replied to (`in_reply_to_screen_name`), so Ariadne can
place that parent in the branch, attribute it, and link it — while having
nothing of what it said. Community dumps, twitterapi.io, and generic tweet
files can all produce the same partial knowledge. Because the post is *known*,
this case is not a warning; it will not appear in your warning count, and only
the placeholder text distinguishes it from a post that was fetched in full.

Separately, a post whose text was retrieved but whose author is unknown is
attributed to `[deleted]` while keeping its own text.

In every case the id stays in `tweet_ids` / `all_tweet_ids`, and in the `json`
format the record carries `available: false` for the first case. Both kinds
can be retried later with `ariadne cache retry`. Pass `--strict` to fail the
build rather than render a partial branch.

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

A human-readable view: each branch in root-to-target order with attribution,
quote context, and warnings. When a render contains more than one branch, each
is headed by `## Conversation N: <target id>`; a single-branch render has no
such heading.

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
classified reconstructed branch:

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
    "subject": "bob",
    "dataset_role": "conversation",
    "classification": "reply",
    "has_subject_response": true,
    "response_tweet_ids": ["1002"],
    "subject_tweet_ids": ["1002"],
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

`dataset_role` is the routing contract. `conversation` rows are proven replies
to another author and use `kind: tweet_conversation`; `corpus` rows use
`kind: tweet_thread` and contain the subject's standalone post, self-thread,
quote commentary, or incomplete reply. For corpus rows, `text` contains only
the subject-authored material. `messages` keeps per-post attribution and
timestamps, `quotes` preserves attached quote paths, and the remaining
metadata keeps selection, dedupe, and warning provenance.

## Which branches are emitted

Every format renders the same set of branches, chosen before rendering:

- A branch is one target's ancestor path back to its root. Sibling replies are
  not discovered or appended.
- A branch fully contained inside another is pruned, so overlapping targets in
  one thread do not produce duplicate documents.
- With `--responses-only` (API: `responses_only=True`), only proven replies to
  known other authors survive. Standalone posts, self-threads, quote
  commentary, and replies with unresolved prompts are dropped.
