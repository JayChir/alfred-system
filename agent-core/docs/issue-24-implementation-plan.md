# Issue #24: PostgreSQL Cache Backend - Implementation Plan

## Overview
Replace the in-memory cache with a PostgreSQL-backed cache to enable persistence, sharing across instances, and better scalability.

## Current State Analysis

### Existing Components
1. **InvokeCache Protocol** (`src/services/cache_service.py`):
   - Defines the transport-agnostic interface
   - Methods: `get()`, `set()`, `delete()`, `stats()`

2. **MemoryInvokeCache** (`src/services/cache_service.py`):
   - Current in-memory implementation using TTLCache
   - Features: singleflight pattern, per-entry TTL, metrics
   - Size limit: 2000 entries with LRU eviction

3. **Database Migration** (`b85f1c0aec2a_add_sessions_and_cache.py`):
   - `agent_cache` table already created with:
     - `cache_key` (text, primary key)
     - `content` (JSONB)
     - `content_hash` (text)
     - `expires_at` (timestamptz)
     - `hit_count` (int)
     - `created_at`, `updated_at`, `last_accessed` (timestamptz)
     - Index on `expires_at` for cleanup

4. **Cache Key Generation**:
   - Format: `mcp:{server}:{tool}:{version}:{schema}:{scope}:{args_hash}`
   - Canonical JSON normalization for consistent hashing

## Implementation Tasks

### 1. Create Cache Database Model (`src/db/models.py`)
```python
class AgentCache(Base):
    """PostgreSQL-backed cache for MCP tool invocations."""
    __tablename__ = "agent_cache"

    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(Text)
    idempotent: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now())
    last_accessed: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

### 2. Implement PostgreSQLInvokeCache (`src/services/cache_service.py`)

#### Core Methods
- **get()**:
  - Atomic SELECT + UPDATE for hit count and last_accessed
  - Check expiry and max_age constraints
  - Return metadata (cache age, TTL remaining)

- **set()**:
  - UPSERT operation (ON CONFLICT UPDATE)
  - Calculate expiry based on TTL
  - Store content hash for verification

- **delete()**:
  - Simple DELETE by key
  - Used for manual invalidation

- **stats()**:
  - Aggregate metrics from database
  - Hit rate, total entries, expired count

#### Additional Features
- **Batch operations** for efficiency
- **Connection pooling** via SQLAlchemy
- **Async/await** throughout
- **Transaction management** for consistency

### 3. TTL Policy Configuration

Create TTL policy map with environment overrides:

```python
DEFAULT_TTL_POLICIES = {
    # Notion tools
    "notion.get_page": 14400,     # 4 hours (active documents)
    "notion.search": 14400,        # 4 hours
    "notion.get_database": 86400,  # 24 hours (schema rarely changes)

    # GitHub tools
    "github.get_repo": 86400,      # 24 hours
    "github.get_file": 14400,      # 4 hours
    "github.search": 3600,         # 1 hour (more dynamic)

    # Default for unknown tools
    "*": 3600,                      # 1 hour fallback
}

# Environment override format:
# CACHE_TTL_NOTION_GET_PAGE=86400
# CACHE_TTL_GITHUB_SEARCH=7200
```

### 4. Cache Invalidation Strategy

#### Automatic Invalidation
- On write operations (POST/PUT/DELETE tools)
- Pattern-based invalidation (e.g., invalidate all `notion.search:*` on page creation)
- Cascade invalidation for related keys

#### Manual Invalidation
- `forceRefresh=true` parameter bypasses and updates cache
- Admin endpoint for cache clearing (future)

### 5. Cleanup and Maintenance

#### Periodic Cleanup Task
```python
async def cleanup_expired_cache(db: AsyncSession) -> int:
    """Remove expired entries in batches."""
    result = await db.execute(
        text("""
            DELETE FROM agent_cache
            WHERE cache_key IN (
                SELECT cache_key FROM agent_cache
                WHERE expires_at < now()
                LIMIT 1000
            )
        """)
    )
    return result.rowcount
```

#### Background Task Schedule
- Run every 5 minutes
- Delete in batches of 1000 to avoid locks
- Log cleanup metrics

### 6. Performance Optimizations

#### Query Optimizations
- Use prepared statements
- Batch reads for multiple keys
- Partial indexes for common queries

#### Caching Strategy
- Read-through: Check cache, fetch if miss, store result
- Write-through: Update cache on successful tool execution
- Lazy expiry: Check TTL on read, cleanup periodically

#### Connection Management
- Connection pooling (pool_size=10, max_overflow=20)
- Statement caching
- Async execution throughout

### 7. Monitoring and Metrics

#### Cache Metrics to Track
- Hit rate (target: >70%)
- Average response time
- Cache size (entries and bytes)
- TTL distribution
- Most/least accessed keys

#### Logging
```python
logger.info(
    "Cache operation",
    operation="get|set|delete",
    key=key[:32],
    hit=True|False,
    ttl_remaining=seconds,
    duration_ms=time
)
```

### 8. Migration Path

#### Configuration Switch
```python
# In settings.py
CACHE_BACKEND = env.str("CACHE_BACKEND", "memory")  # "memory" | "postgres"

# In cache_service.py
def get_cache_service(settings: Settings, db: AsyncSession = None):
    if settings.cache_backend == "postgres":
        if not db:
            raise ValueError("PostgreSQL cache requires database session")
        return PostgreSQLInvokeCache(db, settings)
    else:
        return MemoryInvokeCache(settings)
```

#### Gradual Rollout
1. Implement PostgreSQL cache alongside memory cache
2. Feature flag for A/B testing
3. Monitor performance metrics
4. Switch default once stable

## Testing Strategy

### Unit Tests
- Test each cache method independently
- Mock database interactions
- Verify TTL calculations
- Test edge cases (expired entries, concurrent access)

### Integration Tests
```python
async def test_postgresql_cache_integration():
    # Create cache instance
    cache = PostgreSQLInvokeCache(db, settings)

    # Test basic operations
    await cache.set("test:key", {"data": "value"}, ttl_s=60)
    result = await cache.get("test:key")
    assert result["data"] == "value"

    # Test expiry
    await cache.set("expire:key", {"temp": "data"}, ttl_s=1)
    await asyncio.sleep(2)
    assert await cache.get("expire:key") is None

    # Test hit counting
    stats_before = await cache.stats()
    await cache.get("test:key")
    stats_after = await cache.stats()
    assert stats_after["hits"] > stats_before["hits"]
```

### Performance Tests
- Benchmark read/write operations
- Test under concurrent load
- Measure cache hit rates
- Verify cleanup performance

## Success Metrics

### Performance Targets
- **Cache hit rate**: >70% on repeated queries
- **Read latency**: <50ms P95
- **Write latency**: <100ms P95
- **Cleanup efficiency**: <5% CPU overhead

### Functional Requirements
- ✅ Persistent cache across restarts
- ✅ Shared cache across instances
- ✅ Configurable TTLs per tool
- ✅ Force refresh capability
- ✅ Automatic cleanup of expired entries
- ✅ Hit/miss metrics and monitoring

## Implementation Order

1. **Phase 1: Core Implementation** (Day 1)
   - Create AgentCache model
   - Implement basic get/set/delete operations
   - Add to existing migration (already done)

2. **Phase 2: TTL and Policies** (Day 1-2)
   - Implement TTL policy configuration
   - Add environment override support
   - Create cleanup task

3. **Phase 3: Integration** (Day 2)
   - Wire up to cache_service.py
   - Add configuration switching
   - Update dependency injection

4. **Phase 4: Testing and Metrics** (Day 2-3)
   - Write comprehensive tests
   - Add monitoring and logging
   - Performance benchmarking

5. **Phase 5: Documentation** (Day 3)
   - Update API documentation
   - Document TTL policies
   - Create operations guide

## Risk Mitigation

### Potential Issues
1. **Lock contention**: Use advisory locks for hot keys
2. **Memory pressure**: Monitor JSONB size, implement size limits
3. **Cleanup performance**: Use partial indexes, batch deletes
4. **Network latency**: Keep connection pool warm

### Rollback Plan
- Feature flag to switch back to memory cache
- Keep memory cache implementation intact
- Database migration is reversible

## Configuration Examples

### Environment Variables
```bash
# Cache backend selection
CACHE_BACKEND=postgres

# Default TTL (4 hours)
CACHE_DEFAULT_TTL_SECONDS=14400

# Tool-specific TTLs
CACHE_TTL_NOTION_GET_PAGE=86400      # 24 hours for stable docs
CACHE_TTL_NOTION_SEARCH=14400        # 4 hours for search
CACHE_TTL_GITHUB_GET_REPO=86400      # 24 hours for repo info
CACHE_TTL_GITHUB_SEARCH=3600         # 1 hour for dynamic search

# Cache size limits
CACHE_MAX_ENTRIES=10000
CACHE_MAX_ENTRY_SIZE_KB=100

# Cleanup schedule
CACHE_CLEANUP_INTERVAL_SECONDS=300   # 5 minutes
CACHE_CLEANUP_BATCH_SIZE=1000
```

### Usage Example
```python
# In endpoint
@router.post("/chat")
async def chat_endpoint(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    cache: InvokeCache = Depends(get_cache_service),
):
    # Check cache for tool result
    cache_key = make_cache_key(
        server="notion",
        tool="get_page",
        args={"page_id": "abc123"},
        user_scope=request.user_id,
    )

    if not request.forceRefresh:
        if cached := await cache.get(cache_key):
            return ChatResponse(
                reply=cached["content"],
                meta={
                    "cacheHit": True,
                    "cacheTtlRemaining": cached["_cache_ttl_remaining_s"],
                }
            )

    # Execute tool and cache result
    result = await mcp_router.execute_tool(...)

    # Determine TTL based on tool
    ttl = get_ttl_for_tool("notion", "get_page")
    await cache.set(cache_key, result, ttl_s=ttl)

    return ChatResponse(
        reply=result,
        meta={"cacheHit": False}
    )
```

## Deliverables

1. **Code**:
   - `src/db/models.py`: AgentCache model
   - `src/services/postgres_cache.py`: PostgreSQL cache implementation
   - `src/services/cache_service.py`: Updated with backend selection
   - `tests/test_postgres_cache.py`: Comprehensive tests

2. **Configuration**:
   - Updated `.env.example` with cache settings
   - TTL policy configuration file

3. **Documentation**:
   - Cache architecture diagram
   - TTL policy guide
   - Operations runbook

4. **Metrics**:
   - Grafana dashboard for cache metrics
   - Alert rules for cache health

## Timeline

- **Day 1**: Core implementation (model, basic operations)
- **Day 2**: TTL policies, cleanup, integration
- **Day 3**: Testing, metrics, documentation

Total estimated effort: 3 days

## Notes

- The migration already exists (b85f1c0aec2a) with the correct schema
- We should maintain backward compatibility with the memory cache
- Consider adding Redis support in the future for even better performance
- The singleflight pattern from memory cache may not be needed with PostgreSQL's built-in concurrency control
