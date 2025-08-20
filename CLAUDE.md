# CLAUDE.md - Alfred System Development

This file provides guidance to Claude Code when working in the Alfred System repository for tactical development and deployment.

## Project Context

**Alfred System**: Cloud-hosted MCP infrastructure serving as unified AI assistant backend across all devices.

- **Repository**: `alfred-system` (JayChir/alfred-system on GitHub)
- **Production**: DigitalOcean droplet at 134.209.51.66 (artemsys.ai domain)
- **Stack**: Python/FastAPI, PostgreSQL, Docker Compose, Pydantic AI
- **Phase**: Moving from infrastructure setup to active development

## Project Documentation Access

### Key Alfred System PRDs in Notion
```bash
# Main project page
notion:fetch id="2509c2b2-6704-81cb-a374-f52714624083"

# Comprehensive PRD v2.0
notion:fetch id="2519c2b2-6704-8024-8ed2-feabe84b1d37"

# MCP Infrastructure PRD
notion:fetch id="2519c2b2-6704-818a-a2b3-e7497af1be21"

# Agent Core PRD (partial - in development)
notion:fetch id="2529c2b2-6704-811f-b262-fcfb4041da8f"
```

### Session Logging (Abbreviated)
When documenting development sessions, create entry in Claude Session Log:
```bash
# Create session log entry
notion:create-pages
  parent: {"database_id": "edfeee6d276b4cbfa84c2a8e15864e24"}
  pages: [{
    properties: {
      "Session Summary": "[YYYY-MM-DD] Brief description of main work",
      "System": "Claude Code",
      "Session Date": "Month DD, YYYY HH:MM AM/PM PST",
      "Key Outcomes": "• Achievement 1\n• Achievement 2",
      "Next Goals": "• Next task\n• Follow-up item",
      "Tags": "[\"#development\", \"#alfred-system\"]"
    }
  }]
```

**Valid session tags**: `#project-creation`, `#documentation`, `#system-design`, `#quick-session`, `#deep-work`, `#optimization`, `#troubleshooting`

## Infrastructure Details

### Production Environment
- **Server**: DigitalOcean Premium Intel 4GB/2vCPU droplet
- **IP Address**: 134.209.51.66
- **SSH Access**: `ssh -i ~/droplet_key root@134.209.51.66`
- **Project Location**: `/opt/alfred-system`
- **Domain**: artemsys.ai (managed via Cloudflare)
  - api.artemsys.ai → Agent API endpoints
  - chat.artemsys.ai → Web chat interface  
  - mcp.artemsys.ai → MCP server endpoints
  - admin.artemsys.ai → Admin interface

### Authentication & Access
- **GitHub Personal**: JayChir account with `github_pat_11AHA3SHQ0...` token
- **GitHub Work**: BCG account with `github_pat_11BRDNFJY0...` token
- **Anthropic API**: `sk-ant-api03-...` (replaces DeepSeek for production)
- **Notion Integration**: `ntn_419211773484...` token

## Development Environment

### Local Setup Requirements
```bash
# Required tools
python 3.11+
docker & docker-compose
git
doctl (DigitalOcean CLI)

# Python dependencies (in venv)
pip install fastapi uvicorn pydantic-ai fastmcp asyncpg
pip install -e . # Install project in editable mode
```

### Environment Configuration
```bash
# Local development (.env.local)
ANTHROPIC_API_KEY=sk-ant-api03-...
NOTION_TOKEN=ntn_419211773484...
GITHUB_PERSONAL_TOKEN=github_pat_11AHA3SHQ0...
GITHUB_WORK_TOKEN=github_pat_11BRDNFJY0...
DATABASE_URL=postgresql://alfred:password@localhost:5432/alfred_dev
ENVIRONMENT=development
LOG_LEVEL=DEBUG

# Production (.env - already configured on droplet)
ENVIRONMENT=production
DATABASE_URL=postgresql://alfred:secure_password@db:5432/alfred_prod
DOMAIN=artemsys.ai
```

### Docker Development
```bash
# Start local development stack
docker compose -f docker-compose.dev.yml up -d

# View logs
docker compose logs -f [service_name]
docker compose logs -f api
docker compose logs -f db

# Rebuild after code changes
docker compose build [service_name]
docker compose restart [service_name]

# Clean rebuild
docker compose down && docker compose build --no-cache && docker compose up -d
```

## Git Workflow & Branching Strategy

### Branch Structure
```
main                 # Production-ready code (auto-deploys to droplet)
├── develop          # Integration branch for features
├── feature/xyz      # Feature development branches
├── bugfix/xyz       # Bug fix branches
└── hotfix/xyz       # Production hotfixes
```

### Development Protocol
```bash
# Start new feature
git checkout develop
git pull origin develop
git checkout -b feature/agent-core-implementation

# Regular commits (atomic, descriptive)
git add -A
git commit -m "feat: implement Agent Core with Pydantic AI integration

- Add FastAPI application structure with streaming SSE
- Integrate Anthropic model via Pydantic AI
- Implement PostgreSQL session storage
- Add health check and basic error handling"

# Push and create PR to develop branch
git push -u origin feature/agent-core-implementation

# Merge develop -> main triggers production deployment
```

### Commit Message Format
```
type(scope): brief description

Extended description explaining the what and why,
not the how. Reference issues if applicable.

Examples:
feat(agent): add Agent Core with Pydantic AI
fix(cache): resolve TTL configuration bug
docs(readme): update deployment instructions  
refactor(mcp): simplify server initialization
test(api): add integration tests for streaming
perf(db): optimize session query performance
```

## Production Deployment

### DigitalOcean CLI Setup
```bash
# Install doctl (if not already installed)
curl -sL https://github.com/digitalocean/doctl/releases/download/v1.104.0/doctl-1.104.0-linux-amd64.tar.gz | tar -xzv
sudo mv doctl /usr/local/bin

# Authenticate with personal access token
doctl auth init

# Common commands
doctl compute droplet list
doctl compute droplet get alfred-system
doctl compute ssh alfred-system --ssh-key-path ~/droplet_key
```

### Manual Deployment Process
```bash
# Connect to production server
ssh -i ~/droplet_key root@134.209.51.66

# Navigate to project directory
cd /opt/alfred-system

# Deploy latest changes from main branch
git pull origin main
docker compose down
docker compose build --no-cache
docker compose up -d

# Verify deployment
docker compose ps
curl https://api.artemsys.ai/health
curl https://chat.artemsys.ai/health
```

### Production Monitoring
```bash
# Check service status on droplet
docker compose ps
docker compose logs -f api
docker compose logs -f db
docker compose logs --tail=100 [service_name]

# Database access
docker compose exec db psql -U alfred -d alfred_prod

# System resources
htop
df -h  # Disk usage
free -h  # Memory usage
docker system df  # Docker storage usage

# SSL certificate status
certbot certificates
```

## Development Commands

### Local Development
```bash
# Run API server locally (outside Docker)
cd agent/src
python -m uvicorn api.main:app --reload --port 8000 --host 0.0.0.0

# Run tests
pytest tests/ -v
pytest agent/tests/ -v
pytest mcp-servers/memory/tests/ -v --disable-warnings

# Code quality checks
black agent/src/ mcp-servers/ tests/ --check
isort agent/src/ mcp-servers/ tests/ --check-only  
flake8 agent/src/ mcp-servers/ tests/
mypy agent/src/

# Apply formatting
black agent/src/ mcp-servers/ tests/
isort agent/src/ mcp-servers/ tests/
```

### Database Management
```bash
# Local PostgreSQL (if running locally)
createdb alfred_dev
psql alfred_dev

# Production database access (careful!)
ssh -i ~/droplet_key root@134.209.51.66
cd /opt/alfred-system
docker compose exec db psql -U alfred -d alfred_prod

# Database migrations (when implemented)
alembic upgrade head
alembic revision --autogenerate -m "add agent core tables"
```

### Service Health Checks
```bash
# Local health checks
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/status

# Production health checks  
curl https://api.artemsys.ai/health
curl https://api.artemsys.ai/api/v1/agent/status
curl https://mcp.artemsys.ai/health

# Check SSL certificates
curl -I https://api.artemsys.ai
openssl s_client -connect api.artemsys.ai:443 -servername api.artemsys.ai
```

## Architecture Guidelines

### Code Organization (Current Structure)
```
agent/
├── src/
│   ├── api/            # FastAPI application
│   ├── config/         # Configuration management
│   ├── core/           # Pydantic AI agent core
│   └── models/         # Data models
└── tests/              # Agent tests

mcp-servers/
├── memory/src/         # Personal memory MCP
├── notion/src/         # Enhanced Notion MCP  
├── filesystem/src/     # Filesystem operations
├── github/src/         # GitHub integration
├── gmail/src/          # Gmail/Calendar MCP
└── shared/             # Shared utilities

infrastructure/
├── docker/             # Docker configurations
├── db/                 # Database setup and migrations
├── nginx/              # Nginx configuration
└── deploy/             # Deployment scripts

web/src/                # Next.js PWA interface
cli/src/                # Python CLI client
```

### Configuration Management
```python
# agent/src/config/settings.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    anthropic_api_key: str
    notion_token: str
    database_url: str
    environment: str = "development"
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"
```

### Error Handling Standards
```python
# Use structured logging
import structlog
logger = structlog.get_logger()

# Proper exception handling with context
try:
    response = await client.messages.create(...)
except Exception as e:
    logger.error(
        "Agent request failed",
        user_id=session.user_id,
        session_id=session.id,
        error=str(e),
        error_type=type(e).__name__
    )
    raise AgentError("Unable to process request") from e
```

## Debugging & Troubleshooting

### Common Issues & Solutions
```bash
# MCP server connection issues
docker compose logs mcp-memory
netstat -tulpn | grep :8001  # Check port availability
docker compose restart mcp-memory

# Database connection problems
docker compose exec db pg_isready -U alfred
docker compose logs db

# SSL/Certificate issues on production  
certbot certificates
certbot renew --dry-run
systemctl status nginx

# Docker disk space issues
docker system prune -a
docker volume prune
```

### Performance Monitoring
```bash
# API response times
curl -w "@curl-format.txt" -s -o /dev/null https://api.artemsys.ai/health

# Database query performance (production)
ssh -i ~/droplet_key root@134.209.51.66
cd /opt/alfred-system
docker compose exec db psql -U alfred -d alfred_prod
\timing on
EXPLAIN ANALYZE SELECT * FROM sessions WHERE active = true;

# System resource monitoring
htop  # CPU and memory
iotop  # Disk I/O
docker stats  # Container resource usage
```

### Log Analysis
```bash
# Production logs
docker compose logs --tail=100 api
docker compose logs --since=1h api
docker compose logs -f api | grep ERROR

# System logs
journalctl -u docker -f
tail -f /var/log/nginx/error.log
```

## Security Considerations

### API Keys & Secrets Management
- All secrets in environment variables, never committed to git
- Different keys for development vs production environments
- Regular key rotation (quarterly minimum)
- Monitor API usage for anomalies via provider dashboards
- Use least-privilege access for all integrations

### Network Security (Production)
```bash
# Firewall configuration (already set)
ufw status
ufw allow 22/tcp    # SSH (key-based only)
ufw allow 80/tcp    # HTTP (redirects to HTTPS)
ufw allow 443/tcp   # HTTPS
ufw --force enable

# SSH security
# Disable password auth (key-based only)
# Regular security updates: apt update && apt upgrade
```

### Database Security
```bash
# Secure PostgreSQL configuration
# Non-default passwords (already configured)
# Connection limits and timeouts
# Regular backups with encryption
```

## Backup & Recovery

### Database Backups
```bash
# Manual backup (production)
ssh -i ~/droplet_key root@134.209.51.66
cd /opt/alfred-system
docker compose exec db pg_dump -U alfred alfred_prod > backup_$(date +%Y%m%d_%H%M%S).sql

# Restore from backup
docker compose exec -i db psql -U alfred alfred_prod < backup_file.sql
```

### Code & Configuration Backups
```bash
# Repository backups via GitHub (automatic)
# Environment file backups (manual, encrypted)
# SSL certificate backups (Let's Encrypt auto-renewal)
```

## Development Protocols

### Code Review Checklist
- [ ] Tests added/updated for new functionality
- [ ] Error handling implemented with proper logging
- [ ] No hardcoded secrets, URLs, or configuration
- [ ] Performance impact considered (especially for caching)
- [ ] Documentation updated (docstrings, README, CLAUDE.md)
- [ ] Security implications reviewed
- [ ] Database migrations (if applicable) tested

### Definition of Done
- [ ] Feature works locally with docker-compose
- [ ] All tests pass (unit + integration)
- [ ] Code reviewed and approved
- [ ] Deployed to production successfully
- [ ] Health checks pass in production
- [ ] Performance metrics within acceptable ranges
- [ ] Documentation updated

### Release Process
```bash
# 1. Feature development on feature branches
# 2. PR review and merge to develop
# 3. Integration testing on develop branch
# 4. PR from develop to main (triggers production deployment)
# 5. Verify production deployment health
# 6. Tag release: git tag v1.0.0 && git push origin v1.0.0
```

---

## Quick Reference Commands

### Daily Development Cycle
```bash
# Start development session
cd ~/dev/personal/alfred-system
git checkout develop && git pull origin develop
docker compose -f docker-compose.dev.yml up -d

# Create feature branch
git checkout -b feature/my-feature

# Development workflow
# ... code, test, commit ...
git push -u origin feature/my-feature
# Create PR via GitHub

# End of day
docker compose down
```

### Production Deployment
```bash
# Quick deployment
ssh -i ~/droplet_key root@134.209.51.66 "cd /opt/alfred-system && git pull origin main && docker compose up -d --build"

# Verify deployment  
curl https://api.artemsys.ai/health
```

### Emergency Debugging
```bash
# SSH into production
ssh -i ~/droplet_key root@134.209.51.66

# Check what's running
docker compose ps
docker compose logs --tail=50 api

# Quick fixes
docker compose restart api
docker compose down && docker compose up -d
```

Remember: The Alfred System is designed for 83% token reduction through intelligent caching at the Agent Core level, targeting sub-3 second response latency across all interfaces.