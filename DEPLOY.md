# Alfred System - MCP Servers Deployment

Quick deployment guide for running official MCP servers on DigitalOcean droplet with subdomain routing.

## 1. Droplet Setup

```bash
# SSH into droplet
ssh -i ~/droplet_key root@134.209.51.66

# Navigate to project
cd /opt/alfred-system

# Pull latest changes
git checkout main
git pull origin main
```

## 2. Environment Configuration

```bash
# Copy and configure environment variables
cp .env.example .env
nano .env
```

Required variables:
- `GITHUB_PERSONAL_TOKEN` - Personal GitHub access token
- `GITHUB_WORK_TOKEN` - Work GitHub access token  
- `NOTION_TOKEN` - Notion integration token
- `CONFLUENCE_URL` - BCG Confluence URL
- `JIRA_URL` - BCG JIRA URL
- `ATLASSIAN_USERNAME` - BCG email
- `ATLASSIAN_API_TOKEN` - Atlassian API token

## 3. Deploy MCP Servers

```bash
# Pull all official MCP images
docker compose pull

# Start all MCP servers
docker compose up -d time-mcp github-personal-mcp github-work-mcp fetch-mcp notion-mcp sequential-thinking-mcp filesystem-mcp playwright-mcp memory-mcp-official atlassian-mcp

# Verify all containers are running
docker compose ps
```

## 4. Configure Nginx Subdomain Routing

Add to nginx configuration:

```nginx
# MCP Server routing
server {
    listen 443 ssl;
    server_name mcp-time.artemsys.ai;
    location / { proxy_pass http://localhost:3001; }
}
server {
    listen 443 ssl;
    server_name mcp-github-personal.artemsys.ai;
    location / { proxy_pass http://localhost:3002; }
}
server {
    listen 443 ssl;
    server_name mcp-github-work.artemsys.ai;
    location / { proxy_pass http://localhost:3003; }
}
server {
    listen 443 ssl;
    server_name mcp-fetch.artemsys.ai;
    location / { proxy_pass http://localhost:3004; }
}
server {
    listen 443 ssl;
    server_name mcp-notion.artemsys.ai;
    location / { proxy_pass http://localhost:3005; }
}
server {
    listen 443 ssl;
    server_name mcp-sequential.artemsys.ai;
    location / { proxy_pass http://localhost:3006; }
}
server {
    listen 443 ssl;
    server_name mcp-filesystem.artemsys.ai;
    location / { proxy_pass http://localhost:3007; }
}
server {
    listen 443 ssl;
    server_name mcp-playwright.artemsys.ai;
    location / { proxy_pass http://localhost:3008; }
}
server {
    listen 443 ssl;
    server_name mcp-memory.artemsys.ai;
    location / { proxy_pass http://localhost:3009; }
}
server {
    listen 443 ssl;
    server_name mcp-atlassian.artemsys.ai;
    location / { proxy_pass http://localhost:3010; }
}
```

## 5. SSL Certificates

```bash
# Add subdomains to Cloudflare DNS (*.artemsys.ai)
# Restart nginx
systemctl reload nginx
```

## 6. Update Local Claude Configuration

Replace NPX commands with HTTP connections:

```json
{
  "mcpServers": {
    "time": {
      "command": "curl",
      "args": ["-X", "POST", "https://mcp-time.artemsys.ai"]
    },
    "github-personal": {
      "command": "curl", 
      "args": ["-X", "POST", "https://mcp-github-personal.artemsys.ai"]
    },
    "github-work": {
      "command": "curl",
      "args": ["-X", "POST", "https://mcp-github-work.artemsys.ai"]
    }
    // ... etc for all services
  }
}
```

## 7. Verification

Test each service:
```bash
curl https://mcp-time.artemsys.ai/health
curl https://mcp-notion.artemsys.ai/health
# etc.
```

## Result

All MCP servers running on your droplet, accessible via `*.artemsys.ai` subdomains, with no dependency on NPX proxy services.

**Benefits**: 
- Data sovereignty (your infrastructure)
- No rate limits 
- Always available
- Easy to update (just pull new Docker images)