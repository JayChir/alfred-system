# Alfred Agent Core Documentation

## 📚 Documentation Index

### Core Documentation
- [Database Schema & ERD](./database-schema.md) - Complete database design with entity relationships
- [Cache Policy & TTL Strategy](./cache-policy.md) - Cache key specification, TTL policies, and invalidation patterns
- [Database Maintenance](./database-maintenance.md) - PostgreSQL operations, Docker deployment, backup & monitoring

### Implementation Guides
- [Context Strategy](./context-strategy-v1.md) - Token optimization and context management
- [Threads Implementation](./threads-implementation.md) - Cross-device conversation continuity
- [Issue #23 Implementation](./issue-23-implementation-plan.md) - Device session management

### Operational Documentation
- **Security & Encryption** - Token storage, Fernet encryption, key rotation (see Database Schema)
- **Cache Operations** - Runtime TTL overrides, invalidation strategies (see Cache Policy)
- **Database Maintenance** - PostgreSQL tuning, vacuum settings, index optimization (see Database Maintenance)

## 🔗 Quick Reference

### Database Tables (13 total)
1. `users` - User accounts and authentication
2. `device_sessions` - Device-based session management
3. `notion_connections` - OAuth tokens (encrypted)
4. `oauth_states` - CSRF protection states
5. `threads` - Conversation threads
6. `thread_messages` - Message history
7. `tool_call_log` - Tool execution journal
8. `agent_cache` - Cache entries
9. `agent_cache_tags` - Tag-based invalidation
10. `token_usage` - Per-request token tracking
11. `token_usage_rollup_daily` - Daily aggregations
12. `user_token_budgets` - Usage limits
13. `oauth_states` - OAuth flow states

### Cache Key Format
```
{namespace}:{tool}:v{version}:{args_hash_16}
```
Example: `user_123:notion.get_page:v1:a1b2c3d4e5f67890`

### Environment Variables
- Cache TTLs: `CACHE_TTL_{PROVIDER}_{TOOL}` (seconds)
- Feature flags: `FEATURE_NOTION_HOSTED_MCP`
- Security: `FERNET_KEY`, `JWT_SECRET`

## 🚀 Getting Started

For developers new to the codebase:
1. Review the [Database Schema](./database-schema.md) to understand data relationships
2. Read the [Cache Policy](./cache-policy.md) for performance optimization patterns
3. Check implementation guides for specific features

## 🔧 Maintenance

### Cache Operations
```bash
# Override TTL at runtime
export CACHE_TTL_NOTION_GET_PAGE=7200  # 2 hours

# Force cache refresh for specific request
curl -X POST /chat -d '{"forceRefresh": true, ...}'
```

### Database Operations
```sql
-- Check cache hit rates
SELECT
  date_trunc('hour', last_accessed) as hour,
  COUNT(*) as requests,
  SUM(hit_count) as hits,
  ROUND(100.0 * SUM(hit_count) / COUNT(*), 2) as hit_rate
FROM agent_cache
WHERE last_accessed > NOW() - INTERVAL '24 hours'
GROUP BY 1 ORDER BY 1 DESC;
```

## 📝 Contributing

When updating documentation:
1. Keep diagrams in Mermaid format for version control
2. Update this index when adding new documents
3. Include examples for complex concepts
4. Document security implications
5. Run markdown linting before commits

## 🔍 CI/CD Checks

- Markdown linting: `markdownlint docs/**/*.md`
- Mermaid rendering: Validated in GitHub preview
- Link checking: All internal references must be valid
