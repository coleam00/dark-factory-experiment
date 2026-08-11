# Benchmark Issue Candidates

Three candidate issues to file for the matrix run. Picked to span: concurrency reasoning, text-processing edge cases, and domain-specific semantics. Each is sized to require *reasoning* to identify the right approach but *only a small diff* to fix — the precise shape that tests "where does reasoning matter."

**Action required:** Cole reviews each candidate, validates against current code (especially candidate C which I didn't fully verify), then files them with `factory:in-progress` + `priority:medium` so the dark-factory orchestrator skips them. See `BENCHMARK-PLAYBOOK.md` for the lockout rationale.

---

## Candidate A — Concurrency reasoning

**Title:** `fix(rag): _video_cache miss path has a thundering-herd race`

### Body

The module-level `_video_cache` in `app/backend/rag/retriever_hybrid.py` is populated lazily on cache miss:

```python
if video_id not in _video_cache:
    video = await repository.get_video(video_id)
    if video:
        _video_cache[video_id] = { ... }
    else:
        ...
        _video_cache[video_id] = { "title": "Unknown Video", ... }

video_meta = _video_cache[video_id]
```

Two concurrent calls to `retrieve_hybrid()` that both miss on the same `video_id` will both enter the cache-miss branch, both await `repository.get_video()`, and both write the entry. This is harmless in steady state (the writes are idempotent) but wasteful under cold-cache load and creates a small window where a third concurrent reader can read a half-formed entry. There is also no upper bound on cache size — long-running processes accumulate entries indefinitely.

### Acceptance

- Concurrent `retrieve_hybrid()` calls for the same uncached `video_id` result in at most one `repository.get_video()` call.
- The cache has a soft upper bound (LRU or simple size cap) to prevent unbounded growth.
- No behavior change for cache hits (still O(1) lookup, no extra awaits).

### Out of scope

- Don't refactor the result-construction loop.
- Don't change the schema of cached entries.
- Don't add a new module.

### Why this tests reasoning

Requires reasoning about asyncio lifecycle, lock granularity (per-video vs single global), and the interaction with `asyncio.gather`/concurrent requests. The wrong answer (e.g. a global `asyncio.Lock` around the entire cache miss block) serializes all unique cache misses unnecessarily. The right answer (per-video coordination via `asyncio.Lock` or a dict of futures) preserves concurrency for distinct videos.

---

## Candidate B — Text-processing edge case

**Title:** `fix(citations): non-marker bracket text triggers excessive holdback in streaming stripper`

### Body

`CitationMarkerStripper` in `app/backend/rag/citations.py` holds back any tail that *could be the start of* a `[c:<id>]` marker, flushing only when 128 chars accumulate without closing:

```python
_PARTIAL_AT_END_RE: Final = re.compile(r"\[(?:c(?::[A-Za-z0-9_-]*)?)?$")
_MAX_HOLDBACK: Final = 128
```

Streaming output that contains a lone `[` followed by anything that is not `c:` (e.g. `[note]`, `[1]`, `[link text](...)`) currently holds back up to 128 chars before realizing it's not a marker, introducing a visible streaming delay for any user-facing answer that uses brackets for non-citation purposes.

The regex partials we hold back are:

1. `[` alone — could be anything; should be released as soon as the next char isn't `c`.
2. `[c` alone — could be anything starting with `[c`; release when the next char isn't `:`.
3. `[c:` and `[c:<partial-id>` — bounded by max-UUID-length (36 + 3 = 39 chars), never needs to wait 128 chars.

### Acceptance

- A stream containing `[note]` followed by more text emits `[note]` without an artificial delay (held back only momentarily until the next char rules it out as a marker prefix).
- A stream containing `[c:<full-uuid>]` markers continues to strip them correctly across token boundaries (existing tests still pass).
- The 128-char `_MAX_HOLDBACK` constant becomes irrelevant for non-marker cases (or is lowered to ~40 to bound legitimate marker holdback).

### Out of scope

- Don't change the marker syntax or the regex character class.
- Don't change `extract_cited_chunk_ids()` or `strip_citation_markers()` (the non-streaming helpers).

### Why this tests reasoning

Requires reasoning about regex state machines, streaming protocols, and how to discriminate "definitely-not-a-marker" partials from "still-could-be-a-marker" partials with minimal lookahead. The right answer reshapes the partial-detection logic; the wrong answer just lowers `_MAX_HOLDBACK` (which would truncate legitimate markers).

---

## Candidate C — Domain semantics (REQUIRES CODE VERIFICATION BEFORE FILING)

**Title:** `fix(rate-limit): conversation deletion must not release a user's 25/day audit rows`

### Body

`DAILY_MESSAGE_CAP = 25 messages per user per 24 hours` is a hard MISSION-level invariant. The rate-limiter counts rows in `user_messages` in the last 24 hours.

A user who deletes their conversation must NOT have their `user_messages` audit rows deleted as a side effect — otherwise the user can evade the cap by deleting and re-creating conversations. Confirm that conversation deletion (`DELETE /api/conversations/{id}`) only deletes conversation + message rows, not the `user_messages` audit table.

### Acceptance

- Deleting a conversation does NOT delete any rows from `user_messages`.
- If a test does not already exist, add a regression test asserting that after a delete + re-count, the per-user 24h count is unchanged.
- No behavior change to conversation deletion otherwise.

### Out of scope

- Don't change `DAILY_MESSAGE_CAP` (hard invariant per MISSION).
- Don't change the rate-limit advisory-lock pattern.
- Don't add a "soft delete" — just confirm the audit table is separate from conversation cascade.

### Why this tests reasoning

Requires reading two unrelated modules (`db/repository.py` for delete cascade behavior, `rate_limit.py` for the counted table) and reasoning about a security property that is not obvious from a single file. Tests cross-file domain understanding.

**⚠ Verification needed:** Confirm this bug actually exists by inspecting the delete cascade behavior in `db/repository.py` and the schema in `alembic/versions/`. If `user_messages` is already isolated from `conversations` deletion, swap this for a different domain candidate — e.g. "rate-limit `oldest_message_in_window_created_at` returns wrong reset time when the cap is exceeded by exactly one over the window edge."

---

## Pick order suggestion

If running all 3 in parallel waves, dispatch order doesn't matter. If running sequentially:

1. **A first** — clearest "concurrency reasoning matters here" hypothesis. Will produce the most visually-different scoreboards across cells.
2. **B second** — narrower scope, smaller diff, tests text-processing reasoning specifically.
3. **C third** — requires the most up-front verification by Cole before filing, so it's the most prep-heavy.
