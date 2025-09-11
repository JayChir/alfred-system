# PostgreSQL Maintenance Guide for Docker Deployment

This guide covers PostgreSQL maintenance best practices for the Alfred Agent Core running in Docker on a DigitalOcean droplet.

## Table of Contents

1. [Docker Configuration](#docker-configuration)
2. [Connection Pool Management](#connection-pool-management)
3. [Autovacuum Configuration](#autovacuum-configuration)
4. [Backup & Recovery](#backup--recovery)
5. [Monitoring & Health Checks](#monitoring--health-checks)
6. [Troubleshooting](#troubleshooting)
7. [Resource Optimization](#resource-optimization)

---

## Docker Configuration

### Current Setup

```yaml
# From docker-compose.yml
db:
  image: postgres:15-alpine
  environment:
    - POSTGRES_USER=alfred
    - POSTGRES_PASSWORD=password
    - POSTGRES_DB=agent_core
  volumes:
    - postgres_data:/var/lib/postgresql/data
  ports:
    - "5432:5432"
  restart: unless-stopped
```

### Production Hardening

For production deployment on a $50-100/mo droplet, enhance the configuration:

```yaml
db:
  image: postgres:15-alpine
  environment:
    - POSTGRES_USER=alfred
    - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}  # Use env file
    - POSTGRES_DB=agent_core
    - POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256
    # Performance tuning for limited resources
    - POSTGRES_SHARED_BUFFERS=256MB          # ~25% of available RAM
    - POSTGRES_EFFECTIVE_CACHE_SIZE=512MB    # ~50% of available RAM
    - POSTGRES_WORK_MEM=8MB                  # For sorting/hashing ops
    - POSTGRES_MAINTENANCE_WORK_MEM=64MB     # For vacuum, index creation
    - POSTGRES_MAX_CONNECTIONS=50           # Conservative for 1GB droplet
  volumes:
    - postgres_data:/var/lib/postgresql/data
    - ./postgres/postgresql.conf:/etc/postgresql/postgresql.conf:ro
    - ./postgres/pg_hba.conf:/etc/postgresql/pg_hba.conf:ro
  command: >
    postgres
    -c config_file=/etc/postgresql/postgresql.conf
    -c hba_file=/etc/postgresql/pg_hba.conf
  ports:
    - "127.0.0.1:5432:5432"  # Only bind to localhost
  restart: unless-stopped
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U alfred -d agent_core"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 30s
```

### Custom PostgreSQL Configuration

Create `postgres/postgresql.conf`:

```ini
# PostgreSQL configuration for Docker deployment
# Optimized for 1GB droplet with shared app resources

# Connection Settings
max_connections = 50
superuser_reserved_connections = 3

# Memory Settings (assumes ~1GB total RAM, ~60% available for PostgreSQL)
shared_buffers = 256MB           # 25% of RAM
effective_cache_size = 512MB     # 50% of RAM
work_mem = 8MB                   # For sorting operations
maintenance_work_mem = 64MB      # For vacuum, reindex

# Checkpoint Settings (reduce I/O spikes)
checkpoint_completion_target = 0.9
wal_buffers = 16MB
max_wal_size = 1GB
min_wal_size = 256MB

# Query Planner
random_page_cost = 1.1          # SSD storage
effective_io_concurrency = 200  # SSD concurrency

# Logging (essential for monitoring)
log_destination = 'stderr'
logging_collector = on
log_directory = 'pg_log'
log_filename = 'postgresql-%Y-%m-%d.log'
log_min_messages = warning
log_min_error_statement = error
log_checkpoints = on
log_connections = on
log_disconnections = on
log_lock_waits = on
log_temp_files = 10MB
log_autovacuum_min_duration = 0

# Autovacuum (tuned for high-write cache tables)
autovacuum = on
autovacuum_max_workers = 2
autovacuum_naptime = 30s         # More frequent than default (1min)
autovacuum_vacuum_threshold = 50
autovacuum_vacuum_scale_factor = 0.1
autovacuum_analyze_threshold = 50
autovacuum_analyze_scale_factor = 0.05

# Statement timeout (prevent runaway queries)
statement_timeout = 30000       # 30 seconds
lock_timeout = 10000           # 10 seconds
idle_in_transaction_session_timeout = 300000  # 5 minutes
```

---

## Connection Pool Management

### Application Pool Configuration

The app uses SQLAlchemy with async PostgreSQL. Configure in `src/db/connection.py`:

```python
# Recommended pool settings for Docker deployment
DATABASE_POOL_SIZE = int(os.getenv("DATABASE_POOL_SIZE", "5"))      # Small pool - app and DB on same host
DATABASE_MAX_OVERFLOW = int(os.getenv("DATABASE_MAX_OVERFLOW", "10")) # Overflow for spikes
DATABASE_POOL_TIMEOUT = int(os.getenv("DATABASE_POOL_TIMEOUT", "10"))  # Fast timeout
DATABASE_POOL_RECYCLE = int(os.getenv("DATABASE_POOL_RECYCLE", "3600")) # 1 hour recycle
```

### Rationale for Small Pools

With Docker deployment on same host:
- **Lower latency**: No network overhead, < 1ms connection time
- **Shared resources**: App and DB compete for same CPU/memory
- **Connection overhead**: PostgreSQL connections use ~8MB each
- **Calculation**: 5 app pool + 10 overflow + 5 system = 20 total connections (well under max_connections=50)

### Environment Configuration

Update `.env` for production:

```env
# Database pool settings for Docker deployment
DATABASE_POOL_SIZE=5          # Core pool size
DATABASE_MAX_OVERFLOW=10      # Additional connections for spikes
DATABASE_POOL_TIMEOUT=10      # Connection timeout in seconds
DATABASE_POOL_RECYCLE=3600    # Recycle connections every hour
```

---

## Autovacuum Configuration

### High-Write Tables Requiring Special Attention

Based on our schema, these tables will have frequent writes:

1. **agent_cache** - High write/delete turnover from cache operations
2. **token_usage** - Per-request inserts for metering
3. **tool_call_log** - Every MCP call logged
4. **thread_messages** - User conversation history

### Per-Table Autovacuum Tuning

```sql
-- Configure aggressive autovacuum for cache table (high churn)
ALTER TABLE agent_cache SET (
  autovacuum_vacuum_scale_factor = 0.05,    -- Vacuum when 5% of rows are dead
  autovacuum_vacuum_threshold = 25,         -- Minimum 25 dead rows
  autovacuum_analyze_scale_factor = 0.02,   -- Analyze when 2% of rows change
  autovacuum_vacuum_cost_delay = 10         -- Faster vacuum (less throttling)
);

-- Configure for token_usage (insert-heavy, minimal updates)
ALTER TABLE token_usage SET (
  autovacuum_vacuum_scale_factor = 0.2,     -- Less frequent vacuum (inserts only)
  autovacuum_analyze_scale_factor = 0.1,    -- More frequent analyze (for query planning)
  autovacuum_vacuum_threshold = 100
);

-- Configure for tool_call_log (append-only with occasional cleanup)
ALTER TABLE tool_call_log SET (
  autovacuum_vacuum_scale_factor = 0.2,
  autovacuum_analyze_scale_factor = 0.1,
  autovacuum_vacuum_threshold = 50
);

-- Configure for thread_messages (moderate write load)
ALTER TABLE thread_messages SET (
  autovacuum_vacuum_scale_factor = 0.1,
  autovacuum_analyze_scale_factor = 0.05,
  autovacuum_vacuum_threshold = 50
);
```

### Manual Vacuum Commands

For maintenance during low-traffic periods:

```sql
-- Full vacuum with analyze (reclaims disk space)
VACUUM FULL ANALYZE agent_cache;

-- Regular vacuum (faster, online operation)
VACUUM ANALYZE agent_cache;

-- Check vacuum progress
SELECT
  schemaname,
  tablename,
  attname,
  n_distinct,
  most_common_vals,
  most_common_freqs,
  last_vacuum,
  last_autovacuum,
  last_analyze,
  last_autoanalyze
FROM pg_stats
WHERE tablename IN ('agent_cache', 'token_usage', 'tool_call_log');
```

---

## Backup & Recovery

### Docker Volume Backup Strategy

#### 1. Consistent Backup Script

Create `scripts/backup-db.sh`:

```bash
#!/bin/bash
# PostgreSQL Docker backup script

set -e  # Exit on any error

BACKUP_DIR="/opt/alfred-backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
CONTAINER_NAME="agent-core-db-1"  # Adjust based on docker-compose naming

echo "Starting PostgreSQL backup: $TIMESTAMP"

# Ensure backup directory exists
mkdir -p $BACKUP_DIR

# Create consistent backup using pg_dump
docker exec $CONTAINER_NAME pg_dump -U alfred -d agent_core -v \
  --format=custom --compress=9 > $BACKUP_DIR/alfred_backup_$TIMESTAMP.dump

# Create SQL export for portability
docker exec $CONTAINER_NAME pg_dump -U alfred -d agent_core -v \
  --format=plain --inserts > $BACKUP_DIR/alfred_backup_$TIMESTAMP.sql

# Verify backup integrity
if docker exec $CONTAINER_NAME pg_restore --list $BACKUP_DIR/alfred_backup_$TIMESTAMP.dump > /dev/null; then
  echo "✅ Backup verified: alfred_backup_$TIMESTAMP.dump"
else
  echo "❌ Backup verification failed!"
  exit 1
fi

# Cleanup old backups (keep last 7 days)
find $BACKUP_DIR -name "alfred_backup_*.dump" -mtime +7 -delete
find $BACKUP_DIR -name "alfred_backup_*.sql" -mtime +7 -delete

echo "✅ Backup completed successfully"
```

#### 2. Volume Snapshot (Alternative)

```bash
#!/bin/bash
# Docker volume backup using tar

BACKUP_DIR="/opt/alfred-backups/volumes"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
VOLUME_NAME="agent-core_postgres_data"

echo "Creating volume snapshot: $TIMESTAMP"

# Stop database container for consistent snapshot
docker-compose stop db

# Create volume backup
docker run --rm -v $VOLUME_NAME:/data -v $BACKUP_DIR:/backup \
  alpine:latest tar czf /backup/postgres_volume_$TIMESTAMP.tar.gz -C /data .

# Restart database
docker-compose start db

echo "✅ Volume snapshot completed: postgres_volume_$TIMESTAMP.tar.gz"
```

#### 3. Automated Backup with Cron

```bash
# Add to crontab for automated backups
# crontab -e

# Daily backup at 2 AM
0 2 * * * /opt/alfred-system/agent-core/scripts/backup-db.sh >> /var/log/alfred-backup.log 2>&1

# Weekly volume snapshot on Sundays at 1 AM
0 1 * * 0 /opt/alfred-system/agent-core/scripts/backup-volume.sh >> /var/log/alfred-backup.log 2>&1
```

#### 4. Recovery Procedures

```bash
# Restore from pg_dump backup
RESTORE_FILE="/opt/alfred-backups/alfred_backup_20240901_020000.dump"

# Stop application to prevent writes during restore
docker-compose stop app

# Drop and recreate database
docker exec agent-core-db-1 dropdb -U alfred agent_core
docker exec agent-core-db-1 createdb -U alfred agent_core

# Restore from backup
docker exec -i agent-core-db-1 pg_restore -U alfred -d agent_core -v < $RESTORE_FILE

# Restart application
docker-compose start app
```

---

## Monitoring & Health Checks

### Key Metrics to Monitor

#### 1. Connection Health

```sql
-- Current connections by state
SELECT
  state,
  count(*) as connections,
  max(now() - state_change) as max_duration
FROM pg_stat_activity
WHERE datname = 'agent_core'
GROUP BY state;

-- Long-running queries (>30 seconds)
SELECT
  pid,
  now() - pg_stat_activity.query_start AS duration,
  query,
  state
FROM pg_stat_activity
WHERE (now() - pg_stat_activity.query_start) > interval '30 seconds'
  AND state != 'idle';
```

#### 2. Cache Hit Ratios

```sql
-- Overall cache hit ratio (should be >95%)
SELECT
  sum(heap_blks_hit) / (sum(heap_blks_hit) + sum(heap_blks_read)) * 100 as cache_hit_ratio
FROM pg_statio_user_tables;

-- Per-table cache hit ratios
SELECT
  schemaname,
  tablename,
  heap_blks_read,
  heap_blks_hit,
  round(heap_blks_hit::numeric / (heap_blks_hit + heap_blks_read) * 100, 2) as hit_ratio
FROM pg_statio_user_tables
WHERE heap_blks_read > 0
ORDER BY hit_ratio ASC;
```

#### 3. Vacuum Effectiveness

```sql
-- Tables needing vacuum attention
SELECT
  schemaname,
  tablename,
  n_dead_tup,
  n_live_tup,
  round(n_dead_tup::numeric / (n_live_tup + n_dead_tup) * 100, 2) as dead_ratio,
  last_vacuum,
  last_autovacuum
FROM pg_stat_user_tables
WHERE n_dead_tup > 0
ORDER BY dead_ratio DESC;
```

### Health Check Endpoints

Add PostgreSQL health checks to the FastAPI app:

```python
# In src/routers/health.py

@router.get("/healthz/db")
async def database_health():
    """Detailed database health check."""
    try:
        async with get_db() as db:
            # Basic connectivity
            result = await db.execute(text("SELECT 1"))

            # Connection pool status
            pool_size = db.engine.pool.size()
            checked_out = db.engine.pool.checkedout()

            # Recent vacuum stats for critical tables
            vacuum_stats = await db.execute(text("""
                SELECT tablename, last_autovacuum, n_dead_tup, n_live_tup
                FROM pg_stat_user_tables
                WHERE tablename IN ('agent_cache', 'token_usage', 'tool_call_log')
            """))

            return {
                "status": "healthy",
                "pool": {
                    "size": pool_size,
                    "checked_out": checked_out,
                    "available": pool_size - checked_out
                },
                "vacuum_stats": [dict(row) for row in vacuum_stats]
            }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Database health check failed: {str(e)}"
        )
```

### Docker Monitoring Commands

```bash
# Container resource usage
docker stats agent-core-db-1 --no-stream

# PostgreSQL process list
docker exec agent-core-db-1 ps aux | grep postgres

# Container logs (last 100 lines)
docker logs --tail 100 agent-core-db-1

# Real-time log monitoring
docker logs -f agent-core-db-1
```

---

## Troubleshooting

### Common Issues and Solutions

#### 1. Out of Connections

**Symptoms:**
```
FATAL: remaining connection slots are reserved for non-replication superuser connections
```

**Diagnosis:**
```sql
-- Check current connections
SELECT count(*), state FROM pg_stat_activity GROUP BY state;

-- Find connection leaks
SELECT pid, application_name, client_addr, state, state_change
FROM pg_stat_activity
WHERE state = 'idle in transaction'
  AND state_change < now() - interval '5 minutes';
```

**Solutions:**
1. Increase `max_connections` in postgresql.conf
2. Reduce application pool size
3. Kill idle connections: `SELECT pg_terminate_backend(pid);`
4. Check for connection leaks in application code

#### 2. High CPU Usage

**Diagnosis:**
```sql
-- Find expensive queries
SELECT query, calls, total_time, mean_time
FROM pg_stat_statements
ORDER BY total_time DESC
LIMIT 10;

-- Check for missing indexes
SELECT schemaname, tablename, attname, n_distinct, correlation
FROM pg_stats
WHERE schemaname = 'public'
  AND n_distinct > 100
  AND correlation < 0.1;
```

**Solutions:**
1. Add missing indexes on high-cardinality columns
2. Optimize expensive queries
3. Increase `work_mem` for sorting operations
4. Consider query rewriting or caching

#### 3. Disk Space Issues

**Diagnosis:**
```bash
# Check Docker volume usage
docker exec agent-core-db-1 df -h

# Check PostgreSQL table sizes
docker exec agent-core-db-1 psql -U alfred -d agent_core -c "
SELECT
  schemaname,
  tablename,
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;"
```

**Solutions:**
1. Run `VACUUM FULL` on large tables during maintenance windows
2. Implement cache expiration to limit `agent_cache` growth
3. Archive old `token_usage` and `tool_call_log` records
4. Monitor and alert on disk usage

#### 4. Container Won't Start

**Common causes:**
1. Permission issues on volume mount
2. Port conflicts (5432 already in use)
3. Invalid PostgreSQL configuration
4. Corrupted data files

**Solutions:**
```bash
# Check container logs
docker logs agent-core-db-1

# Verify port availability
netstat -tlnp | grep :5432

# Reset data volume (DESTRUCTIVE - will lose data)
docker-compose down
docker volume rm agent-core_postgres_data
docker-compose up -d

# Fix permissions
docker exec -u root agent-core-db-1 chown -R postgres:postgres /var/lib/postgresql/data
```

### Emergency Procedures

#### Database Recovery from Backup

```bash
# 1. Stop all services
docker-compose down

# 2. Remove corrupted volume
docker volume rm agent-core_postgres_data

# 3. Recreate services
docker-compose up -d db

# 4. Wait for initialization, then restore
sleep 30
docker exec -i agent-core-db-1 pg_restore -U alfred -d agent_core -v < /opt/alfred-backups/latest_backup.dump

# 5. Restart application
docker-compose up -d app
```

#### Performance Emergency

```bash
# 1. Identify blocking queries
docker exec agent-core-db-1 psql -U alfred -d agent_core -c "
SELECT
  blocked_locks.pid AS blocked_pid,
  blocked_activity.usename AS blocked_user,
  blocking_locks.pid AS blocking_pid,
  blocking_activity.usename AS blocking_user,
  blocked_activity.query AS blocked_statement,
  blocking_activity.query AS current_statement_in_blocking_process
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype
JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;"

# 2. Kill problematic connections (carefully!)
# docker exec agent-core-db-1 psql -U alfred -d agent_core -c "SELECT pg_terminate_backend(PID);"
```

---

## Resource Optimization

### Droplet Size Recommendations

#### $50/mo Droplet (1GB RAM, 1 vCPU, 25GB SSD)
```yaml
# PostgreSQL settings for minimal droplet
shared_buffers = 256MB
effective_cache_size = 512MB
work_mem = 4MB
maintenance_work_mem = 32MB
max_connections = 30
```

#### $100/mo Droplet (2GB RAM, 1 vCPU, 50GB SSD)
```yaml
# PostgreSQL settings for standard droplet
shared_buffers = 512MB
effective_cache_size = 1024MB
work_mem = 8MB
maintenance_work_mem = 64MB
max_connections = 50
```

### Memory Usage Monitoring

```sql
-- Check PostgreSQL memory usage
SELECT
  setting,
  unit,
  current_setting(name) as current_value
FROM pg_settings
WHERE name IN (
  'shared_buffers',
  'effective_cache_size',
  'work_mem',
  'maintenance_work_mem'
);
```

### Performance Tuning Checklist

- [ ] **Connection pooling**: App pool size ≤ 10 for single-droplet deployment
- [ ] **Memory allocation**: shared_buffers = 25% RAM, effective_cache_size = 50% RAM
- [ ] **Autovacuum**: Tuned for high-write tables (agent_cache, token_usage)
- [ ] **Logging**: Enabled for slow queries and connection issues
- [ ] **Backups**: Automated daily backups with 7-day retention
- [ ] **Monitoring**: Health checks and key metrics tracking
- [ ] **Indexes**: Proper indexing on cache keys and foreign keys
- [ ] **Disk space**: Monitoring and alerting on volume usage

---

## Summary

This maintenance guide provides operational procedures for running PostgreSQL in Docker on a DigitalOcean droplet. Key considerations:

1. **Resource constraints**: Shared CPU/memory between app and database
2. **Connection management**: Small pools with fast timeouts
3. **Autovacuum tuning**: Aggressive settings for high-churn cache tables
4. **Backup strategy**: Automated pg_dump with Docker volume snapshots
5. **Monitoring**: Custom health checks and performance metrics
6. **Troubleshooting**: Common Docker PostgreSQL issues and solutions

For production deployment, ensure proper security (password management, network isolation) and monitoring (log aggregation, alerting) are in place.
