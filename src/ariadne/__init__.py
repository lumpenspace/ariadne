"""Reconstruct X/Twitter reply branches and render them for LLM input.

Typical library use:

    import ariadne

    result = ariadne.build(archive="~/twitter-archive.zip", for_user="alice")
    documents = result.raft_documents()

See :func:`ariadne.build` and :class:`ariadne.BuildOptions`.
"""

from .api import BuildOptions, BuildResult, build, build_conversations
from .archive import load_archive
from .models import Conversation, QuoteContext, Tweet, TweetRef
from .render import (
    json_payload,
    message_conversations,
    raft_documents,
    render,
)
from .sources import load_tweets_file
from .store import TweetStore, load_cache, save_cache

__all__ = [
    "__version__",
    # Pipeline
    "build",
    "build_conversations",
    "BuildOptions",
    "BuildResult",
    # Models
    "Conversation",
    "QuoteContext",
    "Tweet",
    "TweetRef",
    "TweetStore",
    # Sources
    "load_archive",
    "load_tweets_file",
    "load_cache",
    "save_cache",
    # Rendering
    "render",
    "raft_documents",
    "message_conversations",
    "json_payload",
]

__version__ = "0.3.0"
