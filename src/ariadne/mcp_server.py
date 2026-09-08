"""MCP server exposing the local ariadne archive library.

Runs over stdio and answers from the imported dumps on this machine: what
archives exist, who is in them, full-text search, a single tweet with its
thread, and full conversation reconstruction.

Deliberately local-only. Ariadne can also reach oEmbed, unofficial RSS, the
Community Archive, twitterapi.io and the X API, and two of those cost money
per read -- so none of them are reachable from here. An agent calling a tool
should never be able to spend the user's API credits or block for minutes on
a few thousand sequential HTTP requests. Use the CLI (``ariadne build
--oembed``, ``ariadne cache retry``) when a run should go to the network.

Start it with ``ariadne mcp`` or ``ariadne-mcp``; install the dependency with
``pip install 'ariadne-x[mcp]'``.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from typing import Any, Literal

from .api import BuildOptions, build_conversations
from .classify import classify_conversation
from .dumps import LocalDumpsClient, list_dumps
from .errors import AriadneError, ConfigurationError
from .ids import tweet_id_from_url
from .models import Tweet
from .store import cache_retry_ids, cache_summary, load_cache, load_missing_ids

SERVER_NAME = "ariadne_mcp"

DEFAULT_LIMIT = 20
MAX_LIMIT = 200
#: Conversations are far larger than tweets, so they default much smaller.
DEFAULT_CONVERSATION_LIMIT = 5
MAX_CONVERSATION_LIMIT = 50
#: Tweet text is truncated in responses so one call cannot flood the context.
DEFAULT_TEXT_CHARS = 600

Format = Literal["markdown", "json"]

INSTRUCTIONS = """Query a local archive of X/Twitter conversations.

Everything is answered from archives already imported on this machine; no
network calls are made and nothing costs money. Start with
`ariadne_list_dumps` to see what is available and `ariadne_list_users` to
find exact handles -- most tools need a handle that exists in the library.

`ariadne_build_conversations` is the substantive one: it walks reply and
quote edges to rebuild whole exchanges, and labels each as a real
conversation or as one-sided corpus material.
"""


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------


class ToolError(Exception):
    """A failure worth explaining to the agent, with a way forward."""

    def __init__(self, problem: str, hint: str = "") -> None:
        super().__init__(problem if not hint else f"{problem} {hint}")
        self.problem = problem
        self.hint = hint


def _clamp(value: int | None, default: int, maximum: int) -> int:
    if value is None:
        return default
    return max(1, min(int(value), maximum))


def _truncate(text: str | None, limit: int) -> str:
    body = " ".join((text or "").split())
    if limit and len(body) > limit:
        return body[: limit - 1] + "…"
    return body


def _page(
    items: list[Any], limit: int, offset: int, *, total_known: bool = True
) -> dict[str, Any]:
    """Slice `items` and describe the slice, so an agent can ask for more.

    `total_known` is False when the caller over-fetched by one instead of
    reading the whole result set -- cheap for a 48,000-post timeline, but it
    means the true total is unknown. Reporting a total we have not counted
    would tell an agent to stop paging early, so it is simply omitted.
    """
    window = items[offset : offset + limit]
    delivered = offset + len(window)
    has_more = delivered < len(items)
    page: dict[str, Any] = {
        "count": len(window),
        "offset": offset,
        "items": window,
        "has_more": has_more,
        "next_offset": delivered if has_more else None,
    }
    if total_known:
        page["total"] = len(items)
    return page


def _tweet_dict(tweet: Tweet, *, text_chars: int) -> dict[str, Any]:
    return {
        "id": tweet.id,
        "author": f"@{tweet.username}" if tweet.username else (tweet.name or "[deleted]"),
        "username": tweet.username,
        "name": tweet.name,
        "created_at": tweet.created_at,
        "text": _truncate(tweet.text, text_chars) if tweet.available else "[deleted]",
        "url": tweet.url,
        "in_reply_to_id": tweet.reply_parent_id(),
        "quoted_ids": tweet.quote_ids(),
        "available": tweet.available,
        "source": tweet.source,
    }


def _tweet_line(row: dict[str, Any]) -> str:
    date = (row.get("created_at") or "")[:10] or "????-??-??"
    return f"- **{row['author']}** `{date}` `{row['id']}`\n  {row['text'] or '[no text]'}"


def _render(payload: dict[str, Any], fmt: Format, markdown: str) -> str:
    """One place where format selection happens, for every tool."""
    if fmt == "json":
        return json.dumps(payload, indent=2, sort_keys=True, default=str)
    footer = ""
    if payload.get("has_more"):
        seen = (
            f"{payload['count']} of {payload['total']}"
            if "total" in payload
            else f"{payload['count']}"
        )
        footer = (
            f"\n\n_{seen} shown. "
            f"Call again with offset={payload['next_offset']} for more._"
        )
    return markdown + footer


def _page_markdown(title: str, page: dict[str, Any], lines: list[str]) -> str:
    if not lines:
        return f"## {title}\n\n_No matches._"
    return f"## {title}\n\n" + "\n".join(lines)


def _dump_names(dump: list[str] | None) -> list[str] | None:
    return [name for name in (dump or []) if name.strip()] or None


@dataclass
class _Library:
    """A LocalDumpsClient plus the check that it has anything in it."""

    names: list[str] | None

    def __enter__(self) -> LocalDumpsClient:
        known = {info.name for info in list_dumps()}
        if not known:
            raise ToolError(
                "No archives have been imported yet.",
                "Run `ariadne dumps import <path>` first; this server only reads "
                "archives already on this machine.",
            )
        missing = [name for name in (self.names or []) if name not in known]
        if missing:
            raise ToolError(
                f"Unknown dump(s): {', '.join(missing)}.",
                f"Imported dumps are: {', '.join(sorted(known))}.",
            )
        self._client = LocalDumpsClient(names=self.names)
        return self._client

    def __exit__(self, *exc: object) -> None:
        self._client.close()


def _resolve_tweet_id(value: str) -> str:
    raw = value.strip()
    if raw.isdigit():
        return raw
    resolved = tweet_id_from_url(raw)
    if not resolved:
        raise ToolError(
            f"Not a tweet id or URL: {value!r}.",
            "Pass a numeric id (e.g. '1234567890123456789') or an x.com/status URL.",
        )
    return resolved


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------


def build_server() -> Any:
    """Construct the MCP server. Imported lazily so `mcp` stays optional."""
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError as McpToolError
        from mcp.types import ToolAnnotations
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise ConfigurationError(
            "The MCP server needs the optional mcp dependency: "
            "pip install 'ariadne-x[mcp]'"
        ) from exc

    server = MCPServer(name=SERVER_NAME, instructions=INSTRUCTIONS)
    read_only = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,  # local archives only; no network
    )

    def tool(name: str, title: str):
        """Register a tool, translating our failures into visible ones.

        The SDK only forwards the message of its own ToolError to the model;
        any other exception is treated as a crash and the model is told just
        "Error executing tool". Our errors are written to be acted on, so
        they have to arrive as that type.
        """

        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any):
                try:
                    return fn(*args, **kwargs)
                except ToolError as exc:
                    raise McpToolError(str(exc)) from exc
                except AriadneError as exc:
                    raise McpToolError(str(exc)) from exc

            return server.tool(name=name, title=title, annotations=read_only)(wrapper)

        return decorator

    @tool("ariadne_list_dumps", "List imported archives")
    def ariadne_list_dumps(response_format: Format = "markdown") -> str:
        """List the tweet archives imported on this machine.

        Start here: every other tool reads from these archives, and their date
        spans tell you which questions the library can actually answer.

        Args:
            response_format: 'markdown' (default) or 'json'.

        Returns:
            For each dump: name, kind, tweet and account counts, date span,
            whether it has a full-text index, and its original source path.
        """
        infos = list_dumps()
        items = [
            {
                "name": info.name,
                "kind": info.kind,
                "tweets": info.tweets,
                "accounts": info.accounts,
                "first_tweet": info.first_tweet,
                "last_tweet": info.last_tweet,
                "full_text_search": bool(info.fts),
                "source": info.source,
            }
            for info in infos
        ]
        payload = {"total": len(items), "count": len(items), "items": items}
        if not items:
            return _render(
                payload,
                response_format,
                "## Archives\n\n_None imported. Run `ariadne dumps import <path>`._",
            )
        lines = [
            f"- **{i['name']}** ({i['kind']}) — {i['tweets']:,} tweets, "
            f"{i['accounts']:,} accounts, "
            f"{(i['first_tweet'] or '?')[:10]} → {(i['last_tweet'] or '?')[:10]}"
            + ("" if i["full_text_search"] else " _(no full-text index)_")
            for i in items
        ]
        return _render(payload, response_format, "## Archives\n\n" + "\n".join(lines))

    @tool("ariadne_list_users", "List handles in the archives")
    def ariadne_list_users(
        contains: str | None = None,
        dump: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
        response_format: Format = "markdown",
    ) -> str:
        """List the handles present in the archives, with post counts and spans.

        Use this to find the exact spelling of a handle before calling
        `ariadne_get_user_posts` or `ariadne_build_conversations`, which both
        need an exact match. A handle absent here has no posts in the library,
        even if other posts mention it.

        Args:
            contains: Only handles or display names containing this text.
            dump: Restrict to these dump names (default: all).
            limit: Max handles to return, 1-200 (default 20).
            offset: Handles to skip, for paging.
            response_format: 'markdown' (default) or 'json'.

        Returns:
            Paginated handles ordered by post count, each with username, display
            name, tweet count and first/last post dates.
        """
        limit = _clamp(limit, DEFAULT_LIMIT, MAX_LIMIT)
        with _Library(_dump_names(dump)) as client:
            users = client.users(match=contains)
            unresolved = client.unresolved_count()
        items = [
            {
                "username": u["username"],
                "name": u["name"],
                "tweets": u["tweets"],
                "first_tweet": u["first"],
                "last_tweet": u["last"],
            }
            for u in users
        ]
        payload = _page(items, limit, offset)
        payload["tweets_with_unresolved_author"] = unresolved
        lines = [
            f"- **@{i['username']}** ({i['name'] or '—'}) — {i['tweets']:,} tweets, "
            f"{(i['first_tweet'] or '?')[:10]} → {(i['last_tweet'] or '?')[:10]}"
            for i in payload["items"]
        ]
        body = _page_markdown("Handles", payload, lines)
        if unresolved:
            body += f"\n\n_{unresolved:,} tweets have no resolved author._"
        return _render(payload, response_format, body)

    @tool("ariadne_search_tweets", "Full-text search the archives")
    def ariadne_search_tweets(
        query: str,
        username: str | None = None,
        since: str | None = None,
        dump: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
        text_chars: int = DEFAULT_TEXT_CHARS,
        response_format: Format = "markdown",
    ) -> str:
        """Search the full text of every archived tweet.

        Uses each dump's full-text index where one exists and falls back to
        substring matching otherwise, so a dump imported with --no-fts still
        works but answers more slowly.

        Args:
            query: Search terms. FTS5 syntax works where indexed, e.g.
                'capitalism', '"exact phrase"', 'accel* NOT decel'.
            username: Restrict to one handle (exact, case-insensitive).
            since: Only tweets on or after this date, e.g. '2024-01-01'.
            dump: Restrict to these dump names (default: all).
            limit: Max tweets to return, 1-200 (default 20).
            offset: Tweets to skip, for paging.
            text_chars: Truncate each tweet's text to this many characters.
            response_format: 'markdown' (default) or 'json'.

        Returns:
            Paginated matching tweets with id, author, date, text and URL.
        """
        if not query.strip():
            raise ToolError(
                "query is empty.", "Pass search terms, e.g. query='capitalism'."
            )
        limit = _clamp(limit, DEFAULT_LIMIT, MAX_LIMIT)
        with _Library(_dump_names(dump)) as client:
            hits = client.search(
                query, username=username, since=since, limit=limit + offset + 1
            )
        items = [_tweet_dict(t, text_chars=text_chars) for t in hits]
        payload = _page(items, limit, offset, total_known=False)
        payload["query"] = query
        body = _page_markdown(
            f"Search: {query}", payload, [_tweet_line(i) for i in payload["items"]]
        )
        return _render(payload, response_format, body)

    @tool("ariadne_get_user_posts", "Read one handle's timeline")
    def ariadne_get_user_posts(
        username: str,
        since: str | None = None,
        include_retweets: bool = False,
        dump: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
        text_chars: int = DEFAULT_TEXT_CHARS,
        response_format: Format = "markdown",
    ) -> str:
        """Read a handle's archived posts, newest first.

        Returns the posts themselves, not conversations. Use
        `ariadne_build_conversations` to see who they were talking to.

        Args:
            username: Exact handle, with or without '@'. Find it with
                `ariadne_list_users`.
            since: Only posts on or after this date, e.g. '2024-01-01'.
            include_retweets: Include retweets (excluded by default).
            dump: Restrict to these dump names (default: all).
            limit: Max posts to return, 1-200 (default 20).
            offset: Posts to skip, for paging.
            text_chars: Truncate each post's text to this many characters.
            response_format: 'markdown' (default) or 'json'.

        Returns:
            Paginated posts with id, author, date, text, URL and reply/quote ids.
        """
        limit = _clamp(limit, DEFAULT_LIMIT, MAX_LIMIT)
        handle = username.strip().lstrip("@")
        with _Library(_dump_names(dump)) as client:
            result = client.get_user_posts(
                handle,
                since=since,
                limit=limit + offset + 1,
                include_retweets=include_retweets,
            )
        if not result.tweets and offset == 0:
            raise ToolError(
                f"No archived posts for @{handle}.",
                "Check the spelling with `ariadne_list_users`; the library may "
                "know the account without holding any of its posts.",
            )
        items = [_tweet_dict(t, text_chars=text_chars) for t in result.tweets]
        payload = _page(items, limit, offset, total_known=False)
        payload["username"] = handle
        body = _page_markdown(
            f"@{handle}", payload, [_tweet_line(i) for i in payload["items"]]
        )
        return _render(payload, response_format, body)

    @tool("ariadne_get_thread", "Show one tweet in context")
    def ariadne_get_thread(
        tweet: str,
        dump: list[str] | None = None,
        reply_limit: int | None = None,
        text_chars: int = DEFAULT_TEXT_CHARS,
        response_format: Format = "markdown",
    ) -> str:
        """Show one tweet with its ancestors, quoted tweets and direct replies.

        The ancestor walk follows reply-parent ids as far as the archives can;
        a parent no source holds appears as unavailable rather than being
        dropped, so a gap in the thread is visible.

        Args:
            tweet: Tweet id or an x.com/twitter.com status URL.
            dump: Restrict to these dump names (default: all).
            reply_limit: Max direct replies to show, 1-200 (default 20).
            text_chars: Truncate each tweet's text to this many characters.
            response_format: 'markdown' (default) or 'json'.

        Returns:
            {'target', 'ancestors', 'quoted', 'replies', 'reply_count'} where
            ancestors run root-first and each entry is a tweet record.
        """
        tweet_id = _resolve_tweet_id(tweet)
        reply_limit = _clamp(reply_limit, DEFAULT_LIMIT, MAX_LIMIT)
        with _Library(_dump_names(dump)) as client:
            found = {t.id: t for t in client.get_posts([tweet_id]).tweets}
            target = found.get(tweet_id)
            if target is None or not target.available:
                raise ToolError(
                    f"Tweet {tweet_id} is not in the archives.",
                    "Try `ariadne_search_tweets` to find it, or a different "
                    "`dump` scope; this server does not fetch from the network.",
                )
            ancestors: list[Tweet] = []
            seen = {tweet_id}
            current = target
            for _ in range(50):
                parent_id = current.reply_parent_id()
                if not parent_id or parent_id in seen:
                    break
                seen.add(parent_id)
                parents = client.get_posts([parent_id]).tweets
                current = parents[0] if parents else Tweet(id=parent_id, available=False)
                ancestors.append(current)
                if not parents:
                    break
            quoted = client.get_posts(target.quote_ids()).tweets if target.quote_ids() else []
            replies = client.children(tweet_id, limit=reply_limit + 1)

        payload = {
            "target": _tweet_dict(target, text_chars=text_chars),
            "ancestors": [
                _tweet_dict(t, text_chars=text_chars) for t in reversed(ancestors)
            ],
            "quoted": [_tweet_dict(t, text_chars=text_chars) for t in quoted],
            "replies": [
                _tweet_dict(t, text_chars=text_chars) for t in replies[:reply_limit]
            ],
            "reply_count": min(len(replies), reply_limit),
            "more_replies": len(replies) > reply_limit,
        }
        parts = ["## Thread"]
        if payload["ancestors"]:
            parts += ["", "### Ancestors (root first)"] + [
                _tweet_line(a) for a in payload["ancestors"]
            ]
        parts += ["", "### Target", _tweet_line(payload["target"])]
        if payload["quoted"]:
            parts += ["", "### Quoted"] + [_tweet_line(q) for q in payload["quoted"]]
        if payload["replies"]:
            heading = "### Direct replies" + (
                " (more exist)" if payload["more_replies"] else ""
            )
            parts += ["", heading] + [_tweet_line(r) for r in payload["replies"]]
        return _render(payload, response_format, "\n".join(parts))

    @tool("ariadne_build_conversations", "Reconstruct conversations")
    def ariadne_build_conversations(
        username: str,
        since: str | None = None,
        conversations_only: bool = False,
        dump: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
        max_posts: int | None = None,
        text_chars: int = DEFAULT_TEXT_CHARS,
        response_format: Format = "markdown",
    ) -> str:
        """Rebuild the conversations a handle took part in.

        For each of the handle's posts this walks reply and quote edges back to
        the root, so the result is a readable exchange rather than isolated
        posts. Every branch is labelled: 'conversation' means the handle
        substantively answered a different, identified author; 'corpus' means
        it is one-sided -- a standalone post, a self-thread, quote commentary,
        or a reply whose parent no archive holds.

        Only archived posts are used. Gaps stay visible as unavailable posts
        rather than being silently dropped, and are counted in 'missing_posts'.

        Args:
            username: Exact handle, with or without '@'.
            since: Only start from posts on or after this date, e.g. '2024-01-01'.
                Ancestors older than this are still followed.
            conversations_only: Keep only real exchanges, dropping corpus branches.
            dump: Restrict to these dump names (default: all).
            limit: Max conversations to return, 1-50 (default 5). Conversations
                are large; raise this only when you need breadth.
            offset: Conversations to skip, for paging.
            max_posts: Cap how many of the handle's posts seed the walk. Needed
                when a handle has more than 10,000 archived posts.
            text_chars: Truncate each post's text to this many characters.
            response_format: 'markdown' (default) or 'json'.

        Returns:
            {'total', 'count', 'offset', 'items', 'has_more', 'next_offset',
            'label_counts', 'missing_posts'} where each item is
            {'target_id', 'label', 'dataset_role', 'is_conversation',
            'participants', 'messages'} and each message carries role, author,
            date and text.
        """
        limit = _clamp(limit, DEFAULT_CONVERSATION_LIMIT, MAX_CONVERSATION_LIMIT)
        handle = username.strip().lstrip("@")
        options = BuildOptions(
            for_user=handle,
            since=since,
            responses_only=conversations_only,
            dump=_dump_names(dump) or [],
            dump_limit=max_posts,
            no_cache=True,
            allow_empty=True,
        )
        # Guard the library check before the build so a bad dump name is a
        # clear error rather than a silent empty result.
        with _Library(_dump_names(dump)):
            pass
        try:
            result = build_conversations(options)
        except ConfigurationError as exc:
            message = str(exc)
            if "more than" in message and "posts" in message:
                raise ToolError(
                    message,
                    "Pass `since` to narrow the range, or `max_posts` to cap how "
                    "many posts seed the walk.",
                ) from exc
            raise ToolError(message) from exc
        except AriadneError as exc:
            raise ToolError(str(exc)) from exc

        store = result.store
        items: list[dict[str, Any]] = []
        label_counts: dict[str, int] = {}
        missing = 0
        for conversation in result.conversations:
            classification = classify_conversation(conversation, store, subject=handle)
            label = classification.label
            label_counts[label] = label_counts.get(label, 0) + 1
            messages = []
            for tweet_id in conversation.path:
                post = store.get(tweet_id)
                if post is None or not post.available:
                    missing += 1
                row = _tweet_dict(
                    post or Tweet(id=tweet_id, available=False), text_chars=text_chars
                )
                row["role"] = (
                    "subject"
                    if post is not None
                    and (post.username or "").lower() == handle.lower()
                    else "other"
                )
                messages.append(row)
            items.append(
                {
                    "target_id": conversation.target_id,
                    "label": label,
                    "dataset_role": classification.dataset_role,
                    "is_conversation": classification.has_subject_response,
                    "participants": sorted(
                        {m["author"] for m in messages if m["author"]}
                    ),
                    "message_count": len(messages),
                    "messages": messages,
                }
            )

        payload = _page(items, limit, offset)
        payload["username"] = handle
        payload["label_counts"] = label_counts
        payload["missing_posts"] = missing
        payload["posts_used"] = len(result.target_ids)

        if not items:
            body = (
                f"## Conversations for @{handle}\n\n_None reconstructed._\n\n"
                "Check the handle with `ariadne_list_users`"
                + (", or widen `since`." if since else ".")
            )
            return _render(payload, response_format, body)

        parts = [
            f"## Conversations for @{handle}",
            "",
            f"{payload['total']:,} branches from {payload['posts_used']:,} posts; "
            f"labels: {', '.join(f'{k} {v:,}' for k, v in sorted(label_counts.items()))}.",
        ]
        for item in payload["items"]:
            parts += [
                "",
                f"### {item['target_id']} — {item['label']}"
                + ("" if item["is_conversation"] else " _(one-sided)_"),
            ]
            for message in item["messages"]:
                marker = "**>**" if message["role"] == "subject" else "  "
                date = (message.get("created_at") or "")[:10]
                parts.append(
                    f"{marker} {message['author']} `{date}`: {message['text'] or '[no text]'}"
                )
        if missing:
            parts += ["", f"_{missing:,} referenced post(s) are not in the archives._"]
        return _render(payload, response_format, "\n".join(parts))

    @tool("ariadne_cache_status", "Inspect a build cache")
    def ariadne_cache_status(
        cache: str = ".ariadne-cache.json",
        limit: int | None = None,
        response_format: Format = "markdown",
    ) -> str:
        """Report what a build cache holds and which posts it still lacks.

        Builds run from the CLI record the ids they could not resolve. This
        reads that record so you can see what is missing; recovering it needs
        the network, so run `ariadne cache retry` in a terminal.

        Args:
            cache: Path to the cache file (default '.ariadne-cache.json').
            limit: Max missing ids to list, 1-200 (default 20).
            response_format: 'markdown' (default) or 'json'.

        Returns:
            {'path', 'tweets', 'with_text', 'empty', 'missing', 'span',
            'missing_ids'} or an error if the cache does not exist.
        """
        limit = _clamp(limit, DEFAULT_LIMIT, MAX_LIMIT)
        summary = cache_summary(cache)
        if summary is None:
            raise ToolError(
                f"No cache at {cache}.",
                "Builds write `.ariadne-cache.json` unless --no-cache was used; "
                "pass `cache` if it lives elsewhere.",
            )
        retryable = cache_retry_ids(
            load_cache(cache), recorded_missing=load_missing_ids(cache)
        )
        payload = {
            "path": summary["path"],
            "tweets": summary["tweets"],
            "with_text": summary["with_text"],
            "empty": summary["empty"],
            "missing": summary["missing"],
            "span": f"{(summary['first'] or '?')[:10]} → {(summary['last'] or '?')[:10]}",
            "missing_ids": retryable[:limit],
            "missing_ids_shown": min(len(retryable), limit),
            "missing_ids_total": len(retryable),
        }
        body = (
            f"## Cache {payload['path']}\n\n"
            f"- {payload['tweets']:,} cached tweets ({payload['with_text']:,} with text, "
            f"{payload['empty']:,} empty)\n"
            f"- {payload['missing']:,} referenced tweets missing\n"
            f"- span {payload['span']}"
        )
        if retryable:
            body += (
                f"\n- {len(retryable):,} id(s) retryable; recover them with "
                f"`ariadne cache retry --cache {payload['path']}`"
            )
        return _render(payload, response_format, body)

    return server


def main(argv: list[str] | None = None) -> int:
    """Run the MCP server on stdio."""
    server = build_server()
    server.run(transport="stdio")
    return 0
