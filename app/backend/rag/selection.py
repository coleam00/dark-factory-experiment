"""Selection helpers for post-retrieval processing."""

from collections import defaultdict


def apply_per_video_cap(chunks: list[dict], max_per_video: int) -> list[dict]:
    """
    Apply a per-video cap to a ranked list of chunks.

    Iterates chunks in input rank order and keeps at most ``max_per_video`` chunks
    per video_id. Relative ordering of kept chunks is preserved.

    Args:
        chunks: Ranked list of chunk dicts (each must contain ``video_id``).
        max_per_video: Maximum number of chunks to keep per video.

    Returns:
        Filtered list of chunks, respecting the per-video cap.
    """
    counts: dict[str, int] = defaultdict(int)
    result: list[dict] = []
    for chunk in chunks:
        video_id = chunk.get("video_id")
        if video_id is None:
            continue
        if counts[video_id] < max_per_video:
            counts[video_id] += 1
            result.append(chunk)
    return result
