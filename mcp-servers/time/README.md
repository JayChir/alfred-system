# MCP Time Server

Provides time and timezone conversion capabilities to Claude.

## Configuration

- **Port**: 7001
- **Domain**: mcp-time.artemsys.ai
- **Docker Image**: mcp/time

## First-Time Setup

1. Clone the repository to your server:
```bash
git clone https://github.com/JayChir/alfred-system.git
cd alfred-system/mcp-servers
```

2. Run the deployment script (only needed once):
```bash
sudo ./deploy.sh
```

3. Start this service:
```bash
sudo systemctl enable --now mcp@time
```

## Updating Configuration

The `time.env` file in this directory is used directly by the service.
To update configuration:

1. Edit `time.env` in this directory
2. Restart the service:
```bash
sudo systemctl restart mcp@time
```

## Testing

```bash
# Check if container is running
docker ps --filter ancestor=mcp/time

# Test HTTP endpoint
curl -i http://127.0.0.1:7001/mcp \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{"clientInfo":{"name":"probe","version":"1.0.0"}}}'
```