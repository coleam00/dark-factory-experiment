# DynaChat API

DynaChat is a RAG-powered chat interface for querying YouTube channel content with streaming
answers and per-chunk citations that deep-link to the exact timestamp in the source video.

**Base URL:** `http://localhost:8000` (development)

## Authentication

All endpoints under `/api/` require a valid session cookie unless noted otherwise.
Auth routes (`/api/auth/*`) are public and do not require a session.

| Endpoint Group | Auth Required |
|--------------|---------------|
| `/api/auth/*` | No (public) |
| `/api/health`, `/api/version` | No |
| All other `/api/*` | Yes (session cookie) |

---

## System

### `GET /api/health`

Health check. Returns database and video/chunk counts.

**Auth:** None

**Response `200`:**
```json
{
  "status": "ok",
  "video_count": 42,
  "chunk_count": 1340,
  "db_type": "postgres"
}
```

---

### `GET /api/version`

Returns the installed package version.

**Auth:** None

**Response `200`:**
```json
{ "version": "0.1.0" }
```

**Response `503`** — package metadata unavailable.

---

## Conversations

### `GET /api/conversations`

List all conversations for the authenticated user, ordered newest first.

**Auth:** Required

**Response `200`:**
```json
[
  {
    "id": "conv_abc123",
    "user_id": "user_xyz",
    "title": "Python Tips",
    "created_at": "2026-03-01T10:00:00Z",
    "updated_at": "2026-03-01T12:30:00Z"
  }
]
```

---

### `POST /api/conversations`

Create a new empty conversation.

**Auth:** Required

**Request body (optional):**
```json
{ "title": "My Conversation" }
```
If omitted, defaults to `"New Conversation"`.

**Response `201`:**
```json
{
  "id": "conv_abc123",
  "user_id": "user_xyz",
  "title": "New Conversation",
  "created_at": "2026-03-01T10:00:00Z",
  "updated_at": "2026-03-01T10:00:00Z"
}
```

---

### `GET /api/conversations/search`

Search conversations by title (case-insensitive substring match).

**Auth:** Required

**Query params:**
| Param | Type | Description |
|-------|------|-------------|
| `q` | string | Search query (required) |

**Response `200`:** Array of matching conversations (same shape as `GET /api/conversations`).

> **Note:** This endpoint must be declared before `/conversations/{conv_id}` in the router
> to avoid FastAPI matching "search" as a path parameter.

---

### `GET /api/conversations/{conv_id}`

Get a single conversation with all its messages.

**Auth:** Required

**Path params:**
| Param | Type | Description |
|-------|------|-------------|
| `conv_id` | string | Conversation ID |

**Response `200`:**
```json
{
  "id": "conv_abc123",
  "user_id": "user_xyz",
  "title": "Python Tips",
  "created_at": "2026-03-01T10:00:00Z",
  "updated_at": "2026-03-01T12:30:00Z",
  "messages": [
    {
      "id": "msg_001",
      "conversation_id": "conv_abc123",
      "role": "user",
      "content": "How do I use decorators?",
      "created_at": "2026-03-01T10:01:00Z"
    },
    {
      "id": "msg_002",
      "conversation_id": "conv_abc123",
      "role": "assistant",
      "content": "Decorators are functions that...",
      "created_at": "2026-03-01T10:01:30Z"
    }
  ]
}
```

**Response `404`** — conversation not found (or belongs to another user — returns 404 to avoid leaking existence).

---

### `DELETE /api/conversations/{conv_id}`

Delete a conversation and all its messages.

**Auth:** Required

**Path params:**
| Param | Type | Description |
|-------|------|-------------|
| `conv_id` | string | Conversation ID |

**Response `204`** — No content on success.

**Response `404`** — conversation not found.

---

### `PATCH /api/conversations/{conv_id}`

Rename a conversation.

**Auth:** Required

**Path params:**
| Param | Type | Description |
|-------|------|-------------|
| `conv_id` | string | Conversation ID |

**Request body:**
```json
{ "title": "New Title" }
```

**Response `200`:** Updated conversation object (same shape as `GET /api/conversations/{conv_id}`).

**Response `404`** — conversation not found.

---

## Messages

### `POST /api/conversations/{conv_id}/messages`

Send a user message and receive a streaming RAG-grounded response via Server-Sent Events.

**Auth:** Required

**Path params:**
| Param | Type | Description |
|-------|------|-------------|
| `conv_id` | string | Conversation ID |

**Request body:**
```json
{ "content": "How does async/await work in Python?" }
```

**Response:** `StreamingResponse` with `Content-Type: text/event-stream`

#### SSE Streaming Format

The response uses **Server-Sent Events** with JSON-encoded tokens.

**Content-Type:** `text/event-stream`

**Event Types:**
1. **Token events** (default) — JSON-encoded string tokens
2. **`sources`** event — JSON array of citation objects (emitted before `[DONE]`)
3. **`[DONE]`** — stream termination signal

**Token Event Format:**
```
data: <json-encoded-token>\n\n
```

Example sequence:
```
data: "Decorators "\n\n
data: "are "\n\n
data: "functions "\n\n
data: "that "\n\n
...
```

Tokens are JSON-encoded strings (wrapped in quotes, escaped newlines) to safely handle
special characters. Parse each token with `JSON.parse(data)`.

**Sources Event Format:**
```
event: sources\n
data: <json-array>\n\n
```

The `sources` event is emitted **before** the `[DONE]` terminator when RAG citations are available.

Example sources array:
```json
[
  {
    "chunk_id": "chk_abc123",
    "video_id": "vid_xyz",
    "video_title": "Advanced Python Patterns",
    "video_url": "https://www.youtube.com/watch?v=abc123xyz",
    "start_seconds": 145.5,
    "end_seconds": 162.3,
    "snippet": "...decorators wrap a function to extend its behavior..."
  }
]
```

**`[DONE]` Terminator:**
```
data: [DONE]\n\n
```

**Full Example SSE Stream:**
```
data: "Decorators "\n\n
data: "are "\n\n
data: "functions "\n\n
event: sources\n
data: [{"chunk_id":"chk_abc","video_title":"Advanced Python","video_url":"https://www.youtube.com/watch?v=abc123","start_seconds":145,"end_seconds":162,"snippet":"Decorators are functions..."}]\n\n
data: [DONE]\n\n
```

#### Error Responses

| Status | Condition |
|--------|-----------|
| `400` | `content` is empty or whitespace-only |
| `404` | Conversation not found (or belongs to another user) |
| `429` | Rate limit exceeded (25 messages per 24 hours) |

**Rate limit `429` response body:**
```json
{
  "error": "rate_limit_exceeded",
  "limit": 25,
  "window_hours": 24,
  "reset_at": "2026-03-02T10:00:00Z"
}
```

---

## Videos

### `GET /api/videos`

List all ingested videos.

**Auth:** Required

**Response `200`:**
```json
[
  {
    "id": "vid_xyz",
    "youtube_video_id": "dQw4w9WgXcQ",
    "title": "Introduction to Python",
    "description": "Learn Python basics...",
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "created_at": "2026-03-01T10:00:00Z"
  }
]
```

---

## Ingest

### `POST /api/ingest`

Ingest a video with full metadata and transcript, triggering the chunk → embed → store pipeline.

**Auth:** Required

**Request body:**
```json
{
  "title": "Advanced Python Patterns",
  "description": "Deep dive into Python decorators and context managers",
  "url": "https://www.youtube.com/watch?v=abc123xyz",
  "transcript": "Welcome to this video on Python decorators...",
  "segments": [
    {
      "start": 0.0,
      "end": 15.5,
      "text": "Welcome to this video on Python decorators..."
    },
    {
      "start": 15.5,
      "end": 32.1,
      "text": "Let's start by understanding what a decorator is..."
    }
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | Yes | Video title (non-empty) |
| `description` | string | Yes | Short description (non-empty) |
| `url` | string (URL) | Yes | Valid YouTube video URL |
| `transcript` | string | Yes | Full transcript text (non-empty) |
| `segments` | array | No | Timestamped segments from Supadata. Each must have `start`, `end` (floats), and `text` (string). If provided, real timestamps are stored. Otherwise, timestamps are estimated evenly. |

**Response `200`:**
```json
{
  "video_id": "vid_abc123",
  "chunks_created": 47,
  "status": "ok"
}
```

**Response `422`** — validation error (empty field, malformed segment, etc.).

**Response `502`** — embeddings API request failed.

#### Ingest Pipeline Flow

```
1. VIDEO RECORD ──► POST /api/ingest {title, desc, url, transcript}
                           │
                           ▼
2. CHUNKING ──► Docling HybridChunker (max_tokens=512)
                           │
                           ▼
3. EMBEDDING ──► OpenRouter text-embedding-3-small (1536-dim)
                           │
                           ▼
4. STORAGE ──► Postgres chunks table with embeddings
                           │
                           ▼
5. RESPONSE ──► { video_id, chunks_created, status }
```

If `segments` are provided, the timestamped chunking path is used (`chunk_video_timestamped`),
preserving exact Supadata timestamps. Otherwise, plain chunking (`chunk_video_fallback`) estimates
timestamps evenly across the transcript.

---

### `POST /api/ingest/from-url`

Ingest a YouTube video by URL alone — fetches transcript and metadata via Supadata.

**Auth:** Required

**Request body:**
```json
{ "url": "https://www.youtube.com/watch?v=abc123xyz" }
```

**Response `200`:**
```json
{
  "video_id": "vid_abc123",
  "chunks_created": 52,
  "status": "ok"
}
```

**Response `400`** — invalid YouTube URL.

**Response `503`** — Supadata rate-limited or transcript fetch failed.

**Response `502`** — Supadata or embeddings API unavailable.

**Flow:**
```
URL ──► Supadata API ──► Fetch transcript + title + description ──► Ingest Pipeline
```

---

## Channels

### `POST /api/channels/sync`

Enumerate all videos from the configured YouTube channel via Supadata and ingest any new ones.
Idempotent by `youtube_video_id` — already-ingested videos are skipped.

**Auth:** Required

**Query params:**
| Param | Type | Description |
|-------|------|-------------|
| `limit` | integer | Max videos to process. Defaults to full channel. Supadata returns newest-first. |

**Response `200`:**
```json
{
  "sync_run_id": "run_abc123",
  "status": "completed",
  "videos_total": 150,
  "videos_new": 3,
  "videos_error": 0
}
```

**Response `400`** — `YOUTUBE_CHANNEL_ID` or `SUPADATA_API_KEY` not configured.

**Response `502`** — failed to enumerate channel videos from Supadata.

> **Note:** This is a synchronous, sequential operation — all videos are processed before the
> HTTP response is returned. Set an appropriate request timeout on the caller.

---

### `GET /api/channels/sync-runs`

List the 10 most recent channel sync runs, ordered newest first.

**Auth:** Required

**Response `200`:**
```json
{
  "sync_runs": [
    {
      "id": "run_abc123",
      "status": "completed",
      "videos_total": 150,
      "videos_new": 3,
      "videos_error": 0,
      "started_at": "2026-03-01T10:00:00Z",
      "finished_at": "2026-03-01T10:15:00Z"
    }
  ]
}
```

---

## Error Responses

All error responses follow a consistent shape:

```json
{
  "detail": "Error message describing what went wrong"
}
```

| Status | Meaning |
|--------|---------|
| `400` | Bad request — invalid input or configuration |
| `401` | Unauthorized — missing or invalid session |
| `403` | Forbidden — authenticated but not permitted |
| `404` | Not found — resource does not exist |
| `409` | Conflict — duplicate resource |
| `422` | Validation error — request body failed Pydantic validation |
| `429` | Rate limit exceeded |
| `500` | Internal server error |
| `502` | Bad gateway — external API (Supadata, OpenRouter) failed |
| `503` | Service unavailable |

---

## Auth (Public)

### `POST /api/auth/signup`

Create a new user account.

**Auth:** None

**Request body:**
```json
{ "email": "user@example.com", "password": "securepassword" }
```

**Response `201`:**
```json
{ "id": "user_xyz", "email": "user@example.com" }
```

**Response `409`** — email already registered.

---

### `POST /api/auth/login`

Authenticate and establish a session cookie.

**Auth:** None

**Request body:**
```json
{ "email": "user@example.com", "password": "securepassword" }
```

**Response `200`:**
```json
{ "id": "user_xyz", "email": "user@example.com" }
```
Sets a session cookie on the client.

**Response `401`** — invalid credentials.

---

### `POST /api/auth/logout`

Destroy the current session.

**Auth:** Required

**Response `204`** — No content.

---

### `GET /api/auth/me`

Get the currently authenticated user.

**Auth:** Required

**Response `200`:**
```json
{ "id": "user_xyz", "email": "user@example.com" }
```

---
