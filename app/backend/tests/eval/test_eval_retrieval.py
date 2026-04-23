"""Tests for eval_retrieval.py metric and utility functions."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.scripts.eval_retrieval import (
    compute_category_metrics,
    load_baseline,
    load_cases,
    mean_reciprocal_rank,
    recall_at_k,
    run_case,
    save_baseline,
)

# ----------------------------------------------------------------------
# recall_at_k
# ----------------------------------------------------------------------


class TestRecallAtK:
    def test_returns_0_when_no_expected(self):
        assert recall_at_k(["v1", "v2"], [], k=5) == 0.0

    def test_returns_1_when_retrieved_contains_expected(self):
        assert recall_at_k(["v1", "v2"], ["v1"], k=5) == 1.0

    def test_returns_fraction_when_partial_match(self):
        # expected: [v1, v2], retrieved: [v1, v3] → 1/2 = 0.5
        assert recall_at_k(["v1", "v3"], ["v1", "v2"], k=5) == 0.5

    def test_deduplicates_retrieved_before_checking(self):
        # [v1, v1, v2], expected: [v1] → hits=1/1=1.0 (dupe v1 counted once)
        assert recall_at_k(["v1", "v1", "v2"], ["v1"], k=5) == 1.0

    def test_respects_k_limit(self):
        # retrieved has v1 at index 5 but k=3 → not in top-3
        assert recall_at_k(["x", "y", "z", "v1", "a", "b"], ["v1"], k=3) == 0.0

    def test_empty_retrieved_returns_0(self):
        assert recall_at_k([], ["v1"], k=5) == 0.0

    def test_both_empty_returns_0(self):
        assert recall_at_k([], [], k=5) == 0.0

    def test_hits_counted_correctly_with_multiple_expected(self):
        # [v1, v2, v3], expected: [v1, v3] → 2/2 = 1.0
        assert recall_at_k(["v1", "v2", "v3"], ["v1", "v3"], k=5) == 1.0

    def test_truncation_at_exactly_k(self):
        # v1 at index 4 (0-indexed), k=5 → should be included
        assert recall_at_k(["a", "b", "c", "d", "v1"], ["v1"], k=5) == 1.0


# ----------------------------------------------------------------------
# mean_reciprocal_rank
# ----------------------------------------------------------------------


class TestMeanReciprocalRank:
    def test_returns_0_when_no_expected(self):
        assert mean_reciprocal_rank(["v1"], [], k=10) == 0.0

    def test_returns_1_when_found_at_rank_1(self):
        assert mean_reciprocal_rank(["v1", "v2"], ["v1"], k=10) == 1.0

    def test_returns_half_when_found_at_rank_2(self):
        assert mean_reciprocal_rank(["v1", "v2"], ["v2"], k=10) == 0.5

    def test_returns_partial_mrr_with_mixed_hits(self):
        # v1 at rank 1 (1.0), v2 not found (0) → mean = 1.0 (only v1 contributes)
        result = mean_reciprocal_rank(["v1", "x"], ["v1", "v2"], k=10)
        assert result == 1.0

    def test_empty_retrieved_returns_0(self):
        assert mean_reciprocal_rank([], ["v1"], k=10) == 0.0

    def test_both_empty_returns_0(self):
        assert mean_reciprocal_rank([], [], k=10) == 0.0

    def test_multiple_hits_aggregated(self):
        # v1 at rank 1 (1.0), v3 at rank 3 (1/3 ≈ 0.333) → mean ≈ 0.667
        result = mean_reciprocal_rank(["v1", "v2", "v3"], ["v1", "v3"], k=10)
        assert result == pytest.approx((1.0 + 1.0 / 3.0) / 2.0)

    def test_respects_k_limit(self):
        # v1 at rank 6, k=3 → not in top-k → 0
        result = mean_reciprocal_rank(["a", "b", "c", "d", "e", "v1"], ["v1"], k=3)
        assert result == 0.0

    def test_exact_hit_at_boundary_k(self):
        # v1 at rank 5, k=5 → included (1/5 = 0.2)
        result = mean_reciprocal_rank(["a", "b", "c", "d", "v1"], ["v1"], k=5)
        assert result == 0.2


# ----------------------------------------------------------------------
# load_cases
# ----------------------------------------------------------------------


class TestLoadCases:
    def test_raises_filenotfound_for_missing_fixture(self, tmp_path):
        import backend.scripts.eval_retrieval as sut

        original = sut.FIXTURE_PATH
        try:
            sut.FIXTURE_PATH = tmp_path / "nonexistent.json"
            with pytest.raises(FileNotFoundError):
                load_cases()
        finally:
            sut.FIXTURE_PATH = original

    def test_loads_valid_fixture(self, tmp_path):
        cases_file = tmp_path / "cases.json"
        cases_file.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "id": "c1",
                            "query": "test",
                            "expected_video_ids": [],
                            "category": "out_of_scope",
                        }
                    ]
                }
            )
        )
        import backend.scripts.eval_retrieval as sut

        original = sut.FIXTURE_PATH
        try:
            sut.FIXTURE_PATH = cases_file
            cases = load_cases()
            assert len(cases) == 1
            assert cases[0]["id"] == "c1"
        finally:
            sut.FIXTURE_PATH = original


# ----------------------------------------------------------------------
# load_baseline / save_baseline
# ----------------------------------------------------------------------


class TestBaseline:
    def test_load_baseline_returns_none_when_missing(self, tmp_path):
        import backend.scripts.eval_retrieval as sut

        original = sut.BASELINE_PATH
        try:
            sut.BASELINE_PATH = tmp_path / "nonexistent.json"
            result = load_baseline()
            assert result is None
        finally:
            sut.BASELINE_PATH = original

    def test_save_and_load_baseline_roundtrip(self, tmp_path):
        import backend.scripts.eval_retrieval as sut

        original = sut.BASELINE_PATH
        try:
            sut.BASELINE_PATH = tmp_path / "baseline.json"
            metrics = {
                "narrow_single_video": {"recall5": 0.8, "recall20": 0.9, "mrr10": 0.7, "n": 10}
            }
            save_baseline(metrics)
            loaded = load_baseline()
            assert loaded == metrics
        finally:
            sut.BASELINE_PATH = original


# ----------------------------------------------------------------------
# compute_category_metrics
# ----------------------------------------------------------------------


class TestComputeCategoryMetrics:
    def test_empty_category_returns_zeros(self):
        result = compute_category_metrics([], "narrow_single_video")
        assert result == {"recall5": 0.0, "recall20": 0.0, "mrr10": 0.0, "n": 0}

    def test_aggregates_recall5_across_cases(self):
        cases = [
            {"recall5": 1.0, "recall20": 1.0, "mrr10": 1.0, "category": "narrow_single_video"},
            {"recall5": 0.0, "recall20": 0.0, "mrr10": 0.0, "category": "narrow_single_video"},
        ]
        result = compute_category_metrics(cases, "narrow_single_video")
        assert result["recall5"] == 0.5
        assert result["n"] == 2

    def test_nonexistent_category_returns_zeros(self):
        result = compute_category_metrics(
            [{"category": "other", "recall5": 1.0, "recall20": 1.0, "mrr10": 1.0}],
            "narrow_single_video",
        )
        assert result == {"recall5": 0.0, "recall20": 0.0, "mrr10": 0.0, "n": 0}


# ----------------------------------------------------------------------
# run_case
# ----------------------------------------------------------------------


class TestRunCase:
    @pytest.mark.asyncio
    async def test_returns_miss_result_on_embedding_failure(self):
        case = {
            "id": "test-1",
            "query": "test query",
            "history": [],
            "expected_video_ids": ["v1"],
            "category": "narrow_single_video",
        }
        with patch("backend.scripts.eval_retrieval.embed_text") as mock_embed:
            mock_embed.side_effect = RuntimeError("API failed")
            result = await run_case(case)
            assert result["id"] == "test-1"
            assert result["recall5"] == 0.0
            assert result["retrieved_video_ids"] == []

    @pytest.mark.asyncio
    async def test_returns_miss_result_on_retrieval_failure(self):
        case = {
            "id": "test-2",
            "query": "test query",
            "history": [],
            "expected_video_ids": ["v1"],
            "category": "narrow_single_video",
        }
        with (
            patch("backend.scripts.eval_retrieval.embed_text") as mock_embed,
            patch("backend.scripts.eval_retrieval.retrieve_hybrid") as mock_retrieve,
        ):
            mock_embed.return_value = [0.1] * 1536
            mock_retrieve.side_effect = RuntimeError("DB error")
            result = await run_case(case)
            assert result["id"] == "test-2"
            assert result["recall5"] == 0.0

    @pytest.mark.asyncio
    async def test_returns_correct_metrics_on_success(self):
        case = {
            "id": "test-3",
            "query": "test query",
            "history": [],
            "expected_video_ids": ["v1"],
            "category": "narrow_single_video",
        }
        with (
            patch("backend.scripts.eval_retrieval.embed_text") as mock_embed,
            patch("backend.scripts.eval_retrieval.retrieve_hybrid") as mock_retrieve,
        ):
            mock_embed.return_value = [0.1] * 1536
            mock_retrieve.return_value = [
                {"video_id": "v1", "score": 0.9, "transcript_snippet": "test"},
                {"video_id": "v2", "score": 0.8, "transcript_snippet": "test2"},
            ]
            result = await run_case(case)
            assert result["id"] == "test-3"
            assert result["retrieved_video_ids"] == ["v1", "v2"]
            assert result["recall5"] == 1.0

    @pytest.mark.asyncio
    async def test_uses_history_over_query_when_available(self):
        case = {
            "id": "test-4",
            "query": "original query",
            "history": ["follow-up question"],
            "expected_video_ids": [],
            "category": "follow_up",
        }
        used_query = None

        def capture_embed(text):
            nonlocal used_query
            used_query = text
            return [0.1] * 1536

        with (
            patch("backend.scripts.eval_retrieval.embed_text", side_effect=capture_embed),
            patch(
                "backend.scripts.eval_retrieval.retrieve_hybrid", new_callable=AsyncMock
            ) as mock_retrieve,
        ):
            mock_retrieve.return_value = []
            await run_case(case)
            assert used_query == "follow-up question"
