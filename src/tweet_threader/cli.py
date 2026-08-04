from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from .archive import load_archive
from .fetch import OEmbedClient, XApiClient, XApiError
from .ids import extract_tweet_ids, extract_tweet_url_map, unique_preserve_order
from .reconstruct import ConversationBuilder
from .render import render
from .sources import load_tweets_file, select_user_tweet_ids
from .store import TweetStore, load_cache, save_cache
from .timeutil import parse_since
from .unofficial import NitterRssClient


COMMANDS = {"build", "inspect-archive", "interactive"}


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
        print(f"tweet-threader: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tweet-threader",
        description="Reconstruct X/Twitter reply branches and render them as LLM input.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_cmd = subparsers.add_parser("build", help="Build conversation branches.")
    add_build_arguments(build_cmd)

    interactive_cmd = subparsers.add_parser("interactive", help="Prompt for sources and build settings.")
    interactive_cmd.add_argument(
        "--cache",
        default=".tweet-threader-cache.json",
        help="JSON cache for fetched/hydrated tweets. Default: .tweet-threader-cache.json",
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
    build_cmd.add_argument("--author-id", help="Select every loaded/fetched tweet by this author ID.")
    build_cmd.add_argument("--since", help="Only select user tweets on or after this date, e.g. 2024-01-01.")
    build_cmd.add_argument(
        "--oembed",
        action="store_true",
        help="Use public X oEmbed to hydrate tweets that have URLs/IDs but no text.",
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
        "--rss-base",
        default="https://nitter.net",
        help="Base URL for --unofficial-rss. Default: https://nitter.net",
    )
    build_cmd.add_argument(
        "--max-user-pages",
        type=positive_int,
        help="Limit X API user timeline pages. Each page is up to 100 tweets.",
    )
    build_cmd.add_argument("--bearer-token", help="X API bearer token for --fetch or --fetch-user-timeline.")
    build_cmd.add_argument(
        "--cache",
        default=".tweet-threader-cache.json",
        help="JSON cache for fetched/hydrated tweets. Default: .tweet-threader-cache.json",
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
    since = parse_since(args.since)
    input_values = collect_input_values(args.items, args.input_file)
    target_ids = extract_tweet_ids(input_values)
    url_by_id = extract_tweet_url_map(input_values)

    store, warnings = load_store(args)
    x_client = make_x_client(args)

    if args.fetch_user_timeline:
        if not args.for_user:
            raise RuntimeError("--fetch-user-timeline requires --for-user")
        if x_client is None:
            raise RuntimeError("--fetch-user-timeline requires --fetch or --bearer-token")
        user = x_client.get_user_by_username(args.for_user)
        timeline = x_client.get_user_posts(
            str(user["id"]),
            start_time=since,
            max_pages=args.max_user_pages,
        )
        store.add_many(timeline.tweets, cacheable=True)
        warnings.extend(f"Could not fetch tweet {tweet_id}: {message}" for tweet_id, message in timeline.errors.items())

    if args.unofficial_rss:
        if not args.for_user:
            raise RuntimeError("--unofficial-rss requires --for-user")
        rss = NitterRssClient(base_url=args.rss_base)
        rss_result = rss.get_user_posts(args.for_user, since=since)
        store.add_many(rss_result.tweets, cacheable=True)
        warnings.extend(rss_result.warnings)

    if args.for_user or args.author_id:
        target_ids = unique_preserve_order(
            target_ids
            + select_user_tweet_ids(
                store.all(),
                username=args.for_user,
                author_id=args.author_id,
                since=since,
            )
        )

    if not target_ids:
        raise RuntimeError("No tweet IDs matched the requested input/user/date range")

    oembed = OEmbedClient(url_by_id=url_by_id, tweet_lookup=store.get) if args.oembed else None
    branch_fetcher = x_client if x_client is not None else oembed

    builder = ConversationBuilder(
        store,
        fetcher=branch_fetcher,
        include_quotes=not args.no_quotes,
        strict=args.strict,
        max_depth=args.max_depth,
    )
    conversations = builder.build(target_ids)

    if oembed is not None:
        ids_to_hydrate = {tweet.id for tweet in store.all()} if args.hydrate_all else conversation_ids(conversations)
        warnings.extend(hydrate_empty_tweets(store, oembed, ids_to_hydrate))

    warnings.extend(builder.warnings)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if not args.no_cache and (x_client is not None or oembed is not None or args.unofficial_rss):
        save_cache(args.cache, store.cacheable_tweets())

    output = render(conversations, store, output_format=args.format)
    if args.output:
        Path(args.output).expanduser().write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0


def interactive(args: argparse.Namespace) -> int:
    print("tweet-threader interactive")
    print("Leave optional answers blank to skip them.")
    print("")

    archive = prompt_existing_path("Archive/dump path first (zip/folder/tweets.js/json/csv/jsonl)")
    tweets_file = ""
    if archive:
        extra = prompt_existing_path("Additional generic tweet dump")
        tweets_file = extra or ""
    else:
        tweets_file = prompt_existing_path("Generic tweet dump path")

    username = prompt("User handle to collect", required=True).strip().strip("@")
    since = prompt("As far back as date [YYYY-MM-DD]")
    output_format = prompt_choice("Output format", ("messages", "openai", "json", "markdown", "raft"), default="messages")
    output = prompt_path("Output file")
    max_depth_text = prompt("Max reply depth", default="50")
    max_depth = positive_int(max_depth_text)

    use_oembed = prompt_yes_no("Use public oEmbed to fill missing tweet text?", default=True)
    hydrate_all = False
    if use_oembed:
        hydrate_all = prompt_yes_no("Hydrate every empty tweet found, not just rendered conversations?", default=False)

    local_sources = bool(archive or tweets_file)
    env_token = os.environ.get("X_BEARER_TOKEN") or os.environ.get("TWITTER_BEARER_TOKEN")
    use_env_token = False
    if env_token:
        use_env_token = prompt_yes_no("Use X_BEARER_TOKEN/TWITTER_BEARER_TOKEN from the environment?", default=not local_sources)
    token = env_token if use_env_token else ""
    if not token:
        prompt_text = "X API bearer token for missing parents/user timeline"
        if local_sources:
            prompt_text += " (optional, may cost API reads)"
        else:
            prompt_text += " (needed if there is no dump)"
        token = prompt_secret(prompt_text)

    fetch_timeline = False
    max_user_pages = None
    use_unofficial_rss = False
    rss_base = "https://nitter.net"
    if token:
        fetch_timeline = prompt_yes_no(
            "Fetch the user's own timeline from X API before building?",
            default=not local_sources,
        )
        if fetch_timeline:
            pages = prompt("Max X timeline pages, 100 tweets/page")
            max_user_pages = positive_int(pages) if pages else None
    elif not local_sources:
        print("")
        print("No dump or X API token was provided.")
        print("You can try unofficial Nitter/XCancel-style RSS, but it is fragile and usually lacks reply-parent metadata.")
        use_unofficial_rss = prompt_yes_no("Try unofficial free RSS fallback?", default=True)
        if not use_unofficial_rss:
            raise RuntimeError("No dump was provided, so an X API bearer token or unofficial RSS fallback is required to fetch user tweets")
        rss_base = prompt("RSS base URL", default="https://nitter.net")

    build_args = argparse.Namespace(
        command="build",
        items=[],
        input_file=[],
        archive=[archive] if archive and not _is_generic_tweet_file(archive) else [],
        tweets_file=[path for path in (tweets_file, archive if _is_generic_tweet_file(archive) else "") if path],
        for_user=username,
        author_id=None,
        since=since or None,
        oembed=use_oembed,
        hydrate_all=hydrate_all,
        fetch=bool(token),
        fetch_user_timeline=fetch_timeline,
        unofficial_rss=use_unofficial_rss,
        rss_base=rss_base,
        max_user_pages=max_user_pages,
        bearer_token=token or None,
        cache=args.cache,
        no_cache=False,
        format=output_format,
        output=output or None,
        strict=False,
        max_depth=max_depth,
        no_quotes=False,
    )
    return build(build_args)


def collect_input_values(items: list[str], input_files: list[str]) -> list[str]:
    values = list(items)
    for input_file in input_files:
        values.append(Path(input_file).expanduser().read_text(encoding="utf-8"))
    return values


def load_store(args: argparse.Namespace) -> tuple[TweetStore, list[str]]:
    store = TweetStore()
    warnings: list[str] = []
    if not args.no_cache:
        store.add_many(load_cache(args.cache), cacheable=True)

    for archive_path in args.archive:
        result = load_archive(archive_path)
        store.add_many(result.tweets)
        warnings.extend(result.warnings)

    for tweets_path in args.tweets_file:
        result = load_tweets_file(tweets_path)
        store.add_many(result.tweets)
        warnings.extend(result.warnings)
    return store, warnings


def make_x_client(args: argparse.Namespace) -> XApiClient | None:
    token = args.bearer_token or os.environ.get("X_BEARER_TOKEN") or os.environ.get("TWITTER_BEARER_TOKEN")
    if not token:
        if args.fetch:
            raise RuntimeError("--fetch requires --bearer-token, X_BEARER_TOKEN, or TWITTER_BEARER_TOKEN")
        return None
    if args.fetch or args.fetch_user_timeline:
        return XApiClient(token)
    return None


def conversation_ids(conversations) -> set[str]:
    ids: set[str] = set()
    for conversation in conversations:
        ids.update(conversation.all_ids)
    return ids


def hydrate_empty_tweets(store: TweetStore, oembed: OEmbedClient, ids: set[str]) -> list[str]:
    empty_ids = []
    for tweet_id in sorted(ids):
        tweet = store.get(tweet_id)
        if tweet and tweet.available and not tweet.text:
            empty_ids.append(tweet_id)
    if not empty_ids:
        return []
    result = oembed.get_posts(empty_ids)
    store.add_many(result.tweets, cacheable=True)
    return [f"Could not hydrate tweet {tweet_id} via oEmbed: {message}" for tweet_id, message in result.errors.items()]


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


def prompt(label: str, *, default: str | None = None, required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""
        print("Please enter a value.")


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
        print(f"Path not found: {path}")
        print("Press Enter to skip this source, or enter an existing file/folder path.")


def prompt_choice(label: str, choices: tuple[str, ...], *, default: str) -> str:
    choice_text = "/".join(choices)
    while True:
        value = prompt(f"{label} ({choice_text})", default=default)
        if value in choices:
            return value
        print(f"Choose one of: {choice_text}")


def prompt_yes_no(label: str, *, default: bool) -> bool:
    default_text = "Y/n" if default else "y/N"
    while True:
        value = prompt(f"{label} [{default_text}]").lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer y or n.")


def prompt_secret(label: str) -> str:
    if sys.stdin.isatty():
        return getpass.getpass(label + ": ").strip()
    return input(label + ": ").strip()


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
