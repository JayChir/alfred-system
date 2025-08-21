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

## CRITICAL DEBUGGING LEARNINGS

### Working Solution Summary

After extensive debugging, here's what actually works:

1. **Use `--shell` mode** - Most reliable for Docker commands with arguments
2. **Install mcp-proxy globally** - Avoid npx in systemd services
3. **Use root user** - Required for Docker access (or add user to docker group)
4. **Absolute paths** - Always use full paths in systemd

### Common Issues and Solutions

#### Issue 1: "Not enough non-option arguments"
**Cause**: mcp-proxy not receiving command arguments correctly
**Solution**: Use `--shell` mode or `--` separator before command

#### Issue 2: "Connection closed" errors
**Cause**: Docker container exits immediately
**Solution**: Ensure proper argument passing and Docker permissions

#### Issue 3: Arguments being misinterpreted
**Cause**: bash -c or incorrect quoting in systemd
**Solution**: Never use bash -c wrapper, use direct execution

### Debugging Checklist

1. **Test Docker directly**: 
   ```bash
   docker run -i --rm mcp/time --help
   ```

2. **Test proxy manually**: 
   ```bash
   mcp-proxy --port 7001 --shell "/usr/bin/docker run -i --rm mcp/time"
   ```

3. **Check container status**: 
   ```bash
   docker ps --filter ancestor=mcp/time
   ```

4. **Check port availability**: 
   ```bash
   lsof -i :7001
   ```

5. **Test HTTP endpoint**: 
   ```bash
   curl -i http://127.0.0.1:7001/mcp \
     -H "Content-Type: application/json" \
     --data '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{"clientInfo":{"name":"probe","version":"1.0.0"}}}'
   ```

## Implementation Phases

### Phase 1: Setup Infrastructure (COMPLETED)

#### Prerequisites Installation

On the droplet (134.209.51.66):

```bash
# Install Node.js 20 for mcp-proxy
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install mcp-proxy globally (CRITICAL: Don't use npx in services)
sudo npm install -g mcp-proxy

# Verify installation
which mcp-proxy  # Should show /usr/bin/mcp-proxy
```

#### Directory Structure

```bash
# Create MCP directory structure
sudo mkdir -p /etc/mcp  # For environment files
sudo mkdir -p /opt/mcp  # For working directory
```

### Phase 2: Reusable Template System (COMPLETED)

#### Systemd Template Unit (WORKING VERSION)

Create `/etc/systemd/system/mcp@.service`:

```ini
[Unit]
Description=MCP proxy for %i (stdio → Streamable HTTP)
After=network.target docker.service
Wants=docker.service

[Service]
EnvironmentFile=/etc/mcp/%i.env
# CRITICAL: Use --shell mode for reliable argument passing
ExecStart=/usr/bin/mcp-proxy --port ${PORT} --shell "${SHELL_CMD}"
Restart=always
RestartSec=2
User=root  # Required for Docker access
Group=root
WorkingDirectory=/opt/mcp

[Install]
WantedBy=multi-user.target
```

**Key Changes from Original:**
- Changed from `/opt/mcp/config` to `/etc/mcp` for env files
- Using `--shell` mode instead of tokenized arguments
- Using root user for Docker permissions
- Direct mcp-proxy binary instead of npx
- Added RestartSec for stability

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

#### Environment Files (WORKING FORMAT)

For each MCP server, create a configuration file in `/etc/mcp/`:

**Time Server** (`/etc/mcp/time.env`):
```env
PORT=7001
SHELL_CMD=/usr/bin/docker run -i --rm mcp/time
```

**Sequential Thinking** (`/etc/mcp/sequential-thinking.env`):
```env
PORT=7002
SHELL_CMD=/usr/bin/docker run -i --rm mcp/sequentialthinking
```

**GitHub Personal** (`/etc/mcp/github-personal.env`):
```env
PORT=7003
SHELL_CMD=/usr/bin/docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN=${GITHUB_PERSONAL_TOKEN} mcp/github
```

**GitHub Work** (`/etc/mcp/github-work.env`):
```env
PORT=7004
SHELL_CMD=/usr/bin/docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN=${GITHUB_WORK_TOKEN} mcp/github
```

**Fetch** (`/etc/mcp/fetch.env`):
```env
PORT=7005
SHELL_CMD=/usr/bin/docker run -i --rm mcp/fetch
```

**Notion** (`/etc/mcp/notion.env`):
```env
PORT=7006
SHELL_CMD=/usr/bin/docker run -i --rm -e NOTION_TOKEN=${NOTION_INTEGRATION_TOKEN} mcp/notion
```

**Filesystem** (`/etc/mcp/filesystem.env`):
```env
PORT=7007
SHELL_CMD=/usr/bin/docker run -i --rm -v /opt/mcp/data:/data:rw mcp/filesystem
```

**Playwright** (`/etc/mcp/playwright.env`):
```env
PORT=7008
SHELL_CMD=/usr/bin/docker run -i --rm mcp/playwright
```

**Memory** (`/etc/mcp/memory.env`):
```env
PORT=7009
SHELL_CMD=/usr/bin/docker run -i --rm -v claude-memory:/app/dist mcp/memory
```

**Atlassian** (`/etc/mcp/atlassian.env`):
```env
PORT=7010
SHELL_CMD=/usr/bin/docker run -i --rm -e CONFLUENCE_URL=${CONFLUENCE_URL} -e CONFLUENCE_USERNAME=${ATLASSIAN_USERNAME} -e CONFLUENCE_API_TOKEN=${ATLASSIAN_API_TOKEN} -e JIRA_URL=${JIRA_URL} -e JIRA_USERNAME=${ATLASSIAN_USERNAME} -e JIRA_API_TOKEN=${ATLASSIAN_API_TOKEN} ghcr.io/sooperset/mcp-atlassian:latest
```

**Important Notes:**
- All use SHELL_CMD format for --shell mode
- Ports changed to 7xxx range to avoid conflicts
- Environment variables will be expanded from system environment
- Memory server needs volume for persistence

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
NOTION_INTEGRATION_TOKEN=ntn_your_notion_token

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