"""Local dump library: bulk tweet dumps imported into SQLite for offline use.

``ariadne dumps import <path>`` converts a source directory (or file) holding
a bulk tweet dump into one normalized SQLite database kept under the settings
directory (``~/.ariadne/dumps/<name>.db``; override the root with
``ARIADNE_HOME``). After the import the source is no longer read: every query
runs against the SQLite copy, so the source directory can move or disappear.

Supported dump kinds (auto-detected, or forced with ``kind=``):

- ``community-csv`` — the Community Archive per-table CSV dump: a folder or
  zip containing ``tweets.csv``, ``account.csv``, and friends.
- ``parquet`` — bulk parquet exports. Both the Community Archive
  ``enriched_tweets.parquet`` schema and the Borg/Hive "tpot dump" layout
  (``tweets.parquet`` keyed by numeric author ids, with usernames resolved
  from ``rankings/*.parquet`` when present). Reading parquet needs the
  optional duckdb dependency: ``pip install 'ariadne-x[parquet]'``.
- ``twitter-archive`` — a personal X/Twitter archive folder, zip, or data file.
- ``tweets-file`` — any generic CSV/JSON/JSONL tweet dump ariadne can read.

:class:`LocalDumpsClient` satisfies the ``TweetFetcher`` protocol
(``get_posts``) and enumerates timelines (``get_user_posts``), so imported
dumps slot into the build pipeline ahead of every network source.
"""

from __future__ import annotations

import csv
import importlib
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Iterable, TextIO

from .fetch import FetchResult
from .ids import unique_preserve_order
from .models import Tweet, TweetRef
from .store import merge_tweets
from .timeutil import api_time, parse_datetime, parse_since

Progress = Callable[[str], None]

SCHEMA_VERSION = "2"

_TWEET_COLUMNS = (
    "id",
    "text",
    "author_id",
    "username",
    "name",
    "created_at",
    "conversation_id",
    "in_reply_to_id",
    "in_reply_to_user_id",
    "in_reply_to_username",
    "quoted_id",
    "retweet_of_id",
    "retweet_count",
    "favorite_count",
)
_COLS = ", ".join(_TWEET_COLUMNS)
_INSERT_TWEET = f"INSERT OR IGNORE INTO tweets ({_COLS}) VALUES ({', '.join('?' * len(_TWEET_COLUMNS))})"

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE accounts (account_id TEXT PRIMARY KEY, username TEXT, name TEXT);
CREATE TABLE tweets (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL DEFAULT '',
    author_id TEXT,
    username TEXT,
    name TEXT,
    created_at TEXT,
    conversation_id TEXT,
    in_reply_to_id TEXT,
    in_reply_to_user_id TEXT,
    in_reply_to_username TEXT,
    quoted_id TEXT,
    retweet_of_id TEXT,
    retweet_count INTEGER,
    favorite_count INTEGER
);
CREATE TABLE user_summary (
    username TEXT PRIMARY KEY COLLATE NOCASE,
    name TEXT,
    tweets INTEGER NOT NULL,
    first_tweet TEXT,
    last_tweet TEXT
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tweets_username ON tweets (username COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_tweets_author ON tweets (author_id);
CREATE INDEX IF NOT EXISTS idx_tweets_reply ON tweets (in_reply_to_id);
CREATE INDEX IF NOT EXISTS idx_tweets_created ON tweets (created_at);
"""

_FTS = "CREATE VIRTUAL TABLE tweets_fts USING fts5(text, content='tweets', content_rowid='rowid')"

_BATCH = 50_000


def ariadne_home() -> Path:
    """The settings directory: ``$ARIADNE_HOME`` or ``~/.ariadne``."""
    return Path(os.environ.get("ARIADNE_HOME") or "~/.ariadne").expanduser()


def dumps_dir() -> Path:
    return ariadne_home() / "dumps"


@dataclass
class DumpInfo:
    name: str
    path: Path
    kind: str
    source: str
    imported_at: str
    tweets: int
    accounts: int
    first_tweet: str | None
    last_tweet: str | None
    fts: bool
    notes: list[str]


def detect_kind(path: str | Path) -> str:
    source = Path(path).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"Dump path does not exist: {source}")
    if source.is_dir():
        if (source / "tweets.csv").is_file() and (source / "account.csv").is_file():
            return "community-csv"
        if list(source.glob("*.parquet")):
            return "parquet"
        if (source / "data").is_dir() or list(source.glob("*.js")):
            return "twitter-archive"
        raise ValueError(
            f"Could not detect the dump kind of {source}; pass kind= "
            "(community-csv, parquet, twitter-archive, or tweets-file)"
        )
    suffix = source.suffix.lower()
    if suffix == ".parquet":
        return "parquet"
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            if _community_zip_members(archive) is not None:
                return "community-csv"
            if _is_twitter_archive_zip(archive):
                return "twitter-archive"
        raise ValueError(f"Could not detect the dump kind of {source}; pass kind=")
    if suffix == ".js":
        return "twitter-archive"
    if suffix in {".csv", ".json", ".jsonl", ".ndjson"}:
        return "tweets-file"
    raise ValueError(f"Could not detect the dump kind of {source}; pass kind=")


def import_dump(
    path: str | Path,
    *,
    name: str | None = None,
    kind: str | None = None,
    fts: bool = True,
    progress: Progress | None = None,
) -> DumpInfo:
    """Import one dump into ``dumps_dir()/<name>.db``, replacing any previous
    import under the same name. Returns the resulting :class:`DumpInfo`."""
    source = Path(path).expanduser().resolve()
    say: Progress = progress or (lambda message: None)
    kind = kind or detect_kind(source)
    if kind not in _IMPORTERS:
        raise ValueError(f"Unknown dump kind: {kind}")
    name = _slug(name or source.stem)
    if not name:
        raise ValueError("Dump name is empty after slugification; pass name=")

    home = ariadne_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = home / "dumps"
    directory.mkdir(exist_ok=True, mode=0o700)
    if os.name != "nt":
        home.chmod(0o700)
        directory.chmod(0o700)
    final_path = directory / f"{name}.db"
    # Each importer gets its own staging database. This keeps simultaneous
    # imports from unlinking or writing through one another; the final replace
    # remains atomic, with the last successful import intentionally winning.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{name}-", suffix=".db.tmp", dir=directory)
    os.close(fd)
    tmp_path = Path(tmp_name)
    if os.name != "nt":
        tmp_path.chmod(0o600)

    try:
        con = sqlite3.connect(tmp_path)
        try:
            con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;")
            con.executescript(_SCHEMA)
            say(f"importing {source} as {name!r} (kind: {kind})")
            try:
                notes = _IMPORTERS[kind](con, source, say)
            except (OSError, sqlite3.Error, RuntimeError, ValueError):
                raise
            except Exception as exc:
                # Optional backends (notably DuckDB) expose their own exception
                # hierarchies. Normalize them at the importer boundary so both
                # the CLI and Python callers receive a concise, contextual error.
                raise RuntimeError(f"Could not import {source} as {kind}: {exc}") from exc
            tweet_count = con.execute("SELECT count(*) FROM tweets").fetchone()[0]
            if not tweet_count:
                raise ValueError(f"No tweets could be imported from {source}")
            say("building indexes")
            con.executescript(_INDEXES)
            con.execute(
                "INSERT INTO user_summary (username, name, tweets, first_tweet, last_tweet) "
                "SELECT min(username), max(name), count(*), min(created_at), max(created_at) "
                "FROM tweets WHERE username IS NOT NULL GROUP BY username COLLATE NOCASE"
            )
            # Some source formats carry author metadata inline rather than in a
            # separate account table. Backfill those authors so DumpInfo and
            # `dumps list` report meaningful account coverage for every kind.
            con.execute(
                "INSERT OR IGNORE INTO accounts (account_id, username, name) "
                "SELECT coalesce(author_id, 'username:' || lower(username)), "
                "min(username), max(name) FROM tweets WHERE username IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM accounts "
                "WHERE accounts.username = tweets.username COLLATE NOCASE) "
                "GROUP BY coalesce(author_id, 'username:' || lower(username))"
            )
            fts_built = False
            if fts:
                try:
                    con.execute(_FTS)
                    con.execute("INSERT INTO tweets_fts(tweets_fts) VALUES('rebuild')")
                    fts_built = True
                    say("built the full-text index")
                except sqlite3.OperationalError:
                    notes.append("FTS5 is unavailable in this SQLite build; search falls back to substring scans")
            account_count = con.execute("SELECT count(*) FROM accounts").fetchone()[0]
            first, last = con.execute("SELECT min(created_at), max(created_at) FROM tweets").fetchone()
            resolved = con.execute("SELECT coalesce(sum(tweets), 0) FROM user_summary").fetchone()[0]
            unresolved = tweet_count - resolved
            if unresolved:
                notes.append(f"{unresolved:,} tweets have no resolved username")
            imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            meta = {
                "version": SCHEMA_VERSION,
                "name": name,
                "kind": kind,
                "source": str(source),
                "imported_at": imported_at,
                "tweets": str(tweet_count),
                "accounts": str(account_count),
                "first_tweet": first or "",
                "last_tweet": last or "",
                "unresolved": str(unresolved),
                "fts": "1" if fts_built else "0",
                "notes": json.dumps(notes),
            }
            con.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", meta.items())
            con.commit()
            con.execute("PRAGMA analysis_limit=1000")
            con.execute("ANALYZE")
            con.commit()
        finally:
            con.close()
        # mkstemp created the database as 0600; rename preserves that mode.
        tmp_path.replace(final_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return DumpInfo(
        name=name,
        path=final_path,
        kind=kind,
        source=str(source),
        imported_at=imported_at,
        tweets=tweet_count,
        accounts=account_count,
        first_tweet=first,
        last_tweet=last,
        fts=fts_built,
        notes=notes,
    )


def list_dumps() -> list[DumpInfo]:
    directory = dumps_dir()
    if not directory.is_dir():
        return []
    infos = []
    for path in sorted(directory.glob("*.db")):
        info = _read_info(path)
        if info is not None:
            infos.append(info)
    return infos


def remove_dump(name: str) -> Path:
    path = dumps_dir() / f"{_slug(name)}.db"
    if not path.is_file():
        raise FileNotFoundError(f"No imported dump named {name!r} in {dumps_dir()}")
    path.unlink()
    return path


def _read_info(path: Path) -> DumpInfo | None:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            meta = dict(con.execute("SELECT key, value FROM meta"))
        finally:
            con.close()
    except sqlite3.Error:
        return None
    try:
        notes = json.loads(meta.get("notes") or "[]")
    except ValueError:
        notes = []
    return DumpInfo(
        name=meta.get("name") or path.stem,
        path=path,
        kind=meta.get("kind") or "unknown",
        source=meta.get("source") or "",
        imported_at=meta.get("imported_at") or "",
        tweets=int(meta.get("tweets") or 0),
        accounts=int(meta.get("accounts") or 0),
        first_tweet=meta.get("first_tweet") or None,
        last_tweet=meta.get("last_tweet") or None,
        fts=meta.get("fts") == "1",
        notes=notes if isinstance(notes, list) else [],
    )


# --- importers ---------------------------------------------------------------


def _community_zip_members(archive: zipfile.ZipFile) -> dict[str, str] | None:
    """Return the CSV members for one Community Archive table directory.

    Exports normally wrap the tables in a dated top-level directory. Matching
    ``tweets.csv`` and ``account.csv`` by their common prefix both recognizes
    that layout and avoids confusing unrelated CSV files elsewhere in a zip.
    """
    names = {info.filename for info in archive.infolist() if not info.is_dir()}
    roots = sorted(
        (
            name[: -len("tweets.csv")]
            for name in names
            if name.endswith("tweets.csv")
            and name[: -len("tweets.csv")] + "account.csv" in names
        ),
        key=lambda root: (root.count("/"), root),
    )
    if not roots:
        return None
    root = roots[0]
    members: dict[str, str] = {}
    for name in names:
        if not name.startswith(root):
            continue
        relative = name[len(root) :]
        if "/" not in relative and relative.endswith(".csv"):
            members[relative] = name
    return members


def _is_twitter_archive_zip(archive: zipfile.ZipFile) -> bool:
    """Whether a zip contains a recognizable personal Twitter data table."""
    tweet_table = re.compile(r"tweets?(?:-part\d+)?\.(?:js|json)$", re.IGNORECASE)
    return any(
        tweet_table.fullmatch(Path(info.filename).name)
        for info in archive.infolist()
        if not info.is_dir()
    )


def _import_community_csv(con: sqlite3.Connection, source: Path, say: Progress) -> list[str]:
    if source.is_dir():
        members = {path.name: path for path in source.glob("*.csv")}
        if not {"tweets.csv", "account.csv"}.issubset(members):
            raise ValueError(f"No Community Archive CSV tables found in {source}")

        def open_csv(name: str) -> TextIO:
            return members[name].open(newline="", encoding="utf-8-sig")

        return _import_community_csv_rows(con, open_csv, set(members), say)

    if not zipfile.is_zipfile(source):
        raise ValueError(f"Community Archive source is not a directory or zip file: {source}")
    with zipfile.ZipFile(source) as archive:
        zip_members = _community_zip_members(archive)
        if zip_members is None:
            raise ValueError(f"No Community Archive CSV tables found in {source}")

        def open_csv(name: str) -> TextIO:
            return io.TextIOWrapper(
                archive.open(zip_members[name]),
                encoding="utf-8-sig",
                newline="",
            )

        return _import_community_csv_rows(con, open_csv, set(zip_members), say)


def _import_community_csv_rows(
    con: sqlite3.Connection,
    open_csv: Callable[[str], TextIO],
    csv_names: set[str],
    say: Progress,
) -> list[str]:
    notes: list[str] = []
    accounts: dict[str, tuple[str | None, str | None]] = {}
    with open_csv("account.csv") as fp:
        for row in csv.DictReader(fp):
            account_id = _norm_id(row.get("account_id"))
            if account_id:
                accounts[account_id] = (
                    _norm_str(row.get("username")),
                    _norm_str(row.get("account_display_name")),
                )
    secondary: dict[str, tuple[str | None, str | None]] = {}
    if "mentioned_users.csv" in csv_names:
        with open_csv("mentioned_users.csv") as fp:
            for row in csv.DictReader(fp):
                user_id = _norm_id(row.get("user_id"))
                if user_id and user_id not in accounts:
                    secondary[user_id] = (_norm_str(row.get("screen_name")), _norm_str(row.get("name")))
    resolved_accounts = {**secondary, **accounts}
    con.executemany(
        "INSERT OR IGNORE INTO accounts (account_id, username, name) VALUES (?, ?, ?)",
        [
            (account_id, username, name)
            for account_id, (username, name) in resolved_accounts.items()
        ],
    )

    total = 0
    with open_csv("tweets.csv") as fp:
        for batch in _batched(csv.DictReader(fp), _BATCH):
            rows = []
            for row in batch:
                tweet_id = _norm_id(row.get("tweet_id"))
                if not tweet_id:
                    continue
                author_id = _norm_id(row.get("account_id"))
                username, display_name = resolved_accounts.get(author_id or "", (None, None))
                rows.append(
                    (
                        tweet_id,
                        row.get("full_text") or "",
                        author_id,
                        username,
                        display_name,
                        _norm_dt(row.get("created_at")),
                        None,
                        _norm_id(row.get("reply_to_tweet_id")),
                        _norm_id(row.get("reply_to_user_id")),
                        _norm_str(row.get("reply_to_username")),
                        None,
                        None,
                        _norm_int(row.get("retweet_count")),
                        _norm_int(row.get("favorite_count")),
                    )
                )
            con.executemany(_INSERT_TWEET, rows)
            con.commit()
            total += len(rows)
            say(f"loaded {total:,} tweets")
    skipped = sorted(csv_names - {"tweets.csv", "account.csv", "mentioned_users.csv"})
    if skipped:
        notes.append(f"tables not imported: {', '.join(skipped)}")
    return notes


def _import_parquet(con: sqlite3.Connection, source: Path, say: Progress) -> list[str]:
    duckdb = _require_duckdb()
    ddb = duckdb.connect()
    try:
        return _import_parquet_with_connection(con, source, say, ddb)
    finally:
        ddb.close()


def _import_parquet_with_connection(
    con: sqlite3.Connection, source: Path, say: Progress, ddb: Any
) -> list[str]:
    ddb.execute("SET TimeZone='UTC'")
    notes: list[str] = []

    tweet_files: list[Path] = []
    account_files: list[Path] = []
    other: list[str] = []
    candidates = [source] if source.is_file() else sorted(source.glob("*.parquet")) + sorted(source.glob("*/*.parquet"))
    for path in candidates:
        columns = {row[0] for row in ddb.execute(f"DESCRIBE SELECT * FROM {_pq(path)}").fetchall()}
        if ("text" in columns or "full_text" in columns) and ("id" in columns or "tweet_id" in columns):
            tweet_files.append(path)
        elif "screen_name" in columns and "platform_account_id" in columns:
            account_files.append(path)
        else:
            other.append(path.name)
    if not tweet_files:
        raise ValueError(f"No tweet-shaped parquet files found in {source}")
    if other:
        notes.append(f"parquet files not imported: {', '.join(sorted(other))}")

    accounts: dict[str, tuple[str | None, str | None]] = {}
    for path in account_files:
        rows = ddb.execute(
            f"SELECT CAST(platform_account_id AS VARCHAR), any_value(screen_name), any_value(name) "
            f"FROM {_pq(path)} WHERE screen_name IS NOT NULL GROUP BY 1"
        ).fetchall()
        for account_id, username, name in rows:
            accounts.setdefault(account_id, (_norm_str(username), _norm_str(name)))
    if accounts:
        con.executemany(
            "INSERT OR IGNORE INTO accounts (account_id, username, name) VALUES (?, ?, ?)",
            [(account_id, username, name) for account_id, (username, name) in accounts.items()],
        )
        say(f"resolved {len(accounts):,} accounts from {len(account_files)} rankings file(s)")

    total = 0
    for path in tweet_files:
        columns = {row[0] for row in ddb.execute(f"DESCRIBE SELECT * FROM {_pq(path)}").fetchall()}
        if "tweet_id" in columns:
            select, make_row = _enriched_select(path, columns), _enriched_row
        else:
            select, make_row = _borg_select(path, columns), _borg_row
        cursor = ddb.execute(select)
        while True:
            batch = cursor.fetchmany(_BATCH)
            if not batch:
                break
            con.executemany(_INSERT_TWEET, [make_row(row, accounts) for row in batch])
            con.commit()
            total += len(batch)
            say(f"loaded {total:,} tweets")
    account_rows = con.execute(
        "SELECT DISTINCT author_id, username, name FROM tweets "
        "WHERE author_id IS NOT NULL AND username IS NOT NULL"
    ).fetchall()
    con.executemany("INSERT OR IGNORE INTO accounts (account_id, username, name) VALUES (?, ?, ?)", account_rows)
    return notes


def _enriched_select(path: Path, columns: set[str]) -> str:
    def col(name: str, cast: bool = False) -> str:
        if name not in columns:
            return "NULL"
        return f"CAST({name} AS VARCHAR)" if cast else name

    return (
        "SELECT "
        f"CAST(tweet_id AS VARCHAR), {col('full_text')}, {col('account_id', cast=True)}, "
        f"{col('username')}, {col('account_display_name')}, "
        f"strftime(created_at, '%Y-%m-%dT%H:%M:%SZ'), {col('conversation_id', cast=True)}, "
        f"{col('reply_to_tweet_id', cast=True)}, {col('reply_to_user_id', cast=True)}, "
        f"{col('reply_to_username')}, {col('quoted_tweet_id', cast=True)}, "
        f"{col('retweet_id', cast=True)}, {col('retweet_count')}, {col('favorite_count')} "
        f"FROM {_pq(path)}"
    )


def _enriched_row(row: tuple, accounts: dict[str, tuple[str | None, str | None]]) -> tuple:
    (tweet_id, text, author_id, username, name, created, conversation, reply, reply_user,
     reply_username, quoted, retweet_of, retweets, favorites) = row
    return (
        tweet_id,
        text or "",
        _norm_id(author_id),
        _norm_str(username),
        _norm_str(name),
        created,
        _norm_id(conversation),
        _norm_id(reply),
        _norm_id(reply_user),
        _norm_str(reply_username),
        _norm_id(quoted),
        _norm_id(retweet_of),
        retweets,
        favorites,
    )


def _borg_select(path: Path, columns: set[str]) -> str:
    def col(name: str) -> str:
        return name if name in columns else "NULL"

    return (
        "SELECT "
        f"CAST(id AS VARCHAR), {col('text')}, CAST(author_id AS VARCHAR), "
        f"strftime(created_at, '%Y-%m-%dT%H:%M:%SZ'), "
        f"{col('in_reply_to_status_id')}, {col('in_reply_to_user_id')}, "
        f"{col('quoted_status_id')}, {col('retweeted_status_id')}, "
        f"{col('retweet_count')}, {col('favorite_count')} "
        f"FROM {_pq(path)}"
    )


def _borg_row(row: tuple, accounts: dict[str, tuple[str | None, str | None]]) -> tuple:
    (tweet_id, text, author_id, created, reply, reply_user, quoted, retweet_of, retweets, favorites) = row
    username, name = accounts.get(author_id or "", (None, None))
    return (
        tweet_id,
        text or "",
        author_id,
        username,
        name,
        created,
        None,
        _norm_id(reply),
        _norm_id(reply_user),
        None,
        _norm_id(quoted),
        _norm_id(retweet_of),
        retweets,
        favorites,
    )


def _import_twitter_archive(con: sqlite3.Connection, source: Path, say: Progress) -> list[str]:
    from .archive import load_archive

    result = load_archive(source)
    account = result.account or {}
    account_id = _norm_id(account.get("accountId") or account.get("account_id"))
    username = _norm_str(account.get("username"))
    name = _norm_str(account.get("accountDisplayName") or account.get("displayName"))
    account_key = account_id
    if account_key is None and username is not None:
        account_key = f"username:{username.lower()}"
    if account_key is not None:
        con.execute(
            "INSERT OR IGNORE INTO accounts (account_id, username, name) VALUES (?, ?, ?)",
            (account_key, username, name),
        )
    total = _insert_tweets(con, result.tweets, say, fallback_username=username, fallback_name=name)
    notes = list(result.warnings)
    if not total:
        notes.append(f"no tweets found in {source}")
    return notes


def _import_tweets_file(con: sqlite3.Connection, source: Path, say: Progress) -> list[str]:
    from .sources import load_tweets_file

    result = load_tweets_file(source)
    _insert_tweets(con, result.tweets, say)
    return list(result.warnings)


def _insert_tweets(
    con: sqlite3.Connection,
    tweets: Iterable[Tweet],
    say: Progress,
    *,
    fallback_username: str | None = None,
    fallback_name: str | None = None,
) -> int:
    total = 0
    for batch in _batched(iter(tweets), _BATCH):
        rows = []
        for tweet in batch:
            quote_ids = tweet.quote_ids()
            rows.append(
                (
                    tweet.id,
                    tweet.text or "",
                    _norm_id(tweet.author_id),
                    _norm_str(tweet.username) or fallback_username,
                    _norm_str(tweet.name) or fallback_name,
                    _norm_dt(tweet.created_at) or tweet.created_at,
                    _norm_id(tweet.conversation_id),
                    _norm_id(tweet.reply_parent_id()),
                    _norm_id(tweet.in_reply_to_user_id),
                    _norm_str(tweet.in_reply_to_username),
                    quote_ids[0] if quote_ids else None,
                    None,
                    None,
                    None,
                )
            )
        con.executemany(_INSERT_TWEET, rows)
        con.commit()
        total += len(rows)
        say(f"loaded {total:,} tweets")
    return total


_IMPORTERS = {
    "community-csv": _import_community_csv,
    "parquet": _import_parquet,
    "twitter-archive": _import_twitter_archive,
    "tweets-file": _import_tweets_file,
}


# --- querying ----------------------------------------------------------------


class LocalDumpsClient:
    """Query imported dumps. Satisfies the ``TweetFetcher`` protocol, so it can
    complete reply/quote parents inside the build pipeline, and additionally
    enumerates timelines and searches text."""

    def __init__(
        self,
        names: str | Iterable[str] | None = None,
        *,
        directory: str | Path | None = None,
    ) -> None:
        base = Path(directory).expanduser() if directory else dumps_dir()
        available = sorted(base.glob("*.db")) if base.is_dir() else []
        by_name = {path.stem: path for path in available}
        requested = [names] if isinstance(names, str) else list(names or [])
        if not all(isinstance(name, str) for name in requested):
            raise TypeError("dump names must be strings")
        if requested:
            missing = [name for name in requested if _slug(name) not in by_name]
            if missing:
                known = ", ".join(sorted(by_name)) or "none"
                raise FileNotFoundError(f"Unknown dump(s): {', '.join(missing)} (imported: {known})")
            self.paths = [by_name[_slug(name)] for name in requested]
        else:
            self.paths = available
        self._connections: dict[Path, sqlite3.Connection] = {}
        self._fts: dict[Path, bool] = {}
        self._user_summaries: dict[Path, bool] = {}
        self._tweet_counts: dict[Path, int] = {}
        self._users_cache: list[dict[str, Any]] | None = None
        self._unresolved_cache: int | None = None

    def available(self) -> bool:
        return bool(self.paths)

    def dump_names(self) -> list[str]:
        return [path.stem for path in self.paths]

    def __enter__(self) -> "LocalDumpsClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        for connection in self._connections.values():
            connection.close()
        self._connections.clear()

    def _con(self, path: Path) -> sqlite3.Connection:
        connection = self._connections.get(path)
        if connection is None:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            self._connections[path] = connection
        return connection

    def _has_fts(self, path: Path) -> bool:
        if path not in self._fts:
            row = self._con(path).execute("SELECT value FROM meta WHERE key='fts'").fetchone()
            self._fts[path] = bool(row and row[0] == "1")
        return self._fts[path]

    def _has_user_summary(self, path: Path) -> bool:
        if path not in self._user_summaries:
            row = self._con(path).execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'user_summary'"
            ).fetchone()
            self._user_summaries[path] = row is not None
        return self._user_summaries[path]

    def _tweet_count(self, path: Path) -> int:
        if path not in self._tweet_counts:
            connection = self._con(path)
            row = connection.execute("SELECT value FROM meta WHERE key = 'tweets'").fetchone()
            try:
                count = int(row[0]) if row is not None else None
            except (TypeError, ValueError):
                count = None
            if count is None:
                count = connection.execute("SELECT count(*) FROM tweets").fetchone()[0]
            self._tweet_counts[path] = count
        return self._tweet_counts[path]

    # TweetFetcher protocol.
    def get_posts(self, ids: list[str]) -> FetchResult:
        result = FetchResult()
        merged: dict[str, Tweet] = {}
        wanted = [tweet_id for tweet_id in unique_preserve_order(ids) if tweet_id.isdigit()]
        for path in self.paths:
            connection = self._con(path)
            for start in range(0, len(wanted), 500):
                batch = wanted[start : start + 500]
                placeholders = ",".join("?" * len(batch))
                rows = connection.execute(
                    f"SELECT {_COLS} FROM tweets WHERE id IN ({placeholders})", batch
                ).fetchall()
                for row in rows:
                    tweet = _row_to_tweet(row, path.stem)
                    current = merged.get(tweet.id)
                    merged[tweet.id] = tweet if current is None else merge_tweets(current, tweet)
        result.tweets.extend(merged.values())
        return result

    def get_user_posts(
        self,
        username: str,
        *,
        since=None,
        limit: int | None = None,
        include_retweets: bool = False,
    ) -> FetchResult:
        limit = _positive_limit(limit)
        result = FetchResult()
        merged: dict[str, Tweet] = {}
        handle = username.strip("@")
        conditions = ["username = ? COLLATE NOCASE"]
        params: list[Any] = [handle]
        since_text = _since_text(since)
        if since_text:
            conditions.append("created_at >= ?")
            params.append(since_text)
        if not include_retweets:
            conditions.append("retweet_of_id IS NULL AND text NOT LIKE 'RT @%'")
        sql = f"SELECT {_COLS} FROM tweets WHERE {' AND '.join(conditions)} ORDER BY created_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        for path in self.paths:
            for row in self._con(path).execute(sql, params):
                tweet = _row_to_tweet(row, path.stem)
                current = merged.get(tweet.id)
                merged[tweet.id] = tweet if current is None else merge_tweets(current, tweet)
        tweets = sorted(merged.values(), key=lambda tweet: tweet.created_at or "", reverse=True)
        result.tweets.extend(tweets[:limit] if limit is not None else tweets)
        return result

    def get_author_posts(
        self,
        author_id: str,
        *,
        since=None,
        limit: int | None = None,
        include_retweets: bool = False,
    ) -> FetchResult:
        """Return posts by numeric author id, including unresolved authors."""
        limit = _positive_limit(limit)
        result = FetchResult()
        merged: dict[str, Tweet] = {}
        normalized_author_id = _norm_id(author_id)
        since_text = _since_text(since)
        if not normalized_author_id:
            return result
        conditions = ["author_id = ?"]
        params: list[Any] = [normalized_author_id]
        if since_text:
            conditions.append("created_at >= ?")
            params.append(since_text)
        if not include_retweets:
            conditions.append("retweet_of_id IS NULL AND text NOT LIKE 'RT @%'")
        sql = f"SELECT {_COLS} FROM tweets WHERE {' AND '.join(conditions)} ORDER BY created_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        for path in self.paths:
            for row in self._con(path).execute(sql, params):
                tweet = _row_to_tweet(row, path.stem)
                current = merged.get(tweet.id)
                merged[tweet.id] = tweet if current is None else merge_tweets(current, tweet)
        tweets = sorted(merged.values(), key=lambda tweet: tweet.created_at or "", reverse=True)
        result.tweets.extend(tweets[:limit] if limit is not None else tweets)
        return result

    def search(
        self,
        query: str,
        *,
        username: str | None = None,
        since=None,
        limit: int = 50,
    ) -> list[Tweet]:
        validated_limit = _positive_limit(limit, name="search limit")
        assert validated_limit is not None
        merged: dict[str, Tweet] = {}
        extra = ""
        params_extra: list[Any] = []
        if username:
            extra += " AND t.username = ? COLLATE NOCASE"
            params_extra.append(username.strip("@"))
        since_text = _since_text(since)
        if since_text:
            extra += " AND t.created_at >= ?"
            params_extra.append(since_text)
        for path in self.paths:
            connection = self._con(path)
            if self._has_fts(path):
                sql = (
                    f"SELECT {', '.join('t.' + column for column in _TWEET_COLUMNS)} "
                    "FROM tweets_fts f JOIN tweets t ON t.rowid = f.rowid "
                    f"WHERE tweets_fts MATCH ?{extra} ORDER BY rank LIMIT ?"
                )
                try:
                    rows = connection.execute(
                        sql, [query, *params_extra, validated_limit]
                    ).fetchall()
                except sqlite3.OperationalError:
                    phrase = '"' + query.replace('"', '""') + '"'
                    rows = connection.execute(
                        sql, [phrase, *params_extra, validated_limit]
                    ).fetchall()
            else:
                sql = (
                    f"SELECT {', '.join('t.' + column for column in _TWEET_COLUMNS)} FROM tweets t "
                    f"WHERE t.text LIKE ? ESCAPE '\\'{extra} ORDER BY t.created_at DESC LIMIT ?"
                )
                like = "%" + re.sub(r"([%_\\])", r"\\\1", query) + "%"
                rows = connection.execute(
                    sql, [like, *params_extra, validated_limit]
                ).fetchall()
            for row in rows:
                tweet = _row_to_tweet(row, path.stem)
                current = merged.get(tweet.id)
                merged[tweet.id] = tweet if current is None else merge_tweets(current, tweet)
        tweets = sorted(merged.values(), key=lambda tweet: tweet.created_at or "", reverse=True)
        return tweets[:validated_limit]

    def children(self, tweet_id: str, *, limit: int | None = None) -> list[Tweet]:
        limit = _positive_limit(limit)
        merged: dict[str, Tweet] = {}
        for path in self.paths:
            sql = f"SELECT {_COLS} FROM tweets WHERE in_reply_to_id = ? ORDER BY created_at"
            if limit is not None:
                sql += f" LIMIT {int(limit)}"
            rows = self._con(path).execute(sql, (tweet_id,)).fetchall()
            for row in rows:
                tweet = _row_to_tweet(row, path.stem)
                current = merged.get(tweet.id)
                merged[tweet.id] = tweet if current is None else merge_tweets(current, tweet)
        children = sorted(merged.values(), key=lambda tweet: tweet.created_at or "")
        return children[:limit] if limit is not None else children

    def _unresolved_for_path(self, path: Path) -> int:
        connection = self._con(path)
        row = connection.execute("SELECT value FROM meta WHERE key = 'unresolved'").fetchone()
        if row is not None:
            try:
                return int(row[0])
            except (TypeError, ValueError):
                pass
        return connection.execute("SELECT count(*) FROM tweets WHERE username IS NULL").fetchone()[0]

    def users(self, *, match: str | None = None) -> list[dict[str, Any]]:
        """Every resolved user across the selected dumps with exact tweet counts."""
        if self._users_cache is not None:
            users = self._users_cache
            if match:
                needle = match.lower()
                return [
                    user
                    for user in users
                    if needle in user["username"].lower() or needle in (user["name"] or "").lower()
                ]
            return users

        totals: dict[str, dict[str, Any]] = {}
        ordered_paths = sorted(self.paths, key=lambda path: (-self._tweet_count(path), path.name))
        if not ordered_paths:
            self._users_cache = []
            self._unresolved_cache = 0
            return []

        base_path = ordered_paths[0]
        if self._has_user_summary(base_path):
            base_rows = self._con(base_path).execute(
                "SELECT username, name, tweets, first_tweet, last_tweet FROM user_summary"
            ).fetchall()
        else:
            base_rows = self._con(base_path).execute(
                "SELECT min(username), max(name), count(*), min(created_at), max(created_at) "
                "FROM tweets WHERE username IS NOT NULL GROUP BY username COLLATE NOCASE"
            ).fetchall()

        def add_rows(path: Path, rows) -> None:
            for username, name, count, first, last in rows:
                entry = totals.setdefault(
                    username.lower(),
                    {"username": username, "name": name, "tweets": 0, "first": first, "last": last, "dumps": []},
                )
                entry["tweets"] += count
                entry["name"] = entry["name"] or name
                entry["first"] = min(filter(None, [entry["first"], first]), default=None)
                entry["last"] = max(filter(None, [entry["last"], last]), default=None)
                if path.stem not in entry["dumps"]:
                    entry["dumps"].append(path.stem)

        add_rows(base_path, base_rows)
        unresolved = self._unresolved_for_path(base_path)
        if len(ordered_paths) > 1:
            # Keep the largest DB attached as the indexed baseline. A temporary
            # table tracks ids from previously visited smaller DBs. Per-dump
            # staging rows also let a later dump resolve the username of a
            # duplicate that was unresolved earlier. This works for any number
            # of dumps without hitting SQLite's ten-attachment default.
            with sqlite3.connect("") as dedupe:
                dedupe.execute(
                    "CREATE TABLE seen_extra (id TEXT PRIMARY KEY, username TEXT) WITHOUT ROWID"
                )
                dedupe.execute("CREATE TABLE resolved_base (id TEXT PRIMARY KEY) WITHOUT ROWID")
                dedupe.execute("ATTACH DATABASE ? AS base", (f"file:{base_path}?mode=ro",))
                for path in ordered_paths[1:]:
                    dedupe.execute("ATTACH DATABASE ? AS current", (f"file:{path}?mode=ro",))
                    dedupe.execute(
                        "CREATE TEMP TABLE current_rows AS "
                        "SELECT current.id, current.username, current.name, current.created_at, "
                        "base.id IS NOT NULL AS in_base, base.username AS base_username, "
                        "seen.id IS NOT NULL AS in_extra, seen.username AS extra_username "
                        "FROM current.tweets AS current "
                        "LEFT JOIN base.tweets AS base ON base.id = current.id "
                        "LEFT JOIN seen_extra AS seen ON seen.id = current.id"
                    )
                    rows = dedupe.execute(
                        "SELECT min(username), max(name), count(*), min(created_at), max(created_at) "
                        "FROM current_rows AS row WHERE username IS NOT NULL AND ("
                        "(in_base AND base_username IS NULL AND NOT EXISTS "
                        "(SELECT 1 FROM resolved_base WHERE resolved_base.id = row.id)) OR "
                        "(NOT in_base AND (NOT in_extra OR extra_username IS NULL))) "
                        "GROUP BY username COLLATE NOCASE"
                    ).fetchall()
                    add_rows(path, rows)
                    dedupe.execute(
                        "INSERT OR IGNORE INTO resolved_base "
                        "SELECT id FROM current_rows "
                        "WHERE in_base AND base_username IS NULL AND username IS NOT NULL"
                    )
                    dedupe.execute(
                        "INSERT INTO seen_extra (id, username) "
                        "SELECT id, username FROM current_rows WHERE NOT in_base "
                        "ON CONFLICT(id) DO UPDATE SET "
                        "username = coalesce(seen_extra.username, excluded.username)"
                    )
                    dedupe.commit()
                    dedupe.execute("DROP TABLE current_rows")
                    dedupe.execute("DETACH DATABASE current")
                resolved_base = dedupe.execute("SELECT count(*) FROM resolved_base").fetchone()[0]
                unresolved_extra = dedupe.execute(
                    "SELECT count(*) FROM seen_extra WHERE username IS NULL"
                ).fetchone()[0]
                unresolved = max(0, unresolved - resolved_base) + unresolved_extra
        users = list(totals.values())
        users.sort(key=lambda user: user["tweets"], reverse=True)
        self._users_cache = users
        self._unresolved_cache = unresolved
        if match:
            needle = match.lower()
            users = [
                user
                for user in users
                if needle in user["username"].lower() or needle in (user["name"] or "").lower()
            ]
        return users

    def unresolved_count(self) -> int:
        if self._unresolved_cache is None:
            if len(self.paths) > 1:
                self.users()
            else:
                self._unresolved_cache = sum(self._unresolved_for_path(path) for path in self.paths)
        assert self._unresolved_cache is not None
        return self._unresolved_cache


def _row_to_tweet(row: tuple, dump_name: str) -> Tweet:
    (tweet_id, text, author_id, username, name, created_at, conversation_id, reply_id,
     reply_user_id, reply_username, quoted_id, retweet_of_id, _retweets, _favorites) = row
    refs: list[TweetRef] = []
    if reply_id:
        refs.append(TweetRef("replied_to", reply_id))
    if quoted_id:
        refs.append(TweetRef("quoted", quoted_id))
    if retweet_of_id:
        refs.append(TweetRef("retweeted", retweet_of_id))
    return Tweet(
        id=tweet_id,
        text=text or "",
        author_id=author_id,
        username=username,
        name=name,
        created_at=created_at,
        conversation_id=conversation_id,
        in_reply_to_id=reply_id,
        in_reply_to_user_id=reply_user_id,
        in_reply_to_username=reply_username,
        referenced_tweets=refs,
        source=f"dump:{dump_name}",
        url=f"https://x.com/{username}/status/{tweet_id}" if username else None,
    )


# --- helpers -----------------------------------------------------------------


def _require_duckdb() -> Any:
    try:
        return importlib.import_module("duckdb")
    except ImportError as exc:
        raise RuntimeError(
            "Reading parquet dumps needs the optional duckdb dependency: pip install 'ariadne-x[parquet]'"
        ) from exc


def _pq(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    return f"read_parquet('{escaped}')"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower()).strip("-")


def _batched(iterator, size: int):
    batch = []
    for item in iterator:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _norm_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text.isdigit() else None


def _norm_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _norm_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _norm_dt(value: Any) -> str | None:
    return api_time(parse_datetime(value if value is None else str(value)))


def _since_text(since) -> str | None:
    if since is None:
        return None
    if isinstance(since, datetime):
        return api_time(since)
    return api_time(parse_since(str(since)))


def _positive_limit(limit: int | None, *, name: str = "limit") -> int | None:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError(f"{name} must be a positive integer")
    return limit
