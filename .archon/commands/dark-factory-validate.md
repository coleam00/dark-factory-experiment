---
description: Run Dark Factory validation — Python backend (ruff + mypy + pytest) + React frontend (tsc + biome + vitest).
argument-hint: (no arguments — reads $ARTIFACTS_DIR/implementation.md and git diff)
---

# Dark Factory Validation

**Workflow ID**: $WORKFLOW_ID

---

## Your Mission

Run the full DynaChat validation suite and fix any failures. DynaChat is a
Python/FastAPI backend + React/TypeScript frontend (see `CLAUDE.md` for the
authoritative command list). This validate step runs the same checks a human
would run before committing.

**Golden rule**: run each check, read the error, fix the root cause (not the
test), re-run until green. Never modify tests to make them pass — that's an
explicit CLAUDE.md violation.

---

## Phase 1: SCOPE — What Did the Implementation Touch?

The diff may be backend-only, frontend-only, docs-only, or both. Run only the
checks that apply, so we don't waste tokens re-validating untouched layers.

```bash
git diff --name-only $BASE_BRANCH...HEAD
```

Classify each changed file:

| Path prefix            | Layer     |
|------------------------|-----------|
| `app/backend/`         | backend   |
| `app/frontend/`        | frontend  |
| `*.md`, `docs/`        | docs only |
| `.github/`, `FACTORY_RULES.md`, `MISSION.md`, `CLAUDE.md` | **forbidden — see hard rules** |

**Hard rules (FACTORY_RULES.md §5 and §Protected Files):**

- If the diff touches `FACTORY_RULES.md`, `MISSION.md`, or `CLAUDE.md` — STOP and
  write a validation BLOCKED artifact. Governance files are not factory-editable.
- If the diff touches `.github/`, Dockerfiles, `.env*`, or `.archon/config.yaml` —
  STOP and write a validation BLOCKED artifact.
- If nothing under `app/` changed and only docs changed, run the `docs-only`
  fast path (see §5.4 below).

**PHASE_1_CHECKPOINT:**
- [ ] Touched layers identified
- [ ] No protected files modified

---

## Phase 2: BACKEND CHECKS (if backend layer touched)

Backend validation commands per `CLAUDE.md` §Lint, Format, Type Check and §Testing.
All commands run from the repo root (`C:\Users\colem\OpenSource\rag-youtube-chat\`
locally, or the worktree equivalent in Archon).

### 2.1 Ensure backend deps are installed

```bash
# uv sync is idempotent — fast no-op if .venv is already populated.
(cd app/backend && uv sync --all-extras)
```

All backend tool commands run from `app/backend/` so `pyproject.toml` config (ruff lint rules, mypy exclude list, pytest asyncio mode) is picked up.

### 2.2 Ruff lint

```bash
cd app/backend && uv run ruff check .
```

**If fails:**
1. Try auto-fix: `cd app/backend && uv run ruff check --fix .`
2. Re-run `cd app/backend && uv run ruff check .`
3. If still failing, manually fix the reported issues

**Record result**: Pass / Fail (fixed)

### 2.3 Ruff format check

```bash
cd app/backend && uv run ruff format --check .
```

**If fails:**
1. Auto-fix: `cd app/backend && uv run ruff format .`
2. Verify: `cd app/backend && uv run ruff format --check .`

**Record result**: Pass / Fail (fixed)

### 2.4 Mypy type check

```bash
cd app/backend && uv run mypy .
```

**If fails:**
1. Read each error carefully — prefer adding a real type annotation over `# type: ignore`
2. Fix by tightening types at the source, not by silencing mypy
3. `# type: ignore` is only acceptable when bridging an untyped third-party dep, with a comment explaining why

**Record result**: Pass / Fail (fixed)

### 2.5 Pytest

```bash
cd app/backend && uv run pytest tests -xvs
```

**If `backend/tests/` does not exist:**
- If the implementation added no tests (e.g., pure refactor, config change), record `SKIPPED - no tests`.
- If the implementation SHOULD have added tests (bug fix, feature), that's a validation failure — the implement step violated FACTORY_RULES.md §3 ("Always include tests for new features" / regression tests for bug fixes). Either add the missing tests yourself or mark validation BLOCKED with a clear reason.

**If fails:**
1. Identify which test(s) failed
2. Is it an implementation bug or test bug? Implementation bugs = fix the source. Test bugs in tests YOU just wrote = fix the test. Test bugs in pre-existing tests = **do not modify**, this likely means your change regressed something — fix the source.
3. Re-run

**Record result**: Pass ({N} tests) / Fail (fixed) / SKIPPED

**PHASE_2_CHECKPOINT:**
- [ ] Ruff lint passes
- [ ] Ruff format passes
- [ ] Mypy passes
- [ ] Pytest passes or skipped with reason

---

## Phase 3: FRONTEND CHECKS (if frontend layer touched)

Frontend validation commands per `CLAUDE.md` §Lint, Format, Type Check and §Testing.

### 3.1 Ensure frontend deps installed

```bash
if [ ! -d "app/frontend/node_modules" ]; then
  cd app/frontend && bun install
fi
```

### 3.2 TypeScript type check

```bash
cd app/frontend && bun run tsc --noEmit
```

**If fails:**
1. Read errors; fix the types. Do NOT add `any` — CLAUDE.md §Code Conventions forbids it except when bridging an untyped dep.
2. Re-run

**Record result**: Pass / Fail (fixed)

### 3.3 Biome lint + format check

```bash
cd app/frontend && bun x biome check src
```

**If fails:**
1. Try auto-fix: `bun x biome check --apply src`
2. Re-run `bun x biome check src`
3. If still failing, manually fix

**Record result**: Pass / Fail (fixed)

### 3.4 Vitest

```bash
cd app/frontend && bun run test
```

**Handling the same test-absence logic as Phase 2.5:**
- If no test script or no tests exist yet, record `SKIPPED - no tests`.
- If the implementation added tests, they must pass.
- Never modify pre-existing passing tests to absorb your changes.

**Record result**: Pass ({N} tests) / Fail (fixed) / SKIPPED

### 3.5 Frontend build sanity (light — only if vite.config.ts or tsconfig.json changed)

```bash
cd app/frontend && bun run build
```

Skip this unless the implementation touched build configuration. The type-check
in 3.2 already catches most build failures.

**PHASE_3_CHECKPOINT:**
- [ ] tsc passes
- [ ] biome passes
- [ ] vitest passes or skipped with reason
- [ ] build passes (if run)

---

## Phase 4: RAG INVARIANTS (always — cheap guard against silent regressions)

CLAUDE.md §RAG Pipeline Invariants lists behaviors that MUST NOT regress. If
the diff touches `app/backend/rag/`, `app/backend/llm/`, `app/backend/config.py`,
`app/frontend/src/hooks/useStreamingResponse.ts`, or any route handler, verify:

- [ ] Chunker still uses `docling_core HybridChunker` with `HYBRID_CHUNKER_MAX_TOKENS=512`
- [ ] Embeddings still use `openai/text-embedding-3-small` via OpenRouter
- [ ] No new vector database introduced (FAISS, Chroma, pgvector) — that's architectural
- [ ] Chat completion still uses `moonshotai/kimi-k2.6` via OpenRouter
- [ ] SSE format is unchanged: `data: <json-string>\n\n` with `event: sources` before `[DONE]`
- [ ] Citations still include title, URL, timestamp deep-link, and transcript snippet

Any regression on the above is an automatic validation FAIL — even if static
checks pass. Write the regression into the artifact and stop.

**PHASE_4_CHECKPOINT:**
- [ ] RAG invariants verified (or skipped because diff didn't touch relevant files)

---

## Phase 5: ARTIFACT — Write validation.md

Write to `$ARTIFACTS_DIR/validation.md`:

```markdown
# Validation Results

**Generated**: {YYYY-MM-DD HH:MM}
**Workflow ID**: $WORKFLOW_ID
**Status**: {ALL_PASS | FIXED | BLOCKED}
**Layers touched**: {backend, frontend, docs, or combinations}

---

## Summary

| Check            | Layer    | Result        | Details                |
|------------------|----------|---------------|------------------------|
| Ruff lint        | backend  | pass / fixed  | {N} auto-fixed         |
| Ruff format      | backend  | pass / fixed  |                        |
| Mypy             | backend  | pass / fixed  | {N} type errors fixed  |
| Pytest           | backend  | pass ({N})    |                        |
| tsc --noEmit     | frontend | pass / fixed  |                        |
| Biome            | frontend | pass / fixed  | {N} auto-fixed         |
| Vitest           | frontend | pass ({N})    |                        |
| RAG invariants   | all      | pass          | {list of invariants checked} |

---

## Files Modified During Validation

{If validation had to fix any files, list them with a one-line reason per file.}

---

## Issues Remaining

{If BLOCKED: what check failed, what was tried, what manual intervention is needed.}
```

### 5.4 Docs-only fast path

If Phase 1 determined the diff is docs-only (no files under `app/` changed),
skip Phases 2-4 entirely and write:

```markdown
# Validation Results

**Status**: ALL_PASS
**Layers touched**: docs
**Skipped**: backend + frontend checks (no source changes)

This PR is documentation-only. Static checks and tests are not applicable.
Reviewed that only `.md` files and/or `docs/` were modified.
```

**PHASE_5_CHECKPOINT:**
- [ ] `$ARTIFACTS_DIR/validation.md` written
- [ ] Status accurately reflects what ran and what passed

---

## Phase 6: OUTPUT — Report back to the workflow

### If all pass:

```markdown
## Validation Complete

**Workflow ID**: `$WORKFLOW_ID`

Backend: ruff lint / format / mypy / pytest — all pass
Frontend: tsc / biome / vitest — all pass
RAG invariants: verified

Artifact: `$ARTIFACTS_DIR/validation.md`

Next: proceed to create-pr.
```

### If blocked:

```markdown
## Validation BLOCKED

**Workflow ID**: `$WORKFLOW_ID`

### Failed check
{check-name}: {short error summary}

### What was tried
1. {attempt 1}
2. {attempt 2}

### Required action
{what needs manual intervention — or why this is a real bug in the implementation
that the implement step produced}

Artifact: `$ARTIFACTS_DIR/validation.md`
```

---

## Success Criteria

- **BACKEND_LINT_PASS**: `ruff check backend` exits 0
- **BACKEND_FORMAT_PASS**: `ruff format --check backend` exits 0
- **BACKEND_TYPE_PASS**: `mypy backend` exits 0
- **BACKEND_TESTS_PASS**: `pytest backend/tests -xvs` all green (or skipped with reason)
- **FRONTEND_TYPE_PASS**: `bun run tsc --noEmit` exits 0
- **FRONTEND_LINT_PASS**: `bun x biome check src` exits 0
- **FRONTEND_TESTS_PASS**: `bun run test` all green (or skipped with reason)
- **RAG_INVARIANTS_PASS**: no regressions per CLAUDE.md §RAG Pipeline Invariants
- **ARTIFACT_WRITTEN**: `$ARTIFACTS_DIR/validation.md` exists with accurate status
