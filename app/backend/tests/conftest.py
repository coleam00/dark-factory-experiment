"""
Session-scoped mocks that must be in place before any backend module
that transitively imports docling_core is collected or imported.

docling_core pulls in pandas → bottleneck (compiled against NumPy 1.x),
which crashes in the current NumPy 2.x environment.  We stub the entire
docling_core namespace so the rest of the backend can be imported cleanly.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock


def _stub_module(name: str) -> MagicMock:
    stub = MagicMock()
    stub.__name__ = name
    stub.__path__ = []  # Make it look like a package
    sys.modules[name] = stub
    return stub


# Stub every docling_core sub-module that chunker.py imports
for _mod in (
    "docling_core",
    "docling_core.transforms",
    "docling_core.transforms.chunker",
    "docling_core.transforms.chunker.hybrid_chunker",
    "docling_core.types",
    "docling_core.types.doc",
    "docling_core.types.doc.document",
):
    if _mod not in sys.modules:
        _stub_module(_mod)
