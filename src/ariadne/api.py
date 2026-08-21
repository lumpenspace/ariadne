"""Programmatic API for ariadne.

The CLI is a thin wrapper around this module. Everything the ``build``
command does is available as a function call:

    import ariadne

    result = ariadne.build(archive="~/twitter-archive.zip", for_user="alice")
    for document in result.raft_documents():
        ...

``build()`` takes the same knobs as the CLI flags (see :class:`BuildOptions`)
and returns a :class:`BuildResult` holding the reconstructed conversations,
the tweet store, and any warnings. Rendering is available both as text
(``result.render("raft")``) and as parsed data (``result.raft_documents()``),
so callers never have to round-trip through a file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator, TypedDict, Unpack, overload

from ._types import OutputFormat, PathInput
from .archive import load_archive
from .dumps import LocalDumpsClient
from .errors import ConfigurationError, NoTargetsError
from .fetch import FetchResult, OEmbedClient, XApiClient
from .ids import extract_tweet_ids, extract_tweet_url_map, unique_preserve_order
from .models import Conversation, Tweet
from .providers import CommunityArchiveClient, TwitterApiIoClient
from .reconstruct import ConversationBuilder, TweetFetcher
from .render import (
    json_payload,
    message_conversations,
    raft_documents,
    render,
)
from .sources import load_tweets_file, select_user_tweet_ids
from .store import TweetStore, load_cache, save_cache
from .timeutil import is_on_or_after, parse_since
from .unofficial import NitterRssClient

__all__ = [
    "BuildKwargs",
    "BuildOptions",
    "BuildResult",
    "OutputFormat",
    "PathInput",
    "build",
    "build_conversations",
    "DEFAULT_RSS_BASES",
]

DEFAULT_RSS_BASES = ("https://nitter.net", "https://rss.xcancel.com")

DEFAULT_CACHE_PATH = ".ariadne-cache.json"
DEFAULT_MAX_LOCAL_POSTS = 10_000

StringValues = str | Iterable[str] | None
PathValues = PathInput | Iterable[PathInput] | None
_OUTPUT_FORMATS = ("messages", "openai", "json", "markdown", "raft")

_STRING_LIST_FIELDS = ("items", "rss_url_template", "dump")
_PATH_LIST_FIELDS = ("input_file", "archive", "tweets_file")


class BuildKwargs(TypedDict, total=False):
    """Typed keyword arguments accepted by :func:`build`.

    Multi-value inputs accept either one value or any iterable of values. Path
    inputs additionally accept :class:`os.PathLike` objects such as
    :class:`pathlib.Path`.
    """

    items: StringValues
    input_file: PathValues
    archive: PathValues
    tweets_file: PathValues
    for_user: str | None
    target_user: str | None
    author_id: str | None
    all_loaded: bool
    replies_only: bool
    since: str | None
    cheap_first: bool
    oembed: bool
    no_oembed: bool
    hydrate_all: bool
    fetch: bool
    fetch_user_timeline: bool
    unofficial_rss: bool
    no_unofficial_rss: bool
    rss_base: StringValues
    rss_url_template: StringValues
    max_user_pages: int | None
    bearer_token: str | None
    community_archive: bool
    twitterapi_key: str | None
    dump: StringValues
    no_dumps: bool
    dump_limit: int | None
    cache: PathInput
    no_cache: bool
    strict: bool
    max_depth: int
    no_quotes: bool
    quote_as_reply: bool
    allow_empty: bool
    format: OutputFormat
    output: PathInput | None


def _normalize_values(
    value: object,
    *,
    name: str,
    path_values: bool = False,
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, os.PathLike)):
        values = [value]
    else:
        if not isinstance(value, Iterable):
            expected = (
                "a path or an iterable of paths"
                if path_values
                else "a string or an iterable of strings"
            )
            raise TypeError(f"{name} must be {expected}")
        try:
            values = list(value)
        except TypeError as exc:
            expected = (
                "a path or an iterable of paths"
                if path_values
                else "a string or an iterable of strings"
            )
            raise TypeError(f"{name} must be {expected}") from exc

    normalized: list[str] = []
    for item in values:
        if path_values and isinstance(item, os.PathLike):
            item = os.fspath(item)
        if not isinstance(item, str):
            expected = "path-like values" if path_values else "strings"
            raise TypeError(f"{name} must contain only {expected}")
        normalized.append(item)
    return normalized


@dataclass
class BuildOptions:
    """Everything the ``build`` pipeline needs.

    Field names match the CLI flags (``--for-user`` is ``for_user``), so a
    parsed ``argparse.Namespace`` can be used interchangeably. List-valued
    fields accept a bare string for convenience: ``archive="a.zip"`` is the
    same as ``archive=["a.zip"]``.
    """

    if TYPE_CHECKING:
        # Dataclasses generate the runtime constructor. This declaration gives
        # type checkers the wider, normalized input contract while keeping the
        # stored attributes precisely typed as lists below.
        def __init__(self, **kwargs: Unpack[BuildKwargs]) -> None: ...

    # Inputs
    items: list[str] = field(default_factory=list)
    input_file: list[str] = field(default_factory=list)
    archive: list[str] = field(default_factory=list)
    tweets_file: list[str] = field(default_factory=list)

    # Target selection
    for_user: str | None = None
    target_user: str | None = None
    author_id: str | None = None
    all_loaded: bool = False
    replies_only: bool = False
    since: str | None = None

    # Sources
    cheap_first: bool = False
    oembed: bool = False
    no_oembed: bool = False
    hydrate_all: bool = False
    fetch: bool = False
    fetch_user_timeline: bool = False
    unofficial_rss: bool = False
    no_unofficial_rss: bool = False
    rss_base: list[str] | None = None
    rss_url_template: list[str] = field(default_factory=list)
    max_user_pages: int | None = None
    bearer_token: str | None = field(default=None, repr=False)
    # Community Archive (community-archive.org): a public pool of donated Twitter
    # exports. Enumerates the target and completes reply/quote parents by anyone.
    community_archive: bool = False
    # twitterapi.io pay-as-you-go gateway; needs the caller's key.
    twitterapi_key: str | None = field(default=None, repr=False)
    # Imported local dumps (`ariadne dumps import`) are read automatically when
    # present; `dump` restricts to the named ones, `no_dumps` disables them.
    dump: list[str] = field(default_factory=list)
    no_dumps: bool = False
    # Safety bound for one user/author timeline read from local dumps. When
    # omitted, timelines above DEFAULT_MAX_LOCAL_POSTS require an explicit cap.
    dump_limit: int | None = None

    # Cache
    cache: str = DEFAULT_CACHE_PATH
    no_cache: bool = False

    # Reconstruction
    strict: bool = False
    max_depth: int = 50
    no_quotes: bool = False
    # Splice a root quote-tweet's quoted tweet in as its reply-parent.
    quote_as_reply: bool = True
    allow_empty: bool = False

    # Output (used by the CLI; `BuildResult` can render any format on demand)
    format: OutputFormat = "messages"
    output: str | None = None

    def __post_init__(self) -> None:
        for name in _STRING_LIST_FIELDS:
            setattr(self, name, _normalize_values(getattr(self, name), name=name))
        for name in _PATH_LIST_FIELDS:
            setattr(
                self,
                name,
                _normalize_values(getattr(self, name), name=name, path_values=True),
            )
        if self.rss_base is not None:
            self.rss_base = _normalize_values(self.rss_base, name="rss_base")
        if isinstance(self.cache, os.PathLike):
            self.cache = os.fspath(self.cache)
        if isinstance(self.output, os.PathLike):
            self.output = os.fspath(self.output)

    def validate(self) -> None:
        """Validate combinations that argparse normally checks for the CLI."""
        for name in ("max_depth", "max_user_pages", "dump_limit"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise ConfigurationError(f"{name} must be a positive integer")
        if self.format not in _OUTPUT_FORMATS:
            choices = ", ".join(_OUTPUT_FORMATS)
            raise ConfigurationError(f"format must be one of: {choices}")
        if self.oembed and self.no_oembed:
            raise ConfigurationError("oembed and no_oembed cannot both be enabled")
        if self.unofficial_rss and self.no_unofficial_rss:
            raise ConfigurationError(
                "unofficial_rss and no_unofficial_rss cannot both be enabled"
            )
        if self.dump and self.no_dumps:
            raise ConfigurationError("dump and no_dumps cannot be used together")
        if self.for_user and self.target_user:
            selected = self.for_user.strip().strip("@").lower()
            target = self.target_user.strip().strip("@").lower()
            if selected != target:
                raise ConfigurationError(
                    "for_user and target_user cannot select different users"
                )

    @classmethod
    def coerce(cls, options: "BuildOptions | Any") -> "BuildOptions":
        """Accept a BuildOptions, an argparse.Namespace, a mapping, or any
        object exposing the option names as attributes."""
        if isinstance(options, cls):
            return options
        names = [f.name for f in fields(cls)]
        if isinstance(options, dict):
            present = {name: options[name] for name in names if name in options}
        else:
            missing = object()
            present = {}
            for name in names:
                value = getattr(options, name, missing)
                if value is not missing:
                    present[name] = value
        return cls(**present)


@dataclass
class BuildResult:
    """The outcome of a build: conversations plus the tweets behind them."""

    store: TweetStore
    conversations: list[Conversation]
    warnings: list[str]
    target_ids: list[str]
    should_save_cache: bool
    options: BuildOptions | None = None

    @property
    def tweets(self) -> list[Tweet]:
        return self.store.all()

    def __len__(self) -> int:
        return len(self.conversations)

    def __bool__(self) -> bool:
        return bool(self.conversations)

    def __iter__(self) -> Iterator[Conversation]:
        return iter(self.conversations)

    def render(self, output_format: OutputFormat | None = None) -> str:
        """Render to text in any supported format."""
        return render(
            self.conversations,
            self.store,
            output_format=output_format or self._default_format(),
        )

    def raft_documents(self) -> list[dict[str, Any]]:
        """The ``raft.documents.v1`` rows as dicts, ready for raft."""
        return raft_documents(self.conversations, self.store)

    def messages(self, *, strict_openai: bool = False) -> list[dict[str, Any]]:
        """Rendered chat conversations as dicts."""
        return message_conversations(
            self.conversations, self.store, strict_openai=strict_openai
        )

    def json_payload(self) -> dict[str, Any]:
        """The full ``ariadne.json.v1`` payload as a dict."""
        return json_payload(self.conversations, self.store)

    def save(
        self,
        path: PathInput,
        output_format: OutputFormat | None = None,
    ) -> Path:
        """Render to `path`, inferring the format from the CLI default."""
        destination = Path(path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.render(output_format), encoding="utf-8")
        return destination

    def save_cache(
        self,
        path: PathInput | None = None,
        *,
        force: bool = False,
    ) -> Path | None:
        """Persist fetched/hydrated tweets, if this run fetched anything."""
        if not (force or self.should_save_cache):
            return None
        target = path or (self.options.cache if self.options else DEFAULT_CACHE_PATH)
        target_path = Path(target).expanduser()
        save_cache(target_path, self.store.cacheable_tweets())
        return target_path

    def _default_format(self) -> OutputFormat:
        return self.options.format if self.options else "messages"


@overload
def build(options: BuildOptions, /) -> BuildResult: ...


@overload
def build(**kwargs: Unpack[BuildKwargs]) -> BuildResult: ...


def build(options: BuildOptions | None = None, /, **kwargs: Any) -> BuildResult:
    """Build conversations from options or typed keyword arguments.

        result = ariadne.build(archive="archive.zip", for_user="alice",
                               since="2024-01-01")

        options = BuildOptions(archive=["archive.zip"], for_user="alice")
        result = ariadne.build(options)

    See :class:`BuildOptions` for the full set of knobs.
    """
    if options is not None:
        if kwargs:
            raise ConfigurationError(
                "build() accepts either one BuildOptions object or keyword options, not both"
            )
        if not isinstance(options, BuildOptions):
            raise ConfigurationError(
                "the positional build() argument must be a BuildOptions object"
            )
        return build_conversations(options)
    return build_conversations(BuildOptions(**kwargs))


def build_conversations(
    options: BuildOptions,
    *,
    base_store: TweetStore | None = None,
    base_warnings: list[str] | None = None,
) -> BuildResult:
    """Run the full source → select → reconstruct → hydrate pipeline.

    ``base_store`` is reused and mutated in place, allowing a second build to
    continue from an earlier result without loading its tweets again.
    """
    options = BuildOptions.coerce(options)
    options.validate()
    if base_warnings is not None and base_store is None:
        raise ConfigurationError("base_warnings requires base_store")
    local_dumps = make_dumps_client(options)
    if local_dumps is None:
        return _run_build(
            options,
            base_store=base_store,
            base_warnings=base_warnings,
            local_dumps=None,
        )
    with local_dumps:
        return _run_build(
            options,
            base_store=base_store,
            base_warnings=base_warnings,
            local_dumps=local_dumps,
        )


def _run_build(
    options: BuildOptions,
    *,
    base_store: TweetStore | None,
    base_warnings: list[str] | None,
    local_dumps: LocalDumpsClient | None,
) -> BuildResult:
    """Pipeline implementation with externally managed source lifetimes."""

    try:
        since = parse_since(options.since)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    input_values = collect_input_values(options.items, options.input_file)
    target_ids = extract_tweet_ids(input_values)
    url_by_id = extract_tweet_url_map(input_values)
    target_user = target_username(options)
    cheap_first = bool(options.cheap_first or options.target_user)
    use_oembed = bool((options.oembed or options.target_user) and not options.no_oembed)
    use_unofficial = bool(
        (options.unofficial_rss or options.target_user) and not options.no_unofficial_rss
    )

    if base_store is None:
        store, warnings = load_store(options)
    else:
        store = base_store
        warnings = list(base_warnings or [])
    x_client = make_x_client(options)
    community = CommunityArchiveClient() if options.community_archive else None
    tapio_key = options.twitterapi_key or os.environ.get("TWITTERAPI_IO_KEY")
    tapio = TwitterApiIoClient(tapio_key) if tapio_key else None

    if options.target_user:
        warnings.append(
            "Target-user mode is cheap-first: local/cache and unofficial RSS are tried before any X API calls."
        )

    if local_dumps is not None:
        # Enrich already-loaded targets too: a cache/oEmbed hit can have text
        # while still lacking the reply/quote metadata held by a local dump.
        dump_lookup_ids = list(target_ids)
        if options.all_loaded:
            dump_lookup_ids.extend(tweet.id for tweet in store.all())
        if dump_lookup_ids:
            store.add_many(local_dumps.get_posts(unique_preserve_order(dump_lookup_ids)).tweets)

    if local_dumps is not None and target_user:
        query_limit = _local_dump_query_limit(options)
        dump_timeline = local_dumps.get_user_posts(target_user, since=since, limit=query_limit)
        dump_tweets, limit_warning = _bounded_local_dump_tweets(
            dump_timeline.tweets, options, f"@{target_user}"
        )
        store.add_many(dump_tweets)
        if dump_tweets:
            warnings.append(
                f"Local dumps ({', '.join(local_dumps.dump_names())}) supplied "
                f"{len(dump_tweets)} tweet(s) for @{target_user}."
            )
        if limit_warning:
            warnings.append(limit_warning)

    if local_dumps is not None and options.author_id:
        query_limit = _local_dump_query_limit(options)
        dump_author_timeline = local_dumps.get_author_posts(
            options.author_id, since=since, limit=query_limit
        )
        dump_author_tweets, limit_warning = _bounded_local_dump_tweets(
            dump_author_timeline.tweets, options, f"author {options.author_id}"
        )
        store.add_many(dump_author_tweets)
        if dump_author_tweets:
            warnings.append(
                f"Local dumps ({', '.join(local_dumps.dump_names())}) supplied "
                f"{len(dump_author_tweets)} tweet(s) for author {options.author_id}."
            )
        if limit_warning:
            warnings.append(limit_warning)

    if community is not None and target_user:
        ca_timeline = community.get_user_posts(target_user, since=since)
        store.add_many(ca_timeline.tweets, cacheable=True)
        if ca_timeline.tweets:
            warnings.append(
                f"Community Archive supplied {len(ca_timeline.tweets)} tweet(s) for @{target_user}."
            )
        else:
            warnings.append(
                f"Community Archive has no tweets for @{target_user} (only donor accounts are covered)."
            )
        warnings.extend(
            f"Community Archive: {message}" for message in ca_timeline.errors.values()
        )

    if tapio is not None and target_user:
        tapio_timeline = tapio.get_user_posts(target_user, since=since, max_pages=options.max_user_pages)
        store.add_many(tapio_timeline.tweets, cacheable=True)
        warnings.extend(
            f"twitterapi.io: {message}" for message in tapio_timeline.errors.values()
        )

    if use_unofficial:
        if not target_user:
            raise ConfigurationError("unofficial_rss requires for_user or target_user")
        fetch_unofficial_timelines(store, warnings, options, target_user, since)

    if target_user or options.author_id or options.all_loaded:
        target_ids = select_targets(
            store,
            target_ids,
            target_user,
            options.author_id,
            since,
            all_loaded=options.all_loaded,
            replies_only=options.replies_only,
        )

    if options.replies_only and not target_ids and x_client is not None and options.fetch:
        candidate_ids = candidate_reply_lookup_ids(store, target_user, options.author_id, since)
        if candidate_ids:
            warnings.append(
                f"Using X API lookup after cheap sources to identify which of {len(candidate_ids)} candidate tweet(s) are replies."
            )
            lookup = x_client.get_posts(candidate_ids)
            store.add_many(lookup.tweets, cacheable=True)
            warnings.extend(
                f"Could not fetch tweet {tweet_id}: {message}"
                for tweet_id, message in lookup.errors.items()
            )
            target_ids = select_targets(
                store,
                target_ids,
                target_user,
                options.author_id,
                since,
                all_loaded=options.all_loaded,
                replies_only=True,
            )

    if options.fetch_user_timeline:
        if not target_user:
            raise ConfigurationError("fetch_user_timeline requires for_user or target_user")
        if x_client is None:
            raise ConfigurationError(
                "fetch_user_timeline requires fetch=True or a bearer_token"
            )
        if cheap_first or use_unofficial:
            warnings.append(
                "Using X API user timeline only after cheap/local timeline sources have finished."
            )
        user = x_client.get_user_by_username(target_user)
        timeline = x_client.get_user_posts(
            str(user["id"]),
            start_time=since,
            max_pages=options.max_user_pages,
        )
        store.add_many(timeline.tweets, cacheable=True)
        warnings.extend(
            f"Could not fetch tweet {tweet_id}: {message}"
            for tweet_id, message in timeline.errors.items()
        )

    if target_user or options.author_id or options.all_loaded:
        target_ids = select_targets(
            store,
            target_ids,
            target_user,
            options.author_id,
            since,
            all_loaded=options.all_loaded,
            replies_only=options.replies_only,
        )

    if not target_ids:
        message = "No tweet IDs matched the requested input/user/date range"
        if options.replies_only:
            message += "; reply-only mode requires reply-parent metadata, which cheap RSS/oEmbed sources often omit"
        if target_user and not options.fetch_user_timeline:
            message += "; use fetch_user_timeline with a bearer token to try X API after cheap sources"
        if options.allow_empty:
            warnings.append(message)
            return BuildResult(
                store=store,
                conversations=[],
                warnings=warnings,
                target_ids=[],
                should_save_cache=should_save_cache(
                    options, x_client, oembed=None, use_unofficial=use_unofficial
                ),
                options=options,
            )
        raise NoTargetsError(message)

    oembed = OEmbedClient(url_by_id=url_by_id, tweet_lookup=store.get) if use_oembed else None

    if x_client is not None and options.fetch and cheap_first:
        warnings.extend(enrich_after_cheap_sources(store, x_client, target_ids))

    branch_fetcher = make_branch_fetcher(
        dumps=local_dumps,
        community=community,
        tapio=tapio,
        oembed=oembed,
        x_client=x_client,
        cheap_first=cheap_first,
    )

    builder = ConversationBuilder(
        store,
        fetcher=branch_fetcher,
        include_quotes=not options.no_quotes,
        quote_as_reply=options.quote_as_reply,
        strict=options.strict,
        max_depth=options.max_depth,
    )
    conversations = builder.build(target_ids)

    if oembed is not None:
        ids_to_hydrate = (
            {tweet.id for tweet in store.all()}
            if options.hydrate_all
            else conversation_ids(conversations)
        )
        warnings.extend(hydrate_empty_tweets(store, oembed, ids_to_hydrate))

    warnings.extend(builder.warnings)
    return BuildResult(
        store=store,
        conversations=conversations,
        warnings=warnings,
        target_ids=target_ids,
        should_save_cache=should_save_cache(
            options, x_client, oembed=oembed, use_unofficial=use_unofficial
        ),
        options=options,
    )


def collect_input_values(items: list[str], input_files: list[str]) -> list[str]:
    values = list(items)
    for input_file in input_files:
        values.append(Path(input_file).expanduser().read_text(encoding="utf-8"))
    return values


def load_store(options: BuildOptions) -> tuple[TweetStore, list[str]]:
    store = TweetStore()
    warnings: list[str] = []
    if not options.no_cache:
        store.add_many(load_cache(options.cache), cacheable=True)

    for archive_path in options.archive:
        archive_result = load_archive(archive_path)
        store.add_many(archive_result.tweets)
        warnings.extend(archive_result.warnings)

    for tweets_path in options.tweets_file:
        source_result = load_tweets_file(tweets_path)
        store.add_many(source_result.tweets)
        warnings.extend(source_result.warnings)
    return store, warnings


def make_x_client(options: BuildOptions) -> XApiClient | None:
    token = (
        options.bearer_token
        or os.environ.get("X_BEARER_TOKEN")
        or os.environ.get("TWITTER_BEARER_TOKEN")
    )
    if not token:
        if options.fetch:
            raise ConfigurationError(
                "fetch=True requires bearer_token, X_BEARER_TOKEN, or TWITTER_BEARER_TOKEN"
            )
        return None
    if options.fetch or options.fetch_user_timeline:
        return XApiClient(token)
    return None


class CheapFirstFetcher:
    """Try a free fetcher first, fall back to the paid one for what's missing."""

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


class ChainFetcher:
    """Try fetchers in order, only asking the next for ids still unresolved.

    An id counts as resolved once a fetcher returns it with text and marks it
    available; partial/tombstone hits are kept but let later fetchers try to do
    better. This is what lets Community Archive complete a reply/quote parent
    that the target's own timeline never included.
    """

    def __init__(self, fetchers) -> None:
        self.fetchers = [f for f in fetchers if f is not None]

    def get_posts(self, ids: list[str]) -> FetchResult:
        merged: dict[str, Tweet] = {}
        errors: dict[str, str] = {}
        remaining = unique_preserve_order(ids)
        for fetcher in self.fetchers:
            if not remaining:
                break
            result = fetcher.get_posts(remaining)
            for tweet in result.tweets:
                current = merged.get(tweet.id)
                if current is None or (tweet.available and tweet.text and not (current.available and current.text)):
                    merged[tweet.id] = tweet
            errors.update(result.errors)
            remaining = [
                tweet_id
                for tweet_id in remaining
                if tweet_id not in merged or not (merged[tweet_id].available and merged[tweet_id].text)
            ]
        for tweet_id in merged:
            errors.pop(tweet_id, None)
        return FetchResult(tweets=list(merged.values()), errors=errors)


def make_dumps_client(options: BuildOptions) -> LocalDumpsClient | None:
    """The local-dump client for this build, or None when disabled/empty."""
    if options.no_dumps:
        return None
    client = LocalDumpsClient(names=options.dump or None)
    return client if client.available() else None


def _local_dump_query_limit(options: BuildOptions) -> int:
    if options.dump_limit is not None and (
        isinstance(options.dump_limit, bool)
        or not isinstance(options.dump_limit, int)
        or options.dump_limit < 1
    ):
        raise ConfigurationError("dump_limit must be a positive integer")
    return (options.dump_limit or DEFAULT_MAX_LOCAL_POSTS) + 1


def _bounded_local_dump_tweets(
    tweets: list[Tweet], options: BuildOptions, subject: str
) -> tuple[list[Tweet], str | None]:
    cap = options.dump_limit or DEFAULT_MAX_LOCAL_POSTS
    if len(tweets) <= cap:
        return tweets, None
    if options.dump_limit is None:
        raise ConfigurationError(
            f"Local dumps contain more than {cap:,} posts for {subject}; "
            "narrow the timeline with since/--since or explicitly choose a maximum "
            "with dump_limit/--dump-limit"
        )
    return (
        tweets[:cap],
        f"Local dump timeline for {subject} was limited to the newest {cap:,} post(s).",
    )


def make_branch_fetcher(
    *,
    dumps: TweetFetcher | None = None,
    community: TweetFetcher | None = None,
    tapio: TweetFetcher | None = None,
    oembed: OEmbedClient | None,
    x_client: XApiClient | None,
    cheap_first: bool,
) -> TweetFetcher | None:
    paid_first: TweetFetcher | None
    if x_client is not None and oembed is not None and cheap_first:
        paid_first = CheapFirstFetcher(oembed, x_client)
    else:
        paid_first = x_client if x_client is not None else oembed
    chain = [f for f in (dumps, community, tapio, paid_first) if f is not None]
    if not chain:
        return None
    if len(chain) == 1:
        return chain[0]
    return ChainFetcher(chain)


def target_username(options: BuildOptions) -> str | None:
    target = (options.target_user or options.for_user or "").strip().strip("@")
    return target or None


def select_targets(
    store: TweetStore,
    existing_ids: list[str],
    username: str | None,
    author_id: str | None,
    since,
    *,
    all_loaded: bool = False,
    replies_only: bool = False,
) -> list[str]:
    if all_loaded:
        selected_ids = [
            tweet.id
            for tweet in store.all()
            if tweet.available
            and is_selected_since(tweet, since)
            and (not replies_only or is_reply_start(tweet))
        ]
    else:
        candidates = [
            tweet for tweet in store.all() if not replies_only or is_reply_start(tweet)
        ]
        selected_ids = select_user_tweet_ids(
            candidates,
            username=username,
            author_id=author_id,
            since=since,
        )
    return unique_preserve_order(existing_ids + selected_ids)


def fetch_unofficial_timelines(
    store: TweetStore,
    warnings: list[str],
    options: BuildOptions,
    username: str,
    since,
) -> None:
    for base_url in rss_bases(options):
        if options.replies_only:
            base = base_url.rstrip("/")
            rss = NitterRssClient(
                url_template=f"{base}/{{username}}/with_replies/rss",
                source_label=f"{base}/with_replies/rss",
            )
        else:
            rss = NitterRssClient(base_url=base_url)
        rss_result = rss.get_user_posts(username, since=since)
        store.add_many(rss_result.tweets, cacheable=True)
        warnings.extend(rss_result.warnings)
    for template in options.rss_url_template or []:
        rss = NitterRssClient(url_template=template)
        rss_result = rss.get_user_posts(username, since=since)
        store.add_many(rss_result.tweets, cacheable=True)
        warnings.extend(rss_result.warnings)


def rss_bases(options: BuildOptions) -> list[str]:
    values = options.rss_base
    if values is None:
        return list(DEFAULT_RSS_BASES if options.target_user else ("https://nitter.net",))
    if isinstance(values, str):
        values = [values]
    cleaned = [part.strip() for value in values for part in value.split(",") if part.strip()]
    return cleaned or list(DEFAULT_RSS_BASES)


def enrich_after_cheap_sources(
    store: TweetStore, x_client: XApiClient, target_ids: list[str]
) -> list[str]:
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
    warnings.extend(
        f"Could not fetch tweet {tweet_id}: {message}"
        for tweet_id, message in result.errors.items()
    )
    return warnings


def needs_official_metadata(tweet: Tweet) -> bool:
    source = tweet.source or ""
    if not tweet.available:
        return True
    if tweet.referenced_tweets or tweet.in_reply_to_id:
        return False
    cheap_metadata_sources = ("oembed", "unofficial-rss:", "reply-reference")
    return any(part in source for part in cheap_metadata_sources)


def is_reply_start(tweet: Tweet) -> bool:
    if tweet.reply_parent_id():
        return True
    source = tweet.source or ""
    return "reply-feed" in source


def candidate_reply_lookup_ids(
    store: TweetStore,
    username: str | None,
    author_id: str | None,
    since,
) -> list[str]:
    return unique_preserve_order(
        [
            tweet.id
            for tweet in store.all()
            if tweet.available
            and is_selected_since(tweet, since)
            and (not username or (tweet.username or "").strip("@").lower() == username.lower())
            and (not author_id or tweet.author_id == author_id)
            and not tweet.reply_parent_id()
        ]
    )


def should_save_cache(
    options: BuildOptions,
    x_client: XApiClient | None,
    *,
    oembed: OEmbedClient | None,
    use_unofficial: bool,
) -> bool:
    return not options.no_cache and (
        x_client is not None
        or oembed is not None
        or use_unofficial
        or options.community_archive
        or bool(options.twitterapi_key or os.environ.get("TWITTERAPI_IO_KEY"))
    )


def is_selected_since(tweet: Tweet, since) -> bool:
    return is_on_or_after(tweet.created_at, since)


def conversation_ids(conversations: Iterable[Conversation]) -> set[str]:
    ids: set[str] = set()
    for conversation in conversations:
        ids.update(conversation.all_ids)
    return ids


def hydrate_empty_tweets(store: TweetStore, oembed: OEmbedClient, ids: set[str]) -> list[str]:
    empty_ids = [
        tweet_id
        for tweet_id in sorted(ids)
        if (tweet := store.get(tweet_id)) is not None and tweet.available and not tweet.text
    ]
    if not empty_ids:
        return []
    result = oembed.get_posts(empty_ids)
    store.add_many(result.tweets, cacheable=True)
    return [
        f"Could not hydrate tweet {tweet_id} via oEmbed: {message}"
        for tweet_id, message in result.errors.items()
    ]
