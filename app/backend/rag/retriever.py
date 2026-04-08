"""
Cosine similarity retriever — in-process NumPy-based semantic search.

Loads all chunk embeddings from SQLite (via repository), computes cosine
similarity against a query embedding, and returns the top-K most relevant
chunks with their video metadata.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np

from backend.db import repository

logger = logging.getLogger(__name__)


async def retrieve(
    query_embedding: List[float],
    k: int = 5,
    min_score: Optional[float] = None,
) -> List[dict]:
    """
    Find the top-K chunks most similar to *query_embedding*.

    Args:
        query_embedding: A list of floats representing the query vector.
        k: Maximum number of results to return (default 5).
        min_score: Minimum cosine similarity score to include a chunk.
            Chunks below this threshold are excluded.
            Must be in the range [-1.0, 1.0].
            Defaults to config.RETRIEVAL_MIN_SCORE when None.

    Returns:
        A list of dicts (length <= k), each containing:
          - chunk_id: str
          - content: str
          - video_id: str
          - video_title: str
          - score: float (cosine similarity, -1.0 to 1.0)
        Sorted by score descending. Returns [] if the DB has no chunks
        or if no chunks meet the min_score threshold.

    Raises:
        ValueError: If min_score is outside the valid cosine similarity range
            [-1.0, 1.0].
    """
    from backend import config  # local import to avoid any circular dep risk

    if min_score is None:
        min_score = config.RETRIEVAL_MIN_SCORE

    if not -1.0 <= min_score <= 1.0:
        raise ValueError(
            f"min_score={min_score!r} is outside the valid cosine similarity range [-1.0, 1.0]"
        )

    # Load all chunks from the DB (includes deserialized embeddings)
    all_chunks = await repository.list_chunks()
    if not all_chunks:
        return []

    # Build the matrix of stored embeddings
    chunk_embeddings = np.array(
        [chunk["embedding"] for chunk in all_chunks], dtype=np.float32
    )  # shape: (N, D)

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
        score = float(scores[int(idx)])

        # Results are sorted descending; once we drop below threshold, stop.
        if score < min_score:
            break

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
                "score": score,
            }
        )

    filtered_count = top_k - len(results)
    if filtered_count > 0:
        # Log the top excluded score for threshold-tuning observability
        first_excluded_idx = int(top_indices[len(results)])
        top_excluded_score = float(scores[first_excluded_idx])
        logger.info(
            "retrieve: %d chunk(s) excluded by min_score=%.2f (top excluded score=%.4f)",
            filtered_count,
            min_score,
            top_excluded_score,
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
    return similarities.astype(np.float32)
