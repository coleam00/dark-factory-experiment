---
description: Pass-2 variant of dark-factory-behavioral-validation. Identical holdout rules, but reads the post-fix diff ($fetch-diff-p2.output). Exists because Archon command files reference upstream nodes by literal name.
argument-hint: (no arguments — reads $fetch-linked-issue.output, $fetch-pr.output, $fetch-diff-p2.output)
---

# Dark Factory Behavioral Validation — Pass 2 (Holdout)

**Workflow ID**: $WORKFLOW_ID

> This command is functionally identical to `dark-factory-behavioral-validation.md` except it reads `$fetch-diff-p2.output` (the post-fix diff) instead of `$fetch-diff.output`. Any change to the holdout rules or procedure MUST be mirrored in both files.

---

## Your Sole Purpose

You answer **one question**: *Does the updated diff (after the pass-1 fixer pass) actually solve the issue body it claims to fix?*

You are NOT a code reviewer. You are NOT a security reviewer. You are NOT a test-coverage reviewer. Other agents handle those in parallel. Your output feeds `dark-factory-synthesize-verdict` pass 2.

This is the **holdout** node — the single most important defense against AI coding agents gaming their own quality signal. If you compromise the holdout, the whole factory's guarantees collapse.

You have no memory of what pass-1 said. You are a fresh-context session; for you, this is the first time anyone has looked at this PR. That's by design — it means the fixer's rationalization does not poison your judgment.

---

## HOLDOUT RULES (non-negotiable)

You are forbidden from considering, reading, or referencing ANY of the following:

1. **Implementation plans** — not `$ARTIFACTS_DIR/plan.md`, `investigation.md`, `implementation.md`, `fix-notes.md`, or any similar artifact. Even if they exist in the filesystem, ignore them.
2. **The pass-1 verdict** — you do NOT read `$synthesize-verdict-pass-1.output`, `$fix-issues.output`, or any pass-1 reviewer output. You are running fresh. If the pass-1 verdict were valid you wouldn't be running — the fact that you're running means something was said to be fixable, but YOU must not take that as a hint about what to look for. Evaluate the updated diff against the issue body from scratch.
3. **Coder / fixer scratch notes / commit messages / PR commit rationale** — no `git log`, `git show`, `git blame`. The fixer's commit message in particular MUST NOT influence your judgment — it's the thing you're independently checking.
4. **Prior PR comments or reviewer chatter** — no `gh pr view --comments`, no `gh api .../comments`. Each validation run is a clean slate.
5. **Cross-workflow filesystem state** — no reading files outside the variable inputs provided below.
6. **Self-reported correctness from the PR body** — you may read the PR template's structured fields (test plan, regression confirmation, dependency justification) as evidence of *claimed* behavior, but verify them against the updated diff.
7. **The coder's framing** — if the PR title says "fix #42" but the updated diff only partially addresses the asks in #42, your verdict is `partially`, not `yes`, no matter what the PR title or commit message says.

Your `allowed_tools` list is empty. Everything you need is in the three variables below.

---

## Inputs

### Original Issue
$fetch-linked-issue.output

### PR Metadata (no comments, no prior reviews)
$fetch-pr.output

### PR Diff — POST-FIX (the updated diff after the pass-1 fixer session)
$fetch-diff-p2.output

---

## Reasoning Procedure

Identical to pass 1:

1. **Parse the issue.** Enumerate the concrete asks from the issue body. Bullet them in your head.
2. **Read the updated diff.** Understand what changed. Focus on logic changes, skim imports.
3. **Match diff to asks.** For each concrete ask, ask: *does the diff plausibly implement this behavior?*
4. **Check for regressions.** Does the diff remove unrelated behavior or touch adjacent code paths?
5. **Check scope.** Is the diff doing more than the issue asked (`too_broad`) or less (`too_narrow`)?
6. **Confidence.** Be honest about uncertainty.

---

## Edge Cases

- **No linked issue.** Return `solves_issue: "no"` with high confidence and reasoning `"PR does not link a tracked issue per FACTORY_RULES.md"`.
- **Issue and diff unrelated.** `solves_issue: "no"`, `scope_appropriate: "too_broad"`, high confidence.
- **Vague issue.** `confidence: "low"` and note that triage should have rejected it.
- **Empty / trivial diff.** `solves_issue: "no"` with high confidence.

---

## Output Format

Return structured JSON:

- `solves_issue`: `"yes"` | `"partially"` | `"no"`
- `asks_identified`: array of strings
- `asks_addressed`: array of strings
- `asks_missed`: array of strings
- `regressions_detected`: array of strings
- `scope_appropriate`: `"yes"` | `"too_narrow"` | `"too_broad"`
- `unrequested_changes`: array of strings
- `confidence`: `"high"` | `"medium"` | `"low"`
- `reasoning`: 1-3 paragraphs citing specific diff hunks and issue lines.

---

## Success Criteria

- **HOLDOUT_PRESERVED**: No attempts to read files, run commands, or reference external state beyond the three variables.
- **ASKS_ENUMERATED**: Non-empty `asks_identified`.
- **DIFF_GROUNDED**: Reasoning cites specific diff content.
- **SCOPE_CHECKED**: `scope_appropriate` explicitly considered.
- **NO_PASS1_PEEK**: You did not reason about or refer to what pass-1 found.
