"""Shared public type aliases without importing the build pipeline."""

from __future__ import annotations

import os
from typing import Literal

OutputFormat = Literal["messages", "openai", "json", "markdown", "raft"]
PathInput = str | os.PathLike[str]
