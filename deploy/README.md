# Deploy

Production deployment via Docker Compose. Runs Caddy (TLS + reverse proxy) and Postgres (pgvector).

## First-time setup on a new VPS

1. Install Docker: https://docs.docker.com/engine/install/
2. Clone this repo to `/opt/dynachat/` (owned by a dedicated `dynachat` user, `chmod 700`)
3. Copy `.env.example` to `.env`, fill in real values (`chmod 600`)
4. Point DNS A record for your subdomain at the VPS public IP
5. `cd deploy && docker compose up -d`
6. Caddy auto-provisions a Let's Encrypt cert on first request

## Files

- `docker-compose.yml` - Caddy + Postgres services
- `Caddyfile` - reverse-proxy config (TLS + subdomain routing)
- `.env.example` - secret template (committed); real `.env` is gitignored

## Ports

- `80` / `443` (public) - Caddy
- `127.0.0.1:5433` (loopback only) - Postgres

## Environment variables

The app container reads these from `/opt/dynachat/.env` via docker-compose:

| Variable | Required | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | **yes** | OpenRouter embeddings + chat |
| `SUPADATA_API_KEY` | prod only | YouTube transcript fetch |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | **yes** | Postgres credentials used by both the `postgres` service and the app's `DATABASE_URL` |
| `JWT_SECRET` | **yes** (auth) | 32+ random bytes used to sign session-cookie JWTs. Generate with `openssl rand -hex 32`. Rotating this value invalidates all live sessions |
| `ADMIN_USER_EMAIL` | optional | Email of the single admin user (case-insensitive match). When unset, every `/api/admin/*` endpoint returns 403. Match MUST equal the email the admin registered with |

The app's `DATABASE_URL` is assembled from the `POSTGRES_*` values inside
`docker-compose.yml` — you do **not** set it directly in `.env`. It points at
the in-compose `postgres` service by DNS name.

Minimal `.env` for a fresh deploy:

```
OPENROUTER_API_KEY=sk-or-...
SUPADATA_API_KEY=...
POSTGRES_USER=dynachat
POSTGRES_PASSWORD=<random>
POSTGRES_DB=dynachat
JWT_SECRET=<openssl rand -hex 32>
ADMIN_USER_EMAIL=admin@yourdomain.com
```

## Secret hygiene

The real `.env` lives ONLY on the deploy host, in a directory owned by a non-factory user with mode 600. It is never committed, never shared via chat, and never readable by the Dark Factory workflow user.

## Automated YouTube channel sync

`deploy/sync-channel.sh` runs a one-shot YouTube sync inside the active app
container by `docker exec`-ing into the color named in `upstream.conf`. Two
systemd units in `deploy/systemd/` drive it on a schedule:

- `dynachat-channel-sync.service` — one-shot, calls `sync-channel.sh`
- `dynachat-channel-sync.timer`   — every hour, with a 5-min jitter

### Install on a host

```bash
# As root, from the repo checkout (typically /opt/dynachat/app/)
install -m 0644 deploy/systemd/dynachat-channel-sync.service /etc/systemd/system/
install -m 0644 deploy/systemd/dynachat-channel-sync.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now dynachat-channel-sync.timer

# Verify
systemctl list-timers dynachat-channel-sync.timer
journalctl -u dynachat-channel-sync.service -n 20
```

### Trigger a sync manually

```bash
# Full sync (newest first, stops once Supadata is exhausted)
systemctl start dynachat-channel-sync.service

# Or run the wrapper directly with custom args (e.g. cap to 20 newest videos)
/opt/dynachat/app/deploy/sync-channel.sh --limit 20
```

The wrapper is idempotent — already-ingested videos are skipped by
`youtube_video_id` unless `--force` is passed (used to backfill new chunk
schemas; see `routes/channels.py`'s `force` flag).

## SQLite → Postgres cutover runbook

When migrating an existing production deployment from SQLite to Postgres:

### Prerequisites
- Postgres must be running and healthy (`postgres` service up)
- `DATABASE_URL` must be set correctly in `.env`
- `alembic.ini` must be present in the app container

### Step 1 — Snapshot SQLite (before cutover)
```bash
# On the host, inside the app container or at app/backend/data/
./scripts/dump_sqlite.sh
# Or manually:
cp /app/data/chat.db /app/data/chat.db.$(date +%s).bak
```

### Step 2 — Run Alembic migrations (first deploy with new build)
The app runs `alembic upgrade head` automatically on startup.
Verify it succeeded:
```bash
docker compose exec app-blue alembic --config /app/backend/alembic.ini current
# Should show: 0001 (or latest revision)
```

### Step 3 — Copy data from SQLite to Postgres (one-time)
```bash
# Run the migration script inside the app container
docker compose exec app-blue python -m backend.scripts.migrate_sqlite_to_pg /app/data/chat.db
# The script will prompt for DATABASE_URL (use the same one from .env)
```

### Step 4 — Verify
```bash
# Check row counts match between snapshot and Postgres
docker compose exec postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c 'SELECT count(*) from videos'
docker compose exec postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c 'SELECT count(*) from chunks'
```

### Step 5 — Restart app (ensures clean pool state)
```bash
docker compose restart app-blue app-green
```
