---
description: Comprehensive test scenario 1 - verify chat UI loads, accepts input, and renders a response.
argument-hint: (no arguments - reads port files from $ARTIFACTS_DIR)
---

# Dark Factory Comprehensive Test — Chat UI

**Workflow ID**: $WORKFLOW_ID

---

## Your Role

You are running scenario 1 of the Dark Factory comprehensive weekly test
for the DynaChat app (dark-factory-experiment). Your only job is to verify
that the chat UI loads, accepts input, and renders a response.

You have access to the Bash tool and the agent-browser skill. Do NOT read
any source code - you are a black-box UI tester.

---

## Running App URLs

Read these from artifact files:
- Frontend: `http://127.0.0.1:$(cat $ARTIFACTS_DIR/.frontend-port)`
- Backend:  `http://127.0.0.1:$(cat $ARTIFACTS_DIR/.backend-port)`

---

## Steps

1. `agent-browser open <frontend URL>`
2. `agent-browser snapshot -i` to see interactive elements
3. Find the chat message input field and a send/submit button/icon.
   If you cannot find a chat input, that is a FAIL.
4. Fill the input with: "What is this app for?"
5. Click the send button (or press Enter if there's no explicit button).
6. Wait up to 60s for a response to render. Snapshot again if needed.
7. Verify that a response message appears in the conversation area
   AND that it contains readable text (not just a spinner stuck forever
   and not an error toast).
8. Take a screenshot to `$ARTIFACTS_DIR/test-chat-ui.png`
9. `agent-browser close`
10. Write a markdown summary to `$ARTIFACTS_DIR/test-chat-ui.md` with:
    - Pass/fail verdict
    - What you observed
    - Screenshot path
    - Any console errors or visible UI errors

---

## Output Format

Return structured JSON:
- `status`: `"pass"` | `"fail"`
- `summary`: one-sentence human description
- `evidence`: list of artifact paths (screenshot, markdown, logs)
- `failure_reason`: null if passing, else concrete problem description
