# Mixed-Provider Routing Benchmark — Dispatch Playbook

## What this is

A matrix benchmark to answer: *where does reasoning matter — planning, implementation, both, or neither?* Each cell runs the same 6-node workflow against the same set of issues, varying only the model for the plan + implement steps. Held-constant: explore + self-review on Sonnet.

It started as the 4 Opus/Kimi cells below and grew to 10. `X` = the plan node is removed entirely (no-plan baseline), `F` = `claude-fable-5`.

| Cell | Plan model | Impl model |
|---|---|---|
| `benchmark-OO` | claude/opus | claude/opus |
| `benchmark-OK` | claude/opus | pi/kimi-coding |
| `benchmark-KO` | pi/kimi-coding | claude/opus |
| `benchmark-KK` | pi/kimi-coding | pi/kimi-coding |
| `benchmark-XO` | *(no plan node)* | claude/opus |
| `benchmark-XK` | *(no plan node)* | pi/kimi-coding |
| `benchmark-FF` | claude-fable-5 | claude-fable-5 |
| `benchmark-FK` | claude-fable-5 | pi/kimi-coding |
| `benchmark-FO` | claude-fable-5 | claude/opus |
| `benchmark-OF` | claude/opus | claude-fable-5 |

## Known confound — read before trusting a number

**`benchmark-OO` is not prompt-identical to the cells it is compared against.** Seven of the
eight two-model cells (FF, FK, FO, KK, KO, OF, OK) share one prompt set; OO alone carries a
materially richer one:

| Node | The other 7 cells | `benchmark-OO` | Delta |
|---|---|---|---|
| explore | 784 chars | 810 | +3% |
| plan | 559 | 750 | **+34%** |
| implement | 476 | 852 | **+79%** |
| self-review | 575 | 1095 | **+90%** |

OO's plan prompt asks for *"function names, line ranges if known, what to add/remove/replace"*;
the others just ask for an *"ordered bullet list of code edits."* That biases **toward** OO,
which is the premium baseline every other cell is measured against.

Two consequences. Any result where OO wins is confounded and should not be quoted. Any result
where a cell **beats** OO is, if anything, understated — the comparison was tilted against it.

Fix by levelling the prompts up to OO's across all cells and re-running, not by adjusting
scores. Until then, treat cross-cell deltas involving OO as directional only.

## Files

All of it lives in `.archon/workflows/benchmark/` (moved out of `.archon/workflows/` on
2026-08-11 so the four `dark-factory-*.yaml` production workflows are the visible content of
that directory):

- `benchmark-OO.yaml`, `-OK.yaml`, `-KO.yaml`, `-KK.yaml`, `-XO.yaml`, `-XK.yaml`,
  `-FF.yaml`, `-FK.yaml`, `-FO.yaml`, `-OF.yaml`
- `benchmark-evaluator.yaml` — 7-dimension Opus scorer
- `BENCHMARK-PLAYBOOK.md` — this file

## Prereqs

- Local Pi 0.74.0+ with `kimi-coding` provider in `~/.pi/agent/auth.json` (verify: `pi --provider kimi-coding --model kimi-for-coding --print "ping"`)
- Local Archon CLI (`archon --version`)
- `gh` CLI authenticated as the repo owner
- Issues are labeled `factory:in-progress` BEFORE dispatch so the production dark-factory orchestrator ignores them. **This is the lockout mechanism — do not skip it.**

## Dispatch — single workflow

From a **plain terminal** (NOT Claude Code — see "Gotchas" below):

```powershell
$env:ARCHON_SUPPRESS_NESTED_CLAUDE_WARNING = "1"
$env:IS_SANDBOX = "1"
archon workflow run benchmark-KK --cwd "C:/Users/colem/dark-factory-experiment" "#225"
```

Or bash:

```bash
ARCHON_SUPPRESS_NESTED_CLAUDE_WARNING=1 IS_SANDBOX=1 \
  archon workflow run benchmark-KK --cwd "C:/Users/colem/dark-factory-experiment" "#225"
```

## Dispatch — full matrix (12 runs)

For each of 3 issues × N cells. **Run in waves of 4 to avoid VPS-style thrash and 5-hour rate-limit pressure on Anthropic.** Approx timing per run from the smoke test: ~3 min per workflow + ~45s per evaluator.

`CELLS` below is the original 4. Widen it to whichever cells you are testing — but if OO is in
the list, read the "Known confound" section above first.

```bash
# Set once
ISSUES=(225 226 227)   # ← replace 225/226/227 with the 3 real benchmark issues
CELLS=(OO OK KO KK)    # ← full set: OO OK KO KK XO XK FF FK FO OF
ROOT="C:/Users/colem/dark-factory-experiment"
export ARCHON_SUPPRESS_NESTED_CLAUDE_WARNING=1 IS_SANDBOX=1

# Wave 1 — 4 workflows in parallel, all 4 cells against issue 1
for cell in "${CELLS[@]}"; do
  STAMP=$(date +%Y%m%d-%H%M%S)
  archon workflow run "benchmark-${cell}" --cwd "$ROOT" "#${ISSUES[0]}" \
    > "$ROOT/.benchmark-logs/${cell}-issue${ISSUES[0]}-${STAMP}.log" 2>&1 &
  sleep 2   # stagger by 2s to avoid Pi session/worktree collisions
done
wait   # block until wave 1 done

# Wave 2 — same for issue 2
for cell in "${CELLS[@]}"; do ... done; wait

# Wave 3 — same for issue 3
for cell in "${CELLS[@]}"; do ... done; wait
```

## Run the evaluator on every PR

After all 12 PRs land:

```bash
# Collect benchmark PRs (one per cell × issue)
for pr in $(gh pr list --repo coleam00/dark-factory-experiment \
              --label "benchmark:OO" --label "benchmark:OK" \
              --label "benchmark:KO" --label "benchmark:KK" \
              --state open --json number --jq '.[].number'); do
  STAMP=$(date +%Y%m%d-%H%M%S)
  archon workflow run benchmark-evaluator --cwd "$ROOT" "#${pr}" \
    > "$ROOT/.benchmark-logs/eval-${pr}-${STAMP}.log" 2>&1 &
  sleep 2
done
wait
```

## Build the scoreboard

After every PR has an evaluator comment:

```bash
# Pretty scoreboard from PR comments
for pr in $(gh pr list --repo coleam00/dark-factory-experiment \
              --label "benchmark:OO" --label "benchmark:OK" \
              --label "benchmark:KO" --label "benchmark:KK" \
              --state open --json number --jq '.[].number' | sort -n); do
  TITLE=$(gh pr view "$pr" --json title --jq '.title')
  SCORE=$(gh pr view "$pr" --json comments \
          --jq '.comments[-1].body' \
          | grep -oE 'score: \*\*[0-9]+/70' | grep -oE '[0-9]+' | head -1)
  echo "PR #${pr}: ${SCORE}/70   ${TITLE}"
done
```

Aggregate by cell + issue manually for the on-stream scoreboard reveal.

## Gotchas

### 1. `CLAUDECODE=1` warning

If you dispatch from inside a Claude Code session, Archon warns about a known hang risk. Suppress with `ARCHON_SUPPRESS_NESTED_CLAUDE_WARNING=1` AND prefer a plain shell. The smoke test ran clean from a nested session but stream day is not the day to gamble.

### 2. `service.title-generator` "Claude Code not found" error

Cosmetic noise — see the Dark Factory plan §13. Fire-and-forget try/catch; does NOT block workflows. Ignore the stack trace in logs.

### 3. `⚠️ Tool bash failed` mid-node

Saw one of these during the smoke test in the `implement` node. Node still completed in 55.7s. Treat single occurrences as transient retries. If a node fails multiple times in a row, kill the workflow and dispatch again.

### 4. Stale worktrees

Each dispatch creates a fresh worktree in `C:/Users/colem/.archon/workspaces/colem/dark-factory-experiment/worktrees/`. They persist after the workflow ends. Periodically clean up:

```bash
cd "$ROOT" && git worktree prune -v
```

### 5. `factory:in-progress` lockout

The benchmark issues must have `factory:in-progress` set BEFORE you dispatch, so the production dark-factory orchestrator (cron every 30 min on the VPS) skips them. Without this label, the orchestrator may dispatch its 23-node `dark-factory-fix-github-issue` workflow against your benchmark issue and contaminate the experiment. Verify with:

```bash
gh issue view $ISSUE --json labels --jq '.labels[].name' | grep factory:in-progress
```

### 6. Rate limit

Anthropic 5-hour window applies to Sonnet + Opus combined. Org-level overage is OFF (per the dispatch log: `overageDisabledReason: org_level_disabled`). 12 workflows × ~3 Opus/Sonnet calls each = ~36 calls, plus 12 evaluator Opus calls = ~48 total. Should fit in one 5-hour bucket if the bucket is fresh. Watch for `rateLimitType:five_hour, status:exceeded` in dispatch logs.

### 7. Plan-to-impl fidelity wiring (resolved 2026-05-20)

The benchmark workflows now stamp their own run-id into the PR body (extracted from `basename "$ARTIFACTS_DIR"`). The evaluator parses that run-id out and copies the source `plan.md` + `exploration.md` from `~/.archon/workspaces/coleam00/dark-factory-experiment/artifacts/runs/<run-id>/` into its own `$ARTIFACTS_DIR`. Opus then reads them via the Read tool to score `plan_impl_fidelity` against the actual plan, not against commit history alone. If the run-id is missing or the artifacts dir was cleaned up, the evaluator falls back to "not visible" and still scores the other 6 dimensions.

## Smoke test results (2026-05-20)

Validated end-to-end on PR #226 (Kimi/Kimi cell, smoke-test issue #225):

| Node | Duration |
|---|---|
| fetch-issue | 0.6s |
| explore (Sonnet) | 31.7s |
| plan (Kimi) | 58s |
| implement (Kimi) | 55.7s |
| self-review (Sonnet) | 25.2s |
| create-pr | 5.8s |
| **Total workflow** | **~3 min** |
| evaluator (Opus) | ~45s |

Evaluator scored 60/70 with rationale per dimension. Result: full chain operates correctly.
