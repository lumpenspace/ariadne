from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .archive import load_archive
from .fetch import FetchResult, OEmbedClient, XApiClient, XApiError
from .ids import extract_tweet_ids, extract_tweet_url_map, unique_preserve_order
from .models import Conversation, Tweet
from .reconstruct import ConversationBuilder
from .render import render
from .sources import load_tweets_file, select_user_tweet_ids
from .store import TweetStore, load_cache, save_cache
from .timeutil import is_on_or_after, parse_since
from .unofficial import NitterRssClient


COMMANDS = {"build", "inspect-archive", "interactive"}
DEFAULT_RSS_BASES = ("https://nitter.net", "https://xcancel.com")


@dataclass
class BuildResult:
    store: TweetStore
    conversations: list[Conversation]
    warnings: list[str]
    target_ids: list[str]
    should_save_cache: bool


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


def collect_conversations(
    args: argparse.Namespace,
    *,
    base_store: TweetStore | None = None,
    base_warnings: list[str] | None = None,
) -> BuildResult:
    since = parse_since(args.since)
    input_values = collect_input_values(args.items, args.input_file)
    target_ids = extract_tweet_ids(input_values)
    url_by_id = extract_tweet_url_map(input_values)
    target_user = target_username(args)
    cheap_first = bool(args.cheap_first or args.target_user)
    use_oembed = bool((args.oembed or args.target_user) and not args.no_oembed)
    use_unofficial = bool((args.unofficial_rss or args.target_user) and not args.no_unofficial_rss)

    if base_store is None:
        store, warnings = load_store(args)
    else:
        store = base_store
        warnings = list(base_warnings or [])
    x_client = make_x_client(args)

    if args.target_user:
        warnings.append(
            "Target-user mode is cheap-first: local/cache and unofficial RSS are tried before any X API calls."
        )

    if use_unofficial:
        if not target_user:
            raise RuntimeError("--unofficial-rss requires --for-user or --target-user")
        fetch_unofficial_timelines(store, warnings, args, target_user, since)

    if target_user or args.author_id or args.all_loaded:
        target_ids = select_targets(
            store,
            target_ids,
            target_user,
            args.author_id,
            since,
            all_loaded=args.all_loaded,
        )

    if args.fetch_user_timeline:
        if not target_user:
            raise RuntimeError("--fetch-user-timeline requires --for-user or --target-user")
        if x_client is None:
            raise RuntimeError("--fetch-user-timeline requires --fetch or --bearer-token")
        if cheap_first or use_unofficial:
            warnings.append("Using X API user timeline only after cheap/local timeline sources have finished.")
        user = x_client.get_user_by_username(target_user)
        timeline = x_client.get_user_posts(
            str(user["id"]),
            start_time=since,
            max_pages=args.max_user_pages,
        )
        store.add_many(timeline.tweets, cacheable=True)
        warnings.extend(f"Could not fetch tweet {tweet_id}: {message}" for tweet_id, message in timeline.errors.items())

    if target_user or args.author_id or args.all_loaded:
        target_ids = select_targets(
            store,
            target_ids,
            target_user,
            args.author_id,
            since,
            all_loaded=args.all_loaded,
        )

    if not target_ids:
        message = "No tweet IDs matched the requested input/user/date range"
        if target_user and not args.fetch_user_timeline:
            message += "; use --fetch-user-timeline with a bearer token to try X API after cheap sources"
        if getattr(args, "allow_empty", False):
            warnings.append(message)
            return BuildResult(
                store=store,
                conversations=[],
                warnings=warnings,
                target_ids=[],
                should_save_cache=should_save_cache(args, x_client, oembed=None, use_unofficial=use_unofficial),
            )
        raise RuntimeError(message)

    oembed = OEmbedClient(url_by_id=url_by_id, tweet_lookup=store.get) if use_oembed else None

    if x_client is not None and args.fetch and cheap_first:
        warnings.extend(enrich_after_cheap_sources(store, x_client, target_ids))

    branch_fetcher = make_branch_fetcher(oembed=oembed, x_client=x_client, cheap_first=cheap_first)

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
    return BuildResult(
        store=store,
        conversations=conversations,
        warnings=warnings,
        target_ids=target_ids,
        should_save_cache=should_save_cache(args, x_client, oembed=oembed, use_unofficial=use_unofficial),
    )


def interactive(args: argparse.Namespace) -> int:
    print("ariadne interactive")
    print("Cheap sources are tried first. X API is offered only after a summary.")
    print("")

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

    cheap_args = argparse.Namespace(
        command="build",
        items=[],
        input_file=[],
        archive=[archive] if archive and not _is_generic_tweet_file(archive) else [],
        tweets_file=[path for path in (tweets_file, archive if _is_generic_tweet_file(archive) else "") if path],
        for_user=None,
        target_user=username or None,
        author_id=None,
        all_loaded=all_loaded,
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

    cheap_result = collect_conversations(cheap_args)
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
            paid_args = argparse.Namespace(**vars(cheap_args))
            paid_args.fetch = True
            paid_args.fetch_user_timeline = fetch_timeline
            paid_args.bearer_token = token
            paid_args.max_user_pages = max_user_pages
            paid_args.unofficial_rss = False
            paid_args.no_unofficial_rss = True
            paid_args.allow_empty = False
            final_result = collect_conversations(paid_args, base_store=cheap_result.store)
            print_interactive_summary("After X API pass", final_result)
        else:
            print("Skipping X API because no bearer token was provided.")

    if not final_result.conversations:
        emit_warnings(final_result.warnings)
        print("No conversations were reconstructed.")
        return 0

    emit_warnings(final_result.warnings)
    if final_result.should_save_cache:
        save_cache(args.cache, final_result.store.cacheable_tweets())

    rendered = render(final_result.conversations, final_result.store, output_format=output_format)
    if output:
        Path(output).expanduser().write_text(rendered, encoding="utf-8")
        print(f"Wrote {output}")
    else:
        sys.stdout.write(rendered)
    return 0


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


class CheapFirstFetcher:
    def __init__(self, cheap_fetcher, paid_fetcher) -> None:
        self.cheap_fetcher = cheap_fetcher
        self.paid_fetcher = paid_fetcher

    def get_posts(self, ids: list[str]) -> FetchResult:
        result = FetchResult()
        cheap = self.cheap_fetcher.get_posts(ids)
        result.tweets.extend(cheap.tweets)
        result.errors.update(cheap.errors)

        cheap_by_id = {tweet.id: tweet for tweet in cheap.tweets if tweet.available}
        missing_ids = [
            tweet_id
            for tweet_id in unique_preserve_order(ids)
            if tweet_id not in cheap_by_id or needs_official_metadata(cheap_by_id[tweet_id])
        ]
        if not missing_ids:
            return result

        paid = self.paid_fetcher.get_posts(missing_ids)
        result.tweets.extend(paid.tweets)
        result.errors.update(paid.errors)
        for tweet in paid.tweets:
            result.errors.pop(tweet.id, None)
        return result


def make_branch_fetcher(*, oembed: OEmbedClient | None, x_client: XApiClient | None, cheap_first: bool):
    if oembed is not None and x_client is not None and cheap_first:
        return CheapFirstFetcher(oembed, x_client)
    return x_client if x_client is not None else oembed


def target_username(args: argparse.Namespace) -> str | None:
    target = (args.target_user or args.for_user or "").strip().strip("@")
    return target or None


def select_targets(
    store: TweetStore,
    existing_ids: list[str],
    username: str | None,
    author_id: str | None,
    since,
    *,
    all_loaded: bool = False,
) -> list[str]:
    if all_loaded:
        selected_ids = [
            tweet.id
            for tweet in store.all()
            if tweet.available and is_selected_since(tweet, since)
        ]
    else:
        selected_ids = select_user_tweet_ids(
            store.all(),
            username=username,
            author_id=author_id,
            since=since,
        )
    return unique_preserve_order(
        existing_ids + selected_ids
    )


def fetch_unofficial_timelines(
    store: TweetStore,
    warnings: list[str],
    args: argparse.Namespace,
    username: str,
    since,
) -> None:
    for base_url in rss_bases(args):
        rss = NitterRssClient(base_url=base_url)
        rss_result = rss.get_user_posts(username, since=since)
        store.add_many(rss_result.tweets, cacheable=True)
        warnings.extend(rss_result.warnings)
    for template in args.rss_url_template or []:
        rss = NitterRssClient(url_template=template)
        rss_result = rss.get_user_posts(username, since=since)
        store.add_many(rss_result.tweets, cacheable=True)
        warnings.extend(rss_result.warnings)


def rss_bases(args: argparse.Namespace) -> list[str]:
    values = args.rss_base
    if values is None:
        return list(DEFAULT_RSS_BASES if args.target_user else ("https://nitter.net",))
    if isinstance(values, str):
        values = [values]
    return parse_csv_values(",".join(values)) or list(DEFAULT_RSS_BASES)


def enrich_after_cheap_sources(store: TweetStore, x_client: XApiClient, target_ids: list[str]) -> list[str]:
    ids_to_enrich = [
        tweet_id
        for tweet_id in unique_preserve_order(target_ids)
        if (tweet := store.get(tweet_id)) is not None and needs_official_metadata(tweet)
    ]
    if not ids_to_enrich:
        return []
    result = x_client.get_posts(ids_to_enrich)
    store.add_many(result.tweets, cacheable=True)
    warnings = [
        f"Using X API lookup after cheap sources to enrich {len(ids_to_enrich)} selected tweet(s) with reply/quote metadata."
    ]
    warnings.extend(f"Could not fetch tweet {tweet_id}: {message}" for tweet_id, message in result.errors.items())
    return warnings


def needs_official_metadata(tweet: Tweet) -> bool:
    source = tweet.source or ""
    if not tweet.available:
        return True
    if tweet.referenced_tweets or tweet.in_reply_to_id:
        return False
    cheap_metadata_sources = ("oembed", "unofficial-rss:", "reply-reference")
    return any(part in source for part in cheap_metadata_sources)


def should_save_cache(
    args: argparse.Namespace,
    x_client: XApiClient | None,
    *,
    oembed: OEmbedClient | None,
    use_unofficial: bool,
) -> bool:
    return not args.no_cache and (x_client is not None or oembed is not None or use_unofficial)


def emit_warnings(warnings: list[str]) -> None:
    for warning in unique_preserve_order(warnings):
        print(f"warning: {warning}", file=sys.stderr)


def print_interactive_summary(label: str, result: BuildResult) -> None:
    print("")
    print(label)
    print("-" * len(label))
    print(f"Tweets in store: {len(result.store.all())}")
    print(f"Selected target tweets: {len(result.target_ids)}")
    print(f"Reconstructed conversations: {len(result.conversations)}")
    print(f"Unavailable tweets in reconstructed output: {len(unavailable_conversation_ids(result))}")
    print(f"Warnings: {len(unique_preserve_order(result.warnings))}")
    counts = source_counts(result.store.all())
    if counts:
        print("Sources:")
        for source, count in counts.most_common():
            print(f"  {source}: {count}")
    if result.warnings:
        print("Recent warnings:")
        for warning in unique_preserve_order(result.warnings)[-5:]:
            print(f"  - {warning}")
    print("")


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


def is_selected_since(tweet: Tweet, since) -> bool:
    return is_on_or_after(tweet.created_at, since)


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
