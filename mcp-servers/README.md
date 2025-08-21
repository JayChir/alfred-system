# MCP Servers Configuration

This directory contains all MCP (Model Context Protocol) server configurations for remote hosting via mcp-proxy.

## Structure

```
mcp-servers/
├── deploy.sh              # One-time deployment script
├── mcp@.service          # Systemd template (deployed to /etc/systemd/system/)
├── time/                 # Time server configuration
│   ├── time.env         # Environment variables (used directly from repo)
│   ├── nginx.conf       # Nginx config (deployed to /etc/nginx/sites-available/)
│   └── README.md        # Server-specific documentation
├── sequential-thinking/  # Sequential thinking server
│   ├── sequential-thinking.env
│   ├── nginx.conf
│   └── README.md
└── ... (other servers)
```

## Quick Start

### First-Time Setup (New Server)

1. **Clone the repository:**
```bash
git clone https://github.com/JayChir/alfred-system.git /opt/alfred-system
cd /opt/alfred-system/mcp-servers
```

2. **Configure environment variables:**
Edit the `.env` files for servers that need API keys:
```bash
# Example: Configure GitHub Personal Access Token
vim github/github.env
# Add your token: GITHUB_PERSONAL_ACCESS_TOKEN=ghp_your_token_here
```

3. **Run the deployment script:**
```bash
sudo ./deploy.sh
```
This will:
- Install Node.js and mcp-proxy
- Deploy systemd service template
- Deploy nginx configurations
- Set up all required directories

4. **Start desired services:**
```bash
# Start individual services
sudo systemctl enable --now mcp@time
sudo systemctl enable --now mcp@sequential-thinking

# Or start all available services
for service in */; do
  name=$(basename "$service")
  [ -f "${service}${name}.env" ] && sudo systemctl enable --now mcp@${name}
done
```

### Updating After Changes

When you pull new changes from git:

1. **Pull latest changes:**
```bash
cd /opt/alfred-system
git pull
```

2. **If systemd template or nginx configs changed:**
```bash
cd mcp-servers
sudo ./deploy.sh
```

3. **Restart affected services:**
```bash
sudo systemctl restart mcp@time
# or restart all:
sudo systemctl restart 'mcp@*'
```

## Configuration Management

### Environment Variables
- Stored in each server's directory (e.g., `time/time.env`)
- Used directly from the git repo - no copying needed
- Changes take effect after service restart

### Ports Assignment
- 7001: Time
- 7002: Sequential Thinking
- 7003: GitHub Personal
- 7004: GitHub Work
- 7005: Fetch
- 7006: Notion
- 7007: Filesystem
- 7008: Playwright
- 7009: Memory
- 7010: Atlassian

### Adding a New MCP Server

1. Create a new directory:
```bash
mkdir new-server
```

2. Create environment file (`new-server/new-server.env`):
```env
PORT=7011
SHELL_CMD=/usr/bin/docker run -i --rm mcp/new-server
```

3. Create nginx config (`new-server/nginx.conf`):
```nginx
server {
    listen 80;
    server_name mcp-new-server.artemsys.ai;
    
    location /mcp {
        include snippets/mcp_proxy.conf;
        proxy_pass http://127.0.0.1:7011/mcp;
    }
    
    location /sse {
        include snippets/mcp_proxy.conf;
        proxy_pass http://127.0.0.1:7011/sse;
    }
}
```

4. Run deploy script and start:
```bash
sudo ./deploy.sh
sudo systemctl enable --now mcp@new-server
```

## Monitoring

### Check Service Status
```bash
# Single service
sudo systemctl status mcp@time

# All MCP services
sudo systemctl status 'mcp@*'
```

### View Logs
```bash
# Recent logs
sudo journalctl -u mcp@time -n 50

# Follow logs
sudo journalctl -u mcp@time -f
```

### Check Running Containers
```bash
# All MCP containers
docker ps | grep mcp
```

## Troubleshooting

### Service Won't Start
1. Check logs: `sudo journalctl -u mcp@service-name -n 50`
2. Verify port not in use: `sudo lsof -i :7001`
3. Test Docker image: `docker run --rm mcp/image-name --help`

### Connection Refused
1. Check service is running: `sudo systemctl status mcp@service-name`
2. Check nginx config: `sudo nginx -t`
3. Verify firewall rules allow the port

### Docker Permission Issues
Ensure the systemd service runs as root (check mcp@.service User=root)

## DNS Configuration

Add these A records to your DNS provider:
```
mcp-time.artemsys.ai            → your.server.ip
mcp-sequential.artemsys.ai      → your.server.ip
mcp-github-personal.artemsys.ai → your.server.ip
mcp-github-work.artemsys.ai     → your.server.ip
mcp-fetch.artemsys.ai           → your.server.ip
mcp-notion.artemsys.ai          → your.server.ip
mcp-filesystem.artemsys.ai      → your.server.ip
mcp-playwright.artemsys.ai      → your.server.ip
mcp-memory.artemsys.ai          → your.server.ip
mcp-atlassian.artemsys.ai       → your.server.ip
```