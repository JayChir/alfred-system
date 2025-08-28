# Alfred System — `context.md`

> **Purpose**
> This document gives any engineer or AI code assistant (e.g., Cursor) the **full context** to work productively on the Alfred System. It explains the **goals, constraints, architecture, success metrics, and working conventions** so new sessions can jump in without reloading the whole project history.

---

## 1) What Alfred Is (North Star)

**Alfred** is a cloud-hosted, model-agnostic assistant platform—think **"Cursor for your life."**
It captures tasks/notes anywhere, understands your personal/work context, and acts through your tools (Notion, Gmail, Calendar, GitHub, etc.) using **MCP** (Model Context Protocol). It favors **speed, token efficiency, reliability, and mobile capture**.

---

## 2) Success Metrics (MVP → SaaS)

- **Latency:** ≤ **3s P95** end-to-end on cached reads; warm reads target **<500ms**.
- **Token Efficiency:** ~**80% reduction** vs. uncached flows (via caching + staging).
- **Cache:** **~90% hit rate** on repeated reads for common objects.
- **Mobile Capture:** **95% capture success**, **<1s acknowledgment**.
- **Reliability:** **99.5% uptime**, **zero context loss incidents**.
- **Cost:** Single droplet, **$50–$100/mo** (performance-first).
- **Velocity:** Ship first, optimize after; small vertical slices, one PR per issue.

---

## 3) Product Scope (initial personas & value)

- **Individual knowledge workers** who live in Notion/Gmail/Calendar/GitHub.
- **ADHD-friendly UX:** capture anywhere, proactive meeting prep, clear escalation for reminders.
- **Cross-device:** Claude Desktop, Web PWA (streaming), Mobile PWA (voice + offline queue), Terminal/CLI.

---

## 4) Architecture (high level)

- **Host/Infra:** DigitalOcean droplet (Docker), **FastAPI** app server, **PostgreSQL** DB, **FastMCP** stack.
- **Core:** Pydantic-AI orchestrator, **MCP Router** with client connections to hosted/remote services, **Session Store**, **Cache Service**.
- **MCP Architecture:**
  - **MCP Client** connecting to Notion's hosted MCP server (https://mcp.notion.com/mcp) with OAuth tokens
  - Existing remote MCP servers (GitHub, Memory, Gmail/Calendar, Time, Web, etc.)
- **Interfaces:** Claude Desktop (MCP), Web PWA (SSE streaming), Mobile PWA (voice/offline), Terminal/SSH CLI.
- **Transport:** Streamable HTTP MCP for servers; **SSE** for client streaming responses.

```mermaid
flowchart LR
  Client[[Web/Mobile/CLI/Claude]] -->|HTTP/SSE| FastAPI

  subgraph FastAPI Agent Core
    Router[MCP Router] --> Orchestrator[Pydantic AI]
    Orchestrator --> Cache[Cache Service]
    Orchestrator --> Sessions[Session Store]
    OAuth[OAuth Manager] --> DB[(PostgreSQL)]
    Cache --> DB
    Sessions --> DB
    Router --> NotionClient[MCP Client → notion.com/mcp]
    Router --> RemoteMCPs[(Remote MCPs: GitHub/Memory/Gmail/...)]
  end
```

---

## 5) Caching & Data Strategy

**Three-tier caching** (aggressive, deterministic):

- **Tier 1: PostgreSQL Cache** — canonical runtime cache (TTL 4–24h based on tool/data volatility).
- **Tier 2: Smart Staging** — batch updates and structured promotion to Notion to avoid refetch churn (~70% API call reduction target).
- **Tier 3: Notion Storage** — source of truth for long-term knowledge.

**Cache key format:**
```
{tool}:{version}:{sha256(sorted_json_args)}
```

Never cache errors/empty results; store `fetched_at`, `ttl_seconds`, `hit_count`.

**Invalidation:** `forceRefresh=true` bypass; automatic invalidation on write tools.

Response metadata must include `cacheHit` and `cacheTtlRemaining`.

---

## 6) Security & Privacy Principles

- **Backend-owned OAuth** (auth code flow), tokens encrypted at rest (Fernet), proactive refresh.
- **No secrets** (tokens, Authorization, cookies, Fernet key, DB creds) ever logged; log scrubber middleware in place.
- **API key auth** in dev; rate limits & strict CORS in prod; security headers (HSTS when behind TLS).
- **Session context** contains tool-relevant state only, no PII beyond what's essential.

---

## 7) Development Philosophy

- **Ship first, optimize later.** Bias toward delivering a vertical slice with real value.
- **Model-agnostic** via Pydantic-AI: start with one provider per request; retain easy swapping.
- **Small PRs**, single issue each (Cursor-friendly).
- **Operational empathy:** observability first (structured logs, timing, cache/tokens in meta).
- **Feature flags** for hosted vs. self-hosted MCPs and experimental features.

---

## 8) Current MVP (what we're building now)

### Week 1 — Core Agent + Remote MCPs + Thin Cache
- FastAPI skeleton: `/healthz`, `/chat` (non-streaming), config/env, error middleware, structured logging.
- MCP Router for existing remote MCPs; 60s tool discovery cache; health pings.
- Pydantic-AI tool adapter.
- Thin cache v0 (in-memory) around idempotent read tools, logs cache hit/miss.

### Week 2 — Notion OAuth + Hosted Notion MCP
- `/connect/notion` redirect + `/oauth/notion/callback` exchange; encrypted token storage; proactive refresh.
- Per-user MCP client connections to Notion's hosted service; feature flag fallback to self-host if needed.
- Minimal consent page.

### Week 3 — Sessions + PostgreSQL Cache + Token Metering
- `user_sessions` & `agent_cache` tables; read-through/write-through PG backend; TTL policy map by tool.
- `meta.tokens.used` counters (request + session), warnings at 80% context capacity.

### Week 4 — SSE Streaming + Hardening + Docs/Tests
- `/chat/stream` SSE (events: token, tool_call, tool_result, warning, done; heartbeat q15s; Last-Event-ID reconnect).
- Timeouts, rate limits, security headers, DB pool tuning; minimal integration tests; deploy runbook.

---

## 9) Roadmap to Full SaaS (high level)

### v1.1–v1.2: Assisted Workflows
- Query classification → subagent routing (Capture/Analysis/Research/Planning).
- Parallel MCP execution for fetch-heavy plans; progressive disclosure in UI.
- Memory quality/rating; staged promotion to Notion; cache write-through.

### v1.5: Proactive Assistant
- Gmail/Calendar listeners (opt-in); meeting prep packets (J-1h, J-10m).
- Intelligent prefetch (pattern-based warming); ADHD-style escalation ladder (push → banner → phone alarm).

### v2.0: Team/Workspace & Billing
- Workspace isolation, RBAC, per-workspace connections; admin console; Stripe billing.

### v2.5: Platform & Extensibility
- Command palette; tool trace view; Subagent SDK contracts; per-task model profiles (still single-model per request).

### v3.0: Autonomy with Guardrails
- "Suggest → simulate → execute" loop; policy engine (budgets, time windows, scopes); feedback-driven tuning.

### v3.5: Scale & SRE
- Horizontal scale; observability dashboards; backup/restore; compliance runway.

---

## 10) Interfaces & Contracts (stable shapes)

### POST /chat (non-streaming)
**Request:**
```json
{
  "messages":[{"role":"user","content":"..."}],
  "session":"optional-session-token",
  "forceRefresh": false
}
```

**Response:**
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

### GET /chat/stream (SSE)
- Events: `token`, `tool_call`, `tool_result`, `warning`, `done`.
- Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
- Heartbeat every 15s; supports `Last-Event-ID` reconnect.

### OAuth (Notion)
- `GET /connect/notion` → 302 to Notion
- `GET /oauth/notion/callback?code=...` → token exchange, encrypted storage, refresh policy

### Error taxonomy (use everywhere)
- `APP-4XX-VALIDATION`, `APP-401-AUTH`, `APP-429-RATE`, `APP-5XX`
- `MCP-TIMEOUT`, `MCP-UNAVAILABLE`, `MCP-BAD-REQUEST`
- `OAUTH-EXCHANGE-FAIL`, `OAUTH-REFRESH-FAIL`

### Logging schema (redacted & structured)
- **Request:** `request_id`, `route`, `method`, `user_id`, `session_id`
- **Timing:** `duration_ms`, `db_ms`, `mcp_ms`
- **Cache:** `cache_key`, `cache_hit`, `ttl_remaining`
- **Tokens:** `input_tokens`, `output_tokens`, `session_tokens_total`
- **Outcome:** `status`, `error_code` (if any)

---

## 11) Required Environment (dev defaults)

```ini
APP_ENV=dev
APP_PORT=8080
LOG_LEVEL=INFO
API_KEY=change-me

# Model (single provider per request; swap by config)
MODEL_PROVIDER=anthropic
MODEL_NAME=claude-3-x  # set the exact model at deploy time

# Database
DB_URL=postgresql+psycopg://user:pass@host:5432/agent

# Crypto
FERNET_KEY=base64-32bytes  # token encryption key

# Cache
CACHE_BACKEND=memory        # week 1; 'postgres' in week 3
CACHE_DEFAULT_TTL_SECONDS=14400
CACHE_NOTION_PAGE_TTL_SECONDS=86400
CACHE_NOTION_SEARCH_TTL_SECONDS=14400

# Notion OAuth
NOTION_CLIENT_ID=...
NOTION_CLIENT_SECRET=...
NOTION_REDIRECT_URI=https://your.domain/oauth/notion/callback

# Flags
FEATURE_NOTION_HOSTED_MCP=true
FEATURE_NOTION_SELF_HOST_FALLBACK=false
```

---

## 12) How to Work on Alfred (for Cursor/agents)

- **One PR per issue**, vertical slice; restate AC in PR and show evidence (curl/logs).
- Follow the **cache policy** and **error taxonomy**—these are contractually important for UX and observability.
- Prefer **idempotent tool calls** and ensure retries only happen for read operations.
- Keep **API shapes stable**; wire new features behind feature flags.
- Respect **token budget** and **latency goals**; assume Postgres cache is the default source for repeat reads.
- **Always remove secrets** from logs; rely on redaction middleware and manual checks.

---

## 13) Example Flows (mental model)

### A) Connect Notion
1. User clicks "Connect Notion" → `/connect/notion`
2. Redirect to Notion → callback receives `code`
3. Backend exchanges code → stores encrypted tokens + `expires_at`
4. MCP client connection to Notion's hosted service is available for that user; refresh eager at T-5m

### B) Chat with Cache
1. `/chat` request arrives with user/session
2. Router assembles tools (remote MCPs + Notion if connected)
3. Cache middleware checks key; miss → fetch tool; store; return reply
4. Repeat same request → `cacheHit:true`, warm reply, low tokens

### C) Streaming (Week 4)
1. `/chat/stream` opens SSE
2. Emit `token` events as model replies; `tool_call`/`tool_result` events as they occur
3. Heartbeats prevent idle disconnects; `done` closes stream

---

## 14) Non-Goals (for now)

- Multi-model routing within a single request (keep it simple).
- Heavy write automation without explicit user approval.
- Complex fine-tuning/LLM training (use provider baselines).
- Full enterprise SSO/compliance in MVP (v2+).

---

## 15) Reference Docs in Repo

- **README.md** — quickstart & API list
- **context.md** (this file) — project overview for agents
- **agent-core/README.md** — detailed development plan & contracts (MVP week-by-week)
- **ADRs/** — short decisions (transport, encryption, cache policy, SSE protocol)
- **docs/deployment/** — deploy & rollback runbook, Notion app registration, DB notes

---

## 16) Repository Structure

```
alfred-system/
├── agent-core/           # FastAPI backend (primary focus)
├── web/                  # Next.js PWA with SSE streaming
├── cli/                  # Terminal/SSH interface
├── mcp-servers/          # Self-hosted MCP implementations
├── infrastructure/       # Docker, Nginx, deployment configs
├── docs/                 # Documentation and guides
├── shared/               # Common types and utilities
└── monitoring/           # Logging, metrics, observability
```

---

## 17) Contact & Ownership

- **Agent Core:** FastAPI app, MCP router, cache/session, OAuth manager.
- **MCPs:** MCP Client to Notion's hosted service (per-user tokens), Remote MCPs (GitHub, Memory, etc.).
- **PWA/Mobile:** Web streaming, voice capture, offline queue.
- **SRE/Ops:** Logs/metrics/alerts; DB backups; cost monitoring.

**If a session is missing context, load `context.md` + `agent-core/README.md` first.**
**Then pick the next open GitHub issue in the active milestone and execute.**
