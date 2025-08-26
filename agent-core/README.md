# Alfred Agent Core — MVP Development Playbook

> **Purpose**: This document is the single source of truth for building the Alfred Agent Core MVP. Paste this at the start of Claude Code sessions. It defines the roadmap, success metrics, API contracts, data models, ops guardrails, and work packets.

---

## TL;DR (what we're building)

- **FastAPI Agent Core** that routes to **MCP** tools (existing remote MCPs + Hosted Notion MCP via OAuth).
- **Pydantic AI** orchestrator with a minimal tool adapter.
- **Three-phase caching** (start thin/in-memory → Postgres).
- **Backend-run Notion OAuth** with encrypted token storage + refresh.
- **Sessions** and **Postgres-backed cache** to hit latency & token targets.
- **SSE streaming** and production hardening at the end.

## Global Constraints (for every task)

- **Stack**: FastAPI + Pydantic AI + PostgreSQL + MCP (Streamable HTTP)
- **Target Metrics**: ≤3s P95 latency, ~90% cache hit on repeat reads, 80% token reduction vs uncached
- **Budget/Runtime**: Single droplet, performance-first, ~$50–100/mo
- **Style**: Ship first, optimize later, add tests minimally where they lock behavior

---

## Success Metrics (MVP)

- **Latency**: ≤ **3s P95** end-to-end on cached reads.
- **Token Efficiency**: ~**80% reduction** vs uncached baselines.
- **Cache**: ~**90% hit rate** on repeated reads in common flows.
- **Reliability**: Clean retries on transient MCP failures; consistent error taxonomy.
- **Cost**: Single DO droplet, **$50–$100/mo** range (perf-first).

---

## Architecture Overview

```mermaid
flowchart LR
  Client[[Web/PWA/CLI]] -->|HTTP| FastAPI

  subgraph FastAPI Agent Core
    Router[MCP Router] --> Orchestrator[Pydantic AI Agent]
    Orchestrator --> Cache[Cache Service]
    Router --> RemoteMCPs[(Remote MCPs)]
    Router --> NotionMCP[(Hosted Notion MCP)]
    OAuth[OAuth Manager] --> DB[(PostgreSQL)]
    Sessions[Session Store] --> DB
    Cache --> DB
  end

  NotionMCP -->|per-user token| NotionCloud[(mcp.notion.com)]
```

**MCP Router**: Manages connections to existing remote MCPs + per-user Notion MCP.

**OAuth Manager**: Backend-only auth code flow, tokens encrypted at rest, proactive refresh.

**Cache Service**: Read-through; in-memory first, then Postgres with TTL policies.

**Session Store**: Conversation context + active Notion workspace per session.

---

## 4-Week Delivery Plan

Each week has: **Goals** → **Deliverables** → **Artifacts** → **Acceptance Criteria (AC)**

### Week 1 — Core Agent, MCP routing, thin cache

**Goals**
- FastAPI app skeleton online
- Call existing remote MCPs
- Introduce a thin cache (in-memory) wrapper so you see immediate wins
- Stable API shape for future streaming (but implement non-streaming first)

**Deliverables**
- **Service skeleton**: endpoints: `GET /healthz`, `POST /chat`; config loader, structured logging, error middleware, request IDs; minimal auth (static API key header) to unblock later rate-limiting
- **MCP Router (remote only)**: registry of your existing remote MCPs; tool discovery cached 60s; connection health pings
- **Pydantic AI orchestration**: single provider (start: Anthropic); adapter that exposes MCP tools to the agent
- **Thin cache v0 (in-memory)**: intercept read-type tool calls; log cache_hit/cache_miss, TTL config in env
- **Ops**: env templates, run scripts, Dockerfile that builds locally and for DO

**Artifacts**
- README quickstart (run, env, curl examples)
- `.env.example` (see Env spec below)
- OpenAPI doc auto-exposed at `/docs`
- Sequence doc (markdown): request → router → MCP → cache (for onboarding)

**AC**
- `POST /chat` can call at least two remote MCPs and return a reply
- Repeated calls with same params show `cache_hit=true` in response metadata
- P95 latency for a repeated read < 500ms (local/dev)

### Week 2 — Notion OAuth + Hosted Notion MCP (per-user)

**Goals**
- Fully backend-owned OAuth flow for Notion
- Tokens encrypted at rest; refresh flow in place
- Use hosted Notion MCP with the user's token (feature-flag fallback to self-hosted MCP)

**Deliverables**
- **OAuth Manager**: endpoints: `GET /connect/notion` (redirect), `GET /oauth/notion/callback` (code exchange); token store: access_token, refresh_token, expires_at, workspace_id/name; encryption at rest via symmetric key (e.g., Fernet) pulled from env/DO secret; refresh 5 minutes before expiry; mark connection invalid if refresh fails
- **Per-user Notion MCP client**: factory that establishes a per-user session with hosted Notion MCP using the fresh token; MCP Router enhanced to expose Notion tools only if user connected
- **Minimal web page for consent**: `GET /connect` static page with "Connect Notion" button → hits backend → completes → success/failure page
- **Feature flags**: `FEATURE_NOTION_HOSTED_MCP=true|false`; `FEATURE_NOTION_SELF_HOST_FALLBACK=true|false`

**Artifacts**
- DB migrations for `users` and `notion_connections`
- Short ops note: how to register Notion app & set callback URLs
- Threat model note (1 page): where tokens live, who can read, rotation procedure

**AC**
- User can connect Notion via `/connect/notion` and see Notion tools appear for their session
- Calling a Notion read tool succeeds via hosted MCP
- Token refresh works (manually set short expiry in dev to verify)

### Week 3 — Sessions + PostgreSQL cache + token metering

**Goals**
- Session continuity (conversation context, active workspace)
- Move cache to PostgreSQL with read-through and TTLs per tool
- Basic token usage tracking per request/session

**Deliverables**
- **Session Store**: `user_sessions` table; create/resume/expire; per-session context blob (JSON); bind session → user → active Notion workspace
- **CacheService (Postgres backend)**: `agent_cache` table; key, jsonb content, content_hash, expires_at, hit_count; read-through/write-through middleware for tools; TTL policy map (env overrides)
- **Token usage**: request-scoped counters surfaced in `meta.tokens.used` and persisted per session; warn in response meta at 80% target context budget
- **Invalidate pathways**: `forceRefresh=true` query opt-in; automatic invalidation on write-type tools (if any present)

**Artifacts**
- Schema doc & ERD image (markdown with Mermaid is fine)
- Cache key spec (see below)
- Ops note: psql maintenance, basic vacuum/autovacuum settings

**AC**
- Multi-request conversation retains context via session token
- Cache hit ratio >70% on repeated reads in a scripted test
- `meta.tokens.used` increments correctly and shows warnings over threshold

### Week 4 — SSE streaming + hardening + docs/tests

**Goals**
- SSE endpoint for streaming responses
- Productionizers: rate limiting, timeouts, connection pooling, security headers
- Minimal integration tests + deployment verification

**Deliverables**
- **SSE interface**: `GET /chat/stream` with event types: token, tool_call, tool_result, cache_hit, warning, done; heartbeat every 15s; idempotent reconnect with Last-Event-ID
- **Hardening**: global timeouts (connect/read), DB pool config, retries to MCP once; rate limits (simple leaky bucket in-memory; room to swap to Redis later); security headers (CORS, HSTS if TLS is terminated at proxy), request size limits
- **Docs & tests**: runbook for deployment on droplet; integration tests: OAuth happy path, cache hit path, SSE stream and reconnect

**AC**
- Browser (or curl) can stream tokens/events for a chat
- Production instance handles basic load without 5xx spikes; logs include request_id and timing

---

## API Contracts (Stable)

### `GET /healthz`
```
200: {"status":"ok","version":"x.y.z"}
```

### `POST /chat` (non-streaming)
**Request**
```json
{
  "messages":[{"role":"user","content":"..."}],
  "session":"optional-session-token",
  "forceRefresh": false
}
```

**Response**
```json
{
  "reply":"...",
  "meta":{
    "cacheHit": false,
    "cacheTtlRemaining": null,
    "tokens":{"input":0,"output":0},
    "requestId":"..."
  }
}
```

### `GET /chat/stream` (Week 4, SSE)

**Endpoint**: `GET /chat/stream?session=...`

**Events**:
- `event: token` → `data: {"text":"..."}`
- `event: tool_call` → `data: {"tool":"notion.get_page","args":{...}}`
- `event: tool_result` → `data: {"tool":"...","cacheHit":true,"elapsedMs":42}`
- `event: warning` → `data: {"code":"CONTEXT_NEAR_LIMIT","detail":"..."}`
- `event: done` → `data: {"tokens":{"input":..., "output":...}}`

**Headers**: `Cache-Control: no-cache`, `X-Accel-Buffering: no`

**Reconnect**: support `Last-Event-ID`; emit heartbeat `:keepalive` every 15s

### OAuth
- `GET /connect/notion` → 302 to Notion
- `GET /oauth/notion/callback?code=...&state=...` → stores tokens, renders success/failure page

---

## Error Taxonomy (Use Everywhere)

`APP-4XX-VALIDATION`, `APP-401-AUTH`, `APP-429-RATE`, `APP-5XX`

`MCP-TIMEOUT`, `MCP-UNAVAILABLE`, `MCP-BAD-REQUEST`

`OAUTH-EXCHANGE-FAIL`, `OAUTH-REFRESH-FAIL`

**Error shape (example):**
```json
{
  "error":"OAUTH-REFRESH-FAIL",
  "message":"Unable to refresh Notion token.",
  "origin":"oauth",
  "requestId":"..."
}
```

---

## Logging Schema

**Request**: ts, level, request_id, route, method, user_id, session_id

**Timing**: duration_ms, db_ms, mcp_ms

**Cache**: cache_key, cache_hit, ttl_remaining

**Tokens**: input_tokens, output_tokens, session_tokens_total

**Outcome**: status, error_code (when applicable)

**Redaction** (never log): access_token, refresh_token, authorization, cookie, set-cookie, FERNET_KEY, DB creds.

### Detailed Logging & Metrics Fields

**Request**: request_id, route, method, user_id, session_id

**Timing**: duration_ms, mcp_attempts, db_time_ms

**Cache**: cache_key, cache_hit, ttl_remaining

**Tokens**: input_tokens, output_tokens, session_tokens_total

**Errors**: error_code, origin (mcp, db, oauth, app)

---

## Data Model (Week 2–3)

### `users`
- `id` (uuid), `email` (text unique), timestamps

### `notion_connections`
- `id` (uuid), `user_id` (uuid fk)
- `workspace_id` (text), `workspace_name` (text)
- `access_token_enc` (bytea), `refresh_token_enc` (bytea), `expires_at` (timestamptz)
- `created_at`, `updated_at`
- Unique (`user_id`, `workspace_id`)

### `user_sessions`
- `id` (uuid), `user_id` (uuid fk)
- `session_token` (text unique)
- `conversation_context` (jsonb) *(no PII; tool-relevant only)*
- `active_notion_workspace` (text nullable)
- `created_at`, `last_accessed`, `expires_at`

### `agent_cache`
- `id` (bigserial), `cache_key` (text unique)
- `content` (jsonb), `content_hash` (text)
- `expires_at` (timestamptz), `hit_count` (int default 0)
- `created_at`, `updated_at`, `last_accessed`

---

## Cache Policy

**Cache key shape**
- `"{tool}:{version}:{normalized_args_hash}"`
- `tool`: e.g., notion.get_page, notion.search
- `version`: bump when response schema changes
- `normalized_args_hash`: stable JSON with sorted keys → sha256

**Never cache**: errors, 4xx, empty results.

**Metadata**: attach `{cacheHit: boolean, cacheTtlRemaining: seconds}` in meta

**TTL suggestions**:
- `notion.get_page`: 24h (active doc), 4h (default)
- `notion.search`: 4h
- `github.get_repo`: 24h
- dynamic/listing endpoints: 1–4h depending on volatility

**Invalidation**
- `forceRefresh=true` bypasses cache & rewrites
- Auto-invalidate on any write tool you expose (if/when added)
- Response meta: include `cacheHit` and `cacheTtlRemaining`

---

## OAuth Pattern (Notion, Week 2)

**Flow**: Auth Code (backend-only).

**Store**: `workspace_id`, `workspace_name`, encrypted `access_token`/`refresh_token`, `expires_at`.

**Encryption**: symmetric key (Fernet) from env/secret store.

**Refresh**: eager 5 minutes before expiry. On failure: mark connection invalid + return `OAUTH-REFRESH-FAIL` (user-safe).

**Feature flags**:
- `FEATURE_NOTION_HOSTED_MCP=true|false`
- `FEATURE_NOTION_SELF_HOST_FALLBACK=true|false`

---

## Session Management (Week 3)

Server-generated `session_token` identifies conversation context and active workspace.

Update `last_accessed` every request; default expiry 7 days idle (configurable).

Context stores only tool-relevant state (no PII).

---

## Streaming (Week 4)

SSE with event stream; flush per event to avoid buffering.

**Backpressure**: keep messages small; no batching of tokens.

**Reconnect** via `Last-Event-ID`; send heartbeat comments (`:keepalive`) every 15s.

---

## Timeouts, Retries, Budgets

**Outbound HTTP (MCP)**: connect=2s, read=10s, 1 retry (idempotent only) with jitter.

No retries for write tools (POST/side-effects).

**`/chat` total non-streaming budget**: 30s. Streaming is long-lived.

---

## Security & Ops

Secrets never logged; redaction middleware in place.

API key header for dev; planned rate limits prod (Week 4).

**CORS**: dev permissive; prod whitelist origins.

**Headers**: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Strict-Transport-Security` (if behind TLS).

---

## Env Vars (`.env.example`)

```ini
APP_ENV=dev
APP_PORT=8080
LOG_LEVEL=INFO
API_KEY=change-me

# Model
MODEL_PROVIDER=anthropic
MODEL_NAME=claude-3-x  # set actual model you'll use

# Database
DB_URL=postgresql+psycopg://user:pass@host:5432/agent

# Crypto
FERNET_KEY=base64-32bytes

# Cache
CACHE_BACKEND=memory  # week 1; postgres in week 3
CACHE_DEFAULT_TTL_SECONDS=14400
CACHE_NOTION_PAGE_TTL_SECONDS=86400
CACHE_NOTION_SEARCH_TTL_SECONDS=14400

# Notion OAuth
NOTION_CLIENT_ID=...
NOTION_CLIENT_SECRET=...
NOTION_REDIRECT_URI=https://your.domain/oauth/notion/callback

# Feature flags
FEATURE_NOTION_HOSTED_MCP=true
FEATURE_NOTION_SELF_HOST_FALLBACK=false
```

---

## Repository Layout (create Week 1 Day 1)

```
/agent-core
  ├─ src/
  │   ├─ app.py                # FastAPI init, routers mount
  │   ├─ config.py             # env loader
  │   ├─ logging.py            # structured logs
  │   ├─ routers/
  │   │   ├─ health.py
  │   │   ├─ chat.py           # /chat, /chat/stream
  │   │   └─ oauth.py          # /connect/notion, /oauth/notion/callback
  │   ├─ services/
  │   │   ├─ mcp_router.py
  │   │   ├─ oauth_manager.py
  │   │   ├─ session_store.py
  │   │   ├─ cache_service.py
  │   │   └─ agent_orchestrator.py
  │   ├─ clients/
  │   │   ├─ mcp_client.py
  │   │   └─ notion_mcp_client.py
  │   ├─ db/
  │   │   ├─ models.py
  │   │   ├─ migrations/
  │   │   └─ schema.sql
  │   └─ utils/
  │       ├─ crypto.py         # encrypt/decrypt tokens
  │       └─ timing.py
  ├─ tests/
  │   ├─ integration/
  │   └─ smoke/
  ├─ Dockerfile
  ├─ docker-compose.yml        # local dev only
  ├─ Makefile                  # run, test, fmt, lint, db-migrate
  ├─ .env.example
  ├─ README.md
  └─ ADRs/                     # short architecture decisions
```

---

## GitHub Project Setup (do this once, Day 1)

### Project
- **Columns**: Backlog → Ready → In Progress → Review → Done → Blocked
- **Milestones**: MVP-W1, MVP-W2, MVP-W3, MVP-W4

### Labels
- `area:api`, `area:mcp`, `area:oauth`, `area:cache`, `area:db`, `area:infra`, `type:bug`, `type:feat`, `type:chore`, `good-first`, `blocked`

### Issue templates
- **Feature**: Summary, Rationale, Scope, Out-of-scope, API changes, Data changes, Acceptance criteria, Rollout/flags
- **Bug**: Steps to Reproduce, Expected, Actual, Logs, Scope of impact, Fix Plan
- **Tech Debt**: Context, Risk, Proposed change, Effort, Acceptance

### PR template
- What/Why, How tested, Breaking changes, Screens, Rollout flag, Checklist (lint/tests)

### Branch naming
- `feat/<issue-number>-<slug>`
- One PR per issue with AC restated + verification evidence (curl/logs/screens)

### ADRs (short)
- ADR-001: Streamable HTTP MCP vs SSE
- ADR-002: Token encryption with Fernet
- ADR-003: Cache policy & key format
- ADR-004: SSE protocol design

---

## Work Packets for Claude Code (Paste as prompts)

### Packet A — Week 1 baseline
- **Goal**: Scaffold FastAPI app, `/healthz`, `/chat`, MCP Router (remote), thin in-memory cache.
- **Constraints**: No streaming yet; no DB yet; structured logging; `.env.example` present.
- **AC**: `POST /chat` hits at least two remote MCP tools; repeated call logs `cache_hit=true` and shows it in response meta.
- **Deliverables**: Service skeleton, MCP Router, Cache v0, OpenAPI docs, README quickstart.

### Packet B — Week 2 OAuth + Notion MCP
- **Goal**: Backend-run Notion OAuth; per-user hosted Notion MCP session; feature flags.
- **Constraints**: Tokens encrypted with Fernet; DB migrations for `users` & `notion_connections`; basic consent page.
- **AC**: "Connect Notion" works end-to-end; Notion tools appear only for connected user; refresh verified.
- **Deliverables**: OAuth Manager, Notion MCP client factory, flags, ops notes.

### Packet C — Week 3 Sessions + PG Cache + Tokens
- **Goal**: Session continuity; Postgres cache backend; token metering.
- **Constraints**: `agent_cache` read-through/write-through; TTLs configurable; `forceRefresh=true` supported.
- **AC**: Cache hit ratio >70% on scripted repeat queries; session context persists; `meta.tokens` present.
- **Deliverables**: Session Store, Cache PG backend, token counters, schema doc/ERD.

### Packet D — Week 4 Streaming + Hardening
- **Goal**: SSE streaming with heartbeats & reconnect; rate limits; timeouts; basic tests.
- **Constraints**: Event protocol as specified; idempotent reconnect; one MCP retry on transient failures.
- **AC**: Live stream visible via curl/browser; no 5xx spikes under light load; integration tests green.
- **Deliverables**: `/chat/stream`, security headers, rate limiting, test suite, deployment runbook.

---

## Day 0 Checklist

- Python 3.11+, package manager chosen/pinned (uv/poetry/pip-tools).
- `.env` from `.env.example` with dev-safe values.
- Makefile targets: `run`, `build`, `lint`, `fmt`, `test`, `db-migrate`.
- `pre-commit` with `ruff`/`black`/`isort`.
- `curl` or `.http` examples for `/healthz`, `/chat`.
- Decide port 8080; confirm Docker/compose runs.

---

## Testing (Minimal, MVP)

**Smoke**: `/healthz` 200; `/chat` returns JSON with `meta.cacheHit`.

**Week 2**: OAuth happy path; short-expiry refresh test.

**Week 3**: Cache hit ratio script and session continuity.

**Week 4**: SSE stream + reconnect test; MCP transient failure retry.

---

## Issue List (ready to paste; grouped by milestone)

### Milestone MVP-W1
- feat(api): scaffold FastAPI app with /healthz and /chat
- chore(config): env loader & .env.example
- chore(logs): structured logging with request IDs
- feat(mcp): implement MCP Router for existing remote MCPs (tool discovery + health)
- feat(agent): wire Pydantic AI and MCP tool adapter
- feat(cache): thin in-memory cache middleware with TTL + hit/miss logging
- docs(readme): quickstart, curl examples
- chore(docker): Dockerfile and compose for local dev

### Milestone MVP-W2
- feat(db): add migrations for users and notion_connections
- feat(oauth): /connect/notion and /oauth/notion/callback endpoints
- feat(security): encrypt tokens at rest with Fernet
- feat(oauth): token refresh scheduler (eager refresh 5m before expiry)
- feat(mcp): Notion MCP client factory (per-user)
- feat(feature-flags): hosted vs self-host fallback flags
- feat(ui): simple consent page & success/failure
- docs(ops): Notion app registration + callback URL guide

### Milestone MVP-W3
- feat(db): migrations for user_sessions and agent_cache
- feat(session): session create/resume/expire + active workspace binding
- feat(cache): Postgres backend read-through/write-through with TTL policy map
- feat(cache): forceRefresh parameter & write-path invalidation
- feat(metrics): token usage counters surfaced in response meta & persisted
- docs(schema): ERD + cache key/TTL policy document

### Milestone MVP-W4
- feat(api): /chat/stream SSE endpoint with events & heartbeat
- feat(hardening): rate limiting, timeouts, request size limits, security headers
- chore(db): pool tuning, basic retry on MCP failures
- test(int): OAuth happy path, cache hit path, SSE reconnect
- docs(runbook): deployment and rollback guide

---

## Definitions of Done (per area)

- **API**: endpoint documented in OpenAPI; returns structured error on failure; logs include timing.
- **OAuth**: tokens encrypted; refresh verified; revocation documented.
- **Cache**: keys deterministic; TTLs configurable; metrics visible; bypass supported.
- **Sessions**: context survives process restarts; expiry respected; last_accessed updated.
- **Streaming**: heartbeats emitted; reconnect safe; "done" event terminates stream.
- **Security/Ops**: timeouts set; rate limits applied; secrets never logged.

---

## If Schedule Slips (cut-lines)

- Ship Week 4 without rate limiting → add in a patch
- Keep cache Postgres but defer write-path invalidation heuristics (retain `forceRefresh`)
- One model provider only; defer multi-model orchestration

---

## Glossary

**MCP**: Model Context Protocol. Tools/servers the agent calls.

**Hosted Notion MCP**: mcp.notion.com with per-user token auth.

**Pydantic AI**: Agent orchestration and tool calling adapter.

**TTL**: Time-to-live for cached entries.

**SSE**: Server-Sent Events (one-way streaming from server to client).

---

## Verification Snippets (for PR bodies)

### `/healthz`
```bash
curl -s localhost:8080/healthz
# -> {"status":"ok","version":"x.y.z"}
```

### `/chat` (repeat to see cache)
```bash
curl -s -X POST localhost:8080/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"messages":[{"role":"user","content":"read X"}]}'
# -> {"reply":"...","meta":{"cacheHit":false,...}}

curl -s -X POST localhost:8080/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"messages":[{"role":"user","content":"read X"}]}'
# -> {"reply":"...","meta":{"cacheHit":true,...}}
```

---

## What Good Looks Like (Milestone Heuristics)

- **W1**: End-to-end with ≥2 MCP tools; cache hits logged & surfaced.
- **W2**: OAuth + hosted Notion MCP functioning; refresh proven.
- **W3**: >70% cache hits on repeats; session continuity; token meta present.
- **W4**: Streaming with heartbeats; reconnect safe; deploy runbook complete.