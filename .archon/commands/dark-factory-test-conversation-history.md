---
description: Comprehensive test scenario 4 - verify multi-turn conversation context is retained within a single thread.
argument-hint: (no arguments - reads port files from $ARTIFACTS_DIR)
---

# Dark Factory Comprehensive Test — Conversation History

**Workflow ID**: $WORKFLOW_ID

---

## Your Role

You are running scenario 4 of the Dark Factory comprehensive weekly test
for DynaChat. Verify that multi-turn conversation context is retained
within a single thread.

You have Bash + agent-browser. Do NOT read source code.

---

## Running App URLs

- Frontend: `http://127.0.0.1:$(cat $ARTIFACTS_DIR/.frontend-port)`
- Backend:  `http://127.0.0.1:$(cat $ARTIFACTS_DIR/.backend-port)`

---

## Steps

1. `agent-browser open <frontend URL>`
2. Start a NEW conversation (not a continuation of any previous
   scenario's thread). If there's a "new chat" button, click it.
3. Send message 1 (exactly): "My favorite color is cerulean."
   Wait for the assistant to reply.
4. Send message 2 (exactly): "I also have a cat named Basil."
   Wait for the assistant to reply.
5. Send message 3 (exactly): "What was the first thing I told you
   about myself?"
   Wait for the assistant to reply.
6. Verify that the assistant's reply to message 3 explicitly mentions
   "cerulean" (or clearly references the favorite-color statement).
   If it talks about the cat, or says "I don't know", or gives a
   generic answer that doesn't reference the favorite color, that
   is a FAIL - the app lost conversation context.
7. Screenshot the full conversation thread (all 3 turns visible if
   possible) to `$ARTIFACTS_DIR/test-conversation-history.png`
8. `agent-browser close`
9. Write a markdown summary to `$ARTIFACTS_DIR/test-conversation-history.md`
   including the verbatim answer to message 3.

---

## Output Format

Return structured JSON:
- `status`: `"pass"` | `"fail"`
- `summary`: one-sentence description
- `evidence`: artifact paths
- `failure_reason`: null if passing, else concrete problem
