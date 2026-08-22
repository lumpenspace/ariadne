"""Remembered API credentials.

Tokens are kept in the settings directory so a token entered once does not
have to be pasted again, and are removed as soon as a provider tells us they
no longer work. Storage is a plain JSON file with owner-only permissions —
the same treatment the dump databases get. Anything with access to the user's
account can read it, so treat it as convenience, not a secret store.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from .dumps import ariadne_home

X_BEARER_TOKEN = "x_bearer_token"
TWITTERAPI_KEY = "twitterapi_key"

__all__ = [
    "X_BEARER_TOKEN",
    "TWITTERAPI_KEY",
    "credentials_path",
    "load_credential",
    "save_credential",
    "delete_credential",
]


def credentials_path() -> Path:
    return ariadne_home() / "credentials.json"


def _read() -> dict[str, Any]:
    path = credentials_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(payload: dict[str, Any]) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path.parent, stat.S_IRWXU)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if os.name == "posix":
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(path)
    return path


def load_credential(name: str) -> str | None:
    value = _read().get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def save_credential(name: str, value: str) -> Path | None:
    """Remember `value` under `name`. Returns the file, or None if unwritable."""
    value = (value or "").strip()
    if not value:
        return None
    payload = _read()
    if payload.get(name) == value:
        return credentials_path()
    payload[name] = value
    try:
        return _write(payload)
    except OSError:
        return None


def delete_credential(name: str) -> bool:
    """Forget `name`. Returns whether anything was actually removed."""
    payload = _read()
    if name not in payload:
        return False
    payload.pop(name)
    try:
        _write(payload)
    except OSError:
        return False
    return True
