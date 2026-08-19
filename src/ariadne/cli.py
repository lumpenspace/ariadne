from __future__ import annotations

import argparse
import getpass
import os
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
from .fetch import XApiError
from .models import Tweet
from .render import render
from .store import save_cache
from .ids import unique_preserve_order


COMMANDS = {"build", "inspect-archive", "interactive"}

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
        return build(args)
    except (FileNotFoundError, RuntimeError, XApiError, ValueError, argparse.ArgumentTypeError) as exc:
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
    return parser


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
            "Default with --target-user: https://nitter.net and https://xcancel.com."
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


def build(args: argparse.Namespace) -> int:
    result = collect_conversations(args)
    emit_warnings(result.warnings)

    if result.should_save_cache:
        save_cache(args.cache, result.store.cacheable_tweets())

    output = render(result.conversations, result.store, output_format=args.format)
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
    output = prompt_path("Output file")
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
    if cheap_result.should_save_cache:
        save_cache(args.cache, cheap_result.store.cacheable_tweets())

    continue_default = should_continue_with_x_api(cheap_result)
    use_x_api = prompt_yes_no(
        "Continue with X API now? This may cost API reads",
        default=continue_default,
    )

    final_result = cheap_result
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
            paid_args = replace(
                cheap_args,
                fetch=True,
                fetch_user_timeline=fetch_timeline,
                bearer_token=token,
                max_user_pages=max_user_pages,
                unofficial_rss=False,
                no_unofficial_rss=True,
                allow_empty=False,
            )
            final_result = build_conversations(paid_args, base_store=cheap_result.store)
            print_interactive_summary("After X API pass", final_result)
        else:
            hx.warn("skipping X API because no bearer token was provided")

    if not final_result.conversations:
        emit_warnings(final_result.warnings)
        hx.warn("no conversations were reconstructed")
        return 0

    emit_warnings(final_result.warnings)
    if final_result.should_save_cache:
        save_cache(args.cache, final_result.store.cacheable_tweets())

    rendered = render(final_result.conversations, final_result.store, output_format=output_format)
    if output:
        Path(output).expanduser().write_text(rendered, encoding="utf-8")
        hx.ok(f"wrote {_lit(output)}")
    else:
        sys.stdout.write(rendered)
    return 0


def emit_warnings(warnings: list[str]) -> None:
    for warning in unique_preserve_order(warnings):
        hx.warn(_lit(warning))


def print_interactive_summary(label: str, result: BuildResult) -> None:
    hx.step(_lit(label))
    hx.say(f"tweets in store: {len(result.store.all())}")
    hx.say(f"selected target tweets: {len(result.target_ids)}")
    hx.say(f"reconstructed conversations: {len(result.conversations)}")
    hx.say(f"unavailable tweets in reconstructed output: {len(unavailable_conversation_ids(result))}")
    hx.say(f"warnings: {len(unique_preserve_order(result.warnings))}")
    counts = source_counts(result.store.all())
    if counts:
        hx.say("sources:")
        for source, count in counts.most_common():
            hx.say(f"  {_lit(source)}: {count}")
    if result.warnings:
        hx.say("recent warnings:")
        for warning in unique_preserve_order(result.warnings)[-5:]:
            hx.say(f"  - {_lit(warning)}")


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


def prompt_x_api_token(*, local_sources: bool, has_username: bool) -> str:
    env_token = os.environ.get("X_BEARER_TOKEN") or os.environ.get("TWITTER_BEARER_TOKEN")
    if env_token:
        if prompt_yes_no("Use X_BEARER_TOKEN/TWITTER_BEARER_TOKEN from the environment?", default=True):
            return env_token

    prompt_text = "X API bearer token for missing parents"
    if has_username:
        prompt_text += "/user timeline"
    if local_sources:
        prompt_text += " (optional, may cost API reads)"
    else:
        prompt_text += " (needed if cheap sources found nothing)"
    return prompt_secret(prompt_text)


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


def prompt_path(label: str) -> str:
    value = prompt(label)
    return str(Path(value).expanduser()) if value else ""


def prompt_existing_path(label: str) -> str:
    while True:
        value = prompt(label)
        if _is_skip_answer(value):
            return ""
        path = Path(value).expanduser()
        if path.exists():
            return str(path)
        hx.warn(f"path not found: {_lit(str(path))}")
        hx.say("press Enter to skip this source, or enter an existing file/folder path")


def prompt_choice(label: str, choices: tuple[str, ...], *, default: str) -> str:
    """Pick one of `choices`. hyperplex renders them numbered; the value is still the choice string."""
    options = list(choices)
    default_index = options.index(default) if default in options else None
    return options[hx.choose(_lit(label), options, default=default_index)]


def prompt_yes_no(label: str, *, default: bool) -> bool:
    return hx.confirm(_lit(label), default)


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
