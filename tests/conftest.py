"""Test-suite wide isolation.

Every test runs with ARIADNE_HOME pointed at a throwaway directory so the
suite never reads (or writes) the developer's real ~/.ariadne dump library —
imported dumps are a global source that `build_conversations` picks up by
default, which would otherwise leak into offline pipeline tests.
"""

from __future__ import annotations

import os
import tempfile

os.environ["ARIADNE_HOME"] = tempfile.mkdtemp(prefix="ariadne-tests-")
