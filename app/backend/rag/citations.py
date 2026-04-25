"""Two-tier citation system: parse `[c:<chunk_id>]` markers from LLM output.

The chat system prompt instructs the model to emit ``[c:<chunk_id>]`` markers
inline after any claim grounded in retrieved content. The frontend then
renders a focused "Sources cited" tier (chunks the model marked) above an
expandable "All sources consulted" tier (every retrieved chunk).
"""

from __future__ import annotations

import re
from typing import Final

# Complete-marker regex: ``[c:<id>]`` where <id> is a non-empty run of
# alphanumerics, dash, and underscore (covers UUIDs and short hash IDs).
_MARKER_RE: Final = re.compile(r"\[c:([A-Za-z0-9_-]+)\]")

# Anchored partial-marker regex: matches ``[``, ``[c``, ``[c:``, ``[c:<id>``
# at the end of a string. Used by the streaming stripper to decide what to
# hold back across SSE token boundaries.
_PARTIAL_AT_END_RE: Final = re.compile(r"\[(?:c(?::[A-Za-z0-9_-]*)?)?$")

# Bound on bytes held by the streaming stripper. If a partial never closes
# within this many characters, flush it as plain text rather than stalling.
_MAX_HOLDBACK: Final = 128


def extract_cited_chunk_ids(text: str) -> set[str]:
    """Return the set of chunk_ids referenced via ``[c:<id>]`` markers."""
    return set(_MARKER_RE.findall(text))


def strip_citation_markers(text: str) -> str:
    """Remove all ``[c:<id>]`` markers from ``text`` (use on complete text)."""
    return _MARKER_RE.sub("", text)


class CitationMarkerStripper:
    """Stream-safe stripper for ``[c:<chunk_id>]`` markers.

    Markers can straddle SSE token boundaries, so we hold back any tail that
    could plausibly be the start of one. Held tails exceeding
    :data:`_MAX_HOLDBACK` are flushed as plain text to bound memory.
    """

    def __init__(self) -> None:
        self._buf: str = ""

    def feed(self, text: str) -> str:
        """Append ``text`` to the buffer; return what is safe to emit now."""
        self._buf += text
        cleaned = _MARKER_RE.sub("", self._buf)
        partial_match = _PARTIAL_AT_END_RE.search(cleaned)
        if partial_match is None:
            self._buf = ""
            return cleaned
        held = cleaned[partial_match.start() :]
        if len(held) >= _MAX_HOLDBACK:
            self._buf = ""
            return cleaned
        self._buf = held
        return cleaned[: partial_match.start()]

    def flush(self) -> str:
        """Return any held text at end-of-stream (markers stripped)."""
        out = _MARKER_RE.sub("", self._buf)
        self._buf = ""
        return out
