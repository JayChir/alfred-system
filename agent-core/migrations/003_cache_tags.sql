-- Migration: Add cache tags table for efficient invalidation
-- Issue #25: forceRefresh param + write-path invalidation

-- Create tags table for many-to-many cache tags
CREATE TABLE IF NOT EXISTS agent_cache_tags (
    cache_key TEXT NOT NULL REFERENCES agent_cache(cache_key) ON DELETE CASCADE,
    tag TEXT NOT NULL,  -- e.g., 'tool:notion.get_page', 'workspace:W123', 'page:PAGE_ID'
    PRIMARY KEY (cache_key, tag)
);

-- Index for fast tag lookups during invalidation
CREATE INDEX idx_agent_cache_tags_tag ON agent_cache_tags(tag);

-- Add index on cache_key for faster joins
CREATE INDEX idx_agent_cache_tags_cache_key ON agent_cache_tags(cache_key);

-- Function to invalidate cache by tags
CREATE OR REPLACE FUNCTION invalidate_cache_by_tags(p_tags TEXT[])
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM agent_cache
    WHERE cache_key IN (
        SELECT DISTINCT cache_key
        FROM agent_cache_tags
        WHERE tag = ANY(p_tags)
    );

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Add idempotent column to track mutation invalidations
ALTER TABLE agent_cache
ADD COLUMN IF NOT EXISTS idempotent BOOLEAN DEFAULT true;

-- Add size tracking for safety caps
ALTER TABLE agent_cache
ADD COLUMN IF NOT EXISTS size_bytes INTEGER;

-- Index for finding large entries
CREATE INDEX IF NOT EXISTS idx_agent_cache_size ON agent_cache(size_bytes)
WHERE size_bytes IS NOT NULL;
