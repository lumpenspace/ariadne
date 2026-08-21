"""Bluesky (AT Protocol) as an alternative source.

Bluesky's public read API needs no auth and returns whole threads pre-nested,
so reconstruction is a walk over ``getPostThread``'s ``parent`` chain rather
than the fetch-by-id loop the X side needs. The output is the same
:class:`~ariadne.api.BuildResult` — ``raft_documents()`` and ``render()`` work
identically, so raft consumes X and Bluesky through one interface.

Post ids are AT-URIs (``at://did/app.bsky.feed.post/rkey``) rather than numeric
snowflakes; every consumer here treats ids as opaque strings.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .models import Conversation, Tweet
from .store import TweetStore
from .timeutil import is_on_or_after, parse_since

PUBLIC_BASE = "https://public.api.bsky.app/xrpc"


def build_bluesky(
    actor: str,
    *,
    limit: int = 60,
    since: str | None = None,
    include_replies: bool = True,
    base_url: str = PUBLIC_BASE,
    timeout: float = 20.0,
    allow_empty: bool = False,
):
    """Reconstruct an actor's Bluesky reply threads as a BuildResult.

    Args:
        actor: a handle (``alice.bsky.social``) or DID.
        limit: how many of the actor's recent posts to walk.
        since: only keep posts on or after this date.
        include_replies: keep the actor's replies (the interview-shaped
            material); standalone posts are always kept as single-message docs.
    """
    from .api import BuildResult  # local import avoids an import cycle

    client = BlueskyClient(base_url=base_url, timeout=timeout)
    store = TweetStore()
    warnings: list[str] = []
    since_dt = parse_since(since)

    try:
        feed = client.get_author_feed(actor, limit=limit)
    except urllib.error.URLError as exc:
        message = f"Bluesky request for @{actor.lstrip('@')} failed: {exc}"
        if allow_empty:
            return BuildResult(store, [], [message], [], should_save_cache=False)
        raise RuntimeError(message) from exc

    conversations: list[Conversation] = []
    target_ids: list[str] = []
    seen_targets: set[str] = set()

    for item in feed:
        if item.get("reason"):
            continue  # a repost, not the actor's own post
        post = item.get("post") or {}
        uri = post.get("uri")
        if not uri or uri in seen_targets:
            continue
        record = post.get("record") or {}
        if not is_on_or_after(record.get("createdAt"), since_dt):
            continue
        is_reply = bool(record.get("reply"))
        if is_reply and not include_replies:
            continue

        _store_post(store, post)
        path = [uri]
        if is_reply:
            try:
                path = client.ancestor_path(uri, store)
            except urllib.error.URLError as exc:
                warnings.append(f"Could not fetch Bluesky thread for {uri}: {exc}")
        seen_targets.add(uri)
        target_ids.append(uri)
        conversations.append(Conversation(target_id=uri, path=path, quotes=[], warnings=[]))

    from .reconstruct import prune_subset_conversations

    conversations = prune_subset_conversations(conversations)
    if not conversations and not allow_empty:
        raise RuntimeError(f"No Bluesky posts reconstructed for @{actor.lstrip('@')}")
    return BuildResult(
        store=store,
        conversations=conversations,
        warnings=warnings,
        target_ids=target_ids,
        should_save_cache=False,
    )


class BlueskyClient:
    def __init__(self, *, base_url: str = PUBLIC_BASE, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, method: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self.base_url}/{method}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "ariadne"}
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    def get_author_feed(self, actor: str, *, limit: int = 60) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        handle = actor.lstrip("@")
        while len(items) < limit:
            params = {
                "actor": handle,
                "filter": "posts_with_replies",
                "limit": str(min(100, limit - len(items))),
            }
            if cursor:
                params["cursor"] = cursor
            payload = self._get("app.bsky.feed.getAuthorFeed", params)
            batch = payload.get("feed") or []
            items.extend(batch)
            cursor = payload.get("cursor")
            if not cursor or not batch:
                break
        return items

    def ancestor_path(self, uri: str, store: TweetStore, *, max_depth: int = 60) -> list[str]:
        payload = self._get(
            "app.bsky.feed.getPostThread",
            {"uri": uri, "depth": "0", "parentHeight": str(max_depth)},
        )
        node = payload.get("thread") or {}
        path: list[str] = []
        seen: set[str] = set()
        while isinstance(node, dict) and node.get("post"):
            post = node["post"]
            post_uri = post.get("uri")
            if not post_uri or post_uri in seen:
                break
            seen.add(post_uri)
            _store_post(store, post)
            path.append(post_uri)
            node = node.get("parent")
            if node is None or len(path) >= max_depth:
                break
        path.reverse()
        return path or [uri]


def _store_post(store: TweetStore, post: dict[str, Any]) -> None:
    uri = post.get("uri")
    if not uri:
        return
    author = post.get("author") or {}
    record = post.get("record") or {}
    handle = author.get("handle")
    reply = record.get("reply") or {}
    parent_uri = (reply.get("parent") or {}).get("uri")
    store.add(
        Tweet(
            id=str(uri),
            text=str(record.get("text") or ""),
            author_id=author.get("did"),
            username=handle,
            name=author.get("displayName") or handle,
            created_at=record.get("createdAt"),
            in_reply_to_id=parent_uri,
            in_reply_to_username=None,
            source="bluesky",
            url=_web_url(uri, handle),
            raw=post,
        )
    )


def _web_url(uri: str, handle: str | None) -> str | None:
    # at://did/app.bsky.feed.post/rkey -> https://bsky.app/profile/<handle>/post/<rkey>
    rkey = uri.rsplit("/", 1)[-1] if "/" in uri else uri
    who = handle or (uri.split("/")[2] if uri.startswith("at://") else None)
    return f"https://bsky.app/profile/{who}/post/{rkey}" if who else None
