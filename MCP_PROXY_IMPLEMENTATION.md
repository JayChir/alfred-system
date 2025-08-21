# MCP Server Remote Hosting Implementation Plan

## Overview

This document outlines the implementation plan for hosting multiple MCP servers remotely on our DigitalOcean droplet using the **mcp-proxy** approach. This strategy allows us to expose stdio-based MCP servers as Streamable HTTP endpoints without modifying the original server code.

## Architecture

```
Local Claude Desktop → HTTPS → artemsys.ai subdomains → nginx → mcp-proxy → Docker MCP containers (stdio)
```

### Key Components

- **mcp-proxy**: TypeScript-based proxy that translates stdio ↔ Streamable HTTP
- **nginx**: Reverse proxy with TLS termination and subdomain routing
- **systemd**: Process management with template units for easy scaling
- **Docker**: Official MCP server containers running in stdio mode

## Implementation Phases

### Phase 1: Setup Infrastructure (1-2 hours)

#### Prerequisites Installation

On the droplet (134.209.51.66):

```bash
# Install Node.js 18+ for mcp-proxy
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install mcp-proxy globally
sudo npm install -g mcp-proxy

# Verify installation
npx mcp-proxy --version
```

#### Directory Structure

```bash
# Create MCP directory structure
sudo mkdir -p /opt/mcp/{config,logs}
sudo useradd -r -s /bin/false mcp
sudo chown -R mcp:mcp /opt/mcp
```

### Phase 2: Reusable Template System (2-3 hours)

#### Systemd Template Unit

Create `/etc/systemd/system/mcp@.service`:

```ini
[Unit]
Description=MCP proxy for %i (stdio → Streamable HTTP)
After=network.target docker.service
Wants=docker.service

[Service]
EnvironmentFile=/opt/mcp/config/%i.env
ExecStart=/usr/bin/npx mcp-proxy --port ${PORT} -- ${CMD} ${ARGS}
Restart=always
User=mcp
Group=mcp
WorkingDirectory=/opt/mcp
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

#### Nginx Proxy Configuration

Create `/etc/nginx/snippets/mcp_proxy.conf`:

```nginx
# Streamable HTTP + SSE proxy configuration
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_buffering off;  # Critical for SSE streaming
proxy_connect_timeout 60s;
proxy_send_timeout 1h;
proxy_read_timeout 1h;
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

### Phase 3: Configure Each MCP Server (15 minutes per server)

#### Environment Files

For each MCP server, create a configuration file in `/opt/mcp/config/`:

**Time Server** (`/opt/mcp/config/time.env`):
```env
PORT=3001
CMD=/usr/bin/docker
ARGS=run -i --rm mcp/time
```

**GitHub Personal** (`/opt/mcp/config/github-personal.env`):
```env
PORT=3002
CMD=/usr/bin/docker
ARGS=run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN=${GITHUB_PERSONAL_TOKEN} mcp/github
```

**GitHub Work** (`/opt/mcp/config/github-work.env`):
```env
PORT=3003
CMD=/usr/bin/docker
ARGS=run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN=${GITHUB_WORK_TOKEN} mcp/github
```

**Fetch** (`/opt/mcp/config/fetch.env`):
```env
PORT=3004
CMD=/usr/bin/docker
ARGS=run -i --rm mcp/fetch
```

**Notion** (`/opt/mcp/config/notion.env`):
```env
PORT=3005
CMD=/usr/bin/docker
ARGS=run -i --rm -e NOTION_API_KEY=${NOTION_TOKEN} mcp/notion
```

**Sequential Thinking** (`/opt/mcp/config/sequential-thinking.env`):
```env
PORT=3006
CMD=/usr/bin/docker
ARGS=run -i --rm mcp/sequentialthinking
```

**Filesystem** (`/opt/mcp/config/filesystem.env`):
```env
PORT=3007
CMD=/usr/bin/docker
ARGS=run -i --rm -v /opt/mcp/data:/data:rw mcp/filesystem
```

**Playwright** (`/opt/mcp/config/playwright.env`):
```env
PORT=3008
CMD=/usr/bin/docker
ARGS=run -i --rm mcp/playwright
```

**Memory** (`/opt/mcp/config/memory.env`):
```env
PORT=3009
CMD=/usr/bin/docker
ARGS=run -i --rm mcp/memory
```

**Atlassian** (`/opt/mcp/config/atlassian.env`):
```env
PORT=3010
CMD=/usr/bin/docker
ARGS=run -i --rm -e CONFLUENCE_URL=${CONFLUENCE_URL} -e CONFLUENCE_USERNAME=${ATLASSIAN_USERNAME} -e CONFLUENCE_API_TOKEN=${ATLASSIAN_API_TOKEN} -e JIRA_URL=${JIRA_URL} -e JIRA_USERNAME=${ATLASSIAN_USERNAME} -e JIRA_API_TOKEN=${ATLASSIAN_API_TOKEN} ghcr.io/sooperset/mcp-atlassian:latest
```

### Phase 4: Nginx Configuration (30 minutes)

#### Server Blocks

Create individual nginx configuration files for each MCP server:

**Time Server** (`/etc/nginx/sites-available/mcp-time.conf`):
```nginx
server {
    listen 80;
    server_name mcp-time.artemsys.ai;
    
    location /mcp {
        include snippets/mcp_proxy.conf;
        proxy_pass http://127.0.0.1:3001/mcp;
    }
    
    location /sse {
        include snippets/mcp_proxy.conf;
        proxy_pass http://127.0.0.1:3001/sse;
    }
}
```

**GitHub Personal** (`/etc/nginx/sites-available/mcp-github-personal.conf`):
```nginx
server {
    listen 80;
    server_name mcp-github-personal.artemsys.ai;
    
    location /mcp {
        include snippets/mcp_proxy.conf;
        proxy_pass http://127.0.0.1:3002/mcp;
    }
    
    location /sse {
        include snippets/mcp_proxy.conf;
        proxy_pass http://127.0.0.1:3002/sse;
    }
}
```

**Repeat pattern for all servers...**

#### Enable Sites

```bash
# Enable all MCP sites
sudo ln -s /etc/nginx/sites-available/mcp-*.conf /etc/nginx/sites-enabled/

# Test nginx configuration
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx
```

### Phase 5: TLS with Let's Encrypt (15 minutes)

```bash
# Install certbot
sudo apt install certbot python3-certbot-nginx

# Request certificates for all subdomains
sudo certbot --nginx \
  -d mcp-time.artemsys.ai \
  -d mcp-github-personal.artemsys.ai \
  -d mcp-github-work.artemsys.ai \
  -d mcp-fetch.artemsys.ai \
  -d mcp-notion.artemsys.ai \
  -d mcp-sequential.artemsys.ai \
  -d mcp-filesystem.artemsys.ai \
  -d mcp-playwright.artemsys.ai \
  -d mcp-memory.artemsys.ai \
  -d mcp-atlassian.artemsys.ai

# Verify auto-renewal
sudo certbot renew --dry-run
```

### Phase 6: Deploy and Test (30 minutes)

#### Start Services

```bash
# Reload systemd configuration
sudo systemctl daemon-reload

# Enable and start all MCP services
sudo systemctl enable --now mcp@time
sudo systemctl enable --now mcp@github-personal
sudo systemctl enable --now mcp@github-work
sudo systemctl enable --now mcp@fetch
sudo systemctl enable --now mcp@notion
sudo systemctl enable --now mcp@sequential-thinking
sudo systemctl enable --now mcp@filesystem
sudo systemctl enable --now mcp@playwright
sudo systemctl enable --now mcp@memory
sudo systemctl enable --now mcp@atlassian

# Check service status
sudo systemctl status mcp@time
```

#### Test Endpoints

```bash
# Test Time MCP server
curl -i https://mcp-time.artemsys.ai/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{"clientInfo":{"name":"test","version":"1.0.0"}}}'

# Look for successful response and Mcp-Session-Id header
```

#### Monitor Logs

```bash
# View service logs
sudo journalctl -u mcp@time -f

# View all MCP services
sudo journalctl -u 'mcp@*' -f
```

### Phase 7: Update Local Configuration (10 minutes)

#### Update Claude Desktop Configuration

Update your local `~/.claude.json` to use remote MCP servers:

```json
{
  "mcpServers": {
    "time": {
      "command": "curl",
      "args": ["-X", "POST", "https://mcp-time.artemsys.ai/mcp"]
    },
    "github-personal": {
      "command": "curl", 
      "args": ["-X", "POST", "https://mcp-github-personal.artemsys.ai/mcp"]
    },
    "github-work": {
      "command": "curl",
      "args": ["-X", "POST", "https://mcp-github-work.artemsys.ai/mcp"]
    },
    "fetch": {
      "command": "curl",
      "args": ["-X", "POST", "https://mcp-fetch.artemsys.ai/mcp"]
    },
    "notion": {
      "command": "curl",
      "args": ["-X", "POST", "https://mcp-notion.artemsys.ai/mcp"]
    },
    "sequential-thinking": {
      "command": "curl",
      "args": ["-X", "POST", "https://mcp-sequential.artemsys.ai/mcp"]
    },
    "filesystem": {
      "command": "curl",
      "args": ["-X", "POST", "https://mcp-filesystem.artemsys.ai/mcp"]
    },
    "playwright": {
      "command": "curl",
      "args": ["-X", "POST", "https://mcp-playwright.artemsys.ai/mcp"]
    },
    "memory": {
      "command": "curl",
      "args": ["-X", "POST", "https://mcp-memory.artemsys.ai/mcp"]
    },
    "atlassian": {
      "command": "curl",
      "args": ["-X", "POST", "https://mcp-atlassian.artemsys.ai/mcp"]
    }
  }
}
```

## Advantages of This Approach

✅ **Zero code changes** - Keep all official Docker images as-is  
✅ **Reusable pattern** - One template works for all 10+ servers  
✅ **Production ready** - Proper TLS, sessions, CORS built-in  
✅ **Easy scaling** - Add new servers in minutes  
✅ **Standard protocols** - Uses official Streamable HTTP transport  
✅ **Session management** - Automatic via Mcp-Session-Id headers  
✅ **CORS enabled** - Works with browser-based clients  

## Implementation Timeline

- **Total setup time:** ~6 hours for full infrastructure + 10 servers
- **Per-server time:** ~15 minutes after initial setup  
- **Maintenance:** Minimal - just pull new Docker images when needed

## DNS Configuration

Ensure the following DNS records point to your droplet (134.209.51.66):

```
mcp-time.artemsys.ai            A    134.209.51.66
mcp-github-personal.artemsys.ai A    134.209.51.66
mcp-github-work.artemsys.ai     A    134.209.51.66
mcp-fetch.artemsys.ai           A    134.209.51.66
mcp-notion.artemsys.ai          A    134.209.51.66
mcp-sequential.artemsys.ai      A    134.209.51.66
mcp-filesystem.artemsys.ai      A    134.209.51.66
mcp-playwright.artemsys.ai      A    134.209.51.66
mcp-memory.artemsys.ai          A    134.209.51.66
mcp-atlassian.artemsys.ai       A    134.209.51.66
```

## Environment Variables

Create `/opt/mcp/.env` with required secrets:

```env
# GitHub tokens
GITHUB_PERSONAL_TOKEN=ghp_your_personal_token
GITHUB_WORK_TOKEN=ghp_your_work_token

# Notion integration
NOTION_TOKEN=secret_your_notion_token

# Atlassian credentials
CONFLUENCE_URL=https://your-company.atlassian.net/wiki/
JIRA_URL=https://your-company.atlassian.net/
ATLASSIAN_USERNAME=your.email@company.com
ATLASSIAN_API_TOKEN=your_atlassian_token
```

## Troubleshooting

### Common Issues

1. **Service fails to start**: Check environment file syntax and Docker image availability
2. **Connection refused**: Verify nginx configuration and service status
3. **SSL certificate issues**: Ensure DNS is properly configured before running certbot
4. **Docker permission issues**: Ensure mcp user can access Docker socket

### Debug Commands

```bash
# Check service status
sudo systemctl status mcp@servicename

# View logs
sudo journalctl -u mcp@servicename -f

# Test proxy directly
curl http://localhost:3001/mcp

# Check nginx configuration
sudo nginx -t

# Verify SSL certificates
sudo certbot certificates
```

## Security Considerations

- **Rate limiting**: Consider adding nginx rate limiting for public endpoints
- **Authentication**: Add API key validation if needed
- **Firewall**: Restrict access to necessary ports only
- **Secrets management**: Use systemd credentials or external secret management
- **Log rotation**: Configure log rotation for service logs

## Future Enhancements

- **Monitoring**: Add Prometheus metrics and Grafana dashboards
- **High availability**: Set up multiple droplets with load balancing
- **Caching**: Implement Redis for session storage across instances
- **API Gateway**: Add centralized authentication and rate limiting