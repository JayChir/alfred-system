# Cache Policy & TTL Strategy

## Overview

Alfred Agent Core implements a sophisticated PostgreSQL-backed cache with the following features:

- **Deterministic key generation** with namespace scoping to prevent cross-user data leaks
- **Singleflight pattern** using PostgreSQL advisory locks to prevent thundering herd
- **Tag-based invalidation** for surgical cache updates on write operations
- **Stale-if-error fallback** with 30-second grace period for resilience
- **TTL jitter** (±10%) to avoid synchronized expiration storms

## Cache Architecture

### Read-Through Cache Flow

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Cache
    participant Lock as Advisory Lock
    participant MCP

    Client->>API: POST /chat
    API->>Cache: Check cache_key

    alt Cache Hit (Fresh)
        Cache-->>API: Return cached data
        API-->>Client: Response (cacheHit: true)
    else Cache Miss or Stale
        API->>Lock: Try acquire lock(key_hash)
        alt Lock Acquired
            API->>MCP: Call tool
            MCP-->>API: Tool result
            API->>Cache: Write result (with TTL)
            API->>Cache: Tag entry
            API->>Lock: Release lock
            API-->>Client: Response (cacheHit: false)
        else Lock Not Acquired (Singleflight)
            API->>Lock: Wait for lock release
            API->>Cache: Re-check cache
            Cache-->>API: Return fresh data
            API-->>Client: Response (cacheHit: true)
        end
    else Cache Stale + Error
        API->>MCP: Call tool (attempt)
        MCP--xAPI: Error
        API->>Cache: Check stale grace
        alt Within Grace Period
            Cache-->>API: Return stale data
            API-->>Client: Response (stale: true)
        else Grace Expired
            API-->>Client: Error response
        end
    end
```

## Cache Key Specification

### Canonical Format

```
{namespace}:{tool}:v{version}:{args_hash_16}
```

### Components

| Component | Format | Description | Example |
|-----------|--------|-------------|---------|
| `namespace` | `user_{user_id}` or `workspace_{workspace_id}` | Scope to prevent cross-tenant leaks | `user_123`, `workspace_abc` |
| `tool` | `{provider}.{method}` | Full tool identifier | `notion.get_page`, `github.get_repo` |
| `version` | `v{major}.{minor}` | Schema version for breaking changes | `v1.0`, `v2.1` |
| `args_hash_16` | 16-char hex | First 16 chars of SHA-256 hash | `a1b2c3d4e5f67890` |

### Argument Canonicalization

Arguments are normalized before hashing to ensure cache consistency:

```python
def canonical_args_hash(args: Dict[str, Any]) -> str:
    """Generate deterministic hash of arguments."""
    # 1. Sort keys recursively
    # 2. Remove null/undefined values
    # 3. Normalize data types:
    #    - Booleans: true/false (lowercase)
    #    - Floats: Round to 10 decimal places
    #    - Dates: UTC ISO-8601 with 'Z' suffix
    #    - Lists: Preserve order, normalize elements
    # 4. Serialize to compact JSON (no whitespace)
    normalized = normalize_recursive(args)
    canonical_json = json.dumps(normalized, sort_keys=True, separators=(',', ':'))

    # 5. Generate SHA-256 hash
    full_hash = hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()

    # 6. Return first 16 hex characters (64 bits)
    return full_hash[:16]
```

### Collision Handling

- **Probability**: With 64-bit prefix, collision chance is ~1 in 10^19 for 1M entries
- **Detection**: Content hash comparison on read
- **Resolution**: Full hash comparison on collision, separate storage if true collision

### Examples

```python
# Example 1: Notion page fetch
tool: "notion.get_page"
args: {"page_id": "abc-123", "include_children": true}
namespace: "user_456"
key: "user_456:notion.get_page:v1:7f3a2b1c9d8e5f6a"

# Example 2: GitHub repository (workspace scoped)
tool: "github.get_repo"
args: {"owner": "acme", "repo": "api", "include_branches": false}
namespace: "workspace_wx789"
key: "workspace_wx789:github.get_repo:v1:2d4e6f8a1c3b5d7e"

# Example 3: Search query (with normalized float)
tool: "notion.search"
args: {"query": "meeting notes", "limit": 10, "score": 0.89999999}
# Normalized: {"limit": 10, "query": "meeting notes", "score": 0.9}
key: "user_123:notion.search:v1:9b8c7d6e5f4a3b2c"
```

## TTL Policies

### Default TTL Configuration

| Tool Pattern | Default TTL | Max Stale | Jitter | Environment Variable | Rationale |
|--------------|------------|-----------|--------|---------------------|-----------|
| `notion.get_page` | 14400s (4h) | +1800s (30m) | ±10% | `CACHE_TTL_NOTION_GET_PAGE` | Document stability vs freshness |
| `notion.get_database` | 86400s (24h) | +3600s (1h) | ±10% | `CACHE_TTL_NOTION_GET_DATABASE` | Schema rarely changes |
| `notion.search` | 14400s (4h) | +900s (15m) | ±10% | `CACHE_TTL_NOTION_SEARCH` | Balance freshness with API limits |
| `github.get_repo` | 86400s (24h) | +3600s (1h) | ±10% | `CACHE_TTL_GITHUB_GET_REPO` | Repository metadata stable |
| `github.get_file` | 14400s (4h) | +1800s (30m) | ±10% | `CACHE_TTL_GITHUB_GET_FILE` | Code changes frequently |
| `github.search` | 3600s (1h) | +600s (10m) | ±10% | `CACHE_TTL_GITHUB_SEARCH` | Search results volatile |
| `time.*` | 0s | N/A | N/A | N/A | Never cache time operations |
| Default (fallback) | 3600s (1h) | +300s (5m) | ±10% | `CACHE_TTL_DEFAULT` | Conservative default |

### TTL Jitter Implementation

```python
def apply_ttl_jitter(base_ttl: int, jitter_pct: float = 0.1) -> int:
    """Apply random jitter to TTL to prevent synchronized expiration."""
    jitter_range = int(base_ttl * jitter_pct)
    jitter = random.randint(-jitter_range, jitter_range)
    return max(60, base_ttl + jitter)  # Minimum 60 seconds
```

### Stale-If-Error Grace Period

- **Default**: 30 seconds past expiration
- **Purpose**: Serve stale data during transient failures
- **Activation**: Only on MCP call failure or timeout
- **Metadata**: Response includes `"stale": true` flag

## Invalidation Strategies

### 1. Manual Invalidation (`forceRefresh`)

```python
# Client request with forceRefresh flag
POST /chat
{
  "messages": [...],
  "forceRefresh": true  # Bypasses cache, refreshes entry
}

# Behavior:
# 1. Skip cache read
# 2. Call MCP tool directly
# 3. Write new result to cache
# 4. Update all associated tags
```

### 2. Tag-Based Invalidation

Tags enable surgical invalidation of related cache entries:

#### Tag Schema

| Entity Type | Tag Format | Example | Invalidated On |
|-------------|------------|---------|----------------|
| Notion Page | `notion:page:{page_id}` | `notion:page:abc123` | Page update/delete |
| Notion Database | `notion:database:{db_id}` | `notion:database:def456` | Schema change |
| GitHub Repo | `github:repo:{owner}/{repo}` | `github:repo:acme/api` | Push, PR merge |
| GitHub File | `github:file:{owner}/{repo}/{path}` | `github:file:acme/api/README.md` | File edit |
| User Scope | `user:{user_id}` | `user:789` | User-wide flush |
| Workspace | `workspace:{workspace_id}` | `workspace:wx123` | Workspace changes |
| Thread | `thread:{thread_id}` | `thread:th456` | Thread deletion |

#### Write-Path Invalidation

```python
async def invalidate_tags_after_write(
    db: AsyncSession,
    tool: str,
    args: Dict[str, Any],
    result: Any
) -> None:
    """Invalidate cache entries after successful write operation."""
    tags = []

    if tool == "notion.update_page":
        tags.append(f"notion:page:{args['page_id']}")
        # Also invalidate parent database if page is in database
        if parent_db := extract_parent_database(result):
            tags.append(f"notion:database:{parent_db}")

    elif tool == "github.create_commit":
        tags.append(f"github:repo:{args['owner']}/{args['repo']}")
        for file in args.get('files', []):
            tags.append(f"github:file:{args['owner']}/{args['repo']}/{file['path']}")

    if tags:
        await db.execute(
            text("""
                DELETE FROM agent_cache
                WHERE id IN (
                    SELECT DISTINCT cache_id
                    FROM agent_cache_tags
                    WHERE tag = ANY(:tags)
                )
            """),
            {"tags": tags}
        )
```

### 3. Automatic Write Invalidation

Write operations automatically invalidate related read caches:

| Write Operation | Invalidated Caches |
|-----------------|-------------------|
| `notion.update_page` | Same page reads, parent database |
| `notion.create_page` | Parent database, searches |
| `notion.delete_page` | Page, parent database, searches |
| `github.create_commit` | Repository, affected files |
| `github.create_pr` | Repository, PR lists |
| `github.merge_pr` | Repository, branch, files |

## Advisory Lock Implementation

### Lock Key Derivation

```python
def get_advisory_lock_key(cache_key: str) -> int:
    """Convert cache key to PostgreSQL bigint for advisory lock."""
    # Use first 8 bytes of hash for signed 64-bit integer
    key_hash = hashlib.sha256(cache_key.encode()).digest()[:8]
    return int.from_bytes(key_hash, byteorder='big', signed=True)

async def acquire_lock_with_timeout(
    db: AsyncSession,
    cache_key: str,
    timeout_ms: int = 5000
) -> bool:
    """Try to acquire advisory lock with timeout."""
    lock_key = get_advisory_lock_key(cache_key)

    # Try non-blocking acquire first
    result = await db.execute(
        text("SELECT pg_try_advisory_lock(:key)"),
        {"key": lock_key}
    )
    if result.scalar():
        return True

    # Wait with timeout
    result = await db.execute(
        text("SELECT pg_advisory_lock_shared(:key, :timeout)"),
        {"key": lock_key, "timeout": timeout_ms}
    )
    return result.scalar()
```

### Failure Modes & Recovery

1. **Lock Timeout**: Fall back to direct MCP call (no cache write)
2. **Process Crash**: PostgreSQL releases advisory locks on connection close
3. **Deadlock**: Impossible with single-key locking pattern
4. **Long-Running Lock**: 30-second maximum via MCP timeout

## Cache Metadata

### Response Metadata Structure

```json
{
  "cacheHit": true,              // Was response served from cache?
  "cacheKey": "user_123:notion.get_page:v1:a1b2c3d4", // Full cache key
  "cached_at": "2024-01-15T10:30:00Z",   // Original cache time
  "expires_at": "2024-01-15T14:30:00Z",  // Expiration time
  "cacheTtlRemaining": 7200,     // Seconds until expiration
  "hit_count": 42,               // Number of cache hits
  "content_hash": "sha256:abc...", // Content verification hash
  "source": "cache",             // "origin" or "cache"
  "stale": false,                // Served past expiration?
  "compute_ms": null             // Original computation time (if known)
}
```

### Observability Metrics

```sql
-- Cache performance dashboard query
WITH cache_stats AS (
  SELECT
    tool,
    namespace,
    COUNT(*) as total_entries,
    SUM(hit_count) as total_hits,
    AVG(hit_count) as avg_hits_per_entry,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY hit_count) as median_hits,
    AVG(EXTRACT(EPOCH FROM (expires_at - created_at))) as avg_ttl_seconds,
    SUM(content_size_kb) as total_size_kb
  FROM agent_cache
  WHERE created_at > NOW() - INTERVAL '24 hours'
  GROUP BY tool, namespace
)
SELECT
  tool,
  total_entries,
  total_hits,
  ROUND(avg_hits_per_entry, 2) as avg_hits,
  median_hits,
  ROUND(avg_ttl_seconds / 3600, 1) as avg_ttl_hours,
  ROUND(total_size_kb / 1024.0, 2) as total_size_mb,
  ROUND(100.0 * total_hits / NULLIF(total_entries, 0), 1) as reuse_rate_pct
FROM cache_stats
ORDER BY total_hits DESC;
```

## Operational Procedures

### Runtime TTL Override

```bash
# Override TTL via environment variable (requires restart)
export CACHE_TTL_NOTION_GET_PAGE=7200  # 2 hours instead of 4

# Override for specific request
curl -X POST /chat \
  -H "X-Cache-TTL-Override: 3600" \  # Custom TTL for this request
  -d '{"messages": [...]}'
```

### Cache Warming

```python
# Proactive cache warming for common queries
async def warm_cache_for_user(user_id: str, workspace_id: str):
    """Pre-populate cache with frequently accessed data."""
    common_pages = await get_frequently_accessed_pages(user_id)

    for page_id in common_pages:
        cache_key = f"user_{user_id}:notion.get_page:v1:{hash_args({'page_id': page_id})}"

        # Check if already cached
        if not await cache_exists(cache_key):
            # Fetch and cache with extended TTL
            result = await mcp_client.call_tool("notion.get_page", {"page_id": page_id})
            await write_cache(cache_key, result, ttl_seconds=28800)  # 8 hours
```

### Batch Invalidation

```sql
-- Invalidate all cache entries for a user
DELETE FROM agent_cache
WHERE namespace = 'user_123';

-- Invalidate all Notion-related caches
DELETE FROM agent_cache
WHERE tool LIKE 'notion.%';

-- Invalidate by age
DELETE FROM agent_cache
WHERE created_at < NOW() - INTERVAL '7 days';

-- Staged invalidation to avoid locks
WITH batch AS (
  SELECT id FROM agent_cache
  WHERE expires_at < NOW()
  LIMIT 1000
)
DELETE FROM agent_cache
WHERE id IN (SELECT id FROM batch);
```

### Version Migration

```python
# When bumping cache version (v1 -> v2)
async def migrate_cache_version(old_version: str, new_version: str, tool: str):
    """Invalidate old version caches when schema changes."""
    await db.execute(
        text("""
            UPDATE agent_cache
            SET expires_at = NOW() + INTERVAL '5 minutes'
            WHERE tool = :tool AND version = :old_version
        """),
        {"tool": tool, "old_version": old_version}
    )
    # Old entries expire gracefully while new version populates
```

## Security & Privacy

### No PII in Cache Keys

- User IDs and workspace IDs are used for namespacing
- Never include user email, names, or content in keys
- Arguments are hashed, not stored in plaintext
- Device tokens are hashed if used in namespace

### Cross-User Isolation

```python
# Namespace enforcement in cache reads
async def read_cache(
    user_id: str,
    tool: str,
    args: Dict[str, Any]
) -> Optional[Dict]:
    namespace = f"user_{user_id}"
    cache_key = make_cache_key(namespace, tool, VERSION, args)

    # Query explicitly includes namespace check
    result = await db.execute(
        text("""
            SELECT content, expires_at, stale_at
            FROM agent_cache
            WHERE cache_key = :key
              AND namespace = :namespace
              AND expires_at > NOW()
        """),
        {"key": cache_key, "namespace": namespace}
    )
    # ...
```

### Audit Trail

```sql
-- Cache access audit log
CREATE TABLE cache_audit_log (
  id BIGSERIAL PRIMARY KEY,
  cache_key VARCHAR(512),
  namespace VARCHAR(255),
  user_id UUID,
  action VARCHAR(20), -- 'read', 'write', 'invalidate'
  request_id UUID,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Periodic audit review
SELECT
  u.email,
  COUNT(DISTINCT a.cache_key) as unique_keys_accessed,
  COUNT(*) as total_accesses,
  MAX(a.created_at) as last_access
FROM cache_audit_log a
JOIN users u ON a.user_id = u.id
WHERE a.created_at > NOW() - INTERVAL '7 days'
GROUP BY u.email
ORDER BY total_accesses DESC;
```

## Performance Tuning

### Index Optimization

```sql
-- Analyze cache query patterns
CREATE INDEX CONCURRENTLY idx_cache_hot_paths
ON agent_cache (namespace, tool, expires_at DESC)
WHERE hit_count > 10;

-- Partial index for active entries only
CREATE INDEX CONCURRENTLY idx_cache_active
ON agent_cache (cache_key)
WHERE expires_at > NOW();

-- BRIN index for time-series queries
CREATE INDEX idx_cache_created_brin
ON agent_cache USING BRIN (created_at);
```

### Connection Pool Settings

```python
# Optimal pool configuration for cache operations
DATABASE_URL = "postgresql://user:pass@host/db"
engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,           # Connections for normal load
    max_overflow=10,        # Burst capacity
    pool_timeout=30,        # Wait for connection
    pool_recycle=3600,      # Recycle connections hourly
    pool_pre_ping=True,     # Verify connections
)
```

### Monitoring Queries

```sql
-- Real-time cache effectiveness
SELECT
  date_trunc('hour', last_accessed) as hour,
  COUNT(*) as requests,
  SUM(CASE WHEN hit_count > 0 THEN 1 ELSE 0 END) as cache_hits,
  ROUND(100.0 * SUM(CASE WHEN hit_count > 0 THEN 1 ELSE 0 END) / COUNT(*), 2) as hit_rate,
  ROUND(AVG(content_size_kb), 2) as avg_size_kb,
  ROUND(AVG(EXTRACT(EPOCH FROM (last_accessed - created_at))), 2) as avg_age_seconds
FROM agent_cache
WHERE last_accessed > NOW() - INTERVAL '24 hours'
GROUP BY 1
ORDER BY 1 DESC;
```
