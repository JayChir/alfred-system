## Summary

This PR improves the cache configuration by switching from an allowlist to a denylist approach, and completes the cache metadata propagation infrastructure.

## Changes

### 🔄 Cache Configuration Refactor
- **Replaced CACHEABLE_TOOLS allowlist with CACHE_DENYLIST**: New tools are now cached by default unless explicitly excluded
- **Added CACHE_TTL_OVERRIDES**: Flexible per-tool TTL configuration using pattern matching
- **Updated MCP router**: Now uses denylist pattern matching logic (lines 275-324 in mcp_router.py)

### ✅ Cache Metadata Propagation (NEW)
- **Added cache_metadata field to AgentDeps**: Fixed root cause of metadata not propagating
- **Fixed user_id handling**: Falls back to default_user_id when None
- **Fixed cacheTtlRemaining type mismatch**: Converts float to int to prevent 500 errors
- **Complete metadata flow**: MCP Router → AgentDeps → Orchestrator → Chat Router → API Response

### ⚙️ Configuration Improvements
- Increased database idle transaction timeout from 15s to 60s for better stability
- Added comprehensive denylist covering:
  - Time-sensitive operations (get_current_time, convert_time, etc.)
  - Mutation operations (create, update, delete, etc.)
  - Authentication operations (oauth, login, token, etc.)
  - Notification operations (notify, webhook, etc.)

### 📚 Documentation
- Created comprehensive context management strategy document (docs/context-strategy-v1.md)
- Added GitHub issue #58 for MVP context foundations

### 🧪 Testing
- Added multiple test scripts to validate cache behavior:
  - test_cache_direct.py: Direct PostgreSQL cache testing
  - test_notion_cache.py: Notion-specific cache validation
  - test_agent_cache.py: Full agent cache integration
  - test_docker_cache.py: Docker-based cache testing
  - test_time_cache.py: Time tool cache testing
  - test_github_simple.py: Simple GitHub cache validation (NEW)
  - test_github_cache_quick.py: GitHub cache with timing metrics (NEW)

## Benefits

1. **Reduced maintenance**: New tools are cached by default
2. **Safer defaults**: Only explicitly unsafe operations are excluded
3. **Better flexibility**: Pattern-based TTL overrides for fine-tuning
4. **Improved stability**: Longer database timeout prevents connection drops
5. **Complete cache visibility**: API responses now correctly report cacheHit status

## Testing Results

Tested with both Notion and GitHub MCP servers:
- ✅ Notion search results properly cached with 300s TTL
- ✅ GitHub operations properly cached based on patterns
- ✅ Time operations correctly excluded from cache
- ✅ Mutation operations correctly excluded from cache
- ✅ Cache metadata correctly reported in API responses

### Latest Test Output
```
Testing GitHub cache with narrowed denylist...

1. First call (expect cache miss):
   Response in 15.42s
   Cache hit: False (expected: False)

2. Second call (expect cache HIT):
   Response in 15.54s
   Cache hit: True (expected: True)
   TTL remaining: 284s

✅ GitHub cache test PASSED!
```

## Fixed Issues

- Resolves API responses always showing `cacheHit: false` even when cache hits occurred
- Fixes `user_id='None'` database lookup errors
- Fixes 500 error on cached responses due to float/int type mismatch

## Related Issues

- Addresses feedback from testing PR #57
- Sets foundation for issue #58 (Context management MVP)

---

🤖 Generated with [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>
