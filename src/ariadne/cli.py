from __future__ import annotations

import argparse
import getpass
import os
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

from . import hx
from .api import (
    DEFAULT_RSS_BASES,
    BuildOptions,
    BuildResult,
    CheapFirstFetcher,
    build_conversations,
    candidate_reply_lookup_ids,
    conversation_ids,
    enrich_after_cheap_sources,
    hydrate_empty_tweets,
    is_reply_start,
    is_selected_since,
    load_store,
    make_branch_fetcher,
    make_x_client,
    needs_official_metadata,
    rss_bases,
    select_targets,
    should_save_cache,
    target_username,
)
from .archive import load_archive
from .credentials import (
    TWITTERAPI_KEY,
    X_BEARER_TOKEN,
    delete_credential,
    load_credential,
    save_credential,
)
from .dumps import LocalDumpsClient, import_dump, list_dumps, remove_dump
from .fetch import XApiError
from .ids import tweet_id_from_url, unique_preserve_order
from .models import Tweet
from .render import render
from .store import cache_summary, stream_path
from .store import save_cache  # noqa: F401  (stays importable from ariadne.cli)


COMMANDS = {"build", "inspect-archive", "interactive", "bluesky", "dumps", "cache"}

# The pipeline moved to `api`; this module is now just an argparse front-end.
# Everything below stays importable from `ariadne.cli` because it was public
# here before the split.
collect_conversations = build_conversations

__all__ = [
    "main",
    "build_parser",
    "add_build_arguments",
    "build",
    "interactive",
    "inspect_archive",
    # Re-exported from `ariadne.api` for backwards compatibility.
    "BuildOptions",
    "BuildResult",
    "CheapFirstFetcher",
    "DEFAULT_RSS_BASES",
    "build_conversations",
    "collect_conversations",
    "candidate_reply_lookup_ids",
    "conversation_ids",
    "enrich_after_cheap_sources",
    "hydrate_empty_tweets",
    "is_reply_start",
    "is_selected_since",
    "load_store",
    "make_branch_fetcher",
    "make_x_client",
    "needs_official_metadata",
    "rss_bases",
    "select_targets",
    "should_save_cache",
    "target_username",
]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in {"-h", "--help"}):
        argv = ["build"] + argv

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect-archive":
            return inspect_archive(args)
        if args.command == "interactive":
            return interactive(args)
        if args.command == "bluesky":
            return bluesky_command(args)
        if args.command == "dumps":
            return dumps_command(args)
        if args.command == "cache":
            return cache_command(args)
        return build(args)
    except (
        OSError,
        sqlite3.Error,
        RuntimeError,
        XApiError,
        ValueError,
        argparse.ArgumentTypeError,
    ) as exc:
        print(f"ariadne: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ariadne",
        description="Reconstruct X/Twitter reply branches and render them as LLM input.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_cmd = subparsers.add_parser("build", help="Build conversation branches.")
    add_build_arguments(build_cmd)

    interactive_cmd = subparsers.add_parser("interactive", help="Prompt for sources and build settings.")
    interactive_cmd.add_argument(
        "--cache",
        default=".ariadne-cache.json",
        help="JSON cache for fetched/hydrated tweets. Default: .ariadne-cache.json",
    )

    inspect_cmd = subparsers.add_parser("inspect-archive", help="Summarize archive tweet data.")
    inspect_cmd.add_argument("archive", help="X/Twitter archive folder, zip, or data file.")

    bluesky_cmd = subparsers.add_parser("bluesky", help="Reconstruct reply threads from Bluesky.")
    bluesky_cmd.add_argument("actor", help="Bluesky handle (alice.bsky.social) or DID.")
    bluesky_cmd.add_argument("--since", help="Only posts on or after this date, e.g. 2024-01-01.")
    bluesky_cmd.add_argument("--limit", type=positive_int, default=60, help="Recent posts to walk. Default: 60.")
    bluesky_cmd.add_argument("--no-replies", action="store_true", help="Skip the actor's replies.")
    bluesky_cmd.add_argument(
        "--responses-only",
        action="store_true",
        help="Only keep substantive replies to a known different author.",
    )
    bluesky_cmd.add_argument(
        "--format",
        choices=("messages", "openai", "json", "markdown", "raft"),
        default="messages",
        help="Output format. Default: messages.",
    )
    bluesky_cmd.add_argument("-o", "--output", help="Write output to this file instead of stdout.")

    dumps_cmd = subparsers.add_parser("dumps", help="Import and explore local bulk tweet dumps.")
    add_dumps_arguments(dumps_cmd)

    cache_cmd = subparsers.add_parser(
        "cache", help="Inspect build caches and retry their missing tweets."
    )
    add_cache_arguments(cache_cmd)
    return parser


def add_cache_arguments(cache_cmd: argparse.ArgumentParser) -> None:
    actions = cache_cmd.add_subparsers(dest="cache_action", required=True)

    list_cmd = actions.add_parser(
        "list",
        help="Summarize cache files: tweets held, span, authors, and what is still missing.",
    )
    list_cmd.add_argument(
        "paths",
        nargs="*",
        help="Cache files to summarize. Default: .ariadne-cache.json here and in your home directory.",
    )

    missing_cmd = actions.add_parser(
        "missing",
        help="Print the tweet ids a cache still lacks, one per line (pipe-friendly).",
    )
    missing_cmd.add_argument(
        "--cache",
        default=".ariadne-cache.json",
        help="Cache file to inspect. Default: .ariadne-cache.json",
    )

    retry_cmd = actions.add_parser(
        "retry",
        help=(
            "Fetch a cache's missing tweets through the available sources and "
            "update it in place. Safe to run in a later session; run it after "
            "a build using the same cache has finished, not alongside it."
        ),
    )
    retry_cmd.add_argument(
        "--cache",
        default=".ariadne-cache.json",
        help="Cache file to update. Default: .ariadne-cache.json",
    )
    retry_cmd.add_argument(
        "--limit", type=positive_int, help="Retry at most this many missing tweets."
    )
    retry_cmd.add_argument(
        "--dump",
        action="append",
        default=[],
        help="Use only this imported local dump. Can be repeated. Default: all.",
    )
    retry_cmd.add_argument("--no-dumps", action="store_true", help="Do not read imported local dumps.")
    retry_cmd.add_argument(
        "--no-oembed",
        action="store_true",
        help="Skip the free public oEmbed lookups (on by default).",
    )
    retry_cmd.add_argument(
        "--community-archive",
        action="store_true",
        help="Also try the Community Archive (community-archive.org, no key).",
    )
    retry_cmd.add_argument(
        "--twitterapi-key",
        help="twitterapi.io API key (or set TWITTERAPI_IO_KEY) to use it as a source.",
    )
    retry_cmd.add_argument(
        "--fetch",
        action="store_true",
        help="Also use X API v2 with X_BEARER_TOKEN or --bearer-token.",
    )
    retry_cmd.add_argument("--bearer-token", help="X API bearer token for --fetch.")


def add_dumps_arguments(dumps_cmd: argparse.ArgumentParser) -> None:
    actions = dumps_cmd.add_subparsers(dest="dumps_action", required=True)

    import_cmd = actions.add_parser(
        "import",
        help="Import a dump directory/file into the settings directory (~/.ariadne/dumps).",
    )
    import_cmd.add_argument("path", help="Source directory or file holding the dump.")
    import_cmd.add_argument("--name", help="Name for the imported dump. Default: derived from the path.")
    import_cmd.add_argument(
        "--kind",
        choices=("community-csv", "parquet", "twitter-archive", "tweets-file"),
        help="Dump kind. Default: auto-detected.",
    )
    import_cmd.add_argument("--no-fts", action="store_true", help="Skip building the full-text search index.")

    actions.add_parser("list", help="List imported dumps.")

    interactive_cmd = actions.add_parser(
        "interactive",
        help="Explore imported dumps with a menu-driven interface.",
    )
    interactive_cmd.add_argument(
        "--dump",
        action="append",
        default=[],
        help="Start with this dump selected. Can be repeated. Default: all.",
    )
    interactive_cmd.add_argument(
        "--reply-limit",
        type=positive_int,
        default=20,
        help="Default maximum direct replies shown for a tweet. Default: 20.",
    )

    users_cmd = actions.add_parser("users", help="List users present in the imported dumps.")
    users_cmd.add_argument("--dump", action="append", default=[], help="Restrict to this dump. Can be repeated.")
    users_cmd.add_argument("--top", type=positive_int, default=40, help="Rows to show. Default: 40.")
    users_cmd.add_argument("--find", help="Only users whose handle or name contains this text.")

    search_cmd = actions.add_parser("search", help="Full-text search across imported dumps.")
    search_cmd.add_argument("query", help="FTS5 query (falls back to substring where FTS is unavailable).")
    search_cmd.add_argument("--dump", action="append", default=[], help="Restrict to this dump. Can be repeated.")
    search_cmd.add_argument("--user", help="Only tweets by this username.")
    search_cmd.add_argument("--since", help="Only tweets on or after this date, e.g. 2020-01-01.")
    search_cmd.add_argument("--limit", type=positive_int, default=20, help="Max results. Default: 20.")

    user_cmd = actions.add_parser("user", help="Show a user's tweets from the imported dumps.")
    user_cmd.add_argument("username", help="Handle, with or without the leading @.")
    user_cmd.add_argument("--dump", action="append", default=[], help="Restrict to this dump. Can be repeated.")
    user_cmd.add_argument("--since", help="Only tweets on or after this date.")
    user_cmd.add_argument("--limit", type=positive_int, default=20, help="Max tweets to show. Default: 20.")
    user_cmd.add_argument("--retweets", action="store_true", help="Include retweets.")

    show_cmd = actions.add_parser("show", help="Show one tweet with its thread context.")
    show_cmd.add_argument("tweet_id", help="Tweet ID or URL.")
    show_cmd.add_argument("--dump", action="append", default=[], help="Restrict to this dump. Can be repeated.")
    show_cmd.add_argument(
        "--reply-limit",
        type=positive_int,
        default=20,
        help="Maximum direct replies to show. Default: 20.",
    )

    remove_cmd = actions.add_parser("remove", help="Delete an imported dump database.")
    remove_cmd.add_argument("name", help="Dump name as shown by `ariadne dumps list`.")


def add_build_arguments(build_cmd: argparse.ArgumentParser) -> None:
    build_cmd.add_argument("items", nargs="*", help="Tweet IDs or URLs.")
    build_cmd.add_argument(
        "-i",
        "--input-file",
        action="append",
        default=[],
        help="File containing tweet IDs or URLs. Can be repeated.",
    )
    build_cmd.add_argument(
        "-a",
        "--archive",
        action="append",
        default=[],
        help="X/Twitter archive folder, zip, or data file. Can be repeated.",
    )
    build_cmd.add_argument(
        "--tweets-file",
        action="append",
        default=[],
        help="Generic CSV/JSON/JSONL tweet dump. Can be repeated.",
    )
    build_cmd.add_argument("--for-user", help="Select every loaded/fetched tweet by this username.")
    build_cmd.add_argument(
        "--target-user",
        help=(
            "Collect an arbitrary public user's tweets cheap-first. Implies --for-user, "
            "--oembed, and unofficial RSS unless disabled."
        ),
    )
    build_cmd.add_argument("--author-id", help="Select every loaded/fetched tweet by this author ID.")
    build_cmd.add_argument(
        "--all-loaded",
        action="store_true",
        help="Select every loaded/fetched tweet on or after --since. Useful for archive-only interactive runs.",
    )
    build_cmd.add_argument(
        "--replies-only",
        action="store_true",
        help=(
            "Only use reply tweets as starting targets. Archive/API/dump sources need reply metadata; "
            "unofficial reply feeds are treated as reply candidates."
        ),
    )
    build_cmd.add_argument(
        "--responses-only",
        action="store_true",
        help=(
            "Only keep substantive replies to a known different author. "
            "Drops standalone posts, self-threads, quote commentary, and incomplete replies."
        ),
    )
    build_cmd.add_argument("--since", help="Only select user tweets on or after this date, e.g. 2024-01-01.")
    build_cmd.add_argument(
        "--cheap-first",
        action="store_true",
        help="Try local/cache/oEmbed/unofficial sources before paid X API calls. Implied by --target-user.",
    )
    build_cmd.add_argument(
        "--oembed",
        action="store_true",
        help="Use public X oEmbed to hydrate tweets that have URLs/IDs but no text.",
    )
    build_cmd.add_argument(
        "--no-oembed",
        action="store_true",
        help="Disable oEmbed defaults implied by --target-user.",
    )
    build_cmd.add_argument(
        "--hydrate-all",
        action="store_true",
        help="With --oembed, hydrate every empty tweet in the store, not just rendered conversations.",
    )
    build_cmd.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch missing tweets through X API v2 using X_BEARER_TOKEN or --bearer-token.",
    )
    build_cmd.add_argument(
        "--fetch-user-timeline",
        action="store_true",
        help="Fetch the --for-user timeline through X API before selecting tweets.",
    )
    build_cmd.add_argument(
        "--unofficial-rss",
        action="store_true",
        help="Try an unsupported Nitter/XCancel-style RSS feed for --for-user when no X API is available.",
    )
    build_cmd.add_argument(
        "--no-unofficial-rss",
        action="store_true",
        help="Disable unofficial RSS defaults implied by --target-user.",
    )
    build_cmd.add_argument(
        "--rss-base",
        action="append",
        help=(
            "Base URL for Nitter/XCancel-style RSS. Can be repeated. "
            "Default with --target-user: https://nitter.net and https://rss.xcancel.com."
        ),
    )
    build_cmd.add_argument(
        "--rss-url-template",
        action="append",
        default=[],
        help=(
            "Full RSS URL template for other cheap RSS services. Use {username}; "
            "can be repeated."
        ),
    )
    build_cmd.add_argument(
        "--max-user-pages",
        type=positive_int,
        help="Limit X API user timeline pages. Each page is up to 100 tweets.",
    )
    build_cmd.add_argument("--bearer-token", help="X API bearer token for --fetch or --fetch-user-timeline.")
    build_cmd.add_argument(
        "--cache",
        default=".ariadne-cache.json",
        help="JSON cache for fetched/hydrated tweets. Default: .ariadne-cache.json",
    )
    build_cmd.add_argument("--no-cache", action="store_true", help="Do not read or write the cache.")
    build_cmd.add_argument(
        "--format",
        choices=("messages", "openai", "json", "markdown", "raft"),
        default="messages",
        help="Output format. Default: messages.",
    )
    build_cmd.add_argument("-o", "--output", help="Write output to this file instead of stdout.")
    build_cmd.add_argument("--strict", action="store_true", help="Fail if a tweet cannot be resolved.")
    build_cmd.add_argument(
        "--max-depth",
        type=positive_int,
        default=50,
        help="Maximum reply ancestors to follow per branch. Default: 50.",
    )
    build_cmd.add_argument("--no-quotes", action="store_true", help="Do not attach quote contexts.")
    build_cmd.add_argument(
        "--no-quote-as-reply",
        dest="quote_as_reply",
        action="store_false",
        help="Do not splice a root quote-tweet's quoted tweet in as its reply-parent.",
    )
    build_cmd.add_argument(
        "--community-archive",
        action="store_true",
        help="Use the Community Archive (community-archive.org) to enumerate the "
        "target and complete reply/quote parents by anyone in the archive.",
    )
    build_cmd.add_argument(
        "--twitterapi-key",
        help="twitterapi.io API key (or set TWITTERAPI_IO_KEY) to use it as a source.",
    )
    build_cmd.add_argument(
        "--dump",
        action="append",
        default=[],
        help="Use only this imported local dump (see `ariadne dumps`). Can be repeated. Default: all.",
    )
    build_cmd.add_argument(
        "--no-dumps",
        action="store_true",
        help="Do not read imported local dumps.",
    )
    build_cmd.add_argument(
        "--dump-limit",
        type=positive_int,
        help=(
            "Maximum posts to select for one user/author from local dumps. "
            "Timelines over 10,000 require this or a narrower --since."
        ),
    )


def bluesky_command(args: argparse.Namespace) -> int:
    from .api import conversation_has_response
    from .bluesky import build_bluesky

    result = build_bluesky(
        args.actor,
        limit=args.limit,
        since=args.since,
        include_replies=not args.no_replies,
    )
    if args.responses_only:
        # subject=None: the actor may be given as a DID, but each
        # conversation's target post is theirs, so the fallback matches.
        result.conversations = [
            conversation
            for conversation in result.conversations
            if conversation_has_response(conversation, result.store)
        ]
    emit_warnings(result.warnings)
    output = render(result.conversations, result.store, output_format=args.format)
    if args.output:
        Path(args.output).expanduser().write_text(output, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(output)
    return 0


def announce_stream(args: argparse.Namespace) -> None:
    """Say where paid reads are being written before any of them happen."""
    if (getattr(args, "fetch", False) or getattr(args, "fetch_user_timeline", False)) and not getattr(
        args, "no_cache", False
    ):
        hx.say(f"streaming X API reads to {stream_path(args.cache)} as they arrive")


def build(args: argparse.Namespace) -> int:
    announce_stream(args)
    result = collect_conversations(args)
    emit_warnings(result.warnings)

    if result.should_save_cache:
        result.save_cache(args.cache)
        unresolved = result.unresolved_ids()
        if unresolved:
            hx.say(
                f"{len(unresolved)} tweet(s) could not be resolved; retry them "
                f"any time with: ariadne cache retry --cache {args.cache}"
            )

    # result.render is subject-aware: the collected user stays the
    # assistant even where a conversation's own tweets cannot tell.
    output = result.render(args.format)
    if args.output:
        Path(args.output).expanduser().write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0


def interactive(args: argparse.Namespace) -> int:
    hx.banner("reconstruct reply branches interactively")
    hx.say("cheap sources are tried first; X API is offered only after a summary")

    archive = prompt_existing_path("Archive/dump path first (zip/folder/tweets.js/json/csv/jsonl)")
    tweets_file = ""
    if archive:
        extra = prompt_existing_path("Additional generic tweet dump")
        tweets_file = extra or ""
    else:
        tweets_file = prompt_existing_path("Generic tweet dump path")

    local_sources = bool(archive or tweets_file)
    inferred_username = infer_archive_username(archive) if archive and not _is_generic_tweet_file(archive) else ""
    username_default = inferred_username or None
    username = prompt(
        "User handle to collect",
        default=username_default,
        required=not local_sources,
    ).strip().strip("@")
    all_loaded = not username and local_sources
    since = prompt("As far back as date [YYYY-MM-DD]")
    output_format = prompt_choice("Output format", ("messages", "openai", "json", "markdown", "raft"), default="messages")
    output = prompt_path(
        "Output file", default=default_output_name(output_format, username, since)
    )
    max_depth_text = prompt("Max reply depth", default="50")
    max_depth = positive_int(max_depth_text)

    use_oembed = prompt_yes_no("Use public oEmbed to fill missing tweet text before paid lookups?", default=True)
    hydrate_all = False
    if use_oembed:
        hydrate_all = prompt_yes_no("Hydrate every empty tweet found, not just rendered conversations?", default=False)

    use_unofficial_rss = prompt_yes_no("Try unofficial/free RSS before any paid X API calls?", default=True)
    rss_bases = list(DEFAULT_RSS_BASES)
    rss_templates: list[str] = []
    if use_unofficial_rss:
        bases = prompt("RSS base URLs, comma-separated", default=", ".join(DEFAULT_RSS_BASES))
        rss_bases = parse_csv_values(bases) or list(DEFAULT_RSS_BASES)
        templates = prompt("Extra RSS URL templates with {username}, comma-separated")
        rss_templates = parse_csv_values(templates)
    replies_only = prompt_yes_no("Use only reply tweets as starting targets?", default=False)
    responses_only = prompt_yes_no(
        "Drop standalone posts and self-threads entirely? "
        "They will not enter the corpus",
        default=False,
    )

    # A cache from earlier sessions may already hold tweets -- and a list of
    # the ones those sessions could not get. Offer to retry them first, so
    # anything recovered flows straight into this build.
    summary = cache_summary(args.cache)
    if summary and summary["tweets"]:
        retryable = summary["missing"] + summary["empty"]
        hx.say(
            f"cache {args.cache}: {summary['tweets']} tweet(s) from earlier "
            f"sessions, {retryable} still missing"
        )
        if retryable and prompt_yes_no(
            "Try to fetch the missing ones again before building?", default=True
        ):
            from .api import retry_cache

            retry = retry_cache(args.cache, oembed=use_oembed)
            hx.ok(
                f"recovered {len(retry.recovered)} of {len(retry.wanted)} "
                f"missing tweet(s)"
            )

    cheap_args = BuildOptions(
        items=[],
        input_file=[],
        archive=[archive] if archive and not _is_generic_tweet_file(archive) else [],
        tweets_file=[path for path in (tweets_file, archive if _is_generic_tweet_file(archive) else "") if path],
        for_user=None,
        target_user=username or None,
        author_id=None,
        all_loaded=all_loaded,
        replies_only=replies_only,
        responses_only=responses_only,
        since=since or None,
        cheap_first=True,
        oembed=use_oembed,
        no_oembed=not use_oembed,
        hydrate_all=hydrate_all,
        fetch=False,
        fetch_user_timeline=False,
        unofficial_rss=use_unofficial_rss and bool(username),
        no_unofficial_rss=not (use_unofficial_rss and bool(username)),
        rss_base=rss_bases,
        rss_url_template=rss_templates,
        max_user_pages=None,
        bearer_token=None,
        cache=args.cache,
        no_cache=False,
        format=output_format,
        output=None,
        strict=False,
        max_depth=max_depth,
        no_quotes=False,
        allow_empty=True,
    )

    cheap_result = build_conversations(cheap_args)
    print_interactive_summary("Cheap-source pass", cheap_result)
    cheap_result.save_cache(args.cache)

    report_missing(cheap_result)
    final_result = cheap_result

    # Escalate one rung at a time, cheapest first. Each pass reuses the store
    # the previous one filled, so nothing already found is fetched twice, and
    # the gap is recounted between rungs -- a free source may close it and
    # make the paid ones unnecessary.
    if should_continue_with_x_api(cheap_result) and prompt_yes_no(
        "Try the Community Archive? It is free, and covers donor accounts",
        default=True,
    ):
        # RSS already ran in the cheap pass; re-running it would just repeat
        # the same requests against the same feeds.
        ca_args = replace(
            cheap_args,
            community_archive=True,
            unofficial_rss=False,
            no_unofficial_rss=True,
            allow_empty=True,
        )
        final_result = build_conversations(ca_args, base_store=final_result.store)
        print_interactive_summary("After Community Archive", final_result)
        report_missing(final_result)

    if should_continue_with_x_api(final_result) and prompt_yes_no(
        "Try twitterapi.io? It is a paid gateway, but cheaper per read than X",
        default=False,
    ):
        tapio_key = prompt_saved_secret(
            "twitterapi.io API key",
            credential=TWITTERAPI_KEY,
            env_names=("TWITTERAPI_IO_KEY",),
        )
        if tapio_key:
            tapio_args = replace(
                cheap_args,
                twitterapi_key=tapio_key,
                unofficial_rss=False,
                no_unofficial_rss=True,
                allow_empty=True,
            )
            final_result = build_conversations(tapio_args, base_store=final_result.store)
            print_interactive_summary("After twitterapi.io", final_result)
            report_missing(final_result)
            remember_secret("twitterapi.io key", TWITTERAPI_KEY, tapio_key)
        else:
            hx.warn("skipping twitterapi.io because no key was provided")

    continue_default = should_continue_with_x_api(final_result)
    use_x_api = prompt_yes_no(
        "Continue with X API now? This may cost API reads",
        default=continue_default,
    )

    if use_x_api:
        token = prompt_x_api_token(local_sources=local_sources, has_username=bool(username))
        if token:
            max_user_pages = None
            fetch_timeline = False
            if username:
                fetch_timeline = prompt_yes_no(
                    "Use X API to backfill the user's timeline after cheap sources?",
                    default=True,
                )
                if fetch_timeline:
                    pages = prompt("Max X timeline pages, 100 tweets/page")
                    max_user_pages = positive_int(pages) if pages else None
            hx.say(f"streaming X API reads to {stream_path(args.cache)} as they arrive")
            paid_args = replace(
                cheap_args,
                fetch=True,
                fetch_user_timeline=fetch_timeline,
                bearer_token=token,
                max_user_pages=max_user_pages,
                unofficial_rss=False,
                no_unofficial_rss=True,
                allow_empty=True,
            )
            final_result = build_conversations(paid_args, base_store=final_result.store)
            print_interactive_summary("After X API pass", final_result)
            # Remember a token that worked; a rejected one is dropped by the
            # pipeline itself, so only save when X was not shut off.
            if x_api_succeeded(final_result):
                remember_secret("X bearer token", X_BEARER_TOKEN, token)
            else:
                hx.warn("not remembering this X token: the API did not accept it")
        else:
            hx.warn("skipping X API because no bearer token was provided")

    if not final_result.conversations:
        emit_warnings(final_result.warnings)
        hx.warn("no conversations were reconstructed")
        return 0

    emit_warnings(final_result.warnings)
    final_result.save_cache(args.cache)

    rendered = final_result.render(output_format)
    if output:
        Path(output).expanduser().write_text(rendered, encoding="utf-8")
        hx.ok(f"wrote {output}")
    else:
        sys.stdout.write(rendered)
    unresolved = final_result.unresolved_ids()
    if unresolved:
        hx.say(
            f"{len(unresolved)} tweet(s) are still unresolved; retry any time "
            f"with: ariadne cache retry --cache {args.cache}"
        )
    return 0


def emit_warnings(warnings: list[str]) -> None:
    for warning in unique_preserve_order(warnings):
        hx.warn(warning)


def print_interactive_summary(label: str, result: BuildResult) -> None:
    hx.step(label)
    hx.say(f"tweets in store: {len(result.store.all())}")
    hx.say(f"selected target tweets: {len(result.target_ids)}")
    hx.say(f"reconstructed conversations: {len(result.conversations)}")
    hx.say(f"unavailable tweets in reconstructed output: {len(unavailable_conversation_ids(result))}")
    hx.say(f"warnings: {len(unique_preserve_order(result.warnings))}")
    counts = source_counts(result.store.all())
    if counts:
        hx.say("sources:")
        for source, count in counts.most_common():
            hx.say(f"  {source}: {count}")
    if result.warnings:
        hx.say("recent warnings:")
        for warning in unique_preserve_order(result.warnings)[-5:]:
            hx.say(f"  - {warning}")


def source_counts(tweets: list[Tweet]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for tweet in tweets:
        if not tweet.source:
            counts["unknown"] += 1
            continue
        for source in tweet.source.split(","):
            source = source.strip()
            if source:
                counts[source] += 1
    return counts


def unavailable_conversation_ids(result: BuildResult) -> set[str]:
    ids: set[str] = set()
    for conversation in result.conversations:
        for tweet_id in conversation.all_ids:
            tweet = result.store.get(tweet_id)
            if tweet is None or not tweet.available:
                ids.add(tweet_id)
    return ids


def should_continue_with_x_api(result: BuildResult) -> bool:
    return not result.conversations or bool(unavailable_conversation_ids(result))


def report_missing(result: BuildResult) -> None:
    """State the size of the gap before offering to spend money on it."""
    missing_ids = unavailable_conversation_ids(result)
    if missing_ids:
        incomplete = sum(
            1
            for conversation in result.conversations
            if conversation.all_ids & missing_ids
        )
        hx.say(
            f"{len(missing_ids)} tweet(s) are missing to complete "
            f"{incomplete} of {len(result.conversations)} conversation(s)"
        )
    elif result.conversations:
        hx.say("no tweets are missing; every reconstructed conversation is complete")


def x_api_succeeded(result: BuildResult) -> bool:
    """Whether the X pass ran without the pipeline switching X off."""
    return not any("X API unavailable" in warning for warning in result.warnings)


def remember_secret(label: str, credential: str, value: str) -> None:
    """Save a working secret, telling the user where it went.

    Storing a token is a side effect on the user's filesystem, so it is
    announced with its path rather than done quietly.
    """
    if load_credential(credential) == value:
        return
    path = save_credential(credential, value)
    if path is None:
        hx.warn(f"could not save the {label}; you will be asked for it again")
        return
    hx.ok(f"saved the {label} to {path} (delete that file to forget it)")


def prompt_saved_secret(
    label: str,
    *,
    credential: str,
    env_names: tuple[str, ...] = (),
) -> str:
    """Read a secret, preferring the environment and then a remembered one."""
    for name in env_names:
        value = os.environ.get(name)
        if value:
            if prompt_yes_no(f"Use {name} from the environment?", default=True):
                return value
    remembered = load_credential(credential)
    if remembered:
        if prompt_yes_no(f"Use the {label} saved from a previous run?", default=True):
            return remembered
        if prompt_yes_no("Forget that saved value?", default=False):
            delete_credential(credential)
    return prompt_secret(label)


def prompt_x_api_token(*, local_sources: bool, has_username: bool) -> str:
    prompt_text = "X API bearer token for missing parents"
    if has_username:
        prompt_text += "/user timeline"
    if local_sources:
        prompt_text += " (optional, may cost API reads)"
    else:
        prompt_text += " (needed if cheap sources found nothing)"
    return prompt_saved_secret(
        prompt_text,
        credential=X_BEARER_TOKEN,
        env_names=("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN"),
    )


def infer_archive_username(path: str) -> str:
    if not path:
        return ""
    try:
        account = load_archive(path).account or {}
    except (FileNotFoundError, ValueError):
        return ""
    return str(account.get("username") or "").strip().strip("@")


def inspect_archive(args: argparse.Namespace) -> int:
    result = load_archive(args.archive)
    account = result.account or {}
    print(f"files_read: {len(result.files_read)}")
    print(f"tweets: {len(result.tweets)}")
    if account:
        print(f"account_id: {account.get('accountId') or ''}")
        print(f"username: {account.get('username') or ''}")
    if result.warnings:
        print("warnings:", file=sys.stderr)
        for warning in result.warnings:
            print(f"- {warning}", file=sys.stderr)
    return 0


def cache_command(args: argparse.Namespace) -> int:
    from .api import retry_cache
    from .store import cache_retry_ids, cache_summary, load_cache, load_missing_ids

    if args.cache_action == "list":
        candidates = [Path(p).expanduser() for p in args.paths] or [
            path
            for path in (Path(".ariadne-cache.json"), Path.home() / ".ariadne-cache.json")
            if path.exists()
        ]
        paths, seen = [], set()
        for path in candidates:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                paths.append(path)
        if not paths:
            hx.warn("no cache files found; builds write .ariadne-cache.json unless --no-cache")
            return 0
        for path in paths:
            summary = cache_summary(path)
            if summary is None:
                hx.warn(f"not found: {path}")
                continue
            print(summary["path"])
            print(
                f"  {summary['tweets']:,} cached tweet(s), {summary['with_text']:,} with text, "
                f"{summary['empty']:,} empty stub(s); "
                f"{summary['missing']:,} referenced tweet(s) missing"
            )
            if summary["first"]:
                print(f"  span {summary['first'][:10]} → {summary['last'][:10]}")
            if summary["authors"]:
                authors = ", ".join(f"{name} ({count})" for name, count in summary["authors"])
                print(f"  authors: {authors}")
            if summary["sources"]:
                sources = ", ".join(f"{name} ({count})" for name, count in summary["sources"])
                print(f"  sources: {sources}")
            if summary["missing"] or summary["empty"]:
                hx.say(f"retry them with: ariadne cache retry --cache {summary['path']}")
        return 0

    if args.cache_action == "missing":
        tweets = load_cache(args.cache)
        recorded = load_missing_ids(args.cache)
        if not tweets and not recorded:
            raise RuntimeError(f"Cache not found or empty: {args.cache}")
        for tweet_id in cache_retry_ids(tweets, recorded_missing=recorded):
            print(tweet_id)
        return 0

    # retry
    hx.step(f"retrying missing tweets from {args.cache}")
    result = retry_cache(
        args.cache,
        limit=args.limit,
        dump=args.dump,
        no_dumps=args.no_dumps,
        community_archive=args.community_archive,
        twitterapi_key=args.twitterapi_key,
        oembed=not args.no_oembed,
        fetch=args.fetch,
        bearer_token=args.bearer_token,
    )
    if not result.wanted:
        hx.ok("nothing to retry -- the cache has no missing or empty tweets")
        return 0
    for warning in unique_preserve_order(result.warnings)[:10]:
        hx.warn(warning)
    remaining_warnings = len(unique_preserve_order(result.warnings)) - 10
    if remaining_warnings > 0:
        hx.say(f"… and {remaining_warnings} more fetch failure(s)")
    hx.ok(
        f"recovered {len(result.recovered)} of {len(result.wanted)} missing tweet(s); "
        f"{len(result.still_missing)} still missing"
    )
    if result.still_missing:
        hx.say(
            "more sources may help: --community-archive, --twitterapi-key, or "
            "--fetch with an X API token"
        )
    return 0


def dumps_command(args: argparse.Namespace) -> int:
    action = args.dumps_action
    if action == "import":
        return dumps_import(args)
    if action == "list":
        return dumps_list(args)
    if action == "remove":
        path = remove_dump(args.name)
        hx.ok(f"removed {path}")
        return 0

    client = LocalDumpsClient(names=args.dump or None)
    if not client.available():
        raise RuntimeError("No imported dumps yet; run `ariadne dumps import <path>` first")
    if action == "users":
        return dumps_users(args, client)
    if action == "search":
        return dumps_search(args, client)
    if action == "user":
        return dumps_user(args, client)
    if action == "show":
        return dumps_show(args, client)
    if action == "interactive":
        return dumps_interactive(args, client)
    raise RuntimeError(f"Unknown dumps action: {action}")


def dumps_import(args: argparse.Namespace) -> int:
    info = import_dump(args.path, name=args.name, kind=args.kind, fts=not args.no_fts, progress=hx.say)
    span = f"{(info.first_tweet or '')[:10]} → {(info.last_tweet or '')[:10]}" if info.first_tweet else "empty"
    hx.ok(
        f"imported {info.name} ({info.kind}): {info.tweets:,} tweets, "
        f"{info.accounts:,} accounts, {span}"
    )
    hx.say(f"stored at {info.path}")
    for note in info.notes:
        hx.warn(note)
    return 0


def dumps_list(args: argparse.Namespace) -> int:
    infos = list_dumps()
    if not infos:
        hx.warn("no imported dumps; run `ariadne dumps import <path>`")
        return 0
    name_width = max(len(info.name) for info in infos)
    kind_width = max(len(info.kind) for info in infos)
    for info in infos:
        span = f"{(info.first_tweet or '')[:10]} → {(info.last_tweet or '')[:10]}" if info.first_tweet else "empty"
        fts = "fts" if info.fts else "   "
        print(
            f"{info.name:<{name_width}}  {info.kind:<{kind_width}}  "
            f"{info.tweets:>10,} tweets  {info.accounts:>7,} accounts  {span}  {fts}  {info.source}"
        )
    return 0


def dumps_users(args: argparse.Namespace, client: LocalDumpsClient) -> int:
    users = client.users(match=args.find)
    for user in users[: args.top]:
        name = (user["name"] or "")[:30]
        span = f"{(user['first'] or '')[:10]} → {(user['last'] or '')[:10]}"
        print(f"{user['username']:<22} {name:<32} {user['tweets']:>8,} tweets  {span}")
    remaining = len(users) - args.top
    if remaining > 0:
        hx.say(f"… and {remaining:,} more user(s); raise --top or filter with --find")
    unresolved = client.unresolved_count()
    if unresolved:
        hx.say(f"{unresolved:,} tweets across dumps have unresolved authors (no username)")
    return 0


def dumps_search(args: argparse.Namespace, client: LocalDumpsClient) -> int:
    tweets = client.search(args.query, username=args.user, since=args.since, limit=args.limit)
    if not tweets:
        hx.warn("no matches")
        return 0
    for tweet in tweets:
        print(_dump_tweet_line(tweet, max_text=200))
    return 0


def dumps_user(args: argparse.Namespace, client: LocalDumpsClient) -> int:
    result = client.get_user_posts(
        args.username, since=args.since, limit=args.limit, include_retweets=args.retweets
    )
    if not result.tweets:
        hx.warn(f"no tweets for @{args.username.strip('@')} in the selected dumps")
        return 0
    for tweet in result.tweets:
        print(_dump_tweet_line(tweet))
    return 0


def dumps_show(args: argparse.Namespace, client: LocalDumpsClient) -> int:
    reply_limit = positive_int(str(getattr(args, "reply_limit", 20)))
    raw = args.tweet_id.strip()
    tweet_id = raw if raw.isdigit() else tweet_id_from_url(raw)
    if not tweet_id:
        raise RuntimeError(f"Not a tweet ID or URL: {raw}")
    found = {tweet.id: tweet for tweet in client.get_posts([tweet_id]).tweets}
    target = found.get(tweet_id)
    if target is None:
        raise RuntimeError(f"Tweet {tweet_id} is not in the selected dumps")

    ancestors: list[Tweet] = []
    current = target
    seen_ids = {target.id}
    for _ in range(50):
        parent_id = current.reply_parent_id()
        if not parent_id:
            break
        if parent_id in seen_ids:
            hx.warn(f"ancestor cycle detected at tweet {parent_id}; stopping context traversal")
            break
        seen_ids.add(parent_id)
        parents = client.get_posts([parent_id]).tweets
        if not parents:
            ancestors.append(Tweet(id=parent_id, text=f"[not in dumps: {parent_id}]", available=False))
            break
        current = parents[0]
        ancestors.append(current)
    for ancestor in reversed(ancestors):
        print(_dump_tweet_line(ancestor))
    print(_dump_tweet_line(target, mark="▶"))
    if target.url:
        hx.say(target.url)
    for quote_id in target.quote_ids():
        quoted = client.get_posts([quote_id]).tweets
        quote = quoted[0] if quoted else Tweet(id=quote_id, text=f"[not in dumps: {quote_id}]", available=False)
        print(_dump_tweet_line(quote, mark="  ↳ quotes"))
    replies = client.children(tweet_id, limit=reply_limit + 1)
    if replies:
        truncated = len(replies) > reply_limit
        visible_replies = replies[:reply_limit]
        if truncated:
            print(f"replies in dumps (showing first {len(visible_replies)}):")
        else:
            print(f"replies in dumps ({len(visible_replies)}):")
        for reply in visible_replies:
            print(_dump_tweet_line(reply, mark="  ·"))
        if truncated:
            hx.say(f"… more direct replies omitted; raise --reply-limit above {reply_limit} to show more")
    return 0


def dumps_interactive(args: argparse.Namespace, client: LocalDumpsClient) -> int:
    """Menu-driven explorer for the persistent local dump library."""
    hx.banner("explore imported tweet dumps")
    current = client
    actions = (
        "search tweets",
        "browse users",
        "show a user's tweets",
        "show a tweet/thread",
        "change dump scope",
        "list imported dumps",
        "quit",
    )
    try:
        while True:
            hx.step(f"scope: {_interactive_scope(current)}")
            action = actions[hx.choose("What would you like to explore?", list(actions), default=0)]
            if action == "quit":
                return 0
            try:
                if action == "change dump scope":
                    current = _choose_dump_scope(current)
                    continue
                if action == "list imported dumps":
                    dumps_list(args)
                    continue
                if action == "search tweets":
                    query = prompt("Search query", required=True)
                    user = prompt("Restrict to user handle")
                    since = prompt("On or after date [YYYY-MM-DD]")
                    limit = positive_int(prompt("Maximum results", default="20"))
                    dumps_search(
                        argparse.Namespace(query=query, user=user or None, since=since or None, limit=limit),
                        current,
                    )
                    continue
                if action == "browse users":
                    match = prompt("Filter handle or display name")
                    top = positive_int(prompt("Maximum users", default="40"))
                    dumps_users(argparse.Namespace(find=match or None, top=top), current)
                    continue
                if action == "show a user's tweets":
                    username = prompt("User handle", required=True)
                    since = prompt("On or after date [YYYY-MM-DD]")
                    limit = positive_int(prompt("Maximum tweets", default="20"))
                    retweets = prompt_yes_no("Include retweets?", default=False)
                    dumps_user(
                        argparse.Namespace(
                            username=username,
                            since=since or None,
                            limit=limit,
                            retweets=retweets,
                        ),
                        current,
                    )
                    continue
                tweet_id = prompt("Tweet ID or URL", required=True)
                reply_limit = positive_int(
                    prompt("Maximum direct replies", default=str(args.reply_limit))
                )
                dumps_show(
                    argparse.Namespace(tweet_id=tweet_id, reply_limit=reply_limit),
                    current,
                )
            except (
                OSError,
                sqlite3.Error,
                RuntimeError,
                ValueError,
                argparse.ArgumentError,
                argparse.ArgumentTypeError,
            ) as exc:
                hx.warn(str(exc))
    except EOFError:
        hx.say("input closed; leaving dump explorer")
        return 0
    except KeyboardInterrupt:
        hx.say("interrupted; leaving dump explorer")
        return 130
    finally:
        current.close()


def _interactive_scope(client: LocalDumpsClient) -> str:
    names = client.dump_names()
    if len(names) == 1:
        return names[0]
    all_names = [info.name for info in list_dumps()]
    label = "all" if set(names) == set(all_names) else "selected"
    return f"{label} ({', '.join(names)})"


def _choose_dump_scope(current: LocalDumpsClient) -> LocalDumpsClient:
    names = [info.name for info in list_dumps()]
    options = [f"all dumps ({', '.join(names)})", *names, "cancel"]
    choice = hx.choose("Choose dump scope", options, default=0)
    if choice == len(options) - 1:
        return current
    selected = None if choice == 0 else [names[choice - 1]]
    replacement = LocalDumpsClient(names=selected)
    current.close()
    return replacement


def _dump_tweet_line(tweet: Tweet, *, mark: str = "·", max_text: int | None = None) -> str:
    date = (tweet.created_at or "")[:10] or "????-??-??"
    handle = f"@{tweet.username}" if tweet.username else f"author:{tweet.author_id or '?'}"
    text = " ".join((tweet.text or "").split()) or "[no text]"
    if max_text is not None and len(text) > max_text:
        text = text[: max_text - 1] + "…"
    dump = tweet.source.removeprefix("dump:") if tweet.source else "?"
    return f"{mark} [{date}] {handle} ({dump}) {tweet.id}\n  {text}"


def _lit(text: str) -> str:
    """Escape `text` so rich prints it literally; a no-op in plain mode."""
    return text.replace("[", "\\[") if hx.console() is not None else text


def _read_value(label: str, default: str | None = None) -> str:
    """Read one line behind the hyperplex `»` marker, with a dim [default] hint."""
    console = hx.console()
    if console is None:
        plain_suffix = f" [{default}]" if default else ""
        return input(f"> {label}{plain_suffix} ").strip()
    suffix = f" [dim]\\[{_lit(default)}][/]" if default else ""
    return console.input(f"[bold {hx.ACCENT}]»[/] {_lit(label)}{suffix} ").strip()


def prompt(label: str, *, default: str | None = None, required: bool = False) -> str:
    while True:
        value = _read_value(label, default)
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""
        hx.warn("please enter a value")


def prompt_path(label: str, *, default: str | None = None) -> str:
    value = prompt(label, default=default)
    return str(Path(value).expanduser()) if value else ""


OUTPUT_SUFFIXES = {
    "messages": "json",
    "openai": "json",
    "json": "json",
    "markdown": "md",
    "raft": "jsonl",
}


def default_output_name(
    output_format: str, username: str = "", since: str = ""
) -> str:
    """A filename that says what the file holds, so runs do not overwrite.

    Interactive output is usually large enough that printing it to the
    terminal loses it; naming a file by subject and range keeps successive
    runs distinguishable without asking the user to invent a name.
    """
    parts = ["ariadne"]
    handle = (username or "").strip().strip("@")
    if handle:
        parts.append(re.sub(r"[^A-Za-z0-9_-]+", "-", handle))
    window = (since or "").strip()[:10]
    if window:
        parts.append(f"since-{re.sub(r'[^0-9-]+', '', window)}")
    return f"{'-'.join(parts)}.{OUTPUT_SUFFIXES.get(output_format, 'txt')}"


def prompt_existing_path(label: str) -> str:
    while True:
        value = prompt(label)
        if _is_skip_answer(value):
            return ""
        path = Path(value).expanduser()
        if path.exists():
            return str(path)
        hx.warn(f"path not found: {path}")
        hx.say("press Enter to skip this source, or enter an existing file/folder path")


def prompt_choice(label: str, choices: tuple[str, ...], *, default: str) -> str:
    """Pick one of `choices`. hyperplex renders them numbered; the value is still the choice string."""
    options = list(choices)
    default_index = options.index(default) if default in options else None
    return options[hx.choose(label, options, default=default_index)]


def prompt_yes_no(label: str, *, default: bool) -> bool:
    return hx.confirm(label, default)


def prompt_secret(label: str) -> str:
    console = hx.console()
    if console is None:
        print(f"> {label}", file=sys.stderr)
    else:
        console.print(f"[bold {hx.ACCENT}]»[/] {_lit(label)}")
    if sys.stdin.isatty():
        return getpass.getpass("> ").strip()
    return input("> ").strip()


def parse_csv_values(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected a positive integer, got {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"Expected a positive integer, got {value!r}")
    return parsed


def _is_generic_tweet_file(path: str) -> bool:
    if not path:
        return False
    return Path(path).suffix.lower() in {".csv", ".json", ".jsonl", ".ndjson"}


def _is_skip_answer(value: str) -> bool:
    return value.strip().lower() in {"", "-", "skip", "none", "no", "\\"}
