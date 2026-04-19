---
description: Comprehensive test scenario 2 - verify adding a YouTube video to the library works end-to-end.
argument-hint: (no arguments - reads port files and health-before.json from $ARTIFACTS_DIR)
---

# Dark Factory Comprehensive Test — Video Ingestion

**Workflow ID**: $WORKFLOW_ID

---

## Your Role

You are running scenario 2 of the Dark Factory comprehensive weekly test
for DynaChat. Verify that adding a YouTube video to the library works
end-to-end: ingestion completes and the backend's chunk count grows.

You have Bash + agent-browser. Do NOT read source code.

---

## Fixed Test Video (locked fixture, do NOT change)

```
https://www.youtube.com/watch?v=pjF-0dliYhg
```

---

## Running App URLs

- Frontend: `http://127.0.0.1:$(cat $ARTIFACTS_DIR/.frontend-port)`
- Backend:  `http://127.0.0.1:$(cat $ARTIFACTS_DIR/.backend-port)`

Baseline snapshot already captured at `$ARTIFACTS_DIR/health-before.json`
(contains `video_count` + `chunk_count` from before any scenario ran).

---

## Steps

1. `agent-browser open <frontend URL>`
2. `agent-browser snapshot -i` - find the library / add-video surface.
   If the app has a sidebar, navigation, or dedicated library page, go
   there. If the video-add control is on the chat page, use it in place.
   If you cannot find any way to add a video, that is a FAIL.
3. Enter the test video URL into the add-video input and submit.
4. Wait for ingestion to complete. This may take 30-90s for transcript
   fetch + chunking + embedding. Poll the UI and also poll
   `curl -sf http://127.0.0.1:<backend_port>/api/health` for up to 180s
   until `chunk_count` increases above the baseline from
   `health-before.json`.
5. Screenshot the library/list view showing the ingested video to
   `$ARTIFACTS_DIR/test-video-ingestion.png`
6. Save the post-ingestion `/api/health` response to
   `$ARTIFACTS_DIR/health-after-ingestion.json`
7. `agent-browser close`
8. Write a markdown summary to `$ARTIFACTS_DIR/test-video-ingestion.md`
   including before/after chunk counts and screenshot path.

---

## Failure Criteria

FAIL if any of:
- No add-video control found
- Ingestion UI shows an error
- `chunk_count` did not increase within 180s
- Test video does not appear in the library list

---

## Output Format

Return structured JSON:
- `status`: `"pass"` | `"fail"`
- `summary`: one-sentence description
- `evidence`: artifact paths
- `failure_reason`: null if passing, else concrete problem
