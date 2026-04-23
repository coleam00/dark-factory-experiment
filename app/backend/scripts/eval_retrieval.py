"""
Offline retrieval evaluation harness for the RAG pipeline.

Reports recall@5, recall@20, and MRR@10 per category against a curated
fixture of test cases. No LLM calls; runs against the live hybrid retrieval
pipeline.

Usage:
    uv run python scripts/eval_retrieval.py              # run eval
    uv run python scripts/eval_retrieval.py --baseline    # write baseline

Exit: 0 always (diagnostic only).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from statistics import mean
from typing import Any

# Ensure the backend package is on the path
# scripts/ is at app/backend/scripts/, parents[1] is app/backend/,
# but import is "from backend.rag..." so we need app/ (parents[2])
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.rag.embeddings import embed_text
from backend.rag.retriever_hybrid import retrieve_hybrid

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
FIXTURE_PATH = SCRIPT_DIR.parent / "tests" / "eval" / "retrieval_cases.json"
BASELINE_PATH = SCRIPT_DIR.parent / "tests" / "eval" / "baseline.json"
RETRIEVE_TOP_K = 20  # over-fetch for recall@20


def load_cases() -> list[dict[str, Any]]:
    """Load test cases from the fixture file."""
    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(
            f"Fixture not found at {FIXTURE_PATH}. Run from app/backend/ directory."
        )
    with open(FIXTURE_PATH) as f:
        data: dict[str, Any] = json.load(f)
    return data["cases"]  # type: ignore[no-any-return]


def load_baseline() -> dict[str, Any] | None:
    """Load baseline metrics if present."""
    if not BASELINE_PATH.exists():
        return None
    with open(BASELINE_PATH) as f:
        return json.load(f)  # type: ignore[no-any-return]


def save_baseline(metrics: dict) -> None:
    """Write current metrics as the new baseline."""
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BASELINE_PATH, "w") as f:
        json.dump(metrics, f, indent=2)


def recall_at_k(retrieved_video_ids: list[str], expected_video_ids: list[str], k: int) -> float:
    """
    Fraction of expected video IDs present in the top-k retrieved video IDs.
    Duplicates in retrieved list are collapsed before checking.
    """
    if not expected_video_ids:
        return 0.0
    unique_retrieved = list(dict.fromkeys(retrieved_video_ids))[:k]
    hits = sum(1 for vid in expected_video_ids if vid in unique_retrieved)
    return hits / len(expected_video_ids)


def mean_reciprocal_rank(
    retrieved_video_ids: list[str], expected_video_ids: list[str], k: int
) -> float:
    """
    Mean of 1/rank for each expected video ID that appears in top-k.
    Rank is 1-indexed. If none found, 0.
    """
    if not expected_video_ids:
        return 0.0
    unique_retrieved = list(dict.fromkeys(retrieved_video_ids))[:k]
    ranks = []
    for vid in expected_video_ids:
        try:
            rank = unique_retrieved.index(vid) + 1
            ranks.append(1.0 / rank)
        except ValueError:
            pass
    return mean(ranks) if ranks else 0.0


async def run_case(case: dict) -> dict:
    """
    Run a single test case through the retrieval pipeline.

    Returns a dict with computed metrics and retrieved video IDs.
    """
    case_id = case["id"]
    category = case["category"]
    expected_video_ids = case.get("expected_video_ids", [])

    query_text = case["history"][-1] if case.get("history") else case["query"]

    try:
        query_embedding = embed_text(query_text)
    except ValueError:
        # Malformed test case — re-raise so the developer fixes the fixture
        raise
    except Exception as exc:
        logger.warning("Case %s: embedding failed (%s) — counting as miss", case_id, exc)
        return _miss_result(case_id, category, expected_video_ids)

    try:
        results = await retrieve_hybrid(query_text, query_embedding, top_k=RETRIEVE_TOP_K)
    except Exception as exc:
        logger.warning("Case %s: retrieval failed (%s) — counting as miss", case_id, exc)
        return _miss_result(case_id, category, expected_video_ids)

    retrieved_video_ids = [r["video_id"] for r in results]

    return {
        "id": case_id,
        "category": category,
        "expected_video_ids": expected_video_ids,
        "retrieved_video_ids": retrieved_video_ids,
        "recall5": recall_at_k(retrieved_video_ids, expected_video_ids, k=5),
        "recall20": recall_at_k(retrieved_video_ids, expected_video_ids, k=20),
        "mrr10": mean_reciprocal_rank(retrieved_video_ids, expected_video_ids, k=10),
    }


def _miss_result(case_id: str, category: str, expected_video_ids: list[str]) -> dict:
    """Return a 'miss' result dict for a case that could not be evaluated."""
    return {
        "id": case_id,
        "category": category,
        "expected_video_ids": expected_video_ids,
        "retrieved_video_ids": [],
        "recall5": 0.0,
        "recall20": 0.0,
        "mrr10": 0.0,
    }


def compute_category_metrics(cases: list[dict], category: str) -> dict:
    """Aggregate metrics for a category."""
    cat_cases = [c for c in cases if c["category"] == category]
    if not cat_cases:
        return {"recall5": 0.0, "recall20": 0.0, "mrr10": 0.0, "n": 0}

    # out_of_scope cases with empty expected_video_ids count as 0 for all metrics
    recall5_vals = [c["recall5"] for c in cat_cases]
    recall20_vals = [c["recall20"] for c in cat_cases]
    mrr10_vals = [c["mrr10"] for c in cat_cases]

    return {
        "recall5": mean(recall5_vals),
        "recall20": mean(recall20_vals),
        "mrr10": mean(mrr10_vals),
        "n": len(cat_cases),
    }


async def main(args: argparse.Namespace) -> None:
    print("Loading test cases...")
    cases = load_cases()
    print(f"Loaded {len(cases)} cases")

    baseline = load_baseline()

    print("Running retrieval evaluation (this may take a while)...")
    results: list[dict] = []
    for case in cases:
        result = await run_case(case)
        results.append(result)
        # progress dot
        print(".", end="", flush=True)
    print()

    categories = ["narrow_single_video", "broad_cross_video", "follow_up", "out_of_scope"]

    print()
    print("=" * 75)
    header = f"{'Category':<22} {'R@5':>6} {'R@20':>6} {'MRR@10':>8} {'N':>4}"
    if baseline:
        header += f" {'ΔR@5':>7} {'ΔR@20':>7} {'ΔM@10':>7}"
    print(header)
    print("-" * 75)

    overall: dict[str, list[float]] = {"recall5": [], "recall20": [], "mrr10": []}

    for cat in categories:
        metrics = compute_category_metrics(results, cat)
        row = f"{cat:<22} {metrics['recall5']:>6.3f} {metrics['recall20']:>6.3f} {metrics['mrr10']:>8.3f} {metrics['n']:>4}"
        if baseline and cat in baseline:
            bl = baseline[cat]
            delta_r5 = metrics["recall5"] - bl["recall5"]
            delta_r20 = metrics["recall20"] - bl["recall20"]
            delta_mrr = metrics["mrr10"] - bl["mrr10"]
            row += f" {delta_r5:>+7.3f} {delta_r20:>+7.3f} {delta_mrr:>+7.3f}"
        print(row)
        overall["recall5"].append(metrics["recall5"] * metrics["n"])
        overall["recall20"].append(metrics["recall20"] * metrics["n"])
        overall["mrr10"].append(metrics["mrr10"] * metrics["n"])

    total_n = sum(compute_category_metrics(results, cat)["n"] for cat in categories)

    overall_recall5 = sum(overall["recall5"]) / total_n if total_n else 0.0
    overall_recall20 = sum(overall["recall20"]) / total_n if total_n else 0.0
    overall_mrr10 = sum(overall["mrr10"]) / total_n if total_n else 0.0

    row = f"{'OVERALL':<22} {overall_recall5:>6.3f} {overall_recall20:>6.3f} {overall_mrr10:>8.3f} {total_n:>4}"
    if baseline:
        bl_overall = baseline.get("overall", {})
        delta_r5 = overall_recall5 - bl_overall.get("recall5", 0)
        delta_r20 = overall_recall20 - bl_overall.get("recall20", 0)
        delta_mrr = overall_mrr10 - bl_overall.get("mrr10", 0)
        row += f" {delta_r5:>+7.3f} {delta_r20:>+7.3f} {delta_mrr:>+7.3f}"
    print("-" * 75)
    print(row)
    print("=" * 75)

    if args.baseline:
        current_metrics = {}
        for cat in categories:
            current_metrics[cat] = compute_category_metrics(results, cat)
        current_metrics["overall"] = {
            "recall5": overall_recall5,
            "recall20": overall_recall20,
            "mrr10": overall_mrr10,
        }
        save_baseline(current_metrics)
        print(f"\nBaseline written to {BASELINE_PATH}")

    print("\nDone. Exit 0.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Offline RAG retrieval evaluation harness.")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Write current metrics as the baseline for future delta comparisons.",
    )
    args = parser.parse_args()
    asyncio.run(main(args))
