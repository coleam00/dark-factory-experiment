# CLAUDE.md

Instructions for AI coding agents working in this repository. Read this before making any code changes.

This file covers **how the code is written**. For *what* to build, see `MISSION.md`. For *how the factory operates*, see `FACTORY_RULES.md`. When this file and those conflict, MISSION.md wins on scope, FACTORY_RULES.md wins on process, and CLAUDE.md wins on code style.

---

## Project Overview

**DynaChat** is a RAG-powered chat interface that lets viewers query a single YouTube channel's content and get streaming answers with per-chunk citations that deep-link to the exact timestamp in the source video. FastAPI + Python backend, React + Vite + TypeScript frontend, SQLite (migrating to Postgres eventually).

---

## Tech Stack

**Backend**
- Python 3.11+ (not specified in any lockfile; don't rely on 3.12+ features)
- `uv` for package management (not pip, not poetry) — `backend/pyproject.toml` is the dependency source of truth, `backend/uv.lock` pins exact versions
- FastAPI with `uvicorn[standard]` ASGI server
- `aiosqlite` for async SQLite access (no ORM — raw SQL through `db/repository.py`)
- `docling-core[chunking]` for transcript chunking (HybridChunker)
- `openai` SDK pointed at OpenRouter's OpenAI-compatible endpoint (for both embeddings and chat completions)
- `numpy` for in-process cosine similarity
- `python-dotenv` for config loading

**Frontend**
- Bun (not npm, not pnpm — use `bun install`, `bun run dev`, `bun run build`)
- React 18.3, TypeScript 5.4, Vite 5.2
- `react-router-dom` v6 for routing
- `react-markdown` + `remark-gfm` for assistant message rendering
- `react-syntax-highlighter` for code blocks
- Tailwind CSS 3.4 (no component library — components are built from Tailwind primitives)
- Vanilla `fetch()` for API calls (no axios, no SDK) — typed wrappers in `src/lib/api.ts`

---

## Repo Layout

```
rag-youtube-chat/
├── MISSION.md               # Product scope — factory reads this at triage
├── FACTORY_RULES.md         # Factory operational rules — every workflow reads this
├── CLAUDE.md                # This file — code conventions
├── README.md                # Human-facing quick start
├── spec.md                  # Product/design spec (reference, not governance)
├── app/
│   ├── start.sh             # POSIX bootstrap: venv → pip install → uvicorn + bun dev
│   ├── start.bat            # Windows equivalent
│   ├── backend/
│   │   ├── main.py          # FastAPI app factory, lifespan init, /api/health
│   │   ├── config.py        # All env var reads + hardcoded constants
│   │   ├── pyproject.toml   # uv dependencies + tool config (ruff, mypy, pytest)
│   │   ├── uv.lock          # uv lockfile (committed, pinned versions)
│   │   ├── data/
│   │   │   ├── chat.db      # SQLite database (auto-created, gitignored)
│   │   │   └── seed.py      # 10 mock videos seeded on first startup
│   │   ├── db/
│   │   │   ├── schema.py    # CREATE TABLE IF NOT EXISTS, PRAGMAs, init_db()
│   │   │   └── repository.py # ALL raw SQL lives here — nowhere else
│   │   ├── llm/
│   │   │   └── openrouter.py # stream_chat() async generator, SSE-formatted output
│   │   ├── rag/
│   │   │   ├── chunker.py    # Docling HybridChunker wrapper
│   │   │   ├── embeddings.py # embed_text / embed_batch via OpenRouter
│   │   │   └── retriever.py  # NumPy cosine similarity top-k
│   │   └── routes/
│   │       ├── conversations.py # GET/POST/DELETE /api/conversations*, GET /api/videos
│   │       ├── messages.py      # POST /api/conversations/{id}/messages (streaming SSE)
│   │       ├── ingest.py        # POST /api/ingest
│   │       └── version.py       # GET /api/version
│   └── frontend/
│       ├── package.json      # Bun dependencies + scripts
│       ├── vite.config.ts    # Dev server port, API proxy to backend
│       ├── tsconfig.json
│       ├── index.html
│       └── src/
│           ├── main.tsx      # React root
│           ├── App.tsx       # BrowserRouter + layout
│           ├── components/   # ChatArea, Sidebar, Message, MarkdownRenderer, ChatInput, VideoExplorer, ToastProvider
│           ├── hooks/        # useConversations, useMessages, useStreamingResponse, useToast
│           ├── lib/
│           │   └── api.ts    # All typed fetch wrappers + TypeScript interfaces
│           └── styles/
│               └── globals.css # Tailwind imports
└── .archon/
    └── config.yaml           # Per-codebase Archon env (GITIGNORED — holds MiniMax auth token)
```

**Placement rules** (where new files go):

- New API routes → new file in `app/backend/routes/`, one file per resource. Mount from `main.py`.
- New SQL queries → `app/backend/db/repository.py` only. Never write SQL in route handlers, services, or components.
- New schema changes → `app/backend/db/schema.py`. For the current SQLite phase, use `CREATE TABLE IF NOT EXISTS` with portable SQL. See "Database" section for the Postgres-portability rules you must follow *now*.
- New RAG pipeline steps → `app/backend/rag/`. Keep chunker, embeddings, and retriever as separate modules.
- New React components → `app/frontend/src/components/`, one component per file, named exports matching filename.
- New React hooks → `app/frontend/src/hooks/`, prefix with `use`.
- New API client functions → `app/frontend/src/lib/api.ts`. Keep all fetch calls in this one file.

---

## Running the App

Install and start everything (backend venv + deps, frontend deps, both dev servers):

```bash
cd app
./start.sh         # POSIX
start.bat          # Windows
```

Manual backend:

```bash
cd app/backend
uv sync --all-extras                   # creates backend/.venv, installs runtime + dev deps
cd ..
uv --project backend run uvicorn backend.main:app --reload --port 8000
```

Backend **must** be run from `app/` (not `app/backend/`) — the `backend.main:app` import path requires it. Running from the wrong cwd gives `ModuleNotFoundError: No module named 'backend'`. The `--project backend` flag tells uv to use `app/backend/.venv` while cwd is `app/`.

Manual frontend:

```bash
cd app/frontend
bun install
bun run dev           # dev server with HMR
bun run build         # production build → dist/
bun run preview       # serve built assets
```

---

## Testing

**Current state: no tests exist yet.** When you add them, use the tools below. The factory's agent-browser regression test (see FACTORY_RULES.md §4) is a separate end-to-end gate and does not replace unit/integration tests.

**Python backend:**

```bash
cd app/backend
uv run pytest tests -xvs
```

All backend tool invocations run from `app/backend/` so that `pyproject.toml` (which holds ruff, mypy, pytest config) is picked up. Running the tools from `app/` with `--project backend` works for package resolution but mypy/pytest **do not** auto-discover config from a non-cwd project, so you'd silently lose the exclude lists and asyncio mode.

- Test directory: `app/backend/tests/` (already exists with skeleton `__init__.py` + `conftest.py`)
- `pytest`, `pytest-asyncio`, and `httpx` are declared in `backend/pyproject.toml` under `[project.optional-dependencies].dev` — installed by `uv sync --all-extras`
- Use `pytest-asyncio` for async tests (`asyncio_mode = "auto"` is set in `pyproject.toml`, so plain `async def` test functions work)
- Use `httpx.AsyncClient` against a test FastAPI app for integration tests
- SQLite tests use a separate temp database; never touch `app/backend/data/chat.db`

**TypeScript frontend:**

```bash
cd app/frontend
bun add -D vitest @testing-library/react @testing-library/jest-dom jsdom
bun run test
```

- Test directory: `app/frontend/src/__tests__/` or co-located `*.test.tsx` files
- Use Vitest (not Jest — Vite-native, faster)
- Mock `fetch` with `vi.stubGlobal('fetch', ...)` for hook tests

---

## Lint, Format, Type Check

**Backend tooling is configured in `app/backend/pyproject.toml`:** ruff (lint + format, line-length 100, target py311, conservative rule set — E/F/W/I/B/UP/SIM/RUF), mypy (lenient `strict = false`, `warn_return_any = true`, `ignore_missing_imports = true`), pytest (asyncio auto mode). All three are in the `dev` optional-dependency group and installed by `uv sync --all-extras`.

**Python:**

```bash
cd app/backend
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

**TypeScript:** `eslint` + `prettier` or the combined `biome`. Prefer `biome` (one tool, fast).

```bash
cd app/frontend
bun x biome check src
bun x biome format --write src
bun run tsc --noEmit           # type check
```

**Before every commit (what the validator runs):**

```bash
# Backend (from app/backend/)
cd app/backend
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest tests -xvs
cd ../..

# Frontend
cd app/frontend
bun run tsc --noEmit
bun x biome check src
bun run test
```

---

## Code Conventions

### Python (backend)

- **Async everywhere.** FastAPI routes are `async def`. Database calls use `aiosqlite`. Any sync blocking call (file I/O, CPU work) in a route handler is a bug — use `asyncio.to_thread` or move it to a background task.
- **Imports:** stdlib first, third-party second, local third. Group with blank lines. No wildcard imports.
- **Type hints:** use them on every function signature and return type. Use `list[str]` / `dict[str, int]` syntax (Python 3.9+), not `List` / `Dict` from `typing`.
- **No `print()` in runtime code.** Use `logging` with a module-level logger: `logger = logging.getLogger(__name__)`. `print()` is acceptable in `data/seed.py` and one-off scripts.
- **Errors:** raise specific exceptions (`ValueError`, `KeyError`, custom) with clear messages. Never `except:` bare. Avoid `except Exception` except at the outermost request handler, where FastAPI's exception handlers take over.
- **SQL:** all queries live in `db/repository.py`. Parameterize — never use f-strings or `%` formatting to build SQL. `aiosqlite` uses `?` placeholders.
- **Config:** every environment variable is read exactly once in `config.py` and exposed as a module-level constant. Routes and services import the constant, never `os.environ` directly.
- **Pydantic models:** use `pydantic.BaseModel` for request/response schemas, defined in the route file that uses them (unless shared).

### TypeScript (frontend)

- **Function components only.** No class components. Named exports, one component per file. File name matches component name.
- **Hooks for state and effects.** Custom hooks live in `src/hooks/`, prefixed `use`, returning a typed object.
- **All API calls go through `src/lib/api.ts`.** Components and hooks import from there. Never `fetch()` inline in a component.
- **Types:** every function signature typed; no `any` except when bridging an untyped dependency with a clear comment explaining why. Prefer `interface` for object shapes, `type` for unions and aliases.
- **Imports:** use relative paths within `src/` (no path aliases configured currently). External libraries first, then internal.
- **Styling:** Tailwind utility classes only. No inline `style={{...}}` except for dynamic values that can't be expressed in Tailwind. No CSS modules, no styled-components.
- **Event handlers:** typed callbacks (`(e: React.ChangeEvent<HTMLInputElement>) => void`), not `any`.
- **State:** React built-ins (`useState`, `useReducer`, Context) only. Do not add Redux, Zustand, Jotai, or any external state library — it's out of scope.
- **SSE parsing:** all SSE consumption goes through `useStreamingResponse`. Do not parse SSE in components or new hooks.

---

## Database

**Current state:** SQLite via `aiosqlite`. Database file at `app/backend/data/chat.db`, auto-created on first startup via `backend.db.schema.init_db()` called from the FastAPI lifespan handler. No ORM. No Alembic migrations. Schema is `CREATE TABLE IF NOT EXISTS` against literal SQL in `schema.py`.

**Tables:** `videos`, `chunks` (FK → videos), `conversations`, `messages` (FK → conversations). `PRAGMA foreign_keys=ON` and `PRAGMA journal_mode=WAL` set at connection time.

### Planned migration: Postgres

The factory is **allowed** to work on the Postgres migration when an issue is filed for it (this is one of the few architectural changes explicitly permitted by MISSION.md). Until then, write all new code in a Postgres-portable way so the eventual migration is a drop-in swap, not a rewrite.

**Rules for new database code today:**

1. **Only use SQL that is portable across SQLite and Postgres.** No `json_extract()`, no `strftime()`, no SQLite-specific pragmas baked into queries. Stick to ANSI SQL.
2. **Never rely on SQLite's permissive typing.** Put explicit `NOT NULL`, `CHECK`, and type constraints on every new column.
3. **Use ISO 8601 strings for timestamps**, not SQLite's `DATETIME`. Both databases round-trip ISO strings cleanly; `DATETIME` does not.
4. **Primary keys are text UUIDs** (already the convention — keep it that way). Do not add auto-increment integer PKs; they'll complicate the Postgres migration.
5. **When the migration happens**, `aiosqlite` will be swapped for `asyncpg` or `psycopg[binary]`. Keep all SQL in `db/repository.py` so the swap is confined to one file.
6. **Do not introduce Alembic yet.** When the migration starts, the first PR of that effort introduces Alembic, generates an initial migration from the existing schema, and vendors it. Until then, `schema.py`'s `CREATE TABLE IF NOT EXISTS` remains authoritative.

---

## RAG Pipeline Invariants

These behaviors are part of DynaChat's contract and must not regress. The agent-browser regression test verifies most of them.

1. **Chunking** uses Docling `HybridChunker` with `max_tokens=512` (`HYBRID_CHUNKER_MAX_TOKENS` in `config.py`). Do not swap to recursive-character splitters or LangChain chunkers.
2. **Embeddings** come from OpenRouter's `openai/text-embedding-3-small` (1536-dim). Never call a different embedding model or provider. Never embed on the frontend.
3. **Retrieval** is in-process NumPy cosine similarity over all chunks, top-k = 5. This is acceptable until the library grows large. Do not introduce a vector database (FAISS, Chroma, pgvector) without an explicit issue authorizing it — that's an architectural change requiring human approval.
4. **Chat completion** uses OpenRouter's `anthropic/claude-sonnet-4.6` via the `openai` SDK pointed at `https://openrouter.ai/api/v1`. Do not change the provider or the model in a PR — that's out of scope per MISSION.md.
5. **Streaming format:** Server-Sent Events with JSON-encoded tokens. Each token is framed as `data: <json-string>\n\n`. The `sources` event is emitted as `event: sources\ndata: <json-array>\n\n` **before** the `data: [DONE]\n\n` terminator. Do not change this format — the frontend parser in `useStreamingResponse.ts` depends on it exactly.
6. **Citations** must include video title, video URL, exact timestamp deep-link, and the quoted transcript snippet. The citation modal opens an embedded YouTube player at the timestamp. This is a MISSION.md quality bar — removing or regressing any of these fields is an auto-reject.

---

## Environment Variables

All env var reads happen in `app/backend/config.py`. Add new variables there and import the constant elsewhere.

| Variable | Required | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | **yes** | Authenticates embeddings and chat completions to OpenRouter |

Everything else is currently hardcoded in `config.py` (model names, ports, chunk size, top-k). When adding configurability, add the constant to `config.py` with a sensible default:

```python
NEW_CONSTANT: int = int(os.environ.get("NEW_CONSTANT", "42"))
```

**Never commit `.env` files.** `.env`, `.env.*`, and `.archon/config.yaml` are in `.gitignore` and on the protected files list in `FACTORY_RULES.md`.

---

## Known Footguns (Fix These When You Touch Them)

These are existing bugs / quirks in the repo. They are fair game for the factory to fix when an issue is filed, but be aware of them so you don't accidentally depend on the broken behavior:

1. **Port mismatch in `vite.config.ts`.** The dev server listens on port 5175 (`vite.config.ts`) but `config.py` reports `FRONTEND_PORT = 5173`. The API proxy target in `vite.config.ts` is also wrong — it points at port 8001 while the backend runs on 8000. If you touch `vite.config.ts` or `config.py`, fix both to agree.
2. **Backend venv is committed to git.** `app/backend/venv/` should be in `.gitignore` but isn't. Do not remove it in an unrelated PR — file a separate issue. (The factory's implementation rules forbid "improvements" beyond the issue scope.) Note: the new uv-managed venv lives at `app/backend/.venv/` (dotted) and IS gitignored; only the legacy `app/backend/venv/` (no dot) remains checked in and should be cleaned up in a dedicated PR.
3. **Runtime dependencies are unpinned in `pyproject.toml`** but pinned in `uv.lock`. Do not add upper bounds to `[project].dependencies` in an unrelated PR — uv's lockfile handles reproducibility already.
4. **No `.env.example`** exists. When authorized, create one listing every variable from `config.py` with placeholder values and comments.
5. **SSE tokens are JSON-encoded** (wrapped in quotes, escaped newlines). This is non-standard but intentional — it safely handles tokens containing newlines. The parser in `useStreamingResponse.ts` expects this exact format. Do not switch to raw-text SSE without updating both sides.

---

## Commit and PR Conventions

- **Commit messages:** conventional commits style — `feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `test:`. Subject line under 72 characters. Body explains *why*, not *what*.
- **PR title:** same conventional-commits prefix as the first commit. Under 72 characters.
- **PR body:** must include `Fixes #N` (or `Closes #N` / `Resolves #N`) on its own line so the validator can extract the linked issue. Missing this link causes validation to fail at behavioral-validation.
- **New dependencies:** PR body must include a "Dependencies" section explaining what the dependency does, why existing dependencies don't work, and evidence of active maintenance. See FACTORY_RULES.md §2.
- **One issue per PR.** Do not bundle unrelated fixes. If you notice a bug while working on something else, file a new issue rather than fixing it in the current PR.

---

## Dos and Don'ts (Quick Reference)

**Do:**
- Read MISSION.md and FACTORY_RULES.md before starting any non-trivial task
- Run the full validation suite (tests, lint, typecheck, agent-browser regression) before declaring a PR done
- Keep all SQL in `db/repository.py`
- Keep all fetch calls in `src/lib/api.ts`
- Use portable SQL only (Postgres migration is coming)
- Add tests for every bug fix (regression test) and every new feature

**Don't:**
- Modify `MISSION.md`, `FACTORY_RULES.md`, or `CLAUDE.md` (this file) — see FACTORY_RULES.md §5
- Modify `.github/`, Dockerfiles, deployment configs, or `.env*` files
- Introduce a new LLM provider, embedding model, or vector database
- Add state management libraries to the frontend
- Add an ORM to the backend
- Write SQL outside `db/repository.py` or fetch calls outside `src/lib/api.ts`
- Use SQLite-specific SQL functions — anything written today must work on Postgres tomorrow
- "Improve" code that wasn't part of the issue you're fixing — scope discipline is enforced by the validator
