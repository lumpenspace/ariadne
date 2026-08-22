"""Reconstruct X/Twitter reply branches and render them for LLM input.

Typical library use:

    import ariadne

    result = ariadne.build(archive="~/twitter-archive.zip", for_user="alice")
    documents = result.raft_documents()

See :func:`ariadne.build` and :class:`ariadne.BuildOptions`.
"""

from .api import (
    BuildKwargs,
    BuildOptions,
    BuildResult,
    CacheRetryResult,
    OutputFormat,
    PathInput,
    build,
    build_conversations,
    conversation_has_response,
    retry_cache,
)
from .archive import load_archive
from .bluesky import build_bluesky
from .dumps import DumpInfo, LocalDumpsClient, ariadne_home, import_dump, list_dumps, remove_dump
from .errors import (
    AriadneError,
    ConfigurationError,
    NoTargetsError,
    ReconstructionError,
    SourceError,
)
from .fetch import XApiError
from .providers import CommunityArchiveClient, TwitterApiIoClient
from .models import Conversation, QuoteContext, Tweet, TweetRef
from .render import (
    json_payload,
    message_conversations,
    raft_documents,
    render,
)
from .credentials import (
    credentials_path,
    delete_credential,
    load_credential,
    save_credential,
)
from .sources import load_tweets_file
from .store import (
    TweetStore,
    load_cache,
    load_stream,
    save_cache,
    stream_path,
)

__all__ = [
    "__version__",
    # Pipeline
    "build",
    "build_conversations",
    "build_bluesky",
    "conversation_has_response",
    "BuildOptions",
    "BuildResult",
    "BuildKwargs",
    "OutputFormat",
    "PathInput",
    "CommunityArchiveClient",
    "TwitterApiIoClient",
    # Errors
    "AriadneError",
    "ConfigurationError",
    "NoTargetsError",
    "ReconstructionError",
    "SourceError",
    "XApiError",
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
    # Durable stream of paid fetches
    "stream_path",
    "load_stream",
    # Remembered credentials
    "credentials_path",
    "load_credential",
    "save_credential",
    "delete_credential",
    # Cache retry
    "CacheRetryResult",
    "retry_cache",
    # Local dumps
    "DumpInfo",
    "LocalDumpsClient",
    "ariadne_home",
    "import_dump",
    "list_dumps",
    "remove_dump",
    # Rendering
    "render",
    "raft_documents",
    "message_conversations",
    "json_payload",
]

__version__ = "0.7.0"
