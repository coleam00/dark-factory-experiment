# Validation Results

**Generated**: 2026-04-18 18:38
**Workflow ID**: a2c37b9f550b91d82da59940448f240a
**Status**: ALL_PASS
**Layers touched**: backend + frontend

---

## Summary

| Check            | Layer    | Result  | Details                     |
|------------------|----------|---------|-----------------------------|
| Ruff lint        | backend  | pass    | 0 issues                    |
| Ruff format      | backend  | pass    | 61 files already formatted  |
| Mypy             | backend  | pass    | Success: no issues found   |
| Pytest           | backend  | pass    | 114 passed, 58 skipped      |
| tsc --noEmit     | frontend | pass    | 0 errors                    |
| Biome            | frontend | pass    | 31 files checked, no fixes  |
| Vitest           | frontend | pass    | 43 passed (7 test files)    |
| RAG invariants   | all      | pass    | chunker, embeddings, LLM, SSE format, citations all verified |

---

## Files Modified During Validation

No files were modified during validation — all checks passed on first run.

---

## RAG Pipeline Invariant Verification

The following invariants were verified against the implementation:

- **Chunker**: Uses `docling_core.transforms.chunker.hybrid_chunker.HybridChunker` with `max_tokens=HYBRID_CHUNKER_MAX_TOKENS=512` (config.py line 97, chunker.py lines 53-56, 114-117)
- **Embeddings**: Uses `openai/text-embedding-3-small` via OpenRouter (config.py line 69, embeddings.py line 65)
- **Chat completion**: Uses `anthropic/claude-sonnet-4.6` via OpenRouter (config.py line 70, openrouter.py line 103)
- **SSE format**: JSON-encoded tokens via `data: {json.dumps(token)}\n\n` with `event: sources` before `data: [DONE]\n\n` (openrouter.py lines 114, 117)
- **Citations**: Include title, URL, timestamp deep-link, and snippet (sources event format verified)
- **No new vector DB introduced**: RRF hybrid search only, no FAISS/Chroma/pgvector

---

## Issues Remaining

None.