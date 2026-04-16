---
description: Comprehensive test scenario 3 - verify RAG pipeline grounds answers in ingested video and surfaces citations.
argument-hint: (no arguments - reads port files from $ARTIFACTS_DIR)
---

# Dark Factory Comprehensive Test — RAG Response

**Workflow ID**: $WORKFLOW_ID

---

## Your Role

You are running scenario 3 of the Dark Factory comprehensive weekly test
for DynaChat. Verify that the RAG pipeline actually grounds its answers
in the ingested video and surfaces a citation back to it.

Context: scenario 2 (test-video-ingestion) has already added this video:
```
https://www.youtube.com/watch?v=pjF-0dliYhg
```

You have Bash + agent-browser. Do NOT read source code.

---

## Running App URLs

- Frontend: `http://127.0.0.1:$(cat $ARTIFACTS_DIR/.frontend-port)`
- Backend:  `http://127.0.0.1:$(cat $ARTIFACTS_DIR/.backend-port)`

---

## Steps

1. `agent-browser open <frontend URL>`
2. Start a new conversation (or open the chat surface).
3. Ask a question that any RAG implementation should be able to ground
   in the ingested video's transcript. Use this exact question:
     "Based on the video I just added, summarize its main topic and
      cite the source."
4. Wait for the response to render fully (up to 60s).
5. Verify TWO things:
   (a) The answer includes substantive content describing the video
       (not "I don't know" / "no videos found" / an empty reply).
   (b) The UI renders at least one citation / source chip / link that
       references the ingested video (by title, URL, or video ID
       "pjF-0dliYhg"). If the app exposes citations via a click-to-
       expand, expand them and verify the reference.
6. Screenshot the rendered response with citations visible to
   `$ARTIFACTS_DIR/test-rag-response.png`
7. `agent-browser close`
8. Write a markdown summary to `$ARTIFACTS_DIR/test-rag-response.md`
   including the answer text and citation evidence.

---

## Failure Criteria

FAIL if any of:
- Response is empty or an error
- Response does not mention the ingested video's topic
- No citation to the ingested video is surfaced in the UI

---

## Output Format

Return structured JSON:
- `status`: `"pass"` | `"fail"`
- `summary`: one-sentence description
- `evidence`: artifact paths
- `failure_reason`: null if passing, else concrete problem
