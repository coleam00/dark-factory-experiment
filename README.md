# The Dark Factory Experiment (RAG YouTube Chat App)

**A public Dark Factory experiment.** This repository is a working web application that is built, reviewed, and merged almost entirely by AI coding agents. Humans only do two things: file issues and promote releases. Everything in between - triage, implementation, code review, testing, merging - is handled by Archon workflows running on a cron.

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
3. **The model: MiniMax M2.7.** Claude Code is routed to MiniMax M2.7 instead of Anthropic's models. Claude Code is the wrapper around the model; MiniMax is the brain doing the reasoning and the writing.

The reason for swapping the model out is purely economic. At the throughput a real Dark Factory needs (multiple multi-hour workflow runs per day, each burning a lot of tokens on planning, implementation, and review), running on an Anthropic subscription would hit rate limits quickly. MiniMax M2.7 is cheap and fast enough to let the experiment actually run continuously without throttling.

### How a change actually ships

```
        GitHub Issues (filed by humans or the regression testing workflow)
                       │
                       ▼
            ┌──────────────────────┐
            │  Orchestrator (cron) │   thin Claude Agent SDK loop
            │  every 4-6 hours     │   reads GitHub state, dispatches
            └──────────┬───────────┘   one Archon workflow at a time
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
3. **Governance files (`MISSION.md`, `FACTORY_RULES.md`) can never be modified by the factory.** The security review hard-fails any PR that touches them.
4. **One workflow at a time.** The orchestrator checks `bun run cli workflow status` before dispatching. The 4-hour cadence caps throughput structurally.
5. **Flood protection.** Non-owner accounts are capped at 3 issues per UTC day; excess get `factory:rate-limited` and re-evaluated after midnight.
6. **Per-node budget caps.** Every workflow node has a `maxBudgetUsd`. Triage batches max 10 issues per run and truncates each body to ~2KB.

### Workflows in this repo

Defined in [`.archon/workflows/`](.archon/workflows):

| Workflow | Job |
|---|---|
| `dark-factory-triage.yaml` | Batch-classify untriaged issues against `MISSION.md` + `FACTORY_RULES.md`. Outputs structured JSON, applies labels and comments deterministically via `gh`. |
| `dark-factory-fix-github-issue.yaml` | The workhorse. A Dark-Factory-owned fork of Archon's bundled `fix-github-issue`, adapted for this repo's Python + Bun stack: classify → research → plan → implement → Python/TS validation (ruff/mypy/pytest + tsc/biome/vitest) → draft PR → smart review → self-fix → simplify. Every AI node references a `.md` command file (no inline prompts). |
| `dark-factory-validate-pr.yaml` | Independent gate. Static checks + tests, then parallel AI review (behavioral validation, code review, error handling, security check), synthesized verdict, auto-merge or fix-and-retry. The fix step is folded in as a fresh-context node so the second-pass validator stays a true holdout. |

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
                                            │  (SQLite) (OpenRouter)  │
                                            │            │            │
                                            │         Retriever       │
                                            │       (NumPy cosine)    │
                                            │            │            │
                                            │           LLM           │
                                            │    (Claude via          │
                                            │     OpenRouter)         │
                                            └─────────────────────────┘
```

- **Frontend:** React 18 + Vite + TypeScript + Tailwind CSS (Bun)
- **Backend:** Python FastAPI, single process handling API + RAG + LLM
- **Database:** SQLite via aiosqlite (Postgres-swappable via repository pattern)
- **LLM:** Claude Sonnet via OpenRouter with SSE streaming
- **Embeddings:** `text-embedding-3-small` via OpenRouter
- **Chunking:** Docling HybridChunker
- **Retrieval:** NumPy cosine similarity, top-5 chunks

### How it works

1. **Ingest** - Video transcripts are chunked with Docling's HybridChunker and embedded via OpenRouter.
2. **Retrieve** - User queries are embedded and matched against chunks using cosine similarity.
3. **Generate** - Top-5 chunks are passed as context to Claude, which streams a cited response back via SSE.

---

## Quick Start

### Prerequisites

- Python 3.11+
- [Bun](https://bun.sh)
- An [OpenRouter](https://openrouter.ai) API key

### Setup

1. Clone the repo and create a `.env` file in the project root:

```
OPENROUTER_API_KEY=your-key-here
```

2. Start everything:

```bash
# Unix/Mac
cd app && ./start.sh

# Windows
cd app && start.bat
```

This sets up the Python venv, installs dependencies, seeds the database with 10 sample videos, and starts both servers.

3. Open [http://localhost:5173](http://localhost:5173)

### Manual start

```bash
# Backend
cd app
python -m venv backend/.venv
source backend/.venv/bin/activate  # or backend\.venv\Scripts\activate on Windows
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000

# Frontend (new terminal)
cd app/frontend
bun install
bun run dev
```

---

## Contributing

You contribute to this repo the same way the factory does: **file an issue.** Don't open a PR - the factory will. If your issue is well-scoped and in line with `MISSION.md`, the next triage cycle will accept it, and a workflow run will open the implementing PR. If it gets rejected, read the comment, sharpen the issue, and reopen.

That's the whole point of the experiment.
