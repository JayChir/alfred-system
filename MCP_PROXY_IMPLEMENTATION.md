# MCP Server Remote Hosting Implementation Plan

## Overview

This document outlines the implementation plan for hosting multiple MCP servers remotely on our DigitalOcean droplet using the **mcp-proxy** approach. This strategy allows us to expose stdio-based MCP servers as Streamable HTTP endpoints without modifying the original server code.

## Repository Structure

### MCP Server Configuration Directory (`/mcp-servers/`)

```
mcp-servers/
├── deploy.sh                    # Deployment script for systemd services
├── setup-env.sh                 # Interactive environment setup script
├── .env                         # Shared environment variables (gitignored)
├── mcp@.service                 # Systemd template unit file
├── README.md                    # MCP servers documentation
│
├── [server-name]/               # Each MCP server has its own directory
│   ├── [server-name].env        # Server-specific configuration
│   ├── nginx.conf               # Nginx server block for this MCP server
│   └── src/                     # Custom server implementation (if any)
│
├── time/                        # Time MCP Server (port 7001)
│   ├── time.env                 # PORT=7001, SHELL_CMD="docker run..."
│   └── nginx.conf               # mcp-time.artemsys.ai → localhost:7001
│
├── sequential-thinking/         # Sequential Thinking MCP (port 7002)
├── github-personal/             # GitHub Personal MCP (port 7003)
├── github-work/                 # GitHub Work MCP (port 7004)
├── fetch/                       # Fetch MCP (port 7005)
├── notion/                      # Notion MCP (port 7006)
├── filesystem/                  # Filesystem MCP (port 7007)
├── playwright/                  # Playwright MCP (port 7008)
├── memory/                      # Memory MCP (port 7009)
└── atlassian/                   # Atlassian MCP (port 7010)
```

### Key Configuration Files

#### 1. **Systemd Template (`mcp@.service`)**
- Deployed to: `/etc/systemd/system/mcp@.service`
- Purpose: Template for all MCP server systemd services
- Loads: `/opt/alfred-system/mcp-servers/.env` + `/opt/alfred-system/mcp-servers/%i/%i.env`
- Usage: `systemctl start mcp@notion` starts Notion MCP server

#### 2. **Environment Files**
- **Shared**: `mcp-servers/.env` - API keys, tokens shared across servers
- **Individual**: `mcp-servers/[server]/.env` - Server-specific PORT and SHELL_CMD
- **Deployment**: Updated via `setup-env.sh` script with proper validation

#### 3. **Nginx Configuration**
- **Individual server blocks**: Each server has `nginx.conf` for subdomain routing
- **Deployed to**: `/etc/nginx/sites-available/mcp-[server].conf`
- **SSL**: Let's Encrypt certificates for `mcp-[server].artemsys.ai`
- **Proxy config**: Routes HTTPS → local mcp-proxy port

#### 4. **Deployment Scripts**
- **`deploy.sh`**: Full infrastructure deployment (systemd, nginx, SSL)
- **`setup-env.sh`**: Interactive API key and token management
- **Process**: Handles repo path substitution, certificate requests, service enabling

### Environment Variable Flow

```
1. User runs setup-env.sh
   ↓
2. Creates mcp-servers/.env with shared API keys
   ↓
3. systemd loads both .env files:
   - mcp-servers/.env (shared: NOTION_INTEGRATION_TOKEN, GITHUB_*)
   - mcp-servers/[server]/[server].env (specific: PORT, SHELL_CMD)
   ↓
4. mcp-proxy starts with environment variables
   ↓
5. Docker container receives environment variables
   ↓
6. MCP server authenticates with APIs using tokens
```

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

### Phase 5: TLS with Let's Encrypt (15 minutes) ✅ COMPLETED

```bash
# Install certbot
sudo apt install certbot python3-certbot-nginx

# Request certificates for all subdomains
sudo certbot --nginx --non-interactive --agree-tos --email your-email@domain.com \
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

**STATUS: COMPLETED ✅**
- All SSL certificates successfully deployed
- All HTTPS endpoints verified working
- DNS records added to Cloudflare
- Auto-renewal configured

### Phase 6: Deploy and Test (30 minutes) ✅ COMPLETED

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

**STATUS: COMPLETED ✅**
- All 10 MCP services deployed and running via systemd
- Environment variables properly configured for all services
- Notion MCP authentication fixed with Bearer tokens
- All HTTP endpoints operational and serving requests
- Infrastructure fully operational on droplet

### Phase 7: Update Local Configuration (10 minutes) ✅ COMPLETED

#### Update Claude Desktop Configuration

**CORRECT FORMAT**: Use `http` type for mcp-proxy endpoints:

```json
{
  "mcpServers": {
    "time-remote": {
      "type": "http",
      "url": "https://mcp-time.artemsys.ai/mcp"
    },
    "github-personal-remote": {
      "type": "http", 
      "url": "https://mcp-github-personal.artemsys.ai/mcp"
    },
    "github-work-remote": {
      "type": "http",
      "url": "https://mcp-github-work.artemsys.ai/mcp"
    },
    "fetch-remote": {
      "type": "http",
      "url": "https://mcp-fetch.artemsys.ai/mcp"
    },
    "notion-remote": {
      "type": "http",
      "url": "https://mcp-notion.artemsys.ai/mcp"
    },
    "sequential-thinking-remote": {
      "type": "http",
      "url": "https://mcp-sequential.artemsys.ai/mcp"
    },
    "filesystem-remote": {
      "type": "http",
      "url": "https://mcp-filesystem.artemsys.ai/mcp"
    },
    "playwright-remote": {
      "type": "http",
      "url": "https://mcp-playwright.artemsys.ai/mcp"
    },
    "memory-remote": {
      "type": "http",
      "url": "https://mcp-memory.artemsys.ai/mcp"
    },
    "atlassian-remote": {
      "type": "http",
      "url": "https://mcp-atlassian.artemsys.ai/mcp"
    }
  }
}
```

**STATUS: INFRASTRUCTURE COMPLETE ✅ - CLIENT CONFIG COMPLETE ✅**
- SSL certificates deployed for all 10 subdomains ✅
- All HTTPS endpoints verified working ✅  
- DNS records configured in Cloudflare ✅
- All MCP services running and operational ✅
- Notion MCP authentication fixed ✅
- Claude client successfully connecting to remote MCPs ✅

## Current Status Summary (August 25, 2025)

### ✅ COMPLETED PHASES
1. **Infrastructure Setup**: Node.js, mcp-proxy, systemd templates ✅
2. **Server Configuration**: All 10 MCP servers deployed with env files ✅
3. **Nginx Reverse Proxy**: All server blocks configured ✅
4. **SSL/TLS Setup**: Let's Encrypt certificates for all subdomains ✅
5. **DNS Configuration**: All mcp-*.artemsys.ai records pointing to droplet ✅
6. **End-to-End Testing**: HTTPS endpoints responding correctly ✅
7. **Client Configuration**: Claude Code successfully connecting to remote MCPs ✅
8. **Authentication Fixes**: Notion MCP Bearer token authentication resolved ✅

### 🎯 PROJECT STATUS: COMPLETE ✅
- **All 10+ MCP servers operational** via HTTPS endpoints
- **Environment variables properly managed** with setup scripts
- **Authentication working** for all services requiring API keys
- **Infrastructure deployed and stable** on DigitalOcean droplet
- **Client successfully connecting** to remote MCP services

### 📋 MAINTENANCE NOTES
- **Cert renewal**: Auto-renewal configured via Let's Encrypt
- **Service monitoring**: All systemd services running and auto-restart on failure  
- **Log management**: Services logging to systemd journals
- **Security**: All endpoints secured with SSL/TLS

## Client Configuration

### Claude Code (Terminal) Configuration

Claude Code uses the `claude mcp add` command with HTTP transport for remote MCP servers:

```bash
# Add all remote MCP servers to Claude Code
# Time server
claude mcp add time --scope user --transport http https://mcp-time.artemsys.ai/mcp

# GitHub Personal
claude mcp add github-personal --scope user --transport http https://mcp-github-personal.artemsys.ai/mcp

# GitHub Work
claude mcp add github-work --scope user --transport http https://mcp-github-work.artemsys.ai/mcp

# Notion
claude mcp add notion --scope user --transport http https://mcp-notion.artemsys.ai/mcp

# Fetch
claude mcp add fetch --scope user --transport http https://mcp-fetch.artemsys.ai/mcp

# Sequential Thinking
claude mcp add sequential-thinking --scope user --transport http https://mcp-sequential.artemsys.ai/mcp

# Filesystem
claude mcp add filesystem --scope user --transport http https://mcp-filesystem.artemsys.ai/mcp

# Playwright
claude mcp add playwright --scope user --transport http https://mcp-playwright.artemsys.ai/mcp

# Memory
claude mcp add memory --scope user --transport http https://mcp-memory.artemsys.ai/mcp

# Atlassian
claude mcp add atlassian --scope user --transport http https://mcp-atlassian.artemsys.ai/mcp
```

**Key Configuration Points:**
- `--scope user`: User-level configuration (not project-specific)
- `--transport http`: Uses HTTP transport for remote endpoints
- All endpoints use HTTPS with SSL certificates

### Claude Desktop Configuration

Claude Desktop requires `mcp-remote` package for HTTP transport. Configuration in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "time": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp-time.artemsys.ai/mcp",
        "--allow-http",
        "--timeout", "100000",
        "--http-only"
      ],
      "env": {
        "NODE_EXTRA_CA_CERTS": "C:\\Users\\chiruvolu jay\\AppData\\Roaming\\Claude\\zscaler_ca.cer"
      }
    },
    "github-personal": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp-github-personal.artemsys.ai/mcp",
        "--allow-http",
        "--timeout", "100000",
        "--http-only"
      ],
      "env": {
        "NODE_EXTRA_CA_CERTS": "C:\\Users\\chiruvolu jay\\AppData\\Roaming\\Claude\\zscaler_ca.cer"
      }
    },
    "github-work": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp-github-work.artemsys.ai/mcp",
        "--allow-http",
        "--timeout", "100000",
        "--http-only"
      ],
      "env": {
        "NODE_EXTRA_CA_CERTS": "C:\\Users\\chiruvolu jay\\AppData\\Roaming\\Claude\\zscaler_ca.cer"
      }
    },
    "notion": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp-notion.artemsys.ai/mcp",
        "--allow-http",
        "--timeout", "100000",
        "--http-only"
      ],
      "env": {
        "NODE_EXTRA_CA_CERTS": "C:\\Users\\chiruvolu jay\\AppData\\Roaming\\Claude\\zscaler_ca.cer"
      }
    },
    "fetch": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp-fetch.artemsys.ai/mcp",
        "--allow-http",
        "--timeout", "100000",
        "--http-only"
      ],
      "env": {
        "NODE_EXTRA_CA_CERTS": "C:\\Users\\chiruvolu jay\\AppData\\Roaming\\Claude\\zscaler_ca.cer"
      }
    },
    "sequential-thinking": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp-sequential.artemsys.ai/mcp",
        "--allow-http",
        "--timeout", "100000",
        "--http-only"
      ],
      "env": {
        "NODE_EXTRA_CA_CERTS": "C:\\Users\\chiruvolu jay\\AppData\\Roaming\\Claude\\zscaler_ca.cer"
      }
    },
    "filesystem": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp-filesystem.artemsys.ai/mcp",
        "--allow-http",
        "--timeout", "100000",
        "--http-only"
      ],
      "env": {
        "NODE_EXTRA_CA_CERTS": "C:\\Users\\chiruvolu jay\\AppData\\Roaming\\Claude\\zscaler_ca.cer"
      }
    },
    "playwright": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp-playwright.artemsys.ai/mcp",
        "--allow-http",
        "--timeout", "100000",
        "--http-only"
      ],
      "env": {
        "NODE_EXTRA_CA_CERTS": "C:\\Users\\chiruvolu jay\\AppData\\Roaming\\Claude\\zscaler_ca.cer"
      }
    },
    "memory": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp-memory.artemsys.ai/mcp",
        "--allow-http",
        "--timeout", "100000",
        "--http-only"
      ],
      "env": {
        "NODE_EXTRA_CA_CERTS": "C:\\Users\\chiruvolu jay\\AppData\\Roaming\\Claude\\zscaler_ca.cer"
      }
    },
    "atlassian": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp-atlassian.artemsys.ai/mcp",
        "--allow-http",
        "--timeout", "100000",
        "--http-only"
      ],
      "env": {
        "NODE_EXTRA_CA_CERTS": "C:\\Users\\chiruvolu jay\\AppData\\Roaming\\Claude\\zscaler_ca.cer"
      }
    }
  }
}
```

**Key Configuration Points:**
- **`mcp-remote` package**: Required for HTTP transport in Claude Desktop
- **`--allow-http`**: Allows HTTP/HTTPS connections
- **`--timeout 100000`**: Extended timeout for enterprise environments
- **`--http-only`**: Forces HTTP-only mode (no stdio fallback)
- **`NODE_EXTRA_CA_CERTS`**: Corporate certificate authority for Zscaler proxy

### Configuration Differences

| Aspect | Claude Code | Claude Desktop |
|--------|-------------|----------------|
| **Command** | `claude mcp add` | Manual JSON config |
| **Transport** | Native HTTP support | Requires `mcp-remote` |
| **Scope** | `--scope user` | Global config file |
| **Corporate Proxy** | Auto-handled | Requires CA cert path |
| **Timeouts** | Default | Manual `--timeout` |

### Testing Client Configuration

```bash
# Claude Code - verify MCP servers
claude mcp list
claude mcp view notion

# Claude Desktop - test specific server
curl -i https://mcp-notion.artemsys.ai/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{"clientInfo":{"name":"test","version":"1.0.0"}}}'
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

## Remote Client Configuration

### Working HTTP Client Format

```json
{
  "mcpServers": {
    "time": {
      "type": "http",
      "url": "https://mcp-time.artemsys.ai/mcp"
    },
    "sequential-thinking": {
      "type": "http",
      "url": "https://mcp-sequential.artemsys.ai/mcp"
    }
  }
}
```

### Configuration Locations to Check

1. **Global User Settings**: `~/.claude/settings.json`
2. **Project-Specific**: `.mcp.json` in project directory  
3. **Main Config**: `~/.claude.json` (mcpServers section)
4. **Environment Variables**: MCP server definitions
5. **Test Config**: `~/.claude-remote-test.json` (working format verified)

### Client Testing Commands

```bash
# Test with specific config file
claude --mcp-config ~/.claude-remote-test.json --strict-mcp-config

# Test HTTP endpoint directly
curl -i https://mcp-time.artemsys.ai/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{"clientInfo":{"name":"test","version":"1.0.0"}}}'
```

## Future Enhancements

- **Monitoring**: Add Prometheus metrics and Grafana dashboards
- **High availability**: Set up multiple droplets with load balancing
- **Caching**: Implement Redis for session storage across instances
- **API Gateway**: Add centralized authentication and rate limiting