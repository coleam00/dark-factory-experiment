---
description: Synthesize results from all comprehensive test scenarios into a single report and identify failures to file as GitHub issues.
argument-hint: (no arguments - reads test-*.md files from $ARTIFACTS_DIR)
---

# Dark Factory Comprehensive Test — Report Synthesizer

**Workflow ID**: $WORKFLOW_ID

---

## Your Role

You are the report synthesizer for the Dark Factory comprehensive
weekly test. Four scenario nodes have run against a real running
DynaChat instance. Your job: read their outputs and produce a single
aggregate report.

Read all files matching `$ARTIFACTS_DIR/test-*.md` (there should be 4:
`test-chat-ui.md`, `test-video-ingestion.md`, `test-rag-response.md`,
`test-conversation-history.md`).

---

## CRITICAL - Infrastructure vs Product Failures

If *fewer than 4* `test-*.md` files exist, that means one or more scenario
nodes never ran (an upstream infra node like pull-latest/install-deps/start-app
failed, and dependent scenarios were skipped). This is an INFRASTRUCTURE
FAILURE, not a product regression. In that case:
- Mark the missing scenarios as "MISSING (infra skipped)"
- Write "INFRA_FAILURE" at the top of the summary section
- Emit an **empty** JSON array `[]` in the failure block below -
  do NOT fabricate product bugs from missing evidence. The next
  node will see the empty array and file zero issues.

Only when all 4 `test-*.md` files exist should you synthesize real
failure objects from scenarios whose own status was "fail".

---

## Report Format

Produce `$ARTIFACTS_DIR/comprehensive-test-report.md` with this structure:

```
## Comprehensive Test Report - <ISO date>

### Summary
- Total scenarios: 4
- Passed: N
- Failed: N

### Per-scenario results

#### <scenario-name>
- **Status:** PASS | FAIL | MISSING
- **Summary:** <one line from the scenario's own summary>
- **Evidence:** <list of artifact paths>
- **Failure reason:** <if FAIL, the concrete reason>

(repeat for each of the 4 scenarios)

### Failures to file as issues

(fenced json block here)
```

The JSON array in the fenced block should contain one object per FAIL scenario:

```json
[
  {
    "scenario": "<scenario-name>",
    "title": "Bug: <short failure title, < 70 chars>",
    "body": "<markdown issue body with failure_reason, evidence paths, and reproduction hint>"
  }
]
```

If zero failures, write an empty array `[]` inside the json block.
The next bash node parses that JSON block to decide what to file.
