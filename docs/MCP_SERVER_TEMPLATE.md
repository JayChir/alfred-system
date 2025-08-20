# MCP Server Development Template

This document codifies learnings and patterns for building MCP servers in the Alfred System.

## 1. FastMCP Framework Standards

```python
# Server template for all MCP servers:
from fastmcp import FastMCP

mcp = FastMCP("server-name")

@mcp.tool()
def tool_name(param: str) -> dict:
    """Tool description"""
    return {"result": "data"}

if __name__ == "__main__":
    # Use HTTP transport for cloud deployment
    mcp.run(transport="http", host="0.0.0.0", port=PORT)
```

## 2. Directory Structure Pattern

```
mcp-servers/
└── server-name/
    ├── src/
    │   └── server.py       # Main server code
    ├── requirements.txt    # Python dependencies
    └── Dockerfile         # Simple, clean Docker config
```

## 3. Dockerfile Template

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
EXPOSE [PORT]
CMD ["python", "src/server.py"]
```
**Key:** Keep it simple - no certificate handling in Docker

## 4. Port Allocation Strategy

- Time MCP: 8005
- Brave Search: 8006 (proposed)
- Sequential Thinking: 8007 (proposed)
- Notion: 8008 (proposed)
- GitHub Personal: 8009 (proposed)
- GitHub Work: 8010 (proposed)
- **Pattern:** 8000+ range, increment by 1

## 5. Transport Configuration

- **Use "http" transport** (this is Streamable HTTP in FastMCP)
- **Not "streamable-http"** - that throws errors
- **Not STDIO** - that's for local-only tools
- All servers should bind to `0.0.0.0` for Docker networking

## 6. Development Workflow

```bash
# 1. Create server locally first
cd mcp-servers/new-server
python3 -m venv venv
source venv/bin/activate
pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org [packages]

# 2. Test locally
python src/server.py

# 3. Then dockerize (disable Zscaler)
docker build -t alfred-new-mcp .

# 4. Commit at logical checkpoints
git add . && git commit -m "feat(mcp): add New MCP server"
```

## 7. Error Response Pattern

```python
try:
    # Main logic
    return {"success": True, "data": result}
except Exception as e:
    return {"error": str(e)}
```

## 8. Requirements.txt Pattern

```
fastmcp>=2.11.0  # Always include
# Add specific server dependencies
# Keep minimal - only what's needed
```

## 9. Docker Compose Integration

```yaml
service-name-mcp:
  build:
    context: ./mcp-servers/service-name
    dockerfile: Dockerfile
  ports:
    - "HOST_PORT:CONTAINER_PORT"
  environment:
    - ENV_VAR=${ENV_VAR}  # Only if needed
  restart: unless-stopped
```

## 10. Corporate Proxy Handling

- **Decision:** Don't handle in code
- **Workaround:** Disable Zscaler for Docker builds
- **Benefit:** Clean, portable code that works everywhere

## 11. Testing Strategy

1. Local Python first (quickest feedback)
2. Docker build second (when switching networks)
3. Docker compose last (integration test)

## 12. Git Hygiene

- Commit after each working server
- Atomic commits with clear messages
- Push regularly for backup
- Use descriptive commit messages following conventional commits

## 13. FastMCP Gotchas

- `mcp.run()` is synchronous, blocks the main thread
- Use `transport="http"` not `transport="streamable-http"`
- Default port is 8000 if not specified
- Server name shows in the banner (keep it short)

## 14. Documentation Pattern

Each server should have:
- Clear docstrings on all tools
- README if complex
- Environment variables documented
- Port number clearly stated

## 15. Secrets Management

- Use environment variables
- Never hardcode API keys
- Document required env vars in docker-compose
- Use `.env` file locally (git-ignored)

## Quick Server Creation Checklist

- [ ] Create directory structure
- [ ] Write server.py with FastMCP
- [ ] Create requirements.txt
- [ ] Test locally with venv
- [ ] Create Dockerfile
- [ ] Add to docker-compose.yml
- [ ] Test with Docker
- [ ] Commit changes
- [ ] Document in README if complex