"""
Cosine similarity retriever — in-process NumPy-based semantic search.

Loads all chunk embeddings from SQLite (via repository), computes cosine
similarity against a query embedding, and returns the top-K most relevant
chunks with their video metadata.
"""

from __future__ import annotations

import logging

import numpy as np

from backend.db import repository

logger = logging.getLogger(__name__)

_cached_chunks: list[dict] | None = None
_cached_matrix: np.ndarray | None = None


def invalidate_embedding_cache() -> None:
    """Clear the in-memory embedding cache.

    Call this after new chunks are written to the DB so the next
    call to retrieve() reloads from the updated database.
    """
    global _cached_chunks, _cached_matrix
    _cached_chunks = None
    _cached_matrix = None
    logger.info("Embedding cache invalidated.")


async def retrieve(
    query_embedding: list[float],
    k: int = 5,
) -> list[dict]:
    """
    Find the top-K chunks most similar to *query_embedding*.

    Args:
        query_embedding: A list of floats representing the query vector.
        k: Maximum number of results to return (default 5).

    Returns:
        A list of dicts (length <= k), each containing:
          - chunk_id: str
          - content: str
          - video_id: str
          - video_title: str
          - score: float (cosine similarity, -1.0 to 1.0)
        Sorted by score descending. Returns [] if the DB has no chunks.
    """
    global _cached_chunks, _cached_matrix

    if _cached_chunks is None or _cached_matrix is None:
        all_chunks = await repository.list_chunks()
        if not all_chunks:
            return []
        _cached_chunks = all_chunks
        _cached_matrix = np.array(
            [chunk["embedding"] for chunk in _cached_chunks], dtype=np.float32
        )  # shape: (N, D)
        logger.info("Embedding cache populated: %d chunks", len(_cached_chunks))

    all_chunks = _cached_chunks
    chunk_embeddings = _cached_matrix

    query_vec = np.array(query_embedding, dtype=np.float32)  # shape: (D,)

    # Compute cosine similarity in batch
    scores = _cosine_similarity_batch(query_vec, chunk_embeddings)  # shape: (N,)

    # Gather top-K indices (descending)
    top_k = min(k, len(all_chunks))
    top_indices = np.argpartition(scores, -top_k)[-top_k:]
    top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

    # Fetch video titles (cache to avoid redundant DB calls)
    video_title_cache: dict[str, str] = {}

    results: list[dict] = []
    for idx in top_indices:
        chunk = all_chunks[int(idx)]
        video_id = chunk["video_id"]

        if video_id not in video_title_cache:
            video = await repository.get_video(video_id)
            video_title_cache[video_id] = video["title"] if video else "Unknown Video"

        results.append(
            {
                "chunk_id": chunk["id"],
                "content": chunk["content"],
                "video_id": video_id,
                "video_title": video_title_cache[video_id],
                "score": float(scores[int(idx)]),
            }
        )

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cosine_similarity_batch(
    query: np.ndarray,
    matrix: np.ndarray,
) -> np.ndarray:
    """
    Compute cosine similarity between *query* (1-D) and every row of *matrix*.

    Returns a 1-D array of similarity scores, one per row in *matrix*.
    Handles zero-norm vectors safely (returns 0.0 similarity).
    """
    query_norm = np.linalg.norm(query)
    if query_norm == 0:
        return np.zeros(len(matrix), dtype=np.float32)

    # Normalize the query once
    query_normalized = query / query_norm

    # Compute row norms for the matrix
    matrix_norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # Avoid division by zero by clamping norms
    matrix_norms = np.where(matrix_norms == 0, 1.0, matrix_norms)
    matrix_normalized = matrix / matrix_norms

    # Dot product of normalized vectors = cosine similarity
    similarities = matrix_normalized @ query_normalized  # shape: (N,)
    result: np.ndarray = similarities.astype(np.float32)
    return result
