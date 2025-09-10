### Review: feat(cache): implement forceRefresh param and write-path invalidation (#25)

Commit: 21614fff09dcf3664f2697b1dd18c3a06bf33d93
Scope: cache invalidation, force refresh path, tagging schema, tests

#### Summary
- Adds tag-based invalidation with safety caps and helper taxonomy.
- Implements write-path invalidation for denylisted (mutation/time-sensitive) tools.
- Honors forceRefresh by skipping cache read and writing fresh results.
- Introduces migration for tag table and size/idempotent columns.
- Adds unit/manual tests that exercise tag creation, safety caps, and refresh behavior.

#### What’s solid
- Tag taxonomy is clear and consistent:
  - `server:{name}`, `tool:{server}.{tool}`, `user:{id}`, `workspace:{id}`, plus resource tags (`page:`, `database:`, `repo:`, `issue:`…).
- Read-path caching now stores tags via `_build_cache_tags(...)` → `labels=tags` in `cache.set(...)`.
- Write-path invalidation:
  - In hook path (process_tool_call) and direct `call_tool`, denylisted ops call `invalidate_after_write(...)` with scope-aware tags.
  - `PostgreSQLInvokeCache.invalidate_by_tags(...)` caps by default and supports force override.
- Migration `003_cache_tags.sql` creates `agent_cache_tags` and an `invalidate_cache_by_tags()` helper. Index coverage looks good.
- Tests cover tag composition, denylist detection, safety caps, and refresh semantics.

#### Nits / risks / follow-ups
- Streaming parity: non-streaming responses surface `meta.cacheHit/cacheTtlRemaining`; streaming final event still lacks these fields. Consider adding for consistency (see snippet below).
- Invalidation cap configurability: `max_entries=100` in `MCPRouter.invalidate_after_write(...)` may be tight for bulk edits. Suggest a settings value (e.g., `cache_invalidation_cap_default`) and optionally per-server overrides.
- Diagnostics: For refresh/bypass paths in `call_tool`, consider including a `mode` flag in returned cache meta (you already do for `bypass`; add for `refresh`) to simplify observability.
- Column `agent_cache.idempotent` added but unused in code paths. Consider wiring this for future audit (e.g., mark entries originating from read-only calls only) or remove until needed to avoid schema drift.
- Tag growth: For high-churn resources, tag tables could grow quickly. You already use `ON DELETE CASCADE`; add periodic pruning job and/or size-based guardrails (Settings for `MAX_CACHE_ENTRY_SIZE` exists, good).

#### Suggested edits (non-blocking)
- Add cache metadata to streaming final event in `agent_orchestrator.py`:
```diff
--- a/agent-core/src/services/agent_orchestrator.py
+++ b/agent-core/src/services/agent_orchestrator.py
@@ -426,12 +426,23 @@
                 # Send final event with usage stats
                 usage = result.usage()
+                cache_hit = False
+                cache_ttl_remaining = None
+                if hasattr(deps, "cache_metadata"):
+                    cache_hit = deps.cache_metadata.get("cache_hit", False)
+                    cache_ttl_remaining = deps.cache_metadata.get("cache_ttl_remaining")
                 yield StreamEvent(
                     type="final",
                     data={
                         "usage": {
                             "input_tokens": usage.input_tokens,
                             "output_tokens": usage.output_tokens,
                             "total_tokens": usage.total_tokens,
                         },
                         "duration_ms": int((time.time() - start_time) * 1000),
                         "model": self.settings.anthropic_model,
+                        "cacheHit": cache_hit,
+                        "cacheTtlRemaining": cache_ttl_remaining,
                     },
                     request_id=request_id,
                 )
```
- Make invalidation cap configurable:
```python
# in src/config.py (Settings)
cache_invalidation_cap_default: int = Field(100, ge=1, description="Max entries invalidated per write")
```
```python
# in mcp_router.invalidate_after_write(...)
result = await self.cache.invalidate_by_tags(tags, max_entries=self.settings.cache_invalidation_cap_default)
```
- Optionally include `mode` in `call_tool` meta for refresh path:
```python
if cache_mode == "refresh":
    # after direct call
    return result, {"cacheHit": False, "mode": "refresh"}
```

#### Validation notes
- Repeat identical read → second call shows `cacheHit=true` and TTL decreases; forceRefresh path shows `cacheHit=false` and fresh TTL.
- Perform a write (denylisted tool) → subsequent read results for affected tags should be invalidated and repopulated.

Overall: Strong, production-leaning change set that brings proper invalidation and a safer default caching stance. The streaming parity and small observability tweaks would round it out nicely.
