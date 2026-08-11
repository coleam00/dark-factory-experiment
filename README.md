# The Dark Factory Experiment (RAG YouTube Chat App)

**A public Dark Factory experiment.** This repository is a working web application that is built, reviewed, and merged almost entirely by AI coding agents. Humans do two things: file issues and promote releases. Everything in between - triage, implementation, code review, testing, merging - is handled by Archon workflows running on a cron.

Two honest caveats, because they are the design and not an asterisk. This runs at **level 4, not level 5**: the factory does not write its own issues. And there is a deliberate **human-authored perimeter** it is never allowed to touch - auth, rate limiting, the deploy configs, and the three governance files that define its own rules. The list is in `FACTORY_RULES.md`, and a PR touching any of it is auto-rejected before anything else is evaluated. An autonomous system is only as trustworthy as the things it cannot change about itself.

The application itself is a dark-mode AI chat app that lets you have grounded conversations about a creator's YouTube videos, with cited answers pulled from transcript passages. But the *real* point of this repo is the factory that builds it.

![Main chat interface](app/screenshots/screenshot-main.png)

---

## The Dark Factory

The term "Dark Factory" comes from Dan Shapiro (Glowforge), inspired by FANUC's 1980s lights-out robotics plants where robots built robots 24/7 with no humans on the floor. Applied to software: **specs go in, software comes out.**

This repo is a live attempt at that pattern, and it uses GitHub itself as the shared state machine.

### The three layers

There's a stack of three distinct things doing the work, and it's worth pulling them apart:

1. **The harness: [Archon](https://github.com/coleam00/archon).** The workflow engine, and the thing that makes the whole experiment possible. Archon lets you stitch coding agent sessions together with deterministic steps (running scripts, calling `gh`, parsing output, branching on results) into a single end-to-end workflow you actually trust. The Dark Factory's logic, "triage these issues, then implement this one, then validate the PR, then merge it," is built in Archon as a handful of workflows under `.archon/workflows/`. Without something like Archon, you're either hand-prompting agents one step at a time or writing a giant brittle script around them. Archon is what turns "AI can sometimes do this" into "the factory does this every few hours, on its own."
2. **The coding agent: Claude Code.** Inside each AI node, Archon spawns Claude Code as the agent. Claude Code is what actually holds the tools (file editing, bash, `gh`, web fetch), runs the loop, and executes the work the prompt asks for.
3. **The model: Claude Sonnet, with Haiku on the cheap nodes.** Set per workflow node, so the routing is a config decision rather than a property of the factory. Claude Code is the wrapper around the model; the model is the brain doing the reasoning and the writing.

Model routing is the cheapest lever in the whole system, and it is worth treating as one. The factory has run on MiniMax M2.7 and on Kimi K2.6 via Pi at different points in the experiment; the workflows under `.archon/workflows/` currently declare `provider: claude` with `sonnet` for reasoning nodes and `haiku` for cheap extraction. Nothing else in the design changes when that swaps, which is the point: the agent and the model are the interchangeable parts, and the plumbing around them is not.

The mixed-provider benchmark under [`.archon/workflows/benchmark/`](.archon/workflows/benchmark) is where that gets measured rather than assumed - a matrix varying the plan and implement models independently to find out where reasoning actually pays for itself. Read `BENCHMARK-PLAYBOOK.md` there before quoting any number from it; it documents a known prompt-parity confound in the premium baseline cell.

### How a change actually ships

```
        GitHub Issues (filed by humans or the regression testing workflow)
                       │
                       ▼
            ┌──────────────────────┐
            │  Orchestrator (cron) │   pure-bash loop, no LLM
            │   every 30 minutes   │   reads GitHub state, dispatches
            └──────────┬───────────┘   up to MAX_PARALLEL=4 workflows
                       │
       ┌───────────────┼────────────────┐
       ▼               ▼                ▼
  dark-factory     fix-github-     dark-factory
  -triage          issue           -validate-pr
  (classify        (10-phase       (independent
   open issues,     implement +     holdout review
   accept/reject)   draft PR)       + auto-merge)
                       │
                       ▼
                ┌─────────────┐
                │    main     │  AI-managed branch
                │ auto-deploys│  → staging / preview
                └──────┬──────┘
                       │  human promotes periodically
                       ▼
                ┌─────────────┐
                │  release/*  │  human-cut stable
                │   deploys   │  → production
                └─────────────┘
```

### Labels are the state machine

The orchestrator does not hold state itself. It reads GitHub labels and decides what to do next:

**Issues:** `factory:triaging` → `factory:accepted` → `factory:in-progress` → (PR opened) or `factory:rejected` (closed with reason).

**PRs:** `factory:implementing` → `factory:needs-review` → `factory:approved` (auto-merged) or `factory:needs-fix` → back to review (max 2 fix attempts) → `factory:needs-human` (escalated).

**Priority:** Triage tags every accepted issue `priority:critical|high|medium|low` so the orchestrator picks the highest-impact work first.

### The non-negotiable rules

These come from research on every prior Dark Factory attempt (StrongDM, Spotify Honk, Steve Yegge's Gas Town) and the failure modes they hit:

1. **The validator never reads the implementation plan.** It checks the *outcome* against the *issue*, not the approach. This is StrongDM's "holdout" pattern - it's what stops an agent from gaming its own acceptance criteria.
2. **Triage has only two verdicts: accept or reject.** No "needs human" inbox. If a human disagrees with a rejection, they reopen with more context and the next triage cycle picks it up fresh.
3. **Governance files (`MISSION.md`, `FACTORY_RULES.md`, `CLAUDE.md`) can never be modified by the factory.** The security review hard-fails any PR that touches them. The agent cannot amend the rules it is judged by.
4. **The dispatcher is dumb on purpose.** Pure bash on a 30-minute cron, reading GitHub labels as the only shared state - no database, no message bus, no LLM deciding what to run. An earlier version asked a model what to dispatch and it hallucinated runs for work that did not exist. It dispatches up to `MAX_PARALLEL=4` workflows, in a fixed priority order: fix a PR, validate a PR, implement an issue, triage. Finishing in-flight work before starting new work is load-bearing - reversed, the factory triages forever while its own PRs rot.
5. **Flood protection.** Non-owner accounts are capped at 3 issues per UTC day; excess get `factory:rate-limited` and re-evaluated after midnight.
6. **Per-node budget caps.** Every workflow node has a `maxBudgetUsd`. Triage batches max 10 issues per run and truncates each body to ~2KB.

### Workflows in this repo

Defined in [`.archon/workflows/`](.archon/workflows):

| Workflow | Job |
|---|---|
| `dark-factory-triage.yaml` | Batch-classify untriaged issues against `MISSION.md` + `FACTORY_RULES.md`. Outputs structured JSON, applies labels and comments deterministically via `gh`. |
| `dark-factory-fix-github-issue.yaml` | The workhorse. A Dark-Factory-owned fork of Archon's bundled `fix-github-issue`, adapted for this repo's Python + Bun stack: classify → research → plan → implement → Python/TS validation (ruff/mypy/pytest + tsc/biome/vitest) → draft PR → smart review → self-fix → simplify. Every AI node references a `.md` command file (no inline prompts). |
| `dark-factory-validate-pr.yaml` | Independent gate. Static checks + tests, then parallel AI review (behavioral validation, code review, error handling, security check), synthesized verdict, auto-merge or fix-and-retry. The fix step is folded in as a fresh-context node so the second-pass validator stays a true holdout. |
| `dark-factory-comprehensive-test.yaml` | Weekly regression. Boots the app, drives four end-to-end browser scenarios with `agent-browser`, synthesizes a report, and files a GitHub issue for anything that broke. This is what closes the self-healing loop: the factory finds its own bugs and queues them for itself. |

**The orchestrator is not in this repo.** It is a ~100-line bash script on the VPS
(`/opt/dark-factory/orchestrator.sh`) driven by cron. Deliberately so - it holds no state
of its own, and everything it reads is visible in this repo's issues, PRs and labels.

The mixed-provider benchmark suite lives separately in [`.archon/workflows/benchmark/`](.archon/workflows/benchmark). It is not part of the factory loop.

---

## The Application

What the factory is actually building.

### Architecture

```
┌─────────────────┐       /api proxy        ┌─────────────────────────┐
│    Frontend     │ ─────────────────────── │        Backend          │
│  React + Vite   │    localhost:5173 →     │       FastAPI           │
│  TypeScript     │        :8000            │                         │
│  Tailwind CSS   │                         │  Routes ── RAG Pipeline │
└─────────────────┘                         │    │        │           │
                                            │    │     Chunker        │
                                            │    │     (Docling)      │
                                            │    │        │           │
                                            │    DB    Embeddings     │
                                            │(Postgres) (OpenRouter)  │
                                            │            │            │
                                            │         Retriever       │
                                            │  (RRF hybrid: tsvector   │
                                            │   + pgvector cosine)     │
                                            │            │            │
                                            │           LLM           │
                                            │    (Claude via          │
                                            │     OpenRouter)         │
                                            └─────────────────────────┘
```

- **Frontend:** React 18 + Vite + TypeScript + Tailwind CSS (Bun)
- **Backend:** Python FastAPI, single process handling API + RAG + LLM
- **Database:** Postgres via asyncpg (with pgvector for hybrid retrieval)
- **LLM:** Claude Sonnet via OpenRouter with SSE streaming
- **Embeddings:** `text-embedding-3-small` via OpenRouter
- **Chunking:** Docling HybridChunker
- **Retrieval:** Reciprocal Rank Fusion (RRF) combining Postgres tsvector full-text search with pgvector cosine similarity, top-5 chunks

### How it works

1. **Ingest** - Video transcripts are chunked with Docling's HybridChunker and embedded via OpenRouter.
2. **Sync** - `POST /api/channels/sync` automatically enumerates and ingests new videos from a YouTube channel via Supadata.
3. **Retrieve** - User queries run through Reciprocal Rank Fusion: a Postgres `tsvector` full-text search and a pgvector cosine search are run independently and their rankings merged.
4. **Generate** - Top-5 chunks are passed as context to Claude, which streams a cited response back via SSE.

---

## Quick Start

### Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- [Bun](https://bun.sh)
- **A Postgres 16+ database with the `pgvector` extension.** There is no SQLite fallback - the backend raises on startup if `DATABASE_URL` is unset.
- An [OpenRouter](https://openrouter.ai) API key

### Setup

1. Clone the repo and create an env file at `app/.env`. `deploy/.env.example` is the
   annotated reference for every variable; the minimum to boot locally is a
   `DATABASE_URL`, an `OPENROUTER_API_KEY` and a `JWT_SECRET`.

2. Apply migrations:

```bash
cd app/backend && uv run alembic upgrade head
```

3. Start everything:

```bash
# Unix/Mac
cd app && ./start.sh

# Windows
cd app && start.bat
```

This installs Python dependencies with `uv sync --all-extras`, starts FastAPI on `:8000`,
runs `bun install` if needed, and starts Vite on `:5173`. Both are skipped if the port is
already in use. Set `SEED_ENABLE=true` if you want the mock video library; it is off by
default, so a fresh database starts empty.

4. Open [http://localhost:5173](http://localhost:5173)

### Manual start

```bash
# Backend
cd app/backend
uv sync --all-extras
cd .. && uv --project backend run uvicorn backend.main:app --reload --port 8000

# Frontend (new terminal)
cd app/frontend
bun install
bun run dev
```

### Checks

```bash
cd app/backend  && uv run ruff check . && uv run mypy . && uv run pytest
cd app/frontend && bun run lint && bun run type-check && bun run test
```

---

## Contributing

You contribute to this repo the same way the factory does: **file an issue.** Don't open a PR - the factory will. If your issue is well-scoped and in line with `MISSION.md`, the next triage cycle will accept it, and a workflow run will open the implementing PR. If it gets rejected, read the comment, sharpen the issue, and reopen.

That's the whole point of the experiment.
