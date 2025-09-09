# Manual Test Scripts

This directory contains manual test scripts for validating specific functionality of the Alfred Agent Core.

## Test Scripts

### Cache Testing
- `test_cache_direct.py` - Direct PostgreSQL cache testing
- `test_cache_simple.py` - Simple cache validation
- `test_cache_comprehensive.py` - Comprehensive cache testing suite
- `test_postgres_integration.py` - PostgreSQL integration tests

### MCP Server Testing
- `test_notion_cache.py` - Notion MCP server cache validation
- `test_notion_mcp_refresh.py` - Notion MCP token refresh testing
- `test_github_simple.py` - Simple GitHub MCP validation
- `test_github_cache_quick.py` - GitHub cache with timing metrics
- `test_time_cache.py` - Time MCP server cache testing (should NOT cache)

### Agent Testing
- `test_agent_cache.py` - Full agent cache integration testing
- `test_docker_cache.py` - Docker-based cache testing

### Other Tests
- `test_logging.py` - Logging configuration tests

## Running Tests

### Prerequisites
1. PostgreSQL must be running
2. Server must be running: `make run` or `uv run python -m src.app`
3. MCP servers should be configured (if testing MCP functionality)

### Running Individual Tests

```bash
# From the agent-core directory
python tests/manual/test_cache_direct.py
python tests/manual/test_notion_cache.py
python tests/manual/test_agent_cache.py
```

## Expected Results

### Cache Tests
- First call: Cache miss (cacheHit: false)
- Second identical call: Cache hit (cacheHit: true)
- Force refresh: Cache bypassed (cacheHit: false)

### Time Tests
- All calls should be cache misses (time operations are denylisted)

### Performance Metrics
- Cache hits should show significant latency reduction (>50%)
- Token usage should be reduced on cache hits
