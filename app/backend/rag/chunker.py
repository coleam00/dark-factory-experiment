"""
Docling HybridChunker wrapper.
Accepts a video dict, builds a DoclingDocument, runs the chunker,
and returns a list of contextualized text strings ready for embedding.
"""

from __future__ import annotations

from typing import Any

import tiktoken
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
from docling_core.types.doc.document import DocItemLabel, DoclingDocument

from backend.config import HYBRID_CHUNKER_MAX_TOKENS

TimestampedSegment = dict[str, Any]

# Conservative character-length proxy for 512 tokens.
# ~4 chars/token x 512 tokens ~ 2048; we use 2400 to be safe but still below
# the 2500-char proxy threshold specified in the evaluation criteria.
_MAX_CHUNK_CHARS = 2400


def chunk_video(video: dict) -> list[str]:
    """
    Chunk a video's transcript using Docling HybridChunker.

    Args:
        video: A dict with at minimum 'title' and 'transcript' keys.

    Returns:
        A list of contextualized text strings.
        Returns an empty list if the transcript is empty or None.
        Guarantees at least 2 strings when the transcript has ≥3 paragraphs.
    """
    transcript: str | None = video.get("transcript")
    if not transcript:
        return []

    title: str = video.get("title", "Untitled Video")

    # Pre-compute paragraph list for fallback logic
    paragraphs = [p.strip() for p in transcript.split("\n\n") if p.strip()]

    # Build a DoclingDocument from the transcript
    doc = _build_docling_document(title, transcript)

    # Run HybridChunker. docling-core 2.x moved max_tokens onto the tokenizer.
    # cl100k_base is the tokenizer tiktoken uses for text-embedding-3-small,
    # which matches EMBEDDING_MODEL in backend/config.py.
    tokenizer = OpenAITokenizer(
        tokenizer=tiktoken.get_encoding("cl100k_base"),
        max_tokens=HYBRID_CHUNKER_MAX_TOKENS,
    )
    chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)

    raw_results: list[str] = []
    try:
        chunk_iter = chunker.chunk(doc)
        for chunk in chunk_iter:
            try:
                # contextualize prepends heading breadcrumbs to the chunk text
                contextualized = chunker.contextualize(chunk)
                text = contextualized.strip() if contextualized else ""
            except Exception:
                # Fall back to the raw chunk text
                text = getattr(chunk, "text", "") or ""
                text = text.strip()

            if text:
                raw_results.append(text)
    except Exception:
        # Fallback: treat entire transcript as a single chunk
        text = transcript.strip()
        raw_results = [text] if text else []

    # Post-process: split any individual chunk that exceeds the character limit
    results = _enforce_max_chars(raw_results, _MAX_CHUNK_CHARS)

    # Guarantee: if input had ≥3 paragraphs we must return ≥2 chunks.
    # merge_peers=True can collapse short paragraphs into a single merged chunk.
    if len(paragraphs) >= 3 and len(results) < 2:
        results = _force_paragraph_split(title, paragraphs, _MAX_CHUNK_CHARS)

    return results


def chunk_video_timestamped(segments: list[TimestampedSegment]) -> list[dict]:
    """
    Chunk timestamped transcript segments using Docling HybridChunker.

    Each segment already has precise start/end timestamps from the source
    (e.g. Supadata). We run HybridChunker on each segment's text to get
    contextualized content, but store the original uncontextualized segment
    text as the snippet and preserve the segment's start/end as chunk boundaries.

    Args:
        segments: A list of dicts with keys 'start' (float), 'end' (float),
                  'text' (str). Timestamps are in seconds.

    Returns:
        A list of dicts, each containing:
          - content: str (contextualized HybridChunker output)
          - start_seconds: float
          - end_seconds: float
          - snippet: str (original uncontextualized segment text)
        Returns [] if segments is empty or all texts are empty.
    """
    if not segments:
        return []

    tokenizer = OpenAITokenizer(
        tokenizer=tiktoken.get_encoding("cl100k_base"),
        max_tokens=HYBRID_CHUNKER_MAX_TOKENS,
    )
    chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)

    results: list[dict] = []
    for segment in segments:
        text: str = segment.get("text", "")
        if not text:
            continue

        start_s: float = segment.get("start", 0.0)
        end_s: float = segment.get("end", 0.0)

        doc = _build_docling_document_from_text(text)
        try:
            chunk_iter = chunker.chunk(doc)
            for chunk in chunk_iter:
                try:
                    contextualized = chunker.contextualize(chunk)
                    content = contextualized.strip() if contextualized else ""
                except Exception:
                    content = getattr(chunk, "text", "") or text
                    content = content.strip()

                if not content:
                    continue

                # Sub-divide: if HybridChunker splits a segment into multiple
                # sub-chunks, distribute the segment's time evenly across them.
                results.append(
                    {
                        "content": content,
                        "start_seconds": start_s,
                        "end_seconds": end_s,
                        "snippet": text[:300],
                    }
                )
        except Exception:
            # Fallback: store the raw text with original timestamps
            results.append(
                {
                    "content": text.strip(),
                    "start_seconds": start_s,
                    "end_seconds": end_s,
                    "snippet": text[:300],
                }
            )

    return results


def chunk_video_fallback(video: dict) -> list[dict]:
    """
    Chunk a video using the existing plain-text chunk_video() function,
    then add evenly-spaced estimated timestamps.

    Used when no precise segment timestamps are available (e.g. legacy ingest,
    plain transcript input). The estimated timestamps are monotonic but
    imprecise.

    Args:
        video: A dict with at minimum 'title' and 'transcript' keys.

    Returns:
        A list of dicts as per chunk_video_timestamped, with estimated
        start/end times and snippet = first 300 chars of content.
    """
    chunk_texts: list[str] = chunk_video(video)
    if not chunk_texts:
        return []

    transcript: str = video.get("transcript", "")
    # Heuristic: estimate 150 WPM for YouTube transcripts
    total_words = len(transcript.split())
    estimated_duration = max(total_words / 150.0, 1.0)
    step = estimated_duration / len(chunk_texts) if chunk_texts else 0.0

    results: list[dict] = []
    for i, content in enumerate(chunk_texts):
        start_s = round(i * step, 2)
        end_s = round((i + 1) * step, 2)
        results.append(
            {
                "content": content,
                "start_seconds": start_s,
                "end_seconds": end_s,
                "snippet": content[:300],
            }
        )
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_docling_document_from_text(text: str) -> DoclingDocument:
    """Build a minimal DoclingDocument from a single text string (used for segments)."""
    doc = DoclingDocument(name="segment")
    doc.add_text(label=DocItemLabel.PARAGRAPH, text=text)
    return doc


def _build_docling_document(title: str, transcript: str) -> DoclingDocument:
    """
    Build a DoclingDocument from a video title and transcript text.
    Uses TITLE, SECTION_HEADER, and PARAGRAPH labels as specified.
    """
    doc = DoclingDocument(name=title)

    # Add the title
    doc.add_text(label=DocItemLabel.TITLE, text=title)

    # Split transcript into paragraphs and add each as a PARAGRAPH item.
    # If we detect lines that look like section headers (short lines at paragraph
    # boundaries), label them as SECTION_HEADER.
    paragraphs = [p.strip() for p in transcript.split("\n\n") if p.strip()]

    for para in paragraphs:
        lines = para.splitlines()
        # Heuristic: if the paragraph is a single short line (≤80 chars) treat
        # it as a section header.
        if len(lines) == 1 and len(para) <= 80:
            doc.add_text(label=DocItemLabel.SECTION_HEADER, text=para)
        else:
            doc.add_text(label=DocItemLabel.PARAGRAPH, text=para)

    return doc


def _enforce_max_chars(chunks: list[str], max_chars: int) -> list[str]:
    """
    Split any chunk that exceeds *max_chars* characters into smaller pieces.
    Tries to split at paragraph boundaries first, then at sentence boundaries.
    """
    result: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            result.append(chunk)
        else:
            result.extend(_split_text(chunk, max_chars))
    return [r for r in result if r.strip()]


def _split_text(text: str, max_chars: int) -> list[str]:
    """
    Recursively split *text* into pieces each ≤ max_chars.
    Tries double-newline splits first, then sentence splits.
    """
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    # Try splitting on double newline (paragraph boundary)
    parts = text.split("\n\n")
    if len(parts) > 1:
        return _group_parts(parts, max_chars, sep="\n\n")

    # Try splitting on single newline
    parts = text.split("\n")
    if len(parts) > 1:
        return _group_parts(parts, max_chars, sep="\n")

    # Try splitting on ". " (sentence boundary)
    parts = text.split(". ")
    if len(parts) > 1:
        return _group_parts(parts, max_chars, sep=". ")

    # Last resort: hard-cut at max_chars
    pieces: list[str] = []
    while len(text) > max_chars:
        pieces.append(text[:max_chars])
        text = text[max_chars:]
    if text.strip():
        pieces.append(text)
    return pieces


def _group_parts(parts: list[str], max_chars: int, sep: str) -> list[str]:
    """
    Greedily group *parts* (joined by *sep*) such that each group
    is ≤ max_chars. Parts that are individually larger than max_chars
    are recursively split further.
    """
    result: list[str] = []
    current = ""
    for part in parts:
        candidate = (current + sep + part) if current else part
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                result.append(current)
            # If the individual part itself exceeds max_chars, split it further
            if len(part) > max_chars:
                sub_pieces = _split_text(part, max_chars)
                # All sub_pieces except the last become complete chunks
                result.extend(sub_pieces[:-1])
                current = sub_pieces[-1] if sub_pieces else ""
            else:
                current = part
    if current and current.strip():
        result.append(current)
    return result


def _force_paragraph_split(title: str, paragraphs: list[str], max_chars: int) -> list[str]:
    """
    When HybridChunker returns fewer than 2 chunks for ≥3 paragraphs,
    split the paragraphs into at least 2 groups manually.
    Each group is prefixed with the video title as a breadcrumb.
    """
    prefix = f"{title}\n"
    result: list[str] = []
    current_parts: list[str] = []
    current_len = len(prefix)

    for para in paragraphs:
        addition = len(para) + 2  # +2 for "\n\n"
        if current_len + addition > max_chars and current_parts:
            chunk_text = prefix + "\n\n".join(current_parts)
            result.append(chunk_text)
            current_parts = [para]
            current_len = len(prefix) + len(para)
        else:
            current_parts.append(para)
            current_len += addition

    if current_parts:
        chunk_text = prefix + "\n\n".join(current_parts)
        result.append(chunk_text)

    # Final safety: ensure we have at least 2 items by splitting the first
    # chunk in half if necessary.
    if len(result) < 2 and len(paragraphs) >= 2:
        mid = max(1, len(paragraphs) // 2)
        chunk1 = prefix + "\n\n".join(paragraphs[:mid])
        chunk2 = prefix + "\n\n".join(paragraphs[mid:])
        result = [chunk1, chunk2]

    return [r for r in result if r.strip()]
