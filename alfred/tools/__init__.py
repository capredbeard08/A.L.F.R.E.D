"""Tool-execution modules for A.L.F.R.E.D.

Each tool is a small, importable module that depends only on the standard
library at rest. Optional third-party clients are probed at call time and used
only when they are actually installed, so the repo keeps zero hard
dependencies.

Note: the ``search`` submodule is deliberately NOT re-exported as a function
here. Doing so would shadow the submodule attribute and break
``alfred.tools.search``. Import the function from the submodule directly::

    from alfred.tools.search import search
"""
from __future__ import annotations

from . import search  # noqa: F401  (keep the submodule attribute intact)
from .search import (
    SearchError,
    SearchResult,
    available_provider,
    build_grounding_block,
    format_brief,
)

__all__ = [
    "search",
    "SearchError",
    "SearchResult",
    "available_provider",
    "build_grounding_block",
    "format_brief",
]
