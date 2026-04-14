---
description: Extract the GitHub PR number from the workflow arguments for Dark Factory validate-pr workflow.
argument-hint: (reads $ARGUMENTS — the workflow invocation message)
---

Find the GitHub PR number for this request.

Request: $ARGUMENTS

Rules:
- If the message contains an explicit PR number (e.g., "#42", "PR 42", "pr-42", "42"), extract that number.
- If the message is ambiguous (e.g., "validate the CORS fix"), use `gh pr list --label factory:needs-review --state open --json number,title` and pick the best match by title keywords.
- Do NOT read the PR body, diff, or comments to decide — only title + number. This is a holdout workflow: the less you see before validation starts, the better.

CRITICAL: Your final output must be ONLY the bare number with no quotes, no markdown, no explanation. Example correct output: 42
