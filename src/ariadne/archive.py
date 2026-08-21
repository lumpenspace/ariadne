from __future__ import annotations

import html
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ids import tweet_id_from_url, unique_preserve_order
from .models import Tweet, TweetRef


@dataclass
class ArchiveLoadResult:
    tweets: list[Tweet] = field(default_factory=list)
    account: dict[str, Any] | None = None
    files_read: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_archive(path: str | Path) -> ArchiveLoadResult:
    archive_path = Path(path).expanduser()
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive path does not exist: {archive_path}")

    if archive_path.is_dir():
        return _load_archive_files(_iter_directory_files(archive_path))
    if zipfile.is_zipfile(archive_path):
        return _load_archive_files(_iter_zip_files(archive_path))
    return _load_archive_files([(archive_path.name, archive_path.read_text(encoding="utf-8"))])


def _iter_directory_files(root: Path) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".js", ".json"}:
            continue
        name = path.relative_to(root).as_posix()
        if _could_contain_archive_data(name):
            files.append((name, path.read_text(encoding="utf-8")))
    return files


def _iter_zip_files(path: Path) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    with zipfile.ZipFile(path) as zf:
        for name in sorted(zf.namelist()):
            if name.endswith("/") or Path(name).suffix.lower() not in {".js", ".json"}:
                continue
            if _could_contain_archive_data(name):
                with zf.open(name) as fp:
                    files.append((name, fp.read().decode("utf-8")))
    return files


def _could_contain_archive_data(name: str) -> bool:
    base = Path(name).name.lower()
    return (
        base.startswith("account")
        or base.startswith("tweet")
        or base.startswith("tweets")
    )


def _load_archive_files(files: list[tuple[str, str]]) -> ArchiveLoadResult:
    result = ArchiveLoadResult()
    parsed: list[tuple[str, Any]] = []
    for name, text in files:
        try:
            payload = _parse_archive_payload(text)
        except json.JSONDecodeError as exc:
            result.warnings.append(f"Skipped {name}: could not parse JSON payload ({exc})")
            continue
        parsed.append((name, payload))
        result.files_read.append(name)

    for _, payload in parsed:
        account = _extract_account(payload)
        if account:
            result.account = account
            break

    for name, payload in parsed:
        for raw_tweet in _extract_tweet_objects(payload):
            tweet = tweet_from_archive(raw_tweet, result.account, source=f"archive:{name}")
            if tweet:
                result.tweets.append(tweet)
    return result


def _parse_archive_payload(text: str) -> Any:
    stripped = text.lstrip("\ufeff\n\r\t ")
    if stripped.startswith("[") or stripped.startswith("{"):
        payload = stripped
    else:
        starts = [index for index in (stripped.find("["), stripped.find("{")) if index >= 0]
        if not starts:
            raise json.JSONDecodeError("No JSON payload found", stripped, 0)
        payload = stripped[min(starts) :]
    payload = payload.strip()
    if payload.endswith(";"):
        payload = payload[:-1]
    return json.loads(payload)


def _extract_account(payload: Any) -> dict[str, Any] | None:
    entries = payload if isinstance(payload, list) else [payload]
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("account"), dict):
            return entry["account"]
    return None


def _extract_tweet_objects(payload: Any) -> list[dict[str, Any]]:
    entries = payload if isinstance(payload, list) else [payload]
    tweets: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("tweet"), dict):
            tweets.append(entry["tweet"])
        elif _looks_like_tweet(entry):
            tweets.append(entry)
    return tweets


def _looks_like_tweet(entry: dict[str, Any]) -> bool:
    return any(key in entry for key in ("id_str", "id", "full_text", "text")) and any(
        key in entry for key in ("created_at", "in_reply_to_status_id_str", "in_reply_to_status_id")
    )


def tweet_from_archive(
    data: dict[str, Any], account: dict[str, Any] | None = None, *, source: str | None = None
) -> Tweet | None:
    tweet_id = _clean_id(data.get("id_str") or data.get("id"))
    if not tweet_id:
        return None

    account = account or {}
    username = _first_str(
        data.get("user", {}).get("screen_name") if isinstance(data.get("user"), dict) else None,
        data.get("username"),
        account.get("username"),
        account.get("accountDisplayName"),
    )
    name = _first_str(
        data.get("user", {}).get("name") if isinstance(data.get("user"), dict) else None,
        account.get("accountDisplayName"),
        username,
    )
    author_id = _clean_id(
        data.get("user", {}).get("id_str") if isinstance(data.get("user"), dict) else None
    ) or _clean_id(account.get("accountId"))

    reply_id = _clean_id(
        data.get("in_reply_to_status_id_str")
        or data.get("in_reply_to_status_id")
        or data.get("in_reply_to_id")
        or data.get("inReplyToId")
        or data.get("reply_to_id")
        or data.get("parent_id")
    )
    refs: list[TweetRef] = []
    if reply_id:
        refs.append(TweetRef("replied_to", reply_id))

    for quote_id in _quote_ids_from_archive(data):
        refs.append(TweetRef("quoted", quote_id))

    text = _first_str(data.get("full_text"), data.get("text"), data.get("note_tweet", {}).get("text"))
    if text:
        text = html.unescape(text)

    return Tweet(
        id=tweet_id,
        text=text or "",
        author_id=author_id,
        username=username,
        name=name,
        created_at=_first_str(data.get("created_at")),
        conversation_id=_clean_id(data.get("conversation_id") or data.get("conversation_id_str")),
        in_reply_to_id=reply_id,
        in_reply_to_user_id=_clean_id(
            data.get("in_reply_to_user_id_str") or data.get("in_reply_to_user_id")
        ),
        in_reply_to_username=_first_str(data.get("in_reply_to_screen_name")),
        referenced_tweets=_dedupe_refs(refs),
        source=source,
        url=f"https://x.com/{username}/status/{tweet_id}" if username else None,
        raw=data,
    )


def _quote_ids_from_archive(data: dict[str, Any]) -> list[str]:
    quote_ids: list[str] = []
    direct = _clean_id(data.get("quoted_status_id_str") or data.get("quoted_status_id"))
    if direct:
        quote_ids.append(direct)

    permalink = data.get("quoted_status_permalink")
    if isinstance(permalink, dict):
        for key in ("expanded", "url", "display"):
            value = permalink.get(key)
            quote_id = tweet_id_from_url(value) if value else None
            if quote_id:
                quote_ids.append(quote_id)

    if data.get("is_quote_status") is True:
        raw_entities = data.get("entities")
        entities: dict[str, Any] = raw_entities if isinstance(raw_entities, dict) else {}
        for url_data in entities.get("urls", []):
            if not isinstance(url_data, dict):
                continue
            for key in ("expanded_url", "url", "display_url"):
                value = url_data.get(key)
                quote_id = tweet_id_from_url(value) if value else None
                if quote_id:
                    quote_ids.append(quote_id)

    return unique_preserve_order(quote_ids)


def _dedupe_refs(refs: list[TweetRef]) -> list[TweetRef]:
    seen: set[tuple[str, str]] = set()
    deduped: list[TweetRef] = []
    for ref in refs:
        key = (ref.type, ref.id)
        if key not in seen:
            seen.add(key)
            deduped.append(ref)
    return deduped


def _first_str(*values: Any) -> str | None:
    for value in values:
        if value is not None and value != "":
            return str(value)
    return None


def _clean_id(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value)
    return text if text.isdigit() else None
