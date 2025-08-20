# Development Guide

## Building Docker Images

### Behind Corporate Proxy/Zscaler

If you're behind a corporate proxy like Zscaler, you have two options:

#### Option 1: Temporarily Disable Zscaler (Recommended)
```bash
# 1. Disable Zscaler
# 2. Run the build
./scripts/docker-build.sh
# or
docker compose build

# 3. Re-enable Zscaler
```

#### Option 2: Run Services Locally Without Docker
```bash
# For Python-based MCP servers
cd mcp-servers/time
python3 -m venv venv
source venv/bin/activate
pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt
python src/server.py
```

## Local Development

### Testing MCP Servers

Each MCP server can be tested locally without Docker:

```bash
# Time MCP Server
cd mcp-servers/time
source venv/bin/activate
python src/server.py
# Server runs at http://localhost:8005/mcp
```

### Using Docker Compose

For local development with all services:
```bash
docker compose up -d
```

For production deployment (on DigitalOcean droplet):
```bash
docker compose -f docker-compose.yml up -d
```