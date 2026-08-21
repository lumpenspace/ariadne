"""Public exception hierarchy for the ariadne Python API.

The concrete exceptions retain their historical ``ValueError`` or
``RuntimeError`` base classes so existing callers can adopt the more precise
types without having to change their error handling immediately.
"""

from __future__ import annotations

__all__ = [
    "AriadneError",
    "ConfigurationError",
    "NoTargetsError",
    "ReconstructionError",
    "SourceError",
]


class AriadneError(Exception):
    """Base class for errors raised by ariadne's public API."""


class ConfigurationError(AriadneError, ValueError, RuntimeError):
    """A build option is invalid or a requested source is not configured.

    Configuration failures historically used both ``ValueError`` and
    ``RuntimeError``. Inheriting from both keeps either compatibility path
    working while giving new callers one precise exception to catch.
    """


class NoTargetsError(AriadneError, RuntimeError):
    """No posts matched the requested inputs and target selection."""


class ReconstructionError(AriadneError, RuntimeError):
    """A conversation could not be reconstructed under the requested policy."""


class SourceError(AriadneError, RuntimeError):
    """An external source failed while loading or hydrating posts."""
