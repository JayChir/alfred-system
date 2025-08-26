# Agent Core MVP Development — Claude Code Instructions

This file provides guidance to Claude Code when working in the agent-core directory for MVP development.

## Project Overview

**System**: Alfred Agent Core MVP — FastAPI-based AI agent that routes to MCP (Model Context Protocol) tools with OAuth, caching, and streaming capabilities.

**Mission**: Build production-ready agent core in 4 weeks following the comprehensive MVP playbook in `README.md`.

**Architecture**: FastAPI + Pydantic AI + PostgreSQL + MCP Router → Target: ≤3s P95 latency, ~90% cache hit rate, 80% token reduction vs uncached.

## Essential Reading (Start Here)

1. **Primary Reference**: `README.md` — Complete 626-line MVP playbook (single source of truth)
2. **Repository Context**: `../CLAUDE.md` — Overall system context and MCP management
3. **Current Issues**: Check GitHub issues #5-35 for 31 MVP tasks across 4 weekly milestones

## Development Workflow

### Session Startup Protocol
1. **Load MVP Context**: Read `README.md` to understand current week's objectives
2. **Check Active Issues**: Review GitHub issues for current milestone (MVP-W1, MVP-W2, etc.)
3. **Log Session Start**: Use personal_memory to capture session goals and context
4. **Verify Environment**: Check `.env.example` for required configuration

### Work Packet Approach
**Use the predefined work packets from README.md:**
- **Packet A (Week 1)**: FastAPI skeleton + MCP Router + in-memory cache
- **Packet B (Week 2)**: Notion OAuth + per-user MCP sessions + encryption
- **Packet C (Week 3)**: PostgreSQL sessions/cache + token metering
- **Packet D (Week 4)**: SSE streaming + hardening + tests

## Development Constraints & Standards

### Stack Requirements
- **FastAPI** for web framework
- **Pydantic AI** for agent orchestration
- **PostgreSQL** for sessions, cache, and OAuth tokens
- **MCP (Model Context Protocol)** for tool routing
- **Python 3.11+** with modern package management

### Performance Targets
- **≤3s P95 latency** for cached reads
- **~90% cache hit rate** on repeated reads
- **80% token reduction** vs uncached baselines
- **Single droplet deployment** ($50-100/mo budget)

### Code Standards
- **Ship first, optimize later** - MVP focus over perfection
- **COMPREHENSIVE COMMENTS REQUIRED** - User is learning API development and specific libraries, so include:
  - Docstrings for all functions and classes explaining purpose and parameters
  - Inline comments explaining complex logic, library usage, and API patterns
  - Comments explaining FastAPI decorators, Pydantic models, SQLAlchemy patterns
  - Architecture decision explanations in code comments
- **Command Documentation** - Always provide brief description for every bash command explaining what and why
- **Follow existing patterns** - check neighboring files for conventions
- **Structured logging** with request IDs and timing
- **Error taxonomy** using predefined codes (see README.md)

### Security Requirements
- **Never log secrets** - access_token, refresh_token, FERNET_KEY, etc.
- **Encrypt tokens at rest** using Fernet symmetric encryption
- **Validate all env vars** at startup
- **Use structured error responses** with request tracking

## Architecture Components

### Core Services (src/services/)
- **mcp_router.py**: Tool discovery, health checks, connection registry
- **oauth_manager.py**: Notion OAuth flow, token encryption/refresh
- **cache_service.py**: Read-through/write-through caching with TTLs
- **session_store.py**: Conversation context and workspace binding
- **agent_orchestrator.py**: Pydantic AI integration and tool adaptation

### API Endpoints (src/routers/)
- **health.py**: `GET /healthz` with status/version
- **chat.py**: `POST /chat` (non-streaming) + `GET /chat/stream` (SSE)
- **oauth.py**: `GET /connect/notion` + `GET /oauth/notion/callback`

### Data Layer (src/db/)
- **models.py**: SQLAlchemy models for users, sessions, cache, oauth
- **schema.sql**: Database schema definitions
- **migrations/**: Alembic migration files

## Development Best Practices

### Git Workflow - Merge As You Go
**IMPORTANT**: Follow a "merge as you go" strategy to maintain code quality and prevent conflicts.

#### Why Merge Frequently
- **Smaller PRs**: Easier to review (aim for <500 lines per PR)
- **Avoid conflicts**: Long-lived branches accumulate merge conflicts
- **Progressive stability**: Each merge is a stable checkpoint
- **Clear history**: One PR = One issue = Easy to track changes
- **Rollback safety**: Smaller changes are easier to revert if needed

#### Workflow Pattern
```bash
# 1. Start from updated main
git checkout main
git pull origin main

# 2. Create feature branch for ONE issue
git checkout -b feat/<issue-number>-<short-description>
# Example: feat/6-env-loader

# 3. Implement the issue
# ... make changes ...

# 4. Commit with conventional commits
git add -A
git commit -m "feat(component): implement issue #X

- Detailed description
- What was implemented
- Any important notes

Closes #X"

# 5. Push and create PR
git push -u origin feat/<issue-number>-<short-description>
gh pr create --title "feat(component): Issue #X - Description" \
  --body "..." --assignee @me

# 6. After PR approval, merge to main
# Then immediately start next issue from fresh main
```

#### When to Bundle Issues
Only combine multiple issues in one PR if they are:
- **Tiny** (<50 lines each)
- **Tightly coupled** (one doesn't work without the other)
- **Same logical unit** (e.g., model + migration + schema)

Otherwise, **always prefer separate PRs**.

### Task Management
- **One issue per branch**: `feat/<issue-number>-<slug>`
- **Merge before next issue**: Don't stack unmerged work
- **AC verification**: Include acceptance criteria verification in PRs
- **Commit messages**: Follow conventional commits with descriptive bodies
- **Work in packets**: Complete logical chunks before moving to next component

### Command Documentation Protocol
**ALWAYS provide brief explanation BEFORE running every bash command:**

**Format:**
```
Brief description: what this does and why
```
```bash
command args
```

**Required explanations for:**
- Package installations: what library does and why needed
- Database operations: what migration/query accomplishes
- Docker commands: what container operation is happening
- Git operations: what change is being tracked
- File operations: what files are being modified and purpose

**Example:**
```
Installing FastAPI web framework for building REST API endpoints
```
```bash
pip install fastapi[all]
```

### Testing Strategy (Minimal MVP)
- **Smoke tests**: Basic endpoint functionality (`/healthz`, `/chat`)
- **Integration tests**: OAuth flow, cache hit ratios, SSE streaming
- **Local verification**: Use curl commands from README.md verification snippets
- **No extensive unit testing** - focus on end-to-end functionality

### Logging & Debugging
- **Structured JSON logs** with request_id, timing, cache status
- **Log all MCP calls** with success/failure and timing
- **Cache hit/miss logging** with TTL remaining
- **Token usage tracking** in response metadata

## Memory & Context Management

### Personal Memory Usage
**CRITICAL**: Use personal_memory tools actively for system continuity. Log observations immediately with timestamps:

```python
personal_memory:create_entities([{
  "name": "Agent Core Session YYYY-MM-DD",
  "entityType": "development_session",
  "observations": [
    "Working on Week N MVP development - [specific component]",
    "Key decisions: [technical choices made]",
    "Blockers encountered: [issues and solutions]",
    "Performance results: [cache hits, latency measurements]"
  ]
}])

# Build relationships between sessions and components
personal_memory:create_relations([{
  "from": "Agent Core Session YYYY-MM-DD",
  "to": "MVP Week N Milestone",
  "relationType": "CONTRIBUTES_TO"
}])
```

### Required Memory Patterns
- **Session Start**: Create entity for each work session with timestamp
- **Technical Decisions**: Log architecture choices with reasoning
- **Code Patterns**: Document reusable patterns discovered during development
- **Performance Data**: Track cache hit rates, latency improvements, token usage
- **Blockers & Solutions**: Document problems encountered and how resolved
- **API Learning**: Log FastAPI, Pydantic, SQLAlchemy patterns learned

### Session Documentation Protocol
When user requests session documentation, create entry in Claude Session Log database:

```python
notion:create-pages
  parent: {"database_id": "edfeee6d276b4cbfa84c2a8e15864e24"}
  properties: {
    "Session Summary": "[YYYY-MM-DD] Agent Core MVP - [component/milestone]",
    "System": "Claude Code",
    "Session Date": "Month DD, YYYY HH:MM-HH:MM AM/PM PST",
    "Key Outcomes": "• Code component completed\n• Architecture decision made\n• Performance milestone achieved",
    "Next Goals": "• Next component to build\n• Tests to write\n• Integration to complete",
    "Session Notes": "Detailed technical context, code patterns learned, blockers resolved...",
    "Project": "[\"alfred-agent-core-page-id\"]",
    "Tags": "[\"#deep-work\", \"#system-design\"]"
  }
```

**Valid Tags for Agent Core Development:**
- `#system-design` - Architecture, infrastructure, technical decisions
- `#deep-work` - Extended development sessions
- `#optimization` - Performance improvements, caching work
- `#troubleshooting` - Debugging, problem-solving
- `#integration` - API connections, MCP work, OAuth flows

### Notion Integration
- Track major milestones and architectural decisions
- Document deployment steps and configuration
- Log security considerations and token handling approaches

## Environment Setup

### Required Files
- `.env` from `.env.example` with actual values
- Database connection (PostgreSQL)
- Anthropic API key for model provider
- Notion OAuth app credentials (when implementing OAuth)

### Development Commands (to implement in Makefile)
```bash
make run          # Start development server
make test         # Run test suite
make lint         # Lint and format code
make db-migrate   # Run database migrations
make docker-build # Build container
```

## Common Patterns & Helpers

### API Response Structure
```python
# Standard response format for all API endpoints
# Includes metadata for debugging, caching, and performance tracking
{
  "reply": "...",  # Main response content
  "meta": {
    "cacheHit": false,              # Whether response came from cache
    "cacheTtlRemaining": null,      # Seconds until cache expires
    "tokens": {"input": 0, "output": 0},  # Token usage for billing/limits
    "requestId": "..."              # Unique ID for request tracing
  }
}
```

### Cache Key Format
```python
# Cache keys use this standardized format for consistency
# Example: "notion.get_page:v1:a1b2c3d4"
"{tool}:{version}:{normalized_args_hash}"

# tool: The MCP tool name (e.g., "notion.get_page", "github.get_repo")
# version: Schema version to handle breaking changes (e.g., "v1", "v2")
# normalized_args_hash: SHA256 hash of sorted/normalized arguments
```

### Error Response Format
```python
# Standardized error responses for debugging and client handling
{
  "error": "APP-4XX-VALIDATION",     # Error code from taxonomy (see README.md)
  "message": "Human readable message",  # User-friendly error description
  "origin": "oauth|mcp|db|app",      # System component where error occurred
  "requestId": "..."                 # Same request ID for tracing
}
```

### FastAPI Code Patterns
```python
# Example FastAPI endpoint with comprehensive comments
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

# Pydantic models define request/response schemas with validation
class ChatRequest(BaseModel):
    """Request model for chat endpoint with field validation."""
    messages: list[dict]        # List of conversation messages
    session: str | None = None  # Optional session token for context
    forceRefresh: bool = False  # Flag to bypass cache

# FastAPI router groups related endpoints
router = APIRouter(prefix="/api/v1", tags=["chat"])

@router.post("/chat")
async def chat_endpoint(
    request: ChatRequest,                    # Pydantic validation
    request_id: str = Depends(get_request_id)  # Dependency injection
):
    """
    Main chat endpoint that processes messages through MCP tools.

    Args:
        request: Validated chat request with messages and options
        request_id: Unique identifier injected for request tracing

    Returns:
        ChatResponse with reply and metadata

    Raises:
        HTTPException: For validation errors or system failures
    """
    # Implementation with extensive comments explaining each step
    pass
```

## Week-by-Week Focus

### Week 1 (MVP-W1) — Foundation
**Goal**: Basic FastAPI app with MCP routing and in-memory cache
**Key Components**: Health endpoint, chat endpoint, MCP router, thin cache
**Success Metric**: Can call ≥2 remote MCP tools with cache hit logging

### Week 2 (MVP-W2) — OAuth & Security
**Goal**: Notion OAuth with encrypted token storage
**Key Components**: OAuth flow, token encryption, per-user MCP sessions
**Success Metric**: End-to-end Notion connection with hosted MCP

### Week 3 (MVP-W3) — Persistence & Sessions
**Goal**: PostgreSQL backend with session management
**Key Components**: Session store, cache persistence, token metering
**Success Metric**: >70% cache hit rate, session continuity

### Week 4 (MVP-W4) — Production Ready
**Goal**: SSE streaming with production hardening
**Key Components**: Server-sent events, rate limiting, deployment
**Success Metric**: Live streaming with reconnect, deployment runbook

## Troubleshooting

### Common Issues
- **MCP Connection Failures**: Check existing MCP server health at ports 3001-3010
- **Cache Misses**: Verify key normalization and TTL configuration
- **OAuth Errors**: Check Notion app configuration and callback URLs
- **Database Issues**: Verify PostgreSQL connection and migration status

### Debug Helpers
- Check logs for request_id correlation
- Verify environment variables are loaded correctly
- Test MCP connectivity independently before integration
- Use verification curl commands from README.md

## Success Metrics & Verification

### Performance Verification
```bash
# Latency check (cached)
curl -w "@curl-format.txt" -s -X POST localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"repeat call"}]}'

# Cache hit verification
# First call: cacheHit: false
# Second call: cacheHit: true
```

### Integration Verification
- OAuth flow completes successfully
- Notion tools appear for connected users only
- Token refresh works with short expiry
- Session context persists across requests
- SSE events stream correctly with heartbeat

## Ready-to-Use Components

The MVP playbook includes complete:
- **API contracts** with exact request/response formats
- **Database schema** with all required tables and indexes
- **Cache policies** with TTL recommendations per tool type
- **OAuth flow** with security best practices
- **Verification snippets** for testing each component

**Remember**: This is MVP development — ship functional components quickly, optimize later. The playbook provides everything needed to build a production-ready agent core in 4 weeks.
