# Issue #23 Implementation Plan: Production-Grade Device Sessions

**Status**: Ready for implementation
**Created**: 2025-01-06
**Updated**: 2025-01-06

## Overview

Implement device session management for transport continuity, user identification, and token metering. Device sessions handle **"who am I and where am I working"** while threads (already complete) handle **"what were we talking about"**.

This is the **production-grade** implementation incorporating performance optimizations, race safety, and proper security practices.

## Key Improvements Applied

✅ **Atomic operations** (no read-modify-write races)
✅ **Hard expiry cap** prevents infinite session extension
✅ **Race-safe token creation** with conflict handling
✅ **Single SQL operations** for better performance
✅ **Proper indexing** for query optimization
✅ **Cookie discipline** (HttpOnly, SameSite, no token echoing)

## Current State

- ✅ `device_sessions` table exists (migration 04eec890c4bc)
- ✅ Basic `DeviceSessionService` skeleton
- ✅ Clear API contract (`deviceToken` field)
- ❌ Missing: SQLAlchemy model, token lifecycle, middleware, integration

## Implementation Phases

### Phase 1: Database Foundation (Enhanced)

#### 1.1: Database Migration - Add Missing Fields
**File**: `src/db/migrations/versions/new_enhance_device_sessions.py`

**Required columns to add/confirm:**
- `session_id` UUID PK DEFAULT gen_random_uuid()
- `user_id` UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
- `workspace_id` TEXT NULL
- `device_token_hash` BYTEA(32) NOT NULL UNIQUE (SHA-256 of dtok_...)
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `last_accessed` TIMESTAMPTZ NOT NULL DEFAULT now()
- `expires_at` TIMESTAMPTZ NOT NULL
- **`hard_expires_at`** TIMESTAMPTZ NOT NULL (created_at + 30d hard cap) **[NEW]**
- `tokens_input_total` BIGINT NOT NULL DEFAULT 0
- `tokens_output_total` BIGINT NOT NULL DEFAULT 0
- **`request_count`** BIGINT NOT NULL DEFAULT 0 **[NEW]**
- **`revoked_at`** TIMESTAMPTZ NULL **[NEW]**

**Required indexes:**
```sql
CREATE INDEX idx_device_sessions_user ON device_sessions (user_id);
CREATE INDEX idx_device_sessions_expires ON device_sessions (expires_at);
CREATE INDEX idx_device_sessions_last_accessed ON device_sessions (last_accessed);
CREATE INDEX idx_device_sessions_hard_expires ON device_sessions (hard_expires_at);
```

#### 1.2: Enhanced DeviceSession Model
**File**: `src/db/models.py`

**Key requirements:**
- All fields mapped with proper types and constraints
- Server defaults for timestamps and counters
- Proper indexing attributes
- Foreign key relationship to users table
- 32-byte binary field for token hash (exactly SHA-256 size)

### Phase 2: Token Utilities & Service Layer

#### 2.1: Deterministic Token Utilities
**File**: `src/utils/device_token.py` (new file)

**Functions to implement:**
- `new_device_token()` → Generate dtok_ tokens with 256-bit entropy
- `hash_device_token()` → SHA-256 hash for secure storage
- `validate_token_format()` → Format validation without hashing

**Security requirements:**
- Use `secrets.token_urlsafe(32)` for 256-bit entropy
- Consistent `dtok_` prefix
- Validate prefix before any processing
- Never log full tokens (prefix only)

#### 2.2: Enhanced DeviceSessionService (Atomic Operations)
**File**: `src/services/device_session_service.py` (major enhancements)

**Critical implementation details:**

**Atomic validation with sliding expiry:**
```sql
UPDATE device_sessions
   SET last_accessed = now(),
       expires_at = LEAST(now() + interval '7 days', hard_expires_at),
       request_count = request_count + 1
 WHERE device_token_hash = :h
   AND (revoked_at IS NULL)
   AND (expires_at > now())         -- idle window valid
   AND (hard_expires_at > now())    -- hard cap valid
RETURNING session_id, user_id, workspace_id, expires_at
```

**Race-safe token creation:**
```sql
INSERT INTO device_sessions (device_token_hash, user_id, workspace_id,
                             expires_at, hard_expires_at)
VALUES (:h, :uid, :ws, now() + interval '7 days', now() + interval '30 days')
ON CONFLICT (device_token_hash) DO NOTHING
```

**Separate metering transaction:**
```sql
UPDATE device_sessions
   SET tokens_input_total  = tokens_input_total  + :tin,
       tokens_output_total = tokens_output_total + :tout
 WHERE session_id = :sid
```

### Phase 3: Integration Layer (Race-Safe)

#### 3.1: Workspace Resolution Service
**File**: `src/services/workspace_resolver.py` (new file)

**Workspace resolution priority:**
1. Thread workspace (always wins if present)
2. Device session workspace (fallback)
3. None (workspace-agnostic mode)

**Function**: `compute_effective_workspace(thread_workspace, device_workspace)`

#### 3.2: Race-Safe Middleware with Auto-Creation
**File**: `src/middleware/device_session.py` (new file)

**Token extraction precedence:**
1. Request body `deviceToken` field (highest)
2. `X-Device-Token` header
3. `dtok` cookie (HttpOnly) (lowest)
4. Auto-create if `ENABLE_AUTO_CREATE_DEVICE_TOKEN=true`

**Auto-creation behavior:**
- Only if no token found and setting enabled
- Use default user ID from settings
- Resolve workspace via most recent Notion connection
- Set HttpOnly, SameSite=Lax cookie with 7-day expiry
- Never echo raw token in JSON response

**Cookie settings:**
```python
response.set_cookie(
    "dtok", token,
    httponly=True,
    samesite="lax",
    max_age=7*24*3600,
    secure=True if production else False
)
```

### Phase 4: Chat Integration & Settings

#### 4.1: Configuration Updates
**File**: `src/config.py`

**New settings required:**
- `enable_auto_create_device_token: bool = True`
- `default_user_id: UUID` (required)
- `device_session_idle_days: int = 7`
- `device_session_max_days: int = 30`

#### 4.2: Chat Endpoint Integration
**File**: `src/routers/chat.py` (enhance existing)

**Integration requirements:**
1. Add `device_ctx: DeviceSessionContext = Depends(get_device_session_context)`
2. Compute effective workspace: `thread.workspace_id or device_ctx.workspace_id`
3. Pass user_id and workspace_id to agent orchestrator
4. Update token usage in `finally` block (separate transaction)
5. Include device session metadata in response (no raw token)

**Response metadata to include:**
- `deviceSessionExpires` (ISO timestamp)
- `effectiveWorkspace` (computed workspace)
- `requestId` (for tracing)
- **Never include `deviceToken`** (security risk)

#### 4.3: Response Models Enhancement

**Enhanced `ChatResponseMeta`:**
```python
class ChatResponseMeta(BaseModel):
    requestId: str
    cacheHit: bool
    cacheTtlRemaining: Optional[int]
    tokens: TokenUsage
    deviceSessionExpires: str  # ISO timestamp
    effectiveWorkspace: Optional[str]
    # Never include deviceToken - security risk
```

### Phase 5: Background Tasks & Testing

#### 5.1: Cleanup Background Task
**File**: `src/tasks/device_cleanup.py` (new file)

**Cleanup logic:**
```sql
DELETE FROM device_sessions
 WHERE (expires_at <= now()) OR (hard_expires_at <= now())
 LIMIT 1000
```

**Schedule**: Run hourly via cron or background task

#### 5.2: Comprehensive Integration Tests
**File**: `tests/test_device_sessions_integration.py` (new file)

**Critical test scenarios:**
- Auto-create device session sets cookie correctly
- Token precedence (body > header > cookie)
- Sliding expiry extends session but respects hard cap
- Hard expiry prevents infinite extension
- Workspace precedence (thread > device > none)
- Token metering updates counters correctly
- Revoked sessions return 401
- Cleanup removes expired sessions

## Security Considerations

### Token Handling
- **Raw tokens**: Only visible at creation time, never logged/stored
- **Storage**: Only SHA-256 hashes stored in database
- **Validation**: Prefix validation before any processing
- **Logging**: Only log token prefixes (first 12 chars) for debugging
- **Cookies**: HttpOnly, SameSite=Lax, secure in production

### Session Security
- **Revocation**: Soft delete via `revoked_at` timestamp
- **Hard expiry**: 30-day absolute maximum lifetime
- **Sliding expiry**: 7-day idle timeout with activity extension
- **Atomic operations**: Race-safe validation and updates

## Dependencies & Integration Points

### Prerequisites (Complete)
- ✅ Database schema (`device_sessions` table exists)
- ✅ Refactor (clear `deviceToken` API contract)
- ✅ Thread system (conversation state separated)
- ✅ User management (foreign key relationship)

### Integration Points
- **Database**: Foreign key to `users.id`, queries `notion_connections`
- **API**: FastAPI dependency injection for `DeviceSessionContext`
- **MCP Router**: Workspace-scoped tool routing
- **Thread Service**: Workspace override capability
- **Agent Orchestrator**: User and workspace context passing

## Implementation Order (Risk-Safe)

### Phase 1: Foundation (No Breaking Changes)
1. Database migration with new fields and indexes
2. `DeviceSession` model + `DeviceSessionContext` dataclass
3. Token utilities (`device_token.py`)
4. Enhanced `DeviceSessionService` with atomic operations
5. Unit tests for service layer

### Phase 2: Integration (Potential Breaking Changes)
1. Workspace resolution service
2. Device session middleware with auto-creation fallback
3. Configuration updates with new settings
4. Integration tests for middleware

### Phase 3: Application Integration (Breaking Changes)
1. Chat endpoint integration with dependency injection
2. Response model enhancements
3. End-to-end integration tests
4. Backward compatibility verification

### Phase 4: Production Features (Safe)
1. Background cleanup task
2. Monitoring and observability
3. Performance optimization
4. Security auditing

## Success Metrics

- ✅ Device tokens authenticate users correctly
- ✅ Token usage metering tracks input/output accurately
- ✅ Workspace resolution routes MCP calls properly
- ✅ Sliding expiry extends active sessions, expires idle ones
- ✅ Hard expiry cap prevents infinite session extension
- ✅ Race conditions eliminated with atomic operations
- ✅ Backward compatibility maintained (no breaking changes)
- ✅ Security best practices followed (no token leakage)

## Performance Targets

- **Token validation**: <10ms P95 (single SQL operation)
- **Session creation**: <50ms P95 (conflict-safe insertion)
- **Cleanup efficiency**: 1000+ sessions per operation
- **Index utilization**: All queries use proper indexes
- **Transaction isolation**: Separate metering updates

## Estimated Effort

- **Phase 1-2**: ~2-3 days (core functionality)
- **Phase 3-4**: ~1-2 days (integration & testing)
- **Total**: ~4-5 days for complete implementation

---

**🚨 POST-COMPACTION REMINDER**: Always reference this plan after context compaction to maintain implementation consistency and avoid regressions.

## Implementation Status

- [ ] Phase 1: Database Foundation
  - [ ] Database migration with new fields
  - [ ] DeviceSession model
  - [ ] Token utilities
  - [ ] Enhanced DeviceSessionService
- [ ] Phase 2: Integration Layer
  - [ ] Workspace resolution service
  - [ ] Device session middleware
- [ ] Phase 3: Application Integration
  - [ ] Chat endpoint integration
  - [ ] Response model enhancements
- [ ] Phase 4: Production Features
  - [ ] Background cleanup task
  - [ ] Integration tests
- [ ] Phase 5: Deployment & Verification
  - [ ] Performance validation
  - [ ] Security audit
  - [ ] Documentation updates

**Next Step**: Begin Phase 1 with database migration and model creation.
